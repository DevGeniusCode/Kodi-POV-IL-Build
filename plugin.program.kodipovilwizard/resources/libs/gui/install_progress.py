# -*- coding: utf-8 -*-
"""The branded install window: what the user sees while the build installs.

File: resources/libs/gui/install_progress.py  (skin: resources/skins/Default/1080i/DialogInstallProgress.xml)

Kodi installs the add-ons itself now (provisioning/provisioner.py), so this window no longer downloads or extracts anything.
It only SHOWS the run:

  * a header and a one-line STAGE ("מתקין תוספים (3/11): POV", "מחיל את הגדרות הבילד", "מסיים: ...");
  * one ROW per step, in order: the two repositories, every add-on, the build settings, the final touches. Each row has a
    status glyph (waiting / installing with the loader animation / installed / already installed / failed), the add-on's
    Hebrew name, a status text and, while it installs, a small progress bar;
  * the OVERALL bar and percentage (0..100), the same monotonic value the top-right banner shows (the shared
    ProvisionProgress tracker), or the share of finished rows when no tracker is given (optional add-on installs).

Kodi's own modal install dialog still opens on top of this window for the length of each native install (that is Kodi: the
`InstallAddon` builtin is modal); this window is what stays on screen between them and gives the whole run its shape.

The state lives in InstallPlan (plain Python, no Kodi): the provisioner reports through the three view methods
`plan(items)`, `item_state(id, state, status)`, `item_progress(id, pct)` and `set_stage(text)`. Worker threads only change that
state; a ticker thread redraws the controls. A window that cannot load never breaks an install: open_window() returns None
and everything carries on with the banner alone.

Control ids per row i: base = 1000 + i * 10 -> base group, +1 loader, +2 glyph, +3 name, +4 status, +5 bar track, +6 bar fill.
Overall: 502 track, 500 fill (width set here), 501 percent label. Window properties: Header, Stage, OverallProgress.
"""
import contextlib
import threading

import xbmc
import xbmcgui

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.common import tools

MAX_ROWS = 16                 # must match DialogInstallProgress.xml
BAR_WIDTH = 620               # px, the row bar track
OVERALL_BAR_ID = 500
OVERALL_LABEL_ID = 501
OVERALL_BAR_WIDTH = 1160      # px, the overall track (id 502)
COLOR_ACTIVE = 'FF43A047'     # green

HEADER_DEFAULT = 'מתקין את הבילד'

PENDING, ACTIVE, DONE, PRESENT, FAILED = 'pending', 'active', 'done', 'present', 'failed'
STATUS_TEXT = {
    PENDING: 'ממתין',
    ACTIVE: 'מתקין...',
    DONE: 'הותקן',
    PRESENT: 'כבר מותקן',
    FAILED: 'נכשל',
}


class InstallPlan(object):
    """The rows, the stage line and the percentage. Thread-safe, no Kodi calls."""

    def __init__(self, tracker=None):
        self._lock = threading.RLock()
        self._items = []
        self._stage = ''
        self._tracker = tracker
        self._finished = False

    # ---- written by the provisioner ----------------------------------------------------------------------------
    def plan(self, items):
        """Replace the rows: items = [(id, display name), ...], every row waiting."""
        with self._lock:
            self._items = [{'id': i, 'name': n, 'state': PENDING, 'status': STATUS_TEXT[PENDING], 'pct': 0}
                           for i, n in items]

    def item_state(self, item_id, state, status=None):
        with self._lock:
            for row in self._items:
                if row['id'] == item_id:
                    row['state'] = state
                    row['status'] = status if status is not None else STATUS_TEXT.get(state, '')
                    row['pct'] = 0 if state != ACTIVE else row['pct']
                    return True
        return False

    def item_progress(self, item_id, pct):
        with self._lock:
            for row in self._items:
                if row['id'] == item_id and row['state'] == ACTIVE:
                    row['pct'] = max(row['pct'], min(100, int(pct)))
                    return

    def set_stage(self, text):
        with self._lock:
            self._stage = text or ''

    def finish(self, ok):
        with self._lock:
            self._finished = bool(ok)

    # ---- read by the window ------------------------------------------------------------------------------------------
    @property
    def stage(self):
        with self._lock:
            return self._stage

    def snapshot(self):
        with self._lock:
            return [dict(r) for r in self._items]

    def all_done(self):
        with self._lock:
            return bool(self._items) and all(r['state'] in (DONE, PRESENT) for r in self._items)

    def percent(self):
        """0..100. The shared tracker when there is one (monotonic, capped at 99 until finish(True)); else finished rows."""
        with self._lock:
            if self._finished:
                return 100
            if self._tracker is not None:
                try:
                    self._tracker.tick()
                    return max(0, min(100, int(self._tracker.percent())))
                except Exception:
                    pass
            if not self._items:
                return 0
            done = sum(1 for r in self._items if r['state'] in (DONE, PRESENT, FAILED))
            return min(99, int(100 * done / float(len(self._items))))

    def visible_rows(self, max_rows=MAX_ROWS):
        """The rows to draw: a sliding window that follows the first unfinished row."""
        rows = self.snapshot()
        for i, row in enumerate(rows):
            if row['state'] in (PENDING, ACTIVE):
                start = max(0, i - max_rows // 2)
                break
        else:
            start = max(0, len(rows) - max_rows)     # everything finished: show the last rows
        if start + max_rows > len(rows):
            start = max(0, len(rows) - max_rows)
        return rows[start:start + max_rows]


class InstallProgressWindow(xbmcgui.WindowXMLDialog):
    """Draws an InstallPlan. Construct through open_window()."""

    def __init__(self, *args, **kwargs):
        self._plan = InstallPlan(kwargs.get('progress'))
        self._header = kwargs.get('header') or HEADER_DEFAULT
        self._monitor = xbmc.Monitor()
        self._stop = threading.Event()
        self._thread = None
        self._closed = False

    # ---- the view protocol (see provisioner._view) ---------------------------------------------------------------------
    def plan(self, items):
        self._plan.plan(items)

    def item_state(self, item_id, state, status=None):
        self._plan.item_state(item_id, state, status)

    def item_progress(self, item_id, pct):
        self._plan.item_progress(item_id, pct)

    def set_stage(self, text):
        self._plan.set_stage(text)

    # ---- drawing ---------------------------------------------------------------------------------------------------------
    def onInit(self):
        try:
            self._refresh()
        except Exception:
            pass

    def _ctrl(self, cid):
        try:
            return self.getControl(cid)
        except Exception:
            return None

    @staticmethod
    def _glyph(state):
        if state in (DONE, PRESENT):
            return '[COLOR FF43A047]✓[/COLOR]'
        if state == FAILED:
            return '[COLOR FFE53935]✗[/COLOR]'
        return '[COLOR 88FFFFFF]⌛[/COLOR]'

    def _draw_overall(self):
        pct = self._plan.percent()
        self.setProperty('OverallProgress', str(pct))
        bar = self._ctrl(OVERALL_BAR_ID)
        if bar is not None:
            try:
                if hasattr(bar, 'setPercent'):
                    bar.setPercent(float(pct))
                else:
                    bar.setWidth(max(1, int(OVERALL_BAR_WIDTH * pct / 100.0)))
            except Exception:
                pass
        label = self._ctrl(OVERALL_LABEL_ID)
        if label is not None:
            try:
                label.setLabel('{0}%'.format(pct))
            except Exception:
                pass

    def _refresh(self):
        self.setProperty('Header', self._header)
        self.setProperty('Stage', self._plan.stage)
        self._draw_overall()
        rows = self._plan.visible_rows(MAX_ROWS)
        for i in range(MAX_ROWS):
            base = 1000 + i * 10
            group = self._ctrl(base)
            if group is None:
                continue
            if i >= len(rows):
                group.setVisible(False)
                continue
            group.setVisible(True)
            row = rows[i]
            active = row['state'] == ACTIVE
            for offset, text in ((3, tools.clean_text(row['name'])), (4, row['status'])):
                c = self._ctrl(base + offset)
                if c is not None:
                    c.setLabel(text)
            anim = self._ctrl(base + 1)
            if anim is not None:
                anim.setVisible(active)
            glyph = self._ctrl(base + 2)
            if glyph is not None:
                glyph.setVisible(not active)
                glyph.setLabel(self._glyph(row['state']))
            track, fill = self._ctrl(base + 5), self._ctrl(base + 6)
            if track is not None:
                track.setVisible(active)
            if fill is not None:
                fill.setVisible(active)
                if active:
                    fill.setColorDiffuse(COLOR_ACTIVE)
                    fill.setWidth(max(1, int(BAR_WIDTH * max(0, min(100, row['pct'])) / 100.0)))

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._refresh()
            except Exception:
                pass
            if self._monitor.waitForAbort(0.3):
                break

    # ---- lifecycle -------------------------------------------------------------------------------------------------------
    def open(self):
        """A fresh install can start before Kodi's home window exists: give the GUI a moment, then show (non-modal)."""
        for _ in range(20):
            if xbmc.getCondVisibility('Window.IsVisible(home)') or self._monitor.waitForAbort(0.5):
                break
        self.show()
        self._thread = threading.Thread(target=self._loop)
        self._thread.daemon = True
        self._thread.start()

    def finish(self, ok=None):
        """Stop the ticker, leave the final state on screen for a moment, close. ok=None: success when every row is done."""
        if self._closed:
            return
        self._closed = True
        if ok is None:
            ok = self._plan.all_done()
        self._plan.finish(ok)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        try:
            self._refresh()
            self._monitor.waitForAbort(1.5 if ok else 0.5)
            self.close()
        except Exception:
            pass


def open_window(progress=None, header=HEADER_DEFAULT):
    """The branded install window, or None when it cannot load (the banner then carries the progress)."""
    try:
        window = InstallProgressWindow('DialogInstallProgress.xml', CONFIG.ADDON_PATH, 'Default',
                                       progress=progress, header=header)
        window.open()
        return window
    except Exception as err:
        logging.log('[InstallProgress] window unavailable ({0}); continuing with the banner'.format(err),
                    level=xbmc.LOGWARNING)
        return None


def close_window(window, ok=None):
    """Close a window from open_window() (None is fine). Never raises."""
    if window is None:
        return
    try:
        window.finish(ok)
    except Exception:
        pass


@contextlib.contextmanager
def window(header=HEADER_DEFAULT, progress=None):
    """with install_progress.window('מתקין את Umbrella') as view: provisioner.provision(ids, view=view)"""
    view = open_window(progress, header)
    try:
        yield view
    finally:
        close_window(view)
