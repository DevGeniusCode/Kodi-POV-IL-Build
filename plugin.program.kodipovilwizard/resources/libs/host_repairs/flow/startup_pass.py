# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""The startup-repair pass and the clock-bound POV language-invoker step that reads its start stamp.

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.
"""
import threading
import time

try:
    import xbmc
except ImportError:
    xbmc = None

from resources.libs.host_repairs.flow.af3_steps import _maybe_patch_af3_home, _maybe_show_af3_first_launch_dialog
from resources.libs.host_repairs.flow.common import _addon_version, _background_settle_seconds, _background_ui_busy
from resources.libs.host_repairs.flow.idanplus_steps import _maybe_fix_idanplus_vod, _maybe_fix_idanplus_youtube_id, _maybe_patch_idanplus_channels
from resources.libs.host_repairs.flow.pov_steps import _maybe_repair_addon_settings_integrity, _skip_pov_patchers
from resources.libs.host_repairs.flow.tonight_steps import _maybe_add_tonight_entry
from resources.libs.host_repairs.flow.umbrella_steps import _maybe_patch_mdblist_reauth, _maybe_patch_umbrella_language


REPAIRS_DONE_PROPERTY = 'kodipovil_startup_repairs_done'


_REPAIRS_STARTED = None


def _publish_repairs_state(value):
    """Announce the repair pass to anyone waiting on it.

    The value is this add-on's VERSION, not a bare 'true', and that is the
    whole point. The quick update installs new files and then has to know
    that the patchers have run FROM THE NEW CODE before it drops the other
    add-ons' cached Python interpreters. A boolean cannot tell the difference
    between 'the new service finished' and 'the old service, still running
    from before the update, finished its own pass' -- and acting on the
    second is exactly how a reload lands half-applied.
    """
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(REPAIRS_DONE_PROPERTY, value or '')
    except Exception:
        pass


def _report_patcher_health():
    """Schedule the full applied-repair audit after the home has settled.

    RUNS LAST in the tuple below, and that is load-bearing: the worker is only
    scheduled AFTER the pass has finished writing to the host add-ons, so a
    repair that just applied reads as applied. Anywhere earlier and it could
    race a repair the pass had not reached yet.

    Why it exists: the loop at the end of _run_build_startup_repairs calls
    `step()` and DISCARDS the return value, and all 122 step functions return
    None anyway. An anchor that stops matching is not an exception, so it never
    reaches the WARNING branch either -- it is a silent, ordinary-looking boot.
    That is exactly how five repairs died on POV 6.08.14 with nobody the wiser
    for days. patcher_health asks the host add-ons what they actually contain
    instead of trusting any of that.

    The scan walks every Python/XML/JSON file in every patched host. On a fast
    desktop that was sub-second; on two real ARM32 logs it took 4.5-13.2s and
    overlapped the first widget wave. Detection does not need to be synchronous
    with the repair pass. It remains a full scan with the same warnings/report,
    just on an idle daemon after the startup budget. If Kodi exits first, the
    next start schedules it again.
    """
    def _worker():
        try:
            monitor = xbmc.Monitor()
            if monitor.waitForAbort(_background_settle_seconds()):
                return
            # Never steal CPU/disk from playback, a modal, or a list Kodi is
            # visibly resolving. There is no deadline: this is diagnostic and
            # the next quiet interval is strictly better than a stutter now.
            while _background_ui_busy():
                if monitor.waitForAbort(2.0):
                    return
            from resources.libs.host_repairs import patcher_health, kodi_utils
            started = time.time()
            st = patcher_health.run()
            kodi_utils.log(
                'patcher health: {0}; deferred scan took {1:.1f}s'.format(
                    st, time.time() - started))
        except Exception as e:
            try:
                from resources.libs.host_repairs import kodi_utils
                kodi_utils.log(
                    'patcher health check unavailable: {0}'.format(e),
                    level='WARNING')
            except Exception:
                pass

    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception:
        pass


def _run_build_startup_repairs():
    """Run build-only UI/POV repairs early in Kodi startup.

    These repairs are idempotent and should settle the skin/menus before
    the user starts navigating. Slow steps are still logged individually
    so a future post-quick-update freeze can be traced to a concrete
    patcher instead of becoming guesswork.
    """
    try:
        monitor = xbmc.Monitor()
    except Exception:
        monitor = None

    # Clear first: a stale value from the PREVIOUS service instance would
    # otherwise satisfy a waiter the moment it looked, before this pass has
    # touched anything.
    _publish_repairs_state('')

    # Stamped so the invoker guard can report how far ahead of POV's own
    # check it actually got. The 19-second margin this ordering relies on was
    # measured on ONE device; this is what turns any future field log into a
    # second measurement instead of an assumption.
    global _REPAIRS_STARTED
    _REPAIRS_STARTED = time.time()

    steps = (
        # BEFORE ANY CROSS-ADDON SETTINGS WRITE.  A torn POV settings.xml makes
        # Kodi return empty/default values and refuse every attempted repair;
        # for source search that looks exactly like a disconnected debrid and
        # ends in "No External Scrapers Enabled" / "No Results".  Recover the
        # document (and Account Manager's canonical AllDebrid token) first.
        _maybe_repair_addon_settings_integrity,
        # BEFORE EVERYTHING, because it is racing a clock we do not control.
        # POV runs its own ReuseLanguageInvokerCheck a few seconds into its
        # service start, and if the setting and addon.xml disagree it throws
        # an English "SETTING/XML mismatch" dialog at the user and offers to
        # reload the profile. They disagree after any POV self-update: POV
        # ships addon.xml with the flag ON, ours is the setting that says OFF,
        # and POV is not in our quickfix at all -- it updates itself from
        # repository.kodifitzwell, so its own addon.xml comes back.
        #
        # Measured on a reporter's device (2026-08-17):
        #     21:00:39.430  our repair pass starts
        #     21:00:59.399  POV's ReuseLanguageInvokerCheck   <- the dialog
        #     21:01:08.934  this guard finally writes, 9.4s too late
        # From ~29 steps in, it lost the race every time. From here it writes
        # around 21:00:39, about 19 seconds ahead of POV's check, so in the
        # common case POV finds the two halves already in agreement. It is a
        # WIDENED MARGIN, not a synchronisation: this pass itself starts after
        # ~35 other calls in main(), one of which (_ensure_pov_enabled) can
        # retry for up to 10 seconds, so a slow enough device can still lose.
        # The guard logs how far ahead it got, so a field log can say whether
        # the margin holds rather than leaving it assumed.
        #
        # WHICH DIRECTION IT WRITES IS NO LONGER FIXED, and this comment used
        # to say the opposite -- "it can only ever turn the flag OFF" -- which
        # was true until 0.2.507 gave the direction to the
        # `pov_fast_navigation` setting. It is still OFF for anyone who has
        # not deliberately turned that on, and OFF is still the fix for the
        # Arctic Fuse 3 native crash. What running it EARLIER buys is the same
        # either way: it settles both halves before POV's own check looks at
        # them, so POV never shows its mismatch dialog. Do not move it down on
        # the strength of the old sentence.
        _maybe_patch_pov_language_invoker,
        _maybe_patch_idanplus_channels,
        _maybe_patch_mdblist_reauth,
        # AF3's compact 32-bit rows read these local shortcut folders. Seed or
        # upgrade them before AF3 exposes the rows, so a fresh profile cannot
        # race the skin and momentarily render an empty personal/network/genre
        # shelf. These are small local SQLite writes/checks, never web calls.
        _maybe_patch_af3_home,
        _maybe_patch_umbrella_language,
        _maybe_add_tonight_entry,
        _maybe_fix_idanplus_youtube_id,
        _maybe_fix_idanplus_vod,
        _maybe_show_af3_first_launch_dialog,
        # LAST on purpose: it reports on the pass above, so it has
        # to run after everything it reports on.
        _report_patcher_health,
    )
    # THE PACING IS A BUDGET NOW, NOT A CONSTANT PER STEP.
    #
    # The 0.25s below every step was introduced in b7ce297 ("Prevent quick
    # update startup freezes") when this tuple had TWENTY-SIX entries -- 6.5
    # seconds of yielding, which is what that change was tested at. It has 63
    # now, so the same line costs 15.75 seconds of pure sleeping on every boot
    # before a single step does any work, and nobody re-derived it as steps
    # were added. Two independent reviews measured it; one field log shows the
    # pass still running 53 seconds in.
    #
    # What the wait is FOR is not starving Kodi while the pass runs, and that
    # is a property of the total time yielded, not of the per-step figure. So
    # spread the same total the original was validated at over however many
    # steps there are. A short pass is unchanged (the cap is the old value), a
    # long one stops paying for its own length, and step 64 costs nothing.
    # THE FLOOR IS A SECOND CONSTRAINT, and it wins. Below ~130 steps the
    # budget binds and the pass yields 6.5s in total however long the tuple
    # gets. Above that the floor binds instead and the total starts growing
    # again -- which is correct, because a yield of nothing is not a yield and
    # the freeze this line exists to prevent would come back. It is also the
    # signal that the answer has stopped being "tune the constant": a pass that
    # long wants splitting, not a smaller sleep. So it says so, once, instead
    # of quietly costing seconds again the way 0.25 did.
    _pace = max(0.05, min(0.25, 6.5 / max(1, len(steps))))
    if _pace * len(steps) > 7.0:
        try:
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.log(
                'the startup repair pass has {0} steps and now yields {1:.1f}s '
                'in total; the per-step floor is binding, so this grows with '
                'every step added from here -- split the pass rather than '
                'shrinking the yield'.format(len(steps), _pace * len(steps)),
                level='WARNING')
        except Exception:
            pass
    for step in steps:
        try:
            if monitor and monitor.abortRequested():
                return
        except Exception:
            pass

        started = time.time()
        try:
            step()
        except Exception as e:
            try:
                from resources.libs.host_repairs import kodi_utils
                kodi_utils.log(
                    'build startup repair {0} failed: {1}'.format(
                        getattr(step, '__name__', 'unknown'), e),
                    level='WARNING')
            except Exception:
                pass
        except BaseException as e:
            # SystemExit or KeyboardInterrupt out of a step. `except Exception`
            # does not catch either, so this used to leave the pass -- and
            # everything queued behind it, including the step that puts Hebrew
            # subtitles on screen -- with NOTHING in the log: the run simply
            # stopped, indistinguishable from a hang. HANDOFF records a patcher
            # raising SystemExit as a thing that has actually happened here.
            #
            # Deliberately re-raised rather than swallowed: an aborted pass must
            # not reach _publish_repairs_state and look finished, because the
            # waiter would then reload POV against half-applied patches. The
            # only thing that changes is that it says so first.
            try:
                from resources.libs.host_repairs import kodi_utils
                kodi_utils.log(
                    'build startup repair {0} raised {1} and ended the whole '
                    'pass: {2}'.format(getattr(step, '__name__', 'unknown'),
                                       type(e).__name__, e),
                    level='WARNING')
            except Exception:
                pass
            raise

        try:
            if monitor and monitor.waitForAbort(_pace):
                return
        except Exception:
            pass
        if time.time() - started > 4:
            try:
                from resources.libs.host_repairs import kodi_utils
                kodi_utils.log(
                    'build startup repair {0} took {1:.1f}s'.format(
                        getattr(step, '__name__', 'unknown'),
                        time.time() - started),
                    level='WARNING')
            except Exception:
                pass

    # Only after every step has been through. An early return above means the
    # pass was aborted, and an aborted pass must NOT look finished -- the
    # waiter would then reload POV against half-applied patches.
    _publish_repairs_state(_addon_version())


def _maybe_patch_pov_language_invoker():
    """Hold POV's reuse-language-invoker flag where this device wants it.

    BY DEFAULT that is OFF, which closes the crash class the two guards above
    only narrow. Both of them remove a TRIGGER for "many POV invocations at
    once"; this removes what makes that burst fatal. POV ships
    <reuselanguageinvoker>true</reuselanguageinvoker>, so concurrent
    invocations share one Python interpreter and corrupt CPython's internals
    (a NULL refcount write inside python3.8.dll in the 2026-08-14 minidump,
    on a thread the Kodi log identifies as POV's). With the flag off, each
    invocation gets its own interpreter and the same burst is merely slower.

    SLOWER TURNED OUT TO BE MEASURABLE, so since 0.2.507 the direction is the
    `pov_fast_navigation` setting rather than a constant -- off out of the
    box, so this step does exactly what it always did unless somebody has
    deliberately asked for the speed back. The module header carries the
    measurement and the reason the obvious "narrow it to Arctic Fuse 3"
    shortcut is wrong.

    POV keeps this flag in TWO places -- a hidden `reuse_language_invoker`
    setting and its own addon.xml -- and runs a service that rewrites the xml
    from the setting, so the module writes both, setting first. Effective from
    the next Kodi start: Kodi has already read addon.xml by the time this pass
    runs. See the module header for why we do not force it live."""
    if _skip_pov_patchers():
        return
    try:
        from resources.libs.host_repairs import pov_language_invoker_guard, kodi_utils
    except Exception:
        return
    try:
        # Read the direction ONCE, here, and hand the same value to the write
        # and to the line that reports it. Reading it again for the log would
        # be a second answer to a question the module's own docstring calls
        # load-bearing, spent on prose.
        try:
            _dir = pov_language_invoker_guard._wanted()
        except Exception:
            _dir = None      # ensure_patched then decides for itself
        status = pov_language_invoker_guard.ensure_patched(_dir)
        try:
            _since = ('%.2fs into the repair pass'
                      % (time.time() - _REPAIRS_STARTED)
                      if _REPAIRS_STARTED else 'pass start not stamped')
        except Exception:
            _since = 'unknown'
        if status == 'patched':
            kodi_utils.log(
                'pov_language_invoker_guard: reuse-language-invoker set to '
                '%s (setting + addon.xml) at %s -- %s. Kodi read POV\'s '
                'addon.xml while building its add-on list, long before this '
                'pass ran, so the value it is RUNNING on is still the old one '
                'this session. POV notices that within a few seconds and '
                'offers a profile reload, which applies it without a second '
                'restart; declining just defers it to the next start. This '
                'number is the margin we beat POV\'s check by'
                % (_dir, _since, pov_language_invoker_guard.describe(_dir)),
                level='INFO')
        elif status == 'setting_only':
            kodi_utils.log(
                'pov_language_invoker_guard: setting written, addon.xml was '
                'not -- POV reconciles it from the setting on its next start',
                level='WARNING')
        elif status in ('unreadable', 'no_tag', 'write_failed'):
            kodi_utils.log(
                'pov_language_invoker_guard: ' + status, level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'pov_language_invoker_guard run failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
