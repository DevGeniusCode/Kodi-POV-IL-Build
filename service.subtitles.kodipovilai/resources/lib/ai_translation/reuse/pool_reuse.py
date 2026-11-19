# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Pool reuse: fetching a pooled translation, its quality gate, the backfill and the source picks.

import os
import time
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi
from resources.lib.ai_translation.repairs import postfix_leaks
from resources.lib.core.srt import blocks as srt_blocks, classify
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client
except Exception:
    pool_contribute = pool_client = None
from resources.lib.core.link_codec import decode_link
from resources.lib.ai_translation.source_prep import content_hash, prepare_source, prepared_source_reader


def pool_reuse_fetch(info, content_id, ar_on):
    """Whether the community pool already has a translation for THIS source hash;
    returns (path_or_None, is_ar). Consults the ALREADY-CACHED /lookup variant list
    (a cache-only peek that NEVER networks) so we only /sub-fetch a hash the pool
    actually has -- avoiding the blind "<hash>_ar then <hash>" GETs (the _ar probe
    is almost always a 404). When the list isn't cached (or lookup failed) we fall
    back to the original blind probe, so this pre-check can NEVER add a request nor
    hide a real pooled translation. `pool` is non-None (caller's use_enabled())."""
    ar_hash = content_id + '_ar'
    try:
        variants = pool_client.lookup_cached(info)   # cache-only; None = not warm/unknown
        if variants is None:
            have = None
        else:
            have = set(v.get('hash') for v in variants
                       if isinstance(v, dict) and v.get('hash'))
    except Exception:
        have = None
    if have is None:
        pooled = pool_client.fetch(info, ar_hash)
        if pooled:
            return pooled, True
        if not ar_on:
            return pool_client.fetch(info, content_id), False
        return None, False
    if ar_hash in have:
        return pool_client.fetch(info, ar_hash), True
    if (not ar_on) and (content_id in have):
        return pool_client.fetch(info, content_id), False
    return None, False


def pool_local_id(source_hash):
    """The file-name-safe id of a pooled variant's local copy: its source hash,
    or for any other key ("<hash>_ar", the Arabic-gender tier) 'h' and a hash
    of it. '' for none."""
    import hashlib as _hpool
    import re as _re_pool
    raw = (source_hash or '').strip().lower()
    if not raw:
        return ''
    if _re_pool.match(r'^[a-f0-9]{8,64}$', raw):
        return raw
    return 'h' + _hpool.sha1(raw.encode('utf-8')).hexdigest()[:16]


def pool_source_text(info, source_hash, cache_only=False):
    """Read a pool SRT from the local immutable cache, fetching it only once.

    The hash-named source is never passed through RTL rendering in place. A
    repeat pick therefore costs zero `/sub` Worker requests and can rebuild the
    display copy under newer client-side RTL rules without changing pool bytes.
    "<hash>_ar" variants are kept too (they used to be fetched on every pick):
    their gender proofread is kept in that copy (_pool_gp_base).
    Returns ``(text, stable_id)`` or ``('', '')``.
    """
    import hashlib as _hpool
    raw_hash = (source_hash or '').strip().lower()
    safe_hash = pool_local_id(raw_hash)
    if safe_hash:
        source_path = os.path.join(
            kodi.cache_dir(),
            'pool_{0}.source.srt'.format(safe_hash))
        try:
            with open(source_path, 'r', encoding='utf-8') as f:
                cached = f.read()
            if cached:
                return cached, safe_hash
        except Exception:
            pass

    if cache_only:
        return '', ''
    text = pool_client.fetch(info, raw_hash or None) if pool_contribute is not None else None
    if not text:
        return '', ''
    stable_id = safe_hash or _hpool.sha1(
        text.encode('utf-8', 'replace')).hexdigest()[:16]
    source_path = os.path.join(
        kodi.cache_dir(),
        'pool_{0}.source.srt'.format(stable_id))
    tmp = source_path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, source_path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return text, stable_id


def pool_quality_ok(src_text, final):
    """Quality gate before SHARING a translation to the community pool. Skips
    obviously-broken output so it can't pollute the pool: a truncated result
    (lost too many blocks vs the source -> failed/partial chunks) or one that
    isn't really Hebrew (translation didn't happen). NOTE: it cannot catch a
    mis-synced SOURCE -- the text is correct, only the timing differs -- so
    this raises reliability but isn't a perfect guarantee. Never blocks on a
    checker error (returns True)."""
    try:
        if not final:
            return False
        if src_text:
            src_n = srt_blocks.count_entries(src_text)
            out_n = srt_blocks.count_entries(final)
            if src_n >= 5 and out_n < src_n * 0.85:
                return False  # truncated: lost too many blocks (failed chunks)
        if not classify.looks_hebrew(final):
            return False  # not really Hebrew overall
        # Partial-failure guard: a doc can read as "Hebrew overall" yet have
        # whole chunks left in English. Reject only when a LARGE share of
        # substantial cues are English-only (no Hebrew at all) -- a generous
        # 0.30 so legitimately-English lines (lyrics, on-screen signs, an
        # English phrase, half-English lines) never trip it; only a mostly-
        # broken translation does. Mixed Hebrew+English lines count as
        # translated because they contain Hebrew.
        if classify.untranslated_line_ratio(final) > 0.30:
            return False
        # The model's working in a cue (field report, update 756): every user
        # who reuses the file would see it.
        if postfix_leaks.generated_editorial_leak_indices(
                srt_blocks.parse_blocks(src_text) if src_text else [],
                srt_blocks.parse_blocks(final)):
            return False
        return True
    except Exception:
        return True


def pool_marker(translated_path, kind):
    """One-shot '.shared' marker path for a pool contribution of `kind`.
    Embedded ('ai_emb') translations track a SEPARATE '<path>.emb2' marker
    (physical '<path>.emb2.shared') so they can UPGRADE a file already shared as
    plain 'ai'/'ai_ar' -- the Worker promotes a dedup-matched entry to 'ai_emb'
    (never downgrades). Using it at EVERY ai-translation contribute site (fresh
    upload, early-cache backfill, content-hash backfill) keeps the convention
    consistent: an embedded file seeds ONLY the '.emb2' marker, so a later
    embedded re-share is correctly one-shot (no redundant round-trip), while a
    plain entry that pre-dates it is never wrongly blocked from upgrading. Ktuvit
    mirror/harvest markers are unaffected -- they live on the downloaded sub
    files, not these translation-cache paths.

    Suffix is '.emb2', NOT '.emb': an early build (0.2.403) shipped the '.emb'
    marker together with a `_post` that still had the OLD dedup pre-check (no
    ai_emb bypass). That pre-check WROTE '<path>.emb.shared' and returned WITHOUT
    posting the ai_emb promote -- so on every title a 0.2.403 user clicked, the
    one-shot marker was set but the Worker never got the promote signal, and once
    they update, the backfill's `was_contributed('.emb')` would skip forever.
    Bumping the suffix makes those stale '.emb.shared' markers irrelevant so the
    promote fires exactly once now. (A 0.2.404 user who genuinely promoted has a
    real '.emb.shared'; they get one extra POST the Worker dedups -- harmless.)"""
    return (translated_path + '.emb2') if kind == 'ai_emb' else translated_path


def backfill_pool_async(info, translated_path, local_source, source_lang,
                         ar_tier=False, embedded=False):
    """Share an ALREADY-cached Hebrew translation to the community pool, in
    the background, the first time the user re-watches it after enabling
    pool_share. Used at the EARLY cache hit, where the source bytes (and
    therefore the content hash) aren't computed yet: we read the source on a
    daemon thread so playback is never delayed, compute the same content hash
    the fresh-translation path uses, and contribute_once (marker + server-side
    dedup => never a duplicate). One-shot per file thanks to the .shared
    marker; silent to the user on any failure.

    `embedded=True` means this cache hit came from the embedded-AI path (the
    Hebrew is synced to the video's own timing): contribute it as kind='ai_emb'
    so the pool surfaces it as "תרגום מובנה". Crucially it tracks its OWN
    one-shot marker ('<path>.emb2.shared') instead of the plain '.shared' -- so a
    file that was ALREADY shared as plain 'ai' (e.g. an earlier non-embedded run,
    or the very first embedded click that hit the cache before this fix) is NOT
    blocked, and its pool entry gets UPGRADED to 'ai_emb' server-side (the Worker
    promotes a dedup-matched 'ai' variant to 'ai_emb', never downgrades). Without
    the separate marker the '.shared' guard would swallow the upgrade and the
    embedded label would never appear on a re-click."""
    if pool_contribute is None:
        return

    def _work():
        try:
            kind = ('ai_emb' if embedded
                    else ('ai_ar' if ar_tier else 'ai'))
            # Embedded upgrades run under a distinct '.emb2' marker (see
            # _pool_marker) so an already-'ai'-shared file can still emit its one
            # ai_emb contribution.
            _marker = pool_marker(translated_path, kind)
            if not pool_client.share_enabled() or pool_contribute.was_contributed(_marker):
                return
            cached = translation_cache.load_text(translated_path)
            if not cached:
                return
            raw = None
            if local_source and os.path.isfile(local_source):
                try:
                    with open(local_source, 'r', encoding='utf-8',
                              errors='replace') as f:
                        raw = f.read()
                except (IOError, OSError):
                    raw = None
            if not raw:
                return
            prepared = prepare_source(raw)
            if not pool_quality_ok(prepared, cached):
                return
            cid = content_hash(prepared)
            _rel = None
            try:
                with open(translated_path + '.release', 'r',
                          encoding='utf-8') as _rf:
                    _rel = (_rf.read().strip() or None)
            except Exception:
                _rel = None
            pool_contribute.contribute_once(info, (cid + '_ar') if ar_tier else cid,
                                 source_lang, cached,
                                 marker_path=_marker,
                                 release_override=_rel,
                                 kind=kind)
        except Exception as e:
            try:
                kodi.log('pool backfill failed: {0}'.format(e),
                               level='DEBUG')
            except Exception:
                pass

    try:
        import threading as _t
        _t.Thread(target=_work, daemon=True).start()
    except Exception:
        pass


def embedded_source_path(base_hash):
    """This device's copy of the subtitle an embedded translation was made
    from, under its content hash (the pool's key for that translation)."""
    return os.path.join(kodi.cache_dir(),
                        'emb_src_{0}.srt'.format(base_hash))


# An external subtitle is a pooled embedded translation's source when nearly
# every Hebrew line starts within this of one of its lines once the gate has
# put it on the Hebrew's cue times. The translation keeps each source cue's
# time, and the gate's map lands a retimed subtitle within milliseconds of the
# times it was laid on; another subtitle of the same film, cut into lines its
# own way, pairs far fewer.
_EMB_SRC_PAIR_MS = 120
_EMB_SRC_MIN_PAIRED = 0.90
# The embedded path tried the list in release order until one fitted, within
# 45 s; the look tries as many in the same order, up to eight downloads (the
# viewer's subtitle quota): a week's pause after a look that found nothing
# (_gp_nosrc), six hours after one where a download failed.
_EMB_SRC_TRIES = 8
_EMB_SRC_BUDGET_S = 45.0


def _emb_source_paired(he_blocks, text):
    """`text`'s lines under the Hebrew blocks' own timing lines, each Hebrew
    line paired with the unused line of `text` starting nearest to it within
    _EMB_SRC_PAIR_MS; '' when fewer than _EMB_SRC_MIN_PAIRED of them pair."""
    import bisect
    from resources.lib.core.srt import cues as srt_cues
    src = []
    for blk in srt_blocks.parse_blocks(text):
        cue = srt_cues.parse_srt(blk)
        if cue:
            src.append((cue[0]['start'], srt_blocks.block_text_only(blk) or ''))
    src.sort(key=lambda c: c[0])
    starts = [c[0] for c in src]
    used = set()
    out = []
    timed = 0
    for blk in he_blocks:
        lines = blk.split('\n')
        cue = srt_cues.parse_srt(blk)
        if len(lines) < 2 or not cue:
            continue
        timed += 1
        h = cue[0]['start']
        j = bisect.bisect_left(starts, h - _EMB_SRC_PAIR_MS)
        best = None
        while j < len(src) and starts[j] <= h + _EMB_SRC_PAIR_MS:
            if j not in used and (best is None
                                  or abs(starts[j] - h) < abs(starts[best] - h)):
                best = j
            j += 1
        if best is not None and src[best][1]:
            used.add(best)
            out.append('\n'.join([lines[0], lines[1], src[best][1]]))
    if not timed or len(out) < _EMB_SRC_MIN_PAIRED * timed:
        return ''
    return '\n\n'.join(out) + '\n'


def _emb_source_laid(he_text, raw):
    """The external subtitle `raw` (as downloaded) on the cue times of the
    Hebrew file `he_text` when the Hebrew was translated from it -- its
    prepared lines under the Hebrew's own timing lines, as the proofread pairs
    them by start time -- else ''. As it is first (an extracted track, or a
    subtitle already on the video's clock); then put on the Hebrew's clock the
    way the embedded path put it on the track's: the SubSync gate on the
    downloaded file, its map applied, then prepared."""
    from resources.lib.core.srt import cues as srt_cues
    from resources.lib.sync.align import common, retime, verify
    he_blocks = srt_blocks.parse_blocks(he_text)
    laid = _emb_source_paired(he_blocks, prepare_source(raw))
    if laid:
        return laid
    try:
        he = srt_cues.parse_srt(he_text)
        if len(he) < common.MIN_CUES:
            return ''
        verdict = verify.verify_cues(
            [{'start': c['start'], 'end': c['end']} for c in he], raw)
        if verdict.get('status') != common.STATUS_FIXABLE:
            return ''
        # it refuses (raises) a map that would break the file
        return _emb_source_paired(he_blocks, prepare_source(
            retime.apply_verdict(raw, verdict)))
    except Exception:
        return ''


def pool_pick_emb_source(info, pool_hash, source_lang, he_path):
    """The subtitle a pooled EMBEDDED translation (ai_emb) was made from, for
    its proofread: the prepared text, '' when it is not to be had, None when
    the look failed.

    That pool kind holds two things. The video's own text track, extracted;
    or an external subtitle retimed onto that track's cue times ("embedded
    timing", _embedded_aligned_source_srt, the way tried first). Neither is in the
    list under the row's release (the video's) nor under its content hash (the
    extracted or retimed text), so _pool_pick_source never found one and these
    were never proofread after the device that made them.

    This device's own copy first (_keep_embedded_source), exact by hash. Then
    an external subtitle in the source language from the cached search, laid
    on the Hebrew's cue times (_emb_source_laid): for embedded timing that is
    the very subtitle it was made from; an extracted track pairs only with an
    external subtitle cut into the same lines. False when some laid on
    nothing and others did not download: looked for again after the
    in-progress pause."""
    base = pool_hash[:-3] if (pool_hash or '').endswith('_ar') else (pool_hash or '')
    if not base:
        return ''
    kept = embedded_source_path(base)
    if os.path.isfile(kept):
        prepared = prepared_source_reader(kept)()
        if prepared and content_hash(prepared) == base:
            return prepared
    try:
        he_text = translation_cache.load_text(he_path) or ''
        if not he_text:
            return ''
        from resources.lib.sync import service
        from resources.lib.scoring_matching import release as scoring_matching_release
        from resources.lib.fetching import bridge
        from resources.lib.fetching.embedded import streams
        # what the list's search found -- one cut at its time limit too: a
        # look for one subtitle, not a list to show
        rows = bridge.reusable_results(info)
        if not rows:
            return None     # nothing searched lately: look another time
        lang = (source_lang or 'en').strip().lower()[:2]
        cands = []
        for c in rows:
            if ((c.get('language') or '').strip().lower()[:2] != lang
                    or c.get('_engine_kind') not in (None, 'other')):
                continue
            p = decode_link(c.get('link') or '') or {}
            if p.get('type') == 'engine' and not p.get('embedded'):
                cands.append(p)
        playing = service.playing_release(info) or ''
        if playing:
            # the order the embedded path tried them in
            cands.sort(key=lambda p: scoring_matching_release.match_pct(
                playing, p.get('filename') or ''), reverse=True)
        if not cands:
            return ''
        read = failed = 0
        until = time.monotonic() + _EMB_SRC_BUDGET_S
        for p in cands[:_EMB_SRC_TRIES]:
            if read and time.monotonic() > until:
                break
            path = streams.download(p, for_delivery=False)
            if not path or not os.path.isfile(path):
                failed += 1
                continue
            read += 1
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                laid = _emb_source_laid(he_text, f.read())
            if laid:
                return laid
        if failed:
            # the one that did not come through may be the source: no week's
            # pause -- but no look at every pick either (the others laid again
            # each time): the in-progress pause, six hours
            return None if not read else False
    except Exception as e:
        kodi.log('pool pick: embedded source lookup failed: {0}'.format(e),
                       level='DEBUG')
        return None
    return ''


def pool_pick_source(info, pool_hash, release):
    """The subtitle a pooled translation was translated FROM, found in the
    engine's cached search (the list the viewer just picked from): the same
    release name, then the same content hash the pool keys it by. The
    prepared text; '' when it is not there, None when the look failed."""
    base = pool_hash[:-3] if (pool_hash or '').endswith('_ar') else (pool_hash or '')
    if not base or not release:
        return ''
    try:
        from resources.lib.fetching import bridge
        from resources.lib.fetching.embedded import streams
        rows = bridge.search(info, modal_progress=False,
                                         cache_only=True)
        if rows is None:
            return None     # nothing cached: no look, so no week's pause
        tried = read = 0
        for c in rows:
            p = decode_link(c.get('link') or '') or {}
            if p.get('type') != 'engine' or p.get('filename') != release:
                continue
            tried += 1
            if tried > 2:
                break
            path = streams.download(p, for_delivery=False)
            if not path or not os.path.isfile(path):
                continue
            read += 1
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                prepared = prepare_source(f.read())
            if content_hash(prepared) == base:
                return prepared
        if tried and not read:
            return None     # no download came through: no look either
    except Exception as e:
        kodi.log('pool pick: source lookup failed: {0}'.format(e),
                       level='DEBUG')
        return None
    return ''
