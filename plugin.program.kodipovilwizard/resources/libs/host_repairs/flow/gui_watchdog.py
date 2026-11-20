"""Diagnostics: say in the log WHEN Kodi's GUI stops answering at start-up (and when it comes back).

Why it exists: on 20 Nov 2026 the GUI thread went silent while Kodi updated three of our add-ons at start-up. The services were
force-killed after 5 s and the log then ended without a hint of what the GUI was waiting for. Kodi writes nothing for a GUI thread
that is stuck, so the log could not say when it started. This watchdog asks the GUI a trivial question every POLL_SECONDS (with a
timeout, via skin_gui_guard.gui_state) and logs every change of its state with the time since the runner started, plus the
Wizard's own GUI calls that have not come back (common/gui_probe.in_flight). It reads and logs only: it repairs nothing.

It runs for the first WATCH_SECONDS after Kodi starts the runner, and keeps going while the GUI is hung (up to HARD_LIMIT_SECONDS).
"""
import time

from resources.libs.common import gui_probe
from resources.libs.host_repairs.flow import skin_gui_guard

POLL_SECONDS = 2.0
WATCH_SECONDS = 120.0
HARD_LIMIT_SECONDS = 600.0
REMIND_SECONDS = 10.0       # while hung: one reminder this often


def _log(msg, level='INFO'):
    try:
        from resources.libs.host_repairs import kodi_utils
        kodi_utils.log('gui_watchdog: ' + msg, level=level)
    except Exception:
        pass


def run(monitor):
    """Blocking loop (the runner starts it on a daemon thread). Never raises."""
    try:
        started = time.monotonic()
        state = None
        reminded = started
        while not monitor.abortRequested():
            elapsed = time.monotonic() - started
            if elapsed > HARD_LIMIT_SECONDS or (elapsed > WATCH_SECONDS and state != 'hung'):
                return
            now_state = skin_gui_guard.gui_state()
            if now_state != state:
                if now_state == 'hung':
                    _log('GUI HUNG {0:.0f}s after the runner started (was {1}); unanswered Wizard GUI calls: {2}'.format(
                        elapsed, state, gui_probe.in_flight() or 'none in this interpreter'), level='WARNING')
                else:
                    _log('GUI {0} at {1:.0f}s (was {2})'.format(now_state, elapsed, state))
                state, reminded = now_state, time.monotonic()
            elif state == 'hung' and time.monotonic() - reminded >= REMIND_SECONDS:
                _log('GUI still hung after {0:.0f}s; unanswered Wizard GUI calls: {1}'.format(
                    elapsed, gui_probe.in_flight() or 'none in this interpreter'), level='WARNING')
                reminded = time.monotonic()
            if monitor.waitForAbort(POLL_SECONDS):
                return
    except Exception as err:
        _log('stopped: {0}'.format(err), level='WARNING')
