"""The one place where the shared startup and router flow meets Profiles.

startup.py and router.py call the functions below and import nothing else from
Profiles. Removing the Profiles feature means deleting this package and the
call sites; no other engine file knows its modules.

Failure policy
--------------
* Startup hooks are FAIL-OPEN: if a Profiles module cannot load, the build
  starts as a master-only build and the reason is logged.
* The router gate is a SAFETY BOUNDARY: a missing age guard raises instead of
  letting a child profile through to build management.
"""
import importlib
import os
import threading

import xbmc

from resources.libs.common.config import CONFIG
from resources.libs.common import logging

PACKAGE = 'resources.libs.profiles.'
# Standalone module: host patches import it by bare name too (see its docstring).
GUARD = PACKAGE + 'profile_age_guard'
HOME_READY = 'kodipovil.profile_home_ready'
GUI_DEFAULTS = 'kodipovil.profile_gui_defaults.xml'
_CHILD_ALLOWED = ('profiles', 'profile_child_open', 'profile_skin')
ROUTES = frozenset(_CHILD_ALLOWED + ('choose_player_style', 'player_settings',
                                     'player_change_source', 'player_episodes'))


def _load(name):
    """Import a Profiles module, or None (logged) if it cannot load."""
    try:
        return importlib.import_module(PACKAGE + name)
    except Exception as exc:
        logging.log('[Profiles] {} unavailable: {}'.format(name, type(exc).__name__),
                    level=xbmc.LOGERROR)
        return None


# ---------------------------------------------------------------- startup.py

def start_guard():
    """Run the profile policy watcher as a thread of the startup service.

    Kodi tells services that it is closing; a script started with RunScript
    never hears it in time and was killed after 5 s each -- 10 s added to every
    exit (QA, Oct 2026). Not a daemon: it keeps this service alive after the
    startup steps finish or exit early.
    """
    def run():
        service = _load('profile_service')
        if service is not None:
            service.run()
    try:
        threading.Thread(target=run, name='POVILProfileGuard').start()
    except Exception as exc:
        logging.log('[Profiles] guard thread failed to start: {}'.format(exc), level=xbmc.LOGWARNING)


def prepare_startup():
    """Repair the ACTIVE profile before startup treats it as a fresh install.

    A native secondary profile does not include build addon_data/favourites.
    """
    # Only Arctic Fuse is patched at runtime; NOX, Estuary and FENtastic ship
    # the profile UI in their own source.
    af3 = _load('af3_skin')
    if af3 is not None:
        try:
            af3.patch()
        except Exception as exc:
            logging.log('[Profiles] AF3 patch failed: {}'.format(type(exc).__name__),
                        level=xbmc.LOGERROR)
    profiles = _load('build_profiles')
    if profiles is None:
        return
    try:
        profiles.repair_active()
        if not profiles.prepare_first_login():
            logging.log('[Profiles] add-on registration incomplete; retrying next startup',
                        level=xbmc.LOGWARNING)
    except Exception as exc:
        logging.log('[Profiles] active repair failed: {}'.format(type(exc).__name__),
                    level=xbmc.LOGERROR)


def complete_home():
    """Verify a secondary profile's live home. False: stop startup here.

    The engine surface used is fresh_install.apply_live_defaults(defaults_path),
    fresh_install.live_favourites_ready() and build_skin.refresh_home(). The
    master profile never enters the completion branch.
    """
    profiles = _load('build_profiles')
    if profiles is None:
        return True
    ready = os.path.join(CONFIG.USERDATA, HOME_READY)
    try:
        profiles.enable_login_screen()
        profiles.prepare_inactive()
        if not profiles.is_master():
            from resources.libs import build_skin, fresh_install
            from resources.libs.profiles import profile_store
            defaults = os.path.join(CONFIG.USERDATA, GUI_DEFAULTS)
            if os.path.isfile(defaults):
                done = (fresh_install.apply_live_defaults(defaults_path=defaults) and
                        build_skin.refresh_home() and fresh_install.live_favourites_ready())
                if done:
                    profile_store.write_missing(CONFIG.USERDATA, HOME_READY, b'1')
                    os.remove(defaults)
            elif not os.path.isfile(ready):
                if build_skin.refresh_home() and fresh_install.live_favourites_ready():
                    profile_store.write_missing(CONFIG.USERDATA, HOME_READY, b'1')
    except Exception as exc:
        logging.log('[Profiles] login repair failed: {}'.format(type(exc).__name__),
                    level=xbmc.LOGERROR)
    try:
        pending = not profiles.is_master() and not os.path.isfile(ready)
    except Exception:
        pending = False
    if pending:
        # Keep normal boot dialogs deferred until the live home is verified.
        logging.log('[Profiles] home completion pending; deferring dialogs', level=xbmc.LOGINFO)
        return False
    return True


def notifications_allowed():
    """Build notifications are not shown inside a child profile."""
    try:
        return importlib.import_module(GUARD).active_policy() is None
    except Exception as exc:
        logging.log('[Profiles] age guard unavailable: {}'.format(type(exc).__name__),
                    level=xbmc.LOGERROR)
        return True


# ---------------------------------------------------------------- router.py

def deny_in_child_profile(mode, handle):
    """True (after telling the user) when a child profile asks for build management."""
    guard = importlib.import_module(GUARD)  # a missing guard must stop the router
    if guard.active_policy() is None or mode in _CHILD_ALLOWED:
        return False
    import xbmcgui
    import xbmcplugin
    xbmcgui.Dialog().ok('פרופיל ילדים', 'ניהול הבילד זמין במשתמש הראשי.')
    if handle >= 0:
        xbmcplugin.endOfDirectory(handle, succeeded=False)
    return True


def dispatch(mode, params, handle):
    """Run one of ROUTES."""
    if mode == 'profiles':
        import xbmcplugin
        if handle >= 0:
            xbmcplugin.endOfDirectory(handle, succeeded=False)
        _load('build_profiles').show()
    elif mode == 'profile_child_open':
        _load('build_profiles').open_child_content(params.get('kind'))
    elif mode == 'profile_skin':
        _load('build_profiles').choose_skin()
    else:
        styles = _load('player_styles')
        {'choose_player_style': styles.choose, 'player_settings': styles.settings,
         'player_change_source': styles.change_source, 'player_episodes': styles.episodes}[mode]()