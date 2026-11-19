# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# A named sub-folder of the add-on's profile folder, created on demand (the engine caches).

import os


def profile_subdir(name):
    """<profile>/<name>, created when missing; None when the profile cannot be resolved."""
    try:
        import xbmcvfs
        from resources.lib.core import addon as core_addon
        base = xbmcvfs.translatePath(
            core_addon.addon().getAddonInfo('profile'))
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            os.makedirs(d)
        return d
    except Exception:
        return None
