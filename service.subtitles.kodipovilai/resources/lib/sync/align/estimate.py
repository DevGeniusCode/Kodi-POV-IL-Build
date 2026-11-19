# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Align: the global offset and scale estimate between a subtitle and a reference.

import bisect
import collections
import time
from resources.lib.core.srt.cues import _median
from resources.lib.sync.align.common import SCALE_MAX, SCALE_MIN, _LOCAL_MIN_REF_CUES, _MAXOFF, _TOL


# ---- gate thresholds (mirroring arabic_gender's production values) --------
_FPS = [24000 / 1001, 24.0, 25.0, 30000 / 1001, 30.0]
SCALES = sorted({1.0} | {round(p / q, 6) for p in _FPS for q in _FPS
                         if 0.9 <= p / q <= 1.11})
                      # a wider peak would be refused anyway, and the
                      # old +/-10 min window cost 2.4x the CPU
_SAMPLE = 500         # cap sampled reference cues
_IDENTITY_SHORTCUT_VOTE = 0.80   # identity this strong: no FPS search


# ---- linear time-map estimation (from arabic_gender, generalized) ----------

# Kodi runs every add-on's Python under one interpreter lock. Measured in QA
# Kodi (21, Python 3.11): while one thread runs a CPU-bound loop that never
# sleeps, a NEW script -- e.g. our own "בחירת כתוביות" button -- does not even
# start until that loop ends (15.8 s behind a 20 s loop, 0.3 s idle); a 1 ms
# sleep every ~20 ms brings it back to 0.3 s. Field logs from an Android box
# showed the same 5-11 s waits behind SubSync's deep check at playback start.
# The long loops here call _breathe(): about 5% slower in the background,
# never in the way of the viewer.
_BREATHE_SLICE = 0.02
_breath = [0.0]


# subsync sets this to its deep-job yield (a no-op on any other thread).
YIELD = None


def _breathe():
    now = time.monotonic()
    if now - _breath[0] >= _BREATHE_SLICE:
        time.sleep(0.001)
        _breath[0] = time.monotonic()
        if YIELD is not None:
            YIELD()


def _best_offset(ref_on, cand_on, a, max_off=_MAXOFF):
    step = max(1, len(ref_on) // _SAMPLE)
    sampled = ref_on[::step]
    hist = collections.Counter()
    left, right = bisect.bisect_left, bisect.bisect_right
    tol = float(_TOL)
    for e in sampled:
        _breathe()
        pe = a * e
        lo = left(cand_on, pe - max_off)
        hi = right(cand_on, pe + max_off)
        if lo >= hi:
            continue
        # ONE vote per reference cue per bin. Without the dedupe, a DENSE
        # candidate (600 cues) inflated bins with multiple hits per ref cue,
        # so a sparse 10-cue reference could report vote=120% on a spurious
        # alignment (seen in the field: offset=-350s applied, subs vanished).
        # (Set comprehension + Counter: same bins, a fraction of the
        # interpreter work -- this loop dominates SubSync's CPU time.)
        hist.update({round((c - pe) / tol) for c in cand_on[lo:hi]})
    if not hist:
        return 0.0, 0
    peak = max(hist, key=lambda k: hist[k] + hist.get(k - 1, 0)
               + hist.get(k + 1, 0))
    votes = min(hist.get(peak - 1, 0) + hist.get(peak, 0)
                + hist.get(peak + 1, 0),
                len(sampled))
    # REFINE: the histogram bin is 500ms, so peak*_TOL is only accurate to
    # +/-250ms -- enough to over/under-shoot a real fix by up to half a second
    # (field: a +1500ms bin fix left subs ~0.5s early). Take the MEDIAN of the
    # actual per-ref-cue deltas closest to the peak center -> sub-50ms offset.
    center = peak * _TOL
    deltas = []
    for e in sampled:
        pe = a * e
        i = bisect.bisect_left(cand_on, pe + center)
        best_d = None
        for j in (i - 1, i):
            if 0 <= j < len(cand_on):
                d = cand_on[j] - pe
                if abs(d - center) <= 1.5 * _TOL and (
                        best_d is None or abs(d - center) < abs(best_d - center)):
                    best_d = d
        if best_d is not None:
            deltas.append(best_d)
    if deltas:
        deltas.sort()
        refined = float(deltas[len(deltas) // 2])
    else:
        refined = float(center)
    return refined, votes


def _data_scale_candidates(ref_cues, cand_cues):
    """A few robust, timing-only scale proposals for non-standard drift.

    Cue-count quantiles are only proposal generators; they never prove an
    alignment.  Consecutive quantile slopes make an inserted scene affect one
    interval rather than bias the whole-file ratio, while their median recovers
    a continuous arbitrary clock drift.  Every proposal still has to win the
    histogram and pass unique/global/local/post-transform gates.
    """
    if len(ref_cues) < _LOCAL_MIN_REF_CUES or len(cand_cues) < _LOCAL_MIN_REF_CUES:
        return []
    fractions = [i / 10.0 for i in range(1, 10)]

    def points(cues):
        last = len(cues) - 1
        return [float(cues[int(round(fraction * last))]['start'])
                for fraction in fractions]

    ref_points = points(ref_cues)
    cand_points = points(cand_cues)
    local_slopes = []
    for i in range(1, len(ref_points)):
        dx = ref_points[i] - ref_points[i - 1]
        if dx > 0:
            local_slopes.append(
                (cand_points[i] - cand_points[i - 1]) / dx)
    pair_slopes = []
    for i in range(len(ref_points)):
        for j in range(i + 1, len(ref_points)):
            dx = ref_points[j] - ref_points[i]
            if dx > 0:
                pair_slopes.append((cand_points[j] - cand_points[i]) / dx)
    proposals = []
    for values in (local_slopes, pair_slopes):
        if values:
            proposal = round(_median(values), 6)
            if SCALE_MIN <= proposal <= SCALE_MAX:
                proposals.append(proposal)
    return list(dict.fromkeys(proposals))


def _adaptive_scale_candidates(ref_cues, cand_cues, max_offset_ms=None):
    """Recover a non-standard continuous clock ratio after the cheap grid fails.

    Quantile ratios are fast, but inserted/deleted cues move the quantile
    indices and can make both proposals miss a perfectly linear 1.01/1.03
    clock.  NG's research detector demonstrated that a broad scale sweep closes
    that hole.  Its exhaustive full-density sweep is too costly for 32-bit Kodi,
    so this production version searches a bounded, span-derived band on a
    deterministic sample, then returns only the strongest scales for the normal
    FULL gate. It is proposal-only: it cannot accept anything by itself.
    """
    if len(ref_cues) < _LOCAL_MIN_REF_CUES or len(cand_cues) < _LOCAL_MIN_REF_CUES:
        return []

    def sample(values, cap):
        if len(values) <= cap:
            return values
        if cap <= 1:
            return values[:1]
        last = len(values) - 1
        return [values[int(round(i * last / (cap - 1)))] for i in range(cap)]

    # Keep candidate onsets dense: every sampled reference cue then retains its
    # actual counterpart unless that cue was genuinely deleted. Sampling the
    # candidate down to a small independent index grid would create an
    # artificial clock ratio; pairing matching indices also fails as soon as
    # real subtitles insert/drop cues. 2,400 covers even dialogue-heavy films
    # while bounding a pathological file.
    ref_on = sample([float(c['start']) for c in ref_cues], 120)
    cand_on = sample([float(c['start']) for c in cand_cues], 2400)
    max_off = _MAXOFF if max_offset_ms is None else max_offset_ms
    scored = {}

    def scan(scales):
        for value in scales:
            scale = round(float(value), 6)
            if scale in scored or not (SCALE_MIN <= scale <= SCALE_MAX):
                continue
            offset, votes = _best_offset(ref_on, cand_on, scale, max_off)
            scored[scale] = (votes, offset)

    def leaders(limit=4):
        return sorted(scored, key=lambda value: (
            -scored[value][0], abs(value - 1.0)))[:limit]

    # The whole dialogue span is stable under irregular insertions/deletions in
    # the middle, exactly where index-quantile ratios fail. Search a generous
    # +/-1.2% band around that evidence at 0.001 resolution. A full 0.90..1.11
    # sweep also finds cadence aliases and is several seconds slower on x86;
    # when the edge span is wrong by more than this (different opening/ending),
    # refusing automatic surgery is the safer outcome. The existing local
    # smooth-drift refit removes the remaining <=0.0005 quantisation error.
    ref_span = ref_on[-1] - ref_on[0]
    cand_span = cand_on[-1] - cand_on[0]
    if ref_span <= 0 or cand_span <= 0:
        return []
    center = cand_span / ref_span
    scan(center + i * 0.001 for i in range(-12, 13))
    return leaders(6)


def estimate(ref_cues, cand_cues, scales=None, max_offset_ms=None):
    """Best linear map cand_time ~= a*ref_time + b over the scale candidates.
    Returns (a, b_ms, vote_ratio). `scales` restricts the candidate scale set
    (sparse references can't support scale estimation -- every extra scale
    multiplies the chance of a spurious histogram peak); `max_offset_ms`
    narrows the offset search window for the same reason."""
    ref_on = [c['start'] for c in ref_cues]
    cand_on = [c['start'] for c in cand_cues]
    if not ref_on or not cand_on:
        return 1.0, 0.0, 0.0
    sampled = len(ref_on[::max(1, len(ref_on) // _SAMPLE)])
    max_off = _MAXOFF if max_offset_ms is None else max_offset_ms
    best = (1.0, 0.0, -1)
    # Try scales nearest-to-1.0 FIRST and require a STRICTLY better vote to
    # switch away: neighbouring FPS ratios (e.g. 23.976/24 = 0.999) can tie
    # with the identity map inside the histogram bin tolerance on short
    # spans, and picking 0.999 over a true 1.0 accumulates seconds of drift
    # by the end of a long movie.
    candidates = list(dict.fromkeys(
        round(float(value), 6)
        for value in (SCALES if scales is None else scales)))

    def consider(values, current):
        for candidate_scale in sorted(values, key=lambda s: abs(s - 1.0)):
            candidate_offset, candidate_votes = _best_offset(
                ref_on, cand_on, candidate_scale, max_off)
            if candidate_votes > current[2]:
                current = (candidate_scale, candidate_offset, candidate_votes)
        return current

    if 1.0 in candidates:
        # Identity first (same tie rule as before: another scale must be
        # strictly better). When the identity clock already carries a strong
        # majority there is no FPS drift to find: skip the dozen other scale
        # histograms -- the bulk of SubSync's CPU on weak TV boxes. Every
        # downstream gate still runs on the result.
        offset1, votes1 = _best_offset(ref_on, cand_on, 1.0, max_off)
        best = (1.0, offset1, votes1)
        if sampled and votes1 / sampled >= _IDENTITY_SHORTCUT_VOTE:
            return 1.0, offset1, votes1 / sampled
        candidates = [value for value in candidates if value != 1.0]
    best = consider(candidates, best)
    # Standard FPS/identity covers almost every healthy file.  Arbitrary
    # quantile proposals cost two extra histogram passes, so evaluate them only
    # when that fast path lacks a strong global majority.  Deep verification is
    # background work, but it must still stay light on 32-bit devices.
    if scales is None and (best[2] / sampled if sampled else 0.0) < 0.85:
        extras = [value for value in _data_scale_candidates(ref_cues, cand_cues)
                  if value not in candidates]
        best = consider(extras, best)
        # If the cheap standard + quantile proposals still cannot establish a
        # strong majority, use NG's broad-search insight in a bounded form.
        # Only six sampled winners reach the full-density evaluator, keeping
        # the slow path practical on 32-bit devices. Every downstream unique,
        # overlap, tight, local, identity and invariant gate remains mandatory.
        if (best[2] / sampled if sampled else 0.0) < 0.80:
            broad = [value for value in _adaptive_scale_candidates(
                ref_cues, cand_cues, max_offset_ms=max_offset_ms)
                     if value not in candidates and value not in extras]
            best = consider(broad, best)
    a, b, v = best
    return a, b, (v / sampled if sampled else 0.0)
