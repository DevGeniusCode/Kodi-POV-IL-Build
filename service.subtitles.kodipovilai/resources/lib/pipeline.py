# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The orchestrator of the subtitle pipeline (ARCHITECTURE_SUBTITLES.md, section 1.7.3).
#
# The SINGLE ROUTE (Phase 3, step 3d): every caller outside fetching/ and ai_translation/ reaches
# list_candidates and resolve only through the two functions below (a test of the contract check
# enforces it). They still only delegate, one line each; the readable lifecycle
# fetching -> scoring_matching -> ai_translation -> sync -> delivery waits for the split of
# resolve in Phase 4.
#
# Both imports sit inside the functions on purpose: importing this module stays free, and the
# import cycles between the feature packages stay broken.


def search(info, modal_progress=True, quick=False, on_provider=None):
    """The rows of the subtitle list for the playing video (what list_candidates returns).
    on_provider(name, rows): autosub's fast lane, told when Ktuvit / Wizdom finish (bridge.search)."""
    from resources.lib.fetching import service
    if on_provider is None:
        return service.list_candidates(info, modal_progress=modal_progress, quick=quick)
    return service.list_candidates(info, modal_progress=modal_progress, quick=quick,
                                   on_provider=on_provider)


def resolve(link, info, **kwargs):
    """The Hebrew SRT path for one picked link (what the download step needs)."""
    from resources.lib.ai_translation import service
    return service.resolve(link, info, **kwargs)
