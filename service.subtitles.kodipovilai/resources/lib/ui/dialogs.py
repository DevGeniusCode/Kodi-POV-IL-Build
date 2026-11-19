# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Shared pieces of the dialogs: the brand title (read lazily) and the safe log call.

try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None


def dialogs_title():
    """The brand title of every dialog (the single constant lives in sub_utils.DIALOG_TITLE;
    imported lazily so a bad import path cannot stop the plugin from registering)."""
    from default import ADDON_ID
    try:
        from resources.lib.core.kodi import DIALOG_TITLE
        return DIALOG_TITLE
    except Exception:
        return ADDON_ID


def safe_log(msg, level='INFO'):
    from default import ADDON_ID
    try:
        from resources.lib.core import kodi
        kodi.log(msg, level=level)
    except Exception:
        try:
            if xbmc:
                xbmc.log('[{0}] {1}'.format(ADDON_ID, msg), xbmc.LOGINFO)
        except Exception:
            pass
