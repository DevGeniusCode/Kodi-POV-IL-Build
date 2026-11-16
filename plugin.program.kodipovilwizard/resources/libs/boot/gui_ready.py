"""Start-up waits: hold boot work while a video plays, then until Home is
live and its rows have settled, so no modal fires before Kodi's GUI exists."""
import sys
import time

import xbmc

from resources.libs.common.config import CONFIG
from resources.libs.common import logging


def stop_if_duplicate():
    NOW = time.time()
    temp = CONFIG.get_setting('time_started')

    if temp:
        if temp > NOW - (60 * 2):
            logging.log('Killing Start Up Script')
            sys.exit()

    logging.log("{0}".format(NOW))
    CONFIG.set_setting('time_started', NOW)
    xbmc.sleep(1000)

    if not CONFIG.get_setting('time_started') == NOW:
        logging.log('Killing Start Up Script')
        sys.exit()
    else:
        logging.log('Continuing Start Up Script')


def check_for_video():
    while xbmc.Player().isPlayingVideo():
        xbmc.sleep(1000)


ROWS_PROPERTY = 'povil.rows.last'


def wait_for_home_rows(monitor, settle=1.5, first=5.0, cap=15.0):
    """Hold start-up work while the home rows load: every row is a Python
    interpreter of its own, and the build's services starting beside them
    stretched the last home row by ~0.8 s on a PC (QA, NOX) -- more on a
    phone. POV's saved lists stamp ROWS_PROPERTY when they serve a row; go
    on once none arrived for `settle` seconds (or none at all within `first`
    seconds of Home), and never wait more than `cap` seconds."""
    import time
    start = time.time()
    home_at = None
    try:
        import xbmc, xbmcgui
        win = xbmcgui.Window(10000)
    except Exception:
        return
    while time.time() - start < cap:
        now = time.time()
        if home_at is None and xbmc.getCondVisibility('Window.IsVisible(home)'):
            home_at = now
        if home_at is not None:
            try:
                last = float(win.getProperty(ROWS_PROPERTY) or 0)
            except ValueError:
                last = 0.0
            if last >= home_at - 1.0:
                if now - last >= settle:
                    break
            elif now - home_at >= first:
                break
        if monitor.waitForAbort(0.25):
            return
    # An episode picked meanwhile is looking for sources: it gets the CPU first.
    end = time.time() + 30.0
    while time.time() < end and _play_starting(win):
        if monitor.waitForAbort(0.25):
            return


def _play_starting(win):
    import time
    try:
        started = float(win.getProperty('povil.play.started') or 0)
    except ValueError:
        return False
    return 0 <= time.time() - started < 60.0


def wait_for_gui_ready(timeout=90):
    """This script is an xbmc.service with start="startup", so it runs
    before Kodi's GUI/Home window exists. Showing a modal dialog
    (doModal) that early deadlocks Kodi: it loads for a few seconds and
    then hangs, and only a force-stop recovers -- after which the
    one-shot dismiss flags are already set, so the next launch is fine.
    That exactly matches the "hang once after every install/quick
    update" symptom. Wait for the Home window to be live before any
    first-launch dialog. Bounded by a timeout so we never wait forever
    (e.g. headless/odd boots); returns True only if Home actually came
    up."""
    logging.log('[GUI Ready] entering Home wait', level=xbmc.LOGINFO)
    try:
        monitor = xbmc.Monitor()
        waited = 0
        while waited < timeout:
            home_visible = xbmc.getCondVisibility('Window.IsVisible(home)')
            if home_visible:
                # Home is already visible. A timed Kodi sleep here can be
                # starved for tens of seconds while AF3 renders widgets;
                # advancing lets the background update run immediately.
                logging.log('[GUI Ready] Home visible after {0}s'.format(waited),
                            level=xbmc.LOGINFO)
                rows_wait = time.time()
                wait_for_home_rows(monitor)
                if monitor.abortRequested():
                    return False
                logging.log('[GUI Ready] home rows settled after {0:.1f}s'.format(
                    time.time() - rows_wait), level=xbmc.LOGINFO)
                return True
            if monitor.waitForAbort(1):
                return False
            waited += 1
        logging.log(
            '[GUI Ready] Home window not visible after {0}s; '
            'continuing without the wait.'.format(timeout),
            level=xbmc.LOGWARNING)
        return False
    except Exception as gui_err:
        logging.log('[GUI Ready] wait failed: {0}'.format(gui_err),
                    level=xbmc.LOGERROR)
        return False
