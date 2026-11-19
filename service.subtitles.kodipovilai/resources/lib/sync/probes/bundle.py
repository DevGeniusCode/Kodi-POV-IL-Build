# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Probes: reference bundles (envelope packing, hypothesis search, file bundle verification).

import time
import base64
import hashlib
import threading
import zlib
try:
    from resources.lib.core import kodi, selection as core_selection, settings, video_context
except Exception:
    kodi = core_selection = settings = video_context = None
try:
    from resources.lib.scoring_matching import release as scoring_matching_release
except Exception:
    scoring_matching_release = None
try:
    from resources.lib.core.srt import cues as srt_cues
    from resources.lib.sync.align import common, piecewise, retime, verify
except Exception:
    srt_cues = common = piecewise = retime = verify = None
from resources.lib.sync.common import MIN_REMOTE_CUES
from resources.lib.sync.oracles import _decode_link, _download_oracle, _oracle_candidates
from resources.lib.sync.probes.cache import _PROBE_CACHE_VERSION, _probe_cache_path, _read_probe_entry, _write_probe_cache
from resources.lib.sync.probes.remote import _current_stream_url, _local_cut_signature, _playing_url, _probe_enabled, _remote_playing_url, _remote_probe_ready, _remote_reference_bundle, _starts_to_cues, _transport_cache_key
from resources.lib.sync.verdicts import _verdict_strength


_NEGATIVE_PROBE_TTL_S = 6 * 60 * 60


def _pack_envelope(envelope):
    """JSON-safe compressed MP4 audio envelope for the probe cache."""
    try:
        values = (envelope or {}).get('values') or []
        if not values:
            return None
        raw = bytes((int(value) + 128) & 0xFF for value in values)
        return {'step_ms': int(envelope.get('step_ms') or 0),
                'z': base64.b64encode(zlib.compress(raw, 6)).decode('ascii'),
                'frames': int(envelope.get('frames') or 0),
                'distinct': int(envelope.get('distinct') or 0)}
    except Exception:
        return None


def _bundle_envelope(bundle):
    """The playing file's audio envelope (values unpacked), or None."""
    packed = (bundle or {}).get('envelope')
    if not isinstance(packed, dict):
        return None
    if isinstance(packed.get('values'), list):
        return packed
    try:
        raw = zlib.decompress(base64.b64decode(packed.get('z') or ''))
        return {'step_ms': int(packed.get('step_ms') or 0),
                'values': [byte - 128 for byte in bytearray(raw)]}
    except Exception:
        return None


def _envelope_diag(check):
    check = check or {}
    return ('audio envelope %s (trusted %d/%d, polarity %+d, plateaus %s%s)'
            % (check.get('status', 'unknown'), int(check.get('trusted') or 0),
               int(check.get('windows') or 0), int(check.get('polarity') or 0),
               ', '.join('%+dms x%d' % (int(o), c)
                         for o, c in check.get('plateaus') or []) or '-',
               ('; ' + check['reason']) if check.get('reason') else ''))


_PROBE_LOCK = threading.RLock()
_PREFETCH_KEYS = set()


def _probe_reference_bundle(info, playing):
    # Autosub may begin this same exact-file read while its first subtitle is
    # downloading.  The service worker must reuse that result, never send a
    # second set of Range requests to the playback provider.
    with _PROBE_LOCK:
        return _probe_reference_bundle_unlocked(info, playing)


def _probe_reference_bundle_unlocked(info, playing):
    """Actual playing-file reference, with independent track timelines.

    The disk cache is keyed by an opaque transport identity, never merely the
    release label.  Its content signature then scopes timing verdicts and human
    corrections to the exact media bytes.
    """
    from resources.lib.sync.service import _log
    if not _probe_enabled():
        return {}
    cache_key = _transport_cache_key(info)
    cpath = _probe_cache_path()
    ent = _read_probe_entry(cache_key) if cache_key else None
    if ent is not None and ent.get('pv') != _PROBE_CACHE_VERSION:
        ent = None
    if ent:
        has_ref = isinstance(ent.get('cues'), list) and bool(ent.get('cues'))
        has_sig = bool(ent.get('cut_signature'))
        if has_ref:
            _log('probe: media cache hit (%d cues, %d track(s), cut=%s)'
                 % (len(ent.get('cues') or []),
                    len(ent.get('track_cues') or []),
                    'yes' if has_sig else 'no'))
            return ent
    if (ent is not None and not ent.get('cues')
            and ent.get('timing_attempted', True)
            and ent.get('envelope_attempted')):
        # No timeline may mean "no embedded subtitles" or transient provider
        # pressure.  Keep the useful cut id, but retry the timing read after the
        # normal negative TTL instead of letting signature success freeze a cue
        # miss forever.
        if time.time() - float(ent.get('ts') or 0) < _NEGATIVE_PROBE_TTL_S:
            return ent if (ent.get('cut_signature')
                           or ent.get('envelope')) else {}

    local_url = _playing_url(info)
    if local_url:
        try:
            with open(local_url, 'rb') as media_file:
                header = media_file.read(12)
            if header[4:8] in (b'ftyp', b'moov', b'wide',
                               b'mdat', b'free', b'skip'):
                from resources.lib.fetching.embedded import mp4_probe
                res = mp4_probe.subtitle_reference(
                    local_url, log=lambda m: _log('probe: ' + m)) or {}
                if not res.get('track_cues'):
                    # Only a file without timed text needs the audio envelope;
                    # re-reading a local index costs nothing.
                    try:
                        res['envelope'] = mp4_probe.audio_envelope_source(
                            None, None,
                            index_data=mp4_probe._read_movie_index(local_url),
                            log=lambda m: _log('probe: ' + m))
                    except Exception:
                        pass
            else:
                res = {}
                if local_url.lower().endswith('.webm'):
                    # WebM commonly has an exact subtitle Cues index. Read it
                    # before the older sparse byte-window fallback.
                    from resources.lib.fetching.embedded import embedded_extract
                    raw = embedded_extract.cue_reference_profile(
                        local_url, allow_http=False,
                        log=lambda m: _log('probe: ' + m)) or {}
                    cues = _starts_to_cues(raw.get('starts') or [])
                    if len(cues) >= MIN_REMOTE_CUES:
                        track_cues = [
                            {'track': item.get('track') or {},
                             'cues': _starts_to_cues(item.get('starts') or [])}
                            for item in raw.get('track_starts') or []]
                        res = {'cues': cues, 'track_cues': track_cues,
                               'tracks': raw.get('tracks') or [],
                               'cut_signature': raw.get('cut_signature') or '',
                               'bytes': raw.get('bytes') or 0}
                if not res:
                    from resources.lib.fetching.embedded import mkv_probe
                    res = mkv_probe.subtitle_reference(
                        local_url, log=lambda m: _log('probe: ' + m)) or {}
        except Exception:
            res = {}
        res['cut_signature'] = (res.get('cut_signature') or
                                _local_cut_signature(info))
    else:
        remote_url = _remote_playing_url(info)
        if not remote_url:
            _log('probe: no probeable playing url')
            return {}
        res = _remote_reference_bundle(remote_url)

    bundle = {
        'ts': time.time(), 'pv': _PROBE_CACHE_VERSION,
        'cues': res.get('cues') or [],
        'track_cues': res.get('track_cues') or [],
        'tracks': res.get('tracks') or [],
        'track': res.get('track') or {},
        'cut_signature': (res.get('cut_signature') or
                          ((ent or {}).get('cut_signature') or '')),
        'bytes': int(res.get('bytes') or 0),
        'legacy_single_track': bool(res.get('legacy_single_track')),
        'timing_attempted': not bool(res.get('probe_error')),
        'envelope': _pack_envelope(res.get('envelope')),
        'envelope_attempted': True,
    }
    # A blocked/not-ready Range read is not evidence that the media has no
    # subtitle track.  In particular, an early prefetch must never poison the
    # negative cache and prevent the ordinary deep worker from retrying.
    if cpath and cache_key and not res.get('probe_error'):
        _write_probe_cache(cache_key, bundle)
    return bundle


def prefetch_autosub_reference(info, candidates):
    """Overlap one safe media read with an uncertain human candidate download.

    This is deliberately narrower than generic on-play prefetch: exact/group
    matches and a single Hebrew choice already have a fast foreground path.
    No provider subtitle search, audio read or Gemini call is started here.
    The deep worker shares _PROBE_LOCK and the same cache entry.
    """
    from resources.lib.sync.candidates import _is_human_hebrew_candidate
    from resources.lib.sync.service import _log, playing_release
    registered_key = ''
    try:
        if (not _probe_enabled()
                or (settings.get_setting('subsync_autorank', 'true') or
                    'true').strip().lower() == 'false'):
            return False
        playing = playing_release(info)
        if not playing or _cached_reference_bundle(info):
            return False
        humans = []
        for candidate in candidates or []:
            if (candidate.get('language') or '').strip().lower() not in (
                    'he', 'heb', 'hebrew'):
                continue
            payload = _decode_link(candidate.get('link') or '') or {}
            if _is_human_hebrew_candidate(payload):
                humans.append((candidate, payload))
            if len(humans) >= 2:
                break
        if len(humans) < 2:
            return False
        first, payload = humans[0]
        release = (payload.get('filename') or payload.get('release') or
                   first.get('filename') or '').strip()
        _pct, tier, _diag = scoring_matching_release.score(playing, release)
        if tier in scoring_matching_release.AUTO_OK_TIERS:
            return False
        local_url = _playing_url(info)
        remote_url = '' if local_url else _remote_playing_url(info)
        if not (local_url or remote_url):
            return False
        key = _transport_cache_key(info)
        if not key:
            return False
        with _PROBE_LOCK:
            if key in _PREFETCH_KEYS or _cached_reference_bundle(info):
                return False
            _PREFETCH_KEYS.add(key)
            registered_key = key
        frozen = dict(info)

        def work():
            started = time.monotonic()
            try:
                # Waiting for playback stability must not poison the negative
                # cache.  The ordinary deep worker can still retry later.
                if remote_url and not _remote_probe_ready(
                        max_wait_s=18.0, expected_url=remote_url):
                    _log('autosub reference prefetch: playback not ready '
                         'after %.2fs' % (time.monotonic() - started))
                    return
                if remote_url and _current_stream_url() != remote_url:
                    return
                bundle = _probe_reference_bundle(frozen, playing)
                _log('autosub reference prefetch: %.2fs, %d cue(s)'
                     % (time.monotonic() - started,
                        len(bundle.get('cues') or [])))
            except Exception as e:
                _log('autosub reference prefetch failed: %r' % e,
                     level='DEBUG')
            finally:
                with _PROBE_LOCK:
                    _PREFETCH_KEYS.discard(key)

        threading.Thread(target=work, daemon=True,
                         name='MoranSubs-ReferencePrefetch').start()
        return True
    except Exception as e:
        if registered_key:
            with _PROBE_LOCK:
                _PREFETCH_KEYS.discard(registered_key)
        _log('autosub reference prefetch skipped: %r' % e, level='DEBUG')
        return False


def _cached_reference_bundle(info):
    """Return a proven media-scoped reference without I/O beyond one JSON read."""
    cache_key = _transport_cache_key(info)
    if not cache_key:
        return {}
    ent = _read_probe_entry(cache_key)
    if (not isinstance(ent, dict)
            or ent.get('pv') != _PROBE_CACHE_VERSION
            or not ent.get('cues')):
        return {}
    return ent


def _verify_profile(profile, text, bitmap_events=False, **kwargs):
    """verify_cues against one file track.  The preferred PGS representative
    (``bitmap_events``) is first compared event to event
    (sync_align.bitmap_events, whose offset is re-measured on caption starts
    and refused when they disagree); when that reaches no decision the classic
    comparison runs exactly as before, so no earlier answer can be lost.  Only
    one track pays for the second pass -- each costs ~2x the classic check."""
    from resources.lib.sync.validators import _track_codec_family
    cues = profile.get('cues') or []
    if bitmap_events and _track_codec_family(profile.get('track') or {}) == 'pgs':
        try:
            verdict = verify.verify_cues(cues, text, ref_events=True,
                                             **kwargs)
            if verdict.get('status') in (common.STATUS_CONFIRMED,
                                         common.STATUS_FIXABLE):
                return verdict
        except TypeError:
            pass          # an older sync_align still in memory after an update
    return verify.verify_cues(cues, text, **kwargs)


_MAX_FILE_FAMILIES = 4


def _distinct_track_families(profiles):
    """One representative per embedded timing family, strongest first.

    Disc remuxes carry a dozen PGS tracks authored on the same timeline; each
    was verified in full, which cost ~60 s of CPU per subtitle on a desktop
    (10-20 minutes on a 32-bit TV box, during playback). A clone family gives
    the same answer as its representative, so only distinct lanes are judged:
    text before bitmap, Hebrew/English first, denser tracks first, at most
    _MAX_FILE_FAMILIES of them.
    """
    from resources.lib.sync.validators import _onset_coverage, _track_language_rank
    def rank(profile):
        track = profile.get('track') or {}
        codec = str(track.get('codec') or '').upper()
        return (0 if codec.startswith('S_TEXT') else 1,
                _track_language_rank(track),
                -len(profile.get('cues') or []))
    def same_lane(left, right):
        # Redundant only when the onsets coincide IN PLACE. A lane with the
        # same shape at another offset gives a different answer and must
        # stay: it is exactly what conflict detection and the exact-track
        # veto need to see.
        a = left.get('cues') or []
        b = right.get('cues') or []
        return (_onset_coverage(a, b) >= 0.90
                and _onset_coverage(b, a) >= 0.90)
    kept = []
    for profile in sorted(profiles or [], key=rank):
        try:
            distinct = not any(same_lane(other, profile) for other in kept)
        except Exception:
            distinct = True
        if distinct:
            kept.append(profile)
            if len(kept) >= _MAX_FILE_FAMILIES:
                break
    return kept


def _file_timeline_conflict(bundle, text):
    """Plateaus proving ``text`` is not on one clock with the playing file.

    Only the preferred complete track is consulted (text before bitmap,
    Hebrew/English first, non-forced); one representative suffices and an
    odd commentary/forced track can never veto on its own.
    """
    from resources.lib.sync.validators import _track_span
    finder = getattr(verify, 'timeline_conflict', None)
    if finder is None or not text:
        return None
    profiles = []
    for profile in (bundle or {}).get('track_cues') or []:
        track = profile.get('track') or {}
        if track.get('forced') or len(profile.get('cues') or []) < MIN_REMOTE_CUES:
            continue
        profiles.append({'track': track, 'cues': profile.get('cues') or []})
    if not profiles:
        envelope = _bundle_envelope(bundle)
        checker = getattr(verify, 'envelope_check', None)
        if envelope is None or checker is None:
            return None
        try:
            check = checker(envelope, text)
        except Exception:
            return None
        # Only a 'conflict' can overrule other evidence: it is relative, so an
        # encoder's constant bias can never produce one, whereas 'shifted'
        # could be nothing but that bias.
        if check.get('status') == 'conflict':
            return check.get('plateaus') or [(0.0, int(check['trusted']))]
        return None
    longest = max(_track_span(item) for item in profiles)
    complete = [item for item in profiles
                if _track_span(item) >= 0.90 * longest]
    preferred = _distinct_track_families(complete)[:1]
    for profile in preferred:
        try:
            return finder(profile['cues'], text)
        except Exception:
            return None
    return None


_ENVELOPE_MAX_DOWNLOADS = 4
_ENVELOPE_SEARCH_S = 45.0


def _envelope_accepts(envelope, before, fixed_text, allow_near=False):
    """Envelope verdict for a proposed timeline, or None when it disagrees.

    'near' (0.5-0.9 s) is within the envelope's encoder bias.  It is good
    enough only for a held-out-validated region map, whose own precision
    comes from text; a single global shift must land squarely 'synced'.
    """
    try:
        check = verify.envelope_check(
            envelope, fixed_text,
            polarity=(before or {}).get('polarity') or None)
    except Exception:
        return None
    wanted = ('synced', 'near') if allow_near else ('synced',)
    if (check.get('status') not in wanted
            or int(check.get('trusted') or 0) < 6):
        return None
    return check


def _envelope_hypothesis_search(info, text, envelope, before, early_job=None,
                                state=None):
    """Let the playing file's own audio choose among provider timelines.

    Reached only when the audio envelope has already proved that the selected
    subtitle is NOT on the file's clock (sectional plateaus or one large
    shift).  Each downloaded provider subtitle proposes a timeline for the
    Hebrew text -- a global map, a proven large-cut map, or a five-fold
    validated short-edit map -- and only a proposal that lands the text on a
    single near-zero envelope plateau is kept.  The release name plays no
    part, so an unnamed or mislabelled MP4 is handled the same way.
    """
    from resources.lib.sync.jobs import _job_matches_current
    from resources.lib.sync.service import _log
    started = time.monotonic()
    try:
        cands = _oracle_candidates(info) or []
    except Exception:
        cands = []

    def rank(candidate):
        try:
            parts = scoring_matching_release.parse(candidate.get('release') or '')
        except Exception:
            parts = {}
        english = (candidate.get('language') or '').lower() in (
            'en', 'eng', 'english')
        return (1 if parts.get('source') else 0, 1 if english else 0,
                1 if parts.get('group') else 0)

    # One row per (source, group) before any second row: the point is to try
    # different timing families, not six translations of one release.
    buckets = {}
    order = []
    for candidate in sorted(cands, key=rank, reverse=True):
        try:
            parts = scoring_matching_release.parse(candidate.get('release') or '')
            key = (parts.get('source') or '?', parts.get('group') or '?')
        except Exception:
            key = ('?', '?')
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(candidate)
    ranked = []
    while any(buckets[key] for key in order):
        for key in order:
            if buckets[key]:
                ranked.append(buckets[key].pop(0))

    downloads = 0
    seen = set()
    state = state if isinstance(state, dict) else {}
    for candidate in ranked:
        if (downloads >= _ENVELOPE_MAX_DOWNLOADS
                or time.monotonic() - started > _ENVELOPE_SEARCH_S):
            break
        if early_job and not _job_matches_current(early_job):
            return None
        downloads += 1
        oracle_text = _download_oracle(candidate.get('payload') or {})
        if not oracle_text.strip():
            state['transient'] = True
            continue
        if oracle_text == text:
            continue
        digest = hashlib.sha1(
            oracle_text.encode('utf-8', 'ignore')).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        if time.monotonic() - started > _ENVELOPE_SEARCH_S:
            break
        proposals = []
        try:
            fixed, verdict = retime.verify_and_fix(oracle_text, text)
        except Exception:
            continue
        if (verdict.get('status') == common.STATUS_FIXABLE
                and fixed and fixed.strip() and fixed != text):
            proposals.append((verdict, fixed))
        elif (verdict.get('status') == common.STATUS_UNKNOWN
                and time.monotonic() - started <= _ENVELOPE_SEARCH_S):
            try:
                oracle_cues = srt_cues.dialogue_cues(
                    srt_cues.parse_srt(oracle_text))
                validated = piecewise.validate_micro_piecewise(
                    oracle_cues, text)
            except Exception:
                validated = None
            if (validated
                    and int(validated.get('validation_folds') or 0) >= 4):
                try:
                    preview = retime.retime(
                        text, validated.get('scale', 1.0),
                        validated.get('offset_ms', 0.0),
                        segments=validated.get('segments'), strict=True)
                except Exception:
                    preview = ''
                if preview:
                    proposals.append((validated, preview))
        for verdict, preview in proposals:
            check = _envelope_accepts(
                envelope, before, preview,
                allow_near=bool(verdict.get('validation_required')))
            if check is None:
                continue
            proof = dict(verdict)
            proof.update({
                'envelope_status': check['status'],
                'envelope_before': (before or {}).get('status'),
                'envelope_trusted': int(check.get('trusted') or 0),
                'envelope_windows': int(check.get('windows') or 0),
                'envelope_offset_ms': float(check.get('offset_ms') or 0.0),
                'validation_primary_oracle': candidate.get('release') or '',
                'diag': ('%s; %s; was %s' % (
                    verdict.get('diag', ''), _envelope_diag(check),
                    (before or {}).get('status', '?'))),
            })
            if proof.get('validation_required'):
                proof['validation_proof'] = 'file-audio-envelope-v1'
            try:
                fixed_text = retime.apply_verdict(text, proof)
            except Exception as e:
                _log('audio-envelope hypothesis from %r refused: %r'
                     % (candidate.get('release') or '?', e))
                continue
            if fixed_text and fixed_text.strip():
                _log('audio envelope chose the timeline proposed by %r '
                     '(%d download(s), %.1fs)'
                     % (candidate.get('release') or '?', downloads,
                        time.monotonic() - started))
                return proof, fixed_text
    _log('audio-envelope hypothesis search: no proposal landed on the '
         "file's clock (%d download(s), %.1fs)"
         % (downloads, time.monotonic() - started))
    return None


def _verify_file_bundle(bundle, text, playing=None):
    """Judge independent embedded tracks; abstain when they are all too sparse.

    A CONFIRMED track wins over a shifted track because it proves the candidate
    already matches one real timeline in the playing file.  Conflicting accepted
    corrections abstain.  A legacy single-track reader remains compatible, but
    modern multi-track timelines are never merged into evidence they did not
    possess separately.  ``playing`` is the playing release's name; without
    one that says WEB the text-lane proof is not used (_text_lane_file).
    Returns ``(verdict, label, cue_count)``.
    """
    from resources.lib.sync.validators import _guard_soft_probe_shift, _maps_agree, _probe_gate_kwargs, _text_lane_file, _track_codec_family, _track_span, _validated_micro_piecewise
    raw_profiles = []
    for profile in (bundle or {}).get('track_cues') or []:
        cues = profile.get('cues') or []
        if len(cues) < MIN_REMOTE_CUES:
            continue
        raw_profiles.append({'track': profile.get('track') or {},
                             'cues': cues})
    # The piecewise validator needs every PGS representative: one of them
    # validates the frozen map and any strong one may veto it.  Clone-family
    # pruning below is only for the expensive per-track verdicts.
    all_profiles = list(raw_profiles)
    raw_profiles = _distinct_track_families(raw_profiles)
    # The one PGS representative read as display events (see _verify_profile).
    # Complete and non-forced first: a sparse forced (signs) track would only
    # ever answer UNKNOWN.
    pgs_profiles = [profile for profile in raw_profiles
                    if _track_codec_family(profile.get('track') or {}) == 'pgs']
    events_profile = max(pgs_profiles, key=lambda profile: (
        0 if (profile.get('track') or {}).get('forced') else 1,
        _track_span(profile), len(profile.get('cues') or [])),
        default=None)

    # A zero-shift answer can be settled cheaply before testing every FPS
    # ratio and edit map.  Check each track independently with the same final
    # verifier constrained to the identity clock.  Only CONFIRMED may return
    # early: a shifted candidate still needs the full scale, local-continuity
    # and cross-track conflict checks below.  A sparse set of tracks cannot
    # become evidence by being combined here.
    identity_checked = []
    for profile in raw_profiles:
        cues = profile['cues']
        kwargs = dict(_probe_gate_kwargs(cues))
        kwargs.update({'scales': (1.0,), 'allow_piecewise': False})
        try:
            verdict = _verify_profile(
                profile, text, bitmap_events=profile is events_profile,
                **kwargs)
        except Exception:
            continue
        identity_checked.append({'track': profile['track'],
                                 'cues': cues, 'verdict': verdict})
    identity_confirmed = [item for item in identity_checked
                          if item['verdict'].get('status')
                          == common.STATUS_CONFIRMED]
    if identity_confirmed:
        chosen = max(identity_confirmed, key=_verdict_strength)
        verdict = _guard_soft_probe_shift(chosen['verdict'], 'FILE PROBE')
        if verdict.get('status') == common.STATUS_CONFIRMED:
            track = chosen['track']
            return (verdict, 'FILE TRACK #%s %s' % (
                track.get('num', '?'), track.get('lang') or '?'),
                    len(chosen['cues']))

    # The short-edit planner is cheap (identity clock, local windows only),
    # whereas a full arbitrary-scale search on every language track is costly
    # on 32-bit Kodi.  Try the independently corroborated path first.  Before
    # accepting it, every track gets an identity-only veto: a truly CONFIRMED
    # track proves that no rewrite is needed, and any competing flat FIXABLE
    # sends us through the complete conflict-aware path below.
    # (A CONFIRMED track already returned above; a flat FIXABLE one makes the
    # map unusable here whatever it says, so the planners -- several dynamic
    # programmes over every cue -- only run when no track answered.)
    quick = [item for item in identity_checked
             if item['verdict'].get('status') in (
                 common.STATUS_CONFIRMED,
                 common.STATUS_FIXABLE)]
    validated_piecewise = (None if quick else _validated_micro_piecewise(
        all_profiles, text, text_lanes=_text_lane_file(bundle, playing)))
    if validated_piecewise is not None:
        verdict = _guard_soft_probe_shift(
            validated_piecewise['verdict'], 'FILE PROBE')
        track = validated_piecewise.get('track') or {}
        label = 'FILE TRACK VALIDATED #%s %s' % (
            track.get('num', '?'), track.get('lang') or '?')
        return (verdict, label,
                len(validated_piecewise.get('cues') or []))

    profiles = []
    for profile in raw_profiles:
        cues = profile.get('cues') or []
        try:
            verdict = _verify_profile(
                profile, text, bitmap_events=profile is events_profile,
                **_probe_gate_kwargs(cues))
        except Exception:
            continue
        profiles.append({'track': profile.get('track') or {},
                         'cues': cues, 'verdict': verdict})

    accepted = [p for p in profiles
                if p['verdict'].get('status') in (
                    common.STATUS_CONFIRMED,
                    common.STATUS_FIXABLE)]
    confirmed = [p for p in accepted
                 if p['verdict'].get('status')
                 == common.STATUS_CONFIRMED]
    if confirmed:
        chosen = max(confirmed, key=_verdict_strength)
    elif accepted:
        chosen = max(accepted, key=_verdict_strength)
        conflicts = [p for p in accepted
                     if not _maps_agree(chosen['verdict'], p['verdict'])]
        if conflicts:
            summary = ', '.join(
                '#%s/%s %+dms' % (
                    (p.get('track') or {}).get('num', '?'),
                    (p.get('track') or {}).get('lang', '?'),
                    int((p.get('verdict') or {}).get('offset_ms') or 0))
                for p in accepted)
            return ({'status': common.STATUS_UNKNOWN,
                     'scale': 1.0, 'offset_ms': 0.0,
                     'reason': 'embedded_track_conflict',
                     'diag': 'independent embedded tracks disagree: ' + summary},
                    'FILE TRACK CONFLICT', 0)
    elif profiles:
        # At least one real track had enough evidence and all refused.  A union
        # must not manufacture confidence that no constituent track possessed.
        chosen = max(profiles, key=_verdict_strength)
    elif (bundle or {}).get('legacy_single_track'):
        union = (bundle or {}).get('cues') or []
        if len(union) < MIN_REMOTE_CUES:
            return None, 'FILE PROBE', 0
        verdict = verify.verify_cues(
            union, text, **_probe_gate_kwargs(union))
        verdict = _guard_soft_probe_shift(verdict, 'FILE PROBE')
        return verdict, 'FILE LEGACY TRACK', len(union)
    else:
        # Several individually sparse tracks are not evidence for one shared
        # timeline.  Unioning them can fabricate a confident majority from
        # unrelated editorial lead/lag, so the safe answer is to abstain.
        return None, 'FILE TRACKS TOO SPARSE', 0

    verdict = _guard_soft_probe_shift(chosen['verdict'], 'FILE PROBE')
    track = chosen.get('track') or {}
    label = 'FILE TRACK #%s %s' % (track.get('num', '?'),
                                   track.get('lang') or '?')
    return verdict, label, len(chosen.get('cues') or [])
