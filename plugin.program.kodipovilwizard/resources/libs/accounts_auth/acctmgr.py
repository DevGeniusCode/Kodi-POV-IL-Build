"""Account Manager Lite: on-demand pilot install and install-for-everyone."""
import os

import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common.config import CONFIG


# KODI-POV-IL - ACCOUNT MANAGER LITE PILOT (opt-in, on-demand). One place to
# authorise Real-Debrid / Premiumize / AllDebrid / TorBox / OffCloud /
# EasyDebrid / Easynews / Trakt / MDBList, which it then pushes into every
# supported add-on it finds installed -- POV and Umbrella both among them, and
# re-pushed at every Kodi startup, so an add-on installed LATER picks the
# accounts up on the next boot. Ships with script.module.acctvwr (a hard
# dependency of acctmgr, in no repo the build already carries) and with the
# developer's own repository, so from here on he is its update channel, not us.

def _acctmgr_was_removed():
    """True when Account Manager's settings are on disk but the add-on is not.

    Kodi's uninstall removes addons/<id>/ and leaves
    userdata/addon_data/<id>/ alone, so this is "it was here and somebody
    took it away" as distinct from "it was never here".
    """
    try:
        import xbmcvfs
        if os.path.isfile(os.path.join(CONFIG.ADDONS, 'script.module.acctmgr', 'addon.xml')):
            return False
        data = xbmcvfs.translatePath(
            'special://profile/addon_data/script.module.acctmgr')
        if not xbmcvfs.exists(data):
            return False
        dirs, files = xbmcvfs.listdir(data)
        return bool(dirs or files)
    except Exception:
        return False


def ensure_acctmgr_installed():
    """Install Account Manager ON DEMAND via Kodi's native InstallAddon.

    Replaces the legacy static monolithic zip extraction. Once the target repository
    is available, this will safely download and resolve dependencies for
    script.module.acctmgr through Kodi's internal provisioning system.
    """
    if xbmc.getCondVisibility('System.HasAddon(script.module.acctmgr)'):
        return True
    try:
        from resources.libs.modular_updater import ModularUpdater
        ModularUpdater(background=False).post_install_provisioning(
            per_addon_timeout=60, ids=['script.module.acctmgr'])
    except Exception as err:
        logging.log('[Account Manager] native install failed: {0}'.format(err), level=xbmc.LOGERROR)
    return xbmc.getCondVisibility('System.HasAddon(script.module.acctmgr)')


ACCTMGR_AUTO_SETTING = 'acctmgr_auto'
# What the marker records: "this device has had Account Manager put on it by
# us, once". Deliberately NOT the pack version. Keying it on the version looks
# tidier and is wrong: the next time ACCTMGR_PACK_VERSION is bumped, every
# device whose marker holds the old version stops matching and gets a forced
# reinstall -- INCLUDING somebody who removed Account Manager on purpose in
# the meantime, which is the one thing this promises not to do. Keeping it
# current is not this function's job anyway: Account Manager updates itself
# from its developer's own repository, and the wizard menu still has a manual
# reinstall for anyone who wants one.
ACCTMGR_AUTO_DONE = 'installed'


def ensure_acctmgr_for_everyone():
    """Put Account Manager on every device -- existing installs included --
    exactly once.

    Why it stopped being opt-in: the build's "חיבור שירותים" screen now routes
    its debrid and Trakt rows through Account Manager, so one authorisation
    reaches every add-on instead of POV alone. On a device without it those
    same rows quietly fall back to authorising POV only. The screen looks
    identical either way, which is precisely why the difference must not be
    left to chance.

    ONCE per device, recorded in a wizard setting. A user who then uninstalls
    Account Manager on purpose is not fought with at every boot -- that is
    their call, and the screen still works without it.

    Silent when there is nothing to do. Never raises; the caller runs at
    startup and a failure here must not stop the rest of it."""
    try:
        if CONFIG.get_setting(ACCTMGR_AUTO_SETTING) == ACCTMGR_AUTO_DONE:
            return False
        if not CONFIG.get_setting('buildname'):
            return False            # build not installed yet -- too early

        # Check if the user deliberately removed it
        if _acctmgr_was_removed():
            logging.log(
                '[Account Manager] settings from a previous install are here but the '
                'add-on is not; treating that as a deliberate removal and not '
                'installing it again', level=xbmc.LOGINFO)
            CONFIG.set_setting(ACCTMGR_AUTO_SETTING, ACCTMGR_AUTO_DONE)
            return False

        ok = ensure_acctmgr_installed()
        if not ok:
            # No marker: a device that was offline (or where the pack host was
            # down) tries again on the next boot instead of never again.
            logging.log(
                '[Account Manager] auto-install did not complete; will retry '
                'on the next startup', level=xbmc.LOGINFO)
            return False
        CONFIG.set_setting(ACCTMGR_AUTO_SETTING, ACCTMGR_AUTO_DONE)
        xbmc.sleep(500)
        try:
            xbmc.executebuiltin('UpdateLocalAddons')
        except Exception:
            pass
        logging.log('[Account Manager] auto-installed successfully', level=xbmc.LOGINFO)
        return True
    except Exception as e:
        logging.log('[Account Manager] auto-install failed: {0}'.format(e),
                    level=xbmc.LOGERROR)
        return False


def install_acctmgr_pilot():
    """Manual (re)install behind the wizard menu entry. Account Manager now
    arrives by itself on every device (ensure_acctmgr_for_everyone), so this
    is the repair path for somebody who removed it or whose auto-install never
    completed. It changes nothing by itself: installing it does not touch a
    single existing setting, because it only writes an account into an add-on
    once the user has actually authorised that account inside it."""
    dialog = xbmcgui.Dialog()
    yes_pressed = dialog.yesno(
        CONFIG.ADDONTITLE,
        '[B]להתקין מחדש את [COLOR gold]Account Manager[/COLOR]?[/B]\n'
        'מחברים את חשבונות הדבריד פעם אחת במקום אחד, והוא מעביר אותם '
        'לכל התוספים המותקנים - גם POV וגם Umbrella. עד שתחברו חשבון, '
        'שום הגדרה קיימת לא משתנה.',
        nolabel='[B][COLOR red]ביטול[/COLOR][/B]',
        yeslabel='[B][COLOR springgreen]התקן[/COLOR][/B]')
    if not yes_pressed:
        return
    if ensure_acctmgr_installed():
        xbmc.sleep(500)
        try:
            xbmc.executebuiltin('UpdateLocalAddons')
        except Exception:
            pass

        # Record it here too so manual and auto paths leave the same mark
        try:
            CONFIG.set_setting(ACCTMGR_AUTO_SETTING, ACCTMGR_AUTO_DONE)
        except Exception:
            pass

        logging.log_notify(
            CONFIG.ADDONTITLE,
            '[COLOR {0}]הותקן! זמין תחת תוספים -> תוכניות -> '
            'Account Manager[/COLOR]'.format(CONFIG.COLOR1))
