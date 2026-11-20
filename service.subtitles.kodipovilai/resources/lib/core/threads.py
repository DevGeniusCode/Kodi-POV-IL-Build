# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Say, when the service ends, which of its threads did not, and where each one is waiting.
#
# Kodi 21 (xbmc/interfaces/python/PythonInvoker.cpp, CPythonInvoker::execute): after a script's main returns, Kodi waits, in a loop
# without a timeout of its own, for EVERY thread of the script's Python interpreter to end, daemon or not ("waiting on thread <n>" is
# its only log line). The one who asked the service to stop (Kodi's add-on installer, an exit) is held meanwhile, and with it a lock
# the GUI thread takes on every frame; after 5 s Kodi kills the script ("script didn't stop in 5 seconds"). So every thread of this
# service must END within a moment of Kodi's abort request. This names the ones that do not, with the frames they are stuck in.

import sys
import threading
import time
import traceback

from resources.lib.core import kodi

FRAMES = 4      # innermost frames shown per thread


def survivors():
    """The threads other than the current one and the main thread that are still alive."""
    current = threading.current_thread()
    main = threading.main_thread()
    return [t for t in threading.enumerate() if t is not current and t is not main and t.is_alive()]


def _where(thread):
    frame = sys._current_frames().get(thread.ident)
    if frame is None:
        return '?'
    steps = traceback.extract_stack(frame)[-FRAMES:]
    return ' < '.join('{0}:{1} {2}'.format(s.filename.replace('\\', '/').rsplit('/', 1)[-1], s.lineno, s.name)
                      for s in reversed(steps))


def log_survivors(wait=1.0):
    """Give the other threads up to `wait` seconds to end, then log one WARNING per thread still alive (name, daemon or not, the
    frames it is in). Silent when all ended. Returns how many were left. Never raises."""
    try:
        deadline = time.monotonic() + wait
        left = survivors()
        while left and time.monotonic() < deadline:
            time.sleep(0.05)
            left = survivors()
        for thread in left:
            kodi.log('service is stopping but thread "{0}"{1} is still alive, in: {2}'.format(
                thread.name, ' (daemon)' if thread.daemon else '', _where(thread)), level='WARNING')
        return len(left)
    except Exception:
        return 0
