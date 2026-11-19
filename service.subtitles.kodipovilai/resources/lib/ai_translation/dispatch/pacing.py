# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Dispatch: Gemini request pacing, the block clock and the per-chunk reply checks.

import threading
import time
from resources.lib.ai_translation.providers import gemini
from resources.lib.core.srt import blocks as srt_blocks, timing


# --- Gemini request pacing (shared across all chunk threads + concurrent jobs) --
# Free-tier Gemini Flash Lite is ~15 requests/minute (RPM). Dispatching chunks in
# parallel with NO pacing bursts well above that -> constant per-minute 429s ->
# noisy retries that waste requests and burn the daily quota. One global "minimum
# interval between request STARTS" caps the rate just under the limit so we almost
# never hit 429. Module scope so it holds across the ThreadPoolExecutor workers AND
# across two titles translating at once (they share one API key's RPM budget).
GEMINI_RATE_LOCK = threading.Lock()
GEMINI_NEXT_SLOT = [0.0]


def gemini_rate_gate(min_interval):
    """Block until this thread's reserved slot, spacing all Gemini request STARTS
    >= min_interval seconds apart process-wide. No-op when min_interval <= 0."""
    if min_interval <= 0:
        return
    with GEMINI_RATE_LOCK:
        now = time.monotonic()
        slot = GEMINI_NEXT_SLOT[0]
        if slot < now:
            slot = now
        GEMINI_NEXT_SLOT[0] = slot + min_interval
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def gemini_free_rpm_cap(model):
    """Requests-per-minute ceiling for the FREE Gemini tier, by model family.

    Flash-Lite's free RPM is ~15, so we pace at 14. Regular Flash's free RPM
    is only ~5 (and just ~20 requests per DAY), so we pace at 4. Pacing regular
    Flash at Flash-Lite's ceiling is exactly what 429-storms it, so the cap has
    to follow the selected model rather than assume Flash-Lite. Anything we
    don't recognise falls back to the most conservative cap."""
    m = (model or '').lower()
    if 'flash-lite' in m or 'flash_lite' in m or 'flashlite' in m:
        return 14
    if 'flash' in m:
        return 4
    return 4


class IntegrityRejected(gemini.FilteredResponse):
    """A reply the output checks refused (a self-edit, an echoed source cue,
    a lost negation, no Hebrew). The ladder retries it as it does a content
    block, but it is no block: it does not start a chunk's block budget
    (_BlockClock)."""


class BlockClock(object):
    """A chunk's budget in the AI translation (resolve's _translate_one).

    It is there for one pathology: a chunk fighting content blocks
    (alternate references, bisecting to the blocked line) can storm for
    minutes and hold up the whole job. So `budget` runs from the chunk's
    FIRST block, not from its first request. Field, 2 Nov 2026 (Frasier
    S11E13, 0.3.68): chunks with no block at all passed 120 s on a slow
    evening Gemini; 18 lines went out without the reference and 7 the reply
    skipped stayed in English. A whole-subtitle request
    (whole_subtitle_request) passed it on almost every run. A chunk that
    never blocks is still bounded: by `slow_cap` seconds, and by
    `max_calls` requests (`calls`, counted by the translator) -- a reply that
    keeps dropping entries bisects down to single lines, about two requests
    a line. A block fight has its time budget alone, as before: on a fast
    paid key the request count would cut it short. Past the budget the
    remainder goes English-only; `grace` (on the time) and half as many
    requests again bound that too."""
    __slots__ = ('budget', 'until', 'cap', 'calls', 'max_calls')

    def __init__(self, budget, slow_cap, max_calls):
        self.budget = float(budget)
        self.until = None
        self.cap = time.monotonic() + float(slow_cap)
        self.calls = 0
        self.max_calls = int(max_calls)

    def block(self, cause=None):
        """A content block: the budget starts now (once per chunk). Not for
        an output-check rejection (`cause`, _IntegrityRejected)."""
        if self.until is None and not isinstance(cause, IntegrityRejected):
            self.until = time.monotonic() + self.budget

    def waited(self, seconds):
        """`seconds` went on waiting for a busy model to take the request
        again: not the chunk being slow, and not it fighting a block."""
        if seconds > 0:
            self.cap += seconds
            if self.until is not None:
                self.until += seconds

    def spent(self, grace=0.0):
        now = time.monotonic() - grace
        if now > self.cap:
            return True
        if self.until is not None:
            return now > self.until
        return self.calls > self.max_calls + (self.max_calls // 2 if grace else 0)


def is_mostly_hebrew(text, min_ratio=0.30):
    """True if a meaningful share of TEXT's letters are Hebrew.

    Two uses: (1) validating a TRANSLATION -- catches the two ways a weak model
    (e.g. a Flash-Lite) silently fails: it returns EMPTY, or it ECHOES the
    source untranslated (German/Spanish/English/Russian/...); both used to be
    cached and served as 'the Hebrew translation'. (2) a SOURCE-language sanity
    check -- 'is this source already Hebrew, so translating it is pointless?'.

    The denominator counts Hebrew letters against ALL OTHER letters of ANY
    script (Latin, Cyrillic, Arabic, CJK, ...), not just ASCII. If it counted
    only ASCII, a non-Latin body (e.g. a Russian subtitle) would be invisible to
    the ratio and a single stray Hebrew credit line ('translated by...') would
    read as '100% Hebrew' -- the exact false-'already Hebrew' misfire this guard
    exists to avoid. Numbers/names keep some non-Hebrew letters, so we only
    require a fraction, not all."""
    if not text or not text.strip():
        return False
    he = 0
    other = 0
    for ch in text:
        o = ord(ch)
        if 0x0590 <= o <= 0x05FF:
            he += 1
        elif ch.isalpha():
            other += 1
    letters = he + other
    if letters < 20:
        return False  # almost no text -> treat as failed
    return (he / letters) >= min_ratio


def answered(source_blocks, response):
    """How many requested entries a reply answers: blocks whose start time is
    one the chunk asked for (srt.align_blocks), not paragraphs of any kind."""
    return len(timing.align_blocks(source_blocks, srt_blocks.parse_blocks(response)))


def is_untranslated_chunk_response(source_blocks, response_blocks):
    """Catch a substantial Gemini reply with no Hebrew dialogue at all.

    A whole source-language chunk can have valid SRT indices/times and still be
    wholly untranslated. It can also precede a second, Hebrew copy in the same
    reply; the first copy would win when the output is aligned to source cues.
    The final-document Hebrew gate can miss one such chunk among many translated
    ones. Require at least three cues and enough alphabetic dialogue to avoid
    retrying tiny proper-name/title-card tails. One Hebrew dialogue character
    in the first source-length run abstains: partial failures remain with the
    existing per-cue/final-file checks rather than a broad heuristic.
    """
    if len(source_blocks) < 3 or len(response_blocks) < 3:
        return False
    letters = 0
    for block in response_blocks[:len(source_blocks)]:
        for ch in srt_blocks.block_text_only(block):
            if '\u0590' <= ch <= '\u05ff':
                return False
            if ch.isalpha():
                letters += 1
    return letters >= 60


def thinking_params(raw, for_model):
    """(thinking level, thinking budget) for `for_model` from the stored
    'thinking_budget' setting."""
    level, budget = None, None
    if raw in ('minimal', 'low', 'medium', 'high'):
        level = raw
    else:
        try:
            budget = int(raw)
        except (TypeError, ValueError):
            budget = 0
        if budget <= 0:
            budget = None
    if budget and for_model.lower().startswith('gemini-3.'):
        if budget <= 512:
            level = 'minimal'
        elif budget <= 768:
            level = 'low'
        elif budget <= 1024:
            level = 'medium'
        else:
            level = 'high'
        budget = None
    return level, budget


def source_context_for_subchunk(chunks, idx, ch, prev_context_lines):
    """Immediate source-cue tail before a worker or recursively split child.

    The parallel workers still depend only on source text. A split child must
    use its own starting position, rather than inherit the parent's original
    cross-chunk window. Count cues before filtering blank dialogue so a blank
    cue cannot silently widen the window into an older scene.
    """
    if prev_context_lines <= 0 or not ch or not (1 <= idx <= len(chunks)):
        return []
    parent = chunks[idx - 1]
    try:
        start = parent.index(ch[0])
    except ValueError:
        return []
    # A top-up can send scattered missing cues in one request. In that case
    # the first cue's neighbors are not valid context for the later cues.
    if parent[start:start + len(ch)] != ch:
        return []
    prior = (chunks[idx - 2][-prev_context_lines:] if idx > 1 else [])
    prior = (prior + parent[:start])[-prev_context_lines:]
    return [text for block in prior
            for text in [srt_blocks.block_text_only(block)] if text]


def chunk_dispatch_order(chunks, playback_ms):
    """Put the visible part of a seeked video first without changing cue order.

    Worker indices and the final SRT remain in source order.  Only the order in
    which independent requests enter the executor changes.  An unparseable or
    out-of-range playback position keeps the ordinary start-to-end schedule.
    """
    ordinary = list(range(1, len(chunks) + 1))
    if len(ordinary) < 2 or playback_ms is None:
        return ordinary
    try:
        position = int(playback_ms)
        starts = [srt_blocks.block_start(chunk[0]) for chunk in chunks if chunk]
        ends = [srt_blocks.block_times(chunk[-1]) for chunk in chunks if chunk]
        if (len(starts) != len(chunks) or len(ends) != len(chunks)
                or any(value is None for value in starts + ends)
                or position < starts[0] or position > ends[-1][1]):
            return ordinary
        current = next((i for i, end in enumerate(ends)
                        if position <= end[1]), None)
        if current is None or current == 0:
            return ordinary
        nearby = [current + 1, current + 2, current]
        return ([index for index in nearby if index in ordinary]
                + [index for index in ordinary if index not in nearby])
    except (TypeError, ValueError, IndexError):
        return ordinary
