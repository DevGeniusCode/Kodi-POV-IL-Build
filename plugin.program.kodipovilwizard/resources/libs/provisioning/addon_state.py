"""Low-level add-on state: is it on disk, enabled, which version, registered in the Addons DB.

No installing and no network: Kodi downloads, installs and updates add-ons itself (see provisioning/provisioner.py). Only the
repository bootstrap (provisioning/repo_bootstrap.py) extracts a zip, and it registers what it extracted through here.
The functions below were moved from the retired ModularUpdater; tree_stamp / wait_until_settled are new.
"""
import json
import os
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


def install_dialog_active():
    """Kodi's own modal install progress dialog is up (an InstallAddon / a manual install is running)."""
    return bool(xbmc.getCondVisibility('Window.IsActive(extendedprogressdialog)'))


def updates_announced():
    """Kodi knows about add-on updates it has not installed yet (System.AddonUpdateCount; 0 when the label is unknown)."""
    try:
        return int(xbmc.getInfoLabel('System.AddonUpdateCount') or 0) > 0
    except ValueError:
        return False


def wait_until_settled(ids, timeout=120.0, quiet=4.0, grace=0.0, sleep=1.0, monitor=None):
    """Wait until Kodi is not installing or updating any of `ids`. True when settled, False on timeout or when Kodi is closing.

    Settled = none of the add-ons' folders changed for `quiet` seconds and no install dialog is up. Within the first `grace`
    seconds an announced-but-not-started update (updates_announced) also counts as "not settled": right after UpdateAddonRepos
    an update starts a few seconds later. An add-on that is not on disk is only watched for appearing."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return True
    monitor = monitor or xbmc.Monitor()
    start = last_change = time.monotonic()
    previous = {i: tree_stamp(i) for i in ids}
    while time.monotonic() - start < timeout:
        if monitor.waitForAbort(sleep):
            return False
        current = {i: tree_stamp(i) for i in ids}
        now = time.monotonic()
        if current != previous or install_dialog_active():
            last_change, previous = now, current
        expecting = (now - start) < grace and updates_announced()
        if now - last_change >= quiet and not expecting:
            return True
    logging.log("[AddonState] still installing / updating after {0}s: {1}".format(int(timeout), ids), level=xbmc.LOGWARNING)
    return False
