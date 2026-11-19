# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Gendered-language reference for AI translation (opt-in): ReferencePlan (the lazy ladder of languages) and
# begin(), the entry point.
#
# Hebrew is heavily gendered and the #1 quality issue is per-line gender (who is speaking / who is addressed).
# English doesn't mark it; Arabic does, almost 1:1 with Hebrew (أنتَ/أنتِ, gendered verbs/imperatives). A HUMAN
# Arabic subtitle of the same title already "solved" gender per line. So, when enabled, we fetch an Arabic sub for
# the same media, time-align it to the source SRT, and hand each entry its aligned Arabic line as a GENDER ORACLE
# in the prompt (see context/prompt.py).
#
# When no Arabic subtitle exists (or none aligns), we fall through a PRIORITY CHAIN of other gender-marking
# languages (see chain.REF_CHAIN): an out-of-sync human HEBREW sub is the best oracle of all, then Arabic, then
# Romance/Slavic/Indic languages whose adjectives/verbs mark speaker+addressee gender. Same fetch-align-hint
# pipeline, one engine search for all of them.
#
# This is fully guarded: ANY failure returns None and the caller falls back to the normal (no-reference)
# translation. It NEVER raises. Validated on real OpenSubtitles pairs (From S03E09, Super Mario Bros Movie):
# global alignment is reliable across different releases + SDH once SFX/music is filtered; per-line interleave
# with the strong prompt lifted gender accuracy from ~27% (cast-only) to ~90%+ with zero regressions.
# (Until Subtitle Phase 4 this was one 1,800-line file; it is now the modules of context/gender/.)

import threading
import time
from resources.lib.ai_translation.context.gender.chain import chain_lang_of, download_candidate, embedded_candidates, PER_LANG_LIMIT, provider_of, REF_CHAIN, REF_TIERS, reference_candidates, REFERENCE_DEADLINE_S, TOTAL_DOWNLOAD_BUDGET
from resources.lib.ai_translation.context.gender.align import align_one, log


class ReferencePlan(object):
    """A lazy, resumable gender-reference chain. Holds the ordered candidate list
    (metadata only, no downloads yet) and yields aligned per-entry maps ONE
    LANGUAGE AT A TIME in priority order via .next().

    The FIRST .next() gives the primary reference (same single-download cost as
    the old prepare()); each subsequent .next() downloads+aligns the NEXT chain
    language that aligns -- used by translate.py as a fallback when a chunk gets
    prompt-blocked (PROHIBITED_CONTENT), so a blocked chunk can be retried with a
    DIFFERENT human-subtitle gender oracle (e.g. Spanish after Arabic) before
    dropping the reference entirely. Downloads are lazy + bounded by
    _TOTAL_DOWNLOAD_BUDGET, so a job that never blocks pays for exactly one
    reference. Thread-safe (parallel chunk workers may pull a fallback at once)
    and fully guarded: any failure yields (None, None), never raises."""

    def __init__(self, src_text, src_blocks, ordered, total, timing_text=None):
        self._src_text = src_text
        self._timing_text = timing_text
        self._src_blocks = src_blocks
        self._ordered = ordered      # [(lang, candidate), ...] in chain order
        self.total = total
        self._pos = 0
        self._downloads = 0
        # Count only time spent downloading/alignment. Long idle gaps between
        # lazy .next() calls (while Gemini translates) must not expire fallbacks.
        self._active_elapsed = 0.0
        self._used = set()           # chain langs already returned (one map each)
        self._spent_said = False     # the providers' budget ran out: logged once
        self._lock = threading.Lock()
        self.last_diag = ''          # diag of the candidate examined last: the
        #                              WINNER right after a successful next(), or
        #                              the last rejection once the chain is dry

    def next(self, max_tries=None, max_seconds=None):
        """Return (lang, map) for the next chain language that aligns, or
        (None, None) when the chain / download budget is exhausted.
        `max_tries` / `max_seconds`: this call gives up, with nothing lost,
        after that many providers' subtitles or that long (a fallback for
        one blocked chunk; the first reference is looked for without them)."""
        try:
            with self._lock:
                tries, began = 0, time.monotonic()
                while self._pos < len(self._ordered):
                    lang, cand = self._ordered[self._pos]
                    if (lang not in self._used and not cand.get('_embedded') and (
                            (max_tries is not None and tries >= max_tries)
                            or (max_seconds is not None
                                and time.monotonic() - began >= max_seconds))):
                        log('no other reference aligned in {0} subtitle(s) / '
                             '{1:.0f}s -- not looked for further'.format(
                                 tries, time.monotonic() - began))
                        return None, None
                    # The two budgets are the providers': a track of the
                    # playing file costs no provider request and has its own
                    # bound (translate._embedded_reference), so one that
                    # comes after the budget is spent is still tried.
                    embedded = bool(cand.get('_embedded'))
                    if not embedded and (
                            self._downloads >= TOTAL_DOWNLOAD_BUDGET
                            or self._active_elapsed >= REFERENCE_DEADLINE_S):
                        if not self._spent_said:
                            self._spent_said = True
                            if self._downloads >= TOTAL_DOWNLOAD_BUDGET:
                                log('download budget ({0}) exhausted -- '
                                     'stopping the chain'.format(
                                         TOTAL_DOWNLOAD_BUDGET))
                            else:
                                log('reference work budget ({0:.0f}s active) '
                                     'reached -- stopping the chain'.format(
                                         REFERENCE_DEADLINE_S))
                        if not any(c.get('_embedded') for _l, c
                                   in self._ordered[self._pos:]):
                            return None, None
                        self._pos += 1
                        continue
                    self._pos += 1
                    if lang in self._used:
                        continue     # already yielded a map for this language
                    attempt_started = time.monotonic()
                    try:
                        ref_text = download_candidate(cand)  # lazy fetch
                        if not embedded:
                            self._downloads += 1
                            tries += 1
                        if not ref_text:
                            continue
                        try:
                            if self._timing_text:
                                mapping, diag = align_one(
                                    self._src_text, self._src_blocks, ref_text,
                                    timing_text=self._timing_text)
                            else:
                                mapping, diag = align_one(
                                    self._src_text, self._src_blocks, ref_text)
                        except Exception as e:
                            log('align [{0}] crashed: {1}'.format(lang, e),
                                 level='WARNING')
                            continue
                        # winner (on return) or last rejection once chain is dry
                        self.last_diag = diag
                        if mapping:
                            self._used.add(lang)
                            log(
                                'reference [{0}] {1} -> {2} entries hinted '
                                '(from {3})'.format(lang, diag, len(mapping),
                                                    provider_of(cand)))
                            return lang, mapping
                        log(
                            'candidate [{0}] rejected: {1} -- trying next'
                            .format(lang, diag))
                    finally:
                        if not embedded:
                            self._active_elapsed += max(
                                0.0, time.monotonic() - attempt_started)
                return None, None
        except Exception as e:
            log('ReferencePlan.next crashed: {0}'.format(e), level='WARNING')
            return None, None


def begin(info, src_text, timing_text=None, embedded=None):
    """ENTRY POINT (lazy). When the feature is on, find gender-reference
    candidates for `info` and return a ReferencePlan that yields aligned maps in
    PRIORITY-CHAIN order (Hebrew, Arabic, then the other gender-marking
    languages -- see _REF_CHAIN), ONE language per .next() call, downloading
    lazily. Returns (plan, diag). `plan` is None when there is no candidate at
    all (diag.reason = 'crash'/'no_source'/'no_arabic'); otherwise call
    plan.next() to pull the primary (and, later, fallback) references. Fully
    guarded; never raises.
    `embedded` (translate._embedded_reference): the playing file's own tracks
    (_embedded_candidates: Arabic) join the candidates, each in its
    language's own turn of its tier's first round, before the providers'
    first subtitle in that language: a provider's Hebrew is tried first as
    before, then the file's Arabic track -- on the file's own clock, no
    provider asked -- then the providers' Arabic."""
    try:
        from resources.lib.core.srt import blocks as _srt
        src_blocks = _srt.parse_blocks(src_text)
    except Exception:
        return None, {'reason': 'crash'}
    if not src_blocks:
        return None, {'reason': 'no_source'}
    try:
        all_cands = reference_candidates(info)
    except Exception as e:
        log('fetch crashed: {0}'.format(e), level='WARNING')
        return None, {'reason': 'crash'}

    # Bucket by chain language, preserving the engine's own ranking (it sorts
    # by release-match %, so earlier candidates align more often).
    #
    # GUARDED because the docstring promises it. Everything from here down used
    # to sit outside every handler, so a candidate the source engine handed back
    # in an unexpected shape -- a bare string, or one whose 'language' is a list
    # -- left this function as an AttributeError. The single caller does wrap the
    # call, so the user outcome was always the correct silent fallback; but a
    # module documented as never raising has to actually never raise, or the next
    # caller inherits a promise the code does not keep.
    by_lang = {}
    try:
        for c in all_cands:
            lang = chain_lang_of(c)
            if lang and len(by_lang.setdefault(lang, [])) < PER_LANG_LIMIT:
                by_lang[lang].append(c)
    except Exception as e:
        log('bucketing crashed: {0}'.format(e), level='WARNING')
        return None, {'reason': 'crash'}

    # NOTE on Hebrew-first: the user reached AI translation, so any Hebrew sub
    # here is out-of-sync / unmatched for their release -- but as a GENDER
    # ORACLE it only needs to time-align to the SOURCE sub, which the scale+
    # offset estimator handles. It is the strongest oracle (it IS Hebrew).
    #
    # ROUND-ROBIN WITHIN A TIER, tiers in order.
    #
    # Two different things have to be true at once, and each one alone gets
    # the other wrong.
    #
    # 1. A STRONG oracle must win even from deep in its own list. Hebrew and
    #    Arabic are not merely first in the chain, they are a different class:
    #    Hebrew IS the target language, and Arabic marks addressee gender with
    #    explicit diacritics that map almost one-to-one onto Hebrew (the block
    #    built around it lifted gender accuracy from ~27% to ~90%+). The third
    #    Arabic candidate is worth far more than the first Slovak one. A flat
    #    round-robin does not know that: it takes whatever aligns first, so a
    #    weak language's opening candidate beats a strong language's third.
    #
    # 2. A strong oracle must be REACHED. The old order walked one language at
    #    a time, so ten Hebrew candidates came before the first Arabic one --
    #    and since the deadline is spent on downloads, the chain could stop
    #    before Arabic was tried at all, leaving the job with no oracle and a
    #    translation that defaults to masculine.
    #
    # Tiers give both. Inside tier 1 the two strong languages alternate, so
    # Arabic is attempt 2 rather than attempt 11; and tier 1 is exhausted
    # completely before tier 2 is touched, so no weaker language can take a
    # job away from an Arabic candidate that would have aligned. Within tier 2
    # the languages are close enough in value that reaching ANY of them
    # matters more than which, so they alternate too.
    inside = embedded_candidates(embedded)
    ordered = []
    for tier in REF_TIERS:
        own = dict(pair for pair in inside if pair[0] in tier)
        depth = max([len(by_lang.get(l, ())) for l in tier] or [0])
        for i in range(max(depth, 1 if own else 0)):
            for lang in tier:
                if i == 0 and lang in own:
                    ordered.append((lang, own[lang]))
                if i < len(by_lang.get(lang, ())):
                    ordered.append((lang, by_lang[lang][i]))
    if inside:
        log('reference candidates inside the playing file: {0}'.format(
            ', '.join('{0} ({1})'.format(lang, c['filename'])
                      for lang, c in inside)))
    if not ordered:
        log('no gender-reference candidates in any chain language -> normal '
             'translation (fallback)')
        return None, {'reason': 'no_arabic', 'cands': 0}

    total = len(ordered)
    langs_present = [l for l in REF_CHAIN
                     if l in by_lang or any(l == pair[0] for pair in inside)]
    log('reference candidates: {0} across {1}'.format(
        total, ','.join(langs_present)))
    return (ReferencePlan(src_text, src_blocks, ordered, total,
                          timing_text=timing_text),
            {'reason': 'pending', 'cands': total})
