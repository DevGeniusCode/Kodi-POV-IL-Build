# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Cue rows (start, end, text) to SRT text; frame decoding for SRT / ASS / WebVTT payloads.

import re
from resources.lib.fetching.embedded.extract.ebml import Buf, CLUSTER_MAGIC, read_uint, read_vint, TIMESTAMP


# ---- text decode ------------------------------------------------------------
_ASS_TAG = re.compile(r'\{[^}]*\}')
_VTT_TAG = re.compile(r'</?[^>]+>')


def _decode_frame(frame, codec):
    try:
        text = frame.decode('utf-8', 'replace')
    except Exception:
        return ''
    up = (codec or '').upper()
    if up in ('S_TEXT/ASS', 'S_TEXT/SSA', 'S_ASS', 'S_SSA'):
        # MKV ASS block body: ReadOrder,Layer,Style,Name,ML,MR,MV,Effect,Text
        parts = text.split(',', 8)
        if len(parts) < 9:
            return ''  # malformed ASS packet, not dialogue to expose as-is
        text = parts[8]
        text = text.replace('\\N', '\n').replace('\\n', '\n')
        text = _ASS_TAG.sub('', text)
    elif up == 'S_TEXT/WEBVTT':
        text = _VTT_TAG.sub('', text)
    return text.strip('﻿').strip()


def fmt_ts(ms):
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ml = ms % 1000
    return '%02d:%02d:%02d,%03d' % (h, m, s, ml)


def entries_to_srt(entries, scale_ms, origin_ms, codec):
    """entries: [(abs_ticks, dur_ticks_or_None, frame_bytes)] -> SRT string."""
    rows = []
    for ticks, dur, frame in entries:
        start = int(ticks * scale_ms - origin_ms)
        if start < 0:
            continue
        text = _decode_frame(frame, codec)
        if not text:
            continue
        dur_ms = int(dur * scale_ms) if dur else None
        rows.append((start, dur_ms, text))
    if not rows:
        return ''
    rows.sort(key=lambda r: r[0])
    # De-duplicate identical (start, text) that a Cues fetch can revisit.
    dedup = []
    seen = set()
    for start, dur_ms, text in rows:
        key = (start, text)
        if key in seen:
            continue
        seen.add(key)
        dedup.append([start, dur_ms, text])
    out = []
    for i, (start, dur_ms, text) in enumerate(dedup):
        if dur_ms and dur_ms > 0:
            end = start + dur_ms
        else:
            nxt = dedup[i + 1][0] if i + 1 < len(dedup) else start + 3000
            end = start + max(700, min(6000, nxt - start - 60))
        if end <= start:
            end = start + 700
        out.append('%d' % (i + 1))
        out.append('%s --> %s' % (fmt_ts(start), fmt_ts(end)))
        out.append(text)
        out.append('')
    return '\n'.join(out)


def timeline_origin(src, seg_start, scale_ms, log):
    """First cluster timestamp in ms (the playback zero point) so cues line up
    with players that rebase to it. 0 for normal zero-based files."""
    try:
        pos = seg_start
        cap = seg_start + 8 * 1024 * 1024
        carry = b''
        while pos < cap:
            chunk = src.read(pos, 1024 * 1024)
            if not chunk:
                return 0.0
            data = carry + chunk
            idx = data.find(CLUSTER_MAGIC)
            if idx >= 0:
                buf = Buf(data, 0)
                buf.p = idx + 4
                _cs, sl = read_vint(buf, False)
                if sl == 0:
                    return 0.0
                eid, _l = read_vint(buf, True)
                size, slen = read_vint(buf, False)
                if (eid == TIMESTAMP and slen and size
                        and buf.p + size <= len(data)):
                    origin = read_uint(data[buf.p:buf.p + size]) * scale_ms
                    if origin > 1000:
                        log('timeline origin %.1fs -- rebasing' % (origin / 1e3))
                    return float(origin)
                return 0.0
            carry = data[-4:]
            pos += len(chunk)
        return 0.0
    except Exception:
        return 0.0
