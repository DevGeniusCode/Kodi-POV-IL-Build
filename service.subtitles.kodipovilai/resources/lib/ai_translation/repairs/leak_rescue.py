# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Repairs: rescue of cues that carry the model's working (one Google request budget).

import time
from resources.lib.core import kodi
from resources.lib.ai_translation.repairs import postfix_cleanup, postfix_leaks
from resources.lib.core.srt import blocks as srt_blocks


# Field report (Oct 2026, update 756): translations cached or pooled before the
# leak check knew every shape still carry the model's working in some cues
# ("... Let's use: ...", drafts side by side). Each read repairs them; this is
# what one read may spend on Google (only for a cue with no Hebrew answer of
# its own) before the rest keep their source line.
_LEAK_RESCUE_BUDGET = 6.0
_LEAK_RESCUE_TIMEOUT = 4


def _rescue_reasoning_leaks(text, source=None, source_lang='auto'):
    """(text, n): `text` with each cue that carries the model's working
    replaced by the model's own Hebrew answer in it -- the line its working
    settled on, else the draft it opened with (it knew the speaker's gender;
    Google does not: measured in the QA Kodi, Google wrote "אתה מוכן" for a
    mother asking her daughter) -- when it is a whole answer for the cue's
    source line (srt.leaked_cue_answer). A cue with none gets Google's Hebrew
    for its source line, and without that the source line itself; a cue with
    neither an answer nor a source line is left for a later read. The cue
    keeps its position tag and italics. Every other cue is left as it is;
    n = cues replaced. `source` is the prepared source text, or a callable
    returning it: it is read only for a file that does carry the working.
    Never raises."""
    try:
        if not postfix_leaks.may_carry_reasoning(text):
            return text, 0
        blocks = srt_blocks.parse_blocks(text or '')
        if not postfix_leaks.generated_editorial_leak_indices([], blocks):
            return text, 0
        if callable(source):
            source = source()
        src_blocks = srt_blocks.parse_blocks(source) if source else []
        sources = postfix_leaks.aligned_source_texts(src_blocks, blocks)
        # A cue with no source line to tell dialogue from working (no source
        # file, or its start time is not in it) counts only on a strong sign:
        # one English phrase alone may be a kept title or a quote.
        strong = set(postfix_leaks.generated_editorial_leak_indices([], blocks, strong_only=True))
        bad = [i for i in postfix_leaks.generated_editorial_leak_indices(src_blocks, blocks)
               if sources[i] or i in strong]
        if not bad:
            return text, 0
        deadline = time.monotonic() + _LEAK_RESCUE_BUDGET
        how = {'model': 0, 'google': 0, 'source': 0}
        for i in bad:
            lines = blocks[i].split('\n')
            if len(lines) < 3 or not srt_blocks._TIMECODE_RE.match(lines[1].strip()):
                continue
            body = srt_blocks.block_text_only(blocks[i]) or ''
            line = postfix_cleanup.strip_leaked_speaker_prefix(sources[i] or '').strip()
            answer = postfix_leaks.leaked_cue_answer(body, line)
            kind = 'model'
            if not answer and line:
                heb = _google_lines(line.split('\n'), source_lang, deadline)
                if heb and srt_blocks._HEB_RE.search(''.join(heb)):
                    answer, kind = '\n'.join(heb), 'google'
            if not answer and line:
                answer, kind = line, 'source'
            if answer:
                blocks[i] = '\n'.join(lines[:2] + [postfix_leaks.restyle_like(body, answer)])
                how[kind] += 1
        kodi.log('{0} cue(s) carried the model\'s working -- replaced: '
                       '{1} by its own answer, {2} by Google, {3} by the '
                       'source line'.format(len(bad), how['model'],
                                            how['google'], how['source']),
                       level='WARNING')
        n = sum(how.values())
        return (srt_blocks.stitch_blocks(blocks), n) if n else (text, 0)
    except Exception as e:
        kodi.log('reasoning-leak repair skipped ({0})'.format(e),
                       level='WARNING')
        return text, 0


def _google_lines(lines, source_lang, deadline):
    """Google's Hebrew for `lines`, one request a line, none started after
    `deadline` and none waiting past it; None when any line has no answer."""
    from resources.lib.ai_translation.providers import google_translate
    out = []
    for line in lines:
        left = deadline - time.monotonic()
        if left < 0.5:
            return None
        try:
            heb = google_translate.translate_lines(
                [line], source_lang or 'auto',
                timeout=min(_LEAK_RESCUE_TIMEOUT, left))
        except Exception:
            heb = None
        if not heb:
            return None
        out.extend(heb)
    return out
