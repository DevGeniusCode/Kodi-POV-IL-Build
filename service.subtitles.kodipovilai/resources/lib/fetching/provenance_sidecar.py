# Download-time provenance sidecar (Provenance P4).
#
# A subtitle that has been downloaded anyway (the user's pick, an auto-sub, SubSync's reference, a season pack's members, the
# Ktuvit pool harvest: every one of them goes through fetching.bridge.download_inner) has its FIRST and LAST 8 cues read once for
# credit lines ('תורגם ע"י ...', 'הפקת כתוביות אולפני ...', ...). What is kept is a handful of normalised credit lines, never the
# subtitle text, in <profile>/provenance/credits.json: outside Cached_subs_v2 (which is wiped past 200 files), keyed by the provider
# and its subtitle id ('ktuvit:<SubtitleID>', 'wizdom:<id>', 'opensubtitles:<file id>', a season pack's member 'ktuvit:<id>:S01E02'),
# and bounded (the oldest entries go first). The chooser attaches the stored lines to the provider rows of a later search
# (attach), and the classifier turns them into the same four positive badges as the provider metadata.
#
# Nothing here downloads, and nothing here is called during a search: a row shows file credits only after its file was fetched once.
# Every entry point never raises: a problem with the sidecar must never touch a download.

import hashlib
import json
import os
import threading
import time

from resources.lib.core import kodi
from resources.lib.core.cache import profile_dir
from resources.lib.core.link_codec import decode_link_safe
from resources.lib.core.srt import credits
from resources.lib.scoring_matching import provenance

SCHEMA = 1
FILE_NAME = 'credits.json'
SUBDIR = 'provenance'
MAX_ENTRIES = 4000
KEEP_AFTER_PRUNE = 3600

_LOCK = threading.Lock()
_MEMO = {'path': None, 'mtime': None, 'data': None}


# ---- the key -----------------------------------------------------------------------------------------------------------------
def engine_key(payload, member=''):
    """'<source>:<subtitle id>[:<member>]' of an 'engine' link payload ('' when the payload names no source). The id is the one the
    provider's download uses: Ktuvit's SubtitleID, Wizdom's id, OpenSubtitles' file id; any other source falls back to a short hash
    of its source, language and file name. `member` is a season pack's episode tag ('S01E02')."""
    try:
        source = str(payload.get('source') or '').strip().lower()
        if not source:
            return ''
        data = payload.get('download_data') or {}
        sid = ''
        if source == 'ktuvit':
            raw = data.get('subtitle_download_data')
            try:
                sid = str(json.loads(raw)['request']['SubtitleID']) if isinstance(raw, str) else ''
            except Exception:
                sid = ''
        elif source in ('wizdom', 'opensubtitles'):
            sid = str(data.get('id') or '')
        if not sid:
            sid = 'n-' + hashlib.sha1('|'.join((source, str(payload.get('language') or ''), str(payload.get('filename') or ''))).encode('utf-8')
                                     ).hexdigest()[:16]
        return '{0}:{1}'.format(source, sid) + (':' + str(member) if member else '')
    except Exception:
        return ''


# ---- the file ----------------------------------------------------------------------------------------------------------------
def _path(folder=None):
    d = folder or profile_dir.profile_subdir(SUBDIR)
    return os.path.join(d, FILE_NAME) if d else None


def _load(path):
    try:
        with open(path, 'rb') as f:
            data = json.loads(f.read().decode('utf-8'))
        if isinstance(data, dict) and isinstance(data.get('e'), dict):
            return data
    except Exception:
        pass
    return {'v': SCHEMA, 'e': {}}


def _save(path, data):
    entries = data['e']
    if len(entries) > MAX_ENTRIES:
        keep = sorted(entries, key=lambda k: entries[k].get('t', 0), reverse=True)[:KEEP_AFTER_PRUNE]
        data['e'] = {k: entries[k] for k in keep}
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    os.replace(tmp, path)
    _MEMO.update(path=None, mtime=None, data=None)


def _data(folder=None):
    """The stored entries, re-read only when the file changed (a chooser asks once per list, several processes may write)."""
    path = _path(folder)
    if not path or not os.path.isfile(path):
        return {'v': SCHEMA, 'e': {}}
    mtime = os.path.getmtime(path)
    if _MEMO['path'] == path and _MEMO['mtime'] == mtime and _MEMO['data'] is not None:
        return _MEMO['data']
    data = _load(path)
    _MEMO.update(path=path, mtime=mtime, data=data)
    return data


# ---- scanning a file ----------------------------------------------------------------------------------------------------------
def scan_file(path, allow=None):
    """(lines, cue count, content hash) of a subtitle file on disk: the credit lines of its first and last 8 cues. None when the file
    cannot be read or has no cue."""
    try:
        with open(path, 'rb') as f:
            data = f.read(credits.MAX_FILE_BYTES)
        texts = credits.cue_texts(credits.decode_bytes(data))
        if not texts:
            return None
        return provenance.relevant_lines(texts, allow), len(texts), hashlib.sha1(data).hexdigest()[:16]
    except Exception:
        return None


def _allowlist(allow):
    if allow is not None:
        return allow
    from resources.lib.core import provenance_allowlist
    return provenance_allowlist.current()


def record(payload, path, member='', folder=None, allow=None, overwrite=True):
    """Scan the downloaded file at `path` and store its credit lines under the payload's key. `overwrite=False` keeps an entry that is
    already there (a cache hit of a file scanned before). True when an entry was written. Never raises."""
    return record_many(payload, {member: path}, folder=folder, allow=allow, overwrite=overwrite) > 0


def record_many(payload, members, folder=None, allow=None, overwrite=True):
    """Like record() for several files of one download ({member tag: path}, a season pack): the file is read and written once.
    Returns how many entries were written."""
    written = 0
    try:
        path = _path(folder)
        if not path or not members:
            return 0
        allow = _allowlist(allow)
        with _LOCK:
            data = _load(path)
            pending = 0
            for member, file_path in members.items():
                key = engine_key(payload, member)
                if not key or (not overwrite and key in data['e']):
                    continue
                scanned = scan_file(file_path, allow)
                if scanned is None:
                    continue
                lines, count, digest = scanned
                data['e'][key] = {'t': int(time.time()), 'h': digest, 'n': count, 'l': lines}
                pending += 1
            if pending:
                _save(path, data)       # raises when the folder cannot be written: nothing counts as written then
                written = pending
    except Exception as e:
        kodi.log('provenance sidecar: record skipped: {0}'.format(e), level='DEBUG')
    return written


# ---- reading it back ----------------------------------------------------------------------------------------------------------
def lookup(payload, member='', folder=None):
    """The stored credit lines of a payload's file: a list (empty when the file had no credit line), or None when the file was never
    scanned. A season pack's member is tried first, then the file itself."""
    try:
        entries = _data(folder)['e']
        for m in ((member, '') if member else ('',)):
            entry = entries.get(engine_key(payload, m))
            if entry is not None:
                return [x for x in entry.get('l') or [] if isinstance(x, str)]
    except Exception:
        pass
    return None


def attach(cands, playing_tag='', folder=None):
    """Put the stored credit lines on the provider rows of a candidate list as `_file_credits` (a list of strings): Hebrew rows
    only (`engine` links), and only the ones whose file was downloaded before. Returns how many rows got them. Never raises."""
    n = 0
    try:
        if not _data(folder)['e']:
            return 0
        for c in cands or ():
            if not isinstance(c, dict) or c.get('language') not in ('he', 'heb', 'iw'):
                continue
            payload = decode_link_safe(c.get('link') or '')
            if not isinstance(payload, dict) or payload.get('type') != 'engine' or payload.get('embedded'):
                continue
            lines = lookup(payload, playing_tag, folder)
            if lines is not None:
                c['_file_credits'] = lines
                n += 1
    except Exception as e:
        kodi.log('provenance sidecar: attach skipped: {0}'.format(e), level='DEBUG')
    return n
