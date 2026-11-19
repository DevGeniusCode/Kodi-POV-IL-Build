# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The deep verify: the slow verification that runs after the subtitle was delivered.

import re
import time
import hashlib
import threading
try:
    from resources.lib.scoring_matching import release as scoring_matching_release
except Exception:
    scoring_matching_release = None
try:
    from resources.lib.core.srt import cues as srt_cues
    from resources.lib.sync.align import common, estimate, retime, verify
except Exception:
    srt_cues = common = estimate = retime = verify = None
from resources.lib.sync.common import AUDIO_MAX_OFFSET_MS, AUDIO_SCALES, ORACLE_SOURCE_MIN_VOTE, ORACLE_SOURCE_SCALES, STATUS_ALTERNATE_APPLIED, STATUS_NO_ORACLE, yield_state
from resources.lib.sync.oracles import download_oracle, oracle_candidates
from resources.lib.sync.probes.audio import audio_probe_reference
from resources.lib.sync.probes.bundle import bundle_envelope, envelope_diag, envelope_hypothesis_search, file_timeline_conflict, probe_reference_bundle, verify_file_bundle
from resources.lib.sync.probes.cache import learn_cut_signature
from resources.lib.sync.probes.remote import current_stream_transport
from resources.lib.sync.validators import corroborate_rescued_oracle, guard_soft_probe_shift, provider_piecewise_rescue
from resources.lib.sync.verdicts import VERDICT_VERSION, cache_key, community_verdict, load_verdicts, store_verdict, sync_registry_release


# Audio-VAD boundaries are softer than subtitle cues -> relaxed vote/overlap,
# BUT hard discipline everywhere else: identity scale only (a sparse VAD
# reference cannot support scale estimation -- every extra scale candidate
# multiplies the spurious-peak chance) and a narrow offset window. The field
# incident: 10 sparse cues x 15 scales x +/-10min window "found" a bogus
# offset=-350s that shifted the subs out of sight.
_AUDIO_MIN_VOTE = 0.55
_AUDIO_MIN_OVERLAP = 0.65


# Kodi runs every add-on's Python under one interpreter lock. Measured in QA
# Kodi (21, Python 3.11): while one thread runs a CPU-bound loop that never
# sleeps, a NEW script -- e.g. our own "בחירת כתוביות" button -- does not even
# start until that loop ends (15.8 s behind a 20 s loop, 0.3 s idle); a 1 ms
# sleep every ~20 ms brings it back to 0.3 s. Field logs from an Android box
# showed the same 5-11 s waits behind SubSync's deep check at playback start.
# The long loops here call _breathe(): about 5% slower in the background,
# never in the way of the viewer.
_BREATHE_SLICE = 0.02
_breath = [0.0]


def breathe():
    now = time.monotonic()
    if now - _breath[0] >= _BREATHE_SLICE:
        time.sleep(0.001)
        _breath[0] = time.monotonic()
        _yield_to_viewer()


# On a Shield the 1 ms breaths were not enough: a new script got ~5% of the
# interpreter, so "בחירת כתוביות" started 6-10 s late behind the deep job's
# tournament, and the list the player returned to replayed in 1.5 s instead of
# 0.1 s while the job ran on after playback stopped (field logs, Oct 2026).
# The deep job (and only it -- never a request the viewer made) therefore
# STOPS while the player's menu or the chooser is open, and ends once the
# video it checks has stopped.
DEEP_THREAD = [None]
_YIELD_CHECK_S = 0.25
_YIELD_MAX_S = 30.0
_YIELD_JOB_MAX_S = 120.0   # all waits of one job together
_STOP_AFTER_S = 2.0
_VIEWER_BUSY = ('Window.IsActive(videoosd) | Window.IsVisible(script.moransubs.chooser.xml)'
                ' | Window.IsActive(subtitlesearch)')


class ViewerLeft(BaseException):
    """The video the deep job checks is no longer playing (BaseException, so
    the many best-effort ``except Exception`` blocks inside cannot hide it)."""


def _yield_to_viewer():
    if DEEP_THREAD[0] is None or threading.get_ident() != DEEP_THREAD[0]:
        return
    now = time.monotonic()
    if now - yield_state['checked'] < _YIELD_CHECK_S:
        return
    yield_state['checked'] = now
    try:
        import xbmc
        import xbmcgui
    except Exception:
        return
    monitor = None
    waited = 0.0
    while True:
        try:
            playing = xbmc.Player().isPlayingVideo()
            busy = (xbmc.getCondVisibility(_VIEWER_BUSY)
                    or _chooser_opening(xbmcgui))
        except Exception:
            return
        if playing:
            yield_state['seen_playing'] = True
            yield_state['gone'] = None
            if (not busy or waited >= _YIELD_MAX_S
                    or yield_state['waited'] >= _YIELD_JOB_MAX_S):
                return
        elif yield_state['seen_playing']:
            gone = yield_state['gone'] or time.monotonic()
            yield_state['gone'] = gone
            if time.monotonic() - gone >= _STOP_AFTER_S:
                raise ViewerLeft('playback ended')
        else:
            return          # not started yet: the remote reader waits for it
        if monitor is None:
            monitor = xbmc.Monitor()
        if monitor.waitForAbort(0.1):
            raise ViewerLeft('Kodi is closing')
        waited += 0.1
        yield_state['waited'] += 0.1
        yield_state['checked'] = time.monotonic()


def _chooser_opening(xbmcgui):
    """The chooser's busy stamp, by the chooser's own rule (a stamp older
    than a minute is a process that died)."""
    try:
        stamp = float(xbmcgui.Window(10000).getProperty('moransubs.chooser_busy') or 0)
    except ValueError:
        return False
    return 0 <= time.time() - stamp < 60


if srt_cues is not None:
    estimate.YIELD = _yield_to_viewer


def timing_attempt_key(text, cut_signature):
    """Identity of verifier input, independent of provider release labels.

    Only strict, well-formed SRTs qualify. The timing of *all* blocks and the
    dialogue-filtered subset both matter: captions with different words can
    share a timeline, while an SFX/credit change can change verifier input.
    Never persist this key or use it across playing-file cut signatures.
    """
    cues, error = srt_cues.preflight_srt(text)
    if error or not cut_signature:
        return None
    dialogue = srt_cues.dialogue_cues(cues)
    skeleton = (
        tuple((c['start'], c['end']) for c in cues),
        tuple((c['start'], c['end']) for c in dialogue),
    )
    return (cut_signature,
            hashlib.sha256(repr(skeleton).encode('ascii')).digest())


def remember_rejected_timing(job, text, cut_signature, verdict):
    """Memoize only a failed verification during this one first-play job."""
    if not isinstance(job, dict) or not isinstance(verdict, dict):
        return
    if verdict.get('status') not in (common.STATUS_UNKNOWN, None):
        return
    timing_key = timing_attempt_key(text, cut_signature)
    if timing_key:
        job.setdefault('_rejected_timing', set()).add(timing_key)


def deep_verify(info, path, text, rel, playing, key, early_job=None):
    """Cross-check the actual file, exact-cut memory, oracle, then local audio.

    The playing file is stronger than a release name, so it is evaluated first.
    Its content id also unlocks an exact-cut local/community verdict before any
    subtitle-provider download. Runs in the service worker; never raises.
    """
    from resources.lib.sync.jobs import apply_verified_human_fallback, job_matches_current, write_fixed
    from resources.lib.sync.service import log
    try:
        pinned = (info.get('_subsync_stream_url') or '').strip()
        if pinned and current_stream_transport() != pinned:
            return path, None

        # Profile the actual media first. Besides being the strongest timing
        # anchor, this produces the content id used by all learning below.
        bundle = probe_reference_bundle(info, playing)
        if pinned and current_stream_transport() != pinned:
            log('deep verify discarded: playback changed during media probe')
            return path, None
        if early_job and not job_matches_current(early_job):
            log('deep verify stopped: subtitle selection changed during '
                 'media probe')
            return path, None
        cut_signature = (bundle.get('cut_signature') or '').strip().lower()
        if not re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature):
            # A useful cue profile can survive optional signature-range
            # pressure. Retry only the tiny identity reader rather than throwing
            # away the timing evidence or caching it under a release-only key.
            cut_signature = learn_cut_signature(info)
            if not re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature or ''):
                cut_signature = ''
        final_key = cache_key(text, playing, cut_signature)
        accepted = (common.STATUS_CONFIRMED, common.STATUS_FIXABLE)

        # The first foreground pass may not have known the remote content id.
        # Once the profile reveals it, reuse an exact local verdict immediately.
        exact_cached = load_verdicts().get(final_key) if cut_signature else None
        if exact_cached and exact_cached.get('v') == VERDICT_VERSION:
            status = exact_cached.get('status')
            result = dict(exact_cached, cut_signature=cut_signature,
                          cache_key=final_key, cached=True)
            if status == common.STATUS_FIXABLE:
                try:
                    fixed = retime.apply_verdict(text, exact_cached)
                except Exception:
                    fixed = ''
                out = write_fixed(path, fixed)
                if out:
                    return out, dict(result, applied=True)
            if status in (common.STATUS_CONFIRMED,
                          common.STATUS_UNKNOWN):
                return path, result

        # Shared (community) learning is consulted only AFTER the playing file
        # and the provider oracle could not decide -- see below. It used to win
        # before either was even looked at, so one wrong shared record (older
        # clients reported every unadjusted 15-minute watch as a HUMAN
        # confirmation) silently disabled all verification for that cut.

        file_verdict, ref_kind, ref_count = verify_file_bundle(bundle, text, playing)
        remember_rejected_timing(early_job, text, cut_signature,
                                  file_verdict)
        if file_verdict:
            log('verdict for %r vs %s (%d ref cues): %s'
                 % (rel or '?', ref_kind, ref_count,
                    file_verdict.get('diag', '?')))

        # An MP4 without a subtitle track still carries its audio frame sizes.
        # They cannot fine-tune a timeline, but they can prove whether one
        # clock fits the whole episode.
        envelope = None
        envelope_before = None
        if not bundle.get('track_cues') and file_verdict is None:
            envelope = bundle_envelope(bundle)
            if envelope is not None:
                try:
                    envelope_before = verify.envelope_check(
                        envelope, text)
                except Exception:
                    envelope_before = None
                if envelope_before:
                    log('verdict for %r vs FILE AUDIO ENVELOPE: %s'
                         % (rel or '?', envelope_diag(envelope_before)))
                # Agreement is never proof: four-minute windows cannot see a
                # half-second error.  Only a 'conflict' is acted upon below.

        # Autosub has already discovered its Hebrew alternatives. If the
        # original cannot be proved against this exact file, check only rows
        # whose subtitle bytes are ALREADY cached against the media profile
        # BEFORE searching/downloading a foreign oracle. Do not start new
        # candidate downloads here: that could make the supposed fast path
        # slower. The ordinary late tournament retains its download fallback.
        # The helper itself checks the live selection/stream before changing
        # Kodi's subtitle, so a later manual pick always wins.
        if (early_job and early_job.get('fallback_links')
                and cut_signature and bundle.get('cues')
                and (not file_verdict
                     or file_verdict.get('status') not in accepted)
                and apply_verified_human_fallback(
                    early_job, cache_only=True)):
            log('autosub tournament: proven alternate selected before '
                 'provider oracle')
            return path, {'status': STATUS_ALTERNATE_APPLIED,
                          'cut_signature': cut_signature,
                          'cache_key': final_key}

        fixed_text = None
        transient_oracle_search = False
        if file_verdict and file_verdict.get('status') in accepted:
            verdict = file_verdict
        elif not playing:
            # Without a trustworthy release name no provider subtitle can be
            # proven to belong to this cut; keep the file's (non-)answer.
            log('no oracle: playing release unavailable')
            verdict = file_verdict
        else:
            # The file could not decide, so only now pay for a provider oracle.
            oracle_started = time.monotonic()
            cands = oracle_candidates(info)
            log('oracle: discovery in %.2fs (%d candidate(s))'
                 % (time.monotonic() - oracle_started, len(cands)))
            if early_job and not job_matches_current(early_job):
                log('deep verify stopped: subtitle selection changed during '
                     'oracle discovery')
                return path, None
            oracle, tier = (retime.pick_oracle(cands, playing)
                            if cands else (None, ''))
            oracle_verdict = None
            oracle_fixed = None
            oracle_text = ''
            if oracle is not None:
                download_started = time.monotonic()
                oracle_text = download_oracle(oracle['payload'])
                log('oracle: download in %.2fs (%d character(s))'
                     % (time.monotonic() - download_started,
                        len(oracle_text)))
                if early_job and not job_matches_current(early_job):
                    log('deep verify stopped: subtitle selection changed '
                         'during oracle download')
                    return path, None
                if oracle_text.strip():
                    okw = {}
                    if tier == scoring_matching_release.TIER_SOURCE:
                        okw = {'scales': ORACLE_SOURCE_SCALES,
                               'min_vote': ORACLE_SOURCE_MIN_VOTE}
                    oracle_fixed, oracle_verdict = retime.verify_and_fix(
                        oracle_text, text, **okw)
                    log('verdict for %r vs oracle %r [%s]: %s'
                         % (rel or '?', oracle['release'], tier,
                            oracle_verdict['diag']))
                    if (oracle_verdict.get('status')
                            == common.STATUS_FIXABLE
                            and oracle_verdict.get('coverage_rescue')):
                        oracle_verdict = corroborate_rescued_oracle(
                            cands, playing, text, oracle, oracle_text,
                            oracle_verdict, early_job=early_job)
                        log('oracle rescue corroboration: %s'
                             % oracle_verdict.get('diag', '?')[-160:])
                        if (oracle_verdict.get('status')
                                != common.STATUS_FIXABLE):
                            oracle_fixed = None
                    if (oracle_verdict.get('status')
                            == common.STATUS_UNKNOWN
                            and oracle_verdict.get('reason') not in (
                                'oracle_conflict',)):
                        rescue_state = {}
                        rescue = provider_piecewise_rescue(
                            info, cands, playing, text, oracle, oracle_text,
                            search_state=rescue_state)
                        transient_oracle_search = bool(
                            rescue_state.get('transient'))
                        if rescue:
                            oracle_verdict = rescue['verdict']
                            try:
                                oracle_fixed = retime.apply_verdict(
                                    text, oracle_verdict)
                            except Exception:
                                oracle_fixed = None
                            if oracle_fixed and oracle_fixed.strip():
                                log('provider-consensus piecewise rescue for '
                                     '%r: %s' % (
                                         rel or '?',
                                         oracle_verdict.get('diag', '?')))
                            else:
                                oracle_verdict = dict(
                                    oracle_verdict,
                                    status=common.STATUS_UNKNOWN,
                                    diag=(oracle_verdict.get('diag', '')
                                          + ' | structural apply failed'))
            else:
                try:
                    scored = sorted(
                        ((scoring_matching_release.match_pct(playing, c['release']),
                          scoring_matching_release.match_tier(playing, c['release']),
                          c['release']) for c in cands), reverse=True)[:5]
                    top = '; '.join('%d%%/%s %r' % s for s in scored) or '-'
                except Exception:
                    top = '?'
                log('no oracle for release %r (%d foreign candidates); '
                     'closest: %s' % (playing, len(cands), top))
            if oracle_verdict and oracle_verdict.get('status') in accepted:
                verdict = oracle_verdict
                fixed_text = oracle_fixed
            else:
                verdict = file_verdict or oracle_verdict
                # The delivered row cannot be proven against an exact /
                # same-group reference. When the file has no subtitle
                # timeline of its own, that reference is the best evidence:
                # let it pick an already-discovered Hebrew row that IS proven.
                if (early_job and early_job.get('fallback_links')
                        and oracle is not None and oracle_text.strip()
                        and not bundle.get('cues')
                        and apply_verified_human_fallback(
                            early_job, reference={
                                'text': oracle_text, 'tier': tier,
                                'release': oracle.get('release')})):
                    log('autosub tournament: proven alternate selected '
                         'against oracle %r' % oracle.get('release'))
                    return path, {'status': STATUS_ALTERNATE_APPLIED,
                                  'cut_signature': cut_signature,
                                  'cache_key': final_key}

        # Shared exact-cut learning, last: only when neither the playing file
        # nor a provider oracle could decide. A CONFIRMED record proves nothing
        # here (see finalize_delay_session); a FIXABLE one is still somebody's
        # deliberate correction for these exact media bytes.
        if (cut_signature and playing
                and (verdict is None or verdict.get('status') not in accepted)):
            community = community_verdict(
                info, final_key, playing, cut_signature=cut_signature)
            if (community
                    and community.get('status') == common.STATUS_FIXABLE):
                try:
                    community_fixed = retime.apply_verdict(text, community)
                except Exception:
                    community_fixed = ''
                if community_fixed and community_fixed.strip():
                    log('community FIXABLE used after file/oracle abstained: '
                         + community.get('diag', ''))
                    verdict = dict(community, community=True)
                    fixed_text = community_fixed

        # With no usable embedded timeline, local AAC speech remains the final
        # fallback.  Remote audio is deliberately not opened: the proven-safe
        # remote path is the tiny cue/signature reader above.
        if (file_verdict is None
                and (verdict is None or verdict.get('status') not in accepted)):
            ref_cues = audio_probe_reference(info, playing)
            if ref_cues:
                gate_kw = {'min_vote': _AUDIO_MIN_VOTE,
                           'min_overlap': _AUDIO_MIN_OVERLAP,
                           'scales': AUDIO_SCALES,
                           'max_offset_ms': AUDIO_MAX_OFFSET_MS}
                verdict = verify.verify_cues(ref_cues, text, **gate_kw)
                log('verdict for %r vs AUDIO PROBE (%d ref cues): %s'
                     % (rel or '?', len(ref_cues), verdict['diag']))
                if (verdict['status'] == common.STATUS_UNKNOWN
                        and 'tight check FAILED' in verdict.get('diag', '')
                        and verdict.get('vote', 0) >= 0.8):
                    more = audio_probe_reference(info, playing,
                                                  second_pass=True)
                    if more and len(more) > len(ref_cues):
                        verdict = verify.verify_cues(
                            more, text, **gate_kw)
                        log('audio verdict (pass 2, %d cues): %s'
                             % (len(more), verdict['diag']))
                verdict = guard_soft_probe_shift(verdict, 'AUDIO PROBE')
                if (verdict.get('status') == common.STATUS_FIXABLE
                        and verdict.get('coverage_rescue')):
                    # Speech-detector onsets carry ~1s of error; they cannot
                    # stand in for the onset agreement a rescue relaxes.
                    verdict = dict(verdict, status=common.STATUS_UNKNOWN,
                                   reason='audio_rescue_refused')

        envelope_searched = False
        envelope_state = {}
        if (envelope is not None and envelope_before
                and envelope_before.get('status') == 'conflict'
                and (verdict is None
                     or verdict.get('status') not in accepted)):
            envelope_searched = True
            found = envelope_hypothesis_search(
                info, text, envelope, envelope_before, early_job=early_job,
                state=envelope_state)
            if found:
                verdict, fixed_text = found
            elif verdict is None:
                verdict = {'status': common.STATUS_UNKNOWN,
                           'scale': 1.0, 'offset_ms': 0.0,
                           'reason': 'file_timeline_conflict',
                           'diag': envelope_diag(envelope_before)}

        if verdict is None:
            return path, {'status': STATUS_NO_ORACLE,
                          'cut_signature': cut_signature,
                          'cache_key': final_key}

        if verdict.get('status') == common.STATUS_FIXABLE and not fixed_text:
            try:
                fixed_text = retime.apply_verdict(text, verdict)
            except Exception:
                fixed_text = None
            if not (fixed_text and fixed_text.strip()):
                verdict = dict(verdict, status=common.STATUS_UNKNOWN)

        # The playing file is the ground truth for its own cut.  A provider or
        # community answer that still leaves the delivered text on two
        # different plateaus of the file's own subtitle timeline cannot be
        # right for this file, however well it matched its own reference.
        if (verdict is not file_verdict
                and verdict.get('status') in accepted
                and not verdict.get('envelope_status')):
            delivered = (fixed_text if verdict.get('status')
                         == common.STATUS_FIXABLE else text)
            conflict = file_timeline_conflict(bundle, delivered)
            if conflict:
                log('file timeline contradicts the %s answer: plateaus %s'
                     % ('community' if verdict.get('community')
                        else 'provider',
                        ', '.join('%+dms x%d' % (int(offset), count)
                                  for offset, count in conflict)))
                verdict = dict(
                    verdict, status=common.STATUS_UNKNOWN,
                    reason='file_timeline_conflict',
                    diag=(verdict.get('diag', '')
                          + ' | contradicted by the playing file'))
                fixed_text = None

        if (envelope is not None and envelope_before and not envelope_searched
                and envelope_before.get('status') == 'conflict'
                and verdict.get('status') not in accepted):
            found = envelope_hypothesis_search(
                info, text, envelope, envelope_before, early_job=early_job,
                state=envelope_state)
            if found:
                verdict, fixed_text = found
        if (envelope_state.get('transient')
                and verdict.get('status') not in accepted):
            transient_oracle_search = True

        if pinned and current_stream_transport() != pinned:
            log('deep verify discarded: playback changed before commit')
            return path, None
        verdict = dict(verdict, cut_signature=cut_signature,
                       cache_key=final_key)
        if (cut_signature
                and not (transient_oracle_search
                         and verdict.get('status')
                         == common.STATUS_UNKNOWN)):
            store_verdict(final_key, verdict)

        # Reuse the existing Worker protocol by namespacing its release key with
        # the content signature.  Old clients keep their legacy records; new
        # clients never apply one cut's vote to another cut with the same name.
        try:
            if (cut_signature and playing and verdict['status'] in accepted
                    and verdict.get('mode', 'global') == 'global'
                    and not verdict.get('community')
                    and verdict.get('reference') != 'audio-envelope'
                    and not verdict.get('envelope_status')):
                from resources.lib.ai_translation.reuse import pool_contribute as _pool
                _pool.report_sync(
                    info, final_key.split('|', 1)[0],
                    sync_registry_release(playing, cut_signature),
                    verdict.get('scale', 1.0),
                    verdict.get('offset_ms', 0.0), verdict['status'],
                    origin='auto')
        except Exception:
            pass

        if verdict['status'] == common.STATUS_FIXABLE:
            out = write_fixed(path, fixed_text)
            if out:
                return out, dict(verdict, applied=True)
        return path, verdict
    except Exception as e:
        log('deep verify failed (fail-open): %r' % e, level='WARNING')
        return path, None
