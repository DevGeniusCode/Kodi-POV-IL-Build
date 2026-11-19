# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The download side of the pipeline (resolve): every link type (passthrough, pool, engine, engine_ai,
# embedded_ai, embedded_sync, ai) to a Hebrew SRT on disk, including the whole AI translation.

from resources.lib.ai_translation.job.state import NEXT, JobState, begin
from resources.lib.ai_translation.job.links.passthrough import passthrough
from resources.lib.ai_translation.job.links.pool import pool
from resources.lib.ai_translation.job.links.embedded_sync import embedded_sync
from resources.lib.ai_translation.job.links.engine import engine
from resources.lib.ai_translation.job.links.embedded_ai import embedded_ai
from resources.lib.ai_translation.job.links.engine_ai import engine_ai
from resources.lib.ai_translation.job.ai.prepare import prepare
from resources.lib.ai_translation.job.ai.cache import cache
from resources.lib.ai_translation.job.ai.handoff import handoff
from resources.lib.ai_translation.job.ai.request import request
from resources.lib.ai_translation.job.ai.reference import reference
from resources.lib.ai_translation.job.ai.plan import plan
from resources.lib.ai_translation.job.ai.dispatch import dispatch
from resources.lib.ai_translation.job.ai.gender import merge
from resources.lib.ai_translation.job.ai.finish import finish


STAGES = (
    begin,
    passthrough,
    pool,
    embedded_sync,
    engine,
    embedded_ai,
    engine_ai,
    prepare,
    cache,
    handoff,
    request,
    reference,
    plan,
    dispatch,
    merge,
    finish,
)


def resolve(link, info, progress_cb=None, progressive_cb=None,
            extract_progress_cb=None, selection=None, fallback_link='',
            fallback_links=None):
    """Return a filesystem path to the SRT for the chosen link.

    For passthrough, hand back the existing file path. For ai
    entries, translate (or read from cache) and return the cached
    file's path. progress_cb, if provided, is called as
    progress_cb(chunk_index, total_chunks).

    progressive_cb, if provided, is an opt-in fast-first-chunk
    callback used by the DarkSubs auto_translate path to release the
    English fallback to Kodi immediately and then swap subtitles in
    flight as each Hebrew chunk lands. Signature:
        progressive_cb(phase, payload)
    where phase is one of:
        'first_ready'  payload={'fallback_text', 'source_id'}
        'chunk_ready'  payload={'completed','total','merged_text',
                                'source_id'}
        'done'         payload={'success', 'source_id'}
    The final canonical Hebrew bytes written via cache.save_text() are
    the same as without a callback for the same source SRT, apart from
    one step that runs only with it: the gender proofread by a stronger
    model (gender_proofread), which takes minutes a caller waiting for
    the return value should not wait. A callback exception NEVER aborts
    the translation."""
    S = JobState(link, info, progress_cb, progressive_cb, extract_progress_cb, selection, fallback_link,
                 fallback_links)
    for stage in STAGES:
        result = stage(S)
        if result is not NEXT:
            return result
