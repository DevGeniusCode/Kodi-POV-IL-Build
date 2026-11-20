# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Manual subtitle search, VLSub style: ONE free-text query the viewer types (or edits: it is pre-filled with the playing
# release name), built into the `info` dict of a search from the playing video's `info`
# (core.video_context.current_video_info).
#
# Pure: no xbmc import, no window, no toast (the chooser, ui/chooser.py, asks the question and tells the viewer).
# The result goes through the normal route, pipeline.search(search_info); the playing video's own `info` stays the one
# the download, the translation, SubSync and the community pool use.
#
# What the query can say (parse_query):
#   * a title, then optionally a year and / or SxxEyy (or 1x03), then release words ("The Odyssey 2026 1080p WEB-DL FLUX"):
#     the title (and season / episode / year) steer the search, the release words (1080p, WEB-DL, FLUX ...) rank the list;
#   * only release words ("FLUX", "WEB-DL x265"): a keyword search ON THE PLAYING VIDEO: its ids stay, and the list is
#     ranked by the words.
#
# What a manual `info` is (build_info):
#   * title / tvshow / year / season / episode / is_episode from the query (see below);
#   * imdb_id / tmdb_id / dbid: kept when the query names the playing title (and episode), else cleared (they name the
#     PLAYING title; resolve_ids finds the typed title's IMDb id);
#   * filepath, tagline, label, li_filename, picked_release, language: kept, so the match % is still scored against the
#     playing release. For an episode other than the playing one the release names of the playing file conflict with the
#     typed season / episode (video_context.release_conflicts_with_episode) and drop out by themselves;
#   * '_manual': True, which makes the list skip what belongs to the PLAYING file only (local subtitle files next to it,
#     its embedded tracks) and the community-pool harvest; '_query': the raw text, which the engine hands to the
#     providers that take text (video_data['query']) and which keys the search caches; '_query_tokens': the release words.
#
# Media type: an SxxEyy in the query makes it an episode; a title that is the playing show / movie keeps the playing
# item's type and numbers; any other title with no SxxEyy is a movie.

import re

YEAR_MIN, YEAR_MAX = 1888, 2100
SEASON_MAX, EPISODE_MAX = 99, 999

_SXXEYY_RE = re.compile(r'\bs(\d{1,3})[ ._-]?e(\d{1,4})\b', re.I)
_NXNN_RE = re.compile(r'\b(\d{1,2})x(\d{2,3})\b', re.I)
_YEAR_RE = re.compile(r'(?<![0-9])((?:19|20)\d{2})(?![0-9])')
# the first word that is certainly not part of a title: it starts the release words
_TECH_RE = re.compile(
    r'(?<![a-z0-9])(?:2160p|1080p|1080i|720p|576p|480p|4k|uhd|web[ ._-]?dl|web[ ._-]?rip|webrip|webdl|blu[ ._-]?ray|bdrip|'
    r'brrip|remux|hdtv|dvdrip|hdrip|hdcam|camrip|x26[45]|h[ .]?26[45]|hevc|avc|av1|xvid|hdr10\+?|hdr|dv|dovi|dolby[ ._-]?vision|'
    r'atmos|truehd|dts(?:[ ._-]?hd)?|ddp?[0-9]|dd\+|aac[0-9]?|ac3|eac3|flac|proper|repack|extended|unrated|'
    r'amzn|nf|dsnp|hmax|atvp|pcok|hulu|max)(?![a-z0-9])', re.I)


def normalize(text):
    """Letters and digits only, lower case: 'WEB-DL' and 'web.dl' compare equal."""
    return re.sub(r'[^0-9a-z֐-׿]+', '', (text or '').lower())


def is_episode(playing):
    """True when the playing item is an episode of a show."""
    playing = playing or {}
    return bool(playing.get('is_episode') or (playing.get('tvshow') and playing.get('episode')))


def playing_title(playing):
    """The playing show (an episode) or movie title."""
    playing = playing or {}
    if is_episode(playing):
        return (playing.get('tvshow') or playing.get('title') or '').strip()
    return (playing.get('title') or '').strip()


def default_query(playing, release=''):
    """The pre-filled text of the prompt: the playing release name when known (the viewer deletes what they do not want
    or adds a group), else 'Title (Year)' / 'Show S01E03'."""
    release = (release or '').strip()
    if release and '://' not in release:
        return release
    playing = playing or {}
    title = playing_title(playing)
    if is_episode(playing):
        try:
            return '{0} S{1:02d}E{2:02d}'.format(title, int(playing.get('season') or 0), int(playing.get('episode') or 0)).strip()
        except (TypeError, ValueError):
            return title
    year = str(playing.get('year') or '').strip()
    return '{0} {1}'.format(title, year).strip() if year not in ('', '0') else title


def clean_query(text):
    """A typed query, tidied: %20 / + / _ become spaces, and a release-style name with dots and no space
    ("The.Odyssey.2026.1080p") gets its dots turned into spaces; runs of spaces collapse."""
    text = (text or '').replace('%20', ' ').replace('+', ' ').replace('_', ' ').strip()
    if ' ' not in text and '.' in text:
        text = text.replace('.', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def clean_title(text):
    """Alias of clean_query for a title part."""
    return clean_query(text)


def parse_number(text, low, high):
    """The integer in `text`, or None (empty, not digits, outside low..high)."""
    text = (text or '').strip()
    if not text.isdigit():
        return None
    value = int(text)
    if value < low or value > high:
        return None
    return value


def parse_query(query, playing_words=()):
    """Split a typed query. `playing_words`: normalized words of the playing release (a lone word found there is a release
    word, e.g. a group). A lone ALL-CAPS word ("FLUX") is a release word too; type a one-word title in normal case, or with
    its year. Returns {'raw', 'title', 'year', 'season', 'episode', 'tokens', 'keyword_only'}: strings are ''
    when absent, `tokens` is the list of release words (lower case, as typed otherwise), `keyword_only` is True when no
    title came before the release words."""
    raw = clean_query(query)
    out = {'raw': raw, 'title': '', 'year': '', 'season': '', 'episode': '', 'tokens': [], 'keyword_only': False}
    if not raw:
        return out
    rest = raw
    m = _SXXEYY_RE.search(rest) or _NXNN_RE.search(rest)
    cut = len(rest)
    if m:
        out['season'], out['episode'] = str(int(m.group(1))), str(int(m.group(2)))
        cut = min(cut, m.start())
    tech = _TECH_RE.search(rest)
    if tech:
        cut = min(cut, tech.start())
    # a year is a boundary only when something comes before it (a title that IS a year, "2012", stays a title)
    for ym in _YEAR_RE.finditer(rest):
        if ym.start() > 0 and (ym.start() < cut):
            if not out['year']:
                out['year'] = ym.group(1)
            cut = min(cut, ym.start())
            break
    title = rest[:cut].strip(' -.')
    tail = rest[cut:]
    if not out['year']:
        ym = _YEAR_RE.search(tail)
        if ym:
            out['year'] = ym.group(1)
    out['title'] = title
    if (title and not tail.strip() and not out['year'] and ' ' not in title
            and (title.isupper() or normalize(title) in set(playing_words or ())) and len(normalize(title)) >= 2):
        title = ''
        out['title'] = ''
        tail = raw
    words = []
    for w in re.split(r'\s+', tail):
        if not w:
            continue
        if _SXXEYY_RE.fullmatch(w) or _NXNN_RE.fullmatch(w) or _YEAR_RE.fullmatch(w):
            continue
        words.append(w)
    out['tokens'] = _tokens(words)
    if not title:
        out['keyword_only'] = True
        out['tokens'] = _tokens(w for w in re.split(r'\s+', rest)
                                if not (_SXXEYY_RE.fullmatch(w) or _NXNN_RE.fullmatch(w) or _YEAR_RE.fullmatch(w)))
    return out


def _tokens(words):
    """Release words -> the normalized pieces the rows are searched for: split at hyphens too ("x265-FLUX" -> x265,
    flux), one-character pieces dropped (a lone "1" of "DDP5 1" would match every row)."""
    out = []
    for w in words:
        for piece in re.split(r'[\s\-]+', w):
            piece = normalize(piece)
            if len(piece) >= 2 and piece not in out:
                out.append(piece)
    return out


def _same_title(a, b):
    a, b = normalize(a), normalize(b)
    return bool(a) and a == b


def build_info(playing, query):
    """The search `info` of a manual search (see the module header). `query` is the typed text."""
    words = set()
    for key in ('picked_release', 'li_filename', 'tagline', 'label'):
        words.update(_tokens([(playing or {}).get(key) or '']))
    parsed = parse_query(query, words)
    info = dict(playing or {})
    info.pop('_list_pending', None)
    info['_manual'] = True
    info['_query'] = parsed['raw']
    info['_query_tokens'] = list(parsed['tokens'])
    if parsed['keyword_only']:
        # a keyword search on the playing video: its title, ids and numbers stay
        info['_query_keyword_only'] = True
        return info
    playing_is_episode = is_episode(playing)
    same_title = _same_title(parsed['title'], playing_title(playing))
    if parsed['season'] and parsed['episode']:
        episode_mode = True
    elif same_title:
        episode_mode = playing_is_episode
    else:
        episode_mode = False
    season = parsed['season'] or (str((playing or {}).get('season') or '') if (same_title and episode_mode) else '')
    episode = parsed['episode'] or (str((playing or {}).get('episode') or '') if (same_title and episode_mode) else '')
    year = parsed['year']
    if not year and same_title:
        year = str((playing or {}).get('year') or '').strip()
        year = '' if year in ('0',) else year

    def _num(value):
        value = str(value if value is not None else '').strip()
        return '' if value in ('', '0') else value

    title = clean_title(parsed['title'])
    info['title'] = title
    info['tvshow'] = title if episode_mode else ''
    info['year'] = _num(year)
    info['season'] = _num(season) if episode_mode else ''
    info['episode'] = _num(episode) if episode_mode else ''
    info['is_episode'] = episode_mode
    same_media = same_title and episode_mode == playing_is_episode and (
        not episode_mode or (info['season'] == _num((playing or {}).get('season'))
                             and info['episode'] == _num((playing or {}).get('episode'))))
    if not same_media:
        info['imdb_id'] = ''
        info['tmdb_id'] = ''
        info['dbid'] = ''
    return info


def resolve_ids(search_info):
    """Find the typed title's IMDb id (TMDB search by title and year, through the engine's own resolver), for a search
    whose ids were cleared. Returns (info, found): `info` is a copy with imdb_id set (and the year filled in when none was
    typed and TMDB knows it); found is True when the ids were already there or TMDB answered; False when TMDB has no
    match or cannot be reached, and the search then goes by the typed text only (the providers that take text)."""
    info = dict(search_info or {})
    if (info.get('imdb_id') or '').strip().startswith('tt'):
        return info, True
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


def row_text(row):
    """The text of a candidate that the release words are looked for in: its label and its release name."""
    row = row or {}
    text = row.get('filename') or ''
    link = row.get('link') or ''
    try:
        from resources.lib.core.link_codec import decode_link_safe
        payload = decode_link_safe(link) or {}
        if isinstance(payload, dict):
            text += ' ' + str(payload.get('filename') or payload.get('release') or '')
    except Exception:
        pass
    return normalize(text)


def rank_by_tokens(rows, tokens):
    """Stable partition of `rows` by the release words: rows holding every word first, then rows holding some (more words
    first), then the rest. Nothing is dropped. Returns (rows, full_matches)."""
    tokens = [t for t in (tokens or []) if t]
    rows = list(rows or [])
    if not tokens:
        return rows, 0
    scored = []
    for i, row in enumerate(rows):
        if row.get('_placeholder'):
            scored.append((0, i, row))
            continue
        text = row_text(row)
        scored.append((sum(1 for t in tokens if t in text), i, row))
    full = [r for n, _i, r in scored if n >= len(tokens)]
    some = sorted((t for t in scored if 0 < t[0] < len(tokens)), key=lambda t: (-t[0], t[1]))
    rest = [r for n, _i, r in scored if n == 0]
    return full + [r for _n, _i, r in some] + rest, len(full)


def head(search_info):
    """The line above the list while manual results are shown: "חיפוש ידני: <query>"."""
    info = search_info or {}
    return 'חיפוש ידני: {0}'.format((info.get('_query') or '').strip())
