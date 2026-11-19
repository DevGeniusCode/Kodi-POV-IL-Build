# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Kodi plumbing: the add-on handle, paths, localised strings, the log and the toast.
# One brand title for every dialog (DIALOG_TITLE).

import os
import threading
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


ADDON_ID = 'service.subtitles.kodipovilai'

# The brand title shown in every dialog, progress bar, toast and HTML page title of this
# add-on. Change it here and nowhere else.
DIALOG_TITLE = 'Kodi POV IL'
_ADDON = None

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


def addon():
    # Cache the Addon() instance. Constructing xbmcaddon.Addon() re-parses and
    # re-validates the addon's ENTIRE settings.xml against Kodi's settings
    # schema every single time (visible in the Kodi log as a burst of
    # "error reading <control>/<dependency> tag" warnings) -- with 100+
    # settings and a few schema quirks, that's real, repeated work. This
    # module has 100+ call sites (get_setting/set_setting/get_bool/...), and
    # a periodic background loop calls into one of them roughly once a
    # minute for the life of the Kodi session, so a fresh Addon() per call
    # meant a full settings.xml reparse every single time, indefinitely.
    # The Addon() handle itself doesn't go stale -- getSetting/setSetting
    # always read/write the live store -- so one cached instance is safe to
    # reuse for the life of the process, across threads.
    global _ADDON
    if _ADDON is None:
        _ADDON = xbmcaddon.Addon(ADDON_ID)
    return _ADDON


def localised(strid, *args):
    try:
        s = addon().getLocalizedString(strid)
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
        return os.path.join(os.path.expanduser('~'), '.kodi-test', ADDON_ID)
    p = xbmcvfs.translatePath('special://profile/addon_data/' + ADDON_ID + '/')
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
    from resources.lib.core.settings import get_setting
    cfg_level = get_setting('log_level', 'INFO').upper()
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
        xbmc.log('[{0}] {1}'.format(ADDON_ID, msg), level=kodi_level)
    except Exception:
        pass


def notify(msg, title=None, icon=None, time_ms=4000):
    from resources.lib.core.settings import get_setting
    if not KODI_AVAILABLE:
        print('NOTIFY:', title, '-', msg)
        return
    # User master switch (like DarkSubs): hide all subtitle/translation toasts
    # when turned off. Default ON, so nothing changes unless the user opts out.
    try:
        if (get_setting('subs_notifications', 'true')
                or 'true').strip().lower() == 'false':
            return
    except Exception:
        pass
    try:
        if title is None:
            title = DIALOG_TITLE
        if icon is None:
            icon = xbmcvfs.translatePath('special://home/addons/' + ADDON_ID + '/icon.png')
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


def _home_window():
    try:
        return xbmcgui.Window(10000) if KODI_AVAILABLE else None
    except Exception:
        return None
