"""Umbrella + CocoScrapers: an OPTIONAL add-on pair, installed on demand from their official repositories.

Nothing here is hosted by us or by any third party's pack: the two repository add-ons (repository.umbrella,
repository.cocoscrapers: the developers' own definitions, pointing at their GitHub Pages indexes) ship in this repository's
manifest like repository.otaku, with a checksum. The Wizard's headless installer then resolves plugin.video.umbrella and
script.module.cocoscrapers (and their dependencies) from those repositories, so the user gets the developers' current
releases and the developers stay the update channel.
"""
import os

import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common.config import CONFIG

UMBRELLA_ID = 'plugin.video.umbrella'
COCOSCRAPERS_ID = 'script.module.cocoscrapers'
CONTENT_IDS = (UMBRELLA_ID, COCOSCRAPERS_ID)
REPOSITORY_IDS = ('repository.umbrella', 'repository.cocoscrapers')


def _on_disk(addon_id):
    return os.path.isfile(os.path.join(CONFIG.ADDONS, addon_id, 'addon.xml'))


def is_installed():
    return all(bool(xbmc.getCondVisibility('System.HasAddon({0})'.format(i))) for i in CONTENT_IDS)


def ensure_repositories():
    """Both repository add-ons are on disk. True when they are.

    A fresh install has them (they are manifest add-ons). A device that predates them, or an install that was interrupted,
    gets them here from the verified manifest (sha256-checked), never from anywhere else."""
    missing = [i for i in REPOSITORY_IDS if not _on_disk(i)]
    if not missing:
        return True
    from resources.libs.headless_installer import HeadlessInstaller
    from resources.libs.modular_updater import ModularUpdater
    updater = ModularUpdater(background=True)
    addons = (updater._load_manifest() or {}).get('addons') or {}
    jobs = [dict(addons[i], id=i) for i in missing if i in addons]
    if len(jobs) != len(missing):
        logging.log('[Umbrella] the manifest does not list {0}'.format(
            [i for i in missing if i not in addons]), level=xbmc.LOGERROR)
        return False
    updater._register_and_enable(HeadlessInstaller().install_jobs(jobs))
    return all(_on_disk(i) for i in REPOSITORY_IDS)


def ensure_umbrella_installed():
    """Install Umbrella + CocoScrapers (and their dependency tree) from the official repositories. Idempotent, never raises.
    True once both are installed."""
    try:
        if is_installed():
            return True
        if not ensure_repositories():
            return False
        from resources.libs.modular_updater import ModularUpdater
        # Longer timeout than the content-add-on default: a large dependency tree may need Kodi's native fallback.
        ModularUpdater(background=False).post_install_provisioning(per_addon_timeout=240, ids=list(CONTENT_IDS))
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
