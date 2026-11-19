# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Probes: where the playing file is (local or remote) and the remote reference bundle.

import os
import time
import hashlib
try:
    from resources.lib.core import kodi, selection as core_selection, settings, video_context
except Exception:
    kodi = core_selection = settings = video_context = None


def probe_enabled():
    try:
        return (settings.get_setting('subsync_probe', 'true') or
                'true').strip().lower() != 'false'
    except Exception:
        return True


def playing_url(info):
    """Return a local playing file suitable for automatic probing.

    Never open extra HTTP media connections alongside Kodi playback: range
    probes can exhaust a host's request/connection allowance and interrupt
    the player itself. Cached references are consulted before this gate.
    Subtitle-provider oracle downloads do not use this media path.
    """
    from resources.lib.sync.service import log
    url = _media_url(info)
    clean = (url or '').split('|')[0].strip()
    low = clean.lower()
    if not low:
        return ''
    if low.endswith(('.m3u8', '.mpd', '.f4m')) or 'manifest' in low:
        # A playlist is not the media bytes. Hashing its text as a cut ID
        # would scope corrections to the manifest rather than the video.
        return ''
    if low.startswith(('http://', 'https://')):
        log('remote media probing skipped to protect playback')
        return ''
    if os.path.isfile(clean):
        return clean
    return ''


def remote_playing_url(info):
    """Direct HTTP(S) Matroska URL, stripped of Kodi's `|Header=...` suffix.

    HLS/DASH manifests are not byte-addressable media files and never enter the
    cue-index probe.  This is intentionally separate from _playing_url(), whose
    contract remains local-only for the heavier mkv_probe/audio paths.
    """
    url = _media_url(info)
    clean = (url or '').split('|')[0]
    low = clean.lower()
    if (low.startswith(('http://', 'https://'))
            and '.m3u8' not in low and '.mpd' not in low
            and '.f4m' not in low and 'manifest' not in low):
        return clean
    return ''


def current_stream_url():
    """The stream Kodi is playing now, without Kodi's header suffix."""
    return current_stream_transport().split('|')[0].strip()


def current_stream_transport():
    """Exact Kodi transport, including its content-selecting header suffix."""
    try:
        import xbmc
        return (xbmc.Player().getPlayingFile() or '').strip()
    except Exception:
        return ''


def _media_url(info):
    """Media URL for this operation, pinned by a queued job when available.

    A service job may outlive the film that created it.  Using Kodi's current
    URL in that case would compare the old subtitle with the next film and could
    poison both local and community timing memory.  Queued jobs inject their
    captured URL through the private key below; foreground calls still use the
    live player and then the metadata fallback.
    """
    try:
        pinned = (info.get('_subsync_stream_url') or '').strip()
    except Exception:
        pinned = ''
    if pinned:
        return pinned
    current = current_stream_transport()
    if current:
        return current
    try:
        return (info.get('filepath') or '').strip()
    except Exception:
        return ''


def transport_cache_key(info):
    """Opaque session/file key for the probe cache; never stores a URL token."""
    try:
        value = _media_url(info)
        if not value:
            return ''
        raw = value.strip()
        clean = raw.split('|')[0].strip()
        # Local paths can be reused after replacement.  Size+mtime keep their
        # transport entry separate until the content signature is recomputed.
        is_local = os.path.isfile(clean)
        if is_local:
            st = os.stat(clean)
            clean = '%s|%d|%d' % (
                os.path.abspath(clean), int(st.st_size),
                int(getattr(st, 'st_mtime_ns', int(st.st_mtime * 1e9))))
        identity = clean if is_local else raw
        digest = hashlib.sha256(
            identity.encode('utf-8', 'replace')).hexdigest()
        return 'media:' + digest[:32]
    except Exception:
        return ''


def local_cut_signature(info):
    """Cheap three-sample content identity for a local playing file."""
    from resources.lib.sync.service import log
    path = playing_url(info)
    if not path:
        return ''
    try:
        from resources.lib.fetching.embedded.extract.cue_reference import media_cut_signature
        return media_cut_signature(
            path, allow_http=False,
            log=lambda m: log('cut-id: ' + m)) or ''
    except Exception as e:
        log('local cut-id failed: %r' % e, level='DEBUG')
        return ''


def remote_probe_ready(max_wait_s=8.0, expected_url=''):
    """Wait in the background until playback is stable enough for tiny reads."""
    try:
        import xbmc
        player = xbmc.Player()
        deadline = time.monotonic() + max(0.0, float(max_wait_s))
        while time.monotonic() < deadline:
            if not player.isPlayingVideo():
                return False
            if expected_url and current_stream_url() != expected_url:
                return False
            try:
                played = float(player.getTime() or 0.0)
            except Exception:
                played = 0.0
            busy = (xbmc.getCondVisibility('Player.Caching')
                    or xbmc.getCondVisibility('Player.Paused'))
            if played >= 6.0 and not busy:
                return True
            xbmc.sleep(400)
        return False
    except Exception:
        return False


def starts_to_cues(starts):
    points = sorted({int(x) for x in (starts or []) if int(x) >= 0})
    cues = []
    for i, start in enumerate(points):
        nxt = points[i + 1] if i + 1 < len(points) else start + 3000
        cues.append({'start': start,
                     'end': start + max(600, min(3000, nxt - start - 100))})
    return cues


def remote_reference_bundle(url):
    """Per-track video reference + cut id via bounded container-index reads.

    This deliberately does not extract subtitle text or scan media clusters.
    The underlying reader reuses one keep-alive connection, paces requests,
    validates byte ranges and trips on provider pressure.  Failure is a silent
    miss; the selected subtitle remains untouched.
    """
    from resources.lib.sync.service import log
    ready_started = time.monotonic()
    if not url or not remote_probe_ready(expected_url=url):
        log('remote-cues: playback not ready after %.2fs'
             % (time.monotonic() - ready_started))
        return {'probe_error': True}
    started = time.monotonic()
    log('remote-cues: playback ready in %.2fs'
         % (started - ready_started))

    def abort():
        if time.monotonic() - started > 35.0:
            return True
        try:
            import xbmc
            return (not xbmc.Player().isPlayingVideo()
                    or current_stream_url() != url)
        except Exception:
            return False

    try:
        from resources.lib.fetching.embedded.extract.cue_reference import cue_reference_profile, cue_reference_times
        reader = cue_reference_profile
        if reader is not None:
            raw = reader(url, allow_http=True, abort_cb=abort,
                         log=lambda m: log('remote-cues: ' + m)) or {}
        else:  # older in-memory module after a quick update
            starts = cue_reference_times(
                url, allow_http=True, abort_cb=abort,
                log=lambda m: log('remote-cues: ' + m))
            raw = {'starts': starts or [], 'track_starts': [],
                   'legacy_single_track': True}
    except Exception as e:
        log('remote cue-index probe failed after %.2fs: %r'
             % (time.monotonic() - started, e), level='WARNING')
        return {'probe_error': True}
    log('remote-cues: index read in %.2fs (%d request(s), %d byte(s))'
         % (time.monotonic() - started, int(raw.get('requests') or 0),
            int(raw.get('bytes') or 0)))
    cues = starts_to_cues(raw.get('starts') or [])
    profiles = []
    for item in raw.get('track_starts') or []:
        tc = starts_to_cues(item.get('starts') or [])
        if tc:
            profiles.append({'track': item.get('track') or {}, 'cues': tc})
    if cues:
        log('remote-cues: %d union cues / %d independent track(s) ready'
             % (len(cues), len(profiles)))
    return {'cues': cues, 'track_cues': profiles,
            'tracks': raw.get('tracks') or [],
            'cut_signature': raw.get('cut_signature') or '',
            'envelope': raw.get('envelope'),
            'bytes': raw.get('bytes') or 0,
            'requests': raw.get('requests') or 0,
            'legacy_single_track': bool(raw.get('legacy_single_track')),
            'track': {'source': 'matroska-cues-index'}}
