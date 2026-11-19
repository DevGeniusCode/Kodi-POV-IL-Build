# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Aligning a reference subtitle with the source: SRT parsing, the global offset / scale search, the windowed
# alignment and its gates, align_one.

import re
try:
    from resources.lib.core import kodi, settings
except Exception:
    kodi = settings = None
from resources.lib.core.srt.cues import MIN_CUES, STATUS_UNKNOWN, to_ms


def log(msg, level='INFO'):
    if kodi is None:
        return
    try:
        kodi.log('arabic_gender: ' + msg, level=level)
    except Exception:
        pass


# ---------------- SRT parsing (encoding-robust, timecode-aware) -------------

_TIME_RE = re.compile(
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')
_NONDIALOG = re.compile(r'\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|<[^>]+>')
_MUSIC = ('♪', '♫', '#')
_RAW_AMP = re.compile(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)')


def _parse(text):
    """Parse SRT text -> list of {start,end,text} sorted by start."""
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    cues = []
    for block in re.split(r'\n\s*\n', text):
        lines = [ln for ln in block.split('\n') if ln.strip() != '']
        if not lines:
            continue
        ti = None
        m = None
        for i, ln in enumerate(lines[:3]):
            m = _TIME_RE.search(ln)
            if m:
                ti = i
                break
        if m is None:
            continue
        start = to_ms(*m.group(1, 2, 3, 4))
        end = to_ms(*m.group(5, 6, 7, 8))
        if end < start:
            end = start
        body = ' '.join(lines[ti + 1:]).strip()
        body = _NONDIALOG.sub(' ', body)
        body = re.sub(r'\s{2,}', ' ', body).strip()
        cues.append({'start': start, 'end': end, 'text': body})
    cues.sort(key=lambda c: c['start'])
    return cues


def _is_dialogue(text):
    t = text or ''
    if any(mk in t for mk in _MUSIC):
        return False
    t = _NONDIALOG.sub(' ', t)
    # Letter ranges cover every script in the reference chain: basic+extended
    # Latin (es/fr/it/pt/pl/cs/ro/hr/sk/nl), Greek, Cyrillic (ru/uk/bg/sr),
    # Hebrew, Arabic (also Urdu), Devanagari (Hindi). Without the extra ranges
    # a Cyrillic/Greek/Hindi reference sub would parse as "no dialogue" and be
    # rejected before alignment even ran.
    letters = re.sub(
        r'[^A-Za-zÀ-ÖØ-öø-ɏͰ-ϿЀ-ӿ'
        r'֐-׿؀-ۿऀ-ॿ]', '', t)
    return len(letters) >= 2


# ---------------- time-map estimation (fps + offset) ------------------------

_FPS = [24000 / 1001, 24.0, 25.0, 30000 / 1001, 30.0]
_SCALES = sorted({1.0} | {round(p / q, 6) for p in _FPS for q in _FPS
                          if 0.9 <= p / q <= 1.11})
_TOL = 500
_TIGHT = 250
_MAXOFF = 600000
_SAMPLE = 500


def _best_offset(en_on, ar_on, a):
    step = max(1, len(en_on) // _SAMPLE)
    import bisect
    hist = {}
    for e in en_on[::step]:
        pe = a * e
        lo = bisect.bisect_left(ar_on, pe - _MAXOFF)
        hi = bisect.bisect_right(ar_on, pe + _MAXOFF)
        for j in range(lo, hi):
            b = int(round((ar_on[j] - pe) / _TOL))
            hist[b] = hist.get(b, 0) + 1
    if not hist:
        return 0.0, 0
    peak = max(hist, key=lambda k: hist[k] + hist.get(k - 1, 0)
               + hist.get(k + 1, 0))
    votes = hist.get(peak - 1, 0) + hist.get(peak, 0) + hist.get(peak + 1, 0)
    return float(peak * _TOL), votes


# ---------------- public: build the per-entry gender map --------------------

def _arabic_for_blocks(src_blocks, ar_cues, a, b, segments=None):
    """Return {srt_entry_number: aligned Arabic dialogue text} for the dialogue
    blocks with a confident time-overlap. Keyed by the block's own SRT number
    (robust to how translate.py later chunks them). SFX/music blocks omitted."""
    import bisect
    ar_centers = [(c['start'] + c['end']) / 2.0 for c in ar_cues]
    ar_starts = [c['start'] for c in ar_cues]
    ar_ends = [c['end'] for c in ar_cues]
    out = {}
    segments = list(segments or [])
    ref_boundaries = [float(seg.get('ref_from_ms')) for seg in segments[1:]
                      if seg.get('ref_from_ms') is not None]
    for blk in src_blocks:
        lines = [ln for ln in blk.split('\n') if ln.strip() != '']
        if len(lines) < 2 or not lines[0].strip().isdigit():
            continue
        num = int(lines[0].strip())
        m = _TIME_RE.search(blk)
        if not m:
            continue
        s = to_ms(*m.group(1, 2, 3, 4))
        e = to_ms(*m.group(5, 6, 7, 8))
        body = ' '.join(lines[2:]).strip()
        if not _is_dialogue(body):
            continue
        applied_b = b
        if segments:
            si = bisect.bisect_right(ref_boundaries, (s + e) / 2.0)
            si = min(si, len(segments) - 1)
            applied_b = float(segments[si].get('offset_ms') or 0.0)
        es, ee = a * s + applied_b, a * e + applied_b
        lo = bisect.bisect_left(ar_ends, es)
        cand = []
        k = lo
        while k < len(ar_cues) and ar_starts[k] < ee:
            ov = min(ee, ar_ends[k]) - max(es, ar_starts[k])
            if ov > 0:
                cand.append((ov, k))
            k += 1
        if cand:
            out[num] = ar_cues[max(cand)[1]]['text']
            continue
        pred = a * ((s + e) / 2.0) + applied_b
        j = bisect.bisect_left(ar_centers, pred)
        bd, bk = 1e9, None
        for kk in (j - 1, j, j + 1):
            if 0 <= kk < len(ar_cues) and abs(ar_centers[kk] - pred) < bd:
                bd, bk = abs(ar_centers[kk] - pred), kk
        if bk is not None and bd <= _TIGHT:
            out[num] = ar_cues[bk]['text']
    return out


# The gender oracle's own fallback when the SubSync-grade gate refuses (0.3.64).
# That gate protects DISPLAYED timing: one global map or at most four >=5 s
# steps, and a whole-file vote. An oracle only needs each line's neighbourhood,
# and most refusals were good pairs: a 64% whole-file vote on a file whose
# every window sits at +0.2 s, a broadcast's 3-4 s step at each ad break (five
# plateaus), one music window minutes off. Here a ~5-minute window counts when
# a neighbour agrees with its offset -- a random pairing scores high votes in
# dense windows too, but at scattered offsets -- windows within 0.6 s form a
# plateau, the cut points between plateaus come from SubSync's own boundary
# search, the clock that lines the cue onsets up best wins, and a line in a
# window nothing vouched for gets no reference. Measured on the aitr corpus (10
# titles, 5 languages; known answers from the gate's map of clean pairs with
# cuts injected): 16 of 28 refused same-title pairs rescued; none of 270 pairs
# with another title's reference accepted, nor any of 5,400 short sources
# (80-240 lines) against them; wrong lines about 0.4% on staircases of either
# sign, 1-1.5 s steps and clock drift, 1-2% with six mixed or large steps, 2-3%
# with a few scenes deleted from the reference (4% with eight short ones).
_WIN_SAME_MS = 1500        # a neighbouring window agrees
_WIN_GROUP_MS = 600        # windows on one plateau (wider smears a staircase)
_WIN_TIGHT_MS = 500        # a weak window needs both neighbours this close
_WIN_WEAK = (0.45, 0.80)   # vote, overlap of a weak window (other line splits)
_WIN_MIN_WINDOWS = 8       # 4-5 short windows saturate on a dense reference
_WIN_MIN_COVER = 0.60      # share of source dialogue cues in vouched windows
_WIN_MIN_OVERLAP = 0.65    # the historical oracle overlap contract, after the map
_WIN_LINE_ONSET_MS = 500    # a line's matched cue: onset this close ...
_WIN_LINE_IOU = 0.5        # ... or this much of their union shared


def _windowed_built(en, ar, s, ws, ok):
    """One clock's plan: (segments, the reference cues mapped) or None."""
    from resources.lib.core.srt import cues as srt_cues
    from resources.lib.sync.align import piecewise
    groups = []
    for w, k in zip(ws, ok):
        if not k:
            continue
        if groups and abs(w['offset_ms'] - srt_cues.cues_median(
                [x['offset_ms'] for x in groups[-1]])) < _WIN_GROUP_MS:
            groups[-1].append(w)
        else:
            groups.append([w])
    offsets = [srt_cues.cues_median([w['offset_ms'] for w in g]) for g in groups]
    if len(groups) == 1:
        return [{'offset_ms': offsets[0]}], piecewise.map_cues_global(ar, s, offsets[0])
    cuts = piecewise.optimize_piecewise_boundaries(en, ar, s, groups, offsets)
    if cuts is None:
        return None
    segments = []
    for i, off in enumerate(offsets):
        seg = {'offset_ms': off}
        if i:
            c = cuts[i - 1]
            # the boundary on the source timeline, each side's own inverse
            seg['ref_from_ms'] = ((ar[c - 1]['end'] - offsets[i - 1]) / s
                                  + (ar[c]['start'] - off) / s) / 2.0
            seg['cand_from_ms'] = (ar[c - 1]['end'] + ar[c]['start']) / 2.0
        segments.append(seg)
    return segments, piecewise.map_cues_piecewise(ar, s, segments)


def _windowed_plan(en, ar, gate_scale):
    """(scale, segments, spans, diag) for _arabic_for_blocks, or
    (None, None, None, diag). `spans`: the source-time ranges of the vouched
    windows; `sa`: sync_align."""
    # identity first, then the familiar clock ratios nearest to it, the gate's
    # own estimate last: a tie keeps the plainer clock
    from resources.lib.sync.align import common, estimate, piecewise, verify
    scales = [1.0] + sorted((s for s in set(round(x, 6) for x in estimate.SCALES)
                             if s != 1.0), key=lambda s: abs(s - 1.0))
    if gate_scale and round(float(gate_scale), 6) not in scales:
        scales.append(float(gate_scale))
    found = []
    for order, s in enumerate(scales):
        estimate.breathe()
        b, _votes = estimate.best_offset([c['start'] for c in en],
                                    [c['start'] for c in ar], s, common.MAXOFF)
        ws = piecewise.piecewise_local_windows(en, ar, s, center_offset=b)
        if len(ws) < _WIN_MIN_WINDOWS:
            return None, None, None, 'windowed: %d windows' % len(ws)
        strong = [w['vote'] >= piecewise.LOCAL_MIN_VOTE
                  and w['overlap'] >= piecewise.LOCAL_MIN_OVERLAP for w in ws]
        weak = [w['vote'] >= _WIN_WEAK[0] and w['overlap'] >= _WIN_WEAK[1]
                for w in ws]
        ok = []
        for i, w in enumerate(ws):
            near = [j for j in (i - 1, i + 1) if 0 <= j < len(ws)]
            if strong[i]:
                ok.append(any((strong[j] or weak[j]) and abs(
                    ws[j]['offset_ms'] - w['offset_ms']) < _WIN_SAME_MS
                    for j in near))
            elif weak[i]:
                ok.append(len(near) == 2 and all(weak[j] and abs(
                    ws[j]['offset_ms'] - w['offset_ms']) < _WIN_TIGHT_MS
                    for j in near))
            else:
                ok.append(False)
        cover = (sum(w['end'] - w['begin'] for w, k in zip(ws, ok) if k)
                 / float(len(en)))
        if cover >= _WIN_MIN_COVER:
            found.append((round(cover, 2), order, cover, s, ws, ok))
    if not found:
        return None, None, None, 'windowed: cover under %d%%' % (
            100 * _WIN_MIN_COVER)
    why = ''
    # the most vouched-for cues first; among those, the clock whose map lines
    # the cue onsets up best (the fewest plateaus can favour a sloped clock
    # laid through a staircase: every window agrees, every line 1-2 s off)
    for level in sorted(set(f[0] for f in found), reverse=True):
        best = None
        for _lvl, order, cover, s, ws, ok in [f for f in found if f[0] == level]:
            estimate.breathe()
            built = _windowed_built(en, ar, s, ws, ok)
            if built is None:
                why = 'windowed: no cut placement'
                continue
            segments, fixed = built
            ov = verify.overlap_rate(en, fixed, 1.0, 0.0)
            if ov < _WIN_MIN_OVERLAP:
                why = 'windowed: overlap %.0f%%' % (100 * ov)
                continue
            tight = verify.tight_agreement(en, fixed, 1.0, 0.0) or 0.0
            key = (round(tight, 3), -order)
            if best is None or key > best[0]:
                best = (key, cover, s, ws, ok, segments, ov, tight)
        if best is None:
            continue
        _key, cover, s, ws, ok, segments, ov, tight = best
        spans = []
        for i, (w, k) in enumerate(zip(ws, ok)):
            if k:
                spans.append((en[w['begin']]['start'] if i else float('-inf'),
                              en[ws[i + 1]['begin']]['start'] if i + 1 < len(ws)
                              else float('inf')))
        return s, segments, spans, (
            'windowed scale=%.6f cover=%.0f%% windows=%d/%d plateaus=%d '
            'overlap=%.0f%% tight=%.0f%%' % (
                s, 100 * cover, sum(ok), len(ws), len(segments), 100 * ov,
                100 * tight))
    return None, None, None, why


def _windowed_for_blocks(src_blocks, ar, plan):
    """{srt entry number: reference line} for the dialogue blocks in vouched
    windows whose matched reference cue fits them: its onset within
    _WIN_LINE_ONSET_MS of the block's mapped onset, or the two overlapping by
    _WIN_LINE_IOU of their union. A line the reference lacks (a scene cut
    from its version) or one beside a cut lands on a cue that fits it badly;
    measured on injected staircases and deleted scenes, this halves the wrong
    lines (staircases 1.0 -> 0.4%, deleted scenes 3.7-5.1 -> 1.9-2.8%) for
    about a tenth of the hinted ones."""
    import bisect
    s, segments, spans = plan
    bounds = [float(seg['ref_from_ms']) for seg in segments[1:]]
    starts = [c['start'] for c in ar]
    ends = [c['end'] for c in ar]
    out = {}
    for blk in src_blocks:
        lines = [ln for ln in blk.split('\n') if ln.strip() != '']
        if len(lines) < 2 or not lines[0].strip().isdigit():
            continue
        m = _TIME_RE.search(blk)
        if not m:
            continue
        b0 = to_ms(*m.group(1, 2, 3, 4))
        b1 = to_ms(*m.group(5, 6, 7, 8))
        if not _is_dialogue(' '.join(lines[2:]).strip()):
            continue
        mid = (b0 + b1) / 2.0
        if not any(lo <= mid < hi for lo, hi in spans):
            continue
        off = float(segments[min(bisect.bisect_right(bounds, mid),
                                 len(segments) - 1)]['offset_ms'])
        es, ee = s * b0 + off, s * b1 + off
        j = bisect.bisect_left(ends, es)
        best = None
        while j < len(ar) and starts[j] < ee:
            ov = min(ee, ends[j]) - max(es, starts[j])
            if ov > 0 and (best is None or ov > best[0]):
                best = (ov, j)
            j += 1
        if best is None:
            continue
        k = best[1]
        union = max(ee, ends[k]) - min(es, starts[k])
        if (abs(starts[k] - es) <= _WIN_LINE_ONSET_MS
                or (union > 0 and best[0] / float(union) >= _WIN_LINE_IOU)):
            out[int(lines[0].strip())] = ar[k]['text']
    return out


# The gate's own map, checked window by window (0.3.65). The gate needs 60% of
# its ~5-minute windows to agree and then maps EVERY line by its map: measured
# (32 clean pairs of the aitr corpus, known answers from their gate maps), the
# references it passed with a third of another title's subtitle in the middle
# had 25% of their lines wrong, with three scenes cut out 7%, eight short cuts
# 11%. A window holds when it agrees with the map as _windowed_plan vouches
# one. One that does not CONTRADICTS the map when the reference has lines
# there and fewer than _GATE_FIT_MIN of them fit it (_WIN_LINE_*): in the
# spliced and cut references most such windows sit under it and carry nearly
# all their wrong lines. A contradiction hands the reference to the windowed
# plan, else keeps only the fitting lines of the windows that hold; without
# one the gate's map stands as it did (dropping the lines that do not fit in
# the other windows cost a clean pair 0.45% of its right lines and saved no
# wrong one). Three seeds of those shapes, every path: another title's third
# 12.5% of lines wrong -> 1.3% (right lines 61.1 -> 60.4%), its sixth 8.4 ->
# 1.1% (82.3 -> 75.4%), three cut scenes 3.5 -> 2.3%, eight short cuts 4.4 ->
# 3.5%, small steps 1.8 -> 1.2%; clean pairs, offsets, clocks and staircases
# unchanged line for line.
# The margin on clean references is thin (review, 0.3.65): of 88
# gate-accepted clean pairs (either English source against every other file)
# none contradicts, but their lowest such windows reach 0.650, 0.656 and 0.667
# -- and those already sit 1.5 s or more off the map, so asking for that too
# separates nothing. A clean pair that crossed would lose about a third of its
# hints and gain next to no wrong one (0, 0 and 3 lines); at 0.60 instead,
# of the references the gate passed, a sixth of another title would keep 2.9%
# of its lines wrong (1.2% here) and three cut scenes 3.9% (2.8%).
_GATE_FIT_MIN = 0.65


def _gate_windows(en, ar, s, segments):
    """(windows, the source-time spans of those that hold, how many
    contradict) for the gate's map; ([], [], 0) for a source too short for
    windows."""
    from resources.lib.sync.align import piecewise
    import bisect
    bounds = [float(seg['ref_from_ms']) for seg in segments[1:]]

    def offset_at(t):
        return float(segments[min(bisect.bisect_right(bounds, t),
                                  len(segments) - 1)].get('offset_ms') or 0.0)
    ws = piecewise.piecewise_local_windows(en, ar, s,
                           center_offset=float(segments[0].get('offset_ms') or 0.0))
    starts = [c['start'] for c in ar]
    ends = [c['end'] for c in ar]
    held = []
    contra = 0
    for i, w in enumerate(ws):
        span = (en[w['begin']]['start'] if i else float('-inf'),
                en[ws[i + 1]['begin']]['start'] if i + 1 < len(ws)
                else float('inf'))
        d = abs(w['offset_ms'] - offset_at(w['center_ms']))
        if ((w['vote'] >= piecewise.LOCAL_MIN_VOTE
                and w['overlap'] >= piecewise.LOCAL_MIN_OVERLAP
                and d < _WIN_SAME_MS)
                or (w['vote'] >= _WIN_WEAK[0] and w['overlap'] >= _WIN_WEAK[1]
                    and d < _WIN_TIGHT_MS)):
            held.append(span)
            continue
        found = fit = 0
        for c in en[w['begin']:w['end']]:
            off = offset_at((c['start'] + c['end']) / 2.0)
            es, ee = s * c['start'] + off, s * c['end'] + off
            j = bisect.bisect_left(ends, es)
            best = None
            while j < len(ar) and starts[j] < ee:
                ov = min(ee, ends[j]) - max(es, starts[j])
                if ov > 0 and (best is None or ov > best[0]):
                    best = (ov, j)
                j += 1
            if best is None:
                continue
            found += 1
            k = best[1]
            union = max(ee, ends[k]) - min(es, starts[k])
            if (abs(starts[k] - es) <= _WIN_LINE_ONSET_MS
                    or (union > 0 and best[0] / float(union) >= _WIN_LINE_IOU)):
                fit += 1
        n = w['end'] - w['begin']
        if found >= max(MIN_CUES, 0.5 * n) and fit < _GATE_FIT_MIN * found:
            contra += 1
    return ws, held, contra


# A short source (under 160 dialogue lines) has at most 7 windows of ~20
# lines (none under 40), too coarse to see a stretch of another title in its
# reference (review, 0.3.65: a third of one passed with 21% of the lines
# wrong). There the gate's map is also checked line by line: _STRETCH_LINES
# lines in a row that found a reference cue, of which under half fit it
# (_WIN_LINE_*), lose their reference. Measured on the aitr corpus (two seeds,
# 80/110/140-line excerpts of 10 titles x 5 languages, 140 pairs per shape):
# a third of another title 14.5% -> 11.0% of lines wrong, a sixth 7.5 ->
# 5.7%, one cut scene 5.5 -> 4.5%. Clean references (review, 0.3.66: 2,635
# excerpts of 45-155 lines, both English sources against every other file):
# 52 lost lines, 0.39% of all hinted lines, 92% of those right ones. Not on
# long sources: there it cost clean pairs 1.6% of their right lines and saved
# no wrong one (the windows see those stretches already).
_STRETCH_LINES = 10


def _misfit_stretches(src_blocks, ar, s, segments):
    """The SRT entry numbers in stretches of _STRETCH_LINES lines, matched
    under the gate's map, of which under half fit it."""
    import bisect
    bounds = [float(seg['ref_from_ms']) for seg in segments[1:]]
    starts = [c['start'] for c in ar]
    ends = [c['end'] for c in ar]
    matched = []
    for blk in src_blocks:
        lines = [ln for ln in blk.split('\n') if ln.strip() != '']
        if len(lines) < 2 or not lines[0].strip().isdigit():
            continue
        m = _TIME_RE.search(blk)
        if not m or not _is_dialogue(' '.join(lines[2:]).strip()):
            continue
        b0 = to_ms(*m.group(1, 2, 3, 4))
        b1 = to_ms(*m.group(5, 6, 7, 8))
        off = float(segments[min(bisect.bisect_right(bounds, (b0 + b1) / 2.0),
                                 len(segments) - 1)].get('offset_ms') or 0.0)
        es, ee = s * b0 + off, s * b1 + off
        j = bisect.bisect_left(ends, es)
        best = None
        while j < len(ar) and starts[j] < ee:
            ov = min(ee, ends[j]) - max(es, starts[j])
            if ov > 0 and (best is None or ov > best[0]):
                best = (ov, j)
            j += 1
        if best is None:
            continue
        k = best[1]
        union = max(ee, ends[k]) - min(es, starts[k])
        matched.append((int(lines[0].strip()),
                        abs(starts[k] - es) <= _WIN_LINE_ONSET_MS
                        or (union > 0 and best[0] / float(union) >= _WIN_LINE_IOU)))
    bad = set()
    for i in range(len(matched) - _STRETCH_LINES + 1):
        run = matched[i:i + _STRETCH_LINES]
        if 2 * sum(1 for _n, fit in run if fit) < _STRETCH_LINES:
            bad.update(n for n, _fit in run)
    return bad


def align_one(src_text, src_blocks, ar_text, timing_text=None):
    """Try to align ONE Arabic SRT to the source. Returns (ar_for_blocks, diag)
    on success, or (None, diag) when the alignment isn't trustworthy.

    `timing_text` is the source as it was cued before same-time captions were
    merged. The gate measures the clock on those original cues (measured: a
    Netflix file that aligns cleanly cue-by-cue fails local consistency once
    its split captions are merged), and the hints are then placed on
    `src_blocks`, whose merged captions keep the exact same timecodes."""
    # A cue with no duration shows nothing (a marker a subbing tool left at an
    # act break, a damaged line) and the gate's preflight refuses a whole list
    # for one of them. The reference is only read, and the hints are placed on
    # `src_blocks` by their own timecodes, so such a cue is left out of the
    # measurement on either side. Measured on 15 Nov 2026 on the providers'
    # files of six episodes the pool recorded as no_align: 5 of 92 candidates
    # were refused for one such cue (two of Survivor S04E12's four), and a
    # source carrying one lost all 24 of its candidates, 23 of which align.
    en = [c for c in _parse(timing_text or src_text)
          if _is_dialogue(c['text']) and c['end'] > c['start']]
    ar = [c for c in _parse(ar_text)
          if _is_dialogue(c['text']) and c['end'] > c['start']]
    if len(en) < 8 or len(ar) < 8:
        return None, 'too few dialogue cues (en=%d ar=%d)' % (len(en), len(ar))
    # Share the hardened SubSync timing engine: deduped/refined global mapping,
    # local-window consistency, and a strictly validated piecewise map for
    # obvious recap/ad/different-cut steps.  Gender-oracle alignment intentionally
    # keeps its historical 65% overlap contract and does not impose SubSync's
    # display-subtitle tight-onset floor: cross-language line segmentation is
    # different, and an oracle miss is fail-soft (it never changes cue timing).
    try:
        from resources.lib.sync.align import common, verify
        verdict = verify.verify_cue_lists(
            en, ar, min_vote=0.65, min_overlap=0.65,
            allow_piecewise=True, require_tight=False)
        if verdict.get('status') == STATUS_UNKNOWN:
            gate_diag = verdict.get('diag') or 'gate FAILED'
            # the oracle's own windowed fallback (above) -- not for a file
            # the gate refused as malformed before measuring anything
            if (gate_diag.startswith('preflight')
                    or len(en) < common.LOCAL_MIN_REF_CUES
                    or len(ar) < MIN_CUES):
                return None, gate_diag
            plan = _windowed_plan(en, ar, verdict.get('scale'))
            if plan[0] is None:
                return None, gate_diag + ' | ' + plan[3]
            mapping = _windowed_for_blocks(src_blocks, ar, plan[:3])
            if not mapping:
                return None, gate_diag + ' | ' + plan[3] + ' (no lines)'
            return mapping, plan[3]
        a = float(verdict.get('scale') or 1.0)
        b = float(verdict.get('offset_ms') or 0.0)
        mapping = _arabic_for_blocks(
            src_blocks, ar, a, b, segments=verdict.get('segments'))
        diag = 'gate OK (' + (verdict.get('diag') or '') + ')'
        segments = verdict.get('segments') or [{'offset_ms': b}]
        ws, held, contra = _gate_windows(en, ar, a, segments)
        if contra:
            why = ' | the gate map held in %d of %d windows, %d contradict it' % (
                len(held), len(ws), contra)
            plan = _windowed_plan(en, ar, verdict.get('scale'))
            if plan[0] is not None:
                windowed = _windowed_for_blocks(src_blocks, ar, plan[:3])
                if windowed:
                    return windowed, plan[3] + why
            kept = (_windowed_for_blocks(src_blocks, ar, (a, segments, held))
                    if held else {})
            # neither reads as the gate's OK (the log, the telemetry note); the
            # windows that hold count as windowed, as on the dashboard
            if not kept:
                return None, 'gate map contradicted' + why + ' (' + diag + ')'
            return kept, ('windowed: the gate map where it held' + why
                          + ' (' + diag + ')')
        if len(ws) < _WIN_MIN_WINDOWS:
            bad = _misfit_stretches(src_blocks, ar, a, segments)
            if bad:
                mapping = {k: v for k, v in mapping.items() if k not in bad}
                diag += ' | %d lines in stretches off the map left out' % len(bad)
        return mapping, diag
    except Exception as e:
        # sync_align ships in the same add-on.  Falling back to the historical
        # global-only vote here is unsafe: a dominant first half can hide a
        # differently cut second half and attach gender evidence to the wrong
        # dialogue.  Gender hints are optional, so fail closed for this helper.
        log('hardened aligner failed; declining gender reference: {0}'.format(e),
             level='WARNING')
        return None, 'hardened aligner failed; reference declined'
