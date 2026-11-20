"""The ONE place the Wizard lets Kodi install add-on updates: a short, controlled "update window".

Why (20 Nov 2026, Kodi 21.3 log): with `general.addonupdates` = install automatically, Kodi's own check, 3 s after launch, replaced the
running Wizard, a running service and the ACTIVE skin while a dozen services were starting. Stopping a service holds a lock the GUI
thread needs on every frame (up to 5 s of frozen UI), and replacing the active skin needs a blocking round trip to the GUI thread:
the GUI wedged for good. The build now keeps the mode at NOTIFY (Kodi checks and tells, installs nothing). This module opens the
window instead, from the host-repair runner (host_repairs/flow/runner.py, its idle loop calls UpdateWindow.poll()):

  1. GATE: automatic runs only when Home is the active window, nothing plays and no modal dialog is open (one GUI question, asked
     on the window's own worker thread, so a GUI that does not answer can only hold that thread). A manual request (Maintenance ->
     update check) and a resumed run skip the gate.
  2. OPEN: when the ACTIVE skin has a newer version on offer, a Hebrew toast says the screen will go dark for a few seconds (Kodi
     unloads the active skin before it unpacks the new one, and nothing can be drawn until it is loaded again) and the window waits
     SKIN_NOTICE_SECONDS. Then the flag and the park file are raised (update_mode.beat), the window waits PARK_SECONDS so the services
     that poll the GUI are out of every GUI call before Kodi may stop them, the mode goes to ON (update_mode) and UpdateAddonRepos is
     posted. Kodi installs what the repositories offer, itself, exactly as before.
  3. SETTLE: file-based, no GUI: wait until the installs started and nothing was written to addons/packages or addons/temp for
     15 s (addon_state.updates_in_flight), or, when none started, until the check is done plus a short margin.
  4. CLOSE, always (try / finally): the mode goes back (NOTIFY, or the owner's NEVER), the flag is cleared.
  5. AFTER: when the Wizard itself was updated, THIS runner runs old code: it asks the runner to hand over (the new Wizard's runner
     does steps 6 on its own start). Otherwise the config pack is applied now, once the add-ons it writes to are quiet.
  6. CONFIG: the newer config pack (userdata) is applied after the add-ons are in place; a skin it touched reloads once.

Windows open: at the first idle moment 60 s after the runner starts, then every INTERVAL_SECONDS, and on a manual request. The
Maintenance "check for updates" entry (provisioner.run_update_check(background=False)) only requests one.
"""
import re
import threading
import time

import xbmc
import xbmcgui

from resources.libs import config_apply
from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.provisioning import addon_state
from resources.libs.provisioning import update_mode

RUNNER_LOCK = 'kodipovil.host_repairs.running'      # Home-window property of the single live runner (runner.py)
RUNNER_STALE_SECONDS = 120.0

PROP_REQUEST = 'povil.update_window.request'        # a manual check was asked for
PROP_RESUME = 'povil.update_window.resume'          # the Wizard updated itself inside a window: the new runner finishes it
PROP_LAST = 'povil.update_window.last'              # monotonic stamp of the last window that ran

INTERVAL_SECONDS = 6 * 3600.0       # between automatic windows on a device that stays on
MIN_UPTIME_SECONDS = 60.0           # after the runner starts: every service is up before anything is installed
RETRY_SECONDS = 120.0               # an automatic window that found the GUI busy tries again after this
GRACE_SECONDS = 45.0                # nothing started this long after the check was asked for: nothing to install
CHECK_MARGIN_SECONDS = 12.0         # the check finished and nothing started this long after: nothing to install
POLL_SECONDS = 2.0
SKIN_NOTICE_SECONDS = 5.0           # the "the skin is updating" toast is on screen this long before the mode goes to ON
SKIN_NOTICE_TEXT = 'הסקין מתעדכן, המסך יוחשך למספר שניות...'
SITE_INDEX_PATH = '/repo/addons.xml'    # our repository index on the site (repository.povil's <info>)
SITE_INDEX_TIMEOUT = 6.0
SITE_INDEX_LIMIT = 2 * 1024 * 1024
HARD_LIMIT_SECONDS = 20 * 60.0

IDLE_CONDITION = 'Window.IsActive(home) + !Player.HasMedia + !System.HasActiveModalDialog'


def _log(msg, level=xbmc.LOGINFO):
    logging.log('[UpdateWindow] ' + msg, level=level)


def _home():
    return xbmcgui.Window(10000)


def _float_property(name):
    try:
        return float(_home().getProperty(name) or 'x')
    except Exception:
        return None


def runner_alive():
    """True when the host-repair runner (which owns the windows) is alive in this Kodi session."""
    stamp = _float_property(RUNNER_LOCK)
    return stamp is not None and 0 <= time.monotonic() - stamp < RUNNER_STALE_SECONDS


def request_manual():
    """The Maintenance "check for updates": ask the runner for a window. Returns 'queued', 'running' or 'no_runner'."""
    if not runner_alive():
        return 'no_runner'
    if update_mode.window_open():
        return 'running'
    _home().setProperty(PROP_REQUEST, '1')
    return 'queued'


def _site_version(addon_id):
    """The version of an add-on in OUR repository index on the site (the same file Kodi reads), None when it cannot be read. Never raises.
    Fresher than Kodi's own copy (Kodi checks every few hours), so a release published since the last check is seen too."""
    try:
        from urllib.request import Request, ProxyHandler, build_opener
        request = Request(CONFIG.REPO_BASE_URL + SITE_INDEX_PATH, headers={'User-Agent': getattr(CONFIG, 'USER_AGENT', 'Kodi POV IL')})
        with build_opener(ProxyHandler({})).open(request, timeout=SITE_INDEX_TIMEOUT) as response:
            text = response.read(SITE_INDEX_LIMIT).decode('utf-8', 'replace')
        for tag in re.finditer(r'<addon\s[^>]*>', text):
            attrs = dict(re.findall(r'([\w:.-]+)="([^"]*)"', tag.group(0)))
            if attrs.get('id') == addon_id:
                return attrs.get('version') or None
    except Exception as err:
        _log('site index not read: {0}'.format(err))
    return None


def pending_skin_update():
    """(skin id, installed version, offered version) when the ACTIVE skin has a newer version on offer, else None. Never raises.
    No GUI call: the active skin is the setting, the versions come from a file, a small HTTP read and a read-only database query."""
    try:
        skin = xbmc.getSkinDir()
        installed = addon_state.local_version(skin) if skin else None
        if not installed:
            return None
        offered = [v for v in (_site_version(skin), addon_state.offered_version(skin)) if v]
        if not offered:
            return None
        newest = max(offered, key=addon_state.version_key)
        if addon_state.version_key(newest) > addon_state.version_key(installed):
            return skin, installed, newest
    except Exception as err:
        _log('skin update probe failed: {0}'.format(err), xbmc.LOGWARNING)
    return None


class UpdateWindow(object):
    """Owned by the runner. poll() is called every idle tick (cheap: property reads); the work happens on one worker thread."""

    def __init__(self, busy):
        self._busy = busy                 # callable: a clean reinstall / a fresh install is running
        self._born = time.monotonic()
        self._thread = None
        self._retry_at = 0.0
        self._handover = False

    # ---- called by the runner --------------------------------------------------------------------------------------------
    def poll(self, monitor):
        """True when the runner must hand over to a new instance (the Wizard updated itself inside the window)."""
        if self._handover:
            return True
        if self._thread is not None:
            if self._thread.is_alive():
                return False
            self._thread = None
            if self._handover:
                return True
        reason = self._due()
        if reason is None:
            return False
        self._thread = threading.Thread(target=self._run, args=(reason, monitor), name='povil_update_window')
        self._thread.daemon = True
        self._thread.start()
        return False

    def join(self, timeout):
        """At exit: give a window that is closing a moment to put the mode back."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    # ---- when -------------------------------------------------------------------------------------------------------------
    def _due(self):
        try:
            if self._busy():
                return None
            if _float_property(PROP_RESUME) is not None:
                return 'resume'
            if _float_property(PROP_REQUEST) is not None:
                return 'manual'
            now = time.monotonic()
            if now < self._retry_at or now - self._born < MIN_UPTIME_SECONDS:
                return None
            last = _float_property(PROP_LAST)
            if last is not None and 0 <= now - last < INTERVAL_SECONDS:
                return None
            return 'timer'
        except Exception as err:
            _log('due check failed: {0}'.format(err), xbmc.LOGWARNING)
            return None

    # ---- the window (worker thread) ---------------------------------------------------------------------------------------
    def _run(self, reason, monitor):
        try:
            self._window(reason, monitor)
        except Exception as err:
            self._retry_at = time.monotonic() + RETRY_SECONDS      # a failing window must not start again on the next tick
            _log('window failed ({0}): {1}'.format(reason, err), xbmc.LOGERROR)

    def _idle(self):
        return bool(xbmc.getCondVisibility(IDLE_CONDITION))

    def _finish(self, reason, changed, note=True):
        home = _home()
        home.setProperty(PROP_LAST, str(time.monotonic()))
        home.clearProperty(PROP_REQUEST)
        if note and (reason == 'manual' or changed):
            if changed:
                text = 'עודכנו {0} תוספים'.format(len(changed))
            else:
                text = 'הכול מעודכן'
            logging.log_notify(CONFIG.ADDONTITLE, text, times=4000)

    def _window(self, reason, monitor):
        home = _home()
        if reason == 'manual':
            home.clearProperty(PROP_REQUEST)         # taken: a request that arrives meanwhile is served by this very window
        if reason == 'resume':
            home.clearProperty(PROP_RESUME)
            _log('resumed after a Wizard update: applying the config pack')
            self._apply_config()
            self._finish(reason, [])
            return
        if reason == 'timer':
            if update_mode.get_mode() == update_mode.NEVER:
                _log('updates are switched off (Never): next try in {0} h'.format(int(INTERVAL_SECONDS / 3600)))
                home.setProperty(PROP_LAST, str(time.monotonic()))
                return
            if not self._idle():
                self._retry_at = time.monotonic() + RETRY_SECONDS
                _log('the GUI is busy (not Home / playing / a dialog): trying again in {0} s'.format(int(RETRY_SECONDS)))
                return
        outcome = self._announce_skin_update(reason, monitor)
        if outcome != 'go':
            return
        _log('opening ({0})'.format(reason))
        before = addon_state.versions_snapshot()
        own_before = addon_state.local_version(CONFIG.ADDON_ID)
        previous = update_mode.get_mode()
        restore = previous if previous in (update_mode.NOTIFY, update_mode.NEVER) else update_mode.NOTIFY
        outcome = 'not opened'
        update_mode.beat()      # also writes the park file: the GUI-polling services stop calling the GUI
        try:
            if monitor.waitForAbort(update_mode.PARK_SECONDS):
                outcome = 'abort'
            elif update_mode.set_mode(update_mode.ON):
                outcome = self._settle(monitor)
        finally:
            update_mode.set_mode(restore)
            update_mode.end_window()
            _log('closed ({0}); the mode is back to {1}'.format(outcome, restore))
        if outcome == 'abort':
            return
        after = addon_state.versions_snapshot()
        changed = sorted(i for i in after if before.get(i) != after[i])
        if changed:
            _log('installed / updated: {0}'.format(', '.join('{0} {1}'.format(i, after[i]) for i in changed)))
        if addon_state.local_version(CONFIG.ADDON_ID) != own_before:
            # Kodi replaced this very add-on: this runner still runs the OLD code. It must not import more of the new tree; the
            # runner leaves, releases its lock and starts the new instance, which applies the config pack.
            home.setProperty(PROP_RESUME, '1')
            self._handover = True
            _log('the Wizard updated itself ({0} -> {1}): handing over to the new instance'.format(
                own_before, addon_state.local_version(CONFIG.ADDON_ID)))
            return
        self._apply_config()
        self._finish(reason, changed)

    def _announce_skin_update(self, reason, monitor):
        """Before the mode goes to ON: when the active skin is about to be replaced, say so (a toast, then SKIN_NOTICE_SECONDS to read it).
        Kodi unloads the active skin before it unpacks the new one, so the screen is empty until the skin loads again; no Wizard window
        can be drawn meanwhile, the notice can only come first. 'go', 'abort', or 'busy' (an automatic window whose GUI got busy while
        the toast was up: it tries again later)."""
        pending = pending_skin_update()
        if pending is None:
            return 'go'
        _log('the active skin {0} will update {1} -> {2}: announcing'.format(*pending))
        logging.log_notify(CONFIG.ADDONTITLE, SKIN_NOTICE_TEXT, times=int(SKIN_NOTICE_SECONDS * 1000))
        if monitor.waitForAbort(SKIN_NOTICE_SECONDS):
            return 'abort'
        if reason == 'timer' and not self._idle():
            self._retry_at = time.monotonic() + RETRY_SECONDS
            _log('the GUI got busy during the notice: trying again in {0} s'.format(int(RETRY_SECONDS)))
            return 'busy'
        return 'go'

    def _settle(self, monitor):
        """Wait, file-based, until Kodi's installs are done. 'settled' / 'nothing' / 'timeout' / 'abort'."""
        start = time.monotonic()
        stamp = addon_state.repo_check_stamp()
        xbmc.executebuiltin('UpdateAddonRepos')       # posted, never waited for: the GUI thread runs it
        started = False
        checked_at = None
        while True:
            if monitor.waitForAbort(POLL_SECONDS):
                return 'abort'
            update_mode.beat()
            now = time.monotonic()
            active = addon_state.updates_in_flight()
            if active:
                if not started:
                    _log('Kodi started installing: {0}'.format(', '.join(active)))
                started = True
            elif started:
                return 'settled'
            if checked_at is None and stamp is not None:
                current = addon_state.repo_check_stamp()
                if current is not None and current != stamp:
                    checked_at = now
            if not started:
                if checked_at is not None and now - checked_at >= CHECK_MARGIN_SECONDS:
                    return 'nothing'
                if now - start >= GRACE_SECONDS:
                    return 'nothing'
            if now - start >= HARD_LIMIT_SECONDS:
                _log('still installing after {0} min: closing the window'.format(int(HARD_LIMIT_SECONDS / 60)), xbmc.LOGWARNING)
                return 'timeout'

    def _apply_config(self):
        """The newer config pack, after the add-ons it writes to are in place (config_apply waits for them). A pack that cannot be
        applied stays unapplied (config_applied_version), so the next window retries."""
        info = config_apply.load_config_info()
        if not info or not config_apply.config_pending(info):
            return
        result = config_apply.apply_config_pack(info, fresh=False, background=True)
        if result.get('deferred'):
            _log('config {0} deferred: Kodi is still installing an add-on of it; the next window retries'.format(
                info.get('config_version')), xbmc.LOGWARNING)
        elif result.get('skin_touched'):
            xbmc.executebuiltin('ReloadSkin()')
