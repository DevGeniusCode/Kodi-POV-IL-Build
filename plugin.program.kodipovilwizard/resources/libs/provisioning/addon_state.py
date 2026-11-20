"""Low-level add-on state: is it on disk, enabled, which version, registered in the Addons DB.

No installing and no network: Kodi downloads, installs and updates add-ons itself (see provisioning/provisioner.py). Only the
repository bootstrap (provisioning/repo_bootstrap.py) extracts a zip, and it registers what it extracted through here.
The functions below were moved from the retired ModularUpdater; tree_stamp / wait_until_settled / the repository check are new.
Nothing here asks the GUI thread anything except has_addon (System.HasAddon).
"""
import json
import os
import re
import time
import xml.etree.ElementTree as ET

import xbmc

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs import db


def has_addon(addon_id):
    """True when Kodi reports the add-on installed AND enabled."""
    return bool(xbmc.getCondVisibility('System.HasAddon({0})'.format(addon_id)))


def on_disk(addon_id):
    """True when the addon's folder + addon.xml physically exist under
    special://home/addons -- i.e. it was extracted, regardless of whether
    Kodi currently considers it enabled."""
    try:
        return os.path.exists(os.path.join(CONFIG.ADDONS, addon_id, 'addon.xml'))
    except Exception:
        return False


def enable_addon(addon_id):
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
        response = json.loads(xbmc.executeJSONRPC(q))
        return ('error' not in response and response.get('result') in ('OK', True))
    except Exception as e:
        logging.log("[AddonState] enable {0} failed: {1}".format(addon_id, e),
                    level=xbmc.LOGWARNING)
        return False


def register_and_enable(ids, settle=1.5):
    """Register extracted addons in the Addons DB, make Kodi load them and
    enable each one. Silent: no dialog."""
    ids = list(ids or [])
    if not ids:
        return
    try:
        logging.log("[AddonState] Registering to Addons DB: {0}".format(ids), level=xbmc.LOGINFO)
        db.addon_database(ids, 1, True)
        xbmc.executebuiltin('UpdateLocalAddons')
        # Give Kodi time to unlock the SQLite DB and process the VFS event
        # queue so new Python modules (like certifi) load.
        xbmc.Monitor().waitForAbort(settle)
        for _aid in ids:
            enable_addon(_aid)
    except Exception as e:
        logging.log("[AddonState] DB sync error: {0}".format(e), level=xbmc.LOGERROR)


def local_version(addon_id):
    """Read the local addon.xml to find the currently installed version.

    Returns None when the addon is NOT installed on this device.
    """
    addon_xml = os.path.join(CONFIG.ADDONS, addon_id, 'addon.xml')
    if not os.path.exists(addon_xml):
        return None
    try:
        tree = ET.parse(addon_xml)
        return tree.getroot().get('version')
    except Exception as e:
        logging.log("[AddonState] Failed to parse local {0} XML: {1}".format(addon_id, e), level=xbmc.LOGERROR)
        return None


def tree_stamp(addon_id, limit=20000):
    """(file count, total bytes, newest mtime_ns) of an add-on's folder, or None when it is not on disk. Capped, never raises.

    Kodi unpacks an install / update over several seconds; while it does, this value keeps changing."""
    root = os.path.join(CONFIG.ADDONS, addon_id)
    if not os.path.isdir(root):
        return None
    count = size = newest = 0
    try:
        for dirpath, _dirs, filenames in os.walk(root):
            for name in filenames:
                try:
                    st = os.stat(os.path.join(dirpath, name))
                except OSError:
                    continue
                count += 1
                size += st.st_size
                newest = max(newest, st.st_mtime_ns)
                if count >= limit:
                    return (count, size, newest)
    except OSError:
        return None
    return (count, size, newest)


# Kodi is installing / updating something when a package zip or an unpack folder is younger than this (see updates_in_flight).
UPDATE_QUIET_SECONDS = 15.0


def _recent_files(directory, quiet, only_zips):
    found = []
    now = time.time()
    try:
        names = os.listdir(directory)
    except OSError:
        return found
    for name in names:
        if only_zips and (name.startswith('.') or name.startswith('build_config') or not name.lower().endswith('.zip')):
            continue
        try:
            age = now - os.stat(os.path.join(directory, name)).st_mtime
        except OSError:
            continue
        if age < quiet:
            found.append(name)
    return found


def updates_in_flight(quiet=UPDATE_QUIET_SECONDS):
    """File-based (NO GUI call) check that Kodi is downloading or unpacking an add-on: what shows it, [] when it is quiet.

    Kodi writes `<id>-<version>.zip` into addons/packages while it downloads and unpacks through a GUID folder in addons/temp; a
    file younger than `quiet` seconds counts. Our own config pack (build_config.zip) is not one. An update Kodi has found but not
    started shows nothing here: callers give Kodi a grace period after asking it to check (provisioning/update_window.py)."""
    return (['packages/' + n for n in _recent_files(CONFIG.PACKAGES, quiet, True)] +
            ['temp/' + n for n in _recent_files(os.path.join(CONFIG.ADDONS, 'temp'), quiet, False)])


def versions_snapshot():
    """{add-on id: version} of every add-on folder under special://home/addons (addon.xml read, 4 KB each). Never raises."""
    found = {}
    try:
        names = os.listdir(CONFIG.ADDONS)
    except OSError:
        return found
    for name in names:
        path = os.path.join(CONFIG.ADDONS, name, 'addon.xml')
        try:
            with open(path, 'rb') as handle:
                match = _VERSION_RE.search(handle.read(4096))
        except OSError:
            continue
        found[name] = match.group(1).decode('utf-8', 'replace') if match else ''
    return found


_VERSION_RE = re.compile(br'<addon\b[^>]*?\sversion="([^"]*)"')


def wait_until_settled(ids, timeout=120.0, quiet=4.0, sleep=1.0, monitor=None):
    """Wait until Kodi is not installing or updating any of `ids`. True when settled; False on timeout or when Kodi is closing.

    Settled = none of the add-ons' folders changed for `quiet` seconds and no add-on zip / unpack folder is being written
    (updates_in_flight). An add-on that is not on disk is only watched for appearing. File-based: it never asks the GUI. Enter /
    leave and the reason are logged."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return True
    monitor = monitor or xbmc.Monitor()
    start = last_change = time.monotonic()
    logging.log("[AddonState] wait_until_settled enter: {0} (timeout {1}s)".format(ids, int(timeout)), level=xbmc.LOGINFO)

    def leave(result, why):
        logging.log("[AddonState] wait_until_settled leave: {0} ({1}, {2:.1f}s)".format(
            'settled' if result else 'not settled', why, time.monotonic() - start), level=xbmc.LOGINFO)
        return result

    previous = {i: tree_stamp(i) for i in ids}
    while time.monotonic() - start < timeout:
        if monitor.waitForAbort(sleep):
            return leave(False, 'Kodi is closing')
        current = {i: tree_stamp(i) for i in ids}
        now = time.monotonic()
        if current != previous or updates_in_flight(quiet):
            last_change, previous = now, current
        if now - last_change >= quiet:
            return leave(True, 'quiet')
    logging.log("[AddonState] still installing / updating after {0}s: {1}".format(int(timeout), ids), level=xbmc.LOGWARNING)
    return leave(False, 'timeout')


# ---- Asking Kodi to check the repositories (never blocks the GUI thread) ----------------------------------------------------
def repo_check_stamp():
    """The `lastcheck` column of every repository in Kodi's Addons database, as a tuple (None when the database cannot be read right
    now: Kodi is writing it). Kodi writes it when a repository check finishes, whether or not anything changed. Read only."""
    try:
        from sqlite3 import dbapi2 as database
        path = os.path.join(CONFIG.DATABASE, db.latest_db('Addons'))
        connection = database.connect('file:{0}?mode=ro'.format(path.replace('\\', '/')), uri=True, timeout=1.0)
        try:
            rows = connection.execute('SELECT addonID, lastcheck FROM repo ORDER BY addonID').fetchall()
        finally:
            connection.close()
        return tuple(rows)
    except Exception:
        return None


def version_key(text):
    """Sort key of a version string: its numbers ('1.0.41' -> (1, 0, 41)); a letter suffix ('1.2.1a') is ignored. The repository's
    versions are numeric and only go up (Kodi never downgrades), so this is enough to say "newer"."""
    return tuple(int(n) for n in re.findall(r'\d+', text or ''))


def offered_version(addon_id):
    """The newest version of an add-on that Kodi's repositories offered at their last check (the Addons database, table `addons`: one
    row per add-on a repository lists), None when unknown or the database cannot be read right now. Read only, never raises."""
    try:
        from sqlite3 import dbapi2 as database
        path = os.path.join(CONFIG.DATABASE, db.latest_db('Addons'))
        connection = database.connect('file:{0}?mode=ro'.format(path.replace('\\', '/')), uri=True, timeout=1.0)
        try:
            rows = connection.execute('SELECT version FROM addons WHERE addonID = ?', (addon_id,)).fetchall()
        finally:
            connection.close()
        versions = [row[0] for row in rows if row and row[0]]
        return max(versions, key=version_key) if versions else None
    except Exception:
        return None


def check_repositories(monitor=None, timeout=30.0):
    """Post UpdateAddonRepos (a builtin posted without `wait`: the calling thread is never held by the GUI thread) and wait until
    Kodi has written a new check time for a repository. True when it did. When the database cannot be read the wait is a fixed
    short pause, and the answer is False. What Kodi does with the updates it finds depends on general.addonupdates (update_mode)."""
    monitor = monitor or xbmc.Monitor()
    before = repo_check_stamp()
    xbmc.executebuiltin('UpdateAddonRepos')
    if before is None:
        monitor.waitForAbort(3.0)
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if monitor.waitForAbort(1.0):
            return False
        now = repo_check_stamp()
        if now is not None and now != before:
            return True
    return False
