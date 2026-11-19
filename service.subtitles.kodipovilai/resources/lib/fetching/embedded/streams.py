# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Embedded subtitle streams of the playing video: the playback snapshot, the embedded rows
# of the list and the stream switch.

import json
import hashlib
import re
import time
import urllib.parse
from resources.lib.core import kodi, settings, video_context


def enabled():
    """Master gate. False => this whole module is inert."""
    try:
        return settings.get_bool('use_builtin_engine', False)
    except Exception:
        return False


def _wait_for_subtitle_streams(player, max_tenths=25):
    """Poll the player's subtitle-stream list until it populates. The
    demuxer often hasn't exposed embedded streams yet right after playback
    starts (the search dialog opens at ~00:00:02), so an immediate read
    returns []. Mirrors DarkSubs's wait_for_video_and_return_subs_list but
    capped shorter to stay responsive. Returns the stream list."""
    import xbmc
    subs = []
    once = True
    vidtime_pre = 0
    for _ in range(max_tenths):
        try:
            subs = player.getAvailableSubtitleStreams() or []
            if subs:
                return subs
            vidtime = player.getTime()
            if vidtime > 0:
                if once:
                    vidtime_pre = vidtime
                    once = False
                elif vidtime_pre != vidtime:
                    # Time advanced and still no streams -> none coming.
                    break
        except Exception:
            pass
        xbmc.sleep(100)
    return subs


# --- Embedded-stream snapshot (taken at PLAYBACK START) ----------------------
# Kodi's getAvailableSubtitleStreams() returns BOTH the file's embedded streams
# AND any EXTERNAL sub that's since been loaded (including one WE loaded -- e.g.
# an AI translation). Polling it live each time the picker opens therefore
# misreads our own external sub as an "embedded Hebrew 101%" entry, which then
# pins the picker on that sub. The fix: snapshot the stream list ONCE at play
# start (before anything external is loaded) and offer embedded entries only
# from that snapshot. Embedded streams keep their (low) indices even after Kodi
# appends externals, so the snapshot index stays valid at select time.
#
# CRITICAL: the snapshot is taken in the SERVICE process (auto-on-play) but read
# in the SUBTITLE-DIALOG process (default.py) -- two separate Python instances,
# so a module global wouldn't be visible across them. Store it on a Kodi WINDOW
# PROPERTY (Window(10000)), which every add-on process shares.
_SNAP_PROP = 'povil.embedded_snap'


def _snap_get():
    try:
        import xbmcgui
        raw = xbmcgui.Window(10000).getProperty(_SNAP_PROP)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _snap_set(key, streams, sealed=False, media_key=None):
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(
            _SNAP_PROP,
            json.dumps({'key': key, 'media': media_key or {},
                        'streams': list(streams or []),
                        'sealed': bool(sealed), 'ts': time.time()},
                       ensure_ascii=False))
    except Exception:
        pass


def _stream_key(info):
    """Private transport identity for the current playback.

    Include Kodi's header suffix because debrid endpoints may reuse the visible
    URL and select the actual object through those headers.  Only a digest is
    stored in the shared Window property.
    """
    try:
        import xbmc
        f = (xbmc.Player().getPlayingFile() or '').strip()
    except Exception:
        f = ''
    value = f or ((info or {}).get('filepath') or '')
    if not value:
        value = (info or {}).get('title') or ''
    if not value:
        return ''
    return hashlib.sha256(
        str(value).encode('utf-8', 'replace')).hexdigest()[:32]


def _stream_media_key(info):
    """Opaque title identity with optional, progressively-known episode ids."""
    info = info or {}
    ids = {
        'tmdb': str(info.get('tmdb_id') or '').strip().lower(),
        'imdb': str(info.get('imdb_id') or '').strip().lower(),
    }
    if not (ids['tmdb'] or ids['imdb']):
        ids.update({
            'tvshow': str(info.get('tvshow') or '').strip().lower(),
            'title': str(info.get('title') or '').strip().lower(),
            'year': str(info.get('year') or '').strip().lower(),
        })
    if not any(ids.values()):
        return {}
    canonical = json.dumps(ids, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'))
    return {
        'base': hashlib.sha256(
            canonical.encode('utf-8', 'replace')).hexdigest()[:24],
        # S/E can arrive a moment after onAVStarted. Store them separately so
        # a later poll can enrich a snapshot, while two fully-known episodes
        # sharing a recycled URL can never reuse each other's stream indices.
        'season': str(info.get('season') or '').strip().lower(),
        'episode': str(info.get('episode') or '').strip().lower(),
    }


def _media_keys_match(stored, current, allow_unknown_current=False):
    if stored and not current:
        # A recycled endpoint can expose the next title before Kodi publishes
        # its metadata. Consumers must not show the previous title's stream
        # indices in that window. Writers may still treat this as the same play
        # so a late poller cannot overwrite a sealed empty baseline with our
        # own external Hebrew file.
        return bool(allow_unknown_current)
    if not stored or not current:
        return True
    if not isinstance(stored, dict) or not isinstance(current, dict):
        return stored == current
    if (stored.get('base') and current.get('base')
            and stored.get('base') != current.get('base')):
        return False
    for field in ('season', 'episode'):
        if stored.get(field) and not current.get(field):
            if not allow_unknown_current:
                return False
            continue
        if (stored.get(field) and current.get(field)
                and stored.get(field) != current.get(field)):
            return False
    return True


def _merge_media_key(stored, current):
    if not isinstance(stored, dict):
        stored = {}
    if not isinstance(current, dict):
        current = {}
    return {field: current.get(field) or stored.get(field) or ''
            for field in ('base', 'season', 'episode')}


def _snapshot_matches(snap, info, allow_unknown_current=False):
    """Match both transport and media when both sides know the media id."""
    if not snap or snap.get('key') != _stream_key(info):
        return False
    stored_media = snap.get('media') or ''
    current_media = _stream_media_key(info)
    return _media_keys_match(
        stored_media, current_media,
        allow_unknown_current=allow_unknown_current)


def have_playback_snapshot(info=None):
    """True when the current file has a complete play-start snapshot.

    A non-empty capture is complete immediately. An empty capture becomes
    complete only when its caller explicitly seals it after a bounded poll; that
    distinction prevents both premature empty snapshots and late external files
    from being mistaken for embedded streams.
    """
    try:
        snap = _snap_get()
        if info is None:
            try:
                info = video_context.current_video_info() or {}
            except Exception:
                info = {}
        return bool(_snapshot_matches(snap, info)
                    and (snap.get('streams') or snap.get('sealed')))
    except Exception:
        return False


def note_playback_streams(info, streams=None, final=False):
    """Snapshot the embedded/local subtitle streams at PLAY START, before any
    external sub is loaded. Call ONCE per file, as early as possible. `streams`
    may be passed if the caller already polled them (auto-on-play does). Set
    ``final`` only after that poll is complete; a final empty list is sealed.
    Stored on a window property so the subtitle-dialog process can read it."""
    try:
        import xbmc
        player = xbmc.Player()
        if not player.isPlayingVideo():
            return
        key = _stream_key(info)
        media_key = _stream_media_key(info)
        cur = _snap_get()
        # A non-empty snapshot is final. An empty one stays provisional until a
        # caller that completed its bounded poll seals it. Once sealed, a late
        # poller cannot overwrite it with the external subtitle we appended.
        if (_snapshot_matches(cur, info, allow_unknown_current=True)
                and (cur.get('streams') or cur.get('sealed'))):
            # Metadata often settles after the earliest play-start poll. Bind
            # that otherwise-complete snapshot to the newly available title id.
            merged_media = _merge_media_key(cur.get('media'), media_key)
            if merged_media != (cur.get('media') or {}):
                _snap_set(key, cur.get('streams') or [],
                          sealed=cur.get('sealed'), media_key=merged_media)
            return  # already captured for this file
        if streams is None:
            streams = _wait_for_subtitle_streams(player)
        _snap_set(key, streams, sealed=final, media_key=media_key)
        kodi.log('embedded baseline ({0} stream(s)): {1}'.format(
            len(streams or []), list(streams or [])), level='INFO')
        # If this source ships a built-in Hebrew track, tell the pool so the
        # source screen flags it "BUILT-IN 100%" for everyone. Automatic,
        # deduped + backgrounded inside pool.report_embedded; never blocks.
        try:
            def _is_he_label(n):
                low = (n or '').strip().lower()
                if not low or is_external_stream(low):
                    return False
                if LANG_NORMALIZE.get(low, low[:2]) == 'he':
                    return True
                # Descriptive labels a code/2-char lookup misses:
                # "Hebrew", "Hebrew SDH", "Forced Hebrew", or Hebrew script.
                if 'hebrew' in low:
                    return True
                return any('֐' <= ch <= 'ת' for ch in (n or ''))
            has_he = any(_is_he_label(n) for n in (streams or []))
            _rel = ''
            if has_he:
                from resources.lib.ai_translation.reuse import pool_contribute
                _rel = video_context.release_name(info, 'pool')
                pool_contribute.report_embedded(info)
                try:
                    from resources.lib import he_sub_match as _hsm
                    if _rel:
                        _hsm.merge_embedded(info, [_rel])
                except Exception:
                    pass
            # Diagnostic (once per play): shows WHY a source did/didn't get the
            # BUILT-IN flag -- whether Hebrew was detected, the release we stored,
            # and which field it came from. Makes a release-name mismatch vs the
            # source row, or an empty release, visible in a single line.
            try:
                kodi.log(
                    'embedded-report he={0} rel={1!r} ids(tmdb={2!r} imdb={3!r} '
                    's={4!r} e={5!r} mt={6!r} isep={7}) (picked={8!r} li={9!r} '
                    'fp={10!r})'.format(
                        has_he, _rel, info.get('tmdb_id', ''),
                        info.get('imdb_id', ''), info.get('season', ''),
                        info.get('episode', ''), info.get('media_type', ''),
                        info.get('is_episode', ''), info.get('picked_release', ''),
                        info.get('li_filename', ''), info.get('filepath', '')),
                    level='INFO')
            except Exception:
                pass
        except Exception:
            pass
    except Exception:
        pass


def seal_playback_streams(info=None):
    """Freeze the embedded baseline before an external subtitle is appended.

    An empty, fully-polled baseline is meaningful. Without this seal, a second
    ``onAVStarted`` poller can see the external Hebrew file we just loaded and
    advertise it as a fake 101% built-in track on the next picker open.
    """
    try:
        import xbmc
        player = xbmc.Player()
        if not player.isPlayingVideo():
            return False
        key = _stream_key(info or {})
        media_key = _stream_media_key(info or {})
        cur = _snap_get()
        if (_snapshot_matches(cur, info or {}, allow_unknown_current=True)
                and cur.get('sealed')):
            merged_media = _merge_media_key(cur.get('media'), media_key)
            if merged_media != (cur.get('media') or {}):
                _snap_set(key, cur.get('streams') or [], sealed=True,
                          media_key=merged_media)
            return True
        streams = []
        if _snapshot_matches(cur, info or {}, allow_unknown_current=True):
            streams = list(cur.get('streams') or [])
        else:
            try:
                streams = list(player.getAvailableSubtitleStreams() or [])
            except Exception:
                streams = []
        _snap_set(key, streams, sealed=True, media_key=media_key)
        return True
    except Exception:
        return False


def _wait_for_snapshot(info, timeout=3.0):
    """Bounded wait for the play-start snapshot to be populated for `key`.

    On the FIRST play, the subtitle dialog / autosub search can fire before the
    service process finished capturing the snapshot (note_playback_streams waits
    for the embedded streams to enumerate). Without this, the embedded-Hebrew
    101% entry only appeared from the SECOND open onward. We poll the same
    play-start snapshot -- never the live stream list -- so there is still no
    risk of misreading an external sub as embedded. Returns the snapshot dict or
    None."""
    try:
        import xbmc
        key = _stream_key(info)
        player = xbmc.Player()
        monitor = xbmc.Monitor()
        elapsed = 0.0
        provisional = None
        while elapsed < timeout:
            if not player.isPlayingVideo():
                return provisional
            snap = _snap_get()
            if _snapshot_matches(snap, info):
                if snap.get('streams'):
                    return snap
                # Empty means "captured nothing YET" (see note_playback_streams):
                # keep waiting for a real list rather than accepting it, but
                # remember it so a file that truly has no streams still resolves
                # instead of blocking the dialog for the full timeout twice.
                if snap.get('streams') is not None:
                    provisional = snap
            if monitor.waitForAbort(0.2):
                return provisional
            elapsed += 0.2
        return provisional
    except Exception:
        return None


def embedded_candidates(info):
    """Offer the file's EMBEDDED / local subtitle streams (mirroring DarkSubs's
    [LOC] entries): Hebrew at the top as 101%, other languages as selectable
    "[מובנה] XX" entries. Uses the play-start snapshot (see above) so an
    external sub we loaded -- e.g. an AI translation -- can NEVER be mistaken
    for an embedded stream. With no snapshot (e.g. autosub off, or the dialog
    opened before play start was captured) it offers nothing rather than risk a
    misread."""
    if not enabled():
        return []
    key = _stream_key(info)
    snap = _snap_get()
    if not _snapshot_matches(snap, info):
        # First-play race: the play-start snapshot is captured in the service
        # process and may not be ready when the dialog opens. Wait briefly for
        # it so the embedded-Hebrew 101% entry shows on the FIRST open too.
        snap = _wait_for_snapshot(info)
        if not _snapshot_matches(snap, info):
            return []
    streams = snap.get('streams')
    if not streams:
        return []
    # Built-in Hebrew here too -> flag the release for everyone (deduped,
    # backgrounded). Covers users who open the picker with autosub off.
    try:
        if any(_stream_code(n) == 'he' for n in streams):
            from resources.lib.ai_translation.reuse import pool_contribute
            pool_contribute.report_embedded(info)
            try:
                from resources.lib import he_sub_match as _hsm
                _rel = video_context.release_name(info, 'pool')
                if _rel:
                    _hsm.merge_embedded(info, [_rel])
            except Exception:
                pass
    except Exception:
        pass
    out = []
    for idx, name in enumerate(streams):
        n = (name or '').strip().lower()
        if not n or is_external_stream(n):
            continue
        code = _stream_code(n)
        if code == 'he':
            out.append({
                'filename': 'תרגום מובנה בעברית · 101%',
                'language': 'he',
                'link': urllib.parse.quote(json.dumps({
                    'type': 'engine', 'embedded': True,
                    'stream_index': idx, 'lang': 'he',
                }, ensure_ascii=False)),
                'sync': 'true', 'rating': '5',
                'is_hi': False, 'is_hd': False,
                '_engine_kind': 'embedded_he', '_pct': 101,
            })
        else:
            out.append({
                'filename': '[מובנה] {0}'.format(code.upper()),
                'language': code or 'und',
                'link': urllib.parse.quote(json.dumps({
                    'type': 'engine', 'embedded': True,
                    'stream_index': idx, 'lang': code,
                }, ensure_ascii=False)),
                'sync': 'false', 'rating': '3',
                'is_hi': False, 'is_hd': False,
                '_engine_kind': 'embedded_other', '_pct': 0,
            })
    return out


def _iso639_1(name):
    """The 2-letter code of a stream's language as Kodi names it ('swe',
    'rum', 'jpn'). The first two letters are another language for several of
    them -- 'sw' is Swahili, 'ru' Russian, 'sl' Slovenian -- so a Swedish
    embedded pick read nothing and a Romanian one was labelled Russian (review
    of 0.3.82): through the ISO table the extractor matches tracks with."""
    n = (name or '').strip().lower()
    if len(n) == 3:
        try:
            from resources.lib.fetching.embedded.extract.tracks import lang_key
            k = lang_key(n)
            if len(k) == 2:
                return k
        except Exception:
            pass
        return n            # 'fil' is not Finnish, 'und' is not a language
    return n[:2] if len(n) >= 2 else n


# Kodi names a subtitle file it loaded beside the video "<name> (External)",
# "(חיצוני)" in Hebrew -- when no language is read from the file name. One
# loaded before the play-start snapshot (POV's own subtitle, a viewer's log of
# 17 Nov 2026: 'POVSubs_tt22084616_he (חיצוני)') is in the snapshot, and the
# Hebrew word alone made it "built-in Hebrew": the release was reported to the
# pool as carrying a Hebrew track, and the list showed an "[מובנה] PO" row.
_EXTERNAL_STREAM_RE = re.compile(r'\((?:external|חיצוני)\)\s*$', re.I)


def is_external_stream(name):
    """True for a stream Kodi marks as a subtitle file, not part of the video."""
    return bool(_EXTERNAL_STREAM_RE.search(name or ''))


def _stream_code(name):
    """The code an embedded stream's row and pick go by."""
    n = (name or '').strip().lower()
    return LANG_NORMALIZE.get(n) or _iso639_1(n)

# Fix common non-ISO-639-1 codes some providers emit so Kodi shows a flag.
LANG_NORMALIZE = {
    'gr': 'el', 'gre': 'el', 'ell': 'el', 'greek': 'el',
    'sp': 'es', 'spa': 'es', 'spanish': 'es',
    'per': 'fa', 'fas': 'fa', 'far': 'fa', 'persian': 'fa',
    'iw': 'he', 'heb': 'he', 'hebrew': 'he',
    'eng': 'en', 'english': 'en',
    'ara': 'ar', 'arabic': 'ar',
    'rus': 'ru', 'russian': 'ru',
    'fre': 'fr', 'fra': 'fr', 'french': 'fr',
    'ger': 'de', 'deu': 'de', 'german': 'de',
    'dut': 'nl', 'nld': 'nl', 'por': 'pt', 'ita': 'it',
    'tur': 'tr', 'pol': 'pl', 'chi': 'zh', 'zho': 'zh',
}


def select_embedded(stream_index, lang=None):
    """Switch Kodi to the chosen EMBEDDED subtitle stream. The index comes from
    the play-start snapshot, where only true embedded streams existed; Kodi
    appends external subs at HIGHER indices, so this index still points at the
    embedded stream. Use it directly. Only if it's out of range do we re-find
    the first stream of the requested language (lowest index = the embedded
    one), never an external appended later.

    NOTE (2026-08-15): an embedded HEBREW track renders with its line-final
    punctuation at the wrong end, because Kodi draws the track itself with an
    LTR BiDi base direction and we cannot put RLE..PDF into bytes we do not
    own. The only repair is to extract the track's text and deliver our own
    copy, and that was built and then REMOVED at the build owner's decision --
    extraction is not dependable enough across debrid providers to be the
    answer. Do not rebuild it here without that conversation. See HANDOFF.md."""
    try:
        import xbmc
        p = xbmc.Player()
        try:
            streams = p.getAvailableSubtitleStreams() or []
        except Exception:
            streams = []
        target = int(stream_index)
        if target < 0 or (streams and target >= len(streams)):
            want = (lang or 'he').lower()
            target = next(
                (i for i, name in enumerate(streams)
                 if _stream_code(name) == want),
                int(stream_index))
        p.setSubtitleStream(target)
        p.showSubtitles(True)
        kodi.log('subs_engine_bridge.select_embedded: set stream {0}'
                       .format(target), level='INFO')
        return True
    except Exception as e:
        kodi.log('subs_engine_bridge.select_embedded failed: {0}'
                       .format(e), level='WARNING')
        return False


def download(payload, for_delivery=True):
    """Resolve an 'engine' link to a Hebrew SRT path on disk. Returns
    the path or None. Called from translate.resolve().

    ``for_delivery=False`` returns the immutable canonical/cache source for
    hashing, pool sharing and language/timing analysis. The default returns a
    separate RTL-rendered copy for a Hebrew SRT/VTT. Neither path performs any
    Worker request by itself.
    """
    from resources.lib.fetching.bridge import download_inner
    if not enabled():
        return None
    try:
        return download_inner(payload, for_delivery=for_delivery)
    except Exception as e:
        kodi.log('subs_engine_bridge.download failed ({0}): {1}'.format(
            (payload.get('source') or '?'), e), level='ERROR')
        # Log only -- no user popup. This fired on every failed provider download,
        # including background/secondary Ktuvit fetches the server "refuses"
        # (raising "Ktuvit refused the file...") while the subtitle the user
        # actually picked loads fine -- which spammed a popup on every playback.
        # The exact reason is in the ERROR log above for diagnosis.
        return None
