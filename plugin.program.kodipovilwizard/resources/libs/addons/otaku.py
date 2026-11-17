"""Otaku (anime): an OPTIONAL add-on, installed on demand from the Wizard's optional add-ons screen.

Not provisioned by a fresh install (modular_updater.PROVISION_IDS no longer lists it). Its baked settings
(userdata/addon_data/plugin.video.otaku) are seeded by the config pack either way, so it starts configured.
Resolved headlessly from repository.otaku (kept in the manifest) like the other content add-ons.
"""
import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common.config import CONFIG

OTAKU_ID = 'plugin.video.otaku'


def is_installed():
    return bool(xbmc.getCondVisibility('System.HasAddon({0})'.format(OTAKU_ID)))


def install_otaku():
    """Install Otaku (+ its dependency tree) on demand. Idempotent; never raises. True once it is installed."""
    dialog = xbmcgui.Dialog()
    try:
        if is_installed():
            dialog.notification(CONFIG.ADDONTITLE, 'Otaku כבר מותקן', xbmcgui.NOTIFICATION_INFO, 3000)
            return True
        if not dialog.yesno(
                CONFIG.ADDONTITLE,
                '[B]להתקין את [COLOR gold]Otaku[/COLOR] (אנימה)?[/B]\n'
                'תוסף אנימה נפרד שמתעדכן ישירות מהמפתחים שלו. ההתקנה כוללת הורדה מהאינטרנט '
                'ועלולה להימשך כמה דקות. הוא מוכן לשימוש מיד, עם הגדרות הבילד.',
                nolabel='[B][COLOR red]ביטול[/COLOR][/B]',
                yeslabel='[B][COLOR springgreen]התקן[/COLOR][/B]'):
            return False
        from resources.libs.modular_updater import ModularUpdater
        # Longer timeout than the content-add-on default: Otaku pulls a large dependency tree.
        ModularUpdater(background=False).post_install_provisioning(per_addon_timeout=240, ids=[OTAKU_ID])
    except Exception as err:
        logging.log('[Otaku] install failed: {0}'.format(err), level=xbmc.LOGERROR)
    if not is_installed():
        dialog.ok(CONFIG.ADDONTITLE, 'התקנת Otaku לא הושלמה. בדוק את החיבור לרשת ונסה שוב.')
        return False
    from resources.libs.addons import optional
    optional.refresh_home_tiles()
    logging.log_notify(CONFIG.ADDONTITLE, '[COLOR {0}]Otaku הותקן! אפשר להפעיל אותו מהמועדפים או מתוספים -> הרחבות וידאו[/COLOR]'.format(CONFIG.COLOR1))
    return True
