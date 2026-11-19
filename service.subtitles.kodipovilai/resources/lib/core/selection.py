# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The subtitle selection: the token triple, registration, delivery staging and the
# sync-status state machine, kept on Window(10000) properties.

import os
import threading
import hashlib
import json
import time
import urllib.parse
try:
    import xbmc
    import xbmcaddon
    import xbmcvfs
    import xbmcgui
    KODI_AVAILABLE = True
except ImportError:
    # Allows unit tests / standalone scripts to import the module
    # without a Kodi runtime.
    KODI_AVAILABLE = False
    xbmc = None
    xbmcaddon = None
    xbmcvfs = None
    xbmcgui = None
from resources.lib.core.kodi import home_window, notify
from resources.lib.core.video_context import current_video_info


_CURRENT_SUB_PROP = 'moransubs.current_sub'
_CURRENT_SUB_ID_PROP = 'moransubs.current_sub_id'
_CURRENT_SUB_STATUS_PROP = 'moransubs.current_subsync_status'
_CURRENT_SUB_TOKEN_PROP = 'moransubs.current_sub_token'
_CURRENT_SUB_FIX_READY_PROP = 'moransubs.current_subsync_fix_ready'
_CURRENT_SUB_DELIVERY_PROP = 'moransubs.current_subsync_delivery'

# Short, skin-safe labels. The value is shown only for the subtitle that is
# actually selected, after the exact playing stream has been matched. It is a
# Window property (RAM for this Kodi session), not an on-disk cache or lookup.
_SUBTITLE_SYNC_LABELS = {
    'checking': 'תזמון בבדיקה',
    'confirmed': 'כבר מסונכרנת',
    'fixed': 'סונכרנה אוטומטית',
    'unverified': 'התזמון טרם אומת',
}


def _current_stream_hash(stream_url=None):
    """Opaque identity of the exact playing URL; never persist/log its token."""
    try:
        value = stream_url
        if value is None:
            value = xbmc.Player().getPlayingFile()
        # Kodi's ``|Header=...`` suffix can select a different debrid object
        # even when the visible URL is identical.  It is safe to include here:
        # only the digest leaves this function, never the URL or its headers.
        value = (value or '').strip()
        if not value:
            return ''
        return hashlib.sha256(
            value.encode('utf-8', 'replace')).hexdigest()[:24]
    except Exception:
        return ''


def _subtitle_link_hash(link):
    try:
        return hashlib.sha256(
            (link or '').encode('utf-8', 'replace')).hexdigest()[:24]
    except Exception:
        return ''


def subtitle_candidate_identity(link):
    """Return a short, private identity that survives refreshed picker links.

    Provider rows are URL-encoded JSON. Some providers refresh transport
    details between the automatic search and a later manual picker open, even
    though the row still names the exact same subtitle. The old UI compared the
    entire encoded payload, so a subtitle that was already on screen could lose
    its ``current`` marker. Keep only the fields that identify the logical
    subtitle, then hash them; no provider URL, token or local path is exposed
    through the Kodi window property.
    """
    try:
        value = str(link or '')
        payload = None
        for _ in range(3):
            try:
                decoded = json.loads(value)
                if isinstance(decoded, dict):
                    payload = decoded
                    break
            except Exception:
                pass
            unquoted = urllib.parse.unquote(value)
            if unquoted == value:
                break
            value = unquoted
        if not payload:
            return _subtitle_link_hash(link)

        kind = str(payload.get('type') or '')
        stable = {'type': kind}
        if kind == 'pool':
            # The content hash is the community row's immutable identity. Its
            # release/source labels may be enriched by a later lookup.
            content_hash = str(payload.get('hash') or '')
            if not content_hash:
                return _subtitle_link_hash(link)
            stable['hash'] = content_hash
        elif kind in ('engine', 'engine_ai'):
            download_data = payload.get('download_data') or {}
            if not isinstance(download_data, dict):
                download_data = {}
            source = str(payload.get('source') or '').strip().lower()

            # A filename is not a provider row id: OpenSubtitles and other
            # engines can return two different files with the same display
            # name.  Use only explicit, stable provider identifiers.  When a
            # provider exposes none, retain strict full-link semantics rather
            # than risking a false ``current`` marker on another row.
            provider_id = None
            id_fields = (
                'id', 'file_id', 'fileId', 'subtitle_id', 'subtitleId',
                'sub_id', 'subId', 'SubtitleID',
            )
            if source != 'ktuvit':
                for field in id_fields:
                    value = download_data.get(field)
                    if value not in ('', None):
                        provider_id = {field: str(value)}
                        break
            if source == 'ktuvit':
                # Ktuvit_Page_ID identifies the film page, not one subtitle;
                # every sibling row shares it. Only FilmID+SubtitleID from the
                # signed request (or an explicit SubtitleID) is row-unique.
                direct_subtitle_id = download_data.get('SubtitleID')
                if direct_subtitle_id not in ('', None):
                    provider_id = {'SubtitleID': str(direct_subtitle_id)}
                raw_request = download_data.get('subtitle_download_data')
                try:
                    request = (json.loads(raw_request)
                               if isinstance(raw_request, str)
                               else raw_request) or {}
                    request = request.get('request') or request
                    film_id = request.get('FilmID')
                    subtitle_id = request.get('SubtitleID')
                    if film_id not in ('', None) and subtitle_id not in ('', None):
                        provider_id = {
                            'FilmID': str(film_id),
                            'SubtitleID': str(subtitle_id),
                        }
                except Exception:
                    pass
            if provider_id is None:
                return _subtitle_link_hash(link)
            stable.update({
                'embedded': bool(payload.get('embedded')),
                'stream_index': payload.get('stream_index', ''),
                'source': source,
                'language': str(payload.get('language') or
                                payload.get('lang') or '').strip().lower(),
                'provider_id': provider_id,
            })
        elif kind == 'embedded_sync':
            stable.update({
                'stream_index': payload.get('stream_index', ''),
                'language': str(payload.get('language') or
                                payload.get('lang') or 'he').strip().lower(),
            })
        elif kind in ('ai', 'embedded_ai'):
            stable.update({
                'source_lang': str(payload.get('source_lang') or '')
                               .strip().lower(),
                # Paths stay private because the canonical structure is hashed.
                'local_path': str(payload.get('local_path') or ''),
                'stream_index': payload.get('stream_index', ''),
            })
        elif kind == 'passthrough':
            stable['path'] = str(payload.get('path') or '')
        else:
            # Unknown future kinds retain strict semantics. Sorting keys makes
            # harmless JSON field-order changes stable without guessing which
            # new fields may be safe to ignore.
            stable = payload
        canonical = json.dumps(stable, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':'))
        return hashlib.sha256(
            canonical.encode('utf-8', 'replace')).hexdigest()[:24]
    except Exception:
        return _subtitle_link_hash(link)


_SELECTION_TOKEN_LOCK = threading.Lock()
_SELECTION_TOKEN_SEQ = [0]


def _new_subtitle_selection_token(link):
    with _SELECTION_TOKEN_LOCK:
        _SELECTION_TOKEN_SEQ[0] += 1
        sequence = _SELECTION_TOKEN_SEQ[0]
    try:
        seed = '{0}\0{1}\0{2}\0{3}'.format(
            time.time_ns(), threading.get_ident(), sequence, link or '')
        return hashlib.sha256(seed.encode('utf-8', 'replace')).hexdigest()[:24]
    except Exception:
        return hashlib.sha256(
            ('{0}\0{1}\0{2}'.format(time.time(), sequence, link or '')).encode(
                'utf-8', 'replace')).hexdigest()[:24]


def clear_subtitle_sync_status():
    """Clear the tiny current-selection status record (no disk/network I/O)."""
    try:
        win = home_window()
        if win is not None:
            token = win.getProperty(_CURRENT_SUB_TOKEN_PROP) or ''
            if token:
                win.clearProperty(_CURRENT_SUB_STATUS_PROP + '.' + token)
                win.clearProperty(_CURRENT_SUB_FIX_READY_PROP + '.' + token)
                win.clearProperty(_CURRENT_SUB_DELIVERY_PROP + '.' + token)
            # Remove a record left by the pre-token implementation too.
            win.clearProperty(_CURRENT_SUB_STATUS_PROP)
            win.clearProperty(_CURRENT_SUB_DELIVERY_PROP)
    except Exception:
        pass


def set_current_subtitle(link, renew=False):
    """Remember which subtitle (by its candidate link) is currently applied,
    so the picker can mark it as '» נוכחית' next time it opens. ``renew``
    gives a verified replacement a fresh ownership token even if its row link
    is unchanged. Stored on the home window across service/picker processes."""
    if not KODI_AVAILABLE:
        return
    try:
        win = home_window()
        if win is None:
            return
        new_link = link or ''
        try:
            old_link = win.getProperty(_CURRENT_SUB_PROP) or ''
        except Exception:
            old_link = get_current_subtitle()
        try:
            old_token = win.getProperty(_CURRENT_SUB_TOKEN_PROP) or ''
        except Exception:
            old_token = ''
        # A verdict belongs to one selected candidate. Re-selecting the same
        # one preserves its result; selecting anything else invalidates it.
        if old_link != new_link or renew:
            clear_subtitle_sync_status()
            # Background and manual-delay records are token-scoped. Retire only
            # the selection being replaced, so a late process cannot erase the
            # newer selection's state.
            if old_token:
                win.clearProperty('subsync.pending.' + old_token)
                win.clearProperty('subsync.delivered.' + old_token)
            win.clearProperty('subsync.pending')
            win.clearProperty('subsync.delivered')
            win.setProperty(
                _CURRENT_SUB_TOKEN_PROP,
                _new_subtitle_selection_token(new_link) if new_link else '')
        elif new_link and not (win.getProperty(_CURRENT_SUB_TOKEN_PROP) or ''):
            # Upgrade a live session from a release that pre-dated tokens.
            win.setProperty(_CURRENT_SUB_TOKEN_PROP,
                            _new_subtitle_selection_token(new_link))
        win.setProperty(_CURRENT_SUB_PROP, new_link)
        win.setProperty(_CURRENT_SUB_ID_PROP,
                        subtitle_candidate_identity(new_link) if new_link else '')
    except Exception:
        pass


def get_current_subtitle():
    """The link of the currently-applied subtitle (see above), or ''."""
    if not KODI_AVAILABLE:
        return ''
    try:
        win = home_window()
        if win is not None:
            value = win.getProperty(_CURRENT_SUB_PROP) or ''
            if value:
                return value
        return xbmc.getInfoLabel(
            'Window(10000).Property({0})'.format(_CURRENT_SUB_PROP)) or ''
    except Exception:
        return ''


def get_current_subtitle_identity():
    """Opaque logical identity of the applied picker row, or ``''``."""
    if not KODI_AVAILABLE:
        return ''
    try:
        win = home_window()
        value = (win.getProperty(_CURRENT_SUB_ID_PROP) or '') if win else ''
        if value:
            return value
        # Upgrade a selection made by an older in-memory module after an add-on
        # hot update. A normal new selection always takes the bounded property
        # path above.
        return subtitle_candidate_identity(get_current_subtitle())
    except Exception:
        return ''


def get_subtitle_selection_token():
    if not KODI_AVAILABLE:
        return ''
    try:
        win = home_window()
        return (win.getProperty(_CURRENT_SUB_TOKEN_PROP) or '') if win else ''
    except Exception:
        return ''


def current_subtitle_selection(expected_link=None):
    """Opaque snapshot a foreground/background timing job can bind to.

    When ``expected_link`` is supplied, return an empty snapshot unless that
    exact picker candidate is still current.  This closes the gap where an old
    RunScript process starts after the user has already picked another row and
    would otherwise snapshot the newer row as if it owned it.
    """
    try:
        link = get_current_subtitle()
        if expected_link is not None and link != (expected_link or ''):
            return {'token': '', 'link_hash': '', 'stream_hash': ''}
        return {
            'token': get_subtitle_selection_token(),
            'link_hash': _subtitle_link_hash(link),
            'stream_hash': _current_stream_hash(),
        }
    except Exception:
        return {'token': '', 'link_hash': '', 'stream_hash': ''}


def subtitle_selection_matches(selection_token='', link_hash='',
                               stream_hash=''):
    """True only while all supplied opaque selection identities are current."""
    try:
        if not (selection_token and link_hash and stream_hash):
            return False
        current_link = get_current_subtitle()
        if not current_link:
            return False
        if selection_token != get_subtitle_selection_token():
            return False
        if link_hash != _subtitle_link_hash(current_link):
            return False
        if stream_hash != _current_stream_hash():
            return False
        return True
    except Exception:
        return False


def set_subtitle_sync_status(state, source='', link=None, stream_url=None,
                             selection_token='', link_hash='',
                             stream_hash=''):
    """Publish timing state for the exact current subtitle and video.

    The record contains only hashes plus a short state/source. It lives in a
    Kodi Window property, so this adds no Cloudflare request, write, user-cache
    file or persistent storage. A stale background job cannot attach its result
    to another pick because both the candidate and stream must match.
    """
    if not KODI_AVAILABLE or state not in _SUBTITLE_SYNC_LABELS:
        return False
    try:
        current = get_current_subtitle()
        selected = current if link is None else (link or '')
        if not current or selected != current:
            return False
        current_token = get_subtitle_selection_token()
        current_link_hash = _subtitle_link_hash(selected)
        current_stream_hash = _current_stream_hash()
        expected_stream_hash = (stream_hash or
                                _current_stream_hash(stream_url))
        if (not current_token or not current_stream_hash
                or not current_link_hash):
            return False
        if selection_token and selection_token != current_token:
            return False
        if link_hash and link_hash != current_link_hash:
            return False
        if expected_stream_hash and expected_stream_hash != current_stream_hash:
            return False
        payload = {
            'v': 1,
            'state': state,
            'source': (source or '')[:24],
            'token': current_token,
            'link': current_link_hash,
            'stream': current_stream_hash,
            'ts': int(time.time()),
        }
        win = home_window()
        if win is None:
            return False
        raw = json.dumps(payload, separators=(',', ':'))
        prop = _CURRENT_SUB_STATUS_PROP + '.' + current_token
        win.setProperty(prop, raw)
        # Close the compare/write race: if the user changed selection while the
        # property was being written, remove only OUR stale record. Never clear
        # a newer job's status that may already have replaced it.
        if not subtitle_selection_matches(
                current_token, current_link_hash, current_stream_hash):
            if win.getProperty(prop) == raw:
                win.clearProperty(prop)
            return False
        return True
    except Exception:
        return False


def get_subtitle_sync_status(link=None, stream_url=None):
    """Return the current timing record only when pick AND stream still match."""
    if not KODI_AVAILABLE:
        return {}
    try:
        current = get_current_subtitle()
        selected = current if link is None else (link or '')
        if not current or selected != current:
            return {}
        win = home_window()
        token = get_subtitle_selection_token()
        raw = (win.getProperty(_CURRENT_SUB_STATUS_PROP + '.' + token)
               if win and token else '')
        record = json.loads(raw) if raw else {}
        state = record.get('state')
        if (record.get('v') != 1 or state not in _SUBTITLE_SYNC_LABELS
                or record.get('token') != get_subtitle_selection_token()
                or record.get('link') != _subtitle_link_hash(selected)
                or record.get('stream') != _current_stream_hash(stream_url)):
            return {}
        return dict(record, label=_SUBTITLE_SYNC_LABELS[state])
    except Exception:
        return {}


def _selection_values(selection=None):
    expected = selection if selection is not None else current_subtitle_selection()
    expected = expected or {}
    return {
        'token': expected.get('token') or '',
        'link_hash': expected.get('link_hash') or '',
        'stream_hash': expected.get('stream_hash') or '',
    }


def _subtitle_path_hash(path):
    try:
        return hashlib.sha256(
            str(path or '').encode('utf-8', 'replace')).hexdigest()[:24]
    except Exception:
        return ''


def stage_subtitle_delivery(path, selection=None, status='', source=''):
    """Arm an exact, RAM-only acknowledgement for the foreground delivery.

    A deep timing worker may be faster than Kodi's subtitle picker callback. It
    must not replace a subtitle until the foreground path has positively seen
    Kodi register and select the original file. Only hashes are stored; neither
    the local path nor the tokenised media URL is exposed or persisted.
    """
    try:
        expected = _selection_values(selection)
        path_hash = _subtitle_path_hash(path)
        if (not path_hash or not all(expected.values())
                or not subtitle_selection_matches(
                    expected['token'], expected['link_hash'],
                    expected['stream_hash'])):
            return False
        final_status = status if status in _SUBTITLE_SYNC_LABELS else ''
        # "checking" is deliberately published before enqueue so the picker can
        # show live work; it is never a final delivery verdict to commit here.
        if final_status == 'checking':
            final_status = ''
        payload = {
            'v': 1,
            'path': path_hash,
            'link': expected['link_hash'],
            'stream': expected['stream_hash'],
            'applied': 0,
            'status': final_status,
            'source': (source or '')[:24],
            'ts': int(time.time()),
        }
        win = home_window()
        if win is None:
            return False
        prop = _CURRENT_SUB_DELIVERY_PROP + '.' + expected['token']
        raw = json.dumps(payload, separators=(',', ':'))
        win.setProperty(prop, raw)
        if not subtitle_selection_matches(
                expected['token'], expected['link_hash'],
                expected['stream_hash']):
            if win.getProperty(prop) == raw:
                win.clearProperty(prop)
            return False
        return True
    except Exception:
        return False


def _subtitle_delivery_record(path, selection=None, require_current=True):
    try:
        expected = _selection_values(selection)
        path_hash = _subtitle_path_hash(path)
        if not path_hash or not all(expected.values()):
            return {}, expected, '', None
        if (require_current and not subtitle_selection_matches(
                expected['token'], expected['link_hash'],
                expected['stream_hash'])):
            return {}, expected, '', None
        win = home_window()
        prop = _CURRENT_SUB_DELIVERY_PROP + '.' + expected['token']
        raw = win.getProperty(prop) if win else ''
        record = json.loads(raw) if raw else {}
        if (record.get('v') != 1 or record.get('path') != path_hash
                or record.get('link') != expected['link_hash']
                or record.get('stream') != expected['stream_hash']):
            return {}, expected, raw, win
        return record, expected, raw, win
    except Exception:
        return {}, _selection_values(selection), '', None


def subtitle_delivery_staged(path, selection=None):
    record, _expected, _raw, _win = _subtitle_delivery_record(
        path, selection=selection)
    return bool(record)


def mark_subtitle_delivery_applied(path, selection=None):
    """Acknowledge only the exact file Kodi visibly registered and selected."""
    try:
        record, expected, raw, win = _subtitle_delivery_record(
            path, selection=selection)
        if not record or win is None:
            return False
        record['applied'] = 1
        record['ts'] = int(time.time())
        prop = _CURRENT_SUB_DELIVERY_PROP + '.' + expected['token']
        updated = json.dumps(record, separators=(',', ':'))
        # Do not overwrite a newer stage that replaced the one we observed.
        if win.getProperty(prop) != raw:
            return False
        win.setProperty(prop, updated)
        if not subtitle_selection_matches(
                expected['token'], expected['link_hash'],
                expected['stream_hash']):
            if win.getProperty(prop) == updated:
                win.clearProperty(prop)
            return False
        if win.getProperty(prop) != updated:
            return False
        if record.get('status'):
            if not set_subtitle_sync_status(
                    record['status'], source=record.get('source') or 'local',
                    selection_token=expected['token'],
                    link_hash=expected['link_hash'],
                    stream_hash=expected['stream_hash']):
                return False
        return bool(subtitle_selection_matches(
            expected['token'], expected['link_hash'],
            expected['stream_hash']))
    except Exception:
        return False


def subtitle_delivery_is_applied(path, selection=None):
    record, _expected, _raw, _win = _subtitle_delivery_record(
        path, selection=selection)
    return bool(record and record.get('applied') == 1)


def clear_subtitle_delivery(path=None, selection=None):
    """Remove only the matching token/path acknowledgement, never a newer one."""
    try:
        expected = _selection_values(selection)
        if not expected.get('token'):
            return False
        win = home_window()
        if win is None:
            return False
        prop = _CURRENT_SUB_DELIVERY_PROP + '.' + expected['token']
        raw = win.getProperty(prop) or ''
        record = json.loads(raw) if raw else {}
        if not record:
            return False
        if path is not None and record.get('path') != _subtitle_path_hash(path):
            return False
        if (expected.get('link_hash')
                and record.get('link') != expected['link_hash']):
            return False
        if (expected.get('stream_hash')
                and record.get('stream') != expected['stream_hash']):
            return False
        if win.getProperty(prop) != raw:
            return False
        win.clearProperty(prop)
        return True
    except Exception:
        return False


def abandon_subtitle_selection(selection=None):
    """Clear a failed pick only if it is still the exact current selection."""
    try:
        expected = _selection_values(selection)
        if (not all(expected.values())
                or not subtitle_selection_matches(
                    expected['token'], expected['link_hash'],
                    expected['stream_hash'])):
            return False
        # set_current_subtitle performs token-scoped cleanup for status, staged
        # fixes, delivery acks and delay-learning records.
        set_current_subtitle('')
        return not get_current_subtitle()
    except Exception:
        return False


def stage_subtitle_sync_fix(path, selection=None, source='local', notice=''):
    """Remember a corrected copy until its caller proves Kodi received it."""
    try:
        expected = _selection_values(selection)
        if (not path or not all(expected.values())
                or not subtitle_selection_matches(
                    expected['token'], expected['link_hash'],
                    expected['stream_hash'])):
            return False
        payload = {
            'v': 1,
            'path': hashlib.sha256(
                str(path).encode('utf-8', 'replace')).hexdigest()[:24],
            'source': (source or '')[:24],
            'notice': (notice or '')[:96],
            'ts': int(time.time()),
        }
        win = home_window()
        if win is None:
            return False
        prop = _CURRENT_SUB_FIX_READY_PROP + '.' + expected['token']
        raw = json.dumps(payload, separators=(',', ':'))
        win.setProperty(prop, raw)
        if not subtitle_selection_matches(
                expected['token'], expected['link_hash'],
                expected['stream_hash']):
            if win.getProperty(prop) == raw:
                win.clearProperty(prop)
            return False
        return True
    except Exception:
        return False


def subtitle_sync_fix_staged(path, selection=None):
    """Whether ``path`` is the correction staged for this exact selection."""
    try:
        expected = _selection_values(selection)
        if (not path or not all(expected.values())
                or not subtitle_selection_matches(
                    expected['token'], expected['link_hash'],
                    expected['stream_hash'])):
            return False
        win = home_window()
        raw = (win.getProperty(
            _CURRENT_SUB_FIX_READY_PROP + '.' + expected['token'])
               if win else '')
        record = json.loads(raw) if raw else {}
        path_hash = hashlib.sha256(
            str(path).encode('utf-8', 'replace')).hexdigest()[:24]
        return bool(record.get('v') == 1 and record.get('path') == path_hash)
    except Exception:
        return False


def subtitle_sync_registration_baseline(path, selection=None, player=None):
    """Capture the stream count when a correction or delivery ack is staged.

    ``None`` means no proof is needed. ``-1`` means Kodi exposes no observation
    channel, so callers still deliver fail-open but must not publish FIXED.
    """
    if not (subtitle_sync_fix_staged(path, selection=selection)
            or subtitle_delivery_staged(path, selection=selection)):
        return None
    try:
        p = player or xbmc.Player()
        return len(p.getAvailableSubtitleStreams() or [])
    except Exception:
        return -1


def confirm_subtitle_sync_registration(path, selection=None, before=None,
                                       player=None, timeout_ms=1000,
                                       select_new=True,
                                       accept_valid_replacement=False,
                                       replacement_path=None):
    """Confirm a subtitle handoff without confusing replacement with failure.

    Kodi may append an external subtitle, or replace the one external slot it
    already owns.  Stream-count growth proves the append form and still gets an
    explicit pin.  For the replacement form, the Python API contract says a
    successful ``setSubtitles`` adds *and activates* the supplied file; callers
    may opt into accepting that handoff only when the exact staged file exists
    and the same selection/stream token is still current.
    """
    if before is None or before < 0:
        return False
    expected = _selection_values(selection)
    if not all(expected.values()):
        return False
    try:
        p = player or xbmc.Player()
        steps = max(1, min(40, int(max(0, timeout_ms) / 50)))
        for _ in range(steps):
            xbmc.sleep(50)
            if not subtitle_selection_matches(
                    expected['token'], expected['link_hash'],
                    expected['stream_hash']):
                return False
            try:
                streams = p.getAvailableSubtitleStreams() or []
            except Exception:
                return False
            if len(streams) > before:
                if select_new:
                    try:
                        p.setSubtitleStream(len(streams) - 1)
                    except Exception:
                        # Growth proves registration, but Kodi may keep an older
                        # Hebrew stream active. Without a successful pin we
                        # cannot truthfully claim the repaired copy is applied.
                        return False
                if not subtitle_selection_matches(
                        expected['token'], expected['link_hash'],
                        expected['stream_hash']):
                    return False
                acknowledged = mark_subtitle_delivery_applied(
                    path, selection=expected)
                confirmed = confirm_subtitle_sync_fix(
                    path, selection=expected)
                return bool(acknowledged or confirmed)
        if accept_valid_replacement:
            # Kodi 21/Android commonly closes the previous external subtitle
            # and opens the new one in the same slot.  getAvailableSubtitleStreams
            # then keeps the same length (and often the same generic "he"
            # label), so growth can never occur although playback changed.
            # Trust the completed API handoff only for a real local/VFS file
            # and only while its exact private selection identity remains live.
            exists = False
            delivered = replacement_path or path
            try:
                exists = os.path.isfile(delivered)
            except Exception:
                exists = False
            if not exists and xbmcvfs is not None:
                try:
                    exists = bool(xbmcvfs.exists(delivered))
                except Exception:
                    exists = False
            if (exists and subtitle_selection_matches(
                    expected['token'], expected['link_hash'],
                    expected['stream_hash'])):
                acknowledged = mark_subtitle_delivery_applied(
                    path, selection=expected)
                confirmed = confirm_subtitle_sync_fix(
                    path, selection=expected)
                return bool(acknowledged or confirmed)
        return False
    except Exception:
        return False


def apply_subtitle_file(path, selection=None, fix_path=None, timeout_ms=1000,
                        abandon_on_failure=True):
    """Hand one file to Kodi without letting stale work affect a newer pick.

    The file is applied fail-open. When it represents a staged timing repair,
    FIXED is published only after the new stream appears in Kodi's stream list.
    ``fix_path`` names the staged source when ``path`` is a display-name copy.
    """
    expected = _selection_values(selection)
    if not path or not all(expected.values()):
        return False
    try:
        if not subtitle_selection_matches(
                expected['token'], expected['link_hash'],
                expected['stream_hash']):
            return False
        p = xbmc.Player()
        try:
            if not p.isPlayingVideo():
                return False
        except Exception:
            return False
        staged_path = fix_path or path
        before = subtitle_sync_registration_baseline(
            staged_path, selection=expected, player=p)
        if not subtitle_selection_matches(
                expected['token'], expected['link_hash'],
                expected['stream_hash']):
            return False
        # Freeze the real embedded-stream baseline before Kodi appends this
        # external file. This is intentionally lazy to avoid a module cycle.
        try:
            from resources.lib.fetching.embedded import streams as embedded_streams
            embedded_streams.seal_playback_streams(current_video_info())
        except Exception:
            pass
        p.setSubtitles(path)
        p.showSubtitles(True)
        if not subtitle_selection_matches(
                expected['token'], expected['link_hash'],
                expected['stream_hash']):
            return False
        if before is not None:
            confirmed = confirm_subtitle_sync_registration(
                staged_path, selection=expected, before=before,
                player=p, timeout_ms=timeout_ms,
                accept_valid_replacement=True, replacement_path=path)
            if not confirmed:
                if abandon_on_failure:
                    abandon_subtitle_selection(expected)
                return False
        return bool(subtitle_selection_matches(
            expected['token'], expected['link_hash'],
            expected['stream_hash']))
    except Exception:
        if abandon_on_failure:
            abandon_subtitle_selection(expected)
        return False


def confirm_subtitle_sync_fix(path, selection=None):
    """Publish FIXED after a caller has positively proven Kodi registration."""
    try:
        expected = _selection_values(selection)
        if (not path or not all(expected.values())
                or not subtitle_selection_matches(
                    expected['token'], expected['link_hash'],
                    expected['stream_hash'])):
            return False
        win = home_window()
        prop = _CURRENT_SUB_FIX_READY_PROP + '.' + expected['token']
        raw = win.getProperty(prop) if win else ''
        record = json.loads(raw) if raw else {}
        path_hash = hashlib.sha256(
            str(path).encode('utf-8', 'replace')).hexdigest()[:24]
        if record.get('v') != 1 or record.get('path') != path_hash:
            return False
        published = set_subtitle_sync_status(
            'fixed', source=record.get('source') or 'local',
            selection_token=expected['token'],
            link_hash=expected['link_hash'],
            stream_hash=expected['stream_hash'])
        if published:
            if win.getProperty(prop) == raw:
                win.clearProperty(prop)
            # The status write itself is token-scoped, but a toast is global.
            # Re-check after the write so A cannot announce over B if the user
            # changed subtitles in that tiny interval.
            still_current = subtitle_selection_matches(
                expected['token'], expected['link_hash'],
                expected['stream_hash'])
            if still_current and record.get('notice'):
                notify(record['notice'], time_ms=5000)
            return bool(still_current)
        return False
    except Exception:
        return False
