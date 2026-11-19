# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Subtitle list stages: the AI-translation rows (cached, pooled, embedded, per source language), their
# sorting, the Hebrew-skip rule, the final ordering and the empty-list message.

import os
from functools import partial
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi, settings
from resources.lib.core.link_codec import decode_link, encode_link
from resources.lib.scoring_matching.badges import finalize
from resources.lib.scoring_matching.ranking import gender_src_rank, is_same_source, is_sdh_ext, lang_display, lang_display_he, match_pct
from resources.lib.ai_translation.policy import embedded_translation_policy
from resources.lib.ai_translation.source_prep import source_id_for_ai
from resources.lib.fetching.embedded.extraction import playing_video_url
from resources.lib.fetching.search.engine import lang_rank
from resources.lib.fetching.search.rows import clean, pool_release
from resources.lib.fetching.search.state import NEXT


def emb_ok(S, v):
    # Embedded variant -> eligible ONLY for its own (exact) source.
    if (v.get('kind') or '') != 'ai_emb':
        return True
    return is_same_source(S.video_ref, pool_release(v))


def ai_sort_key(S, v):
    rel = pool_release(v)
    pct = match_pct(S.video_ref, rel) if rel else 0
    is_emb = (v.get('kind') or '') == 'ai_emb'
    # Embedded items lead (0). Among the (tied at 100%) embedded items for
    # THIS source, order by the source language's gender strength -- the most
    # gender-accurate first (strong-gender sources before English) -- then a
    # deterministic tie-break on source lang. Regular AI items keep ordering
    # by match % (g held constant so it's a no-op for them).
    # Normalise the source lang ONCE, the same way the rank and the label do
    # (default 'en', region-strip, 2-letter), so the tie-break agrees with
    # what's shown and ranked -- a missing source_lang ties with explicit
    # 'en', and 'pt-BR'/'pt-PT' tie-break identically.
    src = (v.get('source_lang') or 'en').strip().lower()[:2]
    g = gender_src_rank(src) if is_emb else 1
    return (0 if is_emb else 1, g, -pct, src)


def ai_variants(S):
    """The AI variants (cached / pooled) and their sorting."""
    # AI pool (machine translations) -- below all the human Ktuvit entries.
    # EMBEDDED-sourced translations (kind='ai_emb') are synced to ONE specific
    # source's OWN timing, so they are surfaced as "תרגום מובנה" ONLY for that
    # exact source (release). For any other release they are hidden entirely --
    # a viewer on a different source can create their own embedded translation
    # for THEIR file (which then pools for that source). An exact-source
    # embedded item leads the AI list and shows NO match-% (it IS your release,
    # so a "%" would be redundant/confusing). Regular AI variants order by %.
    S.ai_variants = [v for v in S.pool_variants
                    if (v.get('kind') or 'ai') != 'ktuvit']

    for v in sorted(S.ai_variants, key=partial(ai_sort_key, S)):
        if not emb_ok(S, v):
            continue   # embedded translation for a DIFFERENT source -> hidden
        S.have_hebrew = True
        release = pool_release(v)
        if (v.get('kind') or '') == 'ai_emb':
            # Embedded is surfaced ONLY for the EXACT source (_emb_ok ->
            # TIER_EXACT), so it is a 100% match by definition -> show 100%. Also
            # name the SOURCE language it was translated FROM: among several
            # embedded translations of the same release the list is ordered by
            # that language's gender accuracy (strong-gender first), so showing it
            # makes the order legible ("מובנה AI (ספרדית)" ranks above "(אנגלית)").
            _slh = lang_display_he((v.get('source_lang') or 'en').strip().lower()[:2])
            _emb_base = 'תרגום מובנה AI ({0}) · מאגר קהילתי · 100%'.format(_slh)
            label = ('{0}  —  {1}'.format(_emb_base, release) if release else _emb_base)
            pct = 100
        else:
            pct = match_pct(S.video_ref, release) if release else 0
            # Only show a % when we actually have a meaningful match (a 0% almost
            # always means we couldn't read the video's release name, not a real
            # zero -- showing "0%" is misleading).
            if release and pct > 0:
                label = 'תרגום AI · מאגר קהילתי · {0}%  —  {1}'.format(pct, release)
            elif release:
                label = 'תרגום AI · מאגר קהילתי  —  {0}'.format(release)
            else:
                label = 'תרגום AI · מאגר קהילתי'
        S.results.append({
            'filename': label,
            'language': 'he',
            'link': encode_link({
                'type': 'pool', 'hash': v.get('hash'),
                'release': release or '',
                'pool_kind': v.get('kind') or 'ai',
                'source_lang': v.get('source_lang') or '',
            }),
            'sync': 'false', 'rating': '5',
            'is_hi': False, 'is_hd': False,
            'pct': pct if (release and pct > 0) or (v.get('kind') or '') == 'ai_emb' else None,
        })

    # Machine-translated Hebrew from the engine.
    for c in S.engine_mt:
        S.have_hebrew = True
        S.results.append(clean(c))

    # Foreign-language engine results. With AI translation ON (default) each
    # becomes a single "translate to Hebrew" action (pick it -> get Hebrew,
    # like DarkSubs auto_translate). With translation_mode = 'none' (the user
    # opted out of AI) we hand back the RAW foreign sub instead -- no AI is
    # ever invoked. Grouped/ordered by source language (en, es, de, fr, pt,
    # then the rest); within a language, best match % first.
    S.ai_translation_on = (settings.get_setting('translation_mode', 'ai')
                         or 'ai') != 'none'
    return NEXT


def ai_rows(S):
    """The AI translation rows per source language and the embedded ones."""
    # Group BOTH the foreign AI-translate sources AND the embedded (built-in)
    # foreign streams by language, then emit language by language (en, es, de,
    # fr, pt, then the rest). Within each language the EMBEDDED track comes
    # FIRST (top of that language's group), then the rest by best match %. So
    # built-in subtitles head their own language instead of all being dumped at
    # the very bottom -- while Hebrew (handled above) still leads the whole list.
    _ai_by_lang = {}
    for c in S.engine_other:
        _ai_by_lang.setdefault(c.get('language') or '?', []).append(c)
    _emb_by_lang = {}
    for c in S.embedded_foreign:
        _emb_by_lang.setdefault(c.get('language') or '?', []).append(c)
    _emb_policy = embedded_translation_policy()
    _emb_mode_available = _emb_policy['enabled']
    if _emb_policy['mode'] == 'local_only':
        _emb_url = playing_video_url(S.info)
        _emb_mode_available = bool(
            _emb_url
            and not _emb_url.lower().startswith(('http://', 'https://')))

    # Embedded FOREIGN tracks as "translate the embedded (perfectly-synced)
    # track to Hebrew" actions. The embedded cue timings ARE the video's own, so
    # the Hebrew we produce is synced with NO re-sync -- these therefore rank
    # right after every Hebrew option and ABOVE all external subs. English first
    # (best AI source; gender still comes from the reference chain), then
    # es/de/fr/pt, then the rest. Only when AI translation is on (opt-out users
    # keep the raw embedded stream in its language group below) and the chosen
    # Advanced mode permits it. local_only deliberately hides this action for a
    # live HTTP/debrid stream instead of presenting a row that cannot run.
    # resolve() re-checks and fail-opens if a track is non-text or unreadable.
    if S.ai_translation_on and S.embedded_foreign and _emb_mode_available:
        # Don't offer the LOCAL "translate embedded -> Hebrew" generator for a
        # source language the community pool ALREADY holds an embedded (ai_emb)
        # translation for THIS EXACT release: that pooled copy is surfaced above
        # as an instant "תרגום מובנה AI · מאגר קהילתי · 100%" item, so re-running
        # the local extract+AI pipeline would redo the whole thing for a result
        # already one click away. Same-source only -- _emb_ok already hides an
        # ai_emb from a different release, so it can never suppress the generator
        # for a release the pool cannot actually serve. (_ai_variants is [] when
        # the pool is disabled, so this never suppresses in that case.)
        def _pool_has_emb(_sl):
            _sl = (_sl or 'en').strip().lower()[:2]
            for _v in S.ai_variants:
                if ((_v.get('kind') or '') == 'ai_emb' and emb_ok(S, _v)
                        and (_v.get('source_lang') or 'en').strip().lower()[:2] == _sl):
                    return True
            return False
        _emb_ai_seen = set()
        for c in sorted(S.embedded_foreign,
                        key=lambda x: (lang_rank(S, x.get('language') or '?'),
                                       x.get('language') or '?')):
            code = c.get('language') or ''
            if (not code or code in _emb_ai_seen
                    or code in ('?', 'und', 'mis', 'zxx')):
                continue
            _emb_ai_seen.add(code)
            if _pool_has_emb(code):
                continue   # instant same-release pool copy already listed above
            S.have_hebrew = True
            # The real Kodi stream index lives INSIDE this candidate's own
            # engine link payload (not a top-level key), so decode it out. Carry
            # it on the embedded_ai link so the pick can show this embedded track
            # NATIVELY (instant, already synced) while the Hebrew is extracted+
            # translated in the background.
            _emb_src = decode_link(c.get('link') or '') or {}
            S.results.append({
                'filename': 'תרגום מובנה → עברית (AI) · {0}'.format(
                    lang_display(code)),
                'language': 'he',
                'link': encode_link({'type': 'embedded_ai',
                                      'src_lang': code,
                                      'stream_index': _emb_src.get(
                                          'stream_index')}),
                'sync': 'true',
                'rating': '5', 'is_hi': False, 'is_hd': False, 'pct': None,
            })

    for code in sorted(set(_ai_by_lang) | set(_emb_by_lang),
                       key=lambda l: (lang_rank(S, l), l)):
        # Built-in (embedded) track of this language -> top of its group.
        for c in _emb_by_lang.get(code, []):
            S.results.append(clean(c))
        # Then the foreign subs of this language. Annotate each with SDH-ness
        # (a whole-token 'SDH' release marker, or a content-detected release --
        # NOT the provider's unreliable hearing-impaired flag), then order SDH
        # FIRST -- an SDH sub has the complete dialogue + speaker
        # labels, the best source for AI gender accuracy -- and within that by
        # best match %. Decode the engine link ONCE here (reused below).
        _lang_cands = []
        for c in _ai_by_lang.get(code, []):
            _s = decode_link(c.get('link') or '') or {}
            _sdh = is_sdh_ext(c, _s.get('filename') or '')
            _lang_cands.append((c, _s, _sdh))
        _lang_cands.sort(key=lambda t: (not t[2], -t[0].get('_pct', 0)))
        for c, src, _sdh in _lang_cands:
            pct = c.get('_pct', 0)
            if S.ai_translation_on:
                if not src or src.get('type') != 'engine':
                    continue
                src = dict(src)
                src['type'] = 'engine_ai'
                src['src_lang'] = code
                rel = src.get('filename') or code
                S.have_hebrew = True
                # SDH items are tagged so the user knows they're the best pick for
                # זכר/נקבה accuracy (complete dialogue + speaker labels).
                _label = ('תרגום AI לעברית · SDH (מדויק למגדר) · {0}%  —  {1}'
                          if _sdh else
                          'תרגום AI לעברית · {0}%  —  {1}').format(pct, rel)
                S.results.append({
                    'filename': _label,
                    'language': code,
                    'link': encode_link(src),
                    'sync': 'false',
                    'rating': c.get('rating', '3'),
                    # NOT is_hi: the DELIVERED Hebrew is plain dialogue (the HI
                    # brackets/sound cues are stripped before translation), so
                    # Kodi's hearing_imp badge would mislabel it. The SDH signal
                    # for the user is the label text above, not this flag.
                    'is_hi': False, 'is_hd': False, 'pct': pct,
                })
            else:
                # Opt-out: deliver the raw foreign sub as-is (SDH still sorts first).
                S.results.append(clean(c))
    return NEXT


def ai_entries(S):
    """Hebrew skip rule and the per-language AI entries."""
    skip_when_hebrew = settings.get_bool('skip_if_hebrew', True)
    if S.have_hebrew and skip_when_hebrew:
        return finalize(S.info, S.results)

    # 2. For each enabled source language, surface ONE "translate
    #    this" entry from a local source:
    #       (a) alongside file (local re-watch)
    #       (b) temp-dir file (loaded by another addon, e.g. DarkSubs)
    #    Built into a separate list so cache hits can be sorted to
    #    the top of the AI section (just under Hebrew passthrough).
    S.ai_entries = []
    seen_langs = set()
    for src_lang in (S.sources if S.ai_translation_on else []):
        if src_lang in seen_langs:
            continue

        local_path = S.alongside.get(src_lang) or S.in_temp.get(src_lang)
        if local_path:
            seen_langs.add(src_lang)
            source_label = lang_display(src_lang)
            source_origin = ('local file' if S.alongside.get(src_lang)
                             else 'loaded by another addon')
            S.ai_entries.append({
                'filename': 'AI Hebrew (translate {0} {1})'.format(
                    source_label, source_origin),
                'language': 'he',
                'link': encode_link({
                    'type': 'ai',
                    'source_lang': src_lang,
                    'local_path': local_path,
                }),
                'sync': 'false',
                'rating': '4' if src_lang == 'en' else '3',
                'is_hi': False, 'is_hd': False, 'pct': None,
                '_payload': {'source_lang': src_lang,
                             'local_path': local_path},
            })
            continue

    # Mark cached entries with a visible label and sort them to the
    # top of the AI section so a returning user picks the
    # already-translated copy first (instant) instead of re-paying
    # for translation by clicking a fresh source.
    for entry in S.ai_entries:
        payload = entry.pop('_payload', {})
        try:
            src_id = source_id_for_ai(payload)
            if src_id:
                # Same tier as the DOWNLOAD path uses (_try_fast_download,
                # also untiered) -- deliberately, and NOT the same tier as
                # resolve(), which pins to _tier and is usually 'ar'. Marking
                # an entry [CACHE] that the download path then cannot serve is
                # worse than not marking it: the label would promise an
                # instant result and the user would get the English fallback.
                translated = translation_cache.translated_path(
                    S.imdb_id, S.season, S.episode,
                    payload.get('source_lang') or 'en',
                    source_id=src_id)
                if os.path.isfile(translated):
                    entry['is_cached'] = True
                    entry['rating'] = '5'
                    entry['sync'] = 'true'
                    entry['filename'] = '[CACHE] ' + entry['filename']
        except Exception as e:
            kodi.log('cache marker check failed: {0}'.format(e),
                           level='DEBUG')
    return NEXT


def finish(S):
    """The final ordering, the labels and the empty-list log line."""
    S.ai_entries.sort(key=lambda e: 0 if e.get('is_cached') else 1)
    S.results.extend(S.ai_entries)

    if not S.results:
        kodi.log('list_candidates returned empty: ' + repr(
            {'imdb_id': S.imdb_id, 'tmdb_id': S.tmdb_id,
             'alongside_count': len(S.alongside),
             'in_temp_count': len(S.in_temp)}),
            level='WARNING')

    return finalize(S.info, S.results)
