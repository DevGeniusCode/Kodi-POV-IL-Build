"""The "תוספים וסקינים אופציונליים" screen: every optional add-on and skin in one place.

    mode=optional   the list: the optional add-ons (Umbrella, Otaku) and the on-demand skins

An add-on that is installed shows a status line instead of an install button. The skin rows and what a tap
does are skins/skin_manager.py (mode=skinaction); the installs are addons/umbrella.py and addons/otaku.py.
"""
import xbmc

from resources.libs.common import directory
from resources.libs.common.config import CONFIG

# (add-on id, name, one-line description, router action of the install button)
OPTIONAL_ADDONS = (
    ('plugin.video.umbrella', 'Umbrella', 'מקורות וחיפוש נוספים (ניסיוני)', 'install_umbrella'),
    ('plugin.video.otaku', 'Otaku', 'אנימה', 'install_otaku'),
)


def refresh_home_tiles():
    """Rebuild favourites.xml (and reload the skin) so the launcher tile of an add-on that was just installed
    shows up. Never raises."""
    try:
        from resources.libs.patches import pov_visibility_mgr
        pov_visibility_mgr.refresh_favourites()
    except Exception:
        pass


def show():
    from resources.libs.skins import skin_manager
    directory.add_file('[B]תוספים אופציונליים[/B]', themeit=CONFIG.THEME3)
    for addon_id, name, blurb, action in OPTIONAL_ADDONS:
        if xbmc.getCondVisibility('System.HasAddon({0})'.format(addon_id)):
            directory.add_file('[B]{0}[/B] - {1}  [COLOR springgreen](מותקן)[/COLOR]'.format(name, blurb),
                               icon=CONFIG.ICONBUILDS, themeit=CONFIG.THEME3)
        else:
            directory.add_file('התקן [B]{0}[/B] - {1}  [COLOR gray](לא מותקן)[/COLOR]'.format(name, blurb),
                               {'mode': 'install', 'action': action}, icon=CONFIG.ICONBUILDS, themeit=CONFIG.THEME1,
                               description='לחיצה להורדה והתקנה לפי דרישה\n{0}'.format(addon_id))
    directory.add_separator()
    directory.add_file('[B]סקינים[/B]', themeit=CONFIG.THEME3)
    skin_manager.add_rows(xbmc.getSkinDir())
