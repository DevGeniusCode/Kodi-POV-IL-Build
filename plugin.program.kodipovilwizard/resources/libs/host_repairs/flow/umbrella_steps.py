# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""Umbrella / MDBList / Trakt repair steps and the service-mirror keeper.

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.

Phase 5: the source patches of Umbrella (prewarm, source name, subtitle match, MDBList token and sync) are registry entries
now, and its settings (API language, content filters, External Provider, CocoScrapers providers, defaults) are baked into
userdata. What stays here is data that is rebuilt after an Umbrella update, the two token mirrors, and the two edits the
registry cannot express (the Hebrew-search URL fix and the personal-list order).
"""
import threading


def _maybe_patch_umbrella():
    """Hebrew menu strings, the Account Manager label mirror, the POV -> Umbrella authorisation mirrors and the two
    runtime source edits for Umbrella (the opt-in pilot add-on). Instant no-op for everyone who never installed it."""
    try:
        from resources.libs.host_repairs import kodi_utils
    except Exception:
        return
    # Hebrew for the menus themselves. Additive: we create the he_il folder Umbrella does not ship, so their updates keep
    # applying and a string we did not translate simply falls back to English. An Umbrella update replaces the add-on
    # folder and takes the file with it, so this re-runs at every start.
    try:
        from resources.libs.host_repairs import umbrella_hebrew_ui_patcher
        if umbrella_hebrew_ui_patcher.ensure_patched() == 'patched':
            kodi_utils.log(
                'umbrella_hebrew_ui_patcher: Hebrew menu strings '
                'installed', level='INFO')
    except Exception:
        pass
    # Account Manager Lite (the other opt-in pilot) trips over the same locale-folder fallback, and the labels it loses
    # are Authorize, Username, Password and API Key -- the controls a user has to press to connect an account.
    # No-op for anyone who never installed it.
    try:
        from resources.libs.host_repairs import legacy_lang_mirror
        if legacy_lang_mirror.mirror('script.module.acctmgr') == 'patched':
            kodi_utils.log(
                'legacy_lang_mirror: Account Manager Lite labels will render',
                level='INFO')
    except Exception:
        pass
    # Keep Umbrella on whatever MDBList and Trakt authorisations POV currently holds. POV owns the refreshing -- only its
    # client_id can -- so this is what carries a refreshed token across to Umbrella, and what covers a user who
    # authorised before this existed.
    #
    # MDBLIST FIRST, AND THE ORDER IS LOAD-BEARING. Both mirrors claim the same two Umbrella settings
    # (indicators.alt / scrobble.source) and the claim is one-shot per key, so whichever runs first while they are still at
    # the shipped Local wins permanently. This build prefers MDBList, and the keeper loop in _start_service_mirror_keeper
    # runs them in this same order for the same reason -- if you change one, change both.
    try:
        from resources.libs.host_repairs import mdblist_umbrella_mirror
        st = mdblist_umbrella_mirror.mirror()
        if st == 'mirrored':
            kodi_utils.log(
                'mdblist_umbrella_mirror: Umbrella now shares POV\'s MDBList '
                'authorisation', level='INFO')
        elif st == 'write_failed':
            kodi_utils.log(
                'mdblist_umbrella_mirror: ' + st, level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'mdblist_umbrella_mirror failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
    try:
        from resources.libs.host_repairs import trakt_umbrella_mirror
        st = trakt_umbrella_mirror.mirror()
        if st == 'mirrored':
            kodi_utils.log(
                'trakt_umbrella_mirror: Umbrella now shares POV\'s Trakt '
                'authorisation', level='INFO')
        elif st in ('write_failed', 'incomplete'):
            kodi_utils.log('trakt_umbrella_mirror: ' + st, level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'trakt_umbrella_mirror failed: {0}'.format(e), level='WARNING')
        except Exception:
            pass
    # Searching Umbrella in Hebrew found nothing: the percent-encoded query made Umbrella's own api_key substitution raise,
    # so the request was never sent. See umbrella_tmdb_apikey_patcher for the full account. It REPLACES six call sites, so
    # it cannot be a registry hook.
    try:
        from resources.libs.host_repairs import umbrella_tmdb_apikey_patcher
        st = umbrella_tmdb_apikey_patcher.ensure_patched()
        if st == 'patched':
            kodi_utils.log(
                'umbrella_tmdb_apikey_patcher: non-ASCII search repaired',
                level='INFO')
        elif st in ('unmatched', 'compile_failed', 'write_failed',
                    'read_failed'):
            kodi_utils.log(
                'umbrella_tmdb_apikey_patcher: ' + st, level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'umbrella_tmdb_apikey_patcher failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
    try:
        from resources.libs.host_repairs import umbrella_list_order_patcher
        st = umbrella_list_order_patcher.ensure_personal_list_order()
        if st in ('patched', 'unmatched', 'write_failed'):
            kodi_utils.log('umbrella personal list order: ' + st)
    except Exception as e:
        kodi_utils.log('umbrella personal list order failed: ' + str(e), level='WARNING')


# The auto-on-play machinery (state, the on-play search/apply flow, and the
# Player listener) lives in the subtitle add-on, resources/lib/autosub/player.py
# (and autosub/policy.py), so the standalone (repo-channel) service runs the exact same code.


def _start_service_mirror_keeper(monitor):
    """Keep Umbrella on whatever MDBList and Trakt authorisations POV holds.

    The startup mirror covers most of it, but POV refreshes its token
    silently in the background -- with its own client_id, the only one that
    can -- and a set-top box stays on for days. Once POV rotates the token,
    the copy Umbrella reads from disk is stale, so the next Umbrella session
    authenticates with a dead token. Umbrella also fixes its Authorization
    header at module-import time and reuses its interpreter, so there is no
    way to hand it a new token mid-session; what matters is that the value on
    disk is right BEFORE it next imports.

    A periodic re-mirror is two settings reads and writes only on a change,
    so it costs nothing to run often. Deliberately NOT a patch to POV's own
    mdbl_refresh(): another injection into somebody else's file, to achieve
    what a cheap poll already achieves, is surface area for no gain.

    Every minute, not every quarter of an hour, and that is what makes a
    fresh connect land. MDBList gets an instant push -- POV's Connect
    Services row fires our mirror the moment it returns -- but Trakt has no
    such hook, and wiring one means wrapping another class on the screen
    that takes the whole of Connect Services down if it raises. A minute of
    lag is worth more than that risk. In the steady state a pass is a
    handful of getSetting calls and no writes at all.

    MDBLIST BEFORE TRAKT, and the startup pass in _maybe_patch_umbrella_
    language uses the same order for the same reason: both claim the same
    two watch-source settings, once, and first past the post wins."""
    try:
        from resources.libs.host_repairs import mdblist_umbrella_mirror
    except Exception:
        return
    try:
        from resources.libs.host_repairs import trakt_umbrella_mirror
    except Exception:
        trakt_umbrella_mirror = None
    try:
        from resources.libs.host_repairs import umbrella_watch_prompt
    except Exception:
        umbrella_watch_prompt = None

    def _loop():
        try:
            if monitor.waitForAbort(90):   # let startup settle first
                return
            while not monitor.abortRequested():
                try:
                    mdblist_umbrella_mirror.mirror()
                except Exception:
                    pass
                if trakt_umbrella_mirror is not None:
                    try:
                        trakt_umbrella_mirror.mirror()
                    except Exception:
                        pass
                # Deliberately in the keeper and not in the startup steps:
                # it can put a dialog on screen, and boot is already crowded
                # with them. By the first tick the splash is long gone.
                if umbrella_watch_prompt is not None:
                    try:
                        umbrella_watch_prompt.maybe_ask_async()
                    except Exception:
                        pass
                if monitor.waitForAbort(60):
                    break
        except Exception:
            pass

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception:
        pass
