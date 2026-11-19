# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# AI stages, 2 of 10 -- cache: preferred-language gate, the two cache lookups (early slot, content hash),
# the source subtitle read and cleaned, the community-pool reuse; the proofread of a cached translation.

import os
import time
from functools import partial
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi, settings
from resources.lib.core.srt import classify
from resources.lib.ai_translation.dispatch.pacing import is_mostly_hebrew
from resources.lib.ai_translation.proofread.gp_jobs import pool_proofread_text, pool_variant_proofread
from resources.lib.ai_translation.proofread.gp_run import proofread_due, proofread_opened
from resources.lib.ai_translation.proofread.gp_state import gp_done, gp_hebrew_ref, gp_mark, gp_marker, gp_models, gp_ref_mark, gp_ref_tier, gp_saved, gp_trying
from resources.lib.ai_translation.providers.google_flow import clear_google_marker, google_cache_upgradeable, is_google_translated
from resources.lib.ai_translation.reuse.pool_reuse import backfill_pool_async, pool_marker, pool_quality_ok, pool_reuse_fetch
from resources.lib.ai_translation.source_prep import content_hash, prepare_source, prepared_source_reader, source_id_for_ai
from resources.lib.delivery.rtl import reapply_rtl_fix_in_place
from resources.lib.ai_translation.job.ai.prepare import deliver, pool_key, proofread_content_ar, proofread_plain_tier
from resources.lib.ai_translation.job.state import NEXT, translation_cancelled
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None


def open_with_proofread(S, path, source_text, source_id, base_hash,
                         pooled_hash=None):
    """A cached or pooled translation opened from the list: the gender
    proofread, once (see _proofread_opened). None when none is due --
    the caller then delivers as before."""

    def held_hash():
        # The pool keys this translation under its source hash, "_ar" for
        # the Arabic-gender tier; the one it holds is the one to replace.
        if pooled_hash or not base_hash:
            return pooled_hash
        found = pool_key(S, base_hash)
        try:
            if pool_contribute is not None and (pool_client.use_enabled() or pool_client.share_enabled()):
                held = set(v.get('hash') for v in pool_client.lookup(S.info) or []
                           if isinstance(v, dict))
                tiers = (base_hash + '_ar', base_hash) if S.ar_on else (base_hash,)
                found = next((h for h in tiers if h in held), found)
        except Exception:
            pass
        return found

    def share_to(pool_hash):
        def share(text, stats=None):
            if pool_quality_ok(source_text, text):
                pool_contribute.contribute_once(
                    S.info, pool_hash, S.source_lang, text,
                    marker_path=gp_marker(path),
                    release_override=S.release_override, kind=S.pool_kind,
                    proofread=stats or True)
        return share

    # Proofread here before, its pool upload never went through (the
    # worker answered 502): offered again, with no new request -- not
    # one made with a Hebrew reference and proofread before 0.3.72 by
    # the models of then (gemini-4-hydrogen's proofread of one, 0.3.88,
    # is offered like any other).
    _made_he = gp_hebrew_ref(path, S.info if gp_ref_tier(path, pooled_hash)
                              else None)
    if (gp_done(path) and source_text and pool_contribute is not None
            and pool_client.share_enabled()
            and not pool_contribute.was_contributed(gp_marker(path))
            and not (_made_he and gp_saved(path).get('model')
                     not in gp_models(True))):
        pool_hash = held_hash()
        if pool_hash:
            share_to(pool_hash)(translation_cache.load_text(path) or '', gp_saved(path))
        return None
    if not proofread_due(path, source_text, S.progressive_cb, held=True,
                          info=(S.info if gp_ref_tier(path, pooled_hash)
                                else None)):
        return None
    # In progress elsewhere, or waiting out a reply of no use: no request
    # -- but another viewer's proofread in the pool is still taken.
    asking = not gp_trying(path)
    if pooled_hash and pool_variant_proofread(S.info, pooled_hash):
        gp_mark(path)      # just fetched, and proofread already
        return None
    pool_hash = held_hash()
    # Someone proofread this source already: theirs, no request.
    if pool_contribute is not None and pool_hash and pool_client.use_enabled():
        text = pool_proofread_text(S.info, pool_hash)
        if text and pool_quality_ok(source_text, text):
            try:
                translation_cache.save_text(path, text)
                reapply_rtl_fix_in_place(path, source=source_text,
                                          source_lang=S.source_lang)
                gp_mark(path)
                kodi.log('gender proofread: the pool holds this '
                               'translation proofread -- taken', level='INFO')
                return deliver(S, path)
            except Exception as e:
                kodi.log('pool proofread copy save failed: '
                               '{0}'.format(e), level='WARNING')
    if not asking:
        return None
    share = None
    if pool_contribute is not None and pool_hash and pool_client.share_enabled():
        share = share_to(pool_hash)
    return proofread_opened(path, source_text,
                             S.steps_source_id or source_id, S.src_release,
                             S.progressive_cb, partial(deliver, S),
                             partial(translation_cancelled, S), S.info, share=share,
                             shown_already=S.proofread_only,
                             later=({'hash': pool_hash, 'lang': S.source_lang,
                                     'release': S.release_override or '',
                                     'kind': S.pool_kind,
                                     'ref_hash': pooled_hash}
                                    if pool_hash else None),
                             hebrew_ref=_made_he)


def cache(S):
    """Preferred-language gate, cache lookups, source read, pool reuse."""
    # Respect the user's preferred subtitle language: if they've chosen a
    # specific non-Hebrew language (e.g. English) DON'T force an AI Hebrew
    # translation -- hand back the SOURCE subtitle untranslated so they get
    # the language they asked for. Checked BEFORE the cache lookups below so
    # we never serve a previously-cached Hebrew file either. This is an
    # extra gate only; it can't re-enable translation that auto_translate /
    # force_ai_when_auto_translate_off already disabled.
    if not S.payload.get('force_ai') and not settings.hebrew_subtitle_wanted():
        if S.proofread_only:
            return None
        kodi.log(
            'resolve: preferred subtitle language is not Hebrew; returning '
            'the source subtitle untranslated', level='INFO')
        kodi.notify(
            'AI: שפת הכתוביות המועדפת אינה עברית — מחזיר כתובית מקור ללא תרגום',
            time_ms=4000)
        # Never hand back nothing: an untranslated source subtitle is far
        # better than no subtitle at all.
        if S.local_source and os.path.isfile(S.local_source):
            return S.local_source
        return None

    # Two-tier cache strategy:
    #  1. EARLY lookup: the local path is hashed by content (cheap
    #     because the file is small). This avoids a redundant
    #     re-translation for entries the user already translated.
    #     Same key the [CACHE] marker in list_candidates uses.
    #  2. CONTENT-HASH lookup after the source is in memory: catches
    #     the rare case where two different local paths point to
    #     byte-identical SRTs.
    S.early_source_id = source_id_for_ai(S.payload)
    if S.early_source_id:
        # TIER-PINNED, and left that way ON PURPOSE after three review rounds.
        #
        # Widening this to find a translation in EITHER tier looks obviously
        # right and is not. Two things break:
        #   * This early return fires BEFORE the first progressive_cb (see
        #     first_ready below), so nothing downstream is told. The picker
        #     handler reads the return only as `if not _resolved:` to decide
        #     whether to toast a failure -- a cache HIT is truthy, so it does
        #     nothing at all and the viewer keeps the English fallback. Turning
        #     a miss (which falls through to the full path and delivers via
        #     'done') into a hit is therefore a straight REGRESSION.
        #   * _backfill_pool_async below is told the tier by _ar_on, the
        #     setting's value today -- not by the file we found. A plain file
        #     found while the setting is on would upload as the ai_ar variant,
        #     which :3414 says must never happen, and the Worker dedup makes it
        #     permanent for every other user.
        # Fixing the auto-load symptom properly means wiring these early
        # returns to progressive_cb. That is its own change with its own
        # review, not a one-line lookup swap.
        if (proofread_plain_tier(S, S.early_source_id)
                and not proofread_content_ar(S)):
            S.ar_on, S.tier = False, ''
            S.pool_kind = 'ai_emb' if S.payload.get('embedded') else 'ai'
        S.translated = translation_cache.translated_path(
            S.imdb_id, S.season, S.episode, S.source_lang,
            source_id=S.early_source_id, tier=S.tier)
        # Only honour the cache if it's a REAL Hebrew translation. Older buggy
        # versions could cache an empty / source-echoed file and then serve it
        # forever as "from cache (previous translation)" -- blank or foreign
        # text. If the cached file isn't really Hebrew, delete it and re-do.
        if os.path.isfile(S.translated) and not is_mostly_hebrew(
                translation_cache.load_text(S.translated) or ''):
            kodi.log('Discarding non-Hebrew cached translation (empty/'
                           'echoed): ' + S.translated, level='WARNING')
            try:
                os.remove(S.translated)
            except OSError:
                pass
            gp_ref_mark(S.translated, None)
        if (S.progressive_cb is not None and S.local_source
                and os.path.isfile(S.local_source)
                and google_cache_upgradeable(S.translated)):
            kodi.log('Cached file is a Google fallback and AI is available '
                           'again -- translating with AI: ' + S.translated,
                           level='INFO')
            S.replacing_google = S.translated
        elif os.path.isfile(S.translated):
            kodi.log('Cache hit (early): ' + S.translated,
                           level='INFO')
            if not S.proofread_only:
                kodi.notify(
                    'AI: כתוביות מ-cache (תרגום קודם)',
                    time_ms=4000)
            try:
                now = time.time()
                os.utime(S.translated, (now, now))
            except OSError:
                pass
            reapply_rtl_fix_in_place(
                S.translated, source_lang=S.source_lang,
                source=(prepared_source_reader(S.local_source)
                        if S.local_source else None))
            # Proofread once; its pool upload stands in for the backfill.
            _early_src = (prepared_source_reader(S.local_source)()
                          if S.progressive_cb is not None and S.local_source
                          and os.path.isfile(S.local_source) else '')
            _opened = open_with_proofread(S,
                S.translated, _early_src, S.early_source_id,
                content_hash(_early_src) if _early_src else '')
            if _opened or S.proofread_only:
                return _opened
            # Backfill: share this previously-translated file to the pool the
            # first time it's re-watched after pool_share is on. Runs on a
            # daemon thread (reads the source to compute the content hash), so
            # the cache hit still returns instantly. One-shot per file.
            if (pool_contribute is not None and pool_client.share_enabled()
                    and not is_google_translated(S.translated)):
                backfill_pool_async(S.info, S.translated, S.local_source,
                                     S.source_lang, ar_tier=S.ar_on,
                                     embedded=(S.pool_kind == 'ai_emb'))
            return deliver(S, S.translated)

    # Read the source SRT recorded at list time (alongside the video
    # or a temp-dir file loaded by another addon, e.g. DarkSubs).
    S.src_text = None
    if S.local_source and os.path.isfile(S.local_source):
        try:
            with open(S.local_source, 'r', encoding='utf-8',
                      errors='replace') as f:
                S.src_text = f.read()
        except (IOError, OSError):
            S.src_text = None
    if not S.src_text:
        if S.replacing_google and os.path.isfile(S.replacing_google):
            return deliver(S, S.replacing_google)
        if not S.proofread_only:
            kodi.notify(
                'מקור הכתוביות לא נמצא — בחר שוב',
                time_ms=5000,
            )
        return None

    # Phase 3: learn SDH-ness from the RAW source text (BEFORE _prepare_source
    # strips the HI annotations the classifier looks for), so a future ranking
    # of the same release can prefer + label it -- the text isn't available when
    # the subtitle list is built. Conservative (zero false positives) and fully
    # best-effort: a miss just means no future hint.
    try:
        if S.src_release and classify.is_sdh_content(S.src_text):
            from resources.lib.ai_translation.reuse import sdh_registry, sdh_pool
            from resources.lib.scoring_matching import release as scoring_matching_release
            sdh_registry.record_sdh(scoring_matching_release.normalize(S.src_release))
            sdh_pool.contribute_sdh(S.src_release)   # Phase 3b: share it (share-gated)
    except Exception:
        pass

    # Strip hearing-impaired noise BEFORE translation. Source SRTs
    # often have things like "[breathing heavily]" / "(music plays)"
    # / "MABEL: ..." that aren't speech we want translated; they
    # just clutter the Hebrew output. Skipped if the cleaner ate
    # the entire file (it won't, but defend against it).
    # The gender-reference aligner measures the clock on the cues as they
    # were authored; merged captions keep the same timecodes, so its hints
    # still land on the right merged line.
    S.timing_src = prepare_source(S.src_text, merge=False)
    S.src_text = prepare_source(S.src_text)
    if S.timing_src == S.src_text:
        S.timing_src = None

    # Content-hash lookup: only catches a hit when SOURCE bytes
    # match a previously translated SRT served from a different
    # url/path. Translation is saved to the early-source-id slot
    # (so list_candidates can pre-mark it as [CACHE]) and ALSO
    # to the content-hash slot so a future click of a different
    # url with identical content also hits cache.
    S.content_id = content_hash(S.src_text)
    if S.content_id != S.early_source_id:
        if proofread_plain_tier(S, S.content_id):
            S.ar_on, S.tier = False, ''
            S.pool_kind = 'ai_emb' if S.payload.get('embedded') else 'ai'
        translated_by_content = translation_cache.translated_path(
            S.imdb_id, S.season, S.episode, S.source_lang,
            source_id=S.content_id, tier=S.tier)
        if os.path.isfile(translated_by_content) and not is_mostly_hebrew(
                translation_cache.load_text(translated_by_content) or ''):
            kodi.log('Discarding non-Hebrew cached translation (empty/'
                           'echoed): ' + translated_by_content,
                           level='WARNING')
            try:
                os.remove(translated_by_content)
            except OSError:
                pass
            gp_ref_mark(translated_by_content, None)
        if (S.progressive_cb is not None
                and google_cache_upgradeable(translated_by_content)):
            kodi.log('Cached file is a Google fallback and AI is available '
                           'again -- translating with AI: ' + translated_by_content,
                           level='INFO')
            S.replacing_google = translated_by_content
        elif os.path.isfile(translated_by_content):
            kodi.log(
                'Cache hit (content): ' + translated_by_content,
                level='INFO')
            if not S.proofread_only:
                kodi.notify(
                    'AI: כתוביות מ-cache (זהה לתרגום קיים)',
                    time_ms=4000)
            try:
                now = time.time()
                os.utime(translated_by_content, (now, now))
            except OSError:
                pass
            reapply_rtl_fix_in_place(translated_by_content, source=S.src_text,
                                      source_lang=S.source_lang)
            _opened = open_with_proofread(S,
                translated_by_content, S.src_text,
                S.early_source_id or S.content_id, S.content_id)
            if _opened or S.proofread_only:
                return _opened
            # Backfill: we already have the content hash here, so share the
            # cached file directly (contribute_once = marker + server dedup).
            if (pool_contribute is not None and pool_client.share_enabled()
                    and not is_google_translated(translated_by_content)):
                _cached_he = translation_cache.load_text(translated_by_content) or ''
                if pool_quality_ok(S.src_text, _cached_he):
                    if S.src_release:
                        try:
                            with open(translated_by_content + '.release', 'w',
                                      encoding='utf-8') as _rf:
                                _rf.write(S.src_release)
                        except OSError:
                            pass
                    try:
                        pool_contribute.contribute_once(
                            S.info, pool_key(S, S.content_id), S.source_lang,
                            _cached_he,
                            marker_path=pool_marker(translated_by_content,
                                                     S.pool_kind),
                            release_override=S.release_override,
                            kind=S.pool_kind)
                    except Exception as e:
                        kodi.log(
                            'pool backfill (content) failed: {0}'.format(e),
                            level='DEBUG')
            return deliver(S, translated_by_content)

    if S.proofread_only:
        return None     # nothing cached to proofread; it never translates

    # No hit: settle on the early-source-id slot as the canonical
    # cache path for this translation; falls back to content_id
    # when we have no stable source_id at all.
    S.translated = translation_cache.translated_path(
        S.imdb_id, S.season, S.episode, S.source_lang,
        source_id=(S.early_source_id or S.content_id), tier=S.tier)

    # Stash the source release name next to the cached translation, so a later
    # "share my cached translations" upload can tag it with the real release too
    # (the cache filename itself is only id+hash). Best-effort.
    if S.src_release:
        try:
            with open(S.translated + '.release', 'w', encoding='utf-8') as _rf:
                _rf.write(S.src_release)
        except OSError:
            pass

    # Community pool: before spending Gemini quota, check whether someone has
    # already translated THIS exact source (same content hash) and shared it.
    # Exact-hash match only -> perfect sync. Gated by pool_use; on any failure
    # we fall through and translate normally. Returns a path like a cache hit
    # (no progressive callbacks -- the caller's sentinel handles that).
    if pool_contribute is not None and pool_client.use_enabled():
        # Prefer the higher-quality Arabic-gender variant for EVERYONE (it lives
        # under "<hash>_ar"). When the feature is ON we accept ONLY ai_ar -- if
        # the pool has just a plain one, we deliberately re-translate to upgrade
        # it. When OFF we take ai_ar if present, else plain.
        pooled, _pooled_ar = pool_reuse_fetch(S.info, S.content_id, S.ar_on)
        if pooled and _pooled_ar:
            kodi.log('pool: reusing Arabic-gender (ai_ar) variant',
                           level='INFO')
        if pooled:
            try:
                translation_cache.save_text(S.translated, pooled)
                clear_google_marker(S.translated)
                # a plain variant had no reference; an "_ar" one had one of
                # a language not known
                gp_ref_mark(S.translated, '?' if _pooled_ar else '')
                reapply_rtl_fix_in_place(S.translated, source=S.src_text,
                                          source_lang=S.source_lang)
                kodi.notify(
                    'AI: כתוביות מהמאגר הקהילתי (לא נדרש תרגום)', time_ms=4000)
                _opened = open_with_proofread(S,
                    S.translated, S.src_text, S.early_source_id or S.content_id,
                    S.content_id,
                    pooled_hash=(S.content_id + '_ar') if _pooled_ar else S.content_id)
                if _opened:
                    return _opened
                return deliver(S, S.translated)
            except Exception as e:
                kodi.log('pool reuse save failed: {0}'.format(e),
                               level='WARNING')
    return NEXT
