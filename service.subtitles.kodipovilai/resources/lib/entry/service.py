# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The service: first-run check, the start-up pass, the background drainers and the daily prune.

import json
import os
import sys
import threading
import time
try:
    import xbmc
except ImportError:
    xbmc = None
from resources.lib.entry.migrations import AFTER_PREWARM, BEFORE_PREWARM


ADDON_ID = 'service.subtitles.kodipovilai'
FIRST_RUN_MARKER = '.disable_on_first_run'

# Strong reference to the SubsFilenamePublisher player monitor,
# kept alive for the lifetime of the service. xbmc.Player subclasses
# stop receiving callbacks when garbage-collected, so this MUST not
# be a local variable.
_subs_filename_publisher = None

# Heavy work that is useful later must not compete with the first home-widget
# wave.  This matters disproportionately on 32-bit Android boxes: two field
# logs measured the subtitle engine's cold import at 13.1s and 19.7s.  It used
# to start while the skin was opening all of its lists.  A real request still
# takes the fast path at
# once; only speculative/background work is delayed.
_BACKGROUND_SETTLE_32 = 45.0
_BACKGROUND_SETTLE_OTHER = 15.0


def _background_settle_seconds():
    return (_BACKGROUND_SETTLE_32 if sys.maxsize <= 2 ** 32
            else _BACKGROUND_SETTLE_OTHER)


def _background_ui_busy():
    """Best-effort gate for CPU/disk work that has no user waiting on it."""
    try:
        return bool(xbmc.getCondVisibility('Player.HasMedia')
                    or xbmc.getCondVisibility('Container.IsUpdating')
                    or xbmc.getCondVisibility('System.HasVisibleModalDialog')
                    or xbmc.getCondVisibility('System.HasActiveModalDialog'))
    except Exception:
        return False


def _addon_version():
    try:
        import xbmcaddon
        return xbmcaddon.Addon().getAddonInfo('version') or ''
    except Exception:
        return ''


def _check_first_run_marker():
    """Return True iff we self-disabled (caller should exit)."""
    if xbmc is None:
        return False
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        marker = os.path.join(here, FIRST_RUN_MARKER)
        if not os.path.isfile(marker):
            return False
        try:
            os.remove(marker)
        except OSError:
            # If we can't delete the marker we still disable, but
            # we'll trip again next launch. Acceptable -- worst case
            # the user has to re-enable twice.
            pass
        try:
            xbmc.log(
                '[' + ADDON_ID + '] first-run marker found; '
                'self-disabling so user can review before opting in',
                level=xbmc.LOGINFO,
            )
        except Exception:
            pass
        # JSON-RPC is the canonical Kodi 19+ way to flip addon state.
        # executebuiltin('DisableAddon(...)') exists but is flakier
        # across Kodi versions, so we use it as a fallback only.
        try:
            import json as _json
            xbmc.executeJSONRPC(_json.dumps({
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'Addons.SetAddonEnabled',
                'params': {'addonid': ADDON_ID, 'enabled': False},
            }))
        except Exception:
            try:
                xbmc.executebuiltin('DisableAddon(' + ADDON_ID + ')')
            except Exception:
                pass
        return True
    except Exception:
        # Never let the first-run check itself crash the service.
        return False


def _prune_once():
    try:
        from resources.lib.core.cache import translation_cache
        from resources.lib.core import kodi
        removed, freed = translation_cache.prune()
        if removed:
            kodi.log(
                'Cache prune: {0} files removed, {1:.1f} MB freed'.format(
                    removed, freed / (1024.0 * 1024.0)),
                level='INFO')
        else:
            kodi.log('Cache prune: nothing to remove', level='DEBUG')
    except Exception as e:
        try:
            from resources.lib.core import kodi
            kodi.log('Cache prune failed: {0}'.format(e),
                           level='ERROR')
        except Exception:
            pass



def _maybe_refresh_shared_sdh():
    """Warm the community-shared SDH set (Phase 3b) into the local cache from
    this background service, so the subtitle-ranking path can read it without a
    network call. use-gated + TTL-gated (at most once/day) inside refresh; a
    no-op when the pool isn't in use. Best-effort."""
    try:
        from resources.lib.ai_translation.reuse import sdh_pool
        sdh_pool.refresh_shared_sdh()
    except Exception:
        pass


def _start_pool_queue_drainer(monitor):
    """Drive both pool queues from the long-lived service:
      1. process_harvest_queue() -- gently pull a couple of queued Ktuvit subs
         from Ktuvit (throttled, retrying) and feed them into the upload queue.
         This is what eventually mirrors EVERY release of a title without
         hammering Ktuvit or depending on the user staying on the video.
      2. drain() -- upload queued contributions to Telegram, one at a time with
         a throttle so a burst can't trip the bot's rate limit.
    Both survive playback ending / a Kodi restart (the queues are on disk).
    Backlog -> short interval; idle -> longer. Best-effort; never blocks."""
    try:
        from resources.lib.ai_translation.reuse import pool_contribute
    except Exception:
        return

    def _loop():
        try:
            if monitor.waitForAbort(20):   # let startup settle first
                return
            while not monitor.abortRequested():
                try:
                    from resources.lib.fetching import pool_harvest
                    pool_harvest.process_harvest_queue(
                        should_cancel=monitor.abortRequested)
                except Exception:
                    pass
                left = 0
                try:
                    _sent, left = pool_contribute.drain(
                        should_cancel=monitor.abortRequested)
                except Exception:
                    left = 0
                try:
                    backlog = bool(left) or pool_harvest.harvest_queue_len() > 0
                except Exception:
                    backlog = bool(left)
                # Backlog -> come back soon (keeps the gentle harvest moving);
                # empty -> idle, but still promptly so a manual pick uploads
                # within ~a minute.
                if monitor.waitForAbort(20 if backlog else 60):
                    break
        except Exception:
            pass

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def _start_gp_later_runner(monitor):
    """Every few minutes, the gender proofread's queue (translate.gp_later_tick,
    0.3.75): a translation whose proofread found gemini-3.7-flash busy is asked
    again later, one request at a time and spaced out (gp_later). A request
    takes minutes, so it has a thread of its own, apart from the uploads."""
    def _loop():
        try:
            if monitor.waitForAbort(300):   # let startup settle first
                return
            while not monitor.abortRequested():
                try:
                    from resources.lib.ai_translation.proofread import gp_jobs
                    gp_jobs.gp_later_tick(should_cancel=monitor.abortRequested)
                except Exception:
                    pass
                if monitor.waitForAbort(300):
                    break
        except Exception:
            pass

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def _start_he_warm_drainer(monitor):
    """Drain the Hebrew-availability warm queue from this long-lived service.

    POV's source window (a SEPARATE, short-lived interpreter) can't run the warm
    itself -- OpenSubtitles/Ktuvit need MoranSubs's own addon context + API keys.
    It used to kick a fresh interpreter via RunScript, but booting one (~3s) was
    slower than POV's ~2s scrape, so the "HEB NN%" badge only showed on the 2nd/
    3rd entry. Instead, prewarm() now drops a tiny JSON job on disk; we pick it up
    here within a fraction of a second and run the (parallelized) warm in the
    already-imported service process, so the cache is ready by the time the source
    dialog opens -> % on the FIRST entry. Best-effort; never blocks."""
    try:
        import json
        from resources.lib import he_sub_match as _hsm
    except Exception:
        return

    def _preimport_engine():
        """Pay the cold import once, outside the initial widget budget."""
        try:
            import time as _t
            _pt0 = _t.time()
            from resources.lib.fetching import bridge as _b
            _b.ensure_engine_settings()
            # Deliberate pre-imports (side effect only): load the two heaviest providers now, off the request path.
            from resources.lib.fetching import provider_adapter
            provider_adapter.load_provider('opensubtitles')  # noqa: F401
            provider_adapter.load_provider('ktuvit')  # noqa: F401
            _hsm.dbg('drainer engine pre-imported in {0:.1f}s after startup '
                      'settled'.format(_t.time() - _pt0))
        except Exception as e:
            _hsm.dbg('drainer engine pre-import failed: ' + repr(e))

    def _loop():
        try:
            if monitor.waitForAbort(0.5):   # tiny settle, then poll fast
                return
            preload_at = time.time() + _background_settle_seconds()
            engine_ready = False
            while not monitor.abortRequested():
                picked_up = False
                try:
                    d = _hsm.warm_queue_dir()
                    if d and os.path.isdir(d):
                        for fn in sorted(os.listdir(d)):
                            if monitor.abortRequested():
                                return
                            if not fn.endswith('.json'):
                                continue
                            path = os.path.join(d, fn)
                            info = None
                            age = -1.0
                            try:
                                import time as _t
                                age = _t.time() - os.path.getmtime(path)
                            except OSError:
                                pass
                            try:
                                with open(path, 'r', encoding='utf-8') as f:
                                    info = json.load(f)
                            except Exception:
                                info = None
                            # Claim the job (delete first) so a slow/failed warm
                            # can't make us reprocess it in a tight loop.
                            try:
                                os.remove(path)
                            except OSError:
                                pass
                            if info:
                                picked_up = True
                                _hsm.dbg('drainer picked up {0} (queued {1:.1f}s ago)'.format(
                                    (info.get('mk') or fn), age))
                                try:
                                    # A user is waiting, so do not impose the
                                    # speculative-preload delay. run_warm imports
                                    # the engine lazily and starts immediately.
                                    _hsm.run_warm(info)
                                    engine_ready = True
                                except Exception:
                                    pass
                except Exception:
                    pass
                # With no real title waiting, import only after the home-widget
                # wave and only while Kodi is quiet. This preserves first-title
                # HEB availability: a title queued before the deadline bypasses
                # this gate above instead of waiting for it.
                if (not engine_ready and not picked_up
                        and time.time() >= preload_at
                        and not _background_ui_busy()):
                    _preimport_engine()
                    engine_ready = True
                # Sub-second poll so prewarm -> warm start is nearly immediate.
                if monitor.waitForAbort(0.2):
                    break
        except Exception:
            pass

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def _start_subsync_drainer(monitor):
    """Drain the SubSync deep-verify queue (see subsync._enqueue_deep) in this
    long-lived service. Jobs are rare (once per new subtitle+release pair) and
    each can take 10-30s (oracle download / container probe / Gemini audio),
    which is exactly why they must not run inline in resolve(). Best-effort."""
    def _loop():
        try:
            if monitor.waitForAbort(1.0):
                return
            from resources.lib.sync import jobs
            from resources.lib.sync.probes import cache
            try:
                # Split the old one-file probe cache now, at startup, rather
                # than at the first episode's first lookup (a one-time parse
                # of up to ~11 MB that holds the interpreter lock).
                cache.migrate_probe_cache()
            except Exception:
                pass
            while not monitor.abortRequested():
                try:
                    jobs.drain_queue_once()
                except Exception:
                    pass
                if monitor.waitForAbort(1.0):
                    break
        except Exception as exc:
            try:
                from resources.lib.core import kodi
                kodi.log('subsync drainer stopped: {0}'.format(exc),
                               level='WARNING')
            except Exception:
                pass

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def _start_subsync_delay_watch(monitor):
    """The HUMAN sync anchor (SubSync S3): while a MoranSubs-delivered
    subtitle plays, sample the user's manual subtitle delay (JSON-RPC); when
    playback ends, a settled non-zero delay becomes a community FIXABLE
    report via pool.report_sync (share-gated, fire-and-forget); an untouched
    delay is not timing evidence and is never reported. One report per
    (subtitle, release) pair per Kodi session. This is what resolves files no
    algorithm can anchor (dubbed re-encodes with no subs anywhere)."""
    def _delay_now():
        try:
            raw = xbmc.executeJSONRPC(json.dumps({
                'jsonrpc': '2.0', 'id': 1,
                'method': 'Player.GetProperties',
                'params': {'playerid': 1,
                           'properties': ['subtitledelay']}}))
            return float((json.loads(raw).get('result') or {})
                         .get('subtitledelay') or 0.0)
        except Exception:
            return 0.0

    def _watching_with_subtitles():
        # Paused time or a hidden subtitle is not "watching this subtitle".
        try:
            return (not xbmc.getCondVisibility('Player.Paused')
                    and xbmc.getCondVisibility('VideoPlayer.SubtitlesEnabled'))
        except Exception:
            return False

    def _loop():
        try:
            if monitor.waitForAbort(2.0):
                return
            from resources.lib.sync import delivery_record, verdicts
            from resources.lib.ai_translation.reuse import pool_contribute as _pool
            from resources.lib.core import kodi
            active, watched, last_delay = None, 0, 0.0
            reported = set()
            while not monitor.abortRequested():
                try:
                    playing = False
                    try:
                        playing = xbmc.Player().isPlayingVideo()
                    except Exception:
                        playing = False
                    if playing:
                        rec = delivery_record.current_delivery_record()
                        if rec and (active is None
                                    or rec.get('key') != active.get('key')
                                    or float(rec.get('ts') or 0)
                                    != float(active.get('ts') or 0)):
                            active, watched, last_delay = rec, 0, 0.0
                        elif not rec:
                            # A piecewise correction deliberately disables
                            # scalar delay learning. Do not keep accumulating
                            # watch time against the subtitle it replaced.
                            active, watched, last_delay = None, 0, 0.0
                        if active is not None:
                            # Sample even while paused: a delay set during a
                            # pause and then stopped is still the viewer's fix.
                            last_delay = _delay_now()
                            if _watching_with_subtitles():
                                watched += 10
                    elif active is not None:
                        akey = active.get('key') or ''
                        if akey and akey not in reported:
                            rep = verdicts.finalize_delay_session(
                                active, last_delay, watched)
                            if rep:
                                # Remember the viewer's correction locally only
                                # for the exact content-derived media cut.  The
                                # existing community report below stays backward
                                # compatible by namespacing the Worker's existing
                                # release field with that same signature.
                                verdicts.store_human_verdict(rep)
                                _pool.report_sync(
                                    rep.get('info') or {}, rep['sub_hash'],
                                    verdicts.sync_registry_release(
                                        rep['release'],
                                        rep.get('cut_signature') or ''),
                                    rep['scale'],
                                    rep['offset_ms'], rep['status'],
                                    origin='human')
                                reported.add(akey)
                                kodi.log(
                                    'subsync delay-watch: human report '
                                    '({0}, {1:+.0f}ms, watched {2}s)'.format(
                                        rep['status'], rep['offset_ms'],
                                        watched), level='INFO')
                        delivery_record.clear_delivery_record(active)
                        active, watched, last_delay = None, 0, 0.0
                except Exception:
                    pass
                if monitor.waitForAbort(10.0):
                    break
        except Exception:
            pass

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def _maybe_start_autosub_player():
    """Register the play-start listener (autosub_service holds the Player
    reference in its module STATE, which outlives this call).

    It always snapshots the file's embedded subtitle streams -- the picker's
    "[מובנה] XX" and "תרגום מובנה → עברית (AI)" rows are built from that
    snapshot -- and auto-searches Hebrew only when engine_autosub is on."""
    try:
        from resources.lib.autosub import player
        player.start_if_enabled()
    except Exception:
        pass


def _maybe_prewarm_engine():
    try:
        from resources.lib.autosub import player
        player.prewarm_engine()
    except Exception:
        pass


def _start_shared_sdh_refresh(monitor):
    """Standalone: warm the community-shared SDH set once per start. The build
    does this inside its startup repairs; without the build nothing else would,
    and the ranking path only reads the local cache. Off the main thread and
    after the startup settle -- it is a network call."""
    def _worker():
        try:
            if monitor.waitForAbort(_background_settle_seconds()):
                return
        except Exception:
            pass
        _maybe_refresh_shared_sdh()

    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception:
        pass



def main():
    if xbmc is None:
        return

    # Register before startup repairs. A monitor created after Kodi starts
    # stopping this service can miss the earlier abort notification.
    monitor = xbmc.Monitor()

    # First-run handshake: if a quick_update patch dropped the
    # disable marker, opt the user back out so they can review
    # before activating. The marker is consumed on first read so
    # subsequent enables behave normally.
    if _check_first_run_marker():
        return

    # Start the Hebrew-availability warm drainer FIRST -- before the start-up
    # migrations below. Otherwise the drainer thread isn't spawned yet
    # when the user plays something right after boot, so the first title's warm
    # sits queued for several seconds and misses the source window's first-entry
    # wait. It's a cheap idle poll until a job appears.
    try:
        _start_he_warm_drainer(xbmc.Monitor())
    except Exception:
        pass

    # SubSync deep-verify worker: resolve() delivers the subtitle IMMEDIATELY
    # and queues the slow verification (oracle download / file probe / audio)
    # here, so the autosub overlay / picker never waits on it; on a proven fix
    # the worker swaps the playing subtitle in place (subsync.run_deep_job).
    try:
        _start_subsync_drainer(xbmc.Monitor())
    except Exception:
        pass

    # SubSync S3 human anchor: watch the viewer's manual subtitle delay and
    # turn a settled fix / a long clean watch into a community sync report.
    try:
        _start_subsync_delay_watch(xbmc.Monitor())
    except Exception:
        pass

    # Initial prune.
    _prune_once()

    for step in BEFORE_PREWARM:
        step()


    # Pre-warm the built-in sources engine (only when the user enabled it) so
    # the first subtitle search doesn't pay the heavy import cost inline.
    _maybe_prewarm_engine()

    for step in AFTER_PREWARM:
        step()

    if monitor.abortRequested():
        return

    # Spin up the SubsFilenamePublisher player monitor. It needs to
    # outlive this function's local scope -- xbmc.Player subclasses
    # only receive callbacks while a strong reference exists. Pinning
    # it to the module is sufficient since `main` runs for the
    # lifetime of the service.
    global _subs_filename_publisher  # noqa: PLW0603
    try:
        from resources.lib.ui import filename_publisher
        _subs_filename_publisher = \
            filename_publisher.SubsFilenamePublisher()
    except Exception as e:
        try:
            from resources.lib.core import kodi
            kodi.log(
                'SubsFilenamePublisher init failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass

    # Phase C: register the auto-on-play Hebrew listener (gated; only when the
    # built-in engine + autosub are on). The loop below keeps us alive.
    _maybe_start_autosub_player()

    if monitor.abortRequested():
        return

    # Drain the persistent pool upload queue here, on the long-lived service.
    # Shared Ktuvit subtitles are queued to disk the moment they're downloaded
    # (so they survive the user leaving the video or restarting Kodi) and
    # uploaded from this thread one at a time with a throttle -- never bursting
    # past Telegram's bot rate limit. Best-effort; never blocks.
    _start_shared_sdh_refresh(monitor)
    _start_pool_queue_drainer(monitor)
    _start_gp_later_runner(monitor)
    # (the Hebrew warm drainer was already started at the top of main(), before
    # the start-up migrations, so it's alive for the first play of the session)

    # 24h between passes. waitForAbort returns True when Kodi is
    # shutting down, so we just need to loop until that fires.
    interval_seconds = 24 * 3600
    while not monitor.abortRequested():
        if monitor.waitForAbort(interval_seconds):
            break
        _prune_once()
