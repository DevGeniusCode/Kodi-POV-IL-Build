# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Link type "engine_ai": a foreign subtitle of the sources engine, downloaded, then translated by the
# AI stages (this stage turns the link into an "ai" one).

import os
from resources.lib.core import kodi
from resources.lib.ai_translation.progress import status
from resources.lib.ai_translation.job.state import NEXT


def engine_ai(S):
    """Link type "engine_ai"."""
            # fall through to the AI logic below

    if S.kind == 'engine_ai':
        # User picked "AI Hebrew (translate from English)" sourced from the
        # built-in engine. Download the English sub via the engine, then fall
        # through to the normal AI pipeline below to translate it to Hebrew.
        try:
            from resources.lib.fetching.embedded import streams
            eng_path = streams.download(S.payload)
        except Exception as e:
            kodi.log('resolve engine_ai download failed: {0}'
                           .format(e), level='ERROR')
            eng_path = None
        if not eng_path or not os.path.isfile(eng_path):
            if not S.proofread_only:
                kodi.notify('AI: לא ניתן היה להוריד את כתובית המקור',
                                  time_ms=4000)
            return None
        status('AI: מוריד אנגלית ומתרגם לעברית...', time_ms=3000)
        S.payload = {'type': 'ai',
                   'source_lang': S.payload.get('src_lang') or 'en',
                   'local_path': eng_path,
                   # Keep the SOURCE sub's real release name (e.g. "Movie.2010.
                   # 1080p.BluRay.x264-GROUP") so the delivered file and the pool
                   # upload carry it instead of a generic Title.Year.
                   'release': S.payload.get('filename') or '',
                   'force_ai': True}  # user explicitly asked to translate
        if S.proofread_only:
            S.payload['proofread_only'] = True
        S.kind = 'ai'
    return NEXT
