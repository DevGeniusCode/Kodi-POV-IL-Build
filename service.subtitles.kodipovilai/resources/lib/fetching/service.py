# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The subtitle list (list_candidates): local files, embedded streams, providers, the community pool and
# the AI-translation rows, ranked and labelled for the dialogs. SEARCH side of the pipeline.

import json
import os
import urllib.parse
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi, settings
from resources.lib.fetching import local
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None
from resources.lib.core.link_codec import _decode_link, _encode_link
from resources.lib.scoring_matching.badges import _finalize
from resources.lib.scoring_matching.ranking import _gender_src_rank, _is_same_source, _is_sdh_ext, _lang_display, _lang_display_he, _looks_like_token, _match_pct, _release_from_path
from resources.lib.ai_translation.policy import _embedded_translation_policy, _enabled_sources
from resources.lib.ai_translation.source_prep import _source_id_for_ai
from resources.lib.fetching.embedded.extraction import _playing_video_url


def _clean(c):
    c.pop('_engine_kind', None)
    c.pop('_pct', None)
    c.pop('_is_mt', None)
    return c


def _norm_rel(s):
    import re as _re_nr
    return _re_nr.sub(r'[^a-z0-9]', '', (s or '').lower())


def _pool_release(v):
    r = (v.get('release') or '').strip()
    # Reject debrid URL / token "releases" stored by older shares.
    return '' if (r and _looks_like_token(r)) else r


def list_candidates(info, modal_progress=True, quick=False):
    """Build the list Kodi's subtitle dialog will render.

    quick=True makes no network request: the provider search is used only
    when it is already cached and the community pool is skipped; info gets
    '_list_pending' = True when either was left out, so the caller can
    complete the list later (the "בחירת כתוביות" window opens on this one).

    Returns a list of dicts with keys: filename, language, link,
    sync, rating. Empty list if nothing plausible is available.
    """
    # The user's preferred subtitle language used to be able to switch this
    # whole add-on off: the gate returned [] -- not "no AI entries" as the old
    # message claimed, but NO ENTRIES AT ALL, including the plain Hebrew subs
    # and the shared pool. Field reports: "no subtitles for anything", on a
    # title that had hundreds the week before, with nothing in the log but this
    # line. All it took was Kodi's download-languages list holding English and
    # not Hebrew -- a setting most people have never opened.
    #
    # Someone who opens a HEBREW subtitle add-on wants Hebrew. So: try to heal
    # the setting first (add Hebrew, keep whatever else is there), and if that
    # does not take, carry on anyway. Offering entries the user can ignore is a
    # far smaller harm than showing them an empty dialog.
    if not settings.hebrew_subtitle_wanted():
        kodi.log(
            'list_candidates: Kodi\'s subtitle language preference does not '
            'name Hebrew -- listing anyway. Opening a Hebrew subtitle add-on '
            'IS the request for Hebrew, and an empty dialog is a dead end. '
            'Add Hebrew to Settings > Player > Language > "languages to '
            'download subtitles for" to silence this.', level='INFO')

    filepath = info.get('filepath') or ''
    imdb_id = (info.get('imdb_id') or '').strip()
    tmdb_id = (info.get('tmdb_id') or '').strip()
    season  = info.get('season') or ''
    episode = info.get('episode') or ''
    sources = _enabled_sources()

    # Collect candidate source files from the two filesystem
    # locations we look at. By-language dicts, so first match wins
    # per language.
    alongside = {}
    for path, lang in local.find_alongside(filepath):
        if lang and lang not in alongside:
            alongside[lang] = path

    in_temp = {}
    for entry in local.find_in_temp():
        lang = entry['lang']
        if not lang or lang in in_temp:
            continue
        # CRITICAL: never accept Hebrew from the temp dir as a
        # passthrough candidate. The file Kodi keeps there
        # (typically TempSubtitle.he.srt) is whatever was
        # selected last -- which means after translating movie
        # A, opening the subtitle dialog for movie B would
        # surface movie A's Hebrew SRT as if it were a match
        # for movie B. Only trust local files alongside the video
        # for Hebrew passthrough.
        if lang == 'he':
            continue
        in_temp[lang] = entry['path']

    results = []

    # 1. Hebrew passthrough -- if there's already a Hebrew SRT we
    #    can hand to Kodi, no need to translate anything.
    have_hebrew = False
    if 'he' in alongside:
        have_hebrew = True
        results.append({
            'filename': os.path.basename(alongside['he']),
            'language': 'he',
            'link': _encode_link({
                'type': 'passthrough', 'path': alongside['he'],
                'release': os.path.basename(alongside['he']),
            }),
            'sync': 'true',
            'rating': '5',
            'is_hi': False, 'is_hd': False,
        })

    # Built-in sources engine (Phase B, gated by use_builtin_engine,
    # default OFF). When on, MoranSubs searches the subtitle sources
    # itself so it can stand in for DarkSubs: embedded Hebrew, human
    # Hebrew, machine-translated Hebrew, and other languages (English
    # etc.). When the gate is off the bridge returns [] without importing
    # the engine, so this block is a no-op.
    engine_human, engine_mt, engine_other, engine_embedded = [], [], [], []
    _engine_on = False
    try:
        from resources.lib.fetching import bridge
        from resources.lib.fetching.embedded import streams
        _engine_on = streams.enabled()
    except Exception as e:
        kodi.log('engine import/enabled() failed: {0}'.format(e),
                       level='WARNING')
    kodi.log('engine gate: enabled={0}'.format(_engine_on), level='INFO')
    if _engine_on:
        # Embedded-stream detection and the provider search are INDEPENDENT --
        # run them in SEPARATE try blocks so a failure in one (e.g. the player
        # stream probe) can NEVER stop the other. Previously a single exception
        # in embedded_candidates aborted the whole block and the provider search
        # never ran, leaving only the community pool. Failures are logged at
        # WARNING (not DEBUG) so they're visible without a debug log.
        try:
            engine_embedded = streams.embedded_candidates(info)
        except Exception as e:
            kodi.log('engine embedded_candidates failed: {0}'.format(e),
                           level='WARNING')
        try:
            _found = bridge.search(
                info, modal_progress=modal_progress, cache_only=quick)
            if _found is None:
                info['_list_pending'] = True
                _found = []
            for c in _found:
                k = c.get('_engine_kind', 'human_he')
                if k == 'human_he':
                    engine_human.append(c)
                elif k == 'mt_he':
                    engine_mt.append(c)
                else:
                    engine_other.append(c)
        except Exception as e:
            kodi.log('engine search failed: {0}'.format(e),
                           level='WARNING')

        # "עברית מסונכרנת למובנה" -- an external Hebrew subtitle re-timed onto
        # the embedded track's own cue skeleton. Same sync as the embedded
        # track, and correct end-of-line punctuation, because it is our file.
        #
        # SHOWN ONLY WHEN IT CAN ACTUALLY WORK. It needs both halves: an
        # embedded HEBREW track to take the timing from, and at least one
        # external Hebrew subtitle to re-time. Both are already in hand here,
        # so a title with no Hebrew subtitle simply never sees this row rather
        # than being offered something that would fail when picked. (If the
        # candidates exist but none of them aligns confidently, resolve() falls
        # back to selecting the embedded track -- what the user gets today.)
        try:
            _emb_he = next((c for c in engine_embedded
                            if c.get('_engine_kind') == 'embedded_he'), None)
            if _emb_he is not None and (engine_human or engine_mt):
                _emb_pl = _decode_link(_emb_he.get('link') or '') or {}
                engine_embedded.insert(0, {
                    'filename': 'עברית מסונכרנת למובנה · 101%',
                    'language': 'he',
                    'link': urllib.parse.quote(json.dumps({
                        'type': 'embedded_sync', 'lang': 'he',
                        'stream_index': _emb_pl.get('stream_index'),
                    }, ensure_ascii=False)),
                    'sync': 'true', 'rating': '5',
                    'is_hi': False, 'is_hd': False,
                    '_engine_kind': 'embedded_he', '_pct': 101,
                })
        except Exception as e:
            kodi.log('embedded_sync row skipped: {0}'.format(e),
                           level='WARNING')

        # Queue EVERY human Ktuvit result for the background harvest, so the
        # whole title's set ends up in the pool over time -- not just the one
        # the user picks. Just a fast local write per sub (no Ktuvit hit here);
        # the long-lived service downloads + uploads them gently. Gated by
        # pool_share; runs on auto-on-play AND a manual "download subtitles".
        try:
            if pool_contribute is not None and pool_client.share_enabled():
                _kt = 0
                for c in engine_human:
                    pl = _decode_link(c.get('link') or '') or {}
                    if (pl.get('type') == 'engine' and not pl.get('embedded')
                            and (pl.get('source') or '').strip().lower()
                            == 'ktuvit'
                            and 'Hebrew' in (pl.get('language') or '')
                            and 'MachineTranslated' not in (
                                pl.get('language') or '')):
                        pool_harvest.enqueue_harvest(info, pl)
                        _kt += 1
                if _kt:
                    kodi.log('ktuvit harvest: queued {0} release(s) for '
                                   'background mirroring'.format(_kt),
                                   level='INFO')
        except Exception as e:
            kodi.log('ktuvit harvest enqueue failed: {0}'.format(e),
                           level='WARNING')

    # Language display order (the user's requested grouping): Hebrew first
    # (handled above as its own groups), then English, Spanish, then the other
    # AI-source languages MoranSubs can translate from (German/French/
    # Portuguese), then everything else. Used to sort + group the raw foreign
    # subs AND to decide which languages get an "AI translate to Hebrew" entry.
    _AI_SOURCE_ORDER = {'en': 0, 'es': 1, 'de': 2, 'fr': 3, 'pt': 4}

    def _lang_rank(code):
        return _AI_SOURCE_ORDER.get(code, 50)


    # Embedded Hebrew (101%) goes to the very top -- above even a local
    # passthrough -- mirroring DarkSubs's [LOC] entry. Embedded FOREIGN streams
    # (e.g. built-in French/English) are NOT Hebrew, so they must rank BELOW all
    # the Hebrew options, not above them -- they're held back and appended after
    # the foreign section.
    embedded_foreign = []
    if engine_embedded:
        emb_he = [c for c in engine_embedded if c.get('language') == 'he']
        embedded_foreign = [c for c in engine_embedded
                            if c.get('language') != 'he']
        if emb_he:
            # Only an embedded HEBREW stream means "Hebrew already exists"; an
            # embedded English (or other) [מובנה] entry must NOT suppress the
            # AI-translate options.
            have_hebrew = True
            results[:0] = [_clean(c) for c in emb_he]

    # Community pool. ONE network lookup returns both kinds of shared Hebrew:
    #   - 'ktuvit': a HUMAN Ktuvit subtitle mirrored to the pool. It loads
    #               INSTANTLY from the channel and never hits Ktuvit, so when a
    #               release exists BOTH live (from the engine) and in the pool we
    #               show the POOL copy and hide the slow live one -- exactly what
    #               you asked: no need for the live Ktuvit when the pool has it.
    #               Labelled "כתובית · מאגר" so it's clearly the pool (a live one
    #               is labelled "Ktuvit"). Human, so it ranks above the AI pool.
    #   - 'ai':     a machine AI translation other users shared.
    # One lookup keeps the request count identical to the AI-only pool.
    _pool_variants = []
    _video_ref = ''
    if quick and pool_contribute is not None and pool_client.use_enabled():
        info['_list_pending'] = True
    elif pool_contribute is not None and pool_client.use_enabled():
        # Mirror the CONTRIBUTION-side release derivation (pool._release_from):
        # it tries basename(li_filename) BEFORE basename(filepath), because on a
        # debrid stream `filepath` is a tokenized URL while `li_filename`
        # (ListItem.FileNameAndPath) carries the real release. Without li_filename
        # here the lookup and the stored release could differ for the SAME file
        # (when picked_release/tagline/label are all blank), which would make the
        # embedded same-source gate wrongly hide the viewer's OWN item on replay.
        _video_ref = (info.get('picked_release') or info.get('tagline')
                      or info.get('label')
                      or _release_from_path(info.get('li_filename'))
                      or os.path.basename(filepath)
                      or info.get('title') or '')
        try:
            _pool_variants = pool_client.lookup(info)
        except Exception:
            _pool_variants = []



    # Map normalised release -> the pooled Ktuvit copy, so we can swap a live
    # Ktuvit result for its faster pool twin.
    _ktuvit_pool_by_rel = {}
    for v in _pool_variants:
        if (v.get('kind') or 'ai') == 'ktuvit':
            r = _pool_release(v)
            if r:
                _ktuvit_pool_by_rel.setdefault(_norm_rel(r), v)

    def _pool_entry(v, release):
        pct = _match_pct(_video_ref, release) if release else 0
        if release and pct > 0:
            label = 'כתובית · מאגר · {0}%  —  {1}'.format(pct, release)
        elif release:
            label = 'כתובית · מאגר  —  {0}'.format(release)
        else:
            label = 'כתובית · מאגר'
        return {
            'filename': label, 'language': 'he',
            # 'release' rides in the link so resolve() can tier-check the
            # pool sub against the playing release (SubSync S2 verify/fix).
            'link': _encode_link({
                'type': 'pool', 'hash': v.get('hash'),
                'release': release or '',
                'pool_kind': v.get('kind') or 'ai',
                'source_lang': v.get('source_lang') or '',
            }),
            'sync': 'false', 'rating': '5', 'is_hi': False, 'is_hd': False,
        }

    # Engine human Hebrew, in the engine's own match-% order. For a LIVE Ktuvit
    # result that's ALSO in the pool, emit the POOL copy in its place (instant +
    # no Ktuvit hit), keeping the position so the %-ordering is preserved. Other
    # human results (Wizdom, not-yet-pooled Ktuvit) render as-is.
    _used_pool_rels = set()
    for c in engine_human:
        have_hebrew = True
        pl = _decode_link(c.get('link') or '') or {}
        is_ktuvit = (pl.get('type') == 'engine'
                     and (pl.get('source') or '').strip().lower() == 'ktuvit')
        fn = c.get('filename') or ''
        rel_norm = _norm_rel(fn.split('—')[-1].strip() if '—' in fn else '')
        if is_ktuvit and rel_norm and rel_norm in _ktuvit_pool_by_rel:
            v = _ktuvit_pool_by_rel[rel_norm]
            results.append(_pool_entry(v, _pool_release(v)))
            _used_pool_rels.add(rel_norm)
        else:
            results.append(_clean(c))

    # Pooled Ktuvit releases the live engine did NOT return this time (e.g.
    # Ktuvit is slow/down) -- still available, instant, human.
    for rel_norm, v in _ktuvit_pool_by_rel.items():
        if rel_norm in _used_pool_rels:
            continue
        have_hebrew = True
        results.append(_pool_entry(v, _pool_release(v)))

    # AI pool (machine translations) -- below all the human Ktuvit entries.
    # EMBEDDED-sourced translations (kind='ai_emb') are synced to ONE specific
    # source's OWN timing, so they are surfaced as "תרגום מובנה" ONLY for that
    # exact source (release). For any other release they are hidden entirely --
    # a viewer on a different source can create their own embedded translation
    # for THEIR file (which then pools for that source). An exact-source
    # embedded item leads the AI list and shows NO match-% (it IS your release,
    # so a "%" would be redundant/confusing). Regular AI variants order by %.
    _ai_variants = [v for v in _pool_variants
                    if (v.get('kind') or 'ai') != 'ktuvit']

    def _emb_ok(v):
        # Embedded variant -> eligible ONLY for its own (exact) source.
        if (v.get('kind') or '') != 'ai_emb':
            return True
        return _is_same_source(_video_ref, _pool_release(v))

    def _ai_sort_key(v):
        rel = _pool_release(v)
        pct = _match_pct(_video_ref, rel) if rel else 0
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
        g = _gender_src_rank(src) if is_emb else 1
        return (0 if is_emb else 1, g, -pct, src)

    for v in sorted(_ai_variants, key=_ai_sort_key):
        if not _emb_ok(v):
            continue   # embedded translation for a DIFFERENT source -> hidden
        have_hebrew = True
        release = _pool_release(v)
        if (v.get('kind') or '') == 'ai_emb':
            # Embedded is surfaced ONLY for the EXACT source (_emb_ok ->
            # TIER_EXACT), so it is a 100% match by definition -> show 100%. Also
            # name the SOURCE language it was translated FROM: among several
            # embedded translations of the same release the list is ordered by
            # that language's gender accuracy (strong-gender first), so showing it
            # makes the order legible ("מובנה AI (ספרדית)" ranks above "(אנגלית)").
            _slh = _lang_display_he((v.get('source_lang') or 'en').strip().lower()[:2])
            _emb_base = 'תרגום מובנה AI ({0}) · מאגר קהילתי · 100%'.format(_slh)
            label = ('{0}  —  {1}'.format(_emb_base, release) if release else _emb_base)
        else:
            pct = _match_pct(_video_ref, release) if release else 0
            # Only show a % when we actually have a meaningful match (a 0% almost
            # always means we couldn't read the video's release name, not a real
            # zero -- showing "0%" is misleading).
            if release and pct > 0:
                label = 'תרגום AI · מאגר קהילתי · {0}%  —  {1}'.format(pct, release)
            elif release:
                label = 'תרגום AI · מאגר קהילתי  —  {0}'.format(release)
            else:
                label = 'תרגום AI · מאגר קהילתי'
        results.append({
            'filename': label,
            'language': 'he',
            'link': _encode_link({
                'type': 'pool', 'hash': v.get('hash'),
                'release': release or '',
                'pool_kind': v.get('kind') or 'ai',
                'source_lang': v.get('source_lang') or '',
            }),
            'sync': 'false', 'rating': '5',
            'is_hi': False, 'is_hd': False,
        })

    # Machine-translated Hebrew from the engine.
    for c in engine_mt:
        have_hebrew = True
        results.append(_clean(c))

    # Foreign-language engine results. With AI translation ON (default) each
    # becomes a single "translate to Hebrew" action (pick it -> get Hebrew,
    # like DarkSubs auto_translate). With translation_mode = 'none' (the user
    # opted out of AI) we hand back the RAW foreign sub instead -- no AI is
    # ever invoked. Grouped/ordered by source language (en, es, de, fr, pt,
    # then the rest); within a language, best match % first.
    ai_translation_on = (settings.get_setting('translation_mode', 'ai')
                         or 'ai') != 'none'
    # Group BOTH the foreign AI-translate sources AND the embedded (built-in)
    # foreign streams by language, then emit language by language (en, es, de,
    # fr, pt, then the rest). Within each language the EMBEDDED track comes
    # FIRST (top of that language's group), then the rest by best match %. So
    # built-in subtitles head their own language instead of all being dumped at
    # the very bottom -- while Hebrew (handled above) still leads the whole list.
    _ai_by_lang = {}
    for c in engine_other:
        _ai_by_lang.setdefault(c.get('language') or '?', []).append(c)
    _emb_by_lang = {}
    for c in embedded_foreign:
        _emb_by_lang.setdefault(c.get('language') or '?', []).append(c)
    _emb_policy = _embedded_translation_policy()
    _emb_mode_available = _emb_policy['enabled']
    if _emb_policy['mode'] == 'local_only':
        _emb_url = _playing_video_url(info)
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
    if ai_translation_on and embedded_foreign and _emb_mode_available:
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
            for _v in _ai_variants:
                if ((_v.get('kind') or '') == 'ai_emb' and _emb_ok(_v)
                        and (_v.get('source_lang') or 'en').strip().lower()[:2] == _sl):
                    return True
            return False
        _emb_ai_seen = set()
        for c in sorted(embedded_foreign,
                        key=lambda x: (_lang_rank(x.get('language') or '?'),
                                       x.get('language') or '?')):
            code = c.get('language') or ''
            if (not code or code in _emb_ai_seen
                    or code in ('?', 'und', 'mis', 'zxx')):
                continue
            _emb_ai_seen.add(code)
            if _pool_has_emb(code):
                continue   # instant same-release pool copy already listed above
            have_hebrew = True
            # The real Kodi stream index lives INSIDE this candidate's own
            # engine link payload (not a top-level key), so decode it out. Carry
            # it on the embedded_ai link so the pick can show this embedded track
            # NATIVELY (instant, already synced) while the Hebrew is extracted+
            # translated in the background.
            _emb_src = _decode_link(c.get('link') or '') or {}
            results.append({
                'filename': 'תרגום מובנה → עברית (AI) · {0}'.format(
                    _lang_display(code)),
                'language': 'he',
                'link': _encode_link({'type': 'embedded_ai',
                                      'src_lang': code,
                                      'stream_index': _emb_src.get(
                                          'stream_index')}),
                'sync': 'true',
                'rating': '5', 'is_hi': False, 'is_hd': False,
            })

    for code in sorted(set(_ai_by_lang) | set(_emb_by_lang),
                       key=lambda l: (_lang_rank(l), l)):
        # Built-in (embedded) track of this language -> top of its group.
        for c in _emb_by_lang.get(code, []):
            results.append(_clean(c))
        # Then the foreign subs of this language. Annotate each with SDH-ness
        # (a whole-token 'SDH' release marker, or a content-detected release --
        # NOT the provider's unreliable hearing-impaired flag), then order SDH
        # FIRST -- an SDH sub has the complete dialogue + speaker
        # labels, the best source for AI gender accuracy -- and within that by
        # best match %. Decode the engine link ONCE here (reused below).
        _lang_cands = []
        for c in _ai_by_lang.get(code, []):
            _s = _decode_link(c.get('link') or '') or {}
            _sdh = _is_sdh_ext(c, _s.get('filename') or '')
            _lang_cands.append((c, _s, _sdh))
        _lang_cands.sort(key=lambda t: (not t[2], -t[0].get('_pct', 0)))
        for c, src, _sdh in _lang_cands:
            pct = c.get('_pct', 0)
            if ai_translation_on:
                if not src or src.get('type') != 'engine':
                    continue
                src = dict(src)
                src['type'] = 'engine_ai'
                src['src_lang'] = code
                rel = src.get('filename') or code
                have_hebrew = True
                # SDH items are tagged so the user knows they're the best pick for
                # זכר/נקבה accuracy (complete dialogue + speaker labels).
                _label = ('תרגום AI לעברית · SDH (מדויק למגדר) · {0}%  —  {1}'
                          if _sdh else
                          'תרגום AI לעברית · {0}%  —  {1}').format(pct, rel)
                results.append({
                    'filename': _label,
                    'language': code,
                    'link': _encode_link(src),
                    'sync': 'false',
                    'rating': c.get('rating', '3'),
                    # NOT is_hi: the DELIVERED Hebrew is plain dialogue (the HI
                    # brackets/sound cues are stripped before translation), so
                    # Kodi's hearing_imp badge would mislabel it. The SDH signal
                    # for the user is the label text above, not this flag.
                    'is_hi': False, 'is_hd': False,
                })
            else:
                # Opt-out: deliver the raw foreign sub as-is (SDH still sorts first).
                results.append(_clean(c))

    skip_when_hebrew = settings.get_bool('skip_if_hebrew', True)
    if have_hebrew and skip_when_hebrew:
        return _finalize(info, results)

    # 2. For each enabled source language, surface ONE "translate
    #    this" entry from a local source:
    #       (a) alongside file (local re-watch)
    #       (b) temp-dir file (loaded by another addon, e.g. DarkSubs)
    #    Built into a separate list so cache hits can be sorted to
    #    the top of the AI section (just under Hebrew passthrough).
    ai_entries = []
    seen_langs = set()
    for src_lang in (sources if ai_translation_on else []):
        if src_lang in seen_langs:
            continue

        local_path = alongside.get(src_lang) or in_temp.get(src_lang)
        if local_path:
            seen_langs.add(src_lang)
            source_label = _lang_display(src_lang)
            source_origin = ('local file' if alongside.get(src_lang)
                             else 'loaded by another addon')
            ai_entries.append({
                'filename': 'AI Hebrew (translate {0} {1})'.format(
                    source_label, source_origin),
                'language': 'he',
                'link': _encode_link({
                    'type': 'ai',
                    'source_lang': src_lang,
                    'local_path': local_path,
                }),
                'sync': 'false',
                'rating': '4' if src_lang == 'en' else '3',
                'is_hi': False, 'is_hd': False,
                '_payload': {'source_lang': src_lang,
                             'local_path': local_path},
            })
            continue

    # Mark cached entries with a visible label and sort them to the
    # top of the AI section so a returning user picks the
    # already-translated copy first (instant) instead of re-paying
    # for translation by clicking a fresh source.
    for entry in ai_entries:
        payload = entry.pop('_payload', {})
        try:
            src_id = _source_id_for_ai(payload)
            if src_id:
                # Same tier as the DOWNLOAD path uses (_try_fast_download,
                # also untiered) -- deliberately, and NOT the same tier as
                # resolve(), which pins to _tier and is usually 'ar'. Marking
                # an entry [CACHE] that the download path then cannot serve is
                # worse than not marking it: the label would promise an
                # instant result and the user would get the English fallback.
                translated = translation_cache.translated_path(
                    imdb_id, season, episode,
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
    ai_entries.sort(key=lambda e: 0 if e.get('is_cached') else 1)
    results.extend(ai_entries)

    if not results:
        # Give the user a hint about why we have nothing -- the
        # "no subtitles found" toast from Kodi alone is
        # uninformative. Each reason is conditional so the message
        # only lists what's actually missing.
        reasons = []
        if not imdb_id and not tmdb_id:
            reasons.append('אין IMDB / TMDB id מהנגן')
        if not alongside and not in_temp:
            reasons.append('אין קבצי SRT ב-temp או ליד הסרט')
        msg = 'AI: אין מקור לתרגום ({0}). בחר כתובית באנגלית מ-DarkSubs ' \
              'ופתח שוב את חיפוש הכתוביות — התרגום ל-AI יופעל אוטומטית.'.format(
                ' / '.join(reasons) or 'לא ידוע')
        kodi.notify(msg, time_ms=5000)
        kodi.log('list_candidates returned empty: ' + repr(
            {'imdb_id': imdb_id, 'tmdb_id': tmdb_id,
             'alongside_count': len(alongside),
             'in_temp_count': len(in_temp)}),
            level='WARNING')

    return _finalize(info, results)
