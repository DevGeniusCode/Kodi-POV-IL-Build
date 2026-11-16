# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""Subset of the subtitle add-on's kodi_utils that the moved build code uses (Phase 3).

The definitions are AST-identical copies. ADDON_ID still names the SUBTITLE add-on, so get_setting / set_setting /
addon_profile_path / log keep reading and writing that add-on's settings and profile folder until the settings
move to the Wizard (Phase 3, step 5).
"""
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


def get_setting(key, default=''):
    try:
        v = addon().getSetting(key)
        return v if v is not None else default
    except Exception:
        return default


def get_bool(key, default=False):
    v = get_setting(key, '')
    if v == '':
        return default
    return v.lower() == 'true'


def set_setting(key, value):
    """Set an addon setting. Returns True if the write persisted,
    False otherwise -- some Kodi/Android combinations silently
    swallow setSetting calls (the API returns ok but the value
    never reaches settings.xml on disk). Reading back is the only
    way to know whether the save actually took. Callers that don't
    care just ignore the return value -- backward compatible."""
    str_value = str(value)
    try:
        addon().setSetting(key, str_value)
    except Exception:
        return False
    try:
        return addon().getSetting(key) == str_value
    except Exception:
        return False


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


def log(msg, level='INFO'):
    """Log to Kodi's log at the appropriate level. Honours the
    addon's log_level setting -- anything below that is suppressed."""
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
            title = 'Kodi POV IL'
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
