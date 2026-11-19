# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The reference chain: which languages are tried and in what order, the embedded and external candidates, and
# downloading one.

import os
import re
from resources.lib.ai_translation.context.gender.align import log
try:
    from resources.lib.core import kodi, settings
except Exception:
    kodi = settings = None


# ---------------- the reference-language priority chain ---------------------

# Priority order for the gender oracle. An out-of-sync HUMAN Hebrew sub is the
# strongest possible reference (it IS the target language); Arabic is the gold
# standard among foreign ones (Semitic, near-1:1 gender marking with Hebrew);
# then languages ranked by gender-signal strength x real-world availability.
REF_CHAIN = ('he', 'ar', 'hi', 'es', 'ru', 'pt', 'pl', 'uk', 'fr', 'it',
              'cs', 'ro', 'el', 'bg', 'sr', 'hr', 'sk', 'ur', 'nl')

# The chain split into quality tiers, which is what begin() actually walks.
# Tier 1 is the two oracles this feature was built and validated on; tier 2 is
# everything else, which is a useful hint but not in the same class. The
# boundary is the thing to move if real data ever says another language earns
# tier 1 -- not the round-robin, which is only about reaching them.
REF_TIERS = (('he', 'ar'), REF_CHAIN[2:])

# Codes/names a provider might report for each chain language (lowercase).
# Providers emit a mix of ISO 639-1, 639-2/B, 639-2/T and English names; the
# bridge normalizes most but not all, so we match generously here.
_REF_LANG_ALIASES = {
    'he': ('he', 'iw', 'heb', 'hebrew'),
    'ar': ('ar', 'ara', 'arabic'),
    # 'ea' is OpenSubtitles' own code for Latin American Spanish, 'pt-pt' its
    # European Portuguese: both came back from the engine on 15 Nov 2026 and
    # neither was read as a chain language (three human pt-pt subtitles in a
    # sample of sixteen episodes went unused).
    'es': ('es', 'sp', 'spa', 'spanish', 'ea', 'es-la', 'es-419', 'es-es',
           'spanish (la)', 'spanish (eu)'),
    'fr': ('fr', 'fre', 'fra', 'french'),
    'ru': ('ru', 'rus', 'russian'),
    'it': ('it', 'ita', 'italian'),
    'pt': ('pt', 'por', 'pb', 'pob', 'pt-br', 'ptbr', 'pt-pt', 'portuguese',
           'brazilian portuguese', 'brazillian portuguese',
           'portuguese (br)', 'portuguese (pt)'),
    'pl': ('pl', 'pol', 'polish'),
    'uk': ('uk', 'ukr', 'ukrainian'),
    'hi': ('hi', 'hin', 'hindi'),
    'cs': ('cs', 'cze', 'ces', 'czech'),
    'ro': ('ro', 'rum', 'ron', 'romanian'),
    'el': ('el', 'gr', 'gre', 'ell', 'greek'),
    'bg': ('bg', 'bul', 'bulgarian'),
    'sr': ('sr', 'srp', 'scc', 'serbian'),
    'hr': ('hr', 'hrv', 'scr', 'croatian'),
    'sk': ('sk', 'slo', 'slk', 'slovak'),
    'ur': ('ur', 'urd', 'urdu'),
    'nl': ('nl', 'dut', 'nld', 'dutch'),
}

_ALIAS_TO_CHAIN = {alias: code
                   for code, aliases in _REF_LANG_ALIASES.items()
                   for alias in aliases}

# Try substantially deeper inside each higher-priority language before moving
# to the next one. Provider metadata search is parallel, but the actual subtitle
# downloads + alignment below are lazy, serial and chain-ordered: first aligning
# candidate wins. Ten is deliberately bounded because OpenSubtitles can paginate
# every result for a popular title; "all" could mean hundreds of downloads and
# minutes of blocked playback. A language may receive up to ten attempts; the
# global download budget lets the search fully examine the five strongest
# saturated languages (he/ar/hi/es/ru), while the active-work deadline remains
# the primary latency circuit-breaker. This still avoids the unbounded
# 10-times-every-language worst case and provider request storms.
PER_LANG_LIMIT = 10
TOTAL_DOWNLOAD_BUDGET = 50
# The chain is walked round-robin (see begin()), so a usable oracle is
# reached early. The deadline is what decides how much DEPTH is left
# afterwards -- how many candidates per language get a turn once every
# language has had its first. At 30s it was one or two rounds, so the
# fifth Hebrew subtitle, which may well be a different release that
# aligns where the first four did not, was rarely reached at all.
#
# 60s buys full depth on the languages that are actually present
# (~30 downloads) and costs nothing in the common case: next() returns
# the moment something aligns, so a job whose first candidate works
# still pays for one download. Only a job where NOTHING aligns spends
# the ceiling, and it spends it inside a translation that already runs
# for minutes behind a progress bar (a film is ~36 requests paced at 14
# per minute), not in front of playback.
REFERENCE_DEADLINE_S = 60.0


def chain_lang_of(cand):
    """Map an engine candidate to its chain language code, or None if the
    candidate's language isn't in the chain (or is unusable as an oracle)."""
    # A machine/AI-translated sub in ANY language is a POISONED oracle (MT
    # defaults to masculine) -- only human subs may anchor gender. The bridge
    # sets '_is_mt' from the provider's flag (e.g. OpenSubtitles ai_translated/
    # machine_translated); missing key (old cached results) -> not flagged.
    if cand.get('_is_mt'):
        return None
    lang = _ALIAS_TO_CHAIN.get((cand.get('language') or '').strip().lower())
    if lang == 'he':
        # Hebrew gets an extra belt-and-braces check via the engine kind: only
        # candidates the bridge classified as HUMAN Hebrew are accepted.
        kind = (cand.get('_engine_kind') or '').strip().lower()
        if kind and kind != 'human_he':
            return None
        if 'HebrewMachineTranslated' in (cand.get('filename') or ''):
            return None
    return lang


def provider_of(cand):
    """The provider in an engine candidate's label ("[XX] Wizdom · 87%  --
    name"), for the log. A Hebrew reference is trusted as a human
    translation; a field log has to show which provider that trust went to
    (NG gender-reference: reference provenance)."""
    import re
    label = re.sub(r'^(?:\[[^\]]*\]\s*)+', '', cand.get('filename') or '')
    return label.split(' · ', 1)[0].strip()[:40] or '?'


# ---------------- fetch reference candidates from the engine ----------------

# How long the reference search waits for a timed-out provider of the search
# before it (see _reference_candidates). The Hebrew waits with it: a live
# translation shows the English meanwhile, the automatic one on play nothing.
_PROVIDER_WAIT_S = 20.0


def reference_candidates(info):
    """Return ALL subtitle CANDIDATES (metadata only -- NOT yet downloaded) for
    this media from the built-in engine (OpenSubtitles / SubSource / YIFY /
    Hebrew providers). Filtering to chain languages happens in prepare().

    The engine gates languages by setting, so JUST for this search we flip
    `language_arab` on and force `all_lang` on (so every chain language comes
    back in one search), then restore. language_arab always settles to 'false'
    (it must never pollute normal searches -- Arabic is only ever an internal
    gender oracle, not a target language); all_lang is restored to whatever the
    user had. Guarded; never raises."""
    try:
        from resources.lib.fetching import bridge
    except Exception:
        return []
    # What the search for the source subtitle found moments ago -- even when
    # it stopped at max_search_time -- holds the other languages already: no
    # new search, no wait.
    try:
        reused = bridge.reusable_results(info) or []
    except Exception:
        reused = []
    if any(isinstance(c, dict) and chain_lang_of(c) not in (None, 'he')
           for c in reused):
        log('gender reference: candidates from the search moments ago '
             '({0})'.format(len(reused)))
        return reused
    # Hebrew alone (the viewer's languages, or the multi-language provider
    # was the one cut): kept, but the other languages are searched for too --
    # an out-of-sync Hebrew subtitle often fails to align.
    reused_he = [c for c in reused
                 if isinstance(c, dict) and chain_lang_of(c) == 'he']
    # That search leaves its slowest provider running, and until it ends the
    # engine refuses every search: this one came moments later and found
    # nothing at all. Measured in Kodi (2 Nov 2026): every search after a cut
    # one came back empty in 0.4 s (Friends, Breaking Bad and The
    # Untouchables among them), the provider ended ~9 s later, and searched
    # then they found 19-335 candidates in the chain's languages. Waited for
    # before the language settings are flipped below.
    try:
        if bridge.providers_busy():
            log('a timed-out provider of the last search still runs: '
                 'waiting up to {0:.0f}s to search for a gender '
                 'reference'.format(_PROVIDER_WAIT_S))
            bridge.wait_for_providers(_PROVIDER_WAIT_S)
    except Exception:
        pass
    cands = []
    prev_all_lang = None
    try:
        if kodi is not None:
            try:
                settings.set_setting('language_arab', 'true')
            except Exception:
                pass
            try:
                prev_all_lang = settings.get_setting('all_lang', '')
                if (prev_all_lang or '').strip().lower() != 'true':
                    settings.set_setting('all_lang', 'true')
                else:
                    prev_all_lang = None  # already true -> nothing to restore
            except Exception:
                prev_all_lang = None
        try:
            cands = bridge.search(info, modal_progress=False) or []
        except Exception as e:
            log('engine search for gender reference failed: {0}'.format(e),
                 level='WARNING')
            cands = []
        if reused_he:
            seen = set(c.get('link') for c in cands if isinstance(c, dict))
            cands = list(cands) + [c for c in reused_he
                                   if c.get('link') not in seen]
    finally:
        # Always settle Arabic back OFF so it never pollutes normal searches
        # (also self-heals installs left 'true' by the old eager code).
        if kodi is not None:
            try:
                settings.set_setting('language_arab', 'false')
            except Exception:
                pass
            if prev_all_lang is not None:
                try:
                    settings.set_setting('all_lang', prev_all_lang)
                except Exception:
                    pass
    return cands


# A track someone ran through a translator is a poisoned oracle, as with a
# provider's (see _chain_lang_of). Releases that carry such tracks say so in
# the track's name.
_EMBEDDED_MT_NAME = re.compile(
    r'(?<![a-z])(machine|mtl|mt|auto|automatic|autogenerated|generated|google|'
    r'deepl|ai|translated)(?![a-z])', re.I)


# The languages a track of the playing file may be a reference in. Arabic
# only (0.3.88, the owner's word): blind study 008 found no gain from a
# Spanish track of the file against no reference (8-10-2), so the second
# tier stays out; Arabic is the foreign oracle this feature was built and
# measured on, and the file's own track is on the file's own clock.
_EMBEDDED_REF_LANGS = ('ar',)


def embedded_candidates(embedded):
    """[(lang, candidate), ...] -- the playing file's own subtitle tracks in
    the languages of _EMBEDDED_REF_LANGS, one a language (a plain track before
    an SDH one), in chain order. `embedded` is translate._embedded_reference()'s (tracks,
    read) or None. Hebrew is left out: a file with a whole human Hebrew track
    is not being translated, and a Hebrew track that is there anyway is one
    nobody could vouch for. Never raises."""
    if not embedded:
        return []
    try:
        tracks, read = embedded
        best = {}
        for t in tracks() or []:
            if _EMBEDDED_MT_NAME.search(t.get('name') or ''):
                continue
            code = (t.get('lang') or '').strip().lower()
            for sep in ('-', '_'):
                code = code.split(sep, 1)[0]
            lang = _ALIAS_TO_CHAIN.get(code)
            if (not lang or lang == 'he' or lang not in _EMBEDDED_REF_LANGS
                    or not t.get('lang_explicit', True)):
                continue
            rank = (bool(t.get('sdh')), t.get('num') or 0)
            if lang not in best or rank < best[lang][0]:
                best[lang] = (rank, t)
        out = []
        for lang in REF_CHAIN:
            if lang in best:
                t = best[lang][1]
                out.append((lang, {
                    'language': lang, '_embedded': True,
                    '_read': (lambda t=t: read(t)),
                    'filename': 'embedded track #{0}{1}'.format(
                        t.get('num'), (' ' + t['name'][:30]) if t.get('name') else '')}))
        return out
    except Exception as e:
        log('embedded reference tracks: {0}'.format(e), level='WARNING')
        return []


def download_candidate(c):
    """Download ONE reference candidate and return its text, or None. Guarded."""
    if c.get('_embedded'):
        try:
            return c['_read']()
        except Exception as e:
            log('embedded reference read failed (continuing): {0}'.format(e),
                 level='DEBUG')
            return None
    try:
        from resources.lib.fetching.embedded import streams
        from resources.lib.core import link_codec
        payload = link_codec.decode_link(c.get('link') or '')
        if not payload:
            return None
        # Reference analysis needs logical text, not the player-rendered RTL copy.
        path = streams.download(payload, for_delivery=False)
        if path and os.path.isfile(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
    except Exception as e:
        log('reference download failed (continuing): {0}'.format(e),
             level='DEBUG')
    return None
