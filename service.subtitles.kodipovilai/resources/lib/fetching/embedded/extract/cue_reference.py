# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# What a media file looks like to a subtitle: the cut signature, and the cue profile / cue times of its
# embedded tracks (the reference other modules compare an external subtitle against).

import hashlib
import struct
from resources.lib.fetching.embedded.extract.tuning import CUT_SAMPLE_BYTES, CUT_SIGNATURE_VERSION, DEFAULT_HEAD_BYTES, HTTP_PIECE, MP4_REMOTE_INDEX_BYTES
from resources.lib.fetching.embedded.extract.http_source import Source
from resources.lib.fetching.embedded.extract.ebml import noop
from resources.lib.fetching.embedded.extract.matroska import parse_head, sub_tracks
from resources.lib.fetching.embedded.extract.tracks import pick_track
from resources.lib.fetching.embedded.extract.cues import read_cue_times, read_cue_times_multi
from resources.lib.fetching.embedded.extract.srt_rows import timeline_origin


def _cut_signature_from_source(src, head_hint=b'', log=None):
    """Content-derived, privacy-safe id for the exact playing media file.

    It deliberately excludes the path/URL, release label, ETag and provider
    token.  Every requested sample must be complete; a short range or provider
    pressure yields ``''`` rather than a weak/colliding identity.  ``head_hint``
    lets a Matroska caller reuse bytes it already fetched.
    """
    _log = log or noop
    try:
        total = int(getattr(src, 'total', 0) or 0)
        if total <= 0:
            return ''
        width = min(CUT_SAMPLE_BYTES, total)
        offsets = [0, max(0, (total - width) // 2), max(0, total - width)]
        # Very small files make the three ranges overlap.  Read each distinct
        # offset once while preserving their positional meaning in the digest.
        offsets = list(dict.fromkeys(offsets))
        digest = hashlib.sha256()
        digest.update(CUT_SIGNATURE_VERSION)
        digest.update(struct.pack('>Q', total))
        for offset in offsets:
            want = min(width, total - offset)
            if offset == 0 and len(head_hint or b'') >= want:
                data = bytes(head_hint[:want])
            else:
                data = src.read(offset, want)
            if len(data) != want:
                _log('cut-signature: short sample at %d (%d/%d) -- skipped'
                     % (offset, len(data), want))
                return ''
            digest.update(struct.pack('>QQ', offset, want))
            digest.update(data)
        return 'cut1:' + digest.hexdigest()[:32]
    except Exception as e:
        _log('cut-signature failed: %s' % e)
        return ''


def media_cut_signature(url_or_path, allow_http=False, abort_cb=None, log=None):
    """Return a content-derived cut signature from three 64 KiB samples.

    HTTP is opt-in.  The shared ``_Source`` supplies keep-alive, pacing,
    range validation, abort polling and provider-pressure handling.  This is
    intentionally much smaller than an audio/cluster probe and never exposes
    the source URL in the returned value.  Never raises.
    """
    _log = log or noop
    src = None
    try:
        src = Source(url_or_path)
        src._abort_cb = abort_cb
        src._log = _log
        if not src.total:
            return ''
        if src.is_http and not allow_http:
            _log('cut-signature: HTTP not allowed -- skipped')
            return ''
        return _cut_signature_from_source(src, log=_log)
    except Exception as e:
        _log('media_cut_signature failed: %s' % e)
        return ''
    finally:
        if src is not None:
            try:
                src.close()
            except Exception:
                pass


def cue_reference_profile(url_or_path, head_bytes=DEFAULT_HEAD_BYTES,
                          allow_http=False, abort_cb=None, log=None):
    """Read Matroska Cues or a bounded MP4/MOV timed-text index.

    Returns ``{'starts', 'track_starts', 'tracks', 'cut_signature', ...}``.
    ``starts`` is the legacy all-track union; ``track_starts`` preserves the
    individual timelines so SubSync can prefer a real matching track and avoid
    a union-majority false shift.  The Cues element is still read exactly once.
    A content-derived cut signature is produced from three tiny byte samples;
    it remains available even when the file has no subtitle Cues.  Never raises.
    """
    _log = log or noop
    src = None
    try:
        src = Source(url_or_path)
        src._abort_cb = abort_cb
        src._log = _log
        if not src.total:
            return {}
        if src.is_http and not allow_http:
            _log('cue-profile: HTTP not allowed (setting off) -- skipping')
            return {}
        try:
            seg_start, ts_scale, tracks, seeks = parse_head(
                src, head_bytes, _log)
        except Exception as e:
            # The same bounded HTTP Range source can read an ISO BMFF/QuickTime
            # movie index.  Debrid URLs frequently hide the file extension, so
            # select by the bytes already fetched for the Matroska head rather
            # than by URL suffix.  A missing text track remains an unknown.
            head = getattr(src, '_cut_head', b'')
            envelope = None
            if len(head) >= 8 and head[4:8] in (
                    b'ftyp', b'moov', b'wide', b'mdat', b'free', b'skip'):
                try:
                    from resources.lib.fetching.embedded import mp4_probe
                    index = mp4_probe.read_movie_index_source(
                        src.total, src.read,
                        max_bytes=(MP4_REMOTE_INDEX_BYTES if src.is_http
                                   else None))
                    reference = mp4_probe.reference_from_index(
                        index, log=_log) or {}
                    if not reference.get('track_cues'):
                        # The same index carries the audio frame sizes: a
                        # free speech envelope for files with no subtitle
                        # track.  Its own guard: an odd audio box must never
                        # cost the timed-text reference.
                        try:
                            envelope = mp4_probe.audio_envelope_source(
                                None, None, index_data=index, log=_log)
                        except Exception:
                            envelope = None
                    if reference.get('track_cues'):
                        profiles = [
                            {'track': item.get('track') or {},
                             'starts': [cue['start'] for cue in
                                        item.get('cues') or []]}
                            for item in reference['track_cues']]
                        union = sorted({t for item in profiles
                                        for t in item['starts']})
                        cut_sig = _cut_signature_from_source(
                            src, head_hint=head, log=_log)
                        _log('cue-profile: MP4 %d cue(s), %d track(s)'
                             % (len(union), len(profiles)))
                        return {'starts': union,
                                'track_starts': profiles,
                                'tracks': reference.get('tracks') or [],
                                'cut_signature': cut_sig,
                                'bytes': src.fetched, 'requests': src.reqs}
                except Exception as mp4_error:
                    _log('cue-profile: MP4 index skipped (%s)'
                         % type(mp4_error).__name__)
            # Other byte-addressable sources may still have a useful cut ID:
            # manual-delay learning can be scoped without an audio scan.
            cut_sig = _cut_signature_from_source(
                src, head_hint=getattr(src, '_cut_head', b''), log=_log)
            _log('cue-profile: no Matroska index (%s), cut=%s'
                 % (e, 'yes' if cut_sig else 'no'))
            out = {'starts': [], 'track_starts': [], 'tracks': [],
                   'cut_signature': cut_sig, 'bytes': src.fetched,
                   'requests': src.reqs}
            if envelope:
                out['envelope'] = envelope
            return out
        subs = sub_tracks(tracks)
        anchors = [t for t in subs if not t.get('forced')]
        if not anchors:
            cut_sig = _cut_signature_from_source(
                src, head_hint=getattr(src, '_cut_head', b''), log=_log)
            return {'starts': [], 'track_starts': [],
                    'tracks': [], 'cut_signature': cut_sig,
                    'bytes': src.fetched, 'requests': src.reqs}
        by_track = read_cue_times_multi(
            src, seeks, seg_start, {t['num'] for t in anchors}, _log)
        scale_ms = ts_scale / 1e6
        origin_ms = (timeline_origin(src, seg_start, scale_ms, _log)
                     if by_track else 0.0)
        # Timing is the primary feature.  Take the two extra signature samples
        # only after Cues+origin are safely in hand, so provider pushback during
        # this optional identity refinement cannot erase a usable reference.
        cut_sig = _cut_signature_from_source(
            src, head_hint=getattr(src, '_cut_head', b''), log=_log)

        def _clean_track(t):
            return {k: v for k, v in t.items()
                    if k != 'private' and not isinstance(v, bytes)}

        profiles = []
        for track in anchors:
            raw = by_track.get(track['num']) or []
            starts = sorted({int(round(t * scale_ms - origin_ms)) for t in raw
                             if (t * scale_ms - origin_ms) >= 0})
            if starts:
                profiles.append({'track': _clean_track(track),
                                 'starts': starts})
        union = sorted({t for p in profiles for t in p['starts']})
        _log('cue-profile: %d union time(s), %d/%d track(s), cut=%s, '
             '%d req / %.0fKB' % (
                 len(union), len(profiles), len(anchors),
                 'yes' if cut_sig else 'no', src.reqs, src.fetched / 1024.0))
        return {'starts': union, 'track_starts': profiles,
                'tracks': [_clean_track(t) for t in subs],
                'cut_signature': cut_sig, 'bytes': src.fetched,
                'requests': src.reqs}
    except Exception as e:
        (log or noop)('cue_reference_profile failed: %s' % e)
        return {}
    finally:
        if src is not None:
            try:
                src.close()
            except Exception:
                pass


def cue_reference_times(url_or_path, track_num=None, lang=None,
                         head_bytes=DEFAULT_HEAD_BYTES, allow_http=False,
                         abort_cb=None, log=None):
    """Return the embedded subtitle track's dense cue START times as a SORTED
    list of ints (milliseconds, rebased to the playback timeline), or [] when
    the file has no per-subtitle Cues index / no matching track / can't be read.

    CHEAP by design: reads ONLY the head + the Cues element + a tiny timeline-
    origin probe -- a handful of range requests, NEVER the ~1 request-per-cue
    cluster fetches that `extract_srt` needs. That makes it safe on a strict
    debrid token (e.g. TorBox) where the full-text extract starves the player.
    The returned times are the exact instants each embedded subtitle line
    appears, i.e. a dense, ground-truth timing skeleton for re-syncing an
    external subtitle. `allow_http` must be True for an HTTP/debrid source
    (default False = local only). Never raises."""
    _log = log or noop
    try:
        src = Source(url_or_path)
        src._abort_cb = abort_cb
        if not src.total:
            return []
        if src.is_http and not allow_http:
            _log('cue-times: HTTP not allowed (setting off) -- skipping')
            return []
        seg_start, ts_scale, tracks, seeks = parse_head(src, head_bytes, _log)
        subs = sub_tracks(tracks)
        if not subs:
            return []
        # A caller asking for a particular language/track needs that exact text
        # track's skeleton.  The no-selector caller (SubSync's playing-file
        # probe) has a different job: establish the VIDEO timeline.  Every
        # non-forced subtitle track, including bitmap PGS/VobSub, lives on that
        # same timeline.  Read the Cues element once and merge all of them, as
        # the local-file mkv_probe already does.  This both covers PGS-only
        # releases and prevents a sparse first text track from being mistaken
        # for the only available anchor. Forced/signs-only tracks stay out.
        if track_num is None and not lang:
            anchors = [t for t in subs if not t.get('forced')]
            if not anchors:
                _log('cue-times: only forced track(s) present -- skipping')
                return []
            by_track = read_cue_times_multi(
                src, seeks, seg_start,
                {t['num'] for t in anchors}, _log)
            raw_times = sorted({value for values in by_track.values()
                                for value in values})
            picked = '%d non-forced track(s)' % len(by_track)
        else:
            track = pick_track(subs, track_num, lang)
            if track is None:
                _log('cue-times: no matching track (num=%s lang=%s)'
                     % (track_num, lang))
                return []
            raw_times = read_cue_times(
                src, seeks, seg_start, track['num'], _log)
            picked = 'track #%s' % track['num']
        if not raw_times:
            _log('cue-times: no per-subtitle Cues index for %s' % picked)
            return []
        scale_ms = ts_scale / 1e6
        origin_ms = timeline_origin(src, seg_start, scale_ms, _log)
        out = sorted({int(round(t * scale_ms - origin_ms)) for t in raw_times
                      if (t * scale_ms - origin_ms) >= 0})
        _log('cue-times: %d dense reference time(s) from %s in %d req / %.0fKB'
             % (len(out), picked, src.reqs, src.fetched / 1024.0))
        return out
    except Exception as e:
        (log or noop)('cue_reference_times failed: %s' % e)
        return []


def cue_reference_times_multi(url_or_path, langs, track_num=None,
                              head_bytes=DEFAULT_HEAD_BYTES, allow_http=False,
                              abort_cb=None, log=None, stats=None):
    """Dense cue START times for SEVERAL languages in ONE head+Cues read.

    Returns {lang: sorted_times_ms} -- the same per-language skeleton
    `cue_reference_times` returns, but computed for every language in `langs` at
    once, reading the head + Cues element + timeline origin EXACTLY ONCE and
    slicing per track (one Cues index carries every track's cue points). This is
    what the cross-language embedded-align fallback uses so trying 2-3 languages
    costs ONE read, not one per language. `langs` is normalised to 2-letter
    codes; several may resolve to the same track. A language whose track has no
    per-cue index is omitted from the result. `allow_http` must be True for an
    HTTP/debrid source. `stats`, if given, is filled in the same way extract_srt
    fills it -- above all 'backoffs', so the caller can tell provider pressure
    from an ordinary pause instead of guessing. Never raises."""
    _log = log or noop
    src = None
    try:
        seen = list(dict.fromkeys(
            [str(l).lower()[:2] for l in (langs or []) if l]))
        if not seen:
            return {}
        src = Source(url_or_path)
        src._abort_cb = abort_cb
        src._log = _log
        # The head is read the gentle way (see _HTTP_PIECE): this runs first
        # when a viewer picks the embedded track in the automatic mode. The
        # index goes in one request, as before: this path has no buffer gate
        # to use the gaps between pieces, and it is meant to be quick.
        if src.is_http:
            src._piece = HTTP_PIECE
        src._stats = stats if stats is not None else None
        if src._stats is not None:
            src._stats['backoffs'] = 0
            src._stats['pace'] = src._pace
        if not src.total:
            return {}
        if src.is_http and not allow_http:
            _log('cue-times: HTTP not allowed (setting off) -- skipping')
            return {}
        seg_start, ts_scale, tracks, seeks = parse_head(src, head_bytes, _log)
        src._piece = None
        subs = sub_tracks(tracks)
        if not subs:
            return {}
        # Map each requested language -> the track it selects (the SAME picker the
        # single-lang path uses); several langs can resolve to one track (e.g. the
        # sole-text-track fallback), which then serves them all from one bucket.
        lang_track = {}
        want_tracks = set()
        for lg in seen:
            tr = pick_track(subs, track_num, lg)
            if tr is not None:
                lang_track[lg] = tr['num']
                want_tracks.add(tr['num'])
        if not want_tracks:
            _log('cue-times(multi): no matching track for langs=%s' % (seen,))
            return {}
        by_track = read_cue_times_multi(src, seeks, seg_start, want_tracks, _log)
        if not by_track:
            _log('cue-times(multi): no per-subtitle Cues index for track(s) %s'
                 % (sorted(want_tracks),))
            return {}
        scale_ms = ts_scale / 1e6
        origin_ms = timeline_origin(src, seg_start, scale_ms, _log)
        out = {}
        for lg, tnum in lang_track.items():
            raw_times = by_track.get(tnum)
            if not raw_times:
                continue
            out[lg] = sorted({int(round(t * scale_ms - origin_ms))
                              for t in raw_times
                              if (t * scale_ms - origin_ms) >= 0})
        _log('cue-times(multi): %d/%d lang(s) indexed in %d req / %.0fKB'
             % (len(out), len(seen), src.reqs, src.fetched / 1024.0))
        return out
    except Exception as e:
        (log or noop)('cue_reference_times_multi failed: %s' % e)
        return {}
    finally:
        try:
            if src is not None and src.is_http:
                src.close()
        except Exception:
            pass
