# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The random-access source of the extractor: a local file or an HTTP (debrid) URL read through Range requests,
# with its part store, connection pool, 429 back-off and request pacing.

import bisect as _bisect
import os
import re
import threading as _threading
import time
from resources.lib.fetching.embedded.extract.tuning import COLD_RANGE_MS, HTTP_429_BASE_WAIT, HTTP_429_MAX_WAIT, HTTP_429_RETRIES, HTTP_429_STREAK_MAX, HTTP_CONNS, HTTP_TIMEOUT, MULTI_BUSY_HOLD_S, MULTI_PARALLEL, MULTI_RANGES, MULTI_SHARE_RANGES, MULTI_SLACK, MULTI_STORE_MAX, MULTI_STORE_PARTS, PARALLEL_HOSTS, PROBE_AHEAD, PROBE_CONTROL, PROBE_DRAIN_MAX, SHARE_AFTER_HOLD_S, STALLED_PROGRESS_S, UA
from resources.lib.fetching.embedded.extract.pace import HTTP_PACE_DECAY, HTTP_PACE_DECAY_AFTER, HTTP_PACE_EPOCH_REQS, HTTP_PACE_EPOCH_S, HTTP_REQ_PACE_MAX, HTTP_REQ_PACE_S
try:
    import urllib.request as _urlreq
except Exception:  # pragma: no cover - urllib always present on CPython 3
    _urlreq = None


def _new_session():
    """ONE keep-alive requests.Session (single pooled connection). This is the
    heart of debrid-safety: all Range reads reuse ONE TCP connection -- the same
    shape as the player's own single connection -- instead of storming the CDN
    with a fresh connection per read (which 429'd the token and killed playback
    in the field). None when requests isn't importable (then HTTP extraction is
    declined rather than risk a fresh-connection storm)."""
    try:
        import requests
        s = requests.Session()
        # Retry TRANSPORT errors, never STATUS ones. urllib3's Retry honours
        # Retry-After for 413/429/503 by default, so a plain `max_retries=1`
        # made it swallow the CDN's 429 -- sleep for the header's duration and
        # replay the request -- BEFORE our code ever saw the status. Measured on
        # the local CDN stand-in: 15 server-side 429s, `_429_total` still 0. That
        # silently bypassed all of the 429 handling below it: the pace never
        # widened, the streak counter never tripped the breaker, nothing was
        # logged (which is why a field log could read "no 429s" while the token
        # was in fact being limited), and worst of all urllib3's sleep is NOT
        # abort-aware, so a starving player waited behind it. Let every 429 reach
        # our own loop, which honours Retry-After with a cap, polls the abort
        # callback while it waits, and feeds the back-pressure logic.
        try:
            _R = requests.adapters.Retry
            retries = _R(total=None, connect=1, read=1, status=0, redirect=0,
                         status_forcelist=frozenset(),
                         respect_retry_after_header=False,
                         raise_on_status=False)
        except Exception:
            retries = 0     # no Retry class -> no retries at all beats hidden ones
        a = requests.adapters.HTTPAdapter(
            pool_connections=1, pool_maxsize=1, max_retries=retries)
        s.mount('https://', a)
        s.mount('http://', a)
        return s
    except Exception:
        return None


def _complete_length(content_range):
    """The file's real length as stated in a Content-Range, or None.

    Both forms carry it: 'bytes 0-15/525000' on a normal partial answer, and
    'bytes */525000' on a 416. The length after the slash is the whole file, not
    the part being sent, so it is authoritative even when the range itself was
    refused. '*' (unknown length) yields None, as it should."""
    m = re.search(r'/\s*(\d+)\s*$', content_range or '')
    return int(m.group(1)) if m else None


class _PartStore(dict):
    """{offset: bytes} that prefetch() brought. Finds the part holding a span
    by bisection over the sorted offsets: a batch makes a few hundred lookups
    over up to a few hundred parts, and a scan each time measured 40 ms a batch
    on a PC -- most of a second on a slow phone (review of 0.3.82). Only the
    methods used here keep the index right: set, del, pop, clear."""

    def __init__(self):
        dict.__init__(self)
        self._keys = None

    def __setitem__(self, k, v):
        dict.__setitem__(self, k, v)
        self._keys = None

    def __delitem__(self, k):
        dict.__delitem__(self, k)
        self._keys = None

    def pop(self, k, *default):
        self._keys = None
        return dict.pop(self, k, *default)

    def clear(self):
        dict.clear(self)
        self._keys = None

    def covering(self, offset, end):
        """(start, bytes) of a part that holds [offset, end), or None."""
        keys = self._keys
        if keys is None:
            keys = self._keys = sorted(dict.keys(self))
        i = _bisect.bisect_right(keys, offset) - 1
        # parts can overlap (a span asked for around a held one): a few back
        for j in range(i, max(i - 8, -1), -1):
            at = keys[j]
            data = dict.__getitem__(self, at)
            if at + len(data) >= end:
                return at, data
        return None

    def drop_before(self, offset):
        """Forget the parts that end before `offset` (already read past)."""
        for at in [a for a, d in dict.items(self) if a + len(d) <= offset]:
            dict.__delitem__(self, at)
        self._keys = None


class Source(object):
    """Byte source with .read(offset, size) -- local file or HTTP Range. HTTP
    reads go over ONE reused keep-alive connection (see _new_session); a 429/5xx
    trips a circuit-breaker so every later read returns b'' and we back off the
    instant the CDN pushes back. Reads are hard-capped at `size`: a server that
    ignores Range (200) at a non-zero offset yields b'' rather than the file."""

    def __init__(self, url_or_path):
        self.url = url_or_path or ''
        self.is_http = bool(re.match(r'^https?://', self.url, re.I))
        self.fetched = 0
        self.reqs = 0
        self.tripped = False          # circuit-breaker: set on a 429/5xx
        self._pace = HTTP_REQ_PACE_S  # adaptive inter-request gap (AIMD)
        self._pace_floor = HTTP_REQ_PACE_S   # ...never decays below the start
        self._429_streak = 0          # consecutive 429-needing fetches (fail-fast)
        self._clean_streak = 0        # consecutive fetches that needed no backoff
        self._429_total = 0           # how many fetches needed a backoff at all
        self._epoch_reqs = None       # requests done when the pace last widened
        self._epoch_at = 0.0          # ...and when, so one event widens it once
        self._epoch_until = 0.0       # Retry-After can make the window longer
        self._429_absorbed = 0        # 429s inside a window we're still measuring
        self._one_shot = 0            # cues resolved in ONE request, not two
        self._log = None              # set by extract_srt so pacing is visible
        self._prefix = None           # learned cluster prefix len (see below)
        self._cue_time_ok = None      # None=untested, True=CueTime verified
        self._hdr_reads = 0           # cluster-header reads we still had to pay
        self._prefix_relearn = 0      # times the learned prefix went stale
        self.http_reqs = 0            # ACTUAL GETs, retries included (see below)
        self._progress_mark = 0       # cues collected when we last moved
        self._progress_at = 0.0       # ...and when that was
        self._stats = None            # caller-visible progress/pressure dict
        self._abort_cb = None         # set by extract_srt; polled DURING sleeps
        self._piece = None            # extract_srt: largest single HTTP request
        self._multi = None            # many ranges a request: None untried, True, False
        self._pref = _PartStore()     # offset -> bytes fetched ahead (prefetch)
        self._multi_hold = 0.0        # no batched request before this (busy)
        self.total = 0
        # Every counter above is now touched by several fetch threads, and the
        # pace gate below decides ONE schedule for all of them, so both live
        # under this lock. It is held only around arithmetic -- never across a
        # network call or a sleep -- so it can never serialise the thing it
        # exists to make concurrent.
        self._lock = _threading.RLock()
        self._gate_at = 0.0           # earliest wall-clock time for the next GET
        self._sess = _new_session() if self.is_http else None
        # Spare connections for the targeted fetches. The first is `_sess`, so a
        # serial caller behaves exactly as before and nothing extra is opened
        # unless the concurrent path actually runs.
        self._spare = []
        if self._sess is not None:
            for _ in range(max(0, HTTP_CONNS - 1)):
                s = _new_session()
                if s is None:
                    break
                self._spare.append(s)
        self._free = None             # session pool, built on first concurrent use
        self._batch_sess = []         # parallel batches' extra connections
        self._range_ms = 0.0          # the last batch's cost a range (cold or warm?)
        self._parallel_off = False    # a shared set failed: one batch at a time
        self._held_at = 0.0           # when the buffer gate last stood aside mid-reading
        try:
            self._host = (re.match(r'^https?://([^/:]+)', self.url, re.I).group(1)
                          if self.is_http else '').lower()
        except Exception:
            self._host = ''
        if not self.is_http:
            try:
                self.total = os.path.getsize(self.url)
            except Exception:
                self.total = 0
        else:
            self.total = self._http_size()

    @property
    def has_session(self):
        """A reused keep-alive connection is available. HTTP extraction requires
        this so it never storms the CDN with fresh connections."""
        return self._sess is not None

    def _http_size(self):
        try:
            if self._sess is not None:
                r = self._sess.get(
                    self.url, headers={'Range': 'bytes=0-0', 'User-Agent': UA},
                    timeout=HTTP_TIMEOUT, stream=True)
                cr = r.headers.get('Content-Range') or ''
                cl = r.headers.get('Content-Length')
                try:
                    r.close()
                except Exception:
                    pass
                m = re.search(r'/(\d+)\s*$', cr)
                if m:
                    return int(m.group(1))
                return int(cl) if cl else 0
        except Exception:
            return 0
        if _urlreq is None:
            return 0
        try:
            req = _urlreq.Request(
                self.url, headers={'Range': 'bytes=0-0', 'User-Agent': UA})
            resp = _urlreq.urlopen(req, timeout=HTTP_TIMEOUT)
            cr = resp.headers.get('Content-Range') or ''
            try:
                resp.read(1)
            except Exception:
                pass
            resp.close()
            m = re.search(r'/(\d+)\s*$', cr)
            if m:
                return int(m.group(1))
            cl = resp.headers.get('Content-Length')
            return int(cl) if cl else 0
        except Exception:
            return 0

    def read(self, offset, size):
        if size <= 0 or offset < 0:
            return b''
        if not self.is_http:
            try:
                with open(self.url, 'rb') as f:
                    f.seek(offset)
                    data = f.read(size)
                self.fetched += len(data)
                return data
            except Exception:
                return b''
        if self.tripped:
            return b''
        if self._pref:
            held = self._pref_take(offset, size)
            if held is not None:
                return held
        if self._sess is not None:
            piece = self._piece
            if not piece or size <= piece:
                return self._read_session(offset, size)
            # In pieces (see _HTTP_PIECE): each one takes its own slot in the
            # paced schedule and polls the abort callback first. What comes back
            # is what one request would have returned: short on EOF or on a
            # stop, and the callers' own checks (tripped, _short_read) see it.
            out = bytearray()
            while len(out) < size:
                want = min(piece, size - len(out))
                got = self._read_session(offset + len(out), want)
                out += got
                if len(got) < want or self.tripped:
                    break
            return bytes(out)
        return self._read_urllib(offset, size)

    def _probe_fetch(self, offset, size):
        """One tiny Range GET for the EOF probe: (code, Content-Range,
        Content-Length, body) or None if the request could not be made.

        Deliberately NOT routed through _read_session: this asks a question
        about the file rather than fetching data, and it must see the status and
        the headers, which _read_session collapses into bytes. It reads at most
        `size` bytes and NEVER drains a body it did not ask for -- a provider
        that ignores Range answers with the whole file, which can be 77GB. When
        the body is small enough to finish, it IS finished, so the shared
        keep-alive connection goes back to the pool instead of being discarded
        and reconnected on the next read."""
        hdrs = {'Range': 'bytes={0}-{1}'.format(offset, offset + size - 1),
                'User-Agent': UA}
        if self._sess is not None:
            r = self._sess.get(self.url, headers=hdrs, timeout=HTTP_TIMEOUT,
                               stream=True)
            self.http_reqs += 1
            cl = r.headers.get('Content-Length')
            try:
                body = r.raw.read(size) or b''
            except Exception:
                body = b''
            try:
                small = 0 <= int(cl) <= PROBE_DRAIN_MAX
            except (TypeError, ValueError):
                small = False
            try:
                if small:
                    r.raw.read()          # finish it -> connection is reusable
            except Exception:
                pass
            try:
                r.close()
            except Exception:
                pass
            return r.status_code, r.headers.get('Content-Range'), cl, body
        if _urlreq is None:
            return None
        req = _urlreq.Request(self.url, headers=hdrs)
        try:
            resp = _urlreq.urlopen(req, timeout=HTTP_TIMEOUT)
        except Exception as e:
            # urllib RAISES on a 416 rather than returning it, and the error
            # object still carries the status and the headers -- which is where
            # the answer is. Anything without a status is not an answer.
            code = getattr(e, 'code', None)
            if code is None:
                return None
            self.http_reqs += 1
            eh = getattr(e, 'headers', None)
            return (code, eh.get('Content-Range') if eh else None,
                    eh.get('Content-Length') if eh else None, b'')
        self.http_reqs += 1
        try:
            code = getattr(resp, 'status', None) or resp.getcode()
            try:
                body = resp.read(size) or b''
            except Exception:
                body = b''
            return (code, resp.headers.get('Content-Range'),
                    resp.headers.get('Content-Length'), body)
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def probe_beyond(self, offset, known=None):
        """Is there anything in this file AT `offset`? Returns 'more', 'eof' or
        'unknown', and only ever says 'eof' on evidence about `offset` itself.

        Reading at `offset` and looking at what comes back cannot answer this.
        `read()` collapses every outcome that is not a successful fetch into the
        same b'': a tripped breaker, any exception, and a server that ignores
        the Range header and answers 200 (which this file already documents
        happening in the field) all look exactly like a genuine end of file. So
        an empty read is NOT evidence of EOF, and treating it as such lets a
        real truncation -- one that happened to land on a clean element
        boundary, where parsing cannot see it either -- be certified as a
        complete subtitle with lines missing.

        Nor is it enough to check that the connection is healthy by re-reading
        bytes from BEFORE `offset`. That proves the provider can still serve a
        region we already have; it establishes nothing about the region in
        question, and a provider inconsistent exactly at the frontier of what
        has been fetched satisfies it while data is still there. (Measured: a
        subtitle delivered as complete, missing half its lines.)

        So the question is asked in the one form a provider is obliged to answer
        precisely: a range that STARTS INSIDE the file -- at bytes the caller
        already read, passed in as `known` -- and runs past `offset`. Being
        satisfiable, it cannot be answered with a 416 or with whatever a
        provider does to an out-of-range ask, and a compliant partial response
        must carry a Content-Range stating the file's true length. That length
        settles it outright. Failing that, the answer is measured rather than
        inferred: the known bytes must come back exactly, and then whether
        anything follows them is the answer -- data past `offset` means the
        earlier response really was cut short, and data that stops dead at
        `offset` means the file does. A provider serving the wrong region, or
        cut short itself, matches nothing and stays unknown.

        For a local file the size on disk answers directly.
        """
        if not self.is_http:
            try:
                sz = os.path.getsize(self.url)
            except Exception:
                return 'unknown'
            return 'eof' if offset >= sz else 'more'
        if self.tripped:
            return 'unknown'
        try:
            c_off, c_want = known if known else (0, b'')
            if not (c_want and 0 <= c_off < offset):
                # Nothing to check the answer against. Still ask from inside the
                # file rather than at the point itself -- a satisfiable range is
                # what obliges a provider to state the real length, and asking
                # at the point is the form that several of them answer in ways
                # nothing can interpret.
                c_off, c_want = max(0, offset - PROBE_CONTROL), b''
            n = len(c_want)
            span = offset - c_off
            got = self._probe_fetch(c_off, span + PROBE_AHEAD)
            if got is None:
                return 'unknown'
            body = got[3]
            past = len(body) > span
            if n and body[:n] == c_want and past:
                # Bytes past the point, from a provider demonstrably serving the
                # right region: the earlier response really was cut short. This
                # takes precedence over any length the same response states,
                # because a response contradicting itself is not evidence of an
                # end of file -- and erring towards "truncated" only ever costs
                # an attempt. The reverse -- a body that stops dead at the point
                # -- is NOT taken as proof of the end: a response that was itself
                # capped there looks identical, and only a provider already
                # violating the spec (a partial answer with no Content-Range)
                # can leave the question in that state.
                return 'more'
            verdict = self._classify_probe(got[0], got[1], got[2], offset)
            if verdict == 'eof' and past:
                # A stated end, with bytes past it in the same breath, and no
                # way to tell which is true -- the known bytes did not come back
                # (so nothing in this response is evidence about our region) or
                # there were none to check against. A header alone is trivial to
                # produce from the request without ever seeking there, which is
                # what a templated caching layer does, so it does not get to
                # settle a question its own body disputes. Defer.
                return 'unknown'
            return verdict if verdict != 'silent' else 'unknown'
        except Exception:
            return 'unknown'

    @staticmethod
    def _classify_probe(code, content_range, content_length, offset):
        """What one probe response's STATUS AND HEADERS establish about
        `offset`. 'silent' means they establish nothing, and the body has to be
        measured instead."""
        if code == 416:
            return 'eof'
        end = _complete_length(content_range)
        if end is not None and end > 0:
            return 'eof' if offset >= end else 'more'
        if code >= 300:
            return 'unknown'          # refused / errored -- says nothing at all
        if code == 200 and offset > 0:
            # Range ignored, so Content-Length describes the WHOLE file. A
            # length beyond our offset proves there is more there. Never read
            # the reverse as a confirmation of EOF: a declared length shorter
            # than the bytes we have already read contradicts them, and a
            # contradiction confirms nothing.
            try:
                cl = int(content_length)
            except (TypeError, ValueError):
                return 'unknown'
            return 'more' if cl > offset else 'unknown'
        return 'silent'

    def _making_progress(self):
        """Are we still actually collecting cues, despite the back-pressure?

        The 429 streak-breaker exists to protect the PLAYER: the reasoning is
        that a saturated token means the movie dies. But a 429 streak is only a
        PROXY for that, and a poor one -- a provider can push back on half our
        reads while the player is completely healthy, which is exactly what a
        field log shows (TorBox: 31 backoffs in 60 reads, the player fine
        throughout, extraction abandoned at 75 of 555 cues after 225s).

        There is a DIRECT guard for player harm already, and it is the one that
        matters: the caller's abort callback watches the playback clock and stops
        us within 8s if it ever freezes. And since an interrupted pass now banks
        its progress, crawling on is strictly better than giving up -- the same
        run would have reached ~300 cues in its remaining budget instead of
        stopping at 75, turning eight play sessions into two.

        So the breaker now fires only when back-pressure has ALSO stalled us: no
        new cues for a while. Real saturation looks like that; a slow provider
        does not."""
        try:
            n = (self._stats or {}).get('done') or 0
        except Exception:
            n = 0
        import time as _t
        now = _t.time()
        if n > self._progress_mark:
            self._progress_mark = n
            self._progress_at = now
            return True
        if not self._progress_at:
            self._progress_at = now
            return True
        return (now - self._progress_at) < STALLED_PROGRESS_S

    def _sleep_or_abort(self, secs):
        """Sleep up to `secs`, in <=1s slices, polling the abort callback between
        them. Returns True the moment we're asked to stop (playback ended / the
        player stalled), so a long 429 backoff can't block us from yielding the
        token back to the player for up to two minutes."""
        import time as _t
        cb = getattr(self, '_abort_cb', None)
        remaining = secs
        while remaining > 0:
            if cb is not None:
                try:
                    if cb():
                        return True
                except Exception:
                    pass
            step = 1.0 if remaining > 1.0 else remaining
            _t.sleep(step)
            remaining -= step
        return False

    def fetched_now(self):
        """Bytes fetched so far, read under the lock. The byte cap is what keeps
        an extraction from competing with the player for the line, so with
        several fetches in flight it must not be decided on a torn read."""
        with self._lock:
            return self.fetched

    def _claim_slot(self, pace):
        """Reserve this request's slot in the one shared schedule; seconds to
        wait for it."""
        now = time.time()
        with self._lock:
            start = self._gate_at if self._gate_at > now else now
            self._gate_at = start + pace
        return start - now

    def parallel_now(self):
        """How many connections may share the next batches: _MULTI_PARALLEL
        on a measured host once a batch has shown the file is cold, else 1."""
        if (not self.can_share() or self._range_ms < COLD_RANGE_MS
                or time.time() - self._held_at < SHARE_AFTER_HOLD_S):
            return 1
        return MULTI_PARALLEL

    def can_share(self):
        """Sharing is possible here at all (a measured host, batches served,
        no shared batch has failed), cold file or not."""
        return not (MULTI_PARALLEL <= 1 or self._parallel_off
                    or self._multi is not True
                    or not self._host.endswith(PARALLEL_HOSTS))

    def prefetch_many(self, spans, keep_from=None):
        """A cold file's next ranges on parallel_now() connections that take
        batches of _MULTI_SHARE_RANGES from one queue (see _MULTI_PARALLEL).
        This thread fetches nothing: it asks the abort callback (the player's
        buffer gate) twice a second, under the lock every worker passes
        before a request -- so while the gate waits, nothing new is sent. A
        batch that fails ends the sharing for the run. Returns the parts
        that came back."""
        workers = self.parallel_now()
        if workers <= 1 or len(spans) <= MULTI_SHARE_RANGES:
            return self.prefetch(spans[:MULTI_RANGES], keep_from) if spans else 0
        if (not self.is_http or self._sess is None or self.tripped
                or time.time() < self._multi_hold):
            return 0
        groups = [spans[i:i + MULTI_SHARE_RANGES]
                  for i in range(0, len(spans), MULTI_SHARE_RANGES)]
        workers = min(workers, len(groups))
        while len(self._batch_sess) < workers:
            s = _new_session()
            if s is None:
                break
            self._batch_sess.append(s)
        workers = min(workers, len(self._batch_sess))
        if workers <= 1:
            return self.prefetch(spans[:MULTI_RANGES], keep_from)
        # the connection the head and the index came on idles meanwhile: not
        # one more on the provider's count for this link
        try:
            self._sess.close()
        except Exception:
            pass
        gate = _threading.Lock()
        stop = _threading.Event()
        state = {'got': 0, 'failed': 0, 'next': 0}

        def worker(sess):
            while not stop.is_set():
                with gate:              # held while the buffer gate waits
                    pass
                with self._lock:
                    i = state['next']
                    if i >= len(groups) or stop.is_set():
                        return
                    state['next'] = i + 1
                try:
                    n = self.prefetch(groups[i], keep_from, sess=sess, checked=True)
                except Exception:
                    n = 0
                with self._lock:
                    state['got'] += n
                    if not n:
                        state['failed'] += 1
                if not n:
                    stop.set()
                    return
        threads = [_threading.Thread(target=worker, args=(self._batch_sess[i],))
                   for i in range(workers)]
        t0 = time.time()
        for t in threads:
            t.daemon = True
            t.start()
        limit = t0 + HTTP_TIMEOUT + 60
        asked = 0.0
        while any(t.is_alive() for t in threads) and time.time() < limit:
            now = time.time()
            if (self._abort_cb is not None and not stop.is_set()
                    and now - asked >= 0.5):
                with gate:
                    try:
                        if self._abort_cb():
                            self.tripped = True
                            stop.set()
                    except Exception:
                        pass
                asked = time.time()
            time.sleep(0.05)
        stop.set()
        for t in threads:
            t.join(HTTP_TIMEOUT + 30)
        # No idle extra connections on the link between sets (review).
        for s in self._batch_sess:
            try:
                s.close()
            except Exception:
                pass
        took = time.time() - t0
        if state['failed'] and not self.tripped:
            # refused, pushed back, failed or empty: one batch at a time for
            # the rest of this run (review of 0.3.83 -- a provider that limits
            # connections per link must not keep seeing several)
            self._parallel_off = True
            if self._log:
                self._log('a shared batch did not come back -- one batch at a '
                          'time from here')
        if self._log and not getattr(self, '_parallel_logged', False):
            self._parallel_logged = True
            self._log('a cold file (%.0f ms a range): %d connections share '
                      'batches of %d -- %d range(s), %d part(s) in %.1fs'
                      % (self._range_ms, workers, MULTI_SHARE_RANGES,
                         len(spans), state['got'], took))
        return state['got']

    def _gate_wait(self, pace):
        """Wait for this request's slot in the ONE shared schedule. Returns True
        if the wait was aborted (player needs the line).

        Claiming the slot is arithmetic under the lock; the waiting happens
        outside it, so several connections can be mid-flight while the next slot
        is already reserved. That is the whole trick: the CDN's view (one
        request per `pace`) is unchanged, and only our idle time between them
        disappears."""
        now = time.time()
        with self._lock:
            start = self._gate_at if self._gate_at > now else now
            self._gate_at = start + pace
        wait = start - now
        if wait <= 0:
            return False
        return self._sleep_or_abort(wait)

    def restore_pace(self, was):
        """Put the pace back to `was` after a burst we CHOSE to make provoked
        pushback. Without this the AIMD ratchet keeps the penalty for the rest
        of the extraction -- 0.20s to 2.00s in the field, and a decay that needs
        25 clean requests per step to walk it back -- so a 29-second experiment
        cost fifteen extra minutes. Only ever lowers the pace back toward where
        it started; a provider that is genuinely slow keeps its own widening,
        because that widening was not our doing."""
        with self._lock:
            if was is None or self._pace <= was:
                return False
            had, self._pace = self._pace, max(was, self._pace_floor)
            self._429_streak = 0
            self._clean_streak = 0
            if self._stats is not None:
                self._stats['pace'] = self._pace
        if self._log:
            self._log('pushback came from our own burst -- pace back to %.2fs '
                      'from %.2fs' % (self._pace, had))
        return True

    def _take_session(self):
        """Borrow a connection. Serial callers always get the original one, so
        nothing about the single-connection path changes; the spares are handed
        out only when something is already using it."""
        free = self._free
        if free is None:
            return self._sess
        try:
            return free.get(timeout=HTTP_TIMEOUT)
        except Exception:
            return self._sess

    def _give_session(self, sess):
        free = self._free
        if free is None or sess is None:
            return
        try:
            free.put_nowait(sess)
        except Exception:
            pass

    def open_pool(self):
        """Arm the connection pool. Returns how many connections are available
        (1 = nothing to parallelise, so callers stay serial)."""
        if self._free is not None:
            return self._free.qsize()
        if self._sess is None or not self._spare:
            return 1
        try:
            import queue as _q
        except Exception:
            return 1
        free = _q.Queue()
        free.put(self._sess)
        for s in self._spare:
            free.put(s)
        self._free = free
        return free.qsize()

    def close_pool(self):
        """Give the spare connections back to the OS as soon as the concurrent
        phase is over -- the player keeps the line for the rest of playback."""
        self._free = None
        for s in self._spare:
            try:
                s.close()
            except Exception:
                pass
        self._spare = []

    def _pref_take(self, offset, size):
        """The bytes [offset, offset + size) from what prefetch() holds, or
        None. A part is handed out once."""
        end = offset + size
        if self.total:
            end = min(end, self.total)     # a part cut at the end of the file is whole
        hit = self._pref.covering(offset, end)
        if hit is None:
            return None
        start, data = hit
        # Kept, not handed out once: ranges asked for as one serve every
        # cluster inside them. The store is bounded where a batch is stored.
        return data[offset - start:offset - start + size]

    def holds(self, offset, size=1):
        """Is all of [offset, offset + size) inside one thing prefetch() holds
        (a part of its own, or ranges that were asked for as one)? Only the
        start is not enough: a window that spills past the next cluster's head
        would skip that cluster's batch (review of 0.3.82)."""
        end = offset + size
        if self.total:
            end = min(end, self.total)
        return self._pref.covering(offset, end) is not None

    def prefetch(self, spans, keep_from=None, sess=None, checked=False):
        """Fetch `spans` [(offset, size), ...] in ONE request (HTTP multi-
        range) into the store read() serves from. Returns how many parts came
        back. Takes its slot in the paced schedule and asks the abort callback
        first, like any read. Anything but a sound multipart answer (or one
        part for one span) switches it off for the rest of the run: the reads
        then go one range a request, as before. Never reads a body larger than
        the spans asked for. `sess`/`checked`: one batch of a parallel set
        (prefetch_many) -- its own connection, and the abort callback already
        asked for the whole set in the calling thread (the callback is not
        made to be entered by several threads at once)."""
        if (not self.is_http or self._sess is None or self.tripped
                or self._multi is False or not spans
                or time.time() < self._multi_hold):
            return 0
        sess = sess or self._sess
        with self._lock:
            self.reqs += 1
            n = self.reqs
            _pace = getattr(self, '_pace', HTTP_REQ_PACE_S)
        if n > 1 and _pace > 0:
            if checked:
                wait = self._claim_slot(_pace)
                if wait > 0:
                    time.sleep(wait)
            elif self._gate_wait(_pace):
                self.tripped = True
                return 0
        if self._abort_cb is not None and not checked:
            try:
                if self._abort_cb():
                    self.tripped = True
                    return 0
            except Exception:
                pass
        want = sum(k for _o, k in spans)
        rng = 'bytes=' + ','.join('%d-%d' % (o, o + k - 1) for o, k in spans)
        r = None
        t0 = time.time()
        try:
            with self._lock:
                self.http_reqs += 1
            r = sess.get(self.url, headers={'Range': rng, 'User-Agent': UA},
                         timeout=HTTP_TIMEOUT, stream=True)
            code = r.status_code
            ctype = r.headers.get('Content-Type') or ''
            try:
                length = int(r.headers.get('Content-Length') or 0)
            except (TypeError, ValueError):
                length = 0
            if code == 429 or code >= 500:
                # pressure, not a refusal of the form: the next blocks go one
                # range a request, where the back-off lives, and no batch is
                # asked for a while (one before every block would double the
                # requests to a provider that is already pushing back). It is
                # pushback like any other: counted, and the pace widened once
                # per event (review of 0.3.81, round 5).
                ra = 0.0
                try:
                    ra = float(r.headers.get('Retry-After') or 0)
                except (TypeError, ValueError):
                    ra = 0.0
                if not (0.0 <= ra <= HTTP_429_MAX_WAIT):
                    ra = 0.0
                _now = time.time()
                with self._lock:
                    self._429_total += 1
                    _was = self._pace
                    if not (self._epoch_reqs is not None
                            and (self.http_reqs - self._epoch_reqs
                                 < HTTP_PACE_EPOCH_REQS
                                 or _now < self._epoch_at + HTTP_PACE_EPOCH_S
                                 or _now < self._epoch_until)):
                        self._pace = min(_was * 1.5, HTTP_REQ_PACE_MAX)
                        self._clean_streak = 0
                        self._epoch_reqs = self.http_reqs
                        self._epoch_at = _now
                        self._epoch_until = _now + ra if ra else 0.0
                    else:
                        self._429_absorbed += 1
                    if self._stats is not None:
                        self._stats['backoffs'] = self._429_total
                        self._stats['pace'] = self._pace
                self._multi_busy('HTTP %s' % code, max(MULTI_BUSY_HOLD_S, ra))
                return 0
            multipart = 'multipart/byteranges' in ctype.lower()
            if (code != 206 or length <= 0 or length > want + MULTI_SLACK
                    or (len(spans) > 1 and not multipart)):
                self._multi_off('HTTP %s, %s, %s byte(s)' % (code, ctype[:40] or '-',
                                                             length or '?'))
                return 0
            body = bytearray()
            while len(body) < length:
                chunk = r.raw.read(length - len(body))
                if not chunk:
                    break
                body += chunk
            body = bytes(body)
            if len(body) < length:
                # the connection dropped mid-answer: a bad moment, not a
                # refusal of the form
                self._multi_busy('a short answer (%d of %d bytes)' % (len(body), length))
                return 0
            if multipart:
                parts = _parse_byteranges(body, ctype)
            else:
                start = _range_start(r.headers.get('Content-Range'))
                parts = [(start, body)] if start == spans[0][0] else []
            if not parts:
                self._multi_off('an answer that could not be split')
                return 0
            with self._lock:
                self.fetched += len(body)
            with self._lock:
                if (len(self._pref) > MULTI_STORE_PARTS
                        or sum(len(d) for d in self._pref.values())
                        > MULTI_STORE_MAX):
                    # what earlier batches left (each set is read before the
                    # next is asked for): never more than a couple in memory.
                    # What is already behind goes first -- a part ahead may be
                    # one this batch left out because it was held (review of
                    # 0.3.82), or a part of a batch of the same set.
                    if keep_from is not None:
                        self._pref.drop_before(keep_from)
                    if (len(self._pref) > MULTI_STORE_PARTS
                            or sum(len(d) for d in self._pref.values())
                            > MULTI_STORE_MAX):
                        self._pref = _PartStore()
                for start, data in parts:
                    self._pref[start] = data
            if self._multi is None and self._log:
                self._log('%d ranges in one request: %d part(s), %.0f KB in '
                          '%.1fs' % (len(spans), len(parts), len(body) / 1024.0,
                                     time.time() - t0))
            self._multi = True
            if len(spans) >= 8 and len(parts) >= len(spans):
                self._range_ms = (time.time() - t0) * 1000.0 / len(spans)
            return len(parts)
        except Exception as ex:
            # a timeout or a reset: TorBox's bad minutes are full of them,
            # and the run should not lose the batches for good over one
            self._multi_busy(type(ex).__name__)
            return 0
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass

    def _multi_busy(self, why, hold=None):
        self._multi_hold = time.time() + (hold or MULTI_BUSY_HOLD_S)
        if self._log:
            self._log('a batched request failed (%s) -- one range a request for '
                      '%.0fs' % (why, hold or MULTI_BUSY_HOLD_S))

    def _multi_off(self, why):
        if self._multi is not False and self._log:
            self._log('many ranges in one request not served (%s) -- one range '
                      'a request' % why)
        with self._lock:
            self._multi = False
            self._pref = _PartStore()

    def release_connection(self):
        """Close the idle keep-alive connection(s) and keep the source usable:
        the next read opens a fresh one. For a caller that stands aside while
        the player refills -- an open connection we are not using is still one
        more on the provider's count for this link. Only between reads."""
        # ...a shared set's too: its workers wait at the gate meanwhile, and a
        # request still in flight keeps its connection until it is answered
        # (review of 0.3.84: six idle connections sat on the link for the
        # whole wait).
        for sess in [self._sess] + list(self._batch_sess):
            if sess is not None:
                try:
                    sess.close()
                except Exception:
                    pass
        if self._multi is True:
            # mid-reading (the wait before the first batch is every pick's
            # start): no shared set for a while (_SHARE_AFTER_HOLD_S)
            self._held_at = time.time()

    def close(self):
        """Release every HTTP session owned by this byte source."""
        self.close_pool()
        for s in self._batch_sess:
            try:
                s.close()
            except Exception:
                pass
        self._batch_sess = []
        sess, self._sess = self._sess, None
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass

    def _read_session(self, offset, size):
        """One Range GET over the SHARED keep-alive connection. Single-range,
        never multipart (a fat multi-range body starves the hardware decoder). On
        a 429/5xx we BACK OFF (honor Retry-After) and retry a few times -- a
        debrid CDN token rate-limits by request count and recovers after a short
        cooldown -- and only trip the breaker after exhausting retries. Both the
        per-request pace and the backoff are abort-aware, so a stalled player is
        noticed within ~1s instead of behind a 2-minute retry storm."""
        # `reqs` counts LOGICAL reads; one of them can cost several GETs, because
        # a 429 is retried in place. On a provider that pushes back on half our
        # reads that is a large, invisible multiplier -- the field log's "60 req"
        # was really closer to 110 as far as the CDN was concerned, which both
        # understated the load and hid it from anyone reading the log. Count the
        # GETs separately and report both.
        with self._lock:
            self.reqs += 1
            n = self.reqs
            _pace = getattr(self, '_pace', HTTP_REQ_PACE_S)
        saw_429 = False               # did THIS fetch need a 429 backoff?
        if n > 1 and _pace > 0:
            # THE rate limiter, and the reason concurrency does not multiply the
            # load: every connection takes its slot from one shared schedule, so
            # the CDN sees at most one request per `_pace` seconds in total --
            # the same rate as when this was serial. What concurrency removes is
            # only the round trip we used to pay AFTER the gap, in series.
            if self._gate_wait(_pace):
                self.tripped = True
                return b''
        sess = self._take_session()
        try:
            return self._read_session_inner(sess, offset, size, saw_429)
        finally:
            self._give_session(sess)

    def _read_session_inner(self, sess, offset, size, saw_429):
        for _attempt in range(HTTP_429_RETRIES):
            if getattr(self, '_abort_cb', None) is not None:
                try:
                    if self._abort_cb():
                        self.tripped = True
                        return b''
                except Exception:
                    pass
            r = None
            try:
                with self._lock:
                    self.http_reqs += 1
                r = sess.get(self.url, headers={
                    'Range': 'bytes={0}-{1}'.format(offset, offset + size - 1),
                    'User-Agent': UA}, timeout=HTTP_TIMEOUT, stream=True)
                code = r.status_code
                if code == 429 or code >= 500:
                    saw_429 = True
                    ra = r.headers.get('Retry-After')
                    # Back-pressure: widen the pace so we ease off the contended
                    # token (protects the player, converges toward a rate the CDN
                    # tolerates). Persists for the rest of this extraction.
                    #
                    # ONCE per congestion event, though -- see _HTTP_PACE_EPOCH_*.
                    # Until the pace we just set has been measured, further 429s
                    # are the same event arriving again (very often literally the
                    # retries of this same fetch) and must not compound.
                    try:
                        _now = time.time()
                        with self._lock:
                            _was = getattr(self, '_pace', HTTP_REQ_PACE_S)
                            self._429_total += 1
                            _measuring = (
                                self._epoch_reqs is not None
                                and (self.http_reqs - self._epoch_reqs
                                     < HTTP_PACE_EPOCH_REQS
                                     or _now < self._epoch_at
                                     + HTTP_PACE_EPOCH_S
                                     or _now < self._epoch_until))
                            if _measuring:
                                self._429_absorbed += 1
                            else:
                                self._pace = min(_was * 1.5,
                                                 HTTP_REQ_PACE_MAX)
                                self._clean_streak = 0
                                self._epoch_reqs = self.http_reqs
                                self._epoch_at = _now
                                # The CDN told us how long it wants: that IS the
                                # window, so never re-widen inside it. Anything
                                # unparseable or non-finite just leaves the
                                # request/seconds window above to decide.
                                self._epoch_until = 0.0
                                if ra:
                                    try:
                                        _ra = float(ra)
                                    except (TypeError, ValueError):
                                        _ra = 0.0
                                    if 0.0 < _ra < 1e6:
                                        self._epoch_until = _now + min(
                                            _ra, HTTP_429_MAX_WAIT)
                        if self._stats is not None:
                            self._stats['backoffs'] = self._429_total
                            self._stats['pace'] = self._pace
                        # Report the first one and then each doubling: the pace
                        # creeping 0.2s -> 2.0s is what turns a 3-minute extract
                        # into an 18-minute one, and it used to leave no trace
                        # at all in the log.
                        if self._log and not _measuring and (
                                self._429_total == 1
                                or self._pace >= _was * 2
                                or self._pace >= HTTP_REQ_PACE_MAX > _was):
                            self._log(
                                'CDN pushed back (%d so far, %d absorbed as the '
                                'same event, HTTP %s) -- pace %.2fs -> %.2fs '
                                'after %d request(s)'
                                % (self._429_total, self._429_absorbed, code,
                                   _was, self._pace, self.reqs))
                    except Exception:
                        pass
                    try:
                        r.close()
                    except Exception:
                        pass
                    r = None
                    if _attempt >= HTTP_429_RETRIES - 1:
                        # UNCONDITIONAL, deliberately. This is what guarantees a
                        # partial extract is never delivered: without `tripped`
                        # the pass can run to completion with reads that returned
                        # nothing and hand back a short subtitle as if it were
                        # whole. (Tried gating it on progress, the way the streak
                        # breaker is gated -- a fully-refusing provider then
                        # produced an SRT instead of deferring.)
                        self.tripped = True   # limiter not recovering -> give up
                        return b''
                    try:
                        wait = (min(float(ra), HTTP_429_MAX_WAIT) if ra
                                else min(HTTP_429_BASE_WAIT * (2 ** _attempt), HTTP_429_MAX_WAIT))
                        # A negative / NaN / -inf Retry-After parses via float()
                        # WITHOUT raising, but then time.sleep(wait) throws -- and
                        # the outer `except Exception` would swallow it and return
                        # b'' WITHOUT ever setting self.tripped, silently killing
                        # both the backoff AND the breaker (unbounded hammering).
                        # Reject any non-finite / out-of-range value up front.
                        if not (0 <= wait <= HTTP_429_MAX_WAIT):
                            raise ValueError
                    except (ValueError, TypeError, OverflowError):
                        wait = min(HTTP_429_BASE_WAIT * (2 ** _attempt), HTTP_429_MAX_WAIT)
                    if self._sleep_or_abort(wait):
                        self.tripped = True   # player needs the token -- stop now
                        return b''
                    # The retry is another GET, so it takes another slot in the
                    # shared schedule. Without this a refused fetch could replay
                    # on its own clock while other connections were dispatching,
                    # which is the one way concurrency could have pushed the rate
                    # the CDN sees above the paced one.
                    with self._lock:
                        _p = getattr(self, '_pace', HTTP_REQ_PACE_S)
                    if _p > 0 and self._gate_wait(_p):
                        self.tripped = True
                        return b''
                    continue
                if code == 200 and offset > 0:
                    return b''
                # Full-body fill-loop: a single r.raw.read(size) short-reads on a
                # urllib3 stream (returned 2 cues/1568 in the field); read until
                # `size` bytes or EOF, exactly like requests' own resp.content.
                buf = bytearray()
                while len(buf) < size:
                    chunk = r.raw.read(size - len(buf))
                    if not chunk:
                        break
                    buf += chunk
                data = bytes(buf)
                # Fail-fast bookkeeping: a clean fetch resets the streak; a fetch
                # that only succeeded after 429 backoff extends it. Too many in a
                # row => the token is saturated (TorBox), so trip the breaker now
                # rather than crawl for minutes with the token held hot. This
                # cue's data is still returned; the caller sees `tripped` next and
                # defers (a partial extract is never delivered anyway).
                #
                # All of it under the lock: with several connections these are
                # read-modify-write on shared state, and a lost increment here
                # would mean a byte cap that never trips or a pace that decays
                # while the CDN is still pushing back.
                _decayed = None
                with self._lock:
                    self.fetched += len(data)
                    if saw_429:
                        self._429_streak = getattr(self, '_429_streak', 0) + 1
                        self._clean_streak = 0
                        if (self._429_streak >= HTTP_429_STREAK_MAX
                                and not self._making_progress()):
                            self.tripped = True
                    else:
                        self._429_streak = 0
                        # Additive-increase half of AIMD: a long clean run is
                        # evidence the widened pace is now costing time for
                        # nothing, so give some of it back. Never below the
                        # floor, and never below where we started.
                        self._clean_streak = getattr(self, '_clean_streak', 0) + 1
                        if (self._clean_streak >= HTTP_PACE_DECAY_AFTER
                                and self._pace > self._pace_floor):
                            self._clean_streak = 0
                            _decayed = self._pace
                            self._pace = max(self._pace * HTTP_PACE_DECAY,
                                             self._pace_floor)
                if _decayed is not None:
                    # Own try/except, like its sibling above. This sits inside
                    # the method's broad `except Exception: return b''`, so a log
                    # callable that raises here would DISCARD a read that already
                    # succeeded -- and report it as a network failure, without
                    # setting `tripped`, so the caller could not tell the loss had
                    # nothing to do with the CDN.
                    try:
                        if self._stats is not None:
                            self._stats['pace'] = self._pace
                        if self._log:
                            self._log('CDN quiet for %d request(s) -- pace '
                                      '%.2fs -> %.2fs'
                                      % (HTTP_PACE_DECAY_AFTER, _decayed,
                                         self._pace))
                    except Exception:
                        pass
                return data
            except Exception:
                return b''
            finally:
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass
        return b''

    def _read_urllib(self, offset, size):
        if _urlreq is None:
            return b''
        try:
            req = _urlreq.Request(self.url, headers={
                'Range': 'bytes={0}-{1}'.format(offset, offset + size - 1),
                'User-Agent': UA})
            resp = _urlreq.urlopen(req, timeout=HTTP_TIMEOUT)
            code = getattr(resp, 'status', None) or resp.getcode()
            if code == 200 and offset > 0:
                resp.close()
                return b''
            # Fill-loop (see _read_session): a single resp.read(size) may return
            # short; accumulate until `size` bytes or EOF.
            buf = bytearray()
            while len(buf) < size:
                chunk = resp.read(size - len(buf))
                if not chunk:
                    break
                buf += chunk
            resp.close()
            data = bytes(buf)
            self.fetched += len(data)
            return data
        except Exception:
            return b''


def _range_start(content_range):
    m = re.match(r'\s*bytes\s+(\d+)-\d+/', content_range or '')
    return int(m.group(1)) if m else None


def _parse_byteranges(body, ctype):
    """[(start, bytes), ...] from a multipart/byteranges body, or [] when it
    is not one that can be split soundly (every part must carry a Content-Range
    and be as long as it says)."""
    m = re.search(r'boundary=("?)([^";]+)\1', ctype or '')
    if not m:
        return []
    sep = b'--' + m.group(2).strip().encode('latin-1')
    out = []
    pos = 0
    while True:
        i = body.find(sep, pos)
        if i < 0:
            break
        j = i + len(sep)
        if body[j:j + 2] == b'--':
            break                                   # the closing boundary
        head_end = body.find(b'\r\n\r\n', j)
        if head_end < 0:
            return []
        head = body[j:head_end].decode('latin-1', 'replace')
        cr = re.search(r'Content-Range:\s*bytes\s+(\d+)-(\d+)/', head, re.I)
        if not cr:
            return []
        a, b = int(cr.group(1)), int(cr.group(2))
        start = head_end + 4
        data = body[start:start + (b - a + 1)]
        if len(data) != b - a + 1:
            return []
        out.append((a, data))
        pos = start + len(data)
    return out
