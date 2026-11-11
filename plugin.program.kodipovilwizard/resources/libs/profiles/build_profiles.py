"""Profile cards backed by Kodi's native profile creation, locks and loader."""
import os
import time
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.libs import config_apply
from resources.libs.build_skin import rpc as _rpc, wait_dialogs as _wait_dialogs
from resources.libs.profiles import profile_age_guard, profile_store

WIZARD = 'plugin.program.kodipovilwizard'
# Everything a seed can raise: bad profile folder/POV database, or no retained pack.
SEED_ERRORS = profile_store.SEED_ERRORS + (config_apply.PackUnavailable,)


def _master_path():
    return xbmcvfs.translatePath('special://masterprofile/')


def is_master():
    # JSON-RPC returns a label, not a stable id. Native paths distinguish the
    # master even if someone creates another profile with the same name.
    return (os.path.normcase(os.path.realpath(xbmcvfs.translatePath('special://profile/'))) ==
            os.path.normcase(os.path.realpath(_master_path())))


def _require_master():
    if not is_master():
        xbmcgui.Dialog().ok('פרופילים', 'רק המשתמש הראשי יכול להוסיף ולנהל פרופילים.')
        return False
    return True


def _seed(profile):
    from resources.libs.common.config import CONFIG
    build = dict(buildname=CONFIG.BUILDNAME_DEFAULT, buildversion=CONFIG.BUILDVERSION_DEFAULT)
    # Only the master owns the shared verified pack, so only it may fetch it.
    with config_apply.open_verified_pack(fetch=is_master()) as pack:
        result = profile_store.seed_profile(_master_path(), profile, pack, build,
                                            prepare_login=is_master())
    if is_master():
        from resources.libs.profiles import profile_home
        profile_home.prepare(_master_path(), profile)
    return result


def repair_active():
    """Repair a native-created secondary profile before fresh-install gating."""
    if is_master():
        return
    active = os.path.realpath(xbmcvfs.translatePath('special://profile/'))
    for profile in profile_store.registered_profiles(_master_path()):
        try:
            target = profile_store.profile_path(_master_path(), profile)
        except ValueError:
            continue
        if profile['id'] == 0 or target != active:
            continue
        was_ready = os.path.isfile(os.path.join(active, 'kodipovil.profile_addons_ready'))
        _seed(profile)
        if not was_ready:
            # Addon settings may have been cached before the missing file was
            # seeded. Apply the small wizard bootstrap through Kodi's API.
            addon = xbmcaddon.Addon(WIZARD)
            root = ET.parse(os.path.join(active, 'addon_data', WIZARD, 'settings.xml')).getroot()
            for item in root.findall('setting'):
                if item.get('id') in ('buildname', 'buildversion', 'installed',
                                      'config_applied_version', 'build_skin_switch_notifcation_dismiss'):
                    addon.setSetting(item.get('id'), item.text or '')
        return


def prepare_inactive():
    """Repair unfinished existing profiles before the next native login."""
    if not is_master():
        return
    master = _master_path()
    if not os.path.isfile(os.path.join(master, 'kodipovil.provisioned')):
        return
    for profile in profile_store.registered_profiles(master):
        if profile['id'] == 0:
            continue
        try:
            path = profile_store.profile_path(master, profile)
            if not os.path.isfile(os.path.join(path, 'kodipovil.profile_home_ready')):
                _seed(profile)
        except SEED_ERRORS:
            xbmc.log('[Profiles] inactive home preparation deferred', xbmc.LOGWARNING)


def addon_enabled(ident):
    # The native Addons API exposes the profile-local enabled state.
    try:
        return _rpc('Addons.GetAddonDetails', dict(addonid=ident, properties=['enabled']))['addon']['enabled'] is True
    except (RuntimeError, KeyError, TypeError):
        return False


def prepare_first_login():
    """Register/enable installed build add-ons in the new profile's own DB.

    Kodi creates a separate Addons DB and initially disables third-party
    add-ons. Never copy the master's DB or undo an established user's choices.
    Dependencies are enabled before their consumers through Kodi's API.
    """
    if is_master():
        return True
    active = xbmcvfs.translatePath('special://profile/')
    receipt = os.path.join(active, 'kodipovil.profile_addons_ready')
    if os.path.isfile(receipt):
        return True
    required = (WIZARD, 'plugin.video.pov', 'skin.povil.nox',
                'script.fentastic.helper', 'service.subtitles.kodipovilai',
                'plugin.program.orderfavourites-hebrew')
    addons = xbmcvfs.translatePath('special://home/addons/')
    graph = {}
    for name in os.listdir(addons):
        manifest = os.path.join(addons, name, 'addon.xml')
        if not os.path.isfile(manifest):
            continue
        try:
            root = ET.parse(manifest).getroot()
            if root.get('id') != name:
                continue
            graph[name] = [row.get('addon') for row in root.findall('requires/import')
                           if row.get('optional') != 'true']
        except (OSError, ET.ParseError):
            continue
    # Enable the installed build and its dependency closure, not arbitrary
    # third-party services the master may have intentionally disabled.
    build_addons = ('script.module.acctmgr', 'plugin.video.umbrella',
                    'plugin.video.idanplus', 'plugin.video.youtube',
                    'plugin.video.themoviedb.helper', 'resource.language.he_il',
                    'skin.estuary', 'skin.fentastic', 'repository.kodipovil')
    required += tuple(ident for ident in build_addons if ident in graph)
    if all(addon_enabled(ident) for ident in required):
        profile_store.write_missing(active, 'kodipovil.profile_addons_ready', b'1')
        return True
    xbmc.executebuiltin('UpdateLocalAddons')
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(0.5):
        return False
    done, visiting = set(), set()
    def enable(ident):
        if ident in done or ident not in graph or ident in visiting:
            return
        visiting.add(ident)
        for dependency in graph[ident]:
            enable(dependency)
        visiting.remove(ident)
        try:
            if not addon_enabled(ident):
                _rpc('Addons.SetAddonEnabled', dict(addonid=ident, enabled=True))
        except RuntimeError:
            return  # failed dependencies can be retried on the next pass
        done.add(ident)
    for ident in required:
        enable(ident)
    for _ in range(15):
        if all(addon_enabled(ident) for ident in required):
            profile_store.write_missing(active, 'kodipovil.profile_addons_ready', b'1')
            return True
        for ident in required:
            done.discard(ident)
            enable(ident)
        if monitor.waitForAbort(0.5):
            return False
    return False


def _native_settings():
    monitor = xbmc.Monitor()
    # ActivateWindow while a closing dialog still holds Kodi's modal window is
    # silently refused.
    if not _wait_dialogs():
        return False
    xbmc.executebuiltin('ActivateWindow(10034)')
    deadline = time.monotonic() + 5
    lock_deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if xbmcgui.getCurrentWindowDialogId() in (10103, 10109, 10110):
            # Native profile locks can need more than five seconds of human
            # input. Wait for Kodi's own authorization without answering it.
            if time.monotonic() >= lock_deadline:
                return False
            deadline = time.monotonic() + 5
        elif xbmc.getCondVisibility('Window.IsActive(10034)'):
            return True
        if monitor.waitForAbort(0.1):
            break
    return False


def enable_login_screen():
    """Turn the login screen on once, as the build's default.

    The receipt means a later manual OFF choice is never undone. Kodi's own
    settings page performs the change, so ProfileManager and profiles.xml stay
    consistent.
    """
    receipt = 'kodipovil.login_screen_default'
    master = _master_path()
    if os.path.isfile(os.path.join(master, receipt)):
        return False
    if (not is_master() or xbmc.getCondVisibility('Player.Playing') or
            not xbmc.getCondVisibility('Window.IsActive(home)') or
            xbmcgui.getCurrentWindowDialogId() not in (0, 9999, 10000)):
        return False
    if xbmc.getCondVisibility('System.HasLoginScreen'):
        profile_store.write_missing(master, receipt, b'1')
        return True
    if not _native_settings():
        return False
    # Native control 4 updates both ProfileManager memory and profiles.xml.
    if (is_master() and xbmc.getCondVisibility('Window.IsActive(10034)') and
            xbmcgui.getCurrentWindowDialogId() in (0, 9999, 10000) and
            not xbmc.getCondVisibility('System.HasLoginScreen')):
        xbmc.executebuiltin('SendClick(10034,4)', True)
    ready = xbmc.getCondVisibility('System.HasLoginScreen')
    if ready:
        profile_store.write_missing(master, receipt, b'1')
    xbmc.executebuiltin('ActivateWindow(home)')
    return ready


def copy_native_profile_defaults():
    """Answer only Kodi's two copy-default questions during add_profile()."""
    if not is_master() or xbmcgui.getCurrentWindowDialogId() != 10100:
        return False
    if xbmc.getInfoLabel('Control.GetLabel(1)') != xbmc.getLocalizedString(20058):
        return False
    if xbmc.getInfoLabel('Control.GetLabel(9)') not in (
            xbmc.getLocalizedString(20048), xbmc.getLocalizedString(20071)):
        return False
    if xbmc.getInfoLabel('Control.GetLabel(11)') != xbmc.getLocalizedString(20064):
        return False
    # Kodi ShowForProfile: no=20044 (start fresh), yes=20064 (copy default).
    xbmc.executebuiltin('SendClick(10100,11)', True)
    return True


def choose_skin():
    if xbmc.getCondVisibility('Player.Playing'):
        xbmcgui.Dialog().ok('סקין הפרופיל', 'יש לעצור את הניגון לפני החלפת סקין.')
        return
    if not _wait_dialogs():
        return
    # The same picker/install path as the master includes on-demand AF3.
    # An installed-only menu made secondary profiles look like another build.
    from resources.libs import wizard
    wizard.build_switch_skin()


def add_profile():
    if not _require_master():
        return
    master = _master_path()
    before = profile_store.registered_profiles(master)
    if not os.path.isfile(os.path.join(master, 'kodipovil.provisioned')):
        xbmcgui.Dialog().ok('פרופילים', 'יש להשלים קודם את התקנת הבילד במשתמש הראשי.')
        return
    if config_apply.ensure_verified_pack() is None:
        xbmcgui.Dialog().ok('פרופילים', 'לא ניתן להוריד את ברירות המחדל של הבילד. בדוק את החיבור לרשת ונסה שוב.')
        return
    kind = xbmcgui.Dialog().select('איזה פרופיל להוסיף?', ['פרופיל רגיל', 'פרופיל ילדים עם הגבלת גיל'])
    if kind < 0:
        return
    from resources.libs.profiles import profile_connections
    connections = profile_connections.choose(master)
    if connections is None:
        return
    xbmcgui.Dialog().ok('הוספת פרופיל',
        'בחר שם ושמור את הפרופיל בחלון שייפתח. אפשר להשאיר את תיקיית הפרופיל המוצעת.\n'
        'המועדפים והבילד יתווספו אוטומטית, יחד עם החיבורים שסימנת.')
    if not _native_settings():
        return
    # Nox and Estuary show the profile list only on the second left-hand
    # category. Focus that native category before trying to focus its list.
    xbmc.executebuiltin('SetFocus(9000,1,absolute)')
    if xbmc.Monitor().waitForAbort(0.2):
        return
    xbmc.executebuiltin('SetFocus(2,{},absolute)'.format(len(before)))
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(0.2):
        return
    if (not is_master() or not xbmc.getCondVisibility('Window.IsActive(10034)') or
            not xbmc.getCondVisibility('Control.HasFocus(2)') or
            xbmc.getInfoLabel('Container(2).CurrentItem') != str(len(before) + 1)):
        return
    # SendClick has no SELECT action code and cannot activate a native list
    # item. Action(Select) follows Kodi's actual add-profile path instead.
    xbmc.executebuiltin('Action(Select,10034)')
    deadline = time.monotonic() + 600
    saw_dialog = False
    child_locks = {}
    started = time.monotonic()
    idle_since = None
    while not monitor.abortRequested() and time.monotonic() < deadline:
        after = profile_store.registered_profiles(master)
        new = [p for p in after if p['id'] not in {v['id'] for v in before}]
        dialog = xbmcgui.getCurrentWindowDialogId()
        if dialog not in (0, 9999, 10034, 10000):
            saw_dialog = True
            idle_since = None
            copy_native_profile_defaults()
            if kind == 1:
                from resources.libs.profiles import child_profiles
                child_profiles.preset_native_locks(child_locks)
        elif saw_dialog:
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since > 0.8:
                if not new or not is_master():
                    return
                try:
                    _seed(new[0])
                    profile_connections.copy_selected(master, new[0], connections)
                except SEED_ERRORS:
                    xbmcgui.Dialog().ok('פרופילים', 'לא ניתן להשלים את הפרופיל. חזור למשתמש הראשי ונסה שוב.')
                    return
                xbmc.executebuiltin('ActivateWindow(home)')
                if kind == 1:
                    from resources.libs.profiles import parental_profiles
                    # An unfinished child setup must never become an
                    # unrestricted profile merely because the parent cancels.
                    profile_store.write_missing(profile_store.profile_path(master, new[0]),
                                                profile_age_guard.CHILD_MARKER, b'1')
                    parental_profiles.setup(new[0])
                    policy = profile_age_guard.policy_for(master, profile_store.profile_path(master, new[0]))
                    if not policy or policy.get('blocked'):
                        xbmcgui.Dialog().ok('פרופיל ילדים', 'הפרופיל נוצר, אך הגבלת הגיל עדיין לא הושלמה. השלם את בקרת ההורים מהמשתמש הראשי לפני השימוש.')
                        return
                if xbmcgui.Dialog().yesno('הפרופיל מוכן', 'לעבור עכשיו לפרופיל החדש?'):
                    load_profile(new[0]['name'])
                return
        elif time.monotonic() - started > 3:
            return  # native creation was not opened (skin navigation/lock)
        if monitor.waitForAbort(0.2):
            return


def load_profile(name):
    profiles = _rpc('Profiles.GetProfiles', dict(properties=['thumbnail', 'lockmode']))['profiles']
    if name not in [p['label'] for p in profiles]:
        return
    if name == _rpc('Profiles.GetCurrentProfile')['label']:
        return
    if xbmc.getCondVisibility('Player.Playing') and not xbmcgui.Dialog().yesno(
            'החלפת פרופיל', 'הניגון ייעצר במעבר לפרופיל אחר. להמשיך?'):
        return
    # Seed only as master, before target startup loads addon settings/favourites.
    if is_master():
        for profile in profile_store.registered_profiles(_master_path()):
            if profile['name'] == name and profile['id'] != 0:
                try:
                    profile_store.profile_path(_master_path(), profile)
                except ValueError:
                    break  # native external profiles remain usable as-is
                _seed(profile)
                break
    if not _wait_dialogs():
        return
    from resources.libs import build_skin
    if not build_skin.persist_live_settings():
        xbmcgui.Dialog().notification('החלפת פרופיל', 'שמירת ההגדרות לא הושלמה. נסה שוב.')
        return
    _rpc('Profiles.LoadProfile', dict(profile=name, prompt=True))
    # Profiles.LoadProfile posts a native GUI message. Return immediately:
    # waiting here races Kodi's shutdown of the departing Python services.
    # The skin's bootstrap hook enables startup in the destination profile.


def open_child_content(kind):
    policy = profile_age_guard.active_policy()
    if policy is None:
        return
    from resources.libs.profiles import parental_profiles
    # The patch engine repairs a hook that a host update overwrote and reports
    # whether every parental hook is now present; unfiltered content never opens.
    if policy.get('blocked') or not parental_profiles.hosts_ready():
        xbmcgui.Dialog().ok('פרופיל ילדים', 'הסינון אינו מוכן. עבור למשתמש הראשי להשלמת העדכון או הגדרות הנעילה.')
        return
    routes = {
        'movie': 'mode=build_movie_list&action=tmdb_movies_genres&genre_id=10751&name=סרטים',
        'tv': 'mode=build_tvshow_list&action=tmdb_tv_genres&genre_id=10762&name=סדרות'}
    if kind == 'search':
        choice = xbmcgui.Dialog().select('מה לחפש?', ['סרט', 'סדרה'])
        if choice < 0:
            return
        routes['search'] = 'mode=get_search_term&mediatype=' + ('movie', 'tvshow')[choice]
    if kind not in routes or not _wait_dialogs():
        return
    xbmc.executebuiltin('ActivateWindow(10025,"plugin://plugin.video.pov/?{}",return)'.format(routes[kind]))


class ProfileCards(xbmcgui.WindowXMLDialog):
    def onInit(self):
        current = _rpc('Profiles.GetCurrentProfile')['label']
        self.profiles = _rpc('Profiles.GetProfiles', dict(properties=['thumbnail', 'lockmode']))['profiles']
        self.setProperty('master', 'true' if is_master() else 'false')
        self.setProperty('current', current)
        child = profile_age_guard.active_policy() is not None
        self.setProperty('child', 'true' if child else 'false')
        self.setProperty('settings_label', 'בחירת סקין' if child else 'סקין וחיבורים')
        items = []
        for profile in self.profiles:
            item = xbmcgui.ListItem(label=profile['label'])
            item.setArt({'thumb': profile.get('thumbnail') or 'DefaultUser.png'})
            item.setProperty('status', 'הפרופיל הפעיל' if profile['label'] == current else 'לחץ למעבר')
            items.append(item)
        self.getControl(100).addItems(items)
        self.getControl(100).selectItem(next((i for i, p in enumerate(self.profiles) if p['label'] == current), 0))
        self.setFocusId(100)

    def onClick(self, control):
        if control == 100:
            self.result = ('load', self.profiles[self.getControl(100).getSelectedPosition()]['label'])
        elif control == 201:
            self.result = ('add', None)
        elif control == 202:
            self.result = ('settings', None)
        elif control == 203:
            self.result = ('native', None)
        elif control == 205:
            self.result = ('parental', None)
        elif control != 204:
            return
        self.close()

    def onAction(self, action):
        if action.getId() in (9, 10, 92):
            self.close()


def show():
    home = xbmcgui.Window(10000)
    # A skin reload/profile transition can abort the previous interpreter
    # before its finally block. A leftover property alone cannot lock the
    # picker forever when no dialog is actually open.
    if (home.getProperty('POVIL.ProfilesBusy') and
            xbmcgui.getCurrentWindowDialogId() not in (0, 9999, 10000)):
        return
    home.setProperty('POVIL.ProfilesBusy', 'true')
    try:
        addon = xbmcaddon.Addon(WIZARD)
        dialog = ProfileCards('Profiles.xml', addon.getAddonInfo('path'), 'Default', '1080i')
        dialog.result = None
        dialog.doModal()
        result = dialog.result
        del dialog
        # Native loading stops this interpreter. Release ownership before
        # posting that message, rather than relying on finally after loading.
        home.clearProperty('POVIL.ProfilesBusy')
        if xbmc.Monitor().waitForAbort(0.2) or not result:
            return
        action, name = result
        if action == 'load':
            load_profile(name)
        elif action == 'add':
            add_profile()
        elif action == 'native' and _require_master():
            _native_settings()
        elif action == 'parental':
            from resources.libs.profiles import parental_profiles
            parental_profiles.setup()
        elif action == 'settings':
            if profile_age_guard.active_policy() is not None:
                choose_skin()
                return
            choice = xbmcgui.Dialog().select('הפרופיל הזה', ['בחירת סקין', 'חיבור שירותים', 'הגדרות כתוביות'])
            if choice == 0:
                choose_skin()
            elif choice == 1:
                xbmc.executebuiltin('RunPlugin(plugin://plugin.video.pov/?mode=myservices)')
            elif choice == 2:
                xbmcaddon.Addon('service.subtitles.kodipovilai').openSettings()
    except Exception as exc:
        xbmc.log('[Profiles] operation failed: {}'.format(type(exc).__name__), xbmc.LOGERROR)
        xbmcgui.Dialog().ok('פרופילים', 'לא ניתן להשלים את הפעולה. אפשר לשלוח לוג דרך הוויזרד.')
    finally:
        home.clearProperty('POVIL.ProfilesBusy')