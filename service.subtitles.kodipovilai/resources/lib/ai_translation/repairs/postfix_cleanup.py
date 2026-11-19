# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Post-fix: annotations, speaker prefixes, niqqud, stutters, broken line breaks, sound cues.

import re
from resources.lib.core.srt.blocks import _HAS_HEBREW_RE, _HEB_RE, _INDEX_RE, _INVISIBLE_BIDI, _SPEAKER_RE, _TIMECODE_RE, _TRAILING_PUNCT_CHARS, _block_timecode_index, parse_blocks, stitch_blocks


# Hearing-impaired annotations. Two flavours:
#  - whole-line annotations like "[breathing heavily]" or "(music
#    swells)" -- we want to drop the whole text line
#  - inline annotations like "Hello! [chuckles] How are you?" --
#    we want to drop just the bracketed part
# Brackets we recognise: [] {} () and unicode equivalents that
# show up in some sources.
_BRACKET_RE = re.compile(
    r'[\[\(\{][^\[\]\(\){}]*?[\]\)\}]'
)
# Same ALL-CAPS Latin speaker prefix, matched per-LINE (MULTILINE) with an
# optional leading dialogue dash preserved. Used to strip a prefix the model
# LEAKED into its Hebrew output. It can only ever match an English "NAME:" at a
# line start -- an index line (digits), a timecode line (digits), a Hebrew line,
# and a blank line can none of them match -- so it's applied to the whole text
# without classifying lines, preserving every newline exactly.
_LEAKED_SPEAKER_RE = re.compile(
    r'(?m)^(?P<pre>[ \t]*(?:-[ \t]+)?)[A-Z][A-Z0-9 \'\.\-]{1,30}:[ \t]*'
)
# Same ALL-CAPS speaker tag, but Hebrew-GATED: strip it only when the rest of
# that line contains a Hebrew character -- i.e. the model translated the line
# to Hebrew and merely LEAKED the tag. Used on the shipped Hebrew OUTPUT so a
# line the model deliberately left in English (an on-screen caption / news
# chyron / URL like "WARNING: ...", "PART 2: THE RETURN", "HTTP://...") is
# NEVER corrupted -- those have no Hebrew after the colon, so they don't match.
# An optional leading inline wrapper (<i>/<b>/<font ...>) the tag can hide
# behind is allowed and preserved (kept in the 'pre' group). The [^>\n]{1,40}
# tag body is bounded and '>'-free, so there is no catastrophic backtracking.
_LEAKED_SPEAKER_RE_HE = re.compile(
    r'(?m)^(?P<pre>[ \t]*(?:<[^>\n]{1,40}>[ \t]*)?(?:-[ \t]+)?)'
    r'[A-Z][A-Z0-9 \'\.\-]{1,30}:[ \t]*'
    r'(?=[^\n]*[֐-׿])'
)
# A line that is nothing but dialogue dashes / whitespace. What is left of
# "- (door creaking)" once the bracketed cue is gone.
_DASH_ONLY_RE = re.compile(r'^[-‐-―\s]+$')
# Hebrew combining points + cantillation. U+05BE (MAQAF), U+05C0 (PASEQ),
# U+05C3 (SOF PASUQ), U+05C6 (NUN HAFUKHA), U+05F3/U+05F4 (geresh, gershayim)
# are punctuation, not points, and are deliberately NOT in this class.
_NIQQUD_RE = re.compile('[' + ''.join(
    chr(c) for c in list(range(0x0591, 0x05BE))
    + [0x05BD, 0x05BF, 0x05C1, 0x05C2, 0x05C4, 0x05C5, 0x05C7]) + ']')


# WebVTT class spans (<c.white><c.mono_sans>...</c.mono_sans></c.white>) that
# some Netflix rips carry into SRT. They style nothing Kodi renders, and they
# sit in front of the dialogue, where they hide the line's real start from the
# RTL punctuation fix -- measured: the period landed before the first word.
_VTT_CLASS_TAG = re.compile(r'</?c(?:\.[A-Za-z0-9_-]+)*\s*>', re.I)


def strip_vtt_class_tags(text):
    if not text or ('<c' not in text and '</c' not in text
                    and '<C' not in text and '</C' not in text):
        return text
    return _VTT_CLASS_TAG.sub('', text)


# A translator's working note written into the subtitle instead of kept to
# itself -- measured on a real episode: "(Walter speaking to Walter Jr., male
# addressee)" as a second line under the Hebrew. Only a line WITHOUT Hebrew
# that talks about speakers/addressees qualifies; dialogue never does.
_MODEL_NOTE = re.compile(
    r'^\s*[\(\[][^֐-׿]*\b(?:speaking\s+to|speaks\s+to|talking\s+to|'
    r'addressee|addressing|speaker|male|female|masculine|feminine|'
    r'translator|translation\s+note)\b[^֐-׿]*[\)\]]\s*$', re.I)


# ...or just the speaker's name in parentheses under the Hebrew line --
# measured: "(Skyler)" under the Hebrew "Skyler." where the source had
# only "Skyler.". Bracketed text in the source is removed before
# translation, so a Latin name in parentheses beside a Hebrew line is
# always the model's own.
_NAME_NOTE = re.compile(
    r"^\s*\(\s*[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,2}\s*\)\s*$")


def drop_model_notes(block):
    """Remove translator-note lines from one SRT block (keeps index/timecode,
    keeps at least one text line)."""
    lines = (block or '').split('\n')
    ti = _block_timecode_index(lines)
    if ti < 0:
        return block
    text = lines[ti + 1:]
    hebrew = any(_HAS_HEBREW_RE.search(l) for l in text)
    kept = [l for l in text if not (_MODEL_NOTE.match(l)
                                    or (hebrew and _NAME_NOTE.match(l)))]
    if not kept or len(kept) == len(text):
        return block
    return '\n'.join(lines[:ti + 1] + kept)


def strip_hi_annotations(text, keep_speaker_prefixes=False):
    """Remove hearing-impaired noise from an SRT body.

    Drops bracketed sound cues like [breathing], (music playing),
    {chuckles}. If an entry's text was nothing but annotations, the
    whole entry is dropped (its timecode goes too -- there's literally
    no speech in that span, so an empty subtitle would be a visual gap
    with nothing useful).

    ALL-CAPS speaker prefixes like 'MABEL: ' are dropped by default, BUT
    kept when keep_speaker_prefixes=True. The AI translation path keeps
    them on purpose: the model matches the name against the TMDB cast
    block to pick the correct Hebrew gender per line (see prompt.py's
    SPEAKER-PREFIX HINT), then drops the tag from its own Hebrew output
    -- so stripping them here would throw away the best per-line gender
    signal. Any prefix the model fails to drop is removed from the OUTPUT
    by strip_leaked_speaker_prefix().

    Returns the cleaned SRT body. Block order and numbering are
    preserved for surviving entries (we keep the original index
    numbers so the model sees stable references).
    """
    if not text:
        return text
    out_blocks = []
    for block in parse_blocks(text):
        lines = block.split('\n')
        kept_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if _INDEX_RE.match(stripped):
                kept_lines.append(line)
                continue
            if _TIMECODE_RE.match(stripped):
                kept_lines.append(line)
                continue
            # text line -- strip bracketed sound cues. Speaker prefixes are
            # KEPT when keep_speaker_prefixes (the AI uses them for gender and
            # drops them from its output; see the docstring).
            cleaned = _BRACKET_RE.sub('', line)
            if not keep_speaker_prefixes:
                cleaned = _SPEAKER_RE.sub('', cleaned)
            # collapse whitespace runs that the strips may have
            # left behind
            cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
            # A line that was ONLY a sound cue behind a dialogue dash
            # ("- (door creaking)") reduces to a bare "-". That dash belongs
            # to the line we just removed, so keeping it ships a visible "-"
            # cue -- 76 of them in one reported file, and the model dutifully
            # copies each one into the Hebrew. Drop the residue, but ONLY when
            # the strip is what emptied the line, so a dash a source
            # deliberately wrote on its own is left exactly as it was.
            if cleaned != line.strip() and _DASH_ONLY_RE.match(cleaned):
                cleaned = ''
            if cleaned:
                kept_lines.append(cleaned)
        # only keep the block if there's actual dialogue text left
        # (more than just the index + timecode)
        text_lines = [ln for ln in kept_lines
                      if ln.strip() and not _INDEX_RE.match(ln.strip())
                      and not _TIMECODE_RE.match(ln.strip())]
        if text_lines and len(kept_lines) >= 3:
            out_blocks.append('\n'.join(kept_lines))
    return '\r\n\r\n'.join(out_blocks) + '\r\n' if out_blocks else ''


def strip_leaked_speaker_prefix(text, hebrew_only=False):
    """Remove any ALL-CAPS Latin speaker prefix (e.g. 'MABEL: ', '- DR. SMITH: ')
    that LEAKED into a line. The prompt tells the model to use such a prefix for
    gender then drop it, but model compliance isn't perfect. Index/timecode/blank
    lines can never match (the pattern needs a leading A-Z Latin run), and the
    exact line/newline structure is preserved (no entries dropped, cue timing
    intact). A leading dialogue dash is kept. Never raises into the caller.

    hebrew_only=True (use on the shipped Hebrew OUTPUT) strips the tag ONLY when
    the rest of that line contains a Hebrew character, so a line the model
    deliberately left in English -- a caption/chyron/URL like 'WARNING: ...' or
    'HTTP://...' -- is never corrupted. The default (un-gated) strips any leading
    ALL-CAPS 'NAME:' and is used on transient ENGLISH placeholders (the interim
    first-chunk fallback, the progressive stitch) and on the pre-Google source,
    matching the historical source-side speaker-prefix stripping."""
    if not text:
        return text
    try:
        rx = _LEAKED_SPEAKER_RE_HE if hebrew_only else _LEAKED_SPEAKER_RE
        return rx.sub(lambda m: m.group('pre'), text)
    except Exception:
        return text


# --- the speaker tag the model TRANSLATED instead of dropping ----------------
# strip_leaked_speaker_prefix covers the tag the model COPIED ("IAN: לא, לא.").
# It cannot see the far more common outcome, because that pattern needs an
# ALL-CAPS LATIN name: the model translates the tag along with the line, and
# "IAN: No, no." comes back as "איאן: לא, לא.". Measured on a file a user
# reported: 60 leaked tags, every one of them Hebrew, zero Latin -- the Latin
# stripper works, the model just rarely gives it anything to do.
#
# Stripping a "word:" prefix from Hebrew on its own evidence would be reckless:
# real dialogue says "תראה: אני לא יודע" and "האמת: לא אכפת לי". So this is
# ANCHORED TO THE SOURCE. An entry's Hebrew may lose a prefix only when the
# SAME entry's source carried an ALL-CAPS speaker tag, and never more of them
# than the source had. Genuine dialogue has no ALL-CAPS tag in the source, so
# it can never match, whatever it does with colons.
# The leading run allows the invisible bidi controls fix_rtl_punctuation adds
# (RLE ... PDF), so this still works on a file that has already been through
# the RTL pass -- the live path runs before it, the repair paths do not.
_HE_TAG_RE = re.compile(
    u'^(?P<pre>[ \t' + _INVISIBLE_BIDI + u']*'
    u'(?:<[^>\n]{1,40}>[ \t]*)?(?:-[ \t]*)?)'
    u'[\u05d0-\u05ea][\u0590-\u05f4 \t\'"]{0,20}?'
    u'[ \t]*:[ \t]*(?=\\S)')
_HE_TAG_WORDS = 2

# Narration and interview subtitles tag the speaker in Title case instead
# ("Celeste: <i>Look at me.</i>", "- Stu:<i> Thing about...</i>" -- measured
# on Big Little Lies, where the model kept "Celeste:" or wrote "סלסט:").
# Title case is also ordinary dialogue ("Look: I don't know"), so such a tag
# is trusted only when the file itself marks it as a name: the tag opens an
# italic narration line, or the same tag opens three or more lines.
_TITLE_TAG_RE = re.compile(
    r"^(?P<name>[A-Z][a-z][A-Za-z'\-]*(?: [A-Z][a-z][A-Za-z'\-]*)?)[ \t]*:[ \t]*(?P<italic><i>)?")
_TITLE_TAG_STOP = frozenset((
    'Look', 'Listen', 'Note', 'Warning', 'Remember', 'Answer', 'Question',
    'Rule', 'Step', 'Lesson', 'Hint', 'Fact', 'Update', 'Okay', 'Well', 'So',
    'Yes', 'No', 'Hey', 'Oh', 'Now', 'Then', 'Example', 'Problem', 'Option',
    'Reason', 'Result', 'Chapter', 'Part', 'Level', 'Round', 'Day', 'Time',
    'Location', 'Subject', 'Breaking', 'News', 'Exhibit', 'Plan', 'Truth',
    'First', 'Second', 'Third', 'Last', 'Also', 'Plus', 'Previously', 'Next',
    'Translation', 'Caution', 'Attention', 'Danger', 'Admit', 'Face', 'Okay'))
_LATIN_TITLE_TAG_RE = re.compile(
    u'^(?P<pre>[ \t' + _INVISIBLE_BIDI + u']*'
    u'(?:<[^>\n]{1,40}>[ \t]*)?(?:-[ \t]*)?)'
    u"(?P<name>[A-Z][a-z][A-Za-z'\\-]*(?: [A-Z][a-z][A-Za-z'\\-]*)?)"
    u'[ \t]*:[ \t]*(?=\\S)')


def _title_tag_names(source_text):
    """The Title-case speaker names this source file itself vouches for."""
    seen, italic = {}, set()
    for line in (source_text or '').split('\n'):
        t = line.strip()
        if t.startswith('-'):
            t = t[1:].lstrip()
        m = _TITLE_TAG_RE.match(t)
        if not m or m.group('name') in _TITLE_TAG_STOP:
            continue
        seen[m.group('name')] = seen.get(m.group('name'), 0) + 1
        if m.group('italic'):
            italic.add(m.group('name'))
    return set(n for n, c in seen.items() if c >= 3 or n in italic)


def strip_hebrew_speaker_prefix(text, source_text):
    """Remove a speaker tag the model rendered in Hebrew ("איאן: לא, לא.").

    `source_text` is the SRT the translation was made from, and it is what
    licenses each removal (see the note above) -- without it nothing is
    touched. Line and newline structure is preserved exactly: only characters
    inside a line are removed, so no cue, line or timing can change. Never
    raises into the caller.
    """
    if not text or not source_text:
        return text
    try:
        # Per source entry: which TEXT-LINE POSITIONS carried an ALL-CAPS
        # speaker tag, and how many text lines that entry had. The positions
        # are what make this precise -- with them, a source entry whose first
        # line is "IAN: No." and whose second is "Look: I don't know." can
        # only ever license a removal on the FIRST Hebrew line.
        #
        # An index number that appears TWICE is dropped rather than merged.
        # The number is the only key there is, so two blocks sharing one would
        # share a licence, and the untagged one would spend the tagged one's.
        src_tags, seen = {}, set()
        names = _title_tag_names(source_text)
        for blk in parse_blocks(source_text):
            lines = blk.split('\n')
            if (len(lines) < 3 or not _INDEX_RE.match(lines[0].strip())
                    or not _TIMECODE_RE.match(lines[1].strip())):
                continue
            n = int(lines[0].strip())
            if n in seen:
                src_tags.pop(n, None)
                continue
            seen.add(n)
            at = set()
            for i, ln in enumerate(lines[2:]):
                s = ln.strip()
                if s.startswith('-'):
                    s = s[1:].lstrip()
                if _SPEAKER_RE.match(s):
                    at.add(i)
                else:
                    tm = _TITLE_TAG_RE.match(s)
                    if tm and tm.group('name') in names:
                        at.add(i)
            if at:
                src_tags[n] = (at, len(lines) - 2)
        if not src_tags:
            return text

        # Which lines of the TRANSLATION are really entry indices: a
        # digits-only line whose next non-empty line is a timecode. Without
        # that second condition a digits-only line of DIALOGUE -- a year, a
        # score, a countdown, and there are plenty in a 2,000-cue file --
        # reads as an entry boundary, and hands the rest of its cue the tag
        # licence belonging to whichever entry happens to share the number.
        lines_all = text.split('\n')
        idx_at, dup = {}, set()
        for i, line in enumerate(lines_all):
            s = line.strip()
            if not _INDEX_RE.match(s):
                continue
            j = i + 1
            while j < len(lines_all) and not lines_all[j].strip():
                j += 1
            if j < len(lines_all) and _TIMECODE_RE.match(lines_all[j].strip()):
                n = int(s)
                if n in idx_at.values():
                    dup.add(n)
                idx_at[i] = n

        # How many text lines each entry of the TRANSLATION has. Positions
        # only mean the same thing on both sides when these agree.
        he_count = {}
        cur, expect_tc = None, False
        for i, line in enumerate(lines_all):
            if i in idx_at:
                cur = idx_at[i]
                he_count[cur] = 0
                expect_tc = True
            elif not line.strip():
                continue
            elif expect_tc:
                expect_tc = False
            elif cur is not None:
                he_count[cur] += 1

        out = []
        tags_at, pos, licensed, expect_tc = set(), -1, False, False
        for i, line in enumerate(lines_all):
            cr = '\r' if line.endswith('\r') else ''
            body = line[:-1] if cr else line
            if i in idx_at:
                n = idx_at[i]
                tags_at, span = src_tags.get(n, (set(), 0))
                # POSITIONAL ONLY. When the two sides split the entry
                # differently -- the model merges two source lines into one
                # often enough -- position 0 of the Hebrew is not position 0
                # of the source, so nothing about the source's positions
                # applies and NOTHING is licensed. Counting instead ("at most
                # this many tags somewhere in the entry") is what would let a
                # merged cue's genuine "תראה:" be read as the tag the source
                # carried on its other line.
                licensed = (bool(tags_at) and n not in dup
                            and he_count.get(n) == span)
                pos = -1
                expect_tc = True
            elif not body.strip():
                pass
            elif expect_tc:
                expect_tc = False
            else:
                pos += 1
                if licensed and pos in tags_at and _HAS_HEBREW_RE.search(body):
                    m = _HE_TAG_RE.match(body)
                    if not m:
                        # the model copied the Title-case tag as it was
                        lm = _LATIN_TITLE_TAG_RE.match(body)
                        if lm and lm.group('name') in names:
                            m = lm
                    # a tag is a NAME, not a sentence: at most two words, and
                    # the line must still have Hebrew left once it is gone
                    # (otherwise the "tag" was the whole line, and removing it
                    # would empty a cue -- never allowed). Counted on the tag
                    # ALONE, with the dash and any bidi control excluded, so
                    # the limit means the same thing on every line shape.
                    tag = m.group(0)[len(m.group('pre')):] if m else ''
                    if m and len(tag.split(':')[0].split()) <= _HE_TAG_WORDS:
                        rest = body[m.end():]
                        if _HAS_HEBREW_RE.search(rest):
                            body = m.group('pre') + rest
            out.append(body + cr)
        return '\n'.join(out)
    except Exception:
        return text


def strip_niqqud(text):
    """Remove Hebrew points and cantillation marks.

    Hebrew subtitles are written unpointed; the model sometimes vocalises a
    word anyway ("בְּסֵדֶר", "מַה?", "דוֹב."), which a user reported as "a lot of
    niqqud that isn't really needed". Every occurrence measured in real output
    was an ordinary word carrying no information the consonants don't already
    have -- no gendered form depended on a point -- so this is a pure display
    cleanup.

    Combining marks ONLY: MAQAF, PASEQ, SOF PASUQ, geresh and gershayim are
    real punctuation and are deliberately outside the class, so a word like
    צה"ל and a hyphenated בֵּית-הַסֵּפֶר keep their spelling. Never raises.
    """
    if not text:
        return text
    try:
        return _NIQQUD_RE.sub('', text)
    except Exception:
        return text


# A stutter repeats one word, so its fragment has the word's gender. Measured
# after a gender repair: "You-you look nice" came back "את-אתה נראה טוב" --
# the full pronoun and the verb were fixed, the fragment kept the old gender.
# Only the tight hyphen form: with a pause between ("לקח את... אתה יודע") the
# first word is just as often the object marker.
_PRONOUN_STUTTER_RE = re.compile(
    u'(?<![\u05d0-\u05ea])(\u05d0\u05ea\u05d4|\u05d0\u05ea)'
    u'(-)(\u05d0\u05ea\u05d4|\u05d0\u05ea)(?![\u05d0-\u05ea])')


def fix_pronoun_stutter(text):
    """'את-אתה' -> 'אתה-אתה' (and the reverse): a stuttered pronoun takes the
    gender of the word it repeats. Never raises."""
    if not text or u'\u05d0\u05ea' not in text:
        return text
    try:
        out = _PRONOUN_STUTTER_RE.sub(lambda m: m.group(3) + m.group(2) + m.group(3), text)
        return text if out == text else out
    except Exception:
        return text


# BBC Subtitle Guidelines 3.4 (bbc.co.uk/accessibility/forproducts/guides/
# subtitles): break lines at natural points -- never split an article or
# preposition from its phrase, a conjunction from its clause, a pronoun from
# its verb or the parts of a verb. Hebrew writes the article and most
# prepositions and conjunctions as prefixes; these are the standalone words
# left to guard. Measured on five AI-translated titles (Oct 2026): 1-3% of
# the two-line single-speaker cues ended their first line on one
# ("היא האביר על / הסוס הלבן שלי", "אפשר לקבל את / תשומת לבכם?").
# Not here: object pronouns (אותו, אותה) and plain זה, which close a verb
# phrase, and comparatives (יותר), which pair with either side.
_BREAK_BEFORE = frozenset(u'''
את של על עם אל אצל מול בין עד בלי לגבי כמו אחרי לפני בגלל בתוך ליד נגד מאז
כי אם או אבל גם רק כל הכי ממש כבר עוד לא
מה מי איך למה איפה מתי כמה איזה איזו
אני אתה הוא היא אנחנו אתם אתן הם הן
שאני שאתה שאת שהוא שהיא שאנחנו שאתם שהם שזה
ואני ואתה ואת והוא והיא ואנחנו והם
'''.split())
# A second line that opens a clause of its own: the break is natural already.
_CLAUSE_OPENERS = frozenset(u'אבל כי אז ואז או אם ואני ואתה ואת והוא והיא ואנחנו והם'.split())
# Netflix's Hebrew timed-text guide: 42 characters a line (BBC 3.1: 37 on
# teletext, 68% of the picture width online).
_LINE_MAX_CHARS = 42
_LINE_ENDS_CLAUSE = u'.,;:!?…"\'״)]-–—♪'
_SPEAKER_DASHES = u'-–—'


def _mend_two_lines(one, two):
    """(line 1, line 2) with the words that belong to line 2 moved down, or
    None. Logical order (before fix_rtl_punctuation); a dialogue line, a
    first line that ends a clause, markup where the words would cross and
    a line opening with its end punctuation are left as they are."""
    a, b = one.strip(), two.strip()
    if not a or not b or a[0] in _SPEAKER_DASHES or b[0] in _SPEAKER_DASHES:
        return None
    # A line still opening with its end punctuation ("?שלום" -- the model's
    # slip that fix_rtl_punctuation repairs after this, also behind a tag:
    # "<i>?שלום") is left alone: a word put in front of it, or moved out from
    # behind it, would strand the mark mid-line.
    if (a[-1] in _LINE_ENDS_CLAUSE
            or _CUE_TAG_RE.sub('', a).lstrip()[:1] in _TRAILING_PUNCT_CHARS
            or b[0] in _TRAILING_PUNCT_CHARS or b[0] in '<{'):
        return None
    if not _HEB_RE.search(a) or not _HEB_RE.search(b):
        return None
    words = a.split(' ')
    moved = []
    while len(moved) < 3 and len(words) > 1 and words[-1] in _BREAK_BEFORE:
        if words[-1] == u'כל' and words[-2] == u'קודם':
            break                       # "קודם כל" (first of all) stays one phrase
        moved.insert(0, words.pop())
    # All or nothing: half a fix ("שהם / כבר הגיעו") is no better.
    if not moved or words[-1] in _BREAK_BEFORE or not _HEB_RE.search(words[-1]):
        return None
    lead, rest = (u'♪ ', b[1:].lstrip()) if b.startswith(u'♪') else ('', b)
    # "♪ <i>..." / "♪ ?...": the words would land between the note and the
    # tag or the misplaced mark.
    if rest[:1] in _TRAILING_PUNCT_CHARS + '<{':
        return None
    if rest.split(' ')[0].rstrip(u',.;:!?…') in _CLAUSE_OPENERS:
        return None
    new_two = lead + ' '.join(moved) + ' ' + rest
    if len(_CUE_TAG_RE.sub('', new_two)) > _LINE_MAX_CHARS:
        return None
    return ' '.join(words), new_two


def mend_line_breaks(text):
    """Two-line cues whose first line ends on a word that belongs with the
    second ("על / הסוס") get it moved down (BBC 3.4). Our translations only,
    in logical order -- before fix_rtl_punctuation. Never raises."""
    if not text or '-->' not in text:
        return text
    try:
        out, changed = [], 0
        for block in parse_blocks(text):
            lines = block.split('\n')
            ti = _block_timecode_index(lines)
            body = lines[ti + 1:] if ti >= 0 else []
            mended = (_mend_two_lines(body[0].rstrip('\r'), body[1].rstrip('\r'))
                      if len(body) == 2 else None)
            if mended is None:
                out.append(block)
                continue
            lines[ti + 1:] = [mended[0] + ('\r' if body[0].endswith('\r') else ''),
                              mended[1] + ('\r' if body[1].endswith('\r') else '')]
            out.append('\n'.join(lines))
            changed += 1
        return stitch_blocks(out) if changed else text
    except Exception:
        return text


# A hearing-impaired sound cue written in capitals without brackets ("HEAVY
# BREATHING", "DOORBELL BUZZES" -- BBC style) gets past the bracket-based HI
# cleaner, and the model hands it back in English. Shouted dialogue in
# capitals is translated, so a cue is dropped only when the model returned
# it VERBATIM: no Hebrew, all capitals, and the very same line is in the
# source cue that starts at the same time. Never in an all-capitals file
# (broadcast captions write every line that way), never one line out of a
# longer cue, and never a line that speaks to or about someone.
_SOUND_CUE_RE = re.compile(r"^[A-Z][A-Z' -]*[A-Z]$")
_SPOKEN_RE = re.compile(r"\b(?:I|I'M|I'LL|ME|MY|WE|US|OUR|YOU|YOUR|YOU'RE|HE|SHE|THEY|"
                        r"IT'S|DON'T|CAN'T|WON'T|WHAT|WHY|WHERE|NO|YES|OK|OKAY|HEY|"
                        r"PLEASE|COME|GET|GO|STOP|LOOK|WAIT)\b")
_CUE_TAG_RE = re.compile(r'<[^>]*>')


def _cue_line(line):
    return re.sub(u'[\u200e\u200f\u202a-\u202e\u2066-\u2069]', '',
                  _CUE_TAG_RE.sub('', line)).strip(' -\r')


def drop_untranslated_sound_cues(text, source_text):
    """Remove capitalised sound cues the model left untranslated. A cue left
    with no text is removed whole. Returns `text` unchanged (the same object)
    when there is nothing to remove. Never raises."""
    try:
        if not text or not source_text:
            return text
        by_start = {}
        latin_lines = mixed_lines = 0
        for block in parse_blocks(source_text):
            lines = block.split('\n')
            ti = _block_timecode_index(lines)
            if ti < 0:
                continue
            text_lines = [_cue_line(l) for l in lines[ti + 1:]]
            for visible in text_lines:
                if re.search(r'[A-Za-z]', visible):
                    latin_lines += 1
                    if re.search(r'[a-z]', visible):
                        mixed_lines += 1
            if (len(text_lines) == 1 and _SOUND_CUE_RE.match(text_lines[0])
                    and len(text_lines[0]) >= 4
                    and not _SPOKEN_RE.search(text_lines[0])):
                by_start.setdefault(lines[ti].strip()[:12], set()).add(text_lines[0])
        if not by_start or mixed_lines < 0.7 * latin_lines:
            return text
        out, changed = [], False
        for block in parse_blocks(text):
            lines = block.split('\n')
            ti = _block_timecode_index(lines)
            cues = by_start.get(lines[ti].strip()[:12]) if ti >= 0 else None
            if not cues:
                out.append(block)
                continue
            body = [l for l in lines[ti + 1:] if _cue_line(l)]
            if len(body) != 1 or _cue_line(body[0]) not in cues:
                out.append(block)
                continue
            changed = True
        return stitch_blocks(out) if changed else text
    except Exception:
        return text
