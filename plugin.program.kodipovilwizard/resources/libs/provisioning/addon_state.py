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
from resources.libs.common import gui_probe
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

_hung = set()


def _gui(name, call, default):
    """A GUI-bound question with a timeout (common/gui_probe): a GUI that stops answering must never hold the calling thread
    (a service that cannot return is force-killed by Kodi after 5 s). `default` is the answer when there is none. The first
    unanswered call of a kind is logged, and so is its recovery."""
    started = time.monotonic()
    answered, value = gui_probe.ask(call, key=name)
    if answered:
        if name in _hung:
            _hung.discard(name)
            logging.log("[AddonState] GUI call '{0}' answers again".format(name), level=xbmc.LOGINFO)
        elapsed = time.monotonic() - started
        if elapsed >= 1.0:
            logging.log("[AddonState] GUI call '{0}' was slow: {1:.1f}s".format(name, elapsed), level=xbmc.LOGINFO)
        return value
    if name not in _hung:
        _hung.add(name)
        logging.log("[AddonState] GUI call '{0}' did not answer within {1:.1f}s: the GUI looks stalled (taking '{2}')".format(
            name, time.monotonic() - started, default), level=xbmc.LOGWARNING)
    return default


def install_dialog_active():
    """Kodi's own modal install progress dialog is up (an InstallAddon / a manual install is running). A GUI that does not answer
    counts as "up": not settled, try later."""
    return bool(_gui('install_dialog', lambda: xbmc.getCondVisibility('Window.IsActive(extendedprogressdialog)'), True))


def updates_announced():
    """Kodi knows about add-on updates it has not installed yet (System.AddonUpdateCount; 0 when the label is unknown). A GUI that
    does not answer counts as "yes": not settled, try later (the caller bounds this with its grace period)."""
    def count():
        try:
            return int(xbmc.getInfoLabel('System.AddonUpdateCount') or 0) > 0
        except ValueError:
            return False
    return bool(_gui('update_count', count, True))


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
    file younger than `quiet` seconds counts. Our own config pack (build_config.zip) is not one. An update Kodi has announced but
    not started shows nothing here (updates_announced asks for that, through the timeout probe)."""
    return (['packages/' + n for n in _recent_files(CONFIG.PACKAGES, quiet, True)] +
            ['temp/' + n for n in _recent_files(os.path.join(CONFIG.ADDONS, 'temp'), quiet, False)])


def own_update_in_flight(quiet=UPDATE_QUIET_SECONDS):
    """Kodi is downloading / installing a new version of the Wizard itself (file-based, no GUI call). That update stops the
    Wizard's service and waits for it, so nothing in the service may wait for the update to finish."""
    prefix = CONFIG.ADDON_ID + '-'
    return [n for n in _recent_files(CONFIG.PACKAGES, quiet, True) if n.startswith(prefix)]


def _manifest_stamp(addon_id):
    """(mtime_ns, size) of the add-on's addon.xml (a replaced add-on has a new one); None when absent. Never raises."""
    try:
        st = os.stat(os.path.join(CONFIG.ADDONS, addon_id, 'addon.xml'))
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def wait_until_settled(ids, timeout=120.0, quiet=4.0, grace=0.0, sleep=1.0, monitor=None):
    """Wait until Kodi is not installing or updating any of `ids`. True when settled; False on timeout, when Kodi is closing, or
    AT ONCE when Kodi is updating the Wizard itself (that update is waiting for the calling service to exit).

    Settled = none of the add-ons' folders changed for `quiet` seconds and no install dialog is up. Within the first `grace`
    seconds an announced-but-not-started update (updates_announced) also counts as "not settled": right after UpdateAddonRepos
    an update starts a few seconds later. An add-on that is not on disk is only watched for appearing. Every GUI-bound question
    goes through a timeout probe (_gui); enter / leave and the reason are logged."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return True
    monitor = monitor or xbmc.Monitor()
    start = last_change = time.monotonic()
    logging.log("[AddonState] wait_until_settled enter: {0} (timeout {1}s, grace {2}s)".format(ids, int(timeout), int(grace)),
                level=xbmc.LOGINFO)

    def leave(result, why):
        logging.log("[AddonState] wait_until_settled leave: {0} ({1}, {2:.1f}s)".format(
            'settled' if result else 'not settled', why, time.monotonic() - start), level=xbmc.LOGINFO)
        return result

    own = CONFIG.ADDON_ID
    own_stamp = _manifest_stamp(own)
    if own_update_in_flight():
        return leave(False, 'Kodi is updating the Wizard itself')
    previous = {i: tree_stamp(i) for i in ids}
    while time.monotonic() - start < timeout:
        if monitor.waitForAbort(sleep):
            return leave(False, 'Kodi is closing')
        if _manifest_stamp(own) != own_stamp or own_update_in_flight():
            return leave(False, 'Kodi is updating the Wizard itself')
        current = {i: tree_stamp(i) for i in ids}
        now = time.monotonic()
        if current != previous or install_dialog_active():
            last_change, previous = now, current
        expecting = (now - start) < grace and updates_announced()
        if now - last_change >= quiet and not expecting:
            return leave(True, 'quiet')
    logging.log("[AddonState] still installing / updating after {0}s: {1}".format(int(timeout), ids), level=xbmc.LOGWARNING)
    return leave(False, 'timeout')
