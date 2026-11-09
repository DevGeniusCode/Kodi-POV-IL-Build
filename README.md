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
9. [Removed / Legacy Components](#9-removed--legacy-components)
10. [Repository Layout & Addon Inventory](#10-repository-layout--addon-inventory)
11. [Strict Guidelines for AI Agents & Contributors (CRITICAL)](#11-strict-guidelines-for-ai-agents--contributors-critical)
12. [Git & Release Workflow](#12-git--release-workflow)

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

- **`.provisioned` marker** (`special://userdata/kodipovil.provisioned`). Written **only** when a fresh install reaches
  the very end (every addon attempted **and** `config.zip` applied) *and* Kodi is not aborting. `startup.py` uses it to
  tell a *completed* setup from one the user force‑closed mid‑provisioning, so an interrupted setup is **resumed** next
  launch instead of left half‑built. `run_fresh_install()` is idempotent — present addons are skipped, the config pack
  self‑applies once.
- **`heal_missing_addons()`** — a background boot‑loop self‑heal. On startup it verifies with `System.HasAddon(...)`
  that every required manifest addon (minus on‑demand skins) and every `PROVISION_IDS` content addon is physically
  present. If anything is missing, it runs the **exact same headless pipeline silently in the background** without
  invoking the `install_manager` GUI thread. This architecture guarantees the build will seamlessly self‑complete over a
  couple of launches if an installation is ever partially interrupted.
- **Graceful reload, not crash.** A forced restart is reserved for *critical* updates only (the Wizard itself, or the *
  *currently active** skin whose live files can't be hot‑swapped). Everything else — plugins, services, inactive skins,
  or a config change that only touched the active skin's look — is applied in place with **`ReloadSkin()`**. We do **not
  ** kill Kodi to apply ordinary updates.
- **First‑Boot Stabilizer (race shield).** The first boot *after* an install is a race: FENtastic fires parallel widget
  directory queries at `plugin.video.pov` at the exact moment POV's service is creating its DBs for the first time —
  POV answers `Unable to find plugin` / `GetDirectory` fails and the skin‑variable engine can hard‑deadlock the Python
  interpreter (the "second boot is always fine" symptom). `fresh_build_auto_install_if_needed()` arms a one‑shot marker
  (`kodipovil.first_boot_stabilize`, same pattern as `.provisioned`) right before the force‑close; on the next boot,
  *after* `wait_for_gui_ready()`, `first_boot_stabilize_if_needed()` holds briefly with a **user‑visible Hebrew
  countdown
  banner** while POV warms up (bounded by `first_boot_warmup_seconds`, default 25s, early‑exit once
  `System.HasAddon(plugin.video.pov)` + a minimum warm‑up), then `UpdateLocalAddons` + `ReloadSkin()` so the home
  widgets re‑evaluate against a ready POV. It is a no‑op on every normal boot, never patches the skin, uses no global
  watchdog, and **always clears the marker in a `finally`** so a failure can never trap Kodi in a stabilize loop or a
  black screen.

---

## 4. Build‑Config System (`config_policy.json`)

`userdata/config_policy.json` is the declarative policy for how the shipped
`userdata/` is merged onto a device. `userdata/` is packaged whole into
`config-<config_version>.zip`; **only files listed in the policy are applied** (so
a file that ships in the zip but isn't in `files[]` is inert — see the POV
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
**never applied** — POV ran on its English /
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

3. **Standardized Hook Construction (`_build_patch_hook`)**
   Every hook that calls a logic module is generated by the `_build_patch_hook()` helper in `patches_config.py`, never
   written by hand. The helper emits one self-contained, fail-safe block (path setup, import, custom logic, error
   logging), so a patch never depends on a name injected by another patch, and a failing patch is logged instead of
   breaking the host addon. See *Writing Hooks* below.

> **Note:** Injected hooks must avoid over-engineering or overcomplicating. They should remain simple, maintainable, and
> straightforward.

4. **Strict Tab Indentation (`\t`)**
   Injected lines inside multi-line hook definitions MUST explicitly utilize tab characters (`\t`) for scope alignment
   to prevent Python `IndentationError` exceptions when evaluated against upstream source code. `_build_patch_hook()`
   already emits tabs for its own structure; the `indent` argument and the lines of `custom_logic` must follow the same
   rule.
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

---

### ✍️ Writing Hooks

#### `_build_patch_hook(module_name, custom_logic='', indent='')`

| Parameter      | Meaning                                                                                                                                                                                                                                   |
|----------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `module_name`  | The logic module imported from the patches directory (e.g. `"pov_ui_labels"`). Its name is also used in the error log.                                                                                                                    |
| `custom_logic` | Code executed after the import, written **without** the helper's own indentation. Nested blocks use tabs (`\t`). `return`, `continue` and `raise SystemExit` work as in any other code, because the logic runs inside the helper's `try`. |
| `indent`       | The leading tabs of the hook in the target file. Use the indentation of the anchor's block (`"\t"` inside a function body, `"\t\t"` inside a method, and so on). Leave empty for module-level hooks.                                      |

For the schema example above, the helper generates:

```python
try:
    import sys, xbmcvfs
    
    _povil_dir = xbmcvfs.translatePath('special://home/addons/plugin.program.kodipovilwizard/resources/libs/patches/')
    if _povil_dir not in sys.path:
        sys.path.append(_povil_dir)
    import pov_source_remember
    
    pov_source_remember.capture(item)
except Exception:
    __import__('xbmc').log('Wizard Patch Error [pov_source_remember]:\n' + __import__('traceback').format_exc(),
                           __import__('xbmc').LOGERROR)
```

#### Behavior rules

1. **Failures are logged, never raised.** An exception inside the block is written to the Kodi log at `LOGERROR` level
   with the module name and the full traceback (including the failing line number). The host addon keeps running.
2. **A failed hook means "patch disabled".** Execution simply continues with the native code that follows the hook.
   Hooks that `return` a value therefore fall back to the native behavior when the patch module fails.
3. **Fail-open applies to guards too.** If a `profile_age_guard` hook fails, the check is skipped (and logged) rather
   than crashing the addon. Treat the log as the signal that a guard is not active.
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

#### The failure branch avoids host-name pollution

Inside a function, `import xbmc` would turn `xbmc` into a local name of the host function for the whole function body,
and a variable such as `error_msg` could shadow a host variable. The helper's failure branch therefore uses
`__import__('xbmc')` and `__import__('traceback')` and creates no named variables. For the same reason, custom logic
should not introduce generic names (use the `_povil_` prefix for temporaries).

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

### 🔁 Lifecycle & Upstream Updates

* **On Addon Updates:** When Kodi updates an upstream addon, it wipes the directory and extracts pristine source files,
  naturally reverting all patches. The Wizard engine detects missing markers on next boot and reapplies hooks
  seamlessly.
* **On Rollbacks / Disabling:** By setting `"enabled": False` or removing a configuration, the engine targets the
  specific versioned marker and drops only injected lines, instantly returning the file to its original upstream state.

---

## 7. The AI Subtitles Service

**`service.subtitles.kodipovilai`** ("MoranSubs") is a Hebrew‑subtitles engine and
the build's runtime patch host in one addon.

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
  is gone (see [§9](#9-removed--legacy-components)). Comments that mention
  "DarkSubs" describe the UX being replicated, not a live dependency.

---

## 8. Skins & the Quick‑Update / Force‑Close UX

**Skins** ship in the manifest but most are **on‑demand**:

- **`skin.fentastic`** — the default/active skin.
- **`skin.povil.nox`** (large) and **`skin.arctic.fuse.3`** (AF3) — installed only
  via the wizard's *Switch Skin* flow (`post_install_provisioning(ids=[...])`,
  headless‑first), never force‑installed by the updater.
- **`skin.estuary`** — kept as the safe fallback skin.

**Quick Update** is the user‑facing "fast update" button: a favourite/menu action
`PlayMedia(plugin://plugin.program.kodipovilwizard/?mode=install&action=quick_update&...)`
that runs the **manifest‑based** update (version diff → modular download/extract →
config merge). It is **not** a monolithic re‑download.

**Force‑close** (`wizard.force_close_kodi_in_5_seconds(...)`) is used **only** when
a critical update genuinely requires a restart (the wizard itself or the active
skin). It is a controlled, announced shutdown — **never** a way to "apply" ordinary
updates and **never** a crash. Fresh installs perform a single force‑close at the
very end, *after* the `.provisioned` marker and `config.zip` are in place.

---

## 9. Removed / Legacy Components

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

---

## 10. Repository Layout & Addon Inventory

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
├── plugin.program.kodipovilwizard/          # the install/OTA/self‑heal engine
│   └── resources/libs/{modular_updater,headless_installer,config_apply,downloader,extract,db}.py
│       └── lib/patches/                     # UNIFIED directory for registry & decoupled logic modules
├── plugin.program.orderfavourites-hebrew/   # favourites generator + icon/media installer
│   ├── povil_icons/                         # flat canonical icon set (source of truth)
│   ├── resources/favourites_config.json     # tiles + per‑skin order + icon_base
│   └── favourites_generator.py
├── service.subtitles.kodipovilai/           # AI Hebrew subtitles + runtime patch host (~38 patchers)
├── repository.{kodifitzwell,Fishenzon,otaku,jurialmunkey}/   # third‑party repos (for content addons)
├── skin.{fentastic,povil.nox,estuary}/      # skins (fentastic active; others on‑demand)
└── script.{fentastic.helper,module.autocompletion}/ , plugin.program.autocompletion/
```

**Manifest addon inventory** (versions are illustrative — the manifest is the live
source):

| Addon                                                            | Type          | Role                                                |
|------------------------------------------------------------------|---------------|-----------------------------------------------------|
| `plugin.program.kodipovilwizard`                                 | plugin        | Install / OTA / self‑heal engine                    |
| `plugin.program.orderfavourites-hebrew`                          | plugin        | Favourites generator + global icon/media installer  |
| `service.subtitles.kodipovilai`                                  | subtitle      | AI Hebrew subtitles + runtime patch host            |
| `script.fentastic.helper`                                        | module        | FENtastic skin helper                               |
| `script.module.autocompletion` / `plugin.program.autocompletion` | module/plugin | Search autocompletion                               |
| `repository.kodifitzwell`                                        | repository    | Source for `plugin.video.pov`                       |
| `repository.Fishenzon`                                           | repository    | Source for `plugin.video.idanplus`                  |
| `repository.otaku`                                               | repository    | Source for `plugin.video.otaku` (+ `context.otaku`) |
| `repository.jurialmunkey`                                        | repository    | Source for Arctic Fuse 3 + helpers                  |
| `skin.fentastic`                                                 | skin          | Default/active skin                                 |
| `skin.povil.nox`                                                 | skin          | On‑demand skin (large)                              |
| `skin.estuary`                                                   | skin          | Fallback skin                                       |

**Content addons (NOT in the manifest — provisioned headlessly):**
`plugin.video.pov`, `plugin.video.idanplus`, `plugin.video.otaku`,
`plugin.video.youtube`, `resource.language.he_il`.

---

## 11. Strict Guidelines for AI Agents & Contributors (CRITICAL)

### Anti‑patterns — DO NOT do these

- ❌ **NEVER revert to monolithic `.zip` payloads** for installs/updates. Updates are
  per‑addon, manifest‑diffed, sha256‑verified zip extractions. No "download the
  whole build" path.
- ❌ **NEVER reintroduce the brute‑force wipe.** The build hydrates and merges; it
  does not destroy user data.
- ❌ **NO rogue background patchers hiding in secondary addons** (e.g. a stray
  `wizard_self_healer.py`). All cross‑addon influence must be **native or safely
  hooked** — marker‑guarded, versioned, fail‑safe — and live in the **subtitle
  service patch host**. Inter‑addon calls go through Kodi (`RunScript` /
  `RunAddon` / `executebuiltin`), not import side‑effects.
- ❌ **NEVER bring back the aggressive dialog watchdog** that closes arbitrary
  windows. The only permitted auto‑confirm is the **minimal** one that clicks **window 10100 only**.
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
- ✅ **Keep patchers fail‑safe and idempotent.** Marker‑guard every on‑disk edit;
  add the previous marker to `OLD_MARKERS` on a version bump; make a missed
  anchor a no‑op, never a crash.
- ✅ **Prefer headless** install paths; the native `InstallAddon` is a last resort
  with the minimal confirmer only.
- ✅ **Verify before shipping.** `python -m py_compile` every changed `.py`; validate
  every JSON/XML; for patchers, simulate the injection against the real host
  source when possible.

---

## 12. Git & Release Workflow

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

*This README reflects the post‑migration architecture (modular manifest engine,
headless installer, config‑pack timing, runtime patch host). Keep it updated when
the installation, config, asset, or patching contracts change — it is the contract
future agents will rely on.*
