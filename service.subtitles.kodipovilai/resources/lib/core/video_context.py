# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The playing video as the rest of the add-on sees it (current_video_info).

import os
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


# ---- the playing release (ONE derivation, named policies) ---------------------------------------------
# Five call sites used to derive "which release is playing" by their own rules (a sixth was found in
# ai_translation.service). They disagree on purpose or by accident -- the equivalence matrix of the
# golden harness (pr.* cases) pins each one -- so the rules live here as named policies and every caller
# passes its own. A divergence is removed only by the owner, in a later, labelled change.
#   'picker'   the subtitle list (fetching.service.list_candidates): picked_release > tagline > label >
#              li_filename (as a release name) > basename(filepath) > title; first truthy value, unstripped
#   'chooser'  the chooser window header (ui.chooser): picked_release > tagline > label > title
#   'sync'     the sync gate (sync.service.playing_release): picked_release, tagline, label,
#              basename(filepath), title; the first that does not name another episode, stripped
#   'scored'   the engine query and the search-cache key (fetching.bridge): every candidate is scored
#              by _release_score() and the most release-like one wins
#   'pool'     the community pool (contribute side, source-screen reports): release / filename /
#              picked_release / tagline / label that are not tokens, then li_filename, filepath
#   'source'   the timing / pool fallback of ai_translation.service.resolve: the first non-empty of
#              release / picked_release / filename / tagline / label, stripped, no token test
# Tokens that mark a string as a real release name (vs a clean title or a
# debrid token). Used to pick the best release name for sync-% matching.
_REL_PATTERNS = (
    r'(?:19|20)\d{2}',
    r'(?:360|480|576|720|1080|2160)p',
    r'web.?dl|webrip|web|bluray|blu.?ray|brrip|bdrip|hdtv|hdrip|dvdrip|remux|hdcam',
    r'x26[45]|h\.?26[45]|hevc|avc|xvid|10bit',
    r'aac|ac3|e?ac.?3|dts|ddp?5|atmos|dd\+|truehd|multi',
    r'-[a-z0-9]{2,}$',
)


def _release_score(s):
    """Heuristic: how much a string looks like a scene/web release name."""
    if not s:
        return 0
    s2 = s.lower()
    sc = sum(1 for p in _REL_PATTERNS if re.search(p, s2))
    if s2.count('.') >= 3 or s2.count(' ') >= 3:
        sc += 1
    return sc


def _scored_release(info):
    """Pick the best available release name for sync-% matching. The player
    filepath is often a tokenized debrid URL, while the real release name
    lives in the ListItem path / label / tagline -- so we score every
    candidate and take the most release-like one."""
    cands = []
    # POV's captured pick is the most reliable release name -- prefer it.
    pr = (info.get('picked_release') or '').strip()
    if release_conflicts_with_episode(
            pr, info.get('season'), info.get('episode')):
        pr = ''
    if pr:
        cands.append(pr)
    for key in ('filepath', 'li_filename'):
        v = (info.get(key) or '').strip()
        if v:
            try:
                cands.append(os.path.basename(v.rstrip('/')) or v)
            except Exception:
                cands.append(v)
    for key in ('label', 'tagline', 'title'):
        v = (info.get(key) or '').strip()
        if v:
            cands.append(v)
    best, best_score = '', -1
    for c in cands:
        if release_conflicts_with_episode(
                c, info.get('season'), info.get('episode')):
            continue
        sc = _release_score(c)
        # tie-break: prefer the longer (more specific) string
        if sc > best_score or (sc == best_score and len(c) > len(best)):
            best, best_score = c, sc
    return best


def looks_like_token(s, blank=False):
    """True when a string is a debrid URL / token / bare UUID / long hex blob rather than a real
    release name. `blank` is the answer for an empty string (a blank is no release either: the pool
    side passes True, the label side False)."""
    s = (s or '').strip()
    if not s:
        return blank
    low = s.lower()
    if 'token=' in low or '://' in low or '?' in low or '&' in low:
        return True
    # bare UUID, e.g. 499157df-d49d-4c1b-96f9-920866a2354a
    if re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                    r'[0-9a-f]{4}-[0-9a-f]{12}', low):
        return True
    # long hex blob with no spaces/dots (a hash, not a release name)
    if (len(s) >= 24 and '.' not in s and ' ' not in s
            and re.fullmatch(r'[0-9a-f-]+', low)):
        return True
    return False


def release_from_path(p):
    """Release name from a PATH field (li_filename / filepath): basename with the last extension
    stripped, or '' when it is a debrid URL / token / UUID."""
    base = os.path.basename((p or '').strip())
    if '.' in base:
        base = base.rsplit('.', 1)[0]
    if not base:
        return ''
    return '' if looks_like_token(base, True) else base


def _sync_release(info):
    candidates = (info.get('picked_release'), info.get('tagline'),
                  info.get('label'),
                  os.path.basename(info.get('filepath') or ''),
                  info.get('title'))
    return next((str(value).strip() for value in candidates
                 if value and not release_conflicts_with_episode(
                     value, info.get('season'), info.get('episode'))), '')


# step kinds of the chain policies
_RAW = 'raw'        # info[key] as it is; the first truthy value wins
_CLEAN = 'clean'    # info[key] stripped; skipped when empty or token-like
_NAMED = 'named'    # info[key] stripped; skipped when empty
_PATH = 'path'      # release_from_path(info[key])
_BASE = 'base'      # os.path.basename(info[key] or '')
_CHAINS = {
    'picker': ((_RAW, 'picked_release'), (_RAW, 'tagline'), (_RAW, 'label'), (_PATH, 'li_filename'),
               (_BASE, 'filepath'), (_RAW, 'title')),
    'chooser': ((_RAW, 'picked_release'), (_RAW, 'tagline'), (_RAW, 'label'), (_RAW, 'title')),
    'pool': ((_CLEAN, 'release'), (_CLEAN, 'filename'), (_CLEAN, 'picked_release'), (_CLEAN, 'tagline'),
             (_CLEAN, 'label'), (_PATH, 'li_filename'), (_PATH, 'filepath')),
    'source': ((_NAMED, 'release'), (_NAMED, 'picked_release'), (_NAMED, 'filename'), (_NAMED, 'tagline'),
               (_NAMED, 'label')),
}


def release_name(info, policy):
    """The release name of the playing video, by the named policy of the caller (see the table above).
    '' when nothing qualifies. Never catches: a caller keeps its own error handling."""
    if policy == 'scored':
        return _scored_release(info)
    if policy == 'sync':
        return _sync_release(info)
    for kind, key in _CHAINS[policy]:
        if kind == _RAW:
            value = info.get(key)
        elif kind == _CLEAN:
            value = (info.get(key) or '').strip()
            if looks_like_token(value):
                continue
        elif kind == _NAMED:
            value = (info.get(key) or '').strip()
        elif kind == _PATH:
            value = release_from_path(info.get(key))
        else:
            value = os.path.basename(info.get(key) or '')
        if value:
            return value
    return ''


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
