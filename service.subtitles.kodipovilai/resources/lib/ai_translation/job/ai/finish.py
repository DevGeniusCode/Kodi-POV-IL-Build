# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# AI stages, 9-10 of 10 -- finish: the post-fix chain over the whole translation, the save, the pool upload,
# the gender proofread and the delivery.

import os
from functools import partial
from resources.lib.core.cache import translation_cache
from resources.lib.ai_translation.repairs import postfix_cleanup, postfix_glyphs, postfix_leaks, postfix_rtl
from resources.lib.core import kodi, settings
from resources.lib.core.srt import blocks as srt_blocks, timing
from resources.lib.ai_translation.dispatch.pacing import is_mostly_hebrew
from resources.lib.ai_translation.proofread.gp_jobs import gp_later_add
from resources.lib.ai_translation.proofread.gp_run import proofread_stopper, run_gender_proofread
from resources.lib.ai_translation.proofread.gp_state import gp_mark, gp_ref_mark, gp_settle, gp_stats, gp_try_mark
from resources.lib.ai_translation.providers.google_flow import clear_google_marker, google_translate_and_save
from resources.lib.ai_translation.reuse.pool_reuse import pool_marker, pool_quality_ok
from resources.lib.ai_translation.job.ai.dispatch import keep_replaced_google
from resources.lib.ai_translation.job.ai.plan import emit
from resources.lib.ai_translation.job.ai.prepare import deliver
from resources.lib.ai_translation.job.state import translation_cancelled
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None


def proofread_gender(S, blocks):
    """The blocks with gender_proofread's corrections applied; the blocks
    as they are when no model answers. Never raises."""
    proofed, report = run_gender_proofread(
        blocks, S.src_text, S.api_key, S.model, S.cast,
        proofread_stopper(S.progressive_source_id, partial(translation_cancelled, S)),
        hebrew_ref=(S.gp_ref == 'he'))
    S.gp_report.update(report or {})
    return proofed


def finish(S):
    """Post-fix chain, save, pool upload, gender proofread, delivery."""
    final = srt_blocks.stitch_blocks(S.out_blocks)
    # Timing backstop. restore_block_timings above pairs positionally and so
    # cannot act when a chunk legitimately came back with a different entry
    # count; this also catches a pathological cue in the SOURCE subtitle itself.
    # Bounds each cue's end by the next cue's start -- a no-op on a healthy file.
    final = timing.clamp_cue_durations(final)
    # Defensive backstop for the SPEAKER-PREFIX HINT: we now KEEP 'MABEL:' prefixes
    # in the source so the model can use them for per-line gender (prompt.py), and
    # it's told to drop the tag from its Hebrew output. Strip any it failed to drop,
    # but ONLY on a line that actually has Hebrew (hebrew_only) -- so a leaked tag on
    # a translated line is removed while a caption/chyron/URL the model deliberately
    # left in English ("WARNING: ...", "PART 2: ...", "HTTP://...") is never eaten.
    final = postfix_cleanup.strip_leaked_speaker_prefix(final, hebrew_only=True)
    # ...and the same tag when the model TRANSLATED it instead of copying it
    # ("IAN: No, no." -> "איאן: לא, לא."). The stripper above cannot see that
    # one -- it matches an ALL-CAPS LATIN name -- and measuring a file a user
    # reported found 60 leaked tags, every one of them Hebrew. Anchored to
    # src_text: an entry's Hebrew may lose a prefix only where the SAME entry's
    # source carried a speaker tag, so ordinary dialogue with a colon is safe.
    _pre_tag = final
    final = postfix_cleanup.strip_hebrew_speaker_prefix(final, S.src_text)
    if final != _pre_tag:
        try:
            kodi.log(
                'translated speaker tags stripped from {0} line(s)'.format(
                    sum(1 for a, b in zip(_pre_tag.split('\n'),
                                          final.split('\n')) if a != b)),
                level='INFO')
        except Exception:
            pass
    # A capitalised sound cue the model returned verbatim ("HEAVY BREATHING").
    _pre_cue = final
    final = postfix_cleanup.drop_untranslated_sound_cues(final, S.src_text)
    if final is not _pre_cue:
        kodi.log('untranslated sound cues dropped', level='INFO')
    # Same class of defect, different source: when the Arabic gender
    # reference is on, the prompt carries real Arabic lines and the model
    # sometimes copies a word or a suffix of one into the Hebrew. Only a
    # line that has BOTH scripts is touched, so an all-Arabic line stays.
    #
    # Logged, not silent. What the reference feeds the model is a HUMAN
    # translation OF THE SAME ENTRY (prompt.build_gender_block: "the
    # time-aligned line from a HUMAN translation of the same scene"), so a leak
    # is a DUPLICATE of the meaning the Hebrew already carries, and removing it
    # loses nothing. What that reasoning cannot rule out is the model
    # code-switching mid-sentence -- rendering half the line in Hebrew and
    # continuing in Arabic -- where the Hebrew left behind would be incomplete.
    # There is no way to tell the two apart from the text, so this line records
    # how often it fires and on how many entries; a jump means the question is
    # worth revisiting with real data instead of reasoning.
    # arabic-strip: our-own-output -- `final` is this run's Gemini output, so
    # there is no provenance question here; may_carry_arabic_leak exists for the
    # repair paths that meet files of unknown origin.
    _pre_ar = final
    final = postfix_glyphs.strip_leaked_arabic(final)
    if final != _pre_ar:
        try:
            _n = sum(1 for a, b in zip(_pre_ar.split('\n'), final.split('\n'))
                     if a != b)
            kodi.log(
                'Arabic-script cleanup changed {0} line(s); '
                'reference_language={1}'.format(_n, S.ref_lang if S.ar_map else 'none'),
                level='WARNING')
        except Exception:
            pass
    # Same class again, one step out: the model sometimes returns the SOURCE
    # lines AND its Hebrew stacked inside one cue. Provenance is not in question
    # here either -- `final` is this run's own output -- so the strip applies
    # directly, with no ai_output gate to consult.
    #
    # This is the path that PRODUCES both defects, and it is the one that must
    # carry the repair. The re-repair paths (_reapply_rtl_fix_in_place,
    # _rtl_delivery_copy) only ever catch them on a LATER pass, by which time
    # this file has already been served to the viewer once and contributed to
    # the community pool -- where the flaw then outlives every local fix,
    # because the pool is written once and only ever repaired on read.
    final = postfix_leaks.strip_source_echo(final)
    # The same wrong-alphabet slip in a script with no reference block behind
    # it -- a Cyrillic or Greek letter dropped into the middle of a Hebrew word
    # ("אמм"). Only a run glued to Hebrew is folded; a foreign word standing on
    # its own is left as written.
    final = postfix_glyphs.fold_foreign_in_hebrew_word(final)
    # ...and its Latin form, one English letter for the Hebrew one it sounds
    # like ("בהTחשב", "בואi").
    final = postfix_glyphs.fold_stray_latin_letter(final)
    final = postfix_cleanup.fix_pronoun_stutter(final)
    # Hebrew subtitles are written unpointed. The model vocalises a word now and
    # then ("בְּסֵדֶר", "מַה?"), reported as "a lot of niqqud that isn't really
    # needed"; every instance measured in real output was an ordinary word whose
    # consonants already carried the meaning, so this only affects display.
    final = postfix_cleanup.strip_niqqud(final)
    # Fold what the shipped fonts cannot draw. Must come BEFORE the RTL wrap
    # below: it strips RLE/PDF, which is exactly what fix_rtl_punctuation adds.
    # See normalize_glyphs' docstring -- that ordering is load-bearing.
    final = postfix_glyphs.normalize_glyphs(final)
    # BBC Subtitle Guidelines 3.4: no first line ending on "על" / "את" /
    # "שאני" that belongs with the line below. Logical order: before the RTL fix.
    _pre_breaks = final
    final = postfix_cleanup.mend_line_breaks(final)
    if final is not _pre_breaks:
        try:
            kodi.log('line breaks moved to natural points in {0} cue(s)'.format(
                sum(1 for a, b in zip(_pre_breaks.split('\n'), final.split('\n'))
                    if a != b) // 2), level='INFO')
        except Exception:
            pass
    # Defensive backstop for RTL punctuation: Gemini sometimes puts
    # punctuation at the logical start of a Hebrew line ("?שלום")
    # when it belongs at the logical end ("שלום?"). The prompt
    # instructs against this, but this post-processor catches any
    # slips so the final SRT renders correctly in Kodi.
    final = postfix_rtl.fix_rtl_punctuation(final)
    # Guard: the model sometimes returns EMPTY (blank subtitles) or ECHOES the
    # source untranslated -- both pass the entry-count check and used to be
    # cached and served as "the Hebrew translation". Verify the result is really
    # Hebrew before caching. If it isn't, do NOT cache the garbage; fall back to
    # Google Translate so the user still gets Hebrew (unless they chose 'none'),
    # otherwise fail visibly and let them retry.
    if not is_mostly_hebrew(final):
        kodi.log(
            'AI output is not Hebrew (empty or echoed source) -- not caching; '
            'len={0}'.format(len(final or '')), level='WARNING')
        mode = (settings.get_setting('translation_mode', 'ai') or 'ai')
        if mode != 'none':
            gpath = google_translate_and_save(
                S.src_text, S.source_lang, S.translated, S.info)
            if gpath:
                delivered_path = deliver(S, gpath)
                # This path returned WITHOUT a 'done', so the canonical swap
                # never ran and the viewer was left on the last progressive
                # slot -- which holds the non-Hebrew output we just rejected.
                # That is the "it plays the original language" report, on the
                # one path nobody had wired.
                if S.progressive_cb is not None:
                    try:
                        S.progressive_cb('done', {
                            'success': True,
                            'source_id': S.progressive_source_id,
                            'release': S.src_release,
                            'path': delivered_path,
                        })
                    except Exception as e:
                        kodi.log(
                            'progressive_cb done(google-rescue) raised: '
                            + str(e), level='WARNING')
                emit(S, True, 'google')
                return delivered_path
        _kept = keep_replaced_google(S, 'not_hebrew')
        if _kept:
            emit(S, False, 'not_hebrew (cached Google file kept)')
            return _kept
        kodi.notify(
            'AI: התרגום לא הוחזר בעברית (ריק/לא תורגם). נסה שוב.', time_ms=5000)
        if S.progressive_cb is not None:
            try:
                S.progressive_cb('done', {'success': False,
                                        'source_id': S.progressive_source_id})
            except Exception:
                pass
        emit(S, False, 'not_hebrew')
        return None
    translation_cache.save_text(S.translated, final)
    clear_google_marker(S.translated)
    # its gender reference, for the proofread here and at a later open
    S.gp_ref = S.ref_lang if (S.ar_on and S.used_ar) else ''
    gp_ref_mark(S.translated, S.gp_ref)
    # Also save under the content-hash slot when it differs from
    # the early-source-id slot. That way the same translation
    # answers a future lookup whether the user comes back via the
    # same local path OR via a different source whose bytes
    # happen to match (e.g. a re-read of the same SRT from a
    # different local path).
    if S.early_source_id and S.content_id and S.content_id != S.early_source_id:
        try:
            _content_slot = translation_cache.translated_path(
                S.imdb_id, S.season, S.episode, S.source_lang,
                source_id=S.content_id, tier=S.tier)
            translation_cache.save_text(_content_slot, final)
            clear_google_marker(_content_slot)
            gp_ref_mark(_content_slot, S.gp_ref)
        except Exception as e:
            kodi.log(
                'content-hash duplicate save failed: {0}'.format(e),
                level='DEBUG')
    # Gender and number, proofread by a stronger model in ONE request over the
    # whole title (gender_proofread) -- on the live path only: the viewer has
    # the Hebrew on screen meanwhile (sent again here, with every repair
    # above), while a caller that waits for resolve() would wait minutes. It
    # runs on the file already saved, so a stop or a crash in those minutes
    # keeps this translation; what it corrects is saved over it and goes out
    # with 'done' below, and to the pool.
    if (S.progressive_cb is not None
            and settings.get_bool('gender_proofread', True)):
        try:
            S.progressive_cb('chunk_ready', {
                'completed': 1, 'total': 1, 'merged_text': final,
                'source_id': S.progressive_source_id, 'release': S.src_release})
        except Exception as e:
            kodi.log('progressive_cb chunk_ready (before the gender '
                           'proofread) raised: ' + str(e), level='WARNING')
        _proof_blocks = srt_blocks.parse_blocks(final)
        # In progress, as for an opened translation: the file is saved, and an
        # open meanwhile (the list again) would ask a second time.
        _gp_slots = [S.translated]
        if S.early_source_id and S.content_id and S.content_id != S.early_source_id:
            _gp_slots.append(translation_cache.translated_path(
                S.imdb_id, S.season, S.episode, S.source_lang,
                source_id=S.content_id, tier=S.tier))
        for _slot in _gp_slots:
            gp_try_mark(_slot, True)
        _proofed = proofread_gender(S, _proof_blocks)
        gp_settle(_gp_slots, S.gp_report, proofread_stopper(
            S.progressive_source_id, partial(translation_cancelled, S)))
        if not S.gp_report.get('model'):
            # 3.7 busy, out of today's quota, or the viewer moved on: the
            # service asks again later, and the pool gets the proofread then
            # (the upload below carries this translation without it)
            gp_later_add(S.gp_report, S.translated, S.src_text, S.info,
                          share={'hash': S.final_pool_hash, 'lang': S.source_lang,
                                 'release': S.release_override or '',
                                 'kind': S.pool_kind},
                          copies=_gp_slots[1:])
        if _proofed != _proof_blocks:
            final = srt_blocks.stitch_blocks(_proofed)
            translation_cache.save_text(S.translated, final)
            if S.early_source_id and S.content_id and S.content_id != S.early_source_id:
                try:
                    translation_cache.save_text(translation_cache.translated_path(
                        S.imdb_id, S.season, S.episode, S.source_lang,
                        source_id=S.content_id, tier=S.tier), final)
                except Exception as e:
                    kodi.log(
                        'content-hash duplicate save failed: {0}'.format(e),
                        level='DEBUG')
        if S.gp_report.get('model'):
            # proofread: opening it again spends no request
            gp_mark(S.translated, S.gp_report)
            if S.early_source_id and S.content_id and S.content_id != S.early_source_id:
                _gp_slot = translation_cache.translated_path(
                    S.imdb_id, S.season, S.episode, S.source_lang,
                    source_id=S.content_id, tier=S.tier)
                if os.path.isfile(_gp_slot):
                    gp_mark(_gp_slot, S.gp_report)
    # The Google file this run replaces can sit in another slot (the gender
    # reference tier came out differently this time). AI owns the title now:
    # a lookup of that slot must not serve the Google text or spend another
    # translation on it.
    if S.replacing_google and os.path.isfile(S.replacing_google + '.google'):
        for _suffix in ('', '.google', '.release'):
            try:
                os.remove(S.replacing_google + _suffix)
            except OSError:
                pass

    # Queue the telemetry event BEFORE the pool contribute below, so it rides
    # THIS translation's own /contribute piggyback (pool._post drains the pending
    # telemetry batch onto the upload it is already sending). Emitting AFTER the
    # contribute -- as this did before -- queued the event too late to ride its
    # own upload, so it had to wait for the NEXT translation's contribute or the
    # periodic /ev flush; the last/only translation of a session then reached the
    # pool (Recent embedded) but never the telemetry-fed Recent activity view,
    # and it caused extra standalone /ev flushes (Worker invocations). _emit is
    # idempotent (_telemetry_done) and we're already on the guaranteed-success
    # path (final is the delivered Hebrew), so this is the right, single emit.
    emit(S, True)

    # Share this fresh translation to the community pool (fire-and-forget on a
    # daemon thread -- never delays handing the subtitle to the player). Gated
    # by pool_share; only reached for a genuinely new translation (local cache
    # and pool both missed above).
    if pool_contribute is not None and pool_client.share_enabled():
        if pool_quality_ok(S.src_text, final):
            try:
                pool_contribute.contribute_once(S.info, S.final_pool_hash, S.source_lang,
                                     final,
                                     marker_path=pool_marker(S.translated,
                                                              S.pool_kind),
                                     release_override=S.release_override,
                                     kind=S.pool_kind,
                                     proofread=(gp_stats(S.gp_report) or False))
            except Exception as e:
                kodi.log('pool contribute dispatch failed: {0}'.format(e),
                               level='DEBUG')
        else:
            kodi.log(
                'pool: skipped share -- translation looks incomplete or not '
                'Hebrew (quality gate)', level='INFO')

    # Append today's Gemini quota usage to the success toast, but only if the user
    # is on a tracked free model (Flash / Flash-Lite) AND not in paid mode (the
    # "X/limit" figure is the FREE daily cap, meaningless/misleading for a paid
    # key). The limit shown follows the model (~500/day Flash-Lite, ~20/day regular
    # Flash). Wrapped so a quota-module bug can't drop the toast itself.
    quota_suffix = ''
    try:
        from resources.lib.ai_translation import quota
        if quota.is_tracked(S.model) and not S.paid_mode:
            quota_suffix = ' · ' + quota.format_status_short(S.model)
    except Exception:
        quota_suffix = ''
    kodi.notify('AI: תרגום הסתיים בהצלחה ({0} chunks){1}'
                      .format(S.total, quota_suffix), time_ms=4000)
    delivered_path = deliver(S, S.translated)
    if S.progressive_cb is not None:
        try:
            S.progressive_cb('done', {
                'success': True,
                'source_id': S.progressive_source_id,
                'release': S.src_release,
                # TELL the handler where the translation is, do not make it
                # guess. It recomputed the path with cache.translated_path()
                # and NO tier=, while resolve() writes with tier='ar' whenever
                # a gender reference was found -- which is the normal case,
                # since the setting defaults on and is force-enabled by
                # migration. So os.path.isfile(canonical) was False on most
                # jobs, the canonical swap never ran, and the viewer was left
                # holding the last PROGRESSIVE SLOT file instead of the
                # finished translation. If that translation had been
                # interrupted, the slot is partly source text -- which is
                # exactly the "it plays the original language" report.
                'path': delivered_path,
            })
        except Exception as e:
            kodi.log(
                'progressive_cb done(success) raised: ' + str(e),
                level='WARNING')
    # (telemetry already emitted above, before the pool contribute, so it rides
    # this translation's own upload; _emit's _telemetry_done guard makes a second
    # call a no-op anyway.)
    return delivered_path
