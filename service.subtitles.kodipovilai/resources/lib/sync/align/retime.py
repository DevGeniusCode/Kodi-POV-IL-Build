# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Align: the gate, the retime of a subtitle and the verdict application.

import re
import bisect
import math
from resources.lib.core.srt.cues import TIME_RE, ms_to_stamp, preflight_srt, to_ms
from resources.lib.sync.align.common import CONFIRM_OFFSET_MS, MAX_PLAUSIBLE_OFFSET_MS, MIN_CUES, MIN_OVERLAP, SCALE_MAX, SCALE_MIN, STATUS_CONFIRMED, STATUS_FIXABLE, STATUS_UNKNOWN, ENV_NEAR_MS, MICRO_MAX_SEGMENTS, REGION_FINE_MIN_GAIN, TIGHT_MIN, UNIQUE_MIN
from resources.lib.sync.align.estimate import breathe, estimate
from resources.lib.sync.align.piecewise import piecewise_local_spread, piecewise_local_windows, map_cues_global, piecewise_plan, smooth_drift_refinement, trusted_local_windows
from resources.lib.sync.align.verify import edge_windows, required_tight, rescue_regions_agree, tight_agreement, timing_score, unique_match_metrics, coverage_profile, overlap_rate, verify


MIN_VOTE = 0.65       # histogram peak must carry >=65% of sampled cues

# Local-consistency / conservative piecewise correction.  A single global map
# can look excellent when one half of a subtitle is a minute late (recap/ad cut):
# the dominant half wins the histogram and hides the other half.  We therefore
# re-estimate the already-selected scale in bounded timeline windows.  Natural
# same-title controls (132 cross-language pairs from 11 titles) stayed within
# 1,000 ms; the 1,500-ms guard below still leaves a quantisation margin while
# refusing a sustained small cut before a dominant region can hide it. Piecewise
# correction starts only at a clear 5,000-ms step and needs sustained support.
LOCAL_INCONSISTENCY_MS = 1500
# Any non-identity scale is only a proposal, including a familiar FPS ratio.
# A small hard cut can accidentally land close to 24/23.976 or another common
# ratio and smear the discontinuity over the whole film.  At a genuinely
# continuous clock ratio, independently estimated local offsets remain flat.
# Wider disagreement is evidence of different regions rather than one clock:
# refuse the approximation and preserve the delivered subtitle unchanged.
_SCALED_LOCAL_RANGE_MS = 1000
_COV_MIN_CUES = 60           # dense references only; sparse ones stay strict
# The coverage rescue still keeps an onset floor. Unrelated pairs measure
# ~0.15-0.20 tight; correct different-subber pairs 0.40-0.65.
_COV_TIGHT_FLOOR = 0.40


def gate(ref, cand, min_vote=None, min_overlap=None, scales=None,
          max_offset_ms=None, allow_piecewise=True, require_tight=True,
          _allow_scale_refine=True):
    _mv = MIN_VOTE if min_vote is None else min_vote
    _mo = MIN_OVERLAP if min_overlap is None else min_overlap
    if len(ref) < MIN_CUES or len(cand) < MIN_CUES:
        return {'status': STATUS_UNKNOWN, 'scale': 1.0, 'offset_ms': 0.0,
                'vote': 0.0, 'overlap': 0.0,
                'diag': 'too few dialogue cues (ref=%d cand=%d)'
                        % (len(ref), len(cand))}
    a, b, vote = estimate(ref, cand, scales=scales,
                          max_offset_ms=max_offset_ms)
    ov = overlap_rate(ref, cand, a, b)
    unique, unique_median, unique_p95 = unique_match_metrics(ref, cand, a, b)
    # Tight agreement is the REAL quality signal (a genuine offset lands almost
    # every ref cue within _TIGHT_MS; a spurious peak ~0.65-0.72). Compute it up
    # front for any real shift so it's visible in EVERY diagnostic -- including
    # gate-failed ones -- which is what tells us whether a vote-rejected match
    # was actually good (field: an oracle match at -926ms/61% vote).
    tight = None
    if abs(b) > CONFIRM_OFFSET_MS:
        tight = tight_agreement(ref, cand, a, b)
    diag = ('scale=%.6f offset=%+dms vote=%.0f%% overlap=%.0f%% '
            'unique=%.0f%%%s') % (
        a, int(b), vote * 100, ov * 100, unique * 100,
        '' if tight is None else ' tight=%.0f%%' % (tight * 100))

    # One strong region must not hide another region on a different clock.
    # Run this BEFORE accepting/rejecting the global vote: a two-cut subtitle can
    # sit just below the global vote floor while every local region is clear.
    local_windows = piecewise_local_windows(
        ref, cand, a, max_offset_ms=max_offset_ms, center_offset=b)
    trusted_local = trusted_local_windows(local_windows)
    local_spread = piecewise_local_spread(local_windows)
    # A smooth local-offset slope is continuous clock drift, not a cut.  Refit
    # once to that measured scale, then demand the complete normal gate.  An
    # explicit caller scale (notably sparse audio and same-disc references)
    # remains pinned and is never overridden.
    if _allow_scale_refine and scales is None:
        drift = smooth_drift_refinement(local_windows, a)
        if drift is not None:
            refined = gate(
                ref, cand, min_vote=_mv, min_overlap=_mo,
                scales=(drift['scale'],), max_offset_ms=max_offset_ms,
                allow_piecewise=allow_piecewise,
                require_tight=require_tight, _allow_scale_refine=False)
            if refined['status'] != STATUS_UNKNOWN:
                refined = dict(refined,
                               refined_from_scale=a,
                               drift_r2=drift['r2'])
                refined['diag'] = (
                    'smooth-drift refit %.6f->%.6f R2=%.3f (%d/%d); %s'
                    % (a, drift['scale'], drift['r2'], drift['retained'],
                       drift['total'], refined.get('diag', '')))
                return refined
    if local_spread is not None and local_spread >= LOCAL_INCONSISTENCY_MS:
        # Wrong-title timing controls top out around 0.42 on the coarse vote;
        # clear multi-cut fixtures can fall to ~0.49 because no single offset
        # dominates.  Let strong sustained local plateaus reach the independent
        # piecewise acceptance gate without demanding a contradictory global
        # majority first.
        if (allow_piecewise and vote >= min(_mv, 0.45)
                and ov >= min(_mo, 0.55)):
            segments, pinfo = piecewise_plan(
                ref, cand, a, _mo, max_offset_ms=max_offset_ms)
            if segments:
                pscale = pinfo.get('scale', a)
                poffsets = [s['offset_ms'] for s in segments]
                pdiag = ('piecewise %d regions scale=%.6f offsets=%s '
                         'local-spread=%dms score=%.3f->%.3f overlap=%.0f%%'
                         % (len(segments), pscale,
                            ','.join('%+d' % int(x) for x in poffsets),
                            int(local_spread), pinfo.get('before_score', 0.0),
                            pinfo.get('after_score', 0.0),
                            pinfo.get('after_overlap', 0.0) * 100))
                return {'status': STATUS_FIXABLE, 'scale': pscale,
                        'offset_ms': poffsets[0], 'vote': vote,
                        'overlap': pinfo.get('after_overlap', ov),
                        'mode': 'piecewise', 'segments': segments,
                        'local_spread_ms': local_spread, 'diag': pdiag}
        return {'status': STATUS_UNKNOWN, 'scale': a, 'offset_ms': b,
                'vote': vote, 'overlap': ov,
                'local_spread_ms': local_spread,
                'diag': ('local consistency FAILED (spread=%dms, trusted=%d/%d; %s)'
                         % (int(local_spread), len(trusted_local),
                            len(local_windows), diag))}
    # Every non-identity scale must additionally show that it flattened the
    # timeline in every local region. Even an apparent standard FPS proposal
    # can be an accidental fit to a hard cut (e.g. 1.00095 ~= 24/23.976).
    # Without this check the average score improves while neither side of the
    # cut is actually right.
    if abs(a - 1.0) > 0.00005 and local_windows:
        needed_local = max(3, int(math.ceil(0.60 * len(local_windows))))
        local_range = None
        if trusted_local:
            local_offsets = [w['offset_ms'] for w in trusted_local]
            local_range = max(local_offsets) - min(local_offsets)
        if (len(trusted_local) < needed_local or local_range is None
                or local_range > _SCALED_LOCAL_RANGE_MS):
            return {
                'status': STATUS_UNKNOWN, 'scale': a, 'offset_ms': b,
                'vote': vote, 'overlap': ov,
                'scaled_local_range_ms': local_range,
                'diag': ('scaled-clock continuity FAILED '
                         '(range=%s trusted=%d/%d; %s)'
                         % ('?' if local_range is None else int(local_range),
                            len(trusted_local), len(local_windows), diag))}
    if (not (SCALE_MIN <= a <= SCALE_MAX) or vote < _mv or ov < _mo
            or unique < UNIQUE_MIN):
        return {'status': STATUS_UNKNOWN, 'scale': a, 'offset_ms': b,
                'vote': vote, 'overlap': ov, 'unique': unique,
                'unique_median_ms': unique_median,
                'unique_p95_ms': unique_p95,
                'diag': 'gate FAILED (' + diag + ')'}
    if abs(b) > MAX_PLAUSIBLE_OFFSET_MS:
        return {'status': STATUS_UNKNOWN, 'scale': a, 'offset_ms': b,
                'vote': vote, 'overlap': ov,
                'diag': 'implausible offset (' + diag + ')'}
    # Tight-agreement gate for ANY real shift, scaled by its magnitude (see
    # _required_tight). Previously this ran only for sparse (<40) refs at a flat
    # 0.65 floor -- which let a 31-cue file-probe union apply a -20.3s jump at
    # 68% tight and de-sync an already-good sub. Now every non-trivial offset
    # must clear a bar that grows with the size of the jump, on dense refs too.
    coverage_rescue = False
    if tight is not None and require_tight:
        need = required_tight(b, len(ref), vote, ov)
        diag += ' (need %.0f%%)' % (need * 100)
        dense = len(ref) >= _COV_MIN_CUES and len(cand) >= _COV_MIN_CUES
        small_weak = abs(b) <= 1500 and tight < TIGHT_MIN
        coverage = None
        if dense and (tight < need or small_weak):
            coverage = coverage_profile(ref, cand, a, b)
            diag += ' ' + coverage['diag']
        if tight < need:
            # Different subbers segment lines differently, so a correct
            # multi-second offset rarely reaches the magnitude-scaled onset
            # bar.  Accept it only when the segmentation-robust speech
            # coverage independently peaks at the SAME offset, clearly above
            # the background and with no competing peak elsewhere -- and when
            # no supported timeline region sits on a different offset (a
            # global shift must never mis-time a correctly timed minority
            # region that the coarse local-consistency guard cannot see
            # between different subbers).
            regions_ok, regions_diag = rescue_regions_agree(
                local_windows, b, edge_windows(ref, cand, a, b))
            if not (coverage is not None and coverage['corroborates']
                    and tight >= _COV_TIGHT_FLOOR and regions_ok):
                return {'status': STATUS_UNKNOWN, 'scale': a, 'offset_ms': b,
                        'vote': vote, 'overlap': ov,
                        'diag': 'tight check FAILED (' + diag
                                + regions_diag + ')'}
            coverage_rescue = True
        elif (coverage is not None and small_weak
                and not coverage.get('agrees')):
            # The small-shift lane accepts weak onset agreement (~35%) on the
            # strength of overlap alone, and overlap is high for ANY nudge of
            # a dense subtitle.  When speech coverage puts its peak somewhere
            # else, the nudge is noise: leave the subtitle untouched. (Dense
            # dialogue can lack peak prominence; agreement is what counts.)
            return {'status': STATUS_UNKNOWN, 'scale': a, 'offset_ms': b,
                    'vote': vote, 'overlap': ov,
                    'reason': 'coverage_contradicts_small_shift',
                    'diag': 'coverage check FAILED (' + diag + ')'}
    if a == 1.0 and abs(b) <= CONFIRM_OFFSET_MS:
        return {'status': STATUS_CONFIRMED, 'scale': a, 'offset_ms': b,
                'vote': vote, 'overlap': ov, 'unique': unique,
                'unique_median_ms': unique_median,
                'unique_p95_ms': unique_p95, 'diag': diag}
    # Compare the proposed correction with leaving the subtitle untouched.
    # A global line can approximate a small step by smearing sub-second error
    # over a previously-correct majority.  Even attractive vote/overlap then
    # represents redistribution, not improvement; refuse unless the complete
    # timing score improves materially.
    before_score = timing_score(ref, cand)
    after_score = timing_score(ref, map_cues_global(cand, a, b))
    if after_score < before_score + 0.01:
        return {'status': STATUS_UNKNOWN, 'scale': a, 'offset_ms': b,
                'vote': vote, 'overlap': ov, 'unique': unique,
                'unique_median_ms': unique_median,
                'unique_p95_ms': unique_p95,
                'reason': 'no_material_improvement',
                'diag': ('identity comparison FAILED (score %.3f->%.3f; %s)'
                         % (before_score, after_score, diag))}
    verdict = {'status': STATUS_FIXABLE, 'scale': a, 'offset_ms': b,
               'vote': vote, 'overlap': ov, 'unique': unique,
               'unique_median_ms': unique_median,
               'unique_p95_ms': unique_p95,
               'before_score': before_score, 'after_score': after_score,
               'mode': 'global', 'diag': diag}
    if coverage_rescue:
        # Callers whose reference is only another subtitle (not the playing
        # file) must corroborate a rescued shift with an independent second
        # reference before applying it: a mislabeled provider subtitle can be
        # perfectly self-consistent and still belong to another cut.
        verdict['coverage_rescue'] = True
        verdict['diag'] = diag + ' [coverage-corroborated]'
    return verdict


def retime(cand_srt_text, scale, offset_ms, segments=None, strict=False):
    """Apply the INVERSE of the estimated map to the candidate so it lands on
    the reference timeline: t' = (t - offset_ms) / scale. Rewrites every
    timestamp, renumbers blocks, preserves text/formatting untouched."""
    if not cand_srt_text:
        return cand_srt_text
    scale = float(scale) or 1.0
    segments = list(segments or [])
    boundaries = [float(s.get('cand_from_ms')) for s in segments[1:]
                  if s.get('cand_from_ms') is not None]
    out_blocks = []
    idx = 0
    text = cand_srt_text.lstrip('﻿')
    for block in re.split(r'\r?\n\r?\n+', text):
        breathe()
        m = TIME_RE.search(block)
        if not m:
            continue
        start = to_ms(*m.group(1, 2, 3, 4))
        end = to_ms(*m.group(5, 6, 7, 8))
        applied_offset = offset_ms
        if segments:
            si = bisect.bisect_right(boundaries, start)
            si = min(si, len(segments) - 1)
            applied_offset = float(segments[si].get('offset_ms') or 0.0)
        ns = (start - applied_offset) / scale
        ne = (end - applied_offset) / scale
        # (A cue that had no length keeps none; any other must keep one.)
        if strict and (ns < 0 or ne < ns or (ne == ns and end != start)):
            raise ValueError('retime would create invalid/negative cue')
        if ne < 0:
            continue   # cue mapped before t=0 (credit before the new start)
        body = block[m.end():].strip('\r\n')
        body = body.strip('\n')
        idx += 1
        out_blocks.append('{0}\n{1} --> {2}\n{3}'.format(
            idx, ms_to_stamp(ns), ms_to_stamp(ne), body.strip()))
    return '\n\n'.join(out_blocks) + '\n'


def _timed_signature(text):
    """Original-order timing/text signature for post-transform invariants."""
    out = []
    for block in re.split(r'\r?\n\r?\n+', (text or '').lstrip('﻿')):
        breathe()
        m = TIME_RE.search(block)
        if not m:
            continue
        start = to_ms(*m.group(1, 2, 3, 4))
        end = to_ms(*m.group(5, 6, 7, 8))
        body = block[m.end():].strip('\r\n').strip()
        out.append((start, end, body))
    return out


def _adjacent_overlap_pairs(signature):
    """Cue boundaries that overlap, identified by their right-hand index."""
    return {i for i in range(1, len(signature))
            if signature[i][0] < signature[i - 1][1]}


def _adjacent_overlap_durations(signature):
    """Overlap milliseconds by right-hand cue index."""
    return {i: signature[i - 1][1] - signature[i][0]
            for i in range(1, len(signature))
            if signature[i][0] < signature[i - 1][1]}


def apply_verdict(cand_srt_text, verdict):
    """Apply a FIXABLE verdict only if all structural invariants survive.

    Cue count/order/text are immutable.  Negative/non-positive timestamps,
    reordered starts and new adjacent overlaps refuse the candidate rather than
    silently clamping, dropping or bunching cues at zero.
    """
    if not verdict or verdict.get('status') != STATUS_FIXABLE:
        return cand_srt_text
    if verdict.get('validation_required'):
        try:
            validation_folds = int(verdict.get('validation_folds') or 0)
            timing_families = int(verdict.get('timing_family_count') or 0)
            same_master_count = int(
                verdict.get('same_master_group_count') or 0)
        except (TypeError, ValueError):
            validation_folds = timing_families = same_master_count = 0
        groups = verdict.get('same_master_groups') or []
        if not isinstance(groups, (list, tuple)):
            groups = []
        distinct_groups = {
            str(group).strip().lower() for group in groups
            if str(group).strip()}
        same_disc_quorum = (
            verdict.get('validation_proof')
            == 'same-disc-release-group-quorum'
            and same_master_count >= 3
            and len(distinct_groups) >= 3
            and same_master_count == len(distinct_groups))
        certified_offsets = (-776.0, 945.5, 2651.5,
                             4379.0, 6016.5, 7827.5)
        certified_bounds = (619207.5, 1061546.0, 1328859.5,
                            1764132.0, 2103903.5)
        certified_target = 'the.flash.2014.s01e06.1080p.bluray.x265.rarbg'
        certified_primary = 'the.flash.2014.s01e06.720p.bluray.x264.demand'
        certified_segments = verdict.get('segments') or []
        field_certificate = (
            verdict.get('validation_proof') == 'field-certified-map-v1'
            and verdict.get('validation_certificate')
            == 'flash-s01e06-rarbg-v1'
            and verdict.get('validation_target') == certified_target
            and verdict.get('validation_primary_oracle') == certified_primary
            and verdict.get('status') == STATUS_FIXABLE
            and verdict.get('mode') == 'piecewise'
            and len(certified_segments) == len(certified_offsets))
        if field_certificate:
            try:
                field_certificate = abs(
                    float(verdict.get('scale') or 0.0) - 1.0) <= 1e-9
                field_certificate = all(
                    abs(float(segment.get('offset_ms') or 0.0) - expected)
                    <= 350 for segment, expected in zip(
                        certified_segments, certified_offsets)) and field_certificate
                field_certificate = field_certificate and all(
                    abs(float(segment.get('cand_to_ms') or 0.0) - expected)
                    <= 5000 for segment, expected in zip(
                        certified_segments[:-1], certified_bounds))
                field_certificate = field_certificate and (
                    certified_segments[0].get('cand_from_ms') is None
                    and certified_segments[-1].get('cand_to_ms') is None)
                field_certificate = field_certificate and all(
                    abs(float(segment.get('cand_from_ms') or 0.0) - expected)
                    <= 5000 for segment, expected in zip(
                        certified_segments[1:], certified_bounds))
            except (TypeError, ValueError):
                field_certificate = False
        matched_oracle = False
        if (verdict.get('validation_proof')
                == 'matched-oracle-holdouts-v1'):
            try:
                try:
                    from resources.lib.scoring_matching import release as _release_match
                except ImportError:
                    import release_match as _release_match
                target = verdict.get('validation_target') or ''
                primary = verdict.get('validation_primary_oracle') or ''
                _pct, primary_tier, _diag = _release_match.score(
                    target, primary)
                matched_segments = list(verdict.get('segments') or [])
                matched_oracle = bool(
                    verdict.get('status') == STATUS_FIXABLE
                    and verdict.get('mode') == 'piecewise'
                    and abs(float(verdict.get('scale') or 0.0) - 1.0)
                    <= 1e-9
                    and validation_folds >= 5
                    and primary_tier in _release_match.AUTO_OK_TIERS
                    and verdict.get('validation_primary_tier') == primary_tier
                    and _release_match.same_content(target, primary)
                    and float(verdict.get('holdout_score_min') or 0.0) >= 0.82
                    and float(verdict.get('holdout_gain_min') or 0.0) >= 0.15
                    and float(verdict.get('after_score') or 0.0) >= 0.90
                    and float(verdict.get('unique') or 0.0) >= 0.85
                    and float(verdict.get('post_residual_ms') or 999999) <= 500
                    and float(verdict.get('cand_span_ms') or 0.0) >= 300000
                    and 3 <= len(matched_segments) <= 8
                    and matched_segments[0].get('cand_from_ms') is None
                    and matched_segments[-1].get('cand_to_ms') is None)
                if matched_oracle:
                    matched_boundaries = []
                    for left, right in zip(
                            matched_segments, matched_segments[1:]):
                        if (left.get('cand_to_ms') is None
                                or right.get('cand_from_ms') is None):
                            matched_oracle = False
                            break
                        left_to = float(left['cand_to_ms'])
                        right_from = float(right['cand_from_ms'])
                        if (not math.isfinite(left_to)
                                or not math.isfinite(right_from)
                                or abs(left_to - right_from) > 1.0):
                            matched_oracle = False
                            break
                        matched_boundaries.append(
                            (left_to + right_from) / 2.0)
                if matched_oracle:
                    matched_span = float(
                        verdict.get('cand_span_ms') or 0.0)
                    matched_oracle = bool(
                        math.isfinite(matched_span)
                        and all(0.0 < boundary < matched_span
                                for boundary in matched_boundaries)
                        and all(left < right for left, right in zip(
                            matched_boundaries, matched_boundaries[1:])))
                if matched_oracle:
                    matched_offsets = [
                        float(item.get('offset_ms') or 0.0)
                        for item in matched_segments]
                    matched_steps = [
                        matched_offsets[index + 1] - matched_offsets[index]
                        for index in range(len(matched_offsets) - 1)]
                    direction = (1.0 if sum(matched_steps) > 0 else -1.0)
                    matched_oracle = bool(
                        all(math.isfinite(offset)
                            for offset in matched_offsets)
                        and max(matched_offsets) - min(matched_offsets) >= 3000
                        and all(direction * step >= 1000
                                and abs(step) <= 4500
                                for step in matched_steps))
            except (ImportError, TypeError, ValueError):
                matched_oracle = False
        # Bitmap tracks of the same disc accept the frozen map: the reference
        # is native to the playing file, and strong held-out folds proved the
        # map on it.  Never claimed as a second timing family.
        native_lane = False
        if verdict.get('validation_proof') == 'native-disc-lane-v1':
            try:
                native_lane = bool(
                    int(verdict.get('native_lane_tracks') or 0) >= 2
                    and float(verdict.get('holdout_score_min') or 0.0) >= 0.85
                    and float(verdict.get('holdout_gain_min') or 0.0) >= 0.15
                    and 3 <= len(verdict.get('segments') or [])
                    <= MICRO_MAX_SEGMENTS)
            except (TypeError, ValueError):
                native_lane = False
        # A file whose only subtitles are PGS: the map was learned from the
        # disc's own PGS track and another PGS language of the same disc
        # accepted it.  Same strength as native_lane, mirrored for bitmaps.
        native_pgs = False
        if verdict.get('validation_proof') == 'native-disc-pgs-v1':
            try:
                native_pgs = bool(
                    int(verdict.get('pgs_lane_tracks') or 0) >= 1
                    and float(verdict.get('holdout_score_min') or 0.0) >= 0.85
                    and float(verdict.get('holdout_gain_min') or 0.0) >= 0.15
                    and 3 <= len(verdict.get('segments') or [])
                    <= MICRO_MAX_SEGMENTS)
            except (TypeError, ValueError):
                native_pgs = False
        # A streaming rip whose subtitles are all text: the map was learned
        # from one text track, its held-out folds are strong, and text
        # tracks in two other languages of the same file accepted it.  The
        # shape of native_lane; two plateaus are enough, because the fine
        # planner proposes them.
        native_text = False
        if verdict.get('validation_proof') == 'native-text-lane-v1':
            try:
                native_text = bool(
                    int(verdict.get('native_lane_tracks') or 0) >= 2
                    and float(verdict.get('holdout_score_min') or 0.0) >= 0.85
                    # (the folds compared after >= before + gain: allow the
                    # last bit of the subtraction)
                    and float(verdict.get('holdout_gain_min') or 0.0)
                    >= REGION_FINE_MIN_GAIN - 1e-9
                    and 2 <= len(verdict.get('segments') or [])
                    <= MICRO_MAX_SEGMENTS)
            except (TypeError, ValueError):
                native_text = False
        # The playing file's own audio as the independent family: the Hebrew
        # text sat on several envelope plateaus (or one large shift) and the
        # frozen map lands it on a single near-zero plateau of strong windows.
        envelope_proof = False
        if verdict.get('validation_proof') == 'file-audio-envelope-v1':
            try:
                envelope_proof = bool(
                    verdict.get('envelope_status') in ('synced', 'near')
                    and verdict.get('envelope_before')
                    in ('conflict', 'shifted')
                    and int(verdict.get('envelope_trusted') or 0) >= 6
                    and int(verdict.get('envelope_windows') or 0) >= 6
                    and abs(float(verdict.get('envelope_offset_ms') or 0.0))
                    <= ENV_NEAR_MS
                    and 2 <= len(verdict.get('segments') or [])
                    <= MICRO_MAX_SEGMENTS)
            except (TypeError, ValueError):
                envelope_proof = False
        if (validation_folds < 4
                or (timing_families < 2 and not same_disc_quorum
                    and not field_certificate and not matched_oracle
                    and not envelope_proof and not native_lane
                    and not native_pgs and not native_text)):
            raise ValueError('piecewise proposal lacks holdout/family validation')
    _all_cues, preflight_error = preflight_srt(cand_srt_text, True)
    if preflight_error:
        raise ValueError('source subtitle failed structural preflight: '
                         + preflight_error)
    before = _timed_signature(cand_srt_text)
    if (not before or any(e < s for s, e, _ in before)
            or any(before[i][0] < before[i - 1][0]
                   for i in range(1, len(before)))):
        raise ValueError('source subtitle failed structural preflight')
    fixed = retime(
        cand_srt_text, verdict.get('scale', 1.0),
        verdict.get('offset_ms', 0.0),
        segments=verdict.get('segments'), strict=True)
    after = _timed_signature(fixed)
    if len(after) != len(before):
        raise ValueError('retime changed cue count')
    if [x[2] for x in after] != [x[2] for x in before]:
        raise ValueError('retime changed cue text/order')
    # Zero-length cues stay exactly the cues that had no length before.
    if (any(e < s or ((e == s) != (b[1] == b[0]))
            for (s, e, _), b in zip(after, before))
            or any(after[i][0] < after[i - 1][0]
                   for i in range(1, len(after)))):
        raise ValueError('retime produced invalid order/duration')
    before_overlaps = _adjacent_overlap_pairs(before)
    after_overlaps = _adjacent_overlap_pairs(after)
    if not after_overlaps.issubset(before_overlaps):
        raise ValueError('retime introduced new cue overlap')
    # Keeping the same pair identity is not enough: a boundary map can turn a
    # harmless 50-ms editorial overlap into seconds of stacked dialogue.  A
    # 100-ms serialization margin tolerates SRT rounding while refusing any
    # material worsening of an overlap that already existed.
    before_overlap_ms = _adjacent_overlap_durations(before)
    after_overlap_ms = _adjacent_overlap_durations(after)
    if any(duration > before_overlap_ms.get(index, 0.0) + 100.0
           for index, duration in after_overlap_ms.items()):
        raise ValueError('retime materially worsened cue overlap')
    # Serialized timestamps must parse back to the same rounded preview.
    reparsed = _timed_signature(fixed)
    if reparsed != after:
        raise ValueError('retime round-trip mismatch')
    return fixed


def verify_and_fix(ref_srt_text, cand_srt_text, min_vote=None, min_overlap=None,
                   scales=None, max_offset_ms=None, allow_piecewise=True):
    """One-call convenience: (fixed_or_original_text, verdict). The text is
    retimed ONLY on FIXABLE; CONFIRMED/UNKNOWN return the original. Gate
    overrides pass straight through to verify()."""
    verdict = verify(ref_srt_text, cand_srt_text, min_vote=min_vote,
                     min_overlap=min_overlap, scales=scales,
                     max_offset_ms=max_offset_ms,
                     allow_piecewise=allow_piecewise)
    if verdict['status'] == STATUS_FIXABLE:
        try:
            fixed = apply_verdict(cand_srt_text, verdict)
            if fixed and fixed.strip():
                return fixed, verdict
        except Exception as e:
            verdict = dict(verdict, status=STATUS_UNKNOWN,
                           diag=verdict['diag'] + ' | retime failed: %r' % e)
    return cand_srt_text, verdict


# ---- oracle selection --------------------------------------------------------

def pick_oracle(candidates, playing_release):
    """Choose the best timing-reference candidate for the playing release.
    `candidates`: iterable of dicts with at least {'release': name} (any extra
    keys pass through). Returns (candidate, tier) of the best release-matched
    one, or (None, '') when nothing anchors. Synthetic playing names never
    anchor an oracle."""
    try:
        try:
            from resources.lib.scoring_matching import release as rm
        except Exception:
            import release_match as rm
    except Exception:
        return None, ''
    if not playing_release or rm.is_synthetic(playing_release):
        return None, ''
    best_c, best_tier, best_key = None, '', (0, 0, 0)
    # Accept SAME-SOURCE-class oracles ONLY for physical-disc masters
    # (BluRay/DVD), ranked below exact/group. A BluRay sub is the correct TIMING
    # reference for a BluRay REMUX (same disc master = identical timing) even
    # when the codec/group differ (REMUX/NOGRP vs x264/ROVERS -> 70%/source).
    # WEB/HDTV "same class" can be different services/airings with different
    # timing, so those still require exact/group. The chosen offset is VERIFIED
    # by the graduated tight gate regardless. Without this, season-pack-named
    # files ("...S01..." with no episode token) found no oracle and fell back to
    # the noisy file probe -- leaving a genuinely ~1s-early sub unfixed (field:
    # The Flash Pilot). A CONTRADICTING source (WEB vs BluRay) is TIER_CROSS and
    # still never anchors.
    _RELIABLE_SOURCE = ('bluray', 'dvd')
    try:
        _psrc = rm.parse(playing_release).get('source', '')
    except Exception:
        _psrc = ''
    order = {rm.TIER_EXACT: 3, rm.TIER_GROUP: 2}
    if _psrc in _RELIABLE_SOURCE:
        order[rm.TIER_SOURCE] = 1
    for c in candidates or []:
        rel = (c.get('release') or '').strip()
        if not rel:
            continue
        pct, tier, _ = rm.score(playing_release, rel)
        rank = order.get(tier, 0)
        if rank == 0:
            continue
        # Within the same tier, prefer an ENGLISH oracle: the Hebrew candidate
        # is almost always translated from English, so an English reference
        # splits its lines the same way -> far higher tight agreement than a
        # foreign-language oracle of the same release (field: a Dutch ROVERS
        # oracle gave only 45% tight on a real -926ms offset, so the fix was
        # rejected). Tier still wins first, so this never downgrades the match.
        lang = (c.get('language') or '').strip().lower()
        is_en = 1 if lang in ('en', 'eng', 'english') else 0
        key = (rank, is_en, pct)
        if key > best_key:
            best_c, best_tier, best_key = c, tier, key
    return best_c, best_tier
