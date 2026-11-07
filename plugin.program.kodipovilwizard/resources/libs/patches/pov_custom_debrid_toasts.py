# File: plugin.program.kodipovilwizard/resources/lib/patches/pov_custom_debrid_toasts.py

import os
import sys
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

WINDOW_PROP = 'kodipovilai.debrid_status_shown'

SERVICES = (
    {
        'name': 'Real-Debrid',
        'title': 'Real-Debrid',
        'prefix': 'rd',
        'enabled': 'rd.enabled',
        'connected': ('rd.username', 'rd.token', 'rd.refresh'),
        'expires': 'rd.expires',
        'module': 'realdebrid_api',
        'class': 'RealDebridAPI',
        'icon': 'realdebrid.png',
    },
    {
        'name': 'TorBox',
        'title': 'TorBox',
        'prefix': 'tb',
        'enabled': 'tb.enabled',
        'connected': ('tb.account_id', 'tb.token'),
        'expires': 'tb.expires',
        'module': 'torbox_api',
        'class': 'TorBoxAPI',
        'icon': 'torbox.png',
    },
    {
        'name': 'Premiumize',
        'title': 'Premiumize',
        'prefix': 'pm',
        'enabled': 'pm.enabled',
        'connected': ('pm.account_id', 'pm.token'),
        'expires': 'pm.expires',
        'module': 'premiumize_api',
        'class': 'PremiumizeAPI',
        'icon': 'premiumize.png',
    },
    {
        'name': 'AllDebrid',
        'title': 'AllDebrid',
        'prefix': 'ad',
        'enabled': 'ad.enabled',
        'connected': ('ad.account_id', 'ad.token'),
        'expires': 'ad.expires',
        'module': 'alldebrid_api',
        'class': 'AllDebridAPI',
        'icon': 'alldebrid.png',
    },
    {
        'name': 'Offcloud',
        'title': 'Offcloud',
        'prefix': 'oc',
        'enabled': 'oc.enabled',
        'connected': ('oc.account_id', 'oc.token'),
        'expires': 'oc.expires',
        'module': 'offcloud_api',
        'class': 'OffcloudAPI',
        'icon': 'offcloud.png',
    },
)

_REFUSAL_TEXT = {
    'AUTH_MISSING_APIKEY': 'החשבון לא מחובר',
    'AUTH_BAD_APIKEY': 'המפתח אינו תקף -- צריך לחבר מחדש',
    'AUTH_BLOCKED': 'הגישה חסומה',
    'AUTH_USER_BANNED': 'החשבון מושעה',
    'MUST_BE_PREMIUM': 'החשבון אינו פרימיום',
}

_UNKNOWN_CODE_TEXT = 'החשבון נדחה'


def _get_pov_addon():
    try:
        return xbmcaddon.Addon('plugin.video.pov')
    except Exception:
        return None

def _get_setting(addon, key, default=''):
    try:
        value = addon.getSetting(key)
        return value if value is not None else default
    except Exception:
        return default

def _get_pov_lib_path():
    try:
        return xbmcvfs.translatePath('special://home/addons/plugin.video.pov/resources/lib')
    except Exception:
        return ''

def _get_media_icon(filename):
    try:
        return xbmcvfs.translatePath(f'special://home/addons/plugin.video.pov/resources/skins/Default/media/{filename}')
    except Exception:
        return None

def _import_client(service):
    """POV's debrid client, from whichever package THIS POV keeps it in."""
    last = None
    for pkg in ('indexers', 'debrids'):
        try:
            return __import__('%s.%s' % (pkg, service['module']),
                              fromlist=[service['class']])
        except ImportError as exc:
            last = exc
    raise last if last is not None else ImportError(service['module'])

def _get_days_remaining(service):
    lib_path = _get_pov_lib_path()
    if not lib_path or not os.path.isdir(lib_path):
        return None

    inserted = False
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
        inserted = True

    try:
        module = _import_client(service)
        cls = getattr(module, service['class'])
        return cls().days_remaining()
    except Exception as exc:
        xbmc.log(f"[POV Wizard] {service['name']} status lookup failed: {exc}", xbmc.LOGWARNING)
        return None
    finally:
        if inserted:
            try:
                sys.path.remove(lib_path)
            except ValueError:
                pass

def _refusal(service):
    lib_path = _get_pov_lib_path()
    if not lib_path or not os.path.isdir(lib_path):
        return None
    inserted = False
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
        inserted = True
    try:
        module = _import_client(service)
        info = getattr(module, service['class'])().account_info()
        if not isinstance(info, dict) or info.get('status') != 'error':
            return None
        err = info.get('error') or {}
        if not isinstance(err, dict):
            err = {}
        code = (err.get('code') or '').strip()
        message = (err.get('message') or info.get('message') or '').strip()
        if code:
            return (code, message)
        return None
    except Exception:
        return None
    finally:
        if inserted:
            try:
                sys.path.remove(lib_path)
            except ValueError:
                pass

def _refusal_message(service, code, message):
    known = _REFUSAL_TEXT.get(code)
    detail = known or message or code
    if not known and code:
        detail = f"{_UNKNOWN_CODE_TEXT} ({code})"
    return f"[B]{service['name']}: [COLOR red]{detail}[/COLOR][/B]"

def _get_threshold(addon, service):
    raw = _get_setting(addon, service['expires'], '0')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return max(0, value)

def _is_connected(addon, service):
    if _get_setting(addon, service['enabled'], 'true').lower() != 'true':
        return False
    return any(_get_setting(addon, key) for key in service['connected'])

def _should_show(days, threshold):
    if days is None:
        return False
    return threshold == 0 or days <= threshold

def _build_message(service, days):
    if days > 0:
        status = '[COLOR limegreen]פרימיום[/COLOR]'
        suffix = f' (נותרו {days} ימים)'
    else:
        status = '[COLOR red]לא בתוקף[/COLOR]'
        suffix = ''
    return f"[B]סטטוס מנוי {service['name']}: {status}{suffix}[/B]"

def run(local_vars):
    """
    Background worker that checks POV settings and APIs to fire custom Debrid toasts.
    """
    try:
        window = xbmcgui.Window(10000)
        if window.getProperty(WINDOW_PROP) == '1':
            return

        addon = _get_pov_addon()
        if addon is None:
            return

        queue = []
        for service in SERVICES:
            if not _is_connected(addon, service):
                continue

            days = _get_days_remaining(service)
            if days is None:
                refused = _refusal(service)
                if refused:
                    queue.append((service, refused))
                continue

            threshold = _get_threshold(addon, service)
            if _should_show(days, threshold):
                queue.append((service, days))

        if not queue:
            return

        monitor = xbmc.Monitor()
        if monitor.waitForAbort(2.0):
            return

        shown = 0
        for idx, (service, result) in enumerate(queue):
            if idx and monitor.waitForAbort(5.0):
                return

            if isinstance(result, tuple):
                text = _refusal_message(service, result[0], result[1])
            else:
                text = _build_message(service, result)

            xbmcgui.Dialog().notification(
                heading=service['title'],
                message=text,
                icon=_get_media_icon(service['icon']),
                time=4500,
                sound=True
            )
            shown += 1

        try:
            window.setProperty(WINDOW_PROP, '1')
            xbmc.log(f"[POV Wizard] Displayed {shown} custom startup Debrid notifications.", xbmc.LOGINFO)
        except Exception:
            pass

    except Exception as e:
        xbmc.log(f"[POV Wizard] Error in custom debrid toasts runner: {e}", xbmc.LOGERROR)