# Kodi‑POV‑IL Build — Developer & AI Agent Bible

> A modular, repository‑driven Hebrew Kodi build centered on the **POV** video addon,
> the **FENtastic** skin, and an in‑house **AI Hebrew‑subtitles** service.
> This document is the canonical reference for humans **and** AI agents working on
> the repository. Read it fully before changing installation, config, asset, or
> cross‑addon‑patching code.

---

## Table of Contents

1. [Project Overview & Architecture](#1-project-overview--architecture)
2. [CI/CD & the Repository Pipeline](#2-cicd--the-repository-pipeline)
3. [The Installation Engine](#3-the-installation-engine)
    - [A. Repositories, Native Provisioning & the First‑Run Bootstrap](#a-repositories-native-provisioning--the-first-run-bootstrap)
    - [B. Install, Updates & Self‑Healing](#b-install-updates--self-healing)
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
    - [Wizard code layout: domain folders under `libs/`](#wizard-code-layout-domain-folders-under-libs)
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

The current architecture is **repository‑driven**. Our add-ons are served by two standard **Kodi repositories** hosted on
GitHub Pages; **Kodi itself installs and updates them** (and resolves their `<import>` dependencies). The Wizard no longer
downloads, extracts or dependency-resolves add-ons. It manages only what a Kodi repository cannot: the `userdata/` config
pack, first-run provisioning, healing, and the runtime patches of other add-ons.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  GitHub (source of truth) ── CI: .github/scripts/gen_repo.py ──► GitHub Pages │
│    repo/addons.xml (+ .sha256)    zips/<id>/<id>-<ver>.zip (+ .sha256)         │
│    <id>-latest.zip (+ .sha256)    config/config.json + config-<ver>.zip        │
└───────────┬─────────────────────────────────────────────┬─────────────────┘
            │ repository.povil   (our add-ons)              │ config/config.json
            │ repository.povil.upstream (3rd-party indexes) │ (the userdata pack)
            ▼                                               ▼
┌───────────────────────────────┐        ┌────────────────────────────────────┐
│ Kodi (native)                 │        │ Wizard: plugin.program.kodipovilwizard │
│  installs + updates add-ons,  │◄───────│  repository bootstrap (first run),     │
│  resolves <import>s, verifies │ Install│  InstallAddon for the build's add-ons, │
│  index and zips with sha256   │ Addon  │  config pack, healing, PatchEngine     │
└───────────────────────────────┘        └────────────────────────────────────┘
```

Three classes of payload:

| Payload                         | What                                                                                                                  | How the device gets it                                                                                                                                  |
|---------------------------------|-----------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Our add-ons**                 | Wizard, skins, subtitles, favourites, helpers, the two repository add-ons                                             | `repository.povil` (our own `<dir>`, index + every zip verified with sha256 by Kodi); Kodi installs and updates them                                      |
| **Third-party content add-ons** | `plugin.video.pov`, `plugin.video.idanplus`, `plugin.video.youtube`, `resource.language.he_il`, Account Manager (`script.module.acctmgr` + `acctvwr`), optional Umbrella / Otaku / Arctic Fuse 3 | `repository.povil.upstream` aggregates the developers' own indexes (it is separate on purpose, see below); the Wizard asks Kodi to install them, and they keep updating from their developers. **No third-party code is stored in this repository or on our site.** |
| **`config-<ver>.zip`**          | The `userdata/` tree (settings, favourites, sources, skin settings, POV settings)                                     | Not an add-on. `config/config.json` names it; `config_apply.py` applies it per `config_policy.json` — **always the final step** of a fresh install        |

### Two install paths

1. **Repository first (the documented route):** add the site URL as a source (Kodi's file browser lists exactly one file there,
   `repository.povil-latest.zip`: `gen_repo.write_landing` allows one static zip link and `verify()` enforces it), install
   `repository.povil` from it, then install the Wizard from the repository. The Wizard's first start installs
   `repository.povil.upstream`. Neither repository add-on has a `<requires>` block: Kodi 21 refuses to install a repository
   add-on that declares any dependency (`failed to install repository ... It has dependencies defined`), so Kodi cannot pull the
   upstream repository in by itself and `gen_repo.py` fails the build on a `<requires>` in either of them.
2. **Standalone Wizard zip:** download `https://devgeniuscode.github.io/Kodi-POV-IL-Build/plugin.program.kodipovilwizard-latest.zip`
   in a normal browser (or the button on the site's `index.html`) and install the file from local storage. The first start installs
   **both** repository add-ons from the site's `<id>-latest.zip` (each verified against `<id>-latest.zip.sha256`;
   `provisioning/repo_bootstrap.py`). The Wizard does **not** `<import>` the repository, because a zip installed without it would
   fail on the missing dependency. **Do not install it from a remote source in Kodi's file manager:** Kodi's `zip://https://` reader
   opens one HTTP range connection per file (274 files, about 1 s each, and a cold CDN edge can stall one for a minute), which
   takes minutes and ends in cURL error 28. The Wizard zip and `repository.povil.upstream-latest.zip` are deployed at their root
   URLs but deliberately not linked as `<a href>` on `index.html`.

Either way the build then provisions itself exactly as before (banner, fresh-install resume, `.provisioned` marker).

### Why two repository add-ons

Kodi merges every `<dir>` of a repository add-on into one update and **fails the whole update when any single `<dir>` fails**
(`xbmc/addons/Repository.cpp`, `FetchIfChanged`). `repository.povil` therefore holds ONE `<dir>` (ours, `verify="sha256"`,
`<hashes>sha256</hashes>`), and everything we do not control lives in `repository.povil.upstream`: a dead third-party server can
stop POV from installing, never our own add-ons from updating. `gen_repo.py` enforces the split.

### Why the index is not at the site root

Browsing an HTTP source in Kodi's file manager makes Kodi keep that folder's listing in memory (`CDirectoryCache`,
`DIR_CACHE_ONCE`). `CFile::Open()` then answers "no such file" **without any network request** for every file of that folder the
listing lacks. The listing of the site root is what `index.html` links (only `repository.povil-latest.zip`), so a root-level
`addons.xml.sha256` fails the repository update at once with `CRepository: failed read '.../addons.xml.sha256'` and no
`CCurlFile` line in the log (seen on Kodi 21 right after installing `repository.povil` from the browsed source). `repository.povil`
therefore reads `repo/addons.xml` + `repo/addons.xml.sha256`, and `gen_repo.verify()` fails the build if an `addons.xml*` appears
in the root. `zips/<id>/` and `upstream/<name>/` are folders nobody browses, so they are safe; never link them from `index.html`.

### Native installs and updates

- **Install:** `provisioning/provisioner.install_native()` calls Kodi's `InstallAddon(<id>)`, which resolves the add-on and its
  whole dependency tree from the repositories. A tiny watchdog accepts only the dependency Yes/No dialog (window 10100).
- **Update:** Kodi's own updater installs them, but only inside the Wizard's **update window** (`provisioning/update_window.py`, run
  by the host-repair runner). The build keeps `general.addonupdates` at **NOTIFY** (`1`; Kodi 21: `0` = install automatically,
  `1` = notify, `2` = never; baked in `userdata/guisettings.xml`): at boot Kodi checks the repositories and only shows a toast, it
  installs nothing, so it never replaces the running Wizard, a running service or the active skin while the build starts (the 20 Nov
  2026 freeze, see "Update window" below). Kodi never downgrades: to roll back, publish a **higher** version carrying the old code.
- **After an update:** an add-on that Kodi replaced loses its runtime patches. `host_repairs/flow/addon_update_watch.py` watches
  every add-on the PatchEngine patches (POV, Umbrella, Idan Plus, Arctic Fuse 3): when one is installed or updated it waits until its
  folder and `addon.xml` are stable and re-applies its patches under one lock, within seconds and without a restart (§6). It stands
  down during a clean reinstall and a fresh install. A skin Kodi updates is Kodi's business (it posts `ReloadSkin` itself).
- **Config vs. updates:** before `config_apply` writes settings it waits (bounded) for Kodi to finish installing or updating the
  add-ons whose `addon_data` the pack carries, so an update cannot overwrite what was just written; on an existing device a timeout
  defers the pack to the next start (§4).



`config-<ver>.zip` carries `userdata/`, including **POV's own `settings.xml`**
(Hebrew metadata language, fanart, TMDB/Trakt keys) and the skin settings. It **must be extracted/applied as the
absolute final step of a fresh install — after
every addon is on disk** — so that nothing an addon writes at install/first‑init
can clobber our configuration. Applying it earlier reintroduces the *black‑background + English‑metadata* race. This
ordering is enforced in
`provisioner.run_fresh_install()`.

### Active-profile paths

`CONFIG.USERDATA` is the **active profile's** userdata, not Kodi's `special://userdata/` alias (which always points at
the master). Everything the engine keeps per profile — the `kodipovil.provisioned` marker, favourites,
`config_applied_version`, GUI defaults — lives with the profile it describes. `CONFIG.MASTERPROFILE` and
`CONFIG.PROFILES` (`profiles.xml`) address the shared, master-owned files. Engine code must therefore never assume
`CONFIG.USERDATA` is the master; see [§9](#9-profiles).

---

## 2. CI/CD & the Repository Pipeline

**Workflows** (`.github/workflows/`):

- `deploy-repo.yml` — on every push to `main` that touches an add-on folder, `userdata/`, `.github/scripts/` or itself (and on
  demand): checks the baked favourites, runs `gen_repo.py`, uploads the result as a **GitHub Pages artifact** and deploys it
  (`actions/deploy-pages`). A Pages artifact replaces the whole site, so **every add-on is rebuilt on every run** (the zips are
  deterministic: sorted entries, fixed timestamps, so unchanged add-ons produce identical bytes). There is no release asset to
  prune and no zip in git history.
- `upstream-health.yml` — daily (and on changes to `repository.povil.upstream/`): `check_upstreams.py` fetches every
  third-party index the upstream repository lists.

**One-time setup:** repository Settings → Pages → Source = **GitHub Actions**; Settings → Environments → `github-pages` must
allow the `main` branch.

**`gen_repo.py`** builds the site (`--out site --base-url https://<owner>.github.io/<repo>`):

```
index.html  .nojekyll                       landing page; plain <a href> links, which Kodi's HTTP file browser follows
repo/addons.xml  .sha256  .md5              every add-on's addon.xml; the sha256 is verified by Kodi (verify="sha256"). In its own
                                            folder ON PURPOSE (`INDEX_DIR`): see "Why the index is not at the site root" below
<id>-latest.zip (+ .sha256)                 stable names for repository.povil, repository.povil.upstream and the Wizard
zips/<id>/<id>-<ver>.zip (+ .sha256)        the <datadir>; every zip is verified by Kodi (<hashes>sha256</hashes>)
zips/<id>/icon.*, fanart.*, changelog-<ver>.txt
config/config.json  config/config-<cfgver>.zip   the userdata pack and {config_version, file, url, size, sha256, commit}
upstream/<name>/addons.xml (+ .sha256, .md5)     a CI-FILTERED copy of a third-party index (see below); no third-party zip is stored
```

It fails the build (and nothing is deployed) on: a malformed `addon.xml`, a folder name that is not the add-on id, a duplicate
id, a missing required add-on (both repositories, the Wizard), `repository.povil` having anything but one `<dir>` that matches
`--base-url`, `repository.povil.upstream` pointing back at our site, or a finished site that fails `verify()` (index and zip
checksums, zip contents, `config.json`). It warns about versions with letter suffixes (Kodi's comparison of those is unverified).

**Filtered third-party indexes** (`FILTERED_UPSTREAMS` in `gen_repo.py`): Zaxxon709's index carries Account Manager, which we need,
next to `skin.fentastic 100.x` and `script.fentastic.helper 100.x`, which would replace our forks (Kodi installs the highest version of
an id). At build time `gen_repo.py` fetches that index, keeps only `script.module.acctmgr` and `script.module.acctvwr`, and publishes
`upstream/zaxxon709/addons.xml` + `.sha256`. `repository.povil.upstream` points its `<info>` / `<checksum verify="sha256">` at that
copy and its `<datadir>` straight at Zaxxon709's own zip server: Kodi downloads the zips from them and we store none of their code.
A temporary outage of their server never breaks our build: the sources are tried in the order live upstream -> the copy of the
previous deploy (read from our own site) -> an empty index (first deploy only; the add-ons are then simply not offered until a later
deploy). The build prints which one was used.

**`check_upstreams.py`** fails on: a third-party checksum or index that cannot be fetched or parsed, an upstream index that
carries an **id of ours** (Kodi installs the highest version found anywhere, so an upstream `skin.fentastic 100.x` would replace
our fork), an add-on the build needs that is missing or whose zip does not answer, or a filtered copy that still lists an add-on it
should not (the filter is broken). The filtered copy on our own site is checked leniently (a 404 before the first deploy is only a
warning) and the third party's own index is checked directly. A stale upstream checksum is only a warning
(Kodi verifies a checksum only when `verify=` is set, and we never set it on a third-party `<dir>`).

**Key consequences for contributors:**

- To ship a change you **bump the `addon.xml` version** (or `config_version`) and push. Kodi only updates to a **higher** version:
  keep versions strictly increasing and purely numeric.
- Never hand-edit anything that `gen_repo.py` generates. `repository.povil/addon.xml` must keep naming the site in `uservar.py`
  (`REPO_BASE_URL`); a fork changes both.
- A new third-party add-on is a new `<dir>` in `repository.povil.upstream/addon.xml` (copy the upstream's version ranges), plus its
  id in `check_upstreams.REQUIRED`. When an upstream index carries ids of ours (Account Manager's does), add it to
  `FILTERED_UPSTREAMS` instead: nothing third-party is ever copied into this repository.



Owner addon: **`plugin.program.kodipovilwizard`**.

### A. Repositories, Native Provisioning & the First‑Run Bootstrap

Owner modules (all in `resources/libs/provisioning/`):

| Module              | Role                                                                                                                                                              |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `repo_bootstrap.py` | `ensure_repositories()`: installs / enables `repository.povil` and `repository.povil.upstream` from the site's `-latest.zip` (sha256-checked, only an `<id>/` folder is extracted), then `UpdateLocalAddons` + `UpdateAddonRepos`. The **only** place the Wizard extracts an add-on zip. |
| `provisioner.py`    | The id lists (`PROVISION_IDS`, `CORE_PROVISION_IDS`, `OWN_PROVISION_IDS`), the `.provisioned` marker, `install_native()`, `provision()`, `run_fresh_install()`, `run_update_check()`, `heal_missing_addons()`. |
| `addon_state.py`    | `has_addon`, `on_disk`, `enable_addon` (JSON-RPC), `register_and_enable` (Addons DB), `local_version`, `versions_snapshot`, the file-based settle signals (`tree_stamp`, `updates_in_flight`, `wait_until_settled`) and `check_repositories()` (a non-blocking `UpdateAddonRepos`, then waits for a new `lastcheck` in the Addons DB). No network, nothing asks the GUI but `has_addon`. |
| `update_mode.py`    | Kodi's `general.addonupdates` through JSON-RPC (`get_mode`, `set_mode`, `ON` / `NOTIFY` / `NEVER`), the "an update window is open" flag, and `settle_at_start()` (the first call of `startup.py`). Imports nothing of the Wizard. |
| `update_window.py`  | `UpdateWindow` (the runner's idle loop calls `poll()`), `request_manual()` (Maintenance "check for updates"). See "Update window". |
| `build_sync.py`     | The start-up pass called by `startup.py`: `run_update_check` (repositories only) then `heal_missing_addons`.                                                       |

**`install_native(ids)`** — for each add-on not yet enabled: one that is on disk but disabled is only **enabled** (never
reinstalled: the "enable loop"); the rest get `InstallAddon(<id>)` and a bounded wait for `System.HasAddon`, with a second attempt
after a repository refresh (right after the bootstrap Kodi may not have loaded the index yet). Binary add-ons (`inputstream.*`,
`pvr.*`, …) need no special case any more: Kodi picks the platform-correct build itself.

**`run_fresh_install(progress)`** → `(ok, issues)`: `repos` → `content` (every add-on of `fresh_ids()`: our own ones, the default
skin and `PROVISION_IDS`) → `config` (`config_apply.apply_config_pack`, always last) → verify (`required_ids()` enabled and the
config version applied) → write the `.provisioned` marker. Idempotent: a resumed run only does the missing work. The banner
phases are `repos / content / config / finalize` (`provision_progress.PHASES`).

**`run_update_check(background)`** — the start-up pass and the manual "check for updates" (`mode=modular_update` / `wizardupdate`,
Quick Update). Start-up (`background=True`): bootstrap the repositories if needed, nothing else (no check is posted, no pack applied:
the update window does both). Manual: asks the runner for a window and returns (a Hebrew dialog says "started in the background" /
"already running" / "the service is not running"); the window ends with a notification.

**Update window** (`provisioning/update_window.py`; 20 Nov 2026 freeze: with automatic installs Kodi's own check, 3 s after launch,
replaced the running Wizard, a running service and the active skin while a dozen services were starting; stopping a service holds a
lock the GUI thread takes on every frame, and replacing the active skin needs a blocking round trip to the GUI thread). The runner
opens a window at the first idle moment 60 s after it starts, every 6 hours after that, and on a manual request:

1. **Gate** (automatic runs only): `Window.IsActive(home) + !Player.HasMedia + !System.HasActiveModalDialog`, asked on the window's own
   worker thread (a GUI that does not answer can only hold that thread). A busy GUI retries after 2 minutes. `NEVER` (the owner's
   choice in Maintenance -> Toggle Addon Updates) skips automatic windows. A clean reinstall or a fresh install in progress skips all.
2. **Open:** `update_mode.set_mode(ON)`, the flag `povil.update_window` (a monotonic stamp, refreshed every 2 s), then a posted
   `UpdateAddonRepos` (Kodi reads the mode when its repository check job **completes**, so it must be ON until the installs start).
3. **Settle**, file-based: wait until installs started and nothing was written to `addons/packages` / `addons/temp` for 15 s; when
   none start, until the check is done (a new `lastcheck` in the Addons DB) plus 12 s, at most 45 s; hard limit 20 minutes.
4. **Close**, always (`try / finally`): the mode goes back (NOTIFY, or the owner's NEVER), the flag is cleared.
5. **After:** if the Wizard itself was updated, this runner still runs the old code: it asks the runner to hand over (the runner
   releases its lock, then starts `host_repairs_service.py` again, and the new instance applies the config pack: flag
   `povil.update_window.resume`). Otherwise the newer config pack is applied now (`config_apply` waits for the add-ons it writes to),
   and the active skin is reloaded once if the pack touched it.

**Service shutdown.** After a script's `main` returns, Kodi (`CPythonInvoker::execute`) waits, without a timeout of its own, for
**every** thread of the script's interpreter, daemon or not; whoever asked the service to stop (Kodi's installer, an exit) is held
meanwhile and Kodi kills the script after 5 s ("script didn't stop in 5 seconds"). So every long-lived thread must **end** within a
moment of Kodi's abort request (`monitor.waitForAbort`, never a long `sleep` or a call without a timeout). `common/thread_report.py`
(the runner, the startup service) and `core/threads.py` of the subtitle add-on log, when a script ends, one WARNING per thread that is
still alive: its name, whether it is a daemon, and the frames it waits in (`[Threads] ... is still alive, in: ...`).

Crash safety: a JSON-RPC change is not written to `guisettings.xml` at once (Kodi saves settings when it exits), so a crash or a
force-kill inside a window normally leaves NOTIFY on disk; a clean exit inside a window is repaired by `update_mode.settle_at_start()`,
the first call of `startup.py` (ON becomes NOTIFY unless a window is open: a Kodi update of the Wizard restarts `startup.py` in the
middle of one). Residual risk: Kodi's own boot check runs about 3 s after launch, before any service, so an ON that reached the disk
would still be used once. `update.addon_updates('reset')` (after a wipe) always returns to NOTIFY.

**What the user sees** (`gui/install_progress.py`, skin `DialogInstallProgress.xml`; the same branded panel the old installer had, now
only a *view*): a header, a one-line **stage** ("מתקין את מאגרי הבילד" → "מתקין תוספים (3/11): POV" → "מחיל את הגדרות הבילד" → "בודק שהכול הותקן" →
"מסיים: מדיה, טלאים ומסך הבית"), one **row** per step in order (the two repositories, every add-on by its Hebrew name, the build settings,
the final touches) with a glyph, a status ("ממתין" / "מתקין..." with the loader and a small bar / "הותקן" / "כבר מותקן" / "נכשל"), and the **overall
bar 0-100%**. The percentage is the shared `ProvisionProgress` value, the same one the top-right banner shows (the banner message follows the same
stages); without a tracker (optional add-on installs) it is the share of finished rows. Kodi installs have no measurable progress, so the
bar of the add-on being installed creeps toward its slot (about 20 s) and snaps to the next one when Kodi reports it installed. Kodi's own
modal install dialog (`InstallAddon` is modal and asks "install <add-on>?", which the confirmer accepts) still opens on top for the length of
each native install; this window is what stays on screen between them. State lives in `InstallPlan` (plain Python); `provisioner` reports
through `plan / item_state / item_progress / set_stage`, every call guarded: a window that cannot load returns `None` and the banner alone
carries the run. Shown by a fresh install (`auto_install`, manual full install) and by the foreground installs of Umbrella, Otaku, Account
Manager and the skins; the start-up healing is silent.

**`config/config.json`** (`config_apply.load_config_info()`; `CONFIG.REPO_BASE_URL` / `CONFIG.CONFIG_INFO_URL`, set in
`uservar.py`) is the Wizard's only state input from the site. A pack without a sha256 is never applied or retained.

**Provision lists** (`provisioner.py`):

```
PROVISION_IDS = script.module.acctmgr (Zaxxon709, filtered index) · plugin.video.pov · plugin.video.idanplus ·
                plugin.video.youtube · resource.language.he_il · script.xbmc.unpausejumpback
OWN_PROVISION_IDS = plugin.program.autocompletion · plugin.program.orderfavourites-hebrew ·
                    script.fentastic.helper · service.subtitles.kodipovilai   (+ the default skin on a fresh install)
```

`plugin.video.otaku` and Umbrella (+ CocoScrapers) are **optional**: installed on demand from the Wizard's main menu →
**תוספים וסקינים אופציונליים** (`addons/otaku.py`, `addons/umbrella.py`) through the same `provisioner.provision(ids=[...])`, resolved
from the developers' indexes in `repository.povil.upstream`. Because they are not in `PROVISION_IDS`, the start-up healing does not put
them back after the user removed them. Their baked settings (`userdata/addon_data/...`, seeded only if absent) are still laid down by a
fresh install, so they start configured.

### B. Install, Updates & Self‑Healing

- **`.provisioned` marker** (`kodipovil.provisioned` in the **active profile's** userdata, `CONFIG.USERDATA`). Written **only** when a fresh install reaches
  the very end (every addon attempted **and** `config.zip` applied) *and* Kodi is not aborting. `startup.py` uses it to
  tell a *completed* setup from one the user force‑closed mid‑provisioning, so an interrupted setup is **resumed** next
  launch instead of left half‑built. `run_fresh_install()` is idempotent — present addons are skipped, the config pack
  self‑applies once.
  Secondary profiles receive the marker when they are seeded ([§9.3](#93-profile-seeding--configuration-management)).
- **`heal_missing_addons()`** — a start-up self-heal. It verifies with `System.HasAddon(...)` that every add-on of
  `heal_ids()` (our own add-ons and `PROVISION_IDS`; skins are the user's choice) is present and, if anything is missing, installs
  it through `provision()` (the repositories first, then Kodi's native `InstallAddon`). This guarantees the build self-completes
  over a couple of launches if an installation is ever partially interrupted.
- **Graceful reload, not crash.** Add-on updates are Kodi's: it reloads the active skin and restarts the services it updated by
  itself, so the Wizard never force-closes Kodi for an ordinary update. A config change that touched the active skin's look is
  applied in place with **`ReloadSkin()`**.
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
- **Clean reinstall (`provisioning/clean_reinstall.py`, Maintenance → "התקנה מחדש של הבילד נקי", `mode=clean_reinstall`).**
  Wipes Kodi with `install.wipe()` (the Fresh Start engine) and lets the next start provision the build from scratch
  (`fresh_install.begin()` → no `.provisioned` marker → the install above). Master profile only; a kids profile is stopped by
  the router's child gate (the mode is not in `profiles/hooks._CHILD_ALLOWED`); nothing may be playing. Order:
  1. **Pre-flight, before anything is deleted:** `config/config.json` loads (with a sha256) and every file the Wizard itself downloads
     (our `repo/addons.xml`, both repository zips, the config pack) answers a one-byte ranged GET. Offline or unreachable → a Hebrew
     dialog and nothing is touched. (The wipe removes the repository add-ons too; the next start bootstraps them again.)
  2. **Questions:** what to keep (nothing is pre-selected): Account Manager + POV (the add-ons and their data are excluded from the
     wipe as whole folders, as the existing keep switches do), Umbrella, `profiles.xml` (profile contents are rebuilt); then two
     confirmations. The keep flags are set in memory only and every other `KEEP*` switch is forced off (a clean reinstall ignores the
     user's Fresh Start switches).
  3. **Quiesce:** `addon_update_watch` stands down while the window property `kodipovil.clean_reinstall` is set; Kodi's add-on
     auto-update is switched off by `install.wipe()` and restored afterwards. The skin is not switched (`skin_to_default` is a
     documented no-op; the hard kill follows and the next start provisions the default skin).
  4. **After the wipe:** POV's `povil_dropped_*.json`, `povil-*.json` and `navigator.before-*.db` are removed unless POV data is kept; the
     rows of the Addons DB whose add-on no longer exists are deleted; the Wizard's retained config pack, the build settings
     (`installed`, `config_applied_version`, `fresh_build_auto_install_done`, ...) and every `kodipovil.*` marker are reset; then
     `fresh_install.begin()` and the same hard kill as Fresh Start. A wipe cancelled half way still arms the resume.

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
- **Waits for Kodi's installs.** `apply_config_pack` first collects the add-ons whose `addon_data` the pack writes and calls
  `addon_state.wait_until_settled()`: none of their folders changed for 4 s and no add-on zip / unpack folder was written for 15 s
  (`updates_in_flight`; file-based, no GUI call). Timeouts: 60 s on a fresh install (it proceeds anyway), 120 s on an existing
  device, where a timeout **defers** the pack (`result['deferred']`, nothing written, the version stays unapplied, the next update
  window retries) so a Kodi update cannot overwrite freshly applied settings. The pack is applied by the **update window**, after
  its installs, never at boot.
- **Retained verified pack.** When `config.json` supplies a sha256 and the pack verifies, `config_apply` keeps a copy in
  the **master** profile (`addon_data/plugin.program.kodipovilwizard/config_pack/build_config.zip` plus a
  `build_config.json` receipt). Secondary profiles are seeded from it through `config_apply.open_verified_pack()`; the
  Wizard ships no config bundle of its own. A `config.json` without a sha256 is rejected (the pack is never applied or retained). API and
  seeding rules: [§9.3](#93-profile-seeding--configuration-management).

---

## 5. Assets & Dynamic Media Management

### Centralized flat icon set

- The canonical **source** of the build's custom icons is a **flat** directory
  `plugin.program.orderfavourites-hebrew/resources/media/povil_icons/` (no nested subfolders; the same `resources/media/` also holds `fonts/` and `splash.jpg`).
- On startup, `plugin.program.orderfavourites-hebrew/resources/lib/media_installer.py`
  **deploys/overwrites** that set into the **global media cache**
  `special://home/media/povil_icons/` (`resources/media/fonts/` goes to `special://home/media/fonts/`; the case of that folder is an open item, see "Known items" below).
  Kodi/skins/favourites read icons from the **global folder**, never by reaching
  into another addon's private folder (avoids cross‑addon containment breaches).
  The installer overwrites on every run so icon updates always reach users.

### Wizard artwork (`plugin.program.kodipovilwizard/resources/media/`)

- `CONFIG.ART` and `uservar.ART` both point at `resources/media/`. The menu icons are the 12 round labelled PNGs there, wired in `uservar.py`:
  `ICONBUILDS` builds.png, `ICONMAINT` maintenance.png, `ICONSPEED` speed.png, `ICONAPK` apkinstaller.png, `ICONADDONS` addoninstaller.png, `ICONSAVE` savedata.png, `ICONTRAKT` keeptrakt.png,
  `ICONREAL` keepdebrid.png, `ICONLOGIN` keeplogin.png, `ICONCONTACT` information.png, `ICONSETTINGS` settings.png, `ICONWIZARD` wizard.png (the title row of the main menu).
  `CONFIG.ICON*` fall back to the add-on icon when a value is missing or `http://`.
- Other files there: `qricon.jpg` (`CONTACTICON`, the QR of the Contact window), `kodi_rd_il_icon.png` (`HEADERIMAGE`, the notification window), `skins_bg.jpg` (`SKINS_BG`, a placeholder
  for a future preview composite of the skins), `tonight.png`, and `profile-ui/`. The skins read `profile-ui/*` and `tonight.png` by absolute `special://home/addons/plugin.program.kodipovilwizard/...`
  paths: never move them.
- The POV repair step `_maybe_install_pov_gemini_icon` copies the subtitle add-on's `resources/media/gemini.png` into POV's `resources/skins/Default/media/` (the Account Manager list icon).

### Known items (open, not fixed)

- **F1, missing icons.** Code or data name icons that no file provides: `povil_icons/switch_skin_pov_il.png`, three `build_icons/Twilight/...` paths (`host_repairs/af3_home_patcher.py`),
  `povil_icons/{Shows_Apple_TV, Shows_Disney, Shows_The_CW, icon, power, refresh, settings}.png` (`userdata/.../widget_baselines/*.json`) and `Logo_POV.png` / `wizard.png` in the
  `skin.arctic.fuse.3` overrides of `favourites_config.json`. All are AF3 / on-demand paths.
- **F3, font folder case.** The installer writes `special://home/media/fonts/`; Kodi's font manager looks in `special://home/media/Fonts/`. Fine on Windows, to be verified on the Nvidia Shield
  (Android is case-sensitive) before any path changes. Only `arial.ttf` is used (Estuary, FENtastic, Onyx).

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
  internal skin shortcuts** — no user library data.
- **Lifecycle.** `userdata/favourites.xml` is only the **first-boot fallback**: the config pack applies it `replace` on a
  fresh install and `seed_if_absent` on an update (it never overwrites the device file, which would drop the user's
  custom tiles). The generator then owns the file: it runs at the end of a fresh install, on a skin switch, for a new
  profile, when an account service connects or disconnects (`pov_visibility_mgr`) and at every start of the add-on.
  Tiles that name an account service (`"condition"`) are emitted only while that service is connected.
- **Sync check.** The baked file must equal what the generator writes for the default skin with no service connected.
  `.github/scripts/check_favourites.py` (a CI step before the build) fails when it drifts, when an `order` key has no
  tile, or when an `inherit` points nowhere; `python .github/scripts/check_favourites.py --write` regenerates the file
  (then bump `config_version`).

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
   Every hook that calls a logic module is generated by the `_build_patch_hook()` helper in `patches/patches_registry_utils.py`, never
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
   **Exception:** a hook that is `append_after` a `def` / `class` / `if` line (the anchor ends with `:`) must start
   with a column-0 comment (`# Preserve the native function body below.`). The comment stops the dedent from
   stripping the body, which keeps its own absolute indentation; without it the body lands at column 0 under the
   `def`, the patched file no longer compiles and the engine's compile guard rejects EVERY patch of that file (the
   Kodi log then reads `Failed writing <file>: ... (line N is inside patch '<id>'); none of its K patch(es) were
   written: ...`).
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
diffable. The registry file holds entries only, grouped under large banner comments (one per domain: navigator, MDBList,
continue watching, parental control, …); `_build_patch_hook`, the shared `_*_HOOK` constants and `validate_registry` live in
`patches/patches_registry_utils.py`. The registry currently holds 150 entries (120 for `plugin.video.pov`, 13 for
`plugin.video.umbrella`, 12 for `plugin.video.idanplus`, 5 for `skin.arctic.fuse.3`).

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

Identical hooks used by several patches are defined once as `_*_HOOK` constants (for example
`_TORRENT_ID_UNBOUND_HOOK`) in `patches_registry_utils.py`, and referenced from each patch. Do not copy a hook between
patches. Hooks that run on a hot path (per item, per string, per cache write) are plain strings; behaviour that is
needed on every call is installed once at import instead (see the Hebrew labels and the shared HTTP session).

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
| `PatchEngine().run(addon_id='plugin.video.umbrella')` / `run(only_ids=[...])` | The same, restricted to one host add-on and / or a set of patch ids (the filters combine; `only_ids=[]` selects nothing). The Patch Manager runs one toggle with `only_ids=[id]` and a bulk switch with the folder's ids, so an Umbrella toggle never walks POV's 119 entries. |
| `PatchEngine.all_applied(stats)`                 | True only if every selected patch is present and current (`applied`, `upgraded`, `skipped_current`, or `optional_absent`). False for a failed, missing, unanchored or disabled patch, and for an empty selection. |
| `PatchEngine().run_if_changed()`                 | Boot fast path after a write-free full verification; any changed host file, schema, config, override or install path falls back to a full pass. Scheduling information only.       |

Stats keys: `selected`, `applied`, `upgraded`, `skipped_current`, `removed`, `skipped_disabled`, `optional_absent`,
`missing`, `anchor_missing`, `failed`, `malformed`. The engine treats only `plugin.video.umbrella` as an optional host
(absent = `optional_absent`); any other absent host counts as `missing`.

### 🧰 Patch Manager (Maintenance -> מנהל טלאים)

`gui/patch_manager.py` shows the registry as folders: one per host add-on (POV, מטריה, עידן פלוס, Arctic Fuse 3; the name table
is `ADDON_LABELS`, an add-on the table does not know shows its id) with an "active / total" counter, then, for a group of
more than 25 patches (POV), one folder per functional `category` (`CATEGORY_LABELS`, Hebrew), then the patches
(`[ON]` / `[OFF]`, a tap flips one). Each patch list and category list starts with a bulk row (enable / disable the whole
folder, with a confirm). The **כללי / מנוע** folder holds the `_pov_patching_off` switch, "run the engine now" (stats
dialog), the patcher-health report, and "reset all preferences". The user's choice is the Wizard setting
`patch_enabled_<patch id>` (`"true"` / `"false"`; empty = the registry's `enabled`), the same key the engine reads. Routes:
`patchmanager` (`addon`, `cat`), `togglepatch`, `patchbulk`, `patchengine` (`op`).

### ⏯️ Continue watching: "הסר מהמשך צפייה" (`povil_dropped_<provider>.json`)

Implemented by the `CONTINUE WATCHING` entries of the registry and `patches/pov_continue_watching.py`.

* **What the user sees.** A context-menu entry "הסר מהמשך צפייה" on an in-progress / next-episode card and on a show card
  (`mode=dropped_choice`, patched into POV's own `dropped_choice`). It hides the show from the "continue watching"
  rows; it never deletes watched history or bookmarks. The same entry (restore) lives in the hidden-shows lists.
* **Where a hide is stored.** POV's local dropped list (`add_to_sync('dropped', ...)`) always; with Trakt or MDBList
  as the watched indicator, also the account's own dropped list (`remote_toggle` verifies the cloud write before the
  snapshot changes).
* **What the rows exclude.** `dropped_info` = the local list ∪ the cloud list (Trakt `/users/hidden/dropped?type=show`,
  MDBList `/sync/dropped`); `filter_episode_sources` / `filter_show_sources` remove those shows from the next-episode,
  in-progress and in-progress-shows rows.
* **`povil_dropped_trakt.json` / `povil_dropped_mdblist.json`** (POV's `addon_data`, next to a `.lock.db` file) cache the
  cloud list so a directory never waits for the network: `{version, identity, items (tmdb ids), complete, checked,
  retry_at}`. `identity` is a hash of the provider plus the account name (or the token digest), so another account never
  inherits the exclusions. A snapshot is fresh for 300 s (`TTL`), a read has a 5 s budget, and a failed or partial read
  never replaces a complete list (it only sets `retry_at`). Before the very first successful read the rows are held back
  (`_UnknownHidden`) and a notice says the list is unavailable. POV's service refreshes it every 30 s
  (`service_prefetch`), native dropped-list activity invalidates it (`invalidate`).
* **Kept across a POV wipe** by `pov_keep_files`, and hashed by `pov_list_snapshot` (the saved-list snapshots notice a
  change). A disconnect or an account switch leaves the file behind: its `identity` no longer matches, it is ignored and
  rewritten on the next read. A clean reinstall removes it.

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

**`service.subtitles.kodipovilai`** ("MoranSubs") is the build's Hebrew‑subtitles engine. Cross‑addon patches are **not** hosted here: the
source‑hook patches live in the Wizard's patch registry ([§6](#6-runtime-cross-addon-patching)), the runtime repairs of other add‑ons in
`host_repairs/`, and the Tonight home tile in `resources/libs/tonight/` (all in the Wizard, [§11](#11-repository-layout--addon-inventory)).

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
- **Provenance badges:** a Hebrew provider row can carry one of four positive badges (`תרגום אולפנים`, `צוות תרגום מוביל`, `מתרגם מוכר`,
  `מעלה מוביל`) with an `איכות ומקור הכתובית` block in the chooser's right-hand panel; evidence is the provider's metadata, the credits read
  inside a downloaded file (a profile-folder sidecar, never during a search) and the viewer's own handles (`custom_trusted_uploaders`). Positive
  only, display only. The full description is in `service.subtitles.kodipovilai/README.md` section 5.7.

---

## 8. Skins & the Quick‑Update / Force‑Close UX

**Skins** are served by the Kodi repositories; how they are installed depends on the flow:

- **The default skin is `skin.fentastic`** (`DEFAULT_BUILD_SKIN` in `uservar.py`, read as `CONFIG.DEFAULT_BUILD_SKIN`; the
  config pack's `lookandfeel.skin` agrees). It is the only skin a fresh install or the background updater downloads, the
  one set last through `build_skin.activate`, and the one a new secondary profile starts in.
- **Every other skin is on demand, like Umbrella** (`skins/skin_catalog.py`: `on_demand_ids()` = the catalog minus the default;
  `skin_catalog.SKINS` is the single list of skin ids, `build_skin` and `child_profiles` read it): Estuary
  (the build edition), NOX, Onyx and Arctic Fuse 3 are downloaded only when the user installs them from the **optional add-ons and skins** screen
  (Wizard main menu -> תוספים וסקינים אופציונליים, `addons/optional.py`, rows from `skin_manager.add_rows`) or picks them in *Switch Skin*; Kodi keeps an *installed* one current. NOX /
  Onyx / Estuary / AF3 all install through Kodi's native InstallAddon (`skin_packs.ensure_skin_installed` →
  `provisioner.provision`): ours from `repository.povil`, AF3 from jurialmunkey's index in `repository.povil.upstream`. The one-table catalog also feeds the
  picker, so a new skin is one line there.
- **Optional add-ons (Umbrella, Otaku)** live on the same screen (`mode=optional`: an install button while the add-on is absent, a
  status line once installed). Their **launcher tiles** are gated: `favourites_config.json` gives the `umbrella` (and its
  `search_provider`) and `otaku` tiles `"condition"` values answered by `pov_visibility_mgr.is_service_active` (`System.HasAddon`),
  so the tile shows only while the add-on is installed. An install ends with `pov_visibility_mgr.refresh_favourites()`; a removal is
  noticed at the next snapshot (the add-on presence is part of the snapshot fingerprint and of `_read_states`). The tile
  `optional_addons` (always shown) opens the screen. NOX hides its Otaku buttons while it is absent (`System.HasAddon`); Onyx did already.
- **Nothing is seeded for a skin that is not installed.** `skin_catalog.blocks_seeding('addon_data/<skin>/...')` guards the
  config pack's auto-seed of unlisted files (`config_apply`), secondary-profile seeding (`profile_store.seed_profile`),
  `build_skin.prepare_layout` (`LAYOUT_DEFAULTS`) and `child_profiles.seed_skin_flags`; the default skin is never blocked.
  A skin that ships baked settings gets them from `build_skin.switch` (it seeds before activating). The runtime repairs and
  the registry already skip an absent skin (`no_af3`, `optional` entries).
- Skins carry the Profiles UI in their own XML ([§9.6](#96-skin-integration--xml-patching-strategy)); **`skin.arctic.fuse.3`**
  is the only skin patched at runtime.

**Skin switching is live.** `build_skin.switch()` enables the skin and its dependencies, activates it through Kodi's
settings API, accepts Kodi's own retention question with the scoped confirmer, and refreshes the home — no restart.

**Quick Update** is the user‑facing "fast update" button: a favourite/menu action
`PlayMedia(plugin://plugin.program.kodipovilwizard/?mode=install&action=quick_update&...)`
that runs `provisioner.run_update_check()` (the manual form: it asks the runner for an update window, see "Update window"; Kodi installs the
add-on updates itself, a newer config pack is merged after them). It is **not** a monolithic re‑download.

**Force‑close** (`Wizard.force_close_kodi_in_5_seconds(...)`, `build/wizard_actions.py`) is used **only** when
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
| `profile_store.py`      | File-level seeding: `registered_profiles()`, `profile_path()`, `write_missing()`, `seed_profile()`, `SEED_ERRORS`. Standard library at import time; `seed_profile()` reads the build name/version from `CONFIG` lazily. Receives the verified pack's path as an argument. |
| `profile_home.py`       | Regenerates an *inactive* profile's home tiles (favourites generator and the shortcut writers) against that profile's own files and connection state, before Kodi reads its cache.                                                                                                        |
| `profile_connections.py`| Explicit account inheritance: copies the selected account **fields** (never databases, watch state or unrelated preferences) from the master into an inactive profile.                                                                                                                      |
| `profile_age_guard.py`  | **Standalone** policy and decision module: `active_policy()`, `policy_for()`, `allows()`, `visible()`, `stamp_item()`, `permit_play()`, `route_allowed()`, `install_directory_guard()`, `unclassified_source_allowed()`. Imported by host add-ons by bare name (9.5).                         |
| `parental_profiles.py`  | Master-only parental setup and policy persistence (`setup()`, `set_child()`, `approve()`), and `hosts_ready()` (9.5).                                                                                                                                                                      |
| `child_profiles.py`     | Child state: the `POVILChild` skin flag (`seed_skin_flags()`, `sync_active()`), `focus_home()`, native creation-lock presets (`preset_native_locks()`), `write_xml()`.                                                                                                                      |
| `af3_skin.py`           | Runtime profile UI for Arctic Fuse 3 only (9.6). `patch()`.                                                                                                                                                                                                                                |
| `profile_service.py`    | Service-side watcher started by `hooks.start_guard()`: refreshes the `POVIL.ChildProfile` window property and stops playback in a child profile that has no valid play receipt.                                                                                                           |
| `player_styles.py`      | NOX playback controls: style chooser, player settings, change source, next-episodes list (routes in `hooks.ROUTES`).                                                                                                                                                                       |

Path-pinned inside the package: `profile_bootstrap.py` (`resources/libs/profiles/`; the skins' `Home.xml` `<onload>` runs it, so its path
is a contract). **Known issue:** FENtastic and Estuary use that path, but NOX, Onyx and `af3_skin.BOOTSTRAP` (AF3) still
point at the old add-on-root path, where the file no longer exists. Outside the package: the Wizard windows `resources/skins/Default/1080i/{Profiles,ChildHome,ProfileLogin}.xml`, the artwork in
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
| `skins/switcher.py`, `skins/af3_tools.py`, `main_menu.py` | `build_switch_skin()` (`skins/switcher.py`) consults `profile_age_guard.active_policy()` (blocked children cannot switch skin; no promo image in a child profile) and calls `build_skin.switch()`. Menu and `AF3_TOOLS` (`skins/af3_tools.py`) carry the `mode=profiles` entry. |
| `modular_updater.py`      | No Profiles code. It works per profile because `is_provisioned()` and `config_applied_version` read the active profile (9.3).                                                      |

### 9.3 Profile Seeding & Configuration Management

#### Active-profile paths

`CONFIG.USERDATA` is the **active** profile's userdata, not Kodi's master alias. Everything the engine keeps per profile
— `kodipovil.provisioned`, favourites, `config_applied_version`, GUI defaults — therefore lives with the profile it
describes. Only `profiles.xml` and the policy file below are shared, and they are addressed through
`CONFIG.MASTERPROFILE`.

#### Retained verified config pack

Secondary profiles are seeded **without any zip bundled in the Wizard**. After a config pack is applied and its sha256
matches `config.json`, `config_apply` keeps a copy in the **master** profile:

```
<masterprofile>/addon_data/plugin.program.kodipovilwizard/config_pack/
├── build_config.zip     # the verified pack
└── build_config.json    # {"config_version": "...", "sha256": "..."}
```

| API (`resources.libs.config_apply`)       | Meaning                                                                                                                                                       |
|-------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `verified_pack_path()`                    | Path of the retained pack, or `None`. The pack is **re-hashed on every call**; a corrupt, tampered or half-written copy counts as absent.                      |
| `ensure_verified_pack(info=None)`         | Returns the retained path, downloading and verifying the pack **once** if it is absent. A `config.json` without a sha256 yields `None` — an unverifiable pack is never retained. |
| `open_verified_pack(fetch=False)`         | Context manager returning a `VerifiedPack`; raises `PackUnavailable` when no verified pack exists. Only the master passes `fetch=True` (it owns the shared copy).   |
| `VerifiedPack`                            | Read-only view keyed by **userdata-relative paths**, mapped through `config_policy.json` exactly as `apply_config_pack` places files: `names()`, `in`, `read(path)`, `guisettings()`, `.version`. |

`guisettings()` returns the build's GUI defaults with the policy's machine-specific `exclude_ids` removed (screen
resolution, device uuid/name, …). The fresh-install snapshot (`kodipovil.fresh_gui_defaults.xml`) and the profile seed
use the **same** filter (`_gui_defaults`), so device-specific values never reach a profile.

#### Idempotent provisioning (`profile_store.seed_profile`)

`seed_profile(master, profile, archive, prepare_login=False, *, verified=False, gui_defaults=None)` repairs a secondary
profile from a zip whose root mirrors `userdata/`. By default `archive` must be the bundled bootstrap pinned by
`BOOTSTRAP_SHA256`; the build ships none, so `build_profiles._seed()` passes the retained pack's path with
`verified=True` (`open_verified_pack()` has just re-hashed it against the receipt) and
`gui_defaults=pack.guisettings()` (the policy's machine-specific `exclude_ids` removed, exactly as on a fresh install).
The build name and version written into the profile come from `CONFIG.BUILDNAME_DEFAULT` /
`CONFIG.BUILDVERSION_DEFAULT` (`uservar.py`). It seeds:

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
- A new profile starts in the default skin (`skin_catalog.default_skin()`, `skin.fentastic`); when the master prepares it
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

- `Home.xml`: first `<onload>` runs `special://home/addons/plugin.program.kodipovilwizard/resources/libs/profiles/profile_bootstrap.py`; the
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

- **`profile_bootstrap.py`** (`resources/libs/profiles/`): run by the skins' Home `<onload>`; keep it short and import-light. In a newly
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
- Engine files import nothing from `resources.libs.profiles` except through `hooks`. (`skins/switcher.py` and `build_skin.py`
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
| "Cannot download build defaults" when adding a profile    | No verified pack: check `[config_apply]` log lines and that `config/config.json` has a `sha256`.              |
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
  the repository + config‑pack model.
- **`ModularUpdater`, `HeadlessInstaller`, the `install_manager` window (its downloader / extractor machinery), `manifest.json`, the rolling
  `addons-latest` release and the seven third-party `repository.*` folders** — removed in the repository migration (Phase 7). Kodi
  installs and updates add-ons; the Wizard keeps the state (`provisioning/`). Their code moved: the repository bootstrap
  (`repo_bootstrap.py`: download / extract / checksum) and the add-on state helpers (`addon_state.py`). The branded install window
  survives as `gui/install_progress.py` + `DialogInstallProgress.xml` (same panel; it now only shows the run, see §3.A).
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
│   ├── workflows/deploy-repo.yml           # CI: gen_repo.py -> GitHub Pages (the Kodi repository)
│   ├── workflows/upstream-health.yml       # daily check of the third-party indexes
│   └── scripts/
│       ├── kodi_addons.py                   # discover_addons(), addon.xml parsing, type
│       ├── build_addons.py                  # package one addon folder → <id>-<ver>.zip (deterministic)
│       ├── build_config.py                  # package userdata/ → config-<ver>.zip
│       ├── gen_repo.py                      # build the whole repository site (addons.xml, zips, config/, aliases) and verify it
│       ├── check_upstreams.py               # health + collision check of repository.povil.upstream
│       ├── check_favourites.py              # CI: userdata/favourites.xml must match favourites_config.json
│       ├── format_fentastic_settings.py     # sorts / categorizes skin.fentastic settings.xml (same module as RunScript mode=sort_settings)
│       └── ...
├── repository.povil/                        # OUR repository add-on: one <dir> (this site), verify=sha256 + hashes
├── repository.povil.upstream/               # the third-party <dir>s (kodifitzwell, Fishenzon, Umbrella, CocoScrapers, Otaku, jurialmunkey)
├── userdata/                                # the build‑config tree (shipped as config/config-<ver>.zip)
│   ├── config_policy.json                   # apply policy + config_version + cleanup
│   ├── guisettings.xml / sources.xml / advancedsettings.xml / favourites.xml
│   └── addon_data/
│       ├── plugin.video.pov/settings.xml    # he metadata + fanart + keys (merge_id, creds excluded)
│       └── skin.fentastic/settings.xml
├── plugin.program.kodipovilwizard/          # the provisioning/self‑heal engine + the Profiles feature
│   ├── default.py / startup.py              # entry points named in addon.xml (plugin source / startup service)
│   ├── uservar.py                           # build identity (BUILDNAME_DEFAULT, BUILDVERSION_DEFAULT, URLs) → CONFIG
│   └── resources/
│       ├── libs/                            # see "Wizard code layout" below
│       │   ├── {config_apply,fresh_install,provision_progress,patch_engine,downloader,extract,db}.py
│       │   ├── boot/ provisioning/ build/ skins/ addons/ accounts_auth/ system/ host_repairs/   # domain folders (Phase 1 / 3)
│       │   ├── common/ gui/                 # CONFIG, logging, router, tools · Wizard menus and windows
│       │   ├── patches/                     # UNIFIED directory: patch registry (patches_config.py, 150 entries) & 44 decoupled logic modules
│       │   └── profiles/                    # the Profiles feature (§9); hooks.py is its only engine‑facing surface (+ build_skin, profile_bootstrap)
│       ├── skins/Default/1080i/             # Wizard windows: Profiles.xml + shared ChildHome.xml / ProfileLogin.xml templates
│       └── media/profile-ui/                # Wizard artwork: menu icons, qricon, tonight, kodi_rd_il_icon, skins_bg; profile-ui/ = picker / child‑home / login
├── plugin.program.orderfavourites-hebrew/   # favourites generator + icon/media installer
│   ├── povil_icons/                         # resources/media/povil_icons/: flat canonical icon set (source of truth)
│   ├── resources/favourites_config.json     # tiles + per‑skin order + icon_base
│   └── favourites_generator.py
├── service.subtitles.kodipovilai/           # AI Hebrew subtitles
├── repository.{kodifitzwell,Fishenzon,otaku,jurialmunkey}/   # third‑party repos (for content addons)
├── skin.{fentastic,povil.nox,estuary}/      # build skins; they carry the Profiles UI in their own XML (§9.6)
└── script.{fentastic.helper,module.autocompletion}/ , plugin.program.autocompletion/
```

### Wizard code layout: domain folders under `libs/`

Phase 1 of the restructuring (November 2026) broke the Wizard's two monoliths, `startup.py` (848 lines) and
`resources/libs/wizard.py` (1534 lines, now **deleted**), into small single-purpose modules, so most changes touch only
one or two files. Every new folder lives inside `plugin.program.kodipovilwizard/resources/libs/`:

| Folder           | Modules                                                                                    | Responsibility                                                                                                                                                                                |
|------------------|--------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `boot/`          | `gui_ready`, `notifications`, `build_check`, `maintenance_pass`, `resource_check`, `language_sync` | `startup.py` helpers: wait for video / the Home window / the home rows, start-up notices, the low-memory / big-cache question, APK build registration and the installed-build check, start-up housekeeping, the POV Hebrew strings sync |
| `provisioning/`  | `banner`, `auto_install`, `provisioner`, `repo_bootstrap`, `addon_state`, `update_mode`, `update_window`, `build_sync`, `clean_reinstall`            | Fresh install / resume (`fresh_build_auto_install_if_needed`), its persistent progress banner, the start-up OTA pass (`run_startup_ota`), and the Maintenance "clean reinstall" (`mode=clean_reinstall`)  |
| `build/`         | `wizard_actions`                                                                           | The `Wizard` class: fresh / normal install, GUI fix, theme, quick update, restart, force-close                                                                                                |
| `skins/`         | `skin_catalog`, `skin_manager`, `skin_packs`, `switcher`, `af3_tools`, `favourites_sync`, `skin_settings`, `fentastic_manager`, `onyx_home/` (`favourites_row`, `pvr_row`, `hero_details`), `search_provider_picker` | The skin catalog (default skin, on-demand rule, seeding guard), the Skin Manager menu (browse / install / switch), on-demand skins (Estuary / NOX / Onyx / AF3) and their auto-update, the Switch Skin flow, the AF3 tools menu, the favourites rewrite on a switch; `skin_settings`: the `lookandfeel.*` JSON-RPC helpers (`switch_to_skin`, `skin_to_default`, `look_and_feel_data`); `fentastic_manager`: FENtastic helper data / skin XMLs kept across a wipe and the home-widget layout a quick update leaves alone; `onyx_home/`: the Onyx home's favourites / live TV rows and its hero details. |
| `addons/`        | `umbrella`, `debrid_notice`                                                                                 | Add-on install / management (today Umbrella + CocoScrapers, from their official repositories); the home for all add-on management (POV, Umbrella, Idan Plus, a future POV/Umbrella selector)                                     |
| `accounts_auth/` | `acctmgr`                                                                                  | Account Manager Lite: pilot install and install-for-everyone                                                                                                                                  |
| `system/`        | `kodi_version`, `speedtest`, `playlist_timeout`                                                                | The official-Kodi release check (Android / Windows) and the network speed tests                                                                                                               |
| `host_repairs/`  | 20 patcher / repair modules (flat), `kodi_utils`, `media_assets/`, and `flow/` (`startup_pass`, `*_steps`, `runner`, `addon_update_watch`) | Runtime repairs of other add-ons and skins moved out of the subtitle add-on (Phase 3): the POV, Umbrella / MDBList / Trakt, Idan Plus, Arctic Fuse 3, NOX and Kodi patchers, the repair-state audit (`patcher_health`) and the startup-repair pass. Not the source-hook engine: that stays in `patches/`. Its settings, state files and log prefix are the Wizard's (`settings.xml`, `addon_data/`); it reads only `use_builtin_engine` and `pov_fast_navigation` from the subtitle add-on (`kodi_utils.subs_get_*`). |
| `tonight/`       | `ui`, `entrypoints`, `hub`, `engine`, `catalog`, `providers`, `history`, `storage`, `taste`, `discovery`, `experience` | The "Tonight" home tile: the one-time favourites shortcut (`entrypoints.ensure`, called by `fresh_install`, `profile_home` and the repair runner) and the picker window (`script.moransubs.tonight.xml`, `resources/media/tonight.png`), served by `Router.dispatch` `mode=tonight`; its state lives under the Wizard's `addon_data/tonight/`. |

`default.py` and `startup.py` stay at the add-on root because `addon.xml` names them. `default.py` is a generic entry
point: it hands every `mode=` to `Router` (the Onyx home modes are answered inside `Router.dispatch`, see the rules
below). `startup.py` is now a ~180-line,
ordered sequence of calls into these modules; every `sys.exit()` and both `PatchEngine` passes stay in it. `common/`,
`gui/`, `profiles/`, `patches/` and the single-purpose engine modules at the `libs/` root (`config_apply`,
`fresh_install`, …) are unchanged.

Rules for this layout:

- Import from the module that owns the code (`from resources.libs.skins.switcher import build_switch_skin`). There are
  **no compatibility shims**: old paths such as `resources.libs.wizard` do not exist.
- New subfolders of `resources/libs/` need **no `__init__.py`**: they are Python 3 namespace packages (`xbmc.python`
  3.0.0, Kodi 19+), as `profiles/` and `patches/` always were. Keep `resources/__init__.py` and
  `resources/libs/__init__.py`, which keep every `resources.libs.*` lookup inside the Wizard.
- Build-wide values (build name / version, paths, settings) come from the `CONFIG` singleton
  (`resources.libs.common.config`, fed by `uservar.py`), never from hard-coded copies.
- Imports that sit inside a function stay inside it: they break import cycles (`build` with `install` and
  `modular_updater`, `skins` with `profiles`).
- The Onyx home modes (`favourites_row`, `favourite_run`, `pvr_row`, `pvr_play`, `hero_details`, `hero_warm`) are answered
  at the top of `Router.dispatch`, **before** the child-profile gate: their modules apply the child policy themselves
  (a kids profile gets an empty row), and the gate's "build management" dialog must never fire for them. Do not add them
  to `profiles/hooks._CHILD_ALLOWED` (it also feeds `ROUTES`). The same holds for `mode=tonight`, `mode=search_provider`
  and `mode=debrid_notice_settings` (callers use `RunPlugin(plugin://plugin.program.kodipovilwizard/?mode=…)`): they were
  RunScript actions of the subtitle add-on and never gated. `addons/debrid_notice.py` was restored from git history.
- `host_repairs/` is **flat on purpose**: `patcher_health.collect()` audits every `*.py` next to itself, so the patchers
  must stay in one folder and non-patcher code (the `flow/` package) in a subfolder. The modules keep their original file
  names; only the package prefix of their imports changed (`resources.lib` → `resources.libs.host_repairs`).
- The build's startup repairs run in their own script: `startup.py` launches `host_repairs_service.py` (`RunScript`) as
  its first statement, and `host_repairs/flow/runner.py` runs them with a single-runner lock. Kodi starts only one
  service script per add-on, which is why this is not a second `xbmc.service` entry.
- Router `mode=` names are a public API used by skins, `favourites.xml` and other add-ons; moving code never renames a
  mode.

**Add-on inventory** (versions are illustrative — the live source is each `addon.xml`; the published index is `addons.xml` on the site):

| Addon                                                            | Type          | Role                                                              |
|------------------------------------------------------------------|---------------|-------------------------------------------------------------------|
| `plugin.program.kodipovilwizard`                                 | plugin        | Provisioning / self‑heal engine + Profiles                        |
| `repository.povil`                                               | repository    | OUR index (one `<dir>`)                                           |
| `repository.povil.upstream`                                      | repository    | The third-party indexes (POV, Idan Plus, Umbrella, CocoScrapers, Otaku, jurialmunkey, and Account Manager through our filtered copy of Zaxxon709's) |
| `plugin.program.orderfavourites-hebrew`                          | plugin        | Favourites generator + global icon/media installer                |
| `service.subtitles.kodipovilai`                                  | subtitle      | AI Hebrew subtitles                                               |
| `script.fentastic.helper`                                        | module        | FENtastic skin helper                                             |
| `script.module.autocompletion` / `plugin.program.autocompletion` | module/plugin | Search autocompletion                                             |
| `skin.fentastic`                                                 | skin          | Default build skin (the only one provisioned)                     |
| `skin.povil.nox`                                                 | skin          | Build skin, on demand (Skin Manager)                              |
| `skin.povil.onyx`                                                | skin          | Build skin, on demand (Skin Manager)                              |
| `skin.estuary`                                                   | skin          | Build edition of Estuary, on demand (Skin Manager)                |

**Content add-ons (NOT in this repository — Kodi installs them from `repository.povil.upstream` or Kodi's official repository):**
`plugin.video.pov`, `plugin.video.idanplus`, `plugin.video.youtube`, `resource.language.he_il`
plus `script.module.acctmgr` / `script.module.acctvwr` (Zaxxon709's zips, offered through our filtered index), and
`plugin.video.otaku` / `plugin.video.umbrella` only when the user installs them from the optional add-ons screen.

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
9. **Domain layout.** Wizard code goes in its `resources/libs/<domain>/` folder
   ([§11](#wizard-code-layout-domain-folders-under-libs)): no compatibility shims, no empty `__init__.py`, build-wide
   values through `CONFIG`.

### Anti‑patterns — DO NOT do these

- ❌ **NEVER revert to monolithic `.zip` payloads** for installs/updates. Add-ons come from the Kodi
  repositories and Kodi installs and updates them. No "download the whole build" path.
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
- ❌ **NEVER extract an add-on zip from the Wizard** (only the two repository add-ons, in `repo_bootstrap.py`, which checks
  their sha256). Everything else goes through Kodi's `InstallAddon`.
- ❌ **NEVER put a third-party `<dir>` into `repository.povil`** (one failing `<dir>` fails the whole repository) and **NEVER add an
  upstream index that carries an id of ours** (the highest version wins): `gen_repo.py` / `check_upstreams.py` fail on both.
- ❌ **NEVER hand‑edit what `gen_repo.py` generates** (`addons.xml`, checksums, the site). Bump the
  `addon.xml` version instead; Kodi only updates to a higher one.
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
- ✅ **Install through `provisioner.install_native` / `provision`** (Kodi's `InstallAddon` with the minimal dependency-dialog
  confirmer); never extract a zip yourself.
- ✅ **Verify before shipping.** `python -m py_compile` (and `pyflakes`) every changed `.py`; run
  `python patches_config.py` after any registry change; validate every JSON/XML; for patchers, simulate the injection
  against the real host source when possible.

---

## 13. Git & Release Workflow

- **Develop on a feature branch**, then fast‑forward `main`. **Only the owner pushes**: agents commit locally and never run
  `git push`. Pushing to `main` triggers `deploy-repo.yml`, which builds the whole repository with `gen_repo.py`, verifies it and
  deploys it to GitHub Pages (no manifest commit-back, no release assets).
- Do **not** open a pull request unless explicitly asked.
- A change is "done" only when: code compiles, configs validate, the version is bumped (Kodi updates only to a higher one),
  CI is green, and `addons.xml` on the site lists the new version(s).

---

*This README reflects the repository architecture: the two Kodi repositories on GitHub Pages, native add-on installs and updates,
the Wizard's repository bootstrap and verified live completion path, config‑pack timing and retention, the Unified Patching Engine, the Profiles feature (§9) and the domain-driven Wizard
layout under `resources/libs/` (§11). Keep it updated when the installation, config, asset, patching or Profiles contracts change — it is the contract
future agents will rely on.*