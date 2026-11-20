# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The IMDb id of a video Kodi gave no ids for (this add-on installed alone, a plain file on stock Estuary): the title, year
# and season / episode come out of the release name (core.video_context.parse_release, done by current_video_info), and
# here TMDB is asked for the title's IMDb id through the engine's own resolver (the one the manual search uses), once per
# title: the answer is kept for the session (video_context.ids_cache_put), so every later current_video_info() carries it.
# Without an IMDb id Wizdom, YIFY and SubSource cannot search at all, so this is what makes them work for such a file.

from resources.lib.core import kodi, video_context


def resolve(info):
    """`info` with imdb_id (and a missing year) filled in when it had none and TMDB knows the title. Returns the same
    dict object when nothing changed. Network: call from the search, never from a UI thread. Never raises."""
    try:
        if (info.get('imdb_id') or '').strip() or (info.get('tmdb_id') or '').strip() or info.get('dbid'):
            return info
        title = ((info.get('tvshow') if info.get('is_episode') else info.get('title')) or '').strip()
        if not title or info.get('_manual'):
            return info
        hit = video_context.ids_cache_get(info)
        if hit and hit.get('imdb_id'):
            out = dict(info, imdb_id=hit['imdb_id'])
            if not (out.get('year') or '').strip() and hit.get('year'):
                out['year'] = hit['year']
            return out
        from resources.lib.fetching import bridge, provider_adapter
        bridge.ensure_engine_settings()
        general = provider_adapter.load_general()
        video_data = {
            'media_type': 'tv' if info.get('is_episode') else 'movie',
            'OriginalTitle': title,
            'year': (info.get('year') or '').strip(),
            'imdb': '',
        }
        found = general.manual_search_for_imdb_id(video_data) or video_data
        imdb = (found.get('imdb') or '').strip()
        if not imdb.startswith('tt'):
            return info
        year = str(found.get('year') or '').strip()
        year = '' if year in ('0',) else year
        video_context.ids_cache_put(info, imdb, year)
        kodi.log('standalone_ids: {0!r} -> {1}'.format(title, imdb), level='INFO')
        out = dict(info, imdb_id=imdb)
        if not (out.get('year') or '').strip() and year:
            out['year'] = year
        return out
    except Exception as e:
        kodi.log('standalone_ids failed: {0}'.format(e), level='DEBUG')
        return info
