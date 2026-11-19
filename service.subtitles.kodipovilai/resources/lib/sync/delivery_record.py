# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The delivery record: what was handed to the player, for the sync gate and the delay watch.

import json
import time
try:
    from resources.lib.core import kodi, selection as core_selection, settings, video_context
except Exception:
    kodi = core_selection = settings = video_context = None
from resources.lib.sync.common import _INFO_KEYS


_DELIVERED_PROP = 'subsync.delivered'


def _delivery_prop(selection=None):
    expected = selection or {}
    token = expected.get('token') or expected.get('selection_token') or ''
    if not token:
        try:
            token = core_selection.get_subtitle_selection_token() or ''
        except Exception:
            token = ''
    return _DELIVERED_PROP + ('.' + token if token else '')


def _clear_delivery(selection=None):
    """Discard scalar delay-learning state after a non-global delivery."""
    from resources.lib.sync.jobs import _selection_matches
    try:
        if selection and not _selection_matches(selection):
            return False
        import xbmcgui
        win = xbmcgui.Window(10000)
        win.clearProperty(_delivery_prop(selection))
        # A pre-token record cannot belong to a newer token-scoped selection.
        win.clearProperty(_DELIVERED_PROP)
        return True
    except Exception:
        return False


def _record_delivery(info, playing, key, scale, offset_ms,
                     cut_signature='', mode='global', selection=None):
    """Remember what we just delivered (and any applied fix), so the service's
    delay watcher can turn the viewer's manual subtitle-delay into a HUMAN
    community sync report -- the anchor of last resort."""
    try:
        import xbmcgui
        expected = selection or {}
        if expected and not core_selection.subtitle_selection_matches(
                expected.get('token') or '',
                expected.get('link_hash') or '',
                expected.get('stream_hash') or ''):
            return False
        payload = {
            'key': key, 'playing': playing, 'ts': time.time(),
            'scale': float(scale or 1.0),
            'offset': float(offset_ms or 0.0),
            'mode': (mode or '').strip().lower(),
            'cut_signature': (cut_signature or '').strip().lower(),
            'selection_token': expected.get('token') or '',
            'selection_hash': expected.get('link_hash') or '',
            'stream_hash': expected.get('stream_hash') or '',
            'info': {k: info.get(k) for k in _INFO_KEYS
                     if isinstance(info.get(k), (str, int, float, bool))},
        }
        win = xbmcgui.Window(10000)
        raw = json.dumps(payload, ensure_ascii=False)
        prop = _delivery_prop(expected)
        win.setProperty(prop, raw)
        if expected and not core_selection.subtitle_selection_matches(
                expected.get('token') or '',
                expected.get('link_hash') or '',
                expected.get('stream_hash') or ''):
            if win.getProperty(prop) == raw:
                win.clearProperty(prop)
            return False
        return True
    except Exception:
        return False


def current_delivery_record():
    """Return delay-learning state only for the exact live selection/cut."""
    from resources.lib.sync.jobs import _selection_matches
    from resources.lib.sync.service import _selection_snapshot
    try:
        import xbmcgui
        selection = _selection_snapshot()
        if not _selection_matches(selection):
            return None
        win = xbmcgui.Window(10000)
        raw = win.getProperty(_delivery_prop(selection)) or ''
        if not raw:
            # One-session migration from the previous global property.
            raw = win.getProperty(_DELIVERED_PROP) or ''
        record = json.loads(raw) if raw else None
        if not isinstance(record, dict):
            return None
        token = record.get('selection_token') or ''
        link_hash = record.get('selection_hash') or ''
        stream_hash = record.get('stream_hash') or ''
        if token or link_hash or stream_hash:
            if (token != selection.get('token')
                    or link_hash != selection.get('link_hash')
                    or stream_hash != selection.get('stream_hash')):
                return None
        return record
    except Exception:
        return None


def clear_delivery_record(record):
    """Remove only the token-scoped record the delay watcher just consumed."""
    try:
        import xbmcgui
        win = xbmcgui.Window(10000)
        prop = _delivery_prop(record or {})
        raw = win.getProperty(prop) or ''
        current = json.loads(raw) if raw else {}
        if (current.get('key') == (record or {}).get('key')
                and float(current.get('ts') or 0)
                == float((record or {}).get('ts') or 0)):
            win.clearProperty(prop)
            return True
        # Clear only the legacy singleton when it is the exact same record.
        legacy = win.getProperty(_DELIVERED_PROP) or ''
        old = json.loads(legacy) if legacy else {}
        if (old.get('key') == (record or {}).get('key')
                and float(old.get('ts') or 0)
                == float((record or {}).get('ts') or 0)):
            win.clearProperty(_DELIVERED_PROP)
            return True
    except Exception:
        pass
    return False
