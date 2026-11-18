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

import xbmc

import os
import sys

from resources.libs.common.config import CONFIG
from resources.libs import check
from resources.libs import db
from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs import update
from resources.libs.profiles import hooks as profile_hooks
from resources.libs.boot import build_check
from resources.libs.boot import first_boot
from resources.libs.boot import gui_ready
from resources.libs.boot import language_sync
from resources.libs.boot import maintenance_pass
from resources.libs.boot import notifications
from resources.libs.provisioning import auto_install
from resources.libs.provisioning import ota


# HOST REPAIRS (Phase 3): the build's startup repairs (POV / Umbrella / Idan Plus / AF3 / NOX patchers) run as their own
# script, launched FIRST: nothing below (provisioning, the GUI wait, any sys.exit()) can delay or cancel them, and the
# clock-bound POV language-invoker step is reached as early as it always was. Kodi runs only one service script per
# add-on, so this is how the repairs get their own lifetime. host_repairs_service.py keeps a single-runner lock.
xbmc.executebuiltin('RunScript(special://home/addons/{0}/host_repairs_service.py)'.format(CONFIG.ADDON_ID))

# Don't run the script while video is playing :)
profile_hooks.start_guard()
gui_ready.check_for_video()
profile_hooks.prepare_startup()

# Ensure that any needed folders are created
tools.ensure_folders()

# Ensure that the wizard's name matches its folder
check.check_paths()

# AUTO UPDATE WIZARD
if CONFIG.AUTOUPDATE == 'Yes':
    logging.log("[Auto Update Wizard] Started", level=xbmc.LOGINFO)
    update.wizard_update()
else:
    logging.log("[Auto Update Wizard] Not Enabled", level=xbmc.LOGINFO)

# KODI-RD-IL - Auto force addon updates on Kodi startup
if getattr(CONFIG, 'FORCEUPDATEFAST_ONSTARTUP', 'false') == "true":
    db.forceUpdate(startup=True)

# KODI-POV-IL - Clean APK/IPK/Windows/wizard first launch hydration.
# This is intentionally before notifications and quick_update: a clean profile
# first needs the full build (userdata + FENtastic + favourites) extracted.
# Apply installed POV fixes before the GUI-ready/modal-dialog wait. On slow
# skins that wait can consume its full 90-second budget; source selection must
# not spend that period using unpatched POV after an upstream self-update.
# The later pass still repeats after a modular update replaces add-on files.
if os.path.isfile(os.path.join(CONFIG.ADDONS, 'plugin.video.pov', 'addon.xml')):
    try:
        from resources.libs.patch_engine import PatchEngine
        PatchEngine().run_if_changed()
    except Exception as _early_patch_err:
        logging.log('[PatchEngine] Early boot run failed: {0}'.format(
            _early_patch_err), level=xbmc.LOGERROR)

auto_install.fresh_build_auto_install_if_needed()
if os.path.isfile(os.path.join(CONFIG.USERDATA, 'kodipovil.modular_install_started')):
    # Incomplete setup must not proceed to normal notifications or claim ready.
    logging.log('[FreshInstall] setup remains pending', level=xbmc.LOGWARNING)
    sys.exit()

# Everything below can pop a modal dialog (build first-launch notification,
# skin-switch notification, quick-update prompt). Because this is a
# start="startup" service those modals can fire before Kodi's GUI exists
# and deadlock the boot -- the "hangs once after install/quick update,
# force-stop to recover" symptom. Block here until Home is actually live
# (bounded) so every dialog below has a real parent window.
if not gui_ready.wait_for_gui_ready():
    logging.log('[Startup] GUI unavailable; deferring dialogs', level=xbmc.LOGWARNING)
    sys.exit()

# SECONDARY PROFILE HOME (Profiles): stop here until a secondary profile's live home is verified.
if not profile_hooks.complete_home():
    sys.exit()

# FIRST-BOOT STABILIZER (race shield). Runs ONLY when the one-shot marker from a
# just-completed install is present. Warms POV up (user-visible countdown) and
# ReloadSkin()s so the FENtastic home widgets load against a ready POV instead of
# racing its first-run DB setup -> the "first boot freeze". No-op on every normal
# boot. Must run BEFORE the first-launch notification/contact modals below.
first_boot.first_boot_stabilize_if_needed()

# SHOW NOTIFICATIONS
if (profile_hooks.notifications_allowed() and CONFIG.ENABLE_NOTIFICATION == 'Yes'
        and CONFIG.get_setting('buildname')):
    notifications.show_notification()
else:
    logging.log('[Notifications] Not Enabled', level=xbmc.LOGINFO)

# FIRST BUILD LAUNCH: build skin switch notification
notifications.first_launch_skin_notice()

# APK installs that never ran a wizard-driven Fresh Install: register the build
build_check.autoset_buildname_for_apk()

######################################
# KODI-POV-IL - MODULAR OTA UPDATE + STRICT MANIFEST ENFORCEMENT.
# Polls manifest.json and updates addons whose version moved (silent background;
# only a Wizard or active-skin update forces a restart). Then heal_missing_addons
# does the bulletproof part: it physically verifies System.HasAddon(id) for EVERY
# required addon in the manifest + every content addon, and silently installs any
# that are missing -- e.g. a YouTube that timed out during a previous provisioning
# pass and was skipped. This is what makes a half-provisioned build self-complete
# on the very next launch instead of staying broken.
if CONFIG.get_setting('buildname'):
    maintenance_pass.run_startup_maintenance()

    ota.run_startup_ota()
    # Repair installed add-ons even when the manifest server is unavailable or
    # the updater raises. An upstream POV update can erase these hooks while
    # the network is down; patching must not depend on OTA success.
    try:
        from resources.libs.patch_engine import PatchEngine
        PatchEngine().run_if_changed()
    except Exception as _patch_err:
        logging.log("[PatchEngine] Boot-cycle run failed: {0}".format(_patch_err),
                    level=xbmc.LOGERROR)
######################################

# KOD-RD-IL - New Kodi ANDROID/WINDOWS version check on startup

if tools.platform() in ['android', 'windows'] and CONFIG.get_setting('buildname'):
    from resources.libs.system.kodi_version import kodi_version_update_check
    kodi_version_update_check()
######################################

# INSTALLED BUILD CHECK
if CONFIG.get_setting('installed') == 'true':
    logging.log("[Build Installed Check] Started", level=xbmc.LOGINFO)
    build_check.installed_build_check()
else:
    logging.log("[Build Installed Check] Not Enabled", level=xbmc.LOGINFO)

######################################
# KODI-POV-IL - IL Release Fetcher Service
######################################
try:
    from resources.libs.patches import il_fetcher
    il_fetcher.start_service()
except Exception as e:
    from resources.libs.common import logging
    import xbmc
    logging.log("[IL Fetcher] Failed to start background service: {0}".format(e), level=xbmc.LOGERROR)

language_sync.sync_pov_language_file()