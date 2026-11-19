# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# An embedded track shown in steps while it is still being read.

import os
import threading
import time
from resources.lib.ai_translation.providers import gemini
from resources.lib.core import kodi, selection as core_selection, settings
from resources.lib.ai_translation.context import prompt
from resources.lib.core.srt import blocks as srt_blocks
from resources.lib.ai_translation.dispatch.pacing import gemini_rate_gate, gemini_rpm_interval, thinking_params
from resources.lib.ai_translation.progress import status
from resources.lib.ai_translation.providers.google_flow import preview_clean
from resources.lib.ai_translation.providers.model_fallback import FALLBACK_MODEL
from resources.lib.fetching.embedded.extraction import playing_video_url


# --- An embedded track shown in steps while it is still being read ---------
#
# Field report (Nov 2026): "extracting the embedded subtitle takes half the
# film before any translation shows". Nothing could be translated before the
# last cue was read: one request a cue, at the pace a strict provider
# tolerates -- 169 s for a 40-minute episode with no player competing, and in
# Kodi during playback the same stream gave 4 cues a second in one play and
# 0.7 in another (QA Kodi, 13 Nov 2026).
#
# So the track is read in windows that start where the viewer IS (embedded_
# extract.extract_srt steps=), and the lines of each window go to the model
# while the next window is read. Nothing here is the translation that is kept:
# it is a view, held in memory, of lines the full job translates again once
# the whole track is in -- no cache file, no pool, no telemetry, no proofread,
# never Google's text. The first attempt at this (pulled from 0.3.79 in
# review) ran each step through resolve() and failed on exactly those side
# effects, and on the viewer's handler dropping every event after the first
# step's because each step came with a source id of its own.

# Read in steps only a film at least this long (seconds).
_STEPS_MIN_DURATION_S = 15 * 60
# The first window: from the viewer's position this far ahead (ms)...
_STEPS_FIRST_MS = 150 * 1000
# ...but not fewer cues than this (a quiet stretch must not make it an empty
# window: the first Hebrew line is what the viewer is waiting for), nor more
# (dense dialogue: the wait for that first line is counted in cues).
_STEPS_FIRST_MIN_CUES = 15
_STEPS_FIRST_MAX_CUES = 60
# Every window after it is sized from what the last one measured, in CUES:
# a cue is a request, a minute of film is not (a quiet minute is read in no
# time and a window sized by it then swallowed a quarter of an hour; review
# of the first draft of this, 0.3.80). The next window is as many cues as can
# be read in this share of the lead the view has now -- the time until the
# viewer reaches the first line that has not been read.
_STEPS_USE_OF_LEAD = 0.6
# Never fewer than this (a provider slower than the film cannot be kept ahead
# of; small windows at least keep lines arriving)...
_STEPS_MIN_CUES = 10
# ...and never more than this many times the window before it (one fast
# window is not a measurement to bet a long read on).
_STEPS_GROW_MAX = 8
# A window that took less than this, or held fewer cues than this, measures
# nothing (it was read by an earlier attempt, or is too small to time).
_STEPS_MEASURE_MIN_S = 2.0
_STEPS_MEASURE_MIN_CUES = 5
# A window that would leave fewer cues than this ahead takes them too.
_STEPS_TAIL_CUES = 5
# A window starts this far BEHIND the viewer: the line being spoken now.
_STEPS_BEHIND_MS = 15 * 1000
# Cues a request.
_STEPS_CHUNK = 50
# Requests a cue may be part of without coming back.
_STEPS_TRIES = 2
# Requests in a row that brought nothing: stop asking (the full job follows
# and has its own retries and fallbacks).
_STEPS_GIVE_UP = 3
# Requests the views may make for one pick: they come on top of the full
# translation, which translates the same lines again. With the track read in
# minutes they were up to +40% of a regular translation's requests (QA, 14 Nov
# 2026: Interstellar 12 on 54 chunks, Fight Club 9 on 39, Game of Thrones 4 on
# 10); read in seconds, two windows (~100 lines ahead of the viewer) carry the
# picture until the full job's chunks arrive. A jump earns one more (moved()).
# The owner: an embedded pick must not cost more quota than a regular one.
_STEPS_MAX_REQUESTS = 2
# ...unless the track comes in a line a request. A TorBox "store" node
# answers only the first range of many, so the reading goes at the pace the
# provider tolerates for single requests: ~2.5 lines a second, thirteen
# minutes for a film of 1,900 lines (field log, 15 Nov 2026, The Odyssey
# 2160p). The two views are ~80 lines, six or seven minutes of film; after
# them the viewer read the source language until the full job could start.
# So while the reading still has at least _STEPS_SLOW_LEFT_S to go at the
# rate measured, one more request may go out each time the Hebrew ahead of
# the viewer is down to _STEPS_SLOW_LEAD_MS, at most _STEPS_SLOW_MAX for a
# pick. Such a request carries _STEPS_SLOW_CHUNK lines, not _STEPS_CHUNK: a
# request is what the quota counts, and fifty lines of dense dialogue are two
# minutes of film -- in the QA Kodi (Fight Club, 15 Nov 2026) the rule fired
# again 36 s after it had fired. A hundred lines are five to eight minutes:
# one or two such requests for the thirteen minutes that film took to read.
# A track read in seconds earns none.
_STEPS_SLOW_LEFT_S = 60.0
_STEPS_SLOW_LEAD_MS = 120 * 1000
_STEPS_SLOW_MAX = 6
_STEPS_SLOW_CHUNK = 100
# A step's request is answered in seconds or not worth waiting for.
_STEPS_TIMEOUT = 45
# The whole track in before the first view's answer: how long after the
# request went out to wait for it (see _EmbeddedSteps.settle). An answer
# normally lands ~8 s after it is sent; past this, the full translation's first
# lines come about as fast (review).
_STEPS_SETTLE_S = 12.0
# How often the view is checked against the viewer's position (seconds), and
# how far past its last line the viewer may be before the embedded track is
# put back (ms).
_STEPS_WATCH_S = 2.0
_STEPS_PAST_END_MS = 1000
# The embedded track goes back when the viewer will reach a line not read
# before the next check (ms: the watcher's period and a margin)...
_STEPS_HAND_BACK_MS = 2500
# ...and the view comes (back) only with at least this much film read ahead
# of the viewer: with less, a provider about as fast as the film switched
# the subtitle back and forth on every window (ms).
_STEPS_SHOW_LEAD_MS = 20 * 1000


def _srt_start_ms(block):
    """A cue's start in ms, or None."""
    for line in (block or '').split('\n')[:3]:
        if '-->' in line:
            try:
                hh, mm, rest = line.split('-->')[0].strip().replace(
                    '.', ',').split(':')
                ss, ms = rest.split(',')
                return ((int(hh) * 60 + int(mm)) * 60 + int(ss)) * 1000 + int(ms)
            except Exception:
                return None
    return None


def unique_starts(blocks):
    """Each block's start (ms), None where it has none or shares it with
    another block: a start is what ties a line of the view to a line of the
    full translation, so it has to name one line."""
    starts = [_srt_start_ms(b) for b in blocks]
    count = {}
    for s in starts:
        count[s] = count.get(s, 0) + 1
    return [s if (s is not None and count[s] == 1) else None for s in starts]


def _block_with_text(block, text):
    """`block` with its number and times and `text` as its lines."""
    lines = (block or '').strip('\n').split('\n')
    at = next((i for i, line in enumerate(lines[:3]) if '-->' in line), None)
    if at is None or not text:
        return block
    return '\n'.join(lines[:at + 1] + [text])


def _has_hebrew(text):
    return any('֐' <= ch <= '׿' for ch in (text or ''))


def with_steps_preview(blocks, he):
    """`blocks` (source cues the full job has not translated yet) with the
    Hebrew the viewer already has for them. `he` is {start ms: text}."""
    if not he:
        return list(blocks)
    return [_block_with_text(b, he.get(_srt_start_ms(b))) for b in blocks]


def _ms_label(ms):
    ms = int(ms or 0)
    return '%d:%02d' % (ms // 60000, ms // 1000 % 60)


class _EmbeddedSteps(object):
    """What the viewer is shown while an embedded track is still being read.

    The extractor calls timeline() once with every cue's start, window() for
    the next span to read and feed() with everything read so far; a worker
    thread puts the new lines into Hebrew, the ones nearest ahead of the
    viewer first, while the reading goes on. show(text) gets the whole view
    -- Hebrew where there is some, the source line where not -- every time it
    changes.

    The view takes the embedded track's place on screen, and that track has
    every line of the film while the view has only what was read -- a stretch
    from where the viewer picked it, and another after every jump. Which
    lines were read is known one by one (the timeline has every line; a
    window, once fed, is read whole). So:
    - the view is shown only when at least 20 s of film from the viewer on
      have been read and there is a Hebrew line in that stretch;
    - when the viewer is about to reach a line not read while the reading
      goes on (a provider slower than the film, a jump ahead, a jump back to
      before the pick), `restore()` puts the embedded track back, the next
      window starts there (moved() cuts the one being read), and the view
      returns by the rule above;
    - back_out() puts it back when the reading is given up.

    `translate(blocks, before)` returns {index: Hebrew text, or '' for a cue
    the model answered without Hebrew}, {} when the request failed, None to
    stop asking for good. `position()` is the viewer's place in ms or None.
    finish() ends it and returns {start ms: Hebrew text}; no view is shown
    after it returns."""

    def __init__(self, position, translate, show, restore=None, log=None,
                 clock=time.time):
        self._position = position
        self._translate = translate
        self._show = show
        self._restore = restore
        self._log = log or (lambda message: None)
        self._clock = clock
        self._lock = threading.Lock()          # the cues and their Hebrew
        self._show_lock = threading.Lock()     # one view at a time
        self._cues = []                        # every cue's start, the whole film
        self._blocks = []                      # source cues read so far, by time
        self._starts = []                      # _unique_starts(self._blocks)
        self._he = {}                          # start -> Hebrew text
        self._kept = set()                     # starts answered without Hebrew
        self._tries = {}                       # start -> requests that failed
        self._read = set()                     # starts of every line fed
        self._covered = []                     # (from, until) of every window fed
        self._asked_span = None                # the window being read
        self._asked_at_viewer = False          # ...from the viewer's own line
        self._cut = False                      # it was cut short by a jump
        self._read_before = 0                  # lines read when it was asked for
        self._moved_at = 0.0                   # moved(): asked at most 2x a second
        self._frozen = False                   # the last feed: nothing more to ask
        self._mark = None                      # the last window's end (ms)
        self._count = 0                        # lines the last window had to read
        self._asked = 0.0                      # when it was asked for
        self._rate = 0.0                       # cues read a second, last measured
        self._view_end = None                  # start of the view's last cue
        self._closed = False
        self._dead = translate is None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._idle = threading.Event()         # no request out (see settle)
        self._idle.set()
        self._sent_at = 0.0                    # when the last request went out
        self._thread = None
        self._watcher = None
        self.shown = False                     # a view was shown at least once
        self.on_screen = False                 # the view is what the player shows
        self.windows = 0
        self.requests = 0
        self._budget = _STEPS_MAX_REQUESTS     # requests the views may make
        self.slow_requests = 0                 # ...earned by a slow reading
        self.handed_back = 0

    # -- the extractor's side

    def timeline(self, starts_ms):
        """Every cue's start (ms of film), before the first window."""
        self._cues = sorted(int(s) for s in starts_ms)

    def window(self):
        """(from, until) in ms of film for the next read, or None when what
        is left should be read in one go."""
        import bisect
        cues = self._cues
        if self._closed or not cues:
            return None
        pos = self._position()
        if pos is None:
            return None
        now = self._clock()
        low = max(0, pos - _STEPS_BEHIND_MS)
        with self._lock:
            need = self._need(pos)
            first = self._run_end(need)
        if first >= len(cues):
            # everything from the viewer on is read; what is behind is read
            # by the complete pass
            return None
        spent = now - self._asked
        lines = self._count
        if self._cut:
            # a window cut short read only part of its lines: measured by
            # those (a provider the film overtakes is cut too, and that is
            # exactly when the rate matters; review)
            self._cut = False
            with self._lock:
                lines = len(self._read) - self._read_before
        if (lines >= _STEPS_MEASURE_MIN_CUES
                and spent >= _STEPS_MEASURE_MIN_S):
            self._rate = lines / spent
        if first == need:
            # Nothing read ahead of the viewer: the first window, a jump
            # (either way) into a stretch not read, or a provider slower than
            # the film. From here, and small: the viewer is waiting.
            in_time = bisect.bisect_right(cues, pos + _STEPS_FIRST_MS) - first
            count = max(_STEPS_FIRST_MIN_CUES,
                        min(in_time, _STEPS_FIRST_MAX_CUES))
        else:
            # Lines read from the viewer on, up to cues[first]: the next
            # window continues there (not after the last window -- after a
            # jump back that is far ahead, and everything between would be
            # read in one window before anything new is shown; review).
            if self._rate > 0:
                # until the viewer reaches the first line that was not read
                lead_s = max(0, cues[first] - pos) / 1000.0
                count = int(_STEPS_USE_OF_LEAD * self._rate * lead_s)
            else:
                count = 2 * self._count
            count = max(_STEPS_MIN_CUES, min(
                count, _STEPS_GROW_MAX * max(self._count, _STEPS_MIN_CUES)))
        last = first + count - 1
        if last >= len(cues) - 1 - _STEPS_TAIL_CUES:
            # everything ahead of the viewer; what is behind is read last
            last = len(cues) - 1
        # +1: the span's end is turned into the file's ticks and back
        mark = cues[last] + 1
        with self._lock:
            # what the extractor will actually fetch: the lines in the span
            # not read yet (the measured rate is per line fetched)
            count = sum(1 for c in cues[bisect.bisect_left(cues, low):last + 1]
                        if not self._is_read(c))
            self._asked_span = (low, mark)
            self._asked_at_viewer = first == need
            self._read_before = len(self._read)
        self._mark, self._count, self._asked = mark, count, now
        self.windows += 1
        return (low, mark)

    def moved(self):
        """Asked by the extractor between clusters while a window is read:
        has the viewer jumped where this window does not help -- before it or
        past its end, onto a line not read? Then the window is cut short and
        the next one starts from the viewer. Its span is not counted as read
        (only the lines that came in are).

        Inside the window too, when it is one that carries on from lines read
        earlier: nothing of a window is in the view before its end, and such
        a window is as long as the provider allows -- on one that reads a
        line a request, everything left ahead, five minutes and more (field
        log, 15 Nov 2026: a jump ahead inside it, and the embedded track was
        on screen until the viewer jumped again). Cut, it hands over what it
        has read, and the next window is a small one from the viewer -- one
        that is waited for, not cut (it was asked for at the viewer's own
        line), so no window is cut twice for one place. Only a jump out of
        the window earns a request; the lines at the new place inside it
        are asked for by the rule for a slow reading (_earns_slow)."""
        span = self._asked_span
        if span is None or self._closed or not self._cues:
            return False
        now = self._clock()
        if now - self._moved_at < 0.5:
            return False
        self._moved_at = now
        pos = self._position()
        if pos is None:
            return False
        low, mark = span
        inside = low <= pos <= mark
        if inside and self._asked_at_viewer:
            return False
        with self._lock:
            need = self._need(pos)
            if need >= len(self._cues) or self._is_read(self._cues[need]):
                return False
            self._asked_span = None
            self._cut = True
            if not inside:
                # the new place earns one view of its own (_STEPS_MAX_REQUESTS).
                # (A viewer who PLAYS past a window's end is "out" too; what
                # keeps that from repeating is the window sizing -- a window
                # asked for at the viewer's line is small and waited for --
                # and LineARequest's slower-than-the-film test holds it to
                # the ceiling.)
                self._budget = max(self._budget, self.requests + 1)
            # (A cut from inside earns nothing here: it needs no jump -- a
            # viewer who plays on past the lines a slow provider has handed
            # over is cut too, again and again, and each cut would have
            # bought a request outside every ceiling; review of 0.3.85. The
            # request for the new place is _earns_slow's, which counts.)
        if inside:
            self._log('embedded steps: the viewer is at a line the window '
                      'being read has not handed over (at %s) -- cutting it '
                      'short' % _ms_label(pos))
        else:
            self._log('embedded steps: the viewer jumped out of the window '
                      'being read (to %s) -- cutting it short' % _ms_label(pos))
        return True

    # -- what was read (under _lock)

    def _is_read(self, start):
        return start in self._read or any(
            low <= start <= high for low, high in self._covered)

    def _need(self, pos):
        """Index in the timeline of the line the viewer is at or reaches
        next."""
        import bisect
        return bisect.bisect_left(self._cues, pos - _STEPS_PAST_END_MS)

    def _run_end(self, index):
        """Index of the first line not read, from `index` on."""
        cues = self._cues
        while index < len(cues) and self._is_read(cues[index]):
            index += 1
        return index

    def _lead(self, pos):
        """ms until the viewer reaches a line not read: -1 when the line
        the viewer is at was not read, None when everything from there on
        was."""
        if not self._cues:
            # no timeline: the view's last line is all there is to go by
            if self._view_end is None:
                return None
            return self._view_end + _STEPS_PAST_END_MS - pos
        need = self._need(pos)
        end = self._run_end(need)
        if end >= len(self._cues):
            return None
        if end == need:
            return -1
        return self._cues[end] - pos

    def _hebrew_ahead(self, pos):
        """A Hebrew line in the stretch read from where the viewer is."""
        if not self._cues:
            return any(start >= pos for start in self._he)
        need = self._need(pos)
        end = self._run_end(need)
        if end <= need:
            return False
        low, high = pos - _STEPS_PAST_END_MS, self._cues[end - 1]
        return any(low <= start <= high for start in self._he)

    def _may_show(self, pos):
        lead = self._lead(pos)
        return ((lead is None or lead >= _STEPS_SHOW_LEAD_MS)
                and self._hebrew_ahead(pos))

    def feed(self, srt_text, translate=True):
        """Everything read so far, as SRT, after a window. `translate`
        False: the whole track, as the reading ends -- shown, not asked for
        (the full translation is about to start; review)."""
        if self._closed:
            return
        blocks = srt_blocks.parse_blocks(srt_text or '')
        starts = unique_starts(blocks)
        read = set(s for s in (_srt_start_ms(b) for b in blocks)
                   if s is not None)
        with self._lock:
            self._blocks, self._starts, self._read = blocks, starts, read
            if self._asked_span is not None:
                self._covered.append(self._asked_span)
                self._asked_span = None
            if not translate:
                self._frozen = True
        self._view()
        if self._dead or not translate:
            return
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name='povil-embedded-steps')
            self._thread.daemon = True
            self._thread.start()
        self._wake.set()

    def settle(self, cancelled=None, timeout=None):
        """The whole track is in (the last feed froze new requests). When no
        view is up yet but a window's request is out, wait for its answer: it
        is the lines the viewer reaches next, and the full translation's first
        lines take as long again (QA, 14 Nov 2026, South Park S04E04 MULTI
        BluRay: the track was read in 5.5 s, the first window's answer was
        dropped, and the first Hebrew line came at +24 s, not +12 s). At most
        _STEPS_SETTLE_S from when the request went out, and not past a
        replaced pick (`cancelled`). Returns whether it waited."""
        if self.shown or self._dead or self._idle.is_set():
            return False
        limit = timeout if timeout is not None else _STEPS_SETTLE_S
        until = self._sent_at + limit
        while not self._idle.is_set():
            left = until - time.time()
            if left <= 0:
                break
            try:
                if cancelled is not None and cancelled():
                    break
            except Exception:
                pass
            self._idle.wait(min(0.5, left))
        return True

    def finish(self):
        """The reading is over (the whole track is in): no more views."""
        self._stop.set()
        self._wake.set()
        with self._show_lock:
            self._closed = True
        with self._lock:
            return dict(self._he)

    def back_out(self):
        """The reading was given up: the view covers only what was read, so
        the embedded track goes back on screen (as before there were views).
        Call after finish()."""
        with self._show_lock:
            self._hand_back('the reading stopped')

    # -- the worker

    def _earns_slow(self, pos, pending_start):
        """Under _lock, with the budget spent: may one more request go out
        (see _STEPS_SLOW_LEFT_S)? `pending_start`: the first line from the
        viewer on that is read and has no Hebrew."""
        if (pos is None or self._rate <= 0 or not self._cues
                or self.slow_requests >= _STEPS_SLOW_MAX
                or pending_start - pos > _STEPS_SLOW_LEAD_MS):
            return False
        left = sum(1 for c in self._cues if not self._is_read(c))
        if left < self._rate * _STEPS_SLOW_LEFT_S:
            return False              # the full job starts within the minute
        self.slow_requests += 1
        self._budget = self.requests + 1
        self._log('embedded steps: {0} line(s) still to read at {1:.1f} a '
                  'second, and the Hebrew ends {2} s ahead of the viewer -- '
                  'one more request ({3} of {4})'.format(
                      left, self._rate, max(0, pending_start - pos) // 1000,
                      self.slow_requests, _STEPS_SLOW_MAX))
        return True

    def _next_batch(self):
        with self._lock:
            if self._frozen or (self.requests >= self._budget and (
                    self._rate <= 0
                    or self.slow_requests >= _STEPS_SLOW_MAX)):
                return None
            pos = self._position()
            # The lines the viewer reaches next, wherever the reading started
            # (a resumed film, a seek, an attempt carried on) -- and only
            # those: a line behind the viewer is left to the full
            # translation, which costs no request more (review: translating
            # them too doubled the default model's requests for a film).
            pending = [i for i, s in enumerate(self._starts)
                       if s is not None and s not in self._he
                       and s not in self._kept
                       and self._tries.get(s, 0) < _STEPS_TRIES
                       and (pos is None or s >= pos - _STEPS_BEHIND_MS)]
            if not pending:
                return None
            size = _STEPS_CHUNK
            if self.requests >= self._budget:
                if not self._earns_slow(pos, self._starts[pending[0]]):
                    return None
                size = _STEPS_SLOW_CHUNK
            take = pending[:size]
            before = [srt_blocks.block_text_only(b)
                      for b in self._blocks[max(0, take[0] - 3):take[0]]]
            # a request is about to go out: settle() waits from here (under
            # the lock, so the last feed cannot slip between; review)
            self._idle.clear()
            self._sent_at = time.time()
            return ([self._starts[i] for i in take],
                    [self._blocks[i] for i in take],
                    [line for line in before if line])

    def _run(self):
        failed = 0
        while not self._stop.is_set() and not self._dead:
            batch = self._next_batch()
            if not batch:
                self._wake.clear()
                self._wake.wait(2.0)
                continue
            starts, blocks, before = batch
            try:
                if self._stop.is_set():
                    return
                self.requests += 1
                try:
                    got = self._translate(blocks, before)
                except Exception as e:
                    self._log('embedded steps: request failed: {0}'.format(e))
                    got = {}
                if self._stop.is_set():
                    return
                if got is None:
                    self._dead = True
                    self._log('embedded steps: no more requests -- the lines '
                              'keep their source text until the full '
                              'translation')
                    return
                with self._lock:
                    for i, start in enumerate(starts):
                        if got.get(i):
                            self._he[start] = got[i]
                        elif i in got:
                            self._kept.add(start)
                        else:
                            self._tries[start] = self._tries.get(start, 0) + 1
                if any(got.values()):
                    failed = 0
                    self._view()
                    continue
            finally:
                self._idle.set()
            failed += 1
            if failed >= _STEPS_GIVE_UP:
                self._dead = True
                self._log('embedded steps: {0} requests in a row brought '
                          'nothing -- stopped asking'.format(failed))
                return
            self._stop.wait(5.0)

    def _view(self):
        with self._show_lock:
            if self._closed:
                return
            with self._lock:
                if not self._he:
                    return
                if not self.on_screen:
                    # Showing the view takes the embedded track off the
                    # screen. Not before the line the viewer is at has been
                    # read and the stretch read from there has Hebrew (QA
                    # Kodi: a jump to minute 20 while minutes 0-2.5 were
                    # being read left the viewer with a subtitle that had
                    # nothing in it from there on).
                    pos = self._position()
                    if pos is None or not self._may_show(pos):
                        # (no position: the player is going -- nothing to
                        # show it on)
                        return
                source = list(self._blocks)
                blocks = [_block_with_text(b, self._he[s]) if s in self._he
                          else b for b, s in zip(self._blocks, self._starts)]
                ends = [s for s in (_srt_start_ms(b) for b in source[-3:])
                        if s is not None]
            try:
                self._show(preview_clean(blocks, srt_blocks.stitch_blocks(source)))
                self.shown = self.on_screen = True
                self._view_end = max(ends) if ends else None
            except Exception as e:
                self._log('embedded steps: could not show the view: {0}'
                          .format(e))
                return
            if self._watcher is None and self._restore is not None:
                self._watcher = threading.Thread(
                    target=self._watch, name='povil-embedded-steps-watch')
                self._watcher.daemon = True
                self._watcher.start()

    # -- the embedded track back on screen

    def _hand_back(self, why):
        """Under _show_lock."""
        if not self.on_screen or self._restore is None:
            return
        try:
            self._restore()
            self.on_screen = False
            self.handed_back += 1
            self._log('embedded steps: {0} -- the embedded track is back on '
                      'screen'.format(why))
        except Exception as e:
            self._log('embedded steps: could not put the embedded track '
                      'back: {0}'.format(e))

    def _watch(self):
        while not self._stop.wait(_STEPS_WATCH_S):
            self.check()

    def check(self):
        """The viewer is about to reach a line not read while the track is
        still being read (past the view's end, or a jump into a stretch not
        read): the embedded track has that line, the view does not. And the
        other way: the embedded track is on screen and the viewer is back in
        a stretch the view has in Hebrew (a jump back, then forward again) --
        the view returns now, not at the next window's end (review)."""
        with self._show_lock:
            if self._closed:
                return
            if self.on_screen:
                pos = self._position()
                if pos is None:
                    return
                with self._lock:
                    lead = self._lead(pos)
                if lead is not None and lead < _STEPS_HAND_BACK_MS:
                    self._hand_back('the viewer is about to reach a line not '
                                    'read')
                return
        if self.shown:
            self._view()          # takes _show_lock itself; _may_show decides


class StepsScreen(object):
    """Where the views of an embedded track read in steps go, and what the
    full job's own events must look like after them.

    The viewer's handler (default.py on_phase) ties everything it shows to
    ONE source id: it adopts the id of the first 'first_ready' it is handed
    and drops any 'chunk_ready' or 'done' that carries another. The full job
    computes its id from the complete source file, which does not exist while
    the views are being shown -- so every view carries an id made from the
    pick, and after a view the full job reports under that id too (the job
    takes it as its own progressive id, and handler() rewrites any event that
    still carries another). Without that the screen froze on the first view:
    nothing after it passed the handler, not even the finished translation
    (review of the first attempt, 0.3.79).

    With no handler (the on-play pick) the view is written to one of two
    alternating files and handed to the player from here."""

    def __init__(self, link, release, handler, selection):
        import hashlib as _hashlib
        self.source_id = 'steps' + _hashlib.sha1(
            (link or '').encode('utf-8')).hexdigest()[:11]
        self._release = release or ''
        self._handler = handler
        self._selection = selection
        self._slot = 'b'
        self.shown = False

    def show(self, text):
        first, self.shown = not self.shown, True
        if self._handler is not None:
            if first:
                status('AI: הדקות הקרובות מתורגמות, ההמשך בדרך',
                        time_ms=4000)
                self._handler('first_ready', {
                    'fallback_text': text, 'source_id': self.source_id,
                    'release': self._release})
            self._handler('chunk_ready', {
                'completed': 0, 'total': 0, 'merged_text': text,
                'source_id': self.source_id, 'release': self._release})
            return
        self._slot = 'b' if self._slot == 'a' else 'a'
        rel = kodi.safe_release_filename(self._release)
        path = os.path.join(
            kodi.cache_dir(),
            ('{0}.{1}.he.srt'.format(rel, self._slot) if rel else
             'progressive_{0}_{1}.he.srt'.format(self.source_id, self._slot)))
        with open(path + '.aitmp', 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(path + '.aitmp', path)
        core_selection.apply_subtitle_file(path, selection=self._selection,
                                       abandon_on_failure=False)

    def handler(self):
        """The callback the full job reports to: the caller's own while
        nothing was shown, else one that keeps the views' source id."""
        inner = self._handler
        if inner is None or not self.shown:
            return inner
        source_id = self.source_id

        def same_source(phase, data):
            data = dict(data or {})
            if data.get('source_id'):
                data['source_id'] = source_id
            return inner(phase, data)

        return same_source


def _steps_translator(info, source_lang, cancelled):
    """translate(blocks, before) for _EmbeddedSteps, or None when there is
    nothing to ask (no key, or the viewer chose Google as the translator: the
    view never carries Google's text).

    Always the default model, whatever model is chosen: it answers in
    seconds and has the requests to spare, and a chosen full Flash model has
    twenty a day. One request a batch, one more after a busy answer; anything
    else leaves the lines in their source language -- the full job has the
    retries, the fallbacks and the checks."""
    from resources.lib.ai_translation.proofread.gp_run import title_cast
    try:
        if (settings.get_setting('translation_mode', 'ai') or 'ai') != 'ai':
            return None
        api_key = settings.get_setting('api_key', '')
        if not api_key:
            return None
        model = FALLBACK_MODEL
        chosen = (settings.get_setting('model', FALLBACK_MODEL)
                  or FALLBACK_MODEL)
        level, budget = thinking_params(
            (settings.get_setting('thinking_budget', 'medium')
             or 'medium').strip().lower(), model)
        temperature = settings.get_float('temperature', 1.0)
        top_p = settings.get_float('top_p', 0.95)
        interval = gemini_rpm_interval(
            settings.get_bool('ai_paid_mode', False), model)
    except Exception as e:
        kodi.log('embedded steps: no translator: {0}'.format(e),
                       level='WARNING')
        return None
    state = {'template': None}

    def _template():
        if state['template'] is None:
            imdb_id = (info.get('imdb_id') or '').strip()
            season = info.get('season') or ''
            episode = info.get('episode') or ''
            try:
                cast, title, year = title_cast(info, imdb_id, season, episode)
            except Exception:
                cast, title, year = [], info.get('title') or '', info.get(
                    'year') or ''
            state['template'] = prompt.build(
                source_lang=(source_lang() if callable(source_lang)
                             else source_lang),
                title=title, year=year, cast=cast,
                is_episode=info.get('is_episode', False),
                tvshow=info.get('tvshow', ''), season=season, episode=episode,
                numbered=False)
        return state['template']

    def translate(blocks, before):
        nums = [b.lstrip().split('\n', 1)[0].strip() for b in blocks]
        full = (_template()
                .replace('{prev_context_block}',
                         prompt.build_prev_context_block(before))
                .replace('{entry_count}', str(len(blocks)))
                .replace('{first_num}', nums[0] if nums else '?')
                .replace('{last_num}', nums[-1] if nums else '?')
                .replace('{chunk}', '\n\n'.join(blocks)))
        reply = None
        for attempt in (0, 1):
            if cancelled():
                return None
            gemini_rate_gate(interval)
            if cancelled():
                # the gate can hold a request for seconds
                return None
            try:
                reply = gemini.generate(
                    api_key=api_key, model=model, prompt=full,
                    temperature=temperature, max_output_tokens=16384,
                    top_p=top_p, thinking_budget=budget, thinking_level=level,
                    timeout=_STEPS_TIMEOUT, count_usage=(chosen == model))
                break
            except (gemini.QuotaExceeded, gemini.InvalidKey,
                    gemini.ModelUnavailable) as e:
                kodi.log('embedded steps: {0}'.format(e), level='INFO')
                return None
            except gemini.OverloadError:
                if attempt:
                    return {}
                time.sleep(5)
            except gemini.GeminiError as e:
                kodi.log('embedded steps: {0}'.format(e), level='INFO')
                return {}
        if not reply:
            return {}
        # A reply is matched to its cue by the cue's START, which the request
        # carried and the model copies back: a reply renumbered from 1 would
        # otherwise put its Hebrew under other cues (review, 0.3.80). A reply
        # block with no readable time falls back to its number.
        by_start, by_num = {}, {}
        for block in srt_blocks.parse_blocks(reply):
            start = _srt_start_ms(block)
            text = srt_blocks.block_text_only(block)
            if start is not None:
                by_start.setdefault(start, text)
            else:
                first = block.lstrip().split('\n', 1)[0].strip()
                if first.isdigit():
                    by_num.setdefault(first, text)
        out = {}
        for i, block in enumerate(blocks):
            start = _srt_start_ms(block)
            if start in by_start:
                text = by_start[start]
            elif nums[i] in by_num:
                text = by_num[nums[i]]
            else:
                continue
            out[i] = text if _has_hebrew(text) else ''
        return out

    return translate


def embedded_steps(info, lang, show, cancelled, restore=None):
    """An _EmbeddedSteps for the playing film, or None when it should be
    read in one pass as before: not a stream, or under fifteen minutes."""
    try:
        if '://' not in (playing_video_url(info) or ''):
            return None
        import xbmc as _x

        def position():
            try:
                p = _x.Player()
                if not p.isPlayingVideo():
                    return None
                return int(float(p.getTime()) * 1000.0)
            except Exception:
                return None

        duration = float(_x.Player().getTotalTime() or 0.0)
        if duration < _STEPS_MIN_DURATION_S or position() is None:
            return None
        made = {}

        def stopped():
            # the pick was replaced, or the reading is over (finish()): a busy
            # answer's retry is not sent then either (review)
            steps = made.get('steps')
            return bool(cancelled() or (steps is not None
                                        and steps._stop.is_set()))

        translator = _steps_translator(info, lang, stopped)
        if translator is None:
            # no view can come (no key, or Google is the translator): read
            # in one pass, as before -- windows would only cost passes
            return None
        made['steps'] = _EmbeddedSteps(
            position, translator, show,
            restore=restore, log=lambda m: kodi.log(m, level='INFO'))
        return made['steps']
    except Exception as e:
        kodi.log('embedded steps skipped: {0}'.format(e),
                       level='WARNING')
        return None
