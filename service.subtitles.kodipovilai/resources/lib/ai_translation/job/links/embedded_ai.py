# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Link type "embedded_ai": the video's embedded foreign subtitle, read, then translated by the AI
# stages (this stage turns the link into an "ai" one, or into "engine_ai" when the track is unreadable).

import os
from functools import partial
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi
from resources.lib.scoring_matching.ranking import lang_display_he
from resources.lib.ai_translation.embedded_steps import embedded_steps, StepsScreen
from resources.lib.ai_translation.policy import embedded_translation_policy
from resources.lib.ai_translation.progress import status, still_playing
from resources.lib.ai_translation.proofread.gp_jobs import EMBEDDED_RESOLVED, keep_embedded_source, remember_embedded
from resources.lib.fetching.embedded.extraction import embedded_aligned_source_srt, external_for_unreadable_embedded, extract_embedded_srt, note_embedded_miss
from resources.lib.ai_translation.job.state import NEXT, translation_cancelled


def embedded_ai(S):
    """Link type "embedded_ai"."""
    if S.kind == 'embedded_ai':
        # Translate the video's EMBEDDED foreign subtitle. Extract its text
        # (which carries the video's OWN cue timestamps) and feed it to the AI
        # pipeline below, so the Hebrew is perfectly synced with NO re-sync step.
        # The Advanced mode selector controls which method is attempted. Every
        # mode remains fail-open: on a miss we return None and the caller falls
        # through to the normal external-subtitle path.
        # this link's payload from an earlier video goes now: one made here
        # is remembered below, and a miss leaves none (proofread_later)
        EMBEDDED_RESOLVED.pop(S.link, None)
        _emb_policy = embedded_translation_policy()
        _emb_mode = _emb_policy['mode']
        if not _emb_policy['enabled']:
            return None
        _emb_lang = S.payload.get('src_lang') or 'en'
        _emb_why = {}     # why the embedded text could not be read, if it was not
        # FAST PATH first (debrid-safe): re-sync an external source sub to the
        # embedded track's DENSE cue-time skeleton (~5 range requests) rather
        # than pulling ~1700 text blocks -- which a strict provider (TorBox)
        # 429s, starving the player. Only if that misses (no dense Cues index /
        # no external source / low-confidence alignment) do we fall back to the
        # full-text extract (perfect on a lenient provider like Real-Debrid).
        emb_path, _used_lang = None, None
        if _emb_policy['try_align']:
            status('AI: מסנכרן לפי הכתובית המובנית...', time_ms=3000)
            emb_path, _used_lang = embedded_aligned_source_srt(
                S.info, _emb_lang, progress_cb=S.extract_progress_cb,
                allow_http=_emb_policy['allow_http'])
        if emb_path and os.path.isfile(emb_path):
            # The cross-language fallback may have aligned a different language
            # than the picked track (e.g. picked Spanish, no external Spanish ->
            # aligned English); translate from the language we ACTUALLY produced.
            if _used_lang and _used_lang != _emb_lang:
                # Tell the user plainly WHY the source language changed: no
                # external subtitle in the picked language syncs to its embedded
                # timeline (typically only CAM/other-release subs exist, whose
                # cuts don't line up), so we used another language that does.
                # Without this, a "from cache" for English after the user picked
                # French/German reads like a bug rather than a graceful fallback.
                try:
                    kodi.notify(
                        'AI: אין כתובית מסונכרנת ב{0} — מתרגם מ{1}'.format(
                            lang_display_he(_emb_lang),
                            lang_display_he(_used_lang)),
                        time_ms=5000)
                except Exception:
                    pass
            _emb_lang = _used_lang or _emb_lang
        elif _emb_policy['try_extract']:
            status('AI: מחלץ תרגום מובנה...', time_ms=3000)
            # The minutes the viewer is at, in Hebrew, while the rest of the
            # track is still being read (_EmbeddedSteps, _StepsScreen).
            _screen = StepsScreen(S.link, S.info.get('picked_release') or '',
                                   S.progressive_cb, S.timing_selection)

            def _steps_restore():
                # the embedded track itself, as the pick put it on screen
                # before this job started (default.py); not when the viewer
                # has chosen something else since
                if translation_cancelled(S):
                    return
                from resources.lib.fetching.embedded import streams as _seb
                _seb.select_embedded(S.payload.get('stream_index'),
                                     lang=_emb_lang)

            _steps = None
            if _emb_policy['allow_http'] and not S.proofread_only:
                _steps = embedded_steps(
                    S.info, lambda: _emb_why.get('lang_used') or _emb_lang,
                    _screen.show, partial(translation_cancelled, S),
                    restore=(_steps_restore if (
                        S.progressive_cb is not None
                        and S.payload.get('stream_index') is not None)
                        else None))
            try:
                emb_path = extract_embedded_srt(
                    S.info, _emb_lang, S.payload.get('track_num'),
                    progress_cb=S.extract_progress_cb,
                    allow_http=_emb_policy['allow_http'], steps=_steps,
                    cancelled=partial(translation_cancelled, S), why=_emb_why)
                if (_steps is not None and emb_path
                        and os.path.isfile(emb_path)):
                    # The whole track, once: the last pass read lines no view
                    # has shown, and the handler does not show the full job's
                    # 'first_ready'.
                    try:
                        _steps.feed(translation_cache.load_text(emb_path) or '',
                                    translate=False)
                        if _steps.settle(cancelled=partial(translation_cancelled, S)):
                            kodi.log(
                                'embedded steps: the whole track was in before '
                                'the answer for the first view -- waited for '
                                'it, shown={0}'.format(_steps.shown),
                                level='INFO')
                    except Exception:
                        pass
            finally:
                if _steps is not None:
                    S.steps_he = _steps.finish()
                    kodi.log(
                        'embedded steps: {0} window(s), {1} request(s), {2} '
                        'line(s) in Hebrew, shown={3}, embedded track put '
                        'back {4} time(s)'.format(
                            _steps.windows, _steps.requests, len(S.steps_he),
                            _steps.shown, _steps.handed_back), level='INFO')
            if _steps is not None and not (
                    emb_path and os.path.isfile(emb_path)):
                # given up part-way: the view holds only what was read
                _steps.back_out()
            if _screen.shown and S.progressive_cb is not None:
                S.steps_source_id = _screen.source_id
            # the id the viewer's handler adopted with the first view
            S.progressive_cb = _screen.handler()
        _unreadable = None
        if not emb_path or not os.path.isfile(emb_path):
            # A file whose index has no entry for the subtitle lines cannot be
            # read from a stream short of downloading all of it. Instead of
            # failing (field: 'could not extract -- try another subtitle'),
            # translate the external subtitle that best matches the release,
            # as its own "AI translation" row would (owner, 14 Nov 2026: do
            # not hide the row, fix it).
            if (_emb_why.get('reason') in ('no_index', 'no_text', 'not_mkv')
                    and not S.proofread_only and not translation_cancelled(S)
                    and still_playing()):
                _unreadable = external_for_unreadable_embedded(S.info, _emb_lang)
            if _unreadable is None:
                kodi.log('embedded_ai: mode={0} produced no synced source '
                               '-- deferring to the external path'.format(
                                   _emb_mode), level='INFO')
                if not S.proofread_only:
                    note_embedded_miss(_emb_why.get('reason'))
                return None
            _reason = _emb_why.get('reason')
            _no_text = _reason == 'no_text'
            kodi.log(
                'embedded_ai: the {0} embedded track cannot be read {1} -- '
                'translating the external {2} subtitle "{3}" instead'.format(
                    _emb_lang,
                    '(it is a picture track and the file has no other text subtitle)'
                    if _no_text else
                    '(the file is not Matroska; its embedded subtitles are not '
                    'read yet)' if _reason == 'not_mkv'
                    else 'from the stream (its index has no subtitle lines)',
                    _unreadable.get('src_lang'), _unreadable.get('filename') or ''),
                level='INFO')
            status('AI: הכתובית המובנית ב{0} בקובץ הזה היא תמונה ולא טקסט — '
                    'מתרגם במקומה כתובית ב{1} שתואמת לגרסה'.format(
                        lang_display_he(_emb_lang),
                        lang_display_he(_unreadable.get('src_lang')))
                    if _no_text else
                    'AI: את הכתובית המובנית בקובץ הזה אי אפשר לקרוא — '
                    'מתרגם במקומה כתובית תואמת לגרסה', time_ms=6000)
            S.payload, S.kind = _unreadable, 'engine_ai'
        else:
            _used = _emb_why.get('lang_used') or ''
            try:
                from resources.lib.fetching.embedded.extract.tracks import lang_key
                _switched = bool(_used) and (lang_key(_used)
                                             != lang_key(_emb_lang))
            except Exception:
                _switched = False
            if _switched:
                # the viewer was told when the track was chosen (_on_track)
                kodi.log(
                    'embedded_ai: translating from the {1} embedded track, not '
                    'the {0} picture one'.format(_emb_lang, _used), level='INFO')
                _emb_lang = _used
            _kept_src = keep_embedded_source(emb_path)
            status('AI: מתרגם תרגום מובנה לעברית...', time_ms=3000)
            S.payload = {'type': 'ai',
                       'source_lang': _emb_lang,
                       'local_path': emb_path,
                       # An embedded track carries no release string of its own, so
                       # use the video's real release (the 'ai' pipeline's own
                       # fallback chain -- picked_release/tagline/label -- fills any
                       # gap). NEVER reuse the '[מובנה] XX' placeholder here: it
                       # would poison the pool upload's release tag and the display
                       # name for every embedded translation.
                       'release': S.info.get('picked_release') or '',
                       # Mark this as an EMBEDDED-sourced translation so the pool
                       # stores it under kind='ai_emb' -- it is synced to the video's
                       # own timing, so it is surfaced distinctly ("תרגום מובנה")
                       # and sorted first among the community AI translations.
                       'embedded': True,
                       'force_ai': True}
            if _kept_src:
                remember_embedded(S.link, dict(S.payload, local_path=_kept_src))
            S.kind = 'ai'
    return NEXT
