# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Who is speaking and who is addressed: the gender readers of a reference line and the checks of a Hebrew
# translation against them (conflicts, repaired lines).

import re
from resources.lib.ai_translation.context.gender.patterns_other import ADDRESSEE_MARKERS, SLAVIC_SPEAKER_F, SLAVIC_SPEAKER_M, SPEAKER_MARKERS
from resources.lib.ai_translation.context.gender.patterns_he import AR_FEM, AR_MASC, AR_NOUN_EXCEPTIONS, he_addresses_female, HE_MASC
from resources.lib.ai_translation.context.gender.patterns_ar import AR_ME_F, AR_ME_M, ar_more_addressee, AR_SPEAKER_F, AR_SPEAKER_M
from resources.lib.ai_translation.context.gender.align import log


# Invisible direction marks are not text: a human Hebrew file that starts a
# line with U+202B still starts it with "את" (measured by SRT AI Translator
# NG, Round 19A A.1 -- _HE_FEM_CLAUSE anchors on the line start).
_DIRECTION_MARKS = re.compile(u'[‎‏‪-‮⁦-⁩؜﻿]')


def _visible(text):
    return _DIRECTION_MARKS.sub(u'', text or u'')


def reference_addressee_gender(ref_text, lang):
    """'F', 'M', or None when the reference does not mark it unambiguously.

    'he' and 'ar' are read directly; every language in _ADDRESSEE_MARKERS
    (ru, uk, bg, pl, cs, sk, sr, hr, hi) is read from its own validated
    pattern pair. Anything else returns None, so the verification pass does
    not fire rather than guessing from a marking nobody checked here.
    (This used to say only he/ar were read, which stopped being true when
    _ADDRESSEE_MARKERS landed -- tools/test_gender_verification.py exercises
    all nine against both genders plus a must-not-match control.)
    """
    if not ref_text:
        return None
    try:
        ref_text = _visible(ref_text)
        if lang in ('es', 'pt'):
            # A short reviewed list of second-person predicate adjectives
            # (estás cansada / você está pronto); anything else stays None.
            gg = _gg()
            return gg.romance_addressee(ref_text, lang) if gg is not None else None
        if lang == 'he':
            f = he_addresses_female(ref_text)
            m = bool(HE_MASC.search(ref_text))
        elif lang == 'ar':
            signal_text = AR_NOUN_EXCEPTIONS.sub(' ', ref_text)
            f = any(p.search(signal_text) for p in AR_FEM)
            m = any(p.search(ref_text) for p in AR_MASC)
            more_f, more_m = ar_more_addressee(ref_text, signal_text)
            f, m = f or more_f, m or more_m
        elif lang in ADDRESSEE_MARKERS:
            fem_pats, masc_pats = ADDRESSEE_MARKERS[lang]
            f = any(p.search(ref_text) for p in fem_pats)
            m = any(p.search(ref_text) for p in masc_pats)
        else:
            return None
        if f and not m:
            return 'F'
        if m and not f:
            return 'M'
        return None
    except Exception:
        return None


def reference_speaker_gender(ref_text, lang):
    """'F', 'M' or None: the speaker's gender in a non-Hebrew reference line."""
    if not ref_text:
        return None
    try:
        ref_text = _visible(ref_text)
        if lang == 'ar':
            f = bool(AR_SPEAKER_F.search(ref_text) or AR_ME_F.search(ref_text))
            m = bool(AR_SPEAKER_M.search(ref_text) or AR_ME_M.search(ref_text))
        elif lang in ('ru', 'uk'):
            f = bool(SLAVIC_SPEAKER_F.search(ref_text))
            m = bool(SLAVIC_SPEAKER_M.search(ref_text))
        elif lang in SPEAKER_MARKERS:
            fem_pats, masc_pats = SPEAKER_MARKERS[lang]
            f = any(p.search(ref_text) for p in fem_pats)
            m = any(p.search(ref_text) for p in masc_pats)
        else:
            return None
        if f != m:
            return 'F' if f else 'M'
    except Exception:
        pass
    return None


_SLASH_FORM = re.compile(u'[\u05d0-\u05ea]/[\u05d0-\u05ea]')


def person_roles_fixed(body, roles, ref_text=None):
    """True when a rewritten line provably carries the required genders.

    roles: [(role, 'F'|'M')]. Speaker roles need positive proof from the
    person-form reader -- or, given the HEBREW reference line, the
    reference's own word for that person. An addressee role also passes on
    the older rule -- the opposite address form is gone and nothing reads
    as the opposite -- because the older check flags forms outside that
    reader's lexicon.
    """
    # Both forms at once ("תוכל/י") is a hedge, not a choice -- measured
    # in a repair the older addressee rule below let through.
    if _SLASH_FORM.search(body or ''):
        return False
    hpg = gender_reference_hpg()
    if hpg is not None and hpg.fixed(body, roles, ref_text):
        return True
    # With the Hebrew reference at hand, a rewrite that still contradicts it
    # for the role is no fix -- measured: only the verb changed and
    # "את-את נראה נחמד" kept the feminine pronoun.
    if hpg is not None and ref_text:
        try:
            left = set(r for r, _g in hpg.conflicts(body, ref_text))
        except Exception:
            left = set()
        if any(role in left for role, _gender in roles):
            return False
    for role, gender in roles:
        if role != 'addressee':
            return False
        if (addresses_male(body) if gender == 'F' else addresses_female(body)):
            return False
        if hpg is not None:
            seen = [t['addressee'] for t in hpg.read(body) if t['addressee']]
            if any(g != gender for g in seen):
                return False
    return True


def person_gender_conflicts(blocks, ref_map, lang):
    """{entry_number: [(role, reference_gender)]} where the Hebrew output
    contradicts the human reference about the SPEAKER or the ADDRESSEE.

    A Hebrew reference is compared word for word (he_person_gender.conflicts);
    any other language is read for its own speaker/addressee marking and
    compared with what the Hebrew line says, single-speaker lines only.
    Unknown on either side is never a conflict. Never raises.
    """
    out = {}
    if not ref_map:
        return out
    hpg = gender_reference_hpg()
    if hpg is None:
        return out
    for block in blocks:
        try:
            lines = block.split('\n')
            if len(lines) < 3 or not lines[0].strip().isdigit():
                continue
            num = int(lines[0].strip())
            ref = ref_map.get(num)
            if not ref:
                continue
            body = '\n'.join(lines[2:])
            if lang == 'he':
                found = hpg.conflicts(body, ref)
            else:
                turns = hpg.read(body)
                if len(turns) != 1:
                    continue
                found = []
                for role, want in (
                        ('speaker', reference_speaker_gender(ref, lang)),
                        ('addressee', reference_addressee_gender(ref, lang))):
                    if want and turns[0][role] and turns[0][role] != want:
                        found.append((role, want))
            if found:
                out[num] = found
        except Exception:
            continue
    return out


def addresses_female(text):
    """Conservative feminine-address candidate; not speaker/referent identity."""
    return he_addresses_female(_visible(text))


def addresses_male(text):
    """True when `text` contains the Hebrew masculine second-person pronoun.

    Public because the repair pass has to PROVE a rewrite actually fixed the
    error before it accepts it -- a replacement that still says the same thing
    is not a repair, and swapping one wrong line for another wrong line would
    spend a request to stand still.
    """
    try:
        return bool(HE_MASC.search(_visible(text)))
    except Exception:
        return False


def wrong_gender_entries(blocks, ref_map, lang, both_directions=False):
    """Conservative addressee-conflict candidates. Legacy default is F-only.
    The production repair opts into both directions and declines mixed M/F
    output; source context must still be checked by the repair. Never raises."""
    out = []
    if not ref_map:
        return out
    try:
        for block in blocks:
            lines = block.split('\n')
            if len(lines) < 3 or not lines[0].strip().isdigit():
                continue
            num = int(lines[0].strip())
            ref = ref_map.get(num)
            if not ref:
                continue
            target=reference_addressee_gender(ref, lang)
            body='\n'.join(lines[2:])
            male=addresses_male(body);female=he_addresses_female(body)
            if target=='F' and male and (not both_directions or not female):
                out.append(num)
            elif both_directions and target=='M' and female and not male:
                out.append(num)
    except Exception as e:
        log('gender verification crashed: {0}'.format(e), level='WARNING')
    return out


_GG = []


def _gg():
    """gender_guard, or None when it cannot be imported."""
    if _GG:
        return _GG[0]
    try:
        from resources.lib.ai_translation.repairs import gender_guard as mod
    except Exception:
        mod = None
    _GG.append(mod)
    return mod


_HPG = []


def gender_reference_hpg():
    """he_person_gender, or None when it cannot be imported."""
    if _HPG:
        return _HPG[0]
    try:
        from resources.lib.ai_translation.repairs import he_person_gender as mod
    except Exception:
        mod = None
    _HPG.append(mod)
    return mod
