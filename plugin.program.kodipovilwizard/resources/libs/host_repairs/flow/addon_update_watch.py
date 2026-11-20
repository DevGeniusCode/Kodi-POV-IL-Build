"""Re-run the runtime repairs the moment Kodi installs or updates an add-on we patch, while Kodi is running.

An update (Kodi's own updater, from repository.povil / repository.povil.upstream) replaces the add-on's files: every registry patch
and runtime edit disappears. For POV addon.xml also goes back to <reuselanguageinvoker>true</reuselanguageinvoker>, and POV's own
reuseLanguageInvokerCheck can ask its English "SETTING/XML mismatch" question before the next Kodi start repairs anything. A device
that stayed on for days used pristine add-ons in between.

The runner's idle loop calls AddonUpdateWatch.poll() every POLL_SECONDS. The WATCHED add-ons are HOSTS below: every add-on the
PatchEngine registry patches (POV, Umbrella, Idan Plus, Arctic Fuse 3) with the runtime repairs that belong to it. Per host:

  1. DETECT: one cheap stat per poll, the stamp (version, mtime) of its addon.xml. A first install (None -> stamp) counts.
  2. SETTLE (never patch a half-unpacked add-on): after a change, wait until the add-on's whole folder (file count, bytes, newest
     mtime) and its addon.xml stay identical for SETTLE_SECONDS in a row AND Kodi's install dialog is not up. If that does not
     happen within SETTLE_LIMIT_SECONDS the host is left for the next poll; its change is not forgotten.
  3. REPAIR under one lock (the repairs are idempotent, additive and marker-fenced): the PatchEngine for that add-on, then its
     runtime patchers. The repairs' own writes (the POV guard rewrites addon.xml) are stamped afterwards, so they never count as
     an update.

Skins are watched too (every skin of skin_catalog). They carry no patch of ours; the repair is skin_gui_guard.recover: Kodi
unloads the ACTIVE skin to update it, and on 19 Nov 2026 it never loaded it again (frozen GUI). If the GUI has no window some
seconds after the update settled, ReloadSkin() (and ActivateWindow(home)) are posted. The AF3 repair does the same first.

It stands down while the clean reinstall is wiping Kodi, while a fresh install is still provisioning (its finalize step runs the
whole PatchEngine itself) and, for POV, while POV patching is switched off.
"""
import os
import re
import threading
import time

from resources.libs.common import gui_probe
from resources.libs.host_repairs.flow import skin_gui_guard
from resources.libs.host_repairs.flow.pov_steps import _skip_pov_patchers
from resources.libs.host_repairs.flow.startup_pass import _maybe_patch_pov_language_invoker

POLL_SECONDS = 8
SETTLE_SECONDS = 2.0
SETTLE_LIMIT_SECONDS = 60.0
TREE_FILE_LIMIT = 20000

_VERSION_RE = re.compile(br'<addon\b[^>]*?\sversion="([^"]*)"')
_REPAIR_LOCK = threading.Lock()


def _log(msg, level=None):
    try:
        from resources.libs.host_repairs import kodi_utils
        kodi_utils.log('addon_update_watch: ' + msg, level=level or 'INFO')
    except Exception:
        pass


def _addon_dir(addon_id):
    import xbmcvfs
    return xbmcvfs.translatePath('special://home/addons/' + addon_id + '/')


def _stamp(addon_id):
    """(version, mtime_ns) of the add-on's addon.xml; None when it is not on disk (or mid-replacement). Never raises."""
    try:
        path = os.path.join(_addon_dir(addon_id), 'addon.xml')
        mtime = os.stat(path).st_mtime_ns
        with open(path, 'rb') as fh:
            found = _VERSION_RE.search(fh.read(4096))
        return (found.group(1).decode('utf-8', 'replace') if found else '', mtime)
    except Exception:
        return None


def _tree_stamp(addon_id):
    """(file count, total bytes, newest mtime_ns) of the whole add-on folder; None when absent. Only read after a change."""
    try:
        count = size = newest = 0
        for dirpath, _dirs, files in os.walk(_addon_dir(addon_id)):
            for name in files:
                try:
                    st = os.stat(os.path.join(dirpath, name))
                except OSError:
                    continue
                count += 1
                size += st.st_size
                newest = max(newest, st.st_mtime_ns)
                if count >= TREE_FILE_LIMIT:
                    return (count, size, newest)
        return (count, size, newest)
    except Exception:
        return None


def _kodi_installing():
    try:
        import xbmc
        # asked with a timeout: while Kodi swaps the active skin the GUI may not answer at all, and the poll loop must survive that
        answered, value = gui_probe.ask(lambda: xbmc.getCondVisibility('Window.IsActive(extendedprogressdialog)'), key='install_dialog')
        return bool(value) if answered else False
    except Exception:
        return False


def _busy():
    """The clean reinstall is wiping Kodi, or a fresh install is provisioning: never patch in the middle of either."""
    try:
        import xbmcgui
        import xbmcvfs
        from resources.libs.provisioning.clean_reinstall import BUSY_PROPERTY
        if xbmcgui.Window(10000).getProperty(BUSY_PROPERTY):
            return True
        return os.path.exists(os.path.join(xbmcvfs.translatePath('special://profile/'), 'kodipovil.modular_install_started'))
    except Exception:
        return False


# ---- the repairs, one per watched add-on ---------------------------------------------------------------------------------
def _engine(addon_id):
    from resources.libs.patch_engine import PatchEngine
    return PatchEngine().run(addon_id=addon_id)


def _repair_pov():
    # The invoker guard first: POV's own check races it. Then every POV patch.
    _maybe_patch_pov_language_invoker()
    return _engine('plugin.video.pov')


def _repair_umbrella():
    stats = _engine('plugin.video.umbrella')
    from resources.libs.host_repairs.flow.umbrella_steps import _maybe_patch_umbrella
    _maybe_patch_umbrella()          # Hebrew menu strings, token mirrors, the two runtime source edits
    return stats


def _repair_idanplus():
    return _engine('plugin.video.idanplus')


def _repair_af3():
    skin_gui_guard.recover('skin.arctic.fuse.3')   # Kodi may have left the GUI without a skin after updating it
    stats = _engine('skin.arctic.fuse.3')
    from resources.libs.host_repairs.flow.af3_steps import (
        _maybe_patch_af3_dialog_subtitles, _maybe_patch_af3_home, _maybe_patch_hub_search)
    _maybe_patch_af3_dialog_subtitles()
    _maybe_patch_hub_search()
    _maybe_patch_af3_home()
    # Kodi reloaded the skin when it updated it, before our edits existed: show them.
    try:
        import xbmc
        if xbmc.getSkinDir() == 'skin.arctic.fuse.3' and (stats.get('applied', 0) or stats.get('upgraded', 0)):
            xbmc.executebuiltin('ReloadSkin()')
    except Exception:
        pass
    return stats


# (add-on id, repair, optional "stand down" predicate)
HOSTS = (
    ('plugin.video.pov', _repair_pov, _skip_pov_patchers),       # off when POV patching is switched off
    ('plugin.video.umbrella', _repair_umbrella, None),
    ('plugin.video.idanplus', _repair_idanplus, None),
    ('skin.arctic.fuse.3', _repair_af3, None),
)


def _skin_guard(skin_id):
    def repair():
        return skin_gui_guard.recover(skin_id)
    return repair


def _hosts():
    """HOSTS plus every other skin of the catalog (their repair is only the GUI guard)."""
    try:
        from resources.libs.skins import skin_catalog
        known = set(host[0] for host in HOSTS)
        return HOSTS + tuple((sid, _skin_guard(sid), None) for sid in skin_catalog.ids() if sid not in known)
    except Exception:
        return HOSTS


class AddonUpdateWatch(object):
    def __init__(self):
        self._hosts = _hosts()
        self._seen = {addon_id: _stamp(addon_id) for addon_id, _repair, _skip in self._hosts}

    def _settle(self, addon_id, monitor):
        """Wait until the add-on's folder and addon.xml stop changing and Kodi is not installing. Returns (settled, addon.xml stamp)."""
        deadline = time.monotonic() + SETTLE_LIMIT_SECONDS
        last = (_stamp(addon_id), _tree_stamp(addon_id))
        while time.monotonic() < deadline:
            if monitor.waitForAbort(SETTLE_SECONDS):
                return False, last[0]
            now = (_stamp(addon_id), _tree_stamp(addon_id))
            if now == last and not _kodi_installing():
                return True, now[0]
            last = now
        return False, last[0]

    def poll(self, monitor):
        """One cheap stat per host per call. Returns the ids repaired this call. Never raises."""
        repaired = []
        try:
            if _busy():
                return repaired
            for addon_id, repair, skip in self._hosts:
                if monitor.abortRequested():
                    break
                if _stamp(addon_id) == self._seen.get(addon_id):
                    continue
                settled, now = self._settle(addon_id, monitor)
                if not settled:
                    continue            # still unpacking (or Kodi is closing): the change stays pending for the next poll
                previous, self._seen[addon_id] = self._seen.get(addon_id), now
                if now is None:
                    continue            # the add-on is gone (a later reinstall is a change again)
                if skip is not None and skip():
                    continue            # patching of this add-on is switched off
                _log('{0} changed {1} -> {2}'.format(addon_id, previous, now))
                try:
                    with _REPAIR_LOCK:
                        stats = repair()
                    _log('{0} repaired: {1}'.format(addon_id, stats))
                    repaired.append(addon_id)
                except Exception as err:
                    _log('{0} repair failed: {1}'.format(addon_id, err), level='WARNING')
                self._seen[addon_id] = _stamp(addon_id)   # the repair's own writes are not an update
        except Exception as err:
            _log('poll failed: {0}'.format(err), level='WARNING')
        return repaired
