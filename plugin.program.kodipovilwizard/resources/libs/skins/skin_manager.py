"""The Skin Manager: browse the build's skins, install one on demand, switch between installed skins (the rows are also part
of the "optional add-ons and skins" screen, addons/optional.py).

    mode=skinmanager                 the list: every catalog skin with its state (active / installed / not installed)
    mode=skinaction&skin=<skin id>   what a tap on a row does: switch (installed), install then offer to switch (not installed)

The skins and the rule "only the default skin ships with the build, the rest are installed on demand" live in
skin_catalog.py; the install and the switch themselves are skin_packs.ensure_skin_installed and switcher.switch_to.
"""
import xbmc
import xbmcgui

from resources.libs.common import directory
from resources.libs.common.config import CONFIG
from resources.libs.skins import skin_catalog

ACTIVE = 'active'
INSTALLED = 'installed'
AVAILABLE = 'available'


def state_of(skin_id, active_id):
    """Pure: which row state a skin shows."""
    if skin_id == active_id:
        return ACTIVE
    return INSTALLED if skin_catalog.is_installed(skin_id) else AVAILABLE


def show():
    active_id = xbmc.getSkinDir()
    current = skin_catalog.find(active_id)
    directory.add_file('הסקין הפעיל: [COLOR gold][B]{0}[/B][/COLOR]'.format(current.name if current else active_id),
                       themeit=CONFIG.THEME3, fanart=CONFIG.SKINS_BG)
    directory.add_separator(fanart=CONFIG.SKINS_BG)
    add_rows(active_id)


def add_rows(active_id):
    for skin in skin_catalog.SKINS:
        state = state_of(skin.id, active_id)
        if state == ACTIVE:
            tag, hint = '[COLOR springgreen](פעיל)[/COLOR]', 'הסקין הפעיל כרגע'
        elif state == INSTALLED:
            tag, hint = '[COLOR springgreen](מותקן)[/COLOR]', 'לחיצה להחלפה לסקין הזה'
        else:
            tag, hint = '[COLOR gray](לא מותקן)[/COLOR]', 'לחיצה להורדה והתקנה לפי דרישה'
        default_mark = '  [COLOR gray]ברירת מחדל[/COLOR]' if skin.id == skin_catalog.default_skin() else ''
        directory.add_file('[B]{0}[/B] - {1}  {2}{3}'.format(skin.name, skin.blurb, tag, default_mark),
                           {'mode': 'skinaction', 'skin': skin.id}, icon=CONFIG.ICONBUILDS, themeit=CONFIG.THEME1,
                           description='{0}\n{1}'.format(hint, skin.id), fanart=CONFIG.SKINS_BG)


def act(skin_id):
    """A tap on a row. Never touches a skin that is not in the catalog."""
    skin = skin_catalog.find(skin_id)
    if skin is None:
        return
    from resources.libs.skins import switcher
    state = state_of(skin.id, xbmc.getSkinDir())
    dialog = xbmcgui.Dialog()
    if state == ACTIVE:
        dialog.notification(CONFIG.ADDONTITLE, 'הסקין {0} כבר פעיל'.format(skin.name), xbmcgui.NOTIFICATION_INFO, 3000)
        return
    if state == INSTALLED:
        switcher.switch_to(skin.id, confirm=True)
        return
    if not dialog.yesno(CONFIG.ADDONTITLE, 'הסקין {0} אינו מותקן.\nלהוריד ולהתקין אותו עכשיו? (הורדה מהאינטרנט)'.format(skin.name)):
        return
    from resources.libs.skins.skin_packs import ensure_skin_installed
    if not ensure_skin_installed(skin.id):
        dialog.ok(CONFIG.ADDONTITLE, 'התקנת {0} לא הושלמה. בדוק את החיבור לרשת ונסה שוב.'.format(skin.name))
        return
    xbmc.executebuiltin('Container.Refresh()')
    if dialog.yesno(CONFIG.ADDONTITLE, '{0} הותקן.\nלהחליף אליו עכשיו?'.format(skin.name)):
        switcher.switch_to(skin.id, confirm=False)
