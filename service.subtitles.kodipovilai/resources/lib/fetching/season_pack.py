# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Season packs: one subtitle archive (ZIP) that holds a whole season, one .srt per episode.
#
# A provider's download() unpacks the whole archive into the shared download folder and hands back ONE file. For a pack
# that file used to be whatever the archive listed first, and the untagged download cache (Cached_subs_v2, keyed by the
# row's source + language + file name) then served that same file for every episode of the pack. Now, right after a
# download (fetching/bridge.download_inner):
#   1. a pack is recognised by its content: two or more subtitle files whose names carry different SxxEyy tags;
#   2. the file of the episode that is PLAYING is the one delivered (not the first one in the archive); when several files
#      name that episode, a non-hearing-impaired one beats an HI / SDH copy and the closest release name beats the rest;
#   3. that file is stored in the download cache under an episode-tagged stem ("<stem>.S01E02"), and the file of the NEXT
#      episode (S01E03; the first episode of the next season when the season ends) under its own tag, so picking the same
#      pack row for the next episode is a cache hit, without downloading the archive again;
#   4. a Hebrew next-episode file is also remembered in a small index, so the subtitle list of the next episode offers it
#      at once as a local file (fetching/search/state.local_files), even when the providers do not list the pack for that
#      episode and before any provider answers.
# Pure file work (stdlib + core.kodi for the log): runs under the unit tests with a temp folder.

import json
import os
import re
import shutil
import time

_EP_RE = re.compile(r'(?<![0-9a-z])s(\d{1,2})[ ._-]?e(\d{1,3})(?![0-9])|(?<![0-9a-z])(\d{1,2})x(\d{2,3})(?![0-9a-z])', re.I)
_SUB_EXT = ('.srt',)
# a hearing-impaired / SDH / closed-caption copy of an episode's file ('Show.S01E02.HI.srt', '...sdh.srt', '...CC...')
_HI_RE = re.compile(r'(?<![0-9a-z])(?:hi|sdh|cc|hearing[ ._-]?impaired)(?![0-9a-z])', re.I)
_INDEX_NAME = 'index.json'
_KEEP_ENTRIES = 24
_TTL_S = 21 * 86400
_MAX_FILE_BYTES = 3 * 1024 * 1024


def _log(msg, level='INFO'):
    try:
        from resources.lib.core import kodi
        kodi.log('season_pack: ' + msg, level=level)
    except Exception:
        pass


def episode_of(name):
    """(season, episode) ints of the first SxxEyy / 1x03 tag in a file name, or None."""
    m = _EP_RE.search(os.path.basename(name or ''))
    if not m:
        return None
    if m.group(1):
        return int(m.group(1)), int(m.group(2))
    return int(m.group(3)), int(m.group(4))


def tag(season, episode):
    """'S01E02' (the stem suffix of the episode-tagged cache entries); '' without a usable season and episode."""
    try:
        s, e = int(season), int(episode)
    except (TypeError, ValueError):
        return ''
    return 'S{0:02d}E{1:02d}'.format(s, e) if s >= 0 and e > 0 else ''


def playing_tag(info):
    """The tag of the episode that is playing, '' for a movie."""
    info = info or {}
    if not (info.get('is_episode') or (info.get('tvshow') and info.get('episode'))):
        return ''
    return tag(info.get('season'), info.get('episode'))


def is_hearing_impaired(name):
    """True when a subtitle file name marks a hearing-impaired / SDH / closed-caption copy."""
    return bool(_HI_RE.search(os.path.splitext(os.path.basename(name or ''))[0]))


def playing_release(info):
    """The playing video's release name (the one the match % is scored against), '' when unknown."""
    try:
        from resources.lib.core import video_context
        return video_context.release_name(info or {}, 'scored') or ''
    except Exception:
        return ''


def _release_score(playing, name):
    if not playing:
        return 0
    try:
        from resources.lib.scoring_matching import release
        return int(release.match_pct(playing, os.path.basename(name)))
    except Exception:
        return 0


def scan_folder(folder, playing=''):
    """{(season, episode): path} of the .srt files under `folder` whose names carry an episode tag. When several files name
    the same episode (a hearing-impaired copy, several releases in one pack) the winner is, in this order: a file that is
    not hearing-impaired, then the one whose name is closest to the `playing` release (scoring_matching.release.match_pct;
    skipped when `playing` is empty), then the shortest path, then the path itself (a stable result)."""
    found = {}
    ranks = {}
    if not folder or not os.path.isdir(folder):
        return found
    for root, _dirs, files in os.walk(folder):
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() not in _SUB_EXT:
                continue
            ep = episode_of(fn)
            if not ep:
                continue
            path = os.path.join(root, fn)
            rank = (is_hearing_impaired(fn), -_release_score(playing, fn), len(path), path)
            if ep not in found or rank < ranks[ep]:
                found[ep] = path
                ranks[ep] = rank
    return found


def is_pack(episodes):
    """True when the archive held subtitles of two or more different episodes."""
    return len(episodes or {}) >= 2


def next_episode(episodes, season, episode):
    """Key of the episode after (season, episode) among `episodes`: the next number of the same season, else the first
    episode of the next season; None when the pack has none."""
    try:
        season, episode = int(season), int(episode)
    except (TypeError, ValueError):
        return None
    if (season, episode + 1) in episodes:
        return (season, episode + 1)
    later = sorted(k for k in episodes if k[0] == season + 1)
    if later and later[0][1] <= 1:
        return later[0]
    return None


def _show_key(info):
    info = info or {}
    # the show's title first: an episode's own imdb_id can differ from one episode to the next, the show's name does not
    ident = re.sub(r'[^0-9a-z\u0590-\u05ff]+', '', (info.get('tvshow') or info.get('title') or '').lower())
    return ident or (info.get('imdb_id') or info.get('tmdb_id') or '').strip()


def _dir():
    try:
        from resources.lib.core.cache.profile_dir import profile_subdir
        return profile_subdir('season_pack')
    except Exception:
        return None


def _load_index(folder):
    try:
        with open(os.path.join(folder, _INDEX_NAME), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index(folder, data):
    now = time.time()
    keep = {}
    for k, v in sorted(data.items(), key=lambda kv: -float((kv[1] or {}).get('ts') or 0)):
        try:
            if (now - float(v.get('ts') or 0) < _TTL_S and os.path.isfile(v.get('path') or '')
                    and len(keep) < _KEEP_ENTRIES):
                keep[k] = v
        except Exception:
            continue
    for fn in os.listdir(folder):
        if fn != _INDEX_NAME and os.path.join(folder, fn) not in {v['path'] for v in keep.values()}:
            try:
                os.remove(os.path.join(folder, fn))
            except OSError:
                pass
    tmp = os.path.join(folder, _INDEX_NAME + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(keep, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(folder, _INDEX_NAME))


def remember_next(info, language, path, release, folder=None):
    """Keep the next episode's subtitle file for `info`'s show (the PLAYING episode's info) in the index. Only a
    Hebrew file is offered back as a local row (it needs no translation), so any other language is not remembered here
    (the download cache entry still serves it). Returns the stored path or ''."""
    try:
        if 'hebrew' not in (language or '').lower() and (language or '').lower() not in ('he', 'heb', 'iw'):
            return ''
        folder = folder or _dir()
        if not folder or not path or not os.path.isfile(path) or os.path.getsize(path) > _MAX_FILE_BYTES:
            return ''
        ep = episode_of(path)
        show = _show_key(info)
        if not ep or not show:
            return ''
        key = '{0}|{1}'.format(show, tag(*ep))
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, re.sub(r'[^0-9A-Za-z]+', '_', key) + '.srt')
        shutil.copyfile(path, dest)
        data = _load_index(folder)
        data[key] = {'path': dest, 'language': 'he', 'release': os.path.basename(release or path), 'ts': time.time()}
        _save_index(folder, data)
        return dest
    except Exception as e:
        _log('remember_next failed: {0}'.format(e), 'DEBUG')
        return ''


def lookup_for(info, folder=None):
    """{'path', 'language', 'release'} of the file kept for the episode of `info` (the one about to play), or None."""
    try:
        t = playing_tag(info)
        show = _show_key(info)
        folder = folder or _dir()
        if not t or not show or not folder:
            return None
        entry = _load_index(folder).get('{0}|{1}'.format(show, t))
        if entry and os.path.isfile(entry.get('path') or ''):
            return entry
    except Exception:
        pass
    return None


def handle_download(sub_file, sub_folder, info, language='', cache_store=None, folder=None):
    """Called by the bridge right after a provider download. `sub_file` is what the provider handed back, `sub_folder`
    the (freshly emptied) download folder the archive was unpacked into, `info` the playing video's info,
    `cache_store(path, tag)` stores a file in the download cache under the episode-tagged stem.
    Returns {'file': the file to deliver, 'tag': the playing episode's tag, 'next': path or ''} for a pack, None for
    anything that is not one (the caller then does what it always did). Never raises."""
    try:
        playing = playing_tag(info)
        if not playing:
            return None
        episodes = scan_folder(sub_folder, playing_release(info))
        if not is_pack(episodes):
            return None
        season, episode = int(info.get('season')), int(info.get('episode'))
        mine = episodes.get((season, episode))
        if not mine:
            _log('pack of {0} episodes has no file for {1}: the provider\'s own pick is kept'.format(
                len(episodes), playing))
            return None
        out = {'file': mine, 'tag': playing, 'next': ''}
        if cache_store:
            cache_store(mine, playing)
        nxt = next_episode(episodes, season, episode)
        if nxt:
            ntag = tag(*nxt)
            if cache_store:
                cache_store(episodes[nxt], ntag)
            out['next'] = episodes[nxt]
            nxt_info = dict(info, season=str(nxt[0]), episode=str(nxt[1]))
            remember_next(nxt_info, language, episodes[nxt], os.path.basename(episodes[nxt]), folder=folder)
            _log('pack of {0} episodes: {1} delivered, {2} kept for the next episode'.format(len(episodes), playing, ntag))
        else:
            _log('pack of {0} episodes: {1} delivered, no next episode in it'.format(len(episodes), playing))
        return out
    except Exception as e:
        _log('handle_download failed: {0}'.format(e), 'WARNING')
        return None
