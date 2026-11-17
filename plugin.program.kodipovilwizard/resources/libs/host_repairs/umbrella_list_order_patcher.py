# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Newest-first personal lists and local favourites for Umbrella, without startup API calls.
#
# This is what is left of umbrella_setup_patcher (Phase 5): its settings (External Provider, CocoScrapers providers,
# Umbrella defaults) are baked into userdata, and its source-name publisher is the registry entry umbrella_source_name.
#
# It stays a runtime patcher because the registry cannot express it: the sort calls go into about a dozen
# functions of menus/movies.py and menus/tvshows.py whose anchor LINES repeat (several functions end in the same
# `self.sort()` or `self.list = []`), so each anchor is scoped to its function through the AST, and
# modules/favourites.py needs one line REPLACED ("SELECT * FROM %s" -> "... ORDER BY rowid DESC"). The logic lives in
# patches/pov_mdblist_patch_logic.py (umbrella_personal_sort, personal_mdbl_url).

import os

try:
    import xbmcvfs
except Exception:
    xbmcvfs = None

try:
    from resources.libs.host_repairs import kodi_utils
except Exception:
    kodi_utils = None


def _log(msg, level='INFO'):
    if kodi_utils is None:
        return
    try:
        kodi_utils.log('umbrella_list_order_patcher: ' + msg, level=level)
    except Exception:
        pass


def _personal_order_source(source, filename):
    """Scope each anchor to its real function, and validate before any write."""
    import ast
    marker = '# POVIL_PERSONAL_ORDER_v1'
    if marker in source: return source
    tree = ast.parse(source)
    lines = source.splitlines(True)
    eol = '\r\n' if '\r\n' in source[:4096] else '\n'
    edits = []
    def insert(name, anchor, hook, after=False):
        nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
        if len(nodes) != 1: raise ValueError('ambiguous list function: ' + name)
        node = nodes[0]
        found = [i for i in range(node.lineno - 1, node.end_lineno) if anchor in lines[i]]
        if len(found) != 1: raise ValueError('changed list anchor: ' + name)
        i = found[0] + int(after)
        edits.append((i, hook.replace('\n', eol)))
    if filename in ('movies.py', 'tvshows.py'):
        media = 'movies' if filename == 'movies.py' else 'shows'
        # Both direct directory calls and create_directory=False widget calls.
        for function, fetch, setting in (
                ('traktWatchlist', "self.list = traktsync.fetch_watch_list(", media + '.watchlist'),
                ('traktCollection', "self.list = traktsync.fetch_collection(", media + '.collection'),
                ('get_mdbuser_watchlist', 'self.list = cache.get(mdblist.get_user_watchlist,', media + '.watchlist'),
                ('get_mdbuser_collection', 'self.list = cache.get(mdblist.get_user_collection,', media + '.watchlist')):
            insert(function, fetch, "\t\t\tif _povil_order: _povil_order.umbrella_personal_sort(self, %r, getSetting)\n" % setting, True)
        insert('trakt_userList', 'self.sort() # sort before local pagination',
               "\t\tif _povil_order: _povil_order.umbrella_personal_sort(self, %r, getSetting, url)\n" % media)
        insert('mbd_user_lists', 'list_url = self.mbdlist_list_items % (list_id)',
               "\t\t\t\tif _povil_order: list_url += '&povil_personal=1'\n", True)
        insert('mdb_list_items', 'q = dict(parse_qsl(urlsplit(url).query))',
               "\t\tif _povil_order: url = _povil_order.personal_mdbl_url(url, getSetting('sort.%s.type') in ('', '0'))\n" % media)
    elif filename == 'favourites.py':
        nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'getFavourites']
        if len(nodes) != 1: raise ValueError('changed local favourites')
        node = nodes[0]
        found = [i for i in range(node.lineno - 1, node.end_lineno) if 'SELECT * FROM %s' in lines[i]]
        if len(found) != 1: raise ValueError('changed local favourites query')
        i = found[0]
        lines[i] = lines[i].replace('SELECT * FROM %s', 'SELECT * FROM %s ORDER BY rowid DESC')
    for i, hook in sorted(edits, reverse=True): lines.insert(i, hook)
    # Imports in a host must never depend on resources.lib from another addon:
    # the top-level resources package already belongs to Umbrella here.
    import_at = next((n.lineno - 1 for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))), None)
    if import_at is None: raise ValueError('missing host imports')
    block = (marker + '\ntry:\n'
             '    import sys, xbmcvfs\n'
             "    _povil_path = xbmcvfs.translatePath('special://home/addons/plugin.program.kodipovilwizard/resources/libs/patches/')\n"
             '    if _povil_path not in sys.path: sys.path.append(_povil_path)\n'
             '    import pov_mdblist_patch_logic as _povil_order\n'
             "    if not all(hasattr(_povil_order, name) for name in ('personal_mdbl_url', 'umbrella_personal_sort')): _povil_order = None\n"
             'except Exception: _povil_order = None\n')
    lines.insert(import_at, block.replace('\n', eol))
    result = ''.join(lines)
    compile(result, filename, 'exec')
    return result


def ensure_personal_list_order():
    """Newest-first personal lists and local favourites, without startup API calls."""
    if xbmcvfs is None: return 'not_installed'
    base = xbmcvfs.translatePath('special://home/addons/plugin.video.umbrella/resources/lib/')
    if not os.path.isdir(base): return 'not_installed'
    changes = []
    try:
        for relative in ('menus/movies.py', 'menus/tvshows.py', 'modules/favourites.py'):
            path = os.path.join(base, *relative.split('/'))
            with open(path, encoding='utf-8', newline='') as f: original = f.read()
            result = _personal_order_source(original, relative.split('/')[-1])
            if result != original: changes.append((path, result))
    except (OSError, ValueError, SyntaxError) as e:
        _log('personal list order skipped: ' + str(e), 'WARNING')
        return 'unmatched'
    for path, content in changes:
        tmp = path + '.aitmp'
        try:
            with open(tmp, 'w', encoding='utf-8', newline='') as f: f.write(content)
            os.replace(tmp, path)
        except OSError:
            try: os.remove(tmp)
            except OSError: pass
            return 'write_failed'
    return 'patched' if changes else 'unchanged'
