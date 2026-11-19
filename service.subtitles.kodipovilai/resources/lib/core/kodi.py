# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Kodi plumbing: paths, localised strings, the log and the toast (the add-on handle is core/addon.py).
# One brand title for every dialog (DIALOG_TITLE).

import os
import threading
import time
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
from resources.lib.core import addon as core_addon, settings


# The brand title shown in every dialog, progress bar, toast and HTML page title of this
# add-on. Change it here and nowhere else.
DIALOG_TITLE = 'Kodi POV IL'

# notify() fires each GUI toast on a short-lived daemon thread so a wedged GUI
# can never stall the caller (see notify()). This bounds how many such threads
# may be parked at once: under a *sustained* GUI wedge (and with
# reuselanguageinvoker keeping this module warm across invocations) toast
# threads that block inside Kodi's notification() don't get reaped until the
# wedge clears, so without a cap they could accumulate over a session. Past the
# cap we simply drop the toast -- notifications are decorative and nothing
# depends on one landing.
_NOTIFY_MAX_PENDING = 8
_notify_lock = threading.Lock()
_notify_pending = [0]


# Kodi runs every add-on's Python under one interpreter lock. Measured in QA
# Kodi (21, Python 3.11): while one thread runs a CPU-bound loop that never
# sleeps, a NEW script -- e.g. our own "בחירת כתוביות" button -- does not even
# start until that loop ends (15.8 s behind a 20 s loop, 0.3 s idle); a 1 ms
# sleep every ~20 ms brings it back to 0.3 s. Field logs from an Android box
# showed the same 5-11 s waits behind SubSync's deep check at playback start.
# A long loop calls its breathe() (make_breather below): about 5% slower in the background,
# never in the way of the viewer.
BREATHE_SLICE = 0.02


def make_breather(after_nap=None):
    """A breathe() for one module's long loops: at most every BREATHE_SLICE seconds a 1 ms nap (see above).
    Each module keeps ITS OWN timer, as before (the copies this replaced were one per module); `after_nap`, if given, runs
    after every nap (the deep SubSync job's yield to the viewer)."""
    last = [0.0]

    def breathe():
        now = time.monotonic()
        if now - last[0] >= BREATHE_SLICE:
            time.sleep(0.001)
            last[0] = time.monotonic()
            if after_nap is not None:
                after_nap()
    return breathe


def localised(strid, *args):
    try:
        s = core_addon.addon().getLocalizedString(strid)
    except Exception:
        s = ''
    if not s:
        return ''
    if args:
        try:
            return s.format(*args)
        except (IndexError, KeyError):
            return s
    return s


def addon_profile_path():
    """Path to the addon's per-user data dir
    (.kodi/userdata/addon_data/<id>/). Created if missing."""
    if not KODI_AVAILABLE:
        return os.path.join(os.path.expanduser('~'), '.kodi-test', core_addon.ADDON_ID)
    p = xbmcvfs.translatePath('special://profile/addon_data/' + core_addon.ADDON_ID + '/')
    if not os.path.isdir(p):
        try:
            os.makedirs(p)
        except OSError:
            pass
    return p


def cache_dir():
    p = os.path.join(addon_profile_path(), 'cache')
    if not os.path.isdir(p):
        try:
            os.makedirs(p)
        except OSError:
            pass
    return p


def safe_release_filename(release, fallback=''):
    """Turn a subtitle release name into a safe filename STEM (no extension) for
    a delivered .srt, so Kodi shows the real release instead of a hash. Strips a
    trailing language/extension, replaces filesystem-unsafe characters, collapses
    whitespace, and caps the length. Returns `fallback` when nothing usable
    remains."""
    import re
    s = (release or '').strip()
    # drop a trailing extension and a trailing .he/.heb language tag
    s = re.sub(r'\.(srt|ssa|ass|sub|smi|vtt)$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\.(he|heb|hebrew)$', '', s, flags=re.IGNORECASE)
    # filesystem-unsafe characters -> nothing/space; keep dots/dashes/brackets
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', s)
    s = re.sub(r'\s{2,}', ' ', s).strip().strip('.')
    if len(s) > 120:
        s = s[:120].rstrip(' .-_')
    return s or fallback


def log(msg, level='INFO'):
    """Log to Kodi's log at the appropriate level. Honours the
    addon's log_level setting -- anything below that is suppressed."""
    cfg_level = settings.get_setting('log_level', 'INFO').upper()
    order = {'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3}
    if order.get(level.upper(), 1) < order.get(cfg_level, 1):
        return
    if not KODI_AVAILABLE:
        print('[{0}] {1}'.format(level, msg))
        return
    kodi_level = {
        'DEBUG': xbmc.LOGDEBUG,
        'INFO': xbmc.LOGINFO,
        'WARNING': xbmc.LOGWARNING,
        'ERROR': xbmc.LOGERROR,
    }.get(level.upper(), xbmc.LOGINFO)
    try:
        xbmc.log('[{0}] {1}'.format(core_addon.ADDON_ID, msg), level=kodi_level)
    except Exception:
        pass


def notify(msg, title=None, icon=None, time_ms=4000):
    if not KODI_AVAILABLE:
        print('NOTIFY:', title, '-', msg)
        return
    # User master switch (like DarkSubs): hide all subtitle/translation toasts
    # when turned off. Default ON, so nothing changes unless the user opts out.
    try:
        if (settings.get_setting('subs_notifications', 'true')
                or 'true').strip().lower() == 'false':
            return
    except Exception:
        pass
    try:
        if title is None:
            title = DIALOG_TITLE
        if icon is None:
            icon = xbmcvfs.translatePath('special://home/addons/' + core_addon.ADDON_ID + '/icon.png')
        # Force RTL paragraph direction. The previous version used
        # U+200F (RLM) as a prefix -- that's just a strong-RTL
        # invisible character that BIASES the BiDi algorithm but
        # doesn't OVERRIDE it. When a message like "AI: 25% תורגם
        # (5/20 chunks)" has more LTR weight than RTL, RLM loses
        # and Kodi renders the toast left-to-right -- which for a
        # Hebrew reader looks "reversed", reading from the end to
        # the beginning.
        #
        # U+202B (RIGHT-TO-LEFT EMBEDDING) + U+202C (POP DIRECTIONAL
        # FORMATTING) is the proper Unicode mechanism to FORCE a
        # paragraph's base direction to RTL while still letting
        # embedded Latin/digit runs read left-to-right within the
        # paragraph. We strip any pre-existing RLM/RLE the message
        # might already carry so we don't double-wrap.
        if msg:
            stripped = msg.lstrip('‏‪‫‬‭‮')
            stripped = stripped.rstrip('‬')
            msg = '‫' + stripped + '‬'
        # Fire the actual GUI toast on a short-lived daemon thread so a wedged
        # GUI/render subsystem can NEVER stall the calling thread. Field case:
        # during heavy debrid embedded-subtitle extraction the video decoder
        # starves and the GUI message pump backs up; a translation-kickoff
        # toast issued from the translate() worker blocked there and froze the
        # whole translation before it could dispatch a single chunk (the user
        # saw "AI is translating" but nothing ever happened). Dialog().
        # notification() is meant to be async, but under that duress it did not
        # return -- so we never issue it on a thread that has real work to do.
        def _show(_title=title, _msg=msg, _icon=icon, _ms=time_ms):
            try:
                xbmcgui.Dialog().notification(_title, _msg, _icon, _ms)
            except Exception:
                pass
            finally:
                with _notify_lock:
                    _notify_pending[0] -= 1
        # Cap the number of toast threads that may be parked at once. Normally a
        # toast returns in milliseconds so the count sits at 0-1; it only climbs
        # when the GUI is wedged and notification() isn't returning -- exactly
        # when we must NOT pile on more blocked threads.
        with _notify_lock:
            if _notify_pending[0] >= _NOTIFY_MAX_PENDING:
                return
            _notify_pending[0] += 1
        try:
            threading.Thread(target=_show, name='pov-notify',
                             daemon=True).start()
        except Exception:
            # Couldn't spawn a thread (thread/memory exhaustion -- itself a sign
            # of a wedged, resource-starved device). Do NOT fall back to an
            # inline notification() call: on the caller's thread that would
            # re-block it, the very stall this whole change exists to prevent.
            # Drop the toast and release the slot we just reserved.
            with _notify_lock:
                _notify_pending[0] -= 1
    except Exception:
        pass


def home_window():
    try:
        return xbmcgui.Window(10000) if KODI_AVAILABLE else None
    except Exception:
        return None
