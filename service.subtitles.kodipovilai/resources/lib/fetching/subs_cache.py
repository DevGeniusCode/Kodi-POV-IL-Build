# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The cache of downloaded subtitle files (Cached_subs_v2).

import os
from resources.lib.core import kodi, settings


# ---- download -------------------------------------------------------

_SUB_EXTS = ('.srt', '.ssa', '.ass', '.sub', '.smi', '.vtt', '.txt')

_CACHED_SUBS_DIRNAME = 'Cached_subs_v2'
# Do NOT bump/delete this cache for the RTL compatibility repair. Existing
# Ktuvit files may already carry a ".shared" marker, and throwing them away
# would cause needless provider fetches plus pool de-dup lookups. Old v2 bytes
# stay immutable; playback gets a separate locally-rendered copy instead.
# DarkSubs caches every download keyed {source}_{language}_{filename}{ext} and
# wipes the whole folder once it exceeds this many files (its
# "subtitle_trans_cache" setting). We keep the same count-based prune.
_CACHED_SUBS_MAX = 200
_CACHED_SUBS_EXTS = ('.srt', '.idx', '.sup', '.sub', '.str', '.ass', '.ssa',
                     '.smi', '.vtt', '.txt')


def _cached_subs_dir():
    try:
        import xbmcvfs
        from resources.lib.core import kodi
        base = xbmcvfs.translatePath(
            kodi.addon().getAddonInfo('profile'))
        d = os.path.join(base, _CACHED_SUBS_DIRNAME)
        if not os.path.isdir(d):
            os.makedirs(d)
        return d
    except Exception:
        return None


def _cached_subs_max():
    # Honour DarkSubs's subtitle_trans_cache setting if present, else default.
    try:
        v = int(settings.get_setting('subtitle_trans_cache', '') or 0)
        if v > 0:
            return v
    except Exception:
        pass
    return _CACHED_SUBS_MAX


def _cached_subs_keybase(cache_dir, source, language, filename):
    """The DarkSubs cache stem: <dir>/<source>_<language>_<filename>. Filename
    components are sanitised so a provider's title can't escape the folder or
    break the path; the value is still stable per pick, so a repeat pick hits
    the same stem."""
    def _safe(s):
        s = str(s or '')
        out = []
        for ch in s:
            if ch.isalnum() or ch in (' ', '.', '-', '_', '(', ')', '[', ']'):
                out.append(ch)
            else:
                out.append('_')
        return ''.join(out).strip() or '_'
    stem = '{0}_{1}_{2}'.format(_safe(source), _safe(language),
                                _safe(filename))
    return os.path.join(cache_dir, stem)


def _cached_subs_lookup(keybase):
    """Return an existing cached file for this stem (any known ext), or None."""
    for ext in _CACHED_SUBS_EXTS:
        p = keybase + ext
        try:
            if os.path.isfile(p):
                return p
        except Exception:
            pass
    return None


def _cached_subs_prune(cache_dir):
    """Match DarkSubs: once the folder exceeds the cap, wipe it wholesale (a
    simple, predictable bound that never blocks a download)."""
    try:
        names = os.listdir(cache_dir)
    except Exception:
        return
    source_names = [
        n for n in names
        if os.path.splitext(n)[1].lower() in _CACHED_SUBS_EXTS
        and '.povil-rtl.' not in n
    ]
    if len(source_names) <= _cached_subs_max():
        return
    for n in names:
        try:
            os.remove(os.path.join(cache_dir, n))
        except Exception:
            pass


def _cached_subs_store(keybase, sub_file):
    """Copy a freshly-downloaded subtitle into the cache (if not already there),
    preserving its extension -- exactly like DarkSubs's shutil.copy."""
    try:
        import shutil
        ext = os.path.splitext(sub_file)[1] or '.srt'
        dest = keybase + ext
        if not os.path.exists(dest):
            shutil.copy(sub_file, dest)
        return dest
    except Exception as e:
        kodi.log('subs_engine_bridge: cache store skipped: {0}'
                       .format(e), level='DEBUG')
        return None


def _looks_like_subtitle(path):
    """True if the file is a plausible subtitle: not an HTML/zip blob (some
    providers hand back the error page or un-extracted archive when a download
    actually failed). Accepts by known extension OR by content sniff -- some
    providers quote the Content-Disposition filename ("name.srt") so the
    on-disk extension comes out mangled even though the bytes are a real SRT."""
    try:
        with open(path, 'rb') as f:
            head = f.read(2048)
        if not head.strip():
            return False
        if head[:2] == b'PK':          # zip
            return False
        low = head.lstrip().lower()
        if low.startswith((b'<!doctype', b'<html', b'<?xml', b'<head')):
            return False
        ext = os.path.splitext(path)[1].lower().strip('"\'')
        if ext in _SUB_EXTS:
            return True
        # Mangled / missing extension: accept if the CONTENT looks like a
        # subtitle (srt/vtt cue arrow, ass/ssa header, microdvd frame braces).
        if (b'-->' in head or b'[script info]' in low
                or b'dialogue:' in low or head.lstrip()[:1] == b'{'):
            return True
        return False
    except Exception:
        return True  # if unsure, don't block a possibly-good file


def cached_source(payload):
    """Return an already-downloaded canonical subtitle without networking."""
    from resources.lib.fetching.bridge import _ensure_utf8
    try:
        cache_dir = _cached_subs_dir()
        if not cache_dir:
            return None
        keybase = _cached_subs_keybase(
            cache_dir, payload.get('source') or '',
            payload.get('language') or 'Hebrew',
            payload.get('filename') or 'subtitle')
        hit = _cached_subs_lookup(keybase)
        if not hit:
            return None
        _ensure_utf8(hit)
        return hit if os.path.isfile(hit) else None
    except Exception:
        return None
