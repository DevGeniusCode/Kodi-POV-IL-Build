# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The sync gate: process() verifies and fixes a subtitle before it is delivered (fail-open).

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
    from resources.lib.sync.align import common, retime
except Exception:
    common = retime = None
from resources.lib.sync.common import fix_badge
from resources.lib.sync.delivery_record import clear_delivery, record_delivery
from resources.lib.sync.jobs import clear_job_pending, enqueue_deep, fix_notice, mark_pending, selection_matches, write_fixed
from resources.lib.sync.probes.bundle import cached_reference_bundle
from resources.lib.sync.probes.cache import known_cut_signature
from resources.lib.sync.probes.remote import playing_url, remote_playing_url
from resources.lib.sync.verdicts import STATUS_USER_VETO, VERDICT_VERSION, cache_key, community_verdict, is_vetoed, load_verdicts, store_verdict
from resources.lib.core.srt.cues import STATUS_UNKNOWN


# Trusted tiers need no verification at delivery time (same release / same
# group+source are de-facto synced; S3+ may still cross-check them cheaply).
_STATUS_TRUSTED = 'TRUSTED'


def selection_snapshot():
    try:
        return core_selection.current_subtitle_selection() or {}
    except Exception:
        return {}


def publish_selection_status(state, source='local', selection=None, badge=None):
    """Update the current pick's tiny UI record; never performs network I/O. ``badge`` (common.fix_badge)
    accompanies a 'fixed' state."""
    try:
        if kodi is not None:
            expected = selection or {}
            if not all(expected.get(k) for k in (
                    'token', 'link_hash', 'stream_hash')):
                return False
            return bool(core_selection.set_subtitle_sync_status(
                state, source=source,
                selection_token=expected.get('token') or '',
                link_hash=expected.get('link_hash') or '',
                stream_hash=expected.get('stream_hash') or '',
                badge=badge))
    except Exception:
        return False
    return False


def log(msg, level='INFO'):
    try:
        kodi.log('subsync: ' + msg, level=level)
    except Exception:
        pass


def enabled():
    try:
        return (settings.get_setting('subsync_verify', 'true') or
                'true').strip().lower() != 'false'
    except Exception:
        return True


def playing_release(info):
    """The playing stream's release name (same priority order the picker's %
    uses), or '' when unknown/synthetic -- a synthesized player filename must
    never anchor verification."""
    try:
        ref = video_context.release_name(info, 'sync')
        if not ref or scoring_matching_release is None:
            return ''
        if scoring_matching_release.is_synthetic(ref):
            return ''
        # POV can expose a season-pack release for the playing episode, e.g.
        # ``Show.S01.1080p...``.  Ranking/caching that incomplete identity made
        # every real S01E06 subtitle look unrelated and also prevented a
        # field-proven timing map from matching.  Kodi's player metadata is
        # already bound to the active item, so fill only the missing episode
        # part of an explicit, matching season token.  Never rewrite an
        # existing SxxEyy or a token naming a different season.
        try:
            episodic = (info.get('is_episode') is True
                        or str(info.get('is_episode') or '').strip().lower()
                        == 'true')
            media_type = str(info.get('media_type') or '').strip().lower()
            season = int(str(info.get('season') or '').strip())
            episode = int(str(info.get('episode') or '').strip())
            if (episodic and media_type != 'movie'
                    and season > 0 and episode > 0
                    and not re.search(r'(?i)(?<![a-z0-9])s\d{1,2}e\d{1,3}'
                                      r'(?![a-z0-9])', ref)):
                season_token = re.compile(
                    r'(?i)(?<![a-z0-9])s0*(\d{1,2})(?![a-z0-9])')
                match = season_token.search(ref)
                if match and int(match.group(1)) == season:
                    ref = (ref[:match.start()]
                           + 'S%02dE%02d' % (season, episode)
                           + ref[match.end():])
        except (TypeError, ValueError):
            pass
        return ref
    except Exception:
        return ''


# ---- main entry -------------------------------------------------------------

def process(info, path, delivered_release, selection=None, fallback_link='',
            fallback_links=None):
    """Verify (and when confidently possible, FIX) the timing of the Hebrew
    sub at `path` against the playing release. Returns (final_path, verdict)
    where verdict is a dict with at least {'status'} plus 'applied': True when
    a retimed copy was written and returned, or (path, None) when SubSync did
    not run (disabled / no anchor / unreadable file). Fail-open, never raises."""
    try:
        # The caller snapshots the selected candidate when resolve() starts.
        # A download/AI translation may finish much later; by then another
        # subtitle may be current. Never cancel, label, learn from or queue the
        # old result against that newer selection.
        selection = (selection_snapshot()
                     if selection is None else selection)
        if not selection_matches(selection):
            log('delivery skipped: selection no longer owns player')
            return path, None
        if common is None or scoring_matching_release is None or not enabled():
            log('delivery skipped: verifier unavailable or disabled')
            return path, None
        if not path or not os.path.isfile(path):
            return path, None
        playing = playing_release(info)
        if not playing and not (playing_url(info)
                                or remote_playing_url(info)):
            log('delivery unverified: playing release unavailable')
            core_selection.stage_subtitle_delivery(
                path, selection=selection,
                status='unverified', source='no-release')
            return path, None
        if not playing:
            # No trustworthy release name (e.g. "Episode.Title.mp4"): nothing
            # can anchor a provider oracle or a shared record, but the playing
            # file's own subtitle timeline needs no name at all.
            log('playing release unavailable; file evidence only')

        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            return path, None
        if not text.strip():
            return path, None
        cut_signature = known_cut_signature(info, playing)
        key = cache_key(text, playing, cut_signature)

        # The viewer pressed "בטל סנכרון אוטומטי" for this exact (subtitle, cut): deliver it as it is and
        # never verify, fix or report it again (a local decision, nothing goes to the community pool).
        if is_vetoed(key):
            log('delivery kept as delivered: the viewer cancelled the '
                 'automatic sync of this subtitle for this cut')
            record_delivery(info, playing, key, 1.0, 0.0,
                             cut_signature=cut_signature,
                             selection=selection)
            core_selection.stage_subtitle_delivery(
                path, selection=selection, status='', source='user')
            return path, {'status': STATUS_USER_VETO, 'vetoed': True}

        # Trusted tier -> synced by release identity; nothing to do (still
        # recorded so a manual-delay fix on it feeds the community registry).
        rel = (delivered_release or '').strip()
        if rel:
            _pct, tier, _ = scoring_matching_release.score(playing, rel)
            if tier in scoring_matching_release.AUTO_OK_TIERS:
                log('delivery trusted by release tier: %s (%r for %r)'
                     % (tier, rel, playing))
                record_delivery(info, playing, key, 1.0, 0.0,
                                 cut_signature=cut_signature,
                                 selection=selection)
                # A release label is a claim, not proof: Hebrew uploads are
                # routinely tagged with a release they were never timed to.
                # When the playing file carries its own subtitle timeline, the
                # service checks the trusted row against THAT (file evidence
                # only -- no provider oracle, audio or alternate rows) and
                # corrects it only on a proven >=1s shift. The same job learns
                # the content id a later manual delay needs. Stage first: the
                # job must only ever observe an already-staged delivery.
                core_selection.stage_subtitle_delivery(
                    path, selection=selection,
                    status='confirmed', source='release')
                if playing_url(info) or remote_playing_url(info):
                    marked = mark_pending(key, selection=selection)
                    if (marked and not enqueue_deep(
                            info, path, rel, playing, key,
                            file_only=True, selection=selection,
                            fallback_links=fallback_links)):
                        log('file-check job queue unavailable', level='DEBUG')
                return path, {'status': _STATUS_TRUSTED, 'tier': tier}

        cached = load_verdicts().get(key)
        if cached and cached.get('v') != VERDICT_VERSION:
            cached = None   # stored by an older engine -- recompute
        if cached:
            status = cached.get('status')
            if status == common.STATUS_FIXABLE:
                try:
                    fixed = retime.apply_verdict(text, cached)
                except Exception as e:
                    log('cached FIXABLE failed structural recheck: %r' % e,
                         level='WARNING')
                    fixed = ''
                out = write_fixed(path, fixed)
                if out:
                    # Quiet on repeat plays: the fix was announced ONCE when it
                    # was first computed; from then on it just works silently.
                    log('cached FIXABLE applied: ' + cached.get('diag', ''))
                    if cached.get('mode', 'global') == 'global':
                        record_delivery(info, playing, key,
                                         cached.get('scale', 1.0),
                                         cached.get('offset_ms', 0.0),
                                         cut_signature=cut_signature,
                                         selection=selection)
                    else:
                        # A scalar manual-delay record cannot describe a
                        # subtitle corrected by several timeline regions.
                        # Remove the original zero/global record immediately,
                        # before the delay watcher can learn or share it.
                        clear_delivery(selection=selection)
                    core_selection.stage_subtitle_sync_fix(
                        out, selection=selection, source='cache',
                        badge=fix_badge(cached, path, [key]))
                    return out, {'status': status, 'applied': True,
                                 'offset_ms': cached.get('offset_ms', 0.0),
                                 'scale': cached.get('scale', 1.0),
                                 'mode': cached.get('mode', 'global'),
                                 'segments': cached.get('segments') or [],
                                 'diag': cached.get('diag', ''), 'cached': True}
            if status in (common.STATUS_CONFIRMED,
                          STATUS_UNKNOWN):
                record_delivery(info, playing, key, 1.0, 0.0,
                                 cut_signature=cut_signature,
                                 selection=selection)
                core_selection.stage_subtitle_delivery(
                    path, selection=selection,
                    status=('confirmed'
                            if status == common.STATUS_CONFIRMED
                            else 'unverified'),
                    source='cache')
                if (status == STATUS_UNKNOWN
                        and (fallback_link or fallback_links)
                        and mark_pending(key, selection=selection)):
                    enqueue_deep(
                        info, path, rel, playing, key, selection=selection,
                        fallback_link=fallback_link,
                        fallback_links=fallback_links)
                return path, {'status': status, 'cached': True,
                              'diag': cached.get('diag', '')}

        # COMMUNITY registry (S3): a verdict some other device (or a human
        # delay-fix) already established for this exact (subtitle, release)
        # pair -- served inside the pool /lookup the picker already made, so
        # this is a dict lookup, not a request. First hit on THIS device gets
        # the one gentle toast; it's stored locally so repeats are silent.
        # A remote first play has no exact cut id yet.  Do not apply the old
        # release-only community record blindly; the background verifier will
        # fingerprint the actual file and consult/store the scoped namespace.
        cv = (None if ((remote_playing_url(info) and not cut_signature)
                       or not playing) else
              community_verdict(info, key, playing,
                                 cut_signature=cut_signature))
        # A community record is a shortcut, never stronger evidence than the
        # playing file. When this exact transport already has a cached
        # subtitle timeline, let the background worker judge against it first
        # (it consults the community record only if the file cannot decide).
        # A CONFIRMED record never skips that check: older clients reported
        # any unadjusted 15-minute watch as a human confirmation.
        if cv is not None and (
                cv.get('status') == common.STATUS_CONFIRMED
                or cached_reference_bundle(info).get('cues')):
            log('community record deferred to file verification (%s)'
                 % cv.get('status'))
            cv = None
        if cv is not None:
            status = cv.get('status')
            if status == common.STATUS_FIXABLE:
                try:
                    fixed = retime.apply_verdict(text, cv)
                except Exception:
                    fixed = ''
                out = write_fixed(path, fixed)
                if out:
                    store_verdict(key, cv)
                    log('community FIXABLE applied: ' + cv.get('diag', ''))
                    verdict = dict(cv, applied=True, community=True)
                    if cv.get('mode', 'global') == 'global':
                        record_delivery(info, playing, key,
                                         cv.get('scale', 1.0),
                                         cv.get('offset_ms', 0.0),
                                         cut_signature=cut_signature,
                                         selection=selection)
                    else:
                        clear_delivery(selection=selection)
                    core_selection.stage_subtitle_sync_fix(
                        out, selection=selection, source='community',
                        notice=fix_notice(verdict),
                        badge=fix_badge(verdict, path, [key]))
                    return out, verdict
            elif status == common.STATUS_CONFIRMED:
                store_verdict(key, cv)
                log('community CONFIRMED: ' + cv.get('diag', ''))
                record_delivery(info, playing, key, 1.0, 0.0,
                                 cut_signature=cut_signature,
                                 selection=selection)
                core_selection.stage_subtitle_delivery(
                    path, selection=selection,
                    status='confirmed', source='community')
                return path, dict(cv, community=True)

        # DEEP verification needed (oracle download / file probe / audio) --
        # NEVER inline: it can take 10-30s and used to hold the autosub
        # "searching subtitles" overlay up (and the picker spinner) the whole
        # time. Hand the job to the long-lived service (same disk-queue
        # pattern as the he_warm drainer), deliver the ORIGINAL file now, and
        # let the worker swap in a fixed copy when (and only when) it proves
        # one -- self-healing delivery, per the plan's latency budget.
        marked = mark_pending(key, selection=selection)
        delivery_staged = bool(
            marked and core_selection.stage_subtitle_delivery(
                path, selection=selection))
        # Publish before the job file becomes visible to the service. A fast
        # worker can only replace "checking" with its result, never have this
        # foreground path overwrite a verdict that has already completed.
        if delivery_staged:
            publish_selection_status(
                'checking', 'local', selection=selection)
        record_delivery(info, playing, key, 1.0, 0.0,
                         cut_signature=cut_signature, selection=selection)
        if (delivery_staged and enqueue_deep(
                info, path, rel, playing, key, selection=selection,
                fallback_link=fallback_link,
                fallback_links=fallback_links)):
            return path, {'status': 'PENDING'}
        # Remove only this selection's half-created coordination records. A
        # newer pick has another token and cannot be disturbed here.
        try:
            core_selection.clear_subtitle_delivery(path, selection=selection)
        except Exception:
            pass
        clear_job_pending({
            'key': key,
            'selection_token': selection.get('token') or '',
            'selection_hash': selection.get('link_hash') or '',
            'stream_hash': selection.get('stream_hash') or '',
        })
        # Queue unwritable (rare): keep playback responsive and the chosen
        # subtitle untouched.  Deep verification can include provider and media
        # reads; doing it synchronously here would bring the old 10-30s picker
        # freeze back precisely when the background service is unavailable.
        core_selection.stage_subtitle_delivery(
            path, selection=selection,
            status='unverified', source='queue')
        log('deep verify deferred: service queue unavailable', level='WARNING')
        return path, {'status': 'DEFERRED'}
    except Exception as e:
        log('process failed (fail-open): %r' % e, level='WARNING')
        return path, None
