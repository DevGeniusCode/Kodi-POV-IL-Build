# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The Gemini side of the job: one request with its retry ladders (rate limit, overload, block, truncation),
# the per-chunk bisection / top-up / English-only fallbacks, the model fallback.

import time
from resources.lib.ai_translation.providers import gemini
from resources.lib.ai_translation.repairs import postfix_leaks
from resources.lib.ai_translation.dispatch import srt_wire
from resources.lib.core import kodi, settings
from resources.lib.ai_translation.context import prompt
from resources.lib.core.srt import blocks as srt_blocks, timing
from resources.lib.ai_translation.dispatch.pacing import answered, BlockClock, gemini_free_rpm_cap, GEMINI_NEXT_SLOT, gemini_rate_gate, GEMINI_RATE_LOCK, IntegrityRejected, is_untranslated_chunk_response, source_context_for_subchunk
from resources.lib.ai_translation.providers.google_flow import google_fill_missing, google_rescue
from resources.lib.ai_translation.providers.model_fallback import CHOSEN_MODEL_TIMEOUT, model_fallback_notice
from resources.lib.ai_translation.job.ai.plan import count
from resources.lib.ai_translation.job.ai.reference import ref_ensure
from resources.lib.ai_translation.job.ai.request import thinking_for
from resources.lib.ai_translation.job.ai.tuning import AR_EXPLICIT_MARKERS, CHUNK_BLOCK_BUDGET, CHUNK_LINES_PER_CALL, CHUNK_MAX_CALLS, CHUNK_SLOW_CAP, CHUNK_SLOW_PER_LINE, EO_GRACE, FILTERED_BACKOFF, GENERIC_BACKOFF, MAX_ALT_LEVELS, NO_REF, NUMBERED_REQUESTS, OVERLOAD_BACKOFF, RATELIMIT_BACKOFF, REPAIR_BACKOFF, TOPUP_ROUNDS
from resources.lib.ai_translation.job.state import translation_cancelled


def accepted_before(ch, blocks, missing, limit=5):
    """[(number, source text, accepted Hebrew)] of up to `limit` entries
        just before the first missing one (NG's resume context pair): a
        re-asked line keeps the genders the same scene already used."""
    try:
        first = srt_blocks.block_start(missing[0])
        src_by = {}
        order = []
        for b in ch:
            parts = b.strip().split('\n')
            start = srt_blocks.block_start(b)
            if len(parts) >= 3 and start is not None:
                src_by[start] = (parts[0].strip(), '\n'.join(parts[2:]))
                order.append(start)
        if first is None or first not in order:
            return []
        before = set(order[:order.index(first)])
        pairs = []
        for b in blocks:
            parts = b.strip().split('\n')
            start = srt_blocks.block_start(b)
            if len(parts) >= 3 and start in before:
                num, src = src_by[start]
                pairs.append((num, src, '\n'.join(parts[2:])))
        return pairs[-limit:]
    except Exception:
        return []


def go_on_with_fallback(S, asked, why):
    """A request to `asked` failed for `why`. True when it should go out
    again at once to the model the job now uses (_ModelFallback): the
    names every later request reads are moved to that model here."""
    if not S.models.give_up(asked, why):
        return False
    with S.switch_lock:
        if S.model == S.models.model:
            return True          # another chunk has moved the names
        S.model = S.models.model
        S.thinking_level, S.thinking_budget = thinking_for(S, S.model)
        if not S.paid_mode:
            # the pace follows the model: Flash-Lite's free allowance is
            # three times a Flash model's
            _cap = gemini_free_rpm_cap(S.model)
            S.rpm_interval = 60.0 / max(1, min(
                settings.get_int('gemini_rpm', _cap), _cap))
        # Slots already handed out were spaced for the old model (15 s
        # apart on a Flash model): left there, the chunk that caused the
        # switch -- the first, where the viewer is -- waited 36 s for its
        # turn at three chunks in flight (review, fake clock). The new
        # model's allowance is its own; its first slot is now.
        with GEMINI_RATE_LOCK:
            GEMINI_NEXT_SLOT[0] = min(GEMINI_NEXT_SLOT[0],
                                       time.monotonic())
    kodi.log('model {0} did not answer ({1}): the translation '
                   'goes on with {2}'.format(asked, why, S.model),
                   level='WARNING')
    model_fallback_notice(asked, why)
    return True


# Per-chunk translator. Holds the inner retry loop. Returns the
# raw Gemini response text, or raises a Stop-style exception
# that the orchestrator below catches and converts into a
# cancellation across all parallel chunks.
class AbortTranslation(Exception):
    def __init__(self, reason, user_msg, detail=''):
        self.reason = reason
        self.user_msg = user_msg
        self.detail = detail


def gem(S, clock, *args, **kwargs):
    """_call_gemini, counted on the chunk's clock (_BlockClock.calls)."""
    if clock is not None:
        clock.calls += 1
    S.busy_waits.seconds = 0.0
    try:
        return call_gemini(S, *args, **kwargs)
    finally:
        # A chunk answered on its last busy retry had used up its whole
        # slow cap by then, and its top-ups went English-only or to
        # Google although nothing about it was slow (review, 0.3.79).
        if clock is not None:
            clock.waited(getattr(S.busy_waits, 'seconds', 0.0))


def english_only_safe(S, idx, ch, deadline=None):
    """Translate `ch` with NO gender reference (English only), splitting on
    truncation / residual blocks so a large remainder still completes, and
    keeping source only for an individual entry that stays blocked or
    truncates at size 1. Applies the SAME low-yield bisection as the main
    path so silently-dropped entries are re-done, not shipped. This is the
    circuit-breaker's finalization guarantee -- it never propagates a
    content/format error; a genuine quota/overload abort (_AbortTranslation)
    still propagates, as it must. `deadline` + _EO_GRACE caps its OWN spend
    so the guarantee cannot itself run without a clock."""
    if deadline is not None and deadline.spent(EO_GRACE):
        _resc = google_fill_missing(ch, S.source_lang)
        kodi.log(
            'Chunk {0}: English-only grace budget spent too -- no further '
            'Gemini calls; {1} of {2} entr(ies) with Google Translate, '
            'the rest kept as source'.format(idx, len(_resc), len(ch)),
            level='WARNING')
        count(S, 'noar', len(_resc))
        count(S, 'src', len(ch) - len(_resc))
        if not _resc:
            return '\n\n'.join(ch)
        return srt_blocks.stitch_blocks(timing.align_blocks(ch, _resc, ch))
    try:
        resp = gem(S, deadline, idx, ch, NO_REF)
    except (gemini.FilteredResponse, gemini.TruncatedResponse):
        if len(ch) > 1:
            mid = len(ch) // 2
            return (english_only_safe(S, idx, ch[:mid], deadline) + '\n\n'
                    + english_only_safe(S, idx, ch[mid:], deadline))
        # One entry, English-only, and still refused. Same reasoning as the
        # main ladder: try a different engine before shipping English.
        _resc = google_rescue(ch, S.source_lang)
        if _resc:
            kodi.log(
                'Chunk {0} English-only blocked at size 1 -- rescued with '
                'Google Translate'.format(idx), level='WARNING')
            count(S, 'noar', len(ch))
            return '\n\n'.join(_resc)
        count(S, 'src', len(ch))
        return '\n\n'.join(ch)
    # Low-yield guard: Gemini silently dropped entries -> bisect and re-do.
    if len(ch) > 1:
        got = len(srt_blocks.parse_blocks(resp))
        if got < max(1, int(len(ch) * 0.85)):
            mid = len(ch) // 2
            return (english_only_safe(S, idx, ch[:mid], deadline) + '\n\n'
                    + english_only_safe(S, idx, ch[mid:], deadline))
        # ...and the same silent-drop hole the counting guard cannot see.
        # This is the circuit-breaker path, reached only once the block
        # budget is already spent, so it does NOT spend another Gemini
        # call: an entry the reply omitted gets Google Translate, and keeps
        # its source text only if that fails too. Visibly untranslated
        # beats silently absent, and it keeps the entry count aligned for
        # everything downstream.
        _gap = timing.missing_blocks(ch, srt_blocks.parse_blocks(resp))
        if _gap:
            _resc = google_fill_missing(_gap, S.source_lang)
            kodi.log(
                'Chunk {0} English-only: {1} entr(ies) missing from the '
                'reply -- {2} with Google Translate, the rest kept as '
                'source'.format(idx, len(_gap), len(_resc)),
                level='WARNING')
            count(S, 'src', len(_gap) - len(_resc))
            count(S, 'noar', len(ch) - len(_gap) + len(_resc))
            return srt_blocks.stitch_blocks(timing.align_blocks(
                ch, srt_blocks.parse_blocks(resp), _resc, _gap))
    count(S, 'noar', len(ch))
    return resp


def top_up_missing(S, idx, ch, response, deadline, level=0):
    """Re-request only the entries the model silently dropped, and splice
    them back in. Returns the completed reply text (or the original when
    nothing is missing / nothing can be recovered). Never raises: a top-up
    is an improvement on the reply we already have, so any failure just
    returns that reply."""
    try:
        # Admit only entries the chunk actually requested. An untimed
        # preface (or an invented timestamp) must not make a count look
        # healthy or survive into progressive/final output.
        blocks = timing.align_blocks(ch, srt_blocks.parse_blocks(response))
        for _round in range(TOPUP_ROUNDS):
            missing = timing.missing_blocks(ch, blocks)
            if not missing:
                break
            if deadline is not None and deadline.spent():
                kodi.log(
                    'Chunk {0}: {1} entr(ies) still missing but the block '
                    'budget is spent -- no more Gemini calls for them'.format(
                        idx, len(missing)), level='WARNING')
                break
            kodi.log(
                'Chunk {0}: {1}/{2} entr(ies) missing from the reply -- '
                'requesting just those (round {3})'.format(
                    idx, len(missing), len(ch), _round + 1), level='INFO')
            try:
                # The reference the chunk passed with: after a fallback
                # language, asking with the blocked primary again wastes a
                # request and then drops the gender hints.
                fill = gem(S, deadline, idx, missing, level,
                                    he_context=accepted_before(ch, blocks, missing))
            except gemini.FilteredResponse as _fe:
                # These specific lines are what the filter objects to. Drop
                # the gender reference for them and try once more; the
                # single-entry path below has the full ladder for the rest.
                if deadline is not None:
                    deadline.block(_fe)
                try:
                    fill = gem(S, deadline, idx, missing, NO_REF)
                except AbortTranslation:
                    raise
                except (gemini.FilteredResponse, gemini.TruncatedResponse):
                    break
            except gemini.TruncatedResponse as e:
                fill = e.partial_text or ''
            except AbortTranslation:
                # Quota exhausted / invalid key / retries spent. The
                # executor loop catches this to cancel every OTHER chunk and
                # tell the user why. Absorbing it here would return a
                # normal-looking chunk, leave the job "successful", and let
                # the remaining chunks keep hammering a key that is already
                # known to be dead.
                raise
            except Exception:
                break
            fill_blocks = srt_blocks.parse_blocks(fill)
            if not fill_blocks:
                break
            before = len(blocks)
            # Take from the fill ONLY what answers an entry we asked for --
            # a corrupted timestamp in the reply must not become a new cue.
            blocks = timing.align_blocks(ch, blocks, fill_blocks)
            if len(blocks) <= before:
                break          # the reply added nothing -> stop, don't loop
        tallied = 0
        still = timing.missing_blocks(ch, blocks)
        if still and len(still) < len(ch) and (
                deadline is None or not deadline.spent()):
            # The top-up requests the missing entries TOGETHER -- which
            # CONCENTRATES exactly the content the filter objected to, so
            # the refill is MORE likely to block than the chunk that
            # dropped the lines in the first place (field report: a run
            # of profane lines came back as a whole English segment).
            # Route the leftovers through the main ladder instead: it
            # bisects to isolate the poison line, gives the isolated
            # entry the alternate references / English-only / Google
            # rungs, and keeps source per-line only as the true last
            # resort. `still` is strictly smaller than `ch` (the reply
            # already yielded >=85%), so the recursion shrinks, and the
            # shared block budget bounds the spend.
            kodi.log(
                'Chunk {0}: {1} entr(ies) still missing after top-up -- '
                'isolating them through the bisection ladder'.format(
                    idx, len(still)), level='WARNING')
            try:
                rescued = translate_one(S, idx, still, deadline, False)
                blocks = timing.align_blocks(
                    ch, blocks, srt_blocks.parse_blocks(rescued))
                # every leftover was tallied inside the ladder (ar/alt/
                # noar/src) -- the caller must not count them again
                tallied = len(still)
                still = timing.missing_blocks(ch, blocks)
            except AbortTranslation:
                raise
            except Exception as e:
                kodi.log(
                    'Chunk {0}: bisection rescue failed ({1}) -- keeping '
                    'source for the leftovers'.format(idx, e),
                    level='WARNING')
        if still:
            # Google Translate for whatever never came back (machine
            # Hebrew, no gender), and the SOURCE for what even that missed,
            # so the entry count matches the chunk and every later stage
            # (positional timing restore, the merge into the final file)
            # stays aligned.
            _resc = google_fill_missing(still, S.source_lang)
            blocks = timing.align_blocks(ch, blocks, _resc, still)
            kodi.log(
                'Chunk {0}: {1} entr(ies) missing after top-up -- {2} with '
                'Google Translate, the rest kept as source'
                .format(idx, len(still), len(_resc)), level='WARNING')
            if tallied == 0:
                # counted here only when the rescue never ran/tallied them
                count(S, 'noar', len(_resc))
                count(S, 'src', len(still) - len(_resc))
                tallied = len(still)
        return srt_blocks.stitch_blocks(blocks), tallied
    except AbortTranslation:
        raise
    except Exception as e:
        kodi.log('Chunk {0} top-up failed: {1}'.format(idx, e),
                       level='WARNING')
        return response, 0


def translate_one(S, idx, ch, deadline=None, try_alts=True):
    # Recursive bisection on TruncatedResponse OR low-yield response (Gemini
    # sometimes silently skips entries -- observed as a 5-minute gap in the
    # middle of a translated movie). Bisecting forces the model to spend more
    # attention per entry. A FilteredResponse (prompt-blocked, usually
    # PROHIBITED_CONTENT) is handled QUALITY-FIRST: retry the whole chunk
    # with the NEXT human-subtitle language (gender preserved); if that still
    # blocks, bisect to ISOLATE the offending line(s) so every OTHER line
    # keeps its reference; at a single blocked entry, try each alternate
    # language, then English-only (translated, gender dropped), and only as
    # an ABSOLUTE LAST RESORT keep the source for that ONE line.
    if deadline is None:
        deadline = BlockClock(
            CHUNK_BLOCK_BUDGET,
            max(CHUNK_SLOW_CAP, CHUNK_SLOW_PER_LINE * len(ch)),
            CHUNK_MAX_CALLS + len(ch) // CHUNK_LINES_PER_CALL)
    elif deadline.spent():
        # Circuit-breaker (rare, pathologically explicit chunk): translate the
        # remainder ENGLISH-ONLY so the viewer still gets Hebrew and the JOB
        # finalizes (cache + pool). _english_only_safe splits on truncation /
        # residual blocks, so a LARGE remainder is still translated piece by
        # piece -- source is kept only for an individual line that stays
        # blocked, never a whole sub-chunk dumped at once.
        kodi.log(
            'Chunk {0} over its budget ({1}; {2} requests) -- translating '
            'the remainder English-only'.format(
                idx, 'blocked' if deadline.until is not None
                else 'no block, the slow cap', deadline.calls),
            level='WARNING')
        return english_only_safe(S, idx, ch, deadline)
    if len(ch) > 1:
        try:
            response = gem(S, deadline, idx, ch, 0)      # primary reference
        except gemini.TruncatedResponse:
            mid = len(ch) // 2
            kodi.log(
                'Chunk {0} truncated -- bisecting into {1} + {2}'
                .format(idx, mid, len(ch) - mid), level='WARNING')
            return (translate_one(S, idx, ch[:mid], deadline, try_alts) + '\n\n'
                    + translate_one(S, idx, ch[mid:], deadline, try_alts))
        except gemini.StoppedEarly:
            # RECITATION (song lyrics), SPII...: another reference language
            # cannot change what the model will not write, and each one is a
            # download and two requests. Isolate the line directly.
            count(S, 'blocks')
            deadline.block()
            mid = len(ch) // 2
            kodi.log(
                'Chunk {0} stopped early -- bisecting into {1} + {2}'
                .format(idx, mid, len(ch) - mid), level='WARNING')
            return (translate_one(S, idx, ch[:mid], deadline, False) + '\n\n'
                    + translate_one(S, idx, ch[mid:], deadline, False))
        except gemini.FilteredResponse as _fe:
            count(S, 'blocks')
            deadline.block(_fe)
            # 1) Try the WHOLE chunk with the next human-subtitle language(s)
            #    -- gender-preserving. Handles the aggregate-volume block
            #    (Arabic DOUBLED the explicit content; a lighter language may
            #    pass) in one fast call. Only at the top level (try_alts),
            #    never repeated at every bisect node.
            if try_alts:
                for _lvl in range(1, MAX_ALT_LEVELS + 1):
                    if deadline.spent():
                        break     # out of block-budget -> stop, isolate/degrade
                    if not ref_ensure(S, _lvl):
                        break     # no more aligned languages available
                    try:
                        _resp = gem(S, deadline, idx, ch, _lvl)
                    except gemini.FilteredResponse:
                        continue   # this language blocked too -> try next
                    except gemini.TruncatedResponse:
                        break      # too long -> stop trying alts, isolate
                    # Accept only if the yield is adequate (Gemini can silently
                    # drop entries); otherwise stop and isolate by bisection.
                    if answered(ch, _resp) < max(1, int(len(ch) * 0.85)):
                        break
                    kodi.log(
                        'Chunk {0} passed with fallback reference [{1}]'
                        .format(idx, S.ref_stack[_lvl][0]), level='INFO')
                    # The same silent drops the primary path tops up;
                    # measured: one fallback reply left 20 of its 50
                    # entries in English.
                    _resp, _kept_src = top_up_missing(S,
                        idx, ch, _resp, deadline, level=_lvl)
                    count(S, 'alt', len(ch) - _kept_src)
                    return _resp
            # 2) Still blocked -> bisect to ISOLATE the offending line(s),
            #    keeping the primary reference on every OTHER line. Children
            #    do NOT re-try whole-chunk alts (try_alts=False); the isolated
            #    single entry (below) tries alts + English-only + source.
            mid = len(ch) // 2
            kodi.log(
                'Chunk {0} blocked -- bisecting into {1} + {2} to isolate '
                'the offending line(s)'.format(idx, mid, len(ch) - mid),
                level='WARNING')
            return (translate_one(S, idx, ch[:mid], deadline, False) + '\n\n'
                    + translate_one(S, idx, ch[mid:], deadline, False))

        # Yield check: did we get back roughly as many entries
        # as we asked for? Gemini sometimes drops entries
        # mid-chunk, leaving silent gaps in the final SRT.
        # Threshold 85% -- below that, bisect and re-do. Count only
        # entries that answer a requested start time: a reply that lost
        # every timecode still has 50 paragraphs, passed a plain count,
        # and then the top-up found every entry missing and kept the
        # whole chunk in the source language.
        got = answered(ch, response)
        expected = len(ch)
        if got < max(1, int(expected * 0.85)):
            mid = expected // 2
            kodi.log(
                'Chunk {0} low yield ({1}/{2} entries) -- '
                'bisecting into {3} + {4}'.format(
                    idx, got, expected, mid, expected - mid),
                level='WARNING')
            return (translate_one(S, idx, ch[:mid], deadline, try_alts) + '\n\n'
                    + translate_one(S, idx, ch[mid:], deadline, try_alts))

        # The yield check above only COUNTS, and 85% of a 50-line chunk means
        # up to 7 lines may be dropped per chunk while it reports success --
        # ~16% of a feature film, silently left in the source language. That
        # is the "about a hundred lines were not translated" field report.
        # So ask WHICH entries came back, and top up exactly the ones that
        # did not. A top-up is ONE call; bisecting a 50-line chunk to corner
        # a single dropped line costs ~6, on a shared quota.
        response, _kept_src = top_up_missing(S, idx, ch, response, deadline)
        # Entries the top-up had to keep as source were already tallied
        # there; counting them again here would put the same entry in two
        # telemetry buckets and make the per-chunk totals exceed the chunk.
        count(S, 'ar', len(ch) - _kept_src)
        return response
    # ---- single-entry chunk ----
    try:
        _resp = gem(S, deadline, idx, ch, 0)
        count(S, 'ar', len(ch))
        return _resp
    except gemini.TruncatedResponse as e:
        # shouldn't happen (one SRT entry is < 100 tokens), but if it does
        # we surface the partial text so the user sees something.
        kodi.log(
            'Chunk {0} truncated even at size 1 -- '
            'returning partial'.format(idx),
            level='ERROR')
        count(S, 'src', len(ch))
        return e.partial_text or ''
    except gemini.FilteredResponse as _blocked:
        # This ONE entry blocked with the primary reference. Preserve gender
        # if at all possible: try each ALTERNATE human-subtitle language
        # first (e.g. Spanish after Arabic)... -- not for an early stop
        # (RECITATION...), which no reference language changes.
        count(S, 'blocks')
        deadline.block(_blocked)
        for _lvl in range(1, (0 if isinstance(_blocked, gemini.StoppedEarly)
                              else MAX_ALT_LEVELS) + 1):
            # Cap the uninterruptible window: once past the block-budget, stop
            # trying more languages for THIS line and degrade -- so one
            # pathological chunk can't hog the shared RPM gate for other jobs.
            if deadline.spent():
                break
            if not ref_ensure(S, _lvl):
                break
            try:
                _resp = gem(S, deadline, idx, ch, _lvl)
                count(S, 'alt', len(ch))
                return _resp
            except (gemini.FilteredResponse, gemini.TruncatedResponse):
                continue
        # ...then English-only (translated, gender dropped for this line)...
        if S.ref_stack:
            try:
                _resp = gem(S, deadline, idx, ch, NO_REF)
                count(S, 'noar', len(ch))
                return _resp
            except (gemini.FilteredResponse, gemini.TruncatedResponse):
                pass
        # ...then off Gemini entirely. Five references, no reference and a
        # bisect have all been refused by the same safety policy, so the
        # next Gemini call would be refused too. A different engine is the
        # only thing left that can still produce Hebrew here.
        _resc = google_rescue(ch, S.source_lang)
        if _resc:
            kodi.log(
                'Chunk {0} blocked by every Gemini stage -- rescued with '
                'Google Translate ({1} entr(ies)); machine Hebrew, no '
                'gender'.format(idx, len(ch)), level='WARNING')
            count(S, 'noar', len(ch))
            return '\n\n'.join(_resc)
        # ...and only as an ABSOLUTE LAST RESORT keep the source for this ONE
        # line, so the rest of the subtitle still translates. The source
        # block is returned WHOLE and untouched -- an English line on screen
        # is the worst outcome this function has, and losing the line
        # entirely would be worse still.
        kodi.log(
            'Chunk {0} blocked even English-only at size 1, and Google '
            'Translate could not rescue it -- keeping source text (last '
            'resort)'.format(idx), level='WARNING')
        count(S, 'src', len(ch))
        return '\n\n'.join(ch)


def cancel_aware_wait(S, seconds):
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        if translation_cancelled(S) or S.job_gave_up.is_set():
            raise AbortTranslation('cancelled', '')
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def repair_generate(S, cancelled=None, **kwargs):
    # `cancelled` is the caller's own stop (a residual repair also stops
    # when live translation moves to another subtitle); a cancelled job
    # must not send the request it was waiting to retry.
    def _stop():
        if translation_cancelled(S):
            raise AbortTranslation('cancelled', '')
        if cancelled is not None and cancelled():
            raise RuntimeError('repair cancelled')

    for attempt in range(len(REPAIR_BACKOFF) + 1):
        _stop()
        try:
            return gemini.generate(**kwargs)
        except (gemini.OverloadError, gemini.RateLimited) as e:
            if attempt >= len(REPAIR_BACKOFF):
                raise
            wait = REPAIR_BACKOFF[attempt]
            if isinstance(e, gemini.RateLimited):
                # A per-minute limit frees up when the API says, not sooner.
                wait = max(wait, min(getattr(e, 'retry_after', 0) or 0, 65))
            kodi.log('repair request: {0} -- retry {1}/{2} in {3}s'.format(
                e, attempt + 1, len(REPAIR_BACKOFF), wait), level='WARNING')
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                _stop()
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
            gemini_rate_gate(S.rpm_interval)


def call_gemini(S, idx, ch, ref_level=0, he_context=None):
    if translation_cancelled(S):
        raise AbortTranslation('cancelled', '')
    # Numbered entries without timecodes (srt_wire): the reply's timecodes
    # were never used, only paid for. They are restored from `ch` below.
    body = '\n\n'.join(srt_wire.to_wire(ch) if NUMBERED_REQUESTS else ch)
    _nums = [b.lstrip().split('\n', 1)[0].strip() for b in ch]
    _nums = [n for n in _nums if n.isdigit()]
    prev_ctx_block = prompt.build_prev_context_block(
        source_context_for_subchunk(
            S.chunks, idx, ch, S.prev_context_lines)
        if not S.whole_subtitle_request else [])
    if he_context:
        prev_ctx_block += prompt.build_hebrew_context_block(he_context)
    # Gender reference for THIS chunk's entries (opt-in), keyed by the block's
    # own SRT number so it stays aligned regardless of chunking. `ref_level`
    # selects WHICH human-subtitle language: 0 = primary, 1.. = fallback
    # languages (tried when a chunk is prompt-blocked, since the primary --
    # often Arabic -- dialogue text is a common PROHIBITED_CONTENT trigger),
    # NO_REF (-1) = none (English only, when even the fallbacks were blocked).
    ar_block = ''
    if 0 <= ref_level < len(S.ref_stack):
        ref_lang, ref_map = S.ref_stack[ref_level]
        ent = []
        for block in ch:
            first = block.lstrip().split('\n', 1)[0].strip()
            if first.isdigit():
                num = int(first)
                ref_line = ref_map.get(num)
                # Skip per-entry ARABIC that carries a severe policy-trigger
                # term -- it's the redundant repetition of explicit content
                # that pushes the prompt over Google's block threshold. The
                # English still translates these entries; their gender comes
                # from context (see _AR_EXPLICIT_MARKERS). Only the Arabic
                # oracle has this marker list; other languages pass through.
                if ref_line and not (
                        ref_lang == 'ar'
                        and any(mk in ref_line
                                for mk in AR_EXPLICIT_MARKERS)):
                    ent.append((num, ref_line))
        if ent:
            try:
                ar_block = prompt.build_gender_block(ent, ref_lang)
            except Exception:
                ar_block = ''
    full_prompt = (S.prompt_template
                   .replace('{prev_context_block}',
                            prev_ctx_block + ar_block)
                   .replace('{entry_count}', str(len(ch)))
                   .replace('{first_num}', _nums[0] if _nums else '?')
                   .replace('{last_num}', _nums[-1] if _nums else '?')
                   .replace('{chunk}', body))
    overload_attempts = 0
    generic_attempts = 0
    filtered_attempts = 0
    ratelimit_attempts = 0
    while True:
        if translation_cancelled(S):
            raise AbortTranslation('cancelled', '')
        gemini_rate_gate(S.rpm_interval)   # pace to stay under the RPM cap
        if translation_cancelled(S):
            raise AbortTranslation('cancelled', '')
        _asked_model = S.model
        try:
            response = gemini.generate(
                api_key=S.api_key,
                model=S.model,
                prompt=full_prompt,
                temperature=S.temperature,
                max_output_tokens=S.max_output_tokens,
                top_p=S.top_p,
                thinking_budget=S.thinking_budget,
                thinking_level=S.thinking_level,
                timeout=S.gemini_timeout or (
                    gemini.REQUEST_TIMEOUT if S.model == S.models.fallback
                    else CHOSEN_MODEL_TIMEOUT),
            )
            if NUMBERED_REQUESTS:
                response = srt_wire.from_wire(ch, response)
            # A model can expose its private self-correction inside a cue:
            # wrong Hebrew, "[Wait, let's fix ... -> ...]", then a second
            # draft. Deleting the note alone would leave the known-wrong
            # first draft, so reject the response and reuse the existing
            # retry/bisection/fallback ladder. The detector is source-bound
            # and requires an explicit editorial action, keeping literal
            # dialogue, stage directions and ordinary parentheses intact.
            parsed_response = srt_blocks.parse_blocks(response)
            editorial = postfix_leaks.generated_editorial_leak_indices(
                ch, parsed_response)
            source_echo = postfix_leaks.generated_source_echo_indices(
                ch, parsed_response)
            negation_loss = postfix_leaks.generated_negation_loss_indices(
                ch, parsed_response)
            untranslated = is_untranslated_chunk_response(
                ch, parsed_response)
            if editorial or source_echo or negation_loss or untranslated:
                kodi.log(
                    'Chunk {0}/{1}: generated-output integrity rejection '
                    '(self_edit={2}, source_echo={3}, negation_loss={4}, '
                    'untranslated={5}) '
                    '-- retrying safely'
                    .format(idx, S.total, len(editorial), len(source_echo),
                            len(negation_loss), bool(untranslated)),
                    level='WARNING')
                raise IntegrityRejected(
                    'generated-output integrity violation')
            return response
        except gemini.RateLimited as e:
            # TEMPORARY per-minute limit (not the daily quota): back off and
            # retry the SAME chunk so AI keeps going to the end of the movie.
            if ratelimit_attempts < len(RATELIMIT_BACKOFF):
                wait = e.retry_after or RATELIMIT_BACKOFF[ratelimit_attempts]
                wait = max(3, min(int(wait), 65))
                ratelimit_attempts += 1
                kodi.log(
                    'Gemini per-minute rate limit chunk {0}/{1}, '
                    'retry {2}/{3} in {4}s'.format(
                        idx, S.total, ratelimit_attempts,
                        len(RATELIMIT_BACKOFF), wait), level='WARNING')
                if not S.ratelimit_notified[0]:
                    S.ratelimit_notified[0] = True
                    kodi.notify(
                        'AI: קצב זמני מוגבל, ממתין רגע…', time_ms=4000)
                cancel_aware_wait(S, wait)
                continue
            # Still limited after the whole per-minute window -> fall back so
            # the user still gets subtitles for the remainder. Distinct reason
            # ('ratelimit') so the toast says "temporary overload", not the
            # misleading "daily quota exhausted, try again after midnight".
            raise AbortTranslation('ratelimit', 'AI: עומס זמני חורג')
        except gemini.QuotaExceeded:
            # the chosen model's day is spent (20 requests on a Flash
            # model): the default model has its own allowance
            if go_on_with_fallback(S, _asked_model, 'quota'):
                continue
            raise AbortTranslation('quota',
                kodi.localised(32635))
        except gemini.InvalidKey as e:
            kodi.log('InvalidKey: {0}'.format(e),
                           level='ERROR')
            raise AbortTranslation('invalid_key',
                kodi.localised(32634, 'API key rejected'))
        except gemini.TruncatedResponse as e:
            # propagate up to _translate_one which will bisect; the part
            # that did arrive gets its source timecodes like a full reply.
            if NUMBERED_REQUESTS and getattr(e, 'partial_text', ''):
                e.partial_text = srt_wire.from_wire(ch, e.partial_text)
            raise
        except gemini.FilteredResponse as e:
            # Prompt/safety block -- usually FLAKY. Retry the SAME prompt a
            # few times first (preserves the Arabic gender quality); only
            # after that do we propagate so _translate_one drops the Arabic
            # block, then bisects, then keeps source. Never aborts the job.
            if filtered_attempts < len(FILTERED_BACKOFF):
                wait = FILTERED_BACKOFF[filtered_attempts]
                filtered_attempts += 1
                kodi.log(
                    'Chunk {0}/{1} blocked ({2}) -- flaky? retry {3}/{4} '
                    'in {5}s (same prompt)'.format(
                        idx, S.total, str(e)[:50], filtered_attempts,
                        len(FILTERED_BACKOFF), wait), level='WARNING')
                cancel_aware_wait(S, wait)
                continue
            raise
        except gemini.OverloadError:
            # No second try on a chosen model that says it is busy: the
            # refusal itself took 11 s to come back, and its retry, paced
            # at a Flash model's 4 requests a minute, put the switch 68 s
            # into the job (QA Kodi, gemini-3.8-flash, 13 Nov 2026).
            # (A paid key keeps one 5 s retry: nothing paces it, and
            # one blip would move ten minutes of its translations.)
            if ((_asked_model != S.model or not S.paid_mode
                 or overload_attempts >= 1)
                    and go_on_with_fallback(S, _asked_model, 'busy')):
                overload_attempts = 0
                continue
            if overload_attempts < len(OVERLOAD_BACKOFF):
                wait = OVERLOAD_BACKOFF[overload_attempts]
                overload_attempts += 1
                kodi.log(
                    'Gemini overloaded chunk {0}/{1}, '
                    'retry {2}/{3} in {4}s'.format(
                        idx, S.total, overload_attempts,
                        len(OVERLOAD_BACKOFF), wait),
                    level='WARNING')
                if overload_attempts not in S.busy_toasts:
                    S.busy_toasts.add(overload_attempts)
                    kodi.notify(
                        'AI: Gemini עמוס. ניסיון {0}/{1} בעוד {2}ש'
                        .format(overload_attempts,
                                len(OVERLOAD_BACKOFF), wait),
                        time_ms=min(wait * 1000, 8000))
                S.busy_waits.seconds = getattr(
                    S.busy_waits, 'seconds', 0.0) + wait
                cancel_aware_wait(S, wait)
                continue
            raise AbortTranslation('overload',
                'AI: Gemini עמוס מדי גם אחרי {0} ניסיונות. '
                'תרגום נכשל ב-chunk {1}/{2}.'.format(
                    len(OVERLOAD_BACKOFF), idx, S.total))
        except gemini.GeminiError as e:
            # Not served to this key (404), or sent and no reply within
            # the timeout: the same model again is the same wait again.
            _why = ('gone' if isinstance(e, gemini.ModelUnavailable)
                    else 'slow' if (getattr(e, 'transport', False)
                                    and getattr(e, 'answered', False))
                    else '')
            if ((_why or _asked_model != S.model)
                    and go_on_with_fallback(S, _asked_model, _why or 'busy')):
                generic_attempts = 0
                continue
            if generic_attempts < len(GENERIC_BACKOFF):
                wait = GENERIC_BACKOFF[generic_attempts]
                generic_attempts += 1
                kodi.log(
                    'Gemini error chunk {0}/{1} attempt {2}: {3}'
                    .format(idx, S.total, generic_attempts, e),
                    level='WARNING')
                cancel_aware_wait(S, wait)
                continue
            raise AbortTranslation('error',
                kodi.localised(32638, str(e)[:80]),
                detail=str(e)[:100])
