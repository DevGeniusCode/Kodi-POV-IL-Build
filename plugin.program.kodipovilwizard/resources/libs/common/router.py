import xbmc
import xbmcgui
import xbmcplugin

import sys

try:  # Python 3
    from urllib.parse import parse_qsl
except ImportError:  # Python 2
    from urlparse import parse_qsl

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs.gui import menu

advanced_settings_mode = 'advanced_settings'
addon_installer_mode = 'addons'


class Router:
    def __init__(self):
        self.route = None
        self.params = {}
        tools.ensure_folders()

    def _log_params(self, paramstring):
        _url = sys.argv[0]

        self.params = dict(parse_qsl(paramstring))

        logstring = '{0}: '.format(_url)
        for param in self.params:
            logstring += '[ {0}: {1} ] '.format(param, self.params[param])

        logging.log(logstring, level=xbmc.LOGDEBUG)

        return self.params

    def dispatch(self, handle, paramstring):
        self._log_params(paramstring)

        mode = self.params['mode'] if 'mode' in self.params else None
        url = self.params['url'] if 'url' in self.params else None
        name = self.params['name'] if 'name' in self.params else None
        action = self.params['action'] if 'action' in self.params else None
        #####################################################
        # ONYX HOME (skins/onyx_home): the home's favourites and live TV rows and its hero's details.
        # Answered BEFORE the child-profile gate below, on purpose: these modules decide for themselves
        # what a child profile may see (an empty row), so the gate's "build management" dialog must
        # never fire for them. They used to be fast-pathed in default.py; the mode names are called by
        # the Onyx skin and stay byte-identical.
        if mode == 'favourites_row':
            from resources.libs.skins.onyx_home import favourites_row
            favourites_row.row(handle)
            return
        if mode == 'favourite_run':
            from resources.libs.skins.onyx_home import favourites_row
            favourites_row.run(handle, self.params)
            return
        if mode == 'pvr_row':
            from resources.libs.skins.onyx_home import pvr_row
            pvr_row.row(handle)
            return
        if mode == 'pvr_play':
            from resources.libs.skins.onyx_home import pvr_row
            pvr_row.play(handle, self.params)
            return
        if mode == 'hero_details':
            from resources.libs.skins.onyx_home import hero_details
            hero_details.run()
            return
        if mode == 'hero_warm':
            from resources.libs.skins.onyx_home import hero_details
            hero_details.warm()
            return
        #####################################################
        # HOME ACTIONS that used to be RunScript actions of the subtitle add-on (Phase 3): the Tonight tile, the home-search
        # provider picker and the debrid-expiry notice settings. As RunScript actions they were never behind the
        # child-profile gate, so, like the Onyx modes above, they are answered before it.
        if mode == 'tonight':
            from resources.libs.tonight import ui as tonight_ui
            tonight_ui.run()
            return
        if mode == 'search_provider':
            from resources.libs.skins import search_provider_picker
            search_provider_picker._handle_search_provider(self.params)
            return
        if mode == 'debrid_notice_settings':
            from resources.libs.addons import debrid_notice
            debrid_notice._handle_debrid_notice_settings(self.params)
            return
        #####################################################
        from resources.libs.profiles import hooks as profile_hooks
        if profile_hooks.deny_in_child_profile(mode, handle):
            return

        #####################################################
        # KODI-RD-IL
        auto_quick_update = self.params['auto_quick_update'] if 'auto_quick_update' in self.params else None
        kodi_version_update_check_manual = self.params['kodi_version_update_check_manual'] if 'kodi_version_update_check_manual' in self.params else None
        #####################################################

        #####################################################
        # KODI-POV-IL - In-wizard force close. Replaces the external
        # plugin.close.kodi addon: the skins' power menu now calls
        # RunPlugin(plugin://plugin.program.kodipovilwizard/?action=force_close).
        # Handled here -- before the mode-less branch (which opens the main
        # menu) -- so a no-mode force_close URL never pops the wizard UI.
        if action == 'force_close':
            logging.log("[ForceClose] Wizard-native force close requested", level=xbmc.LOGINFO)
            from resources.libs.common import tools
            tools.kill_kodi(over=True)
            return
        #####################################################

        # MAIN MENU
        if mode is None:
            from resources.libs.gui.main_menu import MainMenu
            MainMenu().get_listing()
            self._finish(handle)

        # profiles
        elif mode in profile_hooks.ROUTES:
            profile_hooks.dispatch(mode, self.params, handle)

        # SETTINGS
        elif mode == 'settings':  # OpenWizard settings
            CONFIG.open_settings(name)
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'opensettings':  # Open other addons' settings
            settings_id = eval(url.upper() + 'ID')[name]['plugin']
            CONFIG.open_settings(settings_id)
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'togglesetting':  # Toggle a setting
            CONFIG.set_setting(name, 'false' if CONFIG.get_setting(name) == 'true' else 'true')
            xbmc.executebuiltin('Container.Refresh()')

        # MENU SECTIONS
        elif mode == 'builds':  # Builds
            from resources.libs.gui.build_menu import BuildMenu
            BuildMenu().get_listing()
            self._finish(handle)
        elif mode == 'viewbuild':  # Builds -> "Your Build"
            from resources.libs.gui.build_menu import BuildMenu
            BuildMenu().view_build(name)
            self._finish(handle)
        elif mode == 'buildinfo':  # Builds -> Build Info
            from resources.libs.gui.build_menu import BuildMenu
            BuildMenu().build_info(name)
        elif mode == 'install':  # Builds -> Fresh Install/Standard Install/Apply guifix
            from resources.libs.build.wizard_actions import Wizard

            over = self.params.get('over', 'false') == 'true'
            if action == 'build':
                Wizard().build(name, over=over)
            elif action == 'gui':
                Wizard().gui(name)
            #####################################################
            # KODI-RD-IL
            elif action == 'quick_update':
                Wizard().quick_update(name, auto_quick_update)
            # KODI-RD-IL
            elif action == 'build_switch_skin':
                # This is an action, not a folder. Release Kodi's directory
                # busy dialog before the picker/settings retention windows.
                if handle >= 0:
                    xbmcplugin.endOfDirectory(handle, succeeded=False)
                from resources.libs.skins.switcher import build_switch_skin
                build_switch_skin()
            elif action == 'install_af3_ce':
                from resources.libs.skins.skin_packs import ensure_arctic_fuse_3_installed
                ensure_arctic_fuse_3_installed()
            elif action == 'install_umbrella':
                from resources.libs.addons.umbrella import install_umbrella_pilot
                install_umbrella_pilot()
            elif action == 'install_acctmgr':
                from resources.libs.accounts_auth.acctmgr import install_acctmgr_pilot
                install_acctmgr_pilot()
            elif action == 'af3_tools':
                from resources.libs.skins.af3_tools import af3_tools_menu
                af3_tools_menu()
            elif action == 'af3_tool':
                from resources.libs.skins.af3_tools import af3_tool_action
                af3_tool_action(self.params.get('tool', ''))
            # KODI-RD-IL
            elif action == 'kodi_version_update_check':
                from resources.libs.system.kodi_version import kodi_version_update_check
                kodi_version_update_check(kodi_version_update_check_manual)
            #####################################################
            elif action == 'theme':  # Builds -> "Your Build" -> "Your Theme"
                Wizard().theme(name, url)

        elif mode == 'maint':  # Maintenance + Maintenance -> any "Tools" section
            from resources.libs.gui.maintenance_menu import MaintenanceMenu

            if name == 'clean':
                MaintenanceMenu().clean_menu()
            elif name == 'addon':
                MaintenanceMenu().addon_menu()
            elif name == 'misc':
                MaintenanceMenu().misc_menu()
            elif name == 'backup':
                MaintenanceMenu().backup_menu()
            elif name == 'tweaks':
                MaintenanceMenu().tweaks_menu()
            elif name == 'logging':
                MaintenanceMenu().logging_menu()
            elif name is None:
                MaintenanceMenu().get_listing()
                
            self._finish(handle)

        elif mode == 'enableaddons':  # Maintenance - > Addon Tools -> Enable/Disable Addons
            menu.enable_addons()
            self._finish(handle)
        elif mode == 'enableall':
            menu.enable_addons(all=True)
        elif mode == 'toggleaddon':
            from resources.libs import db
            db.toggle_addon(name, url)
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'forceupdate':
            from resources.libs import db
            db.force_check_updates(auto=action)
        ############KODI-RD-IL##############
        elif mode == 'forceupdateFAST':
            from resources.libs import db
            db.forceUpdate()
        ####################################
        elif mode == 'togglecache':
            from resources.libs import clear
            clear.toggle_cache(name)
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'changefreq':  # Maintenance - Auto Clean Frequency
            menu.change_freq()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'systeminfo':  # Maintenance -> System Tweaks/Fixes -> System Information
            menu.system_info()
            self._finish(handle)
        elif mode == 'nettools':  # Maintenance -> Misc Maintenance -> Network Tools
            menu.net_tools()
            self._finish(handle)
        elif mode == 'viewIP':
            menu.view_ip()
            self._finish(handle)
        elif mode == 'rd_speedtest':
            from resources.libs.system.speedtest import real_debrid_speedtest
            real_debrid_speedtest()
        elif mode == 'standard_speedtest':
            from resources.libs.system.speedtest import standard_speedtest
            standard_speedtest()
        elif mode == 'build_speed_test':
            from resources.libs.system.speedtest import speedtest_dialog
            speedtest_dialog()
        elif mode == 'removeaddondata':  # Maintenance - > Addon Tools -> Remove Addon Data
            menu.remove_addon_data_menu()
            self._finish(handle)

        elif mode == 'savedata':  # Save Data Menu
            menu.save_menu()
            self._finish(handle)

        # ACCOUNT MANAGER / AUTHORIZATIONS
        elif mode == 'acctmgr':  # Account Manager Menu
            menu.acctmgr_menu()
            self._finish(handle)

        elif mode == 'run_acctmgr':
            if not xbmc.getCondVisibility('System.HasAddon(script.module.acctmgr)'):
                from resources.libs.accounts_auth.acctmgr import install_acctmgr_pilot
                install_acctmgr_pilot()
            if action:
                xbmc.executebuiltin('RunScript(script.module.acctmgr, action={})'.format(action))
            else:
                xbmc.executebuiltin('RunAddon(script.module.acctmgr)')

        elif mode == 'developer':  # Developer  Menu
            menu.developer()
            self._finish(handle)

        # SKIN MANAGER (resources/libs/skins/skin_manager.py)
        elif mode == 'skinmanager':
            from resources.libs.skins import skin_manager
            skin_manager.show()
            self._finish(handle)

        elif mode == 'skinaction':
            from resources.libs.skins import skin_manager
            # An action, not a folder: release Kodi's directory busy dialog before the confirm / progress windows
            if handle >= 0:
                xbmcplugin.endOfDirectory(handle, succeeded=False)
            skin_manager.act(self.params.get('skin', ''))

        # PATCH MANAGER (resources/libs/gui/patch_manager.py)
        elif mode == 'patchmanager':
            from resources.libs.gui import patch_manager
            patch_manager.show(self.params)
            self._finish(handle)

        elif mode == 'togglepatch':
            from resources.libs.gui import patch_manager
            patch_manager.toggle(name)

        elif mode == 'patchbulk':
            from resources.libs.gui import patch_manager
            patch_manager.bulk(self.params)

        elif mode == 'patchengine':
            from resources.libs.gui import patch_manager
            patch_manager.engine_action(self.params)

        # MAINTENANCE FUNCTIONS
        elif mode == 'kodi17fix':  # Misc Maintenance -> Kodi 17 Fix
            from resources.libs import db
            db.kodi_17_fix()
        elif mode == 'unknownsources':  # Misc Maintenance -> Enable Unknown Sources
            from resources.libs.skins import skin_settings as skin
            skin.swap_us()
        elif mode == 'enabledebug':  # Misc Maintenance -> Enable Debug Logging
            logging.swap_debug()
        elif mode == 'toggleupdates':  # Misc Maintenance -> Toggle Addon Updates
            from resources.libs import update
            update.toggle_addon_updates()
        elif mode == 'asciicheck':  # System Tweaks -> Scan for Non-Ascii Files
            from resources.libs.common import tools
            tools.ascii_check()
        elif mode == 'convertpath':  # System Tweaks -> Convert Special Paths
            from resources.libs.common import tools
            tools.convert_special(CONFIG.HOME)
        elif mode == 'forceprofile':  # Misc Maintenance -> Reload Profile
            from resources.libs.common import tools
            tools.reload_profile(tools.get_info_label('System.ProfileName'))
        elif mode == 'forceclose':  # Misc Maintenance -> Force Close Kodi
            from resources.libs.common import tools
            tools.kill_kodi()
        elif mode == 'forceskin':  # Misc Maintenance -> Reload Skin
            xbmc.executebuiltin("ReloadSkin()")
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'checksources':  # System Tweaks -> Scan source for broken links
            from resources.libs import check
            check.check_sources()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'checkrepos':  # System Tweaks -> Scan for broken repositories
            from resources.libs import check
            check.check_repos()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'whitelist':  # Whitelist Functions
            from resources.libs import whitelist
            whitelist.whitelist(name)

        #  CLEANING
        elif mode == 'oldThumbs':  # Cleaning Tools -> Clear Old Thumbnails
            from resources.libs import clear
            clear.old_thumbs()
        elif mode == 'clearbackup':  # Backup/Restore -> Clean Up Back Up Folder
            from resources.libs import backup
            backup.cleanup_backup()
        elif mode == 'fullclean':  # Cleaning Tools -> Total Cleanup
            from resources.libs import clear
            clear.total_clean()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'clearcache':  # Cleaning Tools -> Clear Cache
            from resources.libs import clear
            clear.clear_cache()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'clearfunctioncache':  # Cleaning Tools -> Clear Function Caches
            from resources.libs import clear
            clear.clear_function_cache()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'clearpackages':  # Cleaning Tools -> Clear Packages
            from resources.libs import clear
            clear.clear_packages()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'clearcrash':  # Cleaning Tools -> Clear Crash Logs
            from resources.libs import clear
            clear.clear_crash()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'clearthumb':  # Cleaning Tools -> Clear Thumbnails
            from resources.libs import clear
            clear.clear_thumbs()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'cleararchive':  # Cleaning Tools -> Clear Archive Cache
            from resources.libs import clear
            clear.clear_archive()
            xbmc.executebuiltin('Container.Refresh()')
        elif mode == 'freshstart':  # Cleaning Tools -> Fresh Start
            from resources.libs import install
            install.fresh_start()
        elif mode == 'purgedb':  # Cleaning Tools -> Purge Databases
            from resources.libs import db
            db.purge_db()
        elif mode == 'removeaddons':  # Addon Tools -> Remove Addons
            from resources.libs import clear
            clear.remove_addon_menu()
        elif mode == 'removedata':  # Addon Tools -> Remove Addon Data
            from resources.libs import clear
            clear.remove_addon_data(name)
        elif mode == 'resetaddon':  # Addon Tools -> Remove Addon Data -> Remove  Wizard Addon Data
            from resources.libs.common import tools

            tools.clean_house(CONFIG.ADDON_DATA, ignore=True)
            logging.log_notify("[COLOR {0}]{1}[/COLOR]".format(CONFIG.COLOR1, CONFIG.ADDONTITLE),
                               "[COLOR {0}]Addon_Data reset[/COLOR]".format(CONFIG.COLOR2))
        # BACKUP / RESTORE
        elif mode == 'backup' and action:
            from resources.libs import backup
            backup.backup(action)
        elif mode == 'restore' and action:
            from resources.libs import restore
            restore.restore(action, external=name == 'external')

        elif mode == 'wizardupdate':  # Wizard Update
            from resources.libs import update
            update.wizard_update()

        # KODI-POV-IL - Manual modular (manifest-based) update trigger
        elif mode == 'modular_update':
            from resources.libs.modular_updater import ModularUpdater
            ModularUpdater(background=False).run_update_check()

        # LOGGING
        elif mode == 'uploadlog':  # Upload Log File
            logging.upload_log()
        elif mode == 'viewlog':  # View kodi.log
            logging.view_log_file()
        elif mode == 'viewwizlog':  # View wizard.log
            from resources.libs.gui import window
            window.show_log_viewer(log_file=CONFIG.WIZLOG)
        elif mode == 'viewerrorlog':  # View errors in log
            logging.error_checking()
        elif mode == 'viewerrorlast':  # View last error in log
            logging.error_checking(last=True)
        elif mode == 'clearwizlog':  # Clear wizard.log
            from resources.libs.common import tools
            tools.remove_file(CONFIG.WIZLOG)
            logging.log_notify("[COLOR {0}]{1}[/COLOR]".format(CONFIG.COLOR1, CONFIG.ADDONTITLE),
                               "[COLOR {0}]Wizard Log Cleared![/COLOR]".format(CONFIG.COLOR2))

        # ADVANCED SETTINGS
        elif mode == advanced_settings_mode:
            from resources.libs import advanced

            self.route = advanced.AdvancedMenu()
            advanced_settings_actions = ['quick_configure', 'view_current', 'remove_current', 'write_advanced', 'set_setting', 'show_section']

            category = self.params['category'] if 'category' in self.params else None
            tag = self.params['tag'] if 'tag' in self.params else None
            value = self.params['value'] if 'value' in self.params else None
            tags = self.params['tags'] if 'tags' in self.params else None

            if not action:
                self.route.show_menu(url=url)
                self._finish(handle)
            elif action == advanced_settings_actions[0]:  # Advanced Settings Quick Configure
                self.route.quick_configure()
                self._finish(handle)
            elif action == advanced_settings_actions[1]:  # View Current Advanced Settings
                advanced.view_current()
            elif action == advanced_settings_actions[2]:  # Remove Current Advanced Settings
                advanced.remove_current()
            elif action == advanced_settings_actions[3] and url:  # Write New Advanced Settings
                self.route.write_advanced(name, url)
            elif action == advanced_settings_actions[4]:  # Set a Setting
                self.route.set_setting(category, tag, value)
            elif action == advanced_settings_actions[5]:  # Open a Section
                self.route.show_section(tags)
                self._finish(handle)
                
        # ADDON INSTALLER
        elif mode == addon_installer_mode:
            from resources.libs.gui import addon_menu
            
            self.route = addon_menu.AddonMenu()
            addon_installer_actions = ['addon', 'skin', 'addonpack']

            addonurl = self.params['addonurl'] if 'addonurl' in self.params else None
            repository = self.params['repository'] if 'repository' in self.params else None
            repositoryurl = self.params['repositoryurl'] if 'repositoryurl' in self.params else None
            repositoryxml = self.params['repositoryxml'] if 'repositoryxml' in self.params else None
            urls = [addonurl, repository, repositoryurl, repositoryxml]
            
            if not action:
                self.route.show_menu(url=url)
                self._finish(handle)
            elif action == addon_installer_actions[0]:
                self.route.install_addon(name, urls)
            elif action == addon_installer_actions[1]:
                pass
                # self.route.install_skin(name, url)
            elif action == addon_installer_actions[2]:
                pass
                # self.route.install_addon_pack(name, url)
            
        # SAVE DATA
        elif mode == 'managedata':
            from resources.libs import save

            if name == 'import':
                save.import_save_data()
            elif name == 'export':
                save.export_save_data()

        # DEVELOPER MENU
        elif mode == 'testnotify':  # Developer Menu -> Test Notify
            from resources.libs import test
            test.test_notify()
        elif mode == 'testupdate':  # Developer Menu -> Test Update
            from resources.libs import test
            test.test_update()
        elif mode == 'testsavedata':  # Developer Menu -> Test Save Data Settings
            from resources.libs import test
            test.test_save_data_settings()
        elif mode == 'testbuildprompt':  # Developer Menu -> Test Build Prompt
            from resources.libs import test
            test.test_first_run()
        elif mode == 'binarycheck':
            from resources.libs import db
            db.find_binary_addons()
        elif mode == 'contact':  # Contact
            from resources.libs.gui import window
            window.show_contact(CONFIG.CONTACT)
        
    def _finish(self, handle):
        from resources.libs.common import directory
        
        directory.set_view()
        
        xbmcplugin.setContent(handle, 'files')
        xbmcplugin.endOfDirectory(handle)                       
