"""Activate a complete build skin through Kodi's live settings API.

Engine-facing (used by fresh_install and the skin switcher, no Profiles
knowledge): SKINS, rpc, enable_skin, confirm_requested_skin, activate,
persist_live_settings, close_switch_menu, wait_dialogs.

Profiles-facing: prepare_active_skin_defaults, prepare_layout, refresh_home and
switch. They import resources.libs.profiles.* lazily; rpc and wait_dialogs are
shared with the Profiles package.
"""
import importlib.util
import json
import os
import threading
import uuid
import xml.etree.ElementTree as ET

import xbmc
import xbmcvfs
import xbmcgui

SKINS = ('skin.estuary', 'skin.fentastic', 'skin.povil.nox', 'skin.arctic.fuse.3')
# Seeded into the active profile when absent (the master receives them with
# its config pack; secondary profiles through profile_store.seed_profile).
LAYOUT_DEFAULTS = ('addon_data/script.fentastic.helper/cpath_cache.db',
                   'addon_data/skin.fentastic/settings.xml',
                   'addon_data/skin.arctic.fuse.3/settings.xml')
_confirm_lock = threading.Lock()


def close_switch_menu():
    """Dismiss our power-menu entry point before opening the skin picker."""
    if not xbmc.getCondVisibility('Window.IsVisible(10111)'):
        return True
    xbmc.executebuiltin('Dialog.Close(10111,true)')
    monitor = xbmc.Monitor()
    for _ in range(20):
        if not xbmc.getCondVisibility('Window.IsVisible(10111)'):
            return True
        if monitor.waitForAbort(0.1):
            return False
    xbmc.log('[POV IL] Skin switch deferred: power menu did not close', xbmc.LOGWARNING)
    return False


def wait_dialogs(timeout=3):
    """Let a just-closed Dialog.ok/select release Kodi's modal window.

    ActivateWindow and profile/skin requests made while a native dialog is
    still closing are silently refused. False if one stays open past timeout.
    """
    import time
    monitor = xbmc.Monitor()
    deadline = time.monotonic() + timeout
    while xbmcgui.getCurrentWindowDialogId() not in (0, 9999, 10000):
        if time.monotonic() >= deadline or monitor.waitForAbort(0.1):
            return False
    return True


def rpc(method, params=None):
    request = dict(jsonrpc='2.0', id=1, method=method)
    if params is not None:
        request['params'] = params
    reply = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if 'error' in reply or 'result' not in reply:
        raise RuntimeError('Build skin API failed: ' + method)
    return reply['result']


def enable_skin(ident, visiting=None):
    """Enable installed dependencies first; never clone an add-on database."""
    visiting = set() if visiting is None else visiting
    if ident in visiting:
        return False
    visiting.add(ident)
    try:
        path = xbmcvfs.translatePath('special://home/addons/' + ident + '/addon.xml')
        if os.path.isfile(path):
            for row in ET.parse(path).getroot().findall('requires/import'):
                dependency = row.get('addon', '')
                # kodi.resource is a native API capability, not an installed
                # add-on returned by JSON-RPC (used by Arctic's font package).
                if (row.get('optional') != 'true' and dependency != 'kodi.resource'
                        and not dependency.startswith('xbmc.')):
                    if not enable_skin(dependency, visiting):
                        return False
        details = rpc('Addons.GetAddonDetails', dict(addonid=ident, properties=['enabled']))['addon']
        if details['enabled'] is not True:
            rpc('Addons.SetAddonEnabled', dict(addonid=ident, enabled=True))
        return rpc('Addons.GetAddonDetails', dict(addonid=ident, properties=['enabled']))['addon']['enabled'] is True
    except (OSError, ET.ParseError, RuntimeError, KeyError) as exc:
        xbmc.log('[POV IL] Skin dependency unavailable: %s (%s)' %
                 (ident, type(exc).__name__), xbmc.LOGWARNING)
        return False
    finally:
        visiting.remove(ident)


def confirm_requested_skin(target):
    """Accept the exact native retention question only during our own request."""
    if target not in SKINS:
        return False
    # The observer thread and activate()'s own loop both call this; only one
    # may inspect and click the dialog at a time.
    if not _confirm_lock.acquire(False):
        return False
    try:
        return _confirm_requested_skin(target)
    finally:
        _confirm_lock.release()


def _confirm_requested_skin(target):
    home = xbmcgui.Window(10000)
    if (home.getProperty('POVIL.RequestedSkin') != target or
            home.getProperty('POVIL.SkinConfirmed') == target or
            xbmcgui.getCurrentWindowDialogId() != 10100 or
            not xbmc.getCondVisibility('Window.IsVisible(yesnodialog)')):
        return False
    try:
        # Kodi's label wrapper returns its Python-side strText, which is empty
        # for native headings. The textbox API reads the real text under the
        # native GUI lock. Discard wrappers before clicking/unloading anything.
        dialog = xbmcgui.Window(10100)
        control = dialog.getControl(9)
        body = control.getText()
        del control, dialog
    except (RuntimeError, AttributeError):
        return False
    if body != xbmc.getLocalizedString(13111):
        return False
    if (xbmcgui.getCurrentWindowDialogId() != 10100 or
            home.getProperty('POVIL.RequestedSkin') != target or
            home.getProperty('POVIL.SkinConfirmed') == target):
        return False
    home.setProperty('POVIL.SkinConfirmed', target)
    xbmc.executebuiltin('SendClick(10100,11)')
    return True


def persist_live_settings():
    """Use Kodi's native save path before a profile unload discards live values."""
    # Settings.SetSettingValue changes memory only in Kodi 21. Skin.SetBool
    # calls CSettings::Save on the GUI thread, preserving all current values.
    xbmc.executebuiltin('Skin.SetBool(POVIL.SettingsCommitted,true)', True)
    try:
        path = xbmcvfs.translatePath('special://profile/guisettings.xml')
        saved = ET.parse(path).getroot().find("setting[@id='lookandfeel.skin']")
        return saved is not None and saved.text == xbmc.getSkinDir()
    except (OSError, ET.ParseError):
        return False


def activate(target):
    if target not in SKINS or not enable_skin(target):
        return False
    monitor = xbmc.Monitor()
    if monitor.abortRequested():
        return False
    changed = xbmc.getSkinDir() != target
    if changed:
        selected = rpc('Settings.GetSettingValue', dict(setting='lookandfeel.skin'))['value']
        if selected == target:
            # Force a genuine change after native loading fell back from a
            # disabled skin, even if settings still name the desired skin.
            if rpc('Settings.SetSettingValue', dict(setting='lookandfeel.skin', value=xbmc.getSkinDir())) is not True:
                return False
        home = xbmcgui.Window(10000)
        home.clearProperty('POVIL.SkinConfirmed')
        home.setProperty('POVIL.RequestedSkin', target)
        finished = threading.Event()
        def observe():
            for _ in range(100):
                if finished.is_set() or monitor.abortRequested():
                    return
                if confirm_requested_skin(target):
                    return
                finished.wait(0.2)
        observer = threading.Thread(target=observe, daemon=True)
        observer.start()
        try:
            if rpc('Settings.SetSettingValue', dict(setting='lookandfeel.skin', value=target)) is not True:
                return False
            # JSON-RPC updates the setting before the GUI processes ReloadSkin.
            # Keep the scoped request alive until the native window acknowledges
            # and closes its exact retention question, then verify the result.
            for _ in range(100):
                if monitor.waitForAbort(0.2):
                    return False
                confirm_requested_skin(target)
                if (home.getProperty('POVIL.SkinConfirmed') == target and xbmc.getSkinDir() == target
                        and not xbmc.getCondVisibility('Window.IsVisible(yesnodialog)')):
                    break
            else:
                return False
        finally:
            finished.set()
            observer.join(1)
            home.clearProperty('POVIL.RequestedSkin')
            home.clearProperty('POVIL.SkinConfirmed')
    return xbmc.getSkinDir() == target and persist_live_settings()


def prepare_active_skin_defaults():
    """Initialize the build's Estuary sidebar once in each native profile."""
    from resources.libs.profiles import child_profiles
    child_changed = child_profiles.sync_active()
    if xbmc.getSkinDir() != 'skin.estuary':
        return child_changed
    if xbmc.getCondVisibility('Skin.HasSetting(POVIL.BuildSidebarReady)'):
        return child_changed
    # The config pack contains no Estuary settings.xml. New native profiles
    # therefore inherit Kodi's unrelated sidebar items unless initialized
    # through the skin API after activation. Keep later user choices intact.
    for name in ('HomeMenuNoMusicButton', 'HomeMenuNoMusicVideoButton',
                 'HomeMenuNoTVButton', 'HomeMenuNoRadioButton',
                 'HomeMenuNoGamesButton', 'HomeMenuNoPicturesButton',
                 'HomeMenuNoVideosButton', 'HomeMenuNoWeatherButton',
                 # The build home is tile based, not the stock library pages.
                 'HomeMenuNoMovieButton', 'HomeMenuNoTVShowButton'):
        xbmc.executebuiltin('Skin.SetBool({})'.format(name), True)
    for name in ('HomeMenuNoFavButton', 'HomeMenuNoProgramsButton'):
        xbmc.executebuiltin('Skin.Reset({})'.format(name), True)
    xbmc.executebuiltin('Skin.SetBool(POVIL.BuildSidebarReady)', True)
    return True


def prepare_layout():
    """Seed shipped layout only when absent; use the active profile's paths."""
    from resources.libs import config_apply
    from resources.libs.profiles import af3_skin, profile_store
    profile = xbmcvfs.translatePath('special://profile/')
    # Arctic Fuse is installed on demand and is not shipped with profile UI.
    af3_skin.patch()
    prepare_active_skin_defaults()
    try:
        with config_apply.open_verified_pack() as pack:
            for name in LAYOUT_DEFAULTS:
                if name in pack:
                    profile_store.write_missing(profile, name, pack.read(name))
    except config_apply.PackUnavailable:
        # Only the heal-if-deleted seeding is skipped: the master got these
        # files with its config pack and secondary profiles at creation.
        xbmc.log('[POV IL] Layout defaults skipped: no verified config pack', xbmc.LOGWARNING)
    if xbmc.getSkinDir() != 'skin.arctic.fuse.3':
        return True
    # Keep the subtitle add-on's resources namespace in its own interpreter.
    key = 'POVIL.BuildSkin.' + uuid.uuid4().hex
    home = xbmcgui.Window(10000)
    home.clearProperty(key)
    xbmc.executebuiltin('RunScript(service.subtitles.kodipovilai,action=prepare_build_skin,request={})'.format(key))
    monitor = xbmc.Monitor()
    try:
        for _ in range(120):
            result = home.getProperty(key)
            if result:
                return result == 'ready'
            if monitor.waitForAbort(0.5):
                return False
        return False
    finally:
        home.clearProperty(key)


def refresh_home():
    """Refresh the active skin's tiles and media without provider requests."""
    path = xbmcvfs.translatePath('special://home/addons/plugin.program.orderfavourites-hebrew/resources/lib/media_installer.py')
    spec = importlib.util.spec_from_file_location('povil_skin_media', path)
    media = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(media)
    if not media.install_and_verify_global_media_assets():
        return False
    if not prepare_layout():
        return False
    xbmc.executebuiltin('ActivateWindow(home)')
    xbmc.executebuiltin('ReloadSkin()')
    return True


def switch(target):
    from resources.libs.profiles import profile_age_guard
    policy = profile_age_guard.active_policy()
    if xbmc.getCondVisibility('Player.Playing') or (policy is not None and policy.get('blocked')):
        return False
    if policy is not None:
        from resources.libs.profiles import af3_skin, child_profiles
        child_profiles.seed_skin_flags(xbmcvfs.translatePath('special://profile/'))
        af3_skin.patch()
    # FENtastic's helper starts at skin activation. Seed its layout first.
    if target == 'skin.fentastic':
        if not prepare_layout():
            return False
    if not activate(target):
        return False
    return refresh_home()