# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Matroska head parsing: track entries, the SeekHead chain, text / bitmap codec classification.

from resources.lib.fetching.embedded.extract.ebml import Buf, CLUSTER, CODEC, CODEC_PRIVATE, CUES, EBML, FORCED, HEARING_IMPAIRED, INFO, LANG, LANG_BCP47, read_uint, read_vint, SEEK, SEEKHEAD, SEEKID, SEEKPOS, SEGMENT, TRACKENTRY, TRACKNAME, TRACKNUM, TRACKS, TRACKTYPE, TS_SCALE, walk
from resources.lib.fetching.embedded.extract.tuning import CUT_SAMPLE_BYTES, SUB_TRACK_TYPE


_TEXT_CODEC_PREFIX = 'S_TEXT'
_LEGACY_ASS_CODECS = ('S_ASS', 'S_SSA')

# The codecs that really are pictures. NOT the complement of _TEXT_CODEC_PREFIX:
# Matroska has text subtitle codecs that predate the S_TEXT/* naming and sit
# outside it -- S_ASS and S_SSA (handled here), plus S_USF, S_HDMV/TEXTST,
# S_KATE and S_ARIBSUB (not decoded by this extractor).
# Calling "no S_TEXT/* track" the same thing as "all bitmap" would say the file
# has nothing but pictures when it actually has a text track this extractor
# simply does not read -- and that is a message that sends the next reader AWAY
# from a real gap. Whatever we assert has to be what we actually tested.
_BITMAP_CODECS = ('S_HDMV/PGS', 'S_VOBSUB', 'S_DVBSUB', 'S_IMAGE')


def is_bitmap_codec(codec):
    return (codec or '').upper().startswith(_BITMAP_CODECS)


def _parse_track_entry(data):
    t = {'num': None, 'type': None, 'codec': '', 'lang': '', 'forced': False,
         'private': b'', 'name': '', 'hearing_impaired': False}
    buf = Buf(data, 0)
    for eid, size, start in walk(buf, len(data)):
        if size is None:
            break
        payload = data[start:start + size]
        buf.p = start + size
        if eid == TRACKNUM:
            t['num'] = read_uint(payload)
        elif eid == TRACKTYPE:
            t['type'] = read_uint(payload)
        elif eid == CODEC:
            t['codec'] = payload.decode('ascii', 'replace')
        elif eid == CODEC_PRIVATE:
            t['private'] = payload
        elif eid == FORCED:
            t['forced'] = bool(read_uint(payload))
        elif eid == HEARING_IMPAIRED:
            # Authoritative SDH flag -- no guessing. Read from the head bytes we
            # ALREADY parse for lang/codec, so it's free and works on a strict
            # debrid token (this is the cheap head read, not the full extract).
            t['hearing_impaired'] = bool(read_uint(payload))
        elif eid == TRACKNAME:
            t['name'] = payload.decode('utf-8', 'replace').strip('\x00')
        elif eid in (LANG, LANG_BCP47):
            if not t['lang']:
                t['lang'] = payload.decode('ascii', 'replace').strip('\x00')
    # Matroska spec: a TrackEntry with NO Language element IS English ('eng',
    # or 'en' for LanguageBCP47). Many upscale/anime releases omit the tag on
    # their English sub track; Kodi shows it as 'eng' for exactly this reason.
    # Without this default we are stricter than the spec and miss the very
    # track the user asked for. Record whether the tag was explicit so a track
    # that really carries 'eng' outranks one that only defaulted to it.
    t['lang_explicit'] = bool(t['lang'])
    if not t['lang']:
        t['lang'] = 'eng'
    return t


def _head_enough(head, limit=None):
    """Would more bytes change what _parse_head reads from `head`? False while
    an element before the first Cluster is still cut off; True once the walk
    reaches a Cluster or an element of unknown size (where the parse stops),
    or the start is not Matroska at all (the parse raises either way). With
    `limit` (the most a single read would have fetched), also True once the
    next element starts at or runs past it: a single read would not have
    used its payload either (attachments in front of the first Cluster)."""
    buf = Buf(head, 0)
    eid, _l = read_vint(buf, True)
    if eid is None:
        return False
    if eid != EBML:
        return True
    esize, slen = read_vint(buf, False)
    if slen == 0:
        return False
    if esize is None:
        return True
    buf.p += esize
    eid, _l = read_vint(buf, True)
    if eid is None:
        return False
    if eid != SEGMENT:
        return True
    _size, slen = read_vint(buf, False)
    if slen == 0:
        return False
    p = buf.p
    while p < len(head):
        buf.p = p
        eid, _l = read_vint(buf, True)
        if eid is None:
            return False
        size, slen = read_vint(buf, False)
        if slen == 0:
            return False
        if eid == CLUSTER or size is None:
            return True
        p = buf.p + size
        if limit is not None and p > limit:
            return True
    return False


_EBML_MAGIC = b'\x1a\x45\xdf\xa3'


class NotMatroska(ValueError):
    """The file has a head, and it is another container (MP4, AVI, TS)."""


def _container_name(head):
    """'MP4' / 'AVI' / 'MPEG-TS' for a head that is one of them, else None
    (an error page from an expired link is not another container; review)."""
    if head[4:8] == b'ftyp':
        return 'MP4'
    if head[:4] == b'RIFF':
        return 'AVI'
    if len(head) > 188 and head[:1] == b'\x47' and head[188:189] == b'\x47':
        return 'MPEG-TS'
    return None


def parse_head(src, head_bytes, log):
    """(seg_start, ts_scale_ns, tracks, seeks) or raises.
    `seeks` maps element-id -> absolute file offset (from the SeekHead)."""
    piece = getattr(src, '_piece', None)
    if piece and head_bytes > piece:
        # Only as far as the parse below looks: it stops at the first Cluster
        # (or an element of unknown size), so bytes past that change nothing.
        head = b''
        while len(head) < head_bytes:
            want = min(piece, head_bytes - len(head))
            more = src.read(len(head), want)
            head += more
            if len(more) < want or _head_enough(head, head_bytes):
                break
            if len(head) >= 4 and head[:4] != _EBML_MAGIC:
                break           # another container: the rest says nothing more
        # A single read was all or nothing; a piece that failed part-way must
        # not leave a cut head to parse as if it were whole.
        if (head[:4] == _EBML_MAGIC and not _head_enough(head, head_bytes)
                and len(head) < min(head_bytes, src.total or head_bytes)):
            raise ValueError('head read cut short (%d bytes)' % len(head))
    else:
        head = src.read(0, head_bytes)
    # Reused by the content signature so a remote cue-profile probe does not
    # issue another request for bytes it already owns.
    try:
        src._cut_head = head[:CUT_SAMPLE_BYTES]
    except Exception:
        pass
    buf = Buf(head, 0)
    eid, _l = read_vint(buf, True)
    if eid != EBML:
        kind = _container_name(head)
        try:
            src._other_head = head      # the MP4 reader's box headers come from it
        except Exception:
            pass
        if kind:
            raise NotMatroska('not EBML/Matroska (%s)' % kind)
        raise ValueError('not EBML/Matroska (%d byte(s) read)' % len(head))
    esize, _sl = read_vint(buf, False)
    if esize is None:
        raise ValueError('bad EBML header')
    buf.p += esize
    eid, _l = read_vint(buf, True)
    if eid != SEGMENT:
        raise ValueError('no Segment')
    _segsize, _sl = read_vint(buf, False)
    seg_start = buf.p

    ts_scale = 1000000
    tracks = []
    seeks = {}
    p = seg_start
    while p < len(head):
        buf.p = p
        eid, _idl = read_vint(buf, True)
        if eid is None:
            break
        size, slen = read_vint(buf, False)
        if slen == 0:
            break
        pstart = buf.p
        if eid == CLUSTER:
            break
        if size is None:
            break
        in_head = pstart + size <= len(head)
        payload = head[pstart:pstart + size] if in_head else b''
        if eid == SEEKHEAD and in_head:
            for sid, spos in _seekhead_entries(payload, seg_start):
                seeks[sid] = spos
        elif eid == INFO and in_head:
            ibuf = Buf(payload, 0)
            for ieid, isize, istart in walk(ibuf, len(payload)):
                if isize is None:
                    break
                ip = payload[istart:istart + isize]
                ibuf.p = istart + isize
                if ieid == TS_SCALE:
                    ts_scale = read_uint(ip) or 1000000
        elif eid == TRACKS and in_head:
            tbuf = Buf(payload, 0)
            for teid, tsize, tstart in walk(tbuf, len(payload)):
                if tsize is None:
                    break
                tp = payload[tstart:tstart + tsize]
                tbuf.p = tstart + tsize
                if teid == TRACKENTRY:
                    tracks.append(_parse_track_entry(tp))
        p = pstart + size

    # SeekHead fallback for Tracks that live beyond the head fetch.
    if not tracks and seeks.get(TRACKS):
        pos = seeks[TRACKS]
        raw = src.read(pos, 512 * 1024)
        b2 = Buf(raw, pos)
        eid2, _ = read_vint(b2, True)
        size2, sl2 = read_vint(b2, False)
        if eid2 == TRACKS and sl2 and size2 is not None:
            if b2.p + size2 > len(raw):
                raw += src.read(pos + len(raw), size2 - (len(raw) - b2.p))
                b2 = Buf(raw, pos)
                read_vint(b2, True)
                read_vint(b2, False)
            tp_all = raw[b2.p:b2.p + size2]
            tbuf = Buf(tp_all, 0)
            for teid, tsize, tstart in walk(tbuf, len(tp_all)):
                if tsize is None:
                    break
                tp = tp_all[tstart:tstart + tsize]
                tbuf.p = tstart + tsize
                if teid == TRACKENTRY:
                    tracks.append(_parse_track_entry(tp))

    log('head: %d track(s), ts_scale=%dns' % (len(tracks), ts_scale))
    return seg_start, ts_scale, tracks, seeks


def is_text_codec(codec):
    up = (codec or '').upper()
    return up.startswith(_TEXT_CODEC_PREFIX) or up in _LEGACY_ASS_CODECS


def sub_tracks(tracks):
    return [t for t in tracks
            if t.get('type') == SUB_TRACK_TYPE and t.get('num') is not None]


# ---- Cues -------------------------------------------------------------------
def _seekhead_entries(payload, seg_start):
    """[(element id, absolute position)] of one SeekHead's payload."""
    out = []
    sbuf = Buf(payload, 0)
    for seid, ssize, sstart in walk(sbuf, len(payload)):
        if ssize is None:
            break
        sp = payload[sstart:sstart + ssize]
        sbuf.p = sstart + ssize
        if seid == SEEK:
            ibuf = Buf(sp, 0)
            sid, spos = None, None
            for ieid, isize, istart in walk(ibuf, len(sp)):
                if isize is None:
                    break
                ip = sp[istart:istart + isize]
                ibuf.p = istart + isize
                if ieid == SEEKID:
                    sid = read_uint(ip)
                elif ieid == SEEKPOS:
                    spos = read_uint(ip)
            if sid is not None and spos is not None:
                out.append((sid, seg_start + spos))
    return out


# A SeekHead may list a further SeekHead instead of the Cues: QA, 17 Nov 2026,
# Griff Across the Alps S01E01 1080p HDTV H264-DARKFLiX (2.2 GB) -- the head's
# SeekHead lists a SeekHead and the Tags at the end of the file, that one lists
# another SeekHead and the Info, and the Cues (with an entry for every
# subtitle line) are listed there. Read from the head alone the file had "no
# index": its English text track was picked by itself and given up in 2 s,
# with nothing on screen and no external subtitle in any language to
# translate instead (a viewer's log the same day). Matroska allows the chain;
# a few small reads at most, and only when the head's SeekHead has no Cues.
_SEEKHEAD_HOPS = 4
_SEEKHEAD_CAP = 64 * 1024


def cues_pos(src, seeks, seg_start, log=None):
    """The Cues element's absolute position: from the head's SeekHead, or by
    following the further SeekHeads it lists (at most _SEEKHEAD_HOPS reads;
    what they list joins `seeks`, so a second caller reads nothing again).
    None when there is no Cues. A read that failed on the way (an empty or
    short answer, an error) marks `src._cues_unsure`: the file may well have
    an index, so _extract_cues does not call it 'no_index' (review of
    0.3.90 -- the 0.3.81 lesson). Never raises."""
    pos = seeks.get(CUES)
    if pos:
        return pos
    seen = set()
    nxt = seeks.get(SEEKHEAD)
    hops = 0
    try:
        while nxt and nxt not in seen and hops < _SEEKHEAD_HOPS:
            seen.add(nxt)
            hops += 1
            raw = src.read(nxt, 4096)
            if not raw:
                _mark_cues_unsure(src)
                break
            b = Buf(raw, nxt)
            eid, _l = read_vint(b, True)
            size, slen = read_vint(b, False)
            if (eid != SEEKHEAD or not slen or size is None
                    or size > _SEEKHEAD_CAP):
                break
            if b.p + size > len(raw):
                raw += src.read(nxt + len(raw), b.p + size - len(raw)) or b''
                if b.p + size > len(raw):
                    _mark_cues_unsure(src)
                    break
            nxt = None
            for sid, spos in _seekhead_entries(raw[b.p:b.p + size], seg_start):
                if sid == SEEKHEAD:
                    if nxt is None and spos not in seen:
                        nxt = spos
                elif sid not in seeks:
                    seeks[sid] = spos
    except Exception:
        _mark_cues_unsure(src)
    # remembered either way: the chain is read once a file
    seeks[SEEKHEAD] = None
    pos = seeks.get(CUES)
    if pos and log is not None:
        log('cues: listed by a further SeekHead (%d read(s))' % hops)
    return pos


def _mark_cues_unsure(src):
    try:
        src._cues_unsure = True
    except Exception:
        pass
