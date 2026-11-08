"""Apply the build-config (userdata/) pack onto a device, value-by-value.

Option 3 of the modular build design. The build's identity -- active skin,
locale, subtitle config, the FENtastic look, favourites, sources, advanced
settings -- ships as a versioned ``config-<version>.zip`` listed in
manifest.json (see .github/scripts/build_config.py + gen_manifest.py). This
module downloads that pack, verifies its sha256, and applies each file per the
bundled ``config_policy.json``:

  * fresh install  -> seed the full build identity (no monolithic build zip
                      needed), using each file's ``fresh`` mode.
  * existing device -> apply each file's ``update`` mode, which merges at the
                      <setting id=...> / <source><name> level so the user's
                      own keys, widgets, and tweaks are NEVER clobbered.

Apply modes:
  replace        overwrite the whole destination file.
  merge_id       per <setting id=...>: the build value wins, every other user
                 setting in the file is left untouched.
  merge_missing_id
                 add setting IDs absent on the device; keep existing values.
                 ids in ``exclude_ids`` are never written (machine-specific:
                 deviceuuid, resolution).
  merge_name     per <source><name> under each sources.xml section: add the
                 build's sources, keep the user's; never delete.
  seed_if_absent write only when the destination does not already exist.

The id/name-level merges are idempotent and order-independent, so a user who
skipped several config versions converges to the latest in a single apply --
no patch chains, no full-file backup/restore dance.

The XML merge helpers at the top are pure (stdlib only) so they can be unit
tested off-device.

PROGRESS REPORTING
------------------
apply_config_pack() accepts an optional shared ``ProvisionProgress`` tracker
(resources/libs/provision_progress.py). When given, it reports EXACT progress for
the tracker's 'config' phase via ``progress.set_fraction('config', current/total)``
as real work completes (download bytes, zip members extracted, policy files
applied, unlisted files seeded), replacing the tracker's time-based creep. The
download is dialog-free and reports real byte progress, so no extra Kodi
progress dialog pops up over the install window. With ``progress=None`` the
function behaves exactly as before.

RETAINED VERIFIED PACK
----------------------
After a successful apply, the zip whose sha256 matched the manifest is kept in
the MASTER profile's wizard ``addon_data/config_pack/`` together with a small
receipt (version + sha256). Secondary profiles are seeded from that copy
through ``open_verified_pack()``; they never read the master's accounts and the
Wizard ships no config bundle. The copy is re-hashed on every open, and a pack
the manifest did not provide a sha256 for is never retained.
"""

import os
import json
import hashlib
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET


# --------------------------------------------------------------------------- #
#  Pure helpers (stdlib only -- safe to import/unit-test without Kodi).        #
# --------------------------------------------------------------------------- #

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _parse_settings(text):
    """Parse a <settings> document, returning (root_element). Tolerates an
    empty/None/garbage input by returning a fresh <settings> root."""
    if text:
        try:
            root = ET.fromstring(text)
            if root.tag == 'settings':
                return root
        except ET.ParseError:
            pass
    return ET.Element('settings')


def merge_settings_xml(existing_text, incoming_text, exclude_ids=None,
                       overwrite_existing=True):
    """Merge incoming <setting id=...> values into existing, build wins.

    Returns the merged document as a unicode string. Settings present only in
    ``existing`` (the user's own) are preserved untouched. If
    ``overwrite_existing`` is false, even matching IDs retain the installed
    value. ids in
    ``exclude_ids`` are skipped entirely (never written).
    """
    exclude = set(exclude_ids or ())
    existing_root = _parse_settings(existing_text)
    incoming_root = _parse_settings(incoming_text)

    # Index existing settings by id for in-place overwrite.
    by_id = {}
    for el in existing_root.findall('setting'):
        sid = el.get('id')
        if sid is not None:
            by_id[sid] = el

    for inc in incoming_root.findall('setting'):
        sid = inc.get('id')
        if sid is None or sid in exclude:
            continue
        if sid in by_id:
            if not overwrite_existing:
                continue
            target = by_id[sid]
            target.text = inc.text
            # Carry a meaningful type attr if the incoming file declares one
            # (skin settings.xml uses type="bool"/"string"); drop stale
            # default="true" so Kodi treats the value as user-set.
            if inc.get('type') is not None:
                target.set('type', inc.get('type'))
            if 'default' in target.attrib:
                del target.attrib['default']
        else:
            new_el = ET.SubElement(existing_root, 'setting')
            new_el.set('id', sid)
            if inc.get('type') is not None:
                new_el.set('type', inc.get('type'))
            new_el.text = inc.text
            by_id[sid] = new_el

    return _tostring(existing_root)


def merge_sources_xml(existing_text, incoming_text):
    """Merge incoming <source> entries into existing, matched by <name>.

    Build sources missing from the user's file are added under the correct
    section (files/video/...); existing user sources are never removed or
    overwritten. Returns the merged document as a unicode string.
    """
    if existing_text:
        try:
            existing_root = ET.fromstring(existing_text)
        except ET.ParseError:
            existing_root = ET.fromstring(incoming_text)
            return _tostring(existing_root)
    else:
        return incoming_text

    try:
        incoming_root = ET.fromstring(incoming_text)
    except ET.ParseError:
        return _tostring(existing_root)

    for inc_section in list(incoming_root):
        tag = inc_section.tag
        dst_section = existing_root.find(tag)
        if dst_section is None:
            existing_root.append(inc_section)
            continue
        have_names = set()
        for src in dst_section.findall('source'):
            name_el = src.find('name')
            if name_el is not None and name_el.text:
                have_names.add(name_el.text.strip())
        for src in inc_section.findall('source'):
            name_el = src.find('name')
            name = name_el.text.strip() if (name_el is not None and name_el.text) else None
            if name and name not in have_names:
                dst_section.append(src)
                have_names.add(name)

    return _tostring(existing_root)


def _tostring(root):
    return ET.tostring(root, encoding='unicode')


def _gui_defaults(policy, gui_bytes):
    """guisettings.xml with the policy's machine-specific ``exclude_ids`` removed.

    Returns None when the policy declares no guisettings.xml entry. Shared by
    the fresh-install snapshot and the secondary-profile seed, so both start
    from exactly the same defaults.
    """
    spec = next((s for s in policy.get('files', [])
                 if s.get('src') == 'guisettings.xml'), None)
    if spec is None:
        return None
    root = ET.fromstring(gui_bytes)
    excluded = set(spec.get('exclude_ids', []))
    for node in list(root):
        if node.get('id') in excluded:
            root.remove(node)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _pack_layout(zf):
    """Return (policy, {userdata-relative path: zip member}) for an opened pack.

    Mirrors how apply_config_pack places files: policy entries go to their
    declared ``dest``; every unlisted member is auto-seeded to the same
    relative path under userdata/. Only userdata destinations are returned.
    """
    policy = json.loads(zf.read(POLICY_NAME).decode('utf-8'))
    members, handled = {}, set()
    for spec in policy.get('files', []):
        src = _safe_relative_path(spec['src'])
        handled.add(src)
        dest = _safe_relative_path(spec['dest'])
        if dest.startswith('userdata/'):
            members[dest[len('userdata/'):]] = src
    for name in zf.namelist():
        if name.endswith('/') or name == POLICY_NAME:
            continue
        relative = _safe_relative_path(name)
        if relative not in handled:
            members.setdefault(relative, name)
    return policy, members


# --------------------------------------------------------------------------- #
#  Retained, hash-verified pack (read by secondary-profile seeding).           #
# --------------------------------------------------------------------------- #

RETAINED_DIR = 'config_pack'
RETAINED_ZIP = 'build_config.zip'
RETAINED_RECEIPT = 'build_config.json'


class PackUnavailable(Exception):
    """No hash-verified build config pack is retained on this device."""


def _retained_dir():
    from resources.libs.common.config import CONFIG
    return os.path.join(CONFIG.MASTERPROFILE, 'addon_data', CONFIG.ADDON_ID, RETAINED_DIR)


def _retain_verified_pack(zip_path, version, sha256):
    """Keep a copy of a pack that was just verified against the manifest.

    The zip is replaced before the receipt; a crash in between leaves a zip
    that no longer matches the old receipt, which _verified() rejects.
    """
    folder = _retained_dir()
    _atomic_copy(zip_path, os.path.join(folder, RETAINED_ZIP))
    _write_text(os.path.join(folder, RETAINED_RECEIPT), json.dumps(
        {'config_version': version, 'sha256': str(sha256).lower()}))


def _verified():
    """(zip path, version) of the retained pack if its bytes still match the
    sha256 recorded when it was verified, else None."""
    folder = _retained_dir()
    zip_path = os.path.join(folder, RETAINED_ZIP)
    try:
        with open(os.path.join(folder, RETAINED_RECEIPT), 'r', encoding='utf-8') as fh:
            receipt = json.load(fh)
        version, want = receipt['config_version'], receipt['sha256']
        if not isinstance(version, str) or not version or not isinstance(want, str):
            return None
        if sha256_file(zip_path).lower() != want.lower():
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return zip_path, version


def verified_pack_path():
    """Path of the retained, hash-verified config pack, or None."""
    found = _verified()
    return found[0] if found else None


def ensure_verified_pack(manifest=None):
    """Return the retained pack path, downloading it once when it is absent.

    Used when the pack was never retained (for example the config version was
    already applied before this device could retain it). Nothing is applied to
    userdata. A manifest without a sha256 yields None: an unverifiable pack is
    never trusted for profile seeding.
    """
    from resources.libs.common.config import CONFIG
    from resources.libs.common import logging
    from resources.libs.common import tools
    import xbmc
    existing = verified_pack_path()
    if existing:
        return existing
    if manifest is None:
        from resources.libs.modular_updater import ModularUpdater
        manifest = ModularUpdater(background=True)._load_manifest()
    cfg = (manifest or {}).get('config') or {}
    version, url, want = cfg.get('config_version'), cfg.get('zip'), cfg.get('sha256')
    if not (version and url and want):
        logging.log('[config_apply] no verifiable config pack in manifest', level=xbmc.LOGWARNING)
        return None
    tools.ensure_folders(CONFIG.PACKAGES)
    zip_path = os.path.join(CONFIG.PACKAGES, 'build_config_profiles.zip')
    tools.remove_file(zip_path)
    try:
        _download_config_zip(url, zip_path, _ConfigProgress(None))
        if sha256_file(zip_path).lower() != str(want).lower():
            logging.log('[config_apply] sha256 mismatch for retained pack', level=xbmc.LOGERROR)
            return None
        _retain_verified_pack(zip_path, version, want)
    except Exception as exc:
        logging.log('[config_apply] could not fetch config pack: {0}'.format(exc), level=xbmc.LOGERROR)
        return None
    finally:
        tools.remove_file(zip_path)
    return verified_pack_path()


class VerifiedPack(object):
    """Read-only view of the retained pack, keyed by userdata-relative path.

    ``names()`` / ``in`` / ``read()`` use the same paths the pack's files land
    on under userdata/ (policy ``dest`` honoured). ``guisettings()`` returns
    the build's GUI defaults without the machine-specific ``exclude_ids``.
    """

    def __init__(self, path, version):
        self.path = path
        self.version = version
        self._zf = None
        self._policy = None
        self._members = {}

    def __enter__(self):
        try:
            self._zf = zipfile.ZipFile(self.path, 'r')
            self._policy, self._members = _pack_layout(self._zf)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            self.close()
            raise PackUnavailable('retained config pack is unreadable: {0}'.format(exc))
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def close(self):
        if self._zf is not None:
            self._zf.close()
            self._zf = None

    def names(self):
        return list(self._members)

    def __contains__(self, relative):
        return relative in self._members

    def read(self, relative):
        return self._zf.read(self._members[relative])

    def guisettings(self):
        relative = 'guisettings.xml'
        if relative not in self._members:
            return None
        return _gui_defaults(self._policy, self.read(relative))


def open_verified_pack(fetch=False, manifest=None):
    """Context manager over the retained pack; raises PackUnavailable if none.

    ``fetch=True`` downloads and verifies the pack first when it is absent.
    Only the master profile should pass it: it is the owner of the shared copy.
    """
    found = _verified()
    if found is None and fetch:
        ensure_verified_pack(manifest)
        found = _verified()
    if found is None:
        raise PackUnavailable('no hash-verified config pack is retained')
    return VerifiedPack(*found)


# --------------------------------------------------------------------------- #
#  On-device orchestration (uses CONFIG / logging / xbmc -- Kodi only).        #
# --------------------------------------------------------------------------- #

POLICY_NAME = 'config_policy.json'


def _safe_relative_path(value):
    """Reject policy paths that could escape the config pack or Kodi home."""
    if not isinstance(value, str):
        raise ValueError('config path must be text')
    normalized = value.replace('\\', '/')
    parts = normalized.split('/')
    if (not normalized or normalized.startswith('/') or
            any(part in ('', '.', '..') or ':' in part for part in parts)):
        raise ValueError('unsafe config path: {0}'.format(value))
    return normalized


def _home_path(dest_rel):
    """Map a policy 'dest' (relative to special://home/) to an absolute path,
    staying profile-aware for anything under userdata/."""
    from resources.libs.common.config import CONFIG
    dest_rel = _safe_relative_path(dest_rel)
    if dest_rel.startswith('userdata/'):
        return os.path.join(CONFIG.USERDATA, *dest_rel.split('/')[1:])
    return os.path.join(CONFIG.HOME, *dest_rel.split('/'))


def _read_text(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return fh.read()
    except Exception:
        return None


def _write_text(path, text):
    _atomic_write(path, text.encode('utf-8'))


def _atomic_write(path, data):
    """Keep the installed file intact if a copy/write is interrupted."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='.config-', dir=folder)
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def _atomic_copy(src, dest):
    with open(src, 'rb') as fh:
        _atomic_write(dest, fh.read())


def _download_config_zip(url, dest, report):
    """Stream a config ZIP with a timeout and report real byte progress."""
    from urllib.request import Request, ProxyHandler, HTTPSHandler, build_opener
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    temp = None
    try:
        request = Request(url)
        handlers = [ProxyHandler({})]
        if url.lower().startswith('https://'):
            import ssl
            context = ssl.create_default_context()
            # Keep Kodi/system trust and add the portable bundle already used
            # by requests. Android may have no usable system CA store.
            try:
                import certifi
                context.load_verify_locations(cafile=certifi.where())
            except ImportError:
                pass
            handlers.append(HTTPSHandler(context=context))
        opener = build_opener(*handlers)
        with opener.open(request, timeout=20) as source:
            try:
                total = int(source.headers.get('Content-Length') or 0)
            except (TypeError, ValueError):
                total = 0
            fd, temp = tempfile.mkstemp(prefix='.config-download-',
                                        dir=os.path.dirname(dest))
            with os.fdopen(fd, 'wb') as out:
                done = 0
                while True:
                    chunk = source.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        report.step('download', done, total)
        os.replace(temp, dest)
        temp = None
        report.done('download')
    finally:
        if temp and os.path.exists(temp):
            os.remove(temp)


def _require_valid_xml(text, expected_root, label):
    if text is None:
        raise ValueError('{0} could not be read'.format(label))
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError('{0} is malformed; refusing to overwrite it'.format(label)) from exc
    if root.tag != expected_root:
        raise ValueError('{0} has unexpected root; refusing to overwrite it'.format(label))


def _preflight_policy(policy, src_dir, fresh):
    """Validate every declared write before changing any user file."""
    seen = set()
    destinations = []
    for spec in policy.get('files', []):
        src = os.path.join(src_dir, *_safe_relative_path(spec['src']).split('/'))
        dest = _home_path(spec['dest'])
        mode = spec.get('fresh' if fresh else 'update', 'replace')
        if dest in seen:
            raise ValueError('duplicate config destination: {0}'.format(spec['dest']))
        seen.add(dest)
        if not os.path.isfile(src):
            raise FileNotFoundError('missing config source {0}'.format(spec['src']))
        if mode not in ('replace', 'seed_if_absent', 'merge_id',
                        'merge_missing_id', 'merge_name'):
            raise ValueError('unknown config mode {0}'.format(mode))
        if mode in ('merge_id', 'merge_missing_id', 'merge_name'):
            tag = 'sources' if mode == 'merge_name' else 'settings'
            _require_valid_xml(_read_text(src), tag, src)
            if os.path.exists(dest):
                _require_valid_xml(_read_text(dest), tag, dest)
        if mode != 'seed_if_absent' or not os.path.exists(dest):
            destinations.append(dest)
    for rel in (policy.get('cleanup', {}) or {}).get('remove_paths', []) or []:
        _home_path(rel)
    return destinations


def _apply_file(spec, src_dir, fresh):
    """Apply a single policy file entry. Returns the dest path applied, or None
    when nothing was written (e.g. seed_if_absent and dest already exists)."""
    from resources.libs.common import logging

    src_path = os.path.join(src_dir, *_safe_relative_path(spec['src']).split('/'))
    if not os.path.exists(src_path):
        raise FileNotFoundError('missing config source {0}'.format(spec['src']))

    dest_path = _home_path(spec['dest'])
    mode = spec.get('fresh' if fresh else 'update', 'replace')
    exclude_ids = spec.get('exclude_ids', [])

    if mode == 'seed_if_absent' and os.path.exists(dest_path):
        logging.log("[config_apply] seed_if_absent: {0} exists; keeping user copy".format(spec['dest']))
        return None

    if mode in ('replace', 'seed_if_absent'):
        _atomic_copy(src_path, dest_path)
    elif mode in ('merge_id', 'merge_missing_id'):
        old = _read_text(dest_path)
        incoming = _read_text(src_path)
        if os.path.exists(dest_path):
            _require_valid_xml(old, 'settings', dest_path)
        _require_valid_xml(incoming, 'settings', src_path)
        merged = merge_settings_xml(
            old, incoming, exclude_ids,
            overwrite_existing=(mode == 'merge_id'))
        _write_text(dest_path, merged)
    elif mode == 'merge_name':
        old = _read_text(dest_path)
        incoming = _read_text(src_path)
        if os.path.exists(dest_path):
            _require_valid_xml(old, 'sources', dest_path)
        _require_valid_xml(incoming, 'sources', src_path)
        merged = merge_sources_xml(old, incoming)
        _write_text(dest_path, merged)
    else:
        raise ValueError("unknown config policy mode '{0}' for {1}".format(mode, spec['dest']))

    logging.log("[config_apply] {0} -> {1} ({2})".format(spec['src'], spec['dest'], mode))
    return dest_path


# Relative cost of each stage of apply_config_pack, used to turn "N of M done
# inside this stage" into one overall 0..1 value for the tracker's 'config'
# phase. Only the proportions matter.
_STAGE_WEIGHTS = (
    ('download', 20),   # fetching config zip (real byte progress)
    ('verify', 3),      # sha256
    ('extract', 12),    # zip members
    ('apply', 50),      # policy files (merge/replace)
    ('seed', 10),       # unlisted files auto-seeded
    ('finish', 5),      # cleanup + version stamp
)


class _ConfigProgress(object):
    """Maps apply_config_pack's real stages onto 0..1 and reports the result to
    the shared tracker's 'config' phase. Every call is a no-op when no tracker
    was given, and a tracker error can never break the config apply."""

    PHASE = 'config'

    def __init__(self, progress):
        self._progress = progress
        total = float(sum(w for _, w in _STAGE_WEIGHTS))
        self._base, self._span = {}, {}
        acc = 0.0
        for name, weight in _STAGE_WEIGHTS:
            self._base[name] = acc / total
            self._span[name] = weight / total
            acc += weight

    def step(self, stage, done, total):
        """`done` of `total` units finished inside `stage`."""
        if self._progress is None:
            return
        try:
            frac = (float(done) / float(total)) if total else 1.0
            frac = min(1.0, max(0.0, frac))
            self._progress.set_fraction(
                self.PHASE, self._base[stage] + self._span[stage] * frac)
        except Exception:
            pass

    def done(self, stage):
        self.step(stage, 1, 1)


def _run_cleanup(policy):
    import xbmc
    from resources.libs.common.config import CONFIG
    from resources.libs.common import logging
    for rel in (policy.get('cleanup', {}) or {}).get('remove_paths', []) or []:
        target = _home_path(rel)
        try:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
                logging.log("[config_apply] cleanup removed dir {0}".format(rel))
            elif os.path.isfile(target):
                os.remove(target)
                logging.log("[config_apply] cleanup removed file {0}".format(rel))
        except Exception as e:
            logging.log("[config_apply] cleanup failed for {0}: {1}".format(rel, e), level=xbmc.LOGWARNING)


def _saved_active_skin(userdata):
    """Read startup skin from disk without waiting on Kodi's GUI thread.

    During AF3 widget startup xbmc.getSkinDir() can block the background
    updater for longer than the whole update budget. The persisted skin is
    enough to tell whether a policy file belongs to the active skin; an
    unknown skin simply means no skin-specific reload is requested.
    """
    path = os.path.join(userdata, 'guisettings.xml')
    try:
        root = ET.parse(path).getroot()
        setting = root.find(".//setting[@id='lookandfeel.skin']")
        if setting is not None and setting.text:
            return setting.text.strip()
    except (OSError, ET.ParseError):
        pass
    return None


def apply_config_pack(manifest, fresh=False, background=True, progress=None):
    """Download + verify + apply the config pack described by manifest['config'].

    Returns a dict: {'applied': bool, 'skin_touched': bool, 'version': str}.
    On an existing device the pack is applied only when its config_version
    moved past what we last applied (idempotent: once per config release).

    ``progress`` is an optional shared ProvisionProgress. When given, exact step
    progress is reported with ``progress.set_fraction('config', current/total)``
    (download -> verify -> extract -> apply policy files -> seed unlisted files ->
    finish). The caller is expected to call ``progress.complete('config')`` when
    this returns, which also covers the early-return paths below.
    """
    import xbmc
    from resources.libs.common.config import CONFIG
    from resources.libs.common import logging
    from resources.libs.common import tools

    report = _ConfigProgress(progress)

    result = {'applied': False, 'skin_touched': False, 'version': None}
    cfg = (manifest or {}).get('config') or {}
    version = cfg.get('config_version')
    url = cfg.get('zip')
    want_sha = cfg.get('sha256')
    if not version or not url:
        logging.log("[config_apply] manifest has no usable config block; nothing to do", level=xbmc.LOGINFO)
        return result
    result['version'] = version

    if not fresh:
        applied_ver = CONFIG.get_setting('config_applied_version')
        if applied_ver == version:
            logging.log("[config_apply] config {0} already applied; skipping".format(version))
            return result

    tools.ensure_folders(CONFIG.PACKAGES)
    zip_path = os.path.join(CONFIG.PACKAGES, 'build_config.zip')
    tools.remove_file(zip_path)
    try:
        _download_config_zip(url, zip_path, report)
    except Exception as e:
        logging.log("[config_apply] config download failed: {0}".format(e), level=xbmc.LOGERROR)
        return result
    if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
        logging.log("[config_apply] config zip missing/empty after download", level=xbmc.LOGERROR)
        return result

    # Verify integrity before touching the user's userdata.
    if want_sha:
        got = sha256_file(zip_path)
        if got.lower() != str(want_sha).lower():
            logging.log("[config_apply] sha256 mismatch (want {0}, got {1}); aborting".format(want_sha, got), level=xbmc.LOGERROR)
            tools.remove_file(zip_path)
            return result
    report.done('verify')

    work_dir = os.path.join(CONFIG.PACKAGES, 'build_config_extracted')
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            members = zf.namelist()
            total_members = len(members)
            for i, member in enumerate(members, 1):
                zf.extract(member, work_dir)
                report.step('extract', i, total_members)
        report.done('extract')
    except Exception as e:
        logging.log("[config_apply] failed to extract config zip: {0}".format(e), level=xbmc.LOGERROR)
        tools.remove_file(zip_path)
        return result

    policy_path = os.path.join(work_dir, POLICY_NAME)
    try:
        with open(policy_path, 'r', encoding='utf-8') as fh:
            policy = json.load(fh)
    except Exception as e:
        logging.log("[config_apply] cannot read bundled policy: {0}".format(e), level=xbmc.LOGERROR)
        return result

    active_skin = _saved_active_skin(CONFIG.USERDATA)
    policy_files = policy.get('files', [])
    total_policy = len(policy_files)
    try:
        destinations = _preflight_policy(policy, work_dir, fresh)
    except Exception as exc:
        logging.log('[config_apply] preflight failed: {0}'.format(exc),
                    level=xbmc.LOGERROR)
        result['failed_files'] = [spec.get('dest') for spec in policy_files]
        shutil.rmtree(work_dir, ignore_errors=True)
        tools.remove_file(zip_path)
        return result

    # Back up to disk, not RAM: Kodi databases can be large on 32-bit devices.
    # If even the snapshot cannot be made, leave userdata as-is.
    backup_dir = tempfile.mkdtemp(prefix='config-rollback-', dir=CONFIG.PACKAGES)
    original_files = {}
    try:
        for index, path in enumerate(destinations):
            if os.path.isfile(path):
                backup = os.path.join(backup_dir, str(index))
                # Android emulated storage allows copying the bytes but can
                # reject copy2's chmod/utime metadata operation (EACCES).
                # Rollback needs the original content, not its filesystem
                # metadata. A real read/write failure must still abort here.
                shutil.copyfile(path, backup)
                original_files[path] = backup
            else:
                original_files[path] = None
    except Exception as exc:
        logging.log('[config_apply] could not back up userdata: {0}'.format(exc),
                    level=xbmc.LOGERROR)
        shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        tools.remove_file(zip_path)
        return result

    failed_files = []
    for i, spec in enumerate(policy_files, 1):
        applied_path = None
        try:
            applied_path = _apply_file(spec, work_dir, fresh)
        except Exception as e:
            logging.log("[config_apply] failed applying {0}: {1}".format(spec.get('dest'), e), level=xbmc.LOGERROR)
            failed_files.append(spec.get('dest'))
            report.step('apply', i, total_policy)
            continue
        report.step('apply', i, total_policy)   # counted whether it applied or was skipped
        if applied_path:
            result['applied'] = True
            d = spec.get('dest', '')
            # A reload is warranted when we changed the active skin selection
            # (guisettings lookandfeel.skin) or the active skin's own settings.
            if d.endswith('guisettings.xml') or (active_skin and active_skin in d):
                result['skin_touched'] = True
    report.done('apply')

    # Automatically backup/copy files that were forgotten to be configured in config_policy.json
    # (provided they do not exist on the target)
    handled_srcs = {spec.get('src', '').replace('/', os.sep) for spec in policy_files}

    # Collect the unlisted files first so the seeding step has an exact total.
    unlisted = []
    for root, dirs, walk_files in os.walk(work_dir):
        for file in walk_files:
            if file == POLICY_NAME:
                continue
            full_src_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_src_path, work_dir)
            if rel_path not in handled_srcs:
                unlisted.append((full_src_path, rel_path))

    total_unlisted = len(unlisted)
    for i, (full_src_path, rel_path) in enumerate(unlisted, 1):
        dest_rel = rel_path.replace(os.sep, '/')
        dest_rel_userdata = 'userdata/' + dest_rel
        dest_path = _home_path(dest_rel_userdata)

        if not os.path.exists(dest_path):
            try:
                original_files.setdefault(dest_path, None)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copyfile(full_src_path, dest_path)
                result['applied'] = True
                if dest_rel.endswith('guisettings.xml') or (active_skin and active_skin in dest_rel):
                    result['skin_touched'] = True
                logging.log("[config_apply] unlisted file auto-seeded: {0} -> {1}".format(dest_rel, dest_path))
            except Exception as e:
                logging.log("[config_apply] failed auto-seeding unlisted {0}: {1}".format(dest_rel, e), level=xbmc.LOGERROR)
                failed_files.append(dest_rel)
        report.step('seed', i, total_unlisted)
    report.done('seed')

    if failed_files:
        # Do not leave a partially applied config if a later write fails.
        restore_failed = []
        for path, previous in original_files.items():
            try:
                if previous is None:
                    if os.path.isfile(path):
                        os.remove(path)
                else:
                    _atomic_copy(previous, path)
            except Exception as exc:
                restore_failed.append('{0}: {1}'.format(path, exc))
        logging.log('[config_apply] incomplete config {0}; failed: {1}'.format(
            version, ', '.join(failed_files)), level=xbmc.LOGERROR)
        if restore_failed:
            logging.log('[config_apply] rollback failed: {0}'.format(
                ', '.join(restore_failed)), level=xbmc.LOGERROR)
        result['failed_files'] = failed_files
        result['applied'] = False
        shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        tools.remove_file(zip_path)
        return result

    if fresh:
        # Kodi may rewrite guisettings.xml on shutdown after a failed skin
        # activation. Keep the intended fresh defaults until finalization can
        # verify them, rather than certifying the rewritten old profile.
        with open(os.path.join(work_dir, 'guisettings.xml'), 'rb') as fh:
            defaults = _gui_defaults(policy, fh.read())
        if defaults is not None:
            _atomic_write(os.path.join(CONFIG.USERDATA, 'kodipovil.fresh_gui_defaults.xml'), defaults)

    _run_cleanup(policy)

    CONFIG.set_setting('config_applied_version', version)
    if want_sha:
        # Secondary profiles are seeded from this verified copy. Retaining it
        # can never fail the apply that already succeeded.
        try:
            _retain_verified_pack(zip_path, version, want_sha)
        except Exception as exc:
            logging.log('[config_apply] could not retain verified pack: {0}'.format(exc),
                        level=xbmc.LOGWARNING)
    shutil.rmtree(backup_dir, ignore_errors=True)
    shutil.rmtree(work_dir, ignore_errors=True)
    tools.remove_file(zip_path)
    report.done('finish')
    logging.log("[config_apply] applied config {0} (fresh={1})".format(version, fresh), level=xbmc.LOGINFO)
    return result