# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# AI stages, 1 of 10 -- prepare: release names, gender-reference tier, pool kind; the delivery closure.

import os
import threading
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi, settings, video_context
from resources.lib.ai_translation.source_prep import content_hash, prepared_source_reader
from resources.lib.sync.delivery_gate import sync_hebrew_delivery
from resources.lib.ai_translation.job.state import NEXT
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None


def deliver(S, path):
    return sync_hebrew_delivery(
        S.info, path, source_release=S.timing_release,
        embedded_timing=S.embedded_timing,
        selection=S.timing_selection, fallback_link=S.fallback_link,
        fallback_links=S.fallback_links)


# proofread_later's job never translates: it proofreads the translation
# already on screen. One made with the reference on but none found went
# to the PLAIN tier ("no usable reference" below), where this job's
# tier-pinned lookups never looked -- so it never got its proofread (field
# data 2 Nov 2026: 0 of 6 on the automatic path, against 5 of 7 made with
# a reference). The job looks there too when the reference tier holds
# none, and is then a plain-tier job throughout: its pool upload replaces
# the plain variant, never one under "_ar". The pinning below exists for
# the delivery paths (no progressive_cb before an early return, the
# backfill's tier); this job returns at either lookup, before both.
def proofread_plain_tier(S, source_id):
    if not (S.proofread_only and S.ar_on and source_id):
        return False
    try:
        return (not os.path.isfile(translation_cache.translated_path(
                    S.imdb_id, S.season, S.episode, S.source_lang,
                    source_id=source_id, tier='ar'))
                and os.path.isfile(translation_cache.translated_path(
                    S.imdb_id, S.season, S.episode, S.source_lang,
                    source_id=source_id, tier='')))
    except Exception:
        return False


def proofread_content_ar(S):
    # A reference-tier translation of the same source under its content
    # hash (made through a byte-different link) is the one on screen: the
    # early slot's plain file must not be proofread and swapped over it.
    try:
        if not (S.local_source and os.path.isfile(S.local_source)):
            return False
        cid = content_hash(prepared_source_reader(S.local_source)())
        return bool(cid) and os.path.isfile(translation_cache.translated_path(
            S.imdb_id, S.season, S.episode, S.source_lang, source_id=cid, tier='ar'))
    except Exception:
        return False


def pool_key(S, base_hash):
    # ai_ar variants live under "<hash>_ar" so EVERY client can prefer them
    # (better quality for all) with NO worker change, and dedup-by-result
    # still prevents duplicates.
    return (base_hash + '_ar') if S.ar_on else base_hash


def prepare(S):
    """Gate on the link type; release names, gender-reference tier, pool kind."""
        # fall through to the AI logic below

    if S.kind != 'ai':
        kodi.log('resolve: unknown kind ' + str(S.kind),
                       level='WARNING')
        return None

    S.source_lang = S.payload.get('source_lang') or 'en'

    S.local_source = S.payload.get('local_path')

    # Real release name of the SOURCE subtitle being translated (carried from the
    # picked candidate). Used to (a) name the delivered Hebrew file so Kodi shows
    # the full release instead of a hash, and (b) tag the community-pool upload
    # with a real release so match-% works for everyone who downloads it -- a
    # generic "Title.Year" matches almost nothing. Falls back to the video's own
    # release name from info; token-like (debrid URL/uuid) values are dropped.
    # Keep timing provenance separate from the display/pool fallback below.
    # ``info.release`` describes the PLAYING file; substituting it for a source
    # subtitle whose release is unknown would manufacture an exact match and
    # make SubSync trust timing it never verified.
    S.timing_release = (S.payload.get('release') or '').strip()
    if video_context.looks_like_token(S.timing_release):
        S.timing_release = ''
    S.embedded_timing = bool(S.payload.get('embedded'))
    S.src_release = S.timing_release
    if not S.src_release:
        S.src_release = video_context.release_name(S.info, 'source')
    if pool_contribute is not None:
        try:
            if video_context.looks_like_token(S.src_release, True):
                S.src_release = ''
            if video_context.looks_like_token(S.timing_release, True):
                S.timing_release = ''
        except Exception:
            pass
    S.release_override = S.src_release or None

    # Arabic-gender-reference (opt-in, default OFF). When ON we operate in a
    # separate 'ar' quality tier: cache + pool live under their own key, so an
    # existing plain translation does NOT short-circuit (we re-translate to
    # upgrade it), while a finished ai_ar IS reused (no duplicate, no re-spend).
    # The Arabic sub is fetched + aligned later (only if we actually translate).
    # OFF = byte-identical to today.
    S.ar_on = False
    try:
        S.ar_on = bool(settings.get_bool('gender_ref_arabic', False))
    except Exception:
        S.ar_on = False
    S.tier = 'ar' if S.ar_on else ''
    # A cached Google Translate fallback this run sets out to replace with AI
    # (see _google_cache_upgradeable); handed back if the AI run fails. Only
    # with a live preview: a caller without one waits for resolve() to return
    # and got that file at once before, so it still does.
    S.replacing_google = None
    # Embedded-sourced translations (synced to the video's own timing) are
    # stored under 'ai_emb' so the pool can surface them distinctly. Embedded
    # wins over the ar/plain distinction for the POOL kind (the 'ai_ar' tag was
    # already collapsed to 'ai' server-side -- it only ever mattered for
    # telemetry, which is unaffected here).
    S.pool_kind = ('ai_emb' if S.payload.get('embedded')
                  else ('ai_ar' if S.ar_on else 'ai'))
    S.ar_map = None  # {srt_entry_number: reference_line}, PRIMARY ref (_ref_stack[0][1])
    S.ar_diag = {}   # gender-reference diagnostics (reason/cands/diag/lang)
    S.ref_lang = 'ar'  # PRIMARY chain language actually used (he/ar/es/fr/ru/...)
    # the saved translation's gender reference ('' none): no proofread after a
    # Hebrew one (_gp_ref_mark)
    S.gp_ref = ''
    # Reference STACK of gender oracles. [0] = primary; higher tiers are
    # ALTERNATE chain languages pulled LAZILY -- only when a chunk is prompt-
    # blocked -- so a job that never blocks downloads exactly one reference. A
    # blocked chunk is retried with the NEXT human-subtitle language (e.g.
    # Spanish after Arabic) before ever dropping the reference, preserving
    # per-line gender instead of falling straight to English-only.
    S.ref_stack = []            # [(lang, {entry_number: reference_line}), ...]
    S.ref_plan = [None]         # arabic_gender.ReferencePlan (boxed for the closure)
    S.ref_lock = threading.Lock()
    return NEXT
