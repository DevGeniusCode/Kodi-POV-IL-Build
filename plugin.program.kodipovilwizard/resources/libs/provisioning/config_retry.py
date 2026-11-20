"""Hand-off of a deferred config pack from the start-up service to the host-repair runner.

The start-up pass (startup.py, an xbmc.service) must never sit and wait for Kodi's own add-on installs: Kodi stops a service it is
about to replace and force-kills it after 5 s, and on 20 Nov 2026 the GUI went silent while three add-ons updated at start-up. So
when Kodi is downloading / unpacking add-ons (provisioner.run_update_check), the pack is not applied there. A marker file is
written instead (no GUI call: a stalled GUI cannot block it) and the runner (host_repairs/flow/runner.py, its own long-lived
script) calls ConfigRetry.poll() every idle tick. When Kodi has been quiet it runs the ordinary start-up check again, on a worker
thread, which applies the pack. A pack that is not applied stays unapplied (config_applied_version), so the next start retries too.
"""
import os
import threading

import xbmc

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.provisioning import addon_state

MARKER = 'kodipovil.config_retry'


def _path():
    return os.path.join(CONFIG.USERDATA, MARKER)


def request():
    """Ask the runner to apply the config pack as soon as Kodi is quiet."""
    try:
        with open(_path(), 'w') as handle:
            handle.write('1')
    except OSError as err:
        logging.log("[ConfigRetry] cannot write the marker: {0}".format(err), level=xbmc.LOGWARNING)


def clear():
    try:
        os.remove(_path())
    except OSError:
        pass


def requested():
    return os.path.exists(_path())


class ConfigRetry(object):
    """Runner side: one cheap `exists` per poll while nothing is requested."""

    def __init__(self, busy):
        self._busy = busy          # callable: a clean reinstall / a fresh install is running
        self._thread = None

    def poll(self):
        """Start the retry when it is requested and Kodi is quiet. Returns True when a retry was started. Never raises."""
        try:
            if not requested():
                return False
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._busy() or addon_state.updates_in_flight():
                return False
            clear()
            self._thread = threading.Thread(target=self._run, name='povil_config_retry')
            self._thread.daemon = True
            self._thread.start()
            return True
        except Exception as err:
            logging.log("[ConfigRetry] poll failed: {0}".format(err), level=xbmc.LOGWARNING)
            return False

    @staticmethod
    def _run():
        try:
            from resources.libs.provisioning import provisioner
            logging.log("[ConfigRetry] Kodi is quiet: applying the deferred config pack", level=xbmc.LOGINFO)
            provisioner.run_update_check(background=True)
        except Exception as err:
            logging.log("[ConfigRetry] retry failed: {0}".format(err), level=xbmc.LOGWARNING)
