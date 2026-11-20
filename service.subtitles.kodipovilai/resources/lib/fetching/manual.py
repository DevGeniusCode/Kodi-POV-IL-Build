# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Manual subtitle search: the `info` dict of a search the viewer typed by hand (a title and a year, or a show and a
# season and episode), built from the playing video's `info` (core.video_context.current_video_info).
#
# Pure: no xbmc import, no window, no toast (the chooser, ui/chooser.py, asks the questions and tells the viewer).
# The result goes through the normal route, pipeline.search(search_info); the playing video's own `info` stays the one
# the download, the translation, SubSync and the community pool use.
#
# What a manual `info` is:
#   * title / tvshow / year / season / episode / is_episode: the typed values (the playing item decides movie or episode);
#   * imdb_id / tmdb_id / dbid: cleared (they name the PLAYING title; resolve_ids finds the typed title's IMDb id);
#   * filepath, tagline, label, li_filename, picked_release, language: kept, so the match % is still scored against the
#     playing release. For an episode other than the playing one the release names of the playing file conflict with the
#     typed season / episode (video_context.release_conflicts_with_episode) and drop out by themselves, so the % is then
#     scored against the typed title only;
#   * '_manual': True, which makes the list skip what belongs to the PLAYING file only (local subtitle files next to it,
#     its embedded tracks) and the community-pool harvest.

import re

YEAR_MIN, YEAR_MAX = 1888, 2100
SEASON_MAX, EPISODE_MAX = 99, 999


def is_episode(playing):
    """True when the playing item is an episode of a show."""
    playing = playing or {}
    return bool(playing.get('is_episode') or (playing.get('tvshow') and playing.get('episode')))


def defaults(playing):
    """The pre-filled values of the prompts: what is playing."""
    playing = playing or {}
    episode = is_episode(playing)
    if episode:
        title = (playing.get('tvshow') or playing.get('title') or '').strip()
    else:
        title = (playing.get('title') or '').strip()
    return {
        'episode_mode': episode,
        'title': title,
        'year': str(playing.get('year') or '').strip(),
        'season': str(playing.get('season') or '').strip(),
        'episode': str(playing.get('episode') or '').strip(),
    }


def clean_title(text):
    """A typed query, tidied: %20 / + / _ become spaces, and a release-style name with dots and no space
    ("The.Odyssey.2026") gets its dots turned into spaces; runs of spaces collapse."""
    text = (text or '').replace('%20', ' ').replace('+', ' ').replace('_', ' ').strip()
    if ' ' not in text and '.' in text:
        text = text.replace('.', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def parse_number(text, low, high):
    """The integer typed in a numeric prompt, or None (empty, not digits, outside low..high)."""
    text = (text or '').strip()
    if not text.isdigit():
        return None
    value = int(text)
    if value < low or value > high:
        return None
    return value


def build_info(playing, title, year, season, episode):
    """The search `info` of a manual search (see the module header). The media type is the playing item's.
    year / season / episode may be ints, strings or empty (a movie has no season / episode, a year 0 means none)."""
    info = dict(playing or {})
    info.pop('_list_pending', None)
    episode_mode = is_episode(playing)
    title = clean_title(title)

    def _num(value):
        value = str(value if value is not None else '').strip()
        return '' if value in ('', '0') else value

    info['title'] = title
    info['tvshow'] = title if episode_mode else ''
    info['year'] = _num(year)
    info['season'] = _num(season) if episode_mode else ''
    info['episode'] = _num(episode) if episode_mode else ''
    info['is_episode'] = episode_mode
    info['imdb_id'] = ''
    info['tmdb_id'] = ''
    info['dbid'] = ''
    info['_manual'] = True
    return info


def resolve_ids(search_info):
    """Find the typed title's IMDb id (TMDB search by title and year, through the engine's own resolver).
    Returns (info, found): `info` is a copy with imdb_id set (and the year filled in when none was typed and TMDB knows
    it); found is False when TMDB has no match or cannot be reached, and the search then goes by title only."""
    info = dict(search_info or {})
    title = (info.get('title') or '').strip()
    if not title:
        return info, False
    video_data = {
        'media_type': 'tv' if info.get('is_episode') else 'movie',
        'OriginalTitle': title,
        'year': info.get('year') or '',
        'imdb': '',
    }
    try:
        from resources.lib.fetching import bridge, provider_adapter
        bridge.ensure_engine_settings()
        general = provider_adapter.load_general()
        found = general.manual_search_for_imdb_id(video_data) or video_data
    except Exception:
        return info, False
    imdb = (found.get('imdb') or '').strip()
    if not imdb.startswith('tt'):
        return info, False
    info['imdb_id'] = imdb
    if not info.get('year') and str(found.get('year') or '').strip() not in ('', '0'):
        info['year'] = str(found.get('year')).strip()
    return info, True


def head(search_info):
    """The line above the list while manual results are shown: "חיפוש ידני: Title (Year)" for a movie,
    "חיפוש ידני: Show S01E03" for an episode."""
    info = search_info or {}
    title = (info.get('title') or '').strip()
    if info.get('is_episode'):
        try:
            tag = ' S{0:02d}E{1:02d}'.format(int(info.get('season') or 0), int(info.get('episode') or 0))
        except (TypeError, ValueError):
            tag = ''
        return 'חיפוש ידני: {0}{1}'.format(title, tag)
    year = (info.get('year') or '').strip()
    return 'חיפוש ידני: {0}{1}'.format(title, ' ({0})'.format(year) if year else '')
