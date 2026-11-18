"""One verified completion path for startup and a manual full installation."""
import importlib.util
import json
import os
import re
import xml.etree.ElementTree as ET

import xbmc
import xbmcgui
import xbmcvfs
from resources.libs.common.config import CONFIG
from resources.libs.common import logging, tools

PENDING = 'kodipovil.modular_install_started'


def begin():
    os.makedirs(CONFIG.USERDATA, exist_ok=True)
    with open(os.path.join(CONFIG.USERDATA, PENDING), 'w', encoding='utf-8') as fh:
        fh.write('1\n')


def provision(attempts=3, progress=None, view=None):
    """Resume only missing work on both manual and automatic installation.

    Keep a small local diagnostic with addon IDs, never request URLs/accounts.
    Completion still requires the verified provisioning marker.
    """
    from resources.libs.provisioning import provisioner
    monitor = xbmc.Monitor()
    issues = []
    for attempt in range(attempts):
        if monitor.abortRequested():
            break

        try:
            ok, issues = provisioner.run_fresh_install(progress, view)
            if ok and provisioner.is_provisioned():
                try:
                    os.remove(os.path.join(CONFIG.USERDATA, 'kodipovil.install_failure.json'))
                except OSError:
                    pass
                return True, []
            issues = list(issues)
        except Exception as exc:
            logging.log('[Fresh install] provisioning attempt failed: {}'.format(exc),
                        level=xbmc.LOGERROR)
            issues = ['installation interrupted']
        if attempt + 1 < attempts:
            xbmcgui.Dialog().notification(
                CONFIG.ADDONTITLE, 'משלים רכיבים חסרים — ניסיון נוסף', time=4000)
            if monitor.waitForAbort(3):
                break
    issues = issues or ['installation interrupted']
    try:
        with open(os.path.join(CONFIG.USERDATA, 'kodipovil.install_failure.json'),
                  'w', encoding='utf-8') as fh:
            json.dump({'issues': issues, 'attempts': attempt + 1}, fh)
    except OSError:
        pass
    return False, issues


def failure_message(issues):
    """Explain failed prerequisites instead of prescribing reinstall loops."""
    if 'repositories' in issues or 'config unavailable' in issues:
        detail = 'לא ניתן להתחבר למאגרי הבילד. בדוק את החיבור לרשת.'
    else:
        detail = 'רכיבים שלא הושלמו:\n' + '\n'.join(issues[:6])
    return ('ההתקנה לא הושלמה.\n' + detail +
            '\nניתן לנסות השלמת התקנה שוב בלי למחוק את Kodi.\n'
            'אם התקלה נמשכת, שלח לוג דרך הוויזרד.')


def _rpc(method, params):
    reply = json.loads(xbmc.executeJSONRPC(json.dumps(
        {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
    if 'error' in reply or 'result' not in reply:
        raise RuntimeError('Kodi setting API failed: ' + method)
    return reply['result']


def _accept_initial_skin_confirmation():
    from resources.libs.profiles.build_skin import confirm_requested_skin
    return confirm_requested_skin(CONFIG.DEFAULT_BUILD_SKIN)


def apply_live_defaults(defaults_path=None):
    """Persist fresh GUI defaults through Kodi, which owns the in-memory copy.

    Set the skin last, after all assets and dependencies have been verified.
    Only the exact first-install skin-retention question is accepted. A
    reverted skin is not reported as a successfully activated build.
    """
    root = ET.parse(defaults_path or os.path.join(CONFIG.USERDATA, 'kodipovil.fresh_gui_defaults.xml')).getroot()
    target_skin = root.findtext("setting[@id='lookandfeel.skin']", CONFIG.DEFAULT_BUILD_SKIN)
    monitor = xbmc.Monitor()
    for setting in root.findall('setting'):
        key = setting.get('id', '')
        if key.startswith('services.') or key == 'addons.unknownsources':
            continue
        if key == 'lookandfeel.skin':
            continue
        if setting.get('default') == 'true' or setting.text is None:
            continue
        if monitor.abortRequested():
            return False
        # A setting this Kodi build does not expose is skipped, not fatal.
        try:
            current = _rpc('Settings.GetSettingValue', {'setting': key})['value']
        except (RuntimeError, KeyError):
            continue
        text = setting.text
        if isinstance(current, bool):
            value = text.lower() == 'true'
        elif isinstance(current, int):
            value = int(text)
        elif isinstance(current, float):
            value = float(text)
        elif isinstance(current, list):
            values = [v.strip() for v in re.split(r'[|,]', text) if v.strip()]
            value = [int(v) if re.fullmatch(r'-?\d+', v) else v for v in values]
        else:
            value = text
        if current != value and _rpc('Settings.SetSettingValue',
                                      {'setting': key, 'value': value}) is not True:
            raise RuntimeError('Fresh setting was rejected: ' + key)
    from resources.libs.profiles import build_skin
    if not build_skin.activate(target_skin):
        return False
    # A manual completion runs inside the Wizard menu. That is not evidence
    # of a failed home: show the verified home instead of requiring a restart.
    if not xbmc.getCondVisibility('Window.IsActive(home)'):
        xbmc.executebuiltin('ActivateWindow(Home)')
    for _ in range(5):
        if xbmc.getCondVisibility('Window.IsActive(home)'):
            return True
        if monitor.waitForAbort(0.2):
            return False
    return False


def live_favourites_ready():
    """Kodi caches favourites; an XML write alone does not refresh the home."""
    expected = {node.get('name') for node in ET.parse(
        os.path.join(CONFIG.USERDATA, 'favourites.xml')).getroot().findall('favourite')}
    actual = {item.get('title') for item in (_rpc('Favourites.GetFavourites', {}).get('favourites') or [])}
    if expected and expected.issubset(actual):
        return True
    # Loading the already active secondary profile stops Kodi services without
    # restarting them (native LoadProfile returns early for this index).
    # The master prepares its complete favourites before the real switch.
    master = xbmcvfs.translatePath('special://masterprofile/')
    if os.path.normcase(os.path.realpath(CONFIG.USERDATA)) != os.path.normcase(os.path.realpath(master)):
        return False
    # The subtitle service can add home tiles after the first refresh.
    # Allow at most two reloads of the active profile, then leave the pending
    # marker in place. This never closes Kodi or loops indefinitely.
    marker = os.path.join(CONFIG.USERDATA, 'kodipovil.fresh_profile_reload')
    try:
        with open(marker, encoding='utf-8') as fh:
            reloads = int(fh.read().strip())
    except (OSError, ValueError):
        reloads = 0
    if reloads < 2:
        with open(marker, 'w', encoding='utf-8') as fh:
            fh.write(str(reloads + 1))
        profile = xbmc.getInfoLabel('System.ProfileName')
        xbmc.executebuiltin('LoadProfile("{0}")'.format(profile.replace('"', '')))
    return False


def seed_fresh_home_shortcuts():
    """Seed local service shortcuts before certifying Kodi's favourites cache.

    The Tonight shortcut used to be written by the subtitle service, which starts
    asynchronously after activation, so it could arrive after finalization and
    miss the first home. Its writer is part of the Wizard now (resources/libs/tonight).
    """
    from resources.libs.tonight import entrypoints
    entrypoints.ensure()


def finalize():
    """Return success only after media, patches and the live home are ready."""
    from resources.libs.provisioning import addon_state, provisioner
    from resources.libs.patch_engine import PatchEngine
    from resources.libs import db
    if not provisioner.is_provisioned():
        return False
    required = tuple(provisioner.CORE_PROVISION_IDS) + (
        CONFIG.DEFAULT_BUILD_SKIN, 'script.fentastic.helper',
        'plugin.program.orderfavourites-hebrew', 'service.subtitles.kodipovilai')
    db.addon_database(list(required), 1, True)
    xbmc.executebuiltin('UpdateLocalAddons')
    monitor = xbmc.Monitor()
    for _ in range(20):
        missing = [aid for aid in required
                   if not xbmc.getCondVisibility('System.HasAddon({0})'.format(aid))]
        if not missing:
            break
        for aid in missing:
            addon_state.enable_addon(aid)
        if monitor.waitForAbort(1):
            return False
    else:
        raise RuntimeError('Fresh runtime dependencies unavailable')
    mi_file = os.path.join(CONFIG.ADDONS, 'plugin.program.orderfavourites-hebrew',
                           'resources', 'lib', 'media_installer.py')
    spec = importlib.util.spec_from_file_location('povil_install_media', mi_file)
    media = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(media)
    if not media.install_and_verify_global_media_assets():
        raise RuntimeError('Fresh media or favourites are incomplete')
    stats = PatchEngine().run()
    if any(stats.get(key, 0) for key in ('failed', 'missing', 'anchor_missing',
                                       'malformed')):
        raise RuntimeError('Fresh source patches are incomplete')
    db.fix_metas()
    if not apply_live_defaults():
        logging.log('[FreshInstall] home not confirmed; retaining completion marker',
                    level=xbmc.LOGWARNING)
        return False
    # Refresh favourites for the activated skin, without extra provider calls.
    if not media.install_and_verify_global_media_assets():
        raise RuntimeError('Fresh skin favourites are incomplete')
    seed_fresh_home_shortcuts()
    if not live_favourites_ready():
        return False
    version = CONFIG.BUILDVERSION_DEFAULT
    values = {'buildname': CONFIG.BUILDNAME_DEFAULT, 'installed': 'true',
              'buildversion': version, 'latestversion': version,
              'nextbuildcheck': tools.get_date(days=CONFIG.UPDATECHECK, formatted=True),
              'extract': '100', 'errors': '0',
              'fresh_build_auto_install_done': version,
              # HEAD behaviour kept: first-launch notification windows.
              'buildtheme': '',
              'notedismiss': 'false',
              'build_skin_switch_notifcation_dismiss': 'false'}
    for key, value in values.items():
        CONFIG.set_setting(key, value)
    CONFIG.BUILDNAME = CONFIG.BUILDNAME_DEFAULT
    CONFIG.BUILDVERSION = CONFIG.BUILDLATEST = version
    CONFIG.INSTALLED = 'true'
    pending = os.path.join(CONFIG.USERDATA, PENDING)
    if os.path.isfile(pending):
        os.remove(pending)
    for name in ('kodipovil.fresh_profile_reload', 'kodipovil.fresh_gui_defaults.xml'):
        path = os.path.join(CONFIG.USERDATA, name)
        if os.path.isfile(path):
            os.remove(path)
    logging.log('[FreshInstall] verified media, patches and active home', level=xbmc.LOGINFO)
    return True