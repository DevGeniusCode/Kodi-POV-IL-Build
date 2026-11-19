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
from resources.lib.ai_translation.background import _handle_bg_translate_picker, _handle_translate_file
from resources.lib.delivery.kodi_player import _handle_download
from resources.lib.entry.actions import _handle_choose_subs, _handle_he_avail
from resources.lib.ui.dialogs import _safe_log
from resources.lib.ui.listing import _handle_manualsearch, _handle_search
from resources.lib.ui.settings_actions.cache import _handle_clear_cache, _handle_pool_share_cache, _handle_pool_share_cache_run, _handle_purge_temp
from resources.lib.ui.settings_actions.engine import _handle_engine_test
from resources.lib.ui.settings_actions.gemini import _handle_connect_gemini, _handle_open_aistudio, _handle_open_tmdb_notice, _handle_show_gemini_usage, _handle_test_connection


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
        _safe_log('default.py invoked outside Kodi -- nothing to do',
                  level='WARNING')
        return

    try:
        handle = int(sys.argv[1]) if len(sys.argv) > 1 else -1
    except (ValueError, TypeError):
        handle = -1

    params = _parse_query()
    action = (params.get('action') or 'search').lower()

    try:
        if action == 'search':
            _handle_search(handle, params)
        elif action == 'manualsearch':
            _handle_manualsearch(handle, params)
        elif action == 'download':
            _handle_download(handle, params)
        elif action == 'open_aistudio':
            _handle_open_aistudio(params)
        elif action == 'test_connection':
            _handle_test_connection(params)
        elif action == 'connect_gemini':
            _handle_connect_gemini(params)
        elif action == 'show_gemini_usage':
            _handle_show_gemini_usage(params)
        elif action == 'open_tmdb_notice':
            _handle_open_tmdb_notice(params)
        elif action == 'clear_cache':
            _handle_clear_cache(params)
        elif action == 'pool_share_cache':
            _handle_pool_share_cache(params)
        elif action == 'pool_share_cache_run':
            _handle_pool_share_cache_run(params)
        elif action == 'purge_temp':
            _handle_purge_temp(params)
        elif action == 'translate_file':
            _handle_translate_file(params)
        elif action == 'bg_translate_picker':
            _handle_bg_translate_picker(params)
        elif action == 'he_avail':
            _handle_he_avail(params)
        elif action == 'engine_test':
            _handle_engine_test(params)
        elif action == 'choose_subs':
            _handle_choose_subs(params)
        else:
            _safe_log('unknown action: ' + action, level='WARNING')
            if handle >= 0:
                xbmcplugin.endOfDirectory(handle)
    except Exception as e:
        _safe_log('main crashed: {0}'.format(e), level='ERROR')
        try:
            if handle >= 0:
                xbmcplugin.endOfDirectory(handle)
        except Exception:
            pass
