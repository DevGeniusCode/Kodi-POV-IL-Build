# -*- coding: utf-8 -*-
"""Headless resolver and installer for third-party CONTENT addons.

Install it at: resources/libs/headless_installer.py

WHY THIS EXISTS
---------------
Traditionally, installing 3rd-party addons (POV, YouTube, Otaku) relied on Kodi's native
`InstallAddon(id)`. That triggers intrusive Kodi dialogs (dependency confirmations, progress
bars, first-run popups) which required aggressive, buggy watchdogs to suppress.

This module bypasses native Kodi dialogs entirely by resolving the dependency tree from
locally installed repositories (kodifitzwell, xbmc.org, etc.), downloading the zips directly,
and extracting them into special://home/addons.

THE ENGINE (single source of truth)
-----------------------------------
`download_zip()` and `extract_zip()` are the ONLY download / extract primitives used by the
build setup. They open no dialog of their own and report plain numbers through callbacks:

  * install_manager (the custom window) imports them for its parallel-download /
    sequential-extract workers.
  * `HeadlessInstaller.install_jobs()` uses them for every NO-WINDOW path (the fallback when
    the window cannot load, the background OTA update, the self-heal pass). Those paths
    therefore never spawn a per-addon Downloader / extract.all dialog either.

NATIVE `InstallAddon` IS RESERVED FOR BINARY ADDONS
---------------------------------------------------
Binary / platform-specific addons (`inputstream.*`, `pvr.*`, `vfs.*` ...) must match the
device's OS+arch+ABI, so we never extract a zip for them. A missing binary dependency does
NOT block the pure-python addons that import it: those are still installed headlessly, and
the binary ids are returned separately (`binary_missing`) for the post-GUI native fallback.
Everything else that cannot be resolved (not in any repo index, or a non-binary dependency
failed) is DEFERRED to the next launch's self-heal -- it is never installed natively.
"""
import gzip
import json
import os
import re
import zipfile

try:
    import xbmc
except Exception:
    xbmc = None

try:
    import xbmcvfs
except Exception:
    xbmcvfs = None

from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs.common.config import CONFIG

try:
    from resources.libs import db
except Exception:
    db = None


_LOGINFO = xbmc.LOGINFO if xbmc else 1
_LOGWARNING = xbmc.LOGWARNING if xbmc else 2
_LOGERROR = xbmc.LOGERROR if xbmc else 3


# Dependencies Kodi provides itself (the python runtime, gui bindings, resources
# virtual packages...). Never something we download.
VIRTUAL_PREFIXES = ('xbmc.', 'kodi.')

# Binary / platform-specific addons. A repo's addons.xml lists one version per
# Kodi codename, but the actual binary must match the device's OS+arch+ABI, so we
# must NEVER blindly extract a zip for these -- we'd risk planting a binary built
# for the wrong platform. They are almost always bundled with Kodi already; if one
# is genuinely missing it is returned in `binary_missing` so Kodi can install the
# platform-correct build itself (native InstallAddon, after the custom GUI closed).
BINARY_PREFIXES = ('inputstream.', 'peripheral.', 'vfs.', 'audioencoder.',
                   'audiodecoder.', 'imagedecoder.', 'pvr.', 'game.',
                   'screensaver.', 'visualization.')

_ADDON_RE = re.compile(r'<addon\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</addon>', re.DOTALL)
_VER_RE = re.compile(r'\bversion="([^"]+)"')
_IMPORT_RE = re.compile(r'<import\b[^>]*\baddon="([^"]+)"')
_INFO_RE = re.compile(r'<info\b[^>]*>([^<]+)</info>')
_DATADIR_RE = re.compile(r'<datadir\b[^>]*>([^<]+)</datadir>')


def _log(msg, level=None):
    try:
        logging.log('[HeadlessInstaller] ' + msg,
                    level=level if level is not None else _LOGINFO)
    except Exception:
        pass


def _version_tuple(v):
    """Loose, never-throwing version compare key (digits only, dotted)."""
    parts = []
    for chunk in re.split(r'[._-]', str(v or '')):
        m = re.match(r'\d+', chunk)
        parts.append(int(m.group(0)) if m else 0)
    return tuple(parts)


# --------------------------------------------------------------------------- #
#  Dialog-free download / extract primitives (shared with install_manager).    #
# --------------------------------------------------------------------------- #
def download_zip(url, dest, on_progress=None, should_abort=None):
    """Stream a url to dest, reporting integer percent via on_progress(pct).
    Returns True on a non-empty file. Opens NO dialog."""
    on_progress = on_progress or (lambda pct: None)
    should_abort = should_abort or (lambda: False)
    response = None
    try:
        folder = os.path.split(dest)[0]
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
        response = tools.open_url(url, stream=True)
        if not response:
            return False
        total = response.headers.get('content-length')
        with open(dest, 'wb') as f:
            if total is None:
                f.write(response.content)
                on_progress(100)
            else:
                total = int(total)
                downloaded = 0
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if should_abort():
                        return False
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    on_progress(min(100, int(downloaded * 100 / total)) if total else 0)
        return os.path.exists(dest) and os.path.getsize(dest) > 0
    except Exception as e:
        _log("download error for {0}: {1}".format(url, e), level=_LOGERROR)
        return False
    finally:
        try:
            if response is not None and hasattr(response, 'close'):
                response.close()
        except Exception:
            pass


def extract_zip(zip_path, dest_dir, on_progress=None):
    """Extract a single-addon zip into dest_dir reporting (current, total) files.
    Single-addon zips are rooted at the addon id and contain no userdata, so a
    plain extract (no KEEP* build logic, no competing dialog) is correct."""
    on_progress = on_progress or (lambda cur, tot: None)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            members = zf.namelist()
            total = len(members) or 1
            for i, member in enumerate(members, 1):
                zf.extract(member, dest_dir)
                on_progress(i, total)
        return True
    except Exception as e:
        _log("extract error for {0}: {1}".format(zip_path, e), level=_LOGERROR)
        return False


def _sha256_matches(path, want):
    """True when the file's sha256 equals `want`. Any failure counts as a mismatch."""
    try:
        from resources.libs import config_apply
        got = config_apply.sha256_file(path)
        return str(got).lower() == str(want).lower()
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Pure parsing helpers (no Kodi calls -- unit-testable).                      #
# --------------------------------------------------------------------------- #
def _parse_repo_extension(addon_xml_text):
    """From a repository addon's addon.xml, return [(info_url, datadir), ...].

    A repo can declare several <dir> blocks (one per Kodi codename); we pair the
    Nth <info> with the Nth <datadir> positionally, falling back to the last
    datadir when counts differ."""
    infos = [s.strip() for s in _INFO_RE.findall(addon_xml_text)]
    datadirs = [s.strip() for s in _DATADIR_RE.findall(addon_xml_text)]
    out = []
    for i, info in enumerate(infos):
        if i < len(datadirs):
            dd = datadirs[i]
        elif datadirs:
            dd = datadirs[-1]
        else:
            dd = ''
        out.append((info, dd))
    return out


def _parse_addons_xml(text):
    """Parse an addons.xml document into {id: {'version':, 'requires':[...]}}.

    Keeps the highest version when an id appears more than once."""
    index = {}
    for m in _ADDON_RE.finditer(text or ''):
        aid, body = m.group(1), m.group(2)
        head = text[m.start():m.start(2)]
        vm = _VER_RE.search(head)
        version = vm.group(1) if vm else '0'
        requires = [d for d in _IMPORT_RE.findall(body)
                    if not d.startswith(VIRTUAL_PREFIXES)]
        prev = index.get(aid)
        if prev and _version_tuple(prev['version']) >= _version_tuple(version):
            continue
        index[aid] = {'version': version, 'requires': requires}
    return index


def _zip_url(datadir, addon_id, version):
    """Standard Kodi repo layout: <datadir>/<id>/<id>-<version>.zip."""
    base = (datadir or '').rstrip('/')
    return '{0}/{1}/{1}-{2}.zip'.format(base, addon_id, version)


# --------------------------------------------------------------------------- #
#  Installer (Kodi-side).                                                       #
# --------------------------------------------------------------------------- #
class HeadlessInstaller:
    def __init__(self):
        self.index = {}             # id -> {'version', 'requires', 'datadir'}
        self.unresolved = set()     # NON-binary ids we could not resolve -> deferred to self-heal
        self.binary_missing = set() # binary ids not on the device -> post-GUI native fallback
        self._indexed_repos = set()
        self._loaded = False

    # ---- repo discovery + index -------------------------------------------- #
    def _addon_roots(self):
        roots = []
        for sp in ('special://home/addons', 'special://xbmc/addons'):
            try:
                p = xbmcvfs.translatePath(sp) if xbmcvfs else sp
            except Exception:
                p = None
            if p and os.path.isdir(p) and p not in roots:
                roots.append(p)
        return roots

    def _iter_installed_repos(self):
        """Yield (repo_name, [(info_url, datadir), ...]) for every repository
        addon on disk. The pair list preserves the order declared in addon.xml
        (Kodi lists newest codename first), so callers can stop after the first
        pair that yields a usable index instead of fetching every codename."""
        seen = set()
        for root in self._addon_roots():
            try:
                names = os.listdir(root)
            except Exception:
                continue
            for name in names:
                if not name.startswith('repository.') or name in seen:
                    continue
                axml = os.path.join(root, name, 'addon.xml')
                if not os.path.isfile(axml):
                    continue
                try:
                    with open(axml, 'r', encoding='utf-8', errors='replace') as fh:
                        txt = fh.read()
                except Exception:
                    continue
                if 'xbmc.addon.repository' not in txt:
                    continue
                pairs = [(i, d) for i, d in _parse_repo_extension(txt) if i and d]
                if pairs:
                    seen.add(name)
                    yield name, pairs

    def _fetch_text(self, url):
        """Robust fetcher: Tries standard requests, falls back to Kodi C++ VFS on SSL/Race issues."""
        import time
        data = None

        for attempt in range(2):
            try:
                r = tools.open_url(url)
                if r and r.content:
                    data = r.content
                    break
            except Exception as e:
                _log('Python fetch error {0} on {1}: {2}'.format(attempt+1, url, e), level=_LOGWARNING)

            # SSL/VFS Race Condition Fix: Fallback to Kodi's native C++ VFS engine
            try:
                _log('Falling back to xbmcvfs.File C++ engine for {0}'.format(url), level=_LOGINFO)
                f = xbmcvfs.File(url)
                read_bytes = f.readBytes()
                f.close()
                if read_bytes:
                    data = bytearray(read_bytes)
                    break
            except Exception as e:
                _log('xbmcvfs fetch failed: {0}'.format(e), level=_LOGWARNING)

            time.sleep(1)

        if not data:
            _log('Completely failed to fetch: {0}'.format(url), level=_LOGERROR)
            return None

        if data[:2] == b'\x1f\x8b' or url.endswith('.gz'):
            try:
                data = gzip.decompress(data)
            except Exception as e:
                _log('gunzip failed {0}: {1}'.format(url, e), level=_LOGERROR)
                return None
        try:
            return data.decode('utf-8', 'replace')
        except Exception:
            return None

    def load_index(self, attempts=2):
        """Build the union index from every installed repo's addons.xml.

        A repo whose index could not be fetched is retried once more (a fresh
        install can race the network / SSL stack). Repos already indexed are
        not fetched again. If a repo still fails, its addons simply stay out
        of the index and are deferred to the next launch's self-heal -- they
        are NOT handed to Kodi's native installer."""
        if self._loaded:
            return
        monitor = xbmc.Monitor() if xbmc else None
        for attempt in range(max(1, attempts)):
            discovered = set()
            for repo_name, pairs in self._iter_installed_repos():
                discovered.add(repo_name)
                if repo_name in self._indexed_repos:
                    continue
                # Stop at the first pair (codename) that yields a usable index, so the
                # big official repo costs ONE addons.xml(.gz) fetch, not one per
                # codename.
                for info_url, datadir in pairs:
                    text = self._fetch_text(info_url)
                    if not text:
                        _log('repo {0}: could not load {1}'.format(repo_name, info_url), level=_LOGERROR)
                        continue
                    sub = _parse_addons_xml(text)
                    if not sub:
                        continue
                    added = 0
                    for aid, meta in sub.items():
                        prev = self.index.get(aid)
                        if prev and _version_tuple(prev['version']) >= _version_tuple(meta['version']):
                            continue
                        self.index[aid] = {'version': meta['version'],
                                           'requires': meta['requires'],
                                           'datadir': datadir}
                        added += 1
                    _log('repo {0}: indexed {1} addons from {2}'.format(repo_name, added, info_url))
                    self._indexed_repos.add(repo_name)
                    break
            if discovered <= self._indexed_repos:
                break
            if attempt + 1 < attempts:
                _log('index incomplete ({0} of {1} repos); retrying'.format(
                    len(self._indexed_repos & discovered), len(discovered)), level=_LOGWARNING)
                if monitor is not None and monitor.waitForAbort(3):
                    break
        self._loaded = True
        _log('union index built: {0} addons across all repos'.format(len(self.index)))

    # ---- presence checks ---------------------------------------------------- #
    def _installed(self, addon_id):
        """Kodi currently reports the addon as present AND enabled."""
        if xbmc is None:
            return False
        try:
            return bool(xbmc.getCondVisibility('System.HasAddon({0})'.format(addon_id)))
        except Exception:
            return False

    def _on_disk(self, addon_id):
        """The addon's folder + addon.xml physically exist (enabled or not)."""
        for root in self._addon_roots():
            if os.path.isfile(os.path.join(root, addon_id, 'addon.xml')):
                return True
        return False

    def _present(self, addon_id):
        """Present for resolution purposes: enabled in Kodi OR extracted on disk.
        An addon extracted a moment ago (not yet enabled / not yet seen by
        Kodi's in-memory state) must never be queued for download again."""
        return self._installed(addon_id) or self._on_disk(addon_id)

    # ---- resolution -------------------------------------------------------- #
    def _resolve(self, wanted):
        """Return a deps-first install order containing only fully-resolvable,
        not-yet-present addons.

        * Missing BINARY addons are recorded in self.binary_missing and do NOT
          block their dependents (the dependents are plain python and are still
          installed headlessly).
        * Anything else that cannot be resolved is recorded in self.unresolved
          (deferred to the next launch's self-heal)."""
        order, order_set = [], set()
        cache, visiting = {}, set()

        def visit(aid):
            if aid.startswith(VIRTUAL_PREFIXES):
                return True
            if self._present(aid):
                return True
            if aid.startswith(BINARY_PREFIXES):
                # Not on the device + binary -> never extract it ourselves.
                # Remember it for the native fallback and let dependents proceed.
                self.binary_missing.add(aid)
                return True
            if aid in cache:
                return cache[aid]
            if aid in visiting:          # dependency cycle -> assume satisfiable
                return True
            meta = self.index.get(aid)
            if not meta:
                self.unresolved.add(aid)
                cache[aid] = False
                return False
            visiting.add(aid)
            ok = all([visit(dep) for dep in meta['requires']])
            visiting.discard(aid)
            cache[aid] = ok
            if ok and aid not in order_set:
                order.append(aid)
                order_set.add(aid)
            elif not ok:
                _log('Dependency resolution failed for {0}; deferred to self-heal'.format(aid),
                     level=_LOGWARNING)
                self.unresolved.add(aid)

            return ok

        for w in wanted:
            visit(w)
        return order

    def _get_type(self, aid):
        if aid.startswith('script.module.'): return 'module'
        if aid.startswith('resource.language.'): return 'language'
        if aid.startswith('plugin.video.'): return 'plugin'
        if aid.startswith('plugin.audio.'): return 'plugin'
        if aid.startswith('plugin.program.'): return 'plugin'
        if aid.startswith('skin.'): return 'skin'
        if aid.startswith('repository.'): return 'repository'
        return 'addon'

    def _job_for(self, aid):
        meta = self.index[aid]
        return {
            'id': aid,
            'name': aid,
            'version': meta['version'],
            'zip': _zip_url(meta['datadir'], aid, meta['version']),
            'type': self._get_type(aid),
            'requires': list(meta['requires']),
        }

    # ---- registration ------------------------------------------------------ #
    @staticmethod
    def enable_addon(addon_id):
        """Enable an addon in the RUNNING Kodi via JSON-RPC (a sqlite write alone
        does not flip Kodi's in-memory state)."""
        if xbmc is None:
            return
        try:
            q = json.dumps({'jsonrpc': '2.0', 'id': 1,
                            'method': 'Addons.SetAddonEnabled',
                            'params': {'addonid': addon_id, 'enabled': True}})
            xbmc.executeJSONRPC(q)
        except Exception:
            pass

    def register_and_enable(self, ids):
        """Register extracted addons in Addons??.db, make Kodi load them, and
        enable each one. Silent (no dialog)."""
        ids = sorted(set(ids or []))
        if not ids:
            return
        if db is not None:
            try:
                db.addon_database(ids, 1, True)
            except Exception as e:
                _log('addon DB register failed: {0}'.format(e), level=_LOGERROR)
        if xbmc is not None:
            try:
                xbmc.executebuiltin('UpdateLocalAddons')
                xbmc.Monitor().waitForAbort(1.5)
            except Exception:
                pass
            for aid in ids:
                self.enable_addon(aid)

    # ---- silent download + extract ---------------------------------------- #
    def install_jobs(self, jobs, on_progress=None, should_abort=None):
        """Silently download + extract `jobs` (deps-first order) into
        special://home/addons. NO dialog of any kind.

        on_progress(fraction_0_to_1, label) is called as work advances. A job
        carrying a 'requires' list is skipped when one of its non-virtual,
        non-binary dependencies is not in place. Returns the ids extracted
        (the caller registers/enables them)."""
        on_progress = on_progress or (lambda frac, label: None)
        monitor = xbmc.Monitor() if xbmc else None
        if should_abort is None:
            should_abort = (lambda: bool(monitor and monitor.abortRequested()))
        try:
            tools.ensure_folders(CONFIG.PACKAGES)
        except Exception:
            pass

        jobs = [j for j in (jobs or []) if j.get('id') and j.get('zip')]
        total = len(jobs)
        installed = []
        for i, job in enumerate(jobs):
            if should_abort():
                break
            aid = job['id']
            label = str(job.get('name') or aid)

            blocked = [d for d in (job.get('requires') or [])
                       if not (d.startswith(VIRTUAL_PREFIXES) or d.startswith(BINARY_PREFIXES)
                               or d in installed or self._present(d))]
            if blocked:
                _log('deferring {0} -- dependency not in place: {1}'.format(aid, blocked),
                     level=_LOGWARNING)
                on_progress((i + 1.0) / total, label)
                continue

            def report(sub, _i=i, _label=label):
                on_progress(min(1.0, (_i + sub) / float(total)), _label)

            dest = os.path.join(CONFIG.PACKAGES, '{0}_install.zip'.format(aid))
            tools.remove_file(dest)
            report(0.0)
            _log('installing {0} {1} (headless)'.format(aid, job.get('version', '')))

            ok = download_zip(job['zip'], dest,
                              on_progress=lambda pct: report(pct / 200.0),
                              should_abort=should_abort)
            want = job.get('sha256')
            if ok and want and not _sha256_matches(dest, want):
                _log('sha256 mismatch for {0}'.format(aid), level=_LOGERROR)
                ok = False
            if not ok:
                tools.remove_file(dest)
                _log('download failed for {0}'.format(aid), level=_LOGERROR)
                report(1.0)
                continue

            ok = extract_zip(dest, CONFIG.ADDONS,
                             on_progress=lambda cur, tot: report(0.5 + 0.5 * cur / float(tot or 1)))
            tools.remove_file(dest)
            if ok:
                installed.append(aid)
            report(1.0)
        return installed

    def install(self, wanted_ids, on_progress=None):
        """Headlessly install wanted_ids + their resolvable deps (no window, no
        dialogs, no native installer).

        Returns (installed_ids, missing_ids). missing_ids are REQUESTED ids that
        are still not on the device; non-binary ones are retried by the next
        launch's self-heal. Missing BINARY addons are exposed as
        self.binary_missing for the caller's post-GUI native fallback."""
        try:
            tools.ensure_folders(CONFIG.PACKAGES)
        except Exception:
            pass
        self.load_index()

        for a in wanted_ids:
            if self._present(a):
                _log('{0} already present -- skipping'.format(a))

        order = self._resolve(wanted_ids)
        _log('resolved install order ({0}): {1}'.format(len(order), order))
        if self.unresolved:
            _log('unresolved (deferred to self-heal): {0}'.format(sorted(self.unresolved)),
                 level=_LOGWARNING)
        if self.binary_missing:
            _log('binary addons for native fallback: {0}'.format(sorted(self.binary_missing)),
                 level=_LOGWARNING)

        jobs = [self._job_for(a) for a in order if a in self.index]
        installed = self.install_jobs(jobs, on_progress=on_progress)

        # On disk but still disabled in Kodi's in-memory state: just enable.
        disabled = [a for a in wanted_ids
                    if a not in installed and self._on_disk(a) and not self._installed(a)]
        self.register_and_enable(installed + disabled)

        missing = [a for a in wanted_ids if not self._present(a)]
        _log('headless install done. installed={0} missing={1}'.format(
            sorted(installed), missing))
        return sorted(installed), missing

    def resolve_and_prepare(self, wanted_ids):
        """Phase 2 Preparer: Headlessly resolve missing dependencies and return jobs
        for dynamic UI injection, avoiding native popups.

        Returns (phase2_jobs, native_missing):
          * phase2_jobs   -- deps-first download/extract jobs (never binary addons).
          * native_missing -- ONLY binary/platform addons (inputstream.*, pvr.* ...)
                              that are genuinely absent; the caller installs them
                              natively AFTER the custom GUI has closed.
        Non-binary ids that could not be resolved are left in self.unresolved and
        are deferred to the next launch's self-heal (never installed natively)."""
        self.load_index()
        order = self._resolve(wanted_ids)
        _log('resolve_and_prepare calculated install order: {0}'.format(order))

        phase2_jobs = []
        for aid in order:
            if aid in self.unresolved:
                continue
            if aid not in self.index:
                continue
            phase2_jobs.append(self._job_for(aid))

        if self.unresolved:
            _log('deferred to next-launch self-heal: {0}'.format(sorted(self.unresolved)),
                 level=_LOGWARNING)
        native_missing = sorted(a for a in self.binary_missing if a.startswith(BINARY_PREFIXES))
        return phase2_jobs, native_missing