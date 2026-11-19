# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The resume state of an interrupted extraction (identity of the media, load / save / clear).

import os
import struct


# ---- resume ------------------------------------------------------------------
# A deferral used to throw the whole pass away. On a provider that rate-limits
# hard, an HTTP extraction can ONLY ever be interrupted -- circuit-breaker, byte
# cap, time budget, or the user resuming after a pause -- so it never completed
# at all, no matter how many times the file was played. Persisting what a pass
# collected turns that dead end into progress: the next attempt starts where the
# last one stopped, and the extraction finishes across a few plays with the user
# doing nothing.
#
# Only whole CLUSTERS are recorded as finished -- one whose every targeted cue
# resolved, or that a window scan covered cleanly. A partially-resolved cluster
# is simply redone; _entries_to_srt already de-duplicates on (start, text), so
# redoing one costs a request, never a duplicated line.
#
# A partial extract is still NEVER delivered. This changes only what survives an
# interruption, not what counts as a finished subtitle.
_RESUME_MAGIC = b'KPIEMB1'
_RESUME_MAX = 16 * 1024 * 1024     # ~200x a feature film's worth of cues


def resume_identity(src, want, codec, positions):
    """Fingerprint of THIS file and track. A debrid URL is per-session and is
    regenerated between plays, so it is deliberately NOT part of the identity --
    what makes reusing saved work safe is that the byte length, the track, the
    codec and the Cues layout all still match. Any mismatch discards it."""
    import hashlib
    h = hashlib.sha1()
    h.update(('%d|%s|%s|%d|%d|%d|%d' % (
        src.total, want, codec or '', len(positions),
        positions[0][0] if positions else 0,
        positions[-1][0] if positions else 0,
        sum(p[1] or 0 for p in positions[:64]))).encode('utf-8'))
    return h.hexdigest()


def resume_load(path, identity, log):
    """(entries, finished_clusters, scan_pending_clusters) from an earlier pass
    over this exact file, or ([], set(), set()). Never raises: any doubt returns
    nothing, and the extraction simply starts over."""
    try:
        if not path or not os.path.exists(path):
            return [], set(), set()
        if os.path.getsize(path) > _RESUME_MAX:
            return [], set(), set()
        with open(path, 'rb') as f:
            blob = f.read()
        head, _sep, body = blob.partition(b'\n')
        parts = head.split(b' ')
        if len(parts) != 2 or parts[0] != _RESUME_MAGIC:
            return [], set(), set()
        if parts[1].decode('ascii', 'replace') != identity:
            log('resume: saved work belongs to a different file -- ignoring it')
            return [], set(), set()
        entries, done, scan = [], set(), set()
        p, n = 0, len(body)
        while p < n:
            tag = body[p:p + 1]
            p += 1
            if tag == b'E':
                if p + 20 > n:
                    return [], set(), set()
                ticks, dur, ln = struct.unpack('<qqI', body[p:p + 20])
                p += 20
                frame = body[p:p + ln]
                p += ln
                if len(frame) != ln:
                    return [], set(), set()   # truncated -> trust none of it
                entries.append((ticks, None if dur < 0 else dur, frame))
            elif tag in (b'K', b'S'):
                if p + 8 > n:
                    return [], set(), set()
                (cpos,) = struct.unpack('<Q', body[p:p + 8])
                (done if tag == b'K' else scan).add(cpos)
                p += 8
            else:
                return [], set(), set()
        if entries or done or scan:
            log('resume: %d cue(s), %d finished cluster(s) and %d awaiting a '
                'scan, recovered from an earlier pass'
                % (len(entries), len(done), len(scan)))
        return entries, done, scan
    except Exception:
        return [], set(), set()


def resume_save(path, identity, entries, done, scan, log):
    """Persist an interrupted pass. Never raises; a failure only means the next
    attempt starts over, which is exactly today's behaviour."""
    if not path:
        return
    try:
        out = [_RESUME_MAGIC + b' ' + identity.encode('ascii') + b'\n']
        size = len(out[0])
        for ticks, dur, frame in entries:
            rec = b'E' + struct.pack('<qqI', int(ticks),
                                     -1 if dur is None else int(dur),
                                     len(frame)) + frame
            size += len(rec)
            if size > _RESUME_MAX:
                # Save nothing rather than a subset: dropping entries while
                # keeping their cluster marked finished would lose lines
                # permanently.
                return
            out.append(rec)
        for cpos in done:
            out.append(b'K' + struct.pack('<Q', int(cpos)))
        for cpos in scan:
            out.append(b'S' + struct.pack('<Q', int(cpos)))
        tmp = path + '.part'
        with open(tmp, 'wb') as f:
            f.write(b''.join(out))
        os.replace(tmp, path)
        log('resume: saved %d cue(s) / %d finished cluster(s) / %d awaiting a '
            'scan, for the next attempt'
            % (len(entries), len(done), len(scan)))
    except Exception:
        try:
            if os.path.exists(path + '.part'):
                os.remove(path + '.part')
        except Exception:
            pass


def resume_clear(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
