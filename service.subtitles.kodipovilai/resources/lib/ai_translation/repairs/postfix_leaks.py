# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Post-fix: reasoning, draft and source-echo leaks of the model output.

import re
from resources.lib.ai_translation.repairs.postfix_glyphs import self_contained_markup
from resources.lib.core.srt.blocks import HEB_RE, INVISIBLE_BIDI, PROTECTED_RE, TIMECODE_RE, block_start, block_text_only, parse_blocks
from resources.lib.core.srt.timing import align_blocks, missing_blocks, restore_block_timings


# SRT styling tags ('<i>', '</font>') and ASS-style overrides ('{\\an8}'), used
# only to ask whether a line carries any real text of its own.
_MARKUP_RE = re.compile(r'<[^>]*>|\{[^}]*\}')

# Gemini occasionally prints its private self-edit after the subtitle line,
# then prints a corrected line as well. This is not ordinary source echo: the
# leak is mixed Hebrew/English inside one generated cue, so strip_source_echo
# quite correctly leaves it alone. Match only an explicitly editorial fragment
# inside brackets/parentheses. A bare "Wait", ordinary stage direction,
# quotation or arrow is insufficient.
_EDITORIAL_FRAGMENT_RE = re.compile(
    r'\[[^\]\r\n]{1,1000}\]|\([^\)\r\n]{1,1000}\)')
_EDITORIAL_ACTION_RE = re.compile(
    r"(?:\blet['’]?s\s+(?:fix|correct|translate|rephrase)\b|"
    r'\btypo\b\s*(?:->|→)|\btranslate\s+naturally\b|'
    r'\bkeep\s+as\s+is\b|'
    r'\bwait\b(?=[^\r\n]{0,300}(?:\bfix\b|\bcorrect\b|\btypo\b|'
    r'\bidiom\b|\bliteral\b|\btranslate\b|->|→)))', re.I)
# The same leak without the brackets the pattern above needs (QA, Oct 2026,
# a Gilmore Girls chunk that passed its retry): draft lines opened by an
# arrow ("-> ...אוקיי"), labelled working lines ("Let's check Arabic: ...",
# "Hebrew: ..."), and English notes on who is male or female ("(Drella is
# female)", "(Wait: Drella to Lorelai, ...)"). A subtitle line never opens
# with an arrow or such a label, and a Hebrew cue has no English note on a
# character's gender; the source check below still lets dialogue through.
_REASONING_LINE_RE = re.compile(
    r"^(?:-+>|=>|→|"
    r"let['’]?s\s+(?:check|see|look|fix|correct|verify)\b|"
    r"(?:hebrew|arabic|english|translation|note|check|correction)\s*:)",
    re.I)
_REASONING_FRAGMENT_RE = re.compile(
    r"\bwait\s*[:,]|"
    r"\b(?:is|are|was)\s+(?:a\s+)?(?:female|male)\b|"
    r"\b(?:feminine|masculine)\s+(?:addressee|speaker|form|verb|plural)\b|"
    r"\b(?:speaking|talking)\s+to\b[^()\r\n]{0,80}\b(?:female|male)\b",
    re.I)
# Field report (Oct 2026, update 756): the working also reached the screen in
# the middle of a line, with no brackets -- 'תודה שבאשת. / ... wait, "Thanks
# for coming." Let's check Spanish ref: ... (masc. singular) ... Let's use:
# ...', and 'X. / Y. / Z. Let's use: X.'. A Hebrew cue carries neither English
# deliberation on how to translate it nor drafts side by side; the source
# check still lets dialogue that says the same through.
_REASONING_INLINE_RE = re.compile(
    r"\blet['’]?s\s+(?:use|check|see|look|fix|correct|verify|do|try|keep|"
    r"say|write|translate|pick|choose|rephrase|go\s+with|stick\s+with)\b|"
    r"\bwait\s*[:,]|"
    r"\b(?:masc|fem)\.|\b(?:masculine|feminine|singular|plural|addressee)\b|"
    r"\bgender[- ]neutral\b|\bif\s+(?:female|male|plural|singular)\b|"
    r"\b(?:spanish|arabic|french|german|hebrew|english|italian|portuguese|"
    r"russian|turkish|greek|dutch|polish|romanian)(?:\s+ref(?:erence)?)?\s*:|"
    r"\bref(?:erence)?\s*:|\bin\s+hebrew\b|"
    r"\bdepending\s+on\s+(?:the\s+)?context\b|\bwho['’]?s\s+speaking\b",
    re.I)
# Drafts side by side: Hebrew, a spaced slash, Hebrew ("ו/או" has no spaces).
_DRAFTS_RE = re.compile(
    '[א-ת][.!?,…"\'״]*\\s+/\\s+[-"\'״]?[א-ת]')
# Where a leaked line's working begins once it is known to be a leak: an arrow
# to the next draft, or English prose (two Latin words in a row).
_DRAFT_CUT_RE = re.compile(
    r"\s(?:-+>|=>|→)|\b[A-Za-z]+['’]?[A-Za-z]*,?\s+[A-Za-z]+\b")
# Leading punctuation the RTL fix moves around; a '-' that opens an arrow
# stays. A bare '>' / '>>' is a caption speaker marker, never an arrow here.
_REASONING_LEAD_RE = re.compile('^(?:[\\s.,!?:;"\'\u05f4\u2013\u2014]|-(?!-*>))+')


_SOURCE_ARROW_RE = re.compile(r'-+>|=>|→')


def _reasoning_line(line, source_has_arrow=False):
    line = ''.join(c for c in line if c not in INVISIBLE_BIDI)
    line = _MARKUP_RE.sub('', line)
    m = _REASONING_LINE_RE.match(_REASONING_LEAD_RE.sub('', line))
    # A sign or caption arrow the source has anywhere ("Exit ->") may open
    # the Hebrew line once the word order turns around.
    return bool(m) and not (source_has_arrow and _SOURCE_ARROW_RE.match(m.group(0)))


def _plain(text):
    return _MARKUP_RE.sub('', ''.join(c for c in text if c not in INVISIBLE_BIDI))


def _phrase(text):
    return re.sub(r'\s+', ' ', text.replace('’', "'")).strip().lower()


# Where the model states the line it settled on: "Let's use: ...", "Hebrew:",
# "in Hebrew: ..." -- and, for the answer only, an arrow to the next draft.
_ANSWER_LABEL_RE = re.compile(
    r"let['’]?s\s+(?:use|do|go\s+with|stick\s+with|say|write|keep|translate|"
    r"pick|choose)\b[^:\n]{0,40}:|\b(?:in\s+)?hebrew\s*:", re.I)
_ANSWER_MARK_RE = re.compile(_ANSWER_LABEL_RE.pattern + r"|-+>|=>|→", re.I)


def _inline_reasoning(body, source_clean, strong_only=False):
    """English deliberation, or drafts side by side, anywhere in a Hebrew cue.
    A phrase the aligned source says too (source_clean: lowercased) is
    dialogue -- each phrase on its own: "Wait," in the source does not let a
    "Let's use:" through. strong_only (no source to compare with): one phrase
    alone ("Singular Value", a quoted "Let's check") is not enough; it takes
    a stated answer ("Let's use:") or two different phrases."""
    plain = _plain(body)
    source_clean = _phrase(source_clean)
    said = set(_phrase(m.group(0)) for m in _REASONING_INLINE_RE.finditer(plain))
    said = [p for p in said if p not in source_clean]
    if said and (not strong_only or len(said) > 1 or _ANSWER_LABEL_RE.search(plain)):
        return True
    if _redraft_arrow(plain) and not _SOURCE_ARROW_RE.search(source_clean):
        return True
    return bool(_DRAFTS_RE.search(plain)) and '/' not in source_clean


_MID_ARROW_RE = re.compile(r'\s(?:-+>|=>|→)\s*')
_HE_WORDS_RE = re.compile('[א-ת]+')


def _redraft_arrow(plain):
    """An arrow mid-line to Hebrew the cue already says before it -- the line
    drafted again after a note ("?ולמה שאעשה זאת / ?[Lama she-e'ese zot?] ->
    ולמה שאעשה זאת", QA 31 Oct 2026). An arrow to new words is dialogue."""
    for m in _MID_ARROW_RE.finditer(plain):
        after = ' '.join(_HE_WORDS_RE.findall(plain[m.end():].split('\n')[0]))
        before = ' '.join(_HE_WORDS_RE.findall(plain[:m.start()]))
        if len(after.split()) >= 2 and ' %s ' % after in ' %s ' % before:
            return True
    return False


_QUOTED_LINE_RE = re.compile('^["“”״](.+)["“”״]([.!?,…]*)$')
_LINE_LEAD_PUNCT_RE = re.compile('^([.,!?…]+)(?=[א-ת]|\\s*(?:-+>|=>|→|[\\[(]))')
_OPEN_QUOTE_END_RE = re.compile('(?:^|\\s)["“”״\']\\s*$')
_SENTENCE_END = tuple('.!?…')


def _first_of_drafts(line):
    """(line, how): the first of drafts side by side in `line` -- how is ''
    (none), 'sentence' (whole sentences, or a slash closing the line) or
    'word' (a word or phrase swapped mid-sentence: "הקיתארה / הנבל הנהדרת",
    no draft of the whole line to take)."""
    stripped = line.rstrip()
    if stripped.endswith(' /') and HEB_RE.search(stripped):
        return stripped[:-2].rstrip(), 'sentence'
    m = _DRAFTS_RE.search(line)
    if not m:
        return line, ''
    first = line[:m.start() + m.group(0).index('/')].rstrip()
    return first, ('sentence' if first.rstrip('"\'״').endswith(_SENTENCE_END) else 'word')


def _reasoning_note(text):
    """Whether `text` is one bracketed note of working: "(Drella is female)"."""
    return bool(_EDITORIAL_FRAGMENT_RE.fullmatch(text.strip())
                and (_EDITORIAL_ACTION_RE.search(text) or _REASONING_FRAGMENT_RE.search(text)
                     or _REASONING_INLINE_RE.search(text)))


def _cut_working(line):
    """`line` up to where working begins in it: an English phrase, an arrow,
    English prose or a bracketed note (and the bracket left dangling)."""
    cut = line
    for pattern in (_REASONING_INLINE_RE, _DRAFT_CUT_RE):
        m = pattern.search(cut)
        if m:
            cut = cut[:m.start()]
    for m in _EDITORIAL_FRAGMENT_RE.finditer(cut):
        if (_EDITORIAL_ACTION_RE.search(m.group(0))
                or _REASONING_FRAGMENT_RE.search(m.group(0))):
            cut = cut[:m.start()]
            break
    return cut.rstrip().rstrip('([*').rstrip()


# A dialogue line: a dash in front (not an arrow, not a dash alone) or, as
# 'reverse' writes it, after the line.
_SPEAKER_LINE_RE = re.compile(r'^\s*-(?!-*>)\s*\S|\S\s+-\s*$')
# "PERRY: ..." / "- Narrator: ..." in a source line: a name, not words said.
_SOURCE_NAME_RE = re.compile(r"(?m)^(\s*(?:-\s*)?)[A-Z][A-Za-z0-9 .'\-]{0,30}:\s+")


def _speakers(text):
    return sum(1 for l in text.split('\n') if _SPEAKER_LINE_RE.search(l))


def _cue_speakers(lines):
    """Dialogue lines a leaked cue had before its working: its lines up to
    the first working line, or through the first line stating an answer."""
    head = []
    for line in lines:
        if _reasoning_line(line):
            break
        head.append(line)
        if _ANSWER_MARK_RE.search(line):
            break
    return _speakers('\n'.join(head))


def _complete_answer(answer, source='', speakers=0):
    """Whether `answer` can stand for the whole cue: Hebrew, no quote or
    bracket left open (a piece cut out of a note: "אין לך)", 'הוא כתב "'),
    and as many dialogue lines as the cue's source line -- or, without one,
    as the cue had before its working (`speakers`) -- and, with the source
    line, not a fragment of it (under 40% of its words, names left out)."""
    if not answer or not HEB_RE.search(answer):
        return False
    if answer.count('(') != answer.count(')') or answer.count('[') != answer.count(']'):
        return False
    # a quote opened and cut off at once; one that opens or closes a
    # quotation spanning cues ('יקבל מענה."') is fine
    if _OPEN_QUOTE_END_RE.search(answer):
        return False
    if not source:
        return speakers < 2 or _speakers(answer) == speakers
    source = _SOURCE_NAME_RE.sub(r'\1', _plain(source))
    if _speakers(source) > 1 and _speakers(answer) != _speakers(source):
        return False
    words = len(_LATIN_ECHO_WORD_RE.findall(source))
    return words < 4 or len(answer.split()) >= 0.4 * words


def reasoning_final_answer(body, source=''):
    """The line a leaked cue's working settled on: the Hebrew after its last
    "Let's use:" / "Hebrew:" / arrow -- not one inside a bracketed note
    ("(Emily is female -> אין לך)" is working, not the answer), unless the
    note closes right after it ("(..., let's keep it accurate:)") -- cut where
    a note starts on its line. '' when there is none, or when it is not a
    whole answer (_complete_answer; `source`: the cue's source line)."""
    plain = '\n'.join(_plain(l) for l in body.split('\n'))
    spans = [(m.start(), m.end()) for m in _EDITORIAL_FRAGMENT_RE.finditer(plain)]
    starts = []
    for m in _ANSWER_MARK_RE.finditer(plain):
        inside = [s for s in spans if s[0] < m.start() < s[1]]
        if not inside:
            starts.append(m.end())
        elif plain[m.end():inside[0][1]].strip() in (')', ']'):
            starts.append(inside[0][1])
    # A note that ends its line, with the corrected line after it:
    # "ע/ עוררת ...? (Wait, Drella is speaking to Lorelai, ...)\n?האם לא רצית ..."
    # -- not when both are dialogue lines: that is the next speaker.
    for a, b in spans:
        line_end = plain.find('\n', b)
        if (line_end > 0 and not plain[b:line_end].strip(' .*')
                and _reasoning_note(plain[a:b])
                and not (_SPEAKER_LINE_RE.search(plain[plain.rfind('\n', 0, a) + 1:line_end])
                         and _SPEAKER_LINE_RE.search(plain[line_end + 1:].split('\n')[0]))):
            starts.append(line_end)
    if not starts:
        return ''
    start = max(starts)
    line_start = plain.rfind('\n', 0, start) + 1
    lead = _LINE_LEAD_PUNCT_RE.match(plain[line_start:].strip())
    keep = []
    for i, line in enumerate(plain[start:].split('\n')):
        line = line.strip().strip('*').strip()
        cut = _cut_working(line)
        working = cut != line
        cut, how = _first_of_drafts(cut)
        if how == 'word':
            return ''
        m = _QUOTED_LINE_RE.match(cut.strip())
        if m:
            cut = m.group(1) + ('' if m.group(1).endswith(tuple('.!?,…')) else m.group(2))
        if not HEB_RE.search(cut):
            if keep:
                break
            continue
        if i == 0 and lead and not cut.endswith(tuple('.!?,…')):
            # the RTL fix moved this line's end mark to its start
            cut += lead.group(1)
        keep.append(cut.strip())
        if working or how:
            break
    answer = '\n'.join(keep)
    speakers = 0 if source else _cue_speakers(plain.split('\n'))
    return answer if _complete_answer(answer, source, speakers) else ''


def reasoning_free_draft(body, source=''):
    """The Hebrew a leaked cue opened with, before the model's working began:
    the first of drafts side by side (and the next speaker's line after it),
    nothing from a working line or an English note on. '' when no Hebrew
    comes before the working, the drafts differ by a word mid-sentence, or it
    is not a whole answer (_complete_answer)."""
    keep = []
    lines = [_plain(raw).strip() for raw in body.split('\n')]
    for k, plain in enumerate(lines):
        if _reasoning_line(plain):
            break
        cut = _cut_working(plain)
        working = cut != plain
        # only a note ends this speaker's line; the next speaker's follows
        note_only = (working and _reasoning_note(plain[len(cut):].strip().rstrip(' .*'))
                     and _SPEAKER_LINE_RE.search(plain) and k + 1 < len(lines)
                     and _SPEAKER_LINE_RE.search(lines[k + 1])
                     and not _reasoning_line(lines[k + 1]))
        cut, how = _first_of_drafts(cut)
        if how == 'word':
            return ''
        if not HEB_RE.search(cut):
            break
        if how and plain.endswith(' -') and not cut.endswith('-'):
            cut += ' -'     # the dialogue dash 'reverse' put after the last draft
        keep.append(cut.strip())
        if (working and not note_only) or plain.endswith(' /'):
            break       # the working, or the other draft, goes on from here
    draft = '\n'.join(keep)
    speakers = 0 if source else _cue_speakers(lines)
    return draft if _complete_answer(draft, source, speakers) else ''


def leaked_cue_answer(body, source=''):
    """The model's own Hebrew in a leaked cue: the line its working settled
    on, else the draft it opened with; '' when it wrote no whole Hebrew
    answer. `source`: the cue's source line, when there is one."""
    return reasoning_final_answer(body, source) or reasoning_free_draft(body, source)


_AN_TAG_RE = re.compile(r'^\s*(\{\\an\d\})')


def restyle_like(body, text):
    """`text` with the position tag and whole-cue italics `body` had."""
    m = _AN_TAG_RE.match(body)
    rest = body[m.end():].strip() if m else body.strip()
    if rest.startswith('<i>') and rest.endswith('</i>') and rest.count('<i>') == 1:
        text = '<i>' + text + '</i>'
    return (m.group(1) if m else '') + text


_LATIN_ECHO_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_LATIN_ECHO_FINITE_VERBS = frozenset((
    'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'can', 'could',
    'will', 'would', 'shall', 'should', 'may', 'might', 'must',
))
_SEMANTIC_CLAUSE_SPLIT_RE = re.compile(r'[.!?;,]+|\s+[-–—]\s+')
_NEGATIVE_FUTURE_RE = re.compile(
    r"\b(?:"
    r"i(?:\s+am|['’]m)|"
    r"we(?:\s+(?:are|were)|['’]re)|"
    r"you(?:\s+(?:are|were)|['’]re)|"
    r"he(?:\s+(?:is|was)|['’]s)|"
    r"she(?:\s+(?:is|was)|['’]s)|"
    r"they(?:\s+(?:are|were)|['’]re)"
    r")\s+not\s+(?:going\s+to\s+|gonna\s+)", re.I)
_NEGATIVE_FUTURE_TAIL_EXCEPTION_RE = re.compile(
    r'^(?:not\b|never\b|fail\s+to\b|forget\s+to\b|neglect\s+to\b|'
    r'refuse\s+to\b|be\s+un\w+\b)', re.I)
_NEGATIVE_FUTURE_CONDITIONAL_RE = re.compile(
    r'\b(?:if|unless|until|when|whenever|provided(?:\s+that)?|'
    r'providing(?:\s+that)?|assuming(?:\s+that)?|in\s+case|'
    r'as\s+long\s+as|only\s+if|except\s+(?:if|when))\b', re.I)
# A positive-looking target is not proof by itself: "I'm not going to lie" can
# correctly become "I intend to tell the truth". Require the same concrete
# action on both sides. This intentionally small closed set covers the observed
# high-harm inversion and a few unambiguous equivalents; unknown paraphrases
# abstain rather than consume retries or replace good Hebrew with source text.
_NEGATION_ACTION_EQUIVALENTS = (
    (re.compile(r'^(?:kill|murder|slay)\b', re.I),
     re.compile(r'(?<![א-ת])(?:להרוג|לרצוח|לחסל)(?![א-ת])')),
    (re.compile(r'^(?:hurt|harm|injure)\b', re.I),
     re.compile(r'(?<![א-ת])(?:לפגוע|להכאיב|לפצוע)(?![א-ת])')),
)
_HE_POSITIVE_INTENT_RE = re.compile(
    r'(?<![א-ת])(?:(?:אני|אנחנו|אתה|את|הוא|היא|הם|הן)\s+)?'
    r'(?:הולך|הולכת|הולכים|הולכות|מתכוון|מתכוונת|מתכוונים|מתכוונות|'
    r'עומד|עומדת|עומדים|עומדות)\s+(?:ל|ש)[א-ת]')
_HE_NEGATION_RE = re.compile(
    r'(?<![א-ת])(?:'
    r'ו?(?:ש|כש)?(?:לא|אל)|ו?(?:ללא|בלי)|לעולם\s+לא|'
    r'אינ(?:ני|נו|ך|כם|כן|ו|ה|ם|ן)|אין'
    r')(?![א-ת])')


def generated_editorial_leak_indices(src_blocks, out_blocks, strong_only=False):
    """Indices of generated SRT blocks containing model self-edit prose.

    This is a rejection detector, not a text eraser. The caller retries the
    complete cue; cutting only the parenthesis could leave the known-wrong
    draft before it (the observed negation failure did exactly that).

    A candidate is rejected only when all of these hold:
      * it contains Hebrew, so it is generated target output;
      * an English editorial action sits inside [] or ();
      * the aligned source cue did not itself contain such editorial text.

    The last condition preserves literal dialogue about fixing translations,
    source stage directions and examples. English deliberation and drafts
    side by side count anywhere in the cue, brackets or not. Fully fail-open:
    uncertainty returns no indices and the existing translation path remains
    in control. With no source at all (a cached file whose source is gone)
    every cue is judged as if its source said nothing; strong_only then asks
    more than one English phrase of the inline check (_inline_reasoning).
    """
    try:
        if not out_blocks:
            return []
        sources = aligned_source_texts(src_blocks, out_blocks)
        bad = []
        for index, block in enumerate(out_blocks):
            body = block_text_only(block) or ''
            if not HEB_RE.search(body):
                continue
            source = sources[index]
            source_clean = re.sub(r'\s+', ' ', source).strip().lower()
            source_is_editorial = bool(_EDITORIAL_ACTION_RE.search(source_clean))
            leaked = False
            for match in _EDITORIAL_FRAGMENT_RE.finditer(body):
                fragment = ''.join(c for c in match.group(0)
                                   if c not in INVISIBLE_BIDI)
                if not (_EDITORIAL_ACTION_RE.search(fragment)
                        or _REASONING_FRAGMENT_RE.search(fragment)):
                    continue
                fragment_clean = re.sub(
                    r'\s+', ' ', fragment[1:-1]).strip().lower()
                if (source_is_editorial
                        or _REASONING_FRAGMENT_RE.search(source_clean)
                        or (fragment_clean and fragment_clean in source_clean)):
                    continue
                leaked = True
                break
            # A source line that opens the same way ("-> Exit", "Note: ...")
            # makes it dialogue; both sides are cleaned the same way.
            if not leaked and not any(_reasoning_line(l) for l in source.split('\n')):
                arrow = bool(_SOURCE_ARROW_RE.search(source))
                leaked = any(_reasoning_line(l, arrow) for l in body.split('\n'))
            if not leaked:
                leaked = _inline_reasoning(body, source_clean, strong_only)
            if leaked:
                bad.append(index)
        return bad
    except Exception:
        return []


# Everything generated_editorial_leak_indices looks for, in one pattern: a
# file with none of it anywhere has no leaked cue. (An arrow, but not the
# one in every timecode line, which may_carry_reasoning leaves out.)
_ANY_REASONING_RE = re.compile('|'.join(p.pattern for p in (
    _REASONING_INLINE_RE, _REASONING_FRAGMENT_RE, _EDITORIAL_ACTION_RE,
    _DRAFTS_RE, _SOURCE_ARROW_RE))
    + r"|let['’]?s\s+(?:check|see|look|fix|correct|verify)\b"
    + r"|(?:hebrew|arabic|english|translation|note|check|correction)\s*:", re.I)


_LATIN_PAIR_RE = re.compile('[A-Za-z]{2}')


def may_carry_reasoning(text):
    """False only when no cue of `text` can be a leak, so a clean cached file
    skips the per-cue scan. Every sign of one has two Latin letters, an arrow
    or a slash, so only the lines with one are searched -- cleaned as the
    scan cleans them, with the lines on either side (drafts side by side can
    break at the slash)."""
    try:
        lines = (text or '').splitlines()
        for i, line in enumerate(lines):
            if not ('/' in line or '>' in line or '→' in line
                    or _LATIN_PAIR_RE.search(line)):
                continue
            if TIMECODE_RE.match(line.strip()):
                continue
            # each line cleaned on its own, as the scan does ("<3" must not
            # swallow the next line's arrow)
            window = '\n'.join(_plain(l) for l in lines[max(0, i - 1):i + 2]
                               if not TIMECODE_RE.match(l.strip()))
            if _ANY_REASONING_RE.search(window):
                return True
        return False
    except Exception:
        return True


def aligned_source_texts(src_blocks, out_blocks):
    """The text of the source cue each output block was translated from
    (matched by start time, then by order), '' where there is none."""
    sources = {}
    for block in src_blocks or ():
        start = block_start(block)
        if start is not None:
            sources.setdefault(start, []).append(block_text_only(block) or '')
    seen = {}
    texts = []
    for block in out_blocks:
        start = block_start(block)
        occurrence = seen.get(start, 0)
        seen[start] = occurrence + 1
        candidates = sources.get(start, ())
        texts.append(candidates[occurrence] if occurrence < len(candidates) else '')
    return texts


def generated_source_echo_indices(src_blocks, out_blocks):
    """Detect a full English source sentence echoed beside generated Hebrew.

    AI output has appeared as a Hebrew draft followed by ``[and his victim is
    in the wind,]``. The older cleanup only removes non-Hebrew lines *before*
    Hebrew and therefore leaves this trailing form in caches and uploads.

    The existing source-echo cleanup already removes leading English safely.
    This detector targets the surviving shape only: an exact English sentence
    appears *after* generated Hebrew, either on its own line or in brackets.
    It requires the observed continuation shape (source ends in comma, semicolon
    or colon), at least six words, an English finite/auxiliary verb, a
    monolingual and unquoted source, and non-title-like casing. Ordinary complete
    sentences, quoted literals, short codes and untranslated-only cues abstain.
    """
    try:
        if not src_blocks or not out_blocks:
            return []
        sources = {}
        for block in src_blocks:
            start = block_start(block)
            if start is not None:
                sources.setdefault(start, []).append(block_text_only(block) or '')
        seen = {}
        bad = []
        for index, block in enumerate(out_blocks):
            body = block_text_only(block) or ''
            if not HEB_RE.search(body):
                continue
            start = block_start(block)
            occurrence = seen.get(start, 0)
            seen[start] = occurrence + 1
            candidates = sources.get(start, ())
            if occurrence >= len(candidates):
                continue
            source_text = candidates[occurrence]
            source_stripped = source_text.strip()
            if (HEB_RE.search(source_text)
                    or any(mark in source_text for mark in ('"', '“', '”', '«', '»'))
                    or not source_stripped.endswith((',', ';', ':'))):
                continue
            source_tokens = _LATIN_ECHO_WORD_RE.findall(source_text)
            source_words = tuple(
                word.lower().replace('’', "'") for word in source_tokens)
            if (len(source_words) < 6
                    or not _LATIN_ECHO_FINITE_VERBS.intersection(source_words)):
                continue
            title_case_words = sum(
                bool(word and word[0].isupper()) for word in source_tokens)
            if title_case_words * 5 >= len(source_tokens) * 3:
                continue
            first_hebrew = HEB_RE.search(body).start()
            fragments = []
            cursor = 0
            for line in body.splitlines(True):
                line_text = line.rstrip('\r\n')
                if cursor > first_hebrew:
                    fragments.append(line_text)
                cursor += len(line)
            fragments.extend(
                match.group(0)[1:-1]
                for match in _EDITORIAL_FRAGMENT_RE.finditer(body)
                if match.start() > first_hebrew
            )
            for fragment in fragments:
                if HEB_RE.search(fragment):
                    continue
                fragment_words = tuple(
                    word.lower().replace('’', "'")
                    for word in _LATIN_ECHO_WORD_RE.findall(fragment))
                if fragment_words == source_words:
                    bad.append(index)
                    break
        return bad
    except Exception:
        return []


def generated_negation_loss_indices(src_blocks, out_blocks):
    """Detect one narrow, high-confidence polarity-loss suspicion.

    The observed catastrophic result translated ``I'm not gonna kill`` as
    ``אני הולכת להרוג``. General negation counting is unsafe in translation, so
    this detector does much less: source and target must have the same number of
    local clauses; one source clause must contain an explicit negative
    ``not going to/gonna`` future; and the corresponding Hebrew clause must use
    a positive intent construction (``הולך/מתכוון/עומד ל...``) with no Hebrew
    negation. Double-negative/negative-raising tails and conditional source
    cues abstain: Hebrew may preserve their meaning with a positive main verb
    plus ``רק אם``/``עד ש``, so same-action wording is not proof of inversion.

    It returns block indices for retry, never edits text. Anything ambiguous is
    ignored and remains under the ordinary source-fidelity checks.
    """
    try:
        if not src_blocks or not out_blocks:
            return []
        sources = {}
        for block in src_blocks:
            start = block_start(block)
            if start is not None:
                sources.setdefault(start, []).append(block_text_only(block) or '')
        seen = {}
        bad = []
        for index, block in enumerate(out_blocks):
            target = block_text_only(block) or ''
            if not HEB_RE.search(target):
                continue
            start = block_start(block)
            occurrence = seen.get(start, 0)
            seen[start] = occurrence + 1
            candidates = sources.get(start, ())
            if occurrence >= len(candidates):
                continue
            source = candidates[occurrence]
            # Real SRT contractions have no spaces, but the OPUS tokenizer used
            # by a prior audit produced "I 'm". Normalising here makes the
            # invariant independent of that serialization defect.
            source = re.sub(r"\s+(['’])\s*", r'\1', source)
            # Final Kodi-ready subtitles may wrap a cue in RLE/PDF controls.
            # They are presentation metadata, not semantic clauses; leaving the
            # closing PDF after a full stop creates a spurious third clause and
            # silently disables this gate on the exact delivered artifact.
            source = ''.join(c for c in source if c not in INVISIBLE_BIDI)
            target = ''.join(c for c in target if c not in INVISIBLE_BIDI)
            if _NEGATIVE_FUTURE_CONDITIONAL_RE.search(source):
                continue
            source_clauses = [part.strip() for part in
                              _SEMANTIC_CLAUSE_SPLIT_RE.split(source)
                              if part.strip()]
            target_clauses = [part.strip() for part in
                              _SEMANTIC_CLAUSE_SPLIT_RE.split(target)
                              if part.strip()]
            if len(source_clauses) != len(target_clauses):
                continue
            for source_clause, target_clause in zip(source_clauses, target_clauses):
                match = _NEGATIVE_FUTURE_RE.search(source_clause)
                if not match:
                    continue
                tail = source_clause[match.end():].lstrip()
                if _NEGATIVE_FUTURE_TAIL_EXCEPTION_RE.match(tail):
                    continue
                same_action = any(
                    source_action.search(tail)
                    and target_action.search(target_clause)
                    for source_action, target_action
                    in _NEGATION_ACTION_EQUIVALENTS)
                if (same_action
                        and _HE_POSITIVE_INTENT_RE.search(target_clause)
                        and not _HE_NEGATION_RE.search(target_clause)):
                    bad.append(index)
                    break
        return bad
    except Exception:
        return []


def generated_output_blocks(src_blocks, response, fill_missing=False):
    """Shape untrusted model output to the exact requested source slots.

    Returns ``(blocks, missing_before_fallback, integrity_rejections)``. Only
    source start-times and multiplicity are admitted, isolated timing typos are
    repaired by restore_block_timings, and high-confidence generated-output
    violations are rejected. With ``fill_missing=True`` every rejected/missing
    slot receives its original source block so progressive display and final
    assembly cannot lose a cue.
    """
    try:
        parsed = parse_blocks(response)
        # Establish source ownership before semantic rejection. The model may
        # mistype one timestamp while retaining the declared SRT index and both
        # neighbours; restore_block_timings can safely pin that cue. Running the
        # detectors first would miss the cue because its wrong timestamp has no
        # source owner, then accept it immediately after the repair.
        restored = restore_block_timings(src_blocks, parsed)
        rejected = sorted(set(
            generated_editorial_leak_indices(src_blocks, restored)
            + generated_source_echo_indices(src_blocks, restored)
            + generated_negation_loss_indices(src_blocks, restored)))
        if rejected:
            rejected_set = set(rejected)
            restored = [block for index, block in enumerate(restored)
                        if index not in rejected_set]
        aligned = align_blocks(src_blocks, restored)
        missing = missing_blocks(src_blocks, aligned)
        if fill_missing and missing:
            aligned = align_blocks(src_blocks, aligned, missing)
        return aligned, missing, len(rejected)
    except Exception:
        # This function is the final ownership gate before output reaches Kodi.
        # An internal helper failure must not turn that gate into a pass-through.
        # During final assembly retain the complete source; during an earlier
        # rung report every source cue as missing so the normal retry ladder can
        # still recover a Hebrew result.
        try:
            source = list(src_blocks or [])
        except Exception:
            source = []
        return (source if fill_missing else []), source, 0


def strip_source_echo(text):
    """Drop source-language lines the model emitted above its own translation.

    Reported as three English lines followed by three Hebrew ones inside a
    single cue. The model occasionally answers with the original AND the
    translation stacked; it is not deterministic, which is why the same title
    can come back clean on a second run and why a copy fetched from the pool
    (a different run, by a different user) can be clean while a local one is
    not. Nothing on either side removed it.

    Narrow on purpose, in the same spirit as strip_leaked_arabic: a cue is only
    touched when its leading lines carry NO Hebrew at all and a later line
    DOES. That is the echo shape and nothing else -- the Hebrew is always the
    part that survives, so a cue can never become empty, and a subtitle with no
    Hebrew in it (a foreign cue, a sign, a song credit, a line the model
    declined to translate) is never altered, because there is no Hebrew line
    for the non-Hebrew ones to be an echo OF. A line with any Hebrew in it
    counts as Hebrew, so 'נולדתי בChina' is never a candidate either.

    WHAT THIS CAN COST, stated plainly rather than left to be discovered. The
    shape is a heuristic, and these are indistinguishable from an echo:

      * a cue the model translated only PARTLY -- one line rendered, one left
        in the source language;
      * a leading line that is legitimately not Hebrew and not a duplicate --
        a brand name ('STARBUCKS'), an on-screen clock ('12:00 PM'), a chyron.

    Numeric values absent from the Hebrew and quoted literals now veto deletion.
    Unquoted nonnumeric names can still be indistinguishable from an echo. The bet
    is that inside OUR OWN model's output -- which is all this ever sees, via
    the ai_output gate -- a non-Hebrew line sitting directly above a Hebrew one
    is overwhelmingly the echo defect rather than any of the above. Cues with
    no Hebrew, the far more common way untranslated text survives, are exempt
    entirely. Two things are refused outright because the damage would exceed
    the defect: emptying a cue, and orphaning a styling tag.

    Never raises into the caller.

    Cues are separated by scanning for blank lines rather than by splitting on
    '\\n\\n', because SRT is conventionally a CRLF format and in a CRLF file the
    cue separator is '\\r\\n\\r\\n' -- which contains no '\\n\\n' at all. Splitting
    on the literal would hand the whole file back as ONE cue, and the fix would
    then apply to the first cue only and silently skip every echo after it. The
    only caller today reads in text mode, so it never sees a '\\r'; this does not
    depend on that staying true. A line keeps whatever '\\r' it arrived with, so
    the file's line endings come back exactly as they went in.
    """
    if not text:
        return text
    try:
        if not HEB_RE.search(text):
            return text
        lines = text.split('\n')
        out, cue, changed = [], [], False

        def _flush(cue):
            # Keep the index + timecode lines exactly as they are; the cue body
            # is whatever follows the '-->' line.
            head = 0
            for i, ln in enumerate(cue):
                if '-->' in ln:
                    head = i + 1
                    break
            body = cue[head:]
            if len(body) < 2 or not any(HEB_RE.search(l) for l in body):
                return cue, False
            first_heb = next(i for i, l in enumerate(body)
                             if HEB_RE.search(l))
            # Only a LEADING run of non-Hebrew lines counts as an echo. There is
            # no blank-line check here because a blank line cannot reach this
            # point: blanks delimit cues in the loop below, so every line in
            # `cue` is real text. Layout is preserved by never entering a cue
            # that a blank line already ended.
            #
            # A lead line that is PURE MARKUP is not an echo and must not go.
            # '<i>' on its own line above a Hebrew line is formatting, and
            # dropping it strands the matching '</i>' below -- which does not
            # merely lose a line, it corrupts the styling of everything after
            # it. Anything with real text left after tags are removed is still
            # a candidate; only the empty ones veto the cue.
            lead = body[:first_heb]
            if lead and all(_MARKUP_RE.sub('', l).strip() for l in lead):
                removed='\n'.join(lead);kept='\n'.join(body[first_heb:])
                if not self_contained_markup(removed):return cue, False
                # A clock, amount or identifier is content, even above Hebrew.
                from collections import Counter
                numbers=lambda value:Counter(re.findall(r'\d+(?:[.,:/-]\d+)*',value))
                if numbers(removed)-numbers(kept):return cue, False
                # Quoted foreign text may be a literal/password, not an echo.
                if any(m.group(0)[:1] in ('"','“','«') for m in PROTECTED_RE.finditer(removed)):
                    return cue, False
                return cue[:head] + body[first_heb:], True
            return cue, False

        for ln in lines:
            if ln.strip():
                cue.append(ln)
                continue
            kept, hit = _flush(cue)
            out.extend(kept)
            out.append(ln)          # the separator, '\r' and all
            cue, changed = [], changed or hit
        kept, hit = _flush(cue)
        out.extend(kept)
        changed = changed or hit
        return '\n'.join(out) if changed else text
    except Exception:
        return text
