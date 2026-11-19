# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Align: verification of a timing (onsets, coverage, envelopes, timeline plateaus).

import bisect
import math
from resources.lib.core.srt.cues import cues_median, preflight_cues, preflight_srt, preflight_unknown, tolerant_reference, dialogue_cues
from resources.lib.sync.align.common import CONFIRM_OFFSET_MS, MAX_PLAUSIBLE_OFFSET_MS, STATUS_CONFIRMED, STATUS_FIXABLE, STATUS_UNKNOWN, ENV_NEAR_MS, LOCAL_MIN_REF_CUES, TIGHT_MIN
from resources.lib.sync.align.estimate import best_offset, breathe


def overlap_rate(ref_cues, cand_cues, a, b):
    """Share of reference cues that overlap SOME candidate cue after mapping
    ref time t -> a*t + b."""
    cand_starts = [c['start'] for c in cand_cues]
    cand_ends = [c['end'] for c in cand_cues]
    ok = 0
    for c in ref_cues:
        es, ee = a * c['start'] + b, a * c['end'] + b
        lo = bisect.bisect_left(cand_ends, es)
        k = lo
        while k < len(cand_cues) and cand_starts[k] < ee:
            if min(ee, cand_ends[k]) - max(es, cand_starts[k]) > 0:
                ok += 1
                break
            k += 1
    return ok / len(ref_cues) if ref_cues else 0.0


# ---- public API -------------------------------------------------------------

def verify(ref_srt_text, cand_srt_text, min_vote=None, min_overlap=None,
           scales=None, max_offset_ms=None, allow_piecewise=True,
           require_tight=True):
    """Verdict dict:
      {'status': CONFIRMED|FIXABLE|UNKNOWN,
       'scale': a, 'offset_ms': b,          # map: cand ~= a*ref + b, i.e. to
                                             # FIX cand apply t' = (t - b) / a
       'vote': 0..1, 'overlap': 0..1, 'diag': str}
    NOTE on direction: estimate() maps REF time onto CAND time. A candidate
    that lags the reference by +12s yields offset_ms=+12000; retime() is then
    called with (scale, offset_ms) and applies the INVERSE map to the
    candidate so it lands on the reference timeline.

    Optional gate overrides (min_vote / min_overlap / scales / max_offset_ms)
    let a caller tune the coarse pre-filters -- e.g. a same-source oracle pins
    scales to identity (same disc master = same framerate) and relaxes the vote
    floor, since cross-language cue segmentation depresses the vote while the
    graduated tight gate still guards correctness."""
    from resources.lib.sync.align.piecewise import snap_piecewise_boundaries
    from resources.lib.sync.align.retime import gate
    ref_all, ref_error = preflight_srt(ref_srt_text)
    if ref_error:
        ref_all = (tolerant_reference(ref_srt_text)
                   if ref_error.startswith(('malformed timing block',
                                            'unparseable timing block'))
                   else None)
        if ref_all is None:
            return preflight_unknown('reference: ' + ref_error)
    cand_all, cand_error = preflight_srt(cand_srt_text, True)
    if cand_error:
        return preflight_unknown('candidate: ' + cand_error)
    return snap_piecewise_boundaries(
        gate(dialogue_cues(ref_all), dialogue_cues(cand_all),
              min_vote=min_vote, min_overlap=min_overlap,
              scales=scales, max_offset_ms=max_offset_ms,
              allow_piecewise=allow_piecewise,
              require_tight=require_tight), cand_all)


# ---- bitmap (PGS / VobSub) display events ---------------------------------
# A bitmap subtitle track's container index lists every display EVENT: the
# instant a caption appears and the instant it is cleared, both as cue starts.
# Read as cues, half of them are the gaps between lines, so a text subtitle's
# onsets also "match" the clear events one caption-length away and the offset
# vote splits.  Measured on a PGS-only Blu-ray rip: vote 51%, 9/15 trusted
# windows for a subtitle whose timing family matched the disc, against 89% and
# 15/15 once the text subtitle is read the same way -- every start AND end an
# event.  A text file's ends sit a little off the disc's clear events
# (-80..-170 ms on a remux whose subtitle is in sync by the audio), so the
# events only find the map; the final offset is measured again on caption
# STARTS alone.
_EVENT_MIN_MS = 600
_EVENT_MAX_MS = 3000
_ONSET_MATCH_MS = 450
_ONSET_MIN_MATCHES = 30
# Measured on a Blu-ray remux whose subtitles are in sync by the audio: 74-77%
# of caption starts land within 450 ms of a display event, and 67-81% of those
# within 150 ms of their median.  A subtitle whose starts are all 600 ms early
# but whose ends match the disc (the events alone call it in sync) reaches
# 41% and 32%.
_ONSET_MIN_FRACTION = 0.55
_ONSET_TIGHT_MS = 150
_ONSET_MIN_TIGHT = 0.50


def bitmap_events(cues):
    """A cue list seen the way a bitmap track's index is read: every start and
    every end is an event, with the synthetic durations SubSync gives index
    starts (next event - 100 ms, clamped to 0.6-3 s)."""
    points = sorted({int(c['start']) for c in cues or []}
                    | {int(c['end']) for c in cues or []})
    out = []
    for i, start in enumerate(points):
        nxt = points[i + 1] if i + 1 < len(points) else start + _EVENT_MAX_MS
        out.append({'start': start,
                    'end': start + max(_EVENT_MIN_MS,
                                       min(_EVENT_MAX_MS, nxt - start - 100))})
    return out


def _onset_offset(ref, cand, scale, offset, lo=None, hi=None,
                  min_matches=_ONSET_MIN_MATCHES):
    """Median residual of caption starts against the nearest display event the
    map predicts, or None when too few starts land near one."""
    starts = [float(r['start']) for r in ref]
    diffs = []
    considered = 0
    for c in cand:
        s = float(c['start'])
        if (lo is not None and s < lo) or (hi is not None and s >= hi):
            continue
        considered += 1
        predicted = (s - offset) / scale
        j = bisect.bisect_left(starts, predicted)
        best = None
        for k in (j - 1, j):
            if 0 <= k < len(starts):
                d = s - (scale * starts[k] + offset)
                if best is None or abs(d) < abs(best):
                    best = d
        if best is not None and abs(best) <= _ONSET_MATCH_MS:
            diffs.append(best)
    if (len(diffs) < min_matches
            or len(diffs) < _ONSET_MIN_FRACTION * considered):
        return None
    diffs.sort()
    median = diffs[len(diffs) // 2]
    tight = sum(1 for d in diffs if abs(d - median) <= _ONSET_TIGHT_MS)
    if tight < _ONSET_MIN_TIGHT * len(diffs):
        return None
    return offset + median


def _refine_on_onsets(ref, cand, verdict):
    """Re-measure an events verdict's offset on caption starts (see above).
    A verdict the starts do not support is refused: the events alone can read
    a subtitle whose every start is wrong as in sync, if its ends are right."""
    status = verdict.get('status')
    if status not in (STATUS_CONFIRMED, STATUS_FIXABLE):
        return verdict
    a = float(verdict.get('scale') or 1.0)
    b = float(verdict.get('offset_ms') or 0.0)
    out = dict(verdict)

    def refuse(reason):
        return dict(verdict, status=STATUS_UNKNOWN, reason=reason,
                    diag=(verdict.get('diag') or '') + ' [bitmap events: %s]' % reason)

    segments = verdict.get('segments')
    if segments:
        fixed = []
        for i, seg in enumerate(segments):
            lo = seg.get('cand_from_ms')
            hi = segments[i + 1].get('cand_from_ms') if i + 1 < len(segments) else None
            seg_b = float(seg.get('offset_ms') or 0.0)
            refined = _onset_offset(ref, cand, a, seg_b, lo, hi, min_matches=8)
            if refined is None:
                return refuse('bitmap_onsets_unmatched')
            if abs(refined - seg_b) > _ONSET_MATCH_MS:
                return refuse('bitmap_onsets_disagree')
            fixed.append(dict(seg, offset_ms=refined))
        out['segments'] = fixed
        out['offset_ms'] = float(fixed[0].get('offset_ms') or 0.0)
        return out
    refined = _onset_offset(ref, cand, a, b)
    if refined is None:
        return refuse('bitmap_onsets_unmatched')
    if abs(refined - b) > _ONSET_MATCH_MS:
        return refuse('bitmap_onsets_disagree')
    out['offset_ms'] = refined
    out['diag'] = (verdict.get('diag') or '') + ' [bitmap events; onset offset %+dms]' % int(refined)
    if status == STATUS_CONFIRMED and abs(refined) > CONFIRM_OFFSET_MS:
        # The starts say the subtitle is off by more than the in-sync band
        # that the events allowed.  Neither confirm nor fix on a shift no
        # gate has checked.
        out['status'] = STATUS_UNKNOWN
        out['reason'] = 'bitmap_onsets_disagree'
    elif status == STATUS_FIXABLE and a == 1.0 and abs(refined) <= CONFIRM_OFFSET_MS:
        out['status'] = STATUS_CONFIRMED
        out.pop('mode', None)
    return out


def verify_cues(ref_cues, cand_srt_text, min_vote=None, min_overlap=None,
                scales=None, max_offset_ms=None, allow_piecewise=True,
                require_tight=True, ref_events=False):
    """verify() variant whose reference is a raw cue list (start/end ms) --
    e.g. embedded-track timestamps from the mkv_probe container probe, where
    there is no text to filter. Same gate, same verdict shape. min_vote /
    min_overlap override the gate thresholds; `scales` restricts the scale
    candidates and `max_offset_ms` the offset window (MANDATORY discipline
    for sparse audio-VAD references -- see _gate notes). `ref_events` says
    the reference is a bitmap track's display events (see bitmap_events)."""
    from resources.lib.sync.align.piecewise import snap_piecewise_boundaries
    from resources.lib.sync.align.retime import gate
    ref, ref_error = preflight_cues(ref_cues)
    if ref_error:
        return preflight_unknown('reference: ' + ref_error)
    cand_all, cand_error = preflight_srt(cand_srt_text, True)
    if cand_error:
        return preflight_unknown('candidate: ' + cand_error)
    cand = dialogue_cues(cand_all)
    if ref_events:
        verdict = _refine_on_onsets(ref, cand, gate(
            ref, bitmap_events(cand),
            min_vote=min_vote, min_overlap=min_overlap,
            scales=scales, max_offset_ms=max_offset_ms,
            allow_piecewise=allow_piecewise,
            require_tight=require_tight))
        return snap_piecewise_boundaries(verdict, cand_all)
    return snap_piecewise_boundaries(
        gate(ref, cand,
              min_vote=min_vote, min_overlap=min_overlap,
              scales=scales, max_offset_ms=max_offset_ms,
              allow_piecewise=allow_piecewise,
              require_tight=require_tight), cand_all)


def verify_cue_lists(ref_cues, cand_cues, min_vote=None, min_overlap=None,
                     scales=None, max_offset_ms=None, allow_piecewise=True,
                     require_tight=True):
    """Timestamp-only variant for internal pipelines that already parsed SRT.

    The returned piecewise segments include both reference- and candidate-time
    boundaries, so a caller such as the gender helper can map source cues onto
    a differently-cut human reference without rewriting either file.
    """
    from resources.lib.sync.align.retime import gate
    ref, ref_error = preflight_cues(ref_cues)
    if ref_error:
        return preflight_unknown('reference: ' + ref_error)
    cand, cand_error = preflight_cues(cand_cues)
    if cand_error:
        return preflight_unknown('candidate: ' + cand_error)
    return gate(ref, cand, min_vote=min_vote, min_overlap=min_overlap,
                 scales=scales, max_offset_ms=max_offset_ms,
                 allow_piecewise=allow_piecewise,
                 require_tight=require_tight)
# Sparse references (< this many cues) additionally require most ref cues to
# agree at a TIGHT tolerance -- random matches at +/-350ms are rare, so this
# kills spurious peaks that survive the coarse 500ms bins.
_SPARSE_REF = 40
_TIGHT_MS = 450     # genuine sub-vs-speech onsets land within ~0.4s; random
_UNIQUE_MS = 800


def tight_agreement(ref, cand, a, b):
    cand_on = sorted(c['start'] for c in cand)
    ok = 0
    for c in ref:
        pe = a * c['start'] + b
        i = bisect.bisect_left(cand_on, pe)
        for j in (i - 1, i):
            if 0 <= j < len(cand_on) and abs(cand_on[j] - pe) <= _TIGHT_MS:
                ok += 1
                break
    return ok / len(ref) if ref else 0.0


def unique_match_metrics(ref, cand, a, b, tolerance=_UNIQUE_MS):
    """Monotonic one-to-one onset support for a proposed time map.

    Histogram vote and overlap deliberately allow different cue segmentation,
    but that also means one dense candidate cue can appear to support several
    reference cues.  This second measure permits each candidate onset once and
    preserves order.  It is an acceptance check, never a source of a map.
    """
    cand_on = [float(c['start']) for c in cand]
    last = -1
    residuals = []
    for cue in ref:
        target = a * float(cue['start']) + b
        pos = bisect.bisect_left(cand_on, target, lo=last + 1)
        options = [index for index in (pos - 1, pos)
                   if last < index < len(cand_on)
                   and abs(cand_on[index] - target) <= tolerance]
        if not options:
            continue
        chosen = min(options, key=lambda index: abs(cand_on[index] - target))
        last = chosen
        residuals.append(abs(cand_on[chosen] - target))
    denominator = max(1, min(len(ref), len(cand)))
    coverage = len(residuals) / denominator
    if not residuals:
        return coverage, None, None
    ordered = sorted(residuals)
    return (coverage, cues_median(ordered),
            float(ordered[int(0.95 * (len(ordered) - 1))]))


def required_tight(offset_ms, n_ref, vote=1.0, overlap=1.0):
    """Minimum tight-agreement needed to APPLY a shift of `offset_ms`.

    A genuine constant offset makes almost every reference cue land within
    _TIGHT_MS of a candidate cue (tight ~0.9+). A SPURIOUS peak from a sparse or
    heterogeneous reference only reaches ~0.65-0.72. Since a wrong shift is far
    worse than no shift, the requirement RISES with the size of the jump: a
    borderline match may nudge a sub by a fraction of a second, but can never
    move it many seconds on thin evidence (field: a 31-cue file-probe union
    voted -20.3s at 68% tight and de-synced an already-good sub). Sparse
    references are noisier, so they're held a notch stricter.

    SMALL shifts are the exception. A sub-1.5s correction is low-harm, and real
    subtitles from a different subber/translator segment their lines
    differently, so even at the CORRECT offset the tight agreement caps around
    ~45% (field: The Flash Pilot, a real ~1s-early Hebrew pool sub scored 45%
    tight against every BluRay oracle). When the coarse signals still
    corroborate strongly -- high overlap -- we accept that low tight for a small
    shift. This can't de-sync an already-good sub: the estimate only picks a
    small NON-ZERO offset when the sub is genuinely off; a synced sub peaks at 0
    and returns CONFIRMED before reaching here."""
    a = abs(offset_ms)
    if a <= 1500:
        # A small shift is low-harm and lives in a COMPLETELY different regime
        # from the spurious matches we must reject -- those are always LARGE
        # (field garbage: +230s, +560s, -20s), caught by the magnitude-scaled
        # bars below. The reliable discriminator for a small shift is OVERLAP:
        # the shifted sub must cover the same time regions as the reference. The
        # `vote` and `tight` metrics are dominated by different-subber cue
        # segmentation and cap low even at the CORRECT offset -- across four
        # field subs the correct small offset ran vote 54-64%, overlap 87-90%,
        # tight 40-45% (all were being rejected). So when the overlap
        # corroborates strongly we drop the tight floor and lean on overlap +
        # magnitude. This cannot de-sync a good sub: the estimate only picks a
        # small NON-ZERO offset when the sub is genuinely off (a synced sub
        # peaks at 0 -> CONFIRMED before reaching here); vote is still floored by
        # the gate's min_vote so a true non-match can't sneak through.
        need = 0.35 if overlap >= 0.85 else TIGHT_MIN
    elif a <= 6000:
        need = 0.78
    elif a <= 15000:
        need = 0.85
    else:
        need = 0.90                # a multi-second jump must be near-certain
    if n_ref < _SPARSE_REF:
        need = min(0.93, need + 0.05)
    return need


# ---- speech-coverage correlation (segmentation-robust corroboration) -------
# Onset agreement ("tight") asks whether two files START their lines at the
# same instants.  Two different subbers/languages split the same speech into
# different lines, so even an exactly correct offset caps around 40-65% tight
# in the field, while the magnitude-scaled requirement for a multi-second shift
# is 78-90%.  A real desync of several seconds therefore could never be fixed:
# only small (<=1.5s) nudges ever reached a user.  What two authorings of one
# dialogue DO share is the time covered by speech.  The overlap of the two
# coverage masks as a function of offset -- the subtitle analogue of
# ffsubsync's speech correlation -- has one sharp peak at the true offset for a
# genuine pair and stays flat for an unrelated one; merging or splitting lines
# barely moves it.  It never proposes a map: it can only corroborate (or
# contradict) the offset that the histogram estimator already found.
_COV_STEP_MS = 20
_COV_MIN_WINDOW_MS = 60000
_COV_MARGIN_MS = 60000
_COV_PAD_MS = 70000          # integration headroom so no trapezoid is clipped
_COV_MAX_CUE_MS = 60000      # pathological cues never anchor coverage
_COV_MAX_SPAN_MS = 120000    # merged back-to-back blocks longer than this
                             # carry no usable gap structure
_COV_PLATEAU = 0.015         # peak plateau (longer lines flatten the top)
_COV_AGREE_MS = 300          # estimator offset must sit on/near the plateau
_COV_EXCLUDE_MS = 6000       # a competing peak must be clearly elsewhere
_COV_MIN_PEAK = 0.65
_COV_MIN_PROMINENCE = 0.15
_COV_MIN_REL_PROMINENCE = 0.45
_COV_MAX_SECOND = 0.60


_RESCUE_WINDOW_VOTE = 0.50
_RESCUE_WINDOW_RANGE_MS = 1500
_EDGE_VOTE = 0.50
_EDGE_RADIUS_MS = 20000


def edge_windows(ref, cand, scale, offset):
    """Short probes of the very start and end of the reference timeline.

    Recaps, cold opens, logos and end credits are where releases most often
    differ, and such a region (a few minutes) is smaller than one ordinary
    local window, whose estimate it merely dilutes.
    """
    size = max(12, len(ref) // 30)
    if len(ref) < 4 * size:
        return []
    cand_on = [c['start'] - offset for c in cand]
    out = []
    for subset in (ref[:size], ref[-size:]):
        # Search NEAR the global offset only: a short probe searched over
        # +/-150 s finds dense-dialogue aliases (field-like fixtures produced
        # a spurious 139 s "region"); recap/logo differences are seconds.
        found, votes = best_offset([c['start'] for c in subset], cand_on,
                                    scale, _EDGE_RADIUS_MS)
        out.append({'offset_ms': float(found) + offset,
                    'vote': votes / float(len(subset)), 'edge': True})
    return out


def rescue_regions_agree(windows, offset, edges=()):
    """Every moderately supported local region must sit near ``offset``.

    The ordinary local-consistency guard counts only windows with a strong
    (>=65%) vote, which different-subber pairs rarely reach near a region
    boundary, so a minority region on another clock can go unseen. A rescued
    shift therefore needs most windows to carry at least moderate support,
    and all of those -- plus the short start/end probes -- to agree with the
    global offset.
    """
    if not windows:
        return False, '; regions n/a'
    for edge in edges or ():
        if (edge.get('vote', 0.0) >= _EDGE_VOTE
                and abs(float(edge['offset_ms']) - float(offset))
                > _RESCUE_WINDOW_RANGE_MS):
            return False, '; edge region is %dms away' % int(
                abs(float(edge['offset_ms']) - float(offset)))
    supported = [w for w in windows if w.get('vote', 0.0) >= _RESCUE_WINDOW_VOTE]
    needed = max(3, int(math.ceil(0.5 * len(windows))))
    if len(supported) < needed:
        return False, '; regions %d/%d supported' % (len(supported),
                                                     len(windows))
    worst = max(abs(float(w['offset_ms']) - float(offset)) for w in supported)
    if worst > _RESCUE_WINDOW_RANGE_MS:
        return False, '; a supported region is %dms away' % int(worst)
    return True, ''


def _coverage_union(cues, scale=1.0, offset=0.0):
    spans = sorted((scale * float(c['start']) + offset,
                    scale * float(c['end']) + offset) for c in cues
                   if float(c['end']) - float(c['start']) <= _COV_MAX_CUE_MS)
    out = []
    for start, end in spans:
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1][1] = end
        else:
            out.append([start, end])
    return out


def coverage_profile(ref, cand, scale=1.0, offset_hint=0.0):
    """Corroborate a proposed map with speech-coverage cross-correlation.

    C(d) = |scale*ref_coverage  intersect  (cand_coverage - d)| / min(total).
    Every pair of covered intervals contributes a trapezoid in d, accumulated
    exactly through second differences on a 20 ms grid, so the cost is linear
    in the number of nearby interval pairs (no FFT, stdlib only).

    Returns metrics plus ``corroborates``: the estimator's offset lies on the
    single dominant peak and that peak clearly stands above the background.
    """
    hint = float(offset_hint or 0.0)
    window = min(MAX_PLAUSIBLE_OFFSET_MS + _COV_MARGIN_MS,
                 max(_COV_MIN_WINDOW_MS, abs(hint) + _COV_MARGIN_MS))
    step = _COV_STEP_MS
    a_spans = _coverage_union(ref, scale)
    b_spans = _coverage_union(cand)
    a_total = sum(e - s for s, e in a_spans)
    b_total = sum(e - s for s, e in b_spans)
    # 'agrees' None = no information: never a reason to veto anything.
    result = {'corroborates': False, 'agrees': None, 'offset_ms': None,
              'peak': 0.0, 'median': 0.0, 'prominence': 0.0,
              'rel_prominence': 0.0, 'second': 1.0, 'diag': 'coverage n/a'}
    if not a_spans or not b_spans or a_total <= 0 or b_total <= 0:
        return result
    longest = max(max(e - s for s, e in a_spans),
                  max(e - s for s, e in b_spans))
    if longest > _COV_MAX_SPAN_MS:
        # Back-to-back cues merged into minutes-long blocks (continuous
        # captions / auto-generated timing) carry no gap structure to align.
        result['diag'] = 'coverage n/a (continuous %ds block)' % int(
            longest / 1000)
        return result
    # Every interval pair's trapezoid spans at most la+lb beyond the search
    # window on either side; pad so no breakpoint can fall off the grid.
    pad = max(_COV_PAD_MS, 2 * longest + 2000)
    full = window + pad
    bins = int(2 * full / step) + 3
    d2 = [0.0] * (bins + 2)
    b_starts = [s for s, _ in b_spans]

    def add(position, weight):
        k = int(round((position + full) / step))
        if 0 <= k <= bins:
            d2[k] += weight

    for a1, a2 in a_spans:
        la = a2 - a1
        j = bisect.bisect_left(b_starts, a1 - window - longest)
        while j < len(b_spans):
            b1, b2 = b_spans[j]
            j += 1
            if b1 - a2 > window:
                break
            if b2 - a1 < -window:
                continue
            m = min(la, b2 - b1)
            d0 = b1 - a2
            d3 = b2 - a1
            add(d0, 1.0)
            add(d0 + m, -1.0)
            add(d3 - m, -1.0)
            add(d3, 1.0)
    slope = 0.0
    value = 0.0
    norm = float(min(a_total, b_total))
    first = int(round(pad / step))
    last = first + int(2 * window / step)
    curve = []
    for k in range(last + 1):
        # f(k) = f(k-1) + step * slope(k-1): a breakpoint at bin k changes the
        # slope only AFTER that bin (updating first biased the peak 1 step).
        value += slope * step
        slope += d2[k]
        if k >= first:
            curve.append(value / norm)
    if not curve:
        return result
    top = max(range(len(curve)), key=curve.__getitem__)
    peak = curve[top]
    lo = top
    while lo > 0 and curve[lo - 1] >= peak - _COV_PLATEAU:
        lo -= 1
    hi = top
    while hi + 1 < len(curve) and curve[hi + 1] >= peak - _COV_PLATEAU:
        hi += 1

    def at(index):
        return index * step - window

    exclude = int(_COV_EXCLUDE_MS / step)
    outside = [curve[i] for i in range(len(curve))
               if i < lo - exclude or i > hi + exclude]
    ordered = sorted(curve)
    median = ordered[len(ordered) // 2]
    prominence = peak - median
    rel_prominence = prominence / max(0.05, 1.0 - median)
    second = ((max(outside) - median) / prominence
              if outside and prominence > 1e-9 else 1.0)
    plateau_lo, plateau_hi = at(lo), at(hi)
    agrees = (plateau_lo - _COV_AGREE_MS <= hint
              <= plateau_hi + _COV_AGREE_MS)
    corroborates = bool(agrees and peak >= _COV_MIN_PEAK
                        and prominence >= _COV_MIN_PROMINENCE
                        and rel_prominence >= _COV_MIN_REL_PROMINENCE
                        and second <= _COV_MAX_SECOND)
    result.update({
        'corroborates': corroborates, 'agrees': agrees,
        'offset_ms': float(at(top)), 'plateau_ms': (plateau_lo, plateau_hi),
        'peak': peak, 'median': median, 'prominence': prominence,
        'rel_prominence': rel_prominence, 'second': second,
        'diag': ('coverage=%+d[%+d..%+d]ms peak=%.0f%% bg=%.0f%% '
                 'rel=%.0f%% 2nd=%.0f%%%s' % (
                     int(at(top)), int(plateau_lo), int(plateau_hi),
                     peak * 100, median * 100, rel_prominence * 100,
                     second * 100, '' if corroborates else ' (no support)')),
    })
    return result


def nearest_onset_distance(onsets, value, cap=2500.0):
    pos = bisect.bisect_left(onsets, value)
    values = [abs(onsets[j] - value)
              for j in (pos - 1, pos) if 0 <= j < len(onsets)]
    return min(min(values) if values else cap, cap)


def timing_score(ref, cand):
    return (0.60 * overlap_rate(ref, cand, 1.0, 0.0)
            + 0.40 * tight_agreement(ref, cand, 1.0, 0.0))


_ENV_WINDOW_MS = 240000
_ENV_MAX_WINDOWS = 20
_ENV_LAG_MS = 20000
_ENV_EDGE_MS = 30000
_ENV_MIN_Z = 3.5
_ENV_POLARITY_MARGIN = 0.6
_ENV_SYNC_MS = 500.0
_ENV_SHIFT_MS = 1000.0
_ENV_STAIR_MS = 3000.0


def _envelope_scores(prefix, size, spans, lags):
    scores = []
    for lag in lags:
        breathe()
        total = 0.0
        for begin, end in spans:
            a = begin - lag
            b = end - lag
            if a < 0:
                a = 0
            if b > size:
                b = size
            if b > a:
                total += prefix[b] - prefix[a]
        scores.append(total)
    return scores


def envelope_windows(envelope, cand_srt_text):
    """Per-window offsets of a subtitle against an audio frame-size envelope.

    Each window slides the subtitle's dialogue intervals over the envelope by
    up to +/-20 s and scores the summed envelope under them (prefix sums: one
    subtraction per cue per lag).  The windows tile the whole subtitle span
    without gaps -- at least four minutes each, at most twenty of them -- so
    no act can hide between samples.  The search runs on a 200-ms grid and
    is refined around each extreme.  Speech raises the bitrate with some
    encoders and lowers it with others, so both extremes are kept; the caller
    picks the polarity that the whole file supports.  Offsets follow the
    module convention: positive means the subtitle is late.
    """
    try:
        step = float(envelope.get('step_ms') or 0)
        values = list(envelope.get('values') or [])
    except AttributeError:
        return []
    if step <= 0 or len(values) < 1000:
        return []
    cand_all, cand_error = preflight_srt(cand_srt_text, True)
    if cand_error:
        return []
    cand = dialogue_cues(cand_all)
    if len(cand) < LOCAL_MIN_REF_CUES:
        return []
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    size = len(values)
    first = float(cand[0]['start']) + _ENV_EDGE_MS
    last = min(float(cand[-1]['end']), size * step) - _ENV_EDGE_MS
    span = last - first
    if span < _ENV_WINDOW_MS:
        return []
    count = max(1, min(_ENV_MAX_WINDOWS, int(span // _ENV_WINDOW_MS)))
    width = span / float(count)
    lag_bins = int(_ENV_LAG_MS / step)
    coarse = list(range(-lag_bins, lag_bins + 1, 2))
    starts = [c['start'] for c in cand]
    rows = []
    for index in range(count):
        low = first + index * width
        high = low + width
        lo = bisect.bisect_left(starts, low)
        hi = bisect.bisect_left(starts, high)
        spans = [(int(c['start'] / step), int(c['end'] / step))
                 for c in cand[lo:hi] if c['end'] > c['start']]
        if len(spans) < 15:
            continue
        scores = _envelope_scores(prefix, size, spans, coarse)
        ordered = sorted(scores)
        middle = ordered[len(ordered) // 2]
        mean = sum(scores) / float(len(scores))
        spread = math.sqrt(sum((x - mean) ** 2 for x in scores)
                           / float(len(scores))) or 1e-9

        def refine(position, sign):
            lag = coarse[position]
            best_lag, best_value = lag, scores[position]
            for near in (lag - 1, lag + 1):
                if -lag_bins <= near <= lag_bins:
                    value = _envelope_scores(prefix, size, spans, [near])[0]
                    if sign * value > sign * best_value:
                        best_lag, best_value = near, value
            return best_lag, best_value
        hi_lag, hi_value = refine(
            max(range(len(scores)), key=scores.__getitem__), 1)
        lo_lag, lo_value = refine(
            min(range(len(scores)), key=scores.__getitem__), -1)
        rows.append({
            'center_ms': (low + high) / 2.0,
            'pos': (hi_lag * step, (hi_value - middle) / spread),
            'neg': (lo_lag * step, (middle - lo_value) / spread),
        })
    return rows


def _envelope_staircase(ordered):
    """Monotonic drift of strong window offsets that no single window makes.

    A real run of act-break pads moves every later window the same way.  One
    aliased window can fake a 'staircase' on its own, so the pattern must
    survive the removal of any one window.
    """
    def rising_or_falling(seq):
        if len(seq) < 4 or max(seq) - min(seq) < _ENV_STAIR_MS:
            return False
        drops = sum(1 for a, b in zip(seq, seq[1:]) if b < a - 300)
        climbs = sum(1 for a, b in zip(seq, seq[1:]) if b > a + 300)
        return min(drops, climbs) <= 1
    if len(ordered) < 5 or not rising_or_falling(ordered):
        return False
    return all(rising_or_falling(ordered[:i] + ordered[i + 1:])
               for i in range(len(ordered)))


def envelope_check(envelope, cand_srt_text, polarity=None):
    """Judge a subtitle against the playing file's audio envelope.

    ``status`` is 'synced' (one plateau within 0.5 s), 'near' (one plateau
    within 0.9 s), 'conflict' (sustained plateaus >=1.5 s apart, or a
    monotonic staircase of strong windows spanning >=3 s that survives the
    removal of any one window -- no single clock fits), 'shifted' (one
    plateau >=1 s away) or 'unknown'.  Windows count only when their peak is
    strong (z >= 3.5) and a plateau needs two of them, so one aliased window
    decides nothing.  A file whose two polarities score alike is 'unknown'.
    The envelope's absolute offset carries an encoder-dependent bias of a
    few hundred ms: it can tell timelines apart, but it never fine-tunes one,
    and a constant bias can never produce a 'conflict'.
    """
    rows = envelope_windows(envelope, cand_srt_text)
    result = {'status': 'unknown', 'windows': len(rows), 'trusted': 0,
              'plateaus': [], 'polarity': 0}
    if len(rows) < 4:
        return result
    medians = {}
    for key in ('pos', 'neg'):
        zs = sorted(row[key][1] for row in rows)
        medians[key] = zs[len(zs) // 2]
    if polarity in (1, -1):
        key = 'pos' if polarity > 0 else 'neg'
    else:
        key = max(medians, key=medians.get)
        other = 'neg' if key == 'pos' else 'pos'
        if medians[key] - medians[other] < _ENV_POLARITY_MARGIN:
            result['reason'] = 'ambiguous polarity'
            return result
    result['polarity'] = 1 if key == 'pos' else -1
    result['median_z'] = medians[key]
    ordered = [row[key][0] for row in rows if row[key][1] >= _ENV_MIN_Z]
    trusted = sorted(ordered)
    result['trusted'] = len(trusted)
    result['offsets'] = [(int(row['center_ms']), int(row[key][0]),
                          round(row[key][1], 1)) for row in rows]
    if len(trusted) < max(4, int(math.ceil(0.6 * len(rows)))):
        result['reason'] = 'weak envelope'
        return result
    clusters = []
    for value in trusted:
        if clusters and value - clusters[-1][-1] <= _PLATEAU_JOIN_MS:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    plateaus = sorted(((cues_median(c), len(c)) for c in clusters if len(c) >= 2),
                      key=lambda item: -item[1])
    result['plateaus'] = plateaus
    if _envelope_staircase(ordered):
        result['status'] = 'conflict'
        result['reason'] = 'staircase'
        return result
    if not plateaus:
        return result
    values_ = [offset for offset, _count in plateaus]
    if len(plateaus) >= 2 and max(values_) - min(values_) >= _PLATEAU_CONFLICT_MS:
        result['status'] = 'conflict'
        return result
    main_offset, main_count = plateaus[0]
    if main_count < 0.8 * len(trusted):
        return result
    result['offset_ms'] = main_offset
    # One strong window elsewhere is not enough to call a conflict (it may be
    # an alias), but it is enough to withhold agreement: a single act cut
    # differently looks exactly like that.
    if any(abs(value - main_offset) > 1000.0 for value in trusted):
        result['reason'] = 'deviating window'
        return result
    if abs(main_offset) <= _ENV_SYNC_MS:
        result['status'] = 'synced'
    elif abs(main_offset) <= ENV_NEAR_MS:
        result['status'] = 'near'
    elif abs(main_offset) >= _ENV_SHIFT_MS:
        result['status'] = 'shifted'
    return result


_PLATEAU_JOIN_MS = 700.0
_PLATEAU_CONFLICT_MS = 1500.0


def timeline_plateaus(ref_cues, cand_srt_text):
    """Sustained identity-clock offset plateaus of a subtitle on a reference.

    Each trusted local window votes for the offset its own stretch of the
    timeline needs.  Offsets are clustered; only clusters backed by at least
    two trusted windows count, so one aliased window never forms a plateau.
    Returns ``[(median_offset_ms, windows), ...]`` strongest first, or ``[]``
    when the reference cannot tell.
    """
    from resources.lib.sync.align.piecewise import piecewise_local_windows, trusted_local_windows
    try:
        ref, ref_error = preflight_cues(ref_cues)
        cand_all, cand_error = preflight_srt(cand_srt_text, True)
        if ref_error or cand_error:
            return []
        cand = dialogue_cues(cand_all)
        if len(ref) < LOCAL_MIN_REF_CUES or len(cand) < LOCAL_MIN_REF_CUES:
            return []
        center, _votes = best_offset(
            [c['start'] for c in ref], [c['start'] for c in cand], 1.0)
        windows = piecewise_local_windows(ref, cand, 1.0, center_offset=center)
        offsets = sorted(w['offset_ms'] for w in trusted_local_windows(windows))
        clusters = []
        for value in offsets:
            if clusters and value - clusters[-1][-1] <= _PLATEAU_JOIN_MS:
                clusters[-1].append(value)
            else:
                clusters.append([value])
        return sorted(((cues_median(c), len(c)) for c in clusters if len(c) >= 2),
                      key=lambda item: -item[1])
    except Exception:
        return []


def timeline_conflict(ref_cues, cand_srt_text):
    """Two sustained plateaus >=1.5 s apart: no single clock fits the text."""
    plateaus = timeline_plateaus(ref_cues, cand_srt_text)
    if len(plateaus) < 2:
        return None
    values = [offset for offset, _count in plateaus]
    if max(values) - min(values) < _PLATEAU_CONFLICT_MS:
        return None
    return plateaus
