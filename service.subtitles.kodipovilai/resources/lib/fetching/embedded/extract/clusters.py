# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Reading one Matroska cluster: its block groups and simple blocks.

from resources.lib.fetching.embedded.extract.ebml import BLOCK, BLOCKDUR, BLOCKGROUP, Buf, CLUSTER, read_uint, read_vint, SIMPLEBLOCK, TIMESTAMP, walk
from resources.lib.fetching.embedded.extract.cues import block_frame


# Legitimate children of a Cluster. Inside an UNKNOWN-size cluster, ANY other id
# marks the start of the next segment-level element (the cluster has ended) --
# that's how an unbounded cluster's extent is recovered. Void/CRC-32 can appear.
_CLUSTER_CHILD_IDS = frozenset((
    TIMESTAMP, SIMPLEBLOCK, BLOCKGROUP,
    0xA7,    # Position
    0xAB,    # PrevSize
    0x5854,  # SilentTracks
    0xAF,    # EncryptedBlock
    0xEC,    # Void
    0xBF,    # CRC-32
))


def collect_one_cluster(window, want_track, out):
    """`window` starts at a Cluster element. Parse its children STRUCTURALLY (by
    declared size -- no magic-byte scan, so binary block data can never be
    mis-read as a nested cluster) and append (abs_ticks, dur_or_None, frame) for
    want_track into `out`. Returns the offset within `window` one past the
    cluster's last child (where the NEXT element begins, so an UNKNOWN-size
    cluster's true length is recoverable); `truncated` is True when parsing
    stopped because a child element ran PAST the end of `window` -- i.e. the read
    was too small and a later block (possibly a subtitle one) was NOT seen, so
    the caller must top-up rather than trust the result as complete. It is False
    on a genuine end (declared size reached, or an unbounded cluster's next
    segment-level element)."""
    b = Buf(window, 0)
    eid, _l = read_vint(b, True)
    if eid != CLUSTER:
        return 0, False
    size, slen = read_vint(b, False)
    if slen == 0:
        return 0, False
    bounded = size is not None
    limit = min(b.n, b.p + size) if bounded else b.n
    cluster_ts = None
    truncated = False
    while b.p < limit:
        child_start = b.p
        ceid, _cidl = read_vint(b, True)
        if ceid is None:
            truncated = True   # child-id VINT cut off by the window
            b.p = child_start
            break
        if not bounded and ceid not in _CLUSTER_CHILD_IDS:
            # Unknown-size cluster: a non-child id is the next segment-level
            # element -> this cluster ends here (do NOT consume it). A genuine
            # end, NOT a truncation.
            b.p = child_start
            break
        csize, cslen = read_vint(b, False)
        if cslen == 0 or csize is None:
            truncated = True   # child-size VINT cut off by the window
            b.p = child_start
            break
        cstart = b.p
        if cstart + csize > b.n:
            truncated = True   # this child runs past the window -> need more
            b.p = child_start
            break
        payload = window[cstart:cstart + csize]
        if ceid == TIMESTAMP:
            cluster_ts = read_uint(payload)
        elif ceid == SIMPLEBLOCK and cluster_ts is not None:
            r = block_frame(payload, cluster_ts, want_track)
            if r:
                out.append((r[0], None, r[1]))
        elif ceid == BLOCKGROUP and cluster_ts is not None:
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
        b.p = cstart + csize
    return b.p, truncated


def read_and_collect_cluster(src, cpos, want_track, out, cap, log):
    """Read ONE cluster at absolute offset `cpos` by its DECLARED size (capped
    at `cap` bytes -- so a subtitle block deep inside a huge cluster is the only
    thing ever missed, never memory/bandwidth) and collect want_track blocks.
    Returns the declared full cluster length (header + payload) for sequential
    advancement, or 0 when `cpos` is not a Cluster."""
    hdr = src.read(cpos, 16)
    if len(hdr) < 2:
        return 0
    hb = Buf(hdr, 0)
    eid, _idl = read_vint(hb, True)
    if eid != CLUSTER:
        return 0
    size, slen = read_vint(hb, False)
    if slen == 0:
        return 0
    hlen = hb.p
    if size is None:
        # Unknown-size cluster: read a bounded window; the structural parser
        # stops at the next segment-level element and reports where. Advance by
        # that so the walker resumes exactly at that element. If the cluster
        # fills the whole capped read (next element not seen), advance by the
        # read length (best effort).
        window = src.read(cpos, cap)
        consumed, _trunc = collect_one_cluster(window, want_track, out)
        return consumed if 0 < consumed < len(window) else len(window)
    clen = hlen + size
    window = src.read(cpos, min(clen, cap))
    if len(window) >= hlen:
        collect_one_cluster(window, want_track, out)
    return clen
