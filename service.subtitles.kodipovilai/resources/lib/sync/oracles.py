# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Reference ("oracle") subtitles: candidates from other languages and providers, and their download.

import os
import json
try:
    from resources.lib.scoring_matching import release as scoring_matching_release
except Exception:
    scoring_matching_release = None


# ---- oracle acquisition -----------------------------------------------------

def _decode_link(link):
    try:
        import urllib.parse
        return json.loads(urllib.parse.unquote(link))
    except Exception:
        return None


def _oracle_candidates(info, include_he=False, all_languages=False,
                       search_state=None):
    """Foreign-language engine candidates as [{'release', 'payload'}] -- the
    bridge's 24h result cache makes this cheap right after the picker/autosub
    built the list. Never raises.

    Hebrew is excluded by DEFAULT and that is not an oversight: this list's
    original job is to be a timing ORACLE for correcting Hebrew, so Hebrew is
    the thing being corrected and cannot also be the reference.

    `include_he=True` is for the one caller that wants the opposite -- re-timing
    an external HEBREW subtitle onto the embedded track's timeline, where
    Hebrew is the payload rather than the reference. Without the flag that
    caller silently gets zero candidates and falls through to English, which
    would deliver an ENGLISH subtitle to someone who asked for Hebrew.
    """
    from resources.lib.sync.service import _log
    out = []
    try:
        from resources.lib.fetching import bridge
        from resources.lib.fetching.embedded import streams as embedded_streams
        if not embedded_streams.enabled():
            return out
        if (all_languages
                and hasattr(bridge, 'search_all_languages_for_timing')):
            rows = bridge.search_all_languages_for_timing(info)
            if rows is None:
                if isinstance(search_state, dict):
                    search_state['transient'] = True
                return out
        else:
            # Compatibility with an older bridge must remain background-only;
            # never reopen the manual provider dialog from SubSync's worker.
            rows = bridge.search(info, modal_progress=False)
        for c in rows:
            if (c.get('language') or '') == 'he' and not include_he:
                continue
            # The kind filter has to widen with the language filter: Hebrew
            # results are tagged 'human_he'/'mt_he', so leaving this at
            # ('other', None) would drop every Hebrew row again one line after
            # letting it through.
            _kind = c.get('_engine_kind')
            _ok_kinds = ((None, 'other', 'human_he', 'mt_he') if include_he
                         else (None, 'other'))
            if _kind not in _ok_kinds:
                continue
            pl = _decode_link(c.get('link') or '')
            if not pl or pl.get('type') != 'engine' or pl.get('embedded'):
                continue
            rel = (pl.get('filename') or '').strip()
            if rel:
                # Keep the language so the picker can prefer an oracle that
                # SEGMENTS like the Hebrew candidate (which is almost always
                # translated from English). A same-release oracle in a different
                # language splits lines differently -> its cue onsets drift from
                # the Hebrew's, tanking the tight agreement (field: a Dutch
                # ROVERS oracle gave only 45% tight on a real -926ms offset).
                out.append({'release': rel, 'payload': pl,
                            'language': (c.get('language') or '').strip().lower()})
    except Exception as e:
        _log('oracle candidate scan failed: %r' % e, level='WARNING')
    return out


def _merge_oracle_candidates(*groups):
    """Stable de-duplication for ordinary plus all-language oracle rows."""
    out = []
    seen = set()
    for group in groups:
        for candidate in group or []:
            try:
                key = (
                    (candidate.get('release') or '').strip().lower(),
                    (candidate.get('language') or '').strip().lower(),
                    json.dumps(candidate.get('payload') or {}, sort_keys=True,
                               ensure_ascii=False, default=str),
                )
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def _download_oracle(payload):
    from resources.lib.sync.service import _log
    try:
        from resources.lib.fetching.embedded import streams as embedded_streams
        path = embedded_streams.download(payload)
        if path and os.path.isfile(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
    except Exception as e:
        _log('oracle download failed: %r' % e, level='WARNING')
    return ''


def _oracle_match(candidates, playing):
    """Rank provider oracles that may represent the playing edit.

    Exact/group matches are usable for every source. A looser same-source
    match is accepted only for physical-disc masters, matching ``pick_oracle``:
    WEB services and broadcasts can carry different edits despite sharing the
    same broad source label.
    """
    try:
        playing_source = scoring_matching_release.parse(playing).get('source', '')
    except Exception:
        playing_source = ''
    allowed = {scoring_matching_release.TIER_EXACT, scoring_matching_release.TIER_GROUP}
    if playing_source in ('bluray', 'dvd'):
        allowed.add(scoring_matching_release.TIER_SOURCE)
    tier_rank = {
        scoring_matching_release.TIER_EXACT: 3,
        scoring_matching_release.TIER_GROUP: 2,
        scoring_matching_release.TIER_SOURCE: 1,
    }
    ranked = []
    for candidate in candidates or []:
        rel = (candidate.get('release') or '').strip()
        if not rel:
            continue
        try:
            pct, tier, _diag = scoring_matching_release.score(playing, rel)
        except Exception:
            continue
        if tier not in allowed:
            continue
        try:
            group = scoring_matching_release.parse(rel).get('group', '')
        except Exception:
            group = ''
        ranked.append((candidate, tier, int(pct or 0), group,
                       tier_rank.get(tier, 0)))
    return ranked


def _diversify_oracle_matches(ranked):
    """Round-robin release groups before spending provider downloads.

    Subtitle APIs commonly return the same ROVERS/DEMAND timing translated
    into many languages in one contiguous block.  Downloading that whole block
    first wastes the bounded rescue budget on timing clones.  Preserve the
    quality order *within* each group, but take one row from every group before
    taking a second.  Rows with no parsed group are bucketed by normalized
    release so unrelated anonymous masters are not collapsed together.
    """
    buckets = {}
    order = []
    for item in ranked or []:
        try:
            candidate, _tier, _pct, group, _rank = item
            key = (group or '').strip().lower()
            if not key:
                key = 'nogroup:' + scoring_matching_release.normalize(
                    candidate.get('release') or '')
        except Exception:
            key = 'unknown'
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(item)
    out = []
    while True:
        added = False
        for key in order:
            if buckets[key]:
                out.append(buckets[key].pop(0))
                added = True
        if not added:
            break
    return out
