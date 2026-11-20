# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Ranking helpers of the subtitle list: release-name match %, the same-source test, the SDH
# and gender-source ranks and the language names shown in the rows.

def match_pct(video_name, sub_name):
    """Release-name match % via the ONE structured scorer (release_match,
    SubSync S1): exact release=100, same group+source ~90, cross-source
    (WEB vs BluRay) capped low -- so the picker %, the source-screen badge
    and the engine ordering all agree AND the number reflects sync
    likelihood. Legacy token-similarity kept only as an import fallback."""
    try:
        from resources.lib.scoring_matching import release as _rm
        return _rm.match_pct(video_name, sub_name)
    except Exception:
        pass
    import re as _re
    import difflib as _dl

    def toks(s):
        s = _re.sub(r'\.[a-z0-9]{2,4}$', '', s or '', flags=_re.I)
        for ch in '_ +/-':
            s = s.replace(ch, '.')
        return [x.lower() for x in s.split('.') if x]

    a, b = toks(video_name), toks(sub_name)
    if not a or not b:
        return 0
    try:
        return int(round(_dl.SequenceMatcher(None, a, b).ratio() * 100))
    except Exception:
        return 0


def release_similarity(video_name, sub_name):
    """Closeness of the FULL release names, 0-1000 (resolution, codec, audio and the
    exact filename all count). The TIE-BREAK of every ranking that sorts by match %:
    it only orders candidates whose pct is equal and never changes a pct. 0 on any
    problem (so a failure keeps the existing order)."""
    try:
        from resources.lib.scoring_matching import release as _rm
        return _rm.similarity(video_name, sub_name)
    except Exception:
        return 0


def is_same_source(video_name, sub_name):
    """True only when the two release names are the SAME source (normalized
    identical -- release_match's TIER_EXACT). Used to gate embedded-sourced pool
    variants: an embedded translation is synced to ONE specific source's timing,
    so it is only surfaced as "תרגום מובנה" for that exact release. Conservative
    -- anything short of an exact release match is treated as a different source.
    Never raises (returns False on any problem)."""
    if not video_name or not sub_name:
        return False
    try:
        from resources.lib.scoring_matching import release as _rm
        return _rm.match_tier(video_name, sub_name) == _rm.TIER_EXACT
    except Exception:
        return False


def lang_display(code):
    return {
        'en': 'English', 'es': 'Spanish', 'fr': 'French',
        'de': 'German', 'pt': 'Portuguese', 'he': 'Hebrew',
    }.get(code, code or 'Unknown')


def lang_display_he(code):
    """Hebrew language name for user-facing notifications (English name / the raw
    code when unknown)."""
    return {
        'en': 'אנגלית', 'es': 'ספרדית', 'fr': 'צרפתית', 'de': 'גרמנית',
        'pt': 'פורטוגזית', 'it': 'איטלקית', 'ru': 'רוסית', 'ar': 'ערבית',
        'he': 'עברית', 'nl': 'הולנדית', 'sv': 'שוודית', 'da': 'דנית',
        'no': 'נורווגית', 'fi': 'פינית', 'pl': 'פולנית', 'tr': 'טורקית',
        'ja': 'יפנית', 'ko': 'קוריאנית', 'zh': 'סינית', 'el': 'יוונית',
        'cs': 'צ׳כית', 'hi': 'הינדי', 'ro': 'רומנית', 'uk': 'אוקראינית',
    }.get(code, lang_display(code))


# Source-language gender-marking strength, for ordering embedded pool
# translations by gender accuracy. Hebrew renders speaker gender ("אני עייף/
# עייפה"); the gender-reference chain covers most lines, but on the lines it
# doesn't, the AI falls back to the SOURCE text's own gender. A source that marks
# speaker gender on verbs/adjectives (Semitic, Romance, Slavic, Indo-Aryan) gets
# those right; English/German/Dutch don't mark PREDICATIVE gender ("I'm tired" /
# "Ich bin müde" carry none) and must guess -> so a strong-gender source is more
# gender-accurate and is shown FIRST among same-source embedded items.
_GENDER_STRONG_SRC = frozenset((
    'ar', 'he',                                       # Semitic
    'es', 'fr', 'it', 'pt', 'ro', 'ca', 'gl',         # Romance
    'ru', 'uk', 'pl', 'cs', 'sk', 'sr', 'hr', 'bg',   # Slavic
    'sl', 'be',
    'hi', 'ur', 'pa', 'mr', 'gu', 'bn',               # Indo-Aryan
))


def gender_src_rank(source_lang):
    """0 for a source language that marks speaker gender (best Hebrew gender on
    reference-gap lines), 1 for weakly/non-gendered (en/de/nl/...). Lower first."""
    return 0 if (source_lang or 'en').strip().lower()[:2] in _GENDER_STRONG_SRC else 1


def is_sdh_ext(cand, release):
    """True when an EXTERNAL subtitle is SDH in the sense that MATTERS here --
    it carries speaker labels, so it is genuinely the more gender-accurate
    source. Two reliable signals only: a WHOLE-TOKEN 'sdh' / 'hearing impaired'
    marker in the release name (curated by the release group), or a release
    previously CONTENT-detected as SDH (Phase 3 local + shared registry, which
    actually measures speaker-label / sound-cue density).

    We deliberately do NOT trust the provider's own hearing-impaired flag: it
    means "has sound cues", which is NOT the same as "has speaker labels", so it
    mislabels ordinary subs as 'SDH (מדויק למגדר)' -- a false positive the user
    saw in the field (a plain sub flagged HI by the provider but with no
    character names). Zero false positives on this label matters more than
    catching every SDH sub; the content-detection path recovers the genuine ones
    after a first download. NEVER a bare substring -- 'hi' inside 'Highlander' /
    'cc' inside 'Soccer' must not match. Best-effort; never raises."""
    try:
        from resources.lib.scoring_matching import release as scoring_matching_release
        _rel = release or ''
        toks = scoring_matching_release.tokens(_rel)
    except Exception:
        return False
    if 'sdh' in toks:
        return True
    for i in range(len(toks) - 1):
        if toks[i] == 'hearing' and toks[i + 1] == 'impaired':
            return True
    try:
        from resources.lib.ai_translation.reuse import sdh_registry
        if sdh_registry.is_known_sdh(scoring_matching_release.normalize(_rel)):
            return True
    except Exception:
        pass
    try:
        # Phase 3b: the community-shared SDH set (reads a local cache only, no
        # network on this ranking path).
        from resources.lib.ai_translation.reuse import sdh_pool
        if sdh_pool.is_shared_sdh(_rel):
            return True
    except Exception:
        pass
    return False
