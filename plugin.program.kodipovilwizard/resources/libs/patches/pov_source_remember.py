# File: plugin.program.kodipovilwizard/resources/libs/patches/pov_source_remember.py

import os
import json
import xbmc
import xbmcvfs
import xbmcgui
import xbmcaddon

_MARK = '[B][COLOR FFFFD700]«« נצפה לאחרונה »»[/COLOR][/B]  ·  '
_OLD_MARKS = (
    '⭐ ', '« נצפה לאחרונה » ',
    '[B][COLOR FFFFD700]« נצפה לאחרונה »[/COLOR][/B] · ',
    '[B][COLOR FFD700]«« נצפה לאחרונה »»[/COLOR][/B]  ·  '
)

def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[Wizard: Source Remember] ' + msg, level)

def _enabled():
    try:
        wiz = xbmcaddon.Addon('plugin.program.kodipovilwizard')
        choice = (wiz.getSetting('pov_remember_source') or '').strip().lower()
        return choice == 'true'
    except Exception:
        return False

def _dir():
    return xbmcvfs.translatePath(
        'special://profile/addon_data/plugin.program.kodipovilwizard/source_memory/'
    )

def _key(sources_self):
    meta = getattr(sources_self, 'meta', None) or {}
    media_id = str(getattr(sources_self, 'tmdb_id', '') or meta.get('imdb_id') or '')
    media_type = getattr(sources_self, 'media_type', '') or 'movie'
    if not media_id:
        return None
    season = getattr(sources_self, 'season', '') or 0
    episode = getattr(sources_self, 'episode', '') or 0
    return '{0}_{1}_s{2}_e{3}'.format(media_type, media_id, season, episode)

def _norm(x):
    return (x or '').strip().lower()

def _get_record(sources_self):
    key = _key(sources_self)
    if not key:
        return None
    path = os.path.join(_dir(), key + '.json')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.loads(f.read())
    except Exception as e:
        _log('Error reading record: ' + str(e), xbmc.LOGWARNING)
        return None

def _match_index(results, rec):
    rhash = _norm(rec.get('hash'))
    if rhash:
        for i, it in enumerate(results):
            if _norm(it.get('hash')) == rhash:
                return i
    rq = _norm(rec.get('quality'))
    rprov = _norm(rec.get('provider'))
    if rq and rprov:
        for i, it in enumerate(results):
            if 'Uncached' in (it.get('cache_provider') or ''):
                continue
            if (_norm(it.get('quality')) == rq and
                _norm(it.get('scrape_provider') or it.get('provider')) == rprov):
                return i
    return None

def run_capture(sources_self, item, link):
    """
    Hook 1: Stashes window properties for external modules and saves the selected source.
    """
    try:
        win = xbmcgui.Window(10000)
        name = item.get('name', '') or item.get('URLName', '') or ''
        win.setProperty('subs.player_filename', name)
        win.setProperty('pov_picked_source_name', name)
        win.setProperty('pov_picked_source_url', link or '')
    except Exception as e:
        _log('Failed setting Window properties: ' + str(e), xbmc.LOGWARNING)

    if not _enabled():
        return
    try:
        key = _key(sources_self)
        if not key:
            return
        rec = {
            'name': item.get('name', ''),
            'hash': item.get('hash', ''),
            'quality': item.get('quality', ''),
            'provider': item.get('scrape_provider') or item.get('provider', ''),
            'debrid': item.get('debrid', ''),
            'release_title': item.get('release_title', ''),
            'size': item.get('size', 0)
        }
        d = _dir()
        if not os.path.isdir(d):
            os.makedirs(d)

        file_path = os.path.join(d, key + '.json')
        tmp = file_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False, indent=2))
        os.replace(tmp, file_path)
        _log('Captured picked source successfully: ' + key)
    except Exception as e:
        _log('Capture error: ' + str(e), xbmc.LOGERROR)

def run_reorder(sources_self, results):
    """
    Hook 2: Reorders the results list, applying markers and pinning the remembered source.
    """
    if not _enabled():
        return False
    try:
        rec = _get_record(sources_self)
        if not rec:
            return False

        idx = _match_index(results, rec)
        if idx is None:
            return False

        item = results.pop(idx)

        try:
            fld = 'display_name' if item.get('display_name') else 'URLName'
            nm = item.get(fld) or ''
            for _m in (_MARK,) + _OLD_MARKS:
                if nm.startswith(_m):
                    nm = nm[len(_m):]
            if nm:
                item[fld] = _MARK + nm
        except Exception:
            pass

        item['_pin_top'] = True
        results.insert(0, item)
        _log('Remembered source moved to top, marked, and pinned.')
        return True
    except Exception as e:
        _log('Reorder error: ' + str(e), xbmc.LOGERROR)
        return False