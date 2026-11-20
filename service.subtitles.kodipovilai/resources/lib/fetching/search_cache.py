# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The provider search caches: the 24 h result cache on disk and the short "recent search"
# window property.

import glob
import json
import hashlib
import os
import time
from resources.lib.core import kodi
from resources.lib.core.cache.profile_dir import profile_subdir


# ---- result cache (per media, short TTL) ----------------------------

_CACHE_TTL = 24 * 3600  # seconds (matches DarkSubs's 24h search-result cache)


def _cache_key(info):
    mid = (info.get('imdb_id') or info.get('tmdb_id') or '').strip()
    query = (info.get('_query') or '').strip().lower()
    if query:
        # a manual search (fetching/manual.py): the same title with another typed text is another list, and never the
        # automatic list of the playing video
        mid = '{0}_q{1}'.format(mid or 'text', hashlib.sha1(query.encode('utf-8', 'replace')).hexdigest()[:12])
    if not mid:
        return None
    # Include a signature of the ENABLED sources so toggling a source (e.g.
    # turning Ktuvit off) invalidates old cached results instead of serving
    # 6h-stale results that still contain the now-disabled source.
    sig = ''
    try:
        from resources.lib.core import addon as core_addon
        a = core_addon.addon()
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


RECENT_PROP = 'moransubs.engine_recent'
# Searches stopped at max_search_time, apart: the picker never reuses them.
CUT_PROP = 'moransubs.engine_recent_cut'
_RECENT_KEEP = 3
# The window property is held under the GUI lock: an all-language search can return hundreds of rows, so an
# entry keeps only the first (best-ranked) _RECENT_MAX_CANDIDATES. The 24 h disk list is not capped.
_RECENT_MAX_CANDIDATES = 80


def _release_key(info):
    """The release name the match % is computed from, hashed."""
    from resources.lib.core import video_context
    try:
        rel = video_context.release_name(info, 'scored') or ''
    except Exception:
        rel = ''
    return hashlib.sha1(rel.encode('utf-8', 'replace')).hexdigest()[:16]


def _recent_entries(prop=RECENT_PROP):
    try:
        import xbmcgui
        raw = xbmcgui.Window(10000).getProperty(prop)
        rows = json.loads(raw) if raw else []
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def recent_put(info, out, prop=RECENT_PROP):
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
                 'out': list(out or [])[:_RECENT_MAX_CANDIDATES]})
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(prop, json.dumps(
            rows[-_RECENT_KEEP:], ensure_ascii=False))
    except Exception:
        pass


def recent_get(info, prop=RECENT_PROP):
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
    return profile_subdir('engine_cache')


def _release_file(key, info):
    """File stem of the 24 h list of this media AND this playing release. The match % of every row was computed against
    the release that played when the list was searched; a list kept for the media alone showed that old % beside a panel
    scored live against the CURRENT release (a 15 % row whose every attribute matched, field of 20 Nov 2026). The 2-minute
    window (recent_put) already keyed on the release; the disk list now does too."""
    return '{0}_r{1}'.format(key, _release_key(info))


def _newest_release_file(d, key):
    """The newest list of this media for ANY release (the gender reference reads other languages, not a %), or None."""
    try:
        found = glob.glob(os.path.join(d, glob.escape(key) + '_r' + '[0-9a-f]' * 16 + '.json'))
        return max(found, key=os.path.getmtime) if found else None
    except Exception:
        return None


def cache_get(info, variant='', any_release=False):
    """The kept list for this media and playing release (`variant` lists, like the SubSync timing set, are keyed by media
    alone: they carry no %). `any_release`: the newest list of the media whatever release it was scored against."""
    key = _cache_key(info)
    d = _cache_dir()
    if not key or not d:
        return None
    suffix = ('.' + variant) if variant else ''
    if variant:
        p = os.path.join(d, key + suffix + '.json')
    elif any_release:
        p = _newest_release_file(d, key)
        if not p:
            return None
    else:
        p = os.path.join(d, _release_file(key, info) + '.json')
    try:
        if not os.path.isfile(p):
            return None
        if time.time() - os.path.getmtime(p) > _CACHE_TTL:
            return None
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def cache_put(info, candidates, variant=''):
    key = _cache_key(info)
    d = _cache_dir()
    if not key or not d:
        return
    suffix = ('.' + variant) if variant else ''
    p = os.path.join(d, (key + suffix) if variant else _release_file(key, info)) + '.json'
    try:
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(candidates, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass
