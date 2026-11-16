# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""Helpers shared by the build-repair flow (AST-identical copies of the helpers service.py keeps for its own use).

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.
"""
import os
import sys

try:
    import xbmc
except ImportError:
    xbmc = None


ADDON_ID = 'plugin.program.kodipovilwizard'


def _translate_path(path):
    try:
        import xbmcvfs
        return xbmcvfs.translatePath(path)
    except Exception:
        return ''


# Heavy work that is useful later must not compete with the first home-widget
# wave.  This matters disproportionately on 32-bit Android boxes: two field
# logs measured the subtitle engine's cold import at 13.1s and 19.7s, and the
# full patch-health tree scan at 4.5-13.2s.  Both used to start while the skin
# was opening all of its lists.  A real request still takes the fast path at
# once; only speculative/background work is delayed.
_BACKGROUND_SETTLE_32 = 45.0


_BACKGROUND_SETTLE_OTHER = 15.0


def _background_settle_seconds():
    return (_BACKGROUND_SETTLE_32 if sys.maxsize <= 2 ** 32
            else _BACKGROUND_SETTLE_OTHER)


def _background_ui_busy():
    """Best-effort gate for CPU/disk work that has no user waiting on it."""
    try:
        return bool(xbmc.getCondVisibility('Player.HasMedia')
                    or xbmc.getCondVisibility('Container.IsUpdating')
                    or xbmc.getCondVisibility('System.HasVisibleModalDialog')
                    or xbmc.getCondVisibility('System.HasActiveModalDialog'))
    except Exception:
        return False


def _addon_version():
    try:
        import xbmcaddon
        return xbmcaddon.Addon().getAddonInfo('version') or ''
    except Exception:
        return ''


def _without(stamps, skin):
    """The `<skin>=<version>` stamps that are not this skin's, blanks dropped."""
    return [s for s in stamps if s and not s.startswith(skin + '=')]


def _walk_all(roots):
    """os.walk over several roots in turn, skipping the ones that are not
    there. Written out because `break` inside the caller's nested loops has to
    mean "stop scanning entirely", and chaining generators is the only shape
    that keeps that true across two roots."""
    for root in roots:
        if not os.path.isdir(root):
            continue
        for item in os.walk(root):
            yield item


def _other_addon_version(addon_id):
    """Version of SOME OTHER installed add-on, '' if it is not installed.

    Deliberately separate from _addon_version(): that one answers for us, and
    an id-taking overload of it would read at the call site like our own
    version filtered by something.
    """
    try:
        import xbmcaddon
        return xbmcaddon.Addon(addon_id).getAddonInfo('version') or ''
    except Exception:
        return ''
