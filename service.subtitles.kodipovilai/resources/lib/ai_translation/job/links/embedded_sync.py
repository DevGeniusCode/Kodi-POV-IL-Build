# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Link type "embedded_sync": a Hebrew subtitle re-timed onto the embedded track's cue skeleton.

import os
from resources.lib.ai_translation.repairs import postfix_rtl
from resources.lib.core import kodi, selection as core_selection
from resources.lib.ai_translation.policy import embedded_translation_policy
from resources.lib.ai_translation.progress import status
from resources.lib.fetching.embedded.extraction import embedded_aligned_source_srt
from resources.lib.ai_translation.job.state import NEXT, publish_timing_status, selection_current


def embedded_sync(S):
    """Link type "embedded_sync"."""
    if S.kind == 'embedded_sync':
        # A Hebrew subtitle the user could already have picked -- but re-timed
        # onto the EMBEDDED track's own cue skeleton, which is the video's
        # ground-truth timeline. Two things come out of that:
        #   * it is synced as exactly as the embedded track is, which is the
        #     reason anyone prefers the embedded track in the first place;
        #   * it is OUR file, so it goes through fix_rtl_punctuation and the
        #     mark that closes each line lands at the right end -- which the
        #     embedded track itself cannot, because Kodi draws that one with an
        #     LTR base direction out of bytes we do not own.
        #
        # It reads only the Cues INDEX (a handful of range requests), never the
        # track's text, so it is the cheap half of what the abandoned extraction
        # would have cost -- that is the whole reason this shape survives where
        # the extraction did not.
        #
        # The honest trade, and the user knows it: this is a DIFFERENT
        # translation from the one inside the file. That is why it is a picker
        # entry to choose rather than something applied automatically.
        status('מסנכרן כתובית עברית לתזמון המובנה...', time_ms=4000)
        _sync_path = None
        try:
            _pol = embedded_translation_policy()
            _sync_path, _used = embedded_aligned_source_srt(
                S.info, 'he', allow_http=bool(_pol.get('allow_http', True)),
                strict_lang=True, include_he=True)
            if _sync_path and _used != 'he':
                # strict_lang should make this impossible; if it ever is not,
                # delivering it would put a foreign-language subtitle on screen
                # for a Hebrew pick, so refuse rather than trust the flag.
                kodi.log('embedded_sync: aligner returned {0!r}, not '
                               'Hebrew -- discarding'.format(_used),
                               level='WARNING')
                _sync_path = None
        except Exception as e:
            kodi.log('embedded_sync failed: {0}'.format(e),
                           level='WARNING')
        if _sync_path and os.path.isfile(_sync_path):
            try:
                with open(_sync_path, 'r', encoding='utf-8') as f:
                    _body = f.read()
                _fixed = postfix_rtl.fix_rtl_punctuation(_body)
                _out = os.path.join(
                    kodi.cache_dir(),
                    'embedded_sync_he.povil-rtl.srt')
                _tmp = _out + '.aitmp'
                with open(_tmp, 'w', encoding='utf-8', newline='') as f:
                    f.write(_fixed)
                os.replace(_tmp, _out)
                # The resolver only prepares a file.  Commit CONFIRMED after
                # the picker caller proves Kodi registered and selected it.
                core_selection.stage_subtitle_delivery(
                    _out, selection=S.timing_selection,
                    status='confirmed', source='embedded')
                status('כתובית עברית מסונכרנת לתזמון המובנה מוכנה',
                        time_ms=3500)
                return _out
            except Exception as e:
                kodi.log('embedded_sync write failed: {0}'.format(e),
                               level='WARNING')
        # Nothing aligned confidently. Fall back to the embedded track itself --
        # exactly what the user gets today -- rather than leaving them with no
        # subtitle at all because an optional improvement did not land.
        try:
            from resources.lib.fetching.embedded import streams as _seb
            if (selection_current(S)
                    and _seb.select_embedded(
                        S.payload.get('stream_index'), 'he')
                    and selection_current(S)):
                publish_timing_status(S, 'confirmed', 'embedded')
                status('לא נמצאה כתובית עברית שמסתנכרנת — הופעל התרגום המובנה',
                        time_ms=5000)
        except Exception as e:
            kodi.log('embedded_sync fallback select failed: {0}'
                           .format(e), level='WARNING')
        return None
    return NEXT
