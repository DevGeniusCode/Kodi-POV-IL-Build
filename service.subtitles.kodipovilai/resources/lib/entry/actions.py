# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Small RunScript actions: the Hebrew-availability warm (he_avail) and the chooser opener.

try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None
from resources.lib.ui.dialogs import dialogs_title, safe_log


# _he_avail_store AND ITS HE_AVAIL_CACHE PATH USED TO LIVE HERE. Both were
# dead -- nothing called the function, nothing read the constant, and the only
# thing that named either was a comment in he_sub_match pointing here as the
# other writer of that cache.
#
# NOT HARMLESS DEAD CODE. It wrote `names` and `embedded` with no bound, and
# OVERWROTE the embedded list instead of unioning it: the two bugs the live
# warm path had to be fixed for, the second of which wiped a built-in-Hebrew
# flag the device had just detected itself. Anyone reviving it, or copying
# from it because it looked like the shorter version of the same job, would
# have brought both back. he_sub_match._store_avail is the one writer.


def handle_he_avail(params):
    """Background warm of the source-screen "HEB NN%" badge -- runs in
    MoranSubs's own process (fire-and-forget), so NOTHING here blocks POV's
    source window. It gathers every Hebrew-availability source and writes the
    merged result to the shared cache the badge reads on the next open:
      * community pool + Wizdom (networked here now, NOT synchronously in POV's
        window -- that's what removed the multi-second source-list freeze);
      * the pool's embedded-Hebrew flags (releases known to ship a built-in
        Hebrew track) -> the "BUILT-IN 101%" badge;
      * OpenSubtitles (rotating keys, no shared-account load);
      * Ktuvit -- ONLY as a fallback when nothing else found Hebrew, and gated
        by `he_match_ktuvit`. Ktuvit runs on ONE shared, rate-limited account,
        so we must never hit it on every browse (that broke real downloads);
        the fallback-only guard + the 7-day warm cache keep it to a rare,
        bounded trickle for the exact case the user cares about (Hebrew that
        exists only on Ktuvit, not yet in the pool)."""
    try:
        import base64
        import json as _json
        blob = params.get('data') or ''
        if not blob:
            return
        info = _json.loads(base64.b64decode(blob).decode('utf-8'))
        # Delegate to the shared, parallelized warm in he_sub_match so the
        # RunScript fallback path and the in-service queue drainer run IDENTICAL
        # logic (pool+Wizdom fetched concurrently with OpenSubtitles, Ktuvit as a
        # gated shared-registry fallback, then written to the badge cache).
        from resources.lib import he_sub_match as _hsm
        _hsm.run_warm(info)
    except Exception as e:
        safe_log('he_avail crashed: {0}'.format(e), level='WARNING')


def handle_choose_subs(params):
    """Open the subtitle chooser for the player's 'בחר כתוביות' button.

    On FENtastic we open Kodi's NATIVE subtitle window -- that's Tal's own
    redesigned DialogSubtitles, the real thing, so it stays exactly as he made
    it. On every OTHER skin (Estuary / Arctic Fuse 3 / ...) the native
    dialog is plain, so we open our self-contained WindowXMLDialog instead, which
    brings the same gorgeous look everywhere. Both flows use the same candidate
    list + background AI translation. Guarded fallbacks at each step."""
    skin = ''
    if xbmc is not None:
        try:
            skin = (xbmc.getSkinDir() or '').strip()
        except Exception:
            skin = ''
    # FENtastic -> Tal's native window (unchanged).
    if skin == 'skin.fentastic' and xbmc is not None:
        try:
            xbmc.executebuiltin('ActivateWindow(SubtitleSearch)')
            return
        except Exception as e:
            safe_log('open SubtitleSearch failed: {0}'.format(e),
                      level='WARNING')
    # Other skins -> our rich custom window.
    opened = False
    try:
        from resources.lib.ui import chooser
        opened = chooser.show()
    except Exception as e:
        safe_log('choose_subs failed: {0}'.format(e), level='WARNING')
    if not opened and xbmc is not None:
        try:
            xbmc.executebuiltin('ActivateWindow(SubtitleSearch)')
        except Exception:
            pass


# Link kinds whose resolve() translates: a selection of one of these means a translation is (or was just) running.
_TRANSLATING_KINDS = ('engine_ai', 'embedded_ai')
# The marks bg_translate_picker / translate_file keep on the Home window while a live translation runs.
_LIVE_JOB_PROPS = ('ai_subs.live_translate_active', 'ai_subs.live_translate_source', 'ai_subs.live_translate_job')


def handle_cancel_translation(params):
    """The "ביטול תרגום" button of the subtitles dialog (RunScript action=cancel_translation).

    A translation job lives as long as the subtitle selection that started it (translation_cancelled /
    subtitle_selection_matches), and the automatic subtitle on play stops the same way
    (_autosub_owns_player). So cancelling is dropping the current selection: every running job sees it is
    obsolete, spends no further request, saves nothing and publishes nothing. What is already on screen stays."""
    try:
        from resources.lib.core import link_codec, selection as core_selection
        win = xbmcgui.Window(10000)
        running = any(win.getProperty(p) for p in _LIVE_JOB_PROPS)
        if not running:
            payload = link_codec.decode_link_safe(core_selection.get_current_subtitle() or '')
            running = isinstance(payload, dict) and payload.get('type') in _TRANSLATING_KINDS
        if not running:
            msg = 'אין תרגום פעיל'
        else:
            core_selection.set_current_subtitle('')
            for prop in _LIVE_JOB_PROPS:
                win.clearProperty(prop)
            safe_log('cancel_translation: the current selection was dropped', level='INFO')
            msg = 'התרגום בוטל'
        # always shown, whatever the subtitle-notifications switch says: it answers a button the viewer pressed
        xbmcgui.Dialog().notification(dialogs_title(), msg, xbmcgui.NOTIFICATION_INFO, 3000, False)
    except Exception as e:
        safe_log('cancel_translation crashed: {0}'.format(e), level='WARNING')
