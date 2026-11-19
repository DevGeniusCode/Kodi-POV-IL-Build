# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Arabic person-form patterns: second-person feminine / masculine, speaker adjectives, imperatives, vocatives,
# and the helpers that read them.

import re


# The SPEAKER's own gender, read off a non-Hebrew reference. Only first-person
# forms that cannot be anything else: Arabic "أنا"/"لست" + an adjective
# (feminine ends in ة; masculine from a short reviewed list -- an Arabic word
# without ة is not by itself masculine), and Russian/Ukrainian "я" + a
# past-tense verb or short adjective (-ла / -л, уверена / уверен). Anything
# else is None, and None never triggers a rewrite.
_AR_ME = u'(?<![ء-ي])(?:أنا|لست)\\s+'
_AR_SPEAKER_ADJ = (u'آسف', u'متأكد', u'سعيد', u'مستعد', u'جاهز', u'خائف', u'متعب',
                   u'مشغول', u'متأسف', u'غاضب', u'حزين', u'جائع', u'مسرور', u'موافق',
                   u'قلق', u'فخور', u'مخطئ', u'محق', u'متزوج', u'وحيد', u'مريض',
                   u'نادم')
# Feminine from the same reviewed list (+ة). "Any word ending in ة" also
# matched gender-neutral phrases -- "أنا بحاجة" (I need), "أنا عادة" (I usually).
AR_SPEAKER_F = re.compile(
    _AR_ME + u'(?:' + u'|'.join(w + u'\u0629' for w in _AR_SPEAKER_ADJ)
    + u')(?![\u0621-\u064a])')
AR_SPEAKER_M = re.compile(
    _AR_ME + u'(?:آسف|متأكد|سعيد|'
    u'مستعد|جاهز|خائف|متعب|'
    u'مشغول|متأسف|غاضب|حزين|'
    u'جائع|مسرور|موافق|قلق|'
    u'فخور|مخطئ|محق|متزوج|'
    u'وحيد|مريض|نادم)(?![ء-ي])')


# ---------------- more of what an Arabic line says -------------------------
# The two readers above leave most of an Arabic subtitle's gender unread.
# Measured against the human Hebrew of five titles (that Hebrew read by
# he_person_gender as the truth): they found the addressee on 23% of the lines
# where the Hebrew marks one and the speaker on 2%. Everything below is
# consonantal -- no harakat -- and each form was kept only when its own hits
# agreed with the human Hebrew: together 47% / 9%, at 98.7% / 96.1%
# precision. On real no-Hebrew AI translations (two episodes, two runs) the
# check found 19 of 59 gender errors instead of 1, with no new false flag.
_AR_L = u'\u0621-\u064a'
_AR_START = u'(?<![' + _AR_L + u'])'
_AR_END = u'(?![' + _AR_L + u'])'
_AR_CLITIC = u'(?:ني|هما|ها|هم|هن|نا|كم|ه|ك)?'
_AR_CLAUSE = u'(?:^|[.!?\u061f\u060c,:\\-\u2026"]\\s*)'


def _ar_words(words):
    return u'(?:' + u'|'.join(words) + u')'


# 2fs present behind a question/conjunction/future prefix: أتريدين, ستفعلين.
# The six-letter floor on the stem is _AR_2FS_PRESENT's, for the same nouns.
_AR_2FS_PREFIXED = re.compile(
    _AR_START + u'(?:أ|و|ف|س|وس|فس|أف|أو|ل)ت[' + _AR_L + u']{2,5}'
    u'[\u0621-\u0629\u062b-\u064a]ين' + _AR_CLITIC + _AR_END)
# Five-letter 2fs verbs the floor gives up on, from a reviewed list.
_AR_2FS_SHORT = re.compile(
    _AR_START + u'(?:أ|و|ف|س|وس|فس|ل)?' + _ar_words((
        u'تبدين', u'تحبين', u'تظنين', u'ترين', u'تقولين', u'تريدين', u'تعرفين',
        u'تكونين', u'تفعلين', u'تخافين', u'تشعرين', u'تعملين', u'تذهبين', u'تعنين',
        u'تحتاجين', u'تقصدين', u'تمزحين', u'تعتقدين', u'تعيشين', u'تبقين',
        u'تجيئين', u'تأتين', u'تفهمين', u'تسمعين')) + _AR_CLITIC + _AR_END)
# 2fs subjunctive/jussive (the nuun drops) after أن/لن/لم/لا. Only sound and
# hollow verbs: a defective root keeps its ي in the 3fs ("أن تبقي", she
# keeps), so تبقي/تبكي/تأتي stay out.
_AR_2FS_SUBJ = re.compile(
    _AR_START + u'(?:أن|ان|لن|لم|لا|كي|حتى|ألا)\\s+' + _ar_words((
        u'تكوني', u'تقلقي', u'تخافي', u'تفعلي', u'تقولي', u'تذهبي', u'تتكلمي',
        u'تسألي', u'تخبري', u'تلمسي', u'تغضبي', u'تتحدثي', u'تعودي', u'ترحلي',
        u'تقتلي', u'تنظري', u'تعرفي', u'تفكري', u'تضحكي', u'تصدقي', u'تتركي'))
    + _AR_CLITIC + _AR_END)
# Imperatives. Feminine ones end in ي, which no masculine imperative does.
# Left out: أعطي / استرخي, which are also "I give" / "I relax"; and the ones
# that also read as a noun + "my" (توقفي, my stopping) count only where they
# open a clause.
_AR_IMP_F = re.compile(
    _AR_START + u'(?:و|ف)?' + _ar_words((
        u'تعالي', u'انظري', u'أنظري', u'إنظري', u'اسمعي', u'إسمعي', u'اذهبي',
        u'إذهبي', u'اجلسي', u'إجلسي', u'انتظري', u'إنتظري', u'ابقي',
        u'إبقي', u'عودي', u'ارجعي', u'إرجعي', u'قولي', u'خذي', u'دعي', u'اخبري',
        u'أخبري', u'اتصلي', u'اهدئي', u'إهدئي', u'اصمتي', u'إصمتي', u'اسكتي',
        u'انهضي', u'قفي', u'افعلي', u'انسي', u'اركضي', u'ابتعدي', u'ادخلي',
        u'أدخلي', u'اخرجي', u'أخرجي', u'اقرئي', u'اقرأي', u'استمعي',
        u'ساعدي', u'صدقي', u'التقطي', u'اهربي', u'احضري', u'اجلبي',
        u'أجلبي', u'ابتسمي', u'استيقظي',
        u'اتبعي', u'اصعدي', u'انزلي', u'انتبهي', u'تمهلي', u'أسرعي',
        u'اسرعي', u'أريني', u'اريني')) + _AR_CLITIC + _AR_END
    + u'|' + _AR_CLAUSE + u'(?:و|ف)?' + _ar_words((
        u'توقفي', u'تذكري', u'تحركي', u'تنفسي', u'فكري')) + _AR_CLITIC + _AR_END)
# Masculine: forms that cannot be anything else anywhere in a line, then the
# alif-initial ones only where they open a clause -- mid-line, "اسمع" can be
# "أسمع" (I hear) with the hamza dropped. A kasra after the word marks the
# short feminine (تعالِ), but subtitlers also put it on a man's "خذِ", so a
# kasra is read as neither. Left out: forms that are also a past tense or an
# "I ..." verb (اتصل "he called", أخبرني "he told me", أخرج "I go out"); the
# commonest of those count only as a whole utterance opening a subtitle line
# or a dash turn ("توقف!", "أحب إصبعك - توقف -"); "قلت له: توقف." is
# reported speech.
_AR_MARKS = u'[\\s\u200e\u200f\u202a-\u202e]*'
_AR_LINE_START = u'(?:^|\n|\\s-)' + _AR_MARKS + u'(?:-' + _AR_MARKS + u')?'
_AR_IMP_M = re.compile(
    _AR_START + u'(?:و|ف)?' + _ar_words((u'تعال', u'قل', u'خذ', u'دعني', u'دعنا'))
    + u'(?![\u0621-\u064a\u0650])'
    u'|' + _AR_CLAUSE + u'(?:و|ف)?' + _ar_words((
        u'انظر', u'إنظر', u'أنظر', u'اسمع', u'إسمع', u'اجلس', u'إجلس', u'اذهب',
        u'إذهب', u'ارجع', u'إرجع', u'اهدأ', u'إهدأ', u'اصمت',
        u'إصمت', u'اسكت', u'ابق', u'إبق', u'استرخ', u'اخرج',
        u'ادخل', u'صدقني',
        u'أعطني', u'اعطني', u'اتركني', u'أرني', u'ارني', u'خذني', u'اسمعني',
        u'اركض', u'اهرب')) + u'(?![\u0621-\u064a\u0650])'
    u'|' + _AR_LINE_START + u'(?:و|ف)?' + _ar_words((
        u'توقف', u'انتظر', u'إنتظر', u'انتبه', u'إنتبه', u'تمهل', u'تحرك',
        u'استيقظ', u'أسرع', u'اسرع')) + u'(?![\u0621-\u064a\u0650])'
    + _AR_MARKS + u'[.!\u060c,\\-\u2026]')
# "أنت" + a second-person imperfect: the verb's own ending says which.
_AR_ANTA = (_AR_START + u'(?:أ|هل\\s+|و|ف)?(?:أنت|انت)[\u064b-\u0652]*\\s+'
            u'(?:(?:لا|لم|لن|لست|لازلت|فعلاً|فعلا|حقاً|حقا|فقط|دائماً|دائما)\\s+)?')
_AR_ANTA_VERB = re.compile(_AR_ANTA + u'(ت[' + _AR_L + u']{2,9})' + _AR_END)
_AR_CLITICS = (u'هما', u'ني', u'ها', u'هم', u'هن', u'نا', u'كم', u'ه', u'ك')
# t- words after أنت that are not a second-person verb: an adjective or noun
# (ة, tanween), a past tense (ت, also before an object: تركتني), and a few
# frequent function words. ى stays: ترى / تنسى are second-person verbs.
_AR_NOT_IMPERFECT = (u'ة', u'ت', u'ا', u'\u064b', u'\u064c', u'\u064d')
_AR_T_NOT_VERBS = frozenset((u'تلك', u'تحت', u'تماما', u'تقريبا', u'تحديدا',
                             u'تماماً', u'تقريباً', u'تحديداً', u'تاريخ', u'تجربة'))
# ... or + an adjective from the speaker list and a few more (ة = feminine).
_AR_ADDR_ADJ = _ar_words(_AR_SPEAKER_ADJ + (
    u'جميل', u'وسيم', u'غبي', u'ذكي', u'مجنون', u'رائع', u'مجبر', u'مضحك',
    u'لطيف', u'محظوظ', u'متأخر', u'مرتاح', u'مستيقظ', u'متوتر'))
_AR_ANTA_ADJ_F = re.compile(_AR_ANTA + _AR_ADDR_ADJ + u'ة' + _AR_END)
_AR_ANTA_ADJ_M = re.compile(_AR_ANTA + _AR_ADDR_ADJ + u'(?:اً|ًا|ا)?' + _AR_END)
# "تبدو" + a masculine accusative adjective (the feminine says تبدين). تبدو
# is also "she looks", so only an adjective with written tanween counts
# ("تبدو جميلاً"); an adverb wears it too ("تبدو تماماً مثل أمها").
_AR_YOU_LOOK_M = re.compile(_AR_START + u'تبدو\\s+' + _AR_ADDR_ADJ + u'(?:اً|ًا)' + _AR_END)
# Vocatives. A bare endearment counts only after a comma ("أمابيلا، عزيزتي
# هل ...") or as a clause of its own ("عزيزي، اسمع"): "حبيبتي تركتني" and
# "تركتني حبيبتي." are "my girlfriend left me", somebody else. "ابنتي" and
# "صديقي" read as "my daughter / my friend" too often; they count after يا.
_AR_AFTER_COMMA = u'[\u060c,]\\s*'
_AR_VSEP_END = u'\\s*(?:[.!?\u061f\u060c,:\\-\u2026"]|$)'
_AR_VOC_F_BARE = _ar_words((u'عزيزتي', u'حبيبتي', u'صغيرتي', u'سيدتي', u'آنستي'))
_AR_VOC_M_BARE = _ar_words((u'عزيزي', u'حبيبي', u'سيدي'))
_AR_VOC_F = re.compile(
    _AR_START + u'يا\\s+' + _ar_words((
        u'فتاة', u'امرأة', u'سيدة', u'آنسة', u'أمي', u'امي', u'صديقتي', u'عزيزتي',
        u'حبيبتي', u'ابنتي', u'سيدتي', u'أختي', u'اختي', u'جميلة')) + _AR_END
    + u'|' + _AR_AFTER_COMMA + _AR_VOC_F_BARE + _AR_END
    + u'|' + _AR_CLAUSE + _AR_VOC_F_BARE + _AR_VSEP_END)
_AR_VOC_M = re.compile(
    _AR_START + u'يا\\s+' + _ar_words((
        u'رجل', u'صاح', u'سيدي', u'أبي', u'ابي', u'بني', u'فتى', u'صديقي', u'عزيزي',
        u'حبيبي', u'أخي', u'اخي')) + _AR_END
    + u'|' + _AR_AFTER_COMMA + _AR_VOC_M_BARE + _AR_END
    + u'|' + _AR_CLAUSE + _AR_VOC_M_BARE + _AR_VSEP_END)
# A question: هل + a t-imperfect, or the a- question prefix on a verb whose
# own first person is not a+t ("أتعرف" asks, "أتفق" is "I agree"; أتفهم is
# also "I understand", أتعرف على "I get to know"). The same
# t- form is also "she ..."; measured, it reads as "you" in questions. "هل
# تمزح معي" is an idiom subtitlers write in the masculine to anyone.
_AR_QUESTION = re.compile(
    u'(?:' + _AR_START + u'هل\\s+(?:لا\\s+)?(?!تمزح)(ت[' + _AR_L + u']{2,7})|'
    + _AR_START + u'[وف]?أ(' + u'|'.join((
        u'تعرف(?!\\s+عل)', u'تعتقد', u'تريد', u'تحب', u'تظن', u'تسمع', u'ترى',
        u'تقصد', u'تعني', u'تشعر', u'تود', u'ترغب', u'تقول', u'تستطيع', u'تمانع',
        u'تعمل', u'تعيش', u'تبحث', u'تنتظر', u'تصدق', u'تثق', u'تحاول', u'تملك'))
    + u')' + _AR_CLITIC + u')(?![\u0621-\u064a\u064b-\u0652])')


def _ar_strip_clitic(word):
    for c in _AR_CLITICS:
        if len(word) > len(c) + 3 and word.endswith(c):
            return word[:-len(c)]
    return word


def _ar_verb_gender(word):
    """'F'/'M'/None for a bare second-person imperfect (clitic stripped)."""
    word = _ar_strip_clitic(word)
    if word.endswith(u'ين'):
        return 'F'
    # ي ends a defective verb in both genders (تمشي); ون/ان/وا are plural/dual.
    if word.endswith((u'ي', u'ون', u'ان', u'وا')):
        return None
    return 'M'


def ar_more_addressee(text, signal_text):
    """(feminine, masculine) addressee evidence from the forms above."""
    f = bool(_AR_2FS_PREFIXED.search(signal_text) or _AR_2FS_SHORT.search(text)
             or _AR_2FS_SUBJ.search(text) or _AR_IMP_F.search(text)
             or _AR_VOC_F.search(text))
    m = bool(_AR_IMP_M.search(text) or _AR_YOU_LOOK_M.search(text)
             or _AR_VOC_M.search(text))
    for mt in _AR_ANTA_VERB.finditer(text):
        word = mt.group(1)
        bare = _ar_strip_clitic(word)
        if word in _AR_T_NOT_VERBS or bare.endswith(_AR_NOT_IMPERFECT):
            continue          # تعيسة, تحت, تماماً ... or a past tense (تركتني)
        g = _ar_verb_gender(word)
        f, m = f or g == 'F', m or g == 'M'
    if _AR_ANTA_ADJ_F.search(text):
        f = True
    elif _AR_ANTA_ADJ_M.search(text):
        m = True
    for mt in _AR_QUESTION.finditer(text):
        if mt.group(2):
            m = True
            continue
        word = _ar_strip_clitic(mt.group(1))
        if word.endswith((u'ت', u'تم', u'تن', u'ن')) and not word.endswith(u'ين'):
            continue          # a past tense (تحققت) or a plural (تعرفن)
        g = _ar_verb_gender(word)
        f, m = f or g == 'F', m or g == 'M'
    return f, m


# The speaker: "أنا" plus the clitic forms (فأنا, إنني, لكني ...) and a longer
# reviewed adjective list; and "آسفة"/"آسف" opening a clause -- an apology is
# the speaker's own.
_AR_ME_MORE = (_AR_START + u'(?:[وف]?(?:أنا|انا)|[وف]?لست|إنني|انني|إني|أنني|أني|اني|'
               u'لأنني|لأني|لكنني|لكني|فإنني)\\s+(?:(?:جداً|جدا|حقاً|حقا|فعلاً|فعلا|لست)\\s+)?')
_AR_ME_ADJ = _ar_words(_AR_SPEAKER_ADJ + (
    u'ذاهب', u'قادم', u'واثق', u'متوتر', u'مرتبك', u'مستيقظ', u'عائد', u'متحمس',
    u'مستعجل', u'ممتن', u'محرج', u'جاد', u'مجنون', u'غبي', u'مرتاح', u'مسؤول',
    u'متردد', u'مهتم', u'مصدوم', u'متفاجئ', u'متأخر'))
AR_ME_F = re.compile(_AR_ME_MORE + _AR_ME_ADJ + u'ة' + _AR_END
                      + u'|' + _AR_CLAUSE + u'(?:آسفة|متأسفة)' + _AR_END)
AR_ME_M = re.compile(_AR_ME_MORE + _AR_ME_ADJ + u'(?:اً|ًا|ا)?' + _AR_END
                      + u'|' + _AR_CLAUSE + u'(?:آسف|متأسف)' + _AR_END)
