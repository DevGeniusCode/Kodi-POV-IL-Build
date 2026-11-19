# -*- coding: utf-8 -*-
"""
pov_curtain.py - holds a black curtain over the Home window while POV changes windows.

Location : addons/plugin.program.kodipovilwizard/resources/libs/patches/pov_curtain.py
Called by: the hook patched into plugin.video.pov (resources/lib/modules/sources.py, registry entry pov_curtain_start)

    pov_curtain.raise_curtain()

Why: POV's full-screen flow is a chain of separate modal windows (progress_media -> sources_results -> progress_media ->
video). Each one is closed before the next one is up, and the window underneath (Home) shows for 1-3 seconds in between.
skin.fentastic draws a black image on top of Home while the window property below is set (Home.xml, "povil.pov_curtain").

Lifetime: a daemon thread owns the property. It clears it
  * once video has been playing for a moment (the fullscreen window replaces Home), or
  * when neither a dialog nor video has been up for GAP_LIMIT seconds (the flow ended: BACK, no results, a failed source), or
  * after HARD_LIMIT seconds, whatever happens.
So the curtain can never stay on a Home screen the user is looking at.

Design rules: never raises (POV's source flow must not depend on it); no I/O; the thread is a daemon.
"""
import threading
import time

import xbmc
import xbmcgui

PROPERTY = 'povil.pov_curtain'
POLL_SECONDS = 0.25
PLAYING_SETTLE = 1.0        # video must run this long before the curtain is dropped
GAP_LIMIT = 4.0             # seconds with no dialog and no video: the flow is over, drop the curtain
HARD_LIMIT = 150.0          # absolute cap

_lock = threading.Lock()
_thread = [None]


def _dialog_up():
    return bool(xbmc.getCondVisibility('System.HasVisibleModalDialog')
                or xbmc.getCondVisibility('System.HasActiveModalDialog'))


def _watch():
    home = xbmcgui.Window(10000)
    monitor = xbmc.Monitor()
    start = time.monotonic()
    playing_since = None
    gap_since = None
    try:
        while time.monotonic() - start < HARD_LIMIT:
            if monitor.waitForAbort(POLL_SECONDS):
                break
            now = time.monotonic()
            if xbmc.getCondVisibility('Player.HasVideo'):
                gap_since = None
                playing_since = playing_since or now
                if now - playing_since >= PLAYING_SETTLE:
                    break
                continue
            playing_since = None
            if _dialog_up():
                gap_since = None
                continue
            gap_since = gap_since or now
            if now - gap_since >= GAP_LIMIT:
                break
    finally:
        try:
            home.clearProperty(PROPERTY)
        except Exception:
            pass


def raise_curtain():
    """Show the curtain and start (or keep) the thread that takes it down again. Never raises."""
    try:
        with _lock:
            xbmcgui.Window(10000).setProperty(PROPERTY, 'true')
            thread = _thread[0]
            if thread is not None and thread.is_alive():
                return
            thread = threading.Thread(target=_watch, name='povil_pov_curtain')
            thread.daemon = True
            thread.start()
            _thread[0] = thread
    except Exception:
        pass
