# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The questions of the chooser's manual search ("חיפוש כתוביות ידני"): as few as possible, each one pre-filled with what is
# playing, so accepting the value is one OK press. A movie asks 2 (title, year), an episode 3 (show, season, episode); the
# playing item decides which. An emptied box (Escape on Kodi's keyboard returns '') cancels the whole flow, so a year, a
# season or an episode that is wanted "none" is typed as 0 (a movie's year only).

from resources.lib.fetching import manual

try:
    import xbmcgui
except Exception:
    xbmcgui = None

_ALPHANUM = getattr(xbmcgui, 'INPUT_ALPHANUM', 0)
_NUMERIC = getattr(xbmcgui, 'INPUT_NUMERIC', 1)


def _toast(text):
    try:
        from resources.lib.core import kodi
        kodi.notify(text, time_ms=3000)
    except Exception:
        pass


def ask(playing, dialog=None):
    """Ask the viewer for the manual search. Returns {'title', 'year', 'season', 'episode'} (strings; season / episode are ''
    for a movie), or None when the viewer cancelled or typed something unusable (a toast says what)."""
    if dialog is None:
        dialog = xbmcgui.Dialog()
    default = manual.defaults(playing)
    episode = default['episode_mode']

    title = manual.clean_title(dialog.input(
        'חיפוש ידני: שם הסדרה' if episode else 'חיפוש ידני: שם הסרט',
        defaultt=default['title'], type=_ALPHANUM))
    if not title:
        return None

    if episode:
        season = dialog.input('עונה', defaultt=default['season'], type=_NUMERIC)
        if not (season or '').strip():
            return None
        season_n = manual.parse_number(season, 0, manual.SEASON_MAX)
        if season_n is None:
            _toast('מספר עונה לא תקין')
            return None
        episode_text = dialog.input('פרק', defaultt=default['episode'], type=_NUMERIC)
        if not (episode_text or '').strip():
            return None
        episode_n = manual.parse_number(episode_text, 1, manual.EPISODE_MAX)
        if episode_n is None:
            _toast('מספר פרק לא תקין')
            return None
        return {'title': title, 'year': '', 'season': str(season_n), 'episode': str(episode_n)}

    year = dialog.input('שנת יציאה (0 = ללא שנה)', defaultt=default['year'], type=_NUMERIC)
    if not (year or '').strip():
        return None
    if year.strip() == '0':
        year_n = 0
    else:
        year_n = manual.parse_number(year, manual.YEAR_MIN, manual.YEAR_MAX)
        if year_n is None:
            _toast('שנה לא תקינה')
            return None
    return {'title': title, 'year': str(year_n) if year_n else '', 'season': '', 'episode': ''}
