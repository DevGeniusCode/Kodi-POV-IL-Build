"""Umbrella + CocoScrapers: on-demand pilot install and install-for-everyone."""
import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common.config import CONFIG
from resources.libs.packs.pack_installer import NOX_PACK_BASE_URL
from resources.libs.packs.pack_installer import _addon_on_disk
from resources.libs.packs.pack_installer import _ensure_packs_installed


# KODI-POV-IL - UMBRELLA PILOT (opt-in, on-demand). Umbrella + CocoScrapers
# plus BOTH of their official repository addons, so once installed they keep
# updating straight from their developers -- the same trust model POV has via
# repository.kodifitzwell. Nothing here runs unless the user explicitly picks
# the wizard menu entry; no tile, no search wiring, no change for anyone else.
# Reuses NOX_PACK_BASE_URL: it is the same dist/ release folder, just a
# different zip in it -- there is only one pack host for this build.
UMBRELLA_PACK_VERSION = '6.7.85'
UMBRELLA_PACKS = [
    {
        'name': 'Umbrella + CocoScrapers',
        'url': '{0}/Kodi-POV-IL-Umbrella-pack.zip'.format(NOX_PACK_BASE_URL),
        'filename': 'umbrella_pack.zip',
        'sentinel': 'special://home/addons/plugin.video.umbrella/addon.xml',
        'expected_version': UMBRELLA_PACK_VERSION,
        'addon_ids': [
            'plugin.video.umbrella',
            'script.module.cocoscrapers',
            'repository.umbrella',
            'repository.cocoscrapers',
        ],
    },
]


def ensure_umbrella_installed():
    """Download + extract the Umbrella pilot pack on demand (same
    battle-tested path as the NOX/AcctMgr packs, including the Addons-DB
    registration that makes Kodi actually see the new addons)."""
    return _ensure_packs_installed(
        UMBRELLA_PACKS,
        '[COLOR {0}][B]מוריד את Umbrella ותלויות[/B][/COLOR]'.format(
            CONFIG.COLOR2),
        '[COLOR {0}][B]Umbrella מוכן לשימוש[/B][/COLOR]'.format(
            CONFIG.COLOR1))


# KODI-POV-IL - UMBRELLA FOR EVERYONE. Same marker-once pattern as Account
# Manager above; see ensure_acctmgr_for_everyone for the reasoning behind each
# guard, which is identical here.
UMBRELLA_AUTO_SETTING = 'umbrella_auto'
UMBRELLA_AUTO_DONE = 'installed'


def _umbrella_was_removed():
    """True when Umbrella's settings are on disk but the add-on is not.

    Kodi's uninstall removes addons/<id>/ and leaves
    userdata/addon_data/<id>/ alone, so this is "it was here and somebody
    took it away" as distinct from "it was never here". Deliberately narrow:
    an EMPTY addon_data directory does not count, because Kodi creates one
    the first time almost anything asks for a setting.
    """
    try:
        import xbmcvfs
        if _addon_on_disk('plugin.video.umbrella'):
            return False
        data = xbmcvfs.translatePath(
            'special://profile/addon_data/plugin.video.umbrella')
        if not xbmcvfs.exists(data):
            return False
        dirs, files = xbmcvfs.listdir(data)
        return bool(dirs or files)
    except Exception:
        return False


def ensure_umbrella_for_everyone():
    """Put Umbrella and CocoScrapers on every device, existing installs
    included, exactly once.

    Why it stopped being a pilot: half the build already assumes it. The home
    screen has Umbrella tiles, the search wiring has an Umbrella branch, the
    account manager pushes debrid accounts into it, and a dozen patchers in
    the AI add-on exist only to make it behave in Hebrew. On a device without
    it, every one of those quietly does nothing -- and the screen looks the
    same either way, which is exactly why the difference must not be left to
    whether somebody found a menu entry.

    ONCE per device, recorded in a wizard setting. Somebody who then removes
    Umbrella on purpose is not fought with at every boot.

    THE PACK CARRIES ITS OWN REPOSITORIES (repository.umbrella and
    repository.cocoscrapers), so from the moment this runs the developers are
    the update channel, not us -- which is the point. We are not taking on
    shipping Umbrella releases; we are making sure the first one is there.

    Silent when there is nothing to do: the sentinel + version gate inside
    _ensure_packs_installed turns an already-current install into a file
    check rather than an 11 MB download. Never raises."""
    try:
        if CONFIG.get_setting(UMBRELLA_AUTO_SETTING) == UMBRELLA_AUTO_DONE:
            return False
        if not CONFIG.get_setting('buildname'):
            return False            # build not installed yet -- too early
        # SOMEBODY WHO ALREADY SAID NO. Umbrella has been available behind a
        # menu entry (install_umbrella_pilot) for several releases, and that
        # entry never wrote this setting -- so a user who installed it there
        # and then deliberately removed it looks exactly like a user who never
        # had it, and this function would put it back. That is the one thing
        # the docstring above promises it will not do.
        #
        # What tells them apart is what an uninstall leaves behind: the add-on
        # directory goes, its addon_data does not. Files gone plus settings
        # present is somebody who had it and got rid of it, and the answer is
        # to record that and never ask again -- not to reinstall.
        if _umbrella_was_removed():
            logging.log(
                '[Umbrella] settings from a previous install are here but the '
                'add-on is not; treating that as a deliberate removal and not '
                'installing it again', level=xbmc.LOGINFO)
            CONFIG.set_setting(UMBRELLA_AUTO_SETTING, UMBRELLA_AUTO_DONE)
            return False
        ok = ensure_umbrella_installed()
        if not ok:
            # No marker: a device that was offline (or where the pack host was
            # down) tries again on the next boot instead of never again.
            logging.log(
                '[Umbrella] auto-install did not complete; will retry on the '
                'next startup', level=xbmc.LOGINFO)
            return False
        CONFIG.set_setting(UMBRELLA_AUTO_SETTING, UMBRELLA_AUTO_DONE)
        xbmc.sleep(500)
        try:
            xbmc.executebuiltin('UpdateLocalAddons')
        except Exception:
            pass
        logging.log('[Umbrella] auto-installed {0}'.format(
            UMBRELLA_PACK_VERSION), level=xbmc.LOGINFO)
        return True
    except Exception as e:
        logging.log('[Umbrella] auto-install failed: {0}'.format(e),
                    level=xbmc.LOGERROR)
        return False


def install_umbrella_pilot():
    """Manual (re)install behind the wizard menu entry.

    Umbrella now arrives by itself on every device
    (ensure_umbrella_for_everyone), so this is the repair path for somebody
    who removed it, or whose auto-install never completed because the device
    was offline at the wrong moment. It still touches nothing else: no home
    screen change, no default, no existing setting."""
    dialog = xbmcgui.Dialog()
    yes_pressed = dialog.yesno(
        CONFIG.ADDONTITLE,
        '[B]להתקין את [COLOR gold]Umbrella[/COLOR] (ניסיוני)?[/B]\n'
        'תוסף תוכן נוסף שפועל לצד POV, עם חיפוש ומקורות משלו, '
        'ומתעדכן ישירות מהמפתחים שלו. לא משנה שום דבר קיים בבילד.',
        nolabel='[B][COLOR red]ביטול[/COLOR][/B]',
        yeslabel='[B][COLOR springgreen]התקן[/COLOR][/B]')
    if not yes_pressed:
        return
    if ensure_umbrella_installed():
        xbmc.sleep(500)
        try:
            xbmc.executebuiltin('UpdateLocalAddons')
        except Exception:
            pass
        # RECORDED HERE TOO, so that from now on the automatic install and
        # the manual one leave the same mark. Without it, anybody using this
        # entry stays in the population _umbrella_was_removed has to guess
        # about.
        try:
            CONFIG.set_setting(UMBRELLA_AUTO_SETTING, UMBRELLA_AUTO_DONE)
        except Exception:
            pass
        from resources.libs.addons import optional
        optional.refresh_home_tiles()
        logging.log_notify(
            CONFIG.ADDONTITLE,
            '[COLOR {0}]Umbrella הותקן! זמין תחת תוספים -> הרחבות וידאו'
            '[/COLOR]'.format(CONFIG.COLOR1))
