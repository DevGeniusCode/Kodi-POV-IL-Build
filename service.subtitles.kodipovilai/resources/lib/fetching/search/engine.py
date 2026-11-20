# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Subtitle list stages: the built-in sources engine (human Hebrew, machine-translated, foreign, embedded)
# and the AI-source language order with the embedded foreign streams held back.

import json
import urllib.parse
from resources.lib.core import kodi
from resources.lib.core.link_codec import decode_link
from resources.lib.fetching.search.rows import AI_SOURCE_ORDER, clean
from resources.lib.fetching.search.state import NEXT
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None


def lang_rank(S, code):
    return AI_SOURCE_ORDER.get(code, 50)


def engine(S):
    """The built-in sources engine: human Hebrew, machine-translated, foreign, embedded."""
    # Built-in sources engine (Phase B, gated by use_builtin_engine,
    # default OFF). When on, MoranSubs searches the subtitle sources
    # itself so it can stand in for DarkSubs: embedded Hebrew, human
    # Hebrew, machine-translated Hebrew, and other languages (English
    # etc.). When the gate is off the bridge returns [] without importing
    # the engine, so this block is a no-op.
    S.engine_human, S.engine_mt, S.engine_other, S.engine_embedded = [], [], [], []
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
            # not for a manual search: the embedded tracks belong to the playing file, not to the typed title
            if not S.info.get('_manual'):
                S.engine_embedded = streams.embedded_candidates(S.info)
        except Exception as e:
            kodi.log('engine embedded_candidates failed: {0}'.format(e),
                           level='WARNING')
        try:
            _hook = {'on_provider': S.on_provider} if S.on_provider else {}
            _found = bridge.search(
                S.info, modal_progress=S.modal_progress, cache_only=S.quick, **_hook)
            if _found is None:
                S.info['_list_pending'] = True
                _found = []
            for c in _found:
                k = c.get('_engine_kind', 'human_he')
                if k == 'human_he':
                    S.engine_human.append(c)
                elif k == 'mt_he':
                    S.engine_mt.append(c)
                else:
                    S.engine_other.append(c)
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
            _emb_he = next((c for c in S.engine_embedded
                            if c.get('_engine_kind') == 'embedded_he'), None)
            if _emb_he is not None and (S.engine_human or S.engine_mt):
                _emb_pl = decode_link(_emb_he.get('link') or '') or {}
                S.engine_embedded.insert(0, {
                    'filename': 'עברית מסונכרנת למובנה · 101%',
                    'language': 'he',
                    'link': urllib.parse.quote(json.dumps({
                        'type': 'embedded_sync', 'lang': 'he',
                        'stream_index': _emb_pl.get('stream_index'),
                    }, ensure_ascii=False)),
                    'sync': 'true', 'rating': '5',
                    'is_hi': False, 'is_hd': False,
                    '_engine_kind': 'embedded_he', '_pct': 101, 'pct': 101,
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
            # (no harvest for a manual search: its rows are not the playing video's)
            if (pool_contribute is not None and pool_client.share_enabled()
                    and not S.info.get('_manual')):
                _kt = 0
                for c in S.engine_human:
                    pl = decode_link(c.get('link') or '') or {}
                    if (pl.get('type') == 'engine' and not pl.get('embedded')
                            and (pl.get('source') or '').strip().lower()
                            == 'ktuvit'
                            and 'Hebrew' in (pl.get('language') or '')
                            and 'MachineTranslated' not in (
                                pl.get('language') or '')):
                        pool_harvest.enqueue_harvest(S.info, pl)
                        _kt += 1
                if _kt:
                    kodi.log('ktuvit harvest: queued {0} release(s) for '
                                   'background mirroring'.format(_kt),
                                   level='INFO')
        except Exception as e:
            kodi.log('ktuvit harvest enqueue failed: {0}'.format(e),
                           level='WARNING')
    return NEXT


def ai_prep(S):
    """AI source language order; the embedded foreign streams are held back."""
    # Embedded Hebrew (101%) goes to the very top -- above even a local
    # passthrough -- mirroring DarkSubs's [LOC] entry. Embedded FOREIGN streams
    # (e.g. built-in French/English) are NOT Hebrew, so they must rank BELOW all
    # the Hebrew options, not above them -- they're held back and appended after
    # the foreign section.
    S.embedded_foreign = []
    if S.engine_embedded:
        emb_he = [c for c in S.engine_embedded if c.get('language') == 'he']
        S.embedded_foreign = [c for c in S.engine_embedded
                            if c.get('language') != 'he']
        if emb_he:
            # Only an embedded HEBREW stream means "Hebrew already exists"; an
            # embedded English (or other) [מובנה] entry must NOT suppress the
            # AI-translate options.
            S.have_hebrew = True
            S.results[:0] = [clean(c) for c in emb_he]
    return NEXT
