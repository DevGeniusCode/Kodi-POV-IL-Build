"""Ask Kodi's GUI a question without being taken down with it.

A call such as xbmc.getCondVisibility / getInfoLabel / xbmcgui.getCurrentWindowId needs the GUI thread's lock. While Kodi swaps an
add-on or a skin (20 Nov 2026 log: the GUI went silent at the moment Kodi started its updates) that lock may not come back, and the
calling script thread hangs with it: a service that cannot return is force-killed by Kodi after 5 s.

ask() runs the call in a daemon thread and waits at most `timeout`. With a `key` the probe is single-flight: while the thread of
the previous probe of that key is still stuck, no new thread is started (a poll loop would otherwise leave one stuck thread per
tick) and the answer is "no answer" at once. Stdlib only.
"""
import threading

DEFAULT_TIMEOUT = 2.5      # a GUI call that takes longer than this is "hung"

_flights = {}
_lock = threading.Lock()


def ask(call, timeout=DEFAULT_TIMEOUT, key=None):
    """(True, value) when call() answered within `timeout`; (False, None) when it did not (the thread is left to its fate: it is a
    daemon). A call that raises answers (True, None)."""
    if key is not None:
        with _lock:
            previous = _flights.get(key)
            if previous is not None and previous.is_alive():
                return False, None
    box = []

    def run():
        try:
            box.append(call())
        except Exception:
            box.append(None)

    thread = threading.Thread(target=run, name='povil_gui_probe' + ('_' + key if key else ''))
    thread.daemon = True
    if key is not None:
        with _lock:
            _flights[key] = thread
    thread.start()
    thread.join(timeout)
    if box:
        return True, box[0]
    return False, None


def in_flight():
    """Keys whose last probe has not answered yet (what a stalled GUI is holding)."""
    with _lock:
        return sorted(key for key, thread in _flights.items() if thread.is_alive())
