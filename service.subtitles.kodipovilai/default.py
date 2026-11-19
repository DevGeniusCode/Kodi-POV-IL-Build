# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# File: service.subtitles.kodipovilai/default.py

# Kodi subtitle service entry point.
#
# Kodi launches this with action=search or action=download in the
# query string. For search, we hand back a list of available subs
# via ListItem objects. For download, we return the path of an SRT
# file on disk via a single ListItem with the file path.
#
# Everything we do here is wrapped in try/except: a crash in this
# script is invisible to the user except as "no subtitles found",
# but it would leak stack traces into kodi.log. We catch and log
# gracefully so the rest of Kodi keeps running.

from resources.lib.entry.plugin import main


# We import lazily inside handlers so a bad import path doesn't
# prevent the plugin from registering at all.

ADDON_ID = 'service.subtitles.kodipovilai'


if __name__ == '__main__':
    main()
