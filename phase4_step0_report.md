# Phase 4, Step 0: classification report

Read-only analysis. No repo files were modified while producing this report.

**Verdict legend**

| Verdict | Meaning |
|---|---|
| **A** | Runtime injection into code of an add-on we do not ship. Stays or becomes a registry entry in the modern format. |
| **B** | Targets a skin shipped in this repo (Estuary, FENtastic, NOX, Onyx). Pre-bake into the source, wrapped in visible markers. |
| **B-ud** | A static value that could ship in `userdata/` instead. Existing devices still need a one-time migration. |
| **C** | Not a patch (utility, seed, sync, diagnostic, feature, or possible redundancy). Subtypes are in section 3. |
| **D** | Needs a local POV / Umbrella / Idan Plus / AF3 excerpt to fix the exact anchors. Always combined with A. |

## 0. Headline findings

1. **The registry has 127 entries, not 122.** The 122 came from counting entries that carry a `category` field.
   - By host: POV 119, Umbrella 5, Idan Plus 3. None of these add-ons is in this repo, so **no registry entry is Category B**. Every entry is Category A.
   - Ten entries are tagged `[NEEDS_MODULE_CHECK]` (D). The file mentions the tag 11 times.
   - 25 entries are inline hooks with no logic module, which goes against README section 6 principle 2 (lean hooks). They are marked `INL`.
2. **`host_repairs/` holds 45 modules**, plus `flow/` (7 files, 2,565 lines) and `media_assets/fonts`.

| Verdict | Count |
|---|---|
| A | 12 |
| B | 8 |
| B-ud | 4 |
| C | 21 |

   - AF3 (`skin.arctic.fuse.3`) is not in this repo; it installs from `repository.jurialmunkey`. It cannot be pre-baked, so its patchers stay runtime (A).
   - Skin patchers for the four repo skins become B.
3. **Part of the skin patching has already been baked into the repo.** Evidence from a grep of the repo skins:
   - NOX already uses `build_next_episode` and has no `next=1` left.
   - NOX already carries `autoplay=false` in its change-source button.
   - FENtastic's `DialogSubtitles.xml` already reads `subs.player_filename`.
   - Estuary's and NOX's `DialogSubtitles.xml` still contain stock `Player.Filename`.
   - FENtastic's OSD files still use `ActivateWindow(subtitlesearch)`.
   - So some patchers (probably `nox_next_episode`, `nox_change_source`, `skin_dialog_subtitles`) are likely no-ops on the repo skins already. This is evidence, not a verdict. A byte-diff against the pristine-to-patched output in the skin step will prove it.
4. **The engine can patch non-Python files, with one limit.**
   - The compile check runs only for `.py` targets (`patch_engine.py:436`).
   - The block markers are `# --- BEGIN PATCH ID ---`, which is invalid inside XML.
   - Moving the three AF3 XML patchers into the registry therefore needs an XML-safe marker form. That is a marker syntax, not a new action, and it still needs the owner's decision (D-2).
5. **Redundancy found while reading:**
   - `pov_settings_read.py` (231 lines) has no importer anywhere in `libs/`.
   - `pov_widget_crash_guard` (85 lines) is covered by `pov_language_invoker_guard`. Both fix the same crash and the second removes the root cause.
   - `pov_language_invoker_guard.py` and the frozen `patches/pov_invoker_safe.py` are two implementations of the same job.
   - `pov_reload.py` (1,195 lines) and `source_settings_reload.py` (316 lines) are the same disable/enable cycle written twice.
   - `userdata/advancedsettings.xml` still ships `<imageres>9999</imageres>`, and `kodi_32bit_artwork` rewrites it to 720 on every device. That is a candidate to bake instead of migrate.
   - `startup_pass.py` notes a 0.25 s floor per step across 123 steps. Every step converted or deleted shortens the boot pass.

## 1. Registry: all 127 entries (all Category A)

**Columns**
- **Host:** P = POV, U = Umbrella, I = Idan Plus.
- **Flags:** `LEG` = legacy format (hand-written hook, no description / category / priority). `D` = needs excerpts. `INL` = inline logic in the hook, no module. `OFF` = disabled. `opt` = `optional: True`.

| # | id | Host | target (under `resources/lib/`) | category | flags |
|---|---|---|---|---|---|
| 1 | pov_rtl_rows | P | router.py | - | LEG opt |
| 2 | idanplus_rtl_rows | I | default.py | - | LEG opt |
| 3 | umbrella_rtl_rows | U | umbrella.py | - | LEG opt |
| 4 | pov_logo_language | P | indexers/metadata.py | - | LEG opt |
| 5 | pov_logo_for_ui | P | indexers/metadata.py | - | LEG opt |
| 6 | pov_directory_clock | P | modules/entry.py | diagnostics | D INL |
| 7 | pov_directory_latency | P | modules/entry.py | diagnostics | D |
| 8 | pov_list_snapshot_early | P | router.py | performance | |
| 9 | pov_play_guard | P | router.py | ux | |
| 10 | pov_play_guard_end | P | router.py | ux | |
| 11 | pov_linear_tv_history | P | caches/watched_cache.py | ux | |
| 12 | idanplus_child_source | I | default.py | parental_control | |
| 13 | idanplus_list_snapshot | I | default.py | performance | |
| 14 | pov_profile_movie_metadata | P | menus/movies.py | parental_control | |
| 15 | pov_profile_tv_metadata | P | menus/tvshows.py | parental_control | |
| 16 | pov_profile_episode_metadata | P | indexers/metadata.py | parental_control | |
| 17 | umbrella_profile_metadata | U | modules/control.py | parental_control | |
| 18 | pov_profile_directory_guard | P | modules/entry.py | parental_control | |
| 19 | pov_profile_route_guard | P | modules/entry.py | parental_control | |
| 20 | pov_profile_play_guard | P | modules/player.py | parental_control | |
| 21 | umbrella_profile_directory_guard | U | modules/control.py | parental_control | |
| 22 | umbrella_profile_route_guard | U | modules/router.py | parental_control | |
| 23 | umbrella_profile_play_guard | U | modules/player.py | parental_control | |
| 24 | pov_merged_personal_pagination | P | modules/utils.py | ux | INL |
| 25 | pov_trakt_personal_newest | P | indexers/trakt_api.py | ux | |
| 26 | pov_mdblist_personal_newest | P | indexers/mdblist_api.py | ux | |
| 27 | pov_hebrew_interface | P | modules/kodi_utils.py | translation | D |
| 28 | pov_local_favorites_recent | P | indexers/local_api.py | ux | INL |
| 29 | pov_cache_empty_prevention | P | caches/main_cache.py | reliability | |
| 30 | pov_debrid_unbound_guard | P | modules/debrid.py | crash_guard | INL |
| 31 | wizard_pov_visibility_mgr_navigator | P | menus/navigator.py | ux | |
| 32 | pov_genre_icons_rewrite | P | menus/navigator.py | ui | |
| 33 | pov_shortcut_absolute_paths | P | menus/navigator.py | ui | INL |
| 34 | pov_shortcut_fanart_shadow | P | menus/navigator.py | ui | INL |
| 35 | pov_anime_trakt_strings | P | menus/navigator.py | translation | INL |
| 36 | pov_anime_years_breadcrumbs | P | menus/navigator.py | translation | INL |
| 37 | pov_anime_genres_breadcrumbs | P | menus/navigator.py | translation | INL |
| 38 | pov_genre_dict_main_loop_translation | P | menus/navigator.py | translation | |
| 39 | pov_genre_dict_multiselect_translation | P | menus/navigator.py | translation | |
| 40 | pov_tmdb_timeout_widen | P | indexers/tmdb_api.py | reliability | INL |
| 41 | pov_movie_networks_providers_fix | P | indexers/tmdb_api.py | ux | D INL |
| 42 | pov_movie_meta_blank_guard | P | indexers/metadata.py | reliability | D |
| 43 | pov_tvshow_meta_blank_guard | P | indexers/metadata.py | reliability | D |
| 44 | pov_repeat_timer_resiliency | P | modules/myservices.py | crash_guard | INL |
| 45 | pov_acctmgr_integration | P | modules/myservices.py | account_manager | |
| 46 | pov_torbox_api_user_stats | P | indexers/torbox_api.py | account_manager | INL |
| 47 | pov_torbox_usage_ui | P | debrids/tb_cloud.py | account_manager | |
| 48 | pov_trakt_empty_cache_fix | P | caches/trakt_cache.py | reliability | |
| 49 | pov_trakt_table_clear_guard | P | caches/trakt_cache.py | reliability | INL |
| 50 | wizard_pov_view_mode_fix | P | modules/kodi_utils.py | ui | |
| 51 | pov_addon_window_import | P | modules/kodi_utils.py | crash_guard | |
| 52 | wizard_pov_combined_discover | P | menus/tmdb.py | ux | module needs POV's `indexers` |
| 53 | pov_network_id_fix | P | menus/movies.py | ux | INL |
| 54 | wizard_pov_my_lists_movies | P | menus/movies.py | ux | |
| 55 | wizard_pov_my_lists_tvshows | P | menus/tvshows.py | ux | |
| 56 | pov_debrid_status | P | service.py | ui | |
| 57 | pov_custom_debrid_toasts | P | service.py | ui | |
| 58 | wizard_fav_refresh_manage | P | indexers/list_helper.py | ux | |
| 59 | wizard_fav_refresh_dialog | P | modules/dialogs.py | ux | INL |
| 60 | pov_ad_unbound_guard | P | indexers/alldebrid_api.py | crash_guard | |
| 61 | pov_rd_unbound_guard | P | indexers/realdebrid_api.py | crash_guard | |
| 62 | pov_tb_unbound_guard | P | indexers/torbox_api.py | crash_guard | |
| 63 | pov_torbox_url_fix_indexers | P | indexers/torbox_api.py | crash_guard | |
| 64 | pov_ad_error_log | P | indexers/alldebrid_api.py | diagnostics | |
| 65 | pov_tb_error_log | P | indexers/torbox_api.py | diagnostics | |
| 66 | pov_pm_error_log | P | indexers/premiumize_api.py | diagnostics | |
| 67 | pov_debrid_timeout | P | modules/sources.py | reliability | |
| 68 | pov_provider_rank | P | modules/sources.py | crash_guard | INL |
| 69 | pov_heb_prewarm | P | modules/sources.py | subtitles | |
| 70 | pov_playback_capture | P | modules/sources.py | ux | |
| 71 | pov_reorder_sources | P | modules/sources.py | ux | |
| 72 | pov_legacy_scrapers | P | modules/sources.py | compatibility | OFF |
| 73 | pov_resolve_diag | P | modules/debrid.py | diagnostics | |
| 74 | pov_debrid_error_guard | P | modules/debrid.py | reliability | |
| 75 | pov_trakt_reauth_recovery | P | indexers/trakt_api.py | account_manager | |
| 76 | pov_shared_http_tmdb | P | indexers/tmdb_api.py | performance | D |
| 77 | pov_shared_http_trakt | P | indexers/trakt_api.py | performance | |
| 78 | pov_http_lazy_tmdb | P | indexers/tmdb_api.py | performance | D |
| 79 | pov_http_lazy_trakt | P | indexers/trakt_api.py | performance | D |
| 80 | pov_source_quality_fix | P | windows/sources.py | ui | |
| 81 | pov_subtitle_match_percent | P | windows/sources.py | subtitles | |
| 82 | pov_subtitle_match_refresh | P | windows/sources.py | subtitles | |
| 83 | pov_navigator_read_fix | P | caches/navigator_cache.py | reliability | D |
| 84 | pov_aiostreams_credentials_fix | P | modules/settings.py | crash_guard | |
| 85 | mdblist_api_redact_and_reauth_prep | P | indexers/mdblist_api.py | account_manager | INL |
| 86 | mdblist_api_reauth_retry | P | indexers/mdblist_api.py | account_manager | |
| 87 | mdblist_expiry_auth_mode_guard | P | indexers/mdblist_api.py | account_manager | |
| 88 | mdblist_cache_verified_reads | P | caches/mdbl_cache.py | reliability | |
| 89 | mdblist_response_pagination | P | indexers/mdblist_api.py | ux | |
| 90 | mdblist_collection_parent_projection | P | indexers/mdblist_api.py | ux | |
| 91 | mdblist_complete_pagination | P | indexers/mdblist_api.py | ux | |
| 92 | mdblist_manager_read_failure | P | menus/mdblist.py | crash_guard | |
| 93 | mdblist_api_scrobble_stop | P | indexers/mdblist_api.py | ux | |
| 94 | mdblist_activity_failed_read | P | indexers/mdblist_api.py | crash_guard | INL |
| 95 | mdblist_api_add_to_list_guard | P | indexers/mdblist_api.py | crash_guard | INL |
| 96 | mdblist_api_add_to_coll_guard | P | indexers/mdblist_api.py | crash_guard | INL |
| 97 | mdblist_api_sync_guard | P | indexers/mdblist_api.py | crash_guard | INL |
| 98 | mdblist_api_like_routes | P | indexers/mdblist_api.py | ux | |
| 99 | mdblist_api_account_heal | P | indexers/mdblist_api.py | account_manager | |
| 100 | mdblist_menu_manager_watchlist | P | menus/mdblist.py | crash_guard | INL |
| 101 | mdblist_menu_pre_search | P | menus/mdblist.py | ux | |
| 102 | mdblist_menu_like_append | P | menus/mdblist.py | ux | |
| 103 | pov_mdblist_sort_default | P | modules/settings.py | ux | |
| 104 | pov_widget_budget_movies | P | menus/movies.py | performance | |
| 105 | pov_widget_budget_tvshows | P | menus/tvshows.py | performance | |
| 106 | pov_next_episode_widget_budget | P | menus/episodes.py | performance | |
| 107 | pov_bookmark_refresh_order | P | caches/watched_cache.py | ux | INL |
| 108 | pov_watched_lazy_mock | P | caches/watched_cache.py | performance | |
| 109 | pov_watched_lazy_proxy | P | caches/watched_cache.py | performance | |
| 110 | pov_next_episode_constructor | P | menus/episodes.py | performance | |
| 111 | pov_indexed_next_history | P | caches/watched_cache.py | performance | |
| 112 | pov_tmdb_stable_list_actions | P | menus/tmdb.py | ux | |
| 113 | pov_trakt_stable_list_actions | P | menus/trakt.py | ux | |
| 114 | pov_persistent_continue_exclusions | P | caches/watched_cache.py | ux | |
| 115 | pov_continuation_service_prefetch | P | modules/entry.py | performance | |
| 116 | pov_continuation_extra_sources | P | menus/episodes.py | ux | |
| 117 | pov_episode_focused_info | P | menus/episodes.py | ui | |
| 118 | pov_continuation_episode_actions | P | menus/episodes.py | ux | |
| 119 | pov_continuation_show_actions | P | menus/tvshows.py | ux | |
| 120 | pov_continuation_show_sources | P | menus/tvshows.py | ux | |
| 121 | pov_continuation_season_picker | P | caches/watched_cache.py | ux | |
| 122 | pov_continuation_local_toggle | P | modules/dialogs.py | ux | |
| 123 | pov_debrid_cache_upsert | P | caches/debrid_cache.py | crash_guard | INL |
| 124 | pov_trakt_continue_snapshot_invalidation | P | caches/trakt_cache.py | ux | |
| 125 | pov_trakt_verified_continue_toggle | P | indexers/trakt_api.py | ux | |
| 126 | pov_mdbl_continue_snapshot_invalidation | P | caches/mdbl_cache.py | ux | |
| 127 | pov_mdbl_verified_continue_toggle | P | indexers/mdblist_api.py | ux | |

Category counts (field `category`): ux 39, crash_guard 16, performance 14, parental_control 11, reliability 10, ui 8, account_manager 8, diagnostics 6, translation 6, none (legacy) 5, subtitles 3, compatibility 1.

**Registry observations (no action taken)**
- **Legacy entries (#1-5):** their hooks swallow every error silently, while the v2 helper logs errors. That is a behavior difference, so both versions must be shown to the owner before converting.
- **#72** (`pov_legacy_scrapers`) is disabled, but #84 still imports the `pov_legacy_scrapers` module.
- **Parental entries (#12, #14-23):** category and `patch_dir=GUARD_DIR` stay as they are.
- **Ordering:** the entries are already mostly contiguous by domain (for example #85-103 are MDBList and #107-127 are continue-watching). This matters for the split proposal in section 5.

## 2. Host repairs: Category A, B and B-ud (22 modules)

| Module (lines) | What it does | Target | Mechanism | Verdict | Notes |
|---|---|---|---|---|---|
| idanplus_youtube_id_patcher (310) | Idan Plus sends YouTube the word "watch" instead of a video id | Idan `common.py` | `.replace` | **A+D** | One additive hook in `GetYouTube` should do it. |
| idanplus_vod_patcher (491) | Kan VOD stopped playing (Cloudflare 403 on www.kan.org.il); archive and i24NEWS English empty | Idan `common.py`, `kan.py` | `.replace` x3 | **A+D** | Hardest conversion: reroutes a fetch host and fixes page handling. Needs excerpts. |
| idanplus_channels_patcher (272) | Heals a corrupt `displayChannels.json` and hardens `GetChannels` | Idan `common.py` + user data file | `.replace` + data heal | **A+C** | The `common.py` guard becomes a patch. The data-file heal stays a repair. |
| umbrella_tmdb_apikey_patcher (231) | Hebrew search never sends the request (the `api_key` `%s` is mangled) | Umbrella `indexers/tmdb.py` | `.replace` x3 | **A+D** | Needs the `url % self.API_key` excerpt. |
| umbrella_mdblist_token_patcher (225) | Umbrella sends a stale MDBList token and nags | Umbrella `modules/mdblist.py` | `.replace` x9, `re.sub` | **A+D** | |
| umbrella_mdblist_sync_patcher (501) | MDBList watched-history sync loses episodes | Umbrella `modules/mdblist.py` | `.replace` x5 | **A+D** | |
| umbrella_source_ux_patcher (228) | Warms the subtitle match at scrape start; quiet cancel | Umbrella `modules/sources.py` | `.replace` x2 | **A+D** | POV already has the same idea in `pov_heb_prewarm` (#69). |
| umbrella_subtitle_match_patcher (295) | Hebrew match % in the source-results window | Umbrella `windows/source_results.py` | `.replace` x2 | **A+D** | A port of #81 / #82. |
| umbrella_setup_patcher (610) | (1) wires CocoScrapers as external provider via settings; (2) patches favourites, movies, tvshows and sources | Umbrella settings and py files | `.replace` x7, settings | **A+C** | Split: the settings half is C, the code half is A. |
| af3_search_pov_patcher (292) | Repoints AF3 search rows at POV | AF3 `search_path.xml` | `.replace` | **A+D** | AF3 is external. Needs the XML-marker decision (D-2). |
| af3_discover_pov_patcher (283) | Repoints AF3's discover grid at POV | AF3 `Custom_1105_Search.xml`, `Includes_Search.xml` | `.replace` x4 | **A+D** | Same. |
| af3_dialog_subtitles_patcher (176) | AF3 subtitle dialog header uses `subs.player_filename` | AF3 `Dialog_DialogSubtitles.xml` | `.replace` | **A+D** | Same. |
| change_source_pause_patcher (155) | Change-source button pauses the video first | Estuary, FENtastic, NOX | `.replace` x2 | **B** | Repo skins. |
| choose_subs_rewire_patcher (168) | Subtitle button opens the MoranSubs chooser | Estuary, FENtastic, NOX (Onyx to check) | `.replace` x3 | **B** | "All_Subs" is already gone from all four skins, but `ActivateWindow(subtitlesearch)` remains in FENtastic's OSD. |
| nox_change_source_patcher (195) | Adds a change-source button to NOX's OSD | NOX `VideoOSD.xml`, `Includes_POVIL_NativePlayer.xml` | `.replace` x3 | **B** | Likely already baked (`autoplay=false` present). |
| nox_choose_subs_patcher (110) | Repoints NOX button 70046 to the chooser | NOX | `.replace` x3 | **B** | |
| nox_next_episode_patcher (108) | Fixes the NOX next-episode button | NOX | `.replace` x2 | **B** | Likely already baked (no `next=1` left). |
| nox_osd_collision_patcher (176) | Shrinks OSD buttons so they do not collide | NOX | `.replace` x3 | **B** | |
| skin_dialog_subtitles_patcher (259) | Dialog header reads `subs.player_filename` | FENtastic, Zephyr 2 and any other skin | `.replace` x3 | **B+A** | FENtastic is already baked. Estuary and NOX (also in repo) are still stock. Skins we do not ship (Zephyr) stay runtime. |
| skin_dialog_subtitles_row_patcher (259) | Row layout so long release names do not clip | Active skin's `DialogSubtitles.xml` | `re.sub` x2 | **B+A** | Same split. |
| kodi_32bit_artwork (134) | `imageres` 9999 to 720 | `advancedsettings.xml` | `.replace` | **B-ud** | `userdata/advancedsettings.xml` still ships 9999. |
| kodi_playlist_timeout_patcher (220) | Raises `playlisttimeout` so backing out of a source list does not show "playback failed" | `advancedsettings.xml` | `.replace` | **B-ud** | Not present in the shipped file. |
| pov_scraper_settings_patcher (362) | One-time POV scraper settings (CAM/3D on, piratebay off) | POV settings | `set_setting` | **B-ud** | The shipped POV `settings.xml` already holds these keys. This is also where `_pov_scraper_tune_state` is written (undeclared, Kodi drops it). |
| pov_seasons_view_seed (292) | Seeds the season-list view so posters draw | Skin views / POV views | settings | **B-ud** | Verify against the shipped `views.db`. |

## 3. Host repairs: Category C, detailed (21 modules)

Subtypes: **C-util** shared helper; **C-seed** seeds data or config (pre-bake candidate); **C-asset** drops an additive file; **C-sync** keeps two add-ons in step; **C-diag** reporting; **C-feature** user-facing flow; **DEL?** possibly redundant.

| Module (lines) | Subtype | What it really does | Callers | Over-engineering / redundancy assessment | Suggested fate |
|---|---|---|---|---|---|
| kodi_utils (230) | C-util | Subset copy of the subtitle add-on's `kodi_utils` used by moved build code. | ~60 modules | Needed; the subtitle boundary requires it. | Keep. |
| addon_presence (112) | C-util | "Is add-on installed?" without Kodi logging an error for a missing one. | 7 | About 100 of the 112 lines are explanation. Fine. | Keep; move to a utilities folder. |
| addon_settings_safe (234) | C-util | Writes another add-on's settings without Kodi re-serialising and resetting unrelated values. | 7 | Solves a real Kodi behavior. Justified. | Keep. |
| addon_settings_integrity (508) | C-diag | Keeps a last-known-good copy of POV, Umbrella and Coco `settings.xml`; restores a torn one. | `pov_steps` | Large for a rare event. Decide whether the failure is still seen in the field. | Keep or shrink. |
| pov_reload (1195) | C-util | Disables then enables POV via JSON-RPC when idle so it re-imports patched files (25 loop / timer uses). | 7 | Largest in the group. Still needed after patches move to the registry, because a patch needs a reload to take effect. | Keep; candidate to shrink. |
| source_settings_reload (316) | C-util | The same cycle for Umbrella and Coco after a settings repair. | 2 | Duplicates `pov_reload`'s idea. | Merge into `pov_reload`. |
| patcher_health (681) | C-diag | Reports which repairs are applied; knows `_pov_patching_off`. | `startup_pass` | After conversion, `PatchEngine().run()` stats already say this. | Rewrite on engine stats; large reduction. |
| search_provider (277) | C-feature | POV vs Umbrella search switch, used by the router. | 6 | The Onyx skin already reads the setting directly in `Home.xml`; AF3 still goes through the patchers. | Keep. |
| af3_first_launch (191) | C-feature | One-time dialog offering POV's Connect Services on first AF3 launch. | `af3_steps` | Reasonable. | Keep. |
| af3_home_patcher (1563) | C-seed | Writes script.skinvariables node files so AF3's home has POV widgets. | `af3_steps` + 3 more | The biggest file in `host_repairs/`. It seeds data, not code. Static nodes could ship in `userdata/` if AF3 is provisioned. | Review in the AF3 sub-step; candidate for a large reduction. |
| legacy_lang_mirror (162) | C-asset | Copies `English/strings.po` into `resource.language.en_gb/` of an add-on. | 3 | Additive file drop; a static copy would do. | Keep or pre-bake. |
| umbrella_hebrew_ui_patcher (555) | C-asset | Writes a partial `he_il` translation folder for Umbrella. | `umbrella_steps` | Additive; mostly translation data. | Keep; move the data to a file. |
| umbrella_language_patcher (277) | C-seed | Settings plus language wiring for Umbrella. | `umbrella_steps` | Overlaps `legacy_lang_mirror`. | Merge candidate. |
| umbrella_watch_source (204) | C-seed | Chooses which service Umbrella reads watched state from (MDBList, then Trakt). | 3 | Policy logic specific to this build. | Keep. |
| umbrella_watch_prompt (242) | C-feature | Asks the user once when a service is connected but unused. | `umbrella_steps` | Careful design; the comments record that three inference attempts failed. | Keep. |
| mdblist_umbrella_mirror (230) | C-sync | One MDBList authorisation for both add-ons. | `trakt_umbrella_mirror` + 2 | A feature, not a patch. | Keep. |
| trakt_umbrella_mirror (337) | C-sync | One Trakt authorisation for POV and Umbrella. | `umbrella_steps` | Same. | Keep. |
| update_nag_patcher (159) | C-seed | Turns off the update-nag toasts of pinned Umbrella and Account Manager. | `pov_steps` | A settings write, not a patch. Could be baked into the shipped add-on settings. | Pre-bake candidate. |
| pov_language_invoker_guard (540) | C (special) | Turns POV's `reuselanguageinvoker` off in the setting and in POV's `addon.xml`. | `startup_pass` | Duplicates the frozen `patches/pov_invoker_safe.py`. Clock-bound (the open invoker note in `CLAUDE.md`). | Handle in the invoker investigation. |
| pov_widget_crash_guard (85) | DEL? | Turns `trakt.sync_refresh_widgets` off. | `pov_steps`, `pov_language_invoker_guard` | Same root cause as the invoker guard, which removes it. | Likely delete. |
| pov_settings_read (231) | DEL? | Reads a POV setting from the file instead of Kodi's in-memory copy. | none found | Dead as far as a repo-wide grep can see. | Verify, then delete. |

## 4. `flow/` (orchestration, 2,565 lines)

| File | Lines | Role | After conversion |
|---|---|---|---|
| startup_pass.py | 361 | Runs 123 steps with a 0.25 s floor each | Shrinks as steps are converted or deleted |
| runner.py | 248 | Starts the repair runner | Stays |
| skin_steps.py | 633 | Skin patch steps | Mostly disappears after pre-baking |
| pov_steps.py | 490 | POV steps | Shrinks (`pov_scraper_settings`, `update_nag`, `widget_crash_guard`) |
| umbrella_steps.py | 407 | Umbrella steps | Shrinks |
| af3_steps.py | 214 | AF3 steps | Stays until the AF3 decision |
| idanplus_steps.py | 107 | Idan Plus steps | Shrinks |
| common.py | 91 | Helpers | Stays |
| tonight_steps.py | 14 | Tonight step | Stays |

## 5. Minimal-split proposal for `patches_config.py`

The file is 2,548 lines. The registry itself is lines 109-2461 (about 2,350 lines); the rest is helpers and the validator.

- **Option 1 (recommended): one file with large banners.**
  - Entries stay in `patches_config.py`. Banners such as `# ======= NAVIGATION =======` go between the existing domain blocks.
  - Ordering is the one risk. Within a single `(target_file, anchor)` group the relative order must not change. This would be checked mechanically, and any entry that has to move between banners gets reported.
  - Likely sections: legacy / Onyx RTL, parental guards, navigator / translation, debrid / crash guards, sources and subtitles, MDBList, widget budget / performance, continue-watching, Umbrella / Idan Plus.
- **Option 2 (optional follow-up): two extra files at most.**
  - Move the helpers (`_build_patch_hook`, the hook constants, about 100 lines) and the validator (about 90 lines) out. The entry list stays whole.
  - This removes about 190 lines and does not fragment the entries.
- **Option 3 (not recommended): one extra file for the 25 `INL` entries**, next to their future logic modules. It adds to the crowding of `patches/`.

## 6. Consequences for the next steps

- **Registry modernization (Step 1):** 5 legacy entries plus the 25 `INL` entries. The `INL` ones may stay inline; extracting them is optional.
- **A conversions (Step 3):** 12 host repairs, all waiting for excerpts. Everything must be an additive hook (`prepend_before` / `append_after`). No `replace`, `block_replace` or `regex_replace` action will be added to the engine.
- **B pre-baking (Step 4):** 8 skin patchers plus 4 userdata items. Pre-baked blocks must carry visible markers, for example `<!-- =========== WIZARD IL: <id> begin =========== -->`. The existing convention in the skins is checked first.
- **C (Step 5):** 21 modules, of which 2 are likely deletions and about 4 are merge or shrink candidates.
- **Deferred items that stay open:** `_pov_patching_off`, `_pov_scraper_tune_state`, `pov_fast_navigation`, the `reuselanguageinvoker` mismatch, and the `config_version` bump (currently `2.0.6`, bump at release).

## 7. Decisions needed from the owner

1. **D-1:** Which split option for `patches_config.py` (1, 2 or neither)?
2. **D-2:** May the AF3 XML patches go into the registry? That needs only an XML-safe marker form, not a new action. If not, they stay in `host_repairs`.
3. **D-3:** Verify in the next step whether the likely-already-baked patchers (NOX next-episode, NOX change-source, FENtastic dialog subtitles) are real no-ops, with deletion as the follow-up?
4. **D-4:** For the C items, start with the two likely deletions (`pov_settings_read`, `pov_widget_crash_guard`), or collect everything and decide in one pass?
