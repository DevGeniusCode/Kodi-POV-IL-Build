# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The deep-verify job queue: enqueue, pending marks, the drainer and the fallbacks it applies.

import os
import re
import json
import time
import hashlib
import threading
try:
    from resources.lib.core import kodi, selection as core_selection, settings, video_context
except Exception:
    kodi = core_selection = settings = video_context = None
try:
    from resources.lib.scoring_matching import release as scoring_matching_release
except Exception:
    scoring_matching_release = None
try:
    from resources.lib.sync.align import common, retime, verify
except Exception:
    common = retime = verify = None
from resources.lib.sync.candidates import is_human_hebrew_candidate, ready_candidate_text
from resources.lib.sync.common import INFO_KEYS, STATUS_ALTERNATE_APPLIED, STATUS_NO_ORACLE, yield_state
from resources.lib.sync.deep import ViewerLeft, DEEP_THREAD, deep_verify, remember_rejected_timing, timing_attempt_key
from resources.lib.sync.delivery_record import clear_delivery, record_delivery
from resources.lib.core.link_codec import decode_link_safe
from resources.lib.sync.probes.bundle import bundle_envelope, cached_reference_bundle, envelope_diag, envelope_hypothesis_search, probe_reference_bundle, verify_file_bundle
from resources.lib.sync.probes.cache import learn_cut_signature
from resources.lib.sync.probes.remote import current_stream_transport
from resources.lib.sync.verdicts import VERDICT_VERSION, cache_key, load_verdicts, store_verdict
from resources.lib.core.srt.cues import STATUS_UNKNOWN


# ---- background worker plumbing (service-side) -------------------------------

_QUEUE_DIR = ('special://profile/addon_data/service.subtitles.kodipovilai/'
              'subsync_queue')
_PENDING_PROP = 'subsync.pending'
_JOB_FRESH_S = 120


def _queue_dir():
    try:
        import xbmcvfs
        return xbmcvfs.translatePath(_QUEUE_DIR)
    except Exception:
        return ''


def selection_matches(selection):
    expected = selection or {}
    try:
        return bool(expected.get('token') and expected.get('link_hash')
                    and expected.get('stream_hash')
                    and core_selection.subtitle_selection_matches(
                        expected.get('token'), expected.get('link_hash'),
                        expected.get('stream_hash')))
    except Exception:
        return False


def _pending_prop(selection=None):
    """Per-selection marker name, so concurrent picks cannot overwrite it."""
    expected = selection or {}
    token = expected.get('token') or ''
    if not token:
        try:
            token = core_selection.get_subtitle_selection_token() or ''
        except Exception:
            token = ''
    return _PENDING_PROP + ('.' + token if token else '')


def mark_pending(key, selection=None):
    """Remember which (sub, release) pair we delivered un-verified, so the
    worker only swaps if the user hasn't picked something else meanwhile."""
    from resources.lib.sync.service import selection_snapshot
    try:
        import xbmcgui
        expected = (selection_snapshot()
                    if selection is None else selection)
        if not selection_matches(expected):
            return False
        payload = {
            'key': key, 'ts': time.time(),
            'selection_token': expected.get('token') or '',
            'selection_hash': expected.get('link_hash') or '',
            'stream_hash': expected.get('stream_hash') or '',
        }
        win = xbmcgui.Window(10000)
        raw = json.dumps(payload, separators=(',', ':'))
        prop = _pending_prop(expected)
        win.setProperty(prop, raw)
        if not selection_matches(expected):
            if win.getProperty(prop) == raw:
                win.clearProperty(prop)
            return False
        return True
    except Exception:
        return False


def cancel_pending():
    """Invalidate an older background job after a new subtitle selection.

    The old job may still finish and cache its verdict, but it must not hot-swap
    over a newer TRUSTED, cached or embedded subtitle on the same stream.
    """
    try:
        import xbmcgui
        win = xbmcgui.Window(10000)
        win.clearProperty(_pending_prop())
        win.clearProperty(_PENDING_PROP)  # pre-token release residue
    except Exception:
        pass


def _pending_record(selection=None):
    try:
        import xbmcgui
        raw = xbmcgui.Window(10000).getProperty(
            _pending_prop(selection)) or ''
        return (json.loads(raw) or {}) if raw else {}
    except Exception:
        return {}


def clear_job_pending(job):
    """Clear only this job's token-scoped marker; never another selection's."""
    try:
        import xbmcgui
        expected = {
            'token': job.get('selection_token') or '',
            'link_hash': job.get('selection_hash') or '',
            'stream_hash': job.get('stream_hash') or '',
        }
        prop = _pending_prop(expected)
        win = xbmcgui.Window(10000)
        raw = win.getProperty(prop) or ''
        record = json.loads(raw) if raw else {}
        if (record.get('key') == job.get('key')
                and record.get('selection_token') == expected['token']
                and record.get('selection_hash') == expected['link_hash']
                and record.get('stream_hash') == expected['stream_hash']):
            win.clearProperty(prop)
            return True
    except Exception:
        pass
    return False


def enqueue_deep(info, path, rel, playing, key, identity_only=False,
                  selection=None, fallback_link='', fallback_links=None,
                  file_only=False):
    """Drop a deep-verify job for the service drainer. True on success."""
    from resources.lib.sync.service import log, selection_snapshot
    d = _queue_dir()
    if not d:
        return False
    try:
        expected = (selection_snapshot()
                    if selection is None else selection)
        if not selection_matches(expected):
            return False
        os.makedirs(d, exist_ok=True)
        # The readable prefix alone used to collide when two long release keys
        # differed after character 80.  A full-key digest makes the queue name
        # unambiguous; identity-only and full verification are separate jobs so
        # a cheap trusted-subtitle task can never swallow a later timing task.
        job_identity = (key + ('|identity' if identity_only else
                               '|file' if file_only else '|verify')
                        + '|' + (expected.get('token') or ''))
        prefix = re.sub(r'[^0-9A-Za-z]+', '_', key)[:48] or 'job'
        safe = prefix + '_' + hashlib.sha1(
            job_identity.encode('utf-8', 'replace')).hexdigest()[:16]
        jpath = os.path.join(d, safe + '.json')
        try:
            if (os.path.isfile(jpath)
                    and time.time() - os.path.getmtime(jpath) < _JOB_FRESH_S):
                return True   # identical job already queued
        except OSError:
            pass
        job = {
            'key': key, 'path': path, 'release': rel, 'playing': playing,
            'identity_only': bool(identity_only),
            'file_only': bool(file_only) and not identity_only,
            'ts': time.time(),
            'selection_token': expected.get('token') or '',
            'selection_hash': expected.get('link_hash') or '',
            'stream_hash': expected.get('stream_hash') or '',
            # Capture the actual stream identity at delivery time.  Metadata's
            # filepath is optional and may be absent; without this value a
            # later background result must never hot-swap into another video.
            'stream_url': current_stream_transport(),
            # Autosub-only escape hatch, revalidated by the worker before use.
            'fallback_link': fallback_link or '',
            # Other human Hebrew rows discovered by the same autosub search.
            # The worker tries at most a small bounded prefix and stops at the
            # first candidate proven against this exact media cue profile.
            'fallback_links': [str(link) for link in
                               list(fallback_links or [])[:6] if link],
            'info': {k: info.get(k) for k in INFO_KEYS
                     if isinstance(info.get(k), (str, int, float, bool))},
        }
        tmp = jpath + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(job, f, ensure_ascii=False)
        # Last gate before the job becomes visible to the service. A newer
        # selection may have landed while this small file was being written.
        if not selection_matches(expected):
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
        os.replace(tmp, jpath)
        log('deep job enqueued for %r' % key)
        return True
    except Exception as e:
        log('enqueue failed: %r' % e, level='WARNING')
        return False


def _apply_proven_pool_fallback(job):
    """Replace an unalignable autosub human row with release-proven pool AI.

    A manual subtitle pick or source change invalidates the job. If retrieval or
    Kodi registration fails, restore the human row's identity bookkeeping; the
    human file already on screen is never removed by a failed fallback.
    """
    from resources.lib.sync.service import log
    link = job.get('fallback_link') or ''
    if (not link or not job_matches_current(job)
            or not _job_stream_is_current(job)):
        return False
    payload = decode_link_safe(link) or {}
    if (payload.get('type') != 'pool'
            or (payload.get('pool_kind') or 'ai') == 'ktuvit'):
        return False
    playing = job.get('playing') or ''
    rel = (payload.get('release') or '').strip()
    if not (playing and rel and scoring_matching_release is not None):
        return False
    _pct, tier, _diag = scoring_matching_release.score(playing, rel)
    if tier not in scoring_matching_release.AUTO_OK_TIERS:
        return False

    old_link = core_selection.get_current_subtitle() or ''
    fallback_selection = {}
    try:
        core_selection.set_current_subtitle(link)
        selection = core_selection.current_subtitle_selection(
            expected_link=link) or {}
        fallback_selection = dict(selection)
        if (selection.get('stream_hash') != (job.get('stream_hash') or '')
                or not selection_matches(selection)):
            raise RuntimeError('fallback selection/stream changed')
        # Lazy import avoids the translate -> subsync module cycle.
        from resources.lib import pipeline
        path = pipeline.resolve(
            link, dict(job.get('info') or {}), selection=selection)
        if (not path or not selection_matches(selection)
                or not core_selection.apply_subtitle_file(
                    path, selection=selection)):
            raise RuntimeError('fallback delivery was not acknowledged')
        log('autosub fallback: release-proven pool AI replaced '
             'unalignable human')
        try:
            kodi.notify(
                'הכתובית האנושית לא התאימה · הוחלה כתובית מסונכרנת',
                time_ms=4500)
        except Exception:
            pass
        return True
    except Exception as e:
        log('autosub AI fallback skipped: %r' % e, level='WARNING')
        try:
            # A user can manually choose the same AI row while resolve is in
            # flight. Its link is identical but its new selection token is
            # authoritative; never overwrite that manual choice on failure.
            if (old_link and fallback_selection
                    and core_selection.get_current_subtitle() == link
                    and selection_matches(fallback_selection)):
                core_selection.set_current_subtitle(old_link)
                restored = core_selection.current_subtitle_selection(
                    expected_link=old_link) or {}
                if selection_matches(restored):
                    core_selection.set_subtitle_sync_status(
                        'unverified', source='fallback-failed',
                        selection_token=restored.get('token') or '',
                        link_hash=restored.get('link_hash') or '',
                        stream_hash=restored.get('stream_hash') or '')
        except Exception:
            pass
        return False


def _tournament_candidate_text(info, link, cache_only=False):
    """Obtain one already-discovered candidate for timing comparison.

    Prefer disk/pool memory.  A cache miss may perform the candidate's normal
    single subtitle download, but never repeats the provider search and never
    opens the media URL; the exact media cue profile was built once by the
    current deep job.
    """
    from resources.lib.sync.service import log
    candidate = {'link': link, 'language': 'he'}
    text, payload = ready_candidate_text(info, candidate)
    if text:
        return text, payload
    payload = decode_link_safe(link) or {}
    if cache_only:
        return '', payload
    if not is_human_hebrew_candidate(payload):
        return '', payload
    try:
        kind = payload.get('type')
        path = ''
        if kind == 'pool':
            from resources.lib.ai_translation.reuse import pool_reuse
            text, _sid = pool_reuse.pool_source_text(
                info, payload.get('hash'), cache_only=False)
            return text or '', payload
        if kind == 'engine' and not payload.get('embedded'):
            from resources.lib.fetching.embedded import streams as embedded_streams
            path = embedded_streams.download(payload, for_delivery=False) or ''
        elif kind == 'passthrough':
            path = payload.get('path') or ''
        if path and os.path.isfile(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read(), payload
    except Exception as e:
        log('autosub tournament candidate unavailable: %r' % e,
             level='DEBUG')
    return '', payload


def apply_verified_human_fallback(job, cache_only=False, reference=None):
    """Find and apply the first alternate human row proven for this cut.

    This is the first-play continuation path: the initially displayed row may
    be the best release-name guess because no media profile existed yet.  Once
    its deep check has built that profile and abstained, test a bounded set of
    rows returned by the same search.  Only CONFIRMED/FIXABLE evidence may
    replace the visible subtitle.

    ``reference`` ({'text', 'tier', 'release'}) judges the rows against an
    exact/same-group provider subtitle instead of the playing file's own
    timeline -- for files that carry no subtitle track (most small MP4/AVI/
    HDTV encodes). A rescued (low-onset) shift is not enough there: it would
    need a second independent reference, so such rows are skipped.
    """
    from resources.lib.sync.service import log
    links = [str(link) for link in (job.get('fallback_links') or []) if link]
    if (not links or not job_matches_current(job)
            or not _job_stream_is_current(job)):
        return False
    info = dict(job.get('info') or {})
    info['_subsync_stream_url'] = (job.get('stream_url') or '').strip()
    bundle = cached_reference_bundle(info)
    cut_signature = (bundle.get('cut_signature') or '').strip().lower()
    if reference is None:
        if (not bundle.get('cues')
                or not re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature)):
            return False
    else:
        if (reference.get('tier') not in (scoring_matching_release.TIER_EXACT,
                                          scoring_matching_release.TIER_GROUP)
                or not (reference.get('text') or '').strip()):
            return False
        if not re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature):
            cut_signature = ''
    playing = job.get('playing') or ''
    old_link = core_selection.get_current_subtitle() or ''
    attempted = 0
    seen_text = set()
    rejected_timing = job.setdefault('_rejected_timing', set())
    for link in links[:4]:
        if not job_matches_current(job):
            return False
        text, payload = _tournament_candidate_text(
            info, link, cache_only=cache_only)
        if not text.strip() or not is_human_hebrew_candidate(payload):
            continue
        # Providers often list one SRT under several release names.  Skip
        # exact bytes within this pass; the timing key below also covers
        # different text with identical verifier inputs across both passes.
        text_key = hashlib.sha1(text.encode('utf-8', 'replace')).digest()
        if text_key in seen_text:
            continue
        seen_text.add(text_key)
        timing_key = timing_attempt_key(text, cut_signature)
        if timing_key and timing_key in rejected_timing:
            log('autosub tournament: identical rejected timing already '
                 'checked for this playing file', level='DEBUG')
            continue
        attempted += 1
        if reference is None:
            verdict, label, count = verify_file_bundle(bundle, text, playing)
        else:
            verdict = verify.verify(reference['text'], text)
            label = 'ORACLE %r' % (reference.get('release') or '?')
            count = 0
            if (verdict.get('status') == common.STATUS_FIXABLE
                    and verdict.get('coverage_rescue')):
                verdict = dict(verdict, status=STATUS_UNKNOWN,
                               reason='rescue_needs_corroboration')
        verdict = verdict or {}
        status = verdict.get('status')
        rel = (payload.get('filename') or payload.get('release') or '?')
        log('autosub tournament: %r vs %s (%d cues): %s' % (
            rel, label, count, verdict.get('diag', 'no verdict')))
        if status not in (common.STATUS_CONFIRMED,
                          common.STATUS_FIXABLE):
            remember_rejected_timing(job, text, cut_signature, verdict)
            continue
        if status == common.STATUS_FIXABLE:
            # A map this subtitle cannot take is no fix: try the next row, as
            # the primary's deep check does (it turns such a verdict UNKNOWN).
            # Field log, 14 Nov 2026 (Dexter S03E05 2160p AI upscale): HDTV
            # rows proved FIXABLE with a +104 s region, applying it refused a
            # negative cue, and the tournament stopped there -- leaving an
            # unsynced subtitle on screen while the BluRay row the viewer then
            # picked by hand was CONFIRMED at +0.1 s.
            try:
                if not (retime.apply_verdict(text, verdict) or '').strip():
                    raise ValueError('the map leaves no subtitle')
            except Exception as e:
                log('autosub tournament: %r is FIXABLE but its map cannot be '
                     'applied (%r) -- trying the next one' % (rel, e))
                remember_rejected_timing(
                    job, text, cut_signature,
                    dict(verdict, status=STATUS_UNKNOWN,
                         reason='map_not_applicable'))
                continue
        # Preserve the complete proof (including piecewise certificates) for
        # this canonical subtitle/cut pair. Delivery-side RTL may produce a
        # different text hash, in which case its own fast background check
        # repeats against the already-cached media profile.
        if cut_signature:
            store_verdict(cache_key(text, playing, cut_signature), verdict)
        fallback_selection = {}
        try:
            # Candidate retrieval/analysis can take seconds.  Re-check the old
            # ownership immediately before changing the selected-row token, so
            # a user choice made during that work always wins.
            if not job_matches_current(job):
                return False
            core_selection.set_current_subtitle(link, renew=True)
            fallback_selection = core_selection.current_subtitle_selection(
                expected_link=link) or {}
            if (fallback_selection.get('stream_hash')
                    != (job.get('stream_hash') or '')
                    or not selection_matches(fallback_selection)
                    or not _job_stream_is_current(job)):
                raise RuntimeError('tournament selection/stream changed')
            from resources.lib import pipeline
            path = pipeline.resolve(
                link, info, selection=fallback_selection)
            if not path or not selection_matches(fallback_selection):
                raise RuntimeError('verified candidate resolve failed')

            # RTL cleanup changes glyph order/punctuation but never timings,
            # so bind the same timing proof to the exact delivery bytes.  For
            # a FIXABLE row apply the proven map now, rather than briefly
            # showing the uncorrected alternative and asking a nested worker to
            # rediscover it.  Cancel that nested pending marker; its queue file
            # is harmless and will fail its ownership check when drained.
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    delivery_text = f.read()
            except Exception:
                delivery_text = ''
            if not delivery_text.strip():
                raise RuntimeError('verified candidate delivery is unreadable')
            if cut_signature:
                store_verdict(
                    cache_key(delivery_text, playing, cut_signature),
                    verdict)
            delivery_path = path
            if status == common.STATUS_FIXABLE:
                fixed_text = retime.apply_verdict(delivery_text, verdict)
                delivery_path = write_fixed(path, fixed_text)
                if not delivery_path:
                    raise RuntimeError('verified correction could not be written')
                cancel_pending()
                if not core_selection.stage_subtitle_sync_fix(
                        delivery_path, selection=fallback_selection,
                        source='tournament', notice=(
                            'הכתובית סונכרנה אוטומטית')):
                    raise RuntimeError('verified correction could not be staged')
            else:
                if not core_selection.stage_subtitle_delivery(
                        delivery_path, selection=fallback_selection,
                        status='confirmed', source='tournament'):
                    raise RuntimeError('verified delivery could not be staged')
            if not core_selection.apply_subtitle_file(
                    delivery_path, selection=fallback_selection):
                raise RuntimeError('verified candidate delivery failed')
            log('autosub tournament: applied verified human candidate %r'
                 % rel)
            try:
                kodi.notify(
                    'נבחרה אוטומטית כתובית שתזמונה אומת', time_ms=4500)
            except Exception:
                pass
            return True
        except Exception as e:
            log('autosub tournament apply skipped: %r' % e,
                 level='WARNING')
            try:
                if (old_link and fallback_selection
                        and core_selection.get_current_subtitle() == link
                        and selection_matches(fallback_selection)):
                    core_selection.set_current_subtitle(old_link, renew=True)
            except Exception:
                pass
            return False
    if attempted:
        log('autosub tournament: no alternate passed exact-cut proof')
    return False


def _announce(verdict, fresh, offset_hint=None, selection=None):
    """The ONE gentle toast policy: speak ONLY when a timing fix was actually
    APPLIED to the subtitle -- that's the single event the user can feel and
    wants to know about ("synced automatically"). Everything else is SILENT:
    a sub that couldn't be verified is delivered exactly as-is (fail-open), so
    there is nothing to tell the user -- a "couldn't verify" toast on an
    already-synced subtitle is pure noise and reads as a failure when nothing
    failed. (Most toasts in this build were deliberately silenced after user
    complaints; this stays in that spirit.) One toast per (sub,release) pair,
    ever -- fresh applied fixes only."""
    try:
        if not fresh or not verdict:
            return
        if selection is not None and not selection_matches(selection):
            return
        if (verdict.get('status') == common.STATUS_FIXABLE
                and verdict.get('applied')):
            off = float(offset_hint if offset_hint is not None
                        else verdict.get('offset_ms') or 0.0)
            scale = float(verdict.get('scale') or 1.0)
            if verdict.get('mode', 'global') == 'global' and scale == 1.0 and off:
                msg = 'הכתובית סונכרנה אוטומטית ({0:+.1f} שנ׳)'.format(
                    -off / 1000.0)
            else:
                msg = 'הכתובית סונכרנה אוטומטית'
            kodi.notify(msg, time_ms=5000)
    except Exception:
        pass


def fix_notice(verdict, offset_hint=None):
    try:
        off = float(offset_hint if offset_hint is not None
                    else verdict.get('offset_ms') or 0.0)
        scale = float(verdict.get('scale') or 1.0)
        if verdict.get('mode', 'global') == 'global' and scale == 1.0 and off:
            return 'הכתובית סונכרנה אוטומטית ({0:+.1f} שנ׳)'.format(
                -off / 1000.0)
    except Exception:
        pass
    return 'הכתובית סונכרנה אוטומטית'


def job_matches_current(job):
    """Prove a background result still belongs to the visible stream/pick."""
    try:
        expected = {
            'token': job.get('selection_token') or '',
            'link_hash': job.get('selection_hash') or '',
            'stream_hash': job.get('stream_hash') or '',
        }
        pending = _pending_record(expected)
        # Old pre-token jobs may still be on disk after an update. They can be
        # computed/cached, but can never hot-swap or label a live selection.
        if not all(expected.values()):
            return False
        if (pending.get('key') != job.get('key')
                or pending.get('selection_token') != expected['token']
                or pending.get('selection_hash') != expected['link_hash']
                or pending.get('stream_hash') != expected['stream_hash']):
            return False
        return (selection_matches(expected) and
                _job_stream_is_current(job))
    except Exception:
        return False


def _job_stream_is_current(job):
    """Prove only stream identity, independent of the subtitle selection."""
    try:
        import xbmc
        player = xbmc.Player()
        if not player.isPlaying():
            return False
        cur_url = current_stream_transport()
        job_url = (job.get('stream_url') or '').strip()
        # Require the exact captured stream URL.  Some providers use one path
        # for every title and put the content identity in `?file=`/`?id=`;
        # dropping the query could therefore cross films.
        return bool(cur_url and job_url and cur_url == job_url)
    except Exception:
        return False


def _job_selection(job):
    return {
        'token': job.get('selection_token') or '',
        'link_hash': job.get('selection_hash') or '',
        'stream_hash': job.get('stream_hash') or '',
    }


def _wait_for_delivery_ack(job, timeout_ms=3000):
    """Wait until Kodi has registered/pinned the foreground subtitle.

    The service can claim a queue file before the picker callback delivers its
    original SRT. Swapping a correction first lets that later callback overwrite
    it while the UI falsely says FIXED. This bounded gate establishes the only
    safe order: original visibly selected, then verify/replace in background.
    """
    selection = _job_selection(job)
    path = job.get('path') or ''
    if not path or not all(selection.values()):
        return False
    try:
        import xbmc
        steps = max(1, min(120, int(max(0, timeout_ms) / 50) + 1))
        for attempt in range(steps):
            if not job_matches_current(job):
                return False
            if core_selection.subtitle_delivery_is_applied(
                    path, selection=selection):
                return True
            if attempt + 1 < steps:
                xbmc.sleep(50)
    except Exception:
        return False
    return False


def _swap_if_current(job, fixed_path, verdict):
    """Swap the playing subtitle to the fixed copy -- ONLY if the user is
    still watching the same stream and hasn't picked a different subtitle
    since we delivered (the pending marker still names our job)."""
    from resources.lib.sync.service import log
    try:
        import xbmc
        if not job_matches_current(job):
            log('swap skipped: selection or stream changed meanwhile')
            return False
        player = xbmc.Player()
        if not player.isPlaying():
            return False
        try:
            before = len(player.getAvailableSubtitleStreams() or [])
        except Exception:
            # Kodi gave us no positive observation channel. Applying remains
            # fail-open, but do not claim FIXED without proof of registration.
            before = -1
        player.setSubtitles(fixed_path)
        try:
            player.showSubtitles(True)
        except Exception:
            pass
        if before < 0:
            log('fixed subtitle handed to Kodi; registration unobservable',
                 level='WARNING')
            return False
        for _ in range(20):  # setSubtitles posts asynchronously; wait <= 1s.
            xbmc.sleep(50)
            if not job_matches_current(job):
                log('swap confirmation abandoned: selection changed')
                return False
            try:
                streams = player.getAvailableSubtitleStreams() or []
            except Exception:
                return False
            if len(streams) > before:
                try:
                    player.setSubtitleStream(len(streams) - 1)
                except Exception:
                    log('fixed subtitle registered but could not be selected',
                         level='WARNING')
                    return False
                if not job_matches_current(job):
                    return False
                log('fixed subtitle swapped in-place: ' + fixed_path)
                return True
        # Kodi 21/Android replaces its current external-subtitle slot instead
        # of growing the stream list (the field log shows Closing stream 3 /
        # Opening stream 3 with a new source id).  setSubtitles is specified to
        # add and activate the supplied file.  A successfully returned call,
        # a real content-addressed correction file, and the still-current exact
        # selection together are positive evidence for this replacement form.
        if os.path.isfile(fixed_path) and job_matches_current(job):
            log('fixed subtitle replaced current external slot: '
                 + fixed_path)
            return True
        log('fixed subtitle registration was not observed', level='WARNING')
        return False
    except Exception as e:
        log('swap failed: %r' % e, level='WARNING')
        return False


def _run_file_only_job(job, info, playing, path, job_selection):
    """Check a release-trusted subtitle against the playing file only.

    Release identity made the foreground deliver it as synced. The playing
    file's own subtitle timeline is stronger evidence than any label, so when
    the file proves a correction it replaces the trusted copy. Provider
    oracles, audio and alternate rows are deliberately NOT consulted: without
    file evidence the release-identity decision stands unchanged.
    """
    from resources.lib.sync.service import log, publish_selection_status
    if not _wait_for_delivery_ack(job, timeout_ms=6000):
        log('trusted file check skipped: delivery unconfirmed',
             level='WARNING')
        return
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception:
        return
    if not text.strip():
        return
    bundle = probe_reference_bundle(info, playing)
    if not _job_stream_is_current(job):
        log('trusted file check discarded: playback changed during probe')
        return
    cut_signature = (bundle.get('cut_signature') or '').strip().lower()
    if not re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature):
        cut_signature = learn_cut_signature(info)
        if not re.fullmatch(r'cut1:[0-9a-f]{32}', cut_signature or ''):
            cut_signature = ''
    exact_key = cache_key(text, playing, cut_signature)
    verdict = None
    # Always judge against the file itself (its profile is cached per
    # transport, so this is cheap). A cached verdict for the same key may have
    # come from an oracle, a shared record or a sub-second soft probe, none of
    # which this release-trusted path is allowed to act on.
    if bundle.get('track_cues') or bundle.get('cues'):
        verdict, ref_kind, ref_count = verify_file_bundle(bundle, text, playing)
        if verdict:
            log('trusted file check for %r vs %s (%d ref cues): %s'
                 % (job.get('release') or '?', ref_kind, ref_count,
                    verdict.get('diag', '?')))
            if cut_signature and verdict.get('status') in (
                    common.STATUS_CONFIRMED, common.STATUS_FIXABLE):
                store_verdict(exact_key, verdict)
    else:
        # No subtitle timeline, but an MP4 still carries its audio frame
        # sizes.  When they prove the trusted row is on another clock, the
        # label is overruled by the file: let the audio pick a provider
        # timeline, and never keep calling the row synced.
        envelope = bundle_envelope(bundle)
        check = None
        if envelope is not None:
            try:
                check = verify.envelope_check(envelope, text)
            except Exception:
                check = None
        if check and check.get('status') == 'conflict':
            log('trusted file check for %r vs FILE AUDIO ENVELOPE: %s'
                 % (job.get('release') or '?', envelope_diag(check)))
            found = envelope_hypothesis_search(
                info, text, envelope, check, early_job=job)
            if found:
                verdict = found[0]
                if cut_signature:
                    store_verdict(exact_key, verdict)
            else:
                verdict = {'status': STATUS_UNKNOWN,
                           'scale': 1.0, 'offset_ms': 0.0,
                           'reason': 'file_timeline_conflict',
                           'diag': envelope_diag(check)}
                if job_matches_current(job):
                    publish_selection_status('unverified', 'local',
                                              selection=job_selection)
        else:
            log('trusted file check: playing file has no subtitle timeline; '
                 'release identity kept%s'
                 % ((' (' + envelope_diag(check) + ')') if check else ''))
    if (verdict and verdict.get('status') == common.STATUS_FIXABLE
            and job_matches_current(job)):
        try:
            fixed = retime.apply_verdict(text, verdict)
        except Exception:
            fixed = ''
        out = write_fixed(path, fixed)
        if out and _swap_if_current(job, out, dict(verdict, applied=True)):
            if verdict.get('mode', 'global') == 'global':
                record_delivery(info, playing, exact_key,
                                 verdict.get('scale', 1.0),
                                 verdict.get('offset_ms', 0.0),
                                 cut_signature=cut_signature,
                                 selection=job_selection)
            else:
                clear_delivery(selection=job_selection)
            if publish_selection_status('fixed', 'local',
                                         selection=job_selection):
                _announce(dict(verdict, applied=True), fresh=True,
                          selection=job_selection)
            return
    if (verdict and verdict.get('status') == STATUS_UNKNOWN
            and job.get('fallback_links')
            and apply_verified_human_fallback(job)):
        # The file could not confirm the release-trusted row; another
        # already-discovered Hebrew row was PROVEN against this exact file.
        log('trusted row unproven by the file; proven alternate selected')
        return
    if job_matches_current(job):
        record_delivery(info, playing, exact_key, 1.0, 0.0,
                         cut_signature=cut_signature,
                         selection=job_selection)


def run_deep_job(job):
    """Service-side execution of one queued deep-verify job. Computes the
    verdict, and on FIXABLE swaps the playing subtitle in place. One gentle
    toast per pair, ever (see _announce). Never raises."""
    from resources.lib.sync.service import log, publish_selection_status
    started = time.monotonic()
    started_wall = time.time()
    DEEP_THREAD[0] = threading.get_ident()
    yield_state.update(checked=0.0, seen_playing=False, gone=None, waited=0.0)
    try:
        key = job.get('key') or ''
        path = job.get('path') or ''
        job_selection = _job_selection(job)

        def _finish_unverified(source):
            if (not job.get('identity_only')
                    and job_matches_current(job)):
                if publish_selection_status(
                        'unverified', source, selection=job_selection):
                    log('status for current subtitle: unverified ({0})'
                         .format(source))

        if not key or not path or not os.path.isfile(path):
            _finish_unverified('missing')
            return
        if not _job_stream_is_current(job):
            log('deep job discarded: captured stream is no longer playing')
            return
        info = dict(job.get('info') or {})
        info['_subsync_stream_url'] = (job.get('stream_url') or '').strip()
        playing = job.get('playing') or ''
        # A release-exact subtitle needs no timing work.  Its tiny background
        # job exists solely to learn the content-derived cut id, so a later
        # manual delay is never shared with another cut carrying the same name.
        # It does not contact subtitle providers, Gemini or the timing oracle.
        if job.get('identity_only'):
            cut_signature = learn_cut_signature(info)
            if (cut_signature and job_matches_current(job)):
                try:
                    with open(path, 'r', encoding='utf-8', errors='replace') as f:
                        text = f.read()
                except Exception:
                    text = ''
                if text.strip():
                    exact_key = cache_key(text, playing, cut_signature)
                    record_delivery(info, playing, exact_key, 1.0, 0.0,
                                     cut_signature=cut_signature,
                                     selection=job_selection)
            return
        if job.get('file_only'):
            _run_file_only_job(job, info, playing, path, job_selection)
            return
        # The foreground picker/chooser still owns the first application. Never
        # let this faster service thread apply a corrected copy before Kodi has
        # visibly registered and selected that original delivery.
        if not _wait_for_delivery_ack(job):
            _finish_unverified('delivery')
            log('deep job deferred: foreground subtitle delivery unconfirmed',
                 level='WARNING')
            return
        # Someone may have computed it while the job sat in the queue.
        cached = load_verdicts().get(key)
        if (cached and cached.get('v') == VERDICT_VERSION
                and cached.get('status') in (
                    common.STATUS_CONFIRMED, STATUS_UNKNOWN)):
            if job_matches_current(job):
                if cached.get('status') == common.STATUS_CONFIRMED:
                    publish_selection_status(
                        'confirmed', 'cache', selection=job_selection)
                else:
                    if apply_verified_human_fallback(job):
                        return
                    if _apply_proven_pool_fallback(job):
                        return
                    publish_selection_status(
                        'unverified', 'cache', selection=job_selection)
            return
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            _finish_unverified('read-error')
            return
        if not text.strip():
            _finish_unverified('empty')
            return
        rel = job.get('release') or ''
        out, verdict = deep_verify(
            info, path, text, rel, playing, key, early_job=job)
        if not verdict:
            _finish_unverified('no-result')
            return
        if verdict.get('status') == STATUS_ALTERNATE_APPLIED:
            return
        swapped = False
        current = job_matches_current(job)
        if (verdict.get('status') == common.STATUS_FIXABLE
                and verdict.get('applied') and out and out != path):
            swapped = _swap_if_current(job, out, verdict)
            if swapped:
                # Refresh the delivery record with the APPLIED fix, so a later
                # manual delay on top of it folds into the human report right.
                if verdict.get('mode', 'global') == 'global':
                    record_delivery(info, playing,
                                     verdict.get('cache_key') or key,
                                     verdict.get('scale', 1.0),
                                     verdict.get('offset_ms', 0.0),
                                     cut_signature=(
                                         verdict.get('cut_signature') or ''),
                                     selection=job_selection)
                else:
                    # The foreground path recorded the untouched subtitle as a
                    # global zero while verification ran.  Once a piecewise
                    # copy is swapped in, that stale scalar must disappear.
                    clear_delivery(selection=job_selection)
        if current and not swapped:
            if (verdict.get('status') in (
                    STATUS_UNKNOWN, STATUS_NO_ORACLE)
                    and (apply_verified_human_fallback(job)
                         or _apply_proven_pool_fallback(job))):
                    return
            # The original subtitle is still on screen.  Refresh only its exact
            # cut identity (not an unapplied proposed shift), so any later manual
            # delay learns safely even after an UNKNOWN/CONFIRMED result.
            record_delivery(info, playing,
                             verdict.get('cache_key') or key,
                             1.0, 0.0,
                             cut_signature=(
                                 verdict.get('cut_signature') or ''),
                             selection=job_selection)
            final_state = ('confirmed' if verdict.get('status')
                           == common.STATUS_CONFIRMED else 'unverified')
            if publish_selection_status(
                    final_state, 'local', selection=job_selection):
                log('status for current subtitle: {0} (local)'.format(
                    final_state))
        # Announce ONLY an actual in-place swap the user can see. A verdict
        # that couldn't be verified changes nothing on screen -> stay silent.
        if swapped:
            if publish_selection_status(
                    'fixed', 'local', selection=job_selection):
                if selection_matches(job_selection):
                    _announce(dict(verdict, applied=True), fresh=True,
                              selection=job_selection)
    except Exception as e:
        try:
            if job_matches_current(job):
                publish_selection_status(
                    'unverified', 'error', selection={
                        'token': job.get('selection_token') or '',
                        'link_hash': job.get('selection_hash') or '',
                        'stream_hash': job.get('stream_hash') or '',
                    })
        except Exception:
            pass
        log('deep job failed: %r' % e, level='WARNING')
    except ViewerLeft as e:
        log('deep job stopped: %s' % e)
    finally:
        DEEP_THREAD[0] = None
        try:
            queued = float(job.get('ts') or 0.0)
            wait_s = (max(0.0, started_wall - queued)
                      if queued > 0.0 else 0.0)
            log('deep job timing: processing=%.2fs queue_wait=%.2fs '
                 'identity_only=%s' % (
                     time.monotonic() - started, wait_s,
                     bool(job.get('identity_only'))))
        except Exception:
            pass


def drain_queue_once():
    """Pick up and run every queued deep job. Called by the service loop."""
    d = _queue_dir()
    if not d or not os.path.isdir(d):
        return 0
    ran = 0
    try:
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.json'):
                continue
            jpath = os.path.join(d, fn)
            job = None
            try:
                with open(jpath, 'r', encoding='utf-8') as f:
                    job = json.load(f)
            except Exception:
                job = None
            try:
                os.remove(jpath)   # claim before running
            except OSError:
                pass
            if job:
                try:
                    run_deep_job(job)
                finally:
                    # The trusted foreground path owns its own staged record
                    # (as identity jobs always did): clearing it here, e.g.
                    # after an unacknowledged wait, would make Kodi's later
                    # confirmation fail and abandon a good subtitle.
                    if not (job.get('identity_only') or job.get('file_only')):
                        try:
                            core_selection.clear_subtitle_delivery(
                                job.get('path') or '',
                                selection=_job_selection(job))
                        except Exception:
                            pass
                    clear_job_pending(job)
                ran += 1
    except Exception:
        pass
    return ran


def write_fixed(orig_path, fixed_text):
    from resources.lib.sync.service import log
    if not fixed_text or not fixed_text.strip():
        return ''
    try:
        base = os.path.basename(orig_path)
        for ext in ('.he.srt', '.srt'):
            if base.lower().endswith(ext):
                base = base[:-len(ext)]
                break
        # Kodi and subtitle add-ons reuse generic names such as
        # TempSubtitle.he.srt across titles. A basename-only output let a later
        # job overwrite the fixed file cached for another film. Content-address
        # the delivery copy so concurrent/history entries cannot collide.
        import hashlib
        digest = hashlib.sha1(
            fixed_text.encode('utf-8', 'replace')).hexdigest()[:12]
        cache = kodi.cache_dir()
        suffix = '.{0}.synced.he.srt'.format(digest)
        # Windows Kodi commonly runs without long-path support.  The source
        # subtitle can already have a long release name; appending our suffix
        # and the temporary-file extension must still fit MAX_PATH.  Keep the
        # hash intact so truncating a display name cannot merge different
        # corrected subtitles.
        max_base = 240 - len(os.path.abspath(cache)) - 1 - len(suffix) - len('.tmp')
        if max_base < 1:
            raise OSError('subtitle cache path is too long')
        out = os.path.join(cache, '{0}{1}'.format(base[:max_base], suffix))
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(fixed_text)
        os.replace(tmp, out)
        return out
    except Exception as e:
        log('write fixed failed: %r' % e, level='WARNING')
        return ''


def status_line(verdict):
    """Short Hebrew status for the overlay/toast, or '' for silent verdicts
    (quiet-by-default policy: only a CHANGE is announced)."""
    if not verdict:
        return ''
    st = verdict.get('status')
    if st == common.STATUS_FIXABLE and verdict.get('applied'):
        off = float(verdict.get('offset_ms') or 0.0)
        scale = float(verdict.get('scale') or 1.0)
        if verdict.get('mode', 'global') == 'global' and scale == 1.0 and off:
            return 'הכתובית סונכרנה אוטומטית ({0:+.1f} שנ׳)'.format(-off / 1000.0)
        return 'הכתובית סונכרנה אוטומטית'
    return ''
