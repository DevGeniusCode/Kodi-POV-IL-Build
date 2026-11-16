# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""Arctic Fuse 3 repair steps.

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.
"""
import threading
import time

try:
    import xbmc
except ImportError:
    xbmc = None

from resources.libs.host_repairs.flow.common import _translate_path


def _maybe_patch_af3_dialog_subtitles():
    """Self-healing patch of Arctic Fuse 3's Dialog_DialogSubtitles.xml
    so the subtitle picker dialog HEADER prefers our window property
    `subs.player_filename` over the built-in `Player.FileName`. AF3's
    structure differs from FENtastic/Estuary (the layout lives in a
    secondary file referenced by `<include>DialogSubtitles</include>`,
    not in DialogSubtitles.xml directly), so the generic header
    patcher bails with 'no_target'. This dedicated AF3 patcher injects
    a `<variable>` with conditional fallback semantics + swaps the
    param-label to reference it. No-op if AF3 isn't installed."""
    try:
        from resources.libs.host_repairs import (
            af3_dialog_subtitles_patcher, kodi_utils)
    except Exception:
        return
    try:
        status = af3_dialog_subtitles_patcher.ensure_patched()
        if status == 'patched':
            kodi_utils.log(
                'af3_dialog_subtitles_patcher: header label now '
                'prefers subs.player_filename with fallback to '
                'Player.FileName', level='INFO')
        elif status in ('no_af3', 'no_file', 'already_patched'):
            pass  # quiet steady-state -- AF3 not installed yet or
                  # patch already in place
        else:
            kodi_utils.log(
                'af3_dialog_subtitles_patcher: ' + status,
                level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'af3_dialog_subtitles_patcher failed: '
                '{0}'.format(e), level='WARNING')
        except Exception:
            pass


def _maybe_patch_hub_search():
    """Restore the search button on Estuary, FENtastic and NOX at boot.

    The provider picker also calls this patcher after a choice; startup must
    cover the default POV choice and skin updates before the first click.
    """
    try:
        from resources.libs.host_repairs import fentastic_search_patcher, kodi_utils
        status = fentastic_search_patcher.ensure_patched()
        if status not in ('patched', 'unchanged', 'no_target'):
            kodi_utils.log('hub search patcher: {0}'.format(status), level='WARNING')
    except Exception as exc:
        try:
            kodi_utils.log('hub search patcher failed: {0}'.format(exc),
                           level='WARNING')
        except Exception:
            pass


def _maybe_patch_af3_home():
    """Seed Arctic Fuse 3 with POV/FENtastic-style home widgets.

    AF3's default home widgets are Kodi-library smart playlists, which
    are empty in this streaming build and show "No Results" on fresh
    installs. This writes script.skinvariables' per-user node JSON so
    the AF3 home screen opens directly into POV rows: new movies,
    trending shows, continue watching, personal lists, genres, AI
    settings, and working wizard/power-menu actions."""
    try:
        from resources.libs.host_repairs import af3_home_patcher, kodi_utils
    except Exception:
        return
    try:
        status = af3_home_patcher.ensure_patched()
        if status in ('patched', 'patched_rebuilt'):
            kodi_utils.log(
                'af3_home_patcher: seeded POV home nodes ({0})'
                .format(status),
                level='INFO')
        elif status in ('no_af3', 'already_patched'):
            pass
        else:
            kodi_utils.log('af3_home_patcher: ' + status,
                           level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log('af3_home_patcher failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_restore_af3_font_early():
    """Arctic Fuse 3 came up without the build's Hebrew font: Kodi replaced
    the skin before or during the last session and nothing repaired it yet.
    Every Hebrew letter is a box and AF3's labels are English until the
    start-up pass reaches AF3 -- after the home rows, about a minute on an
    Android box (field report, 23 Oct 2026). Put the font back now and reload
    the skin after 5 s in which a reload interrupts nothing (skin_reload_idle);
    the start-up pass still does the rest. The usual boot costs one read of a
    1.4 KB file."""
    try:
        if xbmc.getSkinDir() != 'skin.arctic.fuse.3':
            return
        # Kodi unloads AF3 while it installs an update: its files are in flux.
        if not xbmc.getCondVisibility('System.HasAddon(skin.arctic.fuse.3)'):
            return
        try:
            with open(_translate_path(
                    'special://home/addons/skin.arctic.fuse.3/1080i/Font.xml'), 'rb') as fh:
                if b'NotoSans-Regular.ttf' in fh.read():
                    return
        except OSError:
            pass
        from resources.libs.host_repairs import af3_home_patcher, kodi_utils
        # Monotonic: a box without a real-time clock jumps forward on its
        # first time sync; and it is one clock for every interpreter in Kodi.
        started = time.monotonic()
        if not af3_home_patcher.restore_hebrew_font():
            return
    except Exception:
        return

    def _reload():
        try:
            monitor = xbmc.Monitor()
            held = 0.0
            import xbmcgui
            home = xbmcgui.Window(10000)
            while time.monotonic() - started < 120:
                try:
                    reloaded = float(home.getProperty(af3_home_patcher.RELOADED_PROPERTY) or 0)
                except ValueError:
                    reloaded = 0.0
                if reloaded and reloaded >= started:
                    # The start-up pass's AF3 rebuild got there first and
                    # reloaded with the font in place (QA: a second reload,
                    # 91 s in once a dialog closed, was pure cost).
                    kodi_utils.log('af3: Hebrew font restored at start-up; the AF3 rebuild '
                                   'reloaded', level='INFO')
                    return
                if af3_home_patcher.skin_reload_idle():
                    held += 0.5
                    if held >= 5:
                        if af3_home_patcher._is_af3_active():
                            af3_home_patcher.reload_skin()
                            kodi_utils.log('af3: Hebrew font restored at start-up, skin reloaded '
                                           'after {0:.1f}s'.format(time.monotonic() - started),
                                           level='INFO')
                        return
                else:
                    held = 0.0
                if monitor.waitForAbort(0.5):
                    return
            kodi_utils.log('af3: Hebrew font restored at start-up; no idle moment for the '
                           'reload, the start-up pass reloads', level='INFO')
        except Exception:
            pass

    try:
        threading.Thread(target=_reload, daemon=True).start()
    except Exception:
        pass


def _maybe_show_af3_first_launch_dialog():
    """One-shot: if Arctic Fuse 3 is the active skin and we've never
    shown the first-launch dialog before, prompt the user to connect
    Trakt + TMDb via POV's Connect Services. AF3 needs both to
    populate its hubs; without them the home screen is empty and
    new users assume the skin is broken.

    Runs once per profile; the marker lives in our addon's settings.
    Has its own internal "remind me later" path that intentionally
    doesn't set the marker, so the user gets re-prompted next launch.

    Skin-gated -- a no-op on FENtastic / Estuary / any other skin --
    so existing-build users aren't disturbed when this addon ships
    via quickfix."""
    try:
        from resources.libs.host_repairs import af3_first_launch, kodi_utils
    except Exception:
        return
    try:
        status = af3_first_launch.maybe_show()
        if status not in ('not_af3', 'already_done'):
            try:
                kodi_utils.log(
                    'af3_first_launch dialog status: {0}'.format(status),
                    level='INFO')
            except Exception:
                pass
    except Exception as e:
        try:
            kodi_utils.log(
                'af3_first_launch failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
