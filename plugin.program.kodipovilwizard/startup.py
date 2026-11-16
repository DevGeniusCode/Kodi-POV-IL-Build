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
import xbmcgui

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


# Optional: a missing/broken progress helper must never crash startup or abort the
# install -- the banner then simply stays a static "please wait" message.
try:
    from resources.libs.provision_progress import ProvisionProgress
except Exception:
    ProvisionProgress = None


# Polite Hebrew wait message shown for the whole provisioning run.
PROVISION_WAIT_MSG_HE = 'אנא המתינו עד להשלמת ההתקנה וטעינת מסך הבית.'


def _make_provisioning_banner():
    """Create a persistent, NON-MODAL top banner for the entire fresh-install /
    provisioning run.

    A ``DialogProgressBG`` renders in Kodi's top-right corner and -- unlike a
    modal dialog or a transient notification -- STAYS VISIBLE behind every
    subsequent install dialog / first-run popup until we explicitly close it (or
    Kodi shuts down at the end of setup). It never grabs focus or input, so there
    is NO global watchdog and nothing to fight. Returns the dialog object, which
    the caller MUST keep a reference to (letting it be garbage-collected would
    close it), or ``None`` if it could not be created.

    It starts at 0%. From then on the ONLY thing that updates it is the shared
    ProvisionProgress tracker (see _make_provisioning_progress).
    """
    try:
        banner = xbmcgui.DialogProgressBG()
        banner.create(CONFIG.ADDONTITLE, PROVISION_WAIT_MSG_HE)
        banner.update(0, CONFIG.ADDONTITLE, PROVISION_WAIT_MSG_HE)
        return banner
    except Exception:
        return None


def _close_provisioning_banner(banner):
    try:
        if banner is not None:
            banner.close()
    except Exception:
        pass


def _make_provisioning_progress():
    """Create the banner and wrap it in the shared, monotonic ProvisionProgress
    tracker (its ticker thread is the single banner writer and also drives the
    indeterminate steps: dependency resolution, config apply, finalize).

    Returns (progress_or_None, banner_or_None). Keep BOTH references alive for the
    whole run. If the tracker is unavailable the plain banner is still shown."""
    banner = _make_provisioning_banner()
    if ProvisionProgress is None:
        return None, banner
    try:
        progress = ProvisionProgress(banner, CONFIG.ADDONTITLE, PROVISION_WAIT_MSG_HE)
        progress.start()
        return progress, banner
    except Exception as err:
        logging.log("[Fresh Build Auto Install] progress tracker unavailable: {0}".format(err),
                    level=xbmc.LOGWARNING)
        return None, banner


def _progress_call(progress, method, *args):
    """Call a tracker method; a progress problem must never break the install."""
    if progress is None:
        return
    try:
        getattr(progress, method)(*args)
    except Exception:
        pass


def _close_progress(progress, banner):
    """Bail-out path: stop the tracker AND close the banner."""
    if progress is not None:
        try:
            progress.close()
        except Exception:
            pass
    _close_provisioning_banner(banner)


def fresh_build_auto_install_if_needed():
    """Hydrate (or RESUME) the modular build install.

    Gated on the persistent .provisioned marker rather than buildname/installed,
    so a setup the user force-closed mid-provisioning (addons half-installed,
    config.zip never applied) is RESUMED on the next launch instead of being left
    permanently broken. run_fresh_install() is idempotent -- present addons are
    skipped, the config pack self-applies once -- and it writes the marker only
    after the WHOLE sequence completes.
    """
    from resources.libs import fresh_install
    from resources.libs.modular_updater import ModularUpdater

    # 1. The updater writes .provisioned before startup finishes the build flags,
    # media assets and first-boot setup. If Kodi dies in that interval, finish
    # this tail on the next boot instead of treating the install as complete.
    provision_marker = os.path.join(CONFIG.USERDATA, 'kodipovil.provisioned')
    in_progress_marker = os.path.join(CONFIG.USERDATA, fresh_install.PENDING)
    resume_finalization = (os.path.isfile(provision_marker) and
                           os.path.isfile(in_progress_marker))
    if resume_finalization and not os.path.isfile(os.path.join(
            CONFIG.USERDATA, 'kodipovil.fresh_gui_defaults.xml')):
        resume_finalization = False

    # Already fully provisioned end-to-end -> nothing to do.
    if os.path.isfile(provision_marker) and not os.path.isfile(in_progress_marker):
        return False

    # 2. Marker absent AND setup incomplete -> (re)run the modular fresh install
    #    to FINISH the job. Covers a brand-new device AND a force-closed setup
    #    (pov may already be on disk, but config and/or other addons never
    #    landed -- exactly the case that used to break the build permanently).
    build_name = CONFIG.BUILDNAME_DEFAULT
    build_version = CONFIG.BUILDVERSION_DEFAULT

    if not resume_finalization:
        try:
            fresh_install.begin()
        except OSError as exc:
            logging.log('[Fresh Build Auto Install] cannot persist resume marker: '
                        '{0}'.format(exc), level=xbmc.LOGERROR)
            return False

    # Persistent "please wait" banner for the whole provisioning, driven by the
    # shared ProvisionProgress tracker so it moves in lockstep with the
    # install window's overall bar (same monotonic 0..99 value). It stays at the
    # top of Kodi, surviving every dialog/popup, until setup ends or we bail to
    # resume next launch. Keep BOTH references alive.
    progress, wait_banner = _make_provisioning_progress()
    try:
        tools.ensure_folders(CONFIG.PACKAGES)
        logging.log(
            "[Fresh Build Auto Install] Modular fresh install / resume of {0} v{1}".format(
                build_name, build_version), level=xbmc.LOGINFO)

        if resume_finalization:
            logging.log('[Fresh Build Auto Install] resuming post-provision '
                        'finalization.', level=xbmc.LOGINFO)
        else:
            # A transient repository/download failure can recover while the
            # installer remains open; don't require several manual restarts.
            ready, issues = fresh_install.provision(progress=progress)
            if not ready:
                _close_progress(progress, wait_banner)
                xbmcgui.Dialog().ok(CONFIG.ADDONTITLE, fresh_install.failure_message(issues))
                return False

        # Sanity gate: the build engine must be on disk AND run_fresh_install
        # must have written the .provisioned marker (i.e. it ran to completion
        # and was NOT force-closed mid-provisioning). If either is missing, leave
        # the flags untouched so the next launch resumes.
        if not os.path.exists(os.path.join(CONFIG.ADDONS, 'service.subtitles.kodipovilai')):
            logging.log(
                "[Fresh Build Auto Install] build engine missing after install; "
                "will resume next launch.", level=xbmc.LOGERROR)
            _close_progress(progress, wait_banner)
            return False
        if not ModularUpdater.is_provisioned():
            logging.log(
                "[Fresh Build Auto Install] provisioning did not complete (marker "
                "absent); will resume next launch.", level=xbmc.LOGWARNING)
            _close_progress(progress, wait_banner)
            return False

        # Everything from here is the 'finalize' slice of the progress bar
        # (media assets, runtime patching, live defaults, home verification).
        _progress_call(progress, 'begin_indeterminate', 'finalize')
        completed = fresh_install.finalize()
        if completed:
            _progress_call(progress, 'complete', 'finalize')
        _close_progress(progress, wait_banner)
        return completed
    except Exception as err:
        logging.log(
            "[Fresh Build Auto Install] Failed: {0}".format(err),
            level=xbmc.LOGERROR,
        )
        _close_progress(progress, wait_banner)
        return False


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

fresh_build_auto_install_if_needed()
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

    try:
        from resources.libs.modular_updater import ModularUpdater
        _mu = ModularUpdater(background=True)
        _mu.run_update_check()        # version bumps for installed addons + config
        _mu.heal_missing_addons()     # strict HasAddon enforcement -> install missing

    except Exception as _modular_err:
        logging.log("[ModularUpdater] Startup check failed: {0}".format(_modular_err),
                    level=xbmc.LOGERROR)
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
    from resources.libs.wizard import kodi_version_update_check
    kodi_version_update_check()
######################################

# INSTALLED BUILD CHECK
if CONFIG.get_setting('installed') == 'true':
    logging.log("[Build Installed Check] Started", level=xbmc.LOGINFO)
    build_check.installed_build_check()
else:
    logging.log("[Build Installed Check] Not Enabled", level=xbmc.LOGINFO)

# KODI-POV-IL - Auto-refresh the on-demand NOX skin pack for users who are
# already ON it. On-demand skins are normally only (re)installed from Switch
# Skin; this makes a published NOX update reach existing NOX users on their
# next quick_update + restart, exactly like the other skins, without them
# having to toggle skins. Version-gated, so it never re-downloads needlessly.
try:
    if CONFIG.get_setting('buildname'):
        from resources.libs import wizard as _wiz_skin
        _wiz_skin.auto_update_active_skin_pack()
except Exception as _skin_upd_err:
    logging.log("[Skin Auto Update] startup hook failed: {0}".format(_skin_upd_err),
                level=xbmc.LOGERROR)

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