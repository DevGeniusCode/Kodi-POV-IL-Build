# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# SRT timing: re-aligning a translation to the source cues, pinned starts, duration clamps.

from resources.lib.core.srt.blocks import _block_declared_index, _block_start, _block_timecode_index, _block_times, _fmt_tc, _rebuild_block, parse_blocks, stitch_blocks


# --- Cue-timing integrity ---------------------------------------------------
# The translator hands Gemini whole SRT blocks -- INCLUDING their timecode lines
# -- and stitches the reply back verbatim. The only validation on the reply is an
# ENTRY COUNT check, so a single mistyped digit in a timestamp the model was only
# supposed to copy ships straight to the player. Field reports: one Hebrew line
# frozen on screen while the rest of the dialogue came and went underneath it,
# a DIFFERENT line each re-translation, and clearing the add-on cache did not
# help (a fresh translation just mistypes a different entry). A single hour digit
# is enough: 00:41:22 --> 01:41:24 is a 60-minute cue.
#
# Two defences, deliberately layered:
#   restore_block_timings() -- the real fix. The model is never trusted with
#     timing: each translated block gets the SOURCE block's index + timecode back.
#   clamp_cue_durations()   -- a backstop for what the pairing cannot cover
#     (entry counts that legitimately differ, and a pathological SOURCE cue).
#
# The backstop is deliberately CONSERVATIVE. It is not the primary fix, so it is
# tuned to never damage a correctly-authored subtitle, at the price of leaving a
# corrupt cue a few seconds too long. Real subtitles legitimately contain long
# holds (a 90s ending-credits card, a title card over a silent scene) and
# intentional overlap (a location sign displayed across several dialogue lines,
# ASS-converted dual-speaker tracks) -- an aggressive clamp silently mangles all
# of those, which is a worse outcome than the bug it is guarding against.
_MAX_CUE_MS = 180000       # 3 min: longer than any authored hold, far under an
#                            hour-digit slip. Absolute sanity bound.
_OVERLAP_GRACE_MS = 10000  # how far a cue may outlive the NEXT cue's start before
#                            it is treated as runaway. Covers dual-speaker
#                            overlap and a sign held over roughly one dialogue
#                            turn -- NOT a sign held across many turns, which is
#                            still clipped. Raising it protects more authoring at
#                            the cost of leaving a corrupt cue on screen longer.
_MIN_CUE_MS = 400
_DEFAULT_CUE_MS = 2000
_NEXT_SCAN_MAX = 200           # bound the search for the next later-starting cue


def missing_blocks(src_blocks, out_blocks):
    """Source blocks the model's reply does NOT account for.

    Identity is the START TIMESTAMP, matched as a MULTISET, because that is the
    only field the model is asked to copy verbatim and the only one that
    survives a renumbered reply. Genuinely simultaneous dialogue (two entries at
    the same start) is handled by the multiset: two wanted and one returned
    leaves one missing.

    Counting alone -- "did we get back roughly as many entries as we asked
    for?" -- cannot see this. A reply that drops ten lines and invents ten
    others has the right count and the wrong content, and at an 85% yield
    threshold a chunk of 50 may quietly lose 7 lines and still be accepted.
    Fully fail-open: anything unparseable returns [] (caller keeps the reply)."""
    try:
        if not src_blocks:
            return []
        have = {}
        for b in out_blocks or ():
            st = _block_start(b)
            if st is not None:
                have[st] = have.get(st, 0) + 1
        out = []
        for b in src_blocks:
            st = _block_start(b)
            if st is None:
                continue
            if have.get(st, 0) > 0:
                have[st] -= 1
            else:
                out.append(b)
        return out
    except Exception:
        return []


def align_blocks(src_blocks, *candidate_lists):
    """Blocks drawn from `candidate_lists` to match `src_blocks` exactly: the
    same start timestamps, with the same MULTIPLICITY, in timeline order.
    Earlier lists win; a start is filled from later lists only while the source
    still calls for more at that start.

    Both halves of that rule are load-bearing, and getting either wrong loses
    subtitle content:

      * Multiplicity, not presence. Two source entries may legitimately share a
        start -- simultaneous dialogue -- and de-duplicating on start alone
        drops the second one PERMANENTLY. Not "left untranslated": gone from the
        subtitle. (Reproduced: a 3-entry chunk with two entries at one start
        came back with 2, while the caller's own bookkeeping reported success.)

      * Only what was asked for. A block whose start the model corrupted --
        a documented, already-observed failure of this model -- answers nothing
        that was requested. Adding it inserts a line at a fabricated time while
        the real slot is ALSO filled from the source, shipping the same dialogue
        twice. (Reproduced: 20 entries in, 21 out, one line at two timestamps.)

    Ordering matters downstream too: restore_block_timings pairs POSITIONALLY
    when the counts match, which only holds if the result is back in timeline
    order. The sort is stable, so entries sharing a start keep the order the
    candidates supplied them in. Fail-open: returns the first list on any
    problem."""
    try:
        wanted = {}
        for b in src_blocks or ():
            st = _block_start(b)
            if st is not None:
                wanted[st] = wanted.get(st, 0) + 1
        taken = {}
        keyed = []
        for lst in candidate_lists:
            for b in lst or ():
                st = _block_start(b)
                if st is None or taken.get(st, 0) >= wanted.get(st, 0):
                    continue
                taken[st] = taken.get(st, 0) + 1
                keyed.append((st, b))
        keyed.sort(key=lambda p: p[0])
        return [b for _st, b in keyed]
    except Exception:
        return list(candidate_lists[0]) if candidate_lists else []


def _repair_pinned_starts(src_blocks, out_blocks, fixed, ambiguous):
    """Give back the source timecode to a block whose START the model corrupted,
    but ONLY where its position is pinned by agreeing neighbours on both sides.

    `fixed` is the by-start repair pass's result (same length as out_blocks);
    blocks it already repaired are left exactly as they are. Fail-open: any
    surprise returns `fixed` untouched.

    The safety argument, in full, because the strictness is the whole point:
      * counts are equal and the declared SRT index still matches, so position i
        in the reply corresponds to source i UNLESS something shifted;
      * a shift (dropped or inserted entry) misaligns every block from the shift
        point onward, so a shifted block can never have an agreeing successor;
      * an adjacent transposition makes both members disagree, so neither has an
        agreeing neighbour on the swapped side;
      * therefore a block that disagrees while BOTH neighbours agree can only be
        an isolated mistyped timestamp, and src_blocks[i] is its source.
    A block at either end is pinned by its single existing neighbour, since there
    is no room on the other side for a block to have come from.
    """
    try:
        n = len(out_blocks)
        if n != len(src_blocks) or len(fixed) != n:
            return fixed
        agrees = []
        for i in range(n):
            ss, os_ = _block_start(src_blocks[i]), _block_start(out_blocks[i])
            agrees.append(ss is not None and os_ is not None and ss == os_)
        out = list(fixed)
        for i in range(n):
            if agrees[i]:
                continue
            if fixed[i] is not out_blocks[i]:
                continue          # the by-start pass already identified it
            src_start = _block_start(src_blocks[i])
            if src_start is None or src_start in ambiguous:
                continue
            # One agreeing neighbour alone cannot distinguish a mistyped start
            # from an invented final block that replaced a missing cue. The SRT
            # index is an independent anchor the model was also asked to copy;
            # without an exact match, keep the output unpaired and let the
            # caller restore the missing source slot instead.
            if (_block_declared_index(src_blocks[i]) is None
                    or _block_declared_index(src_blocks[i])
                    != _block_declared_index(out_blocks[i])):
                continue
            # None means "no neighbour on that side", which happens only at the
            # ends -- and an end has no room on the far side for a block to have
            # come from, so it is pinned by its one existing neighbour. (Both
            # None means n == 1, where equal counts pin the single block on
            # their own.)
            left = agrees[i - 1] if i > 0 else None
            right = agrees[i + 1] if i + 1 < n else None
            if left is False or right is False:
                continue          # a neighbour disagrees -> a shift or a swap
            rebuilt = _rebuild_block(src_blocks[i], out_blocks[i])
            if rebuilt:
                out[i] = rebuilt
        return out
    except Exception:
        return fixed


def restore_block_timings(src_blocks, out_blocks):
    """Give every translated block its SOURCE index + timecode line back.

    The model is asked to copy timings verbatim; it usually does, and when it
    does this is a no-op. When it does not, this is the difference between a
    correct subtitle and one with a line welded to the screen.

    Pairing is positional, but positional pairing is VERIFIED before it is
    trusted: equal block counts are necessary and not sufficient (a stray blank
    line inside the model's reply can split one cue into two while another is
    dropped, keeping the count intact but shifting everything after it). Every
    pair's start timestamps must agree -- not most of them, ALL of them. A
    tolerance is tempting, since a corrupted START would then also be repairable,
    but any tolerance is a window a shift can hide in: transposing the last two
    entries of a 30-entry chunk disagrees on only 6.7% of blocks and would sail
    through a 10% gate, silently swapping two lines' text. A wrong line at the
    right time is a worse defect than a right line at a wrong time, so the strict
    rule wins and a corrupted start is left to the clamp instead.

    When the counts differ, or any pair disagrees, each output block is instead
    matched to the source block with the SAME START. That covers the common
    "Gemini silently dropped an entry" case, which the caller accepts without
    retrying at up to 15% loss, and which a positional pass would otherwise
    mis-pair into corrupting every following cue.

    Blocks whose source start is AMBIGUOUS -- the same start appears more than
    once in the source, as genuinely simultaneous dialogue does -- are left
    untouched on both paths. Neither position nor start can tell such a pair
    apart, so repairing them means guessing, and a wrong guess swaps two lines
    while doing nothing beats leaving the model's own self-consistent timing.

    Fully fail-open: any surprise returns the input unchanged.
    """
    try:
        if not out_blocks or not src_blocks:
            return out_blocks
        by_start = {}
        for i, src in enumerate(src_blocks):
            st = _block_start(src)
            if st is not None:
                by_start.setdefault(st, []).append(i)
        ambiguous = set(st for st, idxs in by_start.items() if len(idxs) > 1)
        if len(src_blocks) == len(out_blocks):
            pairs = list(zip(src_blocks, out_blocks))
            comparable = agree = 0
            for src, out in pairs:
                ss, os_ = _block_start(src), _block_start(out)
                if ss is None or os_ is None:
                    continue
                comparable += 1
                if ss == os_:
                    agree += 1
            if comparable and agree == comparable:
                return [out if _block_start(src) in ambiguous
                        else (_rebuild_block(src, out) or out)
                        for src, out in pairs]
        # Counts differ, or a pair disagreed: repair only what can be identified
        # positively.
        used = set()
        fixed = []
        for out in out_blocks:
            st = _block_start(out)
            idxs = by_start.get(st) if st is not None and st not in ambiguous else None
            pick = None
            if idxs:
                for i in idxs:
                    if i not in used:
                        pick = i
                        break
            if pick is None:
                fixed.append(out)
                continue
            used.add(pick)
            fixed.append(_rebuild_block(src_blocks[pick], out) or out)
        # A block whose START the model corrupted matches nothing above, so it
        # kept the corrupted start -- and the clamp deliberately never moves a
        # start, so nothing downstream fixes it either. That is the "line appears
        # several cues early, then vanishes exactly when it is spoken" defect
        # (field report, 0.2.445): the END was repaired, the START never was.
        #
        # It IS recoverable, in the one case where position cannot be in doubt:
        # equal counts, and the block's IMMEDIATE NEIGHBOURS both agree with the
        # source positionally. A shift cannot hide there -- a shift misaligns
        # every block after it, so no shifted block has two agreeing neighbours.
        # Nor can an adjacent transposition, which makes BOTH members disagree.
        # Only an isolated mistyped timestamp fits, and for that one src_blocks[i]
        # is pinned on both sides. Anything less certain is still left alone.
        # (The equal-counts precondition is enforced inside, in one place.)
        return _repair_pinned_starts(src_blocks, out_blocks, fixed, ambiguous)
    except Exception:
        return out_blocks


def clamp_cue_durations(text, max_ms=None):
    """Bound every cue's END so one bad timestamp cannot pin a line on screen.

    Conservative by design (see the note above): a cue is shortened only when it
    outlives the next cue's start by more than _OVERLAP_GRACE_MS, or exceeds the
    absolute _MAX_CUE_MS sanity bound, or does not end after it starts. That
    leaves authored long holds and intentional overlap alone, while an hour-long
    cue -- the failure this exists for -- is still cut back to a few seconds.

    Starts are never touched: a line that appears early is a far smaller defect
    than a permanent one, and moving starts would desynchronise content that is
    otherwise fine.
    """
    try:
        if not text or '-->' not in text:
            return text
        limit = _MAX_CUE_MS if max_ms is None else max_ms
        blocks = parse_blocks(text)
        times = [_block_times(b) for b in blocks]
        # The next start strictly LATER than this cue's own start. A suffix
        # minimum cannot express that (the smallest following start may be
        # EARLIER than ours in an out-of-order file), so this is a forward scan
        # -- but a HARD-BOUNDED one. In a well-formed subtitle the answer is the
        # very next cue; the bound only matters for a degenerate file, which is
        # exactly where an unbounded rescan-per-cue would go quadratic. Past the
        # bound we simply decline to bound that cue by a neighbour and let the
        # absolute ceiling stand.
        n = len(blocks)
        next_later = [None] * n
        for i in range(n):
            ti = times[i]
            if ti is None:
                continue
            for j in range(i + 1, min(n, i + 1 + _NEXT_SCAN_MAX)):
                tj = times[j]
                if tj is not None and tj[0] > ti[0]:
                    next_later[i] = tj[0]
                    break
        out = []
        changed = 0
        for i, block in enumerate(blocks):
            t = times[i]
            if t is None:
                out.append(block)
                continue
            start, end, trail = t
            ceiling = start + limit
            nxt = next_later[i]
            if nxt is not None and nxt > start:
                ceiling = min(ceiling, nxt + _OVERLAP_GRACE_MS)
            new_end = end
            if new_end <= start:
                new_end = start + _DEFAULT_CUE_MS
            # Order matters: the minimum duration must never push the end back
            # past the ceiling, or the "cannot outlive the next cue" guarantee
            # is silently broken for rapid exchanges.
            new_end = min(max(new_end, start + _MIN_CUE_MS), ceiling)
            if new_end <= start:
                new_end = start + 1
            if new_end == end:
                out.append(block)
                continue
            changed += 1
            lines = block.split('\n')
            ti = _block_timecode_index(lines)
            if ti < 0:
                out.append(block)
                continue
            # Preserve the line's own EOL so a CRLF file stays a CRLF file.
            cr = '\r' if lines[ti].endswith('\r') else ''
            lines[ti] = '{0} --> {1}{2}{3}'.format(
                _fmt_tc(start), _fmt_tc(new_end), trail.rstrip('\r'), cr)
            out.append('\n'.join(lines))
        if not changed:
            return text
        return stitch_blocks(out)
    except Exception:
        return text


def merge_same_time_cues(text, max_lines=3):
    """Join consecutive entries that share one exact timecode into one cue.

    Netflix "cc" subtitles split every two-line caption into two entries with
    the same start and end. On screen they are one caption; to the model they
    are two numbered entries, and measured on a real episode it merged pairs
    of them, renumbered, and the stitched result lost 21 lines and doubled 5.
    One entry per caption, renumbered in order, is the shape the model keeps.
    A subtitle with no such pairs is returned byte-for-byte unchanged.
    """
    blocks = parse_blocks(text)
    if len(blocks) < 2:
        return text
    merged, changed = [], False
    prev_tc = None
    for block in blocks:
        lines = block.split('\n')
        ti = _block_timecode_index(lines)
        if ti < 0:
            merged.append([None, block])
            prev_tc = None
            continue
        tc = lines[ti].strip()
        body = [l for l in lines[ti + 1:] if l.strip()]
        if (merged and tc == prev_tc and merged[-1][0] is not None
                and len(merged[-1][2]) + len(body) <= max_lines):
            merged[-1][2].extend(body)
            changed = True
            continue
        merged.append([tc, None, body])
        prev_tc = tc
    if not changed:
        return text
    out = []
    for n, item in enumerate(merged, 1):
        if item[0] is None:
            lines = item[1].split('\n')
            ti = _block_timecode_index(lines)
            out.append('\n'.join([str(n)] + lines[max(ti, 0):] if ti >= 0
                                 else [str(n)] + lines[1:]))
        else:
            out.append('\n'.join([str(n), item[0]] + item[2]))
    return '\n\n'.join(out) + '\n'
