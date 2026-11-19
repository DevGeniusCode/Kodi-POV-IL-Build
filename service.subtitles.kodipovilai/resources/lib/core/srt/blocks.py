# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# SRT blocks: parse, timecodes, rebuild, chunking and stitching.

import re


BLOCK_SEPARATOR = re.compile(r'\r?\n\r?\n')
# Also strip ALL-CAPS speaker prefixes like "MABEL: ..." that are
# common in HI subs but redundant for translation.
_SPEAKER_RE = re.compile(
    r'^[A-Z][A-Z0-9 \'\.\-]{1,30}:\s*'
)
_INDEX_RE = re.compile(r'^\d+$')
_TIMECODE_RE = re.compile(
    r'^\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}\s*-->\s*'
    r'\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}'
)
# Punctuation that goes at the end of a Hebrew sentence but the AI
# sometimes outputs at the start.
_TRAILING_PUNCT_CHARS = '.,;:!?…'


# Invisible BiDi / direction-control / BOM characters that Gemini
# (and other LLMs) sometimes insert at the START of a Hebrew line.
# When they're there, my leading-punct regex misses the punct that
# follows them, so the line never gets corrected. We strip these
# before checking, then drop them entirely from the output (they're
# noise for SRT rendering -- Kodi handles RTL via the text content
# alone).
_INVISIBLE_BIDI = (
    '‎'  # LRM
    '‏'  # RLM
    '‪'  # LRE
    '‫'  # RLE
    '‬'  # PDF
    '‭'  # LRO
    '‮'  # RLO
    '⁦'  # LRI
    '⁧'  # RLI
    '⁨'  # FSI
    '⁩'  # PDI
    '﻿'  # BOM / ZWNBSP
)

# Explicit RTL embedding controls used by the 'rtl_base' rtl_punct_mode (see
# fix_rtl_punctuation). RLE forces a right-to-left BASE direction for the wrapped
# run regardless of the renderer's paragraph base; PDF ends it. Both are in
# _INVISIBLE_BIDI above, so the wrap is idempotent (a re-run strips them first).
_RLE = '‫'   # RIGHT-TO-LEFT EMBEDDING
_PDF = '‬'   # POP DIRECTIONAL FORMATTING


# --- entries the model welded together -------------------------------------
# Field report (0.2.495): one cue rendered its own Hebrew line AND the raw
# "286" + "00:15:51,284 --> 00:15:54,054" of the cue that should have come
# after it, all on screen at once. The next cue was fine.
#
# A blank line is the ONLY thing that separates SRT entries, so when the model
# omits one while copying a chunk back, two entries arrive as a single block --
# and every stage after this one then treats the second entry's index and
# timecode as TEXT belonging to the first. restore_block_timings hands the
# merged block the first source block's header back and leaves the rest of it
# alone; the reply looks exactly one entry short, which the caller tolerates
# (it accepts up to 15% loss without retrying); and the player draws the lot.
#
# Splitting them back apart is unambiguous, which is what makes this safe to do
# on every parse rather than behind a heuristic:
#   * a digits-only line whose next non-empty line is a timecode is an entry
#     header. A bare number DOES occur in real dialogue -- a year, a score, a
#     countdown -- but never with "00:00:00,000 --> 00:00:00,000" under it.
#   * a timecode line anywhere but at the head of a block is impossible as
#     dialogue on its own.
# A well-formed block holds exactly one timecode line and leaves here untouched,
# so this costs one scan and changes nothing for input that was already correct.
def _has_text_line(lines, lo, hi):
    """True when lines[lo:hi] would still say something on screen.

    A digits-only line counts as TEXT unless it is itself an entry header --
    that is, unless a timecode follows it. The first version treated every
    digits-only line as a header, which meant a cue whose only dialogue IS a
    number could never satisfy this test: asked whether entry 41 (text: "42")
    still had something to say, it answered no, so the cut stayed at the
    timecode and the NEXT entry's index line ("50") was left behind as a second
    line of dialogue that nobody ever wrote. Same disambiguation as the split
    itself uses, and for the same reason -- digits alone are ambiguous, digits
    followed by a timecode are not.
    """
    for j in range(lo, min(hi, len(lines))):
        s = lines[j].strip()
        if not s or _TIMECODE_RE.match(s):
            continue
        if _INDEX_RE.match(s):
            k = j + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines) and _TIMECODE_RE.match(lines[k].strip()):
                continue          # a header, not something the viewer reads
        return True
    return False


def _split_welded_block(block):
    """Split a block that carries a LATER entry's header inside its text."""
    lines = block.split('\n')
    tc_at = [i for i, ln in enumerate(lines) if _TIMECODE_RE.match(ln.strip())]
    if len(tc_at) < 2:
        return [block]
    cuts, prev = [], 0
    for i in tc_at[1:]:
        # The index line belongs to the entry its timecode opens, so cut BEFORE
        # the index -- cutting between the two would strand the number as the
        # last line of the previous cue, which is the same defect one line
        # smaller.
        start = i
        if i and _INDEX_RE.match(lines[i - 1].strip()):
            # ...UNLESS taking that line leaves the entry we are closing with
            # no text at all. Then the digits are not the next entry's index,
            # they are this entry's ONLY line of dialogue -- a score, a house
            # number, an answer shouted back -- and cutting in front of them
            # DELETES them: the cue above loses its text and the cue below
            # swallows the number as its index, so the number never reaches the
            # screen at all. Leaving it where it is costs nothing, because the
            # block below gets its index restored from the source either way.
            # Silent deletion is a worse outcome than the weld we came to fix.
            if _has_text_line(lines, prev, i - 1):
                start = i - 1
        if start > prev:
            cuts.append(start)
            prev = start
    if not cuts:
        return [block]
    out, prev = [], 0
    for cut in cuts + [len(lines)]:
        part = '\n'.join(lines[prev:cut])
        if part.strip():
            # rstrip the CR too. Splitting a CRLF block mid-way leaves the last
            # line's '\r' with no '\n' behind it, which a block that came
            # straight from BLOCK_SEPARATOR never carries (the separator eats
            # it), and a caller that rejoins blocks without stripping would
            # carry that artefact into the file.
            out.append(part.strip('\r\n'))
        prev = cut
    return out or [block]


def parse_blocks(text):
    """Return a list of raw entry blocks (still strings). We don't
    bother with a structured parse since the model handles the
    timecodes verbatim -- if we round-trip strings unchanged for
    those, we minimise damage from accidental edits.

    A block that holds more than one entry's header (the model dropped the
    blank line between them) is split back apart first -- see above."""
    if not text:
        return []
    # Some SRTs start with a BOM. Strip it once.
    if text.startswith('﻿'):
        text = text[1:]
    text = text.strip()
    blocks = []
    for b in BLOCK_SEPARATOR.split(text):
        if b.strip():
            blocks.extend(_split_welded_block(b))
    return blocks
# INVARIANT: _OVERLAP_GRACE_MS and _MAX_CUE_MS must both exceed _MIN_CUE_MS.
# The ceiling is always start+_MAX_CUE_MS or start+_OVERLAP_GRACE_MS(+gap), so
# this is what guarantees the minimum-duration floor can never be pushed past
# the ceiling and re-create the overlap the ceiling exists to prevent.

_TIME_PAIR_RE = re.compile(
    r'^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})(.*)$')


def _tc_ms(h, m, sec, ms):
    return h * 3600000 + m * 60000 + sec * 1000 + ms


def _fmt_tc(ms):
    if ms < 0:
        ms = 0
    h, rem = divmod(int(ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, msec = divmod(rem, 1000)
    return '{0:02d}:{1:02d}:{2:02d},{3:03d}'.format(h, m, s, msec)


def _block_timecode_index(lines):
    """Index of the timecode line inside a block's lines, or -1."""
    for i, ln in enumerate(lines[:3]):
        if _TIMECODE_RE.match(ln.strip()):
            return i
    return -1


def _block_times(block):
    """(start_ms, end_ms, trailing) for a block, or None when it has no
    parsable timecode line."""
    for ln in block.split('\n')[:3]:
        m = _TIME_PAIR_RE.match(ln.strip())
        if m:
            g = [int(x) for x in m.groups()[:8]]
            return (_tc_ms(*g[:4]), _tc_ms(*g[4:]), m.group(9) or '')
    return None


def _block_start(block):
    t = _block_times(block)
    return None if t is None else t[0]


def _block_declared_index(block):
    """The numeric SRT index attached to a block, or None.

    A generated block may begin directly with a timecode after dropping its
    index. That is still parseable for by-start alignment, but it cannot provide
    the second independent anchor needed to repair a corrupted start time.
    """
    try:
        lines = block.split('\n')
        ti = _block_timecode_index(lines)
        if ti > 0 and lines[ti - 1].strip().isdigit():
            return int(lines[ti - 1].strip())
        return None
    except Exception:
        return None


def _rebuild_block(src_block, out_block):
    """out_block's text under src_block's index + timecode. None if either
    block isn't SRT-shaped (caller keeps the original)."""
    src_lines = src_block.split('\n')
    out_lines = out_block.split('\n')
    si = _block_timecode_index(src_lines)
    oi = _block_timecode_index(out_lines)
    if si < 0 or oi < 0:
        return None
    return '\n'.join(src_lines[:si + 1] + out_lines[oi + 1:])


def chunk_blocks(blocks, per_chunk=250):
    """Yield groups of `per_chunk` blocks. Last group may be smaller."""
    if per_chunk < 1:
        per_chunk = 1
    for i in range(0, len(blocks), per_chunk):
        yield blocks[i:i + per_chunk]


def block_text_only(block):
    """Return just the dialogue text from a single SRT entry block,
    stripping the entry number and the timecode line. Returns ''
    if the block isn't shaped like SRT.

    Used by the cross-chunk context feature in translate.py: we
    feed the last N source-text lines of the previous chunk to the
    AI as "PREVIOUS DIALOGUE CONTEXT" so the model has the same
    conversational thread it would have had if everything ran in
    one giant chunk -- which catches the cross-chunk gender drift
    that the per-chunk-cast block alone can't prevent.
    """
    if not block:
        return ''
    lines = block.strip().split('\n')
    # First line: entry number. Second line: timecode arrow.
    # Everything from line 3 onward is the dialogue text.
    # We're tolerant: if the entry number is missing (some scrapers
    # emit unnumbered SRT) we accept and start at the timecode.
    start = 0
    if lines and lines[0].strip().isdigit():
        start = 1
    if start < len(lines) and '-->' in lines[start]:
        start += 1
    return '\n'.join(lines[start:]).strip()


def stitch_blocks(blocks):
    """Join blocks back into a single SRT body using CRLF blank lines
    between entries (standard SRT delimiter). Trailing newline so
    Kodi's parser is happy."""
    return '\r\n\r\n'.join(b.strip() for b in blocks) + '\r\n'


def count_entries(text):
    return len(parse_blocks(text))
# Deliberately NOT included: U+1EE00-1EEFF (Arabic Mathematical Alphabetic
# Symbols) -- notation, not text, and outside the BMP; and U+206C/206D, two
# deprecated formatting controls. Everything else Unicode names ARABIC is
# covered; a field report of a stray letter INSIDE a Hebrew word is what turned
# up the Extended-B hole, so this list is now verified against the full
# character database rather than assembled by hand.
_HEBREW_CH = u'\u0590-\u05ff\ufb1d-\ufb4f'
_HAS_HEBREW_RE = re.compile(u'[' + _HEBREW_CH + u']')


# Markup and explicitly quoted literals are data, not alphabet-slip evidence.
_PROTECTED_RE = re.compile(
    r"<!--.*?-->|</?[A-Za-z][A-Za-z0-9:_-]*(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>"
    r'|"[^"\n]*"|“[^”\n]*”|«[^»\n]*»', re.DOTALL)


# --- AI output hygiene ------------------------------------------------------
# Two defects reported from the field, both from the same gap: nothing cleans
# the text the model returns before it is written and handed to the player.

# Written with \u escapes ONLY, never with the characters themselves. A
# literal U+FB1D pasted into source is one editor-normalisation away from
# becoming the yod+hiriq PAIR it decomposes to, which silently turns the
# class into a range STARTING AT U+05B4 -- one that swallows Arabic, CJK and
# the Latin ligatures. That exact mistake was made in this file and caught by
# a test. Do not replace these with the characters they stand for.
_HEB_RE = re.compile('[\u0590-\u05FF\uFB1D-\uFB4F]')
