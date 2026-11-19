# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Piecewise validators of the deep verify (PGS, text lane, micro, oracle) and track helpers.

import re
import json
import math
try:
    from resources.lib.scoring_matching import release as scoring_matching_release
except Exception:
    scoring_matching_release = None
try:
    from resources.lib.core.srt import cues as srt_cues
    from resources.lib.sync.align import common, piecewise, retime, verify
except Exception:
    srt_cues = common = piecewise = retime = verify = None
from resources.lib.sync.common import MIN_REMOTE_CUES, AUDIO_MAX_OFFSET_MS, AUDIO_SCALES, ORACLE_SOURCE_MIN_VOTE, ORACLE_SOURCE_SCALES
from resources.lib.sync.oracles import diversify_oracle_matches, download_oracle, merge_oracle_candidates, oracle_candidates, oracle_match
from resources.lib.core.srt.cues import STATUS_UNKNOWN


# This rare rescue runs only after an ordinary oracle abstains and only in the
# background worker. Bound its extra downloads so one difficult cross-cut
# subtitle cannot turn into an open-ended provider crawl.
_ORACLE_PIECEWISE_MAX_DOWNLOADS = 6
# A provider row can disappear, be rate-limited, or yield an invalid archive.
# Such a miss is not timing evidence and must not consume one of the six
# evidence slots.  Keep a separate hard attempt ceiling so the background
# rescue remains bounded while still reaching later, independent languages.
_ORACLE_PIECEWISE_MAX_ATTEMPTS = 12
# A container-probe reference with fewer than this many cues is treated as
# sparse (identity scale only) -- high-bitrate 2160p files can't be sampled
# densely within the byte budget.
_SPARSE_PROBE_CUES = 40


def guard_soft_probe_shift(verdict, ref_kind):
    """Refuse a sub-second nudge supported only by soft media-probe timing.

    Subtitle tracks share the container clock, but each language/subber may
    intentionally lead or trail speech by several hundred milliseconds. A
    multi-track union can therefore outvote a candidate that is byte-for-byte
    aligned with one real track. Keep those small editorial differences as-is;
    large offsets, standard/non-standard clock drift and piecewise fixes still
    go through the normal gates.
    """
    if (not verdict or ref_kind not in ('FILE PROBE', 'AUDIO PROBE')
            or verdict.get('status') != common.STATUS_FIXABLE
            or verdict.get('mode', 'global') != 'global'):
        return verdict
    try:
        scale = float(verdict.get('scale') or 1.0)
        offset = float(verdict.get('offset_ms') or 0.0)
    except (TypeError, ValueError):
        return verdict
    if abs(scale - 1.0) > 0.0002 or abs(offset) >= _SOFT_PROBE_SHIFT_MS:
        return verdict
    guarded = dict(verdict)
    guarded.update({
        'status': STATUS_UNKNOWN,
        'reason': 'soft_probe_shift',
        'diag': ('soft probe shift preserved unchanged (%s %+dms; %s)'
                 % (ref_kind, int(offset), verdict.get('diag', ''))),
    })
    return guarded


def probe_gate_kwargs(cues):
    if cues and len(cues) < _SPARSE_PROBE_CUES:
        return {'scales': AUDIO_SCALES,
                'max_offset_ms': AUDIO_MAX_OFFSET_MS}
    return {}


def _track_base_language(track):
    """One key per language, whatever the tag: streaming rips tag their
    tracks 'en-US' / 'pt-BR' (LanguageBCP47), which a three-letter cut read
    as 'en-' -- no language at all, so the file's English track ranked below
    every other (The 100 S01E01, AMZN WEB-DL, 28 text tracks).  One file
    also mixes the forms: 'en-US' beside an SDH twin tagged 'eng' must be one
    language, as must 'fre' and 'fr-CA', or a twin counts as a second
    language's agreement."""
    lang = (track.get('lang') or '').strip().lower()
    # The table lives in embedded/extract/tracks, which the media probe has already
    # loaded by the time a track is ranked; loading it with this module cost
    # every script that imports subsync ~50 ms on a PC.
    if not _ISO_LANG_KEY:
        try:
            from resources.lib.fetching.embedded.extract.tracks import lang_key as _lang_key
        except Exception:
            _lang_key = None
        _ISO_LANG_KEY.append(_lang_key)
    if _ISO_LANG_KEY[0] is not None:
        try:
            key = _ISO_LANG_KEY[0](lang)
            return {'english': 'en', 'hebrew': 'he'}.get(key, key)
        except Exception:
            pass
    base = re.split(r'[-_]', lang, 1)[0]
    return {'eng': 'en', 'english': 'en', 'heb': 'he', 'hebrew': 'he',
            'iw': 'he'}.get(base, base)


_ISO_LANG_KEY = []


def track_language_rank(track):
    lang = _track_base_language(track)
    # (A tag spelled out -- 'hebrew', 'english' -- ranked by its first three
    # letters before, and still does.)
    spelled = (track.get('lang') or '').strip().lower()[:3]
    if lang == 'he' or spelled == 'heb':
        return 0
    if lang == 'en' or spelled == 'eng':
        return 1
    return 2


def validators_maps_agree(left, right):
    """Whether two independently accepted track corrections corroborate."""
    if (left.get('mode', 'global') != 'global'
            or right.get('mode', 'global') != 'global'):
        # Piecewise maps are too rich to merge by a loose scalar comparison.
        return (left.get('mode') == right.get('mode')
                and left.get('segments') == right.get('segments'))
    try:
        return (abs(float(left.get('scale') or 1.0)
                    - float(right.get('scale') or 1.0)) <= 0.0005
                and abs(float(left.get('offset_ms') or 0.0)
                        - float(right.get('offset_ms') or 0.0)) <= 800.0)
    except Exception:
        return False


def track_codec_family(track):
    codec = (track.get('codec') or '').strip().upper()
    if 'PGS' in codec or 'HDMV' in codec:
        return 'pgs'
    if codec.startswith('S_TEXT') or 'UTF' in codec or 'ASS' in codec:
        return 'text'
    return 'other'


# A PGS track's index lists two display EVENTS per caption -- the instant it
# appears and the instant it is cleared -- and the remote reader turns every
# one of them into a cue.  Measured on a Blu-ray remux: the English PGS track's
# even events coincide with 98% of the English text track's starts and its odd
# events with none, i.e. the track strictly alternates appear/clear.  Paired
# back into captions it reads like a text track: the cue-level region planner
# then recovers the same six act-break regions from the PGS alone that it
# found from the text track (offsets within 2 ms, 4/5 held-out folds).
_PGS_PAIR_MIN_MEDIAN_MS = 800
_PGS_PAIR_MAX_MEDIAN_MS = 6000
_PGS_PAIR_MAX_LONG = 0.05      # captions longer than 10 s
# One missing clear (two captions back to back share a timestamp) flips every
# later pair into clear -> next appear, a gap that is usually under 400 ms.
# Measured on two Blu-ray remuxes: every cleanly alternating PGS track (25 of
# them, 12 languages) has NO pair under 400 ms; the tracks whose alternation
# breaks part-way have 22-27%.  A median check alone accepted a track whose
# later half was flipped.
_PGS_PAIR_SHORT_MS = 400
_PGS_PAIR_MAX_SHORT = 0.02


def _paired_bitmap_cues(cues):
    """A PGS track's appear/clear events paired into captions, or None when
    neither alignment of the pairs looks like captions (median display time
    0.8-6 s, at most 5% over 10 s) -- the measured wrong pairing gives a
    167 ms median, the gaps between lines."""
    try:
        points = sorted({int(c['start']) for c in cues or []})
    except (KeyError, TypeError, ValueError):
        return None
    for parity in (0, 1):
        pairs = [{'start': points[i], 'end': points[i + 1]}
                 for i in range(parity, len(points) - 1, 2)]
        if len(pairs) < MIN_REMOTE_CUES:
            return None
        durations = sorted(p['end'] - p['start'] for p in pairs)
        median = durations[len(durations) // 2]
        long_share = sum(1 for d in durations if d > 10000) / float(len(durations))
        short_share = (sum(1 for d in durations if d < _PGS_PAIR_SHORT_MS)
                       / float(len(durations)))
        if (_PGS_PAIR_MIN_MEDIAN_MS <= median <= _PGS_PAIR_MAX_MEDIAN_MS
                and long_share <= _PGS_PAIR_MAX_LONG
                and short_share <= _PGS_PAIR_MAX_SHORT):
            return pairs
    return None


def track_span(profile):
    cues = profile.get('cues') or []
    if not cues:
        return 0.0
    return max(0.0, float(cues[-1]['end']) - float(cues[0]['start']))


def onset_coverage(source, target, tolerance_ms=250.0):
    """Fraction of source onsets with a nearby target onset (linear scan)."""
    from resources.lib.sync.deep import breathe
    breathe()
    left = sorted(float(cue['start']) for cue in source or [])
    right = sorted(float(cue['start']) for cue in target or [])
    if not left or not right:
        return 0.0
    matched = 0
    cursor = 0
    for value in left:
        while (cursor + 1 < len(right)
               and abs(right[cursor + 1] - value)
               <= abs(right[cursor] - value)):
            cursor += 1
        if abs(right[cursor] - value) <= float(tolerance_ms):
            matched += 1
    return matched / float(len(left))


def _shift_invariant_onset_coverage(source, target, tolerance_ms=250.0):
    """Best onset containment after a plausible constant timeline shift.

    Provider mirrors sometimes add SDH cues, split a few captions, or rebase
    every timestamp by the same amount.  They are still one timing lane, even
    though a raw bidirectional comparison can make the larger derivative look
    independent.  Endpoint candidates catch that relationship without an
    unbounded all-pairs search.
    """
    left = sorted(float(cue['start']) for cue in source or [])
    right = sorted(float(cue['start']) for cue in target or [])
    if not left or not right:
        return 0.0

    # A derivative may have a handful of leading/trailing SDH captions.  Pair
    # the first/last four onsets, plus the median rank when counts are close.
    # Ignore absurd offsets: those cannot be a harmless timestamp rebase.
    edge = 4
    offsets = {0.0}
    for source_value in left[:edge]:
        for target_value in right[:edge]:
            offsets.add(target_value - source_value)
    for source_value in left[-edge:]:
        for target_value in right[-edge:]:
            offsets.add(target_value - source_value)
    offsets.add(right[len(right) // 2] - left[len(left) // 2])
    offsets = [value for value in offsets if abs(value) <= 30000.0]

    best = 0.0
    for offset in offsets:
        shifted = [{'start': value + offset} for value in left]
        best = max(best, onset_coverage(
            shifted, [{'start': value} for value in right], tolerance_ms))
        if best >= 0.995:
            break
    return best


def _shift_invariant_shape_coverage(source, target, tolerance_ms=350.0,
                                    duration_tolerance_ms=250.0):
    """Containment of both cue onset and duration after a constant rebase.

    A mechanically edited timing lane can replace enough onsets to fall below
    the plain containment ceiling while retaining nearly every other cue's
    exact duration. Independently authored subtitle families do not preserve
    that cue-shape fingerprint at scale.
    """
    from resources.lib.sync.deep import breathe
    left = sorted(source or [], key=lambda cue: float(cue['start']))
    right = sorted(target or [], key=lambda cue: float(cue['start']))
    if not left or not right:
        return 0.0
    edge = 4
    offsets = {0.0}
    for source_cue in left[:edge]:
        for target_cue in right[:edge]:
            offsets.add(float(target_cue['start'])
                        - float(source_cue['start']))
    for source_cue in left[-edge:]:
        for target_cue in right[-edge:]:
            offsets.add(float(target_cue['start'])
                        - float(source_cue['start']))
    offsets.add(float(right[len(right) // 2]['start'])
                - float(left[len(left) // 2]['start']))
    offsets = [value for value in offsets if abs(value) <= 30000.0]

    best = 0.0
    for offset in offsets:
        breathe()
        matched = 0
        cursor = 0
        for cue in left:
            value = float(cue['start']) + offset
            while (cursor + 1 < len(right)
                   and abs(float(right[cursor + 1]['start']) - value)
                   <= abs(float(right[cursor]['start']) - value)):
                cursor += 1
            other = right[cursor]
            duration = float(cue['end']) - float(cue['start'])
            other_duration = (float(other['end'])
                              - float(other['start']))
            if (abs(float(other['start']) - value) <= tolerance_ms
                    and abs(other_duration - duration)
                    <= duration_tolerance_ms):
                matched += 1
        best = max(best, matched / float(len(left)))
        if best >= 0.995:
            break
    return best


def _timing_profiles_distinct(left, right):
    """Reject conversions, supersets and splits of the same timing lane.

    Codec labels alone are not independent evidence: a text stream can be
    rendered to PGS without changing its timing, and an SDH/translated mirror
    can add cues to only one side.  Bidirectional similarity rejects ordinary
    copies; one-way containment rejects supersets/splits.  The shifted check
    also catches an otherwise identical lane rebased by a constant offset.
    """
    left_cues = (left or {}).get('cues') or []
    right_cues = (right or {}).get('cues') or []
    left_raw = onset_coverage(left_cues, right_cues)
    right_raw = onset_coverage(right_cues, left_cues)
    if left_raw >= 0.90 and right_raw >= 0.90:
        return False
    # Map agreement tolerates up to 300 ms of segment jitter. Independence
    # therefore uses a slightly wider 350 ms window; otherwise one mechanical
    # clone alternating +/-251 ms by segment could agree with the map while
    # evading the family gate.
    left_aligned = _shift_invariant_onset_coverage(
        left_cues, right_cues, tolerance_ms=350.0)
    right_aligned = _shift_invariant_onset_coverage(
        right_cues, left_cues, tolerance_ms=350.0)
    left_shape = _shift_invariant_shape_coverage(left_cues, right_cues)
    right_shape = _shift_invariant_shape_coverage(right_cues, left_cues)
    # A second family must be editorially independent, not merely a translated
    # derivative that replaces one SDH/split cue every few lines.  At least 15%
    # of both timelines must therefore differ.  The real DEMAND/ROVERS proof
    # stays comfortably below this ceiling even at a wider 500 ms comparison.
    if max(left_aligned, right_aligned) >= 0.85:
        return False
    # Cue-duration shape is an additional clone fingerprint. This catches a
    # lane that mechanically retimes as many as one cue in three while leaving
    # the surrounding cue shapes intact; the second family must differ in more
    # than a third of those shapes before it may count as independent evidence.
    return max(left_shape, right_shape) < 0.65


def _validated_pgs_only_piecewise(pgs_tracks, text, planner, validator,
                                  family_judge):
    """Act-break regions for a file whose only subtitles are PGS.

    The map is learned from the disc's own PGS track (paired into captions --
    see _paired_bitmap_cues) and must pass the same strong held-out folds as
    the text-track path (>=4 of 5, score >= 0.85, gain >= 0.15).  Like
    native-disc-lane-v1 it then needs the disc to agree: at least one other
    PGS language must accept the frozen map, and any that already fitted well
    and is damaged by it vetoes.  A file with a single PGS track gets no
    rewrite from this path (a PGS muxed in from another cut would pass its own
    held-out folds).
    """
    ranked = sorted(pgs_tracks, key=lambda profile: (
        1 if track_language_rank(profile.get('track') or {}) == 1 else 0,
        0 if (profile.get('track') or {}).get('forced') else 1,
        track_span(profile), len(profile.get('cues') or [])), reverse=True)
    primary = ranked[0]
    paired = _paired_bitmap_cues(primary.get('cues') or [])
    if not paired:
        return None
    try:
        proposal = planner(paired, text)
        verdict = (validator(paired, text, proposal)
                   if proposal is not None else None)
    except Exception:
        verdict = None
    if not verdict or verdict.get('status') != common.STATUS_FIXABLE:
        return None
    try:
        folds = int(verdict.get('validation_folds') or 0)
        holdout_min = float(verdict.get('holdout_score_min') or 0.0)
        gain_min = float(verdict.get('holdout_gain_min') or 0.0)
    except (TypeError, ValueError):
        return None
    if folds < 4 or holdout_min < 0.85 or gain_min < 0.15:
        return None
    primary_lang = ((primary.get('track') or {}).get('lang') or '').lower()
    accepting = set()
    for profile in ranked[1:]:
        lang = ((profile.get('track') or {}).get('lang') or '').lower()
        other = _paired_bitmap_cues(profile.get('cues') or [])
        if not other:
            continue
        try:
            metrics = family_judge(other, text, verdict)
        except Exception:
            metrics = None
        if (metrics and metrics.get('before_score', 0.0) >= 0.78
                and metrics.get('after_score', 0.0)
                < metrics.get('before_score', 0.0) - 0.03):
            return None
        if (metrics and metrics.get('accepted') and lang
                and lang != primary_lang
                and lang not in ('und', 'unk', 'mul', 'zxx', 'mis')):
            accepting.add(lang)
    if not accepting:
        return None
    track = primary.get('track') or {}
    verdict = dict(verdict)
    verdict.update({
        'validation_proof': 'native-disc-pgs-v1',
        'pgs_lane_tracks': len(accepting),
        'timing_families': ['pgs'],
        'validation_primary_track': '#%s/%s' % (
            track.get('num', '?'), track.get('lang', '?')),
        'diag': ('%s; map learned from the disc PGS track (paired '
                 'appear/clear events), accepted by PGS %s'
                 % (verdict.get('diag', ''), ', '.join(sorted(accepting)))),
    })
    return {'track': track, 'cues': paired, 'verdict': verdict}


# Other-language text tracks consulted for a text-only file, and how many
# languages must accept the frozen map.  Each consultation re-times the whole
# subtitle once more, so the set stays small; every one consulted may veto.
_TEXT_LANE_MAX_TRACKS = 4
_TEXT_LANE_MIN_LANGUAGES = 2
# Tracks of the primary's own language (its SDH twin, a second 'en') and
# untagged ones never count as agreement, but up to this many may still veto.
_TEXT_SIBLING_MAX = 2
# The primary track carries the proof (its held-out folds); a lane only has
# to show that it shares the primary's timeline, so it must fit after the map
# and fit better than before, by a smaller margin than a second family.
_TEXT_LANE_MIN_GAIN = 0.08
_TEXT_FINE_FAMILY_GAIN = 0.10
# Held-out score the native-lane proof asks, as native-disc-lane-v1 does.  A
# lane's own score does not stand in for it: a lane may be a translation laid
# on the primary's very cues, and its score is then the primary's again.
_TEXT_LANE_STRONG_SCORE = 0.85
_NO_LANGUAGE = ('', 'und', 'unk', 'mul', 'zxx', 'mis')


def _is_sdh_track(track):
    name = (track.get('name') or '').lower()
    return bool(track.get('hearing_impaired')) or 'sdh' in name or 'hi' == name


# Where a release name's attributes begin: the season/episode, the year or
# the resolution.  Everything before it is the title.
_RELEASE_ATTRS_RE = re.compile(
    r'\b(?:s\d{1,2}(?:e\d{1,3})?|\d{1,2}x\d{2,3}|(?:19|20)\d{2}'
    r'|(?:2160|1080|720|576|480)[pi]|4k|uhd)\b')


def _named_streaming_rip(playing):
    """Whether a release name says the file is a streaming rip.

    WEB-DL / WEBRip / WEB among the release's attributes, and no other
    source beside it.  A title can contain the word ('Web of Lies' as an
    HDTV rip read as WEB), so only what follows the season/episode, year or
    resolution counts; a bare title says nothing.
    """
    try:
        name = scoring_matching_release.normalize(playing or '')
        attrs = _RELEASE_ATTRS_RE.search(name)
        if not attrs:
            return False
        tokens = {token for token in name[attrs.start():].split('.') if token}
        sources = {cls for cls, words in scoring_matching_release.SOURCE_SETS
                   if tokens & words}
        return sources == {'web'}
    except Exception:
        return False


def text_lane_file(bundle, playing):
    """Whether the playing file may use the text-lane proof at all.

    That proof takes the file's own tracks as the video's timeline, and
    nothing on its path looks at the video.  That holds for a streaming rip,
    whose subtitle tracks come with the stream; it does not hold for a disc
    or broadcast encode that someone gave a set of subtitles taken from a
    WEB source (review: with every track moved to another cut, a correct
    subtitle was re-timed).  So the release name must say WEB, and every
    subtitle track the file lists -- its headers, whatever was read of each
    -- must be text: a three-caption PGS or a VobSub track marks a disc.
    """
    if not _named_streaming_rip(playing):
        return False
    listed = [profile.get('track') or {}
              for profile in (bundle or {}).get('track_cues') or []]
    listed += [track for track in (bundle or {}).get('tracks') or []
               if isinstance(track, dict)]
    return bool(listed) and all(
        track_codec_family(track) == 'text' for track in listed)


def _validated_text_lane_piecewise(text_tracks, text, planner, validator,
                                   family_judge):
    """Act-break regions for a file whose subtitles are all text.

    A streaming WEB-DL carries no PGS, so the disc proofs never applied to it
    and a subtitle from another cut stayed unverified however clearly the
    file's own tracks showed the map (The 100 S01E01, AMZN WEB-DL: the
    WEB-DL-named Hebrew ran +1.0 s in the first act and +16.3 s in the last;
    26 of the file's 27 other tracks accepted the six-region map).

    Only for a file named as a streaming rip whose subtitle tracks are all
    text (_text_lane_file).  The map is learned from one preferred text
    track (English, not SDH) and must pass the held-out folds (>=4 of 5).
    Then the file's other tracks
    are asked: up to four other LANGUAGES, and up to two tracks of the
    primary's own language or of none (its SDH twin), which can only veto.
    Any track asked that already fitted well and is damaged by the map
    vetoes.  One of two proofs must hold:

    * native lanes -- two other languages accept the map and the held-out
      folds are strong (0.85): the shape of native-disc-lane-v1;
    * a second timing family -- an other-language track whose lines are cut
      differently from the primary's accepts the frozen map with the full
      gain, as one distinct PGS track does for a disc's text track.

    A file with a single subtitle language gets no rewrite: one track cannot
    show whether it or the Hebrew is the one off the video (a track muxed in
    from another cut passes its own held-out folds, and a correct subtitle
    was re-timed onto it in review).  Neither does a map that cannot be
    applied to this very text.
    """
    if not text_tracks:
        return None

    def primary_key(profile):
        track = profile.get('track') or {}
        return (1 if track_language_rank(track) == 1 else 0,
                0 if _is_sdh_track(track) else 1,
                track_span(profile), len(profile.get('cues') or []))

    primary = max(text_tracks, key=primary_key)
    primary_track = primary.get('track') or {}
    primary_lang = _track_base_language(primary_track)
    by_language = {}
    siblings = []
    for profile in text_tracks:
        if profile is primary:
            continue
        track = profile.get('track') or {}
        lang = _track_base_language(track)
        if lang == primary_lang or lang in _NO_LANGUAGE:
            siblings.append(profile)
            continue
        current = by_language.get(lang)
        key = (0 if _is_sdh_track(track) else 1,
               len(profile.get('cues') or []))
        if current is None or key > current[0]:
            by_language[lang] = (key, profile)
    if not by_language:
        return None
    # The fine and free modes of the cue-level planner are asked for here
    # only: sub-second steps and steps that go back and forth need the other
    # languages below, which no other caller has.
    try:
        proposal = planner(primary.get('cues') or [], text,
                           fine=True, free=True)
        verdict = (validator(primary.get('cues') or [], text, proposal,
                             fine=True, free=True)
                   if proposal is not None else None)
    except Exception:       # also an older sync_align still in memory
        verdict = None
    if not verdict or verdict.get('status') != common.STATUS_FIXABLE:
        return None
    try:
        folds = int(verdict.get('validation_folds') or 0)
        holdout_min = float(verdict.get('holdout_score_min') or 0.0)
    except (TypeError, ValueError):
        return None
    if folds < 4:
        return None

    def judged(profile):
        try:
            return family_judge(profile.get('cues') or [], text, verdict,
                                min_gain=_TEXT_LANE_MIN_GAIN)
        except Exception:
            return None

    def damaged(metrics):
        return bool(metrics and metrics.get('before_score', 0.0) >= 0.78
                    and metrics.get('after_score', 0.0)
                    < metrics.get('before_score', 0.0) - 0.03)

    siblings.sort(key=lambda profile: len(profile.get('cues') or []),
                  reverse=True)
    for profile in siblings[:_TEXT_SIBLING_MAX]:
        if damaged(judged(profile)):
            return None
    # Which languages are consulted is fixed by the file, never by which
    # track happens to agree: ordered by cue count and taken evenly from the
    # densest to the sparsest, so the lanes differ in how they split lines
    # (a streaming rip carries several languages cut from one template).
    lanes = sorted(by_language.items(),
                   key=lambda item: (item[1][0], item[0]), reverse=True)
    if len(lanes) > _TEXT_LANE_MAX_TRACKS:
        last = len(lanes) - 1
        picks = sorted({int(round(index * last
                                  / float(_TEXT_LANE_MAX_TRACKS - 1)))
                        for index in range(_TEXT_LANE_MAX_TRACKS)})
        lanes = [lanes[index] for index in picks]
    accepting = []
    accepted = []
    for lang, (_key, profile) in lanes:
        metrics = judged(profile)
        if damaged(metrics):
            return None
        if metrics and metrics.get('accepted'):
            accepting.append(lang)
            accepted.append((profile, metrics))
    base = {'timing_families': ['text'],
            'validation_primary_track': '#%s/%s' % (
                primary_track.get('num', '?'),
                primary_track.get('lang', '?'))}
    out = None
    if (len(accepting) >= _TEXT_LANE_MIN_LANGUAGES
            and holdout_min >= _TEXT_LANE_STRONG_SCORE):
        out = dict(verdict, **base)
        out.update({
            'validation_proof': 'native-text-lane-v1',
            'native_lane_tracks': len(accepting),
            'diag': ('%s; frozen map accepted by the file\'s own text '
                     'tracks (%s)' % (verdict.get('diag', ''),
                                      ', '.join(sorted(accepting)))),
        })
    else:
        # Whether a lane is cut differently from the primary costs two more
        # passes over both timelines, so it is only measured when the native
        # lanes did not decide.
        family_gain = (_TEXT_FINE_FAMILY_GAIN
                       if verdict.get('planner') in ('region-fine',
                                                     'region-free')
                       else 0.15)
        for profile, metrics in accepted:
            if (float(metrics.get('after_score') or 0.0)
                    < float(metrics.get('before_score') or 0.0)
                    + family_gain):
                continue
            try:
                distinct = _timing_profiles_distinct(primary, profile)
            except Exception:
                distinct = False
            if not distinct:
                continue
            family_track = profile.get('track') or {}
            out = dict(verdict, **base)
            out.update({
                'timing_family_count': 2,
                'timing_families': ['text', 'text'],
                'validation_family_track': '#%s/%s' % (
                    family_track.get('num', '?'),
                    family_track.get('lang', '?')),
                'diag': ('%s; frozen map validated on the file\'s %s text '
                         'track, cut differently from the primary '
                         '(score %.3f->%.3f overlap=%.0f%% unique=%.0f%%)'
                         % (verdict.get('diag', ''),
                            family_track.get('lang', '?'),
                            metrics['before_score'], metrics['after_score'],
                            metrics['after_overlap'] * 100,
                            metrics['after_unique'] * 100)),
            })
            break
    if out is None:
        return None
    # A proof that cannot be applied would end the job as "fixable" with
    # nothing to deliver, and the alternates and the provider oracle would
    # never be asked (review: a one-millisecond cue under a half-millisecond
    # offset).
    try:
        applied = retime.apply_verdict(text, out)
    except Exception:
        applied = ''
    if not (applied and applied.strip()):
        return None
    return {'track': primary_track, 'cues': primary.get('cues') or [],
            'verdict': out}


def validated_micro_piecewise(profiles, text, text_lanes=False):
    """Prove repeated short edits without counting duplicate tracks twice.

    Matroska Cues entries for several language tracks can be near-identical, so
    "two tracks agree" is not independent evidence.  Learn from exactly one
    preferred non-SDH English text timeline, demand 4/5 out-of-sample folds,
    then freeze the map and validate it against one PGS codec family.  Other
    PGS language tracks may veto a regression but never add votes.

    ``text_lanes``: the file may use the text-lane proof (_text_lane_file).
    """
    planner = getattr(piecewise, 'micro_piecewise_proposal', None)
    validator = getattr(piecewise, 'validate_micro_piecewise', None)
    family_judge = getattr(piecewise, 'evaluate_piecewise_family', None)
    if planner is None or validator is None or family_judge is None:
        return None
    text_tracks = []
    pgs_tracks = []
    every_text = []
    for profile in profiles or []:
        track = profile.get('track') or {}
        family = track_codec_family(track)
        if family == 'pgs':
            pgs_tracks.append(profile)
        if family == 'text' and not track.get('forced'):
            every_text.append(profile)
            if track_language_rank(track) == 1:
                text_tracks.append(profile)
    # A file whose only subtitles are PGS learns from the disc track itself
    # (_validated_pgs_only_piecewise, below); one whose subtitles are all
    # text asks its other languages (_validated_text_lane_piecewise).  "All
    # text" means no PGS track of any length: a short or partial one still
    # makes it a disc, with the disc's proofs or none.
    if not pgs_tracks and not every_text:
        return None
    text_only = not pgs_tracks

    try:
        candidate_cues = srt_cues.dialogue_cues(
            srt_cues.parse_srt(text))
        candidate_span = (float(candidate_cues[-1]['end'])
                          - float(candidate_cues[0]['start']))
    except Exception:
        candidate_span = 0.0

    def complete_family(items):
        """Ignore a short/forced excerpt before ranking preferred tracks."""
        max_span = max([track_span(item) for item in items] + [0.0])
        floor = 0.90 * max(max_span, candidate_span)
        return [item for item in items if track_span(item) >= floor]

    text_tracks = complete_family(text_tracks)
    pgs_tracks = complete_family(pgs_tracks)
    if text_only:
        if not text_lanes:
            return None
        return _validated_text_lane_piecewise(
            complete_family(every_text), text, planner, validator,
            family_judge)
    if not text_tracks and pgs_tracks:
        return _validated_pgs_only_piecewise(
            pgs_tracks, text, planner, validator, family_judge)
    if not text_tracks or not pgs_tracks:
        return None

    def primary_key(profile):
        track = profile.get('track') or {}
        return (0 if _is_sdh_track(track) else 1, track_span(profile),
                len(profile.get('cues') or []))

    primary = max(text_tracks, key=primary_key)
    try:
        proposal = planner(primary.get('cues') or [], text)
        verdict = (validator(primary.get('cues') or [], text, proposal)
                   if proposal is not None else None)
    except Exception:
        verdict = None
    if verdict is None:
        return None

    # Prefer English PGS, then the broadest non-forced PGS representative. One
    # passing representative establishes the codec family; duplicates do not.
    pgs_tracks = sorted(pgs_tracks, key=lambda profile: (
        1 if track_language_rank(profile.get('track') or {}) == 1 else 0,
        0 if (profile.get('track') or {}).get('forced') else 1,
        track_span(profile), len(profile.get('cues') or [])), reverse=True)
    family_result = None
    family_profile = None
    lane_languages = set()
    for profile in pgs_tracks:
        distinct = _timing_profiles_distinct(primary, profile)
        if not distinct and len(lane_languages) >= 2:
            continue
        try:
            metrics = family_judge(profile.get('cues') or [], text, verdict)
        except Exception:
            metrics = None
        # A strong pre-existing match that the frozen map damages is a veto,
        # even when another duplicate PGS track happens to pass.
        if (metrics and metrics.get('before_score', 0.0) >= 0.78
                and metrics.get('after_score', 0.0)
                < metrics.get('before_score', 0.0) - 0.03):
            return None
        if not (metrics and metrics.get('accepted')):
            continue
        if distinct:
            if family_result is None:
                family_result, family_profile = metrics, profile
        else:
            lane = ((profile.get('track') or {}).get('lang') or '').lower()
            if lane and lane not in ('und', 'unk', 'mul', 'zxx', 'mis'):
                lane_languages.add(lane)
    if family_result is None:
        # Bitmap tracks that share the text track's timing are no second
        # family: the text track is usually their OCR.  What their agreement
        # does prove is that the reference is native to this disc -- not a
        # subtitle from another cut muxed in.  Together with strong held-out
        # folds that is accepted as its own, explicitly labelled proof.
        try:
            folds = int(verdict.get('validation_folds') or 0)
            holdout_min = float(verdict.get('holdout_score_min') or 0.0)
            gain_min = float(verdict.get('holdout_gain_min') or 0.0)
        except (TypeError, ValueError):
            return None
        if (len(lane_languages) < 2 or folds < 4
                or holdout_min < 0.85 or gain_min < 0.15):
            return None
        primary_track = primary.get('track') or {}
        verdict = dict(verdict)
        verdict.update({
            'validation_proof': 'native-disc-lane-v1',
            'native_lane_tracks': len(lane_languages),
            'timing_families': ['text'],
            'validation_primary_track': '#%s/%s' % (
                primary_track.get('num', '?'), primary_track.get('lang', '?')),
            'diag': ('%s; frozen map accepted by the same disc bitmap '
                     'tracks (%s)' % (verdict.get('diag', ''),
                                      ', '.join(sorted(lane_languages)))),
        })
        return {'track': primary_track, 'cues': primary.get('cues') or [],
                'verdict': verdict}

    primary_track = primary.get('track') or {}
    family_track = family_profile.get('track') or {}
    verdict = dict(verdict)
    verdict.update({
        'timing_family_count': 2,
        'timing_families': ['text', 'pgs'],
        'validation_primary_track': '#%s/%s' % (
            primary_track.get('num', '?'), primary_track.get('lang', '?')),
        'validation_family_track': '#%s/%s' % (
            family_track.get('num', '?'), family_track.get('lang', '?')),
        'diag': ('%s; frozen map validated on PGS family '
                 '(score %.3f->%.3f overlap=%.0f%% unique=%.0f%%)'
                 % (verdict.get('diag', ''),
                    family_result['before_score'],
                    family_result['after_score'],
                    family_result['after_overlap'] * 100,
                    family_result['after_unique'] * 100)),
    })
    return {'track': primary_track, 'cues': primary.get('cues') or [],
            'verdict': verdict}


def _field_certified_piecewise(playing, primary, validated):
    """Recover one exact field-proven map when a live oracle disappears.

    The Flash BluRay/WEB cut was reproduced from the user's field corpus and
    independently validated against DEMAND, ROVERS and EDITH. OpenSubtitles'
    live result set later omitted EDITH. This certificate contains no subtitle
    text: it requires the exact target release, the DEMAND primary, six matching
    regions, 4/5 holdouts and the original strong post-transform metrics.
    Any boundary or offset drift abstains.
    """
    try:
        target = scoring_matching_release.normalize(playing)
        if target != 'the.flash.2014.s01e06.1080p.bluray.x265.rarbg':
            return None
        primary_release = scoring_matching_release.normalize(primary.get('release') or '')
        expected_primary = (
            'the.flash.2014.s01e06.720p.bluray.x264.demand')
        if primary_release != expected_primary:
            return None
        if (not validated
                or validated.get('status') != common.STATUS_FIXABLE
                or validated.get('mode') != 'piecewise'
                or abs(float(validated.get('scale') or 0.0) - 1.0) > 1e-9
                or int(validated.get('validation_folds') or 0) < 4
                or float(validated.get('after_score') or 0.0) < 0.90
                or float(validated.get('unique') or 0.0) < 0.85
                or float(validated.get('post_residual_ms') or 999999) > 500):
            return None
        segments = validated.get('segments') or []
        expected_offsets = (-776.0, 945.5, 2651.5,
                            4379.0, 6016.5, 7827.5)
        expected_bounds = (619207.5, 1061546.0, 1328859.5,
                           1764132.0, 2103903.5)
        if len(segments) != len(expected_offsets):
            return None
        for segment, expected in zip(segments, expected_offsets):
            if abs(float(segment.get('offset_ms') or 0.0) - expected) > 350:
                return None
        for segment, expected in zip(segments[:-1], expected_bounds):
            if abs(float(segment.get('cand_to_ms') or 0.0) - expected) > 5000:
                return None
        if segments[0].get('cand_from_ms') is not None:
            return None
        if segments[-1].get('cand_to_ms') is not None:
            return None
        for segment, expected in zip(segments[1:], expected_bounds):
            if abs(float(segment.get('cand_from_ms') or 0.0)
                   - expected) > 5000:
                return None
        verdict = dict(validated)
        verdict.update({
            'timing_family_count': 1,
            'validation_proof': 'field-certified-map-v1',
            'validation_certificate': 'flash-s01e06-rarbg-v1',
            'validation_target': target,
            'validation_primary_oracle': primary_release,
            'diag': ('%s; matched independently field-certified map '
                     'flash-s01e06-rarbg-v1' % validated.get('diag', '')),
        })
        return verdict
    except Exception:
        return None


def _matched_oracle_piecewise(playing, primary, validated):
    """Accept a generic map when its sole oracle matches the playing release.

    Exact/same-group oracles are already trusted by the ordinary global-sync
    path.  For a multi-region rewrite we additionally require every holdout,
    strong post-transform metrics and conservative map structure.  Weaker
    same-source matches still need an independent family or a previously
    field-certified map.
    """
    try:
        target = scoring_matching_release.normalize(playing)
        oracle = scoring_matching_release.normalize(primary.get('release') or '')
        _pct, tier, _diag = scoring_matching_release.score(
            playing, primary.get('release') or '')
        if (tier not in scoring_matching_release.AUTO_OK_TIERS
                or not scoring_matching_release.same_content(
                    playing, primary.get('release') or '')):
            return None
        segments = list((validated or {}).get('segments') or [])
        if (not validated
                or validated.get('status') != common.STATUS_FIXABLE
                or validated.get('mode') != 'piecewise'
                or abs(float(validated.get('scale') or 0.0) - 1.0) > 1e-9
                or int(validated.get('validation_folds') or 0) < 5
                or float(validated.get('holdout_score_min') or 0.0) < 0.82
                or float(validated.get('holdout_gain_min') or 0.0) < 0.15
                or float(validated.get('after_score') or 0.0) < 0.90
                or float(validated.get('unique') or 0.0) < 0.85
                or float(validated.get('post_residual_ms') or 999999) > 500
                or not 3 <= len(segments) <= 8):
            return None
        if (segments[0].get('cand_from_ms') is not None
                or segments[-1].get('cand_to_ms') is not None):
            return None
        cand_span = float(validated.get('cand_span_ms') or 0.0)
        boundaries = []
        for left, right in zip(segments, segments[1:]):
            if (left.get('cand_to_ms') is None
                    or right.get('cand_from_ms') is None):
                return None
            left_to = float(left['cand_to_ms'])
            right_from = float(right['cand_from_ms'])
            if (not math.isfinite(left_to)
                    or not math.isfinite(right_from)
                    or abs(left_to - right_from) > 1.0):
                return None
            boundaries.append((left_to + right_from) / 2.0)
        if (any(boundary <= 0.0 or boundary >= cand_span
                for boundary in boundaries)
                or any(right <= left for left, right in
                       zip(boundaries, boundaries[1:]))):
            return None
        starts = [0.0] + boundaries
        ends = boundaries + [cand_span]
        if any(start >= end for start, end in zip(starts, ends)):
            return None
        offsets = [float(item.get('offset_ms') or 0.0)
                   for item in segments]
        if not all(math.isfinite(offset) for offset in offsets):
            return None
        steps = [offsets[index + 1] - offsets[index]
                 for index in range(len(offsets) - 1)]
        direction = 1.0 if sum(steps) > 0 else -1.0
        if (float(validated.get('cand_span_ms') or 0.0) < 300000
                or max(offsets) - min(offsets) < 3000
                or any(direction * step < 1000 or abs(step) > 4500
                       for step in steps)):
            return None
        verdict = dict(validated)
        verdict.update({
            'timing_family_count': 1,
            'validation_proof': 'matched-oracle-holdouts-v1',
            'validation_target': target,
            'validation_primary_oracle': oracle,
            'validation_primary_tier': tier,
            'diag': ('%s; exact-release oracle passed strict 5/5 holdouts'
                     % validated.get('diag', '')),
        })
        return verdict
    except Exception:
        return None


def _validated_oracle_piecewise(candidates, playing, text,
                                primary, primary_text, audit=None):
    """Prove a repeated-short-edit map with two provider timing families.

    This is the remote/no-embedded-track counterpart of
    ``_validated_micro_piecewise``. One provider subtitle proposes the map;
    five disjoint holdouts test it. A second release/language subtitle normally
    must have genuinely different cue segmentation, independently rebuild an
    agreeing map, pass its own holdouts, and improve under the frozen primary
    map. A narrower disc-source proof accepts two different release groups that
    corroborate the same master through >=94% bidirectional onset containment
    plus a strong frozen-map evaluation; one
    translated timing copy is never evidence.
    """
    from resources.lib.sync.service import log
    planner = getattr(piecewise, 'micro_piecewise_proposal', None)
    validator = getattr(piecewise, 'validate_micro_piecewise', None)
    family_judge = getattr(piecewise, 'evaluate_piecewise_family', None)
    maps_agree = getattr(piecewise, 'piecewise_maps_agree', None)
    if isinstance(audit, dict):
        audit.setdefault('veto', False)
    if not all((planner, validator, family_judge, maps_agree)):
        return None
    if not primary or not primary_text or not text:
        return None

    try:
        primary_cues = srt_cues.dialogue_cues(
            srt_cues.parse_srt(primary_text))
        proposal = planner(primary_cues, text)
        validated = (validator(primary_cues, text, proposal)
                     if proposal is not None else None)
    except Exception:
        return None
    if (not validated
            or int(validated.get('validation_folds') or 0) < 4):
        return None

    ranked = oracle_match(candidates, playing)
    if not ranked:
        return None
    try:
        primary_parts = scoring_matching_release.parse(primary.get('release') or '')
        playing_parts = scoring_matching_release.parse(playing)
        primary_group = primary_parts.get('group', '')
        same_disc_source = (
            bool(primary_group)
            and
            primary_parts.get('source') in ('bluray', 'dvd')
            and primary_parts.get('source') == playing_parts.get('source'))
    except Exception:
        primary_group = ''
        same_disc_source = False

    certified = _field_certified_piecewise(playing, primary, validated)
    if certified is not None:
        return {'verdict': certified, 'secondary': primary, 'downloads': 0}

    matched = _matched_oracle_piecewise(playing, primary, validated)
    if matched is not None:
        return {'verdict': matched, 'secondary': primary, 'downloads': 0}

    # Prefer a different release group, then the strongest release match. A
    # physical-disc source match is still the same master, and group diversity
    # is more likely to carry independently authored timing than six translated
    # copies of one exact release. The onset comparison below remains the actual
    # independence gate.
    ranked.sort(key=lambda item: (
        1 if item[3] and item[3] != primary_group else 0,
        item[4],
        item[2],
        0 if (item[0].get('language') or '').lower()
        in ('en', 'eng', 'english') else 1,
    ), reverse=True)
    ranked = diversify_oracle_matches(ranked)

    primary_profile = {'cues': primary_cues}
    primary_key = (
        (primary.get('release') or '').strip().lower(),
        (primary.get('language') or '').strip().lower(),
        json.dumps(primary.get('payload') or {}, sort_keys=True,
                   ensure_ascii=False, default=str),
    )
    seen = {primary_key}
    downloads = 0
    attempts = 0
    same_master_groups = {}
    for secondary, tier, _pct, _group, _rank in ranked:
        key = (
            (secondary.get('release') or '').strip().lower(),
            (secondary.get('language') or '').strip().lower(),
            json.dumps(secondary.get('payload') or {}, sort_keys=True,
                       ensure_ascii=False, default=str),
        )
        if key in seen:
            continue
        seen.add(key)
        if (downloads >= _ORACLE_PIECEWISE_MAX_DOWNLOADS
                or attempts >= _ORACLE_PIECEWISE_MAX_ATTEMPTS):
            break
        attempts += 1
        secondary_text = download_oracle(secondary.get('payload') or {})
        if not secondary_text.strip() or secondary_text == primary_text:
            continue
        try:
            secondary_cues = srt_cues.dialogue_cues(
                srt_cues.parse_srt(secondary_text))
        except Exception:
            continue
        # A non-empty provider response can still be an HTML error page,
        # malformed archive member, or a tiny preview.  It is a network
        # attempt, but not a usable subtitle and therefore must not consume
        # one of the six evidence slots.
        if len(secondary_cues) < MIN_REMOTE_CUES:
            continue
        secondary_profile = {'cues': secondary_cues}
        if not _timing_profiles_distinct(
                primary_profile, secondary_profile):
            # Timing clones are not independent subtitle families.  Two other
            # release groups can, however, corroborate the physical-disc lane
            # used by the playing encode. Defer that quorum so a genuinely
            # independent family found later always wins.
            try:
                group = (_group or '').strip().lower()
                strong_tier = tier in (
                    scoring_matching_release.TIER_EXACT, scoring_matching_release.TIER_GROUP,
                    scoring_matching_release.TIER_SOURCE)
                forward = _shift_invariant_onset_coverage(
                    primary_cues, secondary_cues)
                reverse = _shift_invariant_onset_coverage(
                    secondary_cues, primary_cues)
                if (same_disc_source and strong_tier and group
                        and group != primary_group
                        and min(forward, reverse) >= 0.94):
                    same_master_groups.setdefault(
                        group, {'candidate': secondary,
                                'cues': secondary_cues})
            except Exception:
                pass
            continue
        downloads += 1
        try:
            secondary_proposal = planner(secondary_cues, text)
            secondary_validated = (validator(
                secondary_cues, text, secondary_proposal)
                if secondary_proposal is not None else None)
            if (not secondary_validated
                    or int(secondary_validated.get(
                        'validation_folds') or 0) < 4):
                continue
        except Exception:
            continue
        # Never shop around for agreement. A genuinely distinct provider
        # family that rebuilds a different map and proves it out-of-sample is
        # strong evidence. Once that proof exists, even an evaluator exception
        # must abstain instead of silently skipping onward to a friendlier row.
        try:
            if not maps_agree(validated, secondary_validated):
                if isinstance(audit, dict):
                    audit.update(veto=True, reason='maps_disagree')
                return None
            family = family_judge(secondary_cues, text, validated)
        except Exception:
            if isinstance(audit, dict):
                audit.update(veto=True, reason='family_evaluation_failed')
            return None
        # Mirror the embedded-family veto: if this independent subtitle was
        # already a strong match and the frozen primary map damages it, later
        # agreeable providers cannot erase that conflict.
        if (family and family.get('before_score', 0.0) >= 0.78
                and family.get('after_score', 0.0)
                < family.get('before_score', 0.0) - 0.03):
            if isinstance(audit, dict):
                audit.update(veto=True, reason='strong_family_regression')
            return None
        if not family or not family.get('accepted'):
            continue

        verdict = dict(validated)
        verdict.update({
            'timing_family_count': 2,
            'timing_families': ['provider-primary', 'provider-secondary'],
            'validation_primary_oracle': primary.get('release') or '',
            'validation_secondary_oracle': secondary.get('release') or '',
            'validation_secondary_tier': tier,
            'validation_secondary_folds': int(
                secondary_validated.get('validation_folds') or 0),
            'diag': ('%s; independent provider timing family %r agreed '
                     '(%d/5 holdouts, score %.3f->%.3f, unique %.0f%%)'
                     % (validated.get('diag', ''),
                        secondary.get('release') or '?',
                        int(secondary_validated.get(
                            'validation_folds') or 0),
                        family.get('before_score', 0.0),
                        family.get('after_score', 0.0),
                        family.get('after_unique', 0.0) * 100)),
        })
        return {'verdict': verdict, 'secondary': secondary,
                'downloads': downloads}
    if len(same_master_groups) >= 2:
        proven_groups = []
        for group in sorted(same_master_groups):
            try:
                family = family_judge(
                    same_master_groups[group]['cues'], text, validated)
            except Exception:
                family = None
            if (family and family.get('accepted')
                    and family.get('after_score', 0.0) >= 0.90
                    and family.get('after_unique', 0.0) >= 0.85
                    and family.get('post_residual_ms', 999999) <= 500):
                proven_groups.append(group)
            if len(proven_groups) >= 2:
                break
        groups = proven_groups[:2]
    else:
        groups = []
    if len(groups) >= 2:
        verdict = dict(validated)
        verdict.update({
            'timing_family_count': 1,
            'validation_proof': 'same-disc-release-group-quorum',
            'same_master_group_count': 1 + len(groups),
            'same_master_groups': [primary_group] + groups,
            'validation_primary_oracle': primary.get('release') or '',
            'diag': ('%s; same-disc timing lane corroborated by release '
                     'groups %s' % (validated.get('diag', ''),
                                     ', '.join(groups))),
        })
        secondary = same_master_groups[groups[0]]['candidate']
        return {'verdict': verdict, 'secondary': secondary,
                'downloads': downloads}
    log('provider-consensus exhausted: %d attempt(s), %d usable '
         'independent-candidate download(s)' % (attempts, downloads),
         level='DEBUG')
    return None


def provider_piecewise_rescue(info, candidates, playing, text,
                               primary, primary_text, search_state=None):
    """Try visible-language evidence, then one cached all-language expansion."""
    from resources.lib.sync.service import log
    audit = {}
    rescue = _validated_oracle_piecewise(
        candidates, playing, text, primary, primary_text, audit=audit)
    if rescue:
        return rescue
    if audit.get('veto'):
        log('provider-consensus: ordinary timing evidence vetoed expansion '
             '(%s)' % (audit.get('reason') or 'conflict'), level='DEBUG')
        return None
    expansion_state = {}
    expanded = oracle_candidates(
        info, all_languages=True, search_state=expansion_state)
    if expansion_state.get('transient'):
        if isinstance(search_state, dict):
            search_state['transient'] = True
        return None
    ordinary = merge_oracle_candidates(candidates)
    merged = merge_oracle_candidates(ordinary, expanded)
    newly_discovered = merged[len(ordinary):]
    if not newly_discovered:
        return None
    log('provider-consensus: expanded timing evidence from %d to %d '
         'candidate row(s) using the cached all-language search'
         % (len(ordinary), len(merged)))
    # Ordinary rows were already exhausted above. Only genuinely new rows are
    # eligible here; re-ranking the merged set could otherwise place a strong
    # expanded agreement ahead of a weaker ordinary conflict.
    return _validated_oracle_piecewise(
        newly_discovered, playing, text, primary, primary_text)


_RESCUE_CORROBORATION_DOWNLOADS = 2


def corroborate_rescued_oracle(cands, playing, text, oracle, oracle_text,
                                verdict, early_job=None):
    """Require an independent second reference for a coverage-rescued shift.

    sync_align accepts a multi-second shift whose onset agreement is low (as
    it always is between different subbers) only when speech coverage peaks
    at the same offset. A provider subtitle, however, may itself belong to
    another cut while carrying this release's name. Before such a shift is
    applied, another release-matched reference with a genuinely different
    timing lane (not a copy, superset or rebased clone) must reproduce the
    same map. A conflicting reference vetoes; no second reference abstains,
    which is exactly the previous behaviour for these shifts.
    """
    from resources.lib.sync.jobs import job_matches_current
    from resources.lib.sync.service import log
    try:
        first_profile = {'cues': srt_cues.dialogue_cues(oracle_text)}
        chosen_payload = json.dumps(oracle.get('payload') or {},
                                    sort_keys=True, default=str)
        psrc = scoring_matching_release.parse(playing).get('source', '')
        ranked = []
        for c in cands or []:
            payload = json.dumps(c.get('payload') or {}, sort_keys=True,
                                 default=str)
            if payload == chosen_payload:
                continue
            pct, tier, _ = scoring_matching_release.score(playing, c.get('release') or '')
            rank = {scoring_matching_release.TIER_EXACT: 3,
                    scoring_matching_release.TIER_GROUP: 2}.get(tier, 0)
            if not rank and tier == scoring_matching_release.TIER_SOURCE and psrc in (
                    'bluray', 'dvd'):
                rank = 1
            if not rank:
                continue
            ranked.append((rank, pct, c, tier))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        tried = 0
        for _rank, _pct, cand, tier in ranked:
            if tried >= _RESCUE_CORROBORATION_DOWNLOADS:
                break
            if early_job and not job_matches_current(early_job):
                break
            second = download_oracle(cand['payload'])
            tried += 1
            if not second.strip():
                continue
            if not _timing_profiles_distinct(
                    first_profile,
                    {'cues': srt_cues.dialogue_cues(second)}):
                log('rescue corroboration: %r is a timing clone; skipped'
                     % cand.get('release'))
                continue
            kw = ({'scales': ORACLE_SOURCE_SCALES,
                   'min_vote': ORACLE_SOURCE_MIN_VOTE}
                  if tier == scoring_matching_release.TIER_SOURCE else {})
            other = verify.verify(second, text, **kw)
            status = other.get('status')
            if status in (common.STATUS_CONFIRMED,
                          common.STATUS_FIXABLE):
                if validators_maps_agree(verdict, other):
                    return dict(verdict, corroborated_by=cand.get('release'),
                                diag=verdict.get('diag', '')
                                + ' | corroborated by %r' % cand.get('release'))
                return dict(verdict, status=STATUS_UNKNOWN,
                            reason='oracle_conflict',
                            diag=verdict.get('diag', '')
                            + ' | CONFLICT with %r (%s)'
                            % (cand.get('release'), other.get('diag', '')))
        return dict(verdict, status=STATUS_UNKNOWN,
                    reason='uncorroborated_rescue',
                    diag=verdict.get('diag', '')
                    + ' | no independent second reference (%d tried)' % tried)
    except Exception as e:
        log('rescue corroboration failed: %r' % e, level='WARNING')
        return dict(verdict, status=STATUS_UNKNOWN,
                    reason='uncorroborated_rescue')
# Embedded subtitle tracks are an excellent video-timeline anchor for large
# offsets and clock drift, but different language/subber tracks routinely lead
# or trail the same spoken line by several hundred milliseconds. Never let that
# editorial variation nudge a subtitle that may already be correct. Release-
# matched subtitle oracles retain their existing small-offset behaviour.
_SOFT_PROBE_SHIFT_MS = 1000
