"""Umbrella + CocoScrapers: an OPTIONAL add-on pair, installed on demand from the developers' own repositories.

Nothing here is hosted by us: repository.povil.upstream lists the developers' own indexes (umbrellaplug.github.io, not-coco-joe), so
Kodi's native InstallAddon resolves plugin.video.umbrella and script.module.cocoscrapers (and their dependencies) from them, and
the developers stay the update channel.
"""
import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common.config import CONFIG

UMBRELLA_ID = 'plugin.video.umbrella'
COCOSCRAPERS_ID = 'script.module.cocoscrapers'
CONTENT_IDS = (UMBRELLA_ID, COCOSCRAPERS_ID)


def is_installed():
    return all(bool(xbmc.getCondVisibility('System.HasAddon({0})'.format(i))) for i in CONTENT_IDS)


def ensure_umbrella_installed():
    """Install Umbrella + CocoScrapers (and their dependency tree) through Kodi's native InstallAddon. Idempotent, never raises.
    True once both are installed."""
    try:
        if is_installed():
            return True
        from resources.libs.gui import install_progress
        from resources.libs.provisioning import provisioner
        # Longer timeout than the content-add-on default: a large dependency tree.
        with install_progress.window('מתקין את Umbrella') as view:
            provisioner.provision(ids=list(CONTENT_IDS), per_addon_timeout=240, view=view)
    except Exception as err:
        logging.log('[Umbrella] install failed: {0}'.format(err), level=xbmc.LOGERROR)
    return is_installed()


def install_umbrella_pilot():
    """The install button of the optional add-ons screen (mode=install&action=install_umbrella)."""
    dialog = xbmcgui.Dialog()
    if is_installed():
        dialog.notification(CONFIG.ADDONTITLE, 'Umbrella כבר מותקן', xbmcgui.NOTIFICATION_INFO, 3000)
        return True
    if not dialog.yesno(
            CONFIG.ADDONTITLE,
            '[B]להתקין את [COLOR gold]Umbrella[/COLOR] (ניסיוני)?[/B]\n'
            'תוסף תוכן נוסף שפועל לצד POV, עם חיפוש ומקורות משלו, '
            'ומתעדכן ישירות מהמפתחים שלו. ההתקנה כוללת הורדה מהאינטרנט ועלולה להימשך כמה דקות. '
            'לא משנה שום דבר קיים בבילד.',
            nolabel='[B][COLOR red]ביטול[/COLOR][/B]',
            yeslabel='[B][COLOR springgreen]התקן[/COLOR][/B]'):
        return False
    if not ensure_umbrella_installed():
        dialog.ok(CONFIG.ADDONTITLE, 'התקנת Umbrella לא הושלמה. בדוק את החיבור לרשת ונסה שוב.')
        return False
    xbmc.sleep(500)
    xbmc.executebuiltin('UpdateLocalAddons')
    from resources.libs.addons import optional
    optional.refresh_home_tiles()
    logging.log_notify(
        CONFIG.ADDONTITLE,
        '[COLOR {0}]Umbrella הותקן! זמין תחת תוספים -> הרחבות וידאו[/COLOR]'.format(CONFIG.COLOR1))
    return True
