# KODI POV IL BUILD: MAINTENANCE REFERENCE

A Hebrew Kodi 21 (Omega) build served as two standard Kodi repositories on GitHub Pages. Kodi installs and updates every add-on; the Wizard
keeps STATE only (provisioning, config pack, repairs of other add-ons). Code is split into small domain files so a change loads one or two
files. The repo is in **lightweight manual maintenance**: small, targeted changes; the Phase / Step history lives in `git log`.

## Working rules

- **Line endings:** add-on files (`.py`, `.xml`, `.json`, `.txt`, `.po`, ...) are CRLF (a file that is LF stays LF; check, never convert).
  `CLAUDE.md` and the root `README.md` are LF. Git Bash `sed -i` strips CRs: edit with the Edit tool or Python on bytes.
- **Shell:** the owner runs Windows PowerShell 5.1: no `&&` / `||` (use `;` or separate lines). Never `git push`.
- **Commits:** end a task with a ready-to-copy snippet (`git add -A`, then `git commit -m "..."`, separate lines), summary only.
- **`output.txt` mirroring:** every response is written in full to `output.txt` in the repo root (overwritten each time; git-ignored).
- **Versions:** Kodi updates an add-on only when its `addon.xml` version rises: bump the patch version of every add-on you change and add a
  short `changelog.txt` entry where it has one (the subtitle add-on's keeps the last 12 versions; the rest is in git). Any change under
  `userdata/` needs `config_version` in `userdata/config_policy.json` bumped (that is how devices pick the pack up).
- **Unrequested bugs:** report them; a fix is its own labelled change.
- **Code hygiene:** no shims (move code, update every import, delete the old path); no empty `__init__.py` in new folders (namespace
  packages); build-wide values come from `CONFIG` (`resources.libs.common.config`), never a copied constant; an import inside a function stays
  inside it (it breaks the `build` <-> `install`, `skins` <-> `profiles` cycles); `startup.py` runs on import, so nothing imports it.
- **Frozen strings:** window properties (`povil.*`, `POVIL.*`, `subs.player_filename`, `MoranSubsChooserOpen`, ...) and userdata marker file
  names (`kodipovil.provisioned`, ...) are read by other processes. Add-on string ids stay in 30000-32999 (33000+ belong to Kodi).
- **No third-party code or zips are stored here** (owner rule).

## Protected (never remove or alter)

- `_start_subsync_delay_watch` (`service.subtitles.kodipovilai/resources/lib/entry/service.py`) and the community repository reporting
  mechanism (the pool: `fetching/pool_*`, `ai_translation/reuse/pool_*`, `report_sync`, the `pool_*` settings and buttons).
- The subtitle add-on's **public surface**: Kodi hooks (`xbmc.subtitle.module` = `default.py`, `xbmc.service` = `service.py`,
  `subtitles.tv` / `subtitles.movie` in `guisettings.xml` = the add-on id); `RunScript(service.subtitles.kodipovilai,action=...)` for
  `choose_subs` (skins), `connect_gemini` (frozen `patches/pov_myservices.py`), `search`, `manualsearch`, `download`, `translate_file`,
  `bg_translate_picker`, `he_avail`, `cancel_translation`, `unsync` and the Gemini / pool / cache / engine-test buttons of `settings.xml`;
  `Addon.OpenSettings(service.subtitles.kodipovilai)` (AF3 tools, profiles, Onyx); the window properties `subs.player_filename` and
  `MoranSubsChooserOpen`; the chooser XML name `script.moransubs.chooser.xml`.
- `resources/lib/he_sub_match.py`: path, 5 API names (`prewarm`, `release_names`, `embedded_names`, `confirmed_releases`, `label_prefix`)
  and stdlib-only module-level imports (POV / Umbrella import it by bare name; the Wizard's frozen `patches/pov_prewarm`,
  `pov_sub_match_v2`, `umbrella_he_match` depend on it). `scoring_matching/release.py`, `core/srt/credits.py` and
  `scoring_matching/provenance.py` stay stdlib-only too.
- **Path-pinned (never move or rename):** the Wizard's `patches/`, `profiles/profile_age_guard.py` (stdlib-only, imported by bare name by
  host add-ons), `profiles/profile_bootstrap.py` (skins' `Home.xml`), `default.py` / `startup.py` (`addon.xml`), every router `mode=` name.

## Layout

| Add-on / folder | Role |
|---|---|
| `plugin.program.kodipovilwizard` | the Wizard: state, provisioning, config pack, repairs of other add-ons, maintenance UI |
| `service.subtitles.kodipovilai` | subtitles (human providers + AI translation + community pool); standalone, knows nothing of the Wizard |
| `plugin.program.orderfavourites-hebrew` | launcher tiles, icons, favourites ordering |
| `script.fentastic.helper` | FENtastic's helper service (ratings) and actions (settings sorter) |
| `skin.fentastic` (default, the only provisioned skin), `skin.estuary`, `skin.povil.nox`, `skin.povil.onyx` | skins; the others are on demand |
| `repository.povil`, `repository.povil.upstream` | our index (one `<dir>`) and the third-party indexes |
| `userdata/`, `.github/` | the baked config pack (`config_policy.json`); `gen_repo.py`, `check_*.py` and the three workflows |

Wizard `resources/libs/` (full map: `README.md` §11): `boot/`, `provisioning/` (fresh install, bootstrap, update window), `build/`, `skins/`,
`addons/` (optional add-ons), `accounts_auth/`, `system/`, `host_repairs/` (runtime repairs of POV / Umbrella / Idan Plus / AF3 / skins;
`flow/` = start-up repair pass + runner), `tonight/`, `patches/` + `patch_engine.py` (the patch registry), `gui/`.
Subtitle add-on `resources/lib/`: `core/`, `fetching/`, `scoring_matching/`, `sync/` (SubSync), `ai_translation/`, `delivery/`, `autosub/`,
`ui/`, `entry/` (`plugin.py` action tables, `service.py`), `pipeline.py`, `he_sub_match.py`; its own map is its `README.md`.

## Subtitle add-on rules

- Decoupled from MoranSubs upstream (no merges) and from the Wizard: nothing in it names the Wizard, `uservar`, `povil_icons` or build markers;
  the Wizard reads none of its settings. Dialog / toast titles come from `DIALOG_TITLE` in `core/kodi.py`, never a literal. Never name a
  module `kodi_utils` (POV's, and the Wizard's `host_repairs/kodi_utils`, are different things).
- No module imports or reads a `_private` name of another module (frozen exception: `fetching/pool_client._lookup_raw`, listed in
  `.github/scripts/subs_contract/baseline.json`). `core/addon.py` stays a leaf. `fetching/provider_adapter` is the only importer of
  `fetching/providers/`; everything outside `fetching/` and `ai_translation/` reaches `list_candidates` / `resolve` only through `pipeline.py`.
- The playing release comes only from `core.video_context.release_name(info, policy)` (six policies; no seventh copy) and the token test from
  `core.video_context.looks_like_token`.
- Every setting id read or written in code is declared in `settings.xml`; every label id exists in both `strings.po` files (en_gb, he_il).
- **Provider HTTP:** provider modules call `_http.get` / `_http.post` (`fetching/providers/general.ProviderHttp`: one `requests.Session` per
  provider, connections reused, cookies never kept; Subscene has its own TLS session): no bare `requests.get` / `post`.
  `fetching/providers/cache.py` stores a repr and reads it with `ast.literal_eval`: never `eval`.
- **Provenance is positive-only and display-only:** Hebrew provider rows only; no "unverified" / "AI" state; a badge never hides, reorders,
  demotes or blocks auto-sub; sync / fetch credits never give a badge; OpenSubtitles' `from_trusted` is not a signal; only
  `bridge.download_inner` writes the sidecar, only the chooser reads it. A new allowlist name = one entry with its `evidence`.
- **Chooser window: ONE OWNER THREAD** (after a Kodi 21.3 crash in `makePendingCalls`): `ui/chooser._run_owner_loop` (`show()` +
  `Monitor.waitForAbort`) serves the window; every `xbmcgui.ListItem`, control call (`reset` / `addItems` / `selectItem` / `setFocusId`) and
  window property is made on that thread. Background threads (`_complete`, `_run_manual`, any new one) produce plain Python data and hand it
  over with `_post(job)`. No `doModal()` in the XML chooser. A new row column = a property in `chooser_rows.build` + a control in the XML.
  `unit_chooser_threads.py` guards this; the pyxbmct chooser stays as the fallback.
- Long CPU loops call a `breathe()` (`core/kodi.make_breather`: a 1 ms nap every 20 ms keeps the viewer's own scripts starting). Keep it.

## Add-on updates and repositories (do not break)

- **Updates only in the Wizard's update window.** The build bakes `general.addonupdates` = 1 (NOTIFY). Only `provisioning/update_window.py`
  may set it to ON, always inside a `try / finally` that restores it. No code may post a blocking builtin (`executebuiltin(..., True)`) for
  `UpdateAddonRepos`, wait on Kodi's installs from a service, or ask the GUI thread from a poll loop that cannot leave. A long-lived thread
  of a build script ends within about a second of Kodi's abort request (Kodi waits for every thread of a script).
- **Park file** `special://temp/addon_update_window.flag` (refreshed during a window, stale after 90 s): a service of ours that polls the
  GUI makes NO GUI / player / JSON-RPC call while it is fresh (`script.fentastic.helper` and subtitles `core/kodi` `updates_installing`).
  A stop that finds a service inside a GUI call wedges Kodi; a new GUI-polling service checks the file too.
- **Repositories:** `repository.povil` keeps ONE `<dir>` (ours, `verify="sha256"`); every third-party `<dir>` lives in
  `repository.povil.upstream` with no `verify=`. Never add an upstream index that carries an id of ours (Kodi installs the highest version;
  use `FILTERED_UPSTREAMS` in `gen_repo.py`, as for Account Manager). A new third-party add-on = a `<dir>` there + `check_upstreams.REQUIRED`.
  A failing `<dir>` fails the WHOLE repository. Versions only go up and stay purely numeric. The Wizard never extracts an add-on zip itself
  (only `repo_bootstrap.py`, for the two repositories, after a sha256 check) and never `<import>`s `repository.povil`.
- **Config pack:** the baked `favourites.xml` is generated from `favourites_config.json` (`check_favourites.py --write`). Kodi 21's video
  cache is `guisettings.xml` `filecache.*` (384 MB / 1 MB chunk / read factor 1000), not `advancedsettings.xml`. `skin.fentastic`'s baked
  settings stay canonical via `.github/scripts/format_fentastic_settings.py [--check]`.

## Wizard rules

- **Skins are on demand:** only `CONFIG.DEFAULT_BUILD_SKIN` (`skin.fentastic`) is provisioned or background-updated. The Wizard never manages,
  patches or seeds a skin that is not installed (seeding goes through `skin_catalog.blocks_seeding`; repairs gate on the skin's `addon.xml`;
  registry entries of a skin are `optional`). Skin ids come from `skin_catalog.SKINS`. A new optional add-on = a row of
  `optional.OPTIONAL_ADDONS`, an install module, a gated tile.
- **Home routes come before the child gate:** `Router.dispatch` answers the Onyx modes, `tonight`, `search_provider`,
  `debrid_notice_settings` BEFORE `profile_hooks.deny_in_child_profile`; never move them below it or into `profiles/hooks._CHILD_ALLOWED`.
- **Patch registry:** `patch_engine.py` is additive: only `prepend_before` / `append_after`, each block fenced by markers and removable byte
  for byte. No "replace" / "regex" / "scoped anchor" actions. What needs a line replaced stays a runtime patcher in `host_repairs/`
  (marker-gated, `compile()`-checked, atomic, with `revert()`): `umbrella_tmdb_apikey`, `umbrella_list_order`, `af3_dialog_subtitles`,
  `af3_home`. A change to the registry is verified against a copy of the reference add-on in `_reference_addons/` (local, untracked): every
  entry applies once, every patched file compiles, no new pyflakes message, a second run is a no-op, disabling reverts byte for byte; a
  deleted entry is simply removed. Validator:
  `python plugin.program.kodipovilwizard/resources/libs/patches/patches_config.py plugin.program.kodipovilwizard/resources/libs/patches`.
- **A POV update erases our POV patches:** `host_repairs/flow/addon_update_watch.py` re-patches POV, Umbrella, Idan Plus and AF3 after Kodi
  replaces them; `_maybe_patch_pov_language_invoker` stays FIRST in the start-up pass (it races POV's own check).

## Verify before reporting done (no Kodi needed)

```
python .github/scripts/check_favourites.py
python .github/scripts/check_subs_contract.py            # --static skips the per-module import pass
python .github/scripts/gen_repo.py --out site --base-url https://devgeniuscode.github.io/Kodi-POV-IL-Build
python -m pyflakes <changed .py files>                   # compare with the messages already at HEAD
```

`check_subs_contract.py` = static checks (provider contract, frozen names, pinned strings, private names, single route, settings / strings
consistency) + every subtitle module importing under `subs_contract/_stubmaker.py` + `unit_chooser_threads.py`. A change to `he_sub_match.py`
or a stdlib-only module updates `subs_contract/baseline.json` in the same change. There is no golden harness. `site/` is git-ignored build
output. Parse every XML you touch.

## Manual Kodi test checklist (nothing here can be checked offline)

1. **Fresh install** completes (`fresh_build_auto_install_done`), installs only FENtastic as skin, POV's results window opens.
2. **Update window:** no freeze after boot; Maintenance -> manual update check installs updates; NEVER / NOTIFY is respected.
3. **POV patching:** after a POV update the log shows `addon_update_watch` + the PatchEngine pass within ~10 s, no English
   "SETTING/XML mismatch" dialog; Patch Manager toggles persist.
4. **Subtitles:** the subtitle button opens our chooser on every skin, Back / Esc closes it, no crash; manual search; a Hebrew pick plays;
   an English pick is AI-translated; results within `max_search_time` (10 s).
5. **FENtastic:** ratings show while scrolling Home / Videos and scrolling stays smooth; a Hebrew title with an English-only logo shows text.
6. **Optional add-ons screen:** Umbrella, Otaku, NOX, Onyx, AF3, Estuary install / switch; no `addon_data/` of a skin that is not installed.
7. **Movies:** "Networks" tiles open provider lists; list-manager context entries hidden for services that are not connected.
8. **Clean reinstall** (Maintenance): offline = nothing deleted; online restarts into a provisioned build.
9. **Memory:** POV network lists (animated GIF tiles) on a 2 GB Shield: watch RAM.

## Known issues and open notes (reported, not fixed; each needs the owner's go-ahead)

- `resources/libs/check.py:327` uses an undefined name `window` (broken-repositories view).
- `host_repairs/flow/af3_steps.py:63` imports `fentastic_search_patcher`, which does not exist (inside `try/except`, silently skipped);
  `profiles/build_skin.py:291` runs `prepare_build_skin`, which has no handler (waits 60 s, reports failure).
- Inert leftovers: the `subtitlesearch` / `ChooseSubtitlesButtonOpensKodiWindow` skin settings in the baked skin `settings.xml` files,
  `host_repairs/kodi_utils.subs_get_*` (no caller), `tonight/entrypoints.LEGACY_ICON`.
- 10 XML files (4 FENtastic, 4 NOX, 2 Wizard notification skins) are not well-formed (bare `&`, `--` in comments); Kodi's parser is lenient.
- Assets named by code or data but missing on disk (`povil_icons/switch_skin_pov_il.png`, `Shows_Apple_TV.png`, ...);
  `orderfavourites-hebrew/resources/media/fonts/` installs to `home/media/fonts/` while Kodi looks in `Fonts/` (check on the Shield).
- `povil_icons/Animated_*.gif` (7 files, 31-167 frames) are mapped to POV network tiles by `patches/pov_animated_networks`; decoded in full
  they could be tens of MB each (not measured on a device). If RAM is tight use static PNGs; the 512x512 tile PNGs (~1.1 MB) could be recompressed.
- `script.fentastic.helper/resources/lib/service.py` carries ten MDbList API keys in plain text (public repo): consider rotating them.
- Upstream MoranSubs Onyx 1.0.10-1.0.20 features (rating sites, home trailers, hub tab, player redesign) were skipped on purpose; they live
  in git at `96efe59a` (`git show 96efe59a:<path>`) and need a Wizard that provides their properties.
- The subtitle `changelog.txt` has no 0.3.110 entry.
