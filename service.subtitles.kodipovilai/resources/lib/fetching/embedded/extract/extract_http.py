# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Text extraction over HTTP (debrid) Range requests: the Cues-guided visit of the clusters that hold subtitle
# data, the cluster header / block fetches, the windows and the concurrent reads.

import threading as _threading
import time
from resources.lib.fetching.embedded.extract.ebml import aborted, BLOCK, BLOCKDUR, BLOCKGROUP, Buf, CLUSTER, read_uint, read_vint, SIMPLEBLOCK, TIMESTAMP, walk
from resources.lib.fetching.embedded.extract.tuning import BLOCK_READ_HTTP, BLOCK_READ_MULTI, CALLBACK_POLL_S, CLUSTER_BATCH_MAX, CLUSTER_HDR_READ, CLUSTER_HEAD_PEEK, CLUSTER_TOPUP_MAX, CLUSTER_WINDOW_HTTP, COALESCE_GAP, CONC_SEED_MAX, HTTP_TOTAL_CAP, MAX_RANGE, MULTI_PROBE_RANGES, MULTI_RANGES, ONE_SHOT_MAX, PREFIX_MAX, PREFIX_MIN, PROBE_CONTROL
from resources.lib.fetching.embedded.extract.cues import block_frame
from resources.lib.fetching.embedded.extract.clusters import collect_one_cluster
from resources.lib.fetching.embedded.extract.srt_rows import entries_to_srt


def _coalesce_ranges(positions, window, gap, max_range, total):
    """Group sorted cluster positions into (start, end, [positions]) sweep
    ranges: each covers ~`window` bytes per cluster, and adjacent positions
    within `gap` share ONE Range request (his proven request-count cut). Capped
    at `max_range` per range and the file size."""
    ranges = []
    ps = sorted(set(positions))
    if not ps:
        return ranges
    cap = total or (ps[-1] + window)
    cur_start = ps[0]
    cur_end = cur_start + window
    cur = [cur_start]
    for p in ps[1:]:
        if p < cur_end + gap and (p + window - cur_start) <= max_range:
            cur_end = max(cur_end, p + window)
            cur.append(p)
        else:
            ranges.append((cur_start, min(cur_end, cap), cur))
            cur_start, cur_end, cur = p, p + window, [p]
    ranges.append((cur_start, min(cur_end, cap), cur))
    return ranges


def steps_in_ticks(steps, entries, scale_ms, origin_ms, codec):
    """extract_srt's `steps` (film time in ms, SRT text) as _extract_cues
    takes them (the file's ticks, nothing): a cue at tick T starts at
    T * scale_ms - origin_ms, the same sum _entries_to_srt makes."""
    window, show, timeline = steps[:3]
    moved = steps[3] if len(steps) > 3 else None

    def window_ticks():
        span = window()
        if not span:
            return None
        return ((float(span[0]) + origin_ms) / scale_ms,
                (float(span[1]) + origin_ms) / scale_ms)

    def show_so_far():
        show(entries_to_srt(entries, scale_ms, origin_ms, codec))

    def timeline_ms(ticks):
        timeline([int(t * scale_ms - origin_ms) for t in ticks])

    if moved is None:
        return (window_ticks, show_so_far, timeline_ms)
    return (window_ticks, show_so_far, timeline_ms, moved)


# How many windows one extraction may read before the complete pass takes
# over whatever is left (a guard against a caller that never says "the rest";
# a slow provider gets small windows, and a long film needs many of them).
WINDOWS_MAX = 64


def extract_in_windows(src, positions, entries, want, deadline_s, t0,
                        abort_cb, log, progress_cb, done, scan, steps, save):
    """The part of the track the viewer needs next, before the rest of it.

    Field report (Nov 2026): "extracting the embedded subtitle takes half the
    film before any translation shows". Nothing could be shown before the
    last cue was read, and at the pace a strict provider tolerates that is
    minutes for an episode and many more for a film.

    `steps` is (window, show, timeline[, moved]): timeline(ticks) is handed
    every cue's time once; window() names a span of film time in ticks, or
    None to stop; show() is called after each span with nothing (the caller
    reads `entries`). Each span is read by the ordinary pass over the cues of
    every cluster that has a cue inside it. moved(), if given, is asked
    between clusters: True when the viewer has jumped where the span does not
    help, and the span is then cut short -- what was read stays read, and the
    next window() starts from the viewer (QA 14 Nov 2026: after a jump back,
    Hebrew came back only once the window in progress was done, ~2 min on a
    film).

    WHOLE CLUSTERS, never a slice of one. _extract_cues_http records a
    cluster as finished once the cues it was handed came back, and a finished
    cluster is never read again -- so handing it only the cues before a mark
    would lose, for good, every later cue of a cluster that straddles the
    mark (review of the first attempt at this, 0.3.79).

    Returns False when a span could not be finished (the caller saves what
    was read and gives up, as for any interrupted pass -- nothing may follow
    it on the same connection budget). True otherwise, with `done` holding
    the clusters read; the caller's complete pass skips them."""
    window, show, timeline = steps[:3]
    moved = steps[3] if len(steps) > 3 else None
    if any(p[2] is None for p in positions):
        # Cues without a time cannot be placed in a span. That is every cue
        # of a file whose Cues carry no CueRelativePosition (_read_cues): such
        # a file is read in one pass, as before.
        return True
    try:
        timeline(sorted(p[2] for p in positions))
    except Exception as e:
        log('timeline(): %r -- reading in one pass' % (e,))
        return True
    every = set(p[0] for p in positions)
    total = len(positions)
    for _round in range(WINDOWS_MAX):
        try:
            span = window()
        except Exception as e:
            log('window(): %r -- reading the rest in one pass' % (e,))
            return True
        if not span:
            return True
        low, high = span
        picked = set(p[0] for p in positions if low <= p[2] <= high)
        if not (every - done - picked):
            # this span is all that is left: the complete pass reads it, and
            # reports and clears the resume file as it always has
            return True
        todo = picked - done
        if todo:
            part = [p for p in positions if p[0] in picked]
            before = sum(1 for p in positions if p[0] in done)
            carried = sum(1 for p in part if p[0] in done)

            def _progress(n, _total, _before=before, _carried=carried):
                # the pass counts within its span; the caller's bar and the
                # "stopped at N%" message are about the whole track
                n_all = min(total, _before + max(0, n - _carried))
                if src._stats is not None:
                    src._stats['done'] = n_all
                    src._stats['total'] = total
                if progress_cb:
                    progress_cb(n_all, total)

            # Clusters an earlier attempt left for a window scan are scanned
            # by the pass that is handed them: only this span's here.
            scan_here = set(c for c in scan if c in picked)
            # (Each pass closes the connection pool it opened, so a window
            # after the first runs on one connection. Nothing is lost while
            # _HTTP_CONNS is 1; if concurrency is switched back on, the pool
            # has to outlive the windows.)
            ok = extract_cues_http(src, part, entries, want, deadline_s, t0,
                                    abort_cb, log, _progress, done, scan_here,
                                    **({'moved_cb': moved} if moved else {}))
            scan.difference_update(picked)
            scan.update(scan_here)
            if not ok:
                return False
            try:
                save()
            except Exception:
                pass
            log('window %d: film %.0f..%.0f (ticks), %d cluster(s) %s, %d of '
                '%d done' % (_round + 1, low, high, len(todo),
                             'asked, cut short by a jump' if ok == _MOVED
                             else 'read', len(done & every), len(every)))
        try:
            show()
        except Exception as e:
            log('show(): %r' % (e,))
    return True


def _cluster_prefix_and_ts(header):
    """From bytes that START at a Cluster element, return (prefix_len,
    cluster_ts). `prefix_len` = Cluster-ID length + Size-VINT length -- the byte
    distance from the cluster start to the first octet of cluster DATA, which is
    exactly what CueRelativePosition is measured from. `cluster_ts` = the
    cluster's Timestamp (its first child; a small header read always contains
    it). (None, None) when the bytes don't start with a Cluster or are too
    short to resolve both."""
    b = Buf(header, 0)
    eid, idlen = read_vint(b, True)
    if eid != CLUSTER or idlen == 0:
        return None, None
    size, slen = read_vint(b, False)   # size may be None (unknown-size) -- ok
    if slen == 0:
        return None, None
    prefix = idlen + slen
    cluster_ts = None
    limit = b.n if size is None else min(b.n, b.p + size)
    while b.p < limit:
        ceid, _cidl = read_vint(b, True)
        if ceid is None:
            break
        csize, cslen = read_vint(b, False)
        if cslen == 0 or csize is None:
            break
        cst = b.p
        if cst + csize > b.n:
            break
        if ceid == TIMESTAMP:
            cluster_ts = read_uint(header[cst:cst + csize])
            break
        b.p = cst + csize
    if cluster_ts is None:
        return None, None
    return prefix, cluster_ts


def _collect_one_block(window, want_track, cluster_ts, out):
    """Parse ONE (Simple)Block or BlockGroup element at the START of `window` (a
    CueRelativePosition target). Append (abs_ticks, dur_or_None, frame) when it
    carries want_track. Returns True when a want_track block was found & appended;
    False when the element is truncated by the window OR is not want_track's block
    (the caller then falls back to a full window-scan of the cluster, so a mis-
    resolved target never silently drops a line)."""
    b = Buf(window, 0)
    eid, _l = read_vint(b, True)
    if eid is None:
        return False
    size, slen = read_vint(b, False)
    if slen == 0 or size is None:
        return False
    if b.p + size > b.n:
        return False   # element runs past the window -> truncated
    payload = window[b.p:b.p + size]
    if eid == SIMPLEBLOCK:
        r = block_frame(payload, cluster_ts, want_track)
        if r:
            out.append((r[0], None, r[1]))
            return True
        return False
    if eid == BLOCKGROUP:
        gbuf = Buf(payload, 0)
        block, gdur = None, None
        for geid, gsize, gstart in walk(gbuf, len(payload)):
            if gsize is None:
                break
            gp = payload[gstart:gstart + gsize]
            gbuf.p = gstart + gsize
            if geid == BLOCK:
                block = gp
            elif geid == BLOCKDUR:
                gdur = read_uint(gp)
        if block:
            r = block_frame(block, cluster_ts, want_track)
            if r:
                out.append((r[0], gdur, r[1]))
                return True
        return False
    return False


def _batch_relposes(items):
    """Group ONE cluster's [(relpos, cue_time), ...] (sorted) into read batches.
    Blocks within _CLUSTER_BATCH_MAX of each other share a single range request;
    anything further apart gets its own, because the bytes in between are video
    the player needs and are not worth a saved round-trip."""
    batches = []
    cur = [items[0]]
    for it in items[1:]:
        if (it[0] - cur[0][0]) + BLOCK_READ_HTTP <= CLUSTER_BATCH_MAX:
            cur.append(it)
        else:
            batches.append(cur)
            cur = [it]
    batches.append(cur)
    return batches


def _read_cluster_header(src, cpos, first_relpos):
    """(prefix, cluster_ts, header_bytes) for the cluster at `cpos`, or
    (None, None, b''). Sized to swallow the first block too when that block sits
    close enough that one request beats two."""
    need = first_relpos + PREFIX_MAX + BLOCK_READ_HTTP
    one_shot = 0 <= first_relpos and need <= ONE_SHOT_MAX
    with src._lock:
        src._hdr_reads += 1
    header = src.read(cpos, need if one_shot else CLUSTER_HDR_READ)
    if src.tripped or not header:
        return None, None, b''
    prefix, cluster_ts = _cluster_prefix_and_ts(header)
    if prefix is None:
        return None, None, b''
    return prefix, cluster_ts, header


def join_spans(spans):
    """[(offset, size), ...] sorted, with the ones that meet or overlap as one."""
    out = []
    for at, size in sorted(spans):
        if out and at <= out[-1][0] + out[-1][1]:
            last_at, last_size = out[-1]
            out[-1] = (last_at, max(last_size, at + size - last_at))
        else:
            out.append((at, size))
    return out


def _held_prefix(src, cpos):
    """This cluster's own prefix (ID + size VINT length) from the head a
    batched request fetched (see _CLUSTER_HEAD_PEEK), or None."""
    pref = getattr(src, '_pref', None)
    if not pref:
        return None
    head = pref.get(cpos)
    if head is not None and len(head) <= CLUSTER_HEAD_PEEK:
        del pref[cpos]          # only the head; a longer part also holds a block
    elif head is None:
        # inside a part that also holds the cluster before it (ranges that
        # met were asked for as one)
        hit = (pref.covering(cpos, cpos + PREFIX_MAX)
               if hasattr(pref, 'covering') else
               next(((s, d) for s, d in pref.items()
                     if s < cpos and s + len(d) >= cpos + PREFIX_MAX), None))
        if hit is not None:
            start, data = hit
            head = data[cpos - start:cpos - start + CLUSTER_HEAD_PEEK]
    if head is None or len(head) < 6:
        return None
    b = Buf(head, cpos)
    eid, idl = read_vint(b, True)
    if eid != CLUSTER:
        return None
    _size, slen = read_vint(b, False)
    if not slen:
        return None
    return idl + slen


def _fetch_cluster_blocks(src, cpos, items, want, entries, log):
    """CueRelativePosition FAST PATH for ONE cluster. `items` is that cluster's
    [(relpos, cue_time), ...], sorted by relpos. Returns how many of them were
    resolved; the caller window-scans the whole cluster whenever that is short of
    len(items), so a miss can never silently drop a line.

    A block sits at cpos + prefix + relpos, and its absolute timestamp is
    cluster_ts + the block's own 16-bit offset. Both `prefix` and `cluster_ts`
    used to come from an 8KB read at the cluster start, spent AGAIN for every
    single cue -- which is where 2.03-2.10 requests per cue came from. Here the
    prefix is learned once per file and CueTime supplies the timestamp, so the
    steady state is ONE request per block (or per nearby group of them). The
    first cluster still pays the header read, and its answers are used to VERIFY
    both shortcuts before any later cluster relies on them."""
    resolved = 0
    prefix, cluster_ts, header = src._prefix, None, b''
    own = _held_prefix(src, cpos)
    if own is not None:
        prefix = own
    # Take the header when we have not learned the prefix yet, when CueTime has
    # not been proven to agree with it, or when any cue in THIS cluster has no
    # CueTime to stand in for the cluster timestamp. (`_cue_time_ok is False`
    # means the file's CueTimes were wrong, so every cluster keeps paying for a
    # header -- correct, just not fast.)
    if (prefix is None or src._cue_time_ok is not True
            or any(t is None for _r, t in items)):
        prefix, cluster_ts, header = _read_cluster_header(src, cpos, items[0][0])
        if prefix is None:
            return 0
        with src._lock:
            src._prefix = prefix

    def _place(buf, base, relpos, cue_time):
        """Parse the block at `relpos` out of `buf`, which starts at file offset
        `base`. Appends to `entries` on success. Returns True/False."""
        off = (cpos + prefix + relpos) - base
        if off < 0:
            return False
        if not (0 <= off < len(buf)):
            return False
        got = []
        if not _collect_one_block(buf[off:], want, cluster_ts or 0, got):
            return False
        ticks, dur, frame = got[0]
        if cluster_ts is None:
            # No header this time: `ticks` is the block's own 16-bit offset and
            # CueTime is the absolute time. (Only reachable once _cue_time_ok is
            # True, i.e. after this identity was checked against a real header.)
            entries.append((cue_time, dur, frame))
        else:
            entries.append((ticks, dur, frame))
            if cue_time is not None and src._cue_time_ok is None:
                # THE CHECK. Does this file's CueTime really equal the timestamp
                # the header math produces? Only if it does may later clusters
                # skip their header read.
                if ticks != cue_time:
                    src._cue_time_ok = False
                    if src._log:
                        src._log('CueTime disagrees with the cluster timestamp '
                                 '(%s vs %s) -- keeping the per-cluster header '
                                 'read' % (cue_time, ticks))
                elif cluster_ts:
                    # Agreement only PROVES anything at a non-zero cluster
                    # timestamp. The first cluster of every file is at 0, where
                    # cluster_ts + rel_ts == rel_ts -- so a muxer that wrote
                    # CueTime RELATIVE to its cluster instead of absolute would
                    # pass there and be wrong everywhere after it. Stay
                    # undecided (and keep paying for headers) until a cluster
                    # with a real timestamp settles it.
                    src._cue_time_ok = True
        return True

    def _own_prefix(buf, base, relpos, cue_time):
        """This cluster's prefix found inside a batch window that starts at
        the shortest one: the block parses at exactly its own. The learned one
        was tried already."""
        nonlocal prefix
        learned = prefix
        # one byte longer first: a 3-byte size VINT next to 4-byte ones is the
        # common case (review)
        for p in sorted(range(PREFIX_MIN, PREFIX_MAX + 1),
                        key=lambda q: (abs(q - learned), q < learned)):
            if p == learned:
                continue
            prefix = p
            n = len(entries)
            if _place(buf, base, relpos, cue_time):
                # a block found at an inferred prefix must also READ as text
                # (review: ~4e-7 false parses a wrong shift; never keep one)
                # strict UTF-8, and no control/unassigned/private characters
                # (NBSP, RTL marks and a BOM are normal in subtitles; review)
                try:
                    import unicodedata as _ud
                    txt = entries[-1][2].decode('utf-8')
                    sound = not any(
                        _ud.category(c) in ('Cc', 'Cs', 'Co', 'Cn')
                        and c not in '\r\n\t' for c in txt)
                except Exception:
                    sound = False
                if sound:
                    with src._lock:
                        src._prefix_inferred = getattr(src, '_prefix_inferred', 0) + 1
                    return True
                del entries[n:]
        prefix = learned
        return False

    for batch in _batch_relposes(items):
        # Anything the header read already covers costs no extra request.
        pending = []
        for (relpos, cue_time) in batch:
            if header and _place(header, cpos, relpos, cue_time):
                src._one_shot += 1
                resolved += 1
            else:
                pending.append((relpos, cue_time))
        if not pending:
            continue
        if src.fetched_now() >= HTTP_TOTAL_CAP:
            return resolved
        lo = pending[0][0]
        windowed = getattr(src, '_multi', None) is True and cluster_ts is None
        block_read = BLOCK_READ_MULTI if windowed else BLOCK_READ_HTTP
        span = (pending[-1][0] - lo) + block_read
        base = cpos + prefix + lo
        if windowed:
            # the batch's window: from the shortest prefix a cluster can have
            base = cpos + PREFIX_MIN + lo
            span += PREFIX_MAX - PREFIX_MIN
        buf = src.read(base, min(span, HTTP_TOTAL_CAP - src.fetched_now()))
        if src.tripped or not buf:
            return resolved
        for (relpos, cue_time) in pending:
            if _place(buf, base, relpos, cue_time):
                resolved += 1
                continue
            if windowed and _own_prefix(buf, base, relpos, cue_time):
                resolved += 1
                continue
            if block_read != BLOCK_READ_HTTP:
                # the short window of a batched request may cut a long block
                # off: the ordinary window, once, before anything is blamed on
                # the prefix
                base2 = cpos + prefix + relpos
                if src.fetched_now() < HTTP_TOTAL_CAP and _place(
                        src.read(base2, min(BLOCK_READ_HTTP,
                                            HTTP_TOTAL_CAP - src.fetched_now())),
                        base2, relpos, cue_time):
                    resolved += 1
                    continue
                if src.tripped:
                    return resolved
            if cluster_ts is not None:
                continue   # header path already; nothing left to try
            # The learned prefix did not resolve this block. Before writing the
            # cue off to the (expensive) window scan, pay for one header read: a
            # muxer CAN change its size-VINT width mid-file, and re-learning
            # here keeps every later cluster on the fast path.
            #
            # This is also the one place that PROVES the width is not stable for
            # this file, which is why the concurrent phase watches the counter:
            # three threads sharing one prefix value while it is being re-learned
            # is neither faster nor sound (see _healthy).
            with src._lock:
                src._prefix_relearn += 1
            prefix, cluster_ts, header = _read_cluster_header(src, cpos, relpos)
            if prefix is None:
                return resolved
            with src._lock:
                src._prefix = prefix
            if _place(header, cpos, relpos, cue_time):
                resolved += 1
            else:
                base2 = cpos + prefix + relpos
                if src.fetched_now() < HTTP_TOTAL_CAP and _place(
                        src.read(base2, min(BLOCK_READ_HTTP,
                                            HTTP_TOTAL_CAP - src.fetched_now())),
                        base2, relpos, cue_time):
                    resolved += 1
            # Back to the fast path for the rest of this cluster: `buf`/`base`
            # were read against the OLD prefix, and _place's offset math already
            # accounts for the shift, so the remaining blocks still resolve.
            cluster_ts, header = None, b''
    return resolved


def _short_read(src, offset, asked, got):
    """Did the provider return FEWER bytes than a compliant server owes us?

    This is the only reliable truncation signal available. Everything else in
    this file watches HTTP status, and a capped Range response or a connection
    that dropped mid-stream carries none -- no 429, no 5xx, nothing to retry or
    trip on. Parsing cannot see it either: when the bytes happen to run out
    exactly at a Matroska child-element boundary, the cluster walk finishes
    normally and reports `truncated=False`, indistinguishable from a genuine
    complete parse. Three independent reproductions delivered a "complete" SRT
    missing cues that way, including on an ordinary bounded cluster.

    Byte count, by contrast, cannot be fooled. `src.total` is always known and
    accurate here (extract_srt refuses to proceed without it), so for any range
    inside the file a compliant server owes exactly what was asked, and anything
    less is truncation -- whatever the bytes happen to look like."""
    if not src.total or offset >= src.total:
        return False
    if len(got) >= min(asked, src.total - offset):
        return False
    # Short. That is USUALLY truncation -- but not if the file simply ends here
    # and the size probe was optimistic. `total` comes from a Content-Range or
    # Content-Length on a one-byte probe; a server that reports slightly more
    # than it will serve would otherwise make every read near the tail look
    # truncated, forever, at the same offset, on every attempt -- a file that
    # can never complete no matter how many times it is played. (Reproduced: a
    # total inflated by 5000 bytes on a 525KB file froze the extraction at the
    # same cluster across eight attempts, banking nothing new.)
    #
    # One tiny probe tells the two apart -- but ONLY if it can distinguish "the
    # provider says there is nothing there" from "the read failed". src.read()
    # cannot: it returns b'' for a tripped breaker, for any exception, and for a
    # server that ignores Range and answers 200, all of which are real behaviours
    # this file already handles elsewhere. Believing an empty read means EOF
    # would shrink `total` on a genuine truncation that landed on a clean element
    # boundary -- the one case parsing cannot catch either -- and hand back a
    # subtitle with lines silently missing. So ask probe_beyond(), which says
    # 'eof' only when the provider states it (a 416, or a Content-Range length),
    # and treat anything it cannot establish as truncation.
    probe_at = offset + len(got)
    # Hand the probe a stretch of the file we already hold, immediately before
    # the point in question. If a provider answers "nothing here" without saying
    # 416, re-reading those known bytes is what separates a real end of file
    # from a transport that has simply stopped delivering.
    _n = min(PROBE_CONTROL, len(got))
    _known = (probe_at - _n, bytes(got[-_n:])) if _n >= 4 else None
    try:
        verdict = src.probe_beyond(probe_at, _known)
    except Exception:
        verdict = 'unknown'
    if verdict == 'more':
        return True                       # there IS more -- genuinely truncated
    if verdict != 'eof':
        # Unknown. Deferring costs an attempt; the alternative is delivering a
        # subtitle with lines missing.
        return True
    if src._log:
        src._log('file ends at %d though the provider reported %d -- '
                 'trusting the bytes, not the header'
                 % (probe_at, src.total))
    src.total = probe_at
    return False


def _run_concurrently(fn, work, workers, should_stop):
    """Run fn(*item) over `work` with `workers` threads, stopping early when
    `should_stop()` says so. Plain threads and an index counter -- no executor,
    because Kodi's Python has been known to leave one alive after an addon is
    torn down, and this must not outlive the extraction that started it.

    Every worker is a daemon and the caller joins them all, so an abort ends the
    phase as soon as the in-flight reads return rather than at the end of the
    list."""
    nxt = [0]
    lock = _threading.Lock()
    done = set()

    def _worker():
        while True:
            if should_stop():
                return
            with lock:
                i = nxt[0]
                if i >= len(work):
                    return
                nxt[0] += 1
            try:
                fn(*work[i])
            except Exception:
                # Do NOT record it as done. The caller re-runs everything this
                # returns short of, so an item that faulted is retried on one
                # connection instead of disappearing -- the difference between
                # deferring and handing back a subtitle with a hole in it.
                return
            with lock:
                done.add(i)

    threads = [_threading.Thread(target=_worker) for _ in range(workers)]
    for t in threads:
        t.daemon = True
        t.start()
    for t in threads:
        t.join()
    # The COMPLETED prefix, never the dispatched count: an item is only left
    # behind if every item before it also finished. Anything at or after this
    # index the caller runs itself, and re-running one that did finish is
    # harmless (duplicate cues are dropped when the SRT is built).
    n = 0
    while n < len(work) and n in done:
        n += 1
    return n


# What _extract_cues_http returns when `moved_cb` cut a window short: true
# (the clusters it read are in, nothing failed), and not True.
_MOVED = 'moved'


def _cluster_spans(cpos, items):
    # What _fetch_cluster_blocks reads of this cluster: each block window
    # from the smallest prefix a cluster can have, so the cluster's own
    # prefix is found inside it (_fetch_cluster_blocks). No head range:
    # on a slow node every range costs ~40 ms of the server's time,
    # whatever its size (QA, 14 Nov 2026).
    slack = PREFIX_MAX - PREFIX_MIN
    return join_spans([(cpos + PREFIX_MIN + batch[0][0],
                         (batch[-1][0] - batch[0][0]) + BLOCK_READ_MULTI
                         + slack)
                        for batch in _batch_relposes(items)])


def extract_cues_http(src, positions, entries, want, deadline_s, t0, abort_cb,
                       log, progress_cb=None, done_clusters=None,
                       scan_pending=None, moved_cb=None):
    """HTTP/debrid: ONE keep-alive connection, single-range serial. Per cue,
    two strategies chosen by whether the Cues carried a CueRelativePosition:
      * relpos present -> TARGETED: a small window AT the subtitle block, one
        request per block (see _fetch_cluster_blocks -- the cluster prefix is
        learned once for the file and CueTime supplies the timestamp, so the
        per-cue header read is gone). ~18x less data than a full cluster --
        gentle on the player's bandwidth over a scattered remux (the debrid
        case). Cues are walked CLUSTER at a time so a cluster is only ever
        window-scanned once.
      * relpos absent  -> WINDOW SCAN: fetch a ~1.79MB window at the cluster
        start and parse forward (his proven fallback), topping up the rare
        cluster bigger than the window.
    Player-safe by construction (single serial connection, byte/time caps, 429
    circuit-breaker). Returns True on a COMPLETE pass, False to defer."""
    budget = max(deadline_s, 90.0)
    finished = done_clusters if done_clusters is not None else set()
    # Clusters a previous pass already PROVED the targeted path cannot resolve.
    # Without carrying these, every attempt re-ran their (hopeless) targeted
    # fetches before reaching the scan phase -- so on a file with unresolvable
    # cues the scan was never reached and resume could not converge at all.
    needs_scan = scan_pending if scan_pending is not None else set()
    rel_by_cpos = {}
    scan_cues = []
    for (c, r, t) in positions:
        if r is None:
            scan_cues.append(c)
        else:
            rel_by_cpos.setdefault(c, []).append((r, t))
    total = len(positions)
    # Whatever an earlier attempt finished is already in `entries` -- skip it.
    carried = sum(len(v) for c, v in rel_by_cpos.items() if c in finished)
    carried += sum(1 for c in scan_cues if c in finished)
    rel_clusters = [(c, sorted(rel_by_cpos[c])) for c in sorted(rel_by_cpos)
                    if c not in finished and c not in needs_scan]
    scan_cues = [c for c in scan_cues if c not in finished]
    scan_cues.extend(c for c in sorted(needs_scan) if c not in finished)
    n_rel = sum(len(v) for _c, v in rel_clusters)
    log('%d sub-cue cluster(s): %d targeted (relpos) in %d cluster(s) + %d '
        'window-scan%s; caps %dMB / %.0fs'
        % (total, n_rel, len(rel_clusters), len(scan_cues),
           (' (%d already done)' % carried) if carried else '',
           HTTP_TOTAL_CAP // (1 << 20), budget))

    def _cost():
        """What this attempt actually spent -- the numbers that were missing
        from every abort message, so a field log can say WHY it was slow."""
        el = max(time.time() - t0, 0.001)
        return ('%d/%d cue(s), %d read (%d GET, %d hdr, %d free), %.1fMB, '
                '%.0fs, pace %.2fs, %d backoff(s), %.2f cue/s'
                % (done, total, src.reqs, getattr(src, 'http_reqs', 0),
                   getattr(src, '_hdr_reads', 0),
                   getattr(src, '_one_shot', 0), src.fetched / 1e6, el,
                   getattr(src, '_pace', 0.0), getattr(src, '_429_total', 0),
                   done / el))

    _polled = {'abort': 0.0, 'tick': 0.0}

    def _defer():
        """Return a reason string when we must stop, else None."""
        if src.tripped:
            return 'circuit-breaker tripped (CDN 429/5xx)'
        _got = src.fetched_now()
        if _got >= HTTP_TOTAL_CAP:
            return 'http byte cap reached (%.0fMB)' % (_got / 1e6)
        now = time.time()
        if (now - t0) > budget:
            return 'http time budget reached (%.0fs)' % (now - t0)
        # at most every _CALLBACK_POLL_S (see there)
        if now - _polled['abort'] >= CALLBACK_POLL_S:
            if aborted(abort_cb):
                return 'http extract aborted (playback ended)'
            _polled['abort'] = time.time()
        return None

    def _tick(n):
        """Report progress to the UI (throttled). Never fatal."""
        if src._stats is not None:
            src._stats['done'] = min(n, total)
            src._stats['total'] = total
        if progress_cb:
            now = time.time()
            if n < total and now - _polled['tick'] < CALLBACK_POLL_S:
                return
            _polled['tick'] = now
            try:
                progress_cb(min(n, total), total)
            except Exception:
                pass

    done = carried
    state = {'done': carried, 'stop': None}
    state_lock = _threading.Lock()

    probe = {'due': True}        # this pass has not sent a batch yet


    def _fetch_ahead(seq, j):
        # The next clusters' reads in one request, at most _MULTI_RANGES
        # ranges (see there), once the layout is learned -- exactly the reads
        # _fetch_cluster_blocks will make, so they are served from the store.
        if (not getattr(src, 'is_http', False) or not hasattr(src, 'prefetch')
                or getattr(src, '_multi', None) is False
                or src._prefix is None or src._cue_time_ok is not True):
            return
        # a shared set (a cold file, see _MULTI_PARALLEL) looks further ahead
        sets = src.parallel_now() if hasattr(src, 'parallel_now') else 1
        groups, joined = [], []
        for k, (cpos, items) in enumerate(seq[j:]):
            if any(t is None for _r, t in items):
                break                    # such a cluster reads its header
            # never ask twice for what is held, all of it
            mine = [s for s in _cluster_spans(cpos, items) if not src.holds(*s)]
            if k == 0 and not mine:
                return                   # this cluster is in the store already
            # ranges that meet go as one (some servers refuse overlapping
            # ones); the cap counts what is sent
            more = join_spans(joined + mine)
            if joined and len(more) > MULTI_RANGES:
                groups.append(joined)    # this batch is full: the next one
                if len(groups) >= sets:
                    joined = []
                    break
                more = join_spans(mine)
            joined = more[:MULTI_RANGES]
        if joined:
            groups.append(joined)
        if not groups:
            return
        if src.fetched_now() + sum(k for g in groups for _o, k in g) >= HTTP_TOTAL_CAP:
            return
        first, probe['due'] = probe['due'], False
        if sets > 1:
            src.prefetch_many([s for g in groups for s in g],
                              keep_from=seq[j][0])
            return
        joined = groups[0]
        if first and hasattr(src, 'can_share') and src.can_share():
            joined = joined[:MULTI_PROBE_RANGES]    # see _MULTI_PROBE_RANGES
        if src.fetched_now() + sum(k for _o, k in joined) >= HTTP_TOTAL_CAP:
            return
        src.prefetch(joined, keep_from=seq[j][0])

    def _moved():
        # Between clusters: has the viewer gone where this window does not
        # help (a window pass only; see _extract_in_windows)? The clusters
        # read so far stay read; the rest wait for a later window or the
        # complete pass.
        if moved_cb is None or state['stop'] is not None:
            return state['stop'] == _MOVED
        try:
            if moved_cb():
                state['stop'] = _MOVED
                return True
        except Exception:
            pass
        return False

    def _one_cluster(cpos, items):
        """Fetch ONE cluster's blocks and record the outcome. Safe to run from
        several threads: the only shared things it touches are `entries` and the
        two cluster sets, all under `state_lock`."""
        if state['stop'] is not None:
            return
        reason = _defer()
        if reason:
            with state_lock:
                if state['stop'] is None:
                    state['stop'] = reason
            return
        local = []
        try:
            got = _fetch_cluster_blocks(src, cpos, items, want, local, log)
        except Exception as e:
            # An unexpected fault here must NEVER end as a quiet success. Left
            # to escape, this cluster's blocks land in none of `entries`,
            # `finished` or `needs_scan`, the pass still reports COMPLETE, and
            # the resume file -- the one thing that could have recovered the
            # gap on a later attempt -- is cleared. That is a subtitle silently
            # missing lines, which is the exact outcome this whole function is
            # built to prevent. Treat it as "could not resolve these blocks" and
            # let the window scan below cover the cluster, the same path a short
            # read or an odd prefix already takes.
            log('cluster at %d failed unexpectedly (%r) -- window-scanning it'
                % (cpos, e))
            got = 0
        with state_lock:
            entries.extend(local)
            if got < len(items):
                if src.tripped:
                    if state['stop'] is None:
                        state['stop'] = 'circuit-breaker tripped mid-fetch'
                    return
                # Couldn't resolve every block from relpos (odd prefix / short
                # read / wrong track) -> window-scan THIS cluster so we never
                # drop a line. Duplicates are dropped in _entries_to_srt, so
                # re-finding the ones we already have is harmless. NOT marked
                # finished -- the scan below is what completes it -- but
                # remembered as scan-pending, so a later attempt goes straight
                # to the scan instead of paying for the targeted fetches all
                # over again.
                scan_cues.append(cpos)
                needs_scan.add(cpos)
            else:
                finished.add(cpos)
            state['done'] += len(items)
            n = state['done']
            if n % 100 < len(items):
                log('extract progress: %d/%d cue(s), %d req, %.0fMB'
                    % (n, total, src.reqs, src.fetched / 1e6))
            _tick(n)

    # 1) targeted relpos fetches.
    #
    # The FIRST cluster is deliberately alone: it is the one that learns the
    # file's cluster prefix and proves CueTime agrees with it, and every later
    # cluster is fast only because it can rely on both. Starting three at once
    # would have three of them pay that header read and verify the same thing
    # three times.
    if rel_clusters:
        def _healthy():
            """Concurrency is for the HEALTHY path only, and this is the whole
            safety argument for it.

            It needs the two shortcuts already proven on the first cluster (the
            prefix learned and CueTime agreeing), because a file whose prefix
            width changes mid-run has clusters re-learning it and three of them
            racing on that one value is neither faster nor sound. And it needs a
            provider that has not pushed back: back-pressure is measured in
            consecutive refusals against progress, and three connections
            accumulate refusals three times faster against the same progress --
            which would trip the breaker on exactly the flaky-but-usable
            provider the serial code was carefully taught to crawl through
            instead of abandoning.

            So the moment either stops being true we finish serially, in the
            exact code path all of that behaviour was tuned on."""
            return (src._prefix is not None and src._cue_time_ok is True
                    and getattr(src, '_429_total', 0) == 0
                    and getattr(src, '_prefix_relearn', 0) == 0
                    and not needs_scan)

        # Seed serially until both shortcuts are settled. It takes two clusters,
        # not one: the first learns the prefix, and CueTime can only be PROVEN
        # against a cluster with a non-zero timestamp -- the first cluster of
        # every file sits at 0, where the check would pass for a muxer that
        # wrote CueTime relative to its cluster and be wrong everywhere after.
        # If a file has not settled after a few clusters it never will (no
        # CueTime at all, say), so stop seeding and let it run serially.
        seed = 0
        while seed < len(rel_clusters) and state['stop'] is None:
            if _moved():
                break
            _one_cluster(*rel_clusters[seed])
            seed += 1
            if _healthy() or seed >= CONC_SEED_MAX:
                break
        rest = rel_clusters[seed:]

        i = 0
        conns = (src.open_pool() if (src.is_http and rest and _healthy())
                 else 1)
        try:
            if conns > 1:
                # Remember the pace we are starting from. Asking more often is
                # what makes this faster, so if the provider refuses, the
                # refusals are OURS -- and their cost must not outlive the
                # experiment (see restore_pace).
                pace_before = getattr(src, '_pace', None)
                log('%d connections for the remaining %d cluster(s): the pace '
                    'stays %.2fs but the round trip stops counting, so we ask '
                    'about %d times as often'
                    % (conns, len(rest), pace_before or 0.0, conns))
                i = _run_concurrently(
                    _one_cluster, rest, conns,
                    lambda: state['stop'] is not None or src.tripped
                    or aborted(abort_cb) or not _healthy() or _moved())
                if i < len(rest):
                    log('finishing the last %d cluster(s) on one connection '
                        '(%d backoff(s), %d cluster(s) needing a scan)'
                        % (len(rest) - i, getattr(src, '_429_total', 0),
                           len(needs_scan)))
                    src.restore_pace(pace_before)
        finally:
            src.close_pool()
        tail = rest[i:]
        for j, (cpos, items) in enumerate(tail):
            if state['stop'] is not None or _moved():
                break
            _fetch_ahead(tail, j)
            _one_cluster(cpos, items)
    done = state['done']
    if state['stop'] == _MOVED:
        log('window cut short -- the viewer moved [' + _cost() + ']')
        return _MOVED
    if state['stop'] is not None:
        log(state['stop'] + ' -- deferring [' + _cost() + ']')
        return False

    # 2) window-scan the remainder (no-relpos cues + any relpos misses)
    if scan_cues:
        ranges = _coalesce_ranges(sorted(set(scan_cues)), CLUSTER_WINDOW_HTTP,
                                  COALESCE_GAP, MAX_RANGE, src.total)
        for (rstart, rend, cposes) in ranges:
            reason = _defer()
            if reason:
                log(reason + ' -- deferring')
                return False
            _want_n = min(rend - rstart, HTTP_TOTAL_CAP - src.fetched)
            window = src.read(rstart, _want_n)
            if _short_read(src, rstart, _want_n, window):
                log('provider returned %d of %d bytes at %d (no 429) -- '
                    'deferring rather than trusting a truncated window'
                    % (len(window), _want_n, rstart))
                return False
            if src.tripped:
                log('circuit-breaker tripped mid-fetch -- deferring')
                return False
            # A SHORT BODY IS A FAILURE, not an empty region. A CDN may answer a
            # large Range with 200/206 and fewer bytes than asked -- a capped
            # response, or a connection that dropped mid-stream -- and no 429 is
            # ever involved, so the circuit-breaker, the backoff counter and the
            # retry loop are all blind to it. Skipping the clusters it failed to
            # cover let the pass finish and report success while their lines were
            # simply absent: 10 cues of 24 delivered as a whole subtitle. That is
            # precisely the outcome this function exists to prevent, so treat
            # every uncovered cluster as a reason to defer -- the resume file
            # keeps what was collected and the next attempt retries them.
            if not window:
                log('empty window for %d cluster(s) at %d (short body, no 429) '
                    '-- deferring rather than dropping them' % (len(cposes),
                                                                rstart))
                return False
            for cpos in cposes:
                if src.tripped:
                    break
                off = cpos - rstart
                if not (0 <= off < len(window)):
                    log('cluster at %d not covered by a %d-byte window (short '
                        'body, no 429) -- deferring' % (cpos, len(window)))
                    return False
                _end, truncated = collect_one_cluster(window[off:], want, entries)
                if not _end:
                    # Nothing parsed at all: the bytes at this offset do not
                    # begin a Cluster. `truncated` is False here, which reads
                    # identically to "clean end of a cluster with nothing left"
                    # -- so without this the cue is marked finished and its lines
                    # are lost for good.
                    log('no cluster found at %d in the window (short/misaligned '
                        'body) -- deferring' % cpos)
                    return False
                if not truncated:
                    finished.add(cpos)   # covered cleanly -- never redo it
                    needs_scan.discard(cpos)
                    continue
                # Window too small for this cluster (a big co-located video/audio
                # block sits around the subtitle one) -> a LATER subtitle block
                # was not seen. Top-up the whole cluster; dedup in _entries_to_srt
                # drops any re-parsed block. STILL truncated -> DEFER (never
                # deliver a silently-partial subtitle).
                if src.fetched >= HTTP_TOTAL_CAP:
                    log('byte cap reached at top-up -- deferring')
                    return False
                _want_t = min(CLUSTER_TOPUP_MAX,
                              HTTP_TOTAL_CAP - src.fetched)
                tup = src.read(cpos, _want_t)
                if _short_read(src, cpos, _want_t, tup):
                    log('provider returned %d of %d bytes at the top-up for %d '
                        '(no 429) -- deferring' % (len(tup), _want_t, cpos))
                    return False
                if src.tripped:
                    log('circuit-breaker tripped during top-up -- deferring')
                    return False
                _tend, tup_trunc = collect_one_cluster(tup, want, entries)
                if not _tend:
                    log('top-up at %d returned nothing parseable (short body) '
                        '-- deferring' % cpos)
                    return False
                if tup_trunc:
                    log('cluster exceeds top-up cap (%dMB) -- deferring to avoid '
                        'a partial subtitle' % (CLUSTER_TOPUP_MAX >> 20))
                    return False
                finished.add(cpos)       # the top-up completed it
                needs_scan.discard(cpos)
            done += len(cposes)
            _tick(done)

    if src.tripped:
        log('circuit-breaker tripped -- deferring')
        return False
    if progress_cb:
        _tick(total)   # 100% on a complete pass
    return True
