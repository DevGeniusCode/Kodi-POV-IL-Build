# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Link type "passthrough": a Hebrew subtitle that already exists as a file.

import os
from resources.lib.ai_translation.progress import status
from resources.lib.delivery.rtl import rtl_delivery_copy
from resources.lib.sync.delivery_gate import sync_hebrew_delivery
from resources.lib.ai_translation.job.state import NEXT


def passthrough(S):
    """Link type "passthrough"."""
    if S.kind == 'passthrough':
        path = S.payload.get('path')
        status(
            'AI: כתובית קיימת (passthrough) - {0}'.format(
                os.path.basename(path) if path else '?'),
            time_ms=4000)
        if path and os.path.isfile(path):
            return sync_hebrew_delivery(
                S.info, rtl_delivery_copy(path),
                source_release=S.payload.get('release') or '',
                selection=S.timing_selection, fallback_link=S.fallback_link,
                fallback_links=S.fallback_links)
        return None
    return NEXT
