"""First-boot stabilizer (race shield) for the boot after a fresh install."""
import os

import xbmc
import xbmcgui

from resources.libs.common.config import CONFIG
from resources.libs.common import logging


# ---------------------------------------------------------------------------
# First-boot stabilizer (race shield)
# ---------------------------------------------------------------------------
# THE RACE: the very first boot after a fresh install / modular update is chaos.
# FENtastic spins up instantly and fires a burst of PARALLEL widget directory
# queries at plugin.video.pov (popular movies, trending TV, ...) at the exact
# moment POV's own background service (Main/Sync Monitor) is registering and
# creating/migrating its databases for the first time. POV is not yet
# responsive, so the queries fail with "Unable to find plugin plugin.video.pov"
# / XFILE::CDirectory::GetDirectory, and Kodi's skin-variable evaluation engine
# can hard-DEADLOCK against the Python interpreter that is mid-addon-init -> a
# freeze/crash. The SECOND boot is always clean because POV finished initialising
# before the skin asked it anything.
#
# THE SHIELD: reproduce that "clean second boot" ON the first boot. A one-shot
# marker (armed at the end of a successful install, same pattern as
# .provisioned) tells the next boot to: hold briefly with a USER-VISIBLE
# countdown banner while POV warms up, then UpdateLocalAddons + ReloadSkin so the
# home widgets re-evaluate against a now-ready POV. It is fully guarded and the
# marker is ALWAYS cleared (finally), so a failure can never trap Kodi in a
# stabilize loop or a black screen. It is non-intrusive: no skin patching, no
# global watchdog, no modal that can steal input.
FIRST_BOOT_MARKER = 'kodipovil.first_boot_stabilize'
FIRST_BOOT_WARMUP_DEFAULT = 25  # seconds (bounded; overridable via setting)


def _first_boot_marker_path():
    return os.path.join(CONFIG.USERDATA, FIRST_BOOT_MARKER)


def _first_boot_warmup_seconds():
    """Warm-up budget, overridable by the optional wizard setting
    'first_boot_warmup_seconds' (clamped to a sane 5..120s). Falls back to the
    default when unset/invalid -- so it works whether or not the setting is
    declared."""
    try:
        raw = CONFIG.get_setting('first_boot_warmup_seconds')
        v = int(raw) if raw not in (None, '', False) else 0
        if 5 <= v <= 120:
            return v
    except Exception:
        pass
    return FIRST_BOOT_WARMUP_DEFAULT


def first_boot_stabilize_if_needed():
    """If the one-shot marker is present, warm POV up (bounded, with a live
    countdown banner) then UpdateLocalAddons + ReloadSkin so FENtastic's home
    widgets load against a ready POV instead of a half-initialised one.

    Returns True if the stabilizer ran. NEVER raises. Pending work survives
    until the required add-ons are ready."""
    try:
        if not os.path.exists(_first_boot_marker_path()):
            return False
    except Exception:
        return False

    banner = None
    completed = False
    try:
        total = _first_boot_warmup_seconds()
        header = CONFIG.ADDONTITLE
        pov_id = 'plugin.video.pov'
        # POV gets AT LEAST this long even if HasAddon flips true early -- the
        # addon being "present" is not the same as its DB setup being finished.
        min_warm = max(5, int(total * 0.4))

        try:
            banner = xbmcgui.DialogProgressBG()
            banner.create(header, 'מסיים אתחול ראשוני של הבילד, נא להמתין...')
        except Exception:
            banner = None

        monitor = xbmc.Monitor()
        logging.log('[FirstBoot] stabilizing: warming POV for up to {0}s'.format(total),
                    level=xbmc.LOGINFO)
        pov_present = False
        for elapsed in range(total):
            if monitor.abortRequested():
                break
            remaining = total - elapsed
            pct = int((elapsed + 1) / float(total) * 100)
            if banner is not None:
                try:
                    banner.update(
                        pct, header,
                        'מסיים אתחול ראשוני של הבילד... עוד {0} שניות'.format(remaining))
                except Exception:
                    pass
            try:
                pov_present = bool(xbmc.getCondVisibility('System.HasAddon({0})'.format(pov_id)))
            except Exception:
                pov_present = False
            # Early finish once POV is present AND it has had its minimum warm-up,
            # plus one extra beat for in-flight DB writes to settle.
            if pov_present and elapsed >= min_warm:
                if monitor.waitForAbort(2):
                    break
                break
            if monitor.waitForAbort(1):
                break

        if monitor.abortRequested() or not pov_present:
            logging.log('[FirstBoot] core not ready; retaining retry marker', level=xbmc.LOGWARNING)
            return False

        # POV is warm -> force a clean skin reload so home widgets re-query a
        # responsive POV. Guarded individually so one failing builtin can't abort
        # the rest.
        try:
            xbmc.executebuiltin('UpdateLocalAddons')
        except Exception:
            pass
        if not monitor.waitForAbort(1):
            try:
                xbmc.executebuiltin('ReloadSkin()')
            except Exception:
                pass
            # Let the skin finish redrawing before any first-launch modal below.
            monitor.waitForAbort(3)
        if monitor.abortRequested():
            return False
        completed = True
        logging.log('[FirstBoot] stabilizer completed', level=xbmc.LOGINFO)
        return True
    except Exception as e:
        logging.log('[FirstBoot] stabilizer error: {0}'.format(e), level=xbmc.LOGERROR)
        return False
    finally:
        # Clear only after success; incomplete provisioning retries next startup.
        try:
            mp = _first_boot_marker_path()
            if completed and os.path.exists(mp):
                os.remove(mp)
        except Exception:
            pass
        if banner is not None:
            try:
                banner.close()
            except Exception:
                pass
