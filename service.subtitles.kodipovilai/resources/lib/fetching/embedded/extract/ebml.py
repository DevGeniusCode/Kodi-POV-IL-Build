# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Matroska / EBML element ids and the byte-level reading primitives (variable-length integers, element walk,
# the read buffer, the abort check).

from resources.lib.core import kodi


# ---- EBML / Matroska element IDs (raw, incl. length-descriptor bits) --------
EBML = 0x1A45DFA3
SEGMENT = 0x18538067
SEEKHEAD = 0x114D9B74
SEEK = 0x4DBB
SEEKID = 0x53AB
SEEKPOS = 0x53AC
INFO = 0x1549A966
TS_SCALE = 0x2AD7B1
TRACKS = 0x1654AE6B
TRACKENTRY = 0xAE
TRACKNUM = 0xD7
TRACKTYPE = 0x83
CODEC = 0x86
CODEC_PRIVATE = 0x63A2
LANG = 0x22B59C
LANG_BCP47 = 0x22B59D
FORCED = 0x55AA
HEARING_IMPAIRED = 0x55AB     # FlagHearingImpaired (Matroska): 1 == SDH/CC track
TRACKNAME = 0x536E            # TrackName: human label, often "English (SDH)" etc.
CUES = 0x1C53BB6B
CUE_POINT = 0xBB
CUE_TIME = 0xB3
CUE_TRACK_POS = 0xB7
CUE_TRACK = 0xF7
CUE_CLUSTER_POS = 0xF1
CUE_RELATIVE_POS = 0xF0
CLUSTER = 0x1F43B675
CLUSTER_MAGIC = b'\x1f\x43\xb6\x75'
TIMESTAMP = 0xE7
SIMPLEBLOCK = 0xA3
BLOCKGROUP = 0xA0
BLOCK = 0xA1
BLOCKDUR = 0x9B


def noop(_m):
    return None


def aborted(abort_cb):
    """True when the caller signals to stop (e.g. playback ended). A callback
    that raises is treated as 'keep going', never as an abort."""
    if abort_cb is None:
        return False
    try:
        return bool(abort_cb())
    except Exception:
        return False


# ---- primitives (self-contained, byte-faithful to mkv_probe.py) -------------
class Buf(object):
    __slots__ = ('d', 'n', 'p', 'base')

    def __init__(self, data, base):
        self.d = data
        self.n = len(data)
        self.p = 0
        self.base = base

    def left(self):
        return self.n - self.p


def read_vint(buf, keep_marker):
    """(value, length) EBML variable-int at buf.p; (None, 0) when truncated.
    keep_marker=True for element IDs, False for sizes (marker stripped;
    all-ones payload -> None value = 'unknown size')."""
    if buf.left() < 1:
        return None, 0
    first = buf.d[buf.p]
    if first == 0:
        return None, 0
    length = 1
    mask = 0x80
    while not (first & mask):
        mask >>= 1
        length += 1
        if length > 8:
            return None, 0
    if buf.left() < length:
        return None, 0
    raw = buf.d[buf.p:buf.p + length]
    buf.p += length
    val = 0
    for b in raw:
        val = (val << 8) | b
    if keep_marker:
        return val, length
    val &= (1 << (7 * length)) - 1
    if val == (1 << (7 * length)) - 1:
        return None, length
    return val, length


def read_uint(data):
    val = 0
    for b in data:
        val = (val << 8) | b
    return val


# The long loops here call _breathe() (core/kodi.make_breather has the why).
_breathe = kodi.make_breather()


def walk(buf, end):
    """Yield (element_id, size_or_None, payload_start) for children in
    buf.d[buf.p:end]; caller advances past each payload itself."""
    while buf.p < end:
        _breathe()
        eid, _idl = read_vint(buf, True)
        if eid is None:
            return
        size, slen = read_vint(buf, False)
        if slen == 0:
            return
        yield eid, size, buf.p
