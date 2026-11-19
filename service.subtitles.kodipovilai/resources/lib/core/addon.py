# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The add-on handle: the add-on id and ONE cached xbmcaddon.Addon instance. A leaf module (it
# imports nothing of this add-on), so core.kodi and core.settings can both rest on it.

try:
    import xbmcaddon
except ImportError:
    # Allows unit tests / standalone scripts to import the module
    # without a Kodi runtime.
    xbmcaddon = None


ADDON_ID = 'service.subtitles.kodipovilai'

_ADDON = None


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
