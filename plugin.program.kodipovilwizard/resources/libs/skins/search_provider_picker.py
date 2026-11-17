# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""The home-search provider picker (POV or Umbrella), moved from the subtitle add-on's default.py (Phase 3, step 4).

Called by Router.dispatch for mode=search_provider. Moved verbatim; only the import prefix differs.
"""
import xbmcgui


def _handle_search_provider(_params):
    """RunScript action=search_provider -- pick which add-on the home search
    button searches, POV or Umbrella, on every skin the build ships.

    One screen, two rows, the current one ticked. Choosing rewrites all four
    skins' search wiring and reloads the skin the user is looking at, so the
    change is visible immediately rather than after a restart."""
    try:
        from resources.libs.host_repairs import search_provider
    except Exception as e:
        try:
            xbmcgui.Dialog().ok('Kodi POV IL', 'Internal error: {0}'.format(e))
        except Exception:
            pass
        return

    now = search_provider.current()
    have_umbrella = search_provider.umbrella_available()
    if not have_umbrella:
        xbmcgui.Dialog().ok(
            'מנוע החיפוש',
            'החיפוש עובד כרגע דרך POV.\n\n'
            'כדי לחפש דרך Umbrella צריך קודם להתקין אותו: '
            'תוספים ← Kodi POV IL Wizard ← תוספים וסקינים אופציונליים ← Umbrella.')
        return

    options = []
    order = (search_provider.POV, search_provider.UMBRELLA)
    for key in order:
        mark = '✓ ' if key == now else '   '
        options.append('{0}{1}'.format(mark, search_provider.DISPLAY[key]))
    try:
        choice = xbmcgui.Dialog().select(
            'מנוע החיפוש - במה לחפש?', options)
    except Exception:
        choice = -1
    if choice < 0:
        return
    picked = order[choice]
    if picked == now:
        return

    search_provider.set_provider(picked)
    _statuses, reloaded = search_provider.apply_to_skins()
    name = search_provider.DISPLAY[picked]
    # Onyx reads the choice when the button is pressed: nothing to reload.
    if reloaded or search_provider.active_skin_reads_live():
        # The skin reload already IS the feedback; a dialog on top of it would
        # land on a screen that is still rebuilding.
        try:
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.notify('החיפוש עובר ל-{0}'.format(name), time_ms=4000)
        except Exception:
            pass
        return
    xbmcgui.Dialog().ok(
        'מנוע החיפוש',
        'החיפוש יעבוד מעכשיו דרך {0}.\n\n'
        'בסקין שאתם נמצאים בו כרגע השינוי ייכנס לתוקף לאחר הפעלה מחדש '
        'של Kodi.'.format(name))
