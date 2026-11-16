# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""Umbrella / MDBList / Trakt repair steps and the service-mirror keeper.

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.
"""
import threading

from resources.libs.host_repairs.flow.pov_steps import _skip_pov_patchers


def _maybe_patch_mdblist_reauth():
    """Let an expired MDBList token heal itself instead of being reconnected.

    POV refreshes only on a clock check and treats a 401 as just another
    network error, so a token the server stops accepting is permanent: every
    call fails, the sync monitor backs off half an hour, and the account has
    to be authorised again by hand. Umbrella then compounds it with a dialog
    telling the user to re-authenticate in a screen this build does not use.

    Trakt has the identical defect in the file next door, and a field log
    showed it failing in the same breath as the MDBList one recovered, so it
    gets the same treatment. See the modules."""
    # The switch guards the POV half ONLY. Written as an early return over
    # both halves first, which silently took Umbrella's fix down with it --
    # the switch's own text promises to stop changes to plugin.video.pov and
    # says nothing about any other add-on, and turning it on to isolate a POV
    # problem must not change Umbrella's behaviour as a side effect.

    try:
        from resources.libs.host_repairs import umbrella_mdblist_token_patcher, kodi_utils
        st = umbrella_mdblist_token_patcher.ensure_patched()
        if st in ('unmatched', 'compile_failed', 'write_failed'):
            kodi_utils.log(
                'umbrella_mdblist_token_patcher: ' + st, level='WARNING')
    except Exception as e:
        try:
            # Re-imported: if the import above is what raised, `kodi_utils` is
            # unbound here and the handler would raise instead of logging.
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.log(
                'umbrella_mdblist_token_patcher failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
    try:
        from resources.libs.host_repairs import umbrella_mdblist_sync_patcher, kodi_utils
        st = umbrella_mdblist_sync_patcher.ensure_patched()
        if st in ('unmatched', 'compile_failed', 'write_failed',
                  'revert_failed'):
            kodi_utils.log(
                'umbrella_mdblist_sync_patcher: ' + st, level='WARNING')
    except Exception as e:
        try:
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.log(
                'umbrella_mdblist_sync_patcher failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


def _maybe_patch_umbrella_language():
    """Umbrella (the opt-in pilot addon) ships its strings only in the
    LEGACY language layout (resources/language/English/), so on a
    Hebrew-interface Kodi every settings label resolves to an empty
    string -- blank categories, blank labels. Mirror the English po into
    the modern resource.language.en_gb folder Kodi actually looks for.
    Additive-only and self-healing: an Umbrella self-update replaces the
    addon folder, and this re-applies on the next startup. Instant no-op
    for everyone who never installed the pilot."""
    try:
        from resources.libs.host_repairs import umbrella_language_patcher, kodi_utils
    except Exception:
        return
    try:
        status = umbrella_language_patcher.ensure_patched()
        if status == 'patched':
            kodi_utils.log(
                'umbrella_language_patcher: modern en_gb strings installed',
                level='INFO')
        elif status in ('read_failed', 'write_failed'):
            kodi_utils.log(
                'umbrella_language_patcher: ' + status, level='WARNING')
        # Hebrew for the menus themselves. Additive: we create the he_il
        # folder Umbrella does not ship, so their updates keep applying and a
        # string we did not translate simply falls back to English.
        try:
            from resources.libs.host_repairs import umbrella_hebrew_ui_patcher
            if umbrella_hebrew_ui_patcher.ensure_patched() == 'patched':
                kodi_utils.log(
                    'umbrella_hebrew_ui_patcher: Hebrew menu strings '
                    'installed', level='INFO')
        except Exception:
            pass
        # ORDER IS LOAD-BEARING: the metadata language must move BEFORE the
        # content filters are re-evaluated. api.language drives both, and
        # Hebrew with the filters still on asks for titles ORIGINALLY MADE in
        # Hebrew -- which empties every list.
        lang = umbrella_language_patcher.ensure_api_language()
        if lang == 'patched':
            kodi_utils.log(
                'umbrella_language_patcher: metadata language set to Hebrew',
                level='INFO')
        # Second, unrelated half: Umbrella's two language CONTENT FILTERS
        # empty every list when its API language is not English.
        filt = umbrella_language_patcher.ensure_content_filters_sane()
        if filt == 'patched':
            kodi_utils.log(
                'umbrella_language_patcher: language content filters cleared',
                level='INFO')
    except Exception as e:
        try:
            kodi_utils.log(
                'umbrella_language_patcher run failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
    # Account Manager Lite (the other opt-in pilot) trips over the same
    # locale-folder fallback, and the labels it loses are Authorize,
    # Username, Password and API Key -- the controls a user has to press to
    # connect an account. No-op for anyone who never installed it.
    try:
        from resources.libs.host_repairs import legacy_lang_mirror
        if legacy_lang_mirror.mirror('script.module.acctmgr') == 'patched':
            kodi_utils.log(
                'legacy_lang_mirror: Account Manager Lite labels will render',
                level='INFO')
    except Exception:
        pass
    # Wiring + subtitle-matching hook for the same optional add-on.
    try:
        from resources.libs.host_repairs import umbrella_setup_patcher
        prov = umbrella_setup_patcher.ensure_external_provider()
        if prov == 'patched':
            kodi_utils.log(
                'umbrella_setup_patcher: CocoScrapers wired as the external '
                'provider', level='INFO')
        elif prov == 'repaired':
            kodi_utils.log(
                'umbrella_setup_patcher: incomplete CocoScrapers wiring '
                'repaired', level='WARNING')
        cps = umbrella_setup_patcher.ensure_coco_providers()
        if cps == 'patched':
            kodi_utils.log(
                'umbrella_setup_patcher: extra CocoScrapers providers enabled',
                level='INFO')
        dfl = umbrella_setup_patcher.ensure_umbrella_defaults()
        if dfl == 'patched':
            kodi_utils.log(
                'umbrella_setup_patcher: Umbrella defaults applied',
                level='INFO')
        hook = umbrella_setup_patcher.ensure_source_name_published()
        if hook == 'patched':
            kodi_utils.log(
                'umbrella_setup_patcher: picked-source release name is now '
                'published for subtitle matching', level='INFO')
        elif hook in ('unmatched', 'compile_failed', 'write_failed'):
            kodi_utils.log(
                'umbrella_setup_patcher: ' + hook, level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'umbrella_setup_patcher run failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
    # The Hebrew-subtitle match badge in Umbrella's OWN source window -- the
    # same brain (he_sub_match) that already feeds POV's, so a title warmed
    # from one add-on shows its badge immediately in the other. Separate from
    # the block above because it patches a different Umbrella file and must
    # not be lost if the wiring above raises.
    try:
        from resources.libs.host_repairs import umbrella_subtitle_match_patcher
        st = umbrella_subtitle_match_patcher.ensure_patched()
        if st == 'patched':
            kodi_utils.log(
                'umbrella_subtitle_match_patcher: Hebrew match % added to '
                "Umbrella's source window", level='INFO')
        elif st in ('unmatched', 'compile_failed', 'write_failed',
                    'read_failed'):
            kodi_utils.log(
                'umbrella_subtitle_match_patcher: ' + st, level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'umbrella_subtitle_match_patcher failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
    # Kodi's own "playback failed" after a deliberate back-out of the source
    # list. It is a 20-second timer on consecutive unresolved plays, not a
    # report about this playback -- see kodi_playlist_timeout_patcher.
    try:
        from resources.libs.host_repairs import kodi_playlist_timeout_patcher
        st = kodi_playlist_timeout_patcher.ensure_patched()
        if st in ('patched', 'created'):
            kodi_utils.log(
                'kodi_playlist_timeout_patcher: ' + st, level='INFO')
        elif st in ('unmatched', 'bad_xml', 'write_failed', 'read_failed'):
            kodi_utils.log(
                'kodi_playlist_timeout_patcher: ' + st, level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'kodi_playlist_timeout_patcher failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass
    # Keep Umbrella on whatever MDBList and Trakt authorisations POV currently
    # holds. POV owns the refreshing -- only its client_id can -- so this is
    # what carries a refreshed token across to Umbrella, and what covers a
    # user who authorised before this existed.
    #
    # MDBLIST FIRST, AND THE ORDER IS LOAD-BEARING. Both mirrors claim the
    # same two Umbrella settings (indicators.alt / scrobble.source) and the
    # claim is one-shot per key, so whichever runs first while they are still
    # at the shipped Local wins permanently. This build prefers MDBList, and
    # the keeper loop in _start_service_mirror_keeper runs them in this same
    # order for the same reason -- if you change one, change both.
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
    # Searching Umbrella in Hebrew found nothing: the percent-encoded query
    # made Umbrella's own api_key substitution raise, so the request was
    # never sent. See umbrella_tmdb_apikey_patcher for the full account.
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
        from resources.libs.host_repairs import umbrella_setup_patcher
        st = umbrella_setup_patcher.ensure_personal_list_order()
        if st in ('patched', 'unmatched', 'write_failed'):
            kodi_utils.log('umbrella personal list order: ' + st)
    except Exception as e:
        kodi_utils.log('umbrella personal list order failed: ' + str(e), level='WARNING')

    # Two source-flow repairs: fire the Hebrew-availability warm at the START
    # of the scrape so the badge is there on the FIRST entry rather than the
    # second, and stop Kodi announcing "playback failed" when the user simply
    # backed out of the source list.
    try:
        from resources.libs.host_repairs import umbrella_source_ux_patcher
        st = umbrella_source_ux_patcher.ensure_patched()
        if st == 'patched':
            kodi_utils.log(
                'umbrella_source_ux_patcher: applied (its own line says what)',
                level='INFO')
        elif st in ('unmatched', 'compile_failed', 'write_failed',
                    'read_failed'):
            kodi_utils.log(
                'umbrella_source_ux_patcher: ' + st, level='WARNING')
    except Exception as e:
        try:
            kodi_utils.log(
                'umbrella_source_ux_patcher failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


# The auto-on-play machinery (state, the on-play search/apply flow, and the
# Player listener) lives in resources/lib/autosub_service.py -- extracted
# VERBATIM so the standalone (repo-channel) service runs the exact same code.


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
        from resources.libs.host_repairs import pov_seasons_view_seed
    except Exception:
        pov_seasons_view_seed = None
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
                # Same tick, and here because this is the only periodic hook
                # we have: Kodi changes skin without a restart, and POV's
                # seasons view id means a different layout in each skin.
                #
                # _skip_pov_patchers() is checked HERE, not only on the
                # startup pass. This is the one job in this loop that writes
                # inside POV's own profile, and the switch exists so somebody
                # can rule this build out in one step -- a thread that carries
                # on writing to POV ninety seconds later would make the switch
                # a lie.
                # Deliberately in the keeper and not in the startup steps:
                # it can put a dialog on screen, and boot is already crowded
                # with them. By the first tick the splash is long gone.
                if umbrella_watch_prompt is not None:
                    try:
                        umbrella_watch_prompt.maybe_ask_async()
                    except Exception:
                        pass
                if pov_seasons_view_seed is not None:
                    try:
                        # Inside the try, not in the `if`: this is the only
                        # call in the loop body that sat outside one, and an
                        # exception here would take the whole keeper thread
                        # down -- both mirrors with it -- for the rest of the
                        # session, through an outer catch that logs nothing.
                        if not _skip_pov_patchers():
                            pov_seasons_view_seed.ensure_seeded()
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
