# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Align: region planning (cost rows, dynamic programming path, drift) for piecewise maps.

import bisect
import math
from resources.lib.core.srt.cues import cues_median
from resources.lib.sync.align.common import MAX_PLAUSIBLE_OFFSET_MS, MIN_OVERLAP, LOCAL_MIN_REF_CUES, MAXOFF, MICRO_MAX_SEGMENTS, MICRO_MAX_STEP_MS, MICRO_MIN_STEP_MS, PIECEWISE_MIN_GAIN, REGION_FINE_MIN_GAIN
from resources.lib.sync.align.estimate import best_offset, breathe
from resources.lib.sync.align.verify import nearest_onset_distance


# Cue-level region planner for the same narrow proposal space.  Fixed 2.5-min
# windows straddle short acts, so a fifth of the reference removed by a holdout
# fold can move a window onto the wrong plateau and make the re-planned map
# disagree with an otherwise perfect proposal.  Assigning every candidate cue
# its own plateau by dynamic programming locates each act from the cues inside
# it.  The output is still only a proposal: holdout folds and an independent
# timing family must prove it before ``apply_verdict`` will accept it.
_REGION_STEP_MS = 250
_REGION_RADIUS_MS = 32000
_REGION_CAP_MS = 800.0
_REGION_SWITCH_COST = 5000.0
_REGION_MATCH_MS = 400.0
_REGION_MIN_CUES = 20
_REGION_MIN_SPAN_MS = 60000.0
_REGION_BOUNDARY_SLACK = 8
_REGION_MAX_DRIFT_MS = 400.0
# Fine mode of the same planner: only for a caller that asks for it (the
# text-lane proof of a streaming rip), and only where the two planners above
# give no proven map.  Measured on a streaming WEB-DL whose own English track is
# the reference (The 100 S01E01, six acts): the broadcast-cut Hebrew sat at
# -0.46, +0.36, +0.71, +1.17 and +2.31 s and the Blu-ray-cut Hebrew at +1.93,
# +2.75, +3.92, +4.52, +5.91 and +6.32 s.  The dynamic programme separates
# every one of those acts; it was the 1-second merge that folded them into
# drifting plateaus, and the 1-second step floor and 3-second spread that
# refused the map.  Fine mode keeps plateaus 300 ms apart and accepts a map
# from two plateaus up, as long as the plateaus span at least a second (a
# smaller spread is not worth a rewrite).  The proposal space stays as
# narrow as before in every other way: steps in one direction only, none
# over 4.5 s, so zigzags and ordinary cuts are still not proposed here.  It
# is still only a proposal: the same held-out folds and the same independent
# family or native-lane proof decide.
_REGION_FINE_MIN_STEP_MS = 300.0
_REGION_FINE_MIN_RANGE_MS = 1000.0
# Share of the plateaus' total spread that may come from the trend inside
# them (see _region_piecewise_plan).  Thirteen real staircases measured
# 0.00-0.09; pure drift cut into plateaus measures about 1.
_REGION_FINE_MAX_RAMP = 0.2
# Where exactly a sub-second step falls cannot be read from onsets alone: a
# line on the wrong side of the cut is only half a second off, about as much
# as two subbers differ anyway (measured on synthetic half-second steps: cuts
# 4 and 6 lines from the act break, one of them a 24-second pause).  An edit
# is made in a pause, so among cut positions that explain the onsets about
# equally well the longer pause wins: every second of silence is worth 250 ms
# of onset distance, up to 10 s, and only among positions within 2.5 s of
# onset distance of the best.  Only for a step under a second: on a 2-second
# step the onsets do tell the sides apart, and there the pause moved cuts
# the wrong way (review: 38 lines a whole step off against 13 without it).
_REGION_FINE_GAP_WEIGHT = 0.25
_REGION_FINE_GAP_CAP_MS = 10000.0
_REGION_FINE_GAP_REACH_MS = 2500.0
_REGION_FINE_GAP_MAX_STEP_MS = 1000.0


def _region_cost_rows(ref_on, cand_on, center):
    """Capped nearest-onset distance of every candidate cue to every plateau.

    State ``k`` is the offset ``center - radius + k * step``.  Only reference
    onsets that can land within the cap of some state are visited, so a cue
    costs a handful of operations instead of one search per state.
    """
    step = float(_REGION_STEP_MS)
    radius = float(_REGION_RADIUS_MS)
    cap = _REGION_CAP_MS
    count = int(2 * radius / step) + 1
    rows = []
    for onset in cand_on:
        breathe()
        row = [cap] * count
        lo = bisect.bisect_left(ref_on, onset - center - radius - cap)
        hi = bisect.bisect_right(ref_on, onset - center + radius + cap)
        for ref_onset in ref_on[lo:hi]:
            delta = onset - ref_onset - center + radius
            first = max(0, int(math.ceil((delta - cap) / step)))
            last = min(count - 1, int(math.floor((delta + cap) / step)))
            for k in range(first, last + 1):
                distance = abs(delta - k * step)
                if distance < row[k]:
                    row[k] = distance
        rows.append(row)
    return rows


def _region_dp_path(rows):
    """Cheapest plateau per cue; changing plateau costs a fixed penalty.

    The same recurrence as before.  The two per-state passes that only
    located the minimum and recorded the switches now run in the
    interpreter's own loops (min / list.index / map): they were a third of
    this function's time, and it runs once per plan and once per held-out
    fold.
    """
    previous = list(rows[0])
    transitions = []
    for row in rows[1:]:
        breathe()
        floor = min(previous)
        best = previous.index(floor)
        threshold = floor + _REGION_SWITCH_COST
        # 1 where the state is dearer than switching in from the best one.
        flags = bytes(map(threshold.__lt__, previous))
        previous = [cost + (threshold if value > threshold else value)
                    for cost, value in zip(row, previous)]
        transitions.append((best, flags))
    state = previous.index(min(previous))
    path = [state]
    for best, flags in reversed(transitions):
        if flags[state]:
            state = best
        path.append(state)
    path.reverse()
    return path


def _region_matched_deltas(ref_on, cand, begin, end, base):
    """(candidate onset, candidate-minus-reference) pairs near ``base``."""
    pairs = []
    for cue in cand[begin:end + 1]:
        onset = cue['start']
        pos = bisect.bisect_left(ref_on, onset - base)
        best = None
        for j in (pos - 1, pos):
            if 0 <= j < len(ref_on):
                delta = onset - ref_on[j]
                if (abs(delta - base) <= _REGION_MATCH_MS
                        and (best is None
                             or abs(delta - base) < abs(best - base))):
                    best = delta
        if best is not None:
            pairs.append((onset, best))
    return pairs


def _region_trend_ms(pairs):
    """Signed least-squares offset change across one plateau's cues."""
    if len(pairs) < 3:
        return 0.0
    xs = [float(x) for x, _y in pairs]
    ys = [float(y) for _x, y in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0:
        return 0.0
    slope = sum((x - mean_x) * (y - mean_y)
                for x, y in zip(xs, ys)) / var_x
    return slope * (max(xs) - min(xs))


def _region_drift_ms(pairs):
    """Least-squares offset change across one plateau's matched cues."""
    if len(pairs) < 3:
        return 0.0
    xs = [float(x) for x, _y in pairs]
    ys = [float(y) for _x, y in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0:
        return 0.0
    slope = sum((x - mean_x) * (y - mean_y)
                for x, y in zip(xs, ys)) / var_x
    return abs(slope) * (max(xs) - min(xs))


def region_piecewise_plan(ref, cand, min_overlap=MIN_OVERLAP,
                           max_offset_ms=None, fine=False, free=False,
                           _dp=None):
    """Cue-level proposal for repeated short edit pads, pending validation.

    Same proposal space and the same acceptance tail as
    ``_micro_piecewise_plan``: identity clock, 3-8 monotonic plateaus whose
    steps are 1-4.5 s, every act sustained.  Only the plateau evidence
    differs: each candidate cue is assigned to the plateau its own onset
    supports, so a short act is never smeared by a window straddling it.  A
    plateau whose offset drifts is a clock-rate mismatch, not an edit, and is
    refused here so the scale paths handle it.

    ``fine`` lowers the floors to what the dynamic programme can actually
    resolve (see _REGION_FINE_MIN_STEP_MS): plateaus 300 ms apart, two or
    more of them, spanning a second.  Direction and the 4.5 s step ceiling
    are unchanged.  ``free`` is fine mode without those two (see above).
    ``_dp`` (a dict) carries the dynamic programme from one mode to the next
    for the same pair of cue lists; the modes differ only in what they make
    of its path.
    """
    from resources.lib.sync.align.piecewise import micro_verdict_from_cuts
    fine = bool(fine or free)
    name = 'region-free' if free else ('region-fine' if fine else 'region')
    info = {'planner': name}
    min_step = _REGION_FINE_MIN_STEP_MS if fine else float(MICRO_MIN_STEP_MS)
    if len(ref) < LOCAL_MIN_REF_CUES or len(cand) < 3 * _REGION_MIN_CUES:
        return None, info
    max_off = MAXOFF if max_offset_ms is None else max_offset_ms
    ref_on = [float(c['start']) for c in ref]
    cand_on = [float(c['start']) for c in cand]
    if _dp is not None and _dp.get('max_off') == max_off and 'path' in _dp:
        center, path = _dp['center'], _dp['path']
    else:
        center, _votes = best_offset(ref_on, cand_on, 1.0, max_off)
        path = _region_dp_path(_region_cost_rows(ref_on, cand_on, center))
        if _dp is not None:
            _dp.update(max_off=max_off, center=center, path=path)
    step = float(_REGION_STEP_MS)
    radius = float(_REGION_RADIUS_MS)

    runs = []
    for index, state in enumerate(path):
        if runs and runs[-1][0] == state:
            runs[-1][2] = index
        else:
            runs.append([state, index, index])

    def sustained(run):
        return (run[2] - run[1] + 1 >= _REGION_MIN_CUES
                and cand[run[2]]['end'] - cand[run[1]]['start']
                >= _REGION_MIN_SPAN_MS)

    while len(runs) > 1:
        weak = [index for index, run in enumerate(runs)
                if not sustained(run)]
        if not weak:
            break
        index = min(weak, key=lambda i: runs[i][2] - runs[i][1])
        neighbours = [j for j in (index - 1, index + 1)
                      if 0 <= j < len(runs)]
        target = min(neighbours,
                     key=lambda j: abs(runs[j][0] - runs[index][0]))
        low, high = sorted((index, target))
        runs[low] = [runs[target][0], runs[low][1], runs[high][2]]
        del runs[high]

    # Refine each plateau from its own matched cues, then merge neighbours
    # that turn out to be the same plateau.
    regions = []
    for state, begin, end in runs:
        base = center - radius + state * step
        regions.append([begin, end, base])
    merged = True
    while merged:
        merged = False
        refined = []
        for begin, end, base in regions:
            pairs = _region_matched_deltas(ref_on, cand, begin, end, base)
            if len(pairs) < max(8, int(0.25 * (end - begin + 1))):
                info['reason'] = 'unsupported region'
                return None, info
            refined.append([begin, end, cues_median([y for _x, y in pairs]),
                            pairs])
        regions = []
        for begin, end, offset, pairs in refined:
            if (regions
                    and abs(offset - regions[-1][2]) < min_step):
                previous = regions[-1]
                weight = previous[1] - previous[0] + 1
                size = end - begin + 1
                previous[1] = end
                previous[2] = ((previous[2] * weight + offset * size)
                               / float(weight + size))
                merged = True
            else:
                regions.append([begin, end, offset])
    info['regions'] = [(begin, end, offset) for begin, end, offset in regions]
    if not ((2 if fine else 3) <= len(regions) <= MICRO_MAX_SEGMENTS):
        return None, info
    offsets = [offset for _begin, _end, offset in regions]
    if any(abs(offset) > MAX_PLAUSIBLE_OFFSET_MS for offset in offsets):
        return None, info
    steps = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]
    direction = 1.0 if cues_median(steps) > 0 else -1.0
    if not free and any(direction * value < min_step
                        or abs(value) > MICRO_MAX_STEP_MS
                        for value in steps):
        return None, info
    if (max(offsets) - min(offsets)
            < (_REGION_FINE_MIN_RANGE_MS if fine else 3000.0)):
        return None, info
    for begin, end, offset, pairs in refined:
        if _region_drift_ms(pairs) > _REGION_MAX_DRIFT_MS:
            info['reason'] = 'drifting plateau'
            return None, info
    if fine:
        # A slow clock drift cut into 300 ms plateaus looks like a staircase
        # whose every plateau stays under the drift limit.  What tells them
        # apart: on a ramp the change happens INSIDE the plateaus, on real
        # act steps it happens between them and the plateaus are flat.
        inside = sum(_region_trend_ms(pairs)
                     for _begin, _end, _offset, pairs in refined)
        if abs(inside) > _REGION_FINE_MAX_RAMP * (max(offsets) - min(offsets)):
            info['reason'] = 'ramp, not steps'
            return None, info

    # Each cut goes where the two neighbouring plateaus explain the nearby
    # onsets best, among positions that keep adjacent cues ordered.
    cuts = []
    for left, right in zip(regions, regions[1:]):
        left_off, right_off = left[2], right[2]
        nominal = right[0]
        lo = max(left[0] + 4, nominal - _REGION_BOUNDARY_SLACK)
        hi = min(right[1] - 3, nominal + _REGION_BOUNDARY_SLACK)
        if cuts:
            lo = max(lo, cuts[-1] + 4)
        best = None
        options = []
        for position in range(lo, hi + 1):
            if (cand[position]['start'] - right_off
                    < cand[position - 1]['end'] - left_off):
                continue
            cost = 0.0
            for index in range(lo, hi + 1):
                offset = left_off if index < position else right_off
                cost += nearest_onset_distance(
                    ref_on, cand[index]['start'] - offset,
                    cap=_REGION_CAP_MS)
            options.append((cost, position))
            key = (cost, abs(position - nominal))
            if best is None or key < best[0]:
                best = (key, position)
        if best is None:
            info['reason'] = 'no ordered boundary'
            return None, info
        if fine and abs(right_off - left_off) < _REGION_FINE_GAP_MAX_STEP_MS:
            reach = best[0][0] + _REGION_FINE_GAP_REACH_MS

            def with_pause(option):
                cost, position = option
                pause = cand[position]['start'] - cand[position - 1]['end']
                return (cost - _REGION_FINE_GAP_WEIGHT * max(
                    0.0, min(float(pause), _REGION_FINE_GAP_CAP_MS)),
                    abs(position - nominal))
            best = (None, min((option for option in options
                               if option[0] <= reach), key=with_pause)[1])
        cuts.append(best[1])
    support = min(len(_region_matched_deltas(
        ref_on, cand, begin, end, offset)) / float(end - begin + 1)
        for begin, end, offset in regions)
    verdict, info = micro_verdict_from_cuts(
        ref, cand, cuts, offsets, info, vote=support,
        label=('free cue-level region proposal' if free
               else 'fine cue-level region proposal' if fine
               else 'cue-level region proposal'),
        min_overlap=min_overlap, max_offset_ms=max_offset_ms,
        min_gain=REGION_FINE_MIN_GAIN if fine else PIECEWISE_MIN_GAIN)
    if verdict:
        verdict['planner'] = name
    return verdict, info


def region_fine_plan(ref, cand, min_overlap=MIN_OVERLAP, max_offset_ms=None,
                      _dp=None):
    """The cue-level planner in fine mode, as a planner of its own (the
    held-out folds rebuild every fold with the planner that proposed)."""
    return region_piecewise_plan(ref, cand, min_overlap=min_overlap,
                                  max_offset_ms=max_offset_ms, fine=True,
                                  _dp=_dp)


def region_free_plan(ref, cand, min_overlap=MIN_OVERLAP, max_offset_ms=None,
                      _dp=None):
    """The cue-level planner in free mode, as a planner of its own."""
    return region_piecewise_plan(ref, cand, min_overlap=min_overlap,
                                  max_offset_ms=max_offset_ms, free=True,
                                  _dp=_dp)
