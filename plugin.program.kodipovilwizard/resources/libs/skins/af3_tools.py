"""Arctic Fuse 3 tools menu and its actions."""
import sys

import xbmc
import xbmcgui
import xbmcplugin

try:
    from urllib.parse import quote_plus
except ImportError:
    from urllib import quote_plus

from resources.libs.common.config import CONFIG


AF3_TOOLS = [
    {
        'id': 'profiles',
        'label': 'מי צופה? — פרופילים',
        'icon': 'DefaultUser.png',
        'builtin': 'RunPlugin(plugin://plugin.program.kodipovilwizard/?mode=profiles)',
    },
    {
        'id': 'connect_services',
        'label': 'חיבור שירותים',
        'icon': 'special://home/media/povil_icons/Connect_Services.png',
        'builtin': 'RunPlugin("plugin://plugin.video.pov/?mode=myservices")',
    },
    {
        'id': 'debrid_notice_settings',
        'label': 'הגדרת התראות מנוי',
        'icon': 'special://home/media/povil_icons/Connect_Services.png',
        'builtin': 'RunScript(service.subtitles.kodipovilai,action=debrid_notice_settings)',
    },
    {
        'id': 'pov',
        'label': 'כניסה ל-POV',
        'icon': 'special://home/media/povil_icons/Logo_POV_IL.png',
        'builtin': 'RunAddon("plugin.video.pov")',
    },
    {
        'id': 'ai_settings',
        'label': 'הגדרות תרגום AI',
        'icon': 'special://home/addons/service.subtitles.kodipovilai/icon.png',
        'builtin': 'Addon.OpenSettings(service.subtitles.kodipovilai)',
    },
    {
        'id': 'quick_update',
        'label': 'עדכון מהיר',
        'icon': 'special://home/media/povil_icons/fast_update_pov_il.png',
        'builtin': 'PlayMedia("plugin://plugin.program.kodipovilwizard/?mode=install&action=quick_update&name=Kodi+POV+IL+-+FENtastic&auto_quick_update=false")',
    },
    {
        'id': 'switch_skin',
        'label': 'החלף סקין',
        'icon': 'special://home/media/povil_icons/wizard_pov_il.png',
        'builtin': 'RunPlugin("plugin://plugin.program.kodipovilwizard/?mode=install&action=build_switch_skin")',
    },
    {
        'id': 'send_log',
        'label': 'שליחת לוג',
        'icon': 'special://home/media/povil_icons/twilight_send_log.png',
        'builtin': 'ActivateWindow(10025,"plugin://plugin.video.pov/?mode=navigator.log_utils&name=Changelog%20%26%20Log%20Utils",return)',
    },
    {
        'id': 'reload_skin',
        'label': 'טעינת סקין מחדש',
        'icon': 'special://skin/extras/icons/refresh.png',
        'builtin': 'ReloadSkin()',
    },
    {
        'id': 'settings',
        'label': 'הגדרות Kodi',
        'icon': 'special://skin/extras/icons/settings.png',
        'builtin': 'ActivateWindow(settings)',
    },
    {
        'id': 'quit',
        'label': 'יציאה',
        'icon': 'special://skin/extras/icons/power.png',
        'builtin': 'Quit()',
    },
]


def _plugin_url(action, **kwargs):
    query = ['mode=install', 'action={0}'.format(quote_plus(action))]
    for key, value in kwargs.items():
        query.append('{0}={1}'.format(key, quote_plus(value)))
    return 'plugin://{0}/?{1}'.format(CONFIG.ADDON_ID, '&'.join(query))


def af3_tools_menu():
    """Touch-friendly AF3 tools row. The skin power menu is easy to
    miss on phones, so AF3 home widgets can show this directory as
    large cards."""
    try:
        handle = int(sys.argv[1])
    except Exception:
        handle = -1
    items = []
    for tool in AF3_TOOLS:
        li = xbmcgui.ListItem(tool['label'])
        li.setArt({
            'icon': tool['icon'],
            'thumb': tool['icon'],
            'poster': tool['icon'],
            'fanart': 'special://home/media/povil_icons/Logo_POV_IL.png',
        })
        li.setProperty('IsPlayable', 'false')
        url = _plugin_url('af3_tool', tool=tool['id'])
        items.append((url, li, False))
    if handle >= 0:
        xbmcplugin.addDirectoryItems(handle, items, len(items))
        xbmcplugin.setContent(handle, 'files')
        xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def af3_tool_action(tool_id):
    for tool in AF3_TOOLS:
        if tool['id'] == tool_id:
            xbmc.executebuiltin(tool['builtin'])
            return True
    return False
