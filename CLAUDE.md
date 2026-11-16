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
| `host_repairs/`  | Runtime repairs of other add-ons and skins (42 patcher modules, flat on purpose), `flow/` (startup-repair pass + runner), `kodi_utils` (subset copy) |
| `tonight/`       | The Tonight home tile (shortcut writer, picker window logic)                           |

- **Phase 1 COMPLETE (Nov 2026):** `startup.py` 848 → ~180 lines; `wizard.py` split into the folders above and deleted.
- **Phase 2 COMPLETE (Nov 2026):** skin decoupling. Skin code in `profiles/` (`build_skin.py`, `af3_skin.py`) stays there
  (Profiles boundary, README §9.2).
- **Phase 3 COMPLETE (Nov 2026):** `service.subtitles.kodipovilai` is now a decoupled, standalone add-on (verified: it imports and
  its service starts under the stubs with no Wizard on disk; zero Wizard references in its code). All host repairs (44 patcher
  modules + the startup-repair runner), the build's settings and state, Tonight, the search-provider picker, the debrid-notice
  dialog and every `build_mode` block were migrated to the Wizard. It talks to the Wizard only through the boundary below.

## 🛑 CRITICAL TRAPS & GLOBAL RULES

- **STRUCTURAL REFACTORING ONLY:** NO bug fixing, NO logic optimization. A fix is made only when the user explicitly
  approves it, as its own clearly labelled change; report every bug you find instead of fixing it silently.
- **THE `libs/` DIRECTORY RULE:** domain folders live INSIDE the Wizard's `resources/libs/`, never at the root level.
- **NO SHIMS (clean-install build):** move the code, update every import / caller to the new path, delete the old one.
- **NO EMPTY `__init__.py` IN SUBFOLDERS:** folders under `resources/libs/` are Python 3 namespace packages
  (`xbmc.python` 3.0.0). Keep only `resources/__init__.py`, `resources/libs/__init__.py` and the pre-existing ones in
  `common/` and `gui/`.
- **USE `CONFIG` FOR GLOBALS:** build-wide values come from the `CONFIG` singleton (`resources.libs.common.config`, fed by
  `uservar.py`); never copy one into a module-level constant (`startup.py` updates `CONFIG` in memory at boot).
- **SILENT TRAP:** NEVER modify `patches/`, `patches_config.py` or `patch_engine.py` before Phase 4 (calling `PatchEngine`
  is fine). The Phase 1 exception is closed.
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
  `pov_fast_navigation` (`pov_language_invoker_guard`; the frozen `patches/pov_invoker_safe.py` too) from it, and only through
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

## 📝 OPEN NOTES (reported, not fixed; each needs user approval)

- **Onyx router latency.** Onyx home modes went from `default.py`'s fast-path into `Router.dispatch`; with
  `reuselanguageinvoker=false` each call imports ~12 modules instead of ~5 (stubs: ~48 → ~178 ms). If the Onyx home feels
  slow, add a thin generic hook that skips the router (it must stay before the child gate).
- **Phase 4 investigation: POV `reuselanguageinvoker` mismatch.** Every boot races POV's own check (~20 s after start): the
  repair pass's `_maybe_patch_pov_language_invoker` (measured 19 s margin) and the frozen `patches/pov_invoker_safe.py` keep
  the setting and POV's `addon.xml` in agreement; losing shows POV's English "SETTING/XML mismatch" dialog. Investigate
  (a) preventing the mismatch at provisioning / install / POV-update time, (b) decoupling this clock-bound step from the
  rest of the repair bundle. Until then it stays FIRST in the pass.
- **Known, kept as-is until Phase 4 gets to it:** the registry validator misreports
  `profile_age_guard.py` as missing when given the patches dir (it lives in `profiles/`); dead code `boot/gui_ready.stop_if_duplicate`, `build/wizard_actions.wizard()`, the dormant
  first-boot stabilizer; skin IDs hard-coded in `profiles/build_skin.SKINS`, `profiles/child_profiles.SKINS`,
  `skins/switcher.skin_mapping`; `skins/skin_settings.swap_us()` is not skin code.
- `tonight/entrypoints.LEGACY_ICON` still names the subtitle add-on's icon (a string used to match old tiles);
  `service.subtitles.kodipovilai/default.py` has one now-unused `xbmcaddon` name (left to keep upstream merges clean).
- **Phase 3 findings:** `pov_scraper_settings_patcher` writes `_pov_scraper_tune_state`, which no add-on declares (Kodi drops
  it, so that marker never persists; behavior kept as it was); Wizard-side repair logs follow the Wizard's own `log_level`
  (undeclared, so INFO) and no longer the subtitle add-on's; `userdata/` changed (baselines moved, tiles state deleted), so
  `config_version` in `userdata/config_policy.json` must be bumped when this is released (not done: no version bump per
  step); two comments of retained subtitle settings still name moved keys, and the docstring of
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
  lists 16 patchers; (4) the `RunPlugin(plugin://plugin.program.kodipovilwizard/?mode=…)` callers work (Onyx search item,
  favourites "מנוע החיפוש" / "הגדרת התראות מנוי", AF3 tools); the Tonight window opens and keeps its state under the
  Wizard's `addon_data`; the restored debrid dialog writes POV's `*.expires` settings; (5) the Wizard's Maintenance settings show the POV toggle, and the
  Onyx search item follows the Wizard's `search_provider`.

## PHASE 4 (FROZEN until the user starts it)

Optimize `patches_config.py`; decide which `host_repairs/` modules could become registry entries; run the invoker-mismatch
investigation (Open Notes); once `patches/pov_invoker_safe.py` is unfrozen, move the `pov_fast_navigation` declaration out of the
subtitle add-on (it stays there only because that frozen patch reads and writes it by the add-on id). Phases 1-3 are complete.

- **COMMIT COMMAND:** At the end of every step, provide a ready-to-copy Git commit snippet (using `git add -A && git commit -m "..."`) inside a bash code block, with a clear, concise summary of what changed in that step.
