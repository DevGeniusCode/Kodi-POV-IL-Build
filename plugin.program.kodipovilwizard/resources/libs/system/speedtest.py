"""Network speed tests (standard and Real-Debrid)."""
import xbmc
import xbmcgui

from resources.libs.common import tools
from resources.libs.common.config import CONFIG


# KODI-RD-IL - ANDROID
# Required helper for the real_debrid_speedtest android browsers check
def check_if_app_installed(app_package_id):
    import xbmcvfs
    try:
        apps = xbmcvfs.listdir('androidapp://sources/apps/')[1]
        return app_package_id in apps
    except Exception:
        return False
##########################################


##########################################
# KODI-RD-IL - NETWORK TOOLS FUNCTIONS

def standard_speedtest():
    """Launches the standard Kodi speed test addon, installs it if missing."""
    # Check if the addon is already installed
    if not xbmc.getCondVisibility('System.HasAddon(script.speedtester)'):
        xbmc.executebuiltin('InstallAddon("script.speedtester")')

    # Run the addon
    xbmc.executebuiltin('RunAddon("script.speedtester")')


def real_debrid_speedtest():
    """Opens the default system browser or supported Android browser to Real Debrid speedtest."""
    os_type_label = tools.platform().capitalize()

    # Windows platform
    if tools.platform() == 'windows':
        import webbrowser
        webbrowser.get().open_new_tab("https://real-debrid.com/speedtest")

    # Android / Android TV platform
    elif tools.platform() == 'android':
        android_apps_browsers_list = ['com.android.chrome', 'com.phlox.tvwebbrowser', 'com.seraphic.openinet.pre', 'com.tcl.browser']
        installed_browser_package_id = None

        # Check for installed browsers from the supported list
        for browser_package_id in android_apps_browsers_list:
            if check_if_app_installed(browser_package_id):
                installed_browser_package_id = browser_package_id
                break

        # If no supported browser found, prompt user to visit Play Store
        if not installed_browser_package_id:
            dialog = xbmcgui.Dialog()
            yes_pressed = dialog.yesno(f"{CONFIG.ADDONTITLE} ({os_type_label})",
                               f'[B][COLOR yellow]לא מותקן דפדפן תומך!\nדפדפנים נתמכים:[/COLOR]\nGoogle Chrome, TV Bro, OPEN BROWSER, BrowseHere[/B]',
                               nolabel='[B]ביטול[/B]',
                               yeslabel='[B]קח אותי לחנות[/B]')
            if yes_pressed:
                xbmc.executebuiltin('StartAndroidActivity(com.android.vending)')
            return

        # Launch browser with the specified URL
        app      = installed_browser_package_id
        intent   = 'android.intent.action.VIEW'
        dataType = ''
        dataURI  = "https://real-debrid.com/speedtest"
        xbmc.executebuiltin(f'StartAndroidActivity("{app}", "{intent}", "{dataType}", "{dataURI}")')

    # Unsupported OS
    else:
        dialog = xbmcgui.Dialog()
        dialog.ok(CONFIG.ADDONTITLE, f"[B]פתיחת דפדפן עבור בדיקת מהירות Real Debrid אינה זמינה עבור מערכת ההפעלה: {os_type_label}[/B]")


def speedtest_dialog():
    """Displays a choice dialog for legacy buttons or favorites.xml shortcuts."""
    dialog = xbmcgui.Dialog()
    yes_pressed = dialog.yesno(CONFIG.ADDONTITLE,
                       f'[B][COLOR yellow]האם להפעיל בדיקת מהירות דרך הרחבת Speed Test או דרך האתר של ריל דבריד?[/COLOR][/B]',
                       nolabel='[B]Speed Test[/B]',
                       yeslabel='[B]Real Debrid[/B]')

    if not yes_pressed:
        standard_speedtest()
    else:
        real_debrid_speedtest()
#############################################
