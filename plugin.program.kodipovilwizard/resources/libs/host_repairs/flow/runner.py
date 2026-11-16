# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""The build's host-repair runner (Phase 3).

Everything the subtitle add-on's service used to do for the build, in the same order, as its own long-lived script.
Kodi runs ONE service script per add-on, so the Wizard's startup.py launches host_repairs_service.py (which calls main()
here) as an independent script, first thing: nothing in the boot sequence can delay or cancel the repairs, and the
clock-bound POV language-invoker step is reached as early as before.
"""
import threading
import time

try:
    import xbmc
except ImportError:
    xbmc = None

from resources.libs.host_repairs.flow.af3_steps import _maybe_patch_af3_dialog_subtitles, _maybe_patch_hub_search, _maybe_restore_af3_font_early
from resources.libs.host_repairs.flow.pov_steps import _maybe_default_pov_autoplay, _maybe_revert_pov_always_resume, _maybe_revert_pov_autoplay
from resources.libs.host_repairs.flow.skin_steps import _maybe_default_fentastic_player, _maybe_default_nox_poster_rating, _maybe_enable_osd_autoclose, _maybe_patch_change_source_pause, _maybe_patch_choose_subs_buttons, _maybe_patch_nox_change_source, _maybe_patch_nox_next_episode, _maybe_patch_nox_osd_collision, _maybe_patch_skin_dialog_subtitles, _maybe_patch_skin_dialog_subtitles_rows, _point_subtitle_button
from resources.libs.host_repairs.flow.startup_pass import _run_build_startup_repairs
from resources.libs.host_repairs.flow.umbrella_steps import _start_service_mirror_keeper

LOCK = 'kodipovil.host_repairs.running'
LOCK_STALE_SECONDS = 120
LOCK_REFRESH_SECONDS = 30


def _run_build_flow(monitor):
    # Before the home-rows wait: AF3 without its Hebrew font is boxes.
    _maybe_restore_af3_font_early()

    # Keep the default search target correct even before a user opens the
    # POV/Umbrella picker, and heal skin XML after an upstream skin update.
    _maybe_patch_hub_search()

    # Heal the FENtastic player choice only if it's empty (prevents the
    # "player with nothing" bug) -- never overrides a value the user picked.
    # The quickfix no longer ships the skin settings file, so this is what
    # guarantees a valid default without reverting manual choices on update.
    _maybe_default_fentastic_player()

    # When the built-in engine is ON, DarkSubs is intentionally DISABLED
    # (Phase C). In that case we must NOT touch DarkSubs at all: patching it
    # and its reload cycle (disable+enable) would re-enable it -- fighting the
    # disable -- and run its code while disabled, which throws
    # "Unknown addon id 'service.subtitles.All_Subs'". So the entire DarkSubs
    # integration block is skipped when the engine is on. (Existing users with
    # the engine OFF are unaffected: DarkSubs stays enabled + patched as before.)
    try:
        from resources.libs.host_repairs import kodi_utils as _ku
        _engine_on = _ku.subs_get_bool('use_builtin_engine', False)
    except Exception:
        _engine_on = False

    # Point the skins' player "Choose subtitles" button at the right target.
    # The build skins gate it: it opens DarkSubs's picker unless the skin
    # setting says to open Kodi's native subtitle window. With the engine on,
    # DarkSubs is DISABLED -- so the DarkSubs button does nothing ("doesn't
    # work"). Flip the skin settings so the button opens the NATIVE Kodi
    # subtitle dialog (which now runs MoranSubs). Reverts to DarkSubs when the
    # engine is off. Affects the active skin (Estuary / FENtastic).
    _point_subtitle_button(_engine_on)

    # Fix the subtitle-picker dialog HEADER (rendered by Kodi from
    # the skin's DialogSubtitles.xml) to prefer our subs.player_filename
    # property over the built-in Player.Filename. Without this, even
    # if our other patchers set the property, the dialog title still
    # shows the URL basename / UUID.
    _maybe_patch_skin_dialog_subtitles()

    # The picker users actually see when they hit "Choose subtitles"
    # is Kodi's NATIVE DialogSubtitles, rendered by the active skin
    # (FENtastic in this build). DarkSubs is just one of the listed
    # services. The row layout (height, label/textbox dimensions)
    # is in skin.fentastic/xml/DialogSubtitles.xml. We bump
    # itemlayout/focusedlayout heights (and the inner Label2
    # textbox heights) so two wrapped lines of font12 fit without
    # clipping the bottom of the second line.
    _maybe_patch_skin_dialog_subtitles_rows()

    # Arctic Fuse 3 ships its subtitle dialog layout in a separate
    # file (Dialog_DialogSubtitles.xml) referenced via a named
    # include. The generic skin header patcher above won't find
    # $INFO[Player.FileName] there because it's wrapped in a
    # <param> rather than a <control type="label">. Dedicated AF3
    # patcher handles that file -- skin-gated, no-op when AF3 isn't
    # installed.
    _maybe_patch_af3_dialog_subtitles()

    # Add a "change source" button to the NOX skin's player OSD -- NOX
    # shipped without one, so a bad source mid-playback was a dead end.
    # Skin-gated (no-op unless skin.povil.nox is installed), XML-checked.
    _maybe_patch_nox_change_source()

    # That change-source button widened NOX's right-aligned OSD group, pushing
    # "הפרק הבא" left into the play controls during playback. Re-size the right-
    # group buttons back to their original total width to clear it. Runs AFTER
    # the change-source patcher so the button exists. Skin-gated, XML-checked.
    _maybe_patch_nox_osd_collision()

    # Repoint NOX's OSD "next episode" button: it used POV's old
    # play_media&next=1 (dropped in POV 6.07, so it errored). Point it at POV's
    # working next-episode list instead. Skin-gated, idempotent.
    _maybe_patch_nox_next_episode()

    # Turn NOX's rating/score circle ON for posters by default (one-shot, only
    # while NOX is the active skin; a later manual change sticks).
    _maybe_default_nox_poster_rating()

    # Point the player's subtitle button at MoranSubs's own chooser window
    # (FENtastic + Estuary pointed at the now-disabled DarkSubs; NOX's existing
    # subtitles button is rewired in place, not duplicated, to avoid widening
    # its OSD group). Skin-gated, XML-parse-checked, self-healing.
    _maybe_patch_choose_subs_buttons()

    # Make "החלף מקור" pause before opening the source screen (it regressed to
    # playing through in the background). Runs AFTER the change-source button
    # patchers above so Estuary's inserted button is present to patch.
    _maybe_patch_change_source_pause()

    # Same idea for POV: if we patched its sources.py and the user opted into
    # remember-source, cycle POV (deferred, idle-only) so it re-imports the
    # patched code this session. No-op unless armed above.
    try:
        from resources.libs.host_repairs import pov_reload
        pov_reload.reload_if_patched()
    except Exception:
        pass

    # CONTAINED HERE, NOT IN THE LOOP. The pass re-raises a BaseException
    # from a step on purpose, so that _publish_repairs_state is not
    # reached and the pass never looks finished. That is right. What was
    # wrong is where it landed: nothing on this path catches it, so a
    # single misbehaving repair step took main() down with it -- and
    # everything BELOW this line is what actually puts Hebrew subtitles on
    # screen. SubsFilenamePublisher, the autoplay listener, the pool
    # drainer and the maintenance loop are not related to any repair, and
    # none of them ran for the rest of that session.
    #
    # HANDOFF records a patcher raising SystemExit as something that has
    # actually happened here, so this is not hypothetical. Both properties
    # are kept: the pass still does not publish, and the service still
    # starts.
    try:
        _run_build_startup_repairs()
    except BaseException as e:
        try:
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.log(
                'the startup repair pass ended early ({0}: {1}); the '
                'subtitle service is starting anyway and the repairs are '
                'not recorded as done'.format(type(e).__name__, e),
                level='WARNING')
        except Exception:
            pass

    # Same idea for POV: if we patched its sources.py and the user opted into
    # remember-source, cycle POV (deferred, idle-only) so it re-imports the
    # patched code this session. No-op unless a patcher armed it.
    #
    # ARMED HERE, AFTER THE BUILD REPAIRS, AND THAT POSITION IS THE POINT.
    # Arming raises a flag that pov_reload.wait_until_settled() blocks on, and
    # three of its four callers are steps INSIDE _run_build_startup_repairs --
    # run inline on this thread, each with a 30s budget that is not shared. So
    # arming first meant every one of them could spend its budget waiting for a
    # cycle that had not started, come back False, leave its work undone, and
    # cost the subtitle service half a minute apiece for the privilege. That was
    # survivable while the cycle waited only for the home window to appear; it
    # is not now that it waits for the home screen to SETTLE.
    #
    # Arming last also closes a gap that was always there: note_patched() calls
    # made by anything running after this line were simply never seen, because
    # nothing asks again.
    try:
        from resources.libs.host_repairs import pov_reload
        pov_reload.reload_if_patched()
    except Exception:
        pass

    # If malformed Umbrella/Coco settings were repaired on disk, reconstruct
    # their CAddon objects this session after the POV cycle has settled. The
    # worker touches enabled add-ons only and records every disable first.
    try:
        from resources.libs.host_repairs import source_settings_reload
        source_settings_reload.reload_if_repaired()
    except Exception:
        pass

    # One-shot per skin: enable the active skin's own OSD auto-close (4s) so the
    # player bars hide after a few idle seconds. Detected from the skin's XML,
    # not from a list of skin names -- Nox ships the same feature FENtastic does
    # and was missed by the old name check.
    _maybe_enable_osd_autoclose()

    # One-shot: enable POV Auto Play + Always-Resume so "Continue Watching" is
    # one click (no source dialog, resumes where you stopped). Marker-gated.
    _maybe_default_pov_autoplay()

    # One-shot fix: undo the 0.2.158 mistake that forced POV Auto Play on
    # (it skipped the source dialog even on first watch). Restores the dialog.
    _maybe_revert_pov_autoplay()

    # One-shot: undo our forced always-resume so "Play from start" really starts
    # from 0 (it was resuming to the stop point). Marker-gated.
    _maybe_revert_pov_always_resume()

    _start_service_mirror_keeper(monitor)


def _heartbeat(window, monitor, stop):
    """Keep the single-runner lock fresh while this script lives, so a second launch can tell a live runner from a dead one."""
    while not stop.is_set():
        try:
            window.setProperty(LOCK, str(time.monotonic()))
        except Exception:
            pass
        if monitor.waitForAbort(LOCK_REFRESH_SECONDS) or stop.is_set():
            return


def main():
    if xbmc is None:
        return
    import xbmcgui
    window = xbmcgui.Window(10000)
    try:
        held = 0 <= time.monotonic() - float(window.getProperty(LOCK) or 'x') < LOCK_STALE_SECONDS
    except ValueError:
        held = False
    if held:
        return  # a runner started earlier in this Kodi session is still alive
    monitor = xbmc.Monitor()
    stop = threading.Event()
    window.setProperty(LOCK, str(time.monotonic()))
    threading.Thread(target=_heartbeat, args=(window, monitor, stop), daemon=True).start()
    try:
        _run_build_flow(monitor)
        # The service-mirror keeper (and the other helper threads) are daemons: stay alive until Kodi shuts down.
        while not monitor.abortRequested():
            if monitor.waitForAbort(LOCK_REFRESH_SECONDS):
                break
    finally:
        stop.set()
        try:
            window.clearProperty(LOCK)
        except Exception:
            pass
