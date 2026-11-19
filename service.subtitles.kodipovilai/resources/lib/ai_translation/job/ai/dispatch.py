# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# AI stages, 7 of 10 -- dispatch: the parallel chunk workers, the live preview after every chunk, the abort
# and incomplete-job handling.

import os
import threading
from functools import partial
from resources.lib.ai_translation.repairs import postfix_leaks
from resources.lib.core import kodi, settings
from resources.lib.ai_translation.dispatch.pacing import chunk_dispatch_order
from resources.lib.ai_translation.embedded_steps import with_steps_preview
from resources.lib.ai_translation.progress import current_playback_ms
from resources.lib.ai_translation.providers.google_flow import GOOGLE_FILL_ABORTS, google_fill_and_save, is_google_translated, preview_clean
from resources.lib.ai_translation.job.ai.calls import AbortTranslation, translate_one
from resources.lib.ai_translation.job.ai.plan import emit
from resources.lib.ai_translation.job.ai.prepare import deliver
from resources.lib.ai_translation.job.state import NEXT


def keep_replaced_google(S, why):
    """The run set out to replace a cached Google Translate file and did
    not finish. Nothing is saved before the end, so that file is still
    there, and it beats no subtitle at all (it was served before this
    run). None when there is no such file."""
    path = S.replacing_google
    if not (path and os.path.isfile(path) and is_google_translated(path)):
        return None
    kodi.log('AI translation did not finish ({0}) -- keeping the '
                   'cached Google Translate file'.format(why), level='WARNING')
    delivered = deliver(S, path)
    if S.progressive_cb is not None:
        try:
            S.progressive_cb('done', {
                'success': True,
                'source_id': S.progressive_source_id,
                'release': S.src_release,
                'path': delivered,
            })
        except Exception:
            pass
    return delivered


def dispatch(S):
    """Run the chunk workers; handle an abort or an incomplete job."""
    # Cross-chunk continuity uses only known source cues, so workers remain
    # independent. The lookup also handles a retry child that starts partway
    # through its original chunk; it must see the cues immediately before its
    # own boundary, not the parent's stale predecessor window.
    S.prev_context_lines = max(0, settings.get_int(
        'prev_context_lines', 5))

    # Set once the job has given up on one chunk (the dispatcher below):
    # chunks still waiting to ask a busy model again stop there, instead of
    # holding the Google fill -- and spending requests -- for the rest of
    # their own ten minutes.
    S.job_gave_up = threading.Event()
    # busy-retry numbers already announced (one toast a number, not one a chunk)
    S.busy_toasts = set()
    # seconds this thread's current request spent waiting on a busy model
    S.busy_waits = threading.local()

    # Parallel chunk dispatch. Gemini Flash Lite is 15 RPM, so 3
    # in flight at once is safe and turns a ~2-3 minute sequential
    # translation into ~30-60 seconds wall time. Users with the
    # paid tiers can crank this via `parallel_chunks` in the
    # advanced settings.
    if S.whole_subtitle_request:
        parallel = 1
    elif S.paid_mode:
        # Paid tier: no RPM cap to respect, so run more chunks in flight for a
        # much faster wall-time. FLOOR at 8 -- the parallel_chunks setting defaults
        # to 3 (free-tier safe) so we can't rely on its default here; the slider's
        # max is raised to 16 so a paid user can still tune 8..16 up.
        parallel = max(8, min(16, settings.get_int('parallel_chunks', 8)))
    else:
        parallel = max(1, min(8, settings.get_int(
            'parallel_chunks', 3)))
    S.out_blocks_by_index = {}
    completed = 0
    abort_msg = None
    abort_reason = None
    abort_detail = ''

    # Dispatch summary -- the ONLY window into an otherwise silent translation.
    # If a run stalls (no Hebrew appears), this line + the first-chunk-done line
    # below pin whether it's the mode (whole vs chunked), the chunk count, or the
    # very first API call that hangs. src_len flags a source too large for a
    # single 'whole' request (which truncates -> empty result -> discarded).
    kodi.log(
        'translation dispatch: {0} chunk(s), mode={1}, parallel={2}, paid={3}, '
        'model={4}, src_len={5}'.format(
            len(S.chunks), 'whole' if S.whole_subtitle_request else 'chunked',
            parallel, S.paid_mode, S.model, len(S.src_text)),
        level='INFO')

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            dispatch_order = chunk_dispatch_order(
                S.chunks, current_playback_ms())
            future_to_idx = {
                executor.submit(partial(translate_one, S), idx, S.chunks[idx - 1]): idx
                for idx in dispatch_order
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    response = future.result()
                except AbortTranslation as e:
                    abort_msg = e.user_msg
                    abort_reason = e.reason
                    abort_detail = getattr(e, 'detail', '') or ''
                    # Try to cancel pending futures; in-flight ones
                    # will run to completion but we ignore them.
                    S.job_gave_up.set()
                    for f in future_to_idx:
                        f.cancel()
                    break
                except Exception as e:
                    abort_msg = 'AI: שגיאה בלתי צפויה: {0}'.format(
                        str(e)[:80])
                    abort_reason = 'crash'
                    abort_detail = str(e)[:100]
                    S.job_gave_up.set()
                    for f in future_to_idx:
                        f.cancel()
                    break
                # The model is handed each block INCLUDING its timecode line and
                # asked to copy it verbatim. It almost always does -- but a single
                # mistyped digit welds a line to the screen for the rest of the
                # episode (field reports; 00:41:22 --> 01:41:24 is a 60-minute
                # cue), and the only check on the reply is its ENTRY COUNT. Give
                # every block its SOURCE timecode back so timing can never be a
                # translation artefact. No-op when the model copied correctly.
                # Final per-chunk structural gate, shared by every return rung
                # above (primary, alternate reference, English-only, Google
                # rescue and single-entry fallback). It drops untimed prefaces
                # and unrequested timestamps, repairs safe timing typos, and
                # fills any remaining source slot so a cue cannot disappear.
                (_shaped, _missing, _integrity_rejected) = (
                    postfix_leaks.generated_output_blocks(
                        S.chunks[idx - 1], response, fill_missing=True))
                S.out_blocks_by_index[idx] = _shaped
                if _missing or _integrity_rejected:
                    kodi.log(
                        'Chunk {0}: output integrity gate kept {1} missing '
                        'source entr(ies); rejected={2}'.format(
                            idx, len(_missing), _integrity_rejected),
                        level='WARNING')
                completed += 1
                if completed == 1:
                    # First chunk back -> API path is alive. Its absence in a log
                    # means every worker stalled BEFORE any response (network
                    # contention / a hung first request), not a post-parse issue.
                    kodi.log(
                        'translation: first chunk returned ({0} entries) -- '
                        'API path working, {1} chunk(s) to go'.format(
                            len(S.out_blocks_by_index[idx]), S.total - 1),
                        level='INFO')
                if S.progress_cb:
                    try:
                        S.progress_cb(completed, S.total)
                    except Exception:
                        pass
                if S.progressive_cb is not None:
                    try:
                        # Merge: Hebrew where done, source English
                        # where pending. Inline (not a srt.py helper)
                        # because this view is meaningful only here.
                        _merged_blocks = []
                        for _i, _ch in enumerate(S.chunks):
                            # chunks is 0-indexed; out_blocks_by_index
                            # is 1-indexed (idx = i + 1 above).
                            _key = _i + 1
                            if _key in S.out_blocks_by_index:
                                _merged_blocks.extend(
                                    S.out_blocks_by_index[_key])
                            elif S.google_chunks:
                                # Hebrew the viewer already had, not English
                                _merged_blocks.extend(S.google_chunks[_i])
                            else:
                                _merged_blocks.extend(
                                    with_steps_preview(_ch, S.steps_he))
                        # Bound the cues here too: this text is written to a
                        # file and handed to the player LIVE, so an unrepaired
                        # runaway cue would sit frozen on screen for the rest of
                        # the job -- the exact symptom, during the feature built
                        # to show progress early.
                        # arabic-strip: our-own-output -- every Hebrew line here
                        # is the model's reply to OUR prompt (pending chunks are
                        # still untranslated source, which carries no leak), so
                        # provenance is not in question. This is a transient
                        # progress preview; it is never the cached file.
                        # echo-strip + glyph-fold for the same reason, and safe
                        # against the pending half: a not-yet-translated chunk is
                        # a whole cue of source with no Hebrew in it, and a cue
                        # with no Hebrew is exempt by construction. The fold sits
                        # inside fix_rtl_punctuation's argument because it strips
                        # the RLE/PDF that call adds.
                        # The Hebrew-tag strip and the niqqud strip join the
                        # chain here for the same reason as the rest of it: the
                        # preview is what the viewer watches while the job runs,
                        # so a defect the final file will not have should not
                        # show up in it either. Both are safe over the half that
                        # is still untranslated source -- one needs Hebrew on
                        # the line, the other needs Hebrew points.
                        _merged_text = preview_clean(_merged_blocks, S.src_text)
                        S.progressive_cb('chunk_ready', {
                            'completed': completed,
                            'total': S.total,
                            'merged_text': _merged_text,
                            'source_id': S.progressive_source_id,
                            'release': S.src_release,
                        })
                    except Exception as e:
                        kodi.log(
                            'progressive_cb chunk_ready raised: '
                            + str(e),
                            level='WARNING')
    except ImportError:
        # Older Python without concurrent.futures -- shouldn't
        # happen on Kodi 21 but bail safely.
        kodi.notify('AI: שגיאה פנימית, התקן Python 3.6+',
                          time_ms=5000)
        return None

    if abort_reason:
        if abort_reason == 'cancelled':
            # The viewer changed film or subtitle. Do not spend another API
            # request, show an error, publish telemetry or save partial output.
            return None
        # Daily quota exhausted, a per-minute rate limit that outlasted all the
        # retries, Gemini still overloaded after the whole backoff, a request /
        # network error that kept failing, or an unexpected exception in a
        # chunk (telemetry keeps its detail) -> keep the chunks Gemini finished
        # and let Google translate the rest, so the user still gets Hebrew
        # (machine quality; never pooled; a later watch re-translates it with
        # AI). Overload and error used to drop every finished chunk and leave
        # nothing on screen -- nothing the user can fix. An invalid key still
        # surfaces as before: that one the user has to fix.
        if abort_reason in GOOGLE_FILL_ABORTS:
            if abort_reason in ('error', 'crash'):
                # A retired model name or an API that is not enabled is the
                # user's to fix: Google's Hebrew must not hide why AI failed.
                kodi.notify(abort_msg, time_ms=5000)
            if abort_reason == 'quota':
                try:
                    from resources.lib.ai_translation import quota
                    # under the setting's model: that is the one
                    # _google_cache_upgradeable asks about, and after a
                    # fallback both allowances are spent
                    quota.note_exhausted(S.models.chosen)
                except Exception:
                    pass
            # Nothing from the AI and a cached Google file in hand: that file,
            # not a second Google request.
            gpath = (None if S.out_blocks_by_index or not S.google_chunks
                     else keep_replaced_google(S, abort_reason))
            if gpath:
                kodi.notify(abort_msg, time_ms=5000)
                emit(S, False, 'abort:' + str(abort_reason)
                      + ' (cached Google file kept)')
                return gpath
            gpath = google_fill_and_save(S.chunks, dict(S.out_blocks_by_index),
                                          S.src_text, S.source_lang, S.translated,
                                          S.info, reason=abort_reason,
                                          google_chunks=S.google_chunks)
            if gpath:
                delivered_path = deliver(S, gpath)
                if S.progressive_cb is not None:
                    try:
                        S.progressive_cb('done', {
                            'success': True,
                            'source_id': S.progressive_source_id,
                            'release': S.src_release,
                            # The file we ACTUALLY wrote. The handler used to
                            # recompute this path and did so without the tier,
                            # so it looked in the wrong slot on every job that
                            # found a gender reference -- see the note on the
                            # main success emission below.
                            'path': delivered_path,
                        })
                    except Exception:
                        pass
                emit(S, True, 'google' if abort_reason in ('quota', 'ratelimit')
                      else 'google after abort:{0}{1}'.format(
                          abort_reason,
                          (': ' + abort_detail) if abort_detail else ''))
                return delivered_path
        kodi.notify(abort_msg, time_ms=5000)
        _kept = keep_replaced_google(S, abort_reason)
        if _kept:
            emit(S, False, 'abort:' + str(abort_reason or 'crash')
                  + ' (cached Google file kept)')
            return _kept
        if S.progressive_cb is not None:
            try:
                S.progressive_cb('done', {
                    'success': False,
                    'source_id': S.progressive_source_id,
                })
            except Exception as e:
                kodi.log(
                    'progressive_cb done(abort) raised: ' + str(e),
                    level='WARNING')
        emit(S, False, 'abort:' + str(abort_reason or 'crash')
              + ((': ' + abort_detail) if abort_detail else ''))
        return None

    if completed != S.total:
        kodi.notify(
            'AI: תרגום הסתיים חלקית ({0}/{1}). נסה שוב.'.format(
                completed, S.total),
            time_ms=5000)
        _kept = keep_replaced_google(S, 'partial')
        if _kept:
            emit(S, False, 'partial (cached Google file kept)')
            return _kept
        if S.progressive_cb is not None:
            try:
                S.progressive_cb('done', {
                    'success': False,
                    'source_id': S.progressive_source_id,
                })
            except Exception as e:
                kodi.log(
                    'progressive_cb done(partial) raised: ' + str(e),
                    level='WARNING')
        emit(S, False, 'partial')
        return None
    return NEXT
