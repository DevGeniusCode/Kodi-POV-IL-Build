"""Hydrate (or resume) the modular build install on a fresh / unfinished device."""
import os

import xbmc
import xbmcgui

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs.provisioning.banner import _close_progress
from resources.libs.provisioning.banner import _make_provisioning_progress
from resources.libs.provisioning.banner import _progress_call


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
    from resources.libs.provisioning import provisioner

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
    # shared ProvisionProgress tracker (one monotonic 0..99 value). It stays at the
    # top of Kodi, surviving every dialog/popup, until setup ends or we bail to
    # resume next launch. Keep BOTH references alive.
    progress, wait_banner = _make_provisioning_progress()
    from resources.libs.gui import install_progress
    view = install_progress.open_window(progress)
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
            ready, issues = fresh_install.provision(progress=progress, view=view)
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
        if not provisioner.is_provisioned():
            logging.log(
                "[Fresh Build Auto Install] provisioning did not complete (marker "
                "absent); will resume next launch.", level=xbmc.LOGWARNING)
            _close_progress(progress, wait_banner)
            return False

        # Everything from here is the 'finalize' slice of the progress bar
        # (media assets, runtime patching, live defaults, home verification).
        _progress_call(progress, 'begin_indeterminate', 'finalize')
        provisioner.set_stage(progress, view, provisioner.STAGE_FINALIZE)
        provisioner.mark_step(view, provisioner.FINALIZE_ROW, 'active')
        completed = fresh_install.finalize()
        provisioner.mark_step(view, provisioner.FINALIZE_ROW, 'done' if completed else 'failed')
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
    finally:
        install_progress.close_window(view)
