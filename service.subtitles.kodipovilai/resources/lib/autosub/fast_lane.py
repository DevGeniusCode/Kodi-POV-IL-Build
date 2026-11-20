# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Autosub's FAST LANE (Step 8): apply a strong Ktuvit / Wizdom match the moment that provider answers, without
# waiting for the slower sources, the pool lookup and the ranking. The timing check is not skipped: it is what
# the delivery gate already does in the background (sync.service.process), and one better match may still replace
# the subtitle once the full list is in (autosub.policy.swap_choice).
#
# Two parts, both free of Kodi so the contract test can drive them with a fake clock:
#   FastLane   the state machine: provider results go in (offer), a decision comes out (decide)
#   run        the worker loop that applies what the lane decided, through a callable of the caller

import threading

from resources.lib.autosub.policy import FAST_GRACE_S, FAST_SOURCES, fast_payload
from resources.lib.scoring_matching.ranking import release_similarity


class FastLane(object):
    """What the Israeli providers found, and when it is time to apply the best of it.

    expected: the fast providers that were started (a provider that is off never reports, and is not waited for).
    A hit below 100 waits up to `grace` seconds for the providers still expected, so a 100 from the second one wins
    without a swap; a 100, every expected provider done, or the end of the search applies at once."""

    def __init__(self, threshold, expected, info, playing, grace=FAST_GRACE_S):
        self.threshold = int(threshold)
        self.expected = set(expected) & set(FAST_SOURCES)
        self.info = info
        self.playing = playing or ''
        self.grace = float(grace)
        self.done = set()
        self.entries = []
        self.failed = set()          # sources whose subtitle failed to apply: their other rows are skipped too
        self.first_hit = None
        self.search_over = False
        self._lock = threading.Lock()

    def offer(self, provider, rows, now):
        """A provider finished with `rows` (candidate dicts with 'pct'); called from the engine's poll loop."""
        with self._lock:
            self.done.add(provider)
            for row in rows or []:
                payload = fast_payload(row, self.info, self.threshold)
                if payload is None:
                    continue
                rel = payload.get('filename') or ''
                self.entries.append({
                    'row': row, 'payload': payload, 'provider': provider, 'pct': row.get('pct'),
                    'sim': release_similarity(self.playing, rel), 'at': now})
                if self.first_hit is None:
                    self.first_hit = now

    def end_search(self):
        """The provider search is over (every provider answered, was cut, or the list came from the cache)."""
        with self._lock:
            self.search_over = True

    def reject(self, entry):
        """The subtitle of `entry` could not be applied: do not offer it, nor another row of its source, again."""
        with self._lock:
            self.failed.add(entry['provider'])

    def _best(self):
        live = [e for e in self.entries if e['provider'] not in self.failed]
        if not live:
            return None
        return max(live, key=lambda e: (e['pct'], e['sim'], -FAST_SOURCES.index(e['provider'])))

    def decide(self, now):
        """The entry to apply now, or None (nothing eligible yet, or still inside the grace period)."""
        with self._lock:
            best = self._best()
            if best is None:
                return None
            waiting = (best['pct'] < 100 and not self.search_over
                       and bool(self.expected - self.done)
                       and now - self.first_hit < self.grace)
            return None if waiting else best

    @property
    def over(self):
        return self.search_over


def run(lane, apply_fn, clock, wait, stopped, max_failures=2):
    """The worker loop. Returns the entry that was applied, or None when nothing was.

    apply_fn(entry) -> True when the subtitle is on screen; clock() -> seconds; wait(seconds) sleeps a little;
    stopped() -> True when the run must end (another film, a manual pick, the end of autosub)."""
    failures = 0
    while not stopped():
        entry = lane.decide(clock())
        if entry is None:
            if lane.over:
                return None
            wait(0.05)
            continue
        if apply_fn(entry):
            return entry
        lane.reject(entry)
        failures += 1
        if failures >= max_failures:
            return None
    return None
