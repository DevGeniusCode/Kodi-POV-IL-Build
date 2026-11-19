# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Embedded-subtitle TEXT extractor for the AI translation pipeline: extract_srt and the sequential pass.
#
# Reads the *text* of an embedded subtitle track (SRT / ASS-SSA / WebVTT) straight out of a playing MKV/WebM --
# over local files or debrid HTTP Range requests -- so the AI pipeline can translate a PERFECTLY SYNCED source:
# the embedded track's cue timestamps ARE the video's own timeline, so the Hebrew it produces needs no re-sync.
#
# Strategy:
#   local file  -> one sequential pass over the clusters (cheap, complete).
#   HTTP/debrid -> parse Cues, visit only the referenced clusters via surgical Range requests (tens of MB,
#                  never the whole file); if the file has no usable Cues we bail to None (the caller then falls
#                  through to the existing external-subtitle path).
#
# Every entry point is fully guarded and returns None / [] on any problem, so a caller ALWAYS has the existing
# external path to fall back to: this can only ADD a source, never break one. Only TEXT codecs are extracted
# (S_TEXT/*); bitmap tracks (PGS/VOBSUB) are reported by probe_tracks() but not extracted here.
# (Until Subtitle Phase 4 this was one 5,000-line file, stdlib only; it is now the modules of this folder,
# which may import the add-on's own helpers.)

import time
from resources.lib.fetching.embedded.extract.ebml import aborted, Buf, CLUSTER, CUES, noop, read_vint
from resources.lib.fetching.embedded.extract.tuning import CLUSTER_CAP_LOCAL, DEFAULT_DEADLINE_S, DEFAULT_HEAD_BYTES, DEFAULT_MAX_BYTES, HTTP_PIECE, HTTP_PIECE_CLUSTERS
from resources.lib.fetching.embedded.extract.matroska import is_bitmap_codec, is_text_codec, NotMatroska, parse_head, sub_tracks
from resources.lib.fetching.embedded.extract.pace import pace_memory_load, PACE_MEMORY_PROBE, pace_memory_save
from resources.lib.fetching.embedded.extract.http_source import Source
from resources.lib.fetching.embedded.extract.tracks import choose_track, lone_forced_track, LONE_FORCED_MIN_CUES, track_rows
from resources.lib.fetching.embedded.extract.srt_rows import entries_to_srt, timeline_origin
from resources.lib.fetching.embedded.extract.extract_http import extract_cues_http, extract_in_windows, steps_in_ticks
from resources.lib.fetching.embedded.extract.extract_mp4 import extract_srt_mp4
from resources.lib.fetching.embedded.extract.clusters import read_and_collect_cluster
from resources.lib.fetching.embedded.extract.cues import read_cues
from resources.lib.fetching.embedded.extract.resume import resume_clear, resume_identity, resume_load, resume_save


def extract_srt(url_or_path, track_num=None, lang=None,
                head_bytes=DEFAULT_HEAD_BYTES, max_bytes=DEFAULT_MAX_BYTES,
                deadline_s=DEFAULT_DEADLINE_S, allow_http=False,
                abort_cb=None, log=None, progress_cb=None, resume_path=None,
                stats=None, steps=None, other_text=None, on_track=None):
    """Extract an embedded TEXT subtitle track as an SRT string.

    Pick the track by `track_num`, else by `lang` (BCP-47 prefix, e.g. 'en'
    matches 'eng'), else the first non-forced text track. Returns the SRT text,
    or None when there is no matching text track / the file has no usable Cues
    over HTTP / anything fails. NEVER raises -- the caller always has the
    external path to fall back to. `abort_cb`, if given, is polled between
    clusters; when it returns True (e.g. playback ended) extraction stops.
    `allow_http` must be True to extract from a debrid/HTTP stream (default
    False -- local-only); HTTP extraction then uses ONE keep-alive connection
    with coalesced ranges + a 429 circuit-breaker so it can't starve playback.
    `resume_path`, if given, is a scratch file where an INTERRUPTED HTTP pass
    leaves what it collected, so the next attempt continues instead of starting
    over. It never changes what is delivered -- a partial extract still returns
    None -- only what survives a deferral.
    `stats`, if given, is a dict this fills in as it goes: 'done'/'total' cues,
    'backoffs' (how many times the CDN pushed back) and 'pace'. The caller needs
    those to decide things it otherwise has to GUESS at -- above all whether the
    provider is actually under pressure, which is the difference between handing
    the connection back to the player and cancelling useful work for nothing.
    Over HTTP it also carries 'release': a callable that closes the idle
    connection (_Source.release_connection), for an `abort_cb` that stands
    aside for a while before letting the reading go on; and 'why' = 'no_index'
    when the file's index has no entry for the subtitle lines, so they cannot
    be found in a stream short of reading the whole file, or 'no_text' when
    `lang`'s subtitle here is a picture and no other text track is read.
    `other_text`, if given, is language codes in order of preference: when
    `lang`'s subtitle in this file is a picture (PGS), the best other TEXT
    track is read instead, and `on_track`, if given, is called with that
    track's language as soon as it is chosen -- ONLY then: a track _pick_track
    returns under another tag (an untagged one defaults to 'eng') is still
    the picked language's (review of 0.3.82).
    `steps`, if given (HTTP only, and only with `resume_path`), is three
    callables (window, show, timeline). Before the complete pass the track is
    read in windows: timeline(starts_ms) is handed every cue's start once;
    window() returns (from_ms, until_ms) of film time, or None when what is
    left should be read in one go; after each window show(srt_text) is handed
    everything collected so far as SRT. What this function RETURNS
    does not change: the whole track, or None. A window that cannot be
    finished ends the extraction like any other interruption (what was read
    stays in `resume_path`), and nothing follows it."""
    _log = log or noop
    t0 = time.time()
    src = None
    try:
        src = Source(url_or_path)
        src._abort_cb = abort_cb   # polled DURING pace/backoff sleeps too
        src._log = _log            # so pacing/back-pressure is visible in a log
        if src.is_http:
            src._piece = HTTP_PIECE
        # Start where this provider left off last time (see _PACE_MEMORY_*),
        # rather than spending the burst allowance re-learning it every episode.
        _remembered = pace_memory_load(url_or_path)
        if _remembered:
            src._pace = _remembered
            _log('starting at %.2fs -- the pace this provider tolerated last '
                 'time, probed %d%% faster' % (
                     _remembered, int(round((1 - PACE_MEMORY_PROBE) * 100))))
        src._stats = stats if stats is not None else None
        if src._stats is not None:
            src._stats.setdefault('done', 0)
            src._stats.setdefault('total', 0)
            src._stats['backoffs'] = 0
            src._stats['pace'] = src._pace
            if src.is_http:
                src._stats['release'] = src.release_connection
        if not src.total:
            return None
        try:
            seg_start, ts_scale, tracks, seeks = parse_head(src, head_bytes,
                                                             _log)
        except NotMatroska as nm:
            if 'MP4' not in str(nm):
                raise
            # QA, 14 Nov 2026: 4K DV WEB-DLs are often MP4 (The Dark Knight,
            # The Matrix) -- their tx3g subtitles are read from the sample
            # tables, through the same _Source. What still cannot be read
            # keeps the 0.3.82 fallback (src._stats['why'] = 'not_mkv').
            if src.is_http and not (allow_http and src.has_session):
                _log('MP4: HTTP extraction not allowed here -- deferring')
                return None
            try:
                got = extract_srt_mp4(src, track_num, lang, other_text,
                                       on_track, deadline_s, t0, progress_cb,
                                       steps, _log)
            except Exception as ex:
                # a box this parser cannot make sense of: the external
                # subtitle, not "could not extract" (review)
                _log('MP4: could not parse the file (%r)' % (ex,))
                got = None
            if (got is None and src._stats is not None
                    and not src._stats.get('why')):
                src._stats['why'] = 'not_mkv'
            if src._stats is not None:
                # an MP4's other tracks are no reference (probe_tracks reads
                # Matroska only): said here, so nothing reads the head again
                src._stats['tracks'] = []
            return got
        subs = sub_tracks(tracks)
        if src._stats is not None:
            # the file's subtitle tracks, for a caller that would otherwise
            # read the head a second time to list them (the gender reference
            # from another track: translate._embedded_reference)
            try:
                src._stats['tracks'] = track_rows(tracks)
            except Exception:
                pass
        if not subs:
            return None
        # We translate THIS track's own text, so prefer an SDH track: it carries
        # the complete dialogue. (The cue_reference_times* callers deliberately do
        # NOT prefer SDH -- there the track is only a timing skeleton.)
        track = choose_track(src, subs, track_num, lang, other_text, on_track,
                              _log)
        lone_forced = False
        if track is None:
            track = lone_forced_track(subs, track_num, lang)
            lone_forced = track is not None
            if lone_forced:
                _log('the only subtitle track (#%s %s [%s]) is flagged forced -- '
                     'reading it; it is delivered only when it holds %d cues '
                     'or more' % (track['num'], track['codec'], track['lang'],
                                  LONE_FORCED_MIN_CUES))
        if track is None:
            # SAY WHAT WAS ACTUALLY THERE. "no matching text track" reads as a
            # defect in this parser, and on the report that prompted this it was
            # nothing of the kind: a BluRay REMUX whose three subtitle tracks
            # were all bitmap. Three different situations produce this same line
            # -- every track is a bitmap codec, the only match is flagged forced,
            # or the language genuinely is not present -- and the reader cannot
            # tell them apart, so the next question is always "is our extractor
            # broken?" when usually the answer is "that file has no text subs".
            # The inventory is already parsed at this point; printing it costs
            # one line and settles it from the log alone.
            _log('no matching text track (num=%s lang=%s); the file has: %s'
                 % (track_num, lang,
                    ', '.join('#%s %s%s%s' % (
                        t['num'], t['codec'] or '?',
                        # A '?' marks a language we DEFAULTED to rather than
                        # read: _parse_track_entry substitutes 'eng' when the
                        # TrackEntry carries no Language element. Without the
                        # mark this line disagrees with mkvinfo and reads as a
                        # parser bug, in a line whose only job is to be honest.
                        ' [%s%s]' % (t['lang'],
                                     '' if t.get('lang_explicit', True)
                                     else '?') if t['lang'] else '',
                        ' forced' if t['forced'] else '')
                        for t in subs)))
            if subs and all(is_bitmap_codec(t['codec']) for t in subs):
                _log('every subtitle track in this file is a bitmap format '
                     '(image subs, as BluRay remuxes carry) -- there is no text '
                     'to extract, so the external subtitle search is the right '
                     'path here, not a fallback from a failure')
            elif not any(is_text_codec(t['codec']) for t in subs):
                # Text, but spelled in a way this extractor does not read. Say
                # exactly that: it is worth a look, not a shrug.
                _log('none of these is a supported text track, but they are not all '
                     'picture formats either -- at least one is a text codec '
                     'this extractor does not read yet. Worth investigating '
                     'rather than treating as "this file has no text subs"')
            return None
        if not is_text_codec(track['codec']):
            _log('track #%s is %s (not text) -- skipping'
                 % (track['num'], track['codec']))
            return None
        want = track['num']
        codec = track['codec']
        scale_ms = ts_scale / 1e6
        origin_ms = timeline_origin(src, seg_start, scale_ms, _log)
        entries = []
        # DEBRID SAFETY: only extract from a live HTTP stream when explicitly
        # allowed, AND only when a reused keep-alive connection is available
        # (fresh-connection-per-read storms the CDN token and kills playback).
        # Otherwise defer to the external path (which still yields AI Hebrew).
        if src.is_http:
            if not allow_http:
                _log('HTTP extraction not allowed (setting off) -- deferring')
                return None
            if not src.has_session:
                _log('no keep-alive session (requests missing) -- declining '
                     'HTTP extraction to avoid a connection storm')
                return None
        # Surgical Cues-guided fetch first (per-track subtitle cues -- fast for
        # both local and debrid HTTP). If there are none: over HTTP defer to the
        # external path; a local file gets a complete sequential walk. A partial
        # extract is NEVER delivered -- we return None so the caller falls back.
        _steps = None
        if steps and src.is_http and resume_path and scale_ms > 0:
            _steps = steps_in_ticks(steps, entries, scale_ms, origin_ms,
                                     codec)
        if not _extract_cues(src, seeks, seg_start, want, entries,
                             max_bytes, deadline_s, t0, abort_cb, _log,
                             progress_cb, resume_path, codec, _steps):
            entries = []
            if src.is_http:
                return None
            if not _extract_sequential(src, seg_start, want, entries,
                                       deadline_s, t0, abort_cb, _log):
                return None
        if not entries:
            _log('no subtitle blocks collected for track #%s' % want)
            return None
        srt = entries_to_srt(entries, scale_ms, origin_ms, codec)
        if not srt:
            return None
        if lone_forced and srt.count('-->') < LONE_FORCED_MIN_CUES:
            _log('the forced-flagged track #%s holds %d cue(s) -- a real '
                 'forced track (foreign lines only), not the dialogue; not '
                 'delivered' % (want, srt.count('-->')))
            return None
        _log('extracted %d cue(s) from track #%s (%s), %.1fMB, %.1fs'
             % (srt.count('-->'), want, codec, src.fetched / 1e6,
                time.time() - t0))
        return srt
    except Exception as e:
        _log('extract_srt failed: %s' % e)
        if (isinstance(e, NotMatroska) and src is not None
                and getattr(src, '_stats', None) is not None):
            # the caller can offer the external subtitle instead
            src._stats['why'] = 'not_mkv'
        return None
    finally:
        # On EVERY exit, including the ones that gave up. A run the provider
        # throttled into deferring is precisely the run whose lesson the next
        # one needs; saving only on success would keep re-learning it.
        try:
            if src is not None and src.is_http:
                pace_memory_save(url_or_path, src._pace, src.http_reqs)
        except Exception:
            pass
        # Hand the connection back now, not whenever the garbage collector
        # gets to it: the player shares the provider with it, and after a stop
        # for a stalled picture it is the player that needs the line.
        try:
            if src is not None and src.is_http:
                src.close()
        except Exception:
            pass


def _extract_sequential(src, seg_start, want, entries, deadline_s, t0,
                        abort_cb, log):
    """Walk the segment element-by-element on a seekable source, reading each
    Cluster in full by its DECLARED size (no chunk-straddle loss) and skipping
    every non-cluster element. Used for local files. Returns True if it reached
    EOF (complete), False if a deadline/abort cut it short (caller returns None
    so a partial extract is never delivered)."""
    pos = seg_start
    total = src.total
    while pos < total:
        if (time.time() - t0) > deadline_s:
            log('sequential extract deadline reached -- incomplete')
            return False
        if aborted(abort_cb):
            log('sequential extract aborted (playback ended) -- incomplete')
            return False
        hdr = src.read(pos, 16)
        if len(hdr) < 2:
            break
        hb = Buf(hdr, 0)
        eid, _idl = read_vint(hb, True)
        if eid is None:
            break
        size, slen = read_vint(hb, False)
        if slen == 0:
            break
        hlen = hb.p
        if eid == CLUSTER:
            # Route to the cluster reader FIRST -- it handles unknown-size
            # clusters (size is None), which are legitimate EBML; bailing on
            # size-None here would silently truncate the file.
            clen = read_and_collect_cluster(
                src, pos, want, entries, CLUSTER_CAP_LOCAL, log)
            if clen <= 0:
                break
            pos += clen
        else:
            if size is None:
                break   # unknown-size non-cluster element: can't skip reliably
            pos += hlen + size
    return True


def _extract_cues(src, seeks, seg_start, want, entries,
                  max_bytes, deadline_s, t0, abort_cb, log, progress_cb=None,
                  resume_path=None, codec='', steps=None):
    """Cues-guided extraction from per-track SUBTITLE cues. Local visits each
    cluster directly; HTTP uses coalesced sweep-ranges over the single keep-alive
    connection with a 429 circuit-breaker. Returns True only on a COMPLETE pass;
    False (no sub cues / breaker / budget / abort) -> the caller defers to the
    external path (HTTP) or a full sequential walk (local)."""
    # The index in 2 MB pieces too, not the head's 256 KB: a file with many
    # tracks has a Cues of megabytes (QA, 14 Nov 2026: Interstellar with 91
    # tracks -- 13 s of paced 256 KB pieces before the first line). Every piece
    # still asks the buffer gate first.
    if getattr(src, '_piece', None):
        src._piece = HTTP_PIECE_CLUSTERS
    positions, is_sub = read_cues(src, seeks, seg_start, want, log)
    if not positions or not is_sub:
        if positions:
            # Whole-file/video cues don't point at subtitle blocks, so a capped
            # per-cluster fetch would risk missing lines. Defer.
            log('no per-subtitle Cues -- deferring (avoid a partial extract)')
        # Over a stream that is final -- the lines are somewhere in the whole
        # file -- and the caller can offer something else. Only for a file
        # that HAS no such index: the index read whole without subtitle
        # entries, or no index at all in the SeekHead. An index that could
        # not be read (a dropped connection, a stop, a cut answer) is not
        # that (review of 0.3.81, round 5).
        if (src.is_http and src._stats is not None and not src.tripped
                and not getattr(src, '_cues_unsure', False)
                and (positions or not seeks.get(CUES))):
            src._stats['why'] = 'no_index'
        return False
    if not src.is_http:
        # positions can now list a cluster more than once (one entry per relpos);
        # a local full-cluster parse recovers every block, so visit each cpos ONCE.
        seen_local = set()
        for cpos, _rel, _ctime in positions:
            if cpos in seen_local:
                continue
            seen_local.add(cpos)
            if (time.time() - t0) > deadline_s or aborted(abort_cb):
                return False
            read_and_collect_cluster(
                src, cpos, want, entries, CLUSTER_CAP_LOCAL, log)
        return True
    # HTTP only: a local pass is one cheap sequential walk, so there is nothing
    # worth carrying between attempts.
    identity = resume_identity(src, want, codec, positions)
    prior, done, scan = resume_load(resume_path, identity, log)
    entries.extend(prior)
    if steps is not None and resume_path:
        if not extract_in_windows(
                src, positions, entries, want, deadline_s, t0, abort_cb, log,
                progress_cb, done, scan, steps,
                lambda: resume_save(resume_path, identity, entries, done,
                                     scan, log)):
            resume_save(resume_path, identity, entries, done, scan, log)
            return False
    ok = extract_cues_http(src, positions, entries, want, deadline_s, t0,
                            abort_cb, log, progress_cb, done, scan)
    if ok:
        resume_clear(resume_path)
    else:
        resume_save(resume_path, identity, entries, done, scan, log)
    return ok
