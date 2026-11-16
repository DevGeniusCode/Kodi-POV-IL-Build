# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""The Tonight-tile seeding step.

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.
"""


def _maybe_add_tonight_entry():
    try:
        from resources.libs.tonight.entrypoints import ensure
        ensure()
    except Exception:
        pass  # An optional home shortcut must not interrupt startup repairs.
