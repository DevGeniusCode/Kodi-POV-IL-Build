# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Post-fix: Hebrew RTL punctuation, music mojibake and the wrapped-line renderer.

import re
from resources.lib.core.srt.blocks import INDEX_RE, INVISIBLE_BIDI, PDF, RLE, TIMECODE_RE, TRAILING_PUNCT_CHARS


# Hebrew letter range for RTL post-processing.
_HEB_LETTER = r'֐-׿'

# Match a Hebrew text line that has a misplaced punctuation prefix.
# Supports several common wrappers / decorations we need to ignore:
#   - leading dialogue dash: "- " (single dash + space)
#   - leading HTML tags (italic, bold, color, etc.): "<i>", "<b>"
#   - trailing HTML close tags: "</i>"
# The captured groups let us rebuild the line with the punct moved
# to its correct position while preserving the wrappers around it.
_LEADING_PUNCT_RE = re.compile(
    r'^(?P<dash>-\s+)?'                                 # optional "- " dialogue marker
    r'(?P<open_tags_a>(?:<[a-zA-Z!][^>]*>)*)'           # opening tags BEFORE the punct
    r'(?P<leading>[' + TRAILING_PUNCT_CHARS + r']+)\s*'  # the misplaced punct
    r'(?P<open_tags_b>(?:<[a-zA-Z!][^>]*>)*)'           # opening tags AFTER the punct
                                                         # (covers ".<i>text</i>")
    r'(?P<rest>[' + _HEB_LETTER + r'][^\n]*?)'          # Hebrew body (non-greedy)
    r'(?P<close_tags>(?:</[a-zA-Z][^>]*>)*)\s*$'        # zero or more closing tags
)
# A second physical-RTL shape seen in human Hebrew subtitle archives puts a
# sentence mark *and its closing quote/bracket* at the byte start.  For
# example, the logical second line of a quotation ``...אלן",`` is stored as
# ``,"...אלן``.  _LEADING_PUNCT_RE intentionally does not accept the quote
# between the mark and the first Hebrew letter, so handle this stronger and
# unambiguous signature separately.  The closer run is reversed when returned
# to logical order: `,"` -> `",`, `.)` -> `).`.
_LEADING_PUNCT_CLOSER_RE = re.compile(
    r'^(?P<dash>-\s+)?'
    r'(?P<open_tags_a>(?:<[a-zA-Z!][^>]*>)*)'
    r'(?P<leading>[' + TRAILING_PUNCT_CHARS + r']+)'
    r'(?P<closers>["\'\u05f3\u05f4\u2019\u201d\)\]\}\u00bb]+)\s*'
    r'(?P<open_tags_b>(?:<[a-zA-Z!][^>]*>)*)'
    r'(?P<rest>[' + _HEB_LETTER + r'][^\n]*?)'
    r'(?P<close_tags>(?:</[a-zA-Z][^>]*>)*)\s*$'
)
# Detect a pure ellipsis (".." or "..." or more) -- legitimate
# continuation marker, don't move it.
_ELLIPSIS_RE = re.compile(r'^(?:\.{2,}|…+)$')


def _fix_one_text_line(line, move_ellipsis=False):
    """Apply the RTL punctuation correction to a single text line
    (not an index or timecode line). Returns the corrected line."""
    stripped = line.strip()
    # Strip any leading invisible BiDi / BOM characters that would
    # otherwise hide the punct from our regex.
    while stripped and stripped[0] in INVISIBLE_BIDI:
        stripped = stripped[1:]
    # Also strip from the end -- Gemini occasionally appends them too.
    while stripped and stripped[-1] in INVISIBLE_BIDI:
        stripped = stripped[:-1]
    if not stripped:
        return line
    paired = _LEADING_PUNCT_CLOSER_RE.match(stripped)
    if paired:
        dash = paired.group('dash') or ''
        open_tags = ((paired.group('open_tags_a') or '')
                     + (paired.group('open_tags_b') or ''))
        leading = paired.group('leading') or ''
        closers = paired.group('closers') or ''
        rest = paired.group('rest') or ''
        close_tags = paired.group('close_tags') or ''
        if not rest:
            return stripped if stripped != line.strip() else line
        # ``..."שלום`` can be a perfectly logical continuation that opens a
        # quotation.  Like a bare leading ellipsis, the paired form is
        # ambiguous and may move only after another signature has identified
        # the whole file as physical-order legacy text.
        if _ELLIPSIS_RE.match(leading) and not move_ellipsis:
            return stripped if stripped != line.strip() else line
        # The whole neutral suffix was written in visual order at the start;
        # restore its logical character order at the end.  If the body already
        # ends in the same closer, do not manufacture a duplicate quote or
        # bracket; only the displaced sentence mark still needs to move.
        logical_closers = closers[::-1]
        suffix = (leading if rest.endswith(logical_closers)
                  else logical_closers + leading)
        if rest.endswith(suffix):
            suffix = ''
        return dash + open_tags + rest + suffix + close_tags

    m = _LEADING_PUNCT_RE.match(stripped)
    if not m:
        # No leading punct -- but if we stripped invisible chars,
        # the rewritten stripped line is itself cleaner. Return it
        # so the invisible noise doesn't survive.
        if stripped != line.strip():
            return stripped
        return line
    dash       = m.group('dash')        or ''
    # Tags from EITHER side of the punct -- merge so the punct
    # ends up inside the tag wrap regardless of where Gemini put
    # the tag relative to the punct.
    open_tags  = (m.group('open_tags_a') or '') + \
                 (m.group('open_tags_b') or '')
    leading    = m.group('leading')
    rest       = m.group('rest')        or ''
    close_tags = m.group('close_tags')  or ''
    # Leave legitimate leading ellipses alone in normal text. A caller that
    # KNOWS this file was rewritten by the old built-in engine can opt into the
    # inverse operation: that engine moved a logical trailing ellipsis to this
    # physical leading position before rtl_base ever saw the line.
    if _ELLIPSIS_RE.match(leading) and not move_ellipsis:
        return stripped if stripped != line.strip() else line
    if not rest:
        return stripped if stripped != line.strip() else line
    # If the rest already ends with punctuation, the leading one is
    # redundant -- drop it instead of moving (which would double up).
    if rest[-1] in TRAILING_PUNCT_CHARS:
        return dash + open_tags + rest + close_tags
    # Otherwise move leading punct to the end, INSIDE any closing
    # tag (so "<i>.לעזאזל</i>" becomes "<i>לעזאזל.</i>", not
    # "<i>לעזאזל</i>.").
    return dash + open_tags + rest + leading + close_tags


# --- encoding repair: SDH music glyphs mangled by a legacy cp1255 mis-decode ---
# A downloaded UTF-8 subtitle whose only non-ASCII char is a music note (e.g.
# U+266A, bytes E2 99 AA) was historically re-decoded as cp1255 by
# subs_engine.extract_sub.convert_to_utf (now fixed at the root), turning
# E2 99 AA into a 3-char garble ('gimel' + 'trademark' + 'multiplication').
# That garble was translated-through and cached/shared to the community pool,
# so we ALSO repair it on READ -- every cache/pool hit runs fix_rtl_punctuation
# -- which fixes entries already in the pool for everyone who downloads them,
# with no re-upload. An SDH music note is a standalone glyph at a line/word
# boundary, never a real word's final letter, so the replacement is anchored to
# a non-Hebrew-letter left boundary (below): it repairs the genuine garble but
# refuses to eat the trailing 'gimel' of a word that merely happens to be
# followed by stacked trademark-style symbols (e.g. "...gimel + TM + (R)").
_MOJIBAKE_MUSIC = {
    'ג™×': '♪',    # gimel ™ ×   -> eighth note
    'ג™«': '♫',    # gimel ™ «   -> beamed eighth notes
    'ג™¬': '♬',    # gimel ™ ¬   -> beamed sixteenth notes
    'ג™©': '♩',    # gimel ™ ©   -> quarter note
    'ג™­': '♭',    # gimel ™ soft-hyphen -> flat
    'ג™®': '♮',    # gimel ™ ®   -> natural
    'ג™¯': '♯',    # gimel ™ ¯   -> sharp
}
_MOJIBAKE_PREFIX = 'ג™'      # gimel + trademark: the shared 2-char signature
# Match a mojibake note ONLY when its leading gimel (U+05D2) is not glued to a
# real Hebrew word -- negative lookbehind on the Hebrew letter block
# U+05D0-U+05EA. The tail class is the exact 3rd char of each of the 7 keys:
# multiplication / << / not-sign / copyright / soft-hyphen / registered / macron.
_MOJIBAKE_RE = re.compile(
    '(?<![א-ת])ג™[×«¬©­®¯]')


def repair_music_mojibake(text):
    """Restore SDH music glyphs mangled to a 'gimel-trademark-x' style garble by
    a legacy cp1255 mis-decode of a UTF-8 source. Surgical: only the exact
    mojibake sequences are replaced, and only at a non-Hebrew-letter left
    boundary, so genuine Hebrew text (even a gimel-final word followed by a
    trademark sign) is never altered. Idempotent. Never raises."""
    try:
        if _MOJIBAKE_PREFIX not in text:   # cheap fast-out; every key has it
            return text
        return _MOJIBAKE_RE.sub(lambda m: _MOJIBAKE_MUSIC[m.group(0)], text)
    except Exception:
        return text


def fix_rtl_punctuation(text, mode=None, legacy_engine=False):
    """Normalize RTL punctuation placement in a Hebrew SRT body.

    `mode` controls the direction of the correction. Pulled from
    the addon's `rtl_punct_mode` setting if not explicitly passed:
      'rtl_base' (default) -- wrap each Hebrew line in an explicit
                             RTL embedding (RLE..PDF) so the
                             renderer gives it a right-to-left BASE
                             direction. Its own BiDi then places
                             embedded LTR runs (numbers, English
                             names, quoted English), parentheses
                             AND end punctuation correctly -- the
                             root-cause fix for setups whose base
                             direction defaults to LTR. Became the
                             default in v0.2.416 after on-device
                             verification; falls back gracefully to
                             logical order if a renderer ignores the
                             marks.
      'reverse'           -- move END-of-sentence punct from line
                             END to line START only. The prior
                             default (through v0.2.415): a lighter
                             fix that corrects the sentence period
                             but not embedded numbers / English /
                             parentheses. Kept for renderers that do
                             not honour the RTL-base marks.
      'legacy'            -- the inverse: move leading punct to
                             the logical end. Was the default in
                             v0.2.0-v0.2.6 under the (wrong)
                             assumption that Kodi reorders. Kept
                             around in case any setup actually
                             does reorder correctly.
      'off'               -- no processing.

    `legacy_engine` is deliberately narrow compatibility metadata for Hebrew
    files previously passed through the vendored sources engine. That engine
    physically moved trailing ellipses to the start and leading dialogue dashes
    to the end. True reverses those shapes before applying rtl_base; 'auto'
    enables it only when another unambiguous legacy-engine signature exists in
    the same file. Normal AI/local text must leave it False, so a genuine
    authored leading ellipsis remains untouched.

    Idempotent. Skips index + timecode lines. Preserves trailing
    newline so a benign re-run doesn't flag the file as changed."""
    if not text:
        return text
    # Encoding repair runs first and independent of RTL mode (even 'off'): a
    # legacy cp1255 mis-decode turned SDH music notes into 'ג™×'-style garble
    # that reached the cache/pool; restore it on every read.
    text = repair_music_mojibake(text)
    if mode is None:
        try:
            from resources.lib.core import settings
            mode = (settings.get_setting('rtl_punct_mode', 'rtl_base')
                    or 'rtl_base').lower()
        except Exception:
            mode = 'rtl_base'
    # 'auto' was the v0.2.7 name for what is now 'legacy'. Map for
    # backwards compatibility with users who manually selected it.
    if mode == 'auto':
        mode = 'legacy'
    if mode == 'off':
        return text
    if legacy_engine == 'auto':
        legacy_engine = _looks_like_legacy_engine_text(text)
    else:
        legacy_engine = bool(legacy_engine)
    trailing_nl = '\n' if text.endswith(('\n', '\r')) else ''
    out_lines = []
    cue_hebrew = False   # did an earlier text line of THIS cue carry Hebrew?
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or INDEX_RE.match(stripped) or \
                TIMECODE_RE.match(stripped):
            out_lines.append(line)
            cue_hebrew = False   # blank / index / timecode = cue boundary
            continue
        if _HEB_LETTER_RE.search(stripped):
            cue_hebrew = True
        if mode == 'legacy':
            out_lines.append(_fix_one_text_line(line))
        elif mode == 'rtl_base':
            out_lines.append(_wrap_rtl_base_line(
                line, cue_hebrew=cue_hebrew,
                legacy_engine=legacy_engine))
        else:
            # 'reverse' (default) or anything unrecognised
            out_lines.append(_reverse_fix_one_text_line(line, cue_hebrew=cue_hebrew))
    return '\n'.join(out_lines) + trailing_nl


# A dialogue dash with or without its space ("-להתראות.", as the model writes
# it, also before a quote or an ellipsis): the bare one was taken for part of
# the sentence, and only the period moved -- ".-להתראות", the dash beside the
# period on screen (field report, update 756).
_DIALOGUE_DASH = (r'-(?:\s+|(?=[' + _HEB_LETTER
                  + r'<"\'\u05f4\u201c\u201d]|\.\.|\u2026))')
# Match a Hebrew text line that has punctuation at the END (the
# "normal" Hebrew sentence shape). Used by the reverse-mode fix to
# move that punct to the START of the line.
_TRAILING_PUNCT_RE = re.compile(
    r'^(?P<dash>' + _DIALOGUE_DASH + r')?'
    r'(?P<open_tags>(?:<[a-zA-Z!][^>]*>)*)'
    # ... or inside the line's opening tag ("<i>-להתראות.</i>")
    r'(?P<dash2>' + _DIALOGUE_DASH + r')?'
    r'(?P<rest>(?=[^\n]*[' + _HEB_LETTER + r'])[^\n]*?[^'
    + TRAILING_PUNCT_CHARS + r'\s])'
    r'(?P<trailing>[' + TRAILING_PUNCT_CHARS + r']+)'
    r'(?P<close_tags>(?:</[a-zA-Z][^>]*>)*)\s*$'
)
# That ".-להתראות" as cached and pooled files still carry it (field report,
# update 756): the sentence mark in front, the bare dash after it.
_DISPLACED_BARE_DASH_RE = re.compile(
    r'^(?P<open_tags>(?:<[a-zA-Z!][^>]*>)*)'
    r'(?P<leading>[' + TRAILING_PUNCT_CHARS + r']+)-'
    r'(?P<inner_tags>(?:<[a-zA-Z!][^>]*>)*)'
    r'(?P<rest>(?:["\'\u05f4\u201c\u201d]|\.\.+|\u2026)?['
    + _HEB_LETTER + r'][^\n]*?)'
    r'(?P<close_tags>(?:</[a-zA-Z][^>]*>)*)\s*$'
)
_REVERSE_DASHED_LEADING_PUNCT_RE = re.compile(
    r'^(?P<dash>-\s+)'
    r'(?P<open_tags>(?:<[a-zA-Z!][^>]*>)*)'
    r'(?P<leading>[' + TRAILING_PUNCT_CHARS + r']+)'
    r'(?P<rest>(?=[^\n]*[' + _HEB_LETTER + r'])[^\n]*?[^'
    + TRAILING_PUNCT_CHARS + r'\s])'
    r'(?P<close_tags>(?:</[a-zA-Z][^>]*>)*)\s*$'
)


# A line that carries a Hebrew letter (used to tell a Hebrew-cue continuation
# line from a genuinely standalone Latin line).
_HEB_LETTER_RE = re.compile('[' + _HEB_LETTER + ']')
# End-of-line sentence punctuation with NO Hebrew-in-line requirement. Only used
# for the Latin-continuation case below (guarded by cue_hebrew), so it never
# touches a standalone Latin line. Mirrors _TRAILING_PUNCT_RE's dash + open/close
# tag groups (minus the Hebrew lookahead) so a dialogue dash is relocated to the
# line end and the moved punct stays INSIDE an italic/tag pair, exactly like the
# Hebrew-line path does -- not scrambled in front of the dash or tag.
_LATIN_TAIL_PUNCT_RE = re.compile(
    r'^(?P<dash>-\s+)?'
    r'(?P<open_tags>(?:<[a-zA-Z!][^>]*>)*)'
    r'(?P<pre>.*?[^' + TRAILING_PUNCT_CHARS + r'\s])'
    r'(?P<trailing>[' + TRAILING_PUNCT_CHARS + r']+)'
    r'(?P<close_tags>(?:</[a-zA-Z][^>]*>)*)\s*$'
)


def _reverse_dash_suffix(dash):
    return ' -' if dash else ''


def _reverse_fix_one_text_line(line, cue_hebrew=False):
    """Move END-of-line punctuation to the START. Used by the
    'reverse' rtl_punct_mode for Kodi setups whose subtitle
    renderer doesn't BiDi-reorder Hebrew lines and shows source
    text in physical L-to-R order.

    `cue_hebrew` is True when an EARLIER line of the same cue carried Hebrew.
    It only enables the Latin-continuation case below (a sentence-ending punct
    that wrapped onto a Latin-only line of a Hebrew cue, e.g. a username
    'Modelbehavior36.' on its own line); it never affects a standalone Latin
    line (cue_hebrew stays False there)."""
    stripped = line.strip()
    while stripped and stripped[0] in INVISIBLE_BIDI:
        stripped = stripped[1:]
    while stripped and stripped[-1] in INVISIBLE_BIDI:
        stripped = stripped[:-1]
    if not stripped:
        return line
    displaced = _DISPLACED_BARE_DASH_RE.match(stripped)
    if displaced:
        # the mark goes inside the tag, as this fix puts it for "-<i>X.</i>"
        return (displaced.group('open_tags') + displaced.group('inner_tags')
                + displaced.group('leading') + displaced.group('rest')
                + displaced.group('close_tags') + ' -')
    dashed_leading = _REVERSE_DASHED_LEADING_PUNCT_RE.match(stripped)
    if dashed_leading:
        open_tags = dashed_leading.group('open_tags') or ''
        leading = dashed_leading.group('leading')
        rest = dashed_leading.group('rest') or ''
        close_tags = dashed_leading.group('close_tags') or ''
        if rest:
            return open_tags + leading + rest + close_tags + ' -'
    m = _TRAILING_PUNCT_RE.match(stripped)
    if not m:
        # Latin-continuation: the Hebrew cue's sentence-ending punct wrapped
        # onto a line that is itself Latin-only, so the Hebrew-requiring match
        # above skipped it. Move that trailing punct to the line start the same
        # way -- ONLY inside a Hebrew cue (cue_hebrew) and only when the line has
        # no Hebrew of its own, so a genuinely standalone Latin line is untouched.
        if cue_hebrew and not _HEB_LETTER_RE.search(stripped):
            lm = _LATIN_TAIL_PUNCT_RE.match(stripped)
            if lm:
                lat_dash = lm.group('dash') or ''
                lat_open = lm.group('open_tags') or ''
                pre = lm.group('pre') or ''
                lat_trailing = lm.group('trailing')
                lat_close = lm.group('close_tags') or ''
                if pre and pre[0] not in TRAILING_PUNCT_CHARS:
                    # Move the punct to the start, INSIDE any tag pair, and send a
                    # dialogue dash to the end -- same shape as the Hebrew path.
                    return lat_open + lat_trailing + pre + lat_close + \
                        _reverse_dash_suffix(lat_dash)
        if stripped != line.strip():
            return stripped
        return line
    dash       = m.group('dash') or m.group('dash2') or ''
    open_tags  = m.group('open_tags')  or ''
    rest       = m.group('rest')       or ''
    trailing   = m.group('trailing')
    close_tags = m.group('close_tags') or ''
    if not rest:
        return stripped if stripped != line.strip() else line
    # If a leading punct is already present too, don't double up:
    # the trailing one is redundant, drop it. Detection: rest
    # itself starts with punct (after the optional dash/tag prefix).
    if rest[0] in TRAILING_PUNCT_CHARS:
        return open_tags + rest + close_tags + _reverse_dash_suffix(dash)
    return open_tags + trailing + rest + close_tags + \
        _reverse_dash_suffix(dash)


# A Latin-only line that is provably SAFE to wrap in RTL base: a single
# username / handle shaped token -- ASCII letter-first, alphanumeric runs joined
# by single hyphens, ending in exactly ONE sentence-punct char ('Modelbehavior36.',
# 'cutie-patotie87.', 'X-Ray.', 'john-doe99!'). Fuzzed at 50k tokens against a
# reference BiDi engine: wrapping this class reorders ONLY the trailing punct (to
# the RTL-correct left side) and NEVER the token body. Anything outside it -- a
# leading digit or symbol ('7-Eleven.' -> '.Eleven-7', '@handle.' -> '.handle@'),
# an internal symbol or dot, a multi-word line ('50 miles.' -> '.miles 50'), a
# double / edge hyphen, or a trailing symbol -- can have its segments swapped
# under the RTL embedding, so it is excluded and left LTR (unwrapped, unmodified).
# \Z (not $) so a stray trailing newline can never sneak a match, even if this
# helper is ever reused somewhere that doesn't pre-split lines.
_WRAPPABLE_LATIN_TAIL_RE = re.compile(
    r'^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*[' + TRAILING_PUNCT_CHARS + r']\Z')


def _is_wrappable_latin_tail(s):
    """True for a username/handle-shaped Latin token that is the TAIL of a Hebrew
    sentence wrapped onto its own line (see _WRAPPABLE_LATIN_TAIL_RE). Wrapping it
    in RTL base moves ONLY its trailing period to the RTL-correct (left) side; the
    body is provably never reordered. Non-mutating (marks only). Never raises."""
    return bool(_WRAPPABLE_LATIN_TAIL_RE.match(s))


def _looks_like_legacy_engine_text(text):
    """Detect a file rewritten by subs_engine.engine's old punctuation fixer.

    A pure leading ellipsis is ambiguous, so it is never evidence by itself.
    A trailing *bare* dialogue dash after leading sentence punctuation is the
    engine's unique ``- sentence?`` -> ``? sentence-`` shape. A displaced single
    sentence mark is also strong file-level evidence: normal logical Hebrew
    puts it at the end. This lets old pool rows self-identify without a network
    refetch or mass re-upload, while new logical files keep genuine leading
    ellipses intact.
    """
    try:
        for line in (text or '').splitlines():
            st = line.strip()
            while st and st[0] in INVISIBLE_BIDI:
                st = st[1:]
            while st and st[-1] in INVISIBLE_BIDI:
                st = st[:-1]
            if not st or not _HEB_LETTER_RE.search(st):
                continue
            paired = _LEADING_PUNCT_CLOSER_RE.match(st)
            if (paired and not _ELLIPSIS_RE.match(
                    paired.group('leading') or '')):
                return True
            bare_body = st[:-1].rstrip() if st.endswith('-') \
                and not st.endswith(' -') else ''
            if bare_body and _LEADING_PUNCT_RE.match(bare_body):
                return True
            m = _LEADING_PUNCT_RE.match(st)
            if m and not _ELLIPSIS_RE.match(m.group('leading') or ''):
                return True
    except Exception:
        return False
    return False


def _rtl_inside_single_style_run(line):
    """Use a single libass shaping run for a Hebrew line with HTML styles.

    A style boundary can reset the bidi base, moving punctuation and even
    reordering separately styled Hebrew phrases. Expand a single styled text
    run over its surrounding dialogue/music ornaments. For mixed substantive
    runs, drop inline emphasis rather than display phrases in the wrong order.
    Visible text and its logical order are preserved; no words are reversed.
    """
    tag = r'(</?(?:i|b|u|font)(?:\s+[^<>]*)?>)'
    parts = re.split(tag, line, flags=re.I)
    if len(parts) == 1:
        return line
    texts = []
    for index in range(0, len(parts), 2):
        value = parts[index]
        if '<' in value or '>' in value:
            return line
        if re.search(r'[A-Za-z0-9\u0590-\u05ff]', value):
            texts.append(index)
    if not texts or not any(_HEB_LETTER_RE.search(parts[i]) for i in texts):
        return line
    # Previous cache repair may have put controls outside or inside styles.
    # Remove only embedding controls here; retain other intentional bidi marks.
    plain = ''.join(parts[::2]).replace(RLE, '').replace(PDF, '')
    if len(texts) != 1:
        return plain
    # Preserve only one nested style chain. Collecting sibling openings
    # and closings independently would manufacture crossed tags, e.g.
    # <i></i><b>text</b> -> <i><b>text</i></b>.
    stack = []
    saw_close = False
    for token in parts[1::2]:
        name = re.match(r'</?([a-z]+)', token, re.I).group(1).lower()
        if token.startswith('</'):
            saw_close = True
            if stack:
                if stack.pop() != name:
                    return plain
            # An unmatched close may finish a style from the preceding line.
        else:
            if saw_close:
                return plain
            stack.append(name)
    opens = ''.join(t for t in parts[1::2] if not t.startswith('</'))
    closes = ''.join(t for t in parts[1::2] if t.startswith('</'))
    return opens + RLE + plain + PDF + closes



def _wrap_rtl_base_line(line, cue_hebrew=False, legacy_engine=False):
    """Wrap a Hebrew text line in an explicit RTL embedding (RLE .. PDF) so the
    subtitle renderer treats it with a right-to-left BASE direction. Used by the
    'rtl_base' rtl_punct_mode.

    With a correct RTL base, the renderer's own BiDi places embedded LTR runs --
    numbers ('50'), usernames ('cutie-patotie87'), quoted English ('"Model
    behavior"') -- plus parentheses and end-of-sentence punctuation on the
    correct side, so NO manual surgery is needed. This is the root-cause fix for
    setups whose paragraph base defaults to LTR (which shoves that content to the
    wrong edge).

    A Hebrew line is wrapped. A Latin-only line is normally left LTR (an on-screen
    caption / URL stays as-is) -- EXCEPT a single-token username/handle that ends a
    Hebrew sentence on its own line (see _is_wrappable_latin_tail): when it sits
    inside a Hebrew cue (cue_hebrew) it is ALSO wrapped, so its trailing period
    lands on the RTL-correct side. The wrap is non-mutating (marks only), so a
    genuine '.NET' / '.exe' is never corrupted. Index / timecode / blank lines
    never reach here (the caller skips them).

    Crucially, the line is first NORMALIZED back to pristine LOGICAL order before
    wrapping. Cached and pool-shared SRT text was post-processed once already, by
    whatever rtl_punct_mode was active at WRITE time -- and 'reverse' (the
    long-standing default) MOVES end-of-sentence punct to the line START. RTL-base
    rendering needs that punct at its logical END, so we run the inverse
    ('legacy': move a leading sentence-punct back to the end, ellipsis-guarded)
    first. That makes the wrap correct for fresh, reverse-cached AND pool content
    alike -- pool items carry no mode metadata, so a puller must not assume the
    contributor used the same mode. Idempotent: edge BiDi marks (including a prior
    RLE/PDF) are stripped and the normalization converges, so a re-run is a no-op."""
    s = line
    while s and s[0] in INVISIBLE_BIDI:
        s = s[1:]
    while s and s[-1] in INVISIBLE_BIDI:
        s = s[:-1]
    if not s.strip():
        return line
    if not _HEB_LETTER_RE.search(s):
        # No Hebrew. A single-token username/handle ending a Hebrew sentence on its
        # own line ('Modelbehavior36.') is wrapped so BiDi moves its trailing period
        # to the RTL-correct side; everything else Latin stays LTR, UNMODIFIED. The
        # wrap is marks-only, so a genuine '.NET'/'.exe' can never be corrupted (and
        # is excluded anyway -- it starts with the punct). We do NOT try to "undo" a
        # possible reverse-mode move, because a leading '.' is indistinguishable
        # from a genuine one.
        if cue_hebrew and _is_wrappable_latin_tail(s):
            return RLE + s + PDF
        # Return the cleaned form only if we removed stray edge marks; else verbatim.
        return s if s != line else line
    # 'reverse' also relocates a leading dialogue dash "- " to a TRAILING " -"
    # suffix (and moves the sentence punct to the front) -- e.g. "- שלום?" was
    # cached as "?שלום -", and "- <i>שלום</i>." as "<i>.שלום</i> -". Move that dash
    # back to the front so the normalization below (which only recognizes a LEADING
    # dash) can restore the logical order, matching a fresh rtl_base render. The
    # gate uses _LEADING_PUNCT_RE (which skips a leading open-tag run before the
    # punct) so tag-wrapped cues match too; a genuine trailing interruption dash
    # ("שלום -", no leading punct) fails the match and is left untouched.
    st = s.strip()
    displaced = _DISPLACED_BARE_DASH_RE.match(st)
    if displaced:
        # ".-להתראות" from the old 'reverse' fix: the dash goes back in front
        # and the sentence mark to the end.
        s = (displaced.group('open_tags') + '-' + displaced.group('inner_tags')
             + displaced.group('rest') + displaced.group('leading')
             + displaced.group('close_tags'))
    elif st.endswith(' -') and _LEADING_PUNCT_RE.match(st[:-2].rstrip()):
        s = '- ' + st[:-2].rstrip()
    # The vendored engine used lstrip('-') and appended a BARE dash, producing
    # "? sentence-" (no separating space). Only undo it when the remaining line
    # starts with displaced punctuation; a genuine interruption "sentence-" has
    # no such prefix and is preserved.
    elif legacy_engine and st.endswith('-') and not st.endswith(' -') \
            and _LEADING_PUNCT_RE.match(st[:-1].rstrip()):
        s = '- ' + st[:-1].rstrip()
    # Undo any 'reverse'/'legacy' mutation baked into cached/pool text: move a
    # displaced leading sentence-punct back to the logical end. On pristine
    # (fresh AI) text this is a no-op -- Hebrew never authors a leading . , ; : ! ?
    normalized = _fix_one_text_line(s, move_ellipsis=legacy_engine)
    return RLE + _rtl_inside_single_style_run(normalized) + PDF
