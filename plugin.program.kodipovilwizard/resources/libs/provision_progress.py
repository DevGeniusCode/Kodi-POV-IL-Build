# -*- coding: utf-8 -*-
"""Shared, monotonic, thread-safe progress model for the fresh-install run.

Install it at: resources/libs/provision_progress.py

This module is PURELY a progress model plus the (single) writer of the top
banner. It never installs anything and never opens a dialog of its own:

  * install_manager (the DialogModularInstall window) and ModularUpdater only
    REPORT numbers into it (set_fraction / complete / begin_indeterminate).
  * The banner (a DialogProgressBG created by startup.py) is written ONLY from
    this module's ticker thread, so there is exactly one banner writer.
  * The custom window READS percent() to draw its own overall bar, so the
    window and the banner can never disagree.

Phase weights are reserved up-front, so a phase whose size is not yet known
(content addons are only discovered after dependency resolution) cannot make
the overall percentage jump backwards. Phases without measurable progress
"creep" asymptotically toward CREEP_CAP of their slice. The value is
monotonic and capped at BANNER_CAP (99) so the banner can never auto-complete
and close before Kodi's force-close.
"""
import threading
import time

import xbmc

from resources.libs.common import logging

# (name, relative weight) -- in execution order.
PHASES = (
    ('manifest', 40),   # Phase 1: manifest addons (download + extract)
    ('resolve', 5),     # dependency resolution (indeterminate)
    ('content', 30),    # Phase 2: 3rd-party content + dependencies
    ('native', 5),      # rare post-GUI native install of binary addons
    ('config', 12),     # build-config pack (indeterminate)
    ('finalize', 8),    # media installer + PatchEngine (indeterminate)
)
BANNER_CAP = 99         # never hit 100 -> banner can't auto-complete/close
CREEP_CAP = 0.90        # indeterminate phases stop at 90% of their slice
CREEP_TAU = 15.0        # seconds; larger = slower creep


class ProvisionProgress(object):
    def __init__(self, banner=None, header='', message=''):
        self.banner = banner
        self._header = header
        self._message = message
        self._lock = threading.RLock()
        self._order = [n for n, _ in PHASES]
        total = float(sum(w for _, w in PHASES))
        self._span = {}
        acc = 0.0
        for name, weight in PHASES:
            self._span[name] = weight / total * 100.0
            acc += self._span[name]
        self._frac = dict((n, 0.0) for n in self._order)
        self._creep_t0 = {}
        self._high = 0.0
        self._last = (None, None)
        self._stop = threading.Event()
        self._thread = None

    # ---- inputs (pure state; safe from any thread) ------------------------ #
    def set_fraction(self, phase, fraction):
        """Report measured progress (0..1) for a phase. Never moves backwards."""
        if phase not in self._frac:
            return
        try:
            f = min(1.0, max(0.0, float(fraction)))
        except (TypeError, ValueError):
            return
        with self._lock:
            if f > self._frac[phase]:
                self._frac[phase] = f
            if f > 0:
                self._creep_t0.pop(phase, None)   # real data beats the estimate

    def complete(self, phase):
        """Mark a phase AND every earlier one as done (handles skipped phases)."""
        if phase not in self._frac:
            return
        with self._lock:
            for n in self._order:
                self._frac[n] = 1.0
                self._creep_t0.pop(n, None)
                if n == phase:
                    break

    def begin_indeterminate(self, phase, message=None):
        """Start a phase whose duration is unknown; it creeps until real data
        arrives or complete() is called."""
        with self._lock:
            if phase in self._frac and self._frac[phase] < 1.0:
                self._creep_t0.setdefault(phase, time.time())
            if message:
                self._message = message

    def set_message(self, message):
        with self._lock:
            self._message = message

    # ---- output ----------------------------------------------------------- #
    def tick(self):
        """Advance the creep estimate of every indeterminate phase."""
        now = time.time()
        with self._lock:
            for phase, t0 in list(self._creep_t0.items()):
                el = max(0.0, now - t0)
                est = CREEP_CAP * el / (el + CREEP_TAU)
                if est > self._frac[phase]:
                    self._frac[phase] = est

    def percent(self):
        """Overall 0..99, monotonic."""
        with self._lock:
            raw = sum(self._span[n] * self._frac[n] for n in self._order)
            self._high = max(self._high, raw)
            return int(min(BANNER_CAP, max(0.0, self._high)))

    def publish(self):
        """Push the current value to the banner (deduplicated, never raises)."""
        with self._lock:
            pct = self.percent()
            if self.banner is None:
                return pct
            key = (pct, self._message)
            if key == self._last:
                return pct
            self._last = key
            try:
                self.banner.update(pct, self._header, self._message)
            except Exception as e:
                logging.log('[ProvisionProgress] banner update failed: {0}'.format(e),
                            level=xbmc.LOGWARNING)
            return pct

    # ---- background ticker (the ONLY banner writer) ----------------------- #
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run)
            self._thread.daemon = True
            self._thread.start()

    def _run(self):
        monitor = xbmc.Monitor()
        while not self._stop.is_set():
            try:
                self.tick()
                self.publish()
            except Exception:
                pass
            if monitor.waitForAbort(0.5):
                break

    def finish(self):
        """Stop ticking but LEAVE the banner up (it stays through force-close)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        self.publish()

    def close(self):
        """Stop ticking and close the banner (bail-out / resume paths)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        try:
            if self.banner is not None:
                self.banner.close()
        except Exception:
            pass