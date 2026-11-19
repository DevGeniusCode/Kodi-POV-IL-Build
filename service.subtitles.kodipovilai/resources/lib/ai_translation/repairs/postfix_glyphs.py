# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Post-fix: foreign glyphs inside Hebrew (Latin, Arabic, Greek) and glyph normalisation.

import re
from resources.lib.core.srt.blocks import _HAS_HEBREW_RE, _INVISIBLE_BIDI, _PDF, _PROTECTED_RE, _RLE
from resources.lib.core.srt.classify import is_google_translated


# --- leaked Arabic gender-reference text -------------------------------------
# When the Arabic gender reference is on (forced on for everyone), the prompt
# carries REAL Arabic lines from a human translation of the same scene, as a
# gender oracle. prompt.py tells the model in capitals to take "the gender and
# NOTHING else", but a prompt is an instruction, not a guarantee: field report
# of "Arabic word completions / half words" turning up inside otherwise-correct
# Hebrew lines. The speaker-prefix leak already has a post-processor; this one
# had none, so a leak shipped straight to the player.
#
# Deliberately narrow. It strips Arabic ONLY from a line that also carries
# Hebrew -- i.e. a contaminated translation, which is the reported shape. A line
# that is entirely Arabic is left alone: that is what an on-screen sign or a
# deliberately untranslated line looks like, and destroying it would be worse
# than the leak. Same reasoning as strip_leaked_speaker_prefix(hebrew_only=True).
# Arabic script, EXCLUDING two things that live inside the same Unicode blocks
# and are not leaks: Arabic-Indic DIGITS (U+0660-0669 -- "השעה ١٢:٣٠" is a time,
# not a leaked word) and U+FEFF, the byte-order mark, which is the last
# codepoint of Presentation Forms-B and would otherwise make a stray BOM between
# two Hebrew words look like Arabic. Both were found by review.
_ARABIC_CH = (u'\u0600-\u065f\u066a-\u06ff'      # Arabic (no Arabic-Indic digits)
              u'\u0750-\u077f'                    # Arabic Supplement
              u'\u0870-\u089f'                    # Arabic Extended-B
              u'\u08a0-\u08ff'                    # Arabic Extended-A
              u'\ufb50-\ufdff'                    # Presentation Forms-A
              u'\ufe70-\ufefc')                   # Presentation Forms-B (no BOM)
# INVARIANT, and the thing that actually makes strip_leaked_arabic safe:
# these two classes are DISJOINT, and _ARABIC_RUN_RE's continuation class is
# built only from _ARABIC_CH plus blanks/tatweel/Arabic diacritics/Arabic
# punctuation. The regex therefore CANNOT consume a Hebrew character, whatever
# surrounds it -- so a line can never lose part of its Hebrew sentence. The
# 'did the Hebrew survive' check in strip_leaked_arabic is a net under that,
# not the guarantee itself, and it only notices the loss of the LAST Hebrew
# character. Anyone widening either class must keep them disjoint.
# A run of Arabic plus ONLY what belongs to it: surrounding blanks, Arabic
# diacritics/tatweel, and ARABIC punctuation. Latin/Hebrew punctuation is
# deliberately excluded -- the '.' in "קארל מת. ـكِ" and the '?' in
# "מה קורה? ماذا" end the HEBREW sentence, and an earlier version that swallowed
# them turned a leak into a second defect.
_ARABIC_RUN_RE = re.compile(
    u'[ \t]*[' + _ARABIC_CH + u'][' + _ARABIC_CH + u' \t\u0640\u064b-\u0652\u060c\u061b\u061f]*')
_HAS_ARABIC_RE = re.compile(u'[' + _ARABIC_CH + u']')


# --- the leak that is a SCRIPT SWAP, not a foreign word ----------------------
# Deleting the Arabic is right when the model pasted a foreign WORD in. It is
# wrong for the commonest shape by far, which measurement on a reported file
# turned up: the model typed the correct HEBREW WORD but rendered part of it in
# Arabic letters. Deletion then mutilates the word --
#     לעצمي     -> לעצ         (wanted: לעצמי)
#     רוمانטיקה -> רוטיקה      (wanted: רומאנטיקה)
#     קريستل    -> ק           (wanted: קריסתל)
# -- which is how a "letters in the middle of a Hebrew word" report becomes a
# "half a word is missing" one. Hebrew and Arabic are cognate alphabets, so the
# fragment can simply be spelled back into Hebrew letters instead.
#
# Restricted to exactly the swap shape, because that is the only shape where
# transliteration is meaningful:
#   * the run must TOUCH a Hebrew letter with no space in between (a run behind
#     a space is a separate token -- a real Arabic word -- and is deleted as
#     before),
#   * and it must be a short, punctuation-free, space-free letter run. A phrase
#     ("وه، لا، يا لقد...") is a genuine pasted sentence; spelling it in Hebrew
#     letters would produce gibberish, so it stays on the deletion path.
# Nothing here can remove Hebrew -- it only ever ADDS Hebrew letters -- so the
# module-level guarantee that a line never loses its Hebrew holds a fortiori.
_AR2HE = {
    u'ا': u'א', u'أ': u'א', u'إ': u'א',
    u'آ': u'א', u'ء': u'א',
    u'ب': u'ב', u'ت': u'ת', u'ث': u'ת',
    u'ة': u'ה',
    u'ج': u'ג', u'ح': u'ח', u'خ': u'ח',
    u'د': u'ד', u'ذ': u'ד',
    u'ر': u'ר', u'ز': u'ז',
    u'س': u'ס', u'ش': u'ש',
    u'ص': u'צ', u'ض': u'צ',
    u'ط': u'ט', u'ظ': u'ט',
    u'ع': u'ע', u'غ': u'ע',
    u'ف': u'פ', u'ق': u'ק', u'ك': u'כ',
    u'ل': u'ל', u'م': u'מ', u'ن': u'נ',
    u'ه': u'ה', u'و': u'ו',
    u'ي': u'י', u'ى': u'י', u'ئ': u'י',
    u'ؤ': u'ו',
}
# Harakat / tatweel carry no consonant, so they simply drop. Built from the
# codepoints _ARABIC_RUN_RE itself allows inside a run (U+064B-U+0652 plus
# tatweel), plus the Quranic marks, so the two cannot drift apart.
_AR_MARKS = frozenset([chr(0x0640)]
                      + [chr(c) for c in range(0x064B, 0x0653)]
                      + [chr(c) for c in range(0x0610, 0x061B)]
                      + [chr(c) for c in range(0x0653, 0x0656)]
                      + [chr(0x0670)])
# Hebrew letters that take a different shape at the end of a word.
_HE_FINAL = {u'כ': u'ך', u'מ': u'ם', u'נ': u'ן',
             u'פ': u'ף', u'צ': u'ץ'}
_MAX_GLUED_RUN = 8


def _transliterate_glued_run(run, before, after, ate_space):
    """Hebrew spelling of an Arabic run that is part of a Hebrew word, or None
    when this run is not that shape and the caller should fall through to the
    existing deletion path. Never raises."""
    try:
        if ate_space:
            return None          # behind a space: a separate token, not a swap
        # _ARABIC_RUN_RE's continuation class includes blanks, so a run can
        # carry the space that FOLLOWS it ("זוכرة כשהיית" matches "رة "). That
        # space belongs to the Hebrew sentence -- keep it, or the two words
        # weld together, which is how one defect used to become another.
        core = run.rstrip(' \t')
        tail = run[len(core):]
        if not core or len(core) > _MAX_GLUED_RUN:
            return None
        # must be glued to Hebrew on at least one side
        ends_word = bool(tail) or not (after and _HAS_HEBREW_RE.match(after))
        touches = ((before and _HAS_HEBREW_RE.match(before))
                   or (not tail and after and _HAS_HEBREW_RE.match(after)))
        if not touches:
            return None
        out = []
        for ch in core:
            if ch in _AR_MARKS:
                continue
            he = _AR2HE.get(ch)
            if he is None:
                return None          # punctuation / anything unmapped: not a swap
            out.append(he)
        if not out:
            return None
        # word-final letters take their sofit form, but only when the word
        # really ends here (the next character is not more of the same word).
        if ends_word:
            out[-1] = _HE_FINAL.get(out[-1], out[-1])
        return u''.join(out) + tail
    except Exception:
        return None


def _map_unprotected(text, transform):
    """Keep protected bytes and node-boundary whitespace exactly as supplied."""
    out=[];start=0
    for match in _PROTECTED_RE.finditer(text):
        part=text[start:match.start()]
        if part.strip():
            left=part[:len(part)-len(part.lstrip())]
            right=part[len(part.rstrip()):]
            out.append(left+transform(part.strip())+right)
        else:out.append(part)
        out.append(match.group(0));start=match.end()
    part=text[start:]
    if part.strip():
        left=part[:len(part)-len(part.lstrip())];right=part[len(part.rstrip()):]
        out.append(left+transform(part.strip())+right)
    else:out.append(part)
    return ''.join(out)


def _self_contained_markup(text):
    """Removing a prefix may not strand an opening/closing tag elsewhere."""
    from html.parser import HTMLParser
    class Balance(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False);self.stack=[];self.valid=True
        def handle_starttag(self,tag,attrs):
            if tag not in ('br','hr','wbr','img','meta','link','input'):self.stack.append(tag)
        def handle_startendtag(self,tag,attrs):pass
        def handle_endtag(self,tag):
            if not self.stack or self.stack[-1]!=tag:self.valid=False
            else:self.stack.pop()
    parser=Balance()
    try:parser.feed(text);parser.close()
    except Exception:return False
    return parser.valid and not parser.stack


def _clean_arabic_from_line(body):
    """Repair visible unquoted text; preserve markup and quoted literals."""
    if _PROTECTED_RE.search(body):
        return _map_unprotected(body, _clean_arabic_from_line)
    # Replace a run with a SPACE when it stood between words, but with NOTHING
    # when it sat INSIDE one. A field report showed a single Arabic letter glued
    # into the middle of a Hebrew word ("להא<lam>ל"); substituting a space there
    # splits the word in two, which is a second defect rather than a repair.
    def _sub(m):
        start, end = m.start(), m.end()
        ate_space = m.group(0)[:1] in (' ', '\t')
        after = body[end:end + 1]
        before = body[start - 1:start] if start else ''
        glued = _transliterate_glued_run(m.group(0), before, after, ate_space)
        if glued is not None:
            return glued
        tight = (not ate_space and after and not after.isspace()
                 and before and not before.isspace())
        return '' if tight else ' '

    cleaned = _ARABIC_RUN_RE.sub(_sub, body)
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    cleaned = re.sub(r'[ \t]+(</)', r'\1', cleaned)   # no gap before a closing tag
    # A file that already went through fix_rtl_punctuation is wrapped in RLE/PDF,
    # which are not whitespace -- so .strip() cannot reach a space stranded
    # between the text and the closing control. That is exactly the shape of a
    # CACHED subtitle being repaired after the fact, so handle it explicitly.
    #
    # ANCHORED to the two ends, and to RLE/PDF only -- the controls WE insert.
    # An unanchored version of this collapsed blanks around ANY invisible
    # control anywhere in the line, which glued words together across a
    # legitimate space-padded RLM or BOM ("יש 50<RLM> דולר" -> "יש 50<RLM>דולר").
    # Those are zero-width, so the damage is visible in the rendered subtitle.
    _wrap = _RLE + _PDF
    cleaned = re.sub(r'^([' + _wrap + r']*)[ \t]+', r'\1', cleaned)
    cleaned = re.sub(r'[ \t]+([' + _wrap + r']*)$', r'\1', cleaned)
    cleaned = cleaned.strip()
    if body.lstrip().startswith('-') and cleaned and not cleaned.startswith('-'):
        cleaned = '- ' + cleaned
    return cleaned


# --- the same swap, in a script with no reference block behind it -----------
# The Arabic leak has an obvious source (the gender-reference block in the
# prompt). This one does not: the model simply reaches for a letter from
# another alphabet mid-word -- "\u05d0\u05de\u043c" for \u05d0\u05de\u05de, "\u05de\u057a\u0430\u05e6\u05d7\u05ea". It is the same defect the
# user described as "English letters in the middle of a Hebrew word", and the
# same repair applies: the letter stands for a sound, so spell it in Hebrew.
#
# Narrower than the Arabic path in two deliberate ways. Only a run GLUED to a
# Hebrew letter is touched, and a standalone foreign word is NEVER touched --
# there is no reference block here that could have leaked one, so a Cyrillic or
# Greek word standing on its own is far more likely to be a deliberate quote
# than a mistake. And the run is capped at 3 characters: this defect is a
# letter or two, and a longer run is a word, not a slip.
_FOREIGN2HE = {}
for _s, _d in (
        (u'\u0430\u0431\u0432\u0433\u0434\u0435\u0436\u0437\u0438\u0439\u043a\u043b\u043c\u043d\u043e\u043f\u0440\u0441\u0442\u0443\u0444\u0445\u0446\u0447\u0448\u0449\u044b\u044d\u044e\u044f',
         u'\u05d0\u05d1\u05d5\u05d2\u05d3\u05d0\u05d6\u05d6\u05d9\u05d9\u05e7\u05dc\u05de\u05e0\u05d5\u05e4\u05e8\u05e1\u05ea\u05d5\u05e4\u05d7\u05e6\u05e6\u05e9\u05e9\u05d9\u05d0\u05d5\u05d0'),
        (u'\u03b1\u03b2\u03b3\u03b4\u03b5\u03b6\u03b7\u03b8\u03b9\u03ba\u03bb\u03bc\u03bd\u03be\u03bf\u03c0\u03c1\u03c2\u03c3\u03c4\u03c5\u03c6\u03c7\u03c8\u03c9',
         u'\u05d0\u05d1\u05d2\u05d3\u05d0\u05d6\u05d0\u05ea\u05d9\u05e7\u05dc\u05de\u05e0\u05e1\u05d5\u05e4\u05e8\u05e1\u05e1\u05ea\u05d5\u05e4\u05d7\u05e4\u05d5'),
        (u'\u0561\u0562\u0563\u0564\u0565\u0566\u0567\u0568\u0569\u056a\u056b\u056c\u056d\u056e\u056f\u0570\u0571\u0572\u0573\u0574\u0575\u0576\u0577\u0578\u0579\u057a\u057b\u057c\u057d\u057e\u057f\u0580\u0581\u0582\u0583\u0584\u0585\u0586',
         u'\u05d0\u05d1\u05d2\u05d3\u05d0\u05d6\u05d0\u05d0\u05ea\u05d6\u05d9\u05dc\u05d7\u05e6\u05e7\u05d4\u05d6\u05e2\u05e6\u05de\u05d9\u05e0\u05e9\u05d5\u05e6\u05e4\u05d2\u05e8\u05e1\u05d5\u05ea\u05e8\u05e6\u05d5\u05e4\u05e7\u05d5\u05e4')):
    for _a, _b in zip(_s, _d):
        _FOREIGN2HE[_a] = _b
        _FOREIGN2HE[_a.upper()] = _b
# UNBOUNDED, like _ARABIC_RUN_RE, and the length check lives inside the
# substitution. A '{1,3}' regex does not cap the run -- it caps each MATCH,
# so a six-letter Cyrillic word glued to Hebrew came back as two matches and
# was transliterated in pieces, half in each script. Matching the whole run
# and rejecting it by length is what actually enforces the cap.
_FOREIGN_RUN_RE = re.compile(u'[' + u''.join(sorted(_FOREIGN2HE)) + u']+')
_MAX_FOREIGN_RUN = 3


def fold_foreign_in_hebrew_word(text):
    """Spell a foreign-alphabet run that sits INSIDE a Hebrew word back into
    Hebrew letters (Cyrillic, Greek, Armenian).

    Only ever touches a run with a Hebrew letter directly against it on at
    least one side, so a foreign word standing on its own -- a quote, a name,
    a title -- is left exactly as written. Adds Hebrew and removes nothing
    else, so no cue, line or Hebrew character can be lost. Never raises.
    """
    if not text:
        return text
    if _PROTECTED_RE.search(text):
        return _map_unprotected(text, fold_foreign_in_hebrew_word)
    try:
        if not _FOREIGN_RUN_RE.search(text):
            return text
        out = []
        for line in text.split('\n'):
            cr = '\r' if line.endswith('\r') else ''
            body = line[:-1] if cr else line
            if _HAS_HEBREW_RE.search(body):
                def _sub(m, _b=body):
                    run = m.group(0)
                    if len(run) > _MAX_FOREIGN_RUN:
                        return run
                    before = _b[m.start() - 1:m.start()] if m.start() else ''
                    after = _b[m.end():m.end() + 1]
                    if not ((before and _HAS_HEBREW_RE.match(before))
                            or (after and _HAS_HEBREW_RE.match(after))):
                        return run
                    he = [_FOREIGN2HE[c] for c in run]
                    if not (after and _HAS_HEBREW_RE.match(after)):
                        he[-1] = _HE_FINAL.get(he[-1], he[-1])
                    return u''.join(he)
                body = _FOREIGN_RUN_RE.sub(_sub, body)
            out.append(body + cr)
        return '\n'.join(out)
    except Exception:
        return text



# The Latin form of the same slip: one English letter standing in for the
# Hebrew letter that sounds like it -- measured in Kodi runs of the free
# Flash-Lite model: "בהTחשב", "חbרה", "בואi". Latin is far more common in a
# Hebrew subtitle than Cyrillic is (a prefix glued to a brand, "בNetflix",
# "הDJ"), so this one is narrower still: exactly ONE Latin letter, either
# between two Hebrew letters or, lower-case, closing a Hebrew word of at
# least two letters. A run of two Latin letters, or one after a lone prefix
# letter ("בA"), is a word and stays. Letters without one clear Hebrew
# counterpart (a, e, j, x) are left as written; c reads as k ("עצמc").
_LATIN2HE = dict(zip(u'bcdfghiklmnopqrstuvwyz',
                     u'בכדפגהיכלמנופקרסתווויז'))
_STRAY_LATIN_RE = re.compile(
    u'(?<=[\u05d0-\u05ea])([A-Za-z])(?=[\u05d0-\u05ea])'
    u'|(?<=[\u05d0-\u05ea]{2})([a-z])(?![A-Za-z\u05d0-\u05ea])')


def fold_stray_latin_letter(text):
    """Spell a single stray Latin letter inside a Hebrew word in Hebrew.
    Adds Hebrew and removes nothing else. Never raises."""
    if not text:
        return text
    if _PROTECTED_RE.search(text):
        return _map_unprotected(text, fold_stray_latin_letter)
    try:
        if not _STRAY_LATIN_RE.search(text):
            return text

        def _sub(m):
            inside, last = m.group(1), m.group(2)
            he = _LATIN2HE.get((inside or last).lower())
            if not he:
                return m.group(0)
            return _HE_FINAL.get(he, he) if last else he
        out = _STRAY_LATIN_RE.sub(_sub, text)
        return text if out == text else out
    except Exception:
        return text


def may_carry_arabic_leak(path=None, pool_kind=None):
    """Whether these bytes could contain a leak from the AI's gender reference.

    The ONE place that rule lives. strip_leaked_arabic() repairs a defect only
    the gender-reference prompt can produce, so it must not run on anything that
    prompt never touched -- and there turned out to be three such sources, each
    found separately, each after the previous fix had been declared complete:

      * a Ktuvit row mirrored into the community pool -- a HUMAN Hebrew subtitle
        (identified by pool_kind, which is why that argument exists);
      * a Google Translate fallback -- no cast/gender mechanism at all, and it
        writes into cache/translated/ alongside real AI output, so only the
        '.google' sidecar tells them apart;
      * a file of unknown origin sitting next to the video, which is exempted at
        its call site because nothing here can identify it.

    Any of them may legitimately quote Arabic ('הוא אמר "אינשאללה" (إن شاء الله)')
    and would come back with the quote deleted and empty brackets left behind.

    It is a shared function rather than a check at each call site because the
    check WAS at each call site, and the fourth, fifth and sixth repair paths
    silently did not get it. A rule with more than one home has no home.
    """
    try:
        if pool_kind is not None and str(pool_kind).strip().lower() == 'ktuvit':
            return False
        if is_google_translated(path):
            return False
    except Exception:
        # Provenance UNKNOWN. Answer no: stripping deletes text, and this
        # module's stated preference throughout is a visible defect over a
        # silent one. Leaving a stray Arabic word on screen is reportable;
        # deleting a quote out of someone's subtitle is not.
        return False
    return True


def strip_leaked_arabic(text):
    """Remove Arabic that leaked from the gender reference into a Hebrew line.

    HARD GUARANTEE, and the reason this is written the narrow way it is: no cue
    is ever removed, no LINE is ever removed, and no line ever loses its Hebrew.
    All this does is rewrite Arabic characters on a line that ALSO contains
    Hebrew -- i.e. a contaminated translation, which is the shape that was
    actually reported ("Arabic word completions / half words" inside otherwise
    correct lines). The Hebrew on that line always survives, so a cue can never
    turn into silence while people are talking.

    Two different rewrites, because there are two different leaks. A run GLUED
    to a Hebrew letter is the model spelling a Hebrew word in the wrong script,
    and is transliterated back into Hebrew letters (see _transliterate_glued_run
    -- that path only adds Hebrew, so it cannot touch the guarantee above). A
    run standing on its own is a pasted Arabic word and is deleted, as before.

    A line that is ENTIRELY Arabic is deliberately left alone. Structurally it
    is indistinguishable from a leaked reference line that happened to land on
    its own wrapped line, and there is no way to tell the two apart from the
    text: one is an on-screen sign or a deliberately untranslated line, the
    other is a leak. Dropping it would fix the leak at the cost of silently
    deleting real content -- and a stray visible line is a defect a user can see
    and report, whereas missing dialogue is one they cannot. An earlier revision
    dropped such lines; review found it destroying a genuine Arabic sign that
    shared a cue with a contaminated line, and it was reverted for this reason.
    That residual is accepted, and it is the ONLY case this does not cover.

    Markup (including multiline attributes) and paired double-quoted literals
    are preserved byte-for-byte. This can deliberately leave a real defect
    inside a quotation rather than risk deleting intended content.
    Index and timecode lines have no Hebrew, so they can never match. Never
    raises into the caller.
    """
    if not text:
        return text
    if _PROTECTED_RE.search(text):
        return _map_unprotected(text, strip_leaked_arabic)
    try:
        if not _HAS_ARABIC_RE.search(text):
            return text            # overwhelmingly the common case: no-op
        out = []
        for line in text.split('\n'):
            cr = '\r' if line.endswith('\r') else ''
            body = line[:-1] if cr else line
            if (_HAS_ARABIC_RE.search(body)
                    and _HAS_HEBREW_RE.search(body)):
                cleaned = _clean_arabic_from_line(body)
                # Only accept the rewrite if the Hebrew came through it. Any
                # other outcome means the cleanup misread the line, and the
                # original is always the safer answer.
                if cleaned and _HAS_HEBREW_RE.search(cleaned):
                    body = cleaned
            out.append(body + cr)
        return '\n'.join(out)
    except Exception:
        return text
# Zero-width and bidi controls. They are invisible, they survive every existing
# repair, and a player that lacks a glyph draws them as a box.
#
# Built from _INVISIBLE_BIDI rather than from a second hand-rolled range. That
# list is this module's existing answer to the same question, _fix_one_text_line
# already strips exactly it, and two character lists that are meant to agree
# will eventually stop agreeing. The zero-width three are the only additions:
# they are not direction controls, so _INVISIBLE_BIDI never needed them, but
# they are invisible and a font without them draws a box just the same.
_ZERO_WIDTH = '\u200B\u200C\u200D'   # ZWSP, ZWNJ, ZWJ
# The Hebrew presentation-form block. NFC handles most of it; the compatibility
# forms in it need NFKC, which is why this range is picked out by itself.
_HEB_PRESENTATION_RE = re.compile('[\uFB1D-\uFB4F]')
_INVISIBLE_RE = re.compile('[' + re.escape(_INVISIBLE_BIDI + _ZERO_WIDTH) + ']')


def normalize_glyphs(text):
    """Fold the text to characters the build's fonts can actually draw.

    Reported as "a square instead of one letter, mid-word" -- a box is the
    player saying it has no glyph for that codepoint. The usual culprit is a
    Hebrew PRESENTATION FORM: U+FB1D-U+FB4F holds precomposed letter+niqqud and
    ligature variants (U+FB1D is yod-with-hiriq, which looks like a plain yod
    and is not one), and the fonts shipped here do not cover that block. NFC
    normalisation decomposes those back to ordinary letters, so the same word
    renders with the ordinary glyph the font does have.

    NFC alone is not enough for all of them. It leaves the WIDE letters
    (U+FB21-FB28), ALTERNATIVE AYIN (U+FB20), ALTERNATIVE PLUS (U+FB29) and
    LIGATURE ALEF LAMED (U+FB4F) exactly as they are -- those are compatibility
    forms, not canonical ones, so only NFKC folds them. NFKC is applied to that
    block ALONE and nothing else: run over the whole text it would also rewrite
    unrelated characters a subtitle may legitimately contain (the fi ligature,
    fullwidth forms, superscripts, '½' into '1/2'), which is a different and
    unwanted change. Per-character on U+FB1D-FB4F it can only ever turn a
    Hebrew presentation form into the ordinary Hebrew letters it stands for.

    Also drops zero-width and bidi-control characters. They are invisible by
    definition, so removing them cannot change what a correct line looks like,
    and they are the other thing that shows up as a box.

    Applies to ANY subtitle, whatever produced it: it only ever replaces a
    character with the canonical spelling of that same character. Never raises.

    ORDER MATTERS. The invisible set includes RLE/PDF, which is deliberate --
    _fix_one_text_line strips them too, and that is what makes the rtl_base wrap
    idempotent. But it means this must run BEFORE fix_rtl_punctuation, never
    after: run it after and it removes the very wrap that call just added,
    silently undoing the RTL fix for every line. Both callers order it that way
    and test_wiring pins it.
    """
    if not text:
        return text
    try:
        import unicodedata
        out = unicodedata.normalize('NFC', text)
        if _HEB_PRESENTATION_RE.search(out):
            out = _HEB_PRESENTATION_RE.sub(
                lambda m: unicodedata.normalize('NFKC', m.group(0)), out)
        out = _INVISIBLE_RE.sub('', out)
        return out
    except Exception:
        return text
