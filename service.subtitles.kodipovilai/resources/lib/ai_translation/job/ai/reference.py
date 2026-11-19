# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# AI stages, 5 of 10 -- reference: the gender reference (a human subtitle in another language) and its
# lazy fallback tiers.

from functools import partial
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi
from resources.lib.fetching.embedded.extraction import embedded_reference
from resources.lib.ai_translation.job.ai.tuning import REF_FALLBACK_SECONDS, REF_FALLBACK_TRIES
from resources.lib.ai_translation.job.state import NEXT, translation_cancelled


def ref_ensure(S, level):
    """Ensure _ref_stack has a reference at `level` (0 = primary), pulling
    the next aligning chain language from the plan on demand. Returns True if
    a reference exists at that level. Fully guarded.

    Building a fallback tier runs an un-timed provider download; that must
    never stall OTHER blocked chunks. So the lock is taken NON-BLOCKING: if
    another worker is already building the next tier, this caller does not
    wait -- it returns the current state (usually False) and proceeds to
    bisect / English-only / source instead of blocking behind the download.
    The builder appends the tier for everyone; a later call picks it up. The
    startup primary pull (level 0) is single-threaded, so it always wins the
    lock uncontended."""
    if level < 0:
        return False
    if level < len(S.ref_stack):
        return True
    plan = S.ref_plan[0]
    if plan is None:
        return False
    if not S.ref_lock.acquire(False):
        return level < len(S.ref_stack)   # someone else is building -> proceed
    try:
        while len(S.ref_stack) <= level:
            try:
                if S.ref_stack:
                    lang, mp = plan.next(max_tries=REF_FALLBACK_TRIES,
                                         max_seconds=REF_FALLBACK_SECONDS)
                else:
                    lang, mp = plan.next()
            except Exception:
                lang, mp = None, None
            if mp is None:
                if S.ref_stack:
                    S.ref_plan[0] = None    # no later chunk looks again
                return False
            S.ref_stack.append((lang or 'ar', mp))
            if len(S.ref_stack) > 1:
                kodi.log(
                    'gender-ref: added fallback tier {0} [{1}] for blocked '
                    'chunks'.format(len(S.ref_stack) - 1, lang or '?'),
                    level='INFO')
    finally:
        S.ref_lock.release()
    return level < len(S.ref_stack)


def reference(S):
    """The gender reference of the opt-in tier."""
    # Gender reference (opt-in). Only here -- after every cache/pool miss,
    # so we never pay the fetch on a hit. Fetches + time-aligns a human sub in
    # the reference-language priority chain (Hebrew, Arabic, then other
    # gender-marking languages -- see arabic_gender._REF_CHAIN); returns a
    # per-entry map or None. Fully guarded; None => normal translation.
    if S.ar_on:
        kodi.log('gender-ref: ON -- translating "{0}" via the gender '
                       'reference path'.format(
                           (S.info.get('title') or S.imdb_id or '?')), level='INFO')
        try:
            from resources.lib.ai_translation.context.gender.plan import begin
            _emb_ref = (embedded_reference(
                S.info, S.source_lang, cancelled=partial(translation_cancelled, S))
                if S.payload.get('embedded') else None)
            if S.timing_src:
                plan, _diag0 = begin(
                    S.info, S.src_text, timing_text=S.timing_src,
                    embedded=_emb_ref)
            else:
                plan, _diag0 = begin(
                    S.info, S.src_text, embedded=_emb_ref)
            S.ref_plan[0] = plan
            if plan is not None and ref_ensure(S, 0):
                # primary reference pulled (same single-download cost as before);
                # fallback languages stay un-downloaded until a chunk blocks.
                S.ref_lang = (S.ref_stack[0][0] or 'ar')
                S.ar_map = S.ref_stack[0][1]
                S.ar_diag = {'reason': 'ok', 'cands': _diag0.get('cands', 0),
                            'diag': getattr(plan, 'last_diag', ''),
                            'hinted': len(S.ar_map or {}), 'lang': S.ref_lang}
            elif plan is not None:
                # candidates existed but none aligned confidently
                S.ar_map = None
                S.ar_diag = {'reason': 'no_align',
                            'cands': _diag0.get('cands', 0),
                            'diag': getattr(plan, 'last_diag', '')}
            else:
                # no candidate at all: crash / no_source / no_arabic
                S.ar_map = None
                S.ar_diag = _diag0
        except Exception as e:
            kodi.log('gender-ref prepare crashed: {0}'.format(e),
                           level='WARNING')
            S.ar_map = None
            S.ar_diag = {'reason': 'crash'}

    # If the feature is on but NO usable reference was found, this becomes a
    # normal translation -- store it as PLAIN (never masquerade a non-boosted
    # result as ai_ar), so it can still be upgraded later when a reference
    # sub appears.
    S.used_ar = bool(S.ar_map)
    if S.ar_on and not S.used_ar:
        kodi.log('gender-ref: no usable reference sub in any chain '
                       'language this time -> normal translation, stored as '
                       'the plain tier', level='INFO')
        S.tier = ''
        # Keep the embedded marker even when the Arabic gender-ref found no
        # usable reference and we fall back to the plain tier.
        S.pool_kind = 'ai_emb' if S.payload.get('embedded') else 'ai'
        S.translated = translation_cache.translated_path(
            S.imdb_id, S.season, S.episode, S.source_lang,
            source_id=(S.early_source_id or S.content_id), tier='')
        if S.src_release:
            try:
                with open(S.translated + '.release', 'w',
                          encoding='utf-8') as _rf:
                    _rf.write(S.src_release)
            except OSError:
                pass
    S.final_pool_hash = (S.content_id + '_ar') if (S.ar_on and S.used_ar) \
        else S.content_id
    return NEXT
