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
| `packs/`         | The shared on-demand zip-pack engine                                                   |
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
  for AF3 3.5, the AF3 touch-cleanup and autoscroll XML edits became registry entries. The registry stands at 147 entries (117
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

## 🛑 CRITICAL TRAPS & GLOBAL RULES

- **STRUCTURAL REFACTORING ONLY:** NO bug fixing, NO logic optimization. A fix is made only when the user explicitly
  approves it, as its own clearly labelled change; report every bug you find instead of fixing it silently.
- **THE `libs/` DIRECTORY RULE:** domain folders live INSIDE the Wizard's `resources/libs/`, never at the root level.
- **SKINS ARE ON DEMAND (owner rule, Phase 5):** only `CONFIG.DEFAULT_BUILD_SKIN` (`skin.fentastic`) is downloaded by a fresh
  install or the background updater (`skin_catalog.on_demand_ids()` is everything else, an INSTALLED on-demand skin is still
  updated). The Wizard must never manage, patch or seed data for a skin that is not installed: seeding of
  `addon_data/<skin>/` goes through `skin_catalog.blocks_seeding` (config pack auto-seed, profile seeding,
  `build_skin.prepare_layout`, `child_profiles.seed_skin_flags`); runtime repairs gate on the skin's `addon.xml` and registry
  entries of a skin are `optional`. A new code path that touches a skin must follow the same rule. Skin ids come from
  `skin_catalog.SKINS` (the single list: `build_skin`, `child_profiles` and the switcher read it).
- **NO SHIMS (clean-install build):** move the code, update every import / caller to the new path, delete the old one.
- **NO EMPTY `__init__.py` IN SUBFOLDERS:** folders under `resources/libs/` are Python 3 namespace packages
  (`xbmc.python` 3.0.0). Keep only `resources/__init__.py`, `resources/libs/__init__.py` and the pre-existing ones in
  `common/` and `gui/`.
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
- **SUBTITLE ADD-ON BOUNDARY** (`service.subtitles.kodipovilai`; upstream MoranSubs merges keep landing there): never rename,
  move or reorganize a core subtitle file or folder; remove whole functions / blocks, never rewrite them; nothing naming the
  Wizard, `uservar`, `povil_icons` or build markers may re-enter it. Byte-identical public surface: Kodi hooks
  (`xbmc.subtitle.module` = `default.py`, `xbmc.service` = `service.py`, `subtitles.tv` / `subtitles.movie` in `guisettings.xml`
  = the add-on id); `RunScript(service.subtitles.kodipovilai,action=choose_subs)` (skins) and `action=connect_gemini` (frozen
  `patches/pov_myservices.py`) plus its own actions (`search`, `manualsearch`, `download`, `translate_file`,
  `bg_translate_picker`, `he_avail`, the Telegram / Gemini / pool / cache / engine-test buttons of its `settings.xml`);
  `Addon.OpenSettings(service.subtitles.kodipovilai)` (`skins/af3_tools`, `profiles/build_profiles`, Onyx `Home.xml`,
  `widget_baselines`); window properties `subs.player_filename`, `MoranSubsChooserOpen`; `resources/lib/he_sub_match.py`
  (imported by bare name after a `sys.path` append by POV / Umbrella hooks and frozen `patches/pov_prewarm`, `pov_sub_match_v2`:
  stdlib-only at module level; API `prewarm`, `release_names`, `embedded_names`, `confirmed_releases`, `label_prefix`), with
  `subs_chooser` and `subs_filename_publisher`. The Wizard reads only `use_builtin_engine` (`flow/runner.py`) and
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
  198 of 199 modules import (only `patches/pov_combined_discover`, needs POV's `indexers`); subtitle add-on: with
  `chardet` / `telethon` / `pyxbmct` / `urllib3` stubbed, only `subs_engine/sources/telegram_impl` fails (missing `telegram_api`).
- **WORKFLOW:** stop after each step; the user reviews and commits. No version bump or changelog per step.
- **LINE ENDINGS:** sources and docs use CRLF (a file that is LF stays LF).
- **Output Mirroring:** For every single response you generate in this project, you MUST automatically write your full, exact textual response into a file named `output.txt` in the root directory. Overwrite the file completely each time (do not append).

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
- `tonight/entrypoints.LEGACY_ICON` still names the subtitle add-on's icon (a string used to match old tiles);
  `service.subtitles.kodipovilai/default.py` has one now-unused `xbmcaddon` name (left to keep upstream merges clean).
- **Phase 3 findings:** Wizard-side repair logs follow the Wizard's own `log_level`
  (undeclared, so INFO) and no longer the subtitle add-on's; `userdata/` changed (baselines moved, tiles state deleted), so
  `config_version` in `userdata/config_policy.json` was bumped to 2.0.7 in Phase 4 Step 3; two comments of retained subtitle settings still name moved keys, and the docstring of
  `service._maybe_default_builtin_engine` still says "Build-edition only ... SLIM_SERVICE" (both left for clean merges).
  `_maybe_seed_hebrew_subtitle_prefs` now also runs on build devices (it was standalone-only).
- **Defects found (reported, not fixed):** `prepare_build_skin` has no handler (`profiles/build_skin.py:252` waits 60 s for AF3
  and reports failure); `fentastic_search_patcher.py`, `source_memory.py`, `dark_subs_integration.py` are missing (their call
  sites sit in `try/except`, so the hub-search patch, the source-memory prune and the DarkSubs hook silently skip; present
  upstream at `77880b8`); `manifest.json` pins the subtitles zip at 0.3.50 while the source is 0.3.74. Inside the subtitle
  add-on, old DarkSubs leftovers point at files that do not ship (`telegram_api`, `resources/modules/auto_translate`, the
  `service.subtitles.All_Subs` paths): lazy and guarded, none touches the Wizard.
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
  the same rules; movies networks tiles open provider lists.

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
