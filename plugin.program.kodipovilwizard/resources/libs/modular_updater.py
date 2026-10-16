import os
import xbmc
import xbmcgui
import json

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs import db
from resources.libs import config_apply
from resources.libs.headless_installer import HeadlessInstaller, BINARY_PREFIXES


class ModularUpdater:
    def __init__(self, background=False):
        self.background = background
        self.dialog = xbmcgui.Dialog()
        self.progress = xbmcgui.DialogProgressBG() if background else xbmcgui.DialogProgress()
        # MANIFEST_URL is mapped onto CONFIG in config.py -> init_uservars()
        # (and defined in uservar.py). getattr keeps us safe if it is missing.
        self.manifest_url = getattr(CONFIG, 'MANIFEST_URL', 'http://missing_manifest_url')

        # Optional shared ProvisionProgress tracker (resources/libs/provision_progress.py).
        # startup.fresh_build_auto_install_if_needed() sets it for a FRESH install so
        # the top banner and the install window share one monotonic percentage. It is
        # None for ordinary OTA runs. Every call goes through _track(), which can never
        # raise, so a progress problem can never break an install.
        self.provision_progress = None

    def _track(self, method, *args):
        tracker = self.provision_progress
        if tracker is None:
            return
        try:
            getattr(tracker, method)(*args)
        except Exception:
            pass

    def _version_tuple(self, ver):
        """Convert string versions (e.g. '5.12.04', '0.6.20a') into comparable
        numeric tuples.

        NOTE (fix #4): the original split-on-dot logic dropped trailing
        letters, so '0.6.20a' and '0.6.20b' compared equal and a letter-only
        bump (which our build actually uses for script.fentastic.helper) would
        never be detected. We now append the letter as an extra ordinal
        component: '0.6.20' -> (0,6,20), '0.6.20a' -> (0,6,20,1) < (0,6,20,2).
        """
        parts = []
        for chunk in str(ver).split('.'):
            num = ''.join(ch for ch in chunk if ch.isdigit())
            suffix = ''.join(ch for ch in chunk if ch.isalpha())
            parts.append(int(num) if num else 0)
            if suffix:
                parts.append(ord(suffix[0].lower()) - 96)  # a->1, b->2, ...
        return tuple(parts)

    @staticmethod
    def _on_disk(addon_id):
        """True when the addon's folder + addon.xml physically exist under
        special://home/addons -- i.e. it was extracted, regardless of whether
        Kodi currently considers it enabled."""
        try:
            return os.path.exists(os.path.join(CONFIG.ADDONS, addon_id, 'addon.xml'))
        except Exception:
            return False

    @staticmethod
    def _enable_addon(addon_id):
        """Enable an installed addon in the RUNNING Kodi via JSON-RPC.

        Writing enabled=1 straight into Addons??.db (db.addon_database) does NOT
        flip Kodi's in-memory state, so after a raw zip extract an addon can sit
        DISABLED -- System.HasAddon then returns false and heal_missing_addons
        would re-download it on every launch forever (the fishenzon loop). A
        JSON-RPC SetAddonEnabled makes the enable actually stick at runtime."""
        try:
            q = json.dumps({'jsonrpc': '2.0', 'id': 1,
                            'method': 'Addons.SetAddonEnabled',
                            'params': {'addonid': addon_id, 'enabled': True}})
            xbmc.executeJSONRPC(q)
        except Exception as e:
            logging.log("[ModularUpdater] enable {0} failed: {1}".format(addon_id, e),
                        level=xbmc.LOGWARNING)

    def _register_and_enable(self, ids, settle=1.5):
        """Register extracted addons in the Addons DB, make Kodi load them and
        enable each one. Silent: no dialog."""
        ids = list(ids or [])
        if not ids:
            return
        try:
            logging.log("[ModularUpdater] Registering to Addons DB: {0}".format(ids), level=xbmc.LOGINFO)
            db.addon_database(ids, 1, True)
            xbmc.executebuiltin('UpdateLocalAddons')
            # Give Kodi time to unlock the SQLite DB and process the VFS event
            # queue so new Python modules (like certifi) load.
            xbmc.Monitor().waitForAbort(settle)
            for _aid in ids:
                self._enable_addon(_aid)
        except Exception as e:
            logging.log("[ModularUpdater] DB sync error: {0}".format(e), level=xbmc.LOGERROR)

    def get_local_version(self, addon_id):
        """Read the local addon.xml to find the currently installed version.

        Returns None when the addon is NOT installed on this device.
        """
        addon_xml = os.path.join(CONFIG.ADDONS, addon_id, 'addon.xml')
        if not os.path.exists(addon_xml):
            return None
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(addon_xml)
            return tree.getroot().get('version')
        except Exception as e:
            logging.log("[ModularUpdater] Failed to parse local {0} XML: {1}".format(addon_id, e), level=xbmc.LOGERROR)
            return None

    def _already_current(self, mod):
        """True when the local copy is already at (or past) the manifest version."""
        local_ver = self.get_local_version(mod.get('id'))
        remote_ver = mod.get('version')
        if not local_ver or not remote_ver:
            return False
        return self._version_tuple(local_ver) >= self._version_tuple(remote_ver)

    def run_update_check(self):
        """Fetch manifest, diff versions, and execute necessary updates."""
        logging.log("[ModularUpdater] Starting modular update check...", level=xbmc.LOGINFO)

        # 1. Fetch Remote Manifest
        response = tools.open_url(self.manifest_url)
        if not response:
            if not self.background:
                self.dialog.ok(CONFIG.ADDONTITLE, "שגיאה בתקשורת מול שרת העדכונים.")
            return False

        try:
            manifest = json.loads(response.text)
            addons = manifest.get('addons', {})
        except Exception as e:
            logging.log("[ModularUpdater] Manifest parsing failed: {0}".format(e), level=xbmc.LOGERROR)
            return False

        # Keep the parsed manifest so execute_updates can also apply the
        # build-config pack (skin/locale/favourites/sources/...) in the same
        # pass, at the value/id level -- without clobbering the user's keys.
        self._manifest = manifest

        # 2. Compare Versions
        #
        # FIX #2 (safety): only update addons that are ALREADY installed.
        # The manifest lists ALL build addons -- including the on-demand
        # skin.povil.nox (31MB) and the other skins. The original
        # "install if not local_ver" branch would silently force-install
        # every missing skin onto every device on the first check, which
        # fights the build's on-demand-skin design (startup.py only ever
        # refreshes the *active* skin pack). New addons are installed by
        # their own flows (Fresh Install / Switch Skin), never here.
        #
        # If you ever DO want this updater to also install missing addons,
        # set self.install_missing = True before calling run_update_check().
        install_missing = getattr(self, 'install_missing', False)

        update_queue = []
        for addon_id, mod in addons.items():
            remote_ver = mod.get('version')
            local_ver = self.get_local_version(addon_id)

            if not remote_ver:
                continue

            if not local_ver:
                if install_missing:
                    update_queue.append(mod)
                else:
                    logging.log("[ModularUpdater] Skipping not-installed addon {0}".format(addon_id),
                                level=xbmc.LOGINFO)
                continue

            if self._version_tuple(local_ver) < self._version_tuple(remote_ver):
                logging.log("[ModularUpdater] {0}: {1} -> {2}".format(addon_id, local_ver, remote_ver),
                            level=xbmc.LOGINFO)
                update_queue.append(mod)

        # 3. Execute Updates if needed
        #
        # The build-config pack carries its own version, independent of the
        # addons. A user can be up to date on code but behind on config (e.g.
        # a new skin tweak / extra repo source), so we run the update pass when
        # EITHER an addon moved OR the config moved.
        config_pending = self._config_pending(manifest)
        fresh = getattr(self, 'fresh', False)
        if not update_queue and not config_pending and not fresh:
            logging.log("[ModularUpdater] All modules and config are up to date.", level=xbmc.LOGINFO)
            if not self.background:
                self.dialog.ok(CONFIG.ADDONTITLE, "כל ההרחבות מעודכנות לגרסה האחרונה.")
            return True

        # Fresh install ALWAYS falls through to execute_updates even with an
        # empty queue, so provisioning + the .provisioned marker still run on a
        # device that already happens to have every addon (e.g. resuming an
        # interrupted setup whose addons all landed but whose config / marker
        # never did).
        return self.execute_updates(update_queue)

    def run_fresh_install(self):
        """Hydrate a clean device ENTIRELY from the manifest.

        Installs every addon listed in manifest.json and seeds the
        build-config pack in 'fresh' mode -- the modular replacement for the
        legacy monolithic build zip. The caller (startup) pins the build
        settings and performs the single force-close afterwards, so
        execute_updates must NOT restart/reload here (self.fresh guards it).
        """
        self.install_missing = True
        self.fresh = True
        return self.run_update_check()

    # ---- Provisioning state marker -------------------------------------------
    # A persistent file written to userdata ONLY when a fresh install has run all
    # the way to the end (every addon attempted + the config.zip applied).
    # startup.py uses it to tell a COMPLETED setup apart from one the user
    # force-closed mid-provisioning, so an interrupted setup is resumed instead
    # of being left half-built forever.
    @staticmethod
    def provision_marker_path():
        return os.path.join(CONFIG.USERDATA, 'kodipovil.provisioned')

    @classmethod
    def is_provisioned(cls):
        try:
            return os.path.exists(cls.provision_marker_path())
        except Exception:
            return False

    @classmethod
    def mark_provisioned(cls, version=''):
        try:
            with open(cls.provision_marker_path(), 'w') as fh:
                fh.write(str(version or ''))
            logging.log("[Provisioning] wrote .provisioned marker (v{0})".format(version),
                        level=xbmc.LOGINFO)
        except Exception as e:
            logging.log("[Provisioning] failed writing .provisioned marker: {0}".format(e),
                        level=xbmc.LOGERROR)

    @classmethod
    def clear_provisioned(cls):
        try:
            os.remove(cls.provision_marker_path())
        except Exception:
            pass

    def heal_missing_addons(self):
        """Strict OTA enforcement (beyond version bumps).

        Physically verify with System.HasAddon that every REQUIRED addon is
        actually installed, and silently (re)install any that are missing -- e.g.
        a content addon that timed out during a previous provisioning pass
        (YouTube), or a manifest addon that never landed. On-demand skins
        (type 'skin') are intentionally EXCLUDED: by design they are installed
        only via Switch Skin, never force-installed here.

        Everything is installed headlessly (zip download + extract + DB register);
        no per-addon dialog and no native InstallAddon.

        Reuses self._manifest if run_update_check already fetched it this run.
        """
        manifest = getattr(self, '_manifest', None)
        if not manifest:
            response = tools.open_url(self.manifest_url)
            if not response:
                return
            try:
                manifest = json.loads(response.text)
                self._manifest = manifest
            except Exception as e:
                logging.log("[OTA-Heal] manifest parse failed: {0}".format(e), level=xbmc.LOGERROR)
                return
        addons = manifest.get('addons', {})

        # 1. Manifest addons (our private addons + the third-party repos),
        #    excluding on-demand skins.
        #
        #    CRUCIAL: distinguish "absent from disk" (re-download) from "on disk
        #    but not enabled". System.HasAddon is false in BOTH cases, but an
        #    addon that is already extracted must NOT be re-downloaded -- doing so
        #    is exactly the fishenzon infinite loop (extract -> still disabled ->
        #    System.HasAddon false -> re-download -> ...). For on-disk-but-disabled
        #    we just (re)enable it via JSON-RPC; only genuinely absent addons are
        #    pulled through the modular pipeline.
        missing = []
        disabled_on_disk = []
        for addon_id, mod in addons.items():
            if mod.get('type') == 'skin':
                continue
            if xbmc.getCondVisibility('System.HasAddon({0})'.format(addon_id)):
                continue
            if self._on_disk(addon_id):
                disabled_on_disk.append(addon_id)
            else:
                missing.append(mod)
        if disabled_on_disk:
            logging.log("[OTA-Heal] On-disk but disabled -> enabling (NOT re-downloading): "
                        "{0}".format(disabled_on_disk), level=xbmc.LOGWARNING)
            for _aid in disabled_on_disk:
                self._enable_addon(_aid)
            try:
                xbmc.executebuiltin('UpdateLocalAddons')
            except Exception:
                pass
        if missing:
            logging.log("[OTA-Heal] Missing manifest addons -> installing: {0}".format(
                [m.get('id') for m in missing]), level=xbmc.LOGWARNING)
            try:
                self.execute_updates(missing)
            except Exception as e:
                logging.log("[OTA-Heal] manifest heal failed: {0}".format(e), level=xbmc.LOGERROR)

        # 2. Third-party CONTENT addons (provisioned headlessly from the repos
        #    already on disk). post_install_provisioning is idempotent -- present
        #    addons are skipped -- so this re-attempts ONLY what is genuinely missing.
        missing_provision = [a for a in self.PROVISION_IDS
                             if not xbmc.getCondVisibility('System.HasAddon({0})'.format(a))]
        if missing_provision:
            logging.log("[OTA-Heal] Missing content addons -> provisioning: {0}".format(
                missing_provision), level=xbmc.LOGWARNING)
            try:
                self.post_install_provisioning()
            except Exception as e:
                logging.log("[OTA-Heal] provisioning heal failed: {0}".format(e), level=xbmc.LOGERROR)

    # Third-party content addons are NOT vendored in our manifest. The manifest
    # ships only our private addons + the third-party repositories; the content
    # addons themselves are resolved from those repositories and installed
    # HEADLESSLY (zip download + extract + DB register) so that (a) the whole
    # flow stays inside one window / one banner with no Kodi dialogs, (b) their
    # dependency trees (script.module.*, context.otaku ...) are pulled in
    # automatically, and (c) they keep receiving OTA updates straight from their
    # own developers. Only platform/binary dependencies (inputstream.*, pvr.* ...)
    # that are genuinely absent from the device go through Kodi's native
    # InstallAddon, and only after the custom GUI has closed.
    #
    # Order matters: install the lean, reliable core content addons FIRST so the
    # build is operational fast, and keep plugin.video.otaku DEAD LAST. Otaku
    # pulls a large dependency tree and is known to spike background syncs / stall
    # for up to ~60s on a fresh install; placing it last means a transient Otaku
    # timeout can never block the core addons (POV, IdanPlus, YouTube, language
    # pack) -- heal_missing_addons() simply (re)installs Otaku on the next launch.
    PROVISION_IDS = [
        'script.module.acctmgr',            # <- repository.709 (Account Manager)
        'plugin.video.pov',                 # <- repository.kodifitzwell (+ patched at runtime)
        'plugin.video.idanplus',            # <- repository.Fishenzon
        'plugin.video.youtube',             # <- Kodi official repo
        'resource.language.he_il',          # <- Kodi official repo (Hebrew language pack)
        'script.xbmc.unpausejumpback',      # <- Kodi official repo (unpause jumpback)
        'plugin.video.otaku',               # <- repository.otaku (+ context.otaku); LAST: heavy
                                            #    + timeout-prone, must not block core install.
    ]

    def post_install_provisioning(self, per_addon_timeout=60, ids=None):
        """Headless content provisioning WITHOUT the install window (used by the
        startup self-heal pass).

        Workflow:
          1. UpdateLocalAddons so the repositories on disk are visible.
          2. HeadlessInstaller resolves the dependency tree and silently downloads,
             extracts, registers and enables everything (no dialog of any kind).
          3. Only BINARY addons (inputstream.*, pvr.* ...) that are genuinely absent
             are handed to Kodi's native InstallAddon.
          4. Non-binary addons that could not be resolved are NOT installed
             natively; the next launch's self-heal retries them.

        Idempotent: addons already present are skipped.
        """
        provision_ids = list(ids) if ids is not None else list(self.PROVISION_IDS)

        logging.log("[Provisioning] HEADLESS provisioning of {0} addons".format(
            len(provision_ids)), level=xbmc.LOGINFO)

        # The repositories were just extracted to disk. Force Kodi to load them so
        # their addon.xml (datadir) is visible to the headless resolver.
        try:
            xbmc.executebuiltin('UpdateLocalAddons')
            if xbmc.Monitor().waitForAbort(2):
                return False
        except Exception:
            pass

        hi = None
        installed, missing = [], list(provision_ids)
        try:
            hi = HeadlessInstaller()
            installed, missing = hi.install(provision_ids)
            logging.log("[Provisioning] headless installed={0} missing={1}".format(
                installed, missing), level=xbmc.LOGINFO)
        except Exception as e:
            logging.log("[Provisioning] headless installer error: {0}".format(e),
                        level=xbmc.LOGERROR)
            missing = [a for a in provision_ids
                       if not xbmc.getCondVisibility('System.HasAddon({0})'.format(a))]

        native = sorted(hi.binary_missing) if hi is not None else []
        if native and not xbmc.Monitor().abortRequested():
            logging.log("[Provisioning] native fallback (binary addons only): {0}".format(native),
                        level=xbmc.LOGWARNING)
            self._native_install_fallback(native, per_addon_timeout)
            for _aid in installed:
                self._enable_addon(_aid)

        deferred = [m for m in missing if m not in native]
        if deferred:
            logging.log("[Provisioning] not provisioned this pass (retried next launch): {0}".format(
                deferred), level=xbmc.LOGWARNING)

        logging.log("[Provisioning] Provisioning finished", level=xbmc.LOGINFO)

        all_present = all(
            xbmc.getCondVisibility('System.HasAddon({0})'.format(a)) for a in provision_ids
        )
        return all_present

    def _native_install_fallback(self, ids, per_addon_timeout=60):
        """Last-resort native install, RESERVED for binary/platform addons
        (inputstream.*, pvr.* ...) that the headless engine must never extract.
        Anything that is not a binary addon is refused here and left to the next
        launch's headless self-heal.

        Uses Kodi's own InstallAddon so Kodi picks the platform-correct build,
        with a MINIMAL confirmer thread that auto-accepts ONLY the dependency
        Yes/No dialog (window 10100, control 11 = Yes -- fixed by Kodi core
        regardless of skin). It NEVER closes any other dialog.
        """
        import threading
        import time

        refused = [a for a in ids if not a.startswith(BINARY_PREFIXES)]
        ids = [a for a in ids if a.startswith(BINARY_PREFIXES)]
        if refused:
            logging.log("[Provisioning] native install refused for non-binary addons "
                        "(headless self-heal will retry): {0}".format(refused),
                        level=xbmc.LOGWARNING)
        if not ids:
            return

        monitor = xbmc.Monitor()

        # Make sure repo indexes are fresh so InstallAddon can find the addons.
        try:
            xbmc.executebuiltin('UpdateAddonRepos', True)
        except Exception:
            pass
        if monitor.waitForAbort(2):
            return

        stop = threading.Event()

        def _confirmer():
            while not stop.is_set():
                try:
                    if xbmcgui.getCurrentWindowDialogId() == 10100:
                        xbmc.executebuiltin('SendClick(11)')  # Yes / OK
                        if monitor.waitForAbort(0.8):
                            return
                        continue
                except Exception:
                    pass
                if monitor.waitForAbort(0.3):
                    return

        watcher = threading.Thread(target=_confirmer)
        watcher.daemon = True
        watcher.start()
        try:
            for addon_id in ids:
                if monitor.abortRequested():
                    break
                if xbmc.getCondVisibility('System.HasAddon({0})'.format(addon_id)):
                    continue
                logging.log("[Provisioning] native install {0}".format(addon_id),
                            level=xbmc.LOGINFO)
                xbmc.executebuiltin('InstallAddon({0})'.format(addon_id))
                deadline = time.time() + int(per_addon_timeout)
                landed = False
                while time.time() < deadline:
                    if monitor.waitForAbort(1):
                        break
                    if xbmc.getCondVisibility('System.HasAddon({0})'.format(addon_id)):
                        landed = True
                        break
                if not landed:
                    logging.log("[Provisioning] native install did not confirm {0} "
                                "within {1}s (will self-heal next launch)".format(
                                    addon_id, per_addon_timeout), level=xbmc.LOGWARNING)
        finally:
            stop.set()
            watcher.join(5)

    def _config_pending(self, manifest):
        """True when the manifest's config version is ahead of what we last
        applied on this device."""
        cfg = (manifest or {}).get('config') or {}
        remote = cfg.get('config_version')
        if not remote:
            return False
        return CONFIG.get_setting('config_applied_version') != remote

    # ---- install pipelines ---------------------------------------------------
    def _make_ui_orchestrator(self, queue, fresh):
        """Build the orchestrator that drives the custom install window:
        Phase 1 (manifest queue) -> DB/VFS sync -> resolver pause -> Phase 2
        (headless-resolved content jobs injected into the SAME window)."""
        def _orchestrator(dialog):
            if queue:
                dialog.append_to_queue(queue, phase='manifest')
                dialog.wait_for_queue_empty()
            self._track('complete', 'manifest')

            if fresh:
                # Bridge Phase 1 -> Phase 2 DB Registration so Phase 2 logic can "see" the extracted Phase 1 modules
                extracted_so_far = dialog.get_installed()
                if extracted_so_far:
                    logging.log("[ModularUpdater] Phase 1 queue complete. Synchronizing DB & VFS...", level=xbmc.LOGINFO)
                    # CRITICAL: give Kodi 2.5 seconds to unlock the SQLite DB
                    # and process the VFS event queue so new Python modules (like certifi) load.
                    self._register_and_enable(extracted_so_far, settle=2.5)

                self._track('begin_indeterminate', 'resolve')
                dialog.pause_for_resolution()

                try:
                    logging.log("[ModularUpdater] Triggering Phase 2 Headless Resolution...", level=xbmc.LOGINFO)
                    hi = HeadlessInstaller()
                    phase2_jobs, missing_native = hi.resolve_and_prepare(self.PROVISION_IDS)

                    dialog.remove_resolution_pause()
                    self._track('complete', 'resolve')
                    if phase2_jobs:
                        logging.log("[ModularUpdater] Injecting {0} Phase 2 jobs to UI.".format(len(phase2_jobs)), level=xbmc.LOGINFO)
                        dialog.append_to_queue(phase2_jobs, phase='content')
                    else:
                        self._track('complete', 'content')
                    self._missing_native = missing_native
                except Exception as e:
                    logging.log("[ModularUpdater] Phase 2 resolution fatal error: {0}".format(e), level=xbmc.LOGERROR)
                    dialog.remove_resolution_pause()
                    self._track('complete', 'resolve')
                    self._track('complete', 'content')

            dialog.mark_all_jobs_added()
        return _orchestrator

    def _install_silently(self, queue, fresh):
        """Dialog-free pipeline used whenever the custom window is not used
        (background OTA / self-heal) or could not load. Same engine as the
        window (HeadlessInstaller.install_jobs -> download_zip + extract_zip):
        NO Downloader / extract.all / native dialogs.

        Foreground progress goes to the shared banner tracker; a background
        OTA run uses ONE in-place DialogProgressBG (not one per addon)."""
        hi = HeadlessInstaller()
        extracted = []

        bg = None
        if self.background and self.provision_progress is None:
            try:
                self.progress.create(CONFIG.ADDONTITLE, "מבצע עדכון רקע מודולרי...")
                bg = self.progress
            except Exception:
                bg = None

        def _cb(phase):
            def _inner(frac, label):
                self._track('set_fraction', phase, frac)
                if bg is not None:
                    try:
                        bg.update(int(frac * 100), message="{0}".format(tools.clean_text(label)))
                    except Exception:
                        pass
            return _inner

        try:
            # Phase 1: manifest addons (skip anything already at the manifest
            # version, e.g. after a window run that died half-way).
            jobs = [m for m in queue if not self._already_current(m)]
            extracted += hi.install_jobs(jobs, on_progress=_cb('manifest'))
            self._track('complete', 'manifest')

            if fresh:
                # Phase 1 -> Phase 2 bridge: the resolver must be able to see the
                # repositories that were just extracted.
                if extracted:
                    self._register_and_enable(extracted, settle=2.5)

                self._track('begin_indeterminate', 'resolve')
                try:
                    logging.log("[ModularUpdater] Silent Phase 2 headless resolution...", level=xbmc.LOGINFO)
                    phase2_jobs, missing_native = hi.resolve_and_prepare(self.PROVISION_IDS)
                    self._missing_native = missing_native
                except Exception as e:
                    logging.log("[ModularUpdater] Phase 2 resolution fatal error: {0}".format(e), level=xbmc.LOGERROR)
                    phase2_jobs = []
                self._track('complete', 'resolve')

                if phase2_jobs:
                    extracted += hi.install_jobs(phase2_jobs, on_progress=_cb('content'))
                self._track('complete', 'content')
        finally:
            if bg is not None:
                try:
                    bg.close()
                except Exception:
                    pass
        return extracted

    def execute_updates(self, queue):
        """Download, extract, and register the queued modules."""
        tools.ensure_folders(CONFIG.PACKAGES)

        requires_restart = False
        extracted_addons = []
        self._missing_native = []

        fresh = getattr(self, 'fresh', False)
        queue = list(queue or [])
        ui_handled = False

        # Path A -- the custom single-window UI (foreground only).
        if (queue or fresh) and not self.background:
            try:
                from resources.libs.gui.install_manager import run_install_manager
                extracted_addons = list(run_install_manager(
                    orchestrator_func=self._make_ui_orchestrator(queue, fresh),
                    progress=self.provision_progress) or [])
                ui_handled = True
                self._track('complete', 'content')
            except Exception as _ui_err:
                logging.log("[ModularUpdater] install-manager UI failed ({0}); "
                            "continuing with the dialog-free headless pipeline".format(_ui_err),
                            level=xbmc.LOGWARNING)
                self._missing_native = []

        # Path B -- no window (background OTA / self-heal) or the window could not
        # load. Same headless engine, still no per-addon dialogs.
        if not ui_handled and (queue or fresh):
            extracted_addons = self._install_silently(queue, fresh)

        if 'plugin.program.kodipovilwizard' in extracted_addons:
            requires_restart = True

        # 4. Database Registration for all extracted addons
        self._register_and_enable(extracted_addons)

        # A resumed fresh install can find content addons already extracted but
        # still disabled (the resolver rightly does not re-download those). Just
        # enable them -- never re-download, never natively install.
        if fresh:
            for _aid in self.PROVISION_IDS:
                if self._on_disk(_aid) and not xbmc.getCondVisibility('System.HasAddon({0})'.format(_aid)):
                    self._enable_addon(_aid)

        # 4a. Native fallback for BINARY/platform addons only (inputstream.*, pvr.* ...),
        #     strictly AFTER the custom window has closed. With a banner tracker active
        #     no second background dialog is opened.
        native_ids = [a for a in (getattr(self, '_missing_native', None) or [])
                      if a.startswith(BINARY_PREFIXES)]
        if native_ids and not xbmc.Monitor().abortRequested():
            self._track('begin_indeterminate', 'native')
            bg = None
            try:
                if self.provision_progress is None:
                    bg = xbmcgui.DialogProgressBG()
                    bg.create(CONFIG.ADDONTITLE, "משלים התקנות מערכת (Native)...")
                self._native_install_fallback(native_ids, per_addon_timeout=60)
            except Exception as e:
                logging.log("[ModularUpdater] Native fallback failed: {0}".format(e), level=xbmc.LOGERROR)
            finally:
                if bg is not None:
                    try:
                        bg.close()
                    except Exception:
                        pass
            # Binary dependencies now exist -> re-evaluate the addons that import them.
            for _aid in extracted_addons:
                self._enable_addon(_aid)
        self._track('complete', 'native')

        # 4b/4c. Build-config pack Phase
        def _apply_config_pack():
            try:
                manifest = getattr(self, '_manifest', None)
                if manifest:
                    kwargs = {}
                    # Hand over the shared tracker so the config phase reports EXACT
                    # progress. Only when the installed config_apply understands the
                    # argument -- a mismatched pair of files must never make the
                    # config silently fail to apply.
                    if self.provision_progress is not None:
                        try:
                            import inspect
                            if 'progress' in inspect.signature(config_apply.apply_config_pack).parameters:
                                kwargs['progress'] = self.provision_progress
                        except Exception:
                            pass
                    res = config_apply.apply_config_pack(
                        manifest, fresh=fresh, background=self.background, **kwargs)
                    return bool(res.get('skin_touched'))
            except Exception as e:
                logging.log("[ModularUpdater] config apply failed: {0}".format(e), level=xbmc.LOGERROR)
            return False

        self._track('begin_indeterminate', 'config')
        config_skin_touched = _apply_config_pack()
        self._track('complete', 'config')

        if fresh:
            if not xbmc.Monitor().abortRequested():
                cfg = (getattr(self, '_manifest', None) or {}).get('config') or {}
                self.mark_provisioned(cfg.get('config_version') or CONFIG.BUILDVERSION_DEFAULT)
            return True

        if requires_restart:
            if not self.background:
                choice = self.dialog.yesno(
                    CONFIG.ADDONTITLE,
                    "עדכון קריטי בוצע בהצלחה.\nיש להפעיל מחדש את קודי כדי להחיל את השינויים.",
                    yeslabel="יציאה",
                    nolabel="מאוחר יותר"
                )
                if choice:
                    from resources.libs.wizard import Wizard
                    Wizard().force_close_kodi_in_5_seconds("עדכון קריטי הסתיים.")
        elif extracted_addons or config_skin_touched:
            xbmc.executebuiltin('ReloadSkin()')
            if not self.background:
                self.dialog.ok(CONFIG.ADDONTITLE, "העדכון המודולרי הסתיים בהצלחה!")

        return True