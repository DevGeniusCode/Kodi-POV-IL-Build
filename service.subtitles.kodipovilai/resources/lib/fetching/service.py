# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The subtitle list (list_candidates): local files, embedded streams, providers, the community pool and
# the AI-translation rows, ranked and labelled for the dialogs. SEARCH side of the pipeline.

from resources.lib.fetching.search.state import NEXT, SearchState, context, local_files
from resources.lib.fetching.search.engine import engine, ai_prep
from resources.lib.fetching.search.pool import pool
from resources.lib.fetching.search.ai_rows import ai_variants, ai_rows, ai_entries, finish


STAGES = (
    context,
    local_files,
    engine,
    ai_prep,
    pool,
    ai_variants,
    ai_rows,
    ai_entries,
    finish,
)


def list_candidates(info, modal_progress=True, quick=False):
    """Build the list Kodi's subtitle dialog will render.

    quick=True makes no network request: the provider search is used only
    when it is already cached and the community pool is skipped; info gets
    '_list_pending' = True when either was left out, so the caller can
    complete the list later (the "בחירת כתוביות" window opens on this one).

    Returns a list of dicts with keys: filename, language, link,
    sync, rating. Empty list if nothing plausible is available.
    """
    S = SearchState(info, modal_progress, quick)
    for stage in STAGES:
        result = stage(S)
        if result is not NEXT:
            return result
