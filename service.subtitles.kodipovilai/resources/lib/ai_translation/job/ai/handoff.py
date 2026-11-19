# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# AI stages, 3 of 10 -- handoff: the English placeholder and cached Google file go to the live preview,
# the kick-off toast, a source that is already Hebrew.

from resources.lib.core.cache import translation_cache
from resources.lib.ai_translation.repairs import postfix_cleanup
from resources.lib.core import kodi
from resources.lib.core.srt import blocks as srt_blocks
from resources.lib.ai_translation.dispatch.pacing import is_mostly_hebrew
from resources.lib.ai_translation.embedded_steps import unique_starts
from resources.lib.ai_translation.job.ai.prepare import deliver
from resources.lib.ai_translation.job.state import NEXT


def handoff(S):
    """Live-preview hand-off, kick-off toast, a source that is already Hebrew."""
    # Captured once and reused for ALL progressive callback emissions
    # so the caller can correlate first_ready/chunk_ready/done into a
    # single in-flight translation. Same value the cache key uses
    # above. Safe to evaluate here -- both ids are now stable.
    S.progressive_source_id = S.steps_source_id or S.early_source_id or S.content_id
    # Replacing a cached Google Translate file: the viewer already had that
    # Hebrew, so it stands in for the English placeholder while the AI works
    # (a failed run hands the same file back, see _keep_replaced_google).
    S.google_prev = ''
    if S.replacing_google:
        try:
            S.google_prev = translation_cache.load_text(S.replacing_google) or ''
        except Exception:
            S.google_prev = ''

    # Fast-first-chunk hand-off: release the English fallback to the
    # caller (e.g. DarkSubs) so Kodi can start showing SOMETHING in
    # seconds while we translate in the background. The bytes are the
    # POST-strip src_text with any leading ALL-CAPS speaker prefix removed
    # for display -- src_text itself keeps those prefixes (Gemini uses them
    # for per-line gender), but the onscreen English placeholder must not
    # show raw "MABEL:" tags. A buggy callback must not abort us.
    if S.steps_he:
        # The views' Hebrew stands in for lines the full job has not reached
        # (the chunk merge below), keyed by a start the source names once.
        S.steps_he = dict(
            (s, S.steps_he[s])
            for s in unique_starts(srt_blocks.parse_blocks(S.src_text))
            if s in S.steps_he)
    if S.progressive_cb is not None:
        try:
            S.progressive_cb('first_ready', {
                'fallback_text': (S.google_prev
                                  or postfix_cleanup.strip_leaked_speaker_prefix(S.src_text)),
                'source_id': S.progressive_source_id,
                'release': S.src_release,
            })
        except Exception as e:
            kodi.log(
                'progressive_cb first_ready raised: ' + str(e),
                level='WARNING')
    if S.google_prev and S.progressive_cb is not None:
        # The picker shows its own English placeholder and ignores
        # first_ready; a chunk_ready puts the Hebrew the viewer already had
        # on screen now, before the reference download and the first AI chunk.
        try:
            S.progressive_cb('chunk_ready', {
                'completed': 0,
                'merged_text': S.google_prev,
                'source_id': S.progressive_source_id,
                'release': S.src_release,
            })
        except Exception as e:
            kodi.log('progressive_cb chunk_ready (cached Google) raised: '
                           + str(e), level='WARNING')

    kodi.log(
        'No cache hit. Starting translation. imdb={0} content_id={1} '
        'src_len={2}'.format(S.imdb_id, S.content_id, len(S.src_text)),
        level='INFO')

    # Up-front heads-up so the user understands the wait. The
    # progress dialog itself is a DialogProgressBG which sits in
    # the corner during video playback, easy to miss. Kodi has an
    # internal timeout on subtitle downloads and will likely show
    # its own "subtitle download failed" toast before we finish on
    # longer pieces -- the translation continues anyway and the
    # result is cached, so on the next subtitle-search the user
    # sees it as a cached entry and gets it instantly.
    # Kept VERY short on purpose -- Kodi's notification widget
    # scrolls anything past ~50 visible chars, and the scroll
    # direction in most skins is hardcoded LTR which makes a long
    # Hebrew message read "backwards" to the user. The 25/50/75 %
    # milestone toasts later replace the old "התקדמות תופיע בפינה"
    # explanation that used to bloat this kickoff line.
    kodi.notify(
        'AI מתרגם (כדקה-שתיים). תתעלם משגיאות ביניים.',
        time_ms=5000,
    )

    # Sanity: if the source is ALREADY predominantly Hebrew (an upstream
    # mislabel handed us a Hebrew sub), translating it is pointless -- pass it
    # through unchanged. Be CONSERVATIVE: a source that merely *contains* some
    # Hebrew (an English SDH sub with a Hebrew sign/song line, a bilingual sub)
    # must still be translated. The old test used language_detect.detect(),
    # whose 'he' verdict fires on an ABSOLUTE count (>30 Hebrew chars anywhere
    # in the first 8000) -- so a mostly-English source carrying a handful of
    # Hebrew characters was silently passed through as "already Hebrew". It then
    # showed the untranslated English and, because the passthrough writes to the
    # translation cache UNGATED (unlike every real translation, which
    # _is_mostly_hebrew-gates), every retry re-read it, judged it non-Hebrew, and
    # discarded it as "empty/echoed" -- an endless no-op loop. (Field-confirmed:
    # on the SAME movie, the English embedded source died silently here while the
    # Russian one -- 'ru', never 'he' -- translated fine. The English sub carried
    # enough Hebrew, e.g. a credit line, to trip the >30 absolute count.) Use a
    # RATIO over the WHOLE source (not just the first 8000 chars, so a localized
    # Hebrew credit/header block can't skew it) and only skip when Hebrew clearly
    # dominates.
    if is_mostly_hebrew(S.src_text, min_ratio=0.60):
        kodi.log(
            'translate step: source is already predominantly Hebrew -- '
            'passing through without translation', level='INFO')
        translation_cache.save_text(S.translated, S.src_text)
        return deliver(S, S.translated)
    return NEXT
