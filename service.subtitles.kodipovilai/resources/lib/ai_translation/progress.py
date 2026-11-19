# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Progress of a translation: the quiet switch, the toast helper and the playback position.

from resources.lib.core import kodi


# When the auto-on-play flow is driving (autosub_service.autosub_on_play), success /
# progress notifications are shown in the top overlay by the caller -- so the
# scattered success toasts here are suppressed to avoid double messaging. Error
# toasts still fire. Mirrors DarkSubs, which shows status only in its on-play
# overlay, never as toasts.
QUIET = False


def set_quiet(value):
    global QUIET
    QUIET = bool(value)


def status(msg, **kwargs):
    """Success / informational status. Suppressed during auto-on-play (the
    overlay shows it instead); a normal toast otherwise."""
    if QUIET:
        return
    try:
        kodi.notify(msg, **kwargs)
    except Exception:
        pass


def still_playing():
    try:
        import xbmc as _x
        return bool(_x.Player().isPlayingVideo())
    except Exception:
        return False


def current_playback_ms():
    try:
        import xbmc
        player = xbmc.Player()
        if player.isPlayingVideo():
            return int(float(player.getTime()) * 1000)
    except Exception:
        pass
    return None
