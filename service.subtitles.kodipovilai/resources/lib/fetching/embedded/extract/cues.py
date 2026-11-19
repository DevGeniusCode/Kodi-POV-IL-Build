# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The Cues index of a Matroska file: cue positions and the cue times of one or several tracks.

import struct
from resources.lib.fetching.embedded.extract.ebml import Buf, CUE_CLUSTER_POS, CUE_POINT, CUE_RELATIVE_POS, CUE_TIME, CUE_TRACK, CUE_TRACK_POS, CUES, read_uint, read_vint, walk
from resources.lib.fetching.embedded.extract.tuning import CUES_CAP
from resources.lib.fetching.embedded.extract.matroska import cues_pos


def read_cues(src, seeks, seg_start, want_track, log):
    """Absolute cluster positions from the Cues index, as (positions, is_sub).
    Each position is (cluster_pos, relative_pos_or_None, cue_time_or_None) --
    CueTime is the cue's ABSOLUTE timestamp in TimestampScale units, which is
    exactly what a cluster-header read would otherwise be spent computing (see
    _fetch_cluster_blocks).
    Prefers cue points that reference `want_track` (per-track subtitle cues,
    which point straight at subtitle-bearing clusters); falls back to all cue
    positions when the file has none. ([], False) when there's no usable Cues.
    Hard-capped by _CUES_CAP so a corrupt/huge Cues size can NEVER trigger a
    multi-GB read."""
    pos = cues_pos(src, seeks, seg_start, log)
    if not pos:
        return [], False
    raw = src.read(pos, 64 * 1024)
    if not raw:
        return [], False
    b = Buf(raw, pos)
    eid, _l = read_vint(b, True)
    size, slen = read_vint(b, False)
    if eid != CUES or slen == 0 or size is None:
        return [], False
    need = b.p + size
    if need > CUES_CAP:
        log('cues element too large (%d bytes) -- skipping' % size)
        return [], False
    while len(raw) < need:
        more = src.read(pos + len(raw), min(4 * 1024 * 1024, need - len(raw)))
        if not more:
            break
        raw += more
    if len(raw) < need:
        # The Cues element declares its own size, and we did not get all of it.
        # This is the SAME truncation the cluster reads now guard against (a
        # capped response or a dropped connection, no 429 anywhere), reached
        # through a different door -- and it is worse here, because a short Cues
        # index simply yields FEWER cue positions. Every one of them is then
        # fetched successfully, the pass reports a complete success, and a
        # subtitle missing every line past the cut is delivered as if it were
        # whole. Measured: 30 of 60 cues, with nothing in the log to suggest it.
        # An empty return makes the caller defer.
        log('cues element truncated (%d of %d bytes, no 429) -- deferring '
            'rather than indexing half the file' % (len(raw), need))
        return [], False
    b = Buf(raw, pos)
    read_vint(b, True)
    read_vint(b, False)
    data = raw[b.p:b.p + size]
    want_pos, any_pos = [], []
    cbuf = Buf(data, 0)
    for eid2, size2, start2 in walk(cbuf, len(data)):
        if size2 is None:
            break
        cbuf.p = start2 + size2
        if eid2 != CUE_POINT:
            continue
        cp = data[start2:start2 + size2]
        # CueTime is the CuePoint's own first child, a sibling of every
        # CueTrackPositions under it -- so read it before walking them.
        ctime = None
        tb = Buf(cp, 0)
        for peid, psize, pstart in walk(tb, len(cp)):
            if psize is None:
                break
            tb.p = pstart + psize
            if peid == CUE_TIME:
                ctime = read_uint(cp[pstart:pstart + psize])
                break
        pbuf = Buf(cp, 0)
        for peid, psize, pstart in walk(pbuf, len(cp)):
            if psize is None:
                break
            pp = cp[pstart:pstart + psize]
            pbuf.p = pstart + psize
            if peid != CUE_TRACK_POS:
                continue
            ctrack, cpos, crel = None, None, None
            tbuf = Buf(pp, 0)
            for teid, tsize, tstart in walk(tbuf, len(pp)):
                if tsize is None:
                    break
                tp = pp[tstart:tstart + tsize]
                tbuf.p = tstart + tsize
                if teid == CUE_TRACK:
                    ctrack = read_uint(tp)
                elif teid == CUE_CLUSTER_POS:
                    cpos = seg_start + read_uint(tp)
                elif teid == CUE_RELATIVE_POS:
                    crel = read_uint(tp)
            if cpos is not None:
                any_pos.append((cpos, crel, ctime))
                if ctrack == want_track:
                    want_pos.append((cpos, crel, ctime))
    is_sub = bool(want_pos)
    # Keep EVERY distinct (cluster, relpos) pair. A single cluster routinely
    # holds >1 subtitle cue (two lines a couple seconds apart in fast dialogue),
    # and the relpos fast path fetches EXACTLY ONE block per relpos -- collapsing
    # them by cluster would silently drop every line but the first. A cue WITHOUT
    # relpos forces a full window-scan of its cluster (which recovers every block
    # in it), so it subsumes and REPLACES any relpos entries for the same cluster
    # (avoids fetching the same cluster twice).
    scan_only = set()          # clusters that carry a relpos-less cue
    rel_by_cpos = {}           # cpos -> {relpos: cue_time_or_None}
    for cpos, crel, ctime in (want_pos if is_sub else any_pos):
        if crel is None:
            scan_only.add(cpos)
        else:
            rel_by_cpos.setdefault(cpos, {})[crel] = ctime
    out = []
    for cpos in sorted(scan_only | set(rel_by_cpos)):
        if cpos in scan_only:
            out.append((cpos, None, None))
        else:
            for relpos in sorted(rel_by_cpos[cpos]):
                out.append((cpos, relpos, rel_by_cpos[cpos][relpos]))
    nrel = sum(1 for _c, r, _t in out if r is not None)
    ntime = sum(1 for _c, r, t in out if r is not None and t is not None)
    log('cues: %d cue(s) / %d cluster(s) (%s, %d with relpos, %d with cuetime)'
        % (len(out), len(scan_only | set(rel_by_cpos)),
           'sub-track' if is_sub else 'whole-file', nrel, ntime))
    return out, is_sub


def read_cue_times_multi(src, seeks, seg_start, want_tracks, log):
    """{track_num: SORTED distinct raw CueTime (ts_scale ticks)} for every track
    in `want_tracks`, from ONE Cues-element read. This is the read-once core: a
    subtitle file's single Cues index carries cue points for EVERY track, so N
    tracks (e.g. the cross-language align fallback trying several languages) cost
    ONE head+Cues read instead of one per track. Reads ONLY the Cues element --
    NO cluster block fetches -- so it stays a handful of range requests even on a
    huge file (viable on a strict debrid token where the full extract is not).
    CueTime is the cue's START on the segment timeline. A track with no per-cue
    index is simply absent from the result. Never raises (caller wraps)."""
    want_tracks = set(want_tracks)
    if not want_tracks:
        return {}
    pos = cues_pos(src, seeks, seg_start, log)
    if not pos:
        return {}
    raw = src.read(pos, 64 * 1024)
    if not raw:
        return {}
    b = Buf(raw, pos)
    eid, _l = read_vint(b, True)
    size, slen = read_vint(b, False)
    if eid != CUES or slen == 0 or size is None:
        return {}
    need = b.p + size
    if need > CUES_CAP:
        log('cue-times: cues element too large (%d bytes) -- skipping' % size)
        return {}
    while len(raw) < need:
        more = src.read(pos + len(raw), min(4 * 1024 * 1024, need - len(raw)))
        if not more:
            break
        raw += more
    if len(raw) < need:
        # The Cues element declares its own size, and we did not get all of it.
        # This is the SAME truncation the cluster reads now guard against (a
        # capped response or a dropped connection, no 429 anywhere), reached
        # through a different door -- and it is worse here, because a short Cues
        # index simply yields FEWER cue positions. Every one of them is then
        # fetched successfully, the pass reports a complete success, and a
        # subtitle missing every line past the cut is delivered as if it were
        # whole. Measured: 30 of 60 cues, with nothing in the log to suggest it.
        # An empty return makes the caller defer.
        log('cues element truncated (%d of %d bytes, no 429) -- deferring '
            'rather than indexing half the file' % (len(raw), need))
        return {}
    b = Buf(raw, pos)
    read_vint(b, True)
    read_vint(b, False)
    data = raw[b.p:b.p + size]
    buckets = {t: [] for t in want_tracks}
    cbuf = Buf(data, 0)
    for eid2, size2, start2 in walk(cbuf, len(data)):
        if size2 is None:
            break
        cbuf.p = start2 + size2
        if eid2 != CUE_POINT:
            continue
        cp = data[start2:start2 + size2]
        pbuf = Buf(cp, 0)
        ctime = None
        tracks_here = set()
        for peid, psize, pstart in walk(pbuf, len(cp)):
            if psize is None:
                break
            pp = cp[pstart:pstart + psize]
            pbuf.p = pstart + psize
            if peid == CUE_TIME:
                ctime = read_uint(pp)
            elif peid == CUE_TRACK_POS:
                tbuf = Buf(pp, 0)
                for teid, tsize, tstart in walk(tbuf, len(pp)):
                    if tsize is None:
                        break
                    tp = pp[tstart:tstart + tsize]
                    tbuf.p = tstart + tsize
                    if teid == CUE_TRACK:
                        tracks_here.add(read_uint(tp))
        if ctime is None:
            continue
        # Bucket this cue's START under every WANTED subtitle track it indexes.
        # (Video-keyframe cues reference the video track, which isn't in
        # want_tracks, so they're ignored -- only per-subtitle cues mark when a
        # subtitle line appears.)
        for t in (tracks_here & want_tracks):
            buckets[t].append(ctime)
    return {t: sorted(set(v)) for t, v in buckets.items() if v}


def read_cue_times(src, seeks, seg_start, want_track, log):
    """SORTED distinct raw CueTime values (ts_scale ticks) for `want_track`, or []
    when the file has no per-subtitle Cues index for it. Thin wrapper over the
    read-once core (`_read_cue_times_multi`) -- byte-identical parse, one track."""
    return read_cue_times_multi(
        src, seeks, seg_start, {want_track}, log).get(want_track, [])


# ---- block / cluster text ---------------------------------------------------
def block_frame(payload, cluster_ts, want_track):
    """(abs_ticks, frame_bytes) for a (Simple)Block of want_track, or None.
    Laced blocks are skipped (subtitles are virtually never laced)."""
    buf = Buf(payload, 0)
    tnum, _l = read_vint(buf, False)
    if tnum is None or tnum != want_track:
        return None
    if buf.left() < 3:
        return None
    rel = struct.unpack('>h', payload[buf.p:buf.p + 2])[0]
    buf.p += 2
    flags = payload[buf.p]
    buf.p += 1
    if (flags >> 1) & 0x03:
        return None   # laced -> skip (safe: this cue is just omitted)
    frame = payload[buf.p:]
    if not frame:
        return None
    return cluster_ts + rel, frame
