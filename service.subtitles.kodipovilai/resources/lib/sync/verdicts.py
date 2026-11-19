# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Sync verdicts: the local verdict cache, the community verdict and the viewer's own verdicts.

import os
import re
import json
import time
import hashlib
try:
    from resources.lib.scoring_matching import release as scoring_matching_release
except Exception:
    scoring_matching_release = None
try:
    from resources.lib.sync.align import common
except Exception:
    common = None


_VERDICT_FILE = ('special://profile/addon_data/service.subtitles.kodipovilai/'
                 'subsync_verdicts.json')
_MAX_VERDICTS = 400
# Bump to invalidate ALL previously stored verdicts after an engine change.
# v6: offset refinement (median of deltas) supersedes the coarse 500ms bin
# -- fixes stored at bin precision (field: +1500ms should have been ~+1000)
# recompute to the refined value.
# v5: probe cues are rebased to the playback timeline (first-cluster PTS
# origin) -- UNKNOWNs judged against un-rebased references (field: +313s
# "implausible offset" on a synced sub) deserve a recompute.
# v4: the probe now unions ALL embedded tracks + bitrate-aware windows --
# UNKNOWNs stored while it sampled one sparse track (9 cues on a BDRip in the
# field) deserve a recompute.
# v13: small-shift (<1.5s) acceptance now leans on OVERLAP (>=0.85) + magnitude
# rather than the coarse vote (source-oracle min_vote lowered 0.55->0.50). Field:
# four correct small offsets ran vote 54-64% / overlap 87-90% / tight 40-45% --
# the -677ms/54% one still failed the vote floor. Recompute.
# v12: the small-shift (<1.5s) tight floor now drops to ~0.35 when BOTH vote
# (>=0.60) and overlap (>=0.85) corroborate -- different-subber subs cap ~40%
# tight even at the correct offset (field: S01E02 -436ms/64%/87%/40% was right
# but rejected at 42%). Large spurious offsets stay strict. Recompute.
# v11: the tight floor for SMALL (<1.5s) shifts is relaxed to ~0.42 when the
# overlap corroborates (>=0.85). Real subs from a different subber segment lines
# differently, capping tight ~45% even at the correct offset (field: The Flash
# Pilot ~1s-early sub, 45% tight against every oracle) -- previously rejected.
# Recompute so those small real offsets now apply.
# v10: pick_oracle now prefers an ENGLISH oracle within the same tier (the
# Hebrew candidate is translated from English, so an English reference segments
# like it and aligns tightly; a Dutch oracle gave only 45% tight on the real
# -926ms offset). Verdicts that failed the gate against a foreign oracle must
# recompute so the English oracle can be chosen.
# v9: same-source oracle alignment is pinned to identity scale + a relaxed vote
# floor (cross-language segmentation depressed a real -926ms match to 61% vote,
# and a full scale search produced a +560s FPS-fit). Verdicts that FAILED the
# gate under v8 must recompute so a genuine small offset now reaches the tight
# gate and applies.
# v8: pick_oracle now accepts SAME-SOURCE-class oracles (a BluRay sub anchors a
# BluRay REMUX even with a different group/codec). Files that found NO oracle
# under v7 -- and were left unfixed or judged only against the noisy file probe
# (field: The Flash Pilot, ~1s early, no oracle) -- must recompute so the newly
# eligible oracle can supply the real offset.
# v7: the tight-agreement gate is now REQUIRED for every non-trivial shift and
# scaled by its magnitude (a multi-second jump needs ~0.85-0.9 agreement, not
# the old flat 0.65 that only ran for sparse refs). Field: a 31-cue file-probe
# union voted -20.3s at 68% tight and de-synced an already-good sub -- any
# FIXABLE stored under the looser gate must recompute so those spurious large
# shifts are dropped.
# v3: force recompute so UNKNOWNs stored before the adaptive second pass
# get their pass-2 chance. v2: the pre-dedupe voting could store a spurious
# FIXABLE (field case:
# offset=-350s) -- those cached verdicts must never be re-applied.
# v14: validate HTTP ranges and resume interrupted probes within their budgets.
# v15: local-consistency guard + conservatively validated piecewise maps.  Old
# global FIXABLE verdicts must recompute because a dominant region could have
# hidden a differently-cut second half.
# v16: bounded adaptive scale proposals recover continuous drift when irregular
# inserted/deleted cues defeat index quantiles. Every normal acceptance gate is
# still required, so older UNKNOWNs must recompute while older fixes are rechecked.
# v17: every proposed non-identity clock must remain locally continuous; soft
# multi-track probes cannot make sub-second editorial nudges, and a newer
# subtitle selection invalidates any older background hot-swap.
# v18: actual-media cut signatures scope local verdicts; embedded tracks are
# judged independently and sparse modern tracks are never unioned into a fake
# majority, preventing shifted language tracks from moving an exact one.
# v19: repeated short edit pads can be corrected only after five disjoint
# holdouts reproduce the map and a distinct full-span text/PGS timing family
# validates it. Old UNKNOWNs must recompute; exact-cut keys prevent reuse.
# v20: a remote file with no embedded subtitle timeline may prove that same map
# with two distinct provider-oracle timing families. Both must independently
# rebuild an agreeing map and pass >=4/5 held-out folds; translated timing
# clones never count as a second family.
# v21: provider rows that fail, return non-SRT data or repeat one timing family
# no longer exhaust the usable-evidence budget before a later independent row.
# Recompute v20 UNKNOWNs so subtitles already tried on the affected source get
# the corrected bounded search instead of inheriting the old refusal forever.
# v22: when the user's ordinary language set contains no independent provider
# family, perform one separately cached all-language timing search in the deep
# worker. v21 UNKNOWNs may therefore have new evidence and must be recomputed.
# v23: a physical-disc timing lane can be proven by the primary plus two other
# release groups with strong bidirectional/frozen-map checks. v22 UNKNOWNs may
# therefore have new evidence and must be recomputed.
# v24: exact/same-group oracles can prove a strictly gated generic piecewise
# map, and every v23 refusal is retried under that new evidence rule.
# v25: enrich POV season-pack names with the active episode metadata before
# ranking and proof. v24 decisions made against incomplete Sxx identities must
# be retried against the real SxxEyy cut.
# v26: the automatic first-play path can continue through a bounded set of
# already-discovered Hebrew alternatives after the first candidate cannot be
# verified.  Recompute old UNKNOWNs so they do not stop that tournament before
# a later candidate is tested against the exact playing cut.
# v27: autosub tries cached, already-discovered human alternatives before a
# slow foreign-oracle search. Retry earlier UNKNOWN decisions under that order.
# v28: speech-coverage corroboration lets a real multi-second shift between
# differently segmented subtitles pass (it was always refused by the onset
# "tight" bar) and vetoes weak sub-1.5s nudges; release names no longer read
# "WEB-DL"/"DTS-HD" as a group, and service/edition differences are no longer
# trusted. Locally stored "human" CONFIRMED records came from unadjusted
# watches, not from viewers. A cue-level region planner now proves the
# repeated act-break pads of disc remuxes that fixed windows missed, and
# region cuts skip non-dialogue lines instead of stacking them. Every
# older verdict must recompute. v29: a PGS/VobSub track is read as display
# events (appear AND clear) and its offset re-measured on caption starts, so
# PGS-only files decide where they used to abstain.
# v30: a file whose subtitles are all text (a streaming WEB-DL) proves a
# short-edit map with its own other-language tracks, the cue-level planner
# keeps sub-second act steps, and a track tagged 'en-US' is read as English.
# Every UNKNOWN stored for such a file must recompute.
VERDICT_VERSION = 30
# A community sync record with a shift larger than this is only APPLIED blindly
# when a human confirmed it. A large AUTO (machine-computed) offset can be a
# poisoned share (field: a spurious -20.3s file-probe verdict reached the
# registry before the local gate was tightened); rather than jump an
# already-good sub by many seconds on one unverified auto vote, we skip the
# record and let the locally-gated deep-verify decide. Small auto offsets stay
# on the fast pool path.
_COMMUNITY_AUTO_MAX_OFFSET_MS = 6000


# ---- verdict cache ----------------------------------------------------------

def _verdict_path():
    try:
        import xbmcvfs
        return xbmcvfs.translatePath(_VERDICT_FILE)
    except Exception:
        return ''


def cache_key(sub_text, playing, cut_signature=''):
    h = hashlib.sha1(sub_text.encode('utf-8', 'replace')).hexdigest()[:16]
    sig = (cut_signature or '').strip().lower()
    if re.fullmatch(r'cut1:[0-9a-f]{32}', sig):
        return h + '|' + sig
    rel = scoring_matching_release.normalize(playing) if scoring_matching_release else playing.lower()
    return h + '|' + rel


def load_verdicts():
    path = _verdict_path()
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def store_verdict(key, verdict):
    from resources.lib.sync.service import log
    path = _verdict_path()
    if not path:
        return
    try:
        data = load_verdicts()
        # Piecewise verdicts carry their validation certificate.  Dropping it
        # made apply_verdict correctly refuse a cached map on the next play.
        # Verdict dictionaries contain only small JSON-safe scalar/list data;
        # preserve that proof instead of reducing every record to a global
        # offset-shaped subset.
        entry = dict(verdict or {})
        entry.update({'ts': time.time(), 'v': VERDICT_VERSION,
                      'status': verdict.get('status'),
                      'scale': verdict.get('scale', 1.0),
                      'offset_ms': verdict.get('offset_ms', 0.0),
                      'mode': verdict.get('mode', 'global'),
                      'segments': verdict.get('segments') or [],
                      'diag': verdict.get('diag', '')})
        data[key] = entry
        if len(data) > _MAX_VERDICTS:
            data = dict(sorted(data.items(),
                               key=lambda kv: kv[1].get('ts', 0),
                               reverse=True)[:_MAX_VERDICTS])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        log('verdict store failed: %r' % e, level='WARNING')


def sync_registry_release(playing, cut_signature=''):
    """Backward-compatible exact-cut namespace for the existing Worker key."""
    sig = (cut_signature or '').strip().lower()
    if re.fullmatch(r'cut1:[0-9a-f]{32}', sig):
        return '%s POVILCUT %s' % (playing, sig.split(':', 1)[1])
    return playing


def community_verdict(info, key, playing, cut_signature=''):
    """A community /sync record for this (subtitle, release) pair, converted
    to a local-verdict dict -- or None. Never raises, never blocks (the map
    was stashed by the picker's pool lookup; at worst ONE throttled lookup)."""
    from resources.lib.sync.service import log
    try:
        from resources.lib.fetching import pool_client as _pool
        sm = _pool.sync_map(info)
        if not sm:
            return None
        sub_hash = key.split('|', 1)[0]
        registry_release = sync_registry_release(playing, cut_signature)
        ent = sm.get(sub_hash + '|' +
                     _pool.worker_norm_release(registry_release))
        if not isinstance(ent, dict):
            return None
        scale = float(ent.get('s') or 1.0)
        off = float(ent.get('o') or 0.0)
        st = ent.get('st')
        human = int(ent.get('h') or 0)
        diag = 'community record (votes=%s human=%s)' % (
            ent.get('n', 1), human)
        if st == 'CONFIRMED' or (scale == 1.0
                                 and abs(off) <= common.CONFIRM_OFFSET_MS):
            return {'status': common.STATUS_CONFIRMED, 'scale': 1.0,
                    'offset_ms': 0.0, 'diag': diag}
        if not (common.SCALE_MIN <= scale <= common.SCALE_MAX):
            return None
        if abs(off) > common.MAX_PLAUSIBLE_OFFSET_MS:
            return None
        # Don't blindly jump a sub by many seconds on one unverified AUTO vote
        # (poisoned-record guard) -- fall through to the locally-gated
        # deep-verify instead. Human-confirmed records apply at any magnitude.
        if abs(off) > _COMMUNITY_AUTO_MAX_OFFSET_MS and human < 1:
            log('community record skipped (large auto offset %+dms, no human '
                 'confirmation) -- deferring to local verify' % int(off),
                 level='DEBUG')
            return None
        return {'status': common.STATUS_FIXABLE, 'scale': scale,
                'offset_ms': off, 'diag': diag}
    except Exception as e:
        log('community verdict failed: %r' % e, level='DEBUG')
        return None


def finalize_delay_session(record, delay_s, watched_s):
    """Decide whether a finished viewing session yields a HUMAN sync report.
    Returns the report dict for pool.report_sync, or None.
      - settled manual delay >= 0.4s after >= 5 min watched -> FIXABLE
        (combined with whatever fix was already applied at delivery);
      - an untouched delay yields nothing (it proves nothing about timing).
    Pure function (no Kodi imports) so it is unit-testable."""
    try:
        if not record or watched_s < 300:
            return None
        # Human delay is one scalar.  It can refine a global transform, but it
        # must never flatten a multi-region correction into one offset or
        # publish that lossy value to the community registry.  Missing mode is
        # rejected too: current deliveries always write it, so absence means a
        # stale record from an older engine.
        if record.get('mode') != 'global':
            return None
        key = record.get('key') or ''
        playing = (record.get('playing') or '').strip()
        if not key or not playing:
            return None
        cut_signature = (record.get('cut_signature') or '').strip().lower()
        if (not re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature)
                or not key.endswith('|' + cut_signature)):
            # A release name is not a media identity.  If the tiny fingerprint
            # could not be obtained, keep the viewer's delay private rather than
            # teaching another cut a correction that may be wrong for it.
            return None
        sub_hash = key.split('|', 1)[0]
        base_scale = float(record.get('scale') or 1.0)
        base_off = float(record.get('offset') or 0.0)
        d = float(delay_s or 0.0)
        if abs(d) >= 0.4:
            # Kodi delay d shows subs d seconds LATER; our fix map is
            # t' = (t - offset)/scale, so the user's correction folds in as
            # offset' = offset - d*1000*scale (measured on the sub timeline).
            off = base_off - d * 1000.0 * base_scale
            if abs(off) > 240000:
                return None
            return {'sub_hash': sub_hash, 'release': playing,
                    'scale': base_scale, 'offset_ms': off,
                    'status': 'FIXABLE', 'origin': 'human',
                    'mode': 'global',
                    'cache_key': key,
                    'cut_signature': cut_signature,
                    'info': record.get('info') or {}}
        # An untouched delay is NOT evidence of good timing. Most viewers never
        # open the delay control: they tolerate a bad subtitle, switch it, or
        # stop watching. Reporting those sessions as HUMAN confirmations (which
        # outrank machine verdicts on the Worker and skip all later checks)
        # froze both unsynced originals and wrong automatic fixes as
        # "confirmed" for every viewer of that cut. Only a deliberate delay
        # change is a human timing signal.
        return None
    except Exception:
        return None


def store_human_verdict(report):
    """Persist a viewer-confirmed correction under its exact local cut key.

    The existing Worker schema remains unchanged; both its release field and
    this local key are namespaced with the content signature so another cut
    carrying the same release label cannot inherit the correction.
    """
    try:
        if (report or {}).get('mode') != 'global':
            return False
        key = (report or {}).get('cache_key') or ''
        sig = ((report or {}).get('cut_signature') or '').strip().lower()
        if not key or not re.fullmatch(r'cut1:[0-9a-f]{32}', sig):
            return False
        if not key.endswith('|' + sig):
            return False
        status = (report or {}).get('status')
        if status not in (common.STATUS_CONFIRMED,
                           common.STATUS_FIXABLE):
            return False
        verdict = {
            'status': status,
            'scale': float((report or {}).get('scale') or 1.0),
            'offset_ms': float((report or {}).get('offset_ms') or 0.0),
            'mode': 'global',
            'diag': 'human-confirmed exact media cut',
        }
        store_verdict(key, verdict)
        return True
    except Exception:
        return False


def verdict_strength(item):
    from resources.lib.sync.validators import track_language_rank
    verdict = item.get('verdict') or {}
    return (
        1 if verdict.get('status') == common.STATUS_CONFIRMED else 0,
        float(verdict.get('overlap') or 0.0),
        float(verdict.get('vote') or 0.0),
        float(verdict.get('unique') or 0.0),
        -track_language_rank(item.get('track') or {}),
        len(item.get('cues') or []),
    )
