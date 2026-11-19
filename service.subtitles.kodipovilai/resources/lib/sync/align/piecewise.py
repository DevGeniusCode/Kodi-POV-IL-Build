# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Align: piecewise maps (local windows, drift refinement, micro-cuts, family validation).

import bisect
import math
import statistics
from resources.lib.core.srt.cues import cues_median, preflight_cues, preflight_srt, dialogue_cues
from resources.lib.sync.align.common import MAX_PLAUSIBLE_OFFSET_MS, MIN_CUES, MIN_OVERLAP, SCALE_MAX, SCALE_MIN, STATUS_FIXABLE, LOCAL_MIN_REF_CUES, MAXOFF, MICRO_MAX_SEGMENTS, MICRO_MAX_STEP_MS, MICRO_MIN_STEP_MS, PIECEWISE_MIN_GAIN, REGION_FINE_MIN_GAIN, TOL, UNIQUE_MIN
from resources.lib.sync.align.estimate import best_offset
from resources.lib.sync.align.regions import region_fine_plan, region_free_plan, region_piecewise_plan
from resources.lib.sync.align.verify import nearest_onset_distance, tight_agreement, timing_score, unique_match_metrics, overlap_rate


PIECEWISE_MIN_STEP_MS = 5000
_LOCAL_MAX_WINDOWS = 15
LOCAL_MIN_VOTE = 0.65
LOCAL_MIN_OVERLAP = 0.65
_PIECEWISE_MAX_SEGMENTS = 4
_LOCAL_RADIUS_MS = 150000
_LOCAL_SAME_PLATEAU_MS = 1500
_MICRO_SAME_PLATEAU_MS = 1200
_MICRO_MIN_WINDOWS = 10
_MICRO_MIN_CORE_RATIO = 0.75
_MICRO_POST_RESIDUAL_MS = 850
# Two builds of one fine map agree when they time the lines alike.  Counting
# plateaus fails on a 0.4 s step in a three-minute act, which a fold that
# lost a fifth of the reference merges into its neighbour (measured: folds
# of 5 and 6 plateaus for one map, every one of them scoring 0.84-0.90 on its
# held-out cues); comparing cut times fails when a cut slides to the next
# pause.  What matters is the line: at most 8 in 100 may differ by more than
# 300 ms between the two builds.
_REGION_FINE_AGREE_MS = 300.0
_REGION_FINE_AGREE_SHARE = 0.92


def piecewise_local_windows(ref, cand, scale, max_offset_ms=None, center_offset=0.0):
    """Cheap local offset estimates at one already-selected scale.

    The reference is partitioned once, so the work is roughly one additional
    scale evaluation rather than another full FPS search.  Returned offsets use
    the same direction as estimate(): candidate ~= scale*reference + offset.
    """
    if len(ref) < LOCAL_MIN_REF_CUES or len(cand) < MIN_CUES:
        return []
    count = len(ref)
    window_count = min(_LOCAL_MAX_WINDOWS, max(3, count // 20))
    # Search around the already-supported global peak.  Searching each small
    # window across the full +/-10-minute file admits dense-dialogue aliases
    # hundreds of seconds away (an otherwise perfectly aligned natural French
    # track produced a false 570-second local peak).  Real cut steps in the
    # measured campaign were 5-90 seconds; +/-150 seconds preserves those while
    # excluding the aliases.
    center_offset = float(center_offset or 0.0)
    cand_on = [c['start'] - center_offset for c in cand]
    max_off = MAXOFF if max_offset_ms is None else max_offset_ms
    local_radius = min(max_off, _LOCAL_RADIUS_MS)
    windows = []
    for wi in range(window_count):
        begin = round(wi * count / window_count)
        end = round((wi + 1) * count / window_count)
        subset = ref[begin:end]
        if len(subset) < MIN_CUES:
            continue
        offset, votes = best_offset(
            [c['start'] for c in subset], cand_on, scale, local_radius)
        offset += center_offset
        vote = votes / len(subset)
        overlap = overlap_rate(subset, cand, scale, offset)
        tight = tight_agreement(subset, cand, scale, offset)
        windows.append({
            'window': wi, 'begin': begin, 'end': end,
            'center_ms': statistics.fmean(c['start'] for c in subset),
            'offset_ms': float(offset), 'vote': vote,
            'overlap': overlap, 'tight': tight,
        })
    return windows


def trusted_local_windows(windows):
    return [w for w in windows
            if w['vote'] >= LOCAL_MIN_VOTE
            and w['overlap'] >= LOCAL_MIN_OVERLAP]


def piecewise_local_spread(windows):
    groups, trusted = _group_local_offsets(windows)
    if len(trusted) < 2:
        return None
    if len(groups) < 2:
        return 0.0
    offsets = [cues_median([w['offset_ms'] for w in group]) for group in groups]
    return max(offsets) - min(offsets)


def _group_local_offsets(windows):
    """Contiguous sustained timing plateaus; isolated boundary aliases drop."""
    trusted = trusted_local_windows(windows)
    if len(trusted) < max(4, int(0.60 * len(windows))):
        return [], trusted
    groups = []
    for w in trusted:
        if not groups:
            groups.append([w])
            continue
        current = groups[-1]
        if abs(w['offset_ms'] - cues_median(
                [x['offset_ms'] for x in current])) < _LOCAL_SAME_PLATEAU_MS:
            current.append(w)
        else:
            groups.append([w])

    # A window straddling a cut can lock onto an unrelated dense-dialogue peak.
    # It must not become its own segment.  Collapse it only when neighbouring
    # plateaus agree, or discard it when both neighbours are independently
    # supported and it is far from both.
    changed = True
    while changed and len(groups) > 1:
        changed = False
        for i, group in enumerate(list(groups)):
            if len(group) != 1:
                continue
            left = groups[i - 1] if i > 0 else None
            right = groups[i + 1] if i + 1 < len(groups) else None
            if left and right:
                lm = cues_median([x['offset_ms'] for x in left])
                rm = cues_median([x['offset_ms'] for x in right])
                own = float(group[0]['offset_ms'])
                if abs(lm - rm) < _LOCAL_SAME_PLATEAU_MS:
                    # The singleton is the alias; preserve the two agreeing
                    # plateaus and discard the outlier rather than polluting
                    # their spread with it.
                    left.extend(right)
                    del groups[i:i + 2]
                    changed = True
                    break
                if (len(left) >= 2 and len(right) >= 2
                        and abs(own - lm) >= PIECEWISE_MIN_STEP_MS
                        and abs(own - rm) >= PIECEWISE_MIN_STEP_MS):
                    del groups[i]
                    changed = True
                    break
    return groups, trusted


def smooth_drift_refinement(windows, scale):
    """Infer a continuous scale correction from sustained local offsets.

    Discrete 23.976/24/25/29.97/30 ratios cover normal FPS conversions, but a
    damaged or hand-authored subtitle can drift at an arbitrary rate.  Under a
    trial scale, its local offsets form a straight line whose slope is exactly
    the missing scale correction.  A median pairwise slope resists individual
    alias windows; the refit is offered only with strong linear evidence and is
    still subjected to every ordinary global/local acceptance gate afterward.
    """
    trusted = trusted_local_windows(windows)
    if (len(trusted) < 5
            or len(trusted) < max(5, int(0.60 * len(windows)))):
        return None
    xs = [float(w['center_ms']) for w in trusted]
    ys = [float(w['offset_ms']) for w in trusted]
    if max(xs) - min(xs) < 5 * 60 * 1000:
        return None
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i])
              for i in range(len(xs)) for j in range(i + 1, len(xs))
              if xs[j] != xs[i]]
    if not slopes:
        return None
    robust_slope = cues_median(slopes)
    intercept = cues_median([y - robust_slope * x for x, y in zip(xs, ys)])
    residuals = [y - (robust_slope * x + intercept)
                 for x, y in zip(xs, ys)]
    residual_mid = cues_median(residuals)
    mad = cues_median([abs(r - residual_mid) for r in residuals])
    limit = max(750.0, 3.5 * mad)
    kept = [(x, y) for x, y, residual in zip(xs, ys, residuals)
            if abs(residual - residual_mid) <= limit]
    if len(kept) < 5 or len(kept) < int(0.70 * len(trusted)):
        return None
    mean_x = statistics.fmean(x for x, _ in kept)
    mean_y = statistics.fmean(y for _, y in kept)
    denom = sum((x - mean_x) ** 2 for x, _ in kept)
    if denom <= 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in kept) / denom
    fitted_intercept = mean_y - slope * mean_x
    total = sum((y - mean_y) ** 2 for _, y in kept)
    error = sum((y - (slope * x + fitted_intercept)) ** 2
                for x, y in kept)
    r2 = 1.0 if total <= 1.0 else max(0.0, 1.0 - error / total)
    refined = float(scale) + slope
    if (r2 < 0.85 or abs(slope) < 0.0002 or abs(slope) > 0.04
            or not (SCALE_MIN <= refined <= SCALE_MAX)):
        return None
    return {'scale': refined, 'correction': slope, 'r2': r2,
            'retained': len(kept), 'total': len(trusted)}


def optimize_piecewise_boundaries(ref, cand, scale, groups, offsets):
    """Find ordered cut points using timestamps only, never subtitle text.

    Each candidate cue is scored under every supported plateau by nearest
    reference onset plus interval overlap.  Dynamic programming assigns one
    contiguous region per plateau, with boundaries restricted to the
    unsupported space between neighbouring trusted window groups.
    """
    if not cand or len(groups) != len(offsets):
        return None
    ref_on = [c['start'] for c in ref]
    ref_starts = ref_on
    ref_ends = [c['end'] for c in ref]
    cand_starts = [c['start'] for c in cand]
    n, k = len(cand), len(offsets)
    costs = []
    for offset in offsets:
        prefix = [0.0]
        for cue in cand:
            mapped_s = (cue['start'] - offset) / scale
            mapped_e = (cue['end'] - offset) / scale
            nearest = nearest_onset_distance(ref_on, mapped_s)
            overlap = False
            j = bisect.bisect_left(ref_ends, mapped_s)
            while j < len(ref) and ref_starts[j] < mapped_e:
                if min(mapped_e, ref_ends[j]) > max(mapped_s, ref_starts[j]):
                    overlap = True
                    break
                j += 1
            prefix.append(prefix[-1] + nearest
                          + (0.0 if overlap else 1200.0))
        costs.append(prefix)

    allowed = []
    for left, right in zip(groups, groups[1:]):
        left_off = cues_median([w['offset_ms'] for w in left])
        right_off = cues_median([w['offset_ms'] for w in right])
        lo_time = scale * left[-1]['center_ms'] + left_off
        hi_time = scale * right[0]['center_ms'] + right_off
        lo, hi = sorted((lo_time, hi_time))
        margin = max(4, n // 100)
        lo_i = max(4, bisect.bisect_left(cand_starts, lo) - margin)
        hi_i = min(n - 4, bisect.bisect_right(cand_starts, hi) + margin)
        if lo_i > hi_i:
            return None
        # A later plateau with a larger offset moves its first cue further
        # backwards.  A cut placed inside continuous dialogue would therefore
        # reorder/overlap adjacent cues even if its aggregate timing score looks
        # attractive.  Keep only boundaries whose two neighbouring cues remain
        # ordered after applying each side's own map; ``apply_verdict`` repeats
        # this invariant on the complete, unfiltered SRT.
        safe_positions = [index for index in range(lo_i, hi_i + 1)
                          if ((cand[index]['start'] - right_off) / scale
                              >= (cand[index - 1]['end'] - left_off) / scale)]
        if not safe_positions:
            return None
        allowed.append(safe_positions)

    states = {0: (0.0, [])}
    for boundary_index, positions in enumerate(allowed):
        next_states = {}
        for end in positions:
            best = None
            for start, (prior_cost, cuts) in states.items():
                if end - start < 4:
                    continue
                value = (prior_cost + costs[boundary_index][end]
                         - costs[boundary_index][start])
                if best is None or value < best[0]:
                    best = (value, cuts + [end])
            if best is not None:
                next_states[end] = best
        states = next_states
        if not states:
            return None
    best = None
    for start, (prior_cost, cuts) in states.items():
        if n - start < 4:
            continue
        value = prior_cost + costs[k - 1][n] - costs[k - 1][start]
        if best is None or value < best[0]:
            best = (value, cuts)
    return None if best is None else best[1]


def map_cues_piecewise(cand, scale, segments):
    boundaries = [s['cand_from_ms'] for s in segments[1:]]
    out = []
    for cue in cand:
        index = bisect.bisect_right(boundaries, cue['start'])
        offset = segments[index]['offset_ms']
        out.append(dict(cue,
                        start=(cue['start'] - offset) / scale,
                        end=(cue['end'] - offset) / scale))
    return out


def map_cues_global(cand, scale, offset):
    return [dict(cue,
                 start=(cue['start'] - offset) / scale,
                 end=(cue['end'] - offset) / scale)
            for cue in cand]


def _micro_core_windows(windows):
    """Strong local windows after removing isolated dense-dialogue aliases.

    A local histogram can occasionally lock onto dialogue tens of seconds away
    even while its vote/overlap look plausible.  A real sequence of short edit
    pads follows one slow monotonic trend.  Theil-Sen's median pairwise slope
    models that trend without being pulled by one bad window; a generous robust
    residual then removes only gross aliases.  This is still proposal evidence,
    never an acceptance decision.
    """
    trusted = trusted_local_windows(windows)
    if (len(trusted) < _MICRO_MIN_WINDOWS
            or len(trusted) < int(math.ceil(
                _MICRO_MIN_CORE_RATIO * len(windows)))):
        return []
    xs = [float(w['center_ms']) for w in trusted]
    ys = [float(w['offset_ms']) for w in trusted]
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i])
              for i in range(len(xs)) for j in range(i + 1, len(xs))
              if xs[j] != xs[i]]
    if not slopes:
        return []
    slope = cues_median(slopes)
    intercept = cues_median([y - slope * x for x, y in zip(xs, ys)])
    residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
    middle = cues_median(residuals)
    mad = cues_median([abs(value - middle) for value in residuals])
    limit = max(2500.0, 5.0 * mad)
    core = [w for w, residual in zip(trusted, residuals)
            if abs(residual - middle) <= limit]
    if (len(core) < _MICRO_MIN_WINDOWS
            or len(core) < int(math.ceil(
                _MICRO_MIN_CORE_RATIO * len(windows)))):
        return []
    return core


def _micro_plateau_groups(windows):
    core = _micro_core_windows(windows)
    groups = []
    for window in core:
        if (not groups
                or abs(window['offset_ms'] - cues_median(
                    [item['offset_ms'] for item in groups[-1]]))
                >= _MICRO_SAME_PLATEAU_MS):
            groups.append([window])
        else:
            groups[-1].append(window)
    return groups, core


def _micro_piecewise_plan(ref, cand, min_overlap=MIN_OVERLAP,
                          max_offset_ms=None):
    """Proposal for repeated short edit pads, pending track consensus.

    The base clock is intentionally pinned to identity.  Standard/global FPS
    drift and large cuts already have independently proven paths; mixing those
    freedoms here would make a subtle edit too easy to overfit.  The returned
    verdict carries ``validation_required`` and therefore cannot be applied
    until a separate holdout/independent-family validator approves it.
    """
    scale = 1.0
    max_off = MAXOFF if max_offset_ms is None else max_offset_ms
    center_offset, _votes = best_offset(
        [c['start'] for c in ref], [c['start'] for c in cand], scale, max_off)
    windows = piecewise_local_windows(
        ref, cand, scale, max_offset_ms=max_offset_ms,
        center_offset=center_offset)
    groups, core = _micro_plateau_groups(windows)
    info = {'windows': windows, 'core': core, 'groups': groups}
    if not (3 <= len(groups) <= MICRO_MAX_SEGMENTS):
        return None, info
    # Boundary regions must be sustained, and at most one short middle act may
    # be represented by a single window.  Consensus will independently confirm
    # that singleton at the same candidate boundary.
    if len(groups[0]) < 2 or len(groups[-1]) < 2:
        return None, info
    if sum(1 for group in groups if len(group) == 1) > 1:
        return None, info

    offsets = [cues_median([w['offset_ms'] for w in group])
               for group in groups]
    if any(abs(offset) > MAX_PLAUSIBLE_OFFSET_MS for offset in offsets):
        return None, info
    steps = [offsets[i + 1] - offsets[i]
             for i in range(len(offsets) - 1)]
    if not steps:
        return None, info
    direction = 1.0 if cues_median(steps) > 0 else -1.0
    if any(direction * step < MICRO_MIN_STEP_MS
           or abs(step) > MICRO_MAX_STEP_MS for step in steps):
        return None, info
    if max(offsets) - min(offsets) < 3000.0:
        return None, info
    timeline_start = float(ref[0]['start'])
    timeline_end = float(ref[-1]['end'])
    timeline_span = max(1.0, timeline_end - timeline_start)
    if (groups[0][0]['center_ms'] > timeline_start + 0.25 * timeline_span
            or groups[-1][-1]['center_ms']
            < timeline_start + 0.75 * timeline_span):
        return None, info

    cuts = optimize_piecewise_boundaries(
        ref, cand, scale, groups, offsets)
    if cuts is None:
        return None, info
    return micro_verdict_from_cuts(
        ref, cand, cuts, offsets, info,
        vote=min(w['vote'] for w in core),
        label='embedded-track consensus proposal',
        min_overlap=min_overlap, max_offset_ms=max_offset_ms)


def micro_verdict_from_cuts(ref, cand, cuts, offsets, info, vote, label,
                             min_overlap=MIN_OVERLAP, max_offset_ms=None,
                             min_gain=PIECEWISE_MIN_GAIN):
    """Shared acceptance tail for both short-edit planners.

    Whatever proposed the plateaus, the same region-size, ordering, residual,
    overlap, uniqueness and score-gain gates decide whether a validation-
    required proposal exists at all.
    """
    scale = 1.0
    # Whole milliseconds: a median of deltas can end in .5, and a cue one
    # millisecond long then has both its ends rounded to the same stamp, which
    # the rewrite refuses (Supernatural S05E01, a 1 ms cue under -5579.5 ms).
    offsets = [float(round(value)) for value in offsets]
    candidate_boundaries = []
    reference_boundaries = []
    for cut, left_offset, right_offset in zip(
            cuts, offsets, offsets[1:]):
        candidate_boundary = (
            cand[cut - 1]['end'] + cand[cut]['start']) / 2.0
        reference_boundary = (
            (cand[cut - 1]['end'] - left_offset)
            + (cand[cut]['start'] - right_offset)) / 2.0
        candidate_boundaries.append(candidate_boundary)
        reference_boundaries.append(reference_boundary)
    segments = []
    for index, offset in enumerate(offsets):
        segments.append({
            'cand_from_ms': (None if index == 0
                             else candidate_boundaries[index - 1]),
            'cand_to_ms': (None if index == len(offsets) - 1
                           else candidate_boundaries[index]),
            'ref_from_ms': (None if index == 0
                            else reference_boundaries[index - 1]),
            'ref_to_ms': (None if index == len(offsets) - 1
                          else reference_boundaries[index]),
            'offset_ms': offset,
        })

    cue_edges = [0] + list(cuts) + [len(cand)]
    min_segment_cues = max(12, int(math.ceil(0.02 * len(cand))))
    for begin, end in zip(cue_edges, cue_edges[1:]):
        if end - begin < min_segment_cues:
            return None, info
        region_start = float(cand[begin]['start'])
        region_end = float(cand[end - 1]['end'])
        if region_end - region_start < 60000.0:
            return None, info

    fixed = map_cues_piecewise(cand, scale, segments)
    if (len(fixed) != len(cand)
            or any(c['end'] <= c['start'] for c in fixed)
            or any(fixed[i]['start'] < fixed[i - 1]['start']
                   for i in range(1, len(fixed)))):
        return None, info
    post_windows = piecewise_local_windows(
        ref, fixed, 1.0, max_offset_ms=max_offset_ms, center_offset=0.0)
    post_core = _micro_core_windows(post_windows)
    if len(post_core) < _MICRO_MIN_WINDOWS:
        return None, info
    post_offsets = [w['offset_ms'] for w in post_core]
    post_residual = max(post_offsets) - min(post_offsets)
    before_score = timing_score(ref, cand)
    after_score = timing_score(ref, fixed)
    after_overlap = overlap_rate(ref, fixed, 1.0, 0.0)
    after_unique, unique_median, unique_p95 = unique_match_metrics(
        ref, fixed, 1.0, 0.0)
    if (post_residual > _MICRO_POST_RESIDUAL_MS
            or after_overlap < max(0.85, min_overlap)
            or after_unique < 0.72
            or after_score < 0.78
            or after_score < before_score + min_gain):
        info.update({'post_residual_ms': post_residual,
                     'before_score': before_score,
                     'after_score': after_score,
                     'after_overlap': after_overlap,
                     'after_unique': after_unique})
        return None, info
    diag = ('%s: %d regions offsets=%s '
            'score=%.3f->%.3f overlap=%.0f%% unique=%.0f%% residual=%dms'
            % (label, len(segments),
               ','.join('%+d' % int(value) for value in offsets),
               before_score, after_score, after_overlap * 100,
               after_unique * 100, int(post_residual)))
    verdict = {
        'status': STATUS_FIXABLE,
        'mode': 'piecewise',
        'scale': 1.0,
        'offset_ms': offsets[0],
        'segments': segments,
        'vote': vote,
        'overlap': after_overlap,
        'unique': after_unique,
        'unique_median_ms': unique_median,
        'unique_p95_ms': unique_p95,
        'before_score': before_score,
        'after_score': after_score,
        'post_residual_ms': post_residual,
        'cand_span_ms': float(cand[-1]['end']),
        'validation_required': True,
        'validation_folds': 0,
        'timing_family_count': 0,
        'diag': diag,
    }
    return verdict, info


def snap_piecewise_boundaries(verdict, cand_all):
    """Move each cut within its dialogue gap so it is valid for ALL cues.

    Planners place cuts between dialogue cues only, yet ``apply_verdict``
    retimes every cue.  A music/credit line sitting just before a cut keeps
    the left offset while the next spoken line takes the right one, and a
    positive step then stacks the two.  Sliding the cut across such non-
    dialogue lines -- never across a dialogue cue -- leaves every validated
    dialogue mapping untouched.  A cut that cannot be made valid is left as
    is so the strict invariants still refuse it.
    """
    try:
        if (not verdict or verdict.get('status') != STATUS_FIXABLE
                or verdict.get('mode') != 'piecewise'):
            return verdict
        segments = [dict(item) for item in verdict.get('segments') or []]
        if len(segments) < 2 or not cand_all:
            return verdict
        scale = float(verdict.get('scale') or 1.0)
        cues = sorted(cand_all, key=lambda cue: cue['start'])
        starts = [float(cue['start']) for cue in cues]
        spoken = [float(cue['start']) for cue in dialogue_cues(cues)]
        if not spoken:
            return verdict
        changed = False
        for index in range(1, len(segments)):
            boundary = segments[index].get('cand_from_ms')
            if boundary is None:
                return verdict
            boundary = float(boundary)
            left = float(segments[index - 1].get('offset_ms') or 0.0)
            right = float(segments[index].get('offset_ms') or 0.0)

            def valid(k):
                return (0 < k < len(cues) and starts[k] > starts[k - 1]
                        and (starts[k] - right) / scale
                        >= (float(cues[k - 1]['end']) - left) / scale)
            nominal = bisect.bisect_left(starts, boundary)
            if valid(nominal):
                continue
            j = bisect.bisect_left(spoken, boundary)
            low_time = spoken[j - 1] if j > 0 else float('-inf')
            high_time = spoken[j] if j < len(spoken) else float('inf')
            first = max(1, bisect.bisect_right(starts, low_time))
            last = min(len(cues) - 1, bisect.bisect_left(starts, high_time))
            options = [k for k in range(first, last + 1) if valid(k)]
            if not options:
                continue
            k = min(options, key=lambda item: abs(item - nominal))
            previous_end = float(cues[k - 1]['end'])
            new_boundary = min(max((previous_end + starts[k]) / 2.0,
                                   starts[k - 1] + 1.0), starts[k])
            lower = (float(segments[index - 1].get('cand_from_ms'))
                     if segments[index - 1].get('cand_from_ms') is not None
                     else float('-inf'))
            upper = (float(segments[index + 1].get('cand_from_ms'))
                     if index + 1 < len(segments)
                     and segments[index + 1].get('cand_from_ms') is not None
                     else float('inf'))
            if not lower < new_boundary < upper:
                continue
            reference = ((previous_end - left) / scale
                         + (starts[k] - right) / scale) / 2.0
            segments[index]['cand_from_ms'] = new_boundary
            segments[index - 1]['cand_to_ms'] = new_boundary
            if 'ref_from_ms' in segments[index]:
                segments[index]['ref_from_ms'] = reference
            if 'ref_to_ms' in segments[index - 1]:
                segments[index - 1]['ref_to_ms'] = reference
            changed = True
        if not changed:
            return verdict
        snapped = dict(verdict)
        snapped['segments'] = segments
        return snapped
    except (TypeError, ValueError, KeyError):
        return verdict


# Proposal order.  A map from an earlier planner is never replaced by a later
# one, and the fine and free modes exist only for a caller that asked for
# them: every other caller gets the two planners it always had.
_PIECEWISE_PLANNERS = ('micro', 'region')
_FINE_PLANNER = 'region-fine'
_FREE_PLANNER = 'region-free'
# Planners whose folds are compared line by line and scored on the map each
# fold built, with the lower 'materially better' bar.
_FINE_PLANNERS = (region_fine_plan, region_free_plan)


def _piecewise_planner(name):
    if name == 'region':
        return region_piecewise_plan
    if name == 'region-fine':
        return region_fine_plan
    if name == _FREE_PLANNER:
        return region_free_plan
    return _micro_piecewise_plan


def _planner_names(fine, free):
    return (_PIECEWISE_PLANNERS
            + ((_FINE_PLANNER,) if fine or free else ())
            + ((_FREE_PLANNER,) if free else ()))


def _plan(name, ref, cand, max_offset_ms, shared):
    """One planner's full proposal; the cue-level modes share ``shared``."""
    planner = _piecewise_planner(name)
    if planner is _micro_piecewise_plan:
        return planner(ref, cand, max_offset_ms=max_offset_ms)
    return planner(ref, cand, max_offset_ms=max_offset_ms, _dp=shared)


def micro_piecewise_proposal(ref_cues, cand_srt_text,
                             max_offset_ms=None, free=False, fine=False):
    """Return a non-applicable short-edit proposal for external consensus.

    This API deliberately does not accept a single timing track as proof.  A
    media-aware caller must validate unseen timing cues (or compare genuinely
    independent timing families) before ``apply_verdict`` can rewrite anything.

    ``fine`` also offers the cue-level planner's fine mode, and ``free`` its
    free-direction mode after that (see _region_piecewise_plan); only for a
    caller whose proof does not rest on this one reference.
    """
    ref, ref_error = preflight_cues(ref_cues)
    if ref_error:
        return None
    cand_all, cand_error = preflight_srt(cand_srt_text, True)
    if cand_error:
        return None
    cand = dialogue_cues(cand_all)
    if len(ref) < LOCAL_MIN_REF_CUES or len(cand) < LOCAL_MIN_REF_CUES:
        return None
    verdict = None
    shared = {}
    for name in _planner_names(fine, free):
        verdict, _info = _plan(name, ref, cand, max_offset_ms, shared)
        if verdict is not None:
            break
    return verdict


def _piecewise_reference_time(verdict, candidate_ms):
    try:
        scale = float(verdict.get('scale') or 1.0)
        segments = list(verdict.get('segments') or [])
        if not segments:
            return ((float(candidate_ms)
                     - float(verdict.get('offset_ms') or 0.0)) / scale)
        boundaries = [float(item.get('cand_from_ms'))
                      for item in segments[1:]
                      if item.get('cand_from_ms') is not None]
        index = min(bisect.bisect_right(boundaries, float(candidate_ms)),
                    len(segments) - 1)
        offset = float(segments[index].get('offset_ms') or 0.0)
        return (float(candidate_ms) - offset) / scale
    except Exception:
        return None


def piecewise_maps_agree(left, right, tolerance_ms=700.0):
    """Compare plateau maps without sampling directly on a discontinuity.

    A fold can place the same act boundary at a neighbouring quiet cue.  Testing
    the exact boundary timestamp would select the old side in one map and the
    new side in the other, reporting a false full-step disagreement.  Instead,
    corresponding offsets must agree tightly, boundaries must stay within one
    conservative 45-second local-window margin, and each overlapping region is
    compared at its midpoint away from either jump.
    """
    try:
        if left.get('mode') != 'piecewise' or right.get('mode') != 'piecewise':
            return False
        lsegments = list(left.get('segments') or [])
        rsegments = list(right.get('segments') or [])
        if len(lsegments) < 2 or len(lsegments) != len(rsegments):
            return False
        if abs(float(left.get('scale') or 1.0)
               - float(right.get('scale') or 1.0)) > 0.0002:
            return False
        loffsets = [float(item.get('offset_ms') or 0.0)
                    for item in lsegments]
        roffsets = [float(item.get('offset_ms') or 0.0)
                    for item in rsegments]
        if any(abs(a - b) > min(300.0, float(tolerance_ms))
               for a, b in zip(loffsets, roffsets)):
            return False
        lboundaries = [float(item['cand_from_ms']) for item in lsegments[1:]]
        rboundaries = [float(item['cand_from_ms']) for item in rsegments[1:]]
        if any(abs(a - b) > 45000.0
               for a, b in zip(lboundaries, rboundaries)):
            return False
        span = min(float(left.get('cand_span_ms') or 0.0),
                   float(right.get('cand_span_ms') or 0.0))
        if span <= 5 * 60 * 1000:
            return False
        ledges = [0.0] + lboundaries + [span]
        redges = [0.0] + rboundaries + [span]
        points = []
        for index in range(len(lsegments)):
            low = max(ledges[index], redges[index])
            high = min(ledges[index + 1], redges[index + 1])
            if high - low < 1000.0:
                return False
            points.append((low + high) / 2.0)
        for point in points:
            ltime = _piecewise_reference_time(left, point)
            rtime = _piecewise_reference_time(right, point)
            if (ltime is None or rtime is None
                    or abs(ltime - rtime) > float(tolerance_ms)):
                return False
        return True
    except Exception:
        return False


def _piecewise_maps_agree_on_lines(left, right, cand):
    """Whether two region maps time (nearly) every line alike."""
    try:
        if (left.get('mode') != 'piecewise' or right.get('mode') != 'piecewise'
                or not cand):
            return False
        same = 0
        for cue in cand:
            ltime = _piecewise_reference_time(left, cue['start'])
            rtime = _piecewise_reference_time(right, cue['start'])
            if ltime is None or rtime is None:
                return False
            if abs(ltime - rtime) <= _REGION_FINE_AGREE_MS:
                same += 1
        return same >= _REGION_FINE_AGREE_SHARE * len(cand)
    except Exception:
        return False


def validate_micro_piecewise(ref_cues, cand_srt_text, proposal=None,
                             max_offset_ms=None, free=False, fine=False):
    """Five-fold out-of-sample proof for a subtle piecewise proposal.

    Each trial removes one fifth of reference cues, rebuilds the complete map
    without them, and then scores that map only on the unseen fifth. Four of
    five separately held-out sets must materially improve and reproduce the
    full map across the whole timeline. Duplicate Matroska tracks therefore
    cannot masquerade as independent evidence.
    """
    ref, ref_error = preflight_cues(ref_cues)
    if ref_error:
        return None
    cand_all, cand_error = preflight_srt(cand_srt_text, True)
    if cand_error:
        return None
    cand = dialogue_cues(cand_all)
    if len(ref) < 3 * LOCAL_MIN_REF_CUES or len(cand) < LOCAL_MIN_REF_CUES:
        return None
    full = proposal
    if full is None:
        full, _info = _micro_piecewise_plan(
            ref, cand, max_offset_ms=max_offset_ms)
    names = list(_planner_names(fine, free))
    shared = {}
    first = (full or {}).get('planner') or 'micro'
    if first not in names:
        first = 'micro'
    validated = None
    if full and full.get('validation_required'):
        validated = _holdout_validate(
            ref, cand, full, _piecewise_planner(first), max_offset_ms)
    # Window plateaus can be unstable when a fold removes the cues of a short
    # act; every later planner gets its own full proposal and its own five
    # folds.  Never a mix: each fold is rebuilt by the planner that proposed
    # the map it is compared with.
    for name in names[names.index(first) + 1:]:
        if validated is not None:
            break
        planner = _piecewise_planner(name)
        later, _info = _plan(name, ref, cand, max_offset_ms, shared)
        if later and later.get('validation_required'):
            validated = _holdout_validate(
                ref, cand, later, planner, max_offset_ms)
    return snap_piecewise_boundaries(validated, cand_all)


def _holdout_validate(ref, cand, full, planner, max_offset_ms=None):
    reference_edges = [
        float(item.get('ref_from_ms'))
        for item in (full.get('segments') or [])[1:]
        if item.get('ref_from_ms') is not None]
    min_gain = (REGION_FINE_MIN_GAIN if planner in _FINE_PLANNERS
                else PIECEWISE_MIN_GAIN)
    passed = []
    for fold in range(5):
        train = [cue for index, cue in enumerate(ref) if index % 5 != fold]
        holdout = [cue for index, cue in enumerate(ref) if index % 5 == fold]
        # Every holdout must exercise every plateau, rather than validating only
        # the dense beginning/end and leaving a middle correction untested.
        holdout_regions = [0] * len(full.get('segments') or [])
        for cue in holdout:
            region = bisect.bisect_right(reference_edges, cue['start'])
            holdout_regions[min(region, len(holdout_regions) - 1)] += 1
        if not holdout_regions or min(holdout_regions) < 5:
            continue
        fold_plan, _info = planner(
            train, cand, max_offset_ms=max_offset_ms)
        if not fold_plan:
            continue
        if planner in _FINE_PLANNERS:
            if not _piecewise_maps_agree_on_lines(full, fold_plan, cand):
                continue
        elif not piecewise_maps_agree(full, fold_plan):
            continue
        # Score the FULL map that would actually be applied.  The fold-derived
        # map is used as an independent stability requirement above; this keeps
        # the production transform itself under the held-out measurement.
        # The cue-level planner fits every plateau on the very cues being
        # held out, so for it the map rebuilt WITHOUT them is what must
        # explain them: a genuinely out-of-sample score.
        scored = (fold_plan if planner is region_piecewise_plan
                  or planner in _FINE_PLANNERS else full)
        fixed = map_cues_piecewise(
            cand, scored.get('scale', 1.0), scored.get('segments') or [])
        if (len(fixed) != len(cand)
                or any(c['end'] <= c['start'] for c in fixed)
                or any(fixed[index]['start'] < fixed[index - 1]['start']
                       for index in range(1, len(fixed)))):
            continue
        before_score = timing_score(holdout, cand)
        after_score = timing_score(holdout, fixed)
        after_overlap = overlap_rate(holdout, fixed, 1.0, 0.0)
        after_unique, _median_ms, p95_ms = unique_match_metrics(
            holdout, fixed, 1.0, 0.0)
        if (after_overlap < 0.85 or after_unique < 0.72
                or p95_ms is None or p95_ms > 800.0
                or after_score < 0.78
                or after_score < before_score + min_gain):
            continue
        passed.append({'fold': fold, 'before_score': before_score,
                       'after_score': after_score,
                       'overlap': after_overlap,
                       'unique': after_unique})
    if len(passed) < 4:
        return None
    validated = dict(full)
    validated.update({
        'validation_folds': len(passed),
        'holdout_score_min': min(item['after_score'] for item in passed),
        'holdout_gain_min': min(item['after_score'] - item['before_score']
                                for item in passed),
        'diag': ('%s; %d/5 disjoint holdouts passed '
                 '(score>=%.3f gain>=%.3f)'
                 % (full.get('diag', ''), len(passed),
                    min(item['after_score'] for item in passed),
                    min(item['after_score'] - item['before_score']
                        for item in passed))),
    })
    return validated


def evaluate_piecewise_family(ref_cues, cand_srt_text, verdict,
                              max_offset_ms=None,
                              min_gain=PIECEWISE_MIN_GAIN):
    """Out-of-family validation/veto metrics for an already frozen map.

    This does not refit a single parameter.  It asks whether a map learned from
    a preferred English text timeline also improves a different embedded codec
    family (normally PGS).  One representative validates the family; duplicate
    language tracks never increase its evidence count.

    ``min_gain`` is how much better the frozen map must make this timeline.
    A second FAMILY carries the proof and keeps the default; a lane that only
    has to show the reference is native to the file may ask for less.
    """
    ref, ref_error = preflight_cues(ref_cues)
    cand_all, cand_error = preflight_srt(cand_srt_text, True)
    if ref_error or cand_error or not verdict:
        return None
    cand = dialogue_cues(cand_all)
    segments = list(verdict.get('segments') or [])
    if len(ref) < LOCAL_MIN_REF_CUES or not cand or not segments:
        return None
    fixed = map_cues_piecewise(
        cand, float(verdict.get('scale') or 1.0), segments)
    if (len(fixed) != len(cand)
            or any(c['end'] <= c['start'] for c in fixed)
            or any(fixed[index]['start'] < fixed[index - 1]['start']
                   for index in range(1, len(fixed)))):
        return None
    before_score = timing_score(ref, cand)
    after_score = timing_score(ref, fixed)
    after_overlap = overlap_rate(ref, fixed, 1.0, 0.0)
    after_unique, median_ms, p95_ms = unique_match_metrics(
        ref, fixed, 1.0, 0.0)
    post_windows = piecewise_local_windows(
        ref, fixed, 1.0, max_offset_ms=max_offset_ms, center_offset=0.0)
    post_core = _micro_core_windows(post_windows)
    post_residual = None
    if post_core:
        values = [item['offset_ms'] for item in post_core]
        post_residual = max(values) - min(values)
    accepted = bool(
        len(post_core) >= _MICRO_MIN_WINDOWS
        and post_residual is not None
        and post_residual <= _MICRO_POST_RESIDUAL_MS
        and after_overlap >= 0.82
        and after_unique >= 0.70
        and p95_ms is not None and p95_ms <= 800.0
        and after_score >= 0.74
        and after_score >= before_score + float(min_gain))
    return {'accepted': accepted, 'before_score': before_score,
            'after_score': after_score, 'after_overlap': after_overlap,
            'after_unique': after_unique, 'unique_median_ms': median_ms,
            'unique_p95_ms': p95_ms, 'post_residual_ms': post_residual,
            'post_core': len(post_core), 'post_windows': len(post_windows)}


def piecewise_plan(ref, cand, scale, min_overlap, max_offset_ms=None,
                    _refined=False):
    max_off = MAXOFF if max_offset_ms is None else max_offset_ms
    center_offset, _votes = best_offset(
        [c['start'] for c in ref], [c['start'] for c in cand], scale, max_off)
    windows = piecewise_local_windows(
        ref, cand, scale, max_offset_ms=max_offset_ms,
        center_offset=center_offset)
    groups, trusted = _group_local_offsets(windows)
    info = {'windows': windows, 'trusted': trusted, 'groups': groups,
            'scale': scale}
    if len(groups) < 2 or len(groups) > _PIECEWISE_MAX_SEGMENTS:
        return None, info

    # A global search across offset steps can bias its chosen FPS ratio.  The
    # within-plateau offset slope isolates that bias; remove the common slope
    # once, then rebuild all windows at the corrected scale.
    if not _refined:
        slopes = []
        for group in groups:
            if len(group) >= 2:
                dx = group[-1]['center_ms'] - group[0]['center_ms']
                if dx:
                    slopes.append((group[-1]['offset_ms']
                                   - group[0]['offset_ms']) / dx)
        if slopes:
            correction = cues_median(slopes)
            if 0.00005 <= abs(correction) <= 0.02:
                return piecewise_plan(
                    ref, cand, scale + correction, min_overlap,
                    max_offset_ms=max_offset_ms, _refined=True)

    offsets = [cues_median([w['offset_ms'] for w in group])
               for group in groups]
    if any(abs(offset) > MAX_PLAUSIBLE_OFFSET_MS for offset in offsets):
        return None, info
    if min(abs(offsets[i + 1] - offsets[i])
           for i in range(len(offsets) - 1)) < PIECEWISE_MIN_STEP_MS - TOL:
        return None, info
    for i, group in enumerate(groups):
        if len(group) >= 2:
            continue
        edge = i in (0, len(groups) - 1)
        w = group[0]
        if not edge or w['vote'] < 0.85 or w['overlap'] < 0.85:
            return None, info

    cuts = optimize_piecewise_boundaries(
        ref, cand, scale, groups, offsets)
    if cuts is None:
        return None, info
    segments = []
    cand_boundaries = []
    ref_boundaries = []
    for cut, left_off, right_off in zip(cuts, offsets, offsets[1:]):
        cand_boundary = ((cand[cut - 1]['end'] + cand[cut]['start']) / 2.0)
        # Boundary on the reference timeline, using each side's own inverse.
        ref_boundary = (((cand[cut - 1]['end'] - left_off) / scale
                         + (cand[cut]['start'] - right_off) / scale) / 2.0)
        cand_boundaries.append(cand_boundary)
        ref_boundaries.append(ref_boundary)
    for i, offset in enumerate(offsets):
        segments.append({
            'cand_from_ms': None if i == 0 else cand_boundaries[i - 1],
            'cand_to_ms': None if i == len(offsets) - 1 else cand_boundaries[i],
            'ref_from_ms': None if i == 0 else ref_boundaries[i - 1],
            'ref_to_ms': None if i == len(offsets) - 1 else ref_boundaries[i],
            'offset_ms': offset,
        })

    fixed = map_cues_piecewise(cand, scale, segments)
    if (len(fixed) != len(cand)
            or any(c['end'] <= c['start'] for c in fixed)
            or any(fixed[i]['start'] < fixed[i - 1]['start']
                   for i in range(1, len(fixed)))):
        return None, info
    post_windows = piecewise_local_windows(
        ref, fixed, 1.0, max_offset_ms=max_offset_ms, center_offset=0.0)
    post_trusted = trusted_local_windows(post_windows)
    post_spread = piecewise_local_spread(post_windows)
    after_overlap = overlap_rate(ref, fixed, 1.0, 0.0)
    after_unique, _unique_median, _unique_p95 = unique_match_metrics(
        ref, fixed, 1.0, 0.0)
    before_score = timing_score(ref, cand)
    after_score = timing_score(ref, fixed)
    # The candidate must become globally/local-consistent and materially better
    # than leaving it untouched.  A successful engine score alone is never an
    # acceptance decision.
    if (after_overlap < max(0.80, min_overlap)
            or after_unique < UNIQUE_MIN
            or len(post_trusted) < max(3, int(0.60 * len(post_windows)))
            or (post_spread is not None and post_spread > 1200)
            or after_score < before_score + 0.05):
        info.update({'post_spread_ms': post_spread,
                     'before_score': before_score,
                     'after_score': after_score,
                     'after_overlap': after_overlap,
                     'after_unique': after_unique})
        return None, info
    info.update({'post_spread_ms': post_spread,
                 'before_score': before_score, 'after_score': after_score,
                 'after_overlap': after_overlap, 'after_unique': after_unique,
                 'scale': scale})
    return segments, info
