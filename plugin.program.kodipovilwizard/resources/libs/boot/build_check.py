"""Start-up build registration and the installed-build sanity check."""
import os
from datetime import datetime
from datetime import timedelta
from urllib.parse import quote_plus

import xbmc
import xbmcgui

from resources.libs.common.config import CONFIG
from resources.libs import check
from resources.libs.common import logging
from resources.libs import skin


def autoset_buildname_for_apk():
    # KODI-RD-IL - Auto-set buildname for APK installs where the user
    # never ran a wizard-driven Fresh Install. Without this the empty
    # 'buildname' setting silently disables the entire auto_quick_update
    # path below, so existing APK users would never receive quickfix
    # updates. Detect that POV is on disk (so the build really is
    # installed, just not registered with the wizard) and populate the
    # settings the wizard's update gates check.
    try:
        if (not CONFIG.get_setting('buildname') and
                not os.path.isfile(os.path.join(
                    CONFIG.USERDATA, 'kodipovil.modular_install_started'))):
            pov_addon_dir = os.path.join(CONFIG.ADDONS, 'plugin.video.pov')
            if os.path.exists(pov_addon_dir):
                CONFIG.set_setting('buildname', CONFIG.BUILDNAME_DEFAULT)
                CONFIG.set_setting('installed', 'true')
                # The skin-switch first-launch notification would otherwise
                # fire on the next startup now that buildname is set. The
                # user has been using the build for a while, so suppress it.
                CONFIG.set_setting('build_skin_switch_notifcation_dismiss', 'true')

                # CRITICAL: also set buildversion. Without this,
                # check.check_build_update sees an empty buildversion and
                # treats every published version as "newer", which fires
                # a Fresh-Install dialog whose default action overwrites
                # the user's entire userdata (wiping Real-Debrid, Trakt
                # and other connected-services state -- happened to the
                # first test user). Try to fetch the current published
                # version from build.txt; fall back to the constant baked
                # into uservar.py.
                current_version = CONFIG.BUILDVERSION_DEFAULT
                try:
                    v = check.check_build(CONFIG.BUILDNAME_DEFAULT, 'version')
                    if v:
                        current_version = v
                except Exception:
                    pass
                CONFIG.set_setting('buildversion', current_version)
                CONFIG.set_setting('latestversion', current_version)

                # Belt-and-suspenders: also push the next build-update
                # check 30 days into the future. Even if buildversion
                # ends up wrong, this gives us a long window to ship a
                # quickfix before any "update available" dialog fires.
                future_check = (datetime.now() + timedelta(days=30)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                CONFIG.set_setting('nextbuildcheck', future_check)

                # Refresh the in-memory cache so other startup steps see
                # the new values immediately.
                CONFIG.BUILDNAME = CONFIG.BUILDNAME_DEFAULT
                CONFIG.BUILDVERSION = current_version
                CONFIG.BUILDLATEST = current_version
                CONFIG.INSTALLED = 'true'
                CONFIG.BUILDCHECK = future_check

                logging.log(
                    "[Auto-Set Buildname] APK install detected (plugin.video.pov "
                    "present, buildname was empty). Set buildname='{0}', "
                    "installed='true', buildversion='{1}', "
                    "nextbuildcheck='{2}'.".format(
                        CONFIG.BUILDNAME_DEFAULT, current_version, future_check
                    ),
                    level=xbmc.LOGINFO,
                )
    except Exception as _autoset_err:
        try:
            logging.log(
                "[Auto-Set Buildname] Failed (continuing): {0}".format(_autoset_err),
                level=xbmc.LOGERROR,
            )
        except Exception:
            pass


def installed_build_check():
    dialog = xbmcgui.Dialog()

    if not CONFIG.EXTRACT == '100' and CONFIG.EXTERROR > 0:
        logging.log("[Build Installed Check] Build was extracted {0}/100 with [ERRORS: {1}]".format(CONFIG.EXTRACT,
                                                                                                    CONFIG.EXTERROR),
                    level=xbmc.LOGINFO)
        yes = dialog.yesno(CONFIG.ADDONTITLE,
                           '[COLOR {0}]{2}[/COLOR] [COLOR {1}]was not installed correctly![/COLOR]'.format(CONFIG.COLOR1,
                                                                                                   CONFIG.COLOR2,
                                                                                                   CONFIG.BUILDNAME)
                           +'\n'+('Installed: [COLOR {0}]{1}[/COLOR] / '
                            'Error Count: [COLOR {2}]{3}[/COLOR]').format(CONFIG.COLOR1, CONFIG.EXTRACT, CONFIG.COLOR1,
                                                                          CONFIG.EXTERROR)
                           +'\n'+'Would you like to try again?[/COLOR]', nolabel='[B]No Thanks![/B]',
                           yeslabel='[B]Retry Install[/B]')
        CONFIG.clear_setting('build')
        if yes:
            xbmc.executebuiltin("PlayMedia(plugin://{0}/?mode=install&name={1}&url=fresh)".format(CONFIG.ADDON_ID,
                                                                                                  quote_plus(CONFIG.BUILDNAME)))
            logging.log("[Build Installed Check] Fresh Install Re-activated", level=xbmc.LOGINFO)
        else:
            logging.log("[Build Installed Check] Reinstall Ignored")
    elif CONFIG.SKIN in ['skin.confluence', 'skin.estuary', 'skin.estouchy']:
        logging.log("[Build Installed Check] Incorrect skin: {0}".format(CONFIG.SKIN), level=xbmc.LOGINFO)
        defaults = CONFIG.get_setting('defaultskin')
        if not defaults == '':
            if os.path.exists(os.path.join(CONFIG.ADDONS, defaults)):
                if skin.skin_to_default(defaults):
                    skin.look_and_feel_data('restore')
        if not CONFIG.SKIN == defaults and not CONFIG.BUILDNAME == "":
            # KODI-POV-IL - the build skin was reset to a stock Kodi skin. The
            # legacy monolithic "GuiFix" zip recovery is gone; instead force the
            # modular updater to RE-APPLY the build-config pack on the next OTA
            # pass (it re-seeds the build skin + look) by clearing the
            # applied-config marker. Silent and non-destructive.
            logging.log("[Build Installed Check] Build skin reset to {0}; clearing "
                        "config marker so ModularUpdater re-applies the build config."
                        .format(CONFIG.SKIN), level=xbmc.LOGWARNING)
            CONFIG.set_setting('config_applied_version', '')
    else:
        logging.log('[Build Installed Check] Install seems to be completed correctly', level=xbmc.LOGINFO)

    if CONFIG.get_setting('installed') == 'true':
        CONFIG.clear_setting('install')
