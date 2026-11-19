# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The plugin entry: parse the call (plugin URL or RunScript) and dispatch by action.

import sys
import urllib.parse
try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None
from resources.lib.ai_translation.background import handle_bg_translate_picker, handle_translate_file
from resources.lib.delivery.kodi_player import handle_download
from resources.lib.entry.actions import handle_choose_subs, handle_he_avail
from resources.lib.ui.dialogs import safe_log
from resources.lib.ui.listing import handle_manualsearch, handle_search
from resources.lib.ui.settings_actions.cache import handle_clear_cache, handle_pool_share_cache, handle_pool_share_cache_run, handle_purge_temp
from resources.lib.ui.settings_actions.engine import handle_engine_test
from resources.lib.ui.settings_actions.gemini import handle_connect_gemini, handle_open_aistudio, handle_open_tmdb_notice, handle_show_gemini_usage, handle_test_connection


# action -> handler. The directory actions get Kodi's plugin handle; the RunScript / settings actions get the
# params only. An action in neither table is logged and ends the directory.
HANDLE_ACTIONS = {
    'search': handle_search,
    'manualsearch': handle_manualsearch,
    'download': handle_download,
}
PARAMS_ACTIONS = {
    'open_aistudio': handle_open_aistudio,
    'test_connection': handle_test_connection,
    'connect_gemini': handle_connect_gemini,
    'show_gemini_usage': handle_show_gemini_usage,
    'open_tmdb_notice': handle_open_tmdb_notice,
    'clear_cache': handle_clear_cache,
    'pool_share_cache': handle_pool_share_cache,
    'pool_share_cache_run': handle_pool_share_cache_run,
    'purge_temp': handle_purge_temp,
    'translate_file': handle_translate_file,
    'bg_translate_picker': handle_bg_translate_picker,
    'he_avail': handle_he_avail,
    'engine_test': handle_engine_test,
    'choose_subs': handle_choose_subs,
}


def _parse_query():
    """Pull params from the query string Kodi handed us.

    Two invocation styles share this script:
      plugin: sys.argv = [url, handle, '?action=download&link=...']
      runscript: sys.argv = [path, 'action=test_connection', ...]

    We sniff which one we're in by looking at argv[0] -- plugin
    invocations start with 'plugin://'. For runscript we fold each
    'key=value' arg into the params dict.
    """
    out = {}
    if not sys.argv:
        return out

    argv0 = sys.argv[0] or ''
    if argv0.startswith('plugin://'):
        if len(sys.argv) >= 3:
            q = sys.argv[2] or ''
            if q.startswith('?'):
                q = q[1:]
            for k, v in urllib.parse.parse_qsl(q, keep_blank_values=True):
                out[k] = v
        return out

    # RunScript: each remaining arg is "key=value" (or just "key").
    for a in sys.argv[1:]:
        if not a:
            continue
        if '=' in a:
            k, v = a.split('=', 1)
            out[k.strip()] = v.strip()
        else:
            out[a.strip()] = '1'
    return out


def main():
    if xbmc is None:
        safe_log('default.py invoked outside Kodi -- nothing to do',
                  level='WARNING')
        return

    try:
        handle = int(sys.argv[1]) if len(sys.argv) > 1 else -1
    except (ValueError, TypeError):
        handle = -1

    params = _parse_query()
    action = (params.get('action') or 'search').lower()

    try:
        if action in HANDLE_ACTIONS:
            HANDLE_ACTIONS[action](handle, params)
        elif action in PARAMS_ACTIONS:
            PARAMS_ACTIONS[action](params)
        else:
            safe_log('unknown action: ' + action, level='WARNING')
            if handle >= 0:
                xbmcplugin.endOfDirectory(handle)
    except Exception as e:
        safe_log('main crashed: {0}'.format(e), level='ERROR')
        try:
            if handle >= 0:
                xbmcplugin.endOfDirectory(handle)
        except Exception:
            pass
