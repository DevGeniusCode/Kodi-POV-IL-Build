# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Probes: the probe cache on disk and the learned cut signatures.

import os
import re
import json
import time
import hashlib
import threading


# ---- file probe (S4): the playing file's own embedded track as reference ----

_PROBE_CACHE_FILE = ('special://profile/addon_data/'
                     'service.subtitles.kodipovilai/subsync_probe_cache.json')
_MAX_PROBE_ENTRIES = 60
# Bump when the probe's cue semantics change; older entries re-probe.
# v2: cues are rebased to the playback timeline (first-cluster origin) and
# union all non-forced tracks -- v1 entries may carry an un-rebased origin.
# v3: remote Matroska uses the debrid-safe per-subtitle Cues index reader
# (head + Cues + origin, a handful of ranged reads) instead of being skipped.
# v4: the remote reader unions every non-forced subtitle track, including
# bitmap tracks, matching the local-file probe and covering PGS-only releases.
# v5: cache entries are scoped to the hashed playback transport/content rather
# than a release label and preserve every track separately plus a cut signature.
# v6: local ISO BMFF/QuickTime timed-text indexes are now probeable.
# v7: opaque remote MP4/MOV streams use the same bounded Range source; old
# negative entries must not suppress the new index probe after upgrade.
PROBE_CACHE_VERSION = 7


def probe_cache_path():
    try:
        import xbmcvfs
        return xbmcvfs.translatePath(_PROBE_CACHE_FILE)
    except Exception:
        return ''


# One small file per media profile in subsync_probe/. The old single
# subsync_probe_cache.json grew to 11 MB (60 multi-track profiles), and it was
# read whole about six times at the start of every episode. json.load parses
# in C without ever releasing the interpreter lock -- 0.1-0.15 s each on a PC,
# seconds on an Android box -- and while it runs no other script can start
# (the "בחירת כתוביות" button waited behind it). Now each lookup reads only
# its own entry; the old file is split once and removed.
_PROBE_DIR_NAME = 'subsync_probe'
_PROBE_MIGRATE_LOCK = threading.Lock()
_PROBE_MIGRATED = [False]


def _probe_entries_dir():
    path = probe_cache_path()
    return os.path.join(os.path.dirname(path), _PROBE_DIR_NAME) if path else ''


def _probe_entry_path(cache_key):
    folder = _probe_entries_dir()
    if not folder or not cache_key:
        return ''
    name = hashlib.sha1(cache_key.encode('utf-8')).hexdigest()[:32]
    return os.path.join(folder, name + '.json')


def _store_probe_entry(cache_key, bundle):
    fpath = _probe_entry_path(cache_key)
    if not fpath:
        return False
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    tmp = '%s.%d.tmp' % (fpath, threading.get_ident())
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'key': cache_key, 'entry': bundle}, f)
        # Windows refuses while a reader has the file open; never leave the
        # temporary copy behind (prune only looks at *.json).
        os.replace(tmp, fpath)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise
    try:
        # Pruning keeps the newest by file time; a migrated entry keeps its age.
        stamp = float(bundle.get('ts') or 0)
        if stamp > 0:
            os.utime(fpath, (stamp, stamp))
    except Exception:
        pass
    return True


def _prune_probe_entries():
    folder = _probe_entries_dir()
    try:
        paths = [os.path.join(folder, n) for n in os.listdir(folder)
                 if n.endswith('.json')]
    except Exception:
        return
    if len(paths) <= _MAX_PROBE_ENTRIES:
        return

    def age(p):
        try:
            return os.path.getmtime(p)
        except Exception:
            return 0.0

    for old in sorted(paths, key=age, reverse=True)[_MAX_PROBE_ENTRIES:]:
        try:
            os.remove(old)
        except Exception:
            pass


def migrate_probe_cache():
    """Split the legacy single-file cache into per-entry files, once."""
    from resources.lib.sync.service import log
    if _PROBE_MIGRATED[0]:
        return
    path = probe_cache_path()
    if not path or not os.path.isfile(path):
        return
    with _PROBE_MIGRATE_LOCK:
        if _PROBE_MIGRATED[0] or not os.path.isfile(path):
            return
        # Once per interpreter, even if the old file cannot be removed: a
        # failed remove must not turn every lookup into a full parse again.
        _PROBE_MIGRATED[0] = True
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
        moved = 0
        for key, ent in (data.items() if isinstance(data, dict) else ()):
            if (isinstance(ent, dict) and key
                    and not os.path.isfile(_probe_entry_path(key))):
                try:
                    moved += bool(_store_probe_entry(key, ent))
                except Exception:
                    pass
        try:
            os.remove(path)
        except Exception:
            pass
        _prune_probe_entries()
        log('probe cache: split %d entr%s into %s/'
             % (moved, 'y' if moved == 1 else 'ies', _PROBE_DIR_NAME))


def read_probe_entry(cache_key):
    """One cached media profile (dict) or None."""
    if not cache_key:
        return None
    migrate_probe_cache()
    fpath = _probe_entry_path(cache_key)
    if not fpath or not os.path.isfile(fpath):
        return None
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            doc = json.load(f)
    except Exception:
        return None
    if not isinstance(doc, dict) or doc.get('key') != cache_key:
        return None
    ent = doc.get('entry')
    return ent if isinstance(ent, dict) else None


def write_probe_cache(cache_key, bundle):
    """Atomically store one media profile in the bounded probe cache."""
    from resources.lib.sync.service import log
    if not cache_key or not isinstance(bundle, dict):
        return False
    migrate_probe_cache()
    try:
        if not _store_probe_entry(cache_key, bundle):
            return False
        _prune_probe_entries()
        return True
    except Exception as e:
        log('probe cache store failed: %r' % e, level='WARNING')
        return False


def known_cut_signature(info, playing=''):
    """Known exact-media id without any network request.

    Local files are sampled directly (192 KiB total).  A remote id is returned
    only after this exact hashed stream URL was already probed during the
    current/previous delivery; a same release name can never borrow it.
    """
    from resources.lib.sync.probes.remote import local_cut_signature, transport_cache_key
    local = local_cut_signature(info)
    if local:
        return local
    cache_key = transport_cache_key(info)
    if not cache_key:
        return ''
    ent = read_probe_entry(cache_key)
    if not isinstance(ent, dict) or ent.get('pv') != PROBE_CACHE_VERSION:
        return ''
    sig = (ent.get('cut_signature') or '').strip().lower()
    return sig if re.fullmatch(r'cut1:[0-9a-f]{32}', sig) else ''


def learn_cut_signature(info):
    """Learn a remote media id with only three 64 KiB content samples.

    This is for a subtitle already trusted by release name: there is no reason
    to parse Matroska Cues or inspect timing merely to namespace a future manual
    correction.  It runs in the service process, after playback is stable, and
    aborts if Kodi changes streams or the provider applies pressure.
    """
    from resources.lib.sync.probes.remote import current_stream_url, probe_enabled, remote_playing_url, remote_probe_ready, transport_cache_key
    from resources.lib.sync.service import log
    known = known_cut_signature(info)
    if known:
        return known
    if not probe_enabled():
        return ''
    url = remote_playing_url(info)
    if not url or not remote_probe_ready(expected_url=url):
        return ''
    started = time.monotonic()

    def abort():
        if time.monotonic() - started > 15.0:
            return True
        try:
            import xbmc
            return (not xbmc.Player().isPlayingVideo()
                    or current_stream_url() != url)
        except Exception:
            return True

    try:
        from resources.lib.fetching.embedded.extract.cue_reference import media_cut_signature
        sig = media_cut_signature(
            url, allow_http=True, abort_cb=abort,
            log=lambda m: log('cut-id: ' + m)) or ''
    except Exception as e:
        log('remote cut-id failed: %r' % e, level='DEBUG')
        return ''
    sig = sig.strip().lower()
    if not re.fullmatch(r'cut1:[0-9a-f]{32}', sig):
        return ''
    cache_key = transport_cache_key(info)
    old = read_probe_entry(cache_key) if cache_key else None
    if not isinstance(old, dict) or old.get('pv') != PROBE_CACHE_VERSION:
        old = {}
    bundle = dict(old)
    bundle.update({'ts': time.time(), 'pv': PROBE_CACHE_VERSION,
                   'cut_signature': sig})
    bundle.setdefault('cues', [])
    bundle.setdefault('track_cues', [])
    bundle.setdefault('tracks', [])
    bundle.setdefault('timing_attempted', False)
    write_probe_cache(cache_key, bundle)
    return sig
