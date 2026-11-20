"""Bring the GUI back when Kodi updated the ACTIVE skin in the background and never reloaded it.

What the log of 19 Nov 2026 (Kodi 21.3, skin.fentastic 1.0.34 -> 1.0.35) shows: Kodi's updater installed the skin the viewer was
looking at. The GUI thread logged "Window Deinit (Home.xml)", "Window Deinit ()" and "Unloaded skin" and then NOTHING more (no
"load new skin", no key handling), while the installer finished 3 s later and Python threads went on. The viewer was left with a frozen
screen. Why Kodi's own reload did not follow is Kodi's business; what we can do is notice and repair it.

recover(skin_id) runs after the update watcher saw the skin's files change and settle (addon_update_watch):
  1. Only for the skin the profile has saved as active (read from guisettings.xml: never ask the GUI thread for it).
  2. Probe the GUI with a timeout: a call to Kodi that is answered within PROBE_TIMEOUT with a real window id is a living GUI
     (Kodi reloaded the skin itself: nothing to do, the usual case). Give Kodi GUI_WAIT_SECONDS to get there.
  3. Otherwise post ReloadSkin() (a builtin from a script thread is only queued, it cannot block), then ActivateWindow(home) if
     there is still no window.
What it cannot do: if the GUI thread is deadlocked (not just waiting for a skin), nothing a script posts is ever processed. The log
line says which of the two it was ("ok", "none" = no window, "hung" = no answer) so the next log settles it.
"""
import time
import xml.etree.ElementTree as ET

from resources.libs.common import gui_probe

GUI_WAIT_SECONDS = 6.0      # Kodi's own reload normally comes within a second or two of the end of the install
AFTER_RELOAD_SECONDS = 8.0  # how long a ReloadSkin() gets before home is activated by hand
WINDOW_INVALID = 9999       # xbmcgui.getCurrentWindowId() with no window at all


def _log(msg, level='INFO'):
    try:
        from resources.libs.host_repairs import kodi_utils
        kodi_utils.log('skin_gui_guard: ' + msg, level=level)
    except Exception:
        pass


def saved_active_skin():
    """The skin saved in the profile's guisettings.xml (None when unreadable)."""
    try:
        import xbmcvfs
        root = ET.parse(xbmcvfs.translatePath('special://profile/guisettings.xml')).getroot()
        setting = root.find(".//setting[@id='lookandfeel.skin']")
        if setting is not None and setting.text:
            return setting.text.strip()
    except Exception:
        pass
    return None


def gui_state():
    """'ok' (a window is up), 'none' (the GUI answers but has no window) or 'hung' (no answer)."""
    try:
        import xbmcgui
    except ImportError:
        return 'ok'
    answered, window_id = gui_probe.ask(xbmcgui.getCurrentWindowId, key='window_id')
    if not answered:
        return 'hung'
    if not window_id or window_id == WINDOW_INVALID:
        return 'none'
    return 'ok'


def recover(skin_id, monitor=None):
    """See the module docstring. Returns a small dict for the watcher's log. Never raises."""
    try:
        if saved_active_skin() != skin_id:
            return {'gui': 'skipped', 'why': 'not the active skin'}
        import xbmc
        monitor = monitor or xbmc.Monitor()
        deadline = time.monotonic() + GUI_WAIT_SECONDS
        state = gui_state()
        while state != 'ok' and time.monotonic() < deadline:
            if monitor.waitForAbort(1.0):
                return {'gui': state, 'aborted': True}
            state = gui_state()
        if state == 'ok':
            return {'gui': 'ok'}
        _log('{0} was updated and the GUI is {1} {2:.0f}s later: posting ReloadSkin()'.format(
            skin_id, state, GUI_WAIT_SECONDS), level='WARNING')
        xbmc.executebuiltin('ReloadSkin()')
        deadline = time.monotonic() + AFTER_RELOAD_SECONDS
        while time.monotonic() < deadline:
            if monitor.waitForAbort(1.0):
                return {'gui': state, 'reloaded': True, 'aborted': True}
            if gui_state() == 'ok':
                return {'gui': 'ok', 'reloaded': True}
        xbmc.executebuiltin('ActivateWindow(home)')
        final = gui_state()
        _log('no window after ReloadSkin(); posted ActivateWindow(home), GUI is now {0}'.format(final), level='WARNING')
        return {'gui': final, 'reloaded': True, 'home': True}
    except Exception as err:
        _log('failed: {0}'.format(err), level='WARNING')
        return {'gui': 'error'}
