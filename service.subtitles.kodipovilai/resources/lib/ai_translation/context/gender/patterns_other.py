# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Addressee / speaker markers of the other chain languages (Slavic, Hindi, ...) and the pattern compiler.

import re


# The rest of the chain. Arabic and Hebrew are read above; these are the
# languages whose ADDRESSEE marking can be read without parsing, because the
# marker is a VERB ending tied to the second person -- there is no other thing
# in the language it could be.
#
# Deliberately NOT here, and this is the whole design: es, it, pt, fr, ro and
# el mark gender on ADJECTIVES, and an adjective ending is not distinguishable
# from a feminine noun's ending without knowing which word is which ("eres una
# estrella" ends in -a and says nothing about who is being addressed). nl marks
# only referent gender, never the addressee. ur is written in Arabic script but
# marks gender through Indic verb morphology, not the Arabic diacritics above,
# so the patterns there do not transfer. For all of those this returns None and
# the verification pass simply does not fire -- which is exactly today's
# behaviour, and far better than rewriting a line that was already right.
#
# Every pattern below is validated in tools/test_gender_verification.py against
# both genders and against a first/third-person sentence that must NOT match.
def _rx(*pats):
    return tuple(re.compile(p, re.I | re.U) for p in pats)


ADDRESSEE_MARKERS = {
    # Slavic past tense: the second-person pronoun plus a gendered participle.
    'ru': (_rx(r'(?<![\u0430-\u044f\u0451])\u0442\u044b\b[^.!?]{0,40}?\b\w+\u043b\u0430\b'),
           _rx(r'(?<![\u0430-\u044f\u0451])\u0442\u044b\b[^.!?]{0,40}?\b\w+\u043b\b')),
    'uk': (_rx(r'(?<![\u0430-\u044f\u0456\u0457])\u0442\u0438\b[^.!?]{0,40}?\b\w+\u043b\u0430\b'),
           _rx(r'(?<![\u0430-\u044f\u0456\u0457])\u0442\u0438\b[^.!?]{0,40}?\b\w+(\u0432|\u0438\u0439)\b')),
    'bg': (_rx(r'(?<![\u0430-\u044f])\u0442\u0438\b[^.!?]{0,40}?\b\w+(\u043b\u0430|\u043d\u0430)\b'),
           _rx(r'(?<![\u0430-\u044f])\u0442\u0438\b[^.!?]{0,40}?\b\w+(\u0435\u043d|\u043b)\b')),
    # Polish encodes person AND gender in the ending itself.
    'pl': (_rx(r'\w+\u0142a\u015b\b'), _rx(r'\w+\u0142e\u015b\b')),
    # Czech / Slovak / Serbian / Croatian: participle + the 2sg auxiliary.
    # sr/hr take -ao/-io rather than a bare -o: ordinary words end in -o
    # ('tamo'), so a bare -o matched alongside the feminine -la and every
    # line came back ambiguous.
    'cs': (_rx(r'\w+la\s+jsi\b'), _rx(r'\w+l\s+jsi\b')),
    'sk': (_rx(r'\w+la\s+si\b'), _rx(r'\w+l\s+si\b')),
    'sr': (_rx(r'\bti\s+si\b[^.!?]{0,30}?\b\w+la\b'),
           _rx(r'\bti\s+si\b[^.!?]{0,30}?\b\w+(ao|io)\b')),
    'hr': (_rx(r'\bti\s+si\b[^.!?]{0,30}?\b\w+la\b'),
           _rx(r'\bti\s+si\b[^.!?]{0,30}?\b\w+(ao|io)\b')),
    # Hindi: the second-person copula with a feminine/masculine participle.
    'hi': (_rx(r'(\u0924\u0941\u092e|\u0906\u092a)[^\u0964?!]{0,30}?\u0940\s+(\u0939\u094b|\u0939\u0948\u0902)'),
           _rx(r'(\u0924\u0941\u092e|\u0906\u092a)[^\u0964?!]{0,30}?\u0947\s+(\u0939\u094b|\u0939\u0948\u0902)')),
}


_SLAVIC_ME = u'(?<![а-яёії])я\\s+(?:не\\s+)?'
SLAVIC_SPEAKER_F = re.compile(
    _SLAVIC_ME + u'(?:[а-яёії]{2,}ла|уверена|'
    u'рада|готова|должна)'
    u'(?![а-яёії])', re.I)
SLAVIC_SPEAKER_M = re.compile(
    _SLAVIC_ME + u'(?:[а-яёії]{2,}л|уверен|'
    u'рад|готов|должен)'
    u'(?![а-яёії])', re.I)


# The same first-person reading for the rest of the chain, only where the
# ending itself carries person AND gender -- the counterpart of
# _ADDRESSEE_MARKERS. Polish -łam/-łem is anchored to "ja", a negation or the
# line start, because instrumental nouns end the same way ("przed stołem",
# "całym ciałem"); "zdołam" is a future, not a past. Portuguese thanks agree
# with the speaker. Spanish/Italian/French adjectives stay out for the reason
# given above _ADDRESSEE_MARKERS' callers: a feminine noun looks the same.
_PL_ANCHOR = r'(?:^|[.!?\-]\s*|\b(?:ja|nie|już|też|tylko|właśnie|zawsze|nigdy|nawet)\s+)'
SPEAKER_MARKERS = {
    'pl': (_rx(_PL_ANCHOR + r'(?!zdołam\b|podołam\b)\w{2,}łam\b',
               r'\bjestem\s+(?:pewna|gotowa|zmęczona|szczęśliwa|wdzięczna)\b'),
           _rx(_PL_ANCHOR + r'\w{2,}łem\b',
               r'\bjestem\s+(?:pewien|pewny|gotowy|zmęczony|szczęśliwy|wdzięczny)\b')),
    'cs': (_rx(r'\b\w+la\s+jsem\b', r'\bjsem\s+\w+la\b'),
           _rx(r'\b\w+l\s+jsem\b', r'\bjsem\s+\w+l\b')),
    'sk': (_rx(r'\b\w+la\s+som\b', r'\bsom\s+\w+la\b'),
           _rx(r'\b\w+l\s+som\b', r'\bsom\s+\w+l\b')),
    'sr': (_rx(r'\b\w+la\s+sam\b', r'\bsam\s+\w+la\b'),
           _rx(r'\b\w+(?:ao|io)\s+sam\b', r'\bsam\s+\w+(?:ao|io)\b')),
    'hr': (_rx(r'\b\w+la\s+sam\b', r'\bsam\s+\w+la\b'),
           _rx(r'\b\w+(?:ao|io)\s+sam\b', r'\bsam\s+\w+(?:ao|io)\b')),
    # Thanks only: at the start of a clause or after "muito". As a participle
    # ("ela foi obrigada", she was forced) it is about somebody else.
    'pt': (_rx(r'(?:^|[.!?,;:"\-]\s*|\bmuit[oí]ssimo\s+|\bmuito\s+)obrigada\b'),
           _rx(r'(?:^|[.!?,;:"\-]\s*|\bmuit[oí]ssimo\s+|\bmuito\s+)obrigado\b')),
}
