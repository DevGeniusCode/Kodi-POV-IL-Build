# Clean standalone service for Kodi POV IL AI Subtitles.
#
# This file is intentionally minimal. It does not install or heal the
# Kodi POV IL build tools, does not rewrite POV menus/favourites/home nodes,
# and does not touch unrelated update state. It only keeps the AI
# subtitle flow and required DarkSubs/OpenSubtitles integration alive.

import json
import os
import threading

try:
    import xbmc
except ImportError:
    xbmc = None

ADDON_ID = 'service.subtitles.kodipovilai'
FIRST_RUN_MARKER = '.disable_on_first_run'
_subs_filename_publisher = None
TEMP_PURGE_VERSION = '2'
# Keep in step with addons/.../service.py. Bump whenever a new repair is added
# below, or every existing install skips the backfill forever.
CACHE_RTL_FIX_VERSION = '8'


def _check_first_run_marker():
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
            pass
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
        return False


def _prune_once():
    try:
        from resources.lib import cache, kodi_utils
        removed, freed = cache.prune()
        if removed:
            kodi_utils.log(
                'Cache prune: {0} files removed, {1:.1f} MB freed'.format(
                    removed, freed / (1024.0 * 1024.0)),
                level='INFO')
        else:
            kodi_utils.log('Cache prune: nothing to remove', level='DEBUG')
    except Exception:
        pass


def _maybe_purge_temp_once():
    try:
        from resources.lib import kodi_utils
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_temp_purge_done', '') == TEMP_PURGE_VERSION:
            return
        temp_dir = kodi_utils.translate_path('special://temp/')
        if temp_dir and os.path.isdir(temp_dir):
            for fn in os.listdir(temp_dir):
                if fn.lower().endswith(('.srt', '.sub', '.ass', '.ssa', '.vtt')):
                    try:
                        os.remove(os.path.join(temp_dir, fn))
                    except OSError:
                        pass
        kodi_utils.set_setting('_temp_purge_done', TEMP_PURGE_VERSION)
    except Exception:
        pass


def _maybe_repair_rtl_cache():
    try:
        from resources.lib import kodi_utils, srt
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_rtl_fix_done', '') == CACHE_RTL_FIX_VERSION:
            return
        translated_dir = os.path.join(kodi_utils.cache_dir(), 'translated')
        if os.path.isdir(translated_dir):
            for fn in os.listdir(translated_dir):
                if not fn.endswith('.srt'):
                    continue
                p = os.path.join(translated_dir, fn)
                try:
                    with open(p, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    # Gated per file: the Google Translate fallback writes
                    # here too. srt.may_carry_arabic_leak owns that rule.
                    body = (srt.strip_leaked_arabic(content)
                            if srt.may_carry_arabic_leak(p) else content)
                    fixed = srt.clamp_cue_durations(
                        srt.fix_rtl_punctuation(
                            body, legacy_engine='auto'))
                    if fixed != content:
                        tmp = p + '.aitmp'
                        with open(tmp, 'w', encoding='utf-8') as f:
                            f.write(fixed)
                        os.replace(tmp, p)
                except OSError:
                    pass
        kodi_utils.set_setting('_rtl_fix_done', CACHE_RTL_FIX_VERSION)
    except Exception:
        pass


def _maybe_default_fast_first_chunk():
    try:
        from resources.lib import kodi_utils
        if kodi_utils.get_setting('_fast_first_chunk_default_done', '') == '1':
            return
        if kodi_utils.get_setting('fast_first_chunk', '') in ('', 'false'):
            kodi_utils.set_setting('fast_first_chunk', 'true')
        kodi_utils.set_setting('_fast_first_chunk_default_done', '1')
    except Exception:
        pass


def _maybe_migrate_embedded_translation_mode():
    """Preserve legacy hidden toggles before the new mode list becomes canonical."""
    try:
        from resources.lib import kodi_utils
        kodi_utils.embedded_translation_mode()
    except Exception:
        pass


def _maybe_force_gender_ref_arabic():
    """One-shot: turn the Arabic gender reference ON for everyone (forced once,
    even if previously off; a later manual opt-out sticks). Marker-gated."""
    try:
        from resources.lib import kodi_utils
        if kodi_utils.get_setting('_gender_ref_on_v1', '') == '1':
            return
        kodi_utils.set_setting('gender_ref_arabic', 'true')
        kodi_utils.set_setting('_gender_ref_on_v1', '1')
    except Exception:
        pass


def _maybe_tune_gemini3_defaults():
    """One-shot: move existing users to the validated Gemini 3 settings --
    temperature 1.0 + thinking_level medium. Only flips values still on the old
    defaults (0.2 / disabled); a deliberate choice sticks. Marker-gated."""
    try:
        from resources.lib import kodi_utils
        if kodi_utils.get_setting('_gemini3_tune_v1', '') == '1':
            return
        try:
            t = float(kodi_utils.get_setting('temperature', '') or '0.2')
        except (TypeError, ValueError):
            t = 0.2
        if abs(t - 0.2) < 0.005:
            kodi_utils.set_setting('temperature', '1.0')
        th = (kodi_utils.get_setting('thinking_budget', '') or '0').strip().lower()
        if th in ('', '0', 'disabled'):
            kodi_utils.set_setting('thinking_budget', 'medium')
        kodi_utils.set_setting('_gemini3_tune_v1', '1')
    except Exception:
        pass


def _maybe_lower_chunk_lines():
    """One-shot: lower the translation chunk to 50 lines (block-avoidance).
    Big chunks of explicit dialogue trip Google's prompt-level block; 50-line
    chunks stay under the threshold with no loss of gender/quality. Only lowers
    old defaults (>=100 -> 50); a smaller manual choice sticks. Marker-gated."""
    try:
        from resources.lib import kodi_utils
        if kodi_utils.get_setting('_chunk_lines_50_v1', '') == '1':
            return
        try:
            cur = int(kodi_utils.get_setting('chunk_lines', '') or '100')
        except (TypeError, ValueError):
            cur = 100
        if cur >= 100:
            kodi_utils.set_setting('chunk_lines', '50')
        kodi_utils.set_setting('_chunk_lines_50_v1', '1')
    except Exception:
        pass


def _start_pool_queue_drainer(monitor):
    """Drive both pool queues from the long-lived service: gently pull queued
    Ktuvit subs from Ktuvit (process_harvest_queue) and upload queued
    contributions to Telegram (drain), throttled + retrying, surviving playback
    ending / a Kodi restart. Best-effort; never blocks."""
    try:
        import threading
        from resources.lib import pool
    except Exception:
        return

    def _loop():
        try:
            if monitor.waitForAbort(20):
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
                if monitor.waitForAbort(20 if backlog else 60):
                    break
        except Exception:
            pass

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def _start_subsync_drainer(monitor):
    """Run slow SubSync verification outside the subtitle picker thread."""
    def _loop():
        try:
            if monitor.waitForAbort(1.0):
                return
            from resources.lib import subsync as _ss
            while not monitor.abortRequested():
                try:
                    _ss.drain_queue_once()
                except Exception:
                    pass
                if monitor.waitForAbort(1.0):
                    break
        except Exception:
            pass

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass


def _start_subsync_delay_watch(monitor):
    """Learn a settled manual subtitle delay for the exact playing cut."""
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
            from resources.lib import kodi_utils
            import xbmcgui
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
                                _ss.store_human_verdict(rep)
                                _pool.report_sync(
                                    rep.get('info') or {}, rep['sub_hash'],
                                    _ss._sync_registry_release(
                                        rep['release'],
                                        rep.get('cut_signature') or ''),
                                    rep['scale'], rep['offset_ms'],
                                    rep['status'], origin='human')
                                reported.add(akey)
                                kodi_utils.log(
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


def _run(label, func):
    try:
        func()
    except Exception as e:
        try:
            from resources.lib import kodi_utils
            kodi_utils.log('{0} failed: {1}'.format(label, e), level='WARNING')
        except Exception:
            pass


def _maybe_patch_darksubs():
    from resources.lib import dark_subs_integration
    dark_subs_integration.maybe_patch_darksubs()


def _maybe_patch_darksubs_download_sub():
    from resources.lib import darksubs_download_sub_patcher
    darksubs_download_sub_patcher.ensure_patched()


def _maybe_patch_darksubs_opensubtitles():
    from resources.lib import darksubs_opensubtitles_patcher
    darksubs_opensubtitles_patcher.ensure_patched()


def _maybe_patch_darksubs_embedded_demote():
    from resources.lib import darksubs_embedded_demote_patcher
    status = darksubs_embedded_demote_patcher.ensure_patched()
    if status == 'patched':
        from resources.lib import darksubs_reload
        darksubs_reload.note_patched()


def _maybe_patch_darksubs_embedded_insert():
    from resources.lib import darksubs_embedded_insert_patcher
    status = darksubs_embedded_insert_patcher.ensure_patched()
    if status == 'patched':
        from resources.lib import darksubs_reload
        darksubs_reload.note_patched()


def _maybe_patch_darksubs_subwindow_demote():
    from resources.lib import darksubs_subwindow_demote_patcher
    status = darksubs_subwindow_demote_patcher.ensure_patched()
    if status == 'patched':
        from resources.lib import darksubs_reload
        darksubs_reload.note_patched()


def _maybe_surface_darksubs_status():
    from resources.lib import darksubs_hook_diagnostics
    darksubs_hook_diagnostics.surface_status_if_problem()


def _maybe_patch_darksubs_filename():
    from resources.lib import darksubs_filename_fallback_patcher
    darksubs_filename_fallback_patcher.ensure_patched()


def _maybe_patch_skin_dialog_subtitles():
    from resources.lib import skin_dialog_subtitles_patcher
    skin_dialog_subtitles_patcher.ensure_patched()


def _maybe_patch_skin_dialog_subtitles_rows():
    from resources.lib import skin_dialog_subtitles_row_patcher
    skin_dialog_subtitles_row_patcher.ensure_patched()


def _maybe_patch_darksubs_picker_label():
    from resources.lib import darksubs_picker_label_patcher
    darksubs_picker_label_patcher.ensure_patched()


def _maybe_patch_darksubs_picker_height():
    from resources.lib import darksubs_picker_height_patcher
    darksubs_picker_height_patcher.ensure_patched()


def _maybe_patch_all_subs_samefile():
    from resources.lib import all_subs_samefile_patcher
    all_subs_samefile_patcher.ensure_patched()


def _maybe_default_builtin_engine():
    """One-shot: turn the built-in sources engine ON for existing standalone
    users too (marker-gated). New installs get it from the settings.xml default.
    A later manual opt-out STICKS -- we never force it back on."""
    try:
        from resources.lib import kodi_utils
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_builtin_engine_rollout_v1', '') == '1':
            return
        if kodi_utils.get_setting('use_builtin_engine', 'false') != 'true':
            kodi_utils.set_setting('use_builtin_engine', 'true')
        if kodi_utils.get_setting('engine_autosub', 'true') == 'false':
            kodi_utils.set_setting('engine_autosub', 'true')
        kodi_utils.set_setting('_builtin_engine_rollout_v1', '1')
        kodi_utils.log('built-in engine enabled (standalone rollout v1)',
                       level='INFO')
    except Exception:
        pass


def _maybe_bump_gemini_model():
    """One-shot: move existing users off superseded default Gemini models --
    3.1-flash-lite -> 3.5-flash-lite and 3.5/3.6-flash -> 3.8-flash. Drop-in
    upgrades, identical free-tier quota. Marker-gated, so a later manual pick
    sticks. (The regular-Flash target was 3.7 until Google superseded that too;
    this map points at the CURRENT pick rather than at one the picker no longer
    offers.)

    THIS IS A COPY OF THE BUILD EDITION'S MIGRATION, AND THAT IS THE BUG IT
    FIXES. The standalone runs this SLIM_SERVICE template, not the repo's
    service.py, so the migration added for 0.2.494 reached the build, the
    quickfix and the full build -- and never reached the repo-channel users.
    changelog.txt, however, is shared: they read "anyone already on 3.5 or 3.6
    Flash is moved across once, automatically" and nothing moved. Reported by a
    user who read the shipped zip, three releases after the claim.

    The general shape, worth more than this one function: A SHARED CHANGELOG
    OVER TWO DIFFERENT SERVICES CAN TELL ONE CHANNEL THE TRUTH AND THE OTHER A
    LIE. tools/test_channels_agree.py now checks this specific claim; any future
    user-visible promise in a release note needs the same treatment."""
    try:
        from resources.lib import kodi_utils
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_gemini_model_bump_v2', '') == '1':
            return
        cur = (kodi_utils.get_setting('model', '') or '').strip()
        new = {'gemini-3.1-flash-lite': 'gemini-3.5-flash-lite',
               'gemini-3.5-flash': 'gemini-3.8-flash',
               'gemini-3.6-flash': 'gemini-3.8-flash'}.get(cur)
        if new:
            kodi_utils.set_setting('model', new)
            kodi_utils.log('Gemini model bumped {0} -> {1} (migration v2)'.format(
                cur, new), level='INFO')
        kodi_utils.set_setting('_gemini_model_bump_v2', '1')
    except Exception:
        pass


def _maybe_bump_gemini_model_38():
    """One-shot: move existing users off gemini-3.7-flash to gemini-3.8-flash.

    Google shipped 3.8 Flash as the current regular-Flash model and reclassified
    3.7 as previous-generation; the two sit on identical rate limits, so this is
    a drop-in upgrade like the bump above. 3.7 keeps working -- but the picker
    no longer offers it, and a stored id the list cannot show is how a settings
    screen ends up displaying a blank model.

    A NEW marker id: _gemini_model_bump_v2 is already '1' on every device that
    took the previous bump, so reusing it would no-op this migration for exactly
    the users who need it.

    The retired set covers 3.6 and 3.5 Flash as well, so this is correct on its
    own rather than correct only because it runs after the v2 bump in the same
    boot. THIS IS THE STANDALONE'S COPY: the build edition has its own in
    service.py, and tools/test_channels_agree.py requires both to exist -- a
    shared changelog over two different services can tell one channel the truth
    and the other a lie, which is precisely what happened with the 3.7 bump."""
    try:
        from resources.lib import kodi_utils
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_gemini_model_bump_v3', '') == '1':
            return
        retired = ('gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-3.5-flash')
        cur = (kodi_utils.get_setting('model', '') or '').strip()
        if cur in retired:
            kodi_utils.set_setting('model', 'gemini-3.8-flash')
            kodi_utils.log('Gemini model bumped {0} -> gemini-3.8-flash '
                           '(migration v3)'.format(cur), level='INFO')
        kodi_utils.set_setting('_gemini_model_bump_v3', '1')
    except Exception:
        pass


def _engine_on():
    try:
        from resources.lib import kodi_utils
        return kodi_utils.get_bool('use_builtin_engine', False)
    except Exception:
        return False


def _maybe_prewarm_engine():
    """Warm the sources engine in the background so the first search is fast."""
    from resources.lib import autosub_service
    autosub_service.prewarm_engine()


def _maybe_start_autosub_player():
    """Register the auto-on-play Hebrew listener (Phase C): search + apply the
    best Hebrew subtitle automatically at playback start, exactly like the full
    build -- this is what makes the standalone act as a PRIMARY subtitle addon
    on a clean Kodi. autosub_service holds the Player reference in its module
    STATE, which outlives this call; the service keep-alive loop below keeps
    the process (and therefore the Player callbacks) running."""
    from resources.lib import autosub_service
    autosub_service.start_if_enabled()


def _ensure_darksubs_enabled():
    """When the engine is ON, disable DarkSubs + All Subs Plus so only MoranSubs
    runs (reversible: turn the engine off and they come back). When OFF, ensure
    DarkSubs is enabled (the translation hook depends on it). Only writes on a
    mismatch; leaves an add-on that isn't installed alone."""
    if xbmc is None:
        return
    engine_on = _engine_on()
    try:
        from resources.lib import kodi_utils as _ku
        keep = _ku.get_bool('keep_darksubs', False)
    except Exception:
        keep = False
    desired = (not engine_on) or keep
    for addon_id in ('service.subtitles.All_Subs',
                     'service.subtitles.all_subs_plus'):
        try:
            import json as _json
            get = _json.dumps({
                'jsonrpc': '2.0', 'id': 1,
                'method': 'Addons.GetAddonDetails',
                'params': {'addonid': addon_id, 'properties': ['enabled']},
            })
            data = _json.loads(xbmc.executeJSONRPC(get) or '{}')
            addon = (data.get('result') or {}).get('addon') or {}
            if 'enabled' not in addon:
                continue
            if bool(addon.get('enabled')) == desired:
                continue
            en = _json.dumps({
                'jsonrpc': '2.0', 'id': 1,
                'method': 'Addons.SetAddonEnabled',
                'params': {'addonid': addon_id, 'enabled': desired},
            })
            xbmc.executeJSONRPC(en)
            xbmc.log('[{0}] {1} set enabled={2} (engine_on={3})'.format(
                ADDON_ID, addon_id, desired, engine_on), level=xbmc.LOGINFO)
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
        from resources.lib import kodi_utils
        if kodi_utils.get_setting('_he_subs_prefs_v1', '') == '1':
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
        kodi_utils.set_setting('_he_subs_prefs_v1', '1')
        kodi_utils.log('[{0}] Hebrew subtitle preferences seeded '
                       '(one-shot)'.format(ADDON_ID), level=xbmc.LOGINFO)
    except Exception:
        pass


def _maybe_set_default_subtitle_service():
    """When the engine is on, make MoranSubs the default subtitle service for
    movies + TV. Only when the engine is on (we don't override otherwise)."""
    if xbmc is None or not _engine_on():
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
    except Exception:
        pass


def main():
    if xbmc is None:
        return
    if _check_first_run_marker():
        return

    # The standalone carries the complete SubSync stack too. Slow file
    # verification and viewer-confirmed delay learning therefore need the same
    # long-lived workers as the build edition; otherwise jobs stay on disk and
    # a manual correction is never scoped to its exact media cut.
    _start_subsync_drainer(xbmc.Monitor())
    _start_subsync_delay_watch(xbmc.Monitor())

    _prune_once()
    _maybe_purge_temp_once()

    # Roll out / sync the built-in sources engine, then neutralise the competing
    # subtitle add-ons when it's on (reversible -- turn the engine off and they
    # come back). Mirrors the full build.
    _maybe_default_builtin_engine()
    _maybe_bump_gemini_model()
    _maybe_bump_gemini_model_38()
    _ensure_darksubs_enabled()
    _maybe_set_default_subtitle_service()
    _run('hebrew subtitle prefs', _maybe_seed_hebrew_subtitle_prefs)
    _run('engine prewarm', _maybe_prewarm_engine)

    if not _engine_on():
        # DarkSubs provides the sources when the engine is off: keep our hooks
        # alive. Skipped entirely when the engine is on (DarkSubs is disabled).
        _run('darksubs integration', _maybe_patch_darksubs)
        _run('darksubs download_sub patch', _maybe_patch_darksubs_download_sub)
        _run('darksubs OpenSubtitles patch', _maybe_patch_darksubs_opensubtitles)
        _run('darksubs embedded demote patch', _maybe_patch_darksubs_embedded_demote)
        _run('darksubs embedded insert patch', _maybe_patch_darksubs_embedded_insert)
        _run('darksubs subwindow demote patch', _maybe_patch_darksubs_subwindow_demote)
        _run('darksubs status diagnostics', _maybe_surface_darksubs_status)
        _run('darksubs filename fallback patch', _maybe_patch_darksubs_filename)
        _run('darksubs picker label patch', _maybe_patch_darksubs_picker_label)
        _run('darksubs picker height patch', _maybe_patch_darksubs_picker_height)
        _run('allsubs samefile patch', _maybe_patch_all_subs_samefile)
        try:
            from resources.lib import darksubs_reload
            darksubs_reload.reload_if_patched()
        except Exception:
            pass

    # Skin subtitle-dialog fixes are not DarkSubs-specific -- run regardless.
    _run('subtitle dialog filename patch', _maybe_patch_skin_dialog_subtitles)
    _run('subtitle dialog row patch', _maybe_patch_skin_dialog_subtitles_rows)

    _maybe_repair_rtl_cache()
    _maybe_default_fast_first_chunk()
    _maybe_migrate_embedded_translation_mode()
    _maybe_force_gender_ref_arabic()
    _maybe_tune_gemini3_defaults()
    _maybe_lower_chunk_lines()

    global _subs_filename_publisher
    try:
        from resources.lib import subs_filename_publisher
        _subs_filename_publisher = subs_filename_publisher.SubsFilenamePublisher()
    except Exception:
        pass

    # Play-start listener (Phase C): the same one the full build runs. It
    # ALWAYS snapshots the embedded subtitle streams (the picker's embedded
    # and embedded-AI rows are built from that snapshot) whenever
    # use_builtin_engine is on, and additionally auto-searches Hebrew when
    # engine_autosub is on. Both default ON; engine_autosub gates only the
    # search, never the snapshot.
    _run('autosub player', _maybe_start_autosub_player)

    monitor = xbmc.Monitor()
    # Upload any queued community-pool contributions (e.g. mirrored Ktuvit
    # subs) from this long-lived service, throttled + retrying.
    _start_pool_queue_drainer(monitor)
    while not monitor.abortRequested():
        if monitor.waitForAbort(24 * 3600):
            break
        _prune_once()


main()
