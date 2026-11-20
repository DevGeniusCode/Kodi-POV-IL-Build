# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The question of the chooser's manual search ("חיפוש כתוביות ידני"), VLSub style: ONE free-text prompt, pre-filled with the
# playing release name, so the viewer edits it (drops a word, adds a release group such as FLUX, types another title) or
# accepts it with one OK press. An emptied box (Escape on Kodi's keyboard returns '') cancels the flow.

from resources.lib.fetching import manual

try:
    import xbmcgui
except Exception:
    xbmcgui = None

_ALPHANUM = getattr(xbmcgui, 'INPUT_ALPHANUM', 0)

PROMPT_TITLE = 'חיפוש מחרוזת כתובית'


def _toast(text):
    try:
        from resources.lib.core import kodi
        kodi.notify(text, time_ms=3000)
    except Exception:
        pass


def _playing_release(playing):
    try:
        from resources.lib.core import video_context
        return video_context.release_name(playing, 'chooser') or ''
    except Exception:
        return ''


def ask(playing, dialog=None, release=None):
    """Ask the viewer for the query. Returns the cleaned query text, or None when the viewer cancelled (a toast says so
    when the text was unusable). `release` overrides the release name used for the pre-fill (tests)."""
    if dialog is None:
        dialog = xbmcgui.Dialog()
    if release is None:
        release = _playing_release(playing)
    text = dialog.input(PROMPT_TITLE, defaultt=manual.default_query(playing, release), type=_ALPHANUM)
    query = manual.clean_query(text)
    if not query:
        return None
    if len(manual.normalize(query)) < 2:
        _toast('המחרוזת קצרה מדי')
        return None
    return query
