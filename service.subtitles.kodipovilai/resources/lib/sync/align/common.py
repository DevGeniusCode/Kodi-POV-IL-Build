# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Align: constants and helpers shared by the align modules.



TOL = 500            # offset histogram bin (ms)
MAXOFF = 250000      # search window: just past MAX_PLAUSIBLE_OFFSET_MS;
MIN_CUES = 8          # min dialogue cues on each side
MIN_OVERLAP = 0.80    # >=80% of ref cues must overlap after the map
SCALE_MIN, SCALE_MAX = 0.90, 1.11
CONFIRM_OFFSET_MS = 350   # |offset| <= this and scale==1.0 -> already synced

STATUS_CONFIRMED = 'CONFIRMED'
STATUS_FIXABLE = 'FIXABLE'
STATUS_UNKNOWN = 'UNKNOWN'
LOCAL_MIN_REF_CUES = 40

# Some broadcast/stream masters insert a short bumper or black-frame pad at
# several act boundaries.  Against another master this is neither one global
# delay nor a normal FPS clock: the offset grows in small, repeated steps.  A
# single timing track is not enough evidence to rewrite a whole subtitle for
# such a subtle edit, so the planner below only PRODUCES a proposal.  The media
# layer must prove it on held-out timing cues (or genuinely independent timing
# families) before ``apply_verdict`` will accept it.  These bounds describe that
# deliberately narrow proposal space; ordinary >=5-second cuts continue through
# the mature piecewise path above.
MICRO_MIN_STEP_MS = 1000
MICRO_MAX_STEP_MS = 4500
MICRO_MAX_SEGMENTS = 8
# "Materially better than leaving it untouched" is measured by
# _timing_score, whose onset part counts lines within 450 ms.  A staircase of
# large steps leaves almost no line there before the fix, so +0.15 was easy;
# one of sub-second steps leaves many (the broadcast-cut Hebrew above gained
# 0.156-0.177 on the file's English track and 0.11-0.17 on its other
# languages, although its last act ran 2.3 s late).  +0.10 still means about
# four lines in ten moved from more than 450 ms off to within it.
PIECEWISE_MIN_GAIN = 0.15
REGION_FINE_MIN_GAIN = 0.10


# A FIXABLE offset beyond this is almost surely a spurious histogram peak,
# not a real desync -- real wrong-release offsets are seconds, not minutes
# (recuts are refused anyway). Field case: a sparse 10-cue audio reference
# against a dense candidate "found" offset=-350s and shifted the subs out of
# sight.
MAX_PLAUSIBLE_OFFSET_MS = 240000
TIGHT_MIN = 0.65   # matches at this tolerance are rare (~0.2/ref)
UNIQUE_MIN = 0.65
_EDGE_DIV = 25
ENV_NEAR_MS = 900.0
