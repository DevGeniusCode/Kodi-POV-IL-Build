# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Google as a translator: the whole-file mode, the fill of the chunks the AI left and the rescue.

import os
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi, settings
from resources.lib.ai_translation.repairs import postfix_cleanup, postfix_glyphs, postfix_leaks, postfix_rtl
from resources.lib.core.srt import blocks as srt_blocks, classify, timing
from resources.lib.ai_translation.dispatch.pacing import is_mostly_hebrew
from resources.lib.delivery.rtl import reapply_rtl_fix_in_place


# Aborts that keep the finished AI chunks and send only the rest to Google
# (_google_fill_and_save). 'cancelled' and 'invalid_key' are not here.
GOOGLE_FILL_ABORTS = ('quota', 'ratelimit', 'overload', 'error', 'crash')
_GOOGLE_FALLBACK_WHY = {
    'quota': 'מכסת ה-AI היומית נגמרה',
    'ratelimit': 'AI: עומס זמני חורג',
    'overload': 'Gemini עמוס',
    'error': 'שגיאת AI',
    'crash': 'שגיאת AI',
}


# ---- download / translate -------------------------------------------

def google_rescue(blocks, source_lang):
    """The rung between "Gemini refuses this text" and "ship it in English".

    Every stage above this one is still Gemini, and a safety block is
    Gemini's judgement of the CONTENT -- so a chunk that blocked with the
    Arabic reference, with five alternate references, bisected down to one
    entry, and again with no reference at all, is not going to come back
    translated on a sixth Gemini call. It is exactly then that the ladder
    used to fall to "keep the source", which is the one outcome the whole
    feature exists to avoid: an English line on screen.

    Google Translate is a different engine with a different policy, and it
    is already shipped here as the whole-file fallback. It gives no gender
    nuance and no cast context -- this is machine Hebrew -- but machine
    Hebrew is a translation, and English is not.

    Returns the blocks with their TEXT lines in Hebrew and their index and
    timecode untouched, or None. On None the caller keeps the source, so
    the English line is never lost to a failure here. Never raises.
    """
    # The WHOLE body is guarded, not just the import. This is the last rung
    # before the ladder keeps the English line, and it is reached from inside
    # an except handler: an exception escaping here would propagate past the
    # chunk translator and cost the entire chunk, not the one line it was
    # called to save. Any failure has exactly one meaning -- no rescue -- and
    # the caller then keeps the source block whole.
    try:
        from resources.lib.ai_translation.providers import google_translate
        out = []
        for block in blocks:
            lines = block.split('\n')
            if len(lines) < 3:
                return None
            heb = google_translate.translate_lines(lines[2:], source_lang)
            if not heb or len(heb) != len(lines) - 2:
                return None
            # A rescue that produced no Hebrew is a failed rescue, and
            # returning it would REPLACE the English line with something
            # worse. Checked PER BLOCK, not over the joined result: one block
            # coming back blank would otherwise hide behind another block's
            # real Hebrew and ship as a rescued but textless cue. Today every
            # caller passes a single block, so this only matters if a third
            # one is ever added -- which is exactly when it would be missed.
            if not any(u'֐' <= c <= u'׿' for c in ''.join(heb)):
                return None
            out.append('\n'.join(lines[:2] + heb))
        # looks_hebrew on top, as a second opinion over the whole reply. It
        # answers True on thin evidence by design -- it is a "don't block on
        # too little text" gate -- so it can pass a blank or punctuation-only
        # reply, which is why the explicit character test above is the one
        # that actually guarantees there is Hebrew here to ship.
        if not classify.looks_hebrew('\n'.join(out), min_alpha=1):
            return None
        return out
    except Exception as e:
        kodi.log('Google rescue failed ({0}) -- keeping the source '
                       'text for this chunk'.format(e), level='WARNING')
        return None


def google_fill_missing(missing, source_lang):
    """Google Translate for entries a chunk is about to keep in English (its
    budget spent, the reply still without them): one request per ~3,000
    characters. Returns only the blocks that came back with Hebrew, on the
    start times asked for; [] on any failure, and the caller keeps the
    source for the rest. Never raises."""
    try:
        if not missing:
            return []
        from resources.lib.ai_translation.providers import google_translate
        out = google_translate.translate_srt('\n\n'.join(missing), source_lang)
        if not out:
            return []
        got = [b for b in srt_blocks.parse_blocks(out)
               if any(u'\u05d0' <= c <= u'\u05ea'
                      for c in ''.join(b.split('\n')[2:]))]
        return timing.align_blocks(missing, got)
    except Exception as e:
        kodi.log('Google fill failed ({0}) -- keeping the source text '
                       'for {1} entr(ies)'.format(e, len(missing or ())),
                       level='WARNING')
        return []


def is_google_translated(path):
    """True if this cached translation was produced by Google Translate (a
    sidecar '<path>.google' marker is written next to it). Such machine
    translations must NEVER be shared to the community pool.

    Delegates to srt.is_google_translated so the sidecar is read in exactly one
    place -- srt.may_carry_arabic_leak needs the same signal, and two copies of
    a detector drift apart the same way two copies of a rule do.

    Fails to True, the OPPOSITE of may_carry_arabic_leak: if we cannot tell,
    the safe answer for POOL SHARING is "assume Google, do not share", whereas
    the safe answer for a text-deleting repair is "assume unknown, do not
    touch". Same signal, opposite safe failures -- which is why the detector
    itself does not choose one.
    """
    try:
        return classify.is_google_translated(path)
    except Exception:
        return True


def preview_clean(blocks, src_text):
    """The cleaners the live preview runs over merged chunk blocks (see the
    chunk_ready note in translate()): every Hebrew line here is our model's
    reply or Google's, and untranslated source carries no Hebrew, so each
    cleaner is safe on the mix."""
    return timing.clamp_cue_durations(
        postfix_rtl.fix_rtl_punctuation(
            postfix_cleanup.mend_line_breaks(postfix_glyphs.normalize_glyphs(
                postfix_cleanup.strip_niqqud(
                    postfix_glyphs.fold_stray_latin_letter(
                        postfix_glyphs.fold_foreign_in_hebrew_word(
                            postfix_leaks.strip_source_echo(
                                postfix_glyphs.strip_leaked_arabic(
                                    postfix_cleanup.strip_hebrew_speaker_prefix(
                                        postfix_cleanup.strip_leaked_speaker_prefix(
                                            srt_blocks.stitch_blocks(blocks)),
                                        src_text))))))))))


def google_fill_and_save(chunks, done_by_index, src_text, source_lang,
                          translated, info, reason='', google_chunks=None):
    """Keep the chunks Gemini finished and let Google translate only the rest.

    NG continue-and-retry: finished work is not thrown away when a run stops.
    Before this, a daily-quota or rate-limit abort on chunk 9 of 12 sent the
    WHOLE film to Google, discarding eight chunks of AI Hebrew with their
    gender work. The file is still marked Google (never pooled), and a later
    watch with quota translates it with AI again (see
    _google_cache_upgradeable). `google_chunks` is the cached Google file this
    run was replacing, cut like `chunks`: its Hebrew fills the gaps with no
    second request. Falls back to the whole-file Google path when nothing
    finished, Google's reply does not line up entry for entry, or anything
    here fails."""
    missing = [i for i in range(1, len(chunks) + 1) if i not in done_by_index]
    if not done_by_index or not missing:
        return google_translate_and_save(src_text, source_lang, translated,
                                          info, reason=reason)
    try:
        missing_blocks = [b for i in missing for b in chunks[i - 1]]
        if google_chunks:
            filled = [b for i in missing for b in google_chunks[i - 1]]
        else:
            heb = None
            try:
                from resources.lib.ai_translation.providers import google_translate
                heb = google_translate.translate_srt(
                    postfix_cleanup.strip_leaked_speaker_prefix('\n\n'.join(missing_blocks)),
                    source_lang)
            except Exception as e:
                kodi.log('google fill failed: {0}'.format(e), level='WARNING')
            filled = srt_blocks.parse_blocks(heb or '')
        if len(filled) != len(missing_blocks):
            kodi.log('google fill returned {0} of {1} entries -- translating '
                           'the whole file with Google'.format(
                               len(filled), len(missing_blocks)), level='WARNING')
            return google_translate_and_save(src_text, source_lang, translated,
                                              info, reason=reason)
        # The AI cleaners run on the AI part only: they are written for our
        # model's slips, and the read repair and srt.may_carry_arabic_leak
        # exempt Google output on purpose (an Arabic quote in a Google line is
        # the film's own text). Index-keyed, so the subset keeps its numbers.
        ai_blocks = [postfix_cleanup.drop_model_notes(b) for i in sorted(done_by_index)
                     for b in done_by_index[i]]
        cleaned = srt_blocks.parse_blocks(preview_clean(ai_blocks, src_text))
        if len(cleaned) != len(ai_blocks):
            cleaned = ai_blocks
        merged, ai_at, cursor = [], 0, 0
        for i in range(1, len(chunks) + 1):
            if i in done_by_index:
                merged.extend(cleaned[ai_at:ai_at + len(done_by_index[i])])
                ai_at += len(done_by_index[i])
            else:
                merged.extend(filled[cursor:cursor + len(chunks[i - 1])])
                cursor += len(chunks[i - 1])
        text = timing.clamp_cue_durations(
            postfix_glyphs.normalize_glyphs(srt_blocks.stitch_blocks(merged)))
        if not is_mostly_hebrew(text):
            return google_translate_and_save(src_text, source_lang, translated,
                                              info, reason=reason)
        translation_cache.save_text(translated, text)
        try:
            open(translated + '.google', 'w').close()  # keep it out of the pool
        except Exception:
            pass
        reapply_rtl_fix_in_place(translated)
    except Exception as e:
        kodi.log('google fill failed ({0}) -- translating the whole file '
                       'with Google'.format(e), level='WARNING')
        return google_translate_and_save(src_text, source_lang, translated,
                                          info, reason=reason)
    kodi.log('{0}: kept {1} of {2} chunk(s) from Gemini; the other {3} '
                   'entr(ies) are Google Translate{4}'.format(
                       reason or 'abort', len(done_by_index), len(chunks),
                       len(missing_blocks),
                       ' (from the cached file)' if google_chunks else ''),
                   level='WARNING')
    why = _GOOGLE_FALLBACK_WHY.get(reason, 'AI')
    kodi.notify('{0} — {1} מתוך {2} חלקים תורגמו ב-AI, השאר ב-Google Translate'.format(
        why, len(done_by_index), len(chunks)), time_ms=4000)
    return translated


def google_cache_upgradeable(path):
    """True when a cached file is a Google fallback that AI can now replace:
    AI mode, a key, and today's quota not known to be used up. Without this a
    quota or rate-limit night left a title on Google Translate for good --
    the file is Hebrew, so it was served as "from cache" on every watch."""
    try:
        if not path or not os.path.isfile(path) or not is_google_translated(path):
            return False
        if (settings.get_setting('translation_mode', 'ai') or 'ai') != 'ai':
            return False
        if not (settings.get_setting('api_key', '') or '').strip():
            return False
        from resources.lib.ai_translation import quota
        model = settings.get_setting('model', '') or ''
        if (not settings.get_bool('ai_paid_mode', False)
                and quota.exhausted_today(model)):
            return False
        return True
    except Exception:
        return False


def clear_google_marker(path):
    """An AI translation now owns `path`; a '.google' sidecar left from an
    earlier fallback would keep it out of the pool and re-translate it."""
    try:
        os.remove(path + '.google')
    except OSError:
        pass


def google_translate_and_save(src_text, source_lang, translated, info,
                               reason=''):
    """Translate src_text to Hebrew with Google Translate and save it to the
    cache path `translated`. Marks it Google-translated (sidecar) so it is
    never pooled, applies the RTL punctuation fix, and returns the path (or
    None on failure)."""
    heb = None
    try:
        from resources.lib.ai_translation.providers import google_translate
        # Google Translate has no cast/gender mechanism, so the 'MABEL:' speaker
        # prefixes we keep for the AI are just noise to it (and would leak into its
        # output). Strip them from Google's source only -- entry-preserving, so the
        # cache path (keyed by the prefix-kept source hash) still lines up.
        src_text = postfix_cleanup.strip_leaked_speaker_prefix(src_text)
        heb = google_translate.translate_srt(src_text, source_lang)
    except Exception as e:
        kodi.log('google translate failed: {0}'.format(e),
                       level='WARNING')
    if not heb or not heb.strip():
        kodi.notify('Google Translate נכשל — נסה שוב', time_ms=4000)
        return None
    heb = postfix_cleanup.mend_line_breaks(heb)
    try:
        translation_cache.save_text(translated, heb)
        try:
            open(translated + '.google', 'w').close()  # keep it out of the pool
        except Exception:
            pass
        reapply_rtl_fix_in_place(translated)
    except Exception as e:
        kodi.log('google save failed: {0}'.format(e), level='WARNING')
        return None
    if reason in _GOOGLE_FALLBACK_WHY:
        _fb_msg = _GOOGLE_FALLBACK_WHY[reason] + ' — תורגם עם Google Translate'
    else:
        _fb_msg = 'תורגם עם Google Translate'
    kodi.notify(_fb_msg, time_ms=4000)
    return translated
