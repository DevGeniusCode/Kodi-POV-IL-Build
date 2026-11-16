"""Checks for a newer official Kodi release (Android / Windows)."""
import xbmcgui

from resources.libs.common import tools
from resources.libs.common.config import CONFIG


##########################################
# KODI-RD-IL - UPDATE CHECK

def parse_version(ver_str):
    """Converts '21.0-Omega' or '21.0.1' into a tuple of integers like (21, 0)."""
    import re
    match = re.search(r'^(\d+(?:\.\d+)+)', ver_str)
    if match:
        return tuple(map(int, match.group(1).split('.')))
    return (0, 0)

def get_current_kodi_version():
    """Extracts the numeric float version (e.g., 21.0) from the running Kodi instance."""
    import re
    import xbmc
    build_version = xbmc.getInfoLabel('System.BuildVersion')
    return parse_version(build_version)

def get_latest_official_kodi_version():
    """Fetches the latest official Kodi release version from the Kodi GitHub API."""
    import json
    import urllib.request as urllib_req

    try:
        url = "https://api.github.com/repos/xbmc/xbmc/releases/latest"
        req = urllib_req.Request(url, headers={'User-Agent': 'Kodi-POV-IL-Wizard'})
        with urllib_req.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            tag_name = data.get('tag_name', '')
            return parse_version(tag_name)
    except Exception as e:
        from resources.libs.common import logging
        logging.log(f"DEBUG | get_latest_official_kodi_version | Failed: {str(e)}")

    return None

def kodi_version_update_check(kodi_version_update_check_manual="false"):
    kodi_version_update_check_manual = True if kodi_version_update_check_manual == "true" else False
    os_type_label = tools.platform().capitalize()
    dialog = xbmcgui.Dialog()

    try:
        current_version = get_current_kodi_version()
        latest_version = get_latest_official_kodi_version()
        current_str = ".".join(map(str, current_version))
        latest_str = ".".join(map(str, latest_version))

        if not latest_version:
            if kodi_version_update_check_manual:
                dialog.ok(f"{CONFIG.ADDONTITLE} ({os_type_label})",
                          "[COLOR yellow][B]לא הצלחנו לבדוק את הגרסה העדכנית.[/B][/COLOR]\nנסה שוב מאוחר יותר.")
            return

        if latest_version > current_version:
            dialog.ok(f"{CONFIG.ADDONTITLE} ({os_type_label})",
                      f'[COLOR yellow][B]קיים עדכון רשמי ל-Kodi![/B][/COLOR]\n\n'
                      f'גרסה נוכחית: [B][COLOR red]{current_str}[/COLOR][/B]\n'
                      f'גרסה מעודכנת: [B][COLOR limegreen]{latest_str}[/COLOR][/B]\n\n'
                      f'הבילד שלנו מותאם לרוץ על Kodi נקי והעדכני ביותר.\n'
                      f'אנא עדכן את קודי דרך החנות הרשמית במכשירך או אתר Kodi.tv.')

        elif kodi_version_update_check_manual:
            dialog.ok(f"{CONFIG.ADDONTITLE} ({os_type_label})",
                      f'[COLOR limegreen][B]קודי (Kodi) מעודכן לגרסה האחרונה![/B][/COLOR]\n\n'
                      f'גרסה נוכחית: [B]{current_str}[/B]')

    except Exception as e:
        from resources.libs.common import logging
        logging.log(f'[kodi_version_update_check] Exception: {str(e)}')
        if kodi_version_update_check_manual:
            dialog.ok(f"{CONFIG.ADDONTITLE} ({os_type_label})", f'התרחשה שגיאה:\n{str(e)}')
