# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# What a subtitle text is: Hebrew or not, SDH, machine-translated.

import os
import re
from resources.lib.core.srt.blocks import SPEAKER_RE, block_text_only, parse_blocks


def looks_hebrew(text, min_alpha=120, min_ratio=0.5):
    """Rough sanity check that `text` is genuinely a Hebrew translation and not
    a failed / mostly-untranslated one. Among the alphabetic characters, Hebrew
    must dominate. If there's too little text to judge, returns True (never
    block on thin evidence). Used as a pool-upload quality gate."""
    heb = lat = 0
    for c in text or '':
        o = ord(c)
        if 0x0590 <= o <= 0x05FF:
            heb += 1
        elif ('a' <= c <= 'z') or ('A' <= c <= 'Z'):
            lat += 1
    alpha = heb + lat
    if alpha < min_alpha:
        return True
    return heb >= alpha * min_ratio


def untranslated_line_ratio(text, min_cues=10, min_len=8):
    """Fraction of substantial cues that look UNTRANSLATED -- i.e. they carry
    Latin letters but contain no Hebrew at all. Used as a pool-upload quality
    gate to catch a PARTIALLY-failed translation (whole chunks left in English)
    that `looks_hebrew` misses because the document is Hebrew overall.

    A cue with ANY Hebrew counts as translated (mixed Hebrew+English lines are
    fine). Only cues with real text (>= min_len letters) are weighed, so blank
    lines, '♪', numbers and short interjections don't skew it. Returns 0.0 when
    there are too few substantial cues to judge (never block on thin evidence)
    -- so a handful of legitimately-English lines (song lyrics, on-screen
    signs, 'FBI') can't trip the gate; only a large proportion does."""
    substantial = 0
    untranslated = 0
    for block in parse_blocks(text or ''):
        line = block_text_only(block)
        if not line:
            continue
        heb = lat = 0
        for c in line:
            o = ord(c)
            if 0x0590 <= o <= 0x05FF:
                heb += 1
            elif ('a' <= c <= 'z') or ('A' <= c <= 'Z'):
                lat += 1
        if (heb + lat) < min_len:
            continue  # too little text to judge this cue
        substantial += 1
        if heb == 0 and lat > 0:
            untranslated += 1
    if substantial < min_cues:
        return 0.0
    return untranslated / float(substantial)


def is_google_translated(path):
    """True when a '<path>.google' sidecar marks this file as a Google Translate
    fallback. Lives here, next to the rule that consumes it, so there is exactly
    ONE implementation -- translate.py calls this rather than keeping its own
    copy. A signal with two readers drifts the same way a rule with two homes.

    Deliberately does NOT swallow errors, because its two callers want OPPOSITE
    answers when the signal cannot be read: refusing to strip is the safe
    failure for a repair that deletes text, while refusing to SHARE is the safe
    failure for pool contribution. A single fail direction here would be wrong
    for one of them, so each catches and decides. (os.path.exists already
    absorbs ordinary filesystem errors; this is about a malformed path.)
    """
    return bool(path) and os.path.exists(path + '.google')


# --- content-based SDH detection (Phase 3) -----------------------------------
# Music glyphs an SDH sub uses to mark lyrics.
_SDH_MUSIC_GLYPHS = '♪♫♬♩'
# A bracketed cue that carries a real sound/action description -- REQUIRES two
# consecutive letters inside, so a bare "[2020]" / "(?)" / "(!)" never counts
# (those are not SDH markers). Classes are bracket-free so there is no
# catastrophic backtracking on cue-sized text.
_SDH_BRACKET_RE = re.compile(
    r'[\[\(\{][^\[\]\(\){}]*[A-Za-z]{2,}[^\[\]\(\){}]*[\]\)\}]')


def sdh_content_stats(text):
    """Scan cue entries for hearing-impaired / SDH content markers: a bracketed
    sound/action cue ("[door creaks]"), a leading ALL-CAPS speaker label
    ("MABEL:"), or a music glyph. Returns (total_entries, annotated_entries,
    ratio). A plain sub scores ~0; an SDH sub is heavily annotated. Only counts
    entries that actually have dialogue text. Never raises."""
    try:
        blocks = parse_blocks(text)
    except Exception:
        return (0, 0, 0.0)
    total = 0
    annotated = 0
    for b in blocks:
        try:
            t = block_text_only(b)
        except Exception:
            continue
        if not t:
            continue
        total += 1
        if (any(g in t for g in _SDH_MUSIC_GLYPHS)
                or _SDH_BRACKET_RE.search(t)
                or any(SPEAKER_RE.match(ln.strip()) for ln in t.split('\n'))):
            annotated += 1
    ratio = (annotated / float(total)) if total else 0.0
    return (total, annotated, ratio)


def is_sdh_content(text, min_entries=20, min_annotated=12, min_ratio=0.12):
    """Conservative content-based SDH classifier. HIGH precision is the explicit
    design goal (zero false positives): it returns True ONLY when a substantial,
    DENSE fraction of entries carry SDH markers -- a regular subtitle (whose
    marker ratio sits well under a couple of percent) can never cross all three
    thresholds, while a genuinely hearing-impaired sub clears them with margin.
    Used AFTER download (the text isn't available when the list is built) to
    learn a release's SDH-ness for future ranking. Never raises."""
    try:
        total, annotated, ratio = sdh_content_stats(text)
    except Exception:
        return False
    return total >= min_entries and annotated >= min_annotated and ratio >= min_ratio
