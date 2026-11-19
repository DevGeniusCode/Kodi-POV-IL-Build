# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Text extraction from an MP4 / MOV container (the mov_text track), over the same source.

import time
from resources.lib.fetching.embedded import embedded_mp4
from resources.lib.fetching.embedded.extract.tuning import HTTP_PIECE_CLUSTERS, HTTP_TOTAL_CAP, MULTI_RANGES
from resources.lib.fetching.embedded.extract.extract_http import join_spans, WINDOWS_MAX
from resources.lib.fetching.embedded.extract.tracks import choose_track, lang_key
from resources.lib.fetching.embedded.extract.srt_rows import fmt_ts
from resources.lib.fetching.embedded.extract.matroska import is_text_codec


def _rows_to_srt(rows):
    """[(start_ms, end_ms, text)] -> SRT, in time order."""
    out = []
    for i, (start, end, text) in enumerate(sorted(rows), 1):
        if end <= start:
            end = start + 700
        out.append('%d' % i)
        out.append('%s --> %s' % (fmt_ts(max(0, start)), fmt_ts(max(1, end))))
        out.append(text)
        out.append('')
    return '\n'.join(out)


def _mp4_cut(src, log):
    """A reading cut part-way (the player's buffer, the budget, a dropped
    answer): not the file's fault, so not 'not_mkv' -- no external subtitle
    instead -- and nothing is saved, so no "progress saved" either (review)."""
    if src._stats is not None:
        src._stats['why'] = 'mp4_cut'
        src._stats['done'] = 0
    log('MP4: the reading was cut -- pick it again to read it again')
    return None


def extract_srt_mp4(src, track_num, lang, other_text, on_track, deadline_s,
                     t0, progress_cb, steps, log):
    """extract_srt for an MP4 (see embedded_mp4): the sample tables say where
    every subtitle sample is and when it plays, so only those bytes are read
    -- batched like a Matroska track's blocks, the lines near the viewer first
    when `steps` asks for windows. None when it cannot be read (the caller
    then offers the external subtitle)."""
    mp4 = embedded_mp4
    head = getattr(src, '_other_head', b'') or b''

    def read(off, n):
        if off + n <= len(head):
            return head[off:off + n]
        return src.read(off, n)
    found = mp4.find_moov(read, src.total)
    if found is None:
        log('MP4: no moov box within the first %d top-level boxes'
            % mp4.TOP_BOXES_MAX)
        return None
    m_off, m_size = found
    piece = src._piece
    if src.is_http:
        src._piece = HTTP_PIECE_CLUSTERS
    try:
        # a small moov right after ftyp is already in the head
        moov = read(m_off, m_size)
    finally:
        src._piece = piece
    if len(moov) < m_size:
        log('MP4: moov read short (%d of %d bytes)' % (len(moov), m_size))
        return None
    subs = mp4.tracks(moov)
    if not subs:
        log('MP4: no subtitle track (moov %.1f MB)' % (m_size / 1e6))
        return None
    # A text track with a handful of lines beside a full one in the same
    # language is its signs/forced track, flagged or not (QA, 14 Nov 2026: The
    # Dark Knight 2160p DV WEB-DL has a 49-sample track beside ~3,700-sample
    # ones): never the one translated.
    lines = {}
    for t in subs:
        if is_text_codec(t['codec']):
            lines[t['num']] = sum(1 for s in mp4.samples(moov, t)
                                  if s[1] > mp4.empty_size(t['entry']))
    for t in subs:
        n = lines.get(t['num'])
        if n is None or t['forced']:
            continue
        key = lang_key(t['lang'])
        most = max(lines.get(o['num'], 0) for o in subs
                   if lang_key(o['lang']) == key)
        if n < 200 and most >= 5 * max(1, n):
            t['forced'] = True
    track = choose_track(src, subs, track_num, lang, other_text, on_track, log)
    if track is None:
        log('MP4: no matching text track (num=%s lang=%s); the file has: %s'
            % (track_num, lang, ', '.join(
                '#%s %s [%s]%s' % (t['num'], t['codec'], t['lang'],
                                   ' forced' if t['forced'] else '')
                for t in subs)))
        return None
    if not is_text_codec(track['codec']):
        log('MP4: track #%s is %s (not text) -- skipping'
            % (track['num'], track['codec']))
        return None
    entry = track['entry']
    every = mp4.samples(moov, track)
    keep = [s for s in every
            if mp4.empty_size(entry) < s[1] <= mp4.SAMPLE_BYTES_MAX]
    total = len(keep)
    log('MP4: moov %.1f MB at %d; track #%s %s [%s]: %d sample(s), %d with '
        'text' % (m_size / 1e6, m_off, track['num'], entry.decode('latin1'),
                  track['lang'], len(every), total))
    if not keep:
        return None
    rows = {}
    if src._stats is not None:
        src._stats['done'], src._stats['total'] = 0, total

    def fetch(idx):
        """Read the samples keep[i] for i in idx (batched). False when the
        reading was cut (abort, budget, byte cap, a short answer)."""
        want = sorted(idx, key=lambda i: keep[i][0])
        spans = join_spans([(keep[i][0], keep[i][1]) for i in want])
        k = 0
        g = 0
        while g < len(spans):
            if (time.time() - t0) > deadline_s:
                log('MP4: time budget reached')
                return False
            if src.fetched_now() >= HTTP_TOTAL_CAP:
                log('MP4: byte cap reached')
                return False
            share = src.parallel_now() if src.is_http else 1
            group = spans[g:g + MULTI_RANGES * share]
            g += len(group)
            if share > 1:
                src.prefetch_many(group, keep_from=group[0][0])
            elif (src.is_http and len(group) > 1
                    and getattr(src, '_multi', None) is not False):
                src.prefetch(group, keep_from=group[0][0])
            for off, size in group:
                blob = src.read(off, size)
                if len(blob) < size and not (src.total and off + len(blob) >= src.total):
                    log('MP4: short read at %d (%d of %d bytes)'
                        % (off, len(blob), size))
                    return False
                while k < len(want) and keep[want[k]][0] < off + size:
                    i = want[k]
                    s_off, s_size, start, end = keep[i]
                    text = mp4.sample_text(blob[s_off - off:s_off - off + s_size],
                                           entry)
                    if text:
                        rows[i] = (start, end, text)
                    else:
                        rows.setdefault(i, None)
                    k += 1
            if src._stats is not None:
                src._stats['done'] = len(rows)
            if progress_cb:
                try:
                    progress_cb(len(rows), total)
                except Exception:
                    pass
        return True

    def srt_so_far():
        return _rows_to_srt([r for r in rows.values() if r])

    if steps and src.is_http:
        window, show, timeline = steps[:3]
        try:
            timeline(sorted(s[2] for s in keep))
            for n in range(WINDOWS_MAX):
                span = window()
                if not span:
                    break
                low, high = span
                todo = [i for i, s in enumerate(keep)
                        if low <= s[2] <= high and i not in rows]
                if len(rows) + len(todo) >= total:
                    break                      # all that is left: one go
                if todo:
                    if not fetch(todo):
                        return _mp4_cut(src, log)
                    log('MP4 window %d: film %.0f..%.0f ms, %d line(s), %d of '
                        '%d read' % (n + 1, low, high, len(todo), len(rows),
                                     total))
                show(srt_so_far())
        except Exception as ex:
            log('MP4 windows: %r -- reading the rest in one go' % (ex,))
    rest = [i for i in range(total) if i not in rows]
    if rest and not fetch(rest):
        return _mp4_cut(src, log)
    srt = srt_so_far()
    log('MP4: extracted %d line(s) from track #%s, %.1fMB, %.1fs'
        % (srt.count('-->'), track['num'], src.fetched / 1e6, time.time() - t0))
    return srt or None
