# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Hebrew person-form patterns (feminine / masculine verbs, pronouns, the "at" object) used to read a Hebrew
# reference line.

import re


# ---------------- gender VERIFICATION (after the translation) ---------------
# The reference is a hint in a prompt, and a prompt is an instruction rather
# than a guarantee. Measured on a full film with a perfectly aligned Arabic
# oracle: 51 of 52 scorable lines came back with the right addressee gender,
# and the one that did not had an unambiguous "\u0623\u0646\u062a\u0650" (feminine) sitting in
# its own prompt. So the last few points are compliance, not knowledge, and
# the way to close them is to CHECK the output rather than ask more loudly.
#
# ONE DIRECTION ONLY, deliberately. "\u05d0\u05ea\u05d4" can only be the masculine
# second-person pronoun, so finding it where the reference says feminine is
# proof of an error. The feminine "\u05d0\u05ea" is also Hebrew's definite-object
# marker -- "\u05e8\u05d0\u05d9\u05ea\u05d9 \u05d0\u05ea \u05d3\u05df" is not addressing a woman -- so the mirror check
# would flag correct lines, and there is no reliable way to tell the two apart
# without parsing. That asymmetry costs nothing in practice: every one of the
# 24 gender errors in the file a viewer reported was masculine-where-feminine,
# which is exactly what an unhinted translation defaulting to masculine looks
# like. The direction that can be checked safely is the direction that fails.
# EVERY pattern above the blank line needs HARAKAT, and real subtitles do not
# carry it. Measured on a full human Arabic episode: 7,415 Arabic letters, TWO
# diacritics, zero vocalised \u0623\u0646\u062a\u064e/\u0623\u0646\u062a\u0650 -- so the whole Arabic branch read
# F=0 M=0 None=370. Arabic anchors the largest share of real jobs, which made
# it the emptiest reader we had.
#
# The 2fs PRESENT suffix is different: \u062a...\u064a\u0646 is consonantal and is always
# written. Two noun classes wear the same ending and must not be read as an
# addressee:
#   * the \u062a\u0641\u0639\u064a\u0644 verbal noun -- \u062a\u0645\u0631\u064a\u0646, \u062a\u0639\u064a\u064a\u0646, \u062a\u062d\u0633\u064a\u0646, \u062a\u062f\u062e\u064a\u0646 -- which is
#     FIVE letters and structurally identical to a real 5-letter verb like
#     \u062a\u0628\u062f\u064a\u0646. Nothing short of a lexicon separates them, so the floor is
#     six letters and \u062a\u0628\u062f\u064a\u0646 / \u062a\u0638\u0646\u064a\u0646 / \u062a\u0642\u0636\u064a\u0646 are given up on purpose.
#   * the feminine sound dual -- \u062a\u0641\u0635\u064a\u0644\u0629 -> \u062a\u0641\u0635\u064a\u0644\u062a\u064a\u0646 -- so the letter before
#     the suffix may not be \u062a.
# Recall loses a little, precision wins, and that is the right way round: this
# reader feeds a repair pass, and a false F spends a request rewriting a line
# that was already correct. Measured on the same episode: 19 of 370 cues
# (5.1%), eleven distinct forms, every one a genuine 2fs verb, and twelve
# noun controls (\u062a\u0645\u0631\u064a\u0646, \u0633\u0646\u062a\u064a\u0646, \u0645\u0631\u062a\u064a\u0646, ...) all correctly refused.
# Arabic glues object pronouns straight onto the verb -- \u062a\u062e\u064a\u0641\u064a\u0646\u0646\u064a, \u062a\u0631\u064a\u062f\u064a\u0646\u0647,
# \u062a\u0639\u0631\u0641\u064a\u0646\u0647\u0627 -- and a suffix-must-end-the-word rule misses all of them. The
# clitic set is closed, so allowing it costs no precision against the noun
# classes above: those are blocked by the six-letter floor whatever follows.
# It DOES open one new class, a 6+ letter broken plural in \u064a\u0646 carrying a
# possessive (\u062a\u0645\u0627\u0631\u064a\u0646\u0647\u0627, \u062a\u0646\u0627\u0646\u064a\u0646\u0647\u0627). The two verified noun stems are
# excluded below; the general morphological ambiguity remains a limitation.
_AR_2FS_PRESENT = (u'(?<![\u0621-\u064a])\u062a[\u0621-\u064a]{2,5}[\u0621-\u0629\u062b-\u064a]\u064a\u0646'
                   u'(?:\u0646\u064a|\u0647\u0645\u0627|\u0647\u0627|\u0647\u0645|\u0647\u0646|\u0643\u0645|\u0647|\u0643)?(?![\u0621-\u064a])')

AR_FEM = tuple(re.compile(p) for p in (
    u'\u0623\u0646\u062a\u0650', u'\u0644\u0643\u0650', u'\u0628\u0643\u0650', u'\u0639\u0644\u064a\u0643\u0650', u'\u0645\u0639\u0643\u0650', u'\u0625\u0644\u064a\u0643\u0650', u'\u0645\u0646\u0643\u0650',
    u'\u0643\u0650\\s', u'\u0643\u0650$', u'\u062a\u0650\\s', u'\u062a\u0650$', u'\u064a\u0627 \u0633\u064a\u062f\u062a\u064a',
    _AR_2FS_PRESENT,
))
AR_MASC = tuple(re.compile(p) for p in (
    u'\u0623\u0646\u062a\u064e', u'\u0644\u0643\u064e', u'\u0628\u0643\u064e', u'\u0639\u0644\u064a\u0643\u064e', u'\u0645\u0639\u0643\u064e', u'\u0625\u0644\u064a\u0643\u064e', u'\u0645\u0646\u0643\u064e',
    u'\u0643\u064e\\s', u'\u0643\u064e$', u'\u062a\u064e\\s', u'\u062a\u064e$',
))
# Narrow lexical exceptions to the productive 2fs shape. These are nouns
# (exercises / dragons), including possessives, not feminine-address verbs.
# Keep other evidence in the same cue; this is not a general Arabic parser.
AR_NOUN_EXCEPTIONS = re.compile(
    r'(?<![\u0621-\u064a])(?:[وفلب]|ال|وال|بال|فال|لل)?(?:تمارين|تنانين)'
    r'(?:ها|هما|هم|هن|ه|كم|كن|ك|نا|ي)?(?![\u0621-\u064a])')
# Hebrew reference: read the pronoun straight off it.
# The SAME proclitic alternation _HE_MASC carries below. Without it we looked
# for \u05d5\u05d0\u05ea\u05d4/\u05e9\u05d0\u05ea\u05d4 but never for \u05d5\u05d0\u05ea/\u05e9\u05d0\u05ea -- an asymmetry that made the
# reader better at finding a MALE addressee than a female one, in a check whose
# whole purpose is catching masculine-where-feminine.
_HE_PRO = u'(?:\u05d5|\u05e9|\u05db\u05e9|\u05d5\u05e9|\u05d5\u05db\u05e9)?'
_HE_REF_FEM = re.compile(
    u'(?<![\u05d0-\u05ea])' + _HE_PRO + u'\u05d0\u05ea(?![\u05d0-\u05ea])(?!\\s*\u05d4)')

# A letter shape is not morphology: תפקידי, תורכי and תלמידי are
# ordinary nouns/adjectives, not commands. Use a small reviewed vocabulary
# for repair candidates; unknown forms abstain. These are hints, not proof.
_HE_FEM_VERB = re.compile(
    u'(?<![א-ת])' + _HE_PRO +
    u'(?:תגידי|תסמכי|תשמרי|תעשי|תבואי|תלכי|תחכי|תקחי|תתני|תדברי|תראי|תשמעי)(?![א-ת])')
_HE_FEM_IMPER = re.compile(
    u'(?<![א-ת])' + _HE_PRO +
    u'(?:בואי|לכי|חכי|קחי|תני|סלחי|עזרי)(?![א-ת])')
# Bare את is also an object marker, even before a name (ראיתי את דני).
# Admit only a clause-leading pronoun or an explicit subordinating clitic.
_HE_FEM_CLAUSE = re.compile(
    r'(?:^|[.!?\n,:;—-])\s*(?:ו)?את(?![א-ת])|'
    u'(?<![א-ת])(?:ש|כש|וש|וכש)את(?![א-ת])')
# A narrow verbal continuation avoids treating הולכת בלי/איתי as a
# definite noun. Bare הולכת and הולכת הרגל remain ambiguous and abstain.
_HE_AT_OBJ = re.compile(
    r'\s+(?:זה|זאת|זו|אלה|אלו|כל(?!\s+כך)|מה|מי|עצמ|אות|כך|של|ה(?!ולכת\s+(?:בלי|איתי)(?![א-ת]))[א-ת])')
# Clause position alone is insufficient: "את דני ראיתי" fronts an object.
# Require a reviewed predicate too; unseen predicates remain prompt-only hints.
_HE_AT_PREDICATE = re.compile(
    r'\s+(?:(?:כל\s+כך|מאוד|ממש|ודאי|בטעות|כבר|עדיין|לא)\s+){0,3}'
    r'(?:יפה|מוכנה|מבהילה|מפחידה|מעמידה|יודעת|חוזרת|צריכה|יכולה|רוצה|'
    r'חייבת|בטוחה|עייפה|צודקת|טועה|מבינה|מקשיבה|מדברת|הולכת|נראית)(?![א-ת])')


def he_addresses_female(text):
    """Conservative candidate evidence; uncertain Hebrew does not trigger repair."""
    try:
        if _HE_FEM_VERB.search(text) or _HE_FEM_IMPER.search(text):
            return True
        return any(not _HE_AT_OBJ.match(text[m.end():])
                   and _HE_AT_PREDICATE.match(text[m.end():])
                   for m in _HE_FEM_CLAUSE.finditer(text))
    except Exception:
        return False
# The proclitics Hebrew glues straight onto a pronoun: ו (and), ש (that),
# כש (when) and their combinations. Without them "ואתה", "שאתה" and
# "כשאתה" -- ordinary, high-frequency Hebrew -- read as no pronoun at all,
# which both hides real errors from the check and lets a "repair" that is
# still masculine pass as fixed. The set is deliberately just these: allowing
# ANY preceding letter would match the אתה inside ראתה ("she saw").
HE_MASC = re.compile(
    u'(?<![\u05d0-\u05ea])(?:\u05d5|\u05e9|\u05db\u05e9|\u05d5\u05e9|\u05d5\u05db\u05e9)?\u05d0\u05ea\u05d4(?![\u05d0-\u05ea])')
