# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# SRT cues for timing work: tolerant parse, preflight, dialogue-only cues.

import re
import math
import statistics
from resources.lib.core.srt.blocks import TC_PAIR
from resources.lib.sync.align.common import MIN_CUES, STATUS_UNKNOWN


# Free mode: fine mode without the one-direction rule and the 4.5 s ceiling.
# Cuts do not only pad act breaks; one cut can also be shorter in a later
# act (measured on Supernatural S05E01, AMZN WEB-DL, broadcast-cut Hebrew
# against the file's English track: -0.03, +1.98, +2.82, +0.88 and +1.82 s,
# every plateau 57-176 lines).  One reference cannot tell such a zigzag from
# a subber's uneven timing, which is why the planners above refuse it and
# why this mode is never offered by default: a caller must ask for it and
# must hold a proof that does not rest on the reference alone (the playing
# file's other subtitle languages).


# ---- SRT parsing -----------------------------------------------------------

TIME_RE = re.compile(TC_PAIR)

_TAG_RE = re.compile(r'<[^>]+>|\{\\[^}]*\}')

# Credit/watermark lines (translator credits, site plugs) cluster at the very
# start/end of subtitle files and do NOT correspond to dialogue -- they poison
# the histogram, so cues that are clearly credits are dropped before aligning.
_CREDIT_RE = re.compile(
    r'(תורגם|תרגום|סונכרן|סנכרון|כתוביות|הובא|צפייה מהנה|subs?\s*by|'
    r'subtitles?\s+by|sync(?:ed)?\s+by|corrected\s+by|www\.|https?://|\.com|'
    r'\.net|\.org|opensubtitles|subscene|ktuvit|wizdom)', re.I)


def to_ms(h, m, s, ms):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms.ljust(3, '0'))


def ms_to_stamp(ms):
    if ms < 0:
        ms = 0
    ms = int(round(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return '{0:02d}:{1:02d}:{2:02d},{3:03d}'.format(h, m, s, ms)


def parse_srt(text):
    """[{'start': ms, 'end': ms, 'text': str}] for every timed block.
    Tolerant: skips malformed blocks, handles BOM/CRLF, '.' or ',' millis."""
    from resources.lib.sync.align.estimate import breathe
    cues = []
    if not text:
        return cues
    text = text.lstrip('﻿')
    for block in re.split(r'\r?\n\r?\n+', text):
        breathe()
        m = TIME_RE.search(block)
        if not m:
            continue
        start = to_ms(*m.group(1, 2, 3, 4))
        end = to_ms(*m.group(5, 6, 7, 8))
        if end < start:
            continue
        body = block[m.end():].strip()
        body = _TAG_RE.sub('', body)
        cues.append({'start': start, 'end': end, 'text': body.strip()})
    cues.sort(key=lambda c: c['start'])
    return cues


def preflight_srt(text, allow_instant=False):
    """Strict, original-order SRT validation for an automatic rewrite.

    The tolerant public parser is useful for reading imperfect downloads, but
    it is deliberately unsafe as an edit precondition: it skips malformed
    blocks and sorts non-chronological input.  An automatic synchronizer must
    instead leave any questionable file byte-for-byte alone.
    """
    from resources.lib.sync.align.estimate import breathe
    if not isinstance(text, str) or not text.strip():
        return [], 'empty subtitle'
    cues = []
    previous_start = None
    blocks = [b for b in re.split(r'\r?\n\r?\n+', text.lstrip('\ufeff'))
              if b.strip()]
    if not blocks:
        return [], 'empty subtitle'
    for number, block in enumerate(blocks, 1):
        breathe()
        # More than one timing arrow means missing block separation; no parser
        # should silently keep the first cue and throw the remainder away.
        if block.count('-->') != 1:
            return [], 'malformed timing block %d' % number
        match = TIME_RE.search(block)
        if match is None:
            return [], 'unparseable timing block %d' % number
        start = to_ms(*match.group(1, 2, 3, 4))
        end = to_ms(*match.group(5, 6, 7, 8))
        # A cue that ends before it starts is a damaged file.  One that ends
        # WHEN it starts is merely invisible (no player shows it): subbing
        # tools leave such markers at act breaks (four of them in every
        # Blu-ray-lineage Hebrew file of Arrow S01E01, 4 of 29 files
        # surveyed).  In the subtitle being re-timed (``allow_instant``) it
        # stays in the list, so a cut is never placed across it carelessly
        # and the rewrite carries it along unchanged in length;
        # dialogue_cues() keeps it out of every timing comparison.  A
        # reference is still read strictly.
        if end < start or (end == start and not allow_instant):
            return [], 'non-positive cue duration at block %d' % number
        if previous_start is not None and start < previous_start:
            return [], 'non-chronological cue start at block %d' % number
        previous_start = start
        body = _TAG_RE.sub('', block[match.end():].strip())
        cues.append({'start': start, 'end': end, 'text': body.strip()})
    if not any(cue['end'] > cue['start'] for cue in cues):
        return [], 'no cue with a duration'
    return cues, ''


def tolerant_reference(text):
    """Timing skeleton of a reference SRT that failed the strict preflight.

    The strict preflight protects files we REWRITE. A reference is only read
    for its timestamps, and provider files routinely break the strict layout
    without any timing damage -- most often a blank line inside a credit or
    song cue (field: an OpenSubtitles HDTV reference refused at "malformed
    timing block 159" for exactly that). Accept the tolerant parse only when
    virtually every timing line survives it intact.
    """
    if not isinstance(text, str):
        return None
    # Timing itself must be intact and in order; only the block LAYOUT may
    # be imperfect (non-positive or reordered cues still refuse).
    timings = [(to_ms(*m.group(1, 2, 3, 4)), to_ms(*m.group(5, 6, 7, 8)))
               for m in TIME_RE.finditer(text)]
    if len(timings) < MIN_CUES:
        return None
    previous = None
    for start, end in timings:
        if end <= start or (previous is not None and start < previous):
            return None
        previous = start
    cues = parse_srt(text)
    if len(cues) < max(MIN_CUES, int(0.97 * len(timings))):
        return None
    return cues


def preflight_cues(cues):
    """Validate an already-parsed timing skeleton without reordering it."""
    out = []
    previous_start = None
    for number, cue in enumerate(cues or [], 1):
        if not isinstance(cue, dict) or 'start' not in cue or 'end' not in cue:
            return [], 'malformed cue %d' % number
        try:
            start = float(cue['start'])
            end = float(cue['end'])
        except (TypeError, ValueError):
            return [], 'non-numeric cue %d' % number
        if not math.isfinite(start) or not math.isfinite(end):
            return [], 'non-finite cue %d' % number
        if end <= start:
            return [], 'non-positive cue duration at cue %d' % number
        if previous_start is not None and start < previous_start:
            return [], 'non-chronological cue start at cue %d' % number
        previous_start = start
        out.append(dict(cue, start=start, end=end))
    if not out:
        return [], 'empty cue list'
    return out, ''


def preflight_unknown(error):
    return {'status': STATUS_UNKNOWN, 'scale': 1.0, 'offset_ms': 0.0,
            'vote': 0.0, 'overlap': 0.0, 'reason': 'malformed',
            'diag': 'preflight FAILED: ' + error}


def _is_dialogue(c):
    from resources.lib.sync.align.estimate import breathe
    breathe()
    t = (c.get('text') or '').strip()
    if not t or t.startswith(('♪', '♫', '#')):
        return False
    # Python's Unicode-aware predicate avoids silently dropping a whole
    # helper track just because its script was not listed here.  The former
    # hand-written ranges omitted Devanagari (Hindi) and Greek, so those
    # otherwise-valid timing references collapsed to zero cues.
    letters = ''.join(ch for ch in t if ch.isalpha())
    return len(letters) >= 2


def dialogue_cues(text_or_cues):
    """Dialogue-only cues (SFX/music and credit/watermark lines dropped)."""
    cues = (text_or_cues if isinstance(text_or_cues, list)
            else parse_srt(text_or_cues))
    # (A zero-length cue is never on screen, so it is never evidence.)
    out = [c for c in cues if c['end'] > c['start'] and _is_dialogue(c)]
    if not out:
        return out
    span_end = out[-1]['end']
    kept = []
    for c in out:
        near_edge = c['start'] < 120000 or c['start'] > span_end - 120000
        if near_edge and _CREDIT_RE.search(c['text']):
            continue
        kept.append(c)
    return kept


def cues_median(values):
    return float(statistics.median(values))
