"""Name the threads that are still alive when a Wizard script of the build ends, and say where they are stuck.

Kodi 21 (xbmc/interfaces/python/PythonInvoker.cpp, CPythonInvoker::execute): after a script's main returns, Kodi waits, in a loop
without a timeout of its own, for EVERY thread of the script's Python interpreter to end, daemon or not ("waiting on thread <number>"
is its only log line). Meanwhile the one who asked the service to stop (Kodi's add-on installer, an exit) is held, and with it a lock
the GUI thread needs on every frame. After 5 s Kodi kills the script ("script didn't stop in 5 seconds"). Every long-lived thread of
the Wizard therefore has to END within a moment of Kodi's abort request, and this is the check that says which one did not, by name and
with the frames it is waiting in, in the log, so the next freeze report names the call.
"""
import sys
import threading
import time
import traceback

import xbmc

from resources.libs.common import logging

FRAMES = 4      # innermost frames shown per thread


def survivors():
    """The other threads that are alive: [(thread, daemon)], without the current thread and the main thread."""
    current = threading.current_thread()
    main = threading.main_thread()
    return [t for t in threading.enumerate() if t is not current and t is not main and t.is_alive()]


def _where(thread):
    frame = sys._current_frames().get(thread.ident)
    if frame is None:
        return '?'
    steps = traceback.extract_stack(frame)[-FRAMES:]
    return ' < '.join('{0}:{1} {2}'.format(s.filename.replace('\\', '/').rsplit('/', 1)[-1], s.lineno, s.name) for s in reversed(steps))


def log_stragglers(owner, wait=1.0):
    """At the end of a script: give the other threads up to `wait` seconds to end, then log one WARNING per thread that is still alive
    (name, daemon or not, where it is). Silent when every thread ended. Never raises."""
    try:
        deadline = time.monotonic() + wait
        left = survivors()
        while left and time.monotonic() < deadline:
            time.sleep(0.05)
            left = survivors()
        for thread in left:
            logging.log('[Threads] {0} is ending but thread "{1}"{2} is still alive, in: {3}'.format(
                owner, thread.name, ' (daemon)' if thread.daemon else '', _where(thread)), level=xbmc.LOGWARNING)
    except Exception:
        pass
