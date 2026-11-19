# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The playing video as the rest of the add-on sees it (current_video_info).

import re
try:
    import xbmc
    import xbmcaddon
    import xbmcvfs
    import xbmcgui
    KODI_AVAILABLE = True
except ImportError:
    # Allows unit tests / standalone scripts to import the module
    # without a Kodi runtime.
    KODI_AVAILABLE = False
    xbmc = None
    xbmcaddon = None
    xbmcvfs = None
    xbmcgui = None


_EXPLICIT_EPISODE_RE = re.compile(
    r'(?i)(?<![a-z0-9])s0*(\d{1,2})e0*(\d{1,3})(?![a-z0-9])')
# Older scene names: "Show.2014.201.HDTV-LOL" (S02E01, right after the
# show year) and "Show.2x01". Without these a wrong-season subtitle
# passed the episode guard (field: S02E01 offered for S01E01).
_YEAR_EPISODE_RE = re.compile(
    r'(?i)(?<![a-z0-9])(?:19[5-9]\d|20[0-3]\d)[ ._-]+([1-9])(\d{2})'
    r'(?![a-z0-9])')
_CROSS_EPISODE_RE = re.compile(
    r'(?i)(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?![a-z0-9])')


def release_conflicts_with_episode(name, season, episode):
    """Reject a remembered source name explicitly naming another episode.

    A window property can survive autoplay and is not bound to the current
    player item.  Names without an explicit SxxEyy remain inconclusive.

    SubSync's playing_release() calls this for every candidate name. The
    modular package lost it, so the call raised, playing_release() fell back
    to '' and every delivery was left unverified and uncorrected.
    """
    try:
        expected = (int(season), int(episode))
    except (TypeError, ValueError):
        return False
    text = str(name or '')
    matches = (_EXPLICIT_EPISODE_RE.findall(text)
               or _CROSS_EPISODE_RE.findall(text)
               or _YEAR_EPISODE_RE.findall(text))
    return bool(matches and any(
        (int(s), int(e)) != expected for s, e in matches))


def current_video_info():
    """Best-effort snapshot of what Kodi is currently playing.
    Returns a dict with imdb_id, tmdb_id, title, year, season,
    episode, language, filepath -- any field may be empty.

    Kodi's behaviour at subtitle-search time differs across
    library / non-library / direct-play scenarios; we read every
    InfoLabel we plausibly need and let callers pick the bits they
    have."""
    info = {
        'imdb_id': '', 'tmdb_id': '', 'title': '', 'year': '',
        'season': '', 'episode': '', 'language': '', 'filepath': '',
        'tvshow': '', 'is_episode': False, 'tagline': '', 'label': '',
        'dbid': '',
    }
    if not KODI_AVAILABLE:
        return info

    def gi(name):
        try:
            return xbmc.getInfoLabel(name) or ''
        except Exception:
            return ''

    # IMDB id: prefer the explicit UniqueId fetcher because some
    # plugins set that one without setting the legacy IMDBNumber.
    info['imdb_id']  = gi('VideoPlayer.UniqueId(imdb)') or \
                       gi('VideoPlayer.IMDBNumber')
    # TMDB id: POV / FENtastic streaming surfaces this via the
    # UniqueId mechanism even when imdb is empty. Wyzie accepts
    # either, so this is our main fallback for streams.
    info['tmdb_id']  = gi('VideoPlayer.UniqueId(tmdb)')
    # Set only for a Kodi library item -- whose episode TMDB id is the
    # EPISODE's, not the show's (POV and Umbrella give the show's).
    info['dbid']     = gi('VideoPlayer.DBID')
    info['title']    = gi('VideoPlayer.Title') or gi('VideoPlayer.OriginalTitle')
    info['year']     = gi('VideoPlayer.Year')
    info['season']   = gi('VideoPlayer.Season')
    info['episode']  = gi('VideoPlayer.Episode')
    info['tvshow']   = gi('VideoPlayer.TVshowtitle')
    info['filepath'] = gi('Player.Filenameandpath')
    # The release name used for subtitle sync matching. FEN/POV populate
    # the Tagline with the real release (e.g. "Swapped.2026.1080p.NF.WEB-
    # DL.DDP5.1.Atmos.H.264-TURG"); for debrid streams the filepath is a
    # tokenized URL, so the Tagline is what makes the match % meaningful.
    info['tagline'] = gi('VideoPlayer.Tagline') or gi('ListItem.Tagline')
    # The visible label is another good release-name fallback.
    info['label'] = gi('VideoPlayer.Label') or gi('ListItem.Label')
    # ListItem path is often the real release name even when the player's
    # filepath is a tokenized debrid URL -- key for the sync-% matching.
    info['li_filename'] = gi('ListItem.FileNameAndPath') \
        or gi('ListItem.FilenameAndPath')
    # The picked release name POV captures into a home-window property
    # (set by pov_source_name_patcher / subs_filename_publisher, the same
    # one DarkSubs reads). This is the most reliable release name for
    # sync-% matching on debrid streams.
    picked = gi('Window(10000).Property(subs.player_filename)')
    if release_conflicts_with_episode(
            picked, info['season'], info['episode']):
        picked = ''
    info['picked_release'] = picked
    info['is_episode'] = bool(info['tvshow'] and info['episode'])
    return info
