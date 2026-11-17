# File: plugin.program.kodipovilwizard/resources/lib/patches/pov_debrid_status.py

import sys
import xbmc

def run(local_vars):
    """
    Neutralizes POV's generic debrid-expiry toast so the build's Hebrew, icon-aware toasts
    (pov_custom_debrid_toasts) do not fire twice. SettingsMonitor.__call__ looks
    premAccntNotification up in modules.entry when it starts its threads, so replacing that one
    module attribute (called from service.py right after modules.entry is imported) is enough.
    """
    try:
        entry = sys.modules.get('modules.entry')
        if entry is not None and hasattr(entry, 'premAccntNotification'):
            entry.premAccntNotification = lambda *args, **kwargs: None
            xbmc.log("[POV Wizard] Neutralized upstream Debrid expiry notification.", xbmc.LOGINFO)
        else:
            xbmc.log("[POV Wizard] Warning: modules.entry.premAccntNotification not found; "
                     "it may have been renamed or removed.", xbmc.LOGWARNING)
    except Exception as e:
        xbmc.log(f"[POV Wizard] Error in debrid status dynamic patch: {e}", xbmc.LOGERROR)
