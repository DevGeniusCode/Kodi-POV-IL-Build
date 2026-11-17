"""The build's selectable skins: ONE table for the Switch Skin picker, the Skin Manager and the provisioning rules.

PROVISIONING RULE (owner decision, Phase 5): the default skin (`CONFIG.DEFAULT_BUILD_SKIN`, fed by uservar.py) is the only
skin a fresh install or the background updater downloads. Every other skin is ON DEMAND: it is downloaded only when the
user installs it from the Skin Manager / Switch Skin, and afterwards it is kept current like any installed add-on.
Code that seeds, patches or configures a skin must act only on a skin that `is_installed` (README section 8).

Pure data and two disk checks; nothing here imports Kodi's GUI, so the modular updater and the profiles can use it.
"""
import os
from collections import namedtuple

from resources.libs.common.config import CONFIG

Skin = namedtuple('Skin', 'id name blurb source floor')

# source: 'manifest' = a verified zip of the build's modular manifest; 'repository' = Kodi's native InstallAddon from a
# third-party repository (Arctic Fuse 3 and its dependencies). floor: the lowest manifest version the build accepts.
SKINS = (
    Skin('skin.fentastic', 'FENtastic', 'יפהפה', 'manifest', '0'),
    Skin('skin.estuary', 'Estuary', 'מראה פשוט עם כפתורים', 'manifest', '0'),
    Skin('skin.arctic.fuse.3', 'Arctic Fuse 3', 'מודרני (ניסיוני)', 'repository', '0'),
    Skin('skin.povil.nox', 'NOX', 'עברית מלאה (ניסיוני)', 'manifest', '1.0.17'),
    Skin('skin.povil.onyx', 'Onyx', 'יוקרתי, מימין לשמאל (ניסיוני)', 'manifest', '1.0.0'),
)


def default_skin():
    return CONFIG.DEFAULT_BUILD_SKIN


def ids():
    """Every catalog skin id, in catalog order."""
    return tuple(skin.id for skin in SKINS)


def find(skin_id):
    return next((skin for skin in SKINS if skin.id == skin_id), None)


def on_demand_ids():
    """Every catalog skin except the default one: never downloaded by fresh install or the background updater."""
    return frozenset(skin.id for skin in SKINS if skin.id != default_skin())


def is_installed(skin_id):
    """The build's own copy is on disk (Kodi's stock Estuary lives outside the add-ons folder and does not count)."""
    return os.path.isfile(os.path.join(CONFIG.ADDONS, skin_id, 'addon.xml'))


def blocks_seeding(relative):
    """True for a path under `addon_data/<skin>/` of an ON-DEMAND skin that is not installed: the Wizard never seeds
    data for a skin that is not there (it is seeded when the skin is installed and switched to). The default skin is
    never blocked. `relative` is relative to userdata (or to a profile folder)."""
    parts = relative.replace('\\', '/').split('/')
    return (len(parts) >= 3 and parts[0] == 'addon_data' and parts[1] in on_demand_ids()
            and not is_installed(parts[1]))


def picker_label(skin):
    return 'סקין {0} - {1}'.format(skin.name, skin.blurb)
