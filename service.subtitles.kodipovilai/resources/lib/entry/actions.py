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
from resources.lib.ui.dialogs import _safe_log


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


def _handle_he_avail(params):
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
        _safe_log('he_avail crashed: {0}'.format(e), level='WARNING')


def _handle_choose_subs(params):
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
            _safe_log('open SubtitleSearch failed: {0}'.format(e),
                      level='WARNING')
    # Other skins -> our rich custom window.
    opened = False
    try:
        from resources.lib.ui import chooser
        opened = chooser.show()
    except Exception as e:
        _safe_log('choose_subs failed: {0}'.format(e), level='WARNING')
    if not opened and xbmc is not None:
        try:
            xbmc.executebuiltin('ActivateWindow(SubtitleSearch)')
        except Exception:
            pass
