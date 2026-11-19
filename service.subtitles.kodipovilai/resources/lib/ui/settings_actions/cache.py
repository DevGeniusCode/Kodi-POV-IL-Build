# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Settings actions of the caches: clear cache, community pool sharing, purge temp.

try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None
from resources.lib.ui.dialogs import _safe_log, _title


def _handle_clear_cache(_params):
    """Wipe all cached translations + metadata."""
    try:
        from resources.lib.core.cache import translation_cache
        from resources.lib.core import kodi
    except Exception as e:
        xbmcgui.Dialog().ok(_title(), 'Internal error: {0}'.format(e))
        return
    confirm = xbmcgui.Dialog().yesno(
        _title(),
        'נקה את כל ה-cache של התרגומים?\n(תרגומים עתידיים יתבצעו מחדש.)',
    )
    if not confirm:
        return
    n = translation_cache.clear_all()
    xbmcgui.Dialog().ok(_title(), kodi.localised(32637, n))


def _handle_pool_share_cache(_params):
    """Button handler for "share my cached translations". Confirms with the
    user, then hands the actual work to a SEPARATE detached RunScript
    (action=pool_share_cache_run) so the settings UI is freed immediately and
    the upload runs in the background -- the user can keep using Kodi while it
    paces uploads. Safe to run repeatedly: the server dedups by the Hebrew
    result hash and each file is marked once shared."""
    try:
        from resources.lib.fetching import pool_client
        from resources.lib.core import kodi, settings
    except Exception as e:
        xbmcgui.Dialog().ok(_title(), 'Internal error: {0}'.format(e))
        return

    # Run-lock on the global home window (visible across processes) so an
    # accidental double click can't start two background runs.
    win = xbmcgui.Window(10000)
    if win.getProperty('ai_subs.pool_migrating') == '1':
        xbmcgui.Dialog().ok(_title(), 'שיתוף המטמון כבר פועל ברקע.')
        return

    if not pool_client.share_enabled():
        if not xbmcgui.Dialog().yesno(
                _title(),
                'שיתוף למאגר הקהילתי כבוי. להפעיל אותו עכשיו ולשתף את כל '
                'התרגומים שבמטמון?'):
            return
        try:
            settings.set_setting('pool_share', 'true')
        except Exception:
            pass

    if not xbmcgui.Dialog().yesno(
            _title(),
            'לשתף ברקע את כל תרגומי ה-AI ששמורים אצלך במטמון אל המאגר '
            'הקהילתי?\nאפשר להמשיך להשתמש בקודי בזמן זה. פעולה חד-פעמית; '
            'כפילויות נמנעות אוטומטית.'):
        return

    # Claim the lock BEFORE firing the worker so a second click is rejected.
    win.setProperty('ai_subs.pool_migrating', '1')
    try:
        xbmc.executebuiltin(
            'RunScript(service.subtitles.kodipovilai,'
            'action=pool_share_cache_run)')
        kodi.notify('שיתוף המטמון התחיל ברקע — אפשר להמשיך כרגיל',
                          time_ms=5000)
    except Exception as e:
        win.clearProperty('ai_subs.pool_migrating')
        _safe_log('pool_share_cache dispatch failed: {0}'.format(e),
                  level='ERROR')


def _handle_pool_share_cache_run(_params):
    """Background worker for the cache migration (launched detached by
    _handle_pool_share_cache). Shows a non-modal progress banner and a final
    toast -- no modal dialog, so it never interrupts the user. Releases the
    run-lock when done."""
    win = xbmcgui.Window(10000)
    try:
        from resources.lib.ai_translation.reuse import pool_contribute
        from resources.lib.core import kodi
    except Exception:
        try:
            win.clearProperty('ai_subs.pool_migrating')
        except Exception:
            pass
        return

    progress = None
    try:
        progress = xbmcgui.DialogProgressBG()
        progress.create(_title(), 'משתף תרגומים למאגר...')
    except Exception:
        progress = None

    def report(done, total):
        if progress is None:
            return
        try:
            pct = int(done * 100 / max(1, total))
            progress.update(pct, _title(),
                            'משתף תרגומים למאגר ({0}/{1})'.format(done, total))
        except Exception:
            pass

    def cancelled():
        try:
            return progress is not None and progress.isFinished()
        except Exception:
            return False

    submitted = skipped = total = 0
    try:
        submitted, skipped, total = pool_contribute.share_cache(
            progress_cb=report, should_cancel=cancelled)
    except Exception as e:
        _safe_log('pool_share_cache_run crashed: {0}'.format(e),
                  level='ERROR')
    finally:
        win.clearProperty('ai_subs.pool_migrating')
        if progress is not None:
            try:
                progress.close()
            except Exception:
                pass
        try:
            kodi.notify(
                'שיתוף המטמון הסתיים — נשלחו {0}, דולגו {1} (מתוך {2})'.format(
                    submitted, skipped, total),
                time_ms=6000)
        except Exception:
            pass


def _handle_purge_temp(_params):
    """Wipe ALL .srt files in special://temp/. Used to clear out
    stale subtitle leftovers from previous movies that Kodi keeps
    in temp and would otherwise leak into the next movie's
    subtitle search dialog."""
    try:
        from resources.lib.fetching import local
    except Exception as e:
        xbmcgui.Dialog().ok(_title(), 'Internal error: {0}'.format(e))
        return
    n = local.purge_temp_subs()
    xbmcgui.Dialog().ok(
        _title(),
        'נמחקו {0} קבצי כתוביות מ-temp.'.format(n))
