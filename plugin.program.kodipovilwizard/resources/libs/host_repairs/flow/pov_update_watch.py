"""Re-run the POV repairs the moment POV updates itself while Kodi is running.

A POV self-update (repository.kodifitzwell) replaces the add-on's files: every registry patch disappears and addon.xml
goes back to <reuselanguageinvoker>true</reuselanguageinvoker>. Until now the build noticed at the next Kodi start (the
boot pass) or after a manual run, so a device that stayed on for days used pristine POV in between, and POV's own
reuseLanguageInvokerCheck could ask its English "SETTING/XML mismatch" question first.

The runner's idle loop calls PovUpdateWatch.poll() every POLL_SECONDS. It compares POV's addon.xml stamp (version,
mtime); on a change it waits until the stamp is stable (Kodi extracts a zip over several seconds), then runs the
clock-bound invoker guard first and the PatchEngine for POV after it. It stands down when POV patching is switched off.
The repairs' own writes (the guard rewrites addon.xml) are stamped afterwards, so they never count as an update.
"""
import os
import re
import time

from resources.libs.host_repairs.flow.pov_steps import _skip_pov_patchers
from resources.libs.host_repairs.flow.startup_pass import _maybe_patch_pov_language_invoker

POV_ADDON_ID = 'plugin.video.pov'
POLL_SECONDS = 8
SETTLE_SECONDS = 2.0
SETTLE_LIMIT_SECONDS = 30.0

_VERSION_RE = re.compile(br'<addon\b[^>]*?\sversion="([^"]*)"')


def _log(msg, level=None):
    try:
        from resources.libs.host_repairs import kodi_utils
        kodi_utils.log('pov_update_watch: ' + msg, level=level or 'INFO')
    except Exception:
        pass


def _stamp():
    """(version, mtime_ns) of POV's addon.xml; None when POV is not on disk (or mid-replacement). Never raises."""
    try:
        import xbmcvfs
        path = os.path.join(xbmcvfs.translatePath('special://home/addons/' + POV_ADDON_ID + '/'), 'addon.xml')
        mtime = os.stat(path).st_mtime_ns
        with open(path, 'rb') as fh:
            found = _VERSION_RE.search(fh.read(4096))
        return (found.group(1).decode('utf-8', 'replace') if found else '', mtime)
    except Exception:
        return None


def _busy():
    try:
        import xbmcgui
        from resources.libs.provisioning.clean_reinstall import BUSY_PROPERTY
        return bool(xbmcgui.Window(10000).getProperty(BUSY_PROPERTY))
    except Exception:
        return False


def _repair():
    # The invoker guard first: POV's own check races it. Then every POV patch (idempotent, additive, marker-fenced).
    _maybe_patch_pov_language_invoker()
    from resources.libs.patch_engine import PatchEngine
    stats = PatchEngine().run(addon_id=POV_ADDON_ID)
    _log('POV update repaired: {0}'.format(stats))


class PovUpdateWatch(object):
    def __init__(self):
        self._seen = _stamp()

    def poll(self, monitor):
        """One cheap stat per call. Returns True when a POV update was found and repaired. Never raises."""
        try:
            if _busy():
                return False  # the clean reinstall is wiping Kodi: never patch a half-deleted POV
            now = _stamp()
            if now == self._seen:
                return False
            deadline = time.monotonic() + SETTLE_LIMIT_SECONDS
            while time.monotonic() < deadline:
                if monitor.waitForAbort(SETTLE_SECONDS):
                    return False
                again = _stamp()
                if again == now and again is not None:
                    break
                now = again
            previous, self._seen = self._seen, now
            if now is None or _skip_pov_patchers():
                return False  # POV is gone (a later reinstall is a change again) / patching is switched off
            _log('POV addon.xml changed {0} -> {1}'.format(previous, now))
            _repair()
            self._seen = _stamp()
            return True
        except Exception as err:
            _log('poll failed: {0}'.format(err), level='WARNING')
            return False
