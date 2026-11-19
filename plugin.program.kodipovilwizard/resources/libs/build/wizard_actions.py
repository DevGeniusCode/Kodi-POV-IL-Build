################################################################################
#      Copyright (C) 2019 drinfernoo                                           #
#                                                                              #
#  This Program is free software; you can redistribute it and/or modify        #
#  it under the terms of the GNU General Public License as published by        #
#  the Free Software Foundation; either version 2, or (at your option)         #
#  any later version.                                                          #
#                                                                              #
#  This Program is distributed in the hope that it will be useful,             #
#  but WITHOUT ANY WARRANTY; without even the implied warranty of              #
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the                #
#  GNU General Public License for more details.                                #
#                                                                              #
#  You should have received a copy of the GNU General Public License           #
#  along with XBMC; see the file COPYING.  If not, write to                    #
#  the Free Software Foundation, 675 Mass Ave, Cambridge, MA 02139, USA.       #
#  http://www.gnu.org/copyleft/gpl.html                                        #
################################################################################

"""Build actions: fresh / normal install, GUI fix, theme, quick update, restart."""
import os

import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs.common.config import CONFIG
from resources.libs import check
from resources.libs import db
from resources.libs import extract
from resources.libs import install
from resources.libs.downloader import Downloader


class Wizard:

    def __init__(self):
        tools.ensure_folders(CONFIG.PACKAGES)
        
        self.dialog = xbmcgui.Dialog()
        self.dialogProgress = xbmcgui.DialogProgress()

    def _prompt_for_wipe(self):
        # Should we wipe first?
        if self.dialog.yesno(CONFIG.ADDONTITLE,
                           "[COLOR {0}]Do you wish to restore your".format(CONFIG.COLOR2) +'\n' + "Kodi configuration to default settings" + '\n' + "Before installing the build backup?[/COLOR]",
                           nolabel='[B][COLOR red]No[/COLOR][/B]',
                           yeslabel='[B][COLOR springgreen]Yes[/COLOR][/B]'):
            install.wipe()

    def build(self, name, over=False):
        # KODI-POV-IL - FULL INSTALL, now fully MODULAR.
        # The legacy path downloaded a monolithic build zip from build.txt, ran
        # install.wipe(), and extracted it over special://home. ALL of that is
        # gone. A "Full Install" is now a foreground fresh install (provisioning/provisioner):
        # the repositories, every add-on of the build through Kodi's native InstallAddon,
        # and the build-config pack. No wipe, no monolithic zip, no build.txt -- and it
        # is guarded by the same .provisioned marker, so an interrupted run resumes on
        # the next launch.
        if not over:
            if not self.dialog.yesno(
                    CONFIG.ADDONTITLE,
                    '[COLOR {0}]האם ברצונך להתקין את '.format(CONFIG.COLOR2)
                    + '[COLOR {0}]{1}[/COLOR]?[/COLOR]'.format(CONFIG.COLOR1, name),
                    nolabel='[B][COLOR red]ביטול[/COLOR][/B]',
                    yeslabel='[B][COLOR springgreen]התקנה[/COLOR][/B]'):
                logging.log_notify(CONFIG.ADDONTITLE,
                                   '[COLOR {0}]התקנת בילד: בוטלה![/COLOR]'.format(CONFIG.COLOR2))
                return

        CONFIG.clear_setting('build')

        build_name = CONFIG.BUILDNAME_DEFAULT
        build_version = CONFIG.BUILDVERSION_DEFAULT

        from resources.libs.provisioning import provisioner
        logging.log("[Build] Full modular install of {0} v{1}".format(build_name, build_version),
                    level=xbmc.LOGINFO)

        # A user-triggered Full Install starts clean: drop the marker so the whole
        # fresh sequence runs (and so an interrupted run is resumable).
        provisioner.clear_provisioned()
        from resources.libs import fresh_install
        fresh_install.begin()

        from resources.libs.gui import install_progress
        view = install_progress.open_window()
        try:
            completed, issues = fresh_install.provision(view=view)
        except Exception as err:
            logging.log("[Build] Modular fresh install failed: {0}".format(err), level=xbmc.LOGERROR)
            install_progress.close_window(view, False)
            self.dialog.ok(CONFIG.ADDONTITLE,
                           "[COLOR {0}]התקנת הבילד נכשלה. נסה שוב.[/COLOR]".format(CONFIG.COLOR2))
            return

        xbmc.sleep(500)

        # Completion gate: the build engine must be on disk AND run_fresh_install
        # must have written the .provisioned marker (ran end-to-end). Otherwise do
        # NOT flip the build to installed -- startup resumes it next launch.
        engine_present = os.path.exists(os.path.join(CONFIG.ADDONS, 'service.subtitles.kodipovilai'))
        if not completed or not engine_present or not provisioner.is_provisioned():
            logging.log("[Build] Full install did not complete (engine={0}, marker={1}); "
                        "will resume next launch.".format(engine_present, provisioner.is_provisioned()),
                        level=xbmc.LOGERROR)
            install_progress.close_window(view, False)
            self.dialog.ok(CONFIG.ADDONTITLE, fresh_install.failure_message(issues))
            return

        provisioner.set_stage(None, view, provisioner.STAGE_FINALIZE)
        provisioner.mark_step(view, provisioner.FINALIZE_ROW, 'active')
        try:
            if not fresh_install.finalize(view=view):
                install_progress.close_window(view, False)
                self.dialog.ok(CONFIG.ADDONTITLE, 'ההתקנה ממתינה להשלמה. אשר את הסקין או נסה שוב.')
                return
        except Exception as err:
            logging.log('[Build] finalization failed: {0}'.format(err), level=xbmc.LOGERROR)
            install_progress.close_window(view, False)
            self.dialog.ok(CONFIG.ADDONTITLE, 'ההתקנה לא הושלמה. ניתן לנסות שוב בלי למחוק הגדרות.')
            return
        provisioner.mark_step(view, provisioner.FINALIZE_ROW, 'done')
        install_progress.close_window(view, True)
        db.addon_database(CONFIG.ADDON_ID, 1)
        self.dialog.notification(CONFIG.ADDONTITLE, 'הבילד הותקן ומוכן לשימוש')

    def gui(self, name, over=False):
        # KODI-POV-IL - DEPRECATED legacy "GuiFix" quickfix installer.
        # This was a twin of the old quick_update(): it downloaded the
        # monolithic `gui` zip from build.txt and extracted it over CONFIG.HOME.
        # That path is broken/dangerous under the modular architecture (stale
        # monolithic zip overwriting the live modular build). Its menu item is
        # already commented out, but router.py still routes action=gui here, so
        # we redirect it to the safe manifest-based update instead of ever
        # touching the monolithic zip. RECOMMENDATION: delete gui() and its
        # router branch once no plugin URLs reference action=gui.
        return self.quick_update(name, auto_quick_update="false")

    #####################################################
    # KODI-RD-IL
    def quick_update(self, name, auto_quick_update="false"):
        # KODI-POV-IL - "Quick Update" (עדכון מהיר) -- REWIRED for the repository
        # architecture.
        #
        # LEGACY (removed): this used to read the `gui` field of the old
        # monolithic build.txt, download a single "quickfix" build zip
        # (Kodi-POV-IL-FENtastic-quickfix-*.zip) and extract it straight over
        # CONFIG.HOME with ignore=True. Under the hybrid modular build that zip
        # is BROKEN and DANGEROUS: it is frozen on the old repo, still carries
        # the pre-migration monolithic wizard (0.1.30), and extracting it would
        # OVERWRITE the live modular wizard + addons and undo the whole
        # migration on the user's device.
        #
        # NOW: "Quick Update" is a thin, safe wrapper around provisioner.run_update_check.
        # It makes sure the repositories are installed, asks Kodi to check them (Kodi
        # installs the add-on updates itself) and applies a newer config pack -- no
        # monolithic zip, no full reinstall, no wipe. We deliberately do NOT
        # force-close Kodi here.
        auto = True if auto_quick_update == "true" else False

        try:
            from resources.libs.provisioning import provisioner
        except Exception as err:
            logging.log("[quick_update] provisioner import failed: {0}".format(err),
                        level=xbmc.LOGERROR)
            return False

        logging.log("[quick_update] Running modular update check (auto={0})".format(auto),
                    level=xbmc.LOGINFO)
        try:
            # auto path -> silent background pass; manual menu click -> foreground
            # (shows a result dialog).
            result = provisioner.run_update_check(background=auto)

            # KODI-POV-IL - Engine v2 Runtime Patching. Runs once the update
            # check has finished (whether or not it found anything new) so
            # a Quick Update always leaves every patch current, the same
            # guarantee the boot-cycle heal path gives -- without this,
            # patches would only ever refresh on a full Kodi restart.
            try:
                from resources.libs.patch_engine import PatchEngine
                PatchEngine().run()
            except Exception as _patch_err:
                logging.log("[quick_update] PatchEngine run failed: {0}".format(_patch_err),
                            level=xbmc.LOGERROR)

            return bool(result)
        except Exception as err:
            logging.log("[quick_update] Modular update failed: {0}".format(err),
                        level=xbmc.LOGERROR)
            return False
    #####################################################

    #####################################################
    # KODI-RD-IL
    def force_close_kodi_in_5_seconds(self, dialog_header):
        self.dialogProgress.create(f"[COLOR yellow][B]{dialog_header}[/B][/COLOR]", "[B]קודי ייסגר בעוד 5 שניות[/B]")
        for s in range(5, -1, -1):
            self.dialogProgress.update(int((5 - s) / 5.0 * 100), f"[B]קודי ייסגר בעוד {s} שניות[/B]")
            xbmc.sleep(1000)
        self.restart_kodi()
    #####################################################

    #####################################################
    # KODI-RD-IL
    def restart_kodi(self):
        # if tools.platform() == 'windows':
            # try:
                # import subprocess, xbmcvfs
                # kodi_root_path = xbmcvfs.translatePath('special://xbmc/')
                # kodi_full_path = [os.path.join(kodi_root_path, 'kodi.exe')]
                # KODI-RD-IL Custom Windows software - AppData stored in C:\Kodi + Real Debrid Israel\portable_data
                # if "Kodi + Real Debrid Israel" in kodi_root_path:
                    # kodi_full_path.append('-p')
                # subprocess.Popen(kodi_full_path, shell=True)
            # except:
                # pass
        tools.kill_kodi(over=True)
    #####################################################



    def theme(self, name, theme='', over=False):
        installtheme = False

        if not theme:
            themefile = check.check_build(name, 'theme')

            response = tools.open_url(themefile, check=True)
            if response:
                from resources.libs.gui.build_menu import BuildMenu
                themes = BuildMenu().theme_count(name, False)
                if len(themes) > 0:
                    if self.dialog.yesno(CONFIG.ADDONTITLE, "[COLOR {0}]The Build [COLOR {1}]{2}[/COLOR] comes with [COLOR {3}]{4}[/COLOR] different themes".format(CONFIG.COLOR2, CONFIG.COLOR1, name, CONFIG.COLOR1, len(themes)) + '\n' + "Would you like to install one now?[/COLOR]",
                                    yeslabel="[B][COLOR springgreen]Install Theme[/COLOR][/B]",
                                    nolabel="[B][COLOR red]Cancel Themes[/COLOR][/B]"):
                        logging.log("Theme List: {0}".format(str(themes)))
                        ret = self.dialog.select(CONFIG.ADDONTITLE, themes)
                        logging.log("Theme install selected: {0}".format(ret))
                        if not ret == -1:
                            theme = themes[ret]
                            installtheme = True
                        else:
                            logging.log_notify(CONFIG.ADDONTITLE,
                                               '[COLOR {0}]Theme Install: Cancelled![/COLOR]'.format(CONFIG.COLOR2))
                            return
                    else:
                        logging.log_notify(CONFIG.ADDONTITLE,
                                           '[COLOR {0}]Theme Install: Cancelled![/COLOR]'.format(CONFIG.COLOR2))
                        return
            else:
                logging.log_notify(CONFIG.ADDONTITLE,
                                   '[COLOR {0}]Theme Install: None Found![/COLOR]'.format(CONFIG.COLOR2))
        else:
            installtheme = self.dialog.yesno(CONFIG.ADDONTITLE, '[COLOR {0}]Would you like to install the theme:'.format(CONFIG.COLOR2) +' \n' + '[COLOR {0}]{1}[/COLOR]'.format(CONFIG.COLOR1, theme) + '\n' + 'for [COLOR {0}]{1} v{2}[/COLOR]?[/COLOR]'.format(CONFIG.COLOR1, name, check.check_build(name,'version')),yeslabel="[B][COLOR springgreen]Install Theme[/COLOR][/B]", nolabel="[B][COLOR red]Cancel Themes[/COLOR][/B]")

        if installtheme:
            themezip = check.check_theme(name, theme, 'url')
            zipname = name.replace('\\', '').replace('/', '').replace(':', '').replace('*', '').replace('?', '').replace('"', '').replace('<', '').replace('>', '').replace('|', '')

            response = tools.open_url(themezip, check=True)
            if not response:
                logging.log_notify(CONFIG.ADDONTITLE,
                                   '[COLOR {0}]Theme Install: Invalid Zip Url![/COLOR]'.format(CONFIG.COLOR2))
                return False

            self.dialogProgress.create(CONFIG.ADDONTITLE, '[COLOR {0}][B]Downloading:[/B][/COLOR] [COLOR {1}]{2}[/COLOR]'.format(CONFIG.COLOR2, CONFIG.COLOR1, zipname) +' \n' + 'Please Wait')

            lib = os.path.join(CONFIG.PACKAGES, '{0}.zip'.format(zipname))

            try:
                os.remove(lib)
            except:
                pass

            Downloader().download(themezip, lib)
            xbmc.sleep(500)

            if os.path.getsize(lib) == 0:
                try:
                    os.remove(lib)
                except:
                    pass

                return

            self.dialogProgress.update(0, '\n' + "Installing {0}".format(name))

            test1 = False
            test2 = False

            from resources.libs.skins import skin_settings as skin
            from resources.libs import test
            test1 = test.test_theme(lib) if CONFIG.SKIN not in skin.DEFAULT_SKINS else False
            test2 = test.test_gui(lib) if CONFIG.SKIN not in skin.DEFAULT_SKINS else False

            if test1:
                skin.look_and_feel_data('save')
                swap = skin.skin_to_default('Theme Install')

                if not swap:
                    return False

                xbmc.sleep(500)

            title = '[COLOR {0}][B]Installing Theme:[/B][/COLOR] [COLOR {1}]{2}[/COLOR]'.format(CONFIG.COLOR2, CONFIG.COLOR1, theme)
            self.dialogProgress.update(0, title + '\n' + 'Please Wait')
            percent, errors, error = extract.all(lib, CONFIG.HOME, title=title)
            CONFIG.set_setting('buildtheme', theme)
            logging.log('INSTALLED {0}: [ERRORS:{1}]'.format(percent, errors))
            self.dialogProgress.close()

            db.force_check_updates(over=True)
            installed = db.grab_addons(lib)
            db.addon_database(installed, 1, True)

            if test2:
                skin.look_and_feel_data('save')
                skin.skin_to_default("Theme Install")
                gotoskin = CONFIG.get_setting('defaultskin')
                skin.switch_to_skin(gotoskin, "Theme Installer")
                skin.look_and_feel_data('restore')
            elif test1:
                skin.look_and_feel_data('save')
                skin.skin_to_default("Theme Install")
                gotoskin = CONFIG.get_setting('defaultskin')
                skin.switch_to_skin(gotoskin, "Theme Installer")
                skin.look_and_feel_data('restore')
            else:
                xbmc.executebuiltin("ReloadSkin()")
                xbmc.sleep(1000)
                xbmc.executebuiltin("Container.Refresh()")
        else:
            logging.log_notify(CONFIG.ADDONTITLE,
                               '[COLOR {0}]Theme Install: Cancelled![/COLOR]'.format(CONFIG.COLOR2))


def wizard(action, name, url):
    cls = Wizard()

    if action in ['fresh', 'normal']:
        cls.build(action, name)
    elif action == 'gui':
        cls.gui(name)
    elif action == 'theme':
        cls.theme(name, url)
