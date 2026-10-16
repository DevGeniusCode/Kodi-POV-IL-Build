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
                 setting in the file is left untouched. ids in ``exclude_ids``
                 are never written (machine-specific: deviceuuid, resolution).
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
download uses the dialog-free ``headless_installer.download_zip`` so no extra
Kodi progress dialog pops up over the install window. With ``progress=None`` the
function behaves exactly as before.
"""

import os
import json
import hashlib
import shutil
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


def merge_settings_xml(existing_text, incoming_text, exclude_ids=None):
    """Merge incoming <setting id=...> values into existing, build wins.

    Returns the merged document as a unicode string. Settings present only in
    ``existing`` (the user's own) are preserved untouched. ids in
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


# --------------------------------------------------------------------------- #
#  On-device orchestration (uses CONFIG / logging / xbmc -- Kodi only).        #
# --------------------------------------------------------------------------- #

POLICY_NAME = 'config_policy.json'


def _home_path(dest_rel):
    """Map a policy 'dest' (relative to special://home/) to an absolute path,
    staying profile-aware for anything under userdata/."""
    from resources.libs.common.config import CONFIG
    dest_rel = dest_rel.replace('\\', '/')
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def _apply_file(spec, src_dir, fresh):
    """Apply a single policy file entry. Returns the dest path applied, or None
    when nothing was written (e.g. seed_if_absent and dest already exists)."""
    import xbmc
    from resources.libs.common import logging

    src_path = os.path.join(src_dir, spec['src'].replace('/', os.sep))
    if not os.path.exists(src_path):
        logging.log("[config_apply] missing source {0}; skipping".format(spec['src']), level=xbmc.LOGWARNING)
        return None

    dest_path = _home_path(spec['dest'])
    mode = spec.get('fresh' if fresh else 'update', 'replace')
    exclude_ids = spec.get('exclude_ids', [])

    if mode == 'seed_if_absent' and os.path.exists(dest_path):
        logging.log("[config_apply] seed_if_absent: {0} exists; keeping user copy".format(spec['dest']))
        return None

    if mode in ('replace', 'seed_if_absent'):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copyfile(src_path, dest_path)
    elif mode == 'merge_id':
        merged = merge_settings_xml(_read_text(dest_path), _read_text(src_path), exclude_ids)
        _write_text(dest_path, merged)
    elif mode == 'merge_name':
        merged = merge_sources_xml(_read_text(dest_path), _read_text(src_path))
        _write_text(dest_path, merged)
    else:
        logging.log("[config_apply] unknown mode '{0}' for {1}; using replace".format(mode, spec['dest']), level=xbmc.LOGWARNING)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copyfile(src_path, dest_path)

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


def _download_config_zip(url, zip_path, background, report):
    """Fetch the config zip. Primary path is the dialog-free downloader (real
    byte progress, no popup over the install window). If that fails, fall back
    once to the legacy Downloader so a quirk of the silent path can never block
    the config apply. Returns True on a non-empty file."""
    import xbmc
    from resources.libs.common import logging
    from resources.libs.common import tools

    silent_ok = False
    try:
        from resources.libs.headless_installer import download_zip
        silent_ok = bool(download_zip(
            url, zip_path, on_progress=lambda pct: report.step('download', pct, 100)))
    except Exception as e:
        logging.log("[config_apply] silent config download unavailable: {0}".format(e),
                    level=xbmc.LOGWARNING)

    if not silent_ok:
        logging.log("[config_apply] silent download failed; trying legacy Downloader",
                    level=xbmc.LOGWARNING)
        tools.remove_file(zip_path)
        try:
            from resources.libs.downloader import Downloader
            Downloader(progress_dialog_bg=background).download(url, zip_path)
        except Exception as e:
            logging.log("[config_apply] config download failed: {0}".format(e), level=xbmc.LOGERROR)
            return False
        xbmc.sleep(300)

    ok = os.path.exists(zip_path) and os.path.getsize(zip_path) > 0
    if ok:
        report.done('download')
    return ok


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
    if not _download_config_zip(url, zip_path, background, report):
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
        import zipfile
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

    active_skin = xbmc.getSkinDir()
    policy_files = policy.get('files', [])
    total_policy = len(policy_files)
    for i, spec in enumerate(policy_files, 1):
        applied_path = None
        try:
            applied_path = _apply_file(spec, work_dir, fresh)
        except Exception as e:
            logging.log("[config_apply] failed applying {0}: {1}".format(spec.get('dest'), e), level=xbmc.LOGERROR)
        report.step('apply', i, total_policy)   # counted whether it applied, skipped or failed
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
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copyfile(full_src_path, dest_path)
                result['applied'] = True
                if dest_rel.endswith('guisettings.xml') or (active_skin and active_skin in dest_rel):
                    result['skin_touched'] = True
                logging.log("[config_apply] unlisted file auto-seeded: {0} -> {1}".format(dest_rel, dest_path))
            except Exception as e:
                logging.log("[config_apply] failed auto-seeding unlisted {0}: {1}".format(dest_rel, e), level=xbmc.LOGERROR)
        report.step('seed', i, total_unlisted)
    report.done('seed')

    _run_cleanup(policy)

    CONFIG.set_setting('config_applied_version', version)
    shutil.rmtree(work_dir, ignore_errors=True)
    tools.remove_file(zip_path)
    report.done('finish')
    logging.log("[config_apply] applied config {0} (fresh={1})".format(version, fresh), level=xbmc.LOGINFO)
    return result