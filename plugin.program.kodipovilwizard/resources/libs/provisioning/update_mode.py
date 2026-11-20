"""Kodi's add-on update mode (`general.addonupdates`) and the "update window" flag. No Wizard imports: startup.py calls this first.

Kodi 21 (xbmc/addons/AddonSystemSettings.h): 0 = AUTO_UPDATES_ON, 1 = AUTO_UPDATES_NOTIFY, 2 = AUTO_UPDATES_NEVER. Kodi reads the mode
when a repository check job COMPLETES (RepositoryUpdater::OnJobComplete): ON installs every update the check found, NOTIFY only shows a
toast, NEVER shows nothing. The build keeps NOTIFY, so Kodi never replaces the running Wizard, a running service or the active skin at
boot (the 20 Nov 2026 freeze). provisioning/update_window.py flips the mode to ON for one controlled window and puts it back.

A change through JSON-RPC is NOT written to guisettings.xml at once (Kodi saves its settings when it exits), so a crash or a force-kill
inside a window normally leaves NOTIFY on disk. What can still leave ON behind (a clean exit inside a window) is repaired by
settle_at_start(): startup.py runs it before anything else.

The window flag is a Home-window property carrying a monotonic stamp that update_window refreshes every few seconds while the mode is
ON. It lives as long as Kodi: a new Wizard instance started by a Kodi update inside the window sees it and leaves the mode alone.

The PARK FILE (special://temp/addon_update_window.flag) says the same to the services that poll the GUI (script.fentastic.helper,
service.subtitles.kodipovilai): it is written by beat() and removed by end_window(), and while it is fresh those services make no GUI
call. Kodi stops a service it updates, and a stop that finds one INSIDE a GUI call wedged the GUI for good (20 Nov 2026 log, 19:10:31:
the FENtastic helper "didn't stop in 5 seconds", the screen froze and the active skin's install hung on UnloadSkin). A file, not a
property: reading a property takes the GUI lock. The services ignore a file not refreshed for 90 s (a Kodi killed inside a window);
settle_at_start() removes a leftover. The file name is shared with those add-ons: keep it byte-identical.
"""
import json
import os
import time

import xbmc

ON = 0
NOTIFY = 1
NEVER = 2

SETTING = 'general.addonupdates'
WINDOW_PROPERTY = 'povil.update_window'
WINDOW_STALE_SECONDS = 90.0     # a flag not refreshed for this long belongs to a runner that died
PARK_FILE_NAME = 'addon_update_window.flag'     # special://temp/<name>, read by the GUI-polling services (see above)
PARK_SECONDS = 1.5              # after the park file appears, before the mode goes to ON: the services finish their current GUI call


def _log(msg, level=None):
    try:
        xbmc.log('[plugin.program.kodipovilwizard] update_mode: ' + msg, level if level is not None else xbmc.LOGINFO)
    except Exception:
        pass


def get_mode():
    """The current mode (0 / 1 / 2), None when Kodi does not answer. JSON-RPC: it needs no GUI lock."""
    try:
        query = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.GetSettingValue', 'params': {'setting': SETTING}})
        value = (json.loads(xbmc.executeJSONRPC(query) or '{}').get('result') or {}).get('value')
        return int(value) if value is not None else None
    except Exception as err:
        _log('read failed: {0}'.format(err), xbmc.LOGWARNING)
        return None


def set_mode(value):
    """Set the mode; True when Kodi accepted it. Never raises."""
    try:
        query = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.SetSettingValue',
                            'params': {'setting': SETTING, 'value': int(value)}})
        response = json.loads(xbmc.executeJSONRPC(query) or '{}')
        accepted = 'error' not in response and response.get('result') is True
    except Exception as err:
        _log('write {0} failed: {1}'.format(value, err), xbmc.LOGWARNING)
        return False
    if not accepted:
        _log('Kodi did not accept {0}'.format(value), xbmc.LOGWARNING)
    return accepted


# ---- The window flag ------------------------------------------------------------------------------------------------------
def _home():
    import xbmcgui
    return xbmcgui.Window(10000)


def _park_file():
    import xbmcvfs
    return os.path.join(xbmcvfs.translatePath('special://temp/'), PARK_FILE_NAME)


def _touch_park_file():
    try:
        with open(_park_file(), 'w') as handle:
            handle.write(str(time.time()))
    except Exception as err:
        _log('park file not written: {0}'.format(err), xbmc.LOGWARNING)


def _remove_park_file():
    try:
        os.remove(_park_file())
    except FileNotFoundError:
        pass
    except Exception as err:
        _log('park file not removed: {0}'.format(err), xbmc.LOGWARNING)


def beat():
    """Say "a window is open" (and keep saying it: call every few seconds while the mode is ON). The park file first: it needs no
    GUI lock, so it stays fresh even if the property write below has to wait."""
    _touch_park_file()
    try:
        _home().setProperty(WINDOW_PROPERTY, str(time.monotonic()))
    except Exception:
        pass


def end_window():
    _remove_park_file()
    try:
        _home().clearProperty(WINDOW_PROPERTY)
    except Exception:
        pass


def window_open():
    """True while an update window is open in this Kodi session (a flag no longer refreshed is a dead runner's)."""
    try:
        stamp = float(_home().getProperty(WINDOW_PROPERTY) or 'x')
    except Exception:
        return False
    return 0 <= time.monotonic() - stamp < WINDOW_STALE_SECONDS


def settle_at_start():
    """First thing startup.py does: an ON left behind (clean exit inside a window, a mode changed by hand) goes back to NOTIFY,
    unless a window is open right now (a Kodi update of the Wizard restarted startup.py in the middle of one). NEVER is the
    owner's choice (Maintenance -> Toggle Addon Updates) and stays."""
    try:
        if window_open():
            _log('an update window is open: the mode stays')
            return
        _remove_park_file()     # a leftover of a Kodi that was killed inside a window
        if get_mode() == ON:
            _log('found "install updates automatically" at start: back to NOTIFY (a window did not close)', xbmc.LOGWARNING)
            set_mode(NOTIFY)
    except Exception as err:
        _log('start check failed: {0}'.format(err), xbmc.LOGWARNING)
