# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Background daemon: prune the translation cache on Kodi start, then
# again every 24h while Kodi is running. Lightweight -- one stat
# pass over a small directory and we're done. Exits if Kodi tells
# us to shut down via Monitor.abortRequested().
#
# Everything is wrapped in try/except so a bug here can't take
# the rest of Kodi down with it.
#
# First-run disable: if a `.disable_on_first_run` marker file is
# present in the addon's directory (placed there by the rollout-1
# quick_update patch), this daemon disables itself the moment it
# wakes up and removes the marker. That way existing users get the
# addon installed but inactive, so they can review before opting in.
# Fresh Install builds never ship the marker, so they rely on Kodi's
# default "new user addons start disabled" behaviour.

import json
import os
import sys
import threading
import time

# `json` IS USED, AND WAS NOT IMPORTED. Two nested functions in the SubSync
# delay watch called json.dumps/json.loads with nothing named json in scope --
# a NameError, swallowed by their own `except Exception`, on every call. See
# _start_subsync_delay_watch, and tools/test_no_undefined_names.py, which is
# what found it.

try:
    import xbmc
except ImportError:
    xbmc = None

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
        from resources.lib import cache, sub_utils
        removed, freed = cache.prune()
        if removed:
            sub_utils.log(
                'Cache prune: {0} files removed, {1:.1f} MB freed'.format(
                    removed, freed / (1024.0 * 1024.0)),
                level='INFO')
        else:
            sub_utils.log('Cache prune: nothing to remove', level='DEBUG')
    except Exception as e:
        try:
            from resources.lib import sub_utils
            sub_utils.log('Cache prune failed: {0}'.format(e),
                           level='ERROR')
        except Exception:
            pass


# Version tag of the "re-apply fix_rtl_punctuation to every cached
# translated SRT" rollout. Translations cached before v0.1.6 didn't
# get the post-processor run on them, and even later caches may
# have slipped through if the regex didn't catch a specific edge.
# Bump this whenever fix_rtl_punctuation gains coverage and we want
# existing caches to benefit without the user manually clearing.
# Bump when fix_rtl_punctuation gains coverage that needs to flow
# through to already-cached translations.
#   v1 -- initial post-processor, simple-text leading-punct only
#   v2 -- HTML-tag-wrapped and dialogue-dash variants
#   v3 -- direction flipped: default is now 'reverse' (move punct
#         to line start) since the original 'auto' direction was
#         based on a wrong assumption about Kodi's BiDi behaviour
#   v4 -- reverse-mode dialogue dash fix: move leading "- " to the
#         logical line end so Kodi renders it on the right side.
#   v5 -- cue-timing repair: bound runaway cue durations. A mistyped timestamp
#         in an AI translation could leave one line frozen on screen for the
#         rest of the episode; this walk is the only mechanism that repairs an
#         ALREADY-cached translation without the user replaying that title.
#   v6: strip Arabic the AI leaked from the gender reference into a Hebrew
#       line -- see srt.strip_leaked_arabic. NOT every file here is ours: the
#       Google Translate fallback saves into this directory too, so the repair
#       is gated per file by srt.may_carry_arabic_leak.
#   v7: explicit RTL-base controls and style-run normalization.
#   v8: restore archived physical-order ellipses, dialogue marks and paired
#       closing quote/bracket punctuation before wrapping for Kodi.
CACHE_RTL_FIX_VERSION = '8'


def _maybe_repair_rtl_cache():
    """One-shot walk of cache/translated/, re-applying the current display and
    TIMING repairs to each file. Catches up translations that got cached before
    a post-processor was in place or before it handled a specific edge case.
    Marker-gated so it only runs once per CACHE_RTL_FIX_VERSION bump -- which is
    why the constant must be bumped whenever a new repair is added here, or
    every existing install skips the backfill forever."""
    try:
        from resources.lib import sub_utils, srt
    except Exception:
        return
    try:
        if sub_utils.get_setting('_rtl_fix_done', '') == \
                CACHE_RTL_FIX_VERSION:
            return
        translated_dir = os.path.join(
            sub_utils.cache_dir(), 'translated')
        n_scanned = n_repaired = 0
        if os.path.isdir(translated_dir):
            for fn in os.listdir(translated_dir):
                if not fn.endswith('.srt'):
                    continue
                p = os.path.join(translated_dir, fn)
                n_scanned += 1
                try:
                    with open(p, 'r', encoding='utf-8',
                              errors='replace') as f:
                        content = f.read()
                except OSError:
                    continue
                # cache/translated/ is NOT all our own output: the Google
                # Translate fallback saves here too, marked by a '.google'
                # sidecar. srt.may_carry_arabic_leak is the one place that rule
                # lives -- see it before adding a repair path.
                body = (srt.strip_leaked_arabic(content)
                        if srt.may_carry_arabic_leak(p) else content)
                fixed = srt.clamp_cue_durations(
                    srt.fix_rtl_punctuation(
                        body, legacy_engine='auto'))
                if fixed == content:
                    continue
                tmp = p + '.aitmp'
                try:
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(fixed)
                    os.replace(tmp, p)
                    n_repaired += 1
                except OSError:
                    try: os.remove(tmp)
                    except OSError: pass
        sub_utils.set_setting('_rtl_fix_done', CACHE_RTL_FIX_VERSION)
        sub_utils.log(
            'RTL cache repair v{0}: scanned {1}, repaired {2}'.format(
                CACHE_RTL_FIX_VERSION, n_scanned, n_repaired),
            level='INFO')
    except Exception as e:
        try:
            sub_utils.log(
                'RTL cache repair failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass



def _maybe_refresh_shared_sdh():
    """Warm the community-shared SDH set (Phase 3b) into the local cache from
    this background service, so the subtitle-ranking path can read it without a
    network call. use-gated + TTL-gated (at most once/day) inside refresh; a
    no-op when the pool isn't in use. Best-effort."""
    try:
        from resources.lib import sdh_pool
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
        from resources.lib import pool
    except Exception:
        return

    def _loop():
        try:
            if monitor.waitForAbort(20):   # let startup settle first
                return
            while not monitor.abortRequested():
                try:
                    from resources.lib import translate
                    translate.process_harvest_queue(
                        should_cancel=monitor.abortRequested)
                except Exception:
                    pass
                left = 0
                try:
                    _sent, left = pool.drain(
                        should_cancel=monitor.abortRequested)
                except Exception:
                    left = 0
                try:
                    backlog = bool(left) or pool.harvest_queue_len() > 0
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
                    from resources.lib import translate
                    translate.gp_later_tick(should_cancel=monitor.abortRequested)
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
            from resources.lib import subs_engine_bridge as _b
            _b.ensure_engine_settings()
            # Deliberate pre-imports (side effect only): load the two heaviest providers now, off the request path.
            from resources.lib.subs_engine.sources import opensubtitles as _o  # noqa: F401
            from resources.lib.subs_engine.sources import ktuvit as _k  # noqa: F401
            _hsm._dbg('drainer engine pre-imported in {0:.1f}s after startup '
                      'settled'.format(_t.time() - _pt0))
        except Exception as e:
            _hsm._dbg('drainer engine pre-import failed: ' + repr(e))

    def _loop():
        try:
            if monitor.waitForAbort(0.5):   # tiny settle, then poll fast
                return
            preload_at = time.time() + _background_settle_seconds()
            engine_ready = False
            while not monitor.abortRequested():
                picked_up = False
                try:
                    d = _hsm._warm_queue_dir()
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
                                _hsm._dbg('drainer picked up {0} (queued {1:.1f}s ago)'.format(
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
            from resources.lib import subsync as _ss
            try:
                # Split the old one-file probe cache now, at startup, rather
                # than at the first episode's first lookup (a one-time parse
                # of up to ~11 MB that holds the interpreter lock).
                _ss._migrate_probe_cache()
            except Exception:
                pass
            while not monitor.abortRequested():
                try:
                    _ss.drain_queue_once()
                except Exception:
                    pass
                if monitor.waitForAbort(1.0):
                    break
        except Exception as exc:
            try:
                from resources.lib import sub_utils
                sub_utils.log('subsync drainer stopped: {0}'.format(exc),
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
            from resources.lib import subsync as _ss
            from resources.lib import pool as _pool
            from resources.lib import sub_utils
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
                        rec = _ss.current_delivery_record()
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
                            rep = _ss.finalize_delay_session(
                                active, last_delay, watched)
                            if rep:
                                # Remember the viewer's correction locally only
                                # for the exact content-derived media cut.  The
                                # existing community report below stays backward
                                # compatible by namespacing the Worker's existing
                                # release field with that same signature.
                                _ss.store_human_verdict(rep)
                                _pool.report_sync(
                                    rep.get('info') or {}, rep['sub_hash'],
                                    _ss._sync_registry_release(
                                        rep['release'],
                                        rep.get('cut_signature') or ''),
                                    rep['scale'],
                                    rep['offset_ms'], rep['status'],
                                    origin='human')
                                reported.add(akey)
                                sub_utils.log(
                                    'subsync delay-watch: human report '
                                    '({0}, {1:+.0f}ms, watched {2}s)'.format(
                                        rep['status'], rep['offset_ms'],
                                        watched), level='INFO')
                        _ss.clear_delivery_record(active)
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
        from resources.lib import autosub_service
        autosub_service.start_if_enabled()
    except Exception:
        pass


def _maybe_prewarm_engine():
    try:
        from resources.lib import autosub_service
        autosub_service.prewarm_engine()
    except Exception:
        pass


def _maybe_default_fast_first_chunk():
    """One-shot: flip `fast_first_chunk` from the old default-off to
    the new default-on for existing users. Gated by a marker so it
    fires once per install; if the user later turns it off manually
    we don't re-flip on subsequent startups."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        if sub_utils.get_setting(
                '_fast_first_chunk_default_v2', '') == '1':
            return
        # Only flip users currently on the old default 'false' --
        # leaves any explicit 'true' alone.
        if sub_utils.get_setting('fast_first_chunk',
                                  'false') == 'false':
            sub_utils.set_setting('fast_first_chunk', 'true')
            sub_utils.log(
                'fast_first_chunk flipped to True (default v2 '
                'migration)', level='INFO')
        sub_utils.set_setting('_fast_first_chunk_default_v2', '1')
    except Exception as e:
        try:
            sub_utils.log(
                'fast_first_chunk migration failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


def _maybe_migrate_embedded_translation_mode():
    """One-shot bridge from the two hidden booleans to the explained mode list."""
    try:
        from resources.lib import sub_utils
        mode = sub_utils.embedded_translation_mode()
        sub_utils.log(
            'embedded translation mode ready: {0}'.format(mode), level='INFO')
    except Exception as e:
        try:
            sub_utils.log(
                'embedded mode migration failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


def _maybe_default_pool_on():
    """One-shot: turn the community pool ON (both pull and share) for existing
    users who are still on the old default-off. Gated by a marker so it fires
    once per install; if the user later turns either toggle off manually we
    don't re-enable on subsequent startups. New installs get it on via the
    settings.xml defaults; this covers everyone who installed before the
    default flip."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        if sub_utils.get_setting('_pool_default_on_v1', '') == '1':
            return
        # Only flip toggles still on the old default 'false'; leave an explicit
        # choice (already 'true') alone.
        for key in ('pool_use', 'pool_share'):
            if sub_utils.get_setting(key, 'false') == 'false':
                sub_utils.set_setting(key, 'true')
        sub_utils.set_setting('_pool_default_on_v1', '1')
        sub_utils.log('community pool enabled by default (migration v1)',
                       level='INFO')
    except Exception as e:
        try:
            sub_utils.log('pool default-on migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_force_gender_ref_arabic():
    """One-shot: turn the Arabic-gender-reference setting (gender_ref_arabic) ON
    for EVERYONE -- including users who previously had it off. It tested clean
    (gender accuracy ~27% -> ~90%+, no quality regression, full fallback when no
    Arabic aligns), so we want it on by default for the whole base.

    Unlike the gentle pool migration this forces 'true' unconditionally (not just
    when still on the old default). It is still marker-gated so it fires ONCE:
    if a user deliberately turns it off afterwards, that choice sticks and we
    don't re-enable on the next startup."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        if sub_utils.get_setting('_gender_ref_on_v1', '') == '1':
            return
        sub_utils.set_setting('gender_ref_arabic', 'true')
        sub_utils.set_setting('_gender_ref_on_v1', '1')
        sub_utils.log('Arabic gender reference enabled for everyone '
                       '(migration v1)', level='INFO')
    except Exception as e:
        try:
            sub_utils.log('gender_ref_arabic force-on migration failed: '
                           '{0}'.format(e), level='WARNING')
        except Exception:
            pass


def _maybe_tune_gemini3_defaults():
    """One-shot: move existing users to the validated Gemini 3 translation
    settings -- temperature 1.0 (Google's recommended default; 0.2 was our old
    default and degrades Gemini 3 reasoning) and thinking_level MEDIUM (the old
    'disabled'/0 left it at the expensive HIGH default, which truncates and
    garbles long chunks). Only flips values still on the OLD defaults, so a user
    who deliberately picked something else keeps it. Marker-gated -> fires once;
    a later manual change sticks."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        if sub_utils.get_setting('_gemini3_tune_v1', '') == '1':
            return
        # temperature: bump 0.2 (old default) -> 1.0; leave any other choice.
        try:
            t = float(sub_utils.get_setting('temperature', '') or '0.2')
        except (TypeError, ValueError):
            t = 0.2
        if abs(t - 0.2) < 0.005:
            sub_utils.set_setting('temperature', '1.0')
        # thinking: '' / '0' / 'disabled' (old default -> HIGH) -> 'medium'.
        th = (sub_utils.get_setting('thinking_budget', '') or '0').strip().lower()
        if th in ('', '0', 'disabled'):
            sub_utils.set_setting('thinking_budget', 'medium')
        sub_utils.set_setting('_gemini3_tune_v1', '1')
        sub_utils.log('Gemini 3 defaults tuned (temp 1.0 + thinking medium, '
                       'migration v1)', level='INFO')
    except Exception as e:
        try:
            sub_utils.log('gemini3 tune migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_bump_gemini_model():
    """One-shot: move existing users off the superseded default Gemini models to
    their newer, same-quota successors -- gemini-3.1-flash-lite -> 3.5-flash-lite
    (the free 500/day default) and 3.5-flash / 3.6-flash -> 3.8-flash (the paid
    regular-Flash pick; it was 3.7 until Google superseded that too, and this
    map points at the CURRENT pick rather than at a model the picker no longer
    offers -- landing a device on a dropped id and relying on the next
    migration to move it again is a correctness argument that depends on the
    order two functions happen to be called in). Both are drop-in upgrades (identical free-tier quota,
    better quality), so we rewrite the STORED model once. Only those two exact
    old ids are bumped; any other deliberate choice (3.1-flash, 2.5-*) is left
    alone, and an empty setting is left empty (translate falls back to the new
    default). Marker-gated -> fires once; a later manual pick sticks."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        # v2, NOT v1. The v1 marker is already '1' on every device that took
        # the 3.5 -> 3.6 bump, so reusing it would make this migration a no-op
        # for exactly the users who need it -- the ones already on 3.6. A new
        # id per bump is the only thing that makes a once-only migration
        # repeatable across releases.
        if sub_utils.get_setting('_gemini_model_bump_v2', '') == '1':
            return
        cur = (sub_utils.get_setting('model', '') or '').strip()
        new = {'gemini-3.1-flash-lite': 'gemini-3.5-flash-lite',
               'gemini-3.5-flash': 'gemini-3.8-flash',
               'gemini-3.6-flash': 'gemini-3.8-flash'}.get(cur)
        if new:
            sub_utils.set_setting('model', new)
            sub_utils.log('Gemini model bumped {0} -> {1} (migration v2)'.format(
                cur, new), level='INFO')
        sub_utils.set_setting('_gemini_model_bump_v2', '1')
    except Exception as e:
        try:
            sub_utils.log('gemini model bump migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_bump_gemini_model_38():
    """One-shot: move existing users off gemini-3.7-flash to gemini-3.8-flash.

    Google shipped 3.8 Flash as the current regular-Flash model and reclassified
    3.7 as "previous-generation"; the two sit on identical rate limits in every
    table on Google's own page (same TPM, same TPD, same free RPD), so this is a
    drop-in upgrade exactly like the 3.5/3.6 -> 3.7 bump before it. 3.7 keeps
    working, so nothing here is urgent -- but the picker no longer offers it,
    and leaving a stored id the list cannot show is how a settings screen ends
    up displaying a blank model.

    A NEW marker id, never a reused one: `_gemini_model_bump_v2` is already '1'
    on every device that took the previous bump, so reusing it would no-op this
    migration for precisely the users who need it. That mistake is written up in
    _maybe_bump_gemini_model() above; this is the same rule applied again.

    RETIRED, not "everything newer". The set below is every regular-Flash id
    the picker no longer offers. 3.6 and 3.5 Flash are in it because they were
    v2's job and v2 only fires once: a device that already ran v2 has left them,
    but listing them here means this migration is correct on its own rather than
    correct only because it happens to run after v2 in the same boot. Ordering
    is a fact about one file; a complete set is a fact about the migration.

    Every choice the picker still shows -- 3.5-flash-lite, 3.1-flash, either
    2.5 -- is a deliberate pick and is left alone, and an empty setting stays
    empty so translate.py's own default applies."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        if sub_utils.get_setting('_gemini_model_bump_v3', '') == '1':
            return
        retired = ('gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-3.5-flash')
        cur = (sub_utils.get_setting('model', '') or '').strip()
        if cur in retired:
            sub_utils.set_setting('model', 'gemini-3.8-flash')
            sub_utils.log('Gemini model bumped {0} -> gemini-3.8-flash '
                           '(migration v3)'.format(cur), level='INFO')
        sub_utils.set_setting('_gemini_model_bump_v3', '1')
    except Exception as e:
        try:
            sub_utils.log('gemini 3.8 model bump migration failed: {0}'
                           .format(e), level='WARNING')
        except Exception:
            pass


def _maybe_retire_gemini_31_flash():
    """One-shot: a stored 'gemini-3.1-flash' becomes the default model.

    Google has no model of that id (HTTP 404 for every key, Nov 2026), and
    0.3.78 took it out of the picker; a stored id the list cannot show is a
    blank model row (see _maybe_bump_gemini_model_38). translate.py falls
    back to the default model on a 404 anyway, so this only makes the setting
    say what is already happening. The two 2.5 ids stay: Google still serves
    them to older keys. A new marker, as every bump before it."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        if sub_utils.get_setting('_gemini_model_bump_v4', '') == '1':
            return
        cur = (sub_utils.get_setting('model', '') or '').strip()
        if cur == 'gemini-3.1-flash':
            sub_utils.set_setting('model', 'gemini-3.5-flash-lite')
            sub_utils.log('Gemini model gemini-3.1-flash -> '
                           'gemini-3.5-flash-lite: Google no longer has it '
                           '(migration v4)', level='INFO')
        sub_utils.set_setting('_gemini_model_bump_v4', '1')
    except Exception as e:
        try:
            sub_utils.log('gemini 3.1 retire migration failed: {0}'
                           .format(e), level='WARNING')
        except Exception:
            pass


def _maybe_lower_chunk_lines():
    """One-shot: move existing users to the smaller 50-line translation chunk.
    Live testing showed big chunks (100+) of graphically-explicit dialogue trip
    Google's prompt-level PROHIBITED_CONTENT block, while 50-line chunks stay
    under the threshold and translate cleanly -- with NO loss of gender accuracy
    or quality (the Arabic gender oracle is per-entry and the cast/context carry
    the rest). Only lowers values still at the OLD defaults (>=100 -> 50); a user
    who deliberately picked something smaller keeps it. Marker-gated -> once."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        if sub_utils.get_setting('_chunk_lines_50_v1', '') == '1':
            return
        try:
            cur = int(sub_utils.get_setting('chunk_lines', '') or '100')
        except (TypeError, ValueError):
            cur = 100
        if cur >= 100:
            sub_utils.set_setting('chunk_lines', '50')
            sub_utils.log('chunk_lines lowered {0} -> 50 (block-avoidance '
                           'migration v1)'.format(cur), level='INFO')
        sub_utils.set_setting('_chunk_lines_50_v1', '1')
    except Exception as e:
        try:
            sub_utils.log('chunk_lines migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass




def _maybe_force_pool_share():
    """One-shot rollout: turn community-pool SHARING on for EVERYONE, to grow the
    shared Hebrew pool as fast as possible. With pool_share on, every human
    Ktuvit Hebrew sub for a played title is mirrored to the pool in the
    background (the harvest), and AI translations are shared too -- so the pool
    fills for all users. Force-enabled once via a fresh marker (overriding a
    prior opt-out); a later MANUAL opt-out AFTER this run sticks. New installs
    already default on via settings.xml."""
    try:
        from resources.lib import sub_utils
        if sub_utils.get_setting('_pool_share_force_v1', '') == '1':
            return
        sub_utils.set_setting('pool_share', 'true')
        sub_utils.set_setting('_pool_share_force_v1', '1')
        sub_utils.log('pool_share force-enabled for everyone (rollout v1)',
                       level='INFO')
    except Exception as e:
        try:
            sub_utils.log('pool_share force migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_default_builtin_engine():
    """One-shot rollout: move EVERYONE from DarkSubs to MoranSubs's own built-in
    engine. Turns use_builtin_engine ON exactly once (and seeds engine_autosub
    ON so auto-search-and-apply works like DarkSubs did). Marker-gated, so a
    later manual opt-out STICKS -- if the user turns the engine (or autosub) off
    afterwards we never force it back on, on this or any future startup.

    Must run BEFORE _maybe_set_default_subtitle_service() and the _engine_on
    read in main(), so the rest of THIS startup already treats the engine as on
    (MoranSubs default)."""
    try:
        from resources.lib import sub_utils
    except Exception:
        return
    try:
        if sub_utils.get_setting('_builtin_engine_rollout_v2', '') == '1':
            return
        # Flip the master engine toggle on (covers users still on the old
        # default 'false', AND users where it drifted off so DarkSubs came back
        # with no translation -- re-forced once via the v2 marker).
        if sub_utils.get_setting('use_builtin_engine', 'false') != 'true':
            sub_utils.set_setting('use_builtin_engine', 'true')
        # Auto-search & apply on play, like DarkSubs's autosub. Defaults to
        # 'true' already (and was hidden while the engine was off), so this is
        # normally a no-op; flip only if a tester explicitly turned it off.
        if sub_utils.get_setting('engine_autosub', 'true') == 'false':
            sub_utils.set_setting('engine_autosub', 'true')
        sub_utils.set_setting('_builtin_engine_rollout_v2', '1')
        sub_utils.log('built-in engine enabled for everyone (rollout v2)',
                       level='INFO')
    except Exception as e:
        try:
            sub_utils.log('builtin engine rollout failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_set_default_subtitle_service():
    """When the engine is on, make MoranSubs the default subtitle service for
    movies + TV, so Kodi auto-runs it and pre-selects it when the subtitle
    dialog opens (the services list order itself is fixed by Kodi, but the
    default is what opens/searches first). Only writes on a mismatch; only
    when the engine is on (we don't override the user's choice otherwise)."""
    if xbmc is None:
        return
    try:
        from resources.lib import sub_utils
        if not sub_utils.get_bool('use_builtin_engine', False):
            return
    except Exception:
        return
    try:
        import json as _json
        for sid in ('subtitles.tv', 'subtitles.movie'):
            getq = _json.dumps({
                'jsonrpc': '2.0', 'id': 1,
                'method': 'Settings.GetSettingValue',
                'params': {'setting': sid},
            })
            cur = (_json.loads(xbmc.executeJSONRPC(getq) or '{}')
                   .get('result') or {}).get('value')
            if cur == ADDON_ID:
                continue
            setq = _json.dumps({
                'jsonrpc': '2.0', 'id': 1,
                'method': 'Settings.SetSettingValue',
                'params': {'setting': sid, 'value': ADDON_ID},
            })
            xbmc.executeJSONRPC(setq)
        xbmc.log('[{0}] set as default subtitle service (engine on)'
                 .format(ADDON_ID), level=xbmc.LOGINFO)
    except Exception:
        pass

def _maybe_seed_hebrew_subtitle_prefs():
    """One-shot: make Kodi's subtitle-language settings match the intent of
    installing a HEBREW subtitles addon. On foreign builds / clean Kodi the
    defaults point elsewhere, and the addon's language gate then disables
    itself ("preferred subtitle language is not Hebrew") -- the user installed
    a Hebrew subtitle addon and gets NOTHING (field log, Arctic Zephyr user).
    Adds Hebrew to "languages to download subtitles for" (keeping the user's
    other entries) and sets the preferred subtitle language to Hebrew when it
    currently names a different concrete language. Marker-gated: runs once,
    so a later manual change by the user STICKS."""
    if xbmc is None:
        return
    try:
        from resources.lib import sub_utils
        if sub_utils.get_setting('_he_subs_prefs_v1', '') == '1':
            return
    except Exception:
        return
    import json as _json

    def _get(sid):
        try:
            q = _json.dumps({'jsonrpc': '2.0', 'id': 1,
                             'method': 'Settings.GetSettingValue',
                             'params': {'setting': sid}})
            return (_json.loads(xbmc.executeJSONRPC(q) or '{}')
                    .get('result') or {}).get('value')
        except Exception:
            return None

    def _set(sid, value):
        try:
            q = _json.dumps({'jsonrpc': '2.0', 'id': 1,
                             'method': 'Settings.SetSettingValue',
                             'params': {'setting': sid, 'value': value}})
            xbmc.executeJSONRPC(q)
        except Exception:
            pass

    try:
        def _is_he(v):
            return str(v).strip().lower() in (
                'he', 'iw', 'heb', 'hebrew', 'עברית')
        dl = _get('subtitles.languages')
        pref = _get('locale.subtitlelanguage')
        if dl is None and pref is None:
            # JSON-RPC gave us nothing (transient failure at service start?).
            # Do NOT write the marker -- retry on the next service start, so
            # one bad boot can't leave the user unseeded forever.
            return
        if isinstance(dl, str):
            # Some builds return the list as a CSV string; seed in kind.
            parts = [p for p in dl.replace(';', ',').split(',') if p.strip()]
            if not any(_is_he(x) for x in parts):
                _set('subtitles.languages',
                     ','.join(parts + ['Hebrew']) if parts else 'Hebrew')
        elif isinstance(dl, list) and not any(_is_he(x) for x in dl):
            _set('subtitles.languages', dl + ['Hebrew'])
        elif dl in (None, []):
            _set('subtitles.languages', ['Hebrew'])
        specials = ('', 'none', 'forced_only', 'forcedonly', 'original',
                    'default', 'mediadefault')
        if (isinstance(pref, str)
                and pref.strip().lower() not in specials
                and not _is_he(pref)):
            _set('locale.subtitlelanguage', 'Hebrew')
        sub_utils.set_setting('_he_subs_prefs_v1', '1')
        sub_utils.log('Hebrew subtitle preferences seeded (one-shot)',
                       level='INFO')
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

    # Grow the shared Hebrew pool: force community-pool sharing ON for everyone
    # once (a later manual opt-out sticks). Must run before the harvest/drainer
    # below so it mirrors Ktuvit subs to the pool already this session.
    _maybe_force_pool_share()

    # ROLLOUT: switch everyone to MoranSubs's built-in engine (one-shot, marker-
    # gated). A later manual opt-out sticks (marker prevents re-forcing).
    _maybe_default_builtin_engine()

    # When the engine is on, make MoranSubs the default subtitle service so it
    # opens/searches first in the dialog.
    _maybe_set_default_subtitle_service()
    _maybe_seed_hebrew_subtitle_prefs()


    # Pre-warm the built-in sources engine (only when the user enabled it) so
    # the first subtitle search doesn't pay the heavy import cost inline.
    _maybe_prewarm_engine()

    # One-shot RTL punctuation repair of any cached translations
    # that were written before the post-processor caught their
    # specific edge case. Marker-gated so it only runs once.
    _maybe_repair_rtl_cache()

    # One-shot: flip `fast_first_chunk` default from off -> on for
    # existing users on the old default. Marker-gated.
    _maybe_default_fast_first_chunk()

    # Preserve the legacy embedded toggles, then make the explained mode selector
    # canonical. Runs for both build and standalone installations.
    _maybe_migrate_embedded_translation_mode()

    # One-shot: turn the community pool ON (pull + share) for existing users
    # still on the old default-off. New installs get it via settings.xml
    # defaults. Marker-gated so a later manual opt-out sticks.
    _maybe_default_pool_on()

    # One-shot: turn the Arabic-gender-reference setting ON for everyone (it
    # tested clean and lifts gender accuracy a lot). Forced once; a later manual
    # opt-out sticks. Marker-gated.
    _maybe_force_gender_ref_arabic()

    # One-shot: move existing users to the validated Gemini 3 translation
    # settings (temperature 1.0 + thinking medium). Marker-gated; respects a
    # deliberate manual choice.
    _maybe_tune_gemini3_defaults()
    # One-shot: bump the superseded default models to their same-quota
    # successors (3.1-flash-lite -> 3.5-flash-lite, 3.5/3.6-flash -> 3.8-flash).
    _maybe_bump_gemini_model()
    # And again for 3.8, which replaced 3.7 in the picker. Runs after the line
    # above so a device still on 3.5-flash lands on 3.8 in ONE boot rather than
    # two; it does not DEPEND on that order, because its retired set covers the
    # older ids as well.
    _maybe_bump_gemini_model_38()
    _maybe_retire_gemini_31_flash()
    # Lower chunk size to 50 (block-avoidance), one-shot for existing installs.
    _maybe_lower_chunk_lines()

    if monitor.abortRequested():
        return

    # Spin up the SubsFilenamePublisher player monitor. It needs to
    # outlive this function's local scope -- xbmc.Player subclasses
    # only receive callbacks while a strong reference exists. Pinning
    # it to the module is sufficient since `main` runs for the
    # lifetime of the service.
    global _subs_filename_publisher  # noqa: PLW0603
    try:
        from resources.lib import subs_filename_publisher
        _subs_filename_publisher = \
            subs_filename_publisher.SubsFilenamePublisher()
    except Exception as e:
        try:
            from resources.lib import sub_utils
            sub_utils.log(
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


# Kodi loads xbmc.service scripts by executing the module body, not by
# spawning them as `python service.py`, so __name__ is the module name
# here -- the `if __name__ == '__main__':` guard would skip main()
# entirely. Call it directly.
main()
