# -*- coding: utf-8 -*-
"""Unified UI Install Manager.
File: resources/libs/gui/install_manager.py

A custom WindowXMLDialog that visualises the continuous installation flow for both
Phase 1 (Manifest/internal addons) and Phase 2 (Headless/3rd-party content addons).

Key architectural features:
  * Dynamic Queue: Supports injecting new installation jobs on the fly via `append_to_queue()`
    without closing or reopening the dialog window.
  * Phase Transition: Implements a virtual 'resolver' state to display a "Calculating dependencies..."
    pause while the Orchestrator resolves 3rd-party XMLs in the background.
  * Resilient Downloader: Downloads run in parallel and gracefully skip SHA256 verification
    if the hash is omitted (required for upstream 3rd-party repositories).
  * Safe Extraction: Installs (extracts) run strictly one-at-a-time, in download-completion order.
  * Thread Safety: Worker threads ONLY mutate a plain-Python state list (under a lock). The
    window's own thread is the only one touching Kodi controls via _refresh(), allowing
    infinite scrolling/sliding window views.
  * Shared Progress: every state change made by the download/extract workers is folded into
    a shared ProvisionProgress tracker, per PHASE (so injecting Phase 2 jobs later can never
    dilute Phase 1's share). The window's overall bar READS the tracker; the startup banner is
    written by the tracker's own ticker. Workers never touch the banner or any control.
  * No native dialogs: download/extract use the dialog-free primitives from
    headless_installer (download_zip / extract_zip). Nothing here ever calls InstallAddon.

Skin: resources/skins/Default/1080i/DialogModularInstall.xml. Control ids per
row i: base = 1000 + i*10 -> base(group) +1 anim icon, +2 glyph, +3 name,
+4 status, +5 progress track, +6 progress fill.
Overall progress: 502 = track (image), 500 = fill (image, width set from Python),
501 = percent label. Header 200 / subheader 201 are static text.
"""

import os
import threading

try:
    import queue as _queue
except ImportError:  # pragma: no cover - py2 safety, Kodi 19+ is py3
    import Queue as _queue

import xbmc
import xbmcgui

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs import config_apply
from resources.libs.headless_installer import download_zip as _download
from resources.libs.headless_installer import extract_zip as _extract

# A missing / broken progress helper must never knock the window out and push the
# installer onto a fallback path: degrade to "no overall percentage" instead.
try:
    from resources.libs.provision_progress import ProvisionProgress
except Exception:  # pragma: no cover
    ProvisionProgress = None

MAX_ROWS = 16                 # must match the generated DialogModularInstall.xml
BAR_WIDTH = 620               # px, must match the XML progress track width
MAX_PARALLEL_DOWNLOADS = 3

# Overall progress controls in DialogModularInstall.xml (the ones that drive the
# same 0..99 value as the top-right banner):
#   502 = track  (image, static)
#   500 = fill   (image; its WIDTH is set here, 0..OVERALL_BAR_WIDTH)
#   501 = label  (text, "NN%")
OVERALL_BAR_ID = 500
OVERALL_LABEL_ID = 501
OVERALL_BAR_WIDTH = 1160      # px, must match the XML track (id 502) width

COLOR_DOWNLOAD = 'FF1E88E5'   # blue
COLOR_INSTALL = 'FF43A047'    # green


class _NullProgress(object):
    """Stand-in when provision_progress.py is unavailable."""
    def set_fraction(self, *a, **k): pass
    def tick(self): pass
    def percent(self): return 0
    def publish(self): return 0


class ModularInstallDialog(xbmcgui.WindowXMLDialog):
    """Drives the parallel-download / sequential-install pipeline + the window."""

    def __init__(self, *args, **kwargs):
        self.jobs = []
        self._lock = threading.Lock()
        self._installed = []
        self._abort = False
        self._done = False
        self._queue_closed = False
        self._monitor = xbmc.Monitor()

        self._dl_queue = _queue.Queue()
        self._install_q = _queue.Queue()
        self._dl_sem = threading.Semaphore(MAX_PARALLEL_DOWNLOADS)
        self._dummy_pause_idx = None
        self._orchestrator_func = kwargs.get('orchestrator_func')

        tracker = kwargs.get('progress')
        if tracker is None:
            try:
                tracker = ProvisionProgress() if ProvisionProgress is not None else _NullProgress()
            except Exception:
                tracker = _NullProgress()
        self._progress = tracker

    def append_to_queue(self, new_items, phase='manifest'):
        with self._lock:
            start_idx = len(self.jobs)
            for i, j in enumerate(new_items):
                j = dict(j)
                j.setdefault('state', 'pending')
                j.setdefault('status', 'ממתין...')
                j.setdefault('progress', 0)
                j.setdefault('phase', phase)
                self.jobs.append(j)
                self._dl_queue.put(start_idx + i)
            self._sync_progress_locked()

    def mark_all_jobs_added(self):
        # Poison pills to gracefully exit the workers once queue is empty.
        # Idempotent: the orchestrator calls it, and run() calls it again as a
        # safety net if the orchestrator died before getting there.
        with self._lock:
            if self._queue_closed:
                return
            self._queue_closed = True
        for _ in range(MAX_PARALLEL_DOWNLOADS):
            self._dl_queue.put(-1)

    def wait_for_queue_empty(self):
        while not (self._abort or self._monitor.abortRequested()):
            with self._lock:
                # 'downloaded' = downloaded, still waiting for the extractor. It
                # must count as active, otherwise the orchestrator can move on to
                # Phase 2 before the last Phase 1 addons (e.g. the repositories the
                # resolver reads) have actually been extracted.
                active = any(j.get('state') in ('pending', 'downloading', 'downloaded', 'installing')
                             for j in self.jobs if j.get('id') != 'resolver')
            if not active:
                break
            self._monitor.waitForAbort(0.5)

    def get_installed(self):
        with self._lock:
            return list(self._installed)

    def pause_for_resolution(self):
        with self._lock:
            self._dummy_pause_idx = len(self.jobs)
            self.jobs.append({
                'id': 'resolver',
                'name': 'מחשב תלויות תוכן...',
                'state': 'downloading',
                'status': 'מנתח מאגרים...',
                'progress': 100,
                'type': 'system',
                'phase': 'resolve',
            })

    def remove_resolution_pause(self):
        with self._lock:
            if self._dummy_pause_idx is not None and self._dummy_pause_idx < len(self.jobs):
                self.jobs[self._dummy_pause_idx]['state'] = 'success'
                self.jobs[self._dummy_pause_idx]['status'] = 'הושלם'

    def _set(self, idx, **kw):
        with self._lock:
            self.jobs[idx].update(kw)
            self._sync_progress_locked()

    def _snapshot(self):
        with self._lock:
            return [dict(j) for j in self.jobs]

    # ---- progress folding (called by workers; pure state, no GUI) -------- #
    @staticmethod
    def _job_weight(job):
        """0..100 progress for one job: download = first half, extract = second half."""
        state = job.get('state', 'pending')
        if state in ('success', 'failed'):
            return 100.0
        try:
            pct = max(0, min(100, int(job.get('progress', 0) or 0)))
        except (TypeError, ValueError):
            pct = 0
        if state == 'downloading':
            return pct / 2.0
        if state == 'downloaded':
            return 50.0
        if state == 'installing':
            return 50.0 + pct / 2.0
        return 0.0

    def _sync_progress_locked(self):
        """Fold the job list into the shared tracker BY PHASE. The caller holds
        self._lock. A phase's own job count is fixed once its jobs are injected,
        so later phases can never dilute (or reverse) what an earlier one earned.
        The 'resolver' pseudo-job is skipped: it is a UI placeholder, represented
        by the 'resolve' phase's own indeterminate creep in the tracker."""
        totals = {}
        for j in self.jobs:
            if j.get('id') == 'resolver':
                continue
            phase = j.get('phase', 'manifest')
            weight, count = totals.get(phase, (0.0, 0))
            totals[phase] = (weight + self._job_weight(j), count + 1)
        for phase, (weight, count) in totals.items():
            if count:
                try:
                    self._progress.set_fraction(phase, weight / (count * 100.0))
                except Exception:
                    pass

    # ---- rendering (GUI thread only) ------------------------------------ #
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
        if state == 'success':
            return '[COLOR FF43A047]✓[/COLOR]'   # green check
        if state == 'failed':
            return '[COLOR FFE53935]✗[/COLOR]'   # red cross
        if state == 'downloaded':
            return '[COLOR FFFFFFFF]⌛[/COLOR]'   # hourglass (static)
        return '[COLOR 88FFFFFF]⌛[/COLOR]'       # pending hourglass (dim)

    @staticmethod
    def _set_overall_bar(ctrl, pct):
        """Draw pct (0..100) on the overall bar control. Works for either skin
        style: a real ControlProgress (setPercent) or, as shipped in the XML, an
        image whose width is the fill (same technique as the per-row bars)."""
        if hasattr(ctrl, 'setPercent'):
            ctrl.setPercent(float(pct))
        else:
            ctrl.setWidth(max(1, int(OVERALL_BAR_WIDTH * pct / 100.0)))

    def _draw_overall(self):
        """Overall bar + label: READ from the shared tracker (monotonic 0..99),
        the same value the startup banner shows."""
        try:
            self._progress.tick()
            pct = max(0, min(100, int(self._progress.percent())))
        except Exception:
            return
        self.setProperty('OverallProgress', str(pct))   # $INFO[Window.Property(OverallProgress)]
        bar_ctrl = self._ctrl(OVERALL_BAR_ID)
        if bar_ctrl is not None:
            try:
                self._set_overall_bar(bar_ctrl, pct)
            except Exception:
                pass
        label_ctrl = self._ctrl(OVERALL_LABEL_ID)
        if label_ctrl is not None:
            try:
                label_ctrl.setLabel("{0}%".format(pct))
            except Exception:
                pass

    def _refresh(self):
        jobs = self._snapshot()

        self._draw_overall()

        # Sliding window: keep the active view locked to the first executing job
        first_active = 0
        for i, j in enumerate(jobs):
            if j.get('state') in ('pending', 'downloading', 'installing'):
                first_active = i
                break
        else:
            if jobs:
                first_active = max(0, len(jobs) - MAX_ROWS)

        start_idx = max(0, first_active - (MAX_ROWS // 2))
        if start_idx + MAX_ROWS > len(jobs):
            start_idx = max(0, len(jobs) - MAX_ROWS)

        visible_jobs = jobs[start_idx : start_idx + MAX_ROWS]

        for i in range(MAX_ROWS):
            base = 1000 + i * 10
            group = self._ctrl(base)
            if group is None: continue
            if i >= len(visible_jobs):
                group.setVisible(False)
                continue

            group.setVisible(True)
            job = visible_jobs[i]
            state = job.get('state', 'pending')
            active = state in ('downloading', 'installing')

            name_c = self._ctrl(base + 3)
            if name_c is not None:
                name_c.setLabel(tools.clean_text(job.get('name', '')))
            status_c = self._ctrl(base + 4)
            if status_c is not None:
                status_c.setLabel(job.get('status', ''))

            anim = self._ctrl(base + 1)
            if anim is not None:
                anim.setVisible(active)
            glyph = self._ctrl(base + 2)
            if glyph is not None:
                glyph.setVisible(not active)
                glyph.setLabel(self._glyph(state))

            track = self._ctrl(base + 5)
            fill = self._ctrl(base + 6)
            if track is not None:
                track.setVisible(active)
            if fill is not None:
                fill.setVisible(active)
                if active:
                    fill.setColorDiffuse(COLOR_DOWNLOAD if state == 'downloading' else COLOR_INSTALL)
                    pct = max(0, min(100, int(job.get('progress', 0))))
                    fill.setWidth(max(1, int(BAR_WIDTH * pct / 100.0)))

    # ---- orchestration -------------------------------------------------- #
    def _wait_for_gui(self, timeout=10):
        """A fresh install can start before Kodi's home window exists. Give the
        GUI a moment so the dialog actually renders; proceed regardless after
        the timeout (installs still run, just without visuals)."""
        for _ in range(int(timeout / 0.5)):
            if xbmc.getCondVisibility('Window.IsVisible(home)'): return
            if self._monitor.waitForAbort(0.5): return

    def download_worker(self):
        while not (self._abort or self._monitor.abortRequested()):
            try:
                idx = self._dl_queue.get(timeout=0.5)
            except _queue.Empty:
                continue

            if idx == -1:
                self._install_q.put(-1)
                break

            with self._dl_sem:
                if self._abort or self._monitor.abortRequested():
                    self._install_q.put(idx)
                    return

                job = self.jobs[idx]
                if job.get('id') == 'resolver':
                    self._install_q.put(idx)
                    continue

                self._set(idx, state='downloading', status='מוריד...', progress=0)
                dest = os.path.join(CONFIG.PACKAGES, '{0}_install.zip'.format(job['id']))
                tools.remove_file(dest)

                def on_dl(pct):
                    self._set(idx, progress=pct, status='מוריד... {0}%'.format(pct))

                ok = _download(job['zip'], dest, on_dl, lambda: self._abort or self._monitor.abortRequested())

                # SHA256 Bomb Fix: Missing 'sha256' gracefully evaluates to None and gets bypassed
                want = job.get('sha256')
                if ok and want:
                    try:
                        got = config_apply.sha256_file(dest)
                        if got.lower() != str(want).lower():
                            logging.log("[InstallManager] sha256 mismatch for {0}".format(job['id']),
                                        level=xbmc.LOGERROR)
                            ok = False
                    except Exception:
                        ok = False

                if ok:
                    self._set(idx, state='downloaded', status='הורד, ממתין להתקנה...', progress=100)
                else:
                    self._set(idx, state='failed', status='הורדה נכשלה')
                self._install_q.put(idx)

    def installer(self):
        poisons = 0
        while not (self._abort or self._monitor.abortRequested()):
            try:
                idx = self._install_q.get(timeout=0.5)
            except _queue.Empty:
                continue

            if idx == -1:
                poisons += 1
                if poisons == MAX_PARALLEL_DOWNLOADS:
                    self._done = True
                continue

            job = self.jobs[idx]
            if job.get('id') == 'resolver' or job.get('state') == 'failed':
                continue

            self._set(idx, state='installing', status='מתקין...', progress=0)
            dest = os.path.join(CONFIG.PACKAGES, '{0}_install.zip'.format(job['id']))

            def on_ex(cur, tot):
                self._set(idx, progress=int(cur * 100 / tot),
                          status='מתקין... ({0} מתוך {1} קבצים)'.format(cur, tot))

            ok = _extract(dest, CONFIG.ADDONS, on_ex)
            tools.remove_file(dest)
            if ok:
                self._set(idx, state='success', status='הותקן בהצלחה', progress=100)
                self._installed.append(job['id'])
            else:
                self._set(idx, state='failed', status='ההתקנה נכשלה')

    def _run_orchestrator(self):
        """Run the caller's orchestrator; if it dies, still close the queue so
        the workers can exit and the window can never hang forever."""
        try:
            self._orchestrator_func(self)
        except Exception as e:
            logging.log("[InstallManager] orchestrator error: {0}".format(e), level=xbmc.LOGERROR)
        finally:
            self.mark_all_jobs_added()

    def run(self):
        self._wait_for_gui()
        self.show()
        self._monitor.waitForAbort(0.2)

        for _ in range(MAX_PARALLEL_DOWNLOADS):
            t = threading.Thread(target=self.download_worker)
            t.daemon = True
            t.start()

        inst_thread = threading.Thread(target=self.installer)
        inst_thread.daemon = True
        inst_thread.start()

        if self._orchestrator_func:
            orch_thread = threading.Thread(target=self._run_orchestrator)
            orch_thread.daemon = True
            orch_thread.start()
        else:
            self.mark_all_jobs_added()

        # UI refresh loop (this, the window's own thread)
        while not self._done:
            self._refresh()
            if self._monitor.waitForAbort(0.2):
                self._abort = True
                break

        self._refresh()
        # let the final success/failed states settle on screen briefly
        self._monitor.waitForAbort(1.5)
        try:
            self.close()
        except Exception:
            pass
        return list(self._installed)


def run_install_manager(orchestrator_func=None, progress=None):
    """Entry point. Returns the list of addon ids that installed successfully.
    Raises on a window/load failure so the caller can use its (dialog-free)
    fallback.

    `progress` is an optional shared ProvisionProgress; when given, the window's
    overall bar reads it and the workers' job state is folded into it."""
    dialog = ModularInstallDialog(
        'DialogModularInstall.xml', CONFIG.ADDON_PATH, 'Default',
        orchestrator_func=orchestrator_func, progress=progress)
    try:
        return dialog.run()
    finally:
        del dialog