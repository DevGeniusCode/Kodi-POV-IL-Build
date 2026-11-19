# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The provider search caches: the 24 h result cache on disk and the short "recent search"
# window property.

import json
import hashlib
import os
import time
from resources.lib.core import kodi


# ---- result cache (per media, short TTL) ----------------------------

_CACHE_TTL = 24 * 3600  # seconds (matches DarkSubs's 24h search-result cache)


def _cache_key(info):
    mid = (info.get('imdb_id') or info.get('tmdb_id') or '').strip()
    if not mid:
        return None
    # Include a signature of the ENABLED sources so toggling a source (e.g.
    # turning Ktuvit off) invalidates old cached results instead of serving
    # 6h-stale results that still contain the now-disabled source.
    sig = ''
    try:
        from resources.lib.core import kodi
        a = kodi.addon()
        for s in ('ktuvit', 'wizdom', 'opensubtitles',
                  'yify', 'subsource', 'subscene', 'bsplayer', 'all_lang'):
            sig += '1' if (a.getSetting(s) or '') == 'true' else '0'
    except Exception:
        sig = ''
    return '{0}_s{1}_e{2}_{3}'.format(
        mid, info.get('season') or '0', info.get('episode') or '0', sig)


# Every search that FINISHED is kept for two minutes in Kodi's memory, found
# subtitles or not. Measured in two user logs from an Android box: pressing
# "בחירת כתוביות" re-ran every provider each time (6.4 s, then 4.5 s per
# press), although the automatic search had just run -- the 24 h disk cache
# needs a release name and never stores an empty result. The same media,
# sources and release name give the same list, so two minutes of reuse
# changes nothing but the wait. A search cut short (max_search_time hit while
# the link buffers the video, or refused while a timed-out provider is still
# running) is not kept, and Kodi's own subtitle dialog always searches.
_RECENT_TTL = 120


_RECENT_PROP = 'moransubs.engine_recent'
# Searches stopped at max_search_time, apart: the picker never reuses them.
_CUT_PROP = 'moransubs.engine_recent_cut'
_RECENT_KEEP = 3


def _release_key(info):
    """The release name the match % is computed from, hashed."""
    from resources.lib.fetching.bridge import _detect_release_name
    try:
        rel = _detect_release_name(info) or ''
    except Exception:
        rel = ''
    return hashlib.sha1(rel.encode('utf-8', 'replace')).hexdigest()[:16]


def _recent_entries(prop=_RECENT_PROP):
    try:
        import xbmcgui
        raw = xbmcgui.Window(10000).getProperty(prop)
        rows = json.loads(raw) if raw else []
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _recent_put(info, out, prop=_RECENT_PROP):
    key = _cache_key(info)
    if not key:
        return
    now = time.time()
    rows = []
    for r in _recent_entries(prop):
        try:
            if (isinstance(r, dict) and r.get('key') != key
                    and 0 <= now - float(r.get('t') or 0) < _RECENT_TTL):
                rows.append(r)
        except Exception:
            continue          # a malformed entry is simply dropped
    rows.append({'key': key, 't': now, 'rel': _release_key(info),
                 'out': out or []})
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(prop, json.dumps(
            rows[-_RECENT_KEEP:], ensure_ascii=False))
    except Exception:
        pass


def _recent_get(info, prop=_RECENT_PROP):
    """The finished search of the last two minutes for this media, or None
    (`prop` _CUT_PROP: the cut one)."""
    key = _cache_key(info)
    if not key:
        return None
    rec = None
    for r in _recent_entries(prop):
        if isinstance(r, dict) and r.get('key') == key:
            rec = r
    try:
        age = time.time() - float(rec['t']) if rec else None
    except Exception:
        return None
    if (age is None or not 0 <= age < _RECENT_TTL
            or rec.get('rel', '') != _release_key(info)):
        return None
    out = rec.get('out') or []
    kodi.log('subs_engine_bridge: reusing the search from {0:.0f}s ago '
                   '({1} result(s)) -- not searching every source again'.format(
                       age, len(out)), level='INFO')
    return out


def _cache_dir():
    try:
        import xbmcvfs
        from resources.lib.core import kodi
        base = xbmcvfs.translatePath(
            kodi.addon().getAddonInfo('profile'))
        d = os.path.join(base, 'engine_cache')
        if not os.path.isdir(d):
            os.makedirs(d)
        return d
    except Exception:
        return None


def _cache_get(info, variant=''):
    key = _cache_key(info)
    d = _cache_dir()
    if not key or not d:
        return None
    suffix = ('.' + variant) if variant else ''
    p = os.path.join(d, key + suffix + '.json')
    try:
        if not os.path.isfile(p):
            return None
        if time.time() - os.path.getmtime(p) > _CACHE_TTL:
            return None
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _cache_put(info, candidates, variant=''):
    key = _cache_key(info)
    d = _cache_dir()
    if not key or not d:
        return
    suffix = ('.' + variant) if variant else ''
    p = os.path.join(d, key + suffix + '.json')
    try:
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(candidates, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass
