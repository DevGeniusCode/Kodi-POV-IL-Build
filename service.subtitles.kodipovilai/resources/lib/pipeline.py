# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The orchestrator of the subtitle pipeline (ARCHITECTURE_SUBTITLES.md, section 1.7.3).
#
# A STUB IN PHASE 2: it only delegates, one line per step, to the functions the split
# left in the feature packages. Nothing imports it yet. Phase 3 (step 3a) rewires entry/
# to call it and turns the two functions below into the readable lifecycle
# fetching -> scoring_matching -> ai_translation -> sync -> delivery, passing the
# contracts of core/contracts.py instead of the implicit dicts.
#
# Both imports sit inside the functions on purpose: importing this module stays free.


def search(info, modal_progress=True, quick=False):
    """The rows of the subtitle list for the playing video (what list_candidates returns)."""
    from resources.lib.fetching import service
    return service.list_candidates(info, modal_progress=modal_progress, quick=quick)


def resolve(link, info, **kwargs):
    """The Hebrew SRT path for one picked link (what the download step needs)."""
    from resources.lib.ai_translation import service
    return service.resolve(link, info, **kwargs)
