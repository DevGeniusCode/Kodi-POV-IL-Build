# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Ranking of ready Hebrew candidates by how likely each is to be in sync.

import os
import re
try:
    from resources.lib.core import kodi, selection as core_selection, settings, video_context
except Exception:
    kodi = core_selection = settings = video_context = None
try:
    from resources.lib.scoring_matching import release as scoring_matching_release
except Exception:
    scoring_matching_release = None
try:
    from resources.lib.sync.align import common
except Exception:
    common = None
from resources.lib.core.link_codec import decode_link_safe
from resources.lib.scoring_matching.ranking import release_similarity
from resources.lib.sync.probes.bundle import cached_reference_bundle, verify_file_bundle
from resources.lib.sync.verdicts import VERDICT_VERSION, cache_key, load_verdicts, store_verdict


# Step 8: two rows of the same tier and pct are told apart by release_similarity (0-1000)
# only when one is at least this much closer to the playing file than the row on top.
SIM_MARGIN = 60


def ready_candidate_text(info, candidate):
    """Canonical subtitle text already on disk; never starts a download."""
    try:
        payload = decode_link_safe(candidate.get('link') or '') or {}
        kind = payload.get('type')
        path = ''
        if kind == 'passthrough':
            path = payload.get('path') or ''
        elif kind == 'engine' and not payload.get('embedded'):
            from resources.lib.fetching import subs_cache
            path = subs_cache.cached_source(payload) or ''
        elif kind == 'pool':
            from resources.lib.ai_translation.reuse import pool_reuse
            body, _sid = pool_reuse.pool_source_text(
                info, payload.get('hash'), cache_only=True)
            return body or '', payload
        if path and os.path.isfile(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read(), payload
    except Exception:
        pass
    return '', {}


def is_human_hebrew_candidate(payload):
    kind = payload.get('type')
    if kind == 'passthrough':
        return True
    if kind == 'pool':
        return (payload.get('pool_kind') or 'ai') == 'ktuvit'
    if kind == 'engine' and not payload.get('embedded'):
        lang = payload.get('language') or ''
        return (isinstance(lang, str) and 'Hebrew' in lang
                and 'MachineTranslated' not in lang)
    return False


def names_other_episode(name, info):
    try:
        return bool(video_context.release_conflicts_with_episode(
            name, (info or {}).get('season'), (info or {}).get('episode')))
    except Exception:
        return False


def diverse_human_alternatives(candidates, limit=6, info=None):
    """Keep first-play alternates from distinct release timing families.

    The provider search has already finished.  We only inspect row metadata;
    no subtitle download or additional provider query occurs here.  Preserve
    the top row, take the earliest representative of each other release family,
    then fill spare slots in original order.  An unparseable name is not proof
    that two rows share a timeline, so it gets its own family.
    """
    if limit <= 0:
        return []
    rows = []
    seen_links = set()
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        language = candidate.get('language')
        if not isinstance(language, str) or language.strip().lower() not in (
                'he', 'heb', 'hebrew'):
            continue
        link = candidate.get('link') or ''
        if not isinstance(link, str) or not link or link in seen_links:
            continue
        payload = decode_link_safe(link) or {}
        if (not isinstance(payload, dict)
                or not is_human_hebrew_candidate(payload)
                or payload.get('embedded')):
            continue
        release_fields = (payload.get('filename'), payload.get('release'),
                          candidate.get('filename'))
        if any(value is not None and not isinstance(value, str)
               for value in release_fields):
            continue
        seen_links.add(link)
        release = next((value.strip() for value in release_fields
            if isinstance(value, str) and value.strip()), '')
        if info is not None and names_other_episode(release, info):
            continue
        try:
            parsed = scoring_matching_release.parse(release)
        except Exception:
            parsed = {}
        family = ((parsed.get('source') or '').lower(),
                  (parsed.get('group') or '').lower(),
                  (parsed.get('edition') or '').lower(),
                  bool(parsed.get('proper')))
        if not any(family):
            family = ('unknown', release.lower(), link, False)
        rows.append((link, family))
    if not rows:
        return []
    chosen = [rows[0][0]]
    seen_families = {rows[0][1]}
    for link, family in rows[1:]:
        if family not in seen_families:
            chosen.append(link)
            seen_families.add(family)
        if len(chosen) >= limit:
            return chosen
    chosen_set = set(chosen)
    for link, _family in rows[1:]:
        if link not in chosen_set:
            chosen.append(link)
            chosen_set.add(link)
        if len(chosen) >= limit:
            break
    return chosen


def rank_ready_candidates(info, candidates, max_candidates=4, cached_only=False):
    """Conservatively promote a stronger human Hebrew autosub candidate.

    cached_only (the manual chooser): use only timing verdicts already stored
    for the playing file and never run a new comparison -- one comparison
    against a ~1500-cue profile took 7.7 s on a Shield, and the chooser waited
    16 s for two of them before it opened (field log, Oct 2026).

    This is used only by autosub.  It never fetches candidates, never changes
    the manual picker and never moves an AI/non-human row.  On first play it may
    swap human slots for a clear release-name winner.  With an exact cached
    playing-file profile it may instead promote a CONFIRMED human candidate.
    Any doubt preserves the existing order.
    """
    from resources.lib.sync.service import log, playing_release
    try:
        if (settings.get_setting('subsync_autorank', 'true') or
                'true').strip().lower() == 'false':
            return candidates
        playing = playing_release(info)
        if not playing:
            return candidates
        rows = list(candidates or [])
        # First remote play has no media cue profile yet. Provider order alone
        # can then put a malformed/weak HDTV name ahead of a substantially
        # closer human release. Re-rank human rows only when release_match has
        # a clear >=4-point winner (or a stronger trusted tier); manual picker
        # order and AI placement remain untouched.
        meta = []
        tier_rank = {
            scoring_matching_release.TIER_EXACT: 4,
            scoring_matching_release.TIER_GROUP: 3,
            scoring_matching_release.TIER_SOURCE: 2,
        }
        for index, candidate in enumerate(rows):
            listed_lang = (candidate.get('language') or '').strip().lower()
            payload = decode_link_safe(candidate.get('link') or '') or {}
            if (listed_lang not in ('he', 'heb', 'hebrew')
                    or not is_human_hebrew_candidate(payload)):
                continue
            rel = (payload.get('filename') or payload.get('release') or
                   candidate.get('filename') or '').strip()
            try:
                pct, tier, _diag = scoring_matching_release.score(playing, rel)
            except Exception:
                pct, tier = 0, ''
            rank = tier_rank.get(tier, 0)
            if names_other_episode(rel, info):
                # A row explicitly naming another episode ("...2014.201...",
                # "S02E01") is never a first choice, whatever its score.
                rank = -1
            meta.append({'index': index, 'pct': int(pct or 0),
                         'tier': tier, 'rank': rank, 'release': rel,
                         'sim': release_similarity(playing, rel)})
        if len(meta) >= 2:
            first = meta[0]
            # Same tier and same pct: the row whose FULL name (resolution, codec, audio,
            # spelling) is closest to the playing file wins, by a clear margin only.
            best = max(meta, key=lambda item: (
                item['rank'], item['pct'], item['sim'], -item['index']))
            clear_win = (best['rank'] > first['rank'] or
                         (best['rank'] == first['rank']
                          and best['pct'] >= first['pct'] + 4) or
                         (best['rank'] == first['rank']
                          and best['pct'] == first['pct']
                          and best['sim'] >= first['sim'] + SIM_MARGIN))
            if clear_win and best['index'] != first['index']:
                picked = rows[best['index']]
                # Swap only human slots.  In particular, an interleaved AI row
                # must retain the exact same index and relative placement.
                rows[first['index']], rows[best['index']] = (
                    rows[best['index']], rows[first['index']])
                log('autosub release-rank: promoted human candidate %r '
                     '(%d%% vs %d%%)' % (
                         best['release'] or picked.get('filename') or '?',
                         best['pct'], first['pct']))

        bundle = cached_reference_bundle(info)
        if not bundle:
            return rows
        human_indexes = []
        checked = []
        cut_signature = (bundle.get('cut_signature') or '').strip().lower()
        stored = load_verdicts() if cached_only else {}
        for index, candidate in enumerate(rows):
            text, payload = ready_candidate_text(info, candidate)
            listed_lang = (candidate.get('language') or '').strip().lower()
            if (listed_lang not in ('he', 'heb', 'hebrew')
                    or not is_human_hebrew_candidate(payload)):
                continue
            human_indexes.append(index)
            if text and len(checked) < max(1, int(max_candidates)):
                if cached_only:
                    known = stored.get(cache_key(text, playing, cut_signature))
                    if not known or known.get('v') != VERDICT_VERSION:
                        continue
                    verdict, label, count = dict(known), 'cached', 0
                else:
                    verdict, label, count = verify_file_bundle(bundle, text, playing)
                verdict = verdict or {}
                status = verdict.get('status')
                # This comparison uses the cached cue profile of the exact
                # playing object.  Keep the proof under that cut identity so a
                # later click can apply a proven correction immediately instead
                # of repeating the same analysis in the service worker.
                if (status in (common.STATUS_CONFIRMED,
                               common.STATUS_FIXABLE)
                        and re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature)):
                    if not cached_only:   # read from that key already
                        exact_key = cache_key(text, playing, cut_signature)
                        store_verdict(exact_key, verdict)
                    candidate['_subsync_state'] = (
                        'confirmed' if status == common.STATUS_CONFIRMED
                        else 'fixable')
                    candidate['_subsync_label'] = (
                        'תזמון אומת לקובץ זה' if
                        status == common.STATUS_CONFIRMED else
                        'יסונכרן אוטומטית')
                checked.append({'index': index, 'candidate': candidate,
                                'payload': payload, 'verdict': verdict or {},
                                'label': label, 'count': count})
        if not checked or not human_indexes:
            return rows
        accepted = [x for x in checked
                    if x['verdict'].get('status') in (
                        common.STATUS_CONFIRMED,
                        common.STATUS_FIXABLE)]
        if not accepted:
            return rows
        chosen = max(accepted, key=lambda x: (
            1 if x['verdict'].get('status')
            == common.STATUS_CONFIRMED else 0,
            float(x['verdict'].get('after_score') or 0.0),
            float(x['verdict'].get('overlap') or 0.0),
            float(x['verdict'].get('vote') or 0.0), x['count']))
        first_index = human_indexes[0]
        if chosen['index'] == first_index:
            return rows
        first_checked = next((x for x in checked
                              if x['index'] == first_index), None)
        if first_checked is None:
            # A real cut-scoped confirmation beats a release-name guess even
            # when the guessed first file has not yet been downloaded. Without
            # that exact proof, keep the old protection for trusted names.
            first_payload = decode_link_safe(rows[first_index].get('link') or '') or {}
            first_release = (first_payload.get('filename') or
                             rows[first_index].get('filename') or '')
            _pct, tier, _diag = scoring_matching_release.score(playing, first_release)
            exact_confirmed = (
                chosen['verdict'].get('status') == common.STATUS_CONFIRMED
                and re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature))
            if tier in scoring_matching_release.AUTO_OK_TIERS and not exact_confirmed:
                return rows
        else:
            first_status = first_checked['verdict'].get('status')
            chosen_status = chosen['verdict'].get('status')
            if first_status == common.STATUS_CONFIRMED:
                return rows
            if (first_status == common.STATUS_FIXABLE
                    and chosen_status != common.STATUS_CONFIRMED):
                return rows
        picked = rows[chosen['index']]
        # Preserve every non-human slot, including an interleaved AI fallback.
        rows[first_index], rows[chosen['index']] = (
            rows[chosen['index']], rows[first_index])
        log('autosub timing-rank: promoted cached human candidate %r (%s, '
             '%d cues)' % (picked.get('filename') or '?', chosen['label'],
                           chosen['count']))
        return rows
    except Exception as e:
        log('autosub timing-rank skipped: %r' % e, level='DEBUG')
        return candidates


def rank_picker_candidates(info, candidates, max_candidates=4, cached_only=False):
    """Put the strongest ready Hebrew choices first in the manual picker.

    Provider search order is not timing evidence.  Reuse the exact same
    conservative release and cached-media proof used by autosub, while keeping
    an already-applied row at the top so reopening the chooser never hides what
    is currently on screen.  This performs no provider or media request: on a
    first visit it uses release metadata; after the bounded media profile has
    been learned it can check a few subtitle files that are already cached.
    """
    from resources.lib.sync.service import log
    try:
        rows = list(candidates or [])
        current = [row for row in rows if
                   (row.get('filename') or '').startswith('» נוכחית')]
        if not current:
            return rank_ready_candidates(
                info, rows, max_candidates=max_candidates,
                cached_only=cached_only)
        pinned = current[0]
        rest = [row for row in rows if row is not pinned]
        return [pinned] + rank_ready_candidates(
            info, rest, max_candidates=max_candidates,
            cached_only=cached_only)
    except Exception as e:
        log('picker timing-rank skipped: %r' % e, level='DEBUG')
        return candidates
