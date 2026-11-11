# Kodi‑POV‑IL Build — Developer & AI Agent Bible

> A modular, manifest‑driven Hebrew Kodi build centered on the **POV** video addon,
> the **FENtastic** skin, and an in‑house **AI Hebrew‑subtitles** service.
> This document is the canonical reference for humans **and** AI agents working on
> the repository. Read it fully before changing installation, config, asset, or
> cross‑addon‑patching code.

---

## Table of Contents

1. [Project Overview & Architecture](#1-project-overview--architecture)
2. [CI/CD & the Manifest Pipeline](#2-cicd--the-manifest-pipeline)
3. [The Installation Engine](#3-the-installation-engine)
    - [A. Modular Updater & Headless Installer](#a-modular-updater--headless-installer-headless_installerpy)
    - [B. OTA & Self‑Healing](#b-ota--self-healing)
4. [Build‑Config System (`config_policy.json`)](#4-build-config-system-config_policyjson)
5. [Assets & Dynamic Media Management](#5-assets--dynamic-media-management)
6. [Runtime Cross‑Addon Patching](#6-runtime-cross-addon-patching)
7. [The AI Subtitles Service](#7-the-ai-subtitles-service)
8. [Skins & the Quick‑Update / Force‑Close UX](#8-skins--the-quick-update--force-close-ux)
9. [Profiles](#9-profiles)
    - [9.1 Overview](#91-overview)
    - [9.2 Architecture & Module Layout](#92-architecture--module-layout)
    - [9.3 Profile Seeding & Configuration Management](#93-profile-seeding--configuration-management)
    - [9.4 Lifecycle & Data Flows](#94-lifecycle--data-flows)
    - [9.5 Parental Controls & Content Guard Architecture](#95-parental-controls--content-guard-architecture)
    - [9.6 Skin Integration & XML Patching Strategy](#96-skin-integration--xml-patching-strategy)
    - [9.7 Native UI Automation](#97-native-ui-automation)
    - [9.8 Developer Guide](#98-developer-guide)
10. [Removed / Legacy Components](#10-removed--legacy-components)
11. [Repository Layout & Addon Inventory](#11-repository-layout--addon-inventory)
12. [Strict Guidelines for AI Agents & Contributors (CRITICAL)](#12-strict-guidelines-for-ai-agents--contributors-critical)
13. [Git & Release Workflow](#13-git--release-workflow)

---

## 1. Project Overview & Architecture

### From monolith to modular

The build used to ship as a **legacy monolithic model**: one massive `build.zip`
containing every addon + all of `userdata/`, applied by a brute‑force wizard that **wiped** the device and extracted the
whole payload. That approach was fragile —
any single corrupt/oversized file failed the whole install, updates meant
re‑downloading hundreds of MB, and user data (Debrid logins, Trakt auth, custom
favourites) was casually destroyed.

The current architecture is **modular and manifest‑driven**. Every addon is an **independent, versioned zip**; the build
state is described by a single
`manifest.json`; and the device is *hydrated* and *updated* per‑addon, at the
value/setting level, **without wiping anything**.

```
┌──────────────────────────────────────────────────────────────────────┐
│  GitHub (source of truth)                                              │
│    repo folders ──CI──► dist/*.zip ──► Release "addons-latest"         │
│                                  └────► manifest.json  (versions,sha)  │
│                                  └────► config-<ver>.zip  (userdata/)  │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  HTTPS (manifest.json + zips)
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Device: plugin.program.kodipovilwizard                               │
│    ModularUpdater ── diff versions ── download+sha+extract ── DB reg   │
│    HeadlessInstaller ── resolve deps from repos ── silent zip extract  │
│    config_apply ── merge userdata per config_policy.json (LAST)        │
│    startup.py ── .provisioned marker ── heal_missing_addons (boot loop)│
└──────────────────────────────────────────────────────────────────────┘
```

Three classes of payload ship from CI:

| Payload                | What                                                                                                                 | How the device consumes it                                                                                                                                |
|------------------------|----------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Addon zips**         | Our private addons + the third‑party **repository** addons + skins                                                   | `manifest.json` → download + sha256 verify + extract to `special://home/addons`                                                                           |
| **`config-<ver>.zip`** | The entire `userdata/` tree (settings, favourites, sources, skin settings, POV settings)                             | Applied by `config_apply.py` per `config_policy.json` — **always the final step**                                                                         |
| **Content addons**     | `plugin.video.pov`, `plugin.video.idanplus`, `plugin.video.otaku`, `plugin.video.youtube`, `resource.language.he_il` | **Not vendored.** Installed at runtime by the **Headless Installer** from their own repos so they keep getting OTA updates from their original developers |

### Synchronous, isolated installation

Installation is a **sequential queue**, not a parallel free‑for‑all:

- **Downloads** can run concurrently (the foreground install‑manager UI streams up
  to a few at once), but **extraction + DB registration happen one addon at a
  time**, in dependency order (deps before dependents).
- Dependency resolution is handled **gracefully and headlessly** (see
  [§3.A](#a-modular-updater--headless-installer-headless_installerpy)). When the
  native installer must be used as a last resort, it is wrapped in an **isolated
  micro‑watchdog** that touches **only** the dependency‑confirmation dialog — never
  the rest of the UI.
- The default unit of install is a **raw zip extraction** into
  `special://home/addons` followed by `UpdateLocalAddons`, mirroring exactly how
  Kodi itself lays an addon down on disk.

### Critical invariant: `config.zip` is ALWAYS last

`config-<ver>.zip` carries `userdata/`, including **POV's own `settings.xml`**
(Hebrew metadata language, fanart, TMDB/Trakt keys) and the skin settings. It **must be extracted/applied as the
absolute final step of a fresh install — after
every addon is on disk** — so that nothing an addon writes at install/first‑init
can clobber our configuration. Applying it earlier reintroduces the *black‑background + English‑metadata* race. This
ordering is enforced in
`modular_updater.ModularUpdater.execute_updates()`.

### Active-profile paths

`CONFIG.USERDATA` is the **active profile's** userdata, not Kodi's `special://userdata/` alias (which always points at
the master). Everything the engine keeps per profile — the `kodipovil.provisioned` marker, favourites,
`config_applied_version`, GUI defaults — lives with the profile it describes. `CONFIG.MASTERPROFILE` and
`CONFIG.PROFILES` (`profiles.xml`) address the shared, master-owned files. Engine code must therefore never assume
`CONFIG.USERDATA` is the master; see [§9](#9-profiles).

---

## 2. CI/CD & the Manifest Pipeline

**Workflow:** `.github/workflows/build-and-release.yml` (triggers on `push` to
`main`).

Pipeline steps:

1. **Detect changed addons** — only addons whose folder changed this push are
   rebuilt (plus the config zip when any `userdata/` file changed).
2. **Build zips** — `.github/scripts/build_addons.py` packages each changed addon
   into `dist/<id>-<version>.zip`. `.github/scripts/build_config.py` packages all
   of `userdata/` into `dist/config-<version>.zip` (deterministic: sorted order,
   fixed timestamps). The config version comes from
   `userdata/config_policy.json → config_version`.
3. **Publish to the rolling release** (`addons-latest`): for each *changed* addon,
   its previous zip is deleted and the new one uploaded (`--clobber`).
   `*-latest.zip` stable pointers (e.g. the wizard bootstrap) are preserved.
4. **Regenerate `manifest.json`** — `.github/scripts/gen_manifest.py` discovers
   every addon via `kodi_addons.discover_addons()`, parses each `addon.xml`, and
   records `id / name / version / type / filename / zip URL / size / sha256 /
   updated`. Values for addons **not** rebuilt this run are carried over from the
   previous manifest **only if id+version still match**. The generator **refuses to
   write a manifest with a null size/sha256** (that would mean a version bump with
   no published zip → a 404 on install) and fails loudly instead.
5. **Prune orphaned release assets** — reconciles the release against the fresh
   manifest and deletes any zip it no longer references (keeping `*-latest.zip`).
   This is what removes the last zip of a *deleted* addon (the per‑addon delete in
   step 3 only runs for addons that still exist and changed).
6. **Commit `manifest.json`** back to `main` with `chore(manifest): ... [skip ci]`.

**Key consequence for contributors:** to ship a change you only **bump the
`addon.xml` version** (or `config_version`). CI builds, publishes, and regenerates
the manifest. Never hand‑edit `manifest.json` — it is regenerated every run.

---

## 3. The Installation Engine

Owner addon: **`plugin.program.kodipovilwizard`**.

### A. Modular Updater & Headless Installer (`headless_installer.py`)

`resources/libs/modular_updater.py` orchestrates the entire deployment pipeline as a **single, continuous UI experience
**. It coordinates the local asset extraction (Phase 1) and seamlessly transitions into the third‑party content addon
provisioning (Phase 2) without ever closing the custom installer window.

**1. Manifest Phase (Local Addons & Repositories)** — `ModularUpdater.execute_updates()`:

- Diffs local vs manifest versions (`run_update_check`) and only touches addons **already installed** unless
  `install_missing` or `fresh` is set (ensuring unselected on‑demand skins are ignored).
- Per addon: download → **strictly verify sha256** (enforced for manifest items) →
  `extract.all(zip, CONFIG.ADDONS, ignore=True)` → SQLite database registration in `Addons??.db` → `UpdateLocalAddons`.
- Runs via the rich `install_manager` UI featuring parallel bounded downloads and real-time extraction progress tracks.

**2. The Synchronization Gap & Transition:**

- Once Phase 1 finishes, the Orchestrator executes a 2.5-second database settling pause (`waitForAbort(2.5)`) to ensure
  the SQLite database is completely unlocked and Kodi finishes reloading newly extracted local repo structures.
- Concurrently, the UI window enters a visual `pause_for_resolution()` state (displaying a "Calculating dependencies /
  מנתח מאגרים..." status label), maintaining a live, uninterrupted window context.

**3. Headless Content‑Addon Provisioning** — `headless_installer.HeadlessInstaller`:

- **Resilient Manifest Fetching:** It parses repositories already on disk (ours + Kodi's official
  `repository.xbmc.org`). To prevent the post-extraction Python SSL race condition (where rapid folder extraction
  thrashes Python's internal certificate verification, causing an `SSLCertVerificationError`), the fetching engine
  automatically falls back to Kodi’s native C++ abstraction layer (`xbmcvfs.File()`) to pull the remote
  `addons.xml(.gz)`.
- **Dynamic Dependency Closure:** Parses repository XMLs into a unified union index. When resolving dependencies, it *
  *dynamically maps addon types based on ID prefixes** (e.g., `script.module.*` -> `module`, `resource.language.*` ->
  `language`) to prevent database and folder structure corruption, instead of blindly forcing a `'plugin'` type.
- **Dynamic Queue Injection:** Resolved content addons and their dependencies are injected live into the active UI via a
  thread-safe `append_to_queue()` method. During Phase 2 downloading, the UI **gracefully bypasses SHA256 verification**
  since upstream 3rd-party repositories omit file hashes, preventing fatal `KeyErrors`.
- **Resolution Resilience:** If an addon's dependency tree fails to resolve, a bounding fix ensures the parent addon
  doesn't drop into a logic "black hole"; it bubbles up to `self.unresolved` to trigger the native fallback.

**4. Binary Addon Protection & Native Fallback:**

- Binary / platform‑specific addons (`inputstream.*`, `pvr.*`, etc.) are **NEVER extracted blindly**, as zip packages
  must match the device's host OS/architecture/ABI.
- Any binary dependency or unresolvable upstream addon is deferred to the **Minimal Native Fallback** pipeline. This
  triggers *only after* our custom GUI cleanly closes. It executes a background native `InstallAddon(<id>)` via a sleek,
  non-intrusive Kodi status bar overlay (`xbmcgui.DialogProgressBG()`), backed by a micro‑watchdog that auto‑accepts *
  *only window `10100`** (the dependency confirmation dialog, control `11`).

**Provision order** (`PROVISION_IDS`) is deliberate and ends with the heaviest:

```
plugin.video.pov  →  plugin.video.idanplus  →  plugin.video.youtube
  →  resource.language.he_il  →  plugin.video.otaku   (LAST: heavy + timeout‑prone)
```

Otaku is last so a transient Otaku stall can never block the core build; the boot loop re‑installs it next launch.

### B. OTA & Self‑Healing

- **`.provisioned` marker** (`kodipovil.provisioned` in the **active profile's** userdata, `CONFIG.USERDATA`). Written **only** when a fresh install reaches
  the very end (every addon attempted **and** `config.zip` applied) *and* Kodi is not aborting. `startup.py` uses it to
  tell a *completed* setup from one the user force‑closed mid‑provisioning, so an interrupted setup is **resumed** next
  launch instead of left half‑built. `run_fresh_install()` is idempotent — present addons are skipped, the config pack
  self‑applies once.
  Secondary profiles receive the marker when they are seeded ([§9.3](#93-profile-seeding--configuration-management)).
- **`heal_missing_addons()`** — a background boot‑loop self‑heal. On startup it verifies with `System.HasAddon(...)`
  that every required manifest addon (minus on‑demand skins) and every `PROVISION_IDS` content addon is physically
  present. If anything is missing, it runs the **exact same headless pipeline silently in the background** without
  invoking the `install_manager` GUI thread. This architecture guarantees the build will seamlessly self‑complete over a
  couple of launches if an installation is ever partially interrupted.
- **Graceful reload, not crash.** A forced restart is reserved for *critical* updates only (the Wizard itself, or the *
  *currently active** skin whose live files can't be hot‑swapped). Everything else — plugins, services, inactive skins,
  or a config change that only touched the active skin's look — is applied in place with **`ReloadSkin()`**. We do **not
  ** kill Kodi to apply ordinary updates.
- **First‑Boot Stabilizer (race shield).** The first boot after an install is a race: FENtastic fires parallel widget
  directory queries at `plugin.video.pov` while POV's service creates its databases for the first time, which can fail
  the directory calls or deadlock the skin‑variable engine (the "second boot is always fine" symptom).
  `first_boot_stabilize_if_needed()` runs after `wait_for_gui_ready()` and is a **no‑op unless the one‑shot marker
  `kodipovil.first_boot_stabilize` exists**. When it does, it holds briefly with a user‑visible Hebrew countdown while
  POV warms up (bounded by `first_boot_warmup_seconds`, default 25 s, early‑exit once `System.HasAddon(plugin.video.pov)`
  plus a minimum warm‑up), then runs `UpdateLocalAddons` + `ReloadSkin()`. It never patches the skin, uses no global
  watchdog, and **always clears the marker in a `finally`** so a failure can never trap Kodi in a stabilize loop. Fresh
  installs now finish live (below), so the fresh‑install path no longer arms the marker.
- **Verified completion path (`fresh_install.py`).** One path serves both the automatic startup install and a manual full
  installation, and a fresh install is reported complete only after it is verified in the running session — there is
  **no forced restart**.
    - `begin()` drops the `kodipovil.modular_install_started` marker; `provision()` resumes only the missing work (up to
      three attempts) and records a local diagnostic (`kodipovil.install_failure.json`, add‑on IDs only — never URLs or
      accounts). Completion still requires the updater's verified `.provisioned` marker.
    - `finalize()` succeeds only when: the required add‑ons are enabled; media and favourites were installed and
      verified; `PatchEngine().run()` reports no `failed` / `missing` / `anchor_missing` / `malformed`; the GUI defaults
      snapshot (`kodipovil.fresh_gui_defaults.xml`) was applied through Kodi's JSON‑RPC with the **skin set last** via
      `build_skin.activate`; the favourites were refreshed for that skin and the home shortcuts seeded; and Kodi's cached
      favourites match (`live_favourites_ready()`, at most two profile reloads, never a loop and never a close).
    - The same live‑defaults and favourites functions complete a secondary profile's home ([§9.4](#94-lifecycle--data-flows)).

---

## 4. Build‑Config System (`config_policy.json`)

`userdata/config_policy.json` is the declarative policy for how the shipped
`userdata/` is merged onto a device. `userdata/` is packaged whole into
`config-<config_version>.zip`; **only files listed in the policy are updated**; a shipped file that is not in `files[]` is *seeded once* when absent and never changed afterwards, so it never reaches existing devices — see the POV
settings lesson below). **Bump `config_version` whenever any `userdata/` file
changes** — that version bump is what makes the wizard detect and apply the new
config.

Per‑file apply **modes**:

| Mode             | Behaviour                                                                                                                     |
|------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `replace`        | Overwrite the whole destination file.                                                                                         |
| `merge_id`       | Per `<setting id=...>`: **build value wins**, every *other* user setting is left untouched. `exclude_ids[]` is never written. |
| `merge_name`     | Per `<source><name>`: add the build's sources, keep the user's.                                                               |
| `seed_if_absent` | Write only when the destination doesn't already exist.                                                                        |

Each file declares a `fresh` mode (clean/first install) and an `update` mode (existing device).

### The POV settings lesson (credential‑safe OTA)

`userdata/addon_data/plugin.video.pov/settings.xml` was shipped in the zip but was **not in the policy**, so it was
**never merged or updated** — POV ran on its English /
no‑fanart defaults. The fix added it to the policy as:

- `fresh: replace` — land the full config (`meta_language=he`, `get_fanart_data=true`,
  TMDB/Trakt app keys, provider toggles).
- `update: merge_id` with **every credential id in `exclude_ids`** — so a config
  update pushes design/language fixes to existing devices **without wiping the
  user's My Services logins** (Real‑Debrid / Trakt / Premiumize / TorBox /
  AllDebrid / Offcloud / EasyNews / MDBList / TMDB tokens, usernames, session and
  account ids, RPDB key, watched‑indicator state, …).

> **Rule:** any settings file that contains *both* build defaults *and* user
> secrets uses `update=merge_id` with the secret ids excluded. Build wins on
> design; user wins on credentials.

### Applied pack, GUI defaults and the retained verified copy

- **GUI defaults snapshot.** On a fresh install `guisettings.xml` is also written, with the policy's machine‑specific
  `exclude_ids` removed (screen resolution, device uuid/name, …), to `kodipovil.fresh_gui_defaults.xml`. Kodi owns its
  in‑memory settings, so `fresh_install.apply_live_defaults()` applies them through JSON‑RPC instead of overwriting the
  file.
- **Retained verified pack.** When the manifest supplies a sha256 and the pack verifies, `config_apply` keeps a copy in
  the **master** profile (`addon_data/plugin.program.kodipovilwizard/config_pack/build_config.zip` plus a
  `build_config.json` receipt). Secondary profiles are seeded from it through `config_apply.open_verified_pack()`; the
  Wizard ships no config bundle of its own. A manifest without a sha256 still applies, but is never retained. API and
  seeding rules: [§9.3](#93-profile-seeding--configuration-management).

---

## 5. Assets & Dynamic Media Management

### Centralized flat icon set

- The canonical **source** of the build's custom icons is a **flat** directory
  `plugin.program.orderfavourites-hebrew/povil_icons/` (no nested subfolders).
- On startup, `plugin.program.orderfavourites-hebrew/resources/lib/media_installer.py`
  **deploys/overwrites** that set into the **global media cache**
  `special://home/media/povil_icons/` (and fonts into `special://home/media/Fonts/`).
  Kodi/skins/favourites read icons from the **global folder**, never by reaching
  into another addon's private folder (avoids cross‑addon containment breaches).
  The installer overwrites on every run so icon updates always reach users.

### JSON‑driven favourites

- `plugin.program.orderfavourites-hebrew/resources/favourites_config.json` is the
  single source of truth: an `icon_base` (`special://home/media/povil_icons/`), a
  dictionary of named tiles (`name` / `icon` / `action`), and per‑skin ordered tile
  lists (with `inherit` + per‑tile `overrides`).
- `favourites_generator.generate_favourites_xml(skin_id, merge=True, write=True)`
  builds a valid `favourites.xml` for the active skin and writes it to
  `special://userdata/favourites.xml`. `merge=True` preserves any custom tiles the
  user added themselves (anything whose name isn't one of ours).
- Because the build is **Trakt/TMDB‑driven**, `favourites.xml` holds **only
  internal skin shortcuts** — no user library data — so it is safe to **overwrite**
  on update (`config_policy.json` applies it `replace`) to push UI/shortcut/icon
  fixes to existing users.

---

## 6. Runtime Cross‑Addon Patching

### 🛠️ Metadata-Driven Patching Architecture (Engine v2)

Our build system employs a highly decoupled, declarative, and metadata-driven patching architecture managed exclusively
by the **Wizard Addon (`plugin.program.kodipovilwizard`)**.

Instead of executing standalone runtime scripts that perform repetitive file I/O operations and inject heavy logic, the
Wizard runs a **Unified Patching Engine**. This engine ingests static configuration dictionaries (metadata), groups
operations by target files, and executes atomic, additive injections in a single pass.

### 📐 Core Architectural Principles

1. **Additive Patching (Lean Hooks)**
   To prevent upstream breaks, patches *never* overwrite or perform brittle multi-line block replacements of native
   code. Instead, they use strict, single-line string anchors to seamlessly insert an ultra-lean execution hook either
   immediately before (`prepend_before`) or after (`append_after`) a target code line.

2. **Decoupled Business Logic & Single Directory Consolidation**
   Target addon files are kept completely clean of heavy business logic or algorithms. Injected hooks act purely as
   routers—appending our core patches path and calling external helper modules hosted natively inside the Wizard addon.
   All metadata definitions and extracted logic modules MUST reside strictly inside a single unified directory:
   `plugin.program.kodipovilwizard/resources/libs/patches/`. Extracted logic files must be prefixed with the target
   addon shorthand (e.g., `pov_` for `plugin.video.pov`).
   Hooks locate those modules through the `patch_dir` argument of `_build_patch_hook` (default `PATCHES_DIR`). The one
   deliberate exception is the parental guard: `profile_age_guard` is a feature module ([§9.5](#95-parental-controls--content-guard-architecture)) and lives in `resources/libs/profiles/`; its patches pass
   `patch_dir=GUARD_DIR`.

3. **Standardized Hook Construction (`_build_patch_hook`)**
   Every hook that calls a logic module is generated by the `_build_patch_hook()` helper in `patches_config.py`, never
   written by hand. The helper emits one self-contained, fail-safe block (path setup, import, custom logic, error
   logging), so a patch never depends on a name injected by another patch. A failing *standard* patch is logged instead of
   breaking the host addon; a failing *enforcement* patch (`fail_closed=True`) is logged and re-raised. See
   *Writing Hooks* below.

> **Note:** Injected hooks must avoid over-engineering or overcomplicating. They should remain simple, maintainable, and
> straightforward.

4. **Strict Tab Indentation (`\t`)**
   Injected lines inside multi-line hook definitions MUST explicitly utilize tab characters (`\t`) for scope alignment
   to prevent Python `IndentationError` exceptions when evaluated against upstream source code. `_build_patch_hook()`
   already emits tabs for its own structure; the `indent` argument and the lines of `custom_logic` must follow the same
   rule. The engine dedents each hook and re-indents it to the anchor line's own indentation, so only the relative
   tabs inside the block matter; `indent` documents the depth.
5. **Versioned Markers & Anti-Collision**
   Every injected hook is uniquely identified by a versioned marker prefixed with `# WIZARD_` (e.g.,
   `# WIZARD_POV_FEATURE_NAME_v2`). The engine uses the base string identifier to prevent duplicate injections,
   seamlessly upgrading older hook versions (`_v1` -> `_v2`) or executing clean rollbacks without risking source
   corruption.
6. **Clean Environment Assumption (YAGNI)**
   The deployment workflow enforces 100% clean installations. Because target files are guaranteed to be pristine,
   unpatched upstream source files at execution time, the runtime engine does not waste cycles on legacy regex scrubbing
   or historical backward-compatibility logic.
7. **Static Configuration Priority**
   Following a "Configuration over Code" philosophy, all environmental variables, addon settings, XML skin elements, and
   initial database structures (e.g., SQLite schemas) are pre-baked statically into our controlled `userdata`
   distribution. Runtime hooks are strictly reserved for code execution routing that cannot be achieved via static
   files.
8. **Scope & Determinism of Dynamic Patching**
   Dynamic patching is limited to add-ons the build does not own and that Kodi updates over the network: POV, Umbrella
   and, for the child-profile source guard only, Idan Plus. Add-ons we ship (the Wizard, the favourites generator, the
   subtitles service, the skins) carry their fixes in their own source. Anchors are never guessed: there are no
   alternative anchors or target files, and when an upstream anchor changes the patch fails deterministically
   (`anchor_missing`) and must be updated.

---

### 🗂️ Patch Definition Schema

Each patch configuration is declared as a structured Python dictionary within the Wizard's central patch registry
(`patches_config.py`). New patches are appended to the registry; existing patches are never reordered, so branches stay
diffable.

```python
{
    "id": "pov_source_remember",
    "name": "Remember Last Played Source",
    "description": "Captures the last played source in POV and persists it for future sessions.",
    "category": "ux",
    "priority": "major",
    "addon_id": "plugin.video.pov",
    "enabled": True,
    "target_file": "resources/lib/modules/sources.py",
    "marker": "# WIZARD_POV_SOURCE_REMEMBER_v2",
    "anchor": "def play_file(item):",
    "action": "append_after",
    "hook": _build_patch_hook(
        module_name="pov_source_remember",
        indent="\t",
        custom_logic="pov_source_remember.capture(item)"
    )
}
```

| Field         | Allowed values / meaning                                                                                                                                   |
|---------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `category`    | `translation`, `ui`, `ux`, `performance`, `subtitles`, `crash_guard`, `account_manager`, `parental_control`, `reliability`, `diagnostics`, `compatibility` |
| `priority`    | `critical`, `major`, `minor`                                                                                                                               |
| `action`      | `prepend_before` or `append_after` (additive only; `replace` is forbidden)                                                                                 |
| `marker`      | `# WIZARD_<ADDON>_<NAME>_v<X>`                                                                                                                             |
| `description` | A description ending in `[NEEDS_MODULE_CHECK]` is queued for Phase 2 module inspection                                                                     |

> `parental_control` patches are verified **as a group, at the point of use**, through a category-scoped engine run
> (see [§9.5](#95-parental-controls--content-guard-architecture)). Enforcement hooks in this category must use
> `fail_closed=True`.

---

### ✍️ Writing Hooks

#### `_build_patch_hook(module_name, custom_logic='', indent='', patch_dir=PATCHES_DIR, fail_closed=False)`

| Parameter      | Meaning                                                                                                                                                                                                                                   |
|----------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `module_name`  | The logic module imported from `patch_dir` (e.g. `"pov_ui_labels"`). Its name is also used in the error log.                                                                                                                              |
| `custom_logic` | Code executed after the import, written **without** the helper's own indentation. Nested blocks use tabs (`\t`). `return`, `continue` and `raise SystemExit` work as in any other code, because the logic runs inside the helper's `try`. |
| `indent`       | The depth of the hook in the target file (`"\t"` inside a function body, `"\t\t"` inside a method, and so on); empty for module-level hooks. The engine dedents the block and re-indents it to the anchor line, so only relative tabs inside the block are significant. |
| `patch_dir`    | Folder appended to `sys.path` before the import. Default `PATCHES_DIR` (`.../resources/libs/patches/`). Parental guard patches pass `GUARD_DIR` (`.../resources/libs/profiles/`).                                                            |
| `fail_closed`  | `False` (default): the error is logged and the host carries on. `True`: the error is logged and **re-raised** — mandatory for hooks that enforce an access decision.                                                                       |

For the schema example above, the helper generates (tabs shown as spaces):

```python
try:
    _povil_dir = __import__('xbmcvfs').translatePath('special://home/addons/plugin.program.kodipovilwizard/resources/libs/patches/')
    if _povil_dir not in __import__('sys').path:
        __import__('sys').path.append(_povil_dir)
    import pov_source_remember
    pov_source_remember.capture(item)
except Exception:
    __import__('xbmc').log('Wizard Patch Error [pov_source_remember]:\n' + __import__('traceback').format_exc(), __import__('xbmc').LOGERROR)
```

With `fail_closed=True` the block ends with an extra `    raise` inside the `except`. Always compare this shape with the
helper's real output when you change either of them (`python patches_config.py` compiles every hook).

#### Behavior rules

1. **Standard hooks fail open (`fail_closed=False`).** An exception inside the block is written to the Kodi log at
   `LOGERROR` level with the module name and the full traceback (including the failing line number). The host addon
   keeps running.
2. **A failed standard hook means "patch disabled".** Execution simply continues with the native code that follows the
   hook. Hooks that `return` a value therefore fall back to the native behavior when the patch module fails.
3. **Enforcement hooks fail closed (`fail_closed=True`).** Hooks that grant or deny access (routes, playback, directories,
   sources) log the same traceback and then re-raise, so a missing or broken guard stops the host instead of letting a
   child profile through. Only enrichment hooks that merely annotate data (for example stamping a classification) may fail
   open, because an unannotated item is treated as blocked. See [§9.5](#95-parental-controls--content-guard-architecture).
4. **`SystemExit` is not swallowed.** Hooks that end the script early (`raise SystemExit`) work, because the helper only
   catches `Exception`.
5. **Code needed even when the module fails goes after the helper call, at the same indent.** Example: the TorBox URL
   fix returns the raw API response after the helper call, so the native request is not repeated when the patch module
   fails:

   ```python
   "hook": (
       "\t\t_povil_raw = self.api('get', path, params=params)\n"
       + _build_patch_hook(
           module_name="pov_torbox_url_fix",
           indent="\t\t",
           custom_logic="return pov_torbox_url_fix.safe_url(_povil_raw)"
       )
       + "\t\treturn _povil_raw\n"
   )
   ```

6. **Conditional imports keep the condition outside the helper.** If the import must only happen when a condition holds
   (for example only for empty cache results), write the `if` line as a plain string and call the helper one indent
   level deeper.

#### The helper avoids host-name pollution

Inside a function, an `import` statement turns that name into a **local of the whole function body**. A hook that did
`import sys, xbmcvfs` would raise `UnboundLocalError` wherever the host uses those names earlier in the same function.
The helper therefore reaches `sys`, `xbmcvfs`, `xbmc` and `traceback` only through `__import__(...)` and creates no named
variables other than `_povil_dir` and the logic module. Custom logic follows the same rule: do not introduce generic
names (use the `_povil_` prefix for temporaries).

#### When a raw string hook is still correct

Use a plain string (no helper) when the hook:

- imports nothing from the patches directory (pure substitutions such as a changed constant, a shadowed variable or a
  reordered return),
- defines a method whose `except` clause needs the patch module (for example an exception class defined in it),
- replaces a constructor, where a swallowed failure would leave a half-initialised object,
- relies on the patch module raising its own exception (diagnostics that enrich an error).

Add a short Python comment next to the `"hook"` key stating why it stays raw.

#### Shared hooks

Identical hooks used by several patches are defined once as `_*_HOOK` constants (or a small factory such as
`_debrid_unbound_hook(provider)`) above `PATCH_CONFIG`, and referenced from each patch. Do not copy a hook between
patches.

---

### ✅ Validating the Registry

`patches_config.py` can be run directly and is part of the pre-merge checklist:

```bash
python patches_config.py                      # schema checks only
python patches_config.py path/to/patches/     # also verifies that every imported patch module exists
```

The validator checks that all required fields are present, `category`, `priority` and `action` use allowed values, `ids`
and `(target_file, marker)` pairs are unique, markers follow the `# WIZARD_<ADDON>_<NAME>_v<X>` format, and every hook
ends with a newline and compiles at its own indentation depth. It exits with a non-zero status when problems are found.
It is never executed inside Kodi.

### 🚦 Running the Engine

| Call                                             | Use                                                                                                                                                                                  |
|--------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `PatchEngine().run()`                            | Full pass over every enabled patch: repairs, reports stats. A patched file must compile before it is written.                                                                          |
| `PatchEngine().run(category='parental_control')` | The same, restricted to one `category`. Entries are selected **before** schema validation, so unrelated malformed or drifted patches neither block nor are touched by the run.       |
| `PatchEngine.all_applied(stats)`                 | True only if every selected patch is present and current (`applied`, `upgraded`, `skipped_current`, or `optional_absent`). False for a failed, missing, unanchored or disabled patch, and for an empty selection. |
| `PatchEngine().run_if_changed()`                 | Boot fast path after a write-free full verification; any changed host file, schema, config, override or install path falls back to a full pass. Scheduling information only.       |

Stats keys: `selected`, `applied`, `upgraded`, `skipped_current`, `removed`, `skipped_disabled`, `optional_absent`,
`missing`, `anchor_missing`, `failed`, `malformed`. The engine treats only `plugin.video.umbrella` as an optional host
(absent = `optional_absent`); any other absent host counts as `missing`.

### 🔁 Lifecycle & Upstream Updates

* **On Addon Updates:** When Kodi updates an upstream addon, it wipes the directory and extracts pristine source files,
  naturally reverting all patches. The Wizard engine detects missing markers on next boot and reapplies hooks
  seamlessly. An explicit `PatchEngine().run(...)` repairs immediately; Profiles uses a category-scoped run to verify
  the parental hooks at the point of use ([§9.5](#95-parental-controls--content-guard-architecture)).
* **On Rollbacks / Disabling:** By setting `"enabled": False` or removing a configuration, the engine targets the
  specific versioned marker and drops only injected lines, instantly returning the file to its original upstream state.
* **On Hook Changes:** Bump the marker suffix (`_v1` → `_v2`). The engine scrubs the stale block and injects the new
  one in place; no extra bookkeeping is needed.

---

## 7. The AI Subtitles Service

**`service.subtitles.kodipovilai`** ("MoranSubs") is the build's Hebrew‑subtitles engine. Cross‑addon patches are **not** hosted here: they live in the Wizard's
patch registry ([§6](#6-runtime-cross-addon-patching)). It also ships the home‑shortcut writers (`tonight`, recent
updates) that fresh installs and Profiles seed.

- **AI translation:** translates English (or other) subtitles to Hebrew on the fly
  via Google's Gemini Flash‑Lite API (user brings a free key from
  `aistudio.google.com`). It is **gender‑aware** — it pulls cast metadata from TMDB (via `script.module.tmdbhelper`) to
  choose correct Hebrew verb/adjective gender
  forms. It **falls back to existing human Hebrew subtitles** when available and
  never wastes API quota on already‑translated content.
- **Gemini setup** is reached from POV's *My Services* menu (the injected Gemini
  entry) and runs in the addon's own `default.py` (`action=connect_gemini`) via
  `RunScript`, so the injected hook stays a tiny click‑forwarder.
- It provides the **DarkSubs‑style** auto‑subtitle UX (immediate top overlay,
  per‑source status) **natively** — the old DarkSubs/`All_Subs` addon it replaced
  is gone (see [§10](#10-removed--legacy-components)). Comments that mention
  "DarkSubs" describe the UX being replicated, not a live dependency.

---

## 8. Skins & the Quick‑Update / Force‑Close UX

**Skins** ship in the manifest; how they are installed depends on the flow:

- **`skin.fentastic`, `skin.povil.nox`, `skin.estuary`** are repository skins and carry the Profiles UI in their own
  XML ([§9.6](#96-skin-integration--xml-patching-strategy)). On a fresh install the activated skin is the
  `lookandfeel.skin` value of the config pack (fallback `skin.povil.nox`), set last through `build_skin.activate`;
  secondary profiles start in `skin.povil.nox`.
- **`skin.povil.nox`** (large) is provisioned on a fresh install and is *on‑demand* afterwards (`ON_DEMAND_SKINS`: the
  updater never force‑installs it on an existing device). **`skin.arctic.fuse.3`** (AF3) is installed only through the
  wizard's *Switch Skin* flow (`post_install_provisioning(ids=[...])`, headless‑first) and is the only skin patched at
  runtime.
- **`skin.estuary`** — kept as the safe fallback skin.

**Skin switching is live.** `build_skin.switch()` enables the skin and its dependencies, activates it through Kodi's
settings API, accepts Kodi's own retention question with the scoped confirmer, and refreshes the home — no restart.

**Quick Update** is the user‑facing "fast update" button: a favourite/menu action
`PlayMedia(plugin://plugin.program.kodipovilwizard/?mode=install&action=quick_update&...)`
that runs the **manifest‑based** update (version diff → modular download/extract →
config merge). It is **not** a monolithic re‑download.

**Force‑close** (`wizard.force_close_kodi_in_5_seconds(...)`) is used **only** when
a critical update genuinely requires a restart (the wizard itself or the active
skin). It is a controlled, announced shutdown — **never** a way to "apply" ordinary
updates and **never** a crash. Fresh installs and skin switches finish **live**; there is no forced
restart (see the verified completion path in [§3.B](#3-the-installation-engine)).

---

## 9. Profiles

> **Scope.** Everything about the *"מי צופה? — פרופילים"* feature: native Kodi profiles with build defaults, adult and
> child profiles, the picker and login screen, parental age filtering across the content add-ons, and the NOX player
> controls that ship with it. All feature code lives in `plugin.program.kodipovilwizard/resources/libs/profiles/`.
> Read this chapter before touching profile seeding, the age guard, the skins' child Home, or any `parental_control`
> patch.

### 9.1 Overview

Kodi owns profiles: `profiles.xml`, PIN locks, the in-memory profile list and each profile's own `userdata`. The Wizard
**never edits `profiles.xml`**. It adds, on top of native profiles:

| Capability              | What it does                                                                                                                                                         |
|-------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Build defaults**      | A new profile is seeded from the build's hash-verified config pack, so it starts with the same skin, favourites, language and add-on settings as the master (9.3).   |
| **Picker & login**      | A "who is watching" card window (`mode=profiles`) and a branded login screen, opened from the main menu, the AF3 tools list and a seeded home favourites tile.                                 |
| **Adult & child types** | A child profile gets a restricted child Home, an age limit (0-17), per-title parental approvals, and enforced filtering in POV, Umbrella and Idan Plus (9.5).        |
| **Explicit sharing**    | The parent may copy selected service accounts (Real-Debrid, AllDebrid, Premiumize, TorBox, Trakt, MDBList, TMDB) into a profile. Nothing is copied implicitly.       |
| **NOX player controls** | Player-style chooser, player settings, "change source" and "episodes" routes for the NOX skin (`player_styles.py`).                                                  |

Design rules that hold everywhere in the feature:

1. **Kodi is authoritative.** Native dialogs create, lock and delete profiles; the Wizard only automates the native
   screens and seeds *files* around them (9.7).
2. **Seed, never overwrite.** Seeding creates missing files only, is idempotent, and resumes after interruption.
3. **Accounts never travel implicitly.** Defaults come from the build's config pack, never from the master's
   `userdata`. Credentials are copied only through `profile_connections`, on the parent's explicit choice.
4. **Child protection fails closed; everything else fails open** (9.5).

### 9.2 Architecture & Module Layout

#### Boundaries

```
   startup.py · router.py                 (engine entry points)
          │  call hooks.py only
          ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ resources/libs/profiles/                                               │
   │   hooks.py              the ONLY engine-facing surface                 │
   │   build_profiles · profile_store · profile_home · profile_connections  │
   │   profile_age_guard · parental_profiles · child_profiles               │
   │   af3_skin · profile_service · player_styles                           │
   └────────────────────────────────────────────────────────────────────────┘
          │  uses                                │  seeds files / drives native screens
          ▼                                      ▼
   config_apply · build_skin ·            Kodi native profiles: profiles.xml,
   fresh_install · patch_engine           per-profile userdata, PIN locks (owned by Kodi)
```

All profile logic lives in `resources/libs/profiles/`. Engine entry points `startup.py` and `router.py` **do not import
profile internals**; they call `hooks.py` and nothing else.

#### Modules

| Module                  | Responsibility                                                                                                                                                                                                                                                                            |
|-------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `hooks.py`              | **The only engine-facing surface.** Startup hooks, router gate and route dispatch (table below). Owns the failure policy for each.                                                                                                                                                        |
| `build_profiles.py`     | Profile UI and lifecycle: `show()` / `ProfileCards` (picker), `add_profile()`, `load_profile()`, `choose_skin()`, `open_child_content()`; startup repair (`repair_active()`, `prepare_inactive()`, `prepare_first_login()`); `enable_login_screen()`; the native-dialog helpers (9.7).        |
| `profile_store.py`      | Pure file-level seeding (standard library only): `registered_profiles()`, `profile_path()`, `write_missing()`, `seed_profile()`, `SEED_ERRORS`. Receives the verified pack as an argument; imports nothing from the engine.                                                                |
| `profile_home.py`       | Regenerates an *inactive* profile's home tiles (favourites generator and the shortcut writers) against that profile's own files and connection state, before Kodi reads its cache.                                                                                                        |
| `profile_connections.py`| Explicit account inheritance: copies the selected account **fields** (never databases, watch state or unrelated preferences) from the master into an inactive profile.                                                                                                                      |
| `profile_age_guard.py`  | **Standalone** policy and decision module: `active_policy()`, `policy_for()`, `allows()`, `visible()`, `stamp_item()`, `permit_play()`, `route_allowed()`, `install_directory_guard()`, `unclassified_source_allowed()`. Imported by host add-ons by bare name (9.5).                         |
| `parental_profiles.py`  | Master-only parental setup and policy persistence (`setup()`, `set_child()`, `approve()`), and `hosts_ready()` (9.5).                                                                                                                                                                      |
| `child_profiles.py`     | Child state: the `POVILChild` skin flag (`seed_skin_flags()`, `sync_active()`), `focus_home()`, native creation-lock presets (`preset_native_locks()`), `write_xml()`.                                                                                                                      |
| `af3_skin.py`           | Runtime profile UI for Arctic Fuse 3 only (9.6). `patch()`.                                                                                                                                                                                                                                |
| `profile_service.py`    | Service-side watcher started by `hooks.start_guard()`: refreshes the `POVIL.ChildProfile` window property and stops playback in a child profile that has no valid play receipt.                                                                                                           |
| `player_styles.py`      | NOX playback controls: style chooser, player settings, change source, next-episodes list (routes in `hooks.ROUTES`).                                                                                                                                                                       |

Outside the package: `profile_bootstrap.py` (add-on root; referenced by the skins' `Home.xml` `<onload>`, so its path is
a contract), the Wizard windows `resources/skins/Default/1080i/{Profiles,ChildHome,ProfileLogin}.xml`, the artwork in
`resources/media/profile-ui/`, and the `parental_control` entries of the patch registry (`patches_config.py`).

#### `hooks.py` — the engine-facing API

| Function                          | Called from                         | Purpose                                                                                                                                      | Failure policy |
|-----------------------------------|-------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|----------------|
| `start_guard()`                   | `startup.py`, first                 | Runs `profile_service.run()` as a **non-daemon thread of the startup service** (a `RunScript` watcher never hears Kodi's shutdown in time).   | open           |
| `prepare_startup()`               | `startup.py`, after `check_for_video()` | `af3_skin.patch()`, then `repair_active()` and `prepare_first_login()` for the active profile.                                           | open           |
| `complete_home()`                 | `startup.py`, after GUI-ready       | `enable_login_screen()`, `prepare_inactive()`, and for a secondary profile the live-home completion. Returns **False** only when a secondary profile's home is not yet verified; `startup.py` then stops (`sys.exit()`) and defers dialogs and OTA for that boot. | open           |
| `notifications_allowed()`         | `startup.py`                        | False inside a child profile (no build notifications).                                                                                       | open           |
| `deny_in_child_profile(mode, handle)` | `router.py`, before dispatch    | Child profiles may only reach `profiles`, `profile_child_open`, `profile_skin`; everything else shows a notice and ends the directory.        | **closed**     |
| `dispatch(mode, params, handle)`  | `router.py`                         | Runs one of `ROUTES`.                                                                                                                        | loud (raises)  |
| `ROUTES`                          | `router.py`                         | `frozenset` of modes owned by Profiles: `profiles`, `profile_child_open`, `profile_skin`, `choose_player_style`, `player_settings`, `player_change_source`, `player_episodes`. | —              |

*Fail-open* means a Profiles module that cannot load is logged (`[Profiles] <module> unavailable`) and the build keeps
running as a master-only build. *Closed* means an unavailable guard raises instead of letting a child through.

#### Engine ↔ Profiles contract

These are **all** the places where engine code knows about Profiles. Keep it that way.

| Engine file               | Touchpoint                                                                                                                                                                         |
|---------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `startup.py`              | `hooks.start_guard()`, `prepare_startup()`, `complete_home()`, `notifications_allowed()`.                                                                                          |
| `router.py`               | `hooks.deny_in_child_profile()`, `hooks.ROUTES`, `hooks.dispatch()`.                                                                                                               |
| `common/config.py`        | `CONFIG.USERDATA = CONFIG.PROFILE` (the **active** profile), `CONFIG.MASTERPROFILE`, `CONFIG.PROFILES = MASTERPROFILE/profiles.xml`. See *Active-profile paths* in §1.             |
| `config_apply.py`         | Retains the verified config pack and exposes `verified_pack_path()`, `ensure_verified_pack()`, `open_verified_pack()`, `VerifiedPack`, `PackUnavailable` (9.3).                     |
| `fresh_install.py`        | `apply_live_defaults(defaults_path=…)` and `live_favourites_ready()`, reused to complete a secondary profile's home. `live_favourites_ready()` never reloads a non-master profile.   |
| `build_skin.py`           | Engine-facing: `activate`, `confirm_requested_skin`, `rpc`, `wait_dialogs`, `persist_live_settings`. Profiles-facing: `prepare_active_skin_defaults`, `prepare_layout`, `refresh_home`, `switch`. |
| `patch_engine.py`         | `PatchEngine.run(category=…)` and `PatchEngine.all_applied(stats)` — the readiness check for parental hooks (9.5).                                                                 |
| `wizard.py`, `main_menu.py` | `build_switch_skin()` consults `profile_age_guard.active_policy()` (blocked children cannot switch skin; no promo image in a child profile) and calls `build_skin.switch()`. Menu and `AF3_TOOLS` carry the `mode=profiles` entry. |
| `modular_updater.py`      | No Profiles code. It works per profile because `is_provisioned()` and `config_applied_version` read the active profile (9.3).                                                      |

### 9.3 Profile Seeding & Configuration Management

#### Active-profile paths

`CONFIG.USERDATA` is the **active** profile's userdata, not Kodi's master alias. Everything the engine keeps per profile
— `kodipovil.provisioned`, favourites, `config_applied_version`, GUI defaults — therefore lives with the profile it
describes. Only `profiles.xml` and the policy file below are shared, and they are addressed through
`CONFIG.MASTERPROFILE`.

#### Retained verified config pack

Secondary profiles are seeded **without any zip bundled in the Wizard**. After a config pack is applied and its sha256
matches the manifest, `config_apply` keeps a copy in the **master** profile:

```
<masterprofile>/addon_data/plugin.program.kodipovilwizard/config_pack/
├── build_config.zip     # the verified pack
└── build_config.json    # {"config_version": "...", "sha256": "..."}
```

| API (`resources.libs.config_apply`)       | Meaning                                                                                                                                                       |
|-------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `verified_pack_path()`                    | Path of the retained pack, or `None`. The pack is **re-hashed on every call**; a corrupt, tampered or half-written copy counts as absent.                      |
| `ensure_verified_pack(manifest=None)`     | Returns the retained path, downloading and verifying the pack **once** if it is absent. A manifest without a sha256 yields `None` — an unverifiable pack is never retained. |
| `open_verified_pack(fetch=False)`         | Context manager returning a `VerifiedPack`; raises `PackUnavailable` when no verified pack exists. Only the master passes `fetch=True` (it owns the shared copy).   |
| `VerifiedPack`                            | Read-only view keyed by **userdata-relative paths**, mapped through `config_policy.json` exactly as `apply_config_pack` places files: `names()`, `in`, `read(path)`, `guisettings()`, `.version`. |

`guisettings()` returns the build's GUI defaults with the policy's machine-specific `exclude_ids` removed (screen
resolution, device uuid/name, …). The fresh-install snapshot (`kodipovil.fresh_gui_defaults.xml`) and the profile seed
use the **same** filter (`_gui_defaults`), so device-specific values never reach a profile.

#### Idempotent provisioning (`profile_store.seed_profile`)

`seed_profile(master, profile, pack, build, prepare_login=False)` repairs a secondary profile from the verified pack:

| Seeded (only when missing)                                                    | Never seeded                                                                 |
|-------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| `favourites.xml` (with the Profiles tile inserted), `sources.xml`, `keymaps/*` | Watched/history/cache databases, `advancedsettings.xml`                      |
| `addon_data/script.fentastic.helper/cpath_cache.db`                           | Any credential, token or account (copied only by `profile_connections`)      |
| `addon_data/*/settings.xml` shipped defaults                                  | Device-specific GUI values (`exclude_ids`)                                   |
| `guisettings.xml` and `kodipovil.profile_gui_defaults.xml` (the pack's GUI defaults) | Anything outside the registered profile directory (paths are validated)      |
| POV navigator **shortcut folders** only (`INSERT OR IGNORE`; existing DB backed up first) | The rest of any database                                                     |
| Wizard bootstrap settings (`buildname`, `buildversion`, `installed`, `config_applied_version`, skin-switch notice flag) | The master's wizard settings (queues, receipts, flags)                       |
| `kodipovil.provisioned` (= the pack's `config_version`), **written last**     |                                                                              |

Properties that make it safe to call repeatedly:

- `write_missing()` creates a file only if absent (hard-link or exclusive create) and validates the path before and
  after creating directories (symlink-safe). A user's file is never replaced.
- An interrupted seed resumes; the receipts below record what is complete.
- A new profile starts in `skin.povil.nox` (`profile_store.DEFAULT_SKIN`); when the master prepares it
  (`prepare_login=True`) the skin is written into the inactive profile's `guisettings.xml` before Kodi reads it.
- Seed failures raise one of `profile_store.SEED_ERRORS` (plus `config_apply.PackUnavailable` in `build_profiles`), which
  callers treat as "defer and retry".

`config_applied_version` is seeded with the pack version so the updater does not re-apply the config pack into a freshly
seeded profile; a later config bump reaches the profile through the normal update path.

#### State reference

| Location                                   | File / property                                              | Meaning                                                                                  |
|--------------------------------------------|--------------------------------------------------------------|------------------------------------------------------------------------------------------|
| Master userdata                            | `povil_profiles.json`                                         | Parental policy: `{"schema": 1, "children": {"<profile dir>": {"age": 0-17, "approved": {"movie:<tmdb>": label}}}}` |
| Master userdata                            | `kodipovil.login_screen_default`                              | Receipt: the login screen was enabled once as the build default                           |
| Master `addon_data/<wizard>/config_pack/`  | `build_config.zip`, `build_config.json`                       | Retained verified pack                                                                   |
| Profile directory                          | `povil.child-profile`                                         | Child marker. Written **first** during child setup so an interrupted setup stays restricted |
| Profile directory                          | `kodipovil.provisioned`                                       | Engine marker; seeded for secondary profiles                                             |
| Profile directory                          | `kodipovil.profile_addons_ready`                              | Build add-ons enabled in this profile's own add-on database                              |
| Profile directory                          | `kodipovil.profile_gui_defaults.xml`                          | GUI defaults waiting to be applied live; deleted when the home is verified               |
| Profile directory                          | `kodipovil.profile_home_ready`                                | The profile's live home was verified; gates startup in secondary profiles                |
| Profile `addon_data/plugin.video.pov/`     | `povil-shortcuts-defaults-v1.json`, `navigator.before-build-shortcuts-v1.db` | Shortcut-seed receipt and the pre-change backup of POV's navigator DB       |
| Skin settings (per profile)                | `POVILChild` (bool), `__chooseplayer` (string), `POVIL.BuildSidebarReady` | Child flag, selected NOX player style, Estuary sidebar initialised            |
| Home window (10000) properties             | `POVIL.ChildProfile`, `POVIL.ChildPlay`, `POVIL.ProfileGuardOwner`, `POVIL.RequestedSkin` / `POVIL.SkinConfirmed`, `POVIL.PlayerStyleBusy` | Runtime state only; never persisted |
| List-item property                         | `POVIL.AgeMetadata`                                           | Classification captured by the stamping hooks                                            |

### 9.4 Lifecycle & Data Flows

#### Startup

```
startup.py
 ├─ hooks.start_guard()          thread: profile_service.run()
 ├─ check_for_video()
 ├─ hooks.prepare_startup()      af3_skin.patch() → repair_active() → prepare_first_login()
 ├─ … engine: fresh-install gate, folders, wizard auto-update, wait_for_gui_ready() …
 ├─ hooks.complete_home()        enable_login_screen() → prepare_inactive() → secondary-profile home
 │     └─ False ⇒ sys.exit()     (secondary profile home not yet verified; retried next boot)
 └─ notifications (hooks.notifications_allowed()), OTA, patch pass …
```

#### Creating a profile (`build_profiles.add_profile`, master only)

1. Requires the master and a provisioned build; calls `config_apply.ensure_verified_pack()` first and stops with a
   network message if no verified pack can be obtained.
2. Asks adult vs child, then which accounts to inherit (`profile_connections.choose`).
3. Opens Kodi's native *Profiles* settings, focuses the "add profile" row and activates it (9.7). Kodi runs its own
   creation dialogs; the Wizard answers only the two "copy defaults" questions and, for a child, presets the native
   lock options (never the PIN).
4. When the native flow ends and a new registered profile exists, it seeds the profile (`_seed`), copies the chosen
   account fields, and for a child writes the child marker and runs `parental_profiles.setup()`. A child profile without a
   valid policy afterwards is reported as unfinished and stays restricted.
5. Offers to switch to the new profile (`load_profile`).

#### First login into a secondary profile

0. Kodi loads the profile with **its own add-on database, in which the Wizard starts disabled**, so it would not start
   `startup.py`. The skin's Home `<onload>` runs `profile_bootstrap.py`, which enables the Wizard through JSON-RPC; Kodi
   then starts the service and the sequence below runs.
1. `repair_active()` seeds anything missing (this also covers profiles created natively, outside the Wizard).
2. `prepare_first_login()` enables the build add-ons in **this profile's own add-on database** (dependencies first) and
   writes `kodipovil.profile_addons_ready`. Kodi keeps one add-on database per profile and starts third-party add-ons
   disabled.
3. `complete_home()`: if `kodipovil.profile_gui_defaults.xml` exists, `fresh_install.apply_live_defaults()` applies the
   settings through JSON-RPC and sets the skin last (`build_skin.activate`); then `build_skin.refresh_home()` verifies
   media, seeds layout files, patches AF3 if installed and reloads the skin; then `live_favourites_ready()` confirms the
   favourites. Only then is `kodipovil.profile_home_ready` written and the defaults file removed.
4. Until `profile_home_ready` exists, a secondary profile stops after the GUI-ready point on every boot.

#### Switching profile (`build_profiles.load_profile`)

The master seeds the target first (before Kodi reads its settings). The current settings are flushed with
`build_skin.persist_live_settings()` and `Profiles.LoadProfile` is requested; the function returns immediately because
Kodi tears down the departing Python services itself. In the destination profile the skin's `profile_bootstrap.py` hook
makes sure the Wizard is enabled so its service starts (see *First login*).

#### Child setup (`parental_profiles.setup`, master only)

Actions: set age and child screen · approve a movie/series · revoke an approval · share Debrid connections.
Setting the age requires, in order:

1. `hosts_ready()` — every parental host hook is present (9.5).
2. Native protection: the master and every unrestricted profile have a lock code; the child profile locks settings,
   the file manager and the add-on manager.
3. Kodi's HTTP remote control is off (it can switch profiles without PIN prompts).
4. An age from the selector. Then `_seed`, `set_child` (marker first, skin flag in all four build skins, policy JSON
   written atomically).

Title approvals are searched through POV's own TMDB client (`indexers.tmdb_api.get_tmdb`), so they depend on that POV
module staying importable.

### 9.5 Parental Controls & Content Guard Architecture

#### The age guard (`profile_age_guard.py`)

A **standalone** module — standard library plus lazy `xbmc*` imports, never `import resources.*` — because the host
patches import it by bare name inside POV, Umbrella and Idan Plus interpreters. The Wizard imports the same file as
`resources.libs.profiles.profile_age_guard`.

`active_policy()` returns `None` for an adult profile and a policy dict for a child. A dict with `"blocked": True` means
*nothing is allowed*. Decision rules (`allows()`):

- Age comes **only** from classification strings (`G`/`TV-Y`/`TV-G`/`U` = 0, `TV-Y7` = 7, `PG`/`TV-PG` = 10, `PG-13` =
  13, `TV-14` = 14, `12`/`12A` = 12, `15` = 15, `R` = 17, `NC-17`/`TV-MA`/`18` = 18). Never from votes or genres.
- An unclassified title is **blocked**. TMDB's explicit adult flag is blocked even with a low rating.
- A title the parent approved (`movie:<tmdb>` / `tv:<tmdb>`) is allowed regardless of rating; approving a series covers
  its episodes.
- A missing, unreadable or invalid policy file, a child marker without a policy, or incomplete native locks all
  resolve to `{"blocked": True}`. A broken central policy can never turn a child into an adult.

The guard is parental filtering inside Kodi. It is **not** protection against filesystem or remote-control access.

#### Enforcement layers

| Layer | Mechanism                                                                                                                                                                       |
|-------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1     | **Skin:** the child Home shows only the child layout (flag `POVILChild`); the adult controls are hidden (9.6).                                                                   |
| 2     | **Router gate** (`hooks.deny_in_child_profile`): build management is unreachable from a child profile.                                                                           |
| 3     | **Host hooks** (`parental_control` patches): directory guard hides rows that are not allowed; route guard rejects account/settings/cloud/download routes; stamping hooks capture each row's classification; play guards call `permit_play()` before playback. Idan Plus is blocked entirely via `unclassified_source_allowed()` (live sources carry no classification). |
| 4     | **Playback watcher** (`profile_service`): in a child profile, playback without a valid, profile-bound receipt from `permit_play()` is stopped — this also covers unsupported add-ons.   |
| 5     | **Kodi native locks and PINs** (required during setup).                                                                                                                         |

#### Category-scoped readiness (no marker inspection)

Neither the guard nor any profile code reads patch markers. The patch engine is the single owner of patch state:

```python
# parental_profiles.py
def hosts_ready():
    return PatchEngine.all_applied(PatchEngine().run(category='parental_control'))
```

`run(category=…)` selects entries **before** schema validation, repairs what a host update removed, and returns stats;
`all_applied()` is true only when every selected patch is present and current. It is false for a failed, missing,
unanchored or disabled patch and for an **empty selection**. It is called by `parental_profiles.setup()` (age setup) and
by `build_profiles.open_child_content()` (every time a child opens movies, series or search), so a POV or Umbrella update
that wipes a hook is repaired and verified at the point of use. An unrelated broken UI patch can neither block nor be
repaired by this call.

Operational note: only `plugin.video.umbrella` is treated as an *optional* host by the engine (absent = fine). For every
other host of a `parental_control` patch, an uninstalled add-on makes `hosts_ready()` false. See 9.8 when adding a host
that users may remove.

#### Fail-closed vs fail-open

| Boundary                                              | Policy       | Behaviour                                                                                                     |
|-------------------------------------------------------|--------------|---------------------------------------------------------------------------------------------------------------|
| Router gate (`deny_in_child_profile`)                 | **closed**   | A missing guard raises; a child reaches only `profiles`, `profile_child_open`, `profile_skin`.                 |
| Enforcement host hooks (`fail_closed=True`)           | **closed**   | Error is logged, then re-raised: a broken guard stops the host instead of letting a child through.            |
| Policy reads (`policy_for`, `active_policy`)          | **closed**   | Any doubt resolves to `blocked`.                                                                              |
| `open_child_content`, child setup                     | **closed**   | `hosts_ready()` false ⇒ refuse.                                                                               |
| Playback watcher                                      | **closed**   | No valid receipt ⇒ playback stopped.                                                                          |
| Enrichment host hooks (`stamp_item`, `fail_closed=False`) | open     | Logged and skipped; an unstamped row is hidden from child profiles by the directory guard anyway.            |
| Startup hooks (`hooks.start_guard/prepare_startup/complete_home`) | open | A failing Profiles module is logged; the build runs as master-only. Exception: an unverified secondary home defers the boot. |
| `notifications_allowed()`                             | open         | Cosmetic.                                                                                                     |

### 9.6 Skin Integration & XML Patching Strategy

#### Built-in skins (NOX, Estuary, FENtastic): baked into source

The child/adult Home, the profile bootstrap hook and the login screen are part of each skin's **own XML**. The Wizard does
not rewrite these skins at runtime. The contract every baked skin satisfies is specified in
`skin_xml_runtime_modifications_spec.md`:

- `Home.xml`: first `<onload>` runs `special://home/addons/plugin.program.kodipovilwizard/profile_bootstrap.py`; the
  adult controls sit in group `9879` (visible `!Skin.HasSetting(POVILChild)`), the child layout is group `9880` (visible
  `Skin.HasSetting(POVILChild)`), and `SetFocus(9500)` is applied for child profiles. NOX composes the child layout
  through its `Home_child.xml` include `skin_home_child`.
- `LoginScreen.xml`: the shared picker design with Kodi's native list `52`, default focus `52`, button `20` → shutdown
  menu, keeping the skin's own `<onload>` actions.
- Shared templates (single source of truth): `resources/skins/Default/1080i/ChildHome.xml` and `ProfileLogin.xml`.
- Child buttons fire `plugin://plugin.program.kodipovilwizard/?mode=profile_child_open&kind=movie|tv|search`,
  `?mode=profiles` and `?mode=profile_skin` (exactly the modes the router gate allows), and use artwork from
  `special://home/addons/plugin.program.kodipovilwizard/resources/media/profile-ui/`.
- Control IDs `9500-9504`, `9879`, `9880` are unique in the Home window.

To change the child layout, edit the shared template and re-bake each skin (9.8); do not patch skins at runtime.

#### External skin (Arctic Fuse 3): isolated runtime patching

AF3 is installed on demand from a third-party repository and rewritten by every Kodi update, so it is the one skin the
Wizard patches at runtime — and only in `af3_skin.py`. `af3_skin.patch()` is idempotent and is called at startup
(`hooks.prepare_startup`), from `build_skin.prepare_layout()` and when a child profile switches skin. It applies, to each
of AF3's `xml`/`1080i`/`720p` folders that exists:

- `Home.xml`: bootstrap `<onload>`, adult controls wrapped in group `9879`, the shared child group `9880`, and
  `SetFocus(9500)`, written once and only if the bytes changed;
- `LoginScreen.xml`: replaced by the shared design when the original has the native list `52`; the skin's own
  `<onload>` actions are preserved;
- font names of the shared layouts resolved against AF3's `Font.xml` (closest size) or its `Includes_Font.xml`.

A skin file it cannot read or parse is skipped and retried on the next call. If AF3 is active in a child profile and its
Home changed, the skin is reloaded. **If AF3 is ever baked into source or dropped, delete `af3_skin.py` and its three
callers** (`hooks.prepare_startup`, `build_skin.prepare_layout`, `build_skin.switch`).

#### Other skin-side pieces

- **`profile_bootstrap.py`** (add-on root): run by the skins' Home `<onload>`; keep it short and import-light. In a newly
  loaded secondary profile it enables the Wizard (disabled by default in that profile's add-on database) so
  `startup.py` runs, unless the profile is already set up, which preserves a deliberate disable. It also initialises the
  Estuary sidebar defaults (reloading the skin if that changed anything) and places child focus after the skin's own
  `defaultcontrol`. It does nothing while a skin switch awaits confirmation, on an unprovisioned build, or on an
  already-settled adult Home.
- **`child_profiles.sync_active()`** sets or clears `POVILChild` so a different skin can never restore an unrestricted
  Home for a child; `seed_skin_flags()` pre-sets it in all four build skins of an inactive child profile.
- **`build_skin.prepare_active_skin_defaults()`** initialises the build's Estuary sidebar once per profile
  (`POVIL.BuildSidebarReady`) and syncs the child flag.
- **Skin switching** is live: `build_skin.switch()` enables the skin and its dependencies, activates it through
  Kodi's settings API and refreshes the home — no restart.

### 9.7 Native UI Automation

Kodi has no API for creating profiles or answering some of its own dialogs, so a small, closed set of helpers drives the
native screens. Every helper acts only inside an explicit, user-initiated flow, verifies **exact localized text or
control identity** before clicking, and does nothing when anything differs. **PIN and lock authorization dialogs are
never answered by code.**

| Helper                                      | Windows            | Action                                                                  | Preconditions                                                                                 | If they fail                                       |
|---------------------------------------------|--------------------|-------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|----------------------------------------------------|
| `build_skin.confirm_requested_skin`         | 10100 (yes/no)     | Click *Yes* on Kodi's "keep this skin?" question                         | An active request for exactly this skin (`POVIL.RequestedSkin`), body text equals the localized question; serialized by a lock | No click; `activate()` times out and returns False |
| `build_profiles.copy_native_profile_defaults` | 10100            | Click *copy defaults* on the two copy questions of profile creation      | Master; heading and button labels match the localized strings                                 | No click; the parent answers                       |
| `build_profiles._native_settings` / `add_profile` | 10034        | Open Profiles settings, focus the list, activate *Add profile*           | Master; no dialog; focus and item index verified; waits (without answering) through native lock prompts | Flow aborts                                        |
| `build_profiles.enable_login_screen`        | 10034              | Click the *login screen* toggle once                                     | Master; Home active; not playing; no dialog; receipt absent; login screen currently off        | Retried on a later boot; a manual OFF is respected |
| `child_profiles.preset_native_locks`        | 10130, 10131, 12000 | Preset the recommended lock options for a child, choose *All* in the media-lock list | Only inside `add_profile` for a child; labels and every radio control validated first | Returns False; the parent sets locks manually      |

Do not add helpers of this kind casually. Any new one must follow the same rules and be listed here.

### 9.8 Developer Guide

#### Entry points

| You want to…                                  | Start in                                                                                           |
|-----------------------------------------------|----------------------------------------------------------------------------------------------------|
| Change what runs at boot for profiles         | `hooks.py` (`prepare_startup`, `complete_home`) → `build_profiles.py`                              |
| Change profile creation, switching, picker UI | `build_profiles.py` (`add_profile`, `load_profile`, `ProfileCards`), `Profiles.xml`                |
| Change what a new profile receives            | `profile_store.seed_profile()` (the allowlist) and the config pack's `config_policy.json`          |
| Change the config-pack retention / pack API   | `config_apply.py` (`_retain_verified_pack`, `VerifiedPack`)                                        |
| Change a child's rules or approvals           | `profile_age_guard.py` (decisions), `parental_profiles.py` (setup, policy file)                    |
| Add or alter a host guard                     | `patches_config.py` (`category: "parental_control"`) + `profile_age_guard.py`                      |
| Change the child Home or login design         | `ChildHome.xml` / `ProfileLogin.xml`, then each baked skin; `af3_skin.py` follows automatically    |
| Change NOX player routes                      | `player_styles.py`, `hooks.ROUTES` / `dispatch()`                                                  |
| Share accounts between profiles               | `profile_connections.py` (`GROUPS`, `ADDONS`)                                                      |

#### Adding a new profile action / route

1. Implement it in a Profiles module (an existing one, or a new file in `profiles/`). It must be safe to call twice and
   must wait for native dialogs with `build_skin.wait_dialogs()` before activating windows.
2. Add the mode name to `hooks.ROUTES`.
3. Add a branch for it in `hooks.dispatch()`. Load modules with `_load('<module>')`; do not import profile internals at
   the top of an engine file.
4. **`router.py` needs no change**: its single branch `elif mode in profile_hooks.ROUTES:` already routes every member of
   `ROUTES`. If the action is a *script action* rather than a folder, end the directory in the branch
   (`xbmcplugin.endOfDirectory(handle, succeeded=False)`), as the `profiles` branch does.
5. **Child access is denied by default.** The router gate runs before dispatch and allows only
   `hooks._CHILD_ALLOWED`. Add the mode there only when a child must reach it, and then audit it as untrusted input.
6. Add the entry point: a line in `main_menu.py` / `AF3_TOOLS`, or a button URL
   `plugin://plugin.program.kodipovilwizard/?mode=<mode>` in `ChildHome.xml` (re-bake the skins, 9.6).
7. Test it as the master and as a child (the child case must be refused unless allowed in step 5).

#### Extending parental rules

*A new rule inside an existing host* (for example another classification scheme): change `profile_age_guard.py` only. It
must stay standalone (no `resources.*` imports) and must treat anything it does not understand as **blocked**.

*A new third-party add-on guard:*

1. Put the decision function in `profile_age_guard.py`.
2. Add patch entries to `patches_config.py` with `"category": "parental_control"` and hooks built by
   `_build_patch_hook(module_name="profile_age_guard", patch_dir=GUARD_DIR, …)`.
   Use **`fail_closed=True`** for anything that enforces (routes, playback, directories, sources); `fail_closed=False` is
   only for enrichment such as stamping metadata.
3. Give every entry a unique `# WIZARD_<ADDON>_<NAME>_v<N>` marker. If the generated hook changes, bump the suffix; the
   engine scrubs the old block and injects the new one.
4. Make sure `permit_play(meta, host, url)` runs before playback starts, so `profile_service` receives a valid receipt.
5. **Optional hosts:** the engine counts only `plugin.video.umbrella` as optional. If users may uninstall the new add-on,
   extend that optional-host handling in `patch_engine.py`; otherwise its absence makes `hosts_ready()` false and
   blocks child content.
6. Validate: `python patches_config.py path/to/patches/`, then on a device `PatchEngine().run(category='parental_control')`
   and check `PatchEngine.all_applied(stats)`.

`hosts_ready()` needs no change: it covers every `parental_control` patch automatically.

#### Changing seeded defaults

Add the file to the build's `userdata/` and `config_policy.json`, bump `config_version`, and — only if profiles must
receive it — extend the allowlist in `profile_store.seed_profile()`. Never seed credentials or databases that hold
history. New shortcut rows for POV go through `_seed_shortcuts` (insert-only).

#### Verification checklist

- `python -m pyflakes` on every changed module; `python patches_config.py` for any registry change.
- Engine files import nothing from `resources.libs.profiles` except through `hooks`. (`wizard.py` and `build_skin.py`
  use `profile_age_guard` and the lazy imports listed in 9.2 by design.)
- Master: create an adult and a child profile; the child flow must end with a policy and a `blocked`-free
  `active_policy()` in the child.
- Child: Home shows only the child layout; settings, debrid, cloud and download routes are refused; an unrated title is
  hidden; an approved title plays; playback started from an unsupported add-on is stopped.
- Update POV (or Umbrella) so a hook disappears, then open child content: it must repair the hook and open, or refuse.
- Secondary profile first login: `kodipovil.profile_addons_ready` then `kodipovil.profile_home_ready` appear, and the
  home shows the build's tiles.

#### Troubleshooting

| Symptom                                                   | Where to look                                                                                                         |
|-----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| "Cannot download build defaults" when adding a profile    | No verified pack: check `[config_apply]` log lines and that the manifest `config` entry has a `sha256`.              |
| Child content says filtering is not ready                 | `hosts_ready()` is false: look at the patch-engine stats (`anchor_missing`, `missing`, `removed`) for `parental_control`; a required host may be uninstalled or an anchor may have drifted. |
| A guard hook seems inactive                               | Log lines `Wizard Patch Error [profile_age_guard]` — enforcement hooks re-raise, enrichment hooks only log.          |
| A secondary profile repeats a deferred boot               | `kodipovil.profile_home_ready` is missing: the live-home step (`complete_home`) is failing; look for `[Profiles]` lines. |
| Profile creation stops at the native screen               | A localized label or control did not match (9.7); the helper deliberately did nothing. Complete the step manually.   |
| Everything `[Profiles] … unavailable`                     | A Profiles module failed to import; startup keeps running as master-only. Fix the import error shown.                |

---

## 10. Removed / Legacy Components

The following were intentionally removed during the migration; **do not
reintroduce them or references to them**:

- **DarkSubs / `service.subtitles.All_Subs`** — replaced by the native AI subtitles
  service. Its release zip was pruned; only historical UX comments remain.
- **`repository.peno64`** — deleted addon/repo (dead). Removed from `sources.xml`
  and its orphan zip pruned from the release.
- **`kodi7rd` (`repository.KodiRealDebridIsrael` + `‑Wizard`)** — dead `kodi7rd.github.io`
  domain. Removed from `sources.xml`; dead title‑mapping feeds noted in the subs
  engine.
- **`repository.burekasKodi`** and **`packages.Fen + Fen Light` (tikipeter)** — legacy
  sources unrelated to the modular build; removed from `sources.xml`.
- **Twilight (`plugin.video.twilight`)** — predecessor to POV. `config_policy.json`
  `cleanup.remove_paths` deletes stale `userdata/addon_data/plugin.video.twilight`
  so it can't resurrect menus/settings.
- **The legacy monolithic build zip & brute‑force wipe** — replaced wholesale by
  the manifest + config‑pack model.
- **The Wizard‑bundled config zip and all migration code.** The Wizard ships no `resources/bootstrap/config.zip`; profile
  defaults come from the retained verified pack ([§9.3](#93-profile-seeding--configuration-management)). Old‑install
  conversions, "rollout" receipts, Python 2 and zip fallbacks, `OLD_MARKERS`‑style scrubbing and the forced restart after
  an install or skin switch are gone: the build assumes clean installs and finishes live.
- **Superseded engine modules** — never reference: `youtube_platform`, `addon_origins`, `staged_addon_install`,
  `pov_host_handoff`, `pov_version_gate`, `modular_host_migration`, `host_patch_watch`, `host_patch_service`,
  `startup_patched`, `fentastic_widgets`, `fentastic_player_switch_patcher`, `loginit`, `speedtest`, `qr` / `segno`,
  `yt`, `debridit`, `traktit`.
- **Patch mechanics** — `target_alternatives` and any alternative or guessed anchor; marker‑inspecting integrity checks in
  the age guard (`host_integrity`, replaced by the category‑scoped engine run); runtime XML rewriting of the repository
  skins (baked into their source; only AF3 is patched at runtime).
- **Profiles leftovers** — `resume_single_login` / `single_profile_login.py`, the versioned login‑default receipt, and the
  versioned Estuary sidebar flags (a single `POVIL.BuildSidebarReady` remains).

---

## 11. Repository Layout & Addon Inventory

```
.
├── .github/
│   ├── workflows/build-and-release.yml     # CI: build zips, release, manifest, prune
│   └── scripts/
│       ├── kodi_addons.py                   # discover_addons(), addon.xml parsing, type
│       ├── build_addons.py                  # package each addon → dist/<id>-<ver>.zip
│       ├── build_config.py                  # package userdata/ → dist/config-<ver>.zip
│       └── gen_manifest.py                  # regenerate manifest.json (refuses null sha)
├── manifest.json                            # GENERATED — never hand‑edit
├── userdata/                                # the build‑config tree (shipped as config.zip)
│   ├── config_policy.json                   # apply policy + config_version + cleanup
│   ├── guisettings.xml / sources.xml / advancedsettings.xml / favourites.xml
│   └── addon_data/
│       ├── plugin.video.pov/settings.xml    # he metadata + fanart + keys (merge_id, creds excluded)
│       └── skin.fentastic/settings.xml
├── plugin.program.kodipovilwizard/          # the install/OTA/self‑heal engine + the Profiles feature
│   ├── profile_bootstrap.py                 # Home <onload> entry for Profiles (add‑on root: skins reference this path)
│   └── resources/
│       ├── libs/
│       │   ├── {modular_updater,headless_installer,config_apply,fresh_install,patch_engine,build_skin,downloader,extract,db}.py
│       │   ├── patches/                     # UNIFIED directory: patch registry (patches_config.py) & decoupled logic modules
│       │   └── profiles/                    # the Profiles feature (§9); hooks.py is its only engine‑facing surface
│       ├── skins/Default/1080i/             # Wizard windows: Profiles.xml + shared ChildHome.xml / ProfileLogin.xml templates
│       └── media/profile-ui/                # picker / child‑home / login artwork
├── plugin.program.orderfavourites-hebrew/   # favourites generator + icon/media installer
│   ├── povil_icons/                         # flat canonical icon set (source of truth)
│   ├── resources/favourites_config.json     # tiles + per‑skin order + icon_base
│   └── favourites_generator.py
├── service.subtitles.kodipovilai/           # AI Hebrew subtitles (+ home shortcut writers: tonight, recent updates)
├── repository.{kodifitzwell,Fishenzon,otaku,jurialmunkey}/   # third‑party repos (for content addons)
├── skin.{fentastic,povil.nox,estuary}/      # build skins; they carry the Profiles UI in their own XML (§9.6)
└── script.{fentastic.helper,module.autocompletion}/ , plugin.program.autocompletion/
```

**Manifest addon inventory** (versions are illustrative — the manifest is the live
source):

| Addon                                                            | Type          | Role                                                |
|------------------------------------------------------------------|---------------|-----------------------------------------------------|
| `plugin.program.kodipovilwizard`                                 | plugin        | Install / OTA / self‑heal engine + Profiles         |
| `plugin.program.orderfavourites-hebrew`                          | plugin        | Favourites generator + global icon/media installer  |
| `service.subtitles.kodipovilai`                                  | subtitle      | AI Hebrew subtitles                                 |
| `script.fentastic.helper`                                        | module        | FENtastic skin helper                               |
| `script.module.autocompletion` / `plugin.program.autocompletion` | module/plugin | Search autocompletion                               |
| `repository.kodifitzwell`                                        | repository    | Source for `plugin.video.pov`                       |
| `repository.Fishenzon`                                           | repository    | Source for `plugin.video.idanplus`                  |
| `repository.otaku`                                               | repository    | Source for `plugin.video.otaku` (+ `context.otaku`) |
| `repository.jurialmunkey`                                        | repository    | Source for Arctic Fuse 3 + helpers                  |
| `skin.fentastic`                                                 | skin          | Build skin                                          |
| `skin.povil.nox`                                                 | skin          | Build skin (large; on‑demand after a fresh install) |
| `skin.estuary`                                                   | skin          | Fallback skin                                       |

**Content addons (NOT in the manifest — provisioned headlessly):**
`plugin.video.pov`, `plugin.video.idanplus`, `plugin.video.otaku`,
`plugin.video.youtube`, `resource.language.he_il`.

---

## 12. Strict Guidelines for AI Agents & Contributors (CRITICAL)

### Engineering rules (apply to every change)

1. **No legacy or migration code.** Clean installs only: no old‑install conversions, versioned "rollout" receipts, Python 2
   leftovers or zip fallbacks.
2. **Modern environment only — but keep the native‑UI automations** that exist (the installer's window‑10100 confirmer and
   the scoped helpers listed in [§9.7](#97-native-ui-automation)). No OS‑version checks or environment workarounds.
3. **Standard updates must work.** The Wizard and third‑party add‑ons (POV, Umbrella, …) update natively; never gate or
   freeze them. Only dead or legacy migration scripts are removed.
4. **Keep the engine and performance work** (cache warming, VFS‑first index fetch, 8 MiB caps, live completion UX).
5. **Isolate features.** Profiles code lives in `resources/libs/profiles/` and meets the engine only through `hooks.py`
   ([§9.2](#92-architecture--module-layout)).
6. **Deleted files are never referenced**, and no dangling imports are left behind ([§10](#10-removed--legacy-components)).
7. **Core dependencies stay** (for example `plugin.video.youtube` in hints, provision IDs and resolution lists).
8. **Line endings:** existing sources and this document use **CRLF**; keep it.

### Anti‑patterns — DO NOT do these

- ❌ **NEVER revert to monolithic `.zip` payloads** for installs/updates. Updates are
  per‑addon, manifest‑diffed, sha256‑verified zip extractions. No "download the
  whole build" path.
- ❌ **NEVER reintroduce the brute‑force wipe.** The build hydrates and merges; it
  does not destroy user data.
- ❌ **NO rogue background patchers hiding in secondary addons** (e.g. a stray `wizard_self_healer.py`). All
  cross‑addon influence must be declared in the **Wizard's patch registry** ([§6](#6-runtime-cross-addon-patching)) —
  additive, marker‑guarded, versioned, with deterministic anchors. Inter‑addon calls go through Kodi (`RunScript` /
  `RunAddon` / `executebuiltin`), not import side‑effects.
- ❌ **NEVER bring back the aggressive dialog watchdog** that closes arbitrary windows. The only permitted native‑dialog
  automation is the closed set of scoped helpers: the installer's minimal confirmer for **window 10100** and the
  label‑verified Profiles helpers in [§9.7](#97-native-ui-automation). Each acts only inside an explicit user‑initiated
  flow and does nothing when any label differs. **Never answer PIN or lock‑authorization dialogs from code.**
- ❌ **NEVER blindly extract binary/platform addons** (`inputstream.*`, `pvr.*`,
  `vfs.*`, etc.). Defer them to Kodi's native installer.
- ❌ **NEVER hand‑edit `manifest.json`.** It is regenerated by CI. Bump the
  `addon.xml` version instead.
- ❌ **NEVER apply `config.zip` before addons are installed.** It is always the
  final step of setup.
- ❌ **NEVER write empty build values over user credentials.** Credential ids stay in
  `exclude_ids` for `merge_id`.
- ❌ **NEVER re‑add removed sources/addons** (DarkSubs/All_Subs, peno64, kodi7rd,
  burekasKodi, Fen packages, Twilight).
- ❌ **NEVER import `resources.libs.profiles` internals from engine files.** Go through `hooks.py`; the few deliberate
  exceptions are listed in [§9.2](#92-architecture--module-layout).
- ❌ **NEVER inspect patch markers from the age guard or other profile code.** The patch engine owns patch state; ask it
  (`parental_profiles.hosts_ready()` → `PatchEngine().run(category='parental_control')` + `all_applied()`).
- ❌ **NEVER give an access‑enforcing hook `fail_closed=False`**, and never let a child‑profile decision default to
  "allowed". Unknown, unparseable or unclassified means blocked.
- ❌ **NEVER import `resources.*` inside `profile_age_guard.py`.** Host add‑ons import it by bare name.
- ❌ **NEVER rewrite the repository skins' XML at runtime.** Bake changes into the skins; only Arctic Fuse 3 is patched
  at runtime, only from `af3_skin.py`.
- ❌ **NEVER edit `profiles.xml`, and never copy accounts, databases or watch state into a profile implicitly.** Accounts
  move only through `profile_connections`, on the parent's explicit choice.
- ❌ **NEVER bundle a config zip in the Wizard.** Profile defaults come from the retained verified pack
  ([§9.3](#93-profile-seeding--configuration-management)).

### Keep `userdata/addon_data` lean — NO generated/runtime files

`userdata/` ships to **every** device, so it must contain **only** intentional,
device‑agnostic build config. **Do not commit machine‑specific or unmanaged
runtime files**, including:

- **`script.skinvariables` generated nodes** (`skinvariables-shortcut-*.json`,
  `*-viewtypes.json`) and other skin‑generated JSON — regenerated by the skin.
- **Hardware/display‑specific values** — screen resolutions, device UUID/name.

> **Intentional exception — seeded build‑default DBs.** A *small, curated* set of
> binary DBs **is** shipped on purpose to pin the build's UX, and **must be listed
> in `config_policy.json` with `replace`/`replace`** (otherwise, like POV's
> `settings.xml` once was, they ship in the zip but are never applied):
> `addon_data/script.fentastic.helper/cpath_cache.db` (FENtastic home‑view /
> main‑menu + widget path cache) and `addon_data/plugin.video.pov/{navigator,views}.db`
> (POV menu order + per‑list view types). These overwrite on update by design (no
> merge yet). Do **not** add *new* `*.db` files unless they are a deliberate build
> default **and** you add a matching `replace` policy entry + bump `config_version`.

- **Hardware/display‑specific values** — screen resolutions, device UUID/name (`videoscreen.resolution`,
  `services.deviceuuid/devicename`, … are already in
  `exclude_ids` for `guisettings.xml`).
- **Personal tokens / auth** — Debrid/Trakt/TMDB tokens, usernames, session ids,
  API keys. These belong to the user, never to the build.
- **Profiles / keymaps / RSS / thumbnails** and similar per‑device state.

If you bump `config_version`, double‑check the diff contains no leaked cache,
token, or hardware value.

### Process rules

- ✅ **Bump versions** on every shippable change (`addon.xml` version and/or
  `config_version`). No bump → CI carries over the old zip and your change never
  reaches devices.
- ✅ **Keep patchers additive and idempotent.** Marker‑guard every on‑disk edit; **bump the marker suffix when a hook
  changes** (the engine scrubs the stale block itself); make a missed anchor fail deterministically (`anchor_missing`),
  never guess.
- ✅ **Prefer headless** install paths; the native `InstallAddon` is a last resort
  with the minimal confirmer only.
- ✅ **Verify before shipping.** `python -m py_compile` (and `pyflakes`) every changed `.py`; run
  `python patches_config.py` after any registry change; validate every JSON/XML; for patchers, simulate the injection
  against the real host source when possible.

---

## 13. Git & Release Workflow

- **Develop on the feature branch** (e.g. `claude/monorepo-build-automation-uhn68y`),
  then fast‑forward `main`. Pushing to `main` triggers CI, which builds, publishes
  to the `addons-latest` release, regenerates `manifest.json`, prunes orphaned
  release assets, and commits the manifest back with `[skip ci]`.
- **Push with `git push -u origin <branch>`**; retry on transient network errors
  with exponential backoff. Do **not** open a pull request unless explicitly asked.
- After CI lands the `chore(manifest)` commit on `main`, **sync the feature branch**
  (fast‑forward) so it stays even with `main`.
- A change is "done" only when: code compiles, configs validate, the version is
  bumped, CI is green, and the regenerated `manifest.json` shows the new
  version (s).

---

*This README reflects the post‑migration architecture: the modular manifest engine, the headless installer with its
verified live completion path, config‑pack timing and retention, the Unified Patching Engine, and the Profiles feature
(§9). Keep it updated when the installation, config, asset, patching or Profiles contracts change — it is the contract
future agents will rely on.*