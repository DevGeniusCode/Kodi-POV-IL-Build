# KODI BUILD REFACTORING: DOMAIN-DRIVEN RESTRUCTURING

## 🎯 THE GOAL (AI CONTEXT OPTIMIZATION)

Enable isolated feature development: small, domain-specific files, so a future task loads 1-2 files instead of the whole
project. Smaller files = safer, cheaper, more accurate AI coding.

## ✅ STATUS

Domain folders inside `plugin.program.kodipovilwizard/resources/libs/` (the full map is `README.md` §11):

| Folder           | Responsibility                                                                         |
|------------------|----------------------------------------------------------------------------------------|
| `boot/`          | `startup.py` helpers: GUI-ready waits, first-boot stabilizer, notices, build checks, housekeeping, language sync |
| `provisioning/`  | Fresh install / resume, the progress banner, the start-up OTA pass                     |
| `build/`         | `wizard_actions.Wizard`: install / GUI fix / theme / quick update / restart            |
| `skins/`         | On-demand skins, Switch Skin, AF3 tools, favourites sync, `skin_settings`, `fentastic_manager`, `onyx_home/`, `search_provider_picker` |
| `addons/`        | Add-on install / management (Umbrella, `debrid_notice`)                                |
| `accounts_auth/` | Account Manager                                                                        |
| `system/`        | Kodi version check, speed tests                                                        |
| `host_repairs/`  | Runtime repairs of other add-ons and skins (20 patcher / repair modules, flat on purpose), `flow/` (startup-repair pass + runner), `kodi_utils` (subset copy) |
| `tonight/`       | The Tonight home tile (shortcut writer, picker window logic)                           |

- **Phase 1 COMPLETE (Nov 2026):** `startup.py` 848 → ~180 lines; `wizard.py` split into the folders above and deleted.
- **Phase 2 COMPLETE (Nov 2026):** skin decoupling. Skin code in `profiles/` (`build_skin.py`, `af3_skin.py`) stays there
  (Profiles boundary, README §9.2).
- **Phase 3 COMPLETE (Nov 2026):** `service.subtitles.kodipovilai` is now a decoupled, standalone add-on (verified: it imports and
  its service starts under the stubs with no Wizard on disk; zero Wizard references in its code). All host repairs (44 patcher
  modules + the startup-repair runner), the build's settings and state, Tonight, the search-provider picker, the debrid-notice
  dialog and every `build_mode` block were migrated to the Wizard. It talks to the Wizard only through the boundary below.
- **Phase 4 COMPLETE (Nov 2026):** the patch registry was modernized and trimmed (helpers split into `patches_registry_utils.py`;
  XML support in the engine; skin and userdata settings pre-baked into XML / DB; dead host repairs and 4 obsolete patches
  deleted; 3 orphaned patches revived). The registry stands at 130 entries (122 POV, 5 Umbrella, 3 Idan Plus). Carry-overs
  are listed in the Phase 4 section below.
- **Phase 5 IN PROGRESS:** Step 0 docs housekeeping, Step 1 POV patch audit (reference: `_reference_addons/plugin.video.pov`
  6.11.03) and its approved changes (P0 restore lost behaviour, P1 delete dead entries / modules, P2 simplify, the whole Hebrew
  `.po` merged into POV's strings table, P3 decisions) are done. Step 2 (Category C: the 17 runtime patchers of Umbrella, Idan
  Plus and AF3, reference Umbrella 6.8.02, Idan Plus 4.0.6, AF3 3.5.2) is applied: Idan Plus (3) and Umbrella (5 of its 10) became
  registry entries, Umbrella's settings are baked into `userdata` (`config_version` 2.0.8), the AF3 search patcher was rewritten
  for AF3 3.5, the AF3 touch-cleanup and autoscroll XML edits became registry entries. The registry stands at 150 entries (120
  POV, 13 Umbrella, 12 Idan Plus, 5 AF3). Runtime patchers that remain because the registry cannot express them (they REPLACE
  lines or need function-scoped anchors): `umbrella_tmdb_apikey`, `umbrella_list_order`, `af3_dialog_subtitles`, `af3_home`
  (data seeding, font) and the data copies `umbrella_hebrew_ui`, `umbrella_watch_*` (see "Architectural decision" below). The AF3
  discover-grid patcher is retired without replacement; AF3 3.5.2 + script.skinvariables 2.3.2 were re-evaluated (reference:
  `script.skinvariables-nexus`) and its native TMDbHelper discover runs untouched (see "Architectural decision: AF3 discover" below).
  OPEN: the MDBList pagination pair and `pov_mdblist_patch_logic` stay until checked against the live API; the lazy
  `watched_cache` pair is disabled pending a 32-bit measurement.
- **Phase 5, Step 3 (UI; owner-approved):** (a) the **Patch Manager** is now categorized (`gui/patch_manager.py`: add-on folders ->
  category folders for groups over 25 -> patches, bulk switches, a "General / Engine" panel) and lives in Maintenance (Hebrew
  labels); `PatchEngine.run` takes `addon_id` / `only_ids` so a toggle runs only its own patches. (b) the **Skin Manager**
  (`skins/skin_manager.py`, Maintenance) browses / installs / switches skins from one catalog (`skins/skin_catalog.py`).
  (c) **Skin policy:** `skin.fentastic` is the default (`CONFIG.DEFAULT_BUILD_SKIN`, `uservar.py`) and the ONLY skin provisioned;
  Estuary (build edition), NOX, Onyx and AF3 are on demand. See the rule "SKINS ARE ON DEMAND" below.
- **Phase 6 IN PROGRESS (Kodi testing, Nov 2026):** the first fresh-install log (Wizard 0.5.2) died in `fresh_install.finalize`: the Wizard
  called `media.install_and_verify_global_media_assets()`, which `plugin.program.orderfavourites-hebrew` never defined (the add-on only had
  `install_global_media_assets()`, whose return is the number of files written, 0 on a re-run). Fixed at the source, add-on 1.4.7
  (the wrapper returns whether `userdata/favourites.xml` exists; the Wizard callers are unchanged). Same log: `umbrella_watch_prompt._reader`
  built `xbmcaddon.Addon('plugin.video.umbrella')` bare every keeper tick, one `Unknown addon id` ERROR a minute on a device without the
  optional Umbrella; it now asks through `addon_presence` like the two mirrors. Baked POV `settings.xml`: `results.xml_style` was
  `List Contrast Default` and `subtitles.subs_action` was `2`, both invalid for POV 6.11.03 (enums `0..2` / `0..1`; POV indexes a dict with
  the style, a probable `KeyError` when the results window opens); both are now `0` (List Default / Off: subtitles come from
  `service.subtitles.kodipovilai`), `config_version` 2.0.9. The other 60 ids of that file (prowlarr, folder1-5, ed.*, imdb_*, fanart_*,
  tmdb_api, ...) do not exist in POV 6.11.03: inert leftovers of an older POV, left untouched (optional cleanup, needs approval).
  Log triage: `_reference_addons/log_digest.py` (temporary, never committed; the owner drops the full `kodi.log` there) condenses a log to
  its errors and repeats.
  Optional add-ons (Phase 6): the Wizard's main menu has one **"תוספים וסקינים אופציונליים"** screen (`mode=optional`,
  `addons/optional.py`) with Umbrella, Otaku (`addons/otaku.py`) and the on-demand skins (the Maintenance "Skin Manager" entry
  moved there; `mode=skinmanager` stays routed). Otaku is no longer in `ModularUpdater.PROVISION_IDS` (fresh install and
  `heal_missing_addons` leave it alone); its baked settings still seed. Launcher tiles of Umbrella / Otaku are gated in
  `favourites_config.json` (`"condition": "umbrella" | "otaku"`, `pov_visibility_mgr`, which now also tracks both in
  `_read_states` / the fingerprint and has the public `refresh_favourites()`); a new always-visible tile `optional_addons` opens the
  screen. orderfavourites 1.4.8, NOX 1.0.22, `config_version` 2.0.11. The `skins/` + `addons/` rule: a new optional add-on is one row of
  `optional.OPTIONAL_ADDONS`, one install module, one gated tile.
  Movies "Networks" (Phase 6): POV 6.11.03 has no watch-provider action, and the registry entry `pov_movie_networks_providers_fix`
  already turns `tmdb_movies_networks` into a `with_watch_providers` query (US, flatrate), so the ids 8 / 337 / 350 ... are provider ids on purpose.
  The movie menu reads the key `company` (`menus/movies.py` `tmdb_special_key_dict`), but the baked `navigator.db` row "סרטים - לפי רשת" still
  wrote `network_id` (NOX and Onyx already passed `company=`): POV returned an empty list and Kodi showed a failed directory. The 8 items
  now use `company` (data-only edit, the other 11 rows byte-identical, `config_version` 2.0.10). Known side effect of the existing patch:
  POV's extras window ("more from this studio" of a movie) calls the same function with a real company id and now gets provider results.
  POV context menu (Phase 6): the list-manager entries (Trakt, MDBList, TMDb lists; "ניהול רשימות") of movie and show cards are hidden for a service
  that is not connected (registry `pov_cm_hide_unconnected_movies` / `_tvshows`: an insert after `self.cm_sort = settings.context_menu_sort()` that sets the
  sort key of the entry to 0 when `trakt.token` / `mdblist.token` / `tmdb.token` is empty; POV drops keys of 0). Episode cards already gate theirs on `watched_indicators`.
  Continue watching (`povil_dropped_<provider>.json`): the context entry "הסר מהמשך צפייה" (`mode=dropped_choice`) hides a show from the in-progress /
  next-episode rows through POV's local dropped list plus, with Trakt / MDBList as the indicator, the account's dropped list; the cloud list is cached in
  `addon_data/plugin.video.pov/povil_dropped_trakt.json` / `_mdblist.json` (`pov_continue_watching.py`; TTL 300 s, failed reads never replace a complete
  list, kept by `pov_keep_files`; README §6 "Continue watching").
  Stale cleanup (Phase 6): the dead NOX pack constants and `skin_packs.auto_update_active_skin_pack` (plus its `startup.py` hook; a NOX update comes from the
  modular updater) are gone, and with the Umbrella switch the whole `packs/` engine (`pack_installer`) is deleted. The baked `userdata/favourites.xml` is generated from
  `favourites_config.json` (default skin, no service connected) and its policy is `fresh: replace` / `update: seed_if_absent`; CI step
  `.github/scripts/check_favourites.py` fails on drift, `--write` regenerates it. `config_version` 2.0.12.
  Umbrella from its official repositories (Phase 6; the manifest / `ensure_repositories` mechanics below are SUPERSEDED by Phase 7): `repository.umbrella` (2.2.6; its `<dir>` blocks are listed omega first because the headless installer indexes the
  first usable block) and `repository.cocoscrapers` (1.0.1) are add-on folders of this repo, so the manifest ships them (a fresh install has them, like `repository.otaku`);
  `addons/umbrella.py` `ensure_repositories()` installs them from the manifest on a device that predates them, then `post_install_provisioning(ids=[umbrella, cocoscrapers])`
  resolves both from the developers' indexes (current versions: 6.8.02 / 1.0.45 at the time of the switch, the old pack had 6.7.85 / 1.0.39). The pack URL, the
  auto-install-for-everyone function (no callers) and the `umbrella_auto` setting are gone.
  Skin Manager installs (Phase 6; superseded by Phase 7, every skin now installs natively through `skin_packs.ensure_repository_skin`): `skin_packs.ensure_modular_skin` (NOX, Onyx, Estuary) called two `ModularUpdater` methods that no longer exist, so every install failed;
  it now runs `HeadlessInstaller.install_jobs([manifest entry])` (sha256-checked) + `_register_and_enable` + `build_skin.enable_skin`.
  First-boot stall (Phase 6, found in a Shield log): the start-up `kodi_version_update_check()` showed a blocking `dialog.ok` on every boot for a device on an older Kodi (a 39 s
  stall of the startup thread). The automatic check now runs in a daemon thread and shows a non-modal notification once per newer version (hidden setting `kodi_update_notified`);
  the menu's "check now" (`kodi_version_update_check_manual=true`) keeps its dialogs and runs inline.
  POV invoker popup (Phase 6): registry `pov_invoker_mismatch_silent` (`prepend_before` `if refresh and kodi_utils.confirm_dialog(...)` in POV's `entry.py`
  sets `refresh = False`): POV still rewrites addon.xml from the setting but no longer asks, in English, for a profile reload (the value applies at the next
  start). A POV update erases that patch too, so `host_repairs/flow/addon_update_watch.py` (Phase 7: renamed, now for every patched add-on; the runner's idle loop, every 8 s) stamps POV's addon.xml
  (version, mtime); on a change it waits until the stamp is stable, then runs `_maybe_patch_pov_language_invoker` and `PatchEngine().run(addon_id='plugin.video.pov')`
  (it stands down when POV patching is off). A narrow window remains: POV's own check can run before the watcher re-patches, and then shows its dialog once.
  Hebrew titles keep the title, not an English logo (Phase 6): `pov_logo_for_ui` (marker v3) now calls `pov_logo_lang.apply(art, meta, clearlogo)` in POV's
  `art_infodict`: when the title is Hebrew and the only logo is not (`for_ui` returns ''), `clearlogo`, `tvshow.clearlogo` and `povil_logo` are all empty, so every
  skin that shows the title when the clearlogo is empty (FENtastic, NOX, AF3, Estuary, Onyx) shows the Hebrew title. Not covered: the season / episode lists
  (`menus/seasons.py` builds its own art) and POV's own windows (`meta['clearlogo']`). Kodi check: a Hebrew title with an English-only logo shows text in each skin.
  Clean reinstall (Phase 6): Maintenance -> "התקנה מחדש של הבילד נקי" (`mode=clean_reinstall`, `provisioning/clean_reinstall.py`; README §3.B): pre-flight
  (manifest + every download answers) before any deletion, keep choices (Account Manager + POV, Umbrella, `profiles.xml`; none pre-selected), two confirmations,
  `install.wipe()` with the keep flags set in memory only, then POV leftovers / stale Addons DB rows / the Wizard's retained pack, settings and `kodipovil.*`
  markers are reset, `fresh_install.begin()` and the hard kill. The skin is not switched first (`skin_to_default` is a no-op by owner decision) and Otaku data has no keep
  option (no backup mechanism exists for it). Not tested in Kodi: it deletes the whole install.
  Local artwork (Phase 6, works offline): the Optional add-ons and Skin Manager screens pass `fanart=CONFIG.SKINS_BG` (`resources/media/skins_bg.jpg`, a copy of
  `profile-ui/background.jpg`; `directory._add_menu_item` now also sets the `fanart` art type), and the "existing skins" notice (`switcher`, `boot/notifications`)
  and `check.check_build` use `CONFIG.BUILD_SKIN_SWITCH_IMAGE` (`profile-ui/logo.png`); the remote `BUILD_SKIN_SWITCH_IMAGE_URL` is gone. (`addon.xml` now declares
  `<fanart>fanart.jpg</fanart>`, the file that exists; it used to name a missing `fanart.png`.)

- **Phase 7 IN PROGRESS (repository migration, Nov 2026; owner-approved HARD CUT: no users, no backward compatibility):** the build is
  now served as two standard Kodi repositories on GitHub Pages and **Kodi installs and updates every add-on**.
  `.github/scripts/gen_repo.py` (`deploy-repo.yml`, the WHOLE site on every run, `actions/deploy-pages`) writes `addons.xml` +
  `.sha256`/`.md5`, `zips/<id>/<id>-<ver>.zip` + `.sha256`, `<id>-latest.zip` + `.sha256`, `index.html` and `config/config.json` +
  the userdata zip; `check_upstreams.py` (`upstream-health.yml`, daily) watches the third-party indexes. `repository.povil` = OUR
  index, exactly ONE `<dir>` (`verify="sha256"`, `<hashes>sha256</hashes>`); `repository.povil.upstream` = the third-party `<dir>`s
  (kodifitzwell, Fishenzon, Umbrella nexus/omega, CocoScrapers, Otaku, jurialmunkey nexusrepo/omega). Kodi source facts
  (`xbmc/addons/Repository.cpp`): a failing `<dir>` fails the WHOLE repository (hence the split); a `<dir>` without `<checksum>` is
  never fetched; a checksum is verified only with `verify=`; the highest version of an id wins across `<dir>`s.
  The Wizard keeps STATE only (`resources/libs/provisioning/`): `provisioner.py` (id lists, `.provisioned` marker, `install_native`
  = Kodi's `InstallAddon` + the dependency-dialog confirmer, `provision`, `run_fresh_install`, `run_update_check`,
  `heal_missing_addons`), `repo_bootstrap.py` (first run: installs both repository add-ons from the site's `-latest.zip`,
  sha256-checked; the ONLY place the Wizard extracts an add-on zip), `addon_state.py`, `build_sync.py` (start-up pass);
  `config_apply` reads `config/config.json` (`load_config_info`, `config_pending`; `apply_config_pack(info, ...)`). The Wizard does
  NOT `<import>` `repository.povil` (a standalone zip would fail on the missing dependency); `repository.povil` carries an
  `optional="true"` import of the upstream repository, which Kodi does NOT auto-install, so the Wizard installs it.
  DELETED: `ModularUpdater`, `HeadlessInstaller`, `gui/install_manager.py` (the downloader window), `manifest.json`, the release / manifest
  CI (`build-and-release.yml`, `gen_manifest.py`, `list_changed.py`), the 7 third-party `repository.*` folders and `THIRD_PARTY_REPOS`.
  NO THIRD-PARTY CODE IS STORED HERE (owner rule): Account Manager (`script.module.acctmgr` 1.2.1a, `script.module.acctvwr` 1.1.6a) comes from
  Zaxxon709 through `FILTERED_UPSTREAMS` in `gen_repo.py`: the build fetches their index (retries; falls back to the copy of the previous deploy
  read from our own site; then to an empty index), keeps only those two `<addon>`s and publishes `upstream/zaxxon709/addons.xml` + `.sha256`;
  `repository.povil.upstream` points `<info>` / `<checksum verify="sha256">` there and `<datadir>` at THEIR zip server (their index also lists
  `skin.fentastic 100.0.35b` / `script.fentastic.helper 100.6.29b`, which would replace our forks). `script.fentastic.helper` is 0.6.21.
  Install UX (Phase 7): `gui/install_progress.py` + `DialogInstallProgress.xml` (the old branded panel, restored as a pure view): stage line,
  one row per step (repos, add-ons by Hebrew name, settings, final touches), status texts, a creeping bar for the add-on being installed and the
  overall 0-100% bar (the `ProvisionProgress` value, same as the banner); the provisioner reports through `plan / item_state / item_progress /
  set_stage` (every call guarded, a window that cannot load returns `None`). Kodi's `InstallAddon` builtin is MODAL and shows its own Yes/No
  ("install X?") and progress dialogs, which sit on top; the confirmer accepts window 10100 only. Progress phases are `repos / content / config /
  finalize`. Phase 7 step 4 (done): `addon_update_watch` covers POV, Umbrella, Idan Plus and Arctic Fuse 3 (detect by `addon.xml` stamp, settle =
  whole folder + `addon.xml` stable and no Kodi install dialog, repair under one lock, ReloadSkin for an active AF3; stands down during a clean
  reinstall and a fresh install); `config_apply` waits for Kodi's installs of the add-ons it writes to (`addon_state.wait_until_settled`; a timeout
  defers the pack on an existing device); baked `userdata/sources.xml` now holds one source, our Pages URL; `config_version` 2.0.13;
  `skin_catalog.Skin.floor` removed (`source` is now `povil` / `upstream`). NOT tested in Kodi: native `InstallAddon` right after the bootstrap (the
  index may not be loaded; `install_native` retries once after `UpdateAddonRepos`), the stacking of Kodi's modal install dialog over our window,
  `System.AddonUpdateCount` as a "pending updates" signal (the label may not exist: it then reads 0), Kodi's comparison of `1.2.1a`-style versions,
  `<hashes>` / `verify` on Kodi 20, the Pages deployment. Verified mechanically: stub import of every `resources/` module (172 of 173, only
  `pov_combined_discover`), pyflakes (one old message), 76 behaviour checks (bootstrap, provisioner, window plan, settle-wait, config deferral,
  update watcher) against a local copy of the generated site.

- **SUBTITLE PHASE 0 COMPLETE (19 Nov 2026; owner decision: decouple from MoranSubs).** The last upstream merge (`update-migration`
  @ `96efe59a`, 36 commits since `77880b8`) was resolved and the add-on is now ours (rule "SUBTITLE ADD-ON: DECOUPLED" below). Taken:
  MoranSubs 0.3.75 -> 0.3.96 core (translate / subsync / sync_align / embedded_extract + new `embedded_mp4.py`, `gp_later.py`, gender
  proofread, Gemini model migration `_gemini_model_bump_v4`), FENtastic 1.0.34, Estuary 4.0.9, `script.fentastic.helper` lazy MDbList
  import. Kept ours: every `service.py` / `settings.xml` conflict (all were blocks moved to the Wizard), the 4 Wizard `host_repairs` files
  git's rename detection had patched, the whole Onyx skin (see "SKIPPED UPSTREAM FEATURES"). Dropped: `af3_osd_tap_close_patcher.py` (a
  host repair) and the `povil.tr.file` trailer guard in `autosub_service.py`. Verified: same 13 pyflakes messages, same stub-import
  result as before the merge (plus the two new modules), every file compiles. The blueprint is `ARCHITECTURE_SUBTITLES.md` (repo root);
  the next phases are Subtitle Phase 2 (core refactoring and splitting) and 3 (wiring, testing, final statistics).
- **SUBTITLE PHASE 1 COMPLETE (19 Nov 2026).** Cleanup, settings and the Deletion Report (`ARCHITECTURE_SUBTITLES.md` Part 5 / 6). Deleted: the
  DarkSubs machine-translate stack and `download_sub` of the vendored engine (`subs_engine/engine.py` 1,245 -> 542 lines), the calls into the
  missing `dark_subs_integration` / `source_memory` modules, the Wizard-leftover tables and helpers of `default.py` / `service.py`, 50 unused
  functions in all, 15 dead `settings.xml` declarations (among them the two visible DarkSubs switches) and their labels. `settings.xml` now has
  8 categories with comment blocks (General, Providers & Fetching, Matching & Display, AI Translation, Sync Engine, Community Pool, Cache,
  Advanced & Debug) and declares 7 keys the code already read (`subsync_verify` / `_probe` / `_audio` / `_autorank`, `prev_context_lines`,
  `he_match_ktuvit`, `he_embedded_report`); new label ids are 32600-32626 (en_gb and he_il). No id, type,
  default, level or dependency of a surviving setting changed. Verified: stub import 72 of 72, pyflakes 15 -> 7,
  vulture 102 -> 64, the public surface byte-identical. Left open for the owner: `subs_engine/srt.py` (no importer; its MIT notice is in
  `LICENSE.txt`), the DarkSubs empty-list toast of
  `translate.list_candidates`, `gemini_rpm` (declared nowhere, its readers use different defaults).
- **SUBTITLE PHASE 1 FOLLOW-UP (19 Nov 2026).** The 5 labels of `ai_when_no_hebrew` moved from `33009`-`33013` to `32610`-`32614` (settings.xml and
  both `strings.po`); every dialog, progress bar, toast and HTML page title of the subtitle add-on now reads `sub_utils.DIALOG_TITLE` (one place to
  rebrand; "MoranSubs" titles became "Kodi POV IL"). Git forensics against the upstream history: nothing deleted in Phase 1 was a live feature;
  `POOL_API_KEY` is deprecated for good (HMAC signing since 0.2.290), do NOT restore it; the Telegram source (it needed DarkSubs' bundled telethon + `telegram_api`,
  deleted with DarkSubs in `18435d90`) was deleted in the wrap-up below.
- **SUBTITLE PHASE 1 WRAP-UP (19 Nov 2026) - PHASE 1 COMPLETE.** Owner decisions: (1) the 9 runtime strings `33000`-`33008` moved to
  `32630`-`32638` (both `strings.po`, 12 call sites in `default.py` / `translate.py`); no string id >= 33000 remains in the add-on. (2) The broken
  Telegram source is deleted for good: `telegram.py`, `telegram_impl.py`, the `connect_telegram` / `logout_telegram` / `telegram_test` actions, the 5
  Telegram settings, 6 labels and the engine / bridge wiring (Part 5 rows 46-50). (3) `kodi_utils.py` is now `sub_utils.py`, so it is not confused
  with POV's `kodi_utils`; the Wizard's `host_repairs/kodi_utils` is a different module and keeps its name. (4) FENtastic's `700068` is a string id of
  the skin's own `strings.po` (used as `$LOCALIZE[700068]`), not a control id and not an add-on string; the add-on range rule does not apply to a
  skin's table. Verified: stub import 72 of 72 (100%), `compile()` 72 of 72, pyflakes 7 (unchanged), `settings.xml` 93 settings (56 visible, 37
  hidden) consistent with both `strings.po` (143 entries each). The prompts of Subtitle Phases 2 and 3 are in `PHASE_2_AND_3_PROMPTS.md` (repo root).

- **SUBTITLE PHASE 2 COMPLETE (19 Nov 2026; one autonomous session, `ARCHITECTURE_SUBTITLES.md` Part 4 / 6).** Core refactoring and splitting of
  `service.subtitles.kodipovilai` into the tree of §1.7.2: no shims, no empty `__init__.py`, every moved def checked by AST identity against a snapshot.
  Folders under `resources/lib/` (the pre-Phase-2 names are in brackets): `core/` (`kodi`, `settings`, `selection`, `video_context`, `link_codec`
  [ex `sub_utils` and `translate._encode_link`], `contracts` [added, NOT wired until Phase 3], `srt/{blocks,timing,classify,cues}` [ex `srt`, `sync_align.parse_srt`],
  `cache/translation_cache` [ex `cache`]); `fetching/` (`providers/` [ex `subs_engine/`], `provider_adapter` = the ONLY importer of `providers/`,
  `bridge` [ex `subs_engine_bridge`], `search_cache`, `subs_cache`, `local`, `pool_client` / `pool_harvest` [ex `pool` read side], `service` = `list_candidates`,
  `embedded/{embedded_extract,embedded_mp4,mkv_probe,mp4_probe,streams,extraction}`); `scoring_matching/{release,ranking,badges}` [ex `release_match`, `translate` helpers];
  `sync/` (`service` = the gate `process`, `verdicts`, `oracles`, `probes/{remote,audio,cache,bundle}`, `candidates`, `delivery_record`, `validators`, `deep`,
  `jobs`, `delivery_gate`, `common`, `align/{common,estimate,verify,regions,piecewise,retime}` [ex `subsync`, `sync_align`]); `ai_translation/` (`service` = `resolve`
  [ex `translate`], `background`, `policy`, `progress`, `source_prep`, `quota`, `embedded_steps`, `dispatch/{pacing,srt_wire}`, `providers/{gemini,gemini_pair,google_translate,google_flow,
  model_fallback}`, `context/{prompt,tmdb_helper,gender_reference,language_detect}`, `repairs/{postfix_rtl,postfix_glyphs,postfix_cleanup,postfix_leaks,leak_rescue,english_residual,
  script_residual,gender_guard,he_person_gender}`, `proofread/{gender_proofread,gp_later,gp_state,gp_run,gp_jobs}`, `reuse/{pool_reuse,pool_contribute,sdh_pool,sdh_registry}`);
  `delivery/{kodi_player,rtl}`; `autosub/{player,policy}`; `ui/{chooser,filename_publisher,listing,dialogs,settings_actions/{gemini,cache,engine}}`; `entry/{plugin,actions,service,migrations}`;
  `pipeline.py` (a stub that only delegates to `fetching.service.list_candidates` / `ai_translation.service.resolve`, imported by nobody until Phase 3). `default.py` (27 lines)
  and `service.py` (26 lines) are thin routers that import `entry.plugin.main` / `entry.service.main`. `he_sub_match.py` and `telemetry.py` stay at `resources/lib/`.
  Gone for good: `sub_utils`, `translate`, `subsync`, `sync_align`, `srt`, `cache`, `pool`, `subs_engine*`, `autosub_service`, `subs_chooser`, `subs_filename_publisher`, `local_subs`,
  `release_match`, `gemini*`, `prompt`, `arabic_gender` and the other flat modules (the names in the Phase 0 / 1 bullets above are the pre-Phase-2 ones). The only edits that are not moves:
  the 6 Gemini helpers of `default.py` lost their module argument and the 3 gender-reference window helpers of `arabic_gender` (now `gender_reference`) lost their `sa` argument (both import what they
  use); modules that were passed around as arguments now pass `core.settings` / `fetching.service`; optional-import fallbacks (`x = None`) keep their pattern under the new names;
  `he_sub_match`'s flat fallback imports `scoring_matching.release`; `globals()['_status']` became `progress._status`; `_FLAG_DIR` and the gender-reference standalone-load paths
  follow their files. NOT DONE, on purpose, reported in the ARCHITECTURE document and deferred to Phase 4 by the owner (19 Nov 2026), except `core/utils/` (Phase 3 audits it) and the two `entry/` tables (Phase 4 step 4d): the split of `ai_translation.service.resolve` (3,650 lines; its 27 closures capture the
  mutable state of the call and no golden harness with recorded Gemini / Google replies exists), of `list_candidates` (605), `_extract_embedded_srt` (807), `autosub_on_play` (799),
  `_handle_translate_file` (690), `_handle_bg_translate_picker` (458); the splits of `embedded_extract.py` (4,986) and `gender_reference.py` (1,819), because both are stdlib-only and loadable
  on their own by design; `core/utils/`; the action table of `entry/actions.py` and the `migrations.py` table (the 15 `_maybe_*` and the 3 start hooks moved as plain functions). Duplicate
  subsystems (5 SRT parsers, 2 aligners, 6+ caches, 5 playing-release derivations, 2 `_decode_link`, 3 `_breathe`) stand side by side for Phase 3 / the owner. Verified: stub import 138 of 138
  (`python -I`, one interpreter per module), `compile()` 138 of 138 with warnings as errors, pyflakes 7 -> 4 (the 3 intentional pre-imports were replaced by `provider_adapter` calls), cross-module API scan
  clean, AST identity 2,388 top-level statements with the intended differences listed, 311 golden cases (link codec, SRT passes, release scores, settings and selection state machine, `sync_align`
  on fixture pairs, translate helpers) identical to the pristine tree, 26 Gemini-helper scenarios and 19 entry-point actions (every `default.py` action) identical, `settings.xml` / both `strings.po`
  untouched and consistent, no string id >= 33000. Kodi has not been tested: see the checklist in the report.

- **SUBTITLE PHASES 3 AND 4: PLAN (owner decisions, 19 Nov 2026, after Phase 2; the runnable Phase 3 prompt and the Phase 4 plan are in `PHASE_2_AND_3_PROMPTS.md`).**
  Phase 3 (next): 3.0 preparation (sha256 of `he_sub_match.py`, import lists of the standalone modules, the `list_candidates` ordering golden case Phase 2 never had); 3a core hygiene (break the `core/kodi.py` <->
  `core/settings.py` cycle through a leaf `core/addon.py`, so `log` / `notify` need no function-level import; every private name imported or used by another module becomes public, about 319 names in 63 modules,
  except the frozen `fetching/pool_client._lookup_raw`; the Phase 2 removal of the `sa` argument of the 3 `gender_reference` window helpers is ACCEPTED); 3b the 5 "playing release" derivations (Priority #1) become one
  function in `core/video_context.py`, per-caller results kept wherever the copies disagree and every divergence reported to the owner; 3c the other duplicates in order (RTL renderers, SRT parsers, caches,
  `_decode_link`, `_breathe`) only where provably identical, then the `core/utils/` audit (removed from the target tree and the docs unless something needs it); 3d `pipeline.py` as the single route to the whole
  `list_candidates` / `resolve`; 3e the match % as a number; 3f quick toggles (owner switch, default NOT APPROVED); 3g the committed contract test, the pure-function golden harness and CI; 3h the Kodi checklist; 3i
  statistics, docs and the Phase 4 prompt. Phase 3 keeps `he_sub_match.py` byte-identical and leaves `embedded_extract`, `gender_reference` and `scoring_matching/release` with their stdlib-only design; its step 3.0 also checks whether anything outside the
  add-on imports `embedded_extract` / `gender_reference` directly (nothing did on 19 Nov 2026; the only outside users of the add-on's code are the 4 Wizard patches that load `he_sub_match`).
  WHY SEPARATE: the work is split into Phase 3 (internal deduplication and cleanup) and Phase 4 (monolith decomposition and cross-add-on integrations) to keep the blast radius small and so that Phase 3's deduplication
  shrinks the giant functions (the duplicate SRT / RTL / release-detection helpers `resolve` calls) before they are sliced.
  Phase 4 (planned, runnable prompt written at the end of Phase 3): (1) `he_sub_match.py` (1,281 lines; imported by bare name by POV / Umbrella through the Wizard patches `patches_config`, `pov_prewarm`,
  `pov_sub_match_v2`, `umbrella_he_match`) becomes a thin facade with the exact public API (`prewarm`, `release_names`, `embedded_names`, `confirmed_releases`, `label_prefix`) delegating to `scoring_matching/` and
  `fetching/`, plus a dedicated audit of the Wizard patches / repairs (`host_repairs/flow/umbrella_steps.py`...), the skin hooks (FENtastic, Estuary, AF3) and the POV / Umbrella hooks, only with owner approval, each a
  separate labelled change; (2) `resolve` (3,650 lines, 27 closures) gets a golden harness with recorded Gemini / Google conversations, an explicit state object, and is split along its link-type branches (then the other
  functions over 300 lines, the lifecycle of §1.7.3 in `pipeline.py` and the two `entry/` tables); (3) `embedded_extract` (4,986) and `gender_reference` (1,819) drop the single-file standalone constraint (decided
  19 Nov 2026) and are split into modular sub-files; the TOTAL statistics close the refactor.

## 🛑 CRITICAL TRAPS & GLOBAL RULES

- **STRUCTURAL REFACTORING ONLY:** NO bug fixing, NO logic optimization. A fix is made only when the user explicitly
  approves it, as its own clearly labelled change; report every bug you find instead of fixing it silently. (The subtitle
  add-on follows the same rule for behaviour, but its architecture is ours to reshape: see "SUBTITLE ADD-ON: DECOUPLED" below.)
- **THE `libs/` DIRECTORY RULE:** domain folders live INSIDE the Wizard's `resources/libs/`, never at the root level.
- **SKINS ARE ON DEMAND (owner rule, Phase 5):** only `CONFIG.DEFAULT_BUILD_SKIN` (`skin.fentastic`) is downloaded by a fresh
  install or the background updater (`skin_catalog.on_demand_ids()` is everything else, an INSTALLED on-demand skin is still
  updated). The Wizard must never manage, patch or seed data for a skin that is not installed: seeding of
  `addon_data/<skin>/` goes through `skin_catalog.blocks_seeding` (config pack auto-seed, profile seeding,
  `build_skin.prepare_layout`, `child_profiles.seed_skin_flags`); runtime repairs gate on the skin's `addon.xml` and registry
  entries of a skin are `optional`. A new code path that touches a skin must follow the same rule. Skin ids come from
  `skin_catalog.SKINS` (the single list: `build_skin`, `child_profiles` and the switcher read it).
- **REPOSITORIES (Phase 7):** `repository.povil` keeps ONE `<dir>` (ours); every third-party `<dir>` lives in
  `repository.povil.upstream` and carries NO `verify=`. Never add an upstream index that carries an id of ours (Kodi installs the
  highest version). The Wizard never extracts an add-on zip itself (only `repo_bootstrap.py`, for the two repositories, after a
  sha256 check) and never `<import>`s `repository.povil`. NO third-party code or zip is stored in this repository or on our site (use
  `FILTERED_UPSTREAMS` for an index that carries ids of ours). A new third-party add-on = a `<dir>` there + `check_upstreams.REQUIRED`.
  Versions only go up and stay purely numeric (Kodi never downgrades). `gen_repo.py` / `check_upstreams.py` enforce most of this.
- **NO SHIMS (clean-install build):** move the code, update every import / caller to the new path, delete the old one.
- **NO EMPTY `__init__.py` IN SUBFOLDERS:** folders under `resources/libs/` are Python 3 namespace packages
  (`xbmc.python` 3.0.0). Keep only `resources/__init__.py`, `resources/libs/__init__.py` and the pre-existing ones in
  `common/` and `gui/`. The same holds for new feature folders under the subtitle add-on's `resources/lib/`.
- **USE `CONFIG` FOR GLOBALS:** build-wide values come from the `CONFIG` singleton (`resources.libs.common.config`, fed by
  `uservar.py`); never copy one into a module-level constant (`startup.py` updates `CONFIG` in memory at boot).
- **PATCH REGISTRY (Phase 5):** `patches/`, `patches_config.py`, `patches_registry_utils.py` and `patch_engine.py` change ONLY for
  findings the user approved from the Phase 5 audit, each as its own clearly labelled change. Verify every change in the
  scratchpad against the reference POV (real `PatchEngine` on a copy of `_reference_addons/plugin.video.pov`: every entry
  applies once or is a known drop, every patched file compiles, no new pyflakes message, a second run is a no-op, an old-registry
  state upgrades to the same bytes, apart from the blocks of deleted entries). The build is clean-install: a deleted registry
  entry is simply removed (no grace period, no orphan scrub); only a device that already applied it keeps the stale block.
- **PATH-PINNED (never move or rename):** `patches/`, `profiles/profile_age_guard.py` (stdlib-only; host add-ons import it by
  bare name), `profiles/profile_bootstrap.py` (skins' `Home.xml`), `default.py` / `startup.py` (`addon.xml`), every router
  `mode=` name, and every string in the subtitle boundary below.
- **SUBTITLE ADD-ON: DECOUPLED FROM MORANSUBS, WE OWN THE CORE (owner decision, 19 Nov 2026).** `service.subtitles.kodipovilai`
  no longer takes upstream MoranSubs merges: the last one (0.3.74 -> 0.3.96, `update-migration` @ `96efe59a`) closed Subtitle
  Phase 0, and `update-migration` is not merged again. **Architectural refactoring of the subtitle core is strictly encouraged**:
  move, rename, split and reorganize its files and folders to the target of `ARCHITECTURE_SUBTITLES.md` (repo root), with NO SHIMS
  (update every import, delete the old path). The rules that still hold: (a) behaviour stays the same unless the owner approved
  the change (dead-code deletion and the `settings.xml` reorganization are approved; every deletion is logged in the Deletion
  Report of `ARCHITECTURE_SUBTITLES.md`); (b) at the end of every subtitle phase the "Refactoring Statistics" section of
  `ARCHITECTURE_SUBTITLES.md` is updated (lines / files / functions deleted, moved, added; the largest files and functions);
  (c) nothing naming the Wizard, `uservar`, `povil_icons` or build markers may re-enter it (Phase 3 decoupling); dialog / toast / progress titles come from `DIALOG_TITLE` in `resources/lib/core/kodi.py`, never a literal; the add-on's own helper modules are `resources/lib/core/{kodi,settings,selection}.py` (never named `kodi_utils`, which is POV's module and the Wizard's); (d) the
  external public surface below stays byte-identical, because other add-ons, skins and the Wizard call it; (e) through Subtitle Phase 3, `he_sub_match.py` stays byte-identical and `fetching/embedded/embedded_extract.py`, `ai_translation/context/gender_reference.py` and `scoring_matching/release.py` keep their stdlib-only / standalone design (Phase 4 may change them). The "left for
  clean upstream merges" items of the Open Notes are now ordinary cleanup candidates. Public surface: Kodi hooks
  (`xbmc.subtitle.module` = `default.py`, `xbmc.service` = `service.py`, `subtitles.tv` / `subtitles.movie` in `guisettings.xml`
  = the add-on id); `RunScript(service.subtitles.kodipovilai,action=choose_subs)` (skins) and `action=connect_gemini` (frozen
  `patches/pov_myservices.py`) plus its own actions (`search`, `manualsearch`, `download`, `translate_file`,
  `bg_translate_picker`, `he_avail`, the Gemini / pool / cache / engine-test buttons of its `settings.xml`; the Telegram actions were removed in Phase 1 by owner decision);
  `Addon.OpenSettings(service.subtitles.kodipovilai)` (`skins/af3_tools`, `profiles/build_profiles`, Onyx `Home.xml`,
  `widget_baselines`); window properties `subs.player_filename` (published by `ui/filename_publisher.py`), `MoranSubsChooserOpen`
  (set by `ui/chooser.py`; those two modules may move, the strings and the chooser XML file name may not); `resources/lib/he_sub_match.py`
  (imported by bare name after a `sys.path` append by POV / Umbrella hooks and frozen `patches/pov_prewarm`, `pov_sub_match_v2`:
  stdlib-only at module level; API `prewarm`, `release_names`, `embedded_names`, `confirmed_releases`, `label_prefix`; this
  file's path is PINNED). The Wizard reads only `use_builtin_engine` (`flow/runner.py`) and
  `pov_fast_navigation` (`pov_language_invoker_guard`) from it, and only through
  `host_repairs/kodi_utils.subs_get_*`; it names the add-on id only as an existence / required-add-on reference
  (`build/wizard_actions`, `fresh_install`, `auto_install`, `build_profiles`, `pyc_warm`, `clear`, `manifest.json`).
- **HOME ROUTES COME BEFORE THE CHILD GATE:** `Router.dispatch` answers the Onyx modes (`favourites_row`, `favourite_run`,
  `pvr_row`, `pvr_play`, `hero_details`, `hero_warm`) and `tonight`, `search_provider`, `debrid_notice_settings` BEFORE
  `profile_hooks.deny_in_child_profile`: the Onyx modules apply the child policy themselves, and the other three were
  never gated (they were RunScript actions of another add-on). Never move them below the gate or into
  `profiles/hooks._CHILD_ALLOWED` (it also feeds `ROUTES`).
- **IMPORT CYCLES:** an import inside a function stays inside it (they break the cycles `build`↔`install`/`modular_updater`,
  `skins`↔`profiles`).
- **SHARED STRINGS:** window properties (`povil.*`, `POVIL.*`, `subs.player_filename`, `MoranSubsChooserOpen`, …) and
  userdata marker filenames (`kodipovil.provisioned`, …) are read by other processes: keep them byte-identical.
- **`startup.py` RUNS ON IMPORT:** nothing may import it.
- **VERIFY MECHANICALLY** (user tests in Kodi only at the very end). In the scratchpad, never committed, snapshot the working
  tree first, then: stub harness (mock `xbmc*`, `qrcode`, `requests`; run `python -I`); copy moved code by line ranges,
  never retype; AST identity of every moved def / constant (compare after canonicalizing an import-prefix change);
  `symtable` for undefined names and new unused imports; fresh-interpreter import of every module under `resources/`;
  cross-module API scan (names exist, signatures fit); repo-wide grep of callers (skins, `userdata/`, other add-ons) for old
  paths and strings; behavior tests under the stubs for any new code. Compare each against the snapshot. Baselines: Wizard
  171 of 172 `resources/` modules import (Phase 7 stub harness) (only `patches/pov_combined_discover`, needs POV's `indexers`); subtitle add-on: with
  `chardet` / `telethon` / `pyxbmct` / `urllib3` stubbed, every module imports (the Telegram source was deleted, so `telegram_impl` no longer fails): 138 of 138 modules (Subtitle Phase 2 harness; 72 of 72 before the split, `requests` stubbed too, one fresh `python -I` per module, `service` counted as importing when it starts its main loop).
- **WORKFLOW:** stop after each step; the user reviews and commits. No version bump or changelog per step.
- **LINE ENDINGS:** sources and docs use CRLF (a file that is LF stays LF).
- **Output Mirroring:** For every single response you generate in this project, you MUST automatically write your full, exact textual response into a file named `output.txt` in the root directory. Overwrite the file completely each time (do not append).

## ⏭️ SKIPPED UPSTREAM FEATURES (for future review)

Bypassed on purpose in the Subtitle Phase 0 merge (owner decision, 19 Nov 2026). Source of every item: commit `96efe59a` (tip of
`update-migration`); port by hand with `git show 96efe59a:<path>` or `git diff 77880b8 96efe59a -- <path>` (git treats those commits as
merged, so a later merge will NOT bring them back). Each item needs the owner's go-ahead before it is ported.

**Onyx skin, upstream 1.0.10 -> 1.0.20 (we stay on our 1.0.9).** Most items need upstream Wizard 0.5.x features our Wizard does not
have. Our 1.0.9 and upstream's 1.0.9 are different releases; a port must take a version above 1.0.20. About 110 files: 59 modified, 51 new
(`media/povil/osd/*`, `media/povil/ratings/*`, `media/povil/settings/*`, `focus_box.png`, `panel.png`, `tab_line_*.png`).
- **Ratings from several sites** (1.0.15/1.0.16): the hero, every list view, the info screen and the player's info panel show TMDb, IMDb,
  Rotten Tomatoes (critics / audience), Metacritic, Trakt and Letterboxd. Properties `povil.rt.*` / `povil.rtp.*` (`imdb`, `tmdb`,
  `tomato(_state)`, `popcorn(_state)`, `meta(_tone)`, `trakt`, `letterboxd`, `ask`, `for`). Needs the upstream Wizard's `ratings.py`
  (0.5.5/0.5.6), which our Wizard does not have. Also a rating corner plate on every poster / wide tile.
- **Home trailers** (1.0.16-1.0.20): after 3 s on a title its trailer plays behind the hero, then the screen fades to the trailer; a
  settings tile turns it off. Properties `povil.tr.on`, `povil.tr.ask`, `povil.tr.opening`, `povil.tr.file`; LG / busy-dialog guards; a
  tap on the trailer keeps the tab. Needs the upstream Wizard's `trailers.py` (0.5.6-0.5.16, Kodi's own player, HLS 1080p, YouTube
  translated trailers). The subtitle-side half (skip snapshot / autosub while `povil.tr.file` is the playing file) was also dropped from
  `autosub/player.py` (ex `autosub_service.py`) and must come back with it.
- **Streaming-service hub tab** (1.0.16): Netflix / Disney+ / Apple TV+ / Prime Video / HBO Max chips open a page of the home filtered
  to that service. Properties `povil.hub.*` (`enter`, `id`, `name`, `net`, `type`), `povil.tab.hub`, `povil.deep.hub`, `povil.far.hub`.
- **Continue-watching tab** (1.0.18, `povil.tab.continue`, `povil.deep.continue`) and **the viewer chooses the top bar's tabs** (Wizard mode
  `onyx_tabs`, the settings tile "בחירת מסכים"; the bar became a list of buttons, `tab_line_*.png`).
- **Wizard modes our router does not have:** `onyx_tabs`, `player_subs_position` (`step=up|down`, subtitles up / down buttons in the
  player, 1.0.12), `pov_favorites_order`. (`profile_skin`, `profiles` exist.)
- **Player redesign** (1.0.9 upstream / 1.0.12 / 1.0.15): a focusable timeline with real seeking, one seek bar, glass button groups,
  format pills (resolution, fps, HDR kind, audio, codec), the source provider in the details line (`povil_osd_provider`,
  `povil_osd_provider_for`, `pov_picked_source_name`, upstream Wizard source hook 0.5.1), a tap on the picture closes the controls (1.0.10).
- **Other Onyx items:** generated Kodi settings screens and dialogs in the build's look (1.0.9 / 1.0.15), clear-cache tile (1.0.11, POV
  `clear_all_cache`), power button in the top bar (1.0.12), POV chips in favourites and the favourites rows refresh at once (1.0.13 /
  1.0.14, a property changed by upstream Wizard 0.5.4 POV hooks), "dark, not black" veil (1.0.13), home rows not reloaded at every play
  start / stop (`sortby="size"`, 1.0.16), Hebrew in system information.
- **Conflicts with our refactor if taken as-is:** upstream Onyx calls `RunScript(service.subtitles.kodipovilai,action=search_provider)` and
  reads `Addon.SettingStr(service.subtitles.kodipovilai,search_provider)` (both moved to the Wizard: `mode=search_provider`, the Wizard's
  setting); upstream also made Onyx the default skin and retired NOX ("one skin leaves the build", Wizard 0.5.0), against our skin policy.

**Wizard-side upstream edits (reverted to ours).**
- `af3_home_patcher.py`: 12 Hebrew strings for AF3 3.5's search window (msgctxt `#31016`, `#31022`, `#31281`, `#31324`, `#31639`,
  `#31850`-`#31852`, `#31861`, `#31863`-`#31865`). Pure data; a candidate to port.
- `search_provider.py`, `umbrella_watch_prompt.py`, `pov_reload.py`: NOX removed, and `addon_presence` imported as `resources.lib.addon_presence`
  (wrong in the Wizard: `resources.libs.host_repairs.addon_presence`). Our versions already ask through `addon_presence`; nothing to port
  unless NOX is retired.
- `af3_osd_tap_close_patcher.py` (new upstream, AF3 player: a tap on the picture closes the controls; marker `AI_SUBS_AF3_OSD_TAP_CLOSE_v1`,
  control id 6990): a host repair, so it would belong in the Wizard's `host_repairs/` (+ a call in the runner), not in the subtitle add-on.

## 🔮 FUTURE ENHANCEMENTS

- **Playlist Timeout UI setting:** the owner wants a user-facing setting (Wizard settings) for Kodi's `<playlisttimeout>`. Today `<playlisttimeout>0</playlisttimeout>` is pre-baked in `userdata/advancedsettings.xml` (WIZARD IL BAKED fence); the UI setting would replace that baked value. Not started.
- **Startup cache / low-memory check:** create a Kodi startup check that detects low memory or cache issues and prompts the user with an alert to clear the cache. Once implemented, we can remove the runtime `pov_cache_guard` (registry entry `pov_results_cache_guard`).

## 📝 OPEN NOTES (reported, not fixed; each needs user approval)

- **Onyx router latency.** Onyx home modes went from `default.py`'s fast-path into `Router.dispatch`; with
  `reuselanguageinvoker=false` each call imports ~12 modules instead of ~5 (stubs: ~48 → ~178 ms). If the Onyx home feels
  slow, add a thin generic hook that skips the router (it must stay before the child gate).
- **Phase 4 investigation: POV `reuselanguageinvoker` mismatch.** Every boot races POV's own check (~20 s after start): the
  repair pass's `_maybe_patch_pov_language_invoker` (measured 19 s margin) keeps
  the setting and POV's `addon.xml` in agreement; losing shows POV's English "SETTING/XML mismatch" dialog. Investigate
  (a) preventing the mismatch at provisioning / install / POV-update time, (b) decoupling this clock-bound step from the
  rest of the repair bundle. Until then it stays FIRST in the pass.
- **Known, kept as-is until Phase 4 gets to it:** the registry validator misreports
  `profile_age_guard.py` as missing when given the patches dir (it lives in `profiles/`); dead code `boot/gui_ready.stop_if_duplicate`, `build/wizard_actions.wizard()`, the dormant
  first-boot stabilizer; `skins/skin_settings.swap_us()` is not skin code.
- `tonight/entrypoints.LEGACY_ICON` still names the subtitle add-on's icon (a string used to match old tiles).
- **Phase 3 findings:** Wizard-side repair logs follow the Wizard's own `log_level`
  (undeclared, so INFO) and no longer the subtitle add-on's; `userdata/` changed (baselines moved, tiles state deleted), so
  `config_version` in `userdata/config_policy.json` was bumped to 2.0.7 in Phase 4 Step 3 (the stale subtitle comments of this finding were fixed in Subtitle Phase 1).
  `_maybe_seed_hebrew_subtitle_prefs` now also runs on build devices (it was standalone-only).
- **Defects found (reported, not fixed):** `prepare_build_skin` has no handler (`profiles/build_skin.py:252` waits 60 s for AF3
  and reports failure); `fentastic_search_patcher.py` is missing (its call site sits in `try/except`, so the hub-search patch
  silently skips; present upstream at `77880b8`; the call sites of `source_memory` and `dark_subs_integration` were deleted in Subtitle Phase 1); `manifest.json` pins the subtitles zip at 0.3.50 while the source is 0.3.74. The subtitle add-on's Telegram source (it could not
  load: `telegram_api` did not ship) was deleted at the end of Subtitle Phase 1 on the owner's decision.
- **LABEL IDS >= 33000 (audit task, owner request):** Kodi's add-on string range is 30000-32999 (the settings file says so); ids from 33000 up belong to Kodi
  and are ignored or mapped to built-in strings. **The subtitle add-on is clean** (Subtitle Phase 1: `settings.xml` highest 32626, `strings.po` highest
  32638, the 9 runtime strings moved from `33000`-`33008` to `32630`-`32638`; no id >= 33000 anywhere in it). **Still to do: audit every other add-on in this
  repository** (`settings.xml`, `strings.po`, `<label>` / `$LOCALIZE` / `getLocalizedString` uses of an add-on's own strings) for label ids >= 33000. First
  pass done: no other add-on `strings.po` or `settings.xml` of this repository uses an id >= 33000. Skins are different: a skin's `strings.po` is its own
  table, and `skin.fentastic` (upstream-authored, not ours to renumber) defines 72 ids from `700010` up there (`$LOCALIZE[700068]` in `SkinSettings.xml` is
  one: a string id, not a control id); the skins' other `33xxx` references are Kodi core strings and are correct.
- **Check at the final Kodi test:** (1) the host-repair runner starts when the Wizard's service starts (`start="startup"`), not at
  the old login timing: after a profile switch exactly one runner must be alive and repairs must apply to the new profile;
  (2) the POV invoker guard still wins its race (its log line says how far ahead of POV it wrote); (3) `patcher_health`
  lists its 3 remaining marker patchers (`af3_dialog_subtitles`, `umbrella_tmdb_apikey`, `umbrella_watch_prompt`) and a `PatchEngine` pass
  reports no `failed` / `anchor_missing`; (4) the `RunPlugin(plugin://plugin.program.kodipovilwizard/?mode=…)` callers work (Onyx search item,
  favourites "מנוע החיפוש" / "הגדרת התראות מנוי", AF3 tools); the Tonight window opens and keeps its state under the
  Wizard's `addon_data`; the restored debrid dialog writes POV's `*.expires` settings; (5) the Wizard's Maintenance settings show the POV toggle, and the
  Onyx search item follows the Wizard's `search_provider`; (6) **Kan VOD:** play Kan 11 VOD with `idanplus_vod_*` disabled and
  enabled (Idan Plus 4.0.3 says "Fix Kan11-VODs"): keep only what is still needed; (7) the Hebrew `.po` merge: look at POV's
  dialogs and menus in Hebrew (RTL, `[B]` markup, the tmdb / trakt / mdblist list managers); (8) AF3 typed search returns POV
  results through `skinvariables-multisearch-paths.json` (script.skinvariables 2.3.2, the version AF3 3.5.2 requires) with all
  four rows (movies, shows, people, collections), and the native TMDbHelper "Discover" section (genre / decade filters) still
  works and is no longer repointed to POV; (9) Umbrella first launch keeps the baked settings (Hebrew API
  language, CocoScrapers as external provider); (10) the movie "networks" tiles of NOX / Onyx / the shortcut folder open their
  lists (they now pass `company=`); (11) **Patch Manager:** Maintenance -> מנהל טלאים: folders and counters, one toggle,
  a bulk switch and "reset", the General / Engine panel; confirm that `setSetting('patch_enabled_<id>')` persists (the ids are
  not declared in `settings.xml`) and that a toggle only runs its own patches; (12) **Skin Manager / skin policy:** a fresh
  install installs only FENtastic (no NOX / Estuary / Onyx download, provisioning still completes); main menu -> תוספים וסקינים אופציונליים
  installs and switches NOX, Onyx, AF3 and Estuary (the build edition downloads over Kodi's stock one); a skin installed later
  gets its baked settings (AF3, Estuary) before its first load; no `addon_data/` of a not-installed skin appears (fresh
  install, new profile, child profile); the picker of the skins' power menus still works; Onyx needs its zip in
  `manifest.json` (CI) before it can be installed. (13) **Phase 6 fresh install:** it runs to completion (the `fresh_build_auto_install_done` setting is written, no
  "setup remains pending" line) with orderfavourites 1.4.7; no `Unknown addon id 'plugin.video.umbrella'` line without Umbrella; POV's
  log shows no "failed to load value" for `results.xml_style` / `subtitles.subs_action`, and the sources results window opens. (14) **Optional add-ons:** a fresh install has no Otaku, no Otaku / Umbrella tile and
  no dead NOX Otaku button; main menu -> תוספים וסקינים אופציונליים lists Umbrella, Otaku and the skins; installing Otaku (a few
  minutes) makes its status line replace the button and its tile appear without a restart (the tile opens it, it starts with the
  baked settings and no setup screens); the optional add-ons tile opens the screen (`ActivateWindow(Programs, ...)` is
  unverified in FENtastic / AF3); removing Otaku hides the tile at the next refresh and boot does not reinstall it; Umbrella follows
  the same rules; movies networks tiles open provider lists. (15) **POV context menu:** with no Trakt / MDBList / TMDb connected, a movie and a show card show no list-manager entry; connecting one makes its entry appear at the next list.
  (16) **Local artwork:** the optional add-ons and Skin Manager screens show the dark `skins_bg.jpg` with no network, and the "existing skins" notice shows the local logo (check
  whether FENtastic / AF3 honour the `fanart` art of a plugin list). (17) **POV update:** update POV while Kodi runs (or replace its folder with an older build): within about 10 s
  the log shows `addon_update_watch: ...` and the PatchEngine pass, `addon.xml` holds the build's `reuselanguageinvoker`, and no English "SETTING/XML mismatch" question appears
  (it can still appear once if POV's own check wins the race); after a restart the profile is not reloaded. (18) **Hebrew titles:** a Hebrew title whose only logo is English
  shows the title text in FENtastic, NOX, AF3, Estuary and Onyx (home widgets and lists). (19) **Clean reinstall:** run it offline (nothing is deleted, Hebrew message), then
  online with and without the keep options: Kodi closes, the next start provisions the build, the kept data is still there, `kodipovil.provisioned` is rewritten and the home
  is the default skin.

## ARCHITECTURAL DECISION (Phase 5): no "replace" in the PatchEngine

`patch_engine.py` stays additive: its only actions are `prepend_before` / `append_after`, each block is fenced by markers and is
removed byte for byte. The owner decided NOT to add a "replace" action: replacing or overwriting upstream code breaks the safe
reversibility of the registry. Anything that needs a line REPLACED, or an anchor that is not unique in the file, stays a
**runtime patcher** in `host_repairs/` (marker-gated, `compile()`-checked, atomic, with its own `revert()`), or becomes a data
change (baked `userdata`, skin XML) when the goal allows it. Do not propose engine "replace" / "regex" / "scoped anchor" actions again.

The three runtime patchers that stay for this reason (reference Umbrella 6.8.02, AF3 3.5.2):

| Patcher | Why the registry cannot express it | Non-destructive alternative, if one is ever wanted |
|---|---|---|
| `umbrella_tmdb_apikey` | rewrites the `<url> % self.API_key` call sites of `indexers/tmdb.py` (6 variable sites) into a helper call | insert-only: `prepend_before` an escape line (`url = url.replace('%', '%%').replace('%%s', '%s')`) at each site that can carry user text (`tmdb_list`, line 186 first); needs every site checked, the escaped `url` must not feed a later `%`; unverified |
| `umbrella_list_order` | sort calls go into about a dozen functions of `menus/movies.py` / `menus/tvshows.py` whose anchor LINES repeat (function-scoped through the AST), and `modules/favourites.py` needs `SELECT * FROM %s` replaced by `... ORDER BY rowid DESC` | none: the anchors are not unique, so an additive entry cannot place them |
| `af3_dialog_subtitles` | swaps `$INFO[Player.FileName]` for `$VAR[ai_subs_header_label]` inside an include `<param name="label">` | append a second `<param name="label">` after the stock one; depends on how Kodi resolves a duplicated include param (not verified, needs a Kodi test), so it stays a patcher |

## ARCHITECTURAL DECISION (Phase 5): AF3 discover and search (AF3 3.5.2, script.skinvariables 2.3.2)

- In 2.3.2 the multisearch window is built by `script.skinvariables` (`run_multisearch`). Its two TMDbHelper "Discover" sources
  (movies, tv; `multisearch/discover.py`) are Python classes inside that add-on, listed BEFORE the `searchwidgets` rows. There is
  no skin XML to repoint any more (`Custom_1105_Search.xml` is a 4-line launcher), so repointing them to POV would mean editing
  another add-on's Python (a replace-style patch): not done.
- **Owner decision: drop the discover patch entirely and let the native TMDbHelper discover run** (its genre / decade filtering
  is wanted). Nothing replaces it: no skin setting is baked (`HomeSwitcher.Search.DisableDiscover` stays at AF3's default, off)
  and nothing patches script.skinvariables. Discover results are TMDbHelper items and play through TMDbHelper's players.
- Typed search stays `host_repairs/af3_search_pov_patcher` (4 tokens `DefaultSearch-POVMovies / POVTv / POVPeople / POVCollections`
  added to the skin's `skinvariables-multisearch-paths.json`, value = provider path + `{query}`; the window url-encodes `{query}`
  once, POV reads it through `parse_qsl`) and the 4 rows of `af3_home_patcher.SEARCH_WIDGETS`.

## PHASE 4 (COMPLETE, Nov 2026): scope and carry-overs

Phase 4 optimized `patches_config.py` and trimmed `host_repairs/` (Steps 1-4, see the STATUS bullet). Items of its original
scope that were not carried out stay open and go on with Phase 5: the invoker-mismatch investigation (Open Notes); moving the
`pov_fast_navigation` declaration out of the subtitle add-on (it stays there only because the Wizard's
`pov_language_invoker_guard` reads and writes it by the add-on id; `patches/pov_invoker_safe.py` was deleted in Step 4); the
registry validator's `profile_age_guard.py` misreport. Phases 1-4 are complete.

- **COMMIT COMMAND:** At the end of every step, provide a ready-to-copy Git commit snippet (using `git add -A && git commit -m "..."`) inside a bash code block, with a clear, concise summary of what changed in that step.
