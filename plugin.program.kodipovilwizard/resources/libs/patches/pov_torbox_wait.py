# -*- coding: utf-8 -*-
# File: plugin.program.kodipovilwizard/resources/libs/patches/pov_torbox_wait.py
"""
Logic module of the registry entry `pov_torbox_wait_for_files` (POV indexers/torbox_api.py).

POV's TorBoxAPI.parse_magnet_pack() creates a transfer (createtorrent) and looks it up at once
(mylist?id=<id>). Right after the create, TorBox can answer that lookup with a 404 ("Not Found") or with
a body that has no files yet; POV treats the first answer as final, deletes the transfer, and
play_file() moves on to the next source until limit_resolve (10) is spent: a first click that ends in
"No results" while a retry plays. This module is called right after that first lookup and, when the
answer carries no files, asks again a few times.

Rules (owner-approved, Phase 6 playback fix):
  * at most MAX_POLLS lookups per source, INTERVAL_S apart;
  * one shared BUDGET_S per playback attempt (consecutive resolves of one click are one attempt: the
    budget restarts after ATTEMPT_GAP_S of silence), so a dead list still fails fast;
  * the lookup is the same request as POV's torrent_info() (same URL, id parameter, headers and timeout)
    so that the HTTP status can be logged; nothing else about the transfer is changed;
  * a usable answer is returned in POV's own shape (the unwrapped "data" dict), anything else returns the
    ORIGINAL answer untouched, so POV's own failure path (delete, selected_files failed) is unchanged.

Every poll logs one line: `[TorBox wait] id=<id> try=N http=<status> state=<download_state> files=N`.
The hook that calls wait_for_files() is built by _build_patch_hook (it logs and carries on if this module
raises), so a failure here can never be worse than POV without the patch.
"""
import sys
import time

import xbmc

TAG = '[TorBox wait]'
MAX_POLLS = 4          # lookups after POV's own, per source
INTERVAL_S = 1.0       # wait before each poll
BUDGET_S = 8.0         # shared by every source of one playback attempt
ATTEMPT_GAP_S = 10.0   # silence that ends a playback attempt (the next click starts a fresh budget)

_PROP = 'povil.torbox_wait'   # "start,last" (epoch seconds) on the Home window, shared by POV's processes
_mem = {}


def _log(message):
    xbmc.log('%s %s' % (TAG, message), xbmc.LOGINFO)


def _now():
    return time.time()


def _sleep(seconds):
    """Wait; True when Kodi is shutting down (the caller stops polling)."""
    return bool(xbmc.Monitor().waitForAbort(seconds))


def _load_state():
    try:
        import xbmcgui
        raw = xbmcgui.Window(10000).getProperty(_PROP)
        if raw:
            start, last = (float(x) for x in raw.split(','))
            return start, last
    except Exception:
        pass
    return _mem.get('state', (0.0, 0.0))


def _save_state(start, last):
    _mem['state'] = (start, last)
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(_PROP, '%.3f,%.3f' % (start, last))
    except Exception:
        pass


def _attempt_start(now):
    """Start time of the playback attempt that `now` belongs to (registers the activity)."""
    start, last = _load_state()
    if not start or now - last > ATTEMPT_GAP_S:
        start = now
    _save_state(start, now)
    return start


def _usable(answer):
    return isinstance(answer, dict) and isinstance(answer.get('files'), list) and len(answer['files']) > 0


def _fetch(api, torrent_id, path):
    """One lookup, the same request as TorBoxAPI.torrent_info(). Returns (http status, unwrapped body)."""
    from modules.session import session
    module = sys.modules.get(type(api).__module__)
    base_url = getattr(module, 'base_url', 'https://api.torbox.app')
    endpoint = '/v1/api/usenet/mylist' if 'usenet' in path else '/v1/api/torrents/mylist'
    response = session.request('get', base_url + endpoint, params={'id': torrent_id},
                               headers=api.headers(), timeout=api.timeout)
    body = None
    try:
        if response.content and 'json' in response.headers.get('Content-Type', ''):
            body = response.json()
    except Exception:
        body = None
    # POV's TorBoxAPI._parse(): the envelope is unwrapped to its "data".
    if isinstance(body, dict) and 'data' in body and 'success' in body:
        body = body['data']
    return response.status_code, body


def wait_for_files(api, torrent_id, path, current):
    """Return `current` when it already has files (or nothing can be polled); otherwise poll for the file list
    and return the first answer that has one, or `current` when the polls or the budget run out."""
    if _usable(current) or not torrent_id:
        return current
    started = _attempt_start(_now())
    for attempt in range(1, MAX_POLLS + 1):
        if _now() - started + INTERVAL_S > BUDGET_S:
            _log('id=%s budget of %.0fs spent after %d poll(s), giving up' % (torrent_id, BUDGET_S, attempt - 1))
            return current
        if _sleep(INTERVAL_S):
            return current   # Kodi is closing
        try:
            status, body = _fetch(api, torrent_id, path)
        except Exception as error:
            status, body = 'err', None
            _log('id=%s try=%d lookup failed: %s' % (torrent_id, attempt, error))
        _save_state(started, _now())
        files = body.get('files') if isinstance(body, dict) else None
        state = (body.get('download_state') or body.get('status') or '-') if isinstance(body, dict) else '-'
        _log('id=%s try=%d http=%s state=%s files=%d'
             % (torrent_id, attempt, status, state, len(files) if isinstance(files, list) else 0))
        if _usable(body):
            return body
    _log('id=%s no file list after %d poll(s), giving up' % (torrent_id, MAX_POLLS))
    return current
