# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Repoint Arctic Fuse 3's typed search rows at POV (or Umbrella, see search_provider) instead of the library and
# TMDbHelper, so a typed search returns Hebrew results that play through the add-on's own source scraping.
#
# HOW AF3 3.5 SEARCHES (rewritten in Phase 5; the old design edited shortcuts/generator/data/setup/search_path.xml, a
# file AF3 3.5 no longer has). The skin's search window runs script.skinvariables' run_multisearch. The script searches
# the rows of the `searchwidgets` shortcut node with the typed text, resolving each row's `path` TOKEN through
#
#     skin.arctic.fuse.3/shortcuts/skinvariables-multisearch-paths.json      {"DefaultSearch-Movies": "videodb://...{query}"}
#
# `{query}` is the url-encoded text (`{query_double}` is the double-encoded one TMDbHelper wants). POV reads its query
# through parse_qsl, a single decode, so `{query}` is exactly right.
#
# Our searchwidgets node (written by af3_home_patcher) has four rows whose tokens are DefaultSearch-POVMovies / POVTv /
# POVPeople / POVCollections. This patcher adds those four tokens to the skin's paths file, pointed at the chosen
# provider. Nothing else in the file is touched, an AF3 update that ships a fresh file is re-patched on the next start,
# and a file that already holds the right four values is left alone (no write).
#
# Statuses: 'patched' | 'already_patched' | 'no_af3' | 'no_file' | 'unmatched' (not a JSON object) | 'read_failed'
#           | 'write_failed'.

import json
import os

try:
    import xbmcvfs
except Exception:
    xbmcvfs = None

try:
    from resources.libs.host_repairs import kodi_utils
except Exception:
    kodi_utils = None


AF3_SKIN_ID = 'skin.arctic.fuse.3'
PATHS_REL = ('addons/' + AF3_SKIN_ID + '/shortcuts/skinvariables-multisearch-paths.json')

# The tokens the searchwidgets node rows use, keyed by the row names of search_provider.AF3_PREFIXES.
TOKENS = (
    ('DefaultSearch-POVMovies', 'Movies'),
    ('DefaultSearch-POVTv', 'Tv'),
    ('DefaultSearch-POVPeople', 'People'),
    ('DefaultSearch-POVCollections', 'Collections'),
)

# POV's four, repeated as the fallback for the (impossible in the build, possible in a standalone install) case where
# search_provider is absent.
_POV_FALLBACK = {
    'Movies': 'plugin://plugin.video.pov/?mode=build_movie_list&action=tmdb_movies_search&query=',
    'Tv': 'plugin://plugin.video.pov/?mode=build_tvshow_list&action=tmdb_tv_search&query=',
    'People': 'plugin://plugin.video.pov/?mode=person_search&query=',
    'Collections': ('plugin://plugin.video.pov/?mode=build_movie_list&action=tmdb_movies_search_collections&query='),
}


def _log(msg, level='INFO'):
    if kodi_utils is None:
        return
    try:
        kodi_utils.log('af3_search_pov_patcher: ' + msg, level=level)
    except Exception:
        pass


def _prefixes():
    try:
        from resources.libs.host_repairs import search_provider
        return search_provider.current(), search_provider.af3_prefixes()
    except Exception:
        return 'pov', _POV_FALLBACK


def _paths_file():
    if xbmcvfs is None:
        return ''
    try:
        base = xbmcvfs.translatePath('special://home/')
    except Exception:
        return ''
    return os.path.join(base, *PATHS_REL.split('/'))


def ensure_patched():
    path = _paths_file()
    if not path:
        return 'no_af3'
    if not os.path.isfile(path):
        try:
            addon_xml = xbmcvfs.translatePath('special://home/addons/' + AF3_SKIN_ID + '/addon.xml')
        except Exception:
            return 'no_af3'
        return 'no_file' if os.path.isfile(addon_xml) else 'no_af3'
    try:
        with open(path, 'r', encoding='utf-8-sig') as handle:
            data = json.load(handle)
    except Exception as err:
        _log('read failed: {0}'.format(err), level='WARNING')
        return 'read_failed'
    if not isinstance(data, dict):
        _log('multisearch paths file is not a JSON object; leaving it alone', level='WARNING')
        return 'unmatched'

    provider, prefixes = _prefixes()
    wanted = {token: prefixes[row] + '{query}' for token, row in TOKENS}
    if all(data.get(token) == value for token, value in wanted.items()):
        return 'already_patched'
    data.update(wanted)

    tmp = path + '.povil-tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(data, handle, indent=4, ensure_ascii=False)
            handle.write('\n')
        os.replace(tmp, path)
    except OSError as err:
        try:
            os.remove(tmp)
        except OSError:
            pass
        _log('write failed: {0}'.format(err), level='WARNING')
        return 'write_failed'
    _log('added the {0} search tokens to skinvariables-multisearch-paths.json'.format(provider))
    return 'patched'
