# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Embedded subtitle extraction: reading the file's own tracks, the staircase and the aligned source.

import os
import time
from resources.lib.core import kodi, settings
import re as _re_forced
from resources.lib.scoring_matching.ranking import _lang_display_he
from resources.lib.ai_translation.policy import _embedded_translation_policy
from resources.lib.ai_translation import progress


# The last embedded pick that produced nothing at all -- no track that could
# be read, no external subtitle to translate instead. The automatic subtitle
# says so on screen instead of only "no Hebrew subtitle" (a viewer's log, 17
# Nov 2026: such a pick gave up in 2 s and the viewer could not tell why).
_EMBEDDED_MISS = {}


def _note_embedded_miss(reason):
    if reason == 'busy':
        return      # not a miss: the same track is being read already, and
                    # its own toast says so (0.3.83)
    try:
        import xbmc as _xbmc
        playing = _xbmc.Player().getPlayingFile() or ''
    except Exception:
        playing = ''
    _EMBEDDED_MISS.clear()
    _EMBEDDED_MISS.update({'reason': reason or '', 'at': time.time(),
                           'file': playing})


def embedded_miss_since(t0, playing_file=''):
    """The reason ('no_index', 'no_text', 'not_mkv' or '' when unknown) of an
    embedded pick that produced nothing at or after t0, for the file playing
    (`playing_file`, when both sides know it: runs for two files can
    overlap); None when there was none."""
    miss = dict(_EMBEDDED_MISS)
    if not miss or miss.get('at', 0) < t0:
        return None
    if playing_file and miss.get('file') and miss['file'] != playing_file:
        return None
    return miss.get('reason') or ''
_FORCED_NAME_RE = _re_forced.compile(
    r'(?<![^\W\d_])(?:forced|forcés?|forzad[oa]s?|форсов[^\W\d_]*)(?![^\W\d_])',
    _re_forced.I)


def _named_forced(name, title_text):
    """True when `name` marks a forced subtitle and the title does not say
    the same word."""
    m = _FORCED_NAME_RE.search(name or '')
    return bool(m) and m.group(0).lower() not in (title_text or '').lower()


def _external_for_unreadable_embedded(info, lang):
    """The 'AI translation' payload (type engine_ai) for the external subtitle
    in `lang` -- else English -- that best matches the playing release, or
    None. For an embedded track that cannot be read from the stream. A file
    named as forced is never it."""
    try:
        from resources.lib.sync import oracles, service
        from resources.lib.scoring_matching import release as scoring_matching_release
        cands = oracles._oracle_candidates(info) or []
    except Exception:
        return None
    try:
        playing = service.playing_release(info) or ''
    except Exception:
        playing = ''
    picked = (lang or 'en').lower()[:2]
    title_text = ' '.join(str(info.get(k) or '') for k in (
        'title', 'tvshow', 'picked_release', 'tagline')) + ' ' + playing
    mine = [c for c in cands
            if (c.get('language') or '').lower()[:2] in (picked, 'en')
            and c.get('payload')
            and not _named_forced(c.get('release') or '', title_text)]
    if not mine:
        return None

    def rank(c):
        # the picked language first (owner: "I picked English"), English only
        # when it has none; within a language, the best match to the release
        try:
            pct = scoring_matching_release.match_pct(playing, c.get('release') or '') if playing else 0
        except Exception:
            pct = 0
        return ((c.get('language') or '').lower()[:2] == picked, pct)
    best = max(mine, key=rank)
    out = dict(best['payload'])
    out['type'] = 'engine_ai'
    out['src_lang'] = (best.get('language') or picked).lower()[:2]
    return out


def _playing_video_url(info):
    """URL/path of the file being played -- a direct http(s) stream (debrid) or
    a local file; '' for HLS/plugin:// or when unavailable. Mirrors the probe-
    URL logic subsync uses, so embedded extraction reads the same bytes the
    player does."""
    url = ''
    try:
        import xbmc as _xbmc
        url = _xbmc.Player().getPlayingFile() or ''
    except Exception:
        url = ''
    if not url:
        url = (info.get('filepath') or '').strip()
    low = (url or '').lower().split('|')[0]
    if not low:
        return ''
    if low.startswith(('http://', 'https://')):
        if '.m3u8' in low or 'manifest' in low:
            return ''
        return url.split('|')[0]
    try:
        if os.path.isfile(url):
            return url
    except Exception:
        pass
    return ''


# External subtitles the single-shift check refused that are then tried
# against SubSync's file proof, per language (each costs a planner run).
_STAIRCASE_TRIES = 3


def _embedded_staircase_source(info, playing, ref, rejected, abort):
    """An external subtitle made for ANOTHER CUT of the film, re-timed onto
    the embedded track by a map with one offset per act.

    The align path accepts one shift (and one speed) for the whole file, so
    a subtitle whose gap grows after every ad break is "not confident"
    however good its text -- and the embedded text then has to be extracted,
    hundreds of requests at the provider's pace. SubSync proves such maps
    since 0.3.76 (a file named as a streaming rip whose text tracks come in
    two or more languages: the map is learned from one track and the others
    must accept it). Offline, on the timelines of three such files: of 12
    subtitle timings a single shift could not align, the proven map aligned
    11, and each then passed this path's own check as already synced.

    `rejected`: [(release, text)] in the order tried. `ref`: the embedded
    track's cue times the single-shift check used. Returns (text, release)
    or (None, None). Reads nothing from the stream: it uses the file profile
    SubSync cached at play start, and does nothing without one. Takes only a
    verdict SubSync's own gates passed, applied by apply_verdict (which
    re-checks the proof), and only when the result is then CONFIRMED against
    `ref` -- the bar the "already synced" branch above sets."""
    if not rejected:
        return None, None
    try:
        from resources.lib.sync.probes import bundle as probes_bundle
        from resources.lib.sync.align import common, retime, verify
        bundle = probes_bundle._cached_reference_bundle(info)
        if not (bundle and bundle.get('track_cues')):
            return None, None
        for release, text in rejected[:_STAIRCASE_TRIES]:
            if abort():
                return None, None
            verdict = probes_bundle._verify_file_bundle(bundle, text, playing)[0]
            if not verdict or verdict.get('status') != common.STATUS_FIXABLE:
                continue
            try:
                aligned = retime.apply_verdict(text, verdict)
            except Exception:
                continue
            if not aligned or aligned.count('-->') < 8:
                continue
            after = verify.verify_cues(ref, aligned)
            if after.get('status') != common.STATUS_CONFIRMED:
                kodi.log('embedded-align: %r mapped onto the file but not '
                               'confirmed against the embedded track (%s)'
                               % (release, after.get('diag')), level='INFO')
                continue
            kodi.log('embedded-align: %r was made for another cut -- '
                           're-timed by a proven map of %d part(s) (%s)'
                           % (release, len(verdict.get('segments') or []),
                              verdict.get('validation_proof')
                              or 'second timing family'), level='INFO')
            return aligned, release
    except Exception as e:
        kodi.log('embedded-align: staircase check skipped: {0}'.format(e),
                       level='WARNING')
    return None, None


def _embedded_aligned_source_srt(
        info, src_lang, progress_cb=None, allow_http=True,
        strict_lang=False, include_he=False):
    """FAST, debrid-safe source SRT for the embedded-AI path.

    Instead of pulling the embedded track's ~1700 text blocks over the shared
    debrid token (a request storm that a strict provider like TorBox 429s, which
    starves and closes the movie), this reads ONLY the embedded subtitle track's
    DENSE cue-time skeleton from the Matroska Cues index (a handful of range
    requests) and RE-TIMES an external `src_lang` subtitle onto it. The embedded
    timestamps are the video's own ground-truth timeline, so the re-timed source
    -- and therefore the AI Hebrew translated from it, which inherits timing 1:1
    -- is perfectly synced. Returns (path_to_retimed_SRT, used_lang), or
    (None, None) to defer to the full-text extract. `used_lang` is the language
    actually aligned -- usually `src_lang`, but the CROSS-LANGUAGE fallback may
    pick another: if the picked language has no external sub, we align an
    external sub in another language onto THAT language's embedded track (all
    tracks share one timeline) so the caller translates from the right source.
    Fail-open: never raises, and any miss returns (None, None) so the caller
    falls through to `_extract_embedded_srt`."""
    def _p(done, total, label=None):
        if not progress_cb:
            return
        try:
            progress_cb(done, total, label)
        except TypeError:
            try:
                progress_cb(done, total)
            except Exception:
                pass
        except Exception:
            pass

    try:
        import time as _time
        _t0 = _time.time()
        url = _playing_video_url(info)
        if not url:
            return None, None
        try:
            from resources.lib.sync.align import common, retime, verify
            from resources.lib.sync import oracles, service
            from resources.lib.scoring_matching import release as scoring_matching_release
            from resources.lib.fetching.embedded import embedded_extract
        except Exception as e:
            kodi.log('embedded-align: import failed: {0}'.format(e),
                           level='WARNING')
            return None, None

        # Total wall-clock ceiling on the WHOLE align attempt. The happy path is
        # a fraction of a second; this bounds the adversarial "flaky token" shape
        # -- reads that each recover after a few 429 retries but never exhaust
        # the per-read breaker or the streak-of-6 (the align path makes too few
        # reads to accumulate a 6-streak) -- which could otherwise crawl for
        # minutes while holding the token warm. On timeout we abort and defer to
        # the fallback, keeping this path genuinely FAST as designed.
        _ALIGN_DEADLINE_S = 45.0
        # Headroom reserved at the END of the deadline for the fallback
        # language(s): a picked language's exhaustive search YIELDS once it has
        # consumed (deadline - reserve), so a picked language with many
        # non-matching subs can't eat the whole clock and STARVE the reliable
        # English fallback (which usually aligns on its first candidate). The last
        # try-language has nothing after it, so it may use the full deadline.
        _ALIGN_FALLBACK_RESERVE_S = 15.0
        # Search EXHAUSTIVELY: try every external subtitle candidate for a
        # language until one clears the sync gate. Release-NAME similarity is NOT
        # timing similarity, so a lower-ranked release can align to the embedded
        # skeleton better than the top name-match -- there is deliberately NO
        # download-count cap. The search is bounded instead by the 45s wall-clock
        # deadline above (_abort, checked every candidate), the per-language
        # yield-with-reserve just described, the pause/playback-end abort, and the
        # <=3-language read cap. The picked language is tried FIRST.

        # Pause-aware abort (mirrors _extract_embedded_srt's resume guard): the
        # cue-index reads share the debrid token with the player, so if the user
        # PAUSED to let this run and then RESUMES, hand the token back INSTANTLY
        # -- the exact crash mechanism the full-text path guards against. This
        # path is only a handful of requests, but the resume-into-a-hot-token
        # window is real, so guard it the same way. (A never-paused run is
        # unaffected: saw_pause stays False and only playback ending aborts.)
        # Player.Paused reads True during a SEEK and during a buffering hiccup,
        # not only during a pause -- so arming this needs the pause to be HELD,
        # exactly as the full-text path does. Latching on a single observation
        # is what made five consecutive field attempts die on the other path
        # while the user was simply watching; this copy had the same defect and
        # would have killed the CHEAP path the same way, which matters most on
        # precisely the providers where the cheap path is the only one that
        # works.
        _ALIGN_PAUSE_ARM_S = 3.0
        _al = {'saw_pause': False, 'paused_at': None}
        # ...and the SAME evidence test the full-text path uses. Cancelling on
        # resume is only justified when our own reads have left the provider
        # refusing; with a quiet CDN there is nothing to hand back, and the cost
        # of getting it wrong is worst HERE -- this is the cheap path (a handful
        # of requests), so discarding it drops the user onto the expensive one,
        # on exactly the providers where that struggles.
        # NOTE the shape of this window: the extractor is called ONCE, at the
        # start, and everything after it is provider downloads and matching that
        # never touch the debrid link again. So `backoffs` is a snapshot from
        # t=0, not a live pressure reading -- one transient blip during that
        # read would otherwise arm the resume-abort for the whole 45s window,
        # long after we stopped using the connection at all. Track whether we
        # are STILL holding it, and only yield it while that is true.
        _alstats = {'backoffs': 0, 'pace': 0.0}
        _holding = {'link': True}
        _alhot = {'at': 0.0}

        def _abort():
            try:
                # Same throttle memory as the full-text path: the pace decays
                # after a clean run, so reading it live lets the guard disarm
                # itself while the token is still hot. Note it before any early
                # return can skip it.
                if (_alstats.get('pace') or 0.0) >= 1.0:
                    _alhot['at'] = _time.time()
                if _time.time() - _t0 > _ALIGN_DEADLINE_S:
                    return True
                import xbmc as _x
                p = _x.Player()
                if not p.isPlayingVideo():
                    return True
                if _x.getCondVisibility('Player.Paused'):
                    _now = _time.time()
                    if _al['paused_at'] is None:
                        _al['paused_at'] = _now
                    elif (_now - _al['paused_at']) >= _ALIGN_PAUSE_ARM_S:
                        _al['saw_pause'] = True
                    return False
                _al['paused_at'] = None
                # Same bar as the full-text path (_RESUME_ABORT_PACE_S there):
                # a single refusal is CDN noise, not a saturated token, and the
                # pause being resumed is usually the one the subtitle list
                # itself put the player into. Only a pace that has actually been
                # throttled down counts -- and the cost of getting this wrong is
                # worst here, on the cheap path that some providers depend on.
                if (_al['saw_pause'] and _holding['link'] and _alhot['at']
                        and (_time.time() - _alhot['at']) <= 60.0):
                    kodi.log(
                        'embedded-align: playback resumed after a pause and the '
                        'CDN has throttled us to {1:.2f}s/request ({0} '
                        'push-back(s)) -- yielding the connection to the player'
                        .format(_alstats.get('backoffs'),
                                _alstats.get('pace') or 0.0), level='INFO')
                    return True
                return False
            except Exception:
                return False

        # Language try-order for the CROSS-LANGUAGE fallback. The picked language
        # first (respect the track the user chose), then English (most reliable
        # for AI translation and the most widely-available external sub), then
        # any other language that has an external candidate. We only consider
        # languages that actually have an external sub, so we never read the Cues
        # index for a language we can't source. If the picked language has no
        # external sub, we align an external sub in another language onto THAT
        # language's embedded track (all tracks share one timeline) -> still
        # synced Hebrew, instead of falling all the way to the heavy full-text
        # extract.
        _p(10, 100, 'מחפש כתובית מקור תואמת...')
        try:
            all_cands = oracles._oracle_candidates(info, include_he=include_he)
        except Exception:
            all_cands = []
        try:
            playing = service.playing_release(info) or ''
        except Exception:
            playing = ''
        ext_langs = []
        for c in all_cands:
            lg = (c.get('language') or '').lower()[:2]
            if lg and lg not in ext_langs:
                ext_langs.append(lg)
        pref = (src_lang or 'en').lower()[:2]
        try_langs = [x for x in dict.fromkeys([pref, 'en'] + ext_langs)
                     if x in ext_langs]
        # The cross-language fallback is right for the AI path -- an English
        # source still becomes Hebrew at the end of it. It is WRONG for a caller
        # that DELIVERS what comes back: falling back to English there would
        # hand an English subtitle to someone who asked for Hebrew. Such a
        # caller passes strict_lang and gets the picked language or nothing.
        if strict_lang:
            try_langs = [x for x in try_langs if x == pref]
        if not try_langs:
            kodi.log('embedded-align: no external subtitle in any language '
                           '-- deferring to full extract', level='INFO')
            return None, None

        # Languages EXEMPT from the mid-search time-yield (they may run to the
        # full deadline): English -- the reliable, most-available fallback, so it
        # ALWAYS gets its shot even if the picked language's search ran long (NOT
        # merely whichever language sits last, which would let an arbitrary 3rd
        # language steal English's reserved time) -- and whichever language is
        # processed LAST (nothing after it to reserve time for). Bounded to the
        # <=3 languages actually processed (the reads cap), so a 4th+ never-read
        # language can't become the sentinel. `_yield_after_s` floors at 1s so a
        # future reserve>=deadline mis-edit can't collapse it to <=0.
        _yield_exempt = {'en', try_langs[:3][-1]}
        _yield_after_s = max(1.0, _ALIGN_DEADLINE_S - _ALIGN_FALLBACK_RESERVE_S)

        # Automatic/align-only intentionally permit this small, debrid-safe Cues
        # read. local_only passes False; direct mode skips this function.
        allow = bool(allow_http)
        # Read the embedded head + Cues index ONCE for the <=3 try-languages and
        # slice each track's dense cue-time skeleton from it, instead of
        # re-reading the head+Cues per language on the cross-language fallback
        # (all tracks share ONE Cues index). The <=3 bound that used to cap the
        # READS is now a track/language cap; the happy path -- the picked language
        # aligns on its first candidate -- costs the SAME single read it always
        # did, and a fallback across languages no longer pays ~1 read per language.
        try_set = try_langs[:3]
        # Abort BEFORE the read (the old per-language loop checked _abort() ahead
        # of every read): if the deadline already passed, or playback ended, or
        # the user resumed during the candidate scan above, do zero network reads.
        if _abort():
            return None, None
        _p(30, 100, 'קורא תזמון מובנה...')
        try:
            times_by_lang = embedded_extract.cue_reference_times_multi(
                url, try_set, allow_http=allow, abort_cb=_abort, stats=_alstats,
                log=lambda m: kodi.log('embedded-align: ' + m,
                                             level='INFO'))
        finally:
            # Done with the debrid link. From here on there is nothing to hand
            # back, so a pause and resume must not discard the work.
            _holding['link'] = False
        for lang in try_set:
            if _abort():
                break
            # DENSE embedded cue-time skeleton for THIS language's track, sliced
            # from the single read above. [] when that track isn't per-cue indexed.
            starts = times_by_lang.get(lang) or []
            if len(starts) < 8:
                continue
            n = len(starts)
            # Adapt flat start times -> {start,end}; synthesize a plausible end
            # from the gap to the next cue (mirrors mkv_probe).
            ref = []
            for i, t in enumerate(starts):
                nxt = starts[i + 1] if i + 1 < n else t + 3000
                ref.append({'start': t,
                            'end': t + max(600, min(3000, nxt - t - 100))})

            cands = [c for c in all_cands
                     if (c.get('language') or '').lower().startswith(lang)]
            if playing:
                try:
                    cands.sort(key=lambda c: scoring_matching_release.match_pct(
                        playing, c['release']), reverse=True)
                except Exception:
                    pass

            _p(60, 100, 'מסנכרן לפי הכתובית המובנית...' if lang == pref
               else 'מסנכרן ({0}) לפי הכתובית המובנית...'.format(lang))
            # Try EVERY release match for this language until one clears the gate
            # -- a lower name-match can still be the best TIMING match. No
            # download cap; _abort() (checked each iteration) enforces the 45s
            # deadline + pause/playback-end so the exhaustive search can't hang
            # the player.
            _refused = []
            _yielded = False
            for c in cands:
                if _abort():
                    return None, None
                # Yield to the fallback language(s) rather than eat the whole
                # deadline on one language's many non-matching subs, so English
                # still gets its turn. The LAST try-language has nothing after it,
                # so it may run to the full deadline.
                if (lang not in _yield_exempt
                        and _time.time() - _t0 > _yield_after_s):
                    kodi.log('embedded-align: [%s] search time budget '
                                   'reached -- yielding to the English/fallback '
                                   'language' % lang, level='INFO')
                    _yielded = True
                    break
                try:
                    src_text = oracles._download_oracle(c.get('payload'))
                except Exception:
                    src_text = ''
                if not src_text or src_text.count('-->') < 8:
                    continue
                try:
                    verdict = verify.verify_cues(ref, src_text)
                except Exception as e:
                    kodi.log('embedded-align: verify failed: {0}'.format(e),
                                   level='WARNING')
                    continue
                st = verdict.get('status')
                aligned = None
                if st == common.STATUS_CONFIRMED:
                    aligned = src_text
                    kodi.log('embedded-align: [%s] %r already synced (%s)'
                                   % (lang, c.get('release'),
                                      verdict.get('diag')), level='INFO')
                elif st == common.STATUS_FIXABLE:
                    try:
                        aligned = retime.apply_verdict(src_text, verdict)
                    except Exception:
                        aligned = None
                    if aligned and aligned.count('-->') >= 8:
                        kodi.log('embedded-align: [%s] %r retimed (%s)'
                                       % (lang, c.get('release'),
                                          verdict.get('diag')), level='INFO')
                    else:
                        aligned = None
                else:
                    kodi.log('embedded-align: [%s] %r not confident (%s)'
                                   % (lang, c.get('release'),
                                      verdict.get('diag')), level='INFO')
                    _refused.append((c.get('release') or '', src_text))
                if aligned and aligned.count('-->') >= 8:
                    out = os.path.join(
                        kodi.cache_dir(),
                        'embedded_aligned_{0}.srt'.format(lang or 'src'))
                    with open(out, 'w', encoding='utf-8') as f:
                        f.write(aligned)
                    _p(100, 100, 'מוכן')
                    kodi.log('embedded-align: synced %s source ready (%d '
                                   'embedded cue ref, ~%d requests avoided)'
                                   % (lang, n, n), level='INFO')
                    return out, lang
            # No subtitle of this language fits by one shift: one made for
            # another cut may still fit by a map. Not when this language has
            # already used up its share of the search time (the next language
            # needs what is left).
            aligned, _rel = (None, None) if _yielded else (
                _embedded_staircase_source(info, playing, ref, _refused, _abort))
            if aligned:
                out = os.path.join(
                    kodi.cache_dir(),
                    'embedded_aligned_{0}.srt'.format(lang or 'src'))
                with open(out, 'w', encoding='utf-8') as f:
                    f.write(aligned)
                _p(100, 100, 'מוכן')
                kodi.log('embedded-align: synced %s source ready (%d '
                               'embedded cue ref, ~%d requests avoided)'
                               % (lang, n, n), level='INFO')
                return out, lang
        kodi.log('embedded-align: no source aligned in any language -- '
                       'deferring to full extract', level='INFO')
        return None, None
    except Exception as e:
        kodi.log('embedded-align failed: {0}'.format(e), level='WARNING')
        return None, None


# The picked language's embedded subtitle is a picture (PGS): the file's other
# TEXT tracks are read instead, the best language for the AI first -- the order
# the AI rows list languages in (the owner, 14 Nov 2026: "switch to an embedded
# text subtitle, in the preferred language for AI translation"; no OCR).
_EMBEDDED_TEXT_ORDER = ('en', 'es', 'de', 'fr', 'pt')


def _extract_embedded_srt(info, src_lang, track_num=None, deadline_s=900.0,
                          progress_cb=None, allow_http=True, steps=None,
                          cancelled=None, why=None, reference=None):
    """Extract the playing file's embedded `src_lang` subtitle track to a temp
    SRT and return its path, or None. `deadline_s` bounds the extraction (default
    900s). Every current caller runs in the BACKGROUND -- the auto-on-play thread
    and resolve() from the bg_translate_picker RunScript (the native picker and
    chooser now FIRE that RunScript and return immediately, rather than extracting
    inline) -- so the long default is safe; playback-end (abort_cb) is the real
    stop. `progress_cb(done, total)`, if given, drives a corner progress bar. The extracted cues carry the video's OWN
    timestamps, so the Hebrew translated from this file needs no re-sync. Fully
    guarded + fail-open: any problem returns None and resolve() lets the caller
    fall through to the external-subtitle path. Aborts if playback ends mid-
    extraction (bandwidth hygiene).
    `steps` (an _EmbeddedSteps, a stream only): the track is read in windows
    that start where the viewer is, and it is fed what has been read after
    each. What is returned does not change.
    `cancelled()`, if given, is True once the viewer has picked something
    else; a wait for the player's buffer then ends the reading. `why`, a dict
    if given, gets 'reason' = 'no_index' when the stream's index has no entry
    for the subtitle lines (they cannot be read short of the whole file) or
    'no_text' when `src_lang`'s subtitle in this file is a picture (PGS) and
    the file has no other text subtitle; and 'lang_used' as soon as the track
    is chosen -- another text track's, in _EMBEDDED_TEXT_ORDER, when
    `src_lang`'s is a picture.
    `reference(stats)`, if given: the track is read as a GENDER REFERENCE for
    a translation (_embedded_reference), not as a subtitle the viewer picked
    -- nothing is said on screen, no "stopped at N%" flag is left for a
    picker, and the reading ends as soon as reference(stats) is true (a
    provider too slow for a reference to be worth its requests)."""
    try:
        # (a local name on purpose: every toast below, in the nested
        # functions too, goes through it)
        _status = ((lambda *a, **k: None) if reference is not None
                   else progress._status)
        url = _playing_video_url(info)
        if not url:
            kodi.log('embedded: no probeable playing url', level='INFO')
            return None
        try:
            from resources.lib.fetching.embedded import embedded_extract
        except Exception as e:
            kodi.log('embedded_extract import failed: {0}'.format(e),
                           level='WARNING')
            return None

        # Is this a REMOTE source? Everything below that throttles, locks or
        # defers exists to protect a shared debrid token. A local file has no
        # token, no CDN and no rate limit -- reading it twice costs nothing but
        # disk -- so those guards are pure loss there, and one of them (the
        # single-extraction lock) actively refuses work a local user asked for.
        _remote = '://' in (url or '')

        # ONE extraction at a time, REMOTE ONLY. Two concurrent runs (e.g. the
        # user picking "embedded" twice) DOUBLE the range-request load on the
        # shared debrid TOKEN and can push the CDN over its per-token rate limit
        # -- which then 429s the PLAYER's own video stream and CLOSES the movie
        # (field crash on TorBox). A cross-process flag (Window prop -- RunScript
        # runs in its own process) refuses a second run.
        try:
            import xbmcgui as _xg
            _win = _xg.Window(10000) if _remote else None
        except Exception:
            _win = None
        _ACTIVE = 'povil.embedded_extract_active'
        if _win is not None:
            import time as _tm
            # MONOTONIC clock: immune to wall-clock jumps (NTP/RTC steps on cheap
            # Android boxes) that could otherwise make a LIVE flag look stale and
            # let a 2nd concurrent extraction start -- the exact 2x-load that
            # closed the movie. It's per-boot and shared across processes on this
            # machine; the flag lives on a Window prop that a Kodi restart clears,
            # so there's no cross-boot concern.
            _now = _tm.monotonic()
            _raw = _win.getProperty(_ACTIVE)
            if _raw:
                # The flag stores the monotonic START time. A live extraction is
                # bounded by deadline_s; anything older (or a legacy '1' / garbage)
                # is a stale flag from a killed RunScript process -- reclaim it so
                # the feature can't wedge OFF for the rest of the session.
                try:
                    _age = _now - float(_raw)
                except (ValueError, TypeError):
                    _age = deadline_s + 999.0
                if _age < 0:
                    _age = 0.0   # defensive: never treat a fresh flag as stale
                if _age < (deadline_s + 120):
                    kodi.log('embedded: another extraction already running '
                                   '-- skipping to avoid doubling the debrid load',
                                   level='INFO')
                    # Not a failure (QA, 14 Nov 2026: the pick on play had
                    # started this very job, and picking its row -- marked
                    # current -- ended in "could not extract, try another
                    # subtitle"). Say it is under way; the picker's failure
                    # toast reads this flag and stays quiet.
                    if why is not None:
                        why['reason'] = 'busy'
                    _status('AI: תרגום מובנה כבר רץ ברקע — העברית תופיע '
                            'כשיסתיים', time_ms=5000)
                    try:
                        if reference is None:
                            _win.setProperty('povil.embedded_partial',
                                             'busy@%.0f' % time.time())
                    except Exception:
                        pass
                    return None
            _win.setProperty(_ACTIVE, str(_now))

        # Player-stall guard: our range requests share the debrid TOKEN with the
        # player. If playback is PLAYING (not paused) but its clock stops
        # advancing, the player is buffering/starved -- most likely our load
        # contending for the token -- so ABORT at once to hand the bandwidth and
        # request budget back and KEEP THE MOVIE ALIVE. A pause is not a stall.
        _STALL_ABORT_S = 8
        # How long Player.Paused must hold before it counts as a real PAUSE.
        # Kodi reports Paused during a seek and during a buffering hiccup too --
        # the resume-abort below says so itself -- and `saw_pause` LATCHED on a
        # single observation, so one sub-second blip anywhere in the run armed an
        # abort that fired on the very next poll. That is what a field log
        # (0.2.445) shows: five attempts, lifetimes 31.5s / 49.0s / 0.0s /
        # 156.1s / 16.3s, every one of them ending in "playback resumed after a
        # pause", none completing -- while the user was simply watching the
        # movie. A deliberate pause lasts; a blip does not.
        _PAUSE_ARM_S = 3.0
        # How throttled our crawl must already be before resuming playback is
        # worth cancelling it over. The pace starts at 0.20s and multiplies by
        # 1.5 per refusal, so 1.0s is about four of them -- the point where the
        # provider has demonstrably clamped down rather than blinked once.
        _RESUME_ABORT_PACE_S = 1.0
        # ...and for how long after that it still counts. The pace does not only
        # climb: it decays back down after a run of clean requests, so reading it
        # live would let the guard disarm itself. Measured on the real AIMD code:
        # once throttled to 1.01s, twenty-five clean fetches -- half a minute of
        # crawling, which carries on happily while the film is paused -- bring it
        # to 0.91s, and the guard silently switches off although nothing has
        # changed about the token. What that decay actually proves is that our
        # own slow trickle is being tolerated, which is not the question: the
        # question is whether the PLAYER's burst on resume will be. So remember
        # when we were last throttled and treat the token as hot for a while
        # afterwards. Long enough to cover a decay, short enough that a provider
        # which pushed back once early and has been quiet for minutes is
        # forgiven.
        _TOKEN_HOT_S = 60.0
        _hot = {'at': 0.0}
        # How long to stop touching the connection when playback resumes, so
        # the player has the debrid token to itself while it refills. Enough for
        # Kodi's cache to get ahead of the picture, and longer when the provider
        # has been throttling us, because that token needs more room. The cost
        # is these seconds once per resume; the alternative, measured, is either
        # the film closing or the whole extraction being thrown away.
        _YIELD_RESUME_S = 15.0
        _YIELD_HOT_S = 30.0
        _stall = {'t': None, 'since': None, 'saw_pause': False,
                  'paused_at': None}
        # What the extractor is actually experiencing, filled in as it runs:
        # cues done/total, how many times the CDN pushed back, current pace.
        # The resume guard below used to GUESS at this; now it can read it.
        _xstats = {'done': 0, 'total': 0, 'backoffs': 0, 'pace': 0.0}

        def _should_abort():
            try:
                import xbmc as _x
                import time as _tt
                # Note the throttle BEFORE anything can return early: this is
                # polled on every request and every second of every backoff, so
                # it is what keeps the reading from being a snapshot -- and it
                # has to keep running while the film is paused, because that is
                # exactly when the crawl continues and the pace decays.
                if (_xstats.get('pace') or 0.0) >= _RESUME_ABORT_PACE_S:
                    _hot['at'] = _tt.time()
                if reference is not None and reference(_xstats):
                    return True
                p = _x.Player()
                if not p.isPlayingVideo():
                    return True
                if _x.getCondVisibility('Player.Paused'):
                    _stall['t'] = None
                    _buf['trend'] = []
                    _pnow = _tt.time()
                    if _stall['paused_at'] is None:
                        _stall['paused_at'] = _pnow
                    elif (_pnow - _stall['paused_at']) >= _PAUSE_ARM_S:
                        _stall['saw_pause'] = True
                    return False
                # Not paused: any blip that was in progress ends here without
                # arming anything.
                _stall['paused_at'] = None
                # Playing (not paused). If the user PAUSED to let extraction run
                # and has now RESUMED, hand the debrid token back INSTANTLY: on a
                # strict provider our crawl leaves the token rate-limited, and the
                # player's first read on resume 429s and the movie closes (field
                # log 78e1c97c) faster than the 8s stall guard below can react.
                # Aborting on resume keeps the movie alive; extraction defers to
                # the external path. (A never-paused, Real-Debrid-style extract
                # during playback never sets saw_pause, so it is unaffected.)
                # NOTE: Player.Paused ALSO reads True during a seek and during a
                # buffering hiccup, which is why arming it needs _PAUSE_ARM_S of
                # continuously-held pause above: those are momentary, a real
                # pause is not. A seek is still a moment of extra contention for
                # the same token, so a long one is deliberately treated as a
                # pause; the cost of over-aborting is only a clean defer.
                #
                # ...but only when there is something to hand back. The whole
                # premise is that OUR crawl has left the token rate-limited, so
                # the player's first read on resume 429s and the movie closes.
                # That premise is now MEASURED rather than assumed: if the CDN
                # has not pushed back even once, the token demonstrably has
                # headroom and cancelling costs the user real work for nothing.
                #
                # This is not a hypothetical. In a field log (0.2.446) the user
                # paused precisely to let the extraction run, and it was killed
                # twice at "57 req, pace 0.20s, 0 backoff(s)" and "86 req, pace
                # 0.20s, 0 backoff(s)" -- the provider had not complained once.
                # The stall guard below still covers the other failure mode
                # (bandwidth contention, which shows up as a frozen clock).
                #
                # ...and "pushed back at all" is far too low a bar for that.
                # The premise is a SATURATED token, and one refusal in fifty-odd
                # requests is not saturation -- it is the ordinary noise of a
                # busy CDN. Worse, the pause being resumed is usually one WE
                # caused: opening the subtitle list pauses playback, so every
                # manually-picked extraction starts paused and the user pressing
                # play to carry on watching is the NORMAL course of events, not
                # a signal about the provider. Field log 37c47bda: killed at
                # "51/321 cue(s), 57 read, 1 backoff(s), pace 0.30s" the instant
                # play was pressed, then again on the next attempt -- the user
                # never got past 18% of a file that was extracting fine.
                #
                # ...and raising that bar was wrong too, in the other
                # direction. A field log (601c14f5) shows the player's very
                # first read after a 28-second pause answered 429, Kodi read
                # that as the end of the file, and the episode closed back to
                # the season list -- with the crawl at 1 push-back and a pace of
                # 0.30s, comfortably under any threshold. So there is no level
                # of pressure below which resuming is safe while we hold the
                # token: at the moment of resume the player needs it, and a
                # count or a pace cannot tell us otherwise.
                #
                # But the choice was never really between cancelling and
                # carrying on. Both answers throw away something the user wants
                # -- one the film, the other minutes of extraction that would
                # have finished. What the player actually needs is the token to
                # itself while it refills its buffer, which is seconds, not the
                # rest of the run. So STAND ASIDE instead of giving up: stop
                # touching the connection, let the player have it, then pick up
                # exactly where we left off. Longer if the provider had already
                # throttled us, since that token needs more room to recover.
                now = _tt.time()
                if _stall['saw_pause']:
                    _stall['saw_pause'] = False
                    _hot_now = bool(_hot['at']
                                    and (now - _hot['at']) <= _TOKEN_HOT_S)
                    _hold = _YIELD_HOT_S if _hot_now else _YIELD_RESUME_S
                    kodi.log(
                        'embedded: playback resumed -- standing aside for {0:.0f}s '
                        'so the player can refill from the debrid token{1}, then '
                        'continuing from {2}/{3} cue(s)'.format(
                            _hold, ' (it has been throttling us)' if _hot_now
                            else '', _xstats.get('done') or 0,
                            _xstats.get('total') or 0), level='INFO')
                    _release()
                    _until = now + _hold
                    while _tt.time() < _until:
                        try:
                            if not p.isPlayingVideo():
                                return True
                        except Exception:
                            return True
                        _tt.sleep(1.0)
                    # Not an abort: the pass carries on with everything it has
                    # already banked, and the stall guard below still watches
                    # the picture for the contention this cannot prevent --
                    # and the buffer gate decides whether to read now.
                    _stall['t'] = None
                    if _remote and _buffer_stand_aside(p):
                        return True
                    return False
                frozen = _clock_frozen(p, now)
                if frozen is None:
                    return False
                if frozen:
                    _log_stalled()
                    return True
                if _remote and _buffer_stand_aside(p):
                    return True
                return False
            except Exception:
                return False

        def _clock_frozen(p, now):
            """None when the clock cannot be read; True once the picture (playing,
            not paused) has not moved for more than _STALL_ABORT_S."""
            try:
                cur = p.getTime()
            except Exception:
                return None
            if _stall['t'] is None or abs(cur - _stall['t']) > 0.4:
                _stall['t'] = cur
                _stall['since'] = now
                return False
            return bool(_stall['since']
                        and (now - _stall['since']) > _STALL_ABORT_S)

        def _log_stalled():
            kodi.log('embedded: player stalled >{0}s -- aborting '
                           'extraction to free the debrid token'
                           .format(_STALL_ABORT_S), level='WARNING')

        # The player's own buffer. The stall guard acts once the picture has
        # ALREADY frozen, and then it throws the reading away. QA (TorBox,
        # 13-14 Nov 2026): the picture froze in most evening runs with an
        # extraction, and in a quiet night hour the provider also froze it in
        # runs where nothing else touched the link; with the provider well,
        # nothing the reading does -- not even 3.3 MB in two requests at a
        # jump -- slowed the player's refill (24 trials). So the reading
        # cannot cause a provider's bad minutes, but it can stay out of the
        # way during them and survive them: read only while the player's
        # buffer is stocked; once it runs low, close the connection and send
        # nothing -- through a frozen picture too -- until it is stocked
        # again, then carry on.
        # Kodi reports the buffer two ways: how full its read-ahead cache is
        # (Player.CacheLevel, %) and how much film that is (cachepercentage -
        # percentage of the running time). Either one is enough: a small cache
        # under a heavy file is full at a few seconds of film, the build's
        # 128 MB one holds minutes. Measured in QA (TorBox, night): full ~10 s
        # after a start or a jump, ~100 s of film ahead.
        _BUF_READY_S = 30.0      # read with this much film ahead...
        _BUF_READY_LEVEL = 90    # ...or with the cache this full
        _BUF_LOW_S = 15.0        # stand aside below this much film...
        _BUF_LOW_LEVEL = 50      # ...with the cache below this
        # A buffer that fills slowly but steadily -- a provider a little
        # faster than the film -- may never reach the marks above while the
        # film plays on. After this much film played without a stop, with at
        # least _BUF_LOW_S ahead and not falling, the wait ends too (review
        # of 0.3.81: otherwise such a pick waited out the limit and gave up a
        # reading 0.3.80 would have finished).
        _BUF_STEADY_S = 20.0
        # ...with this much ahead: a little over the low mark, so the first
        # requests after it do not put the wait straight back (review)
        _BUF_STEADY_AHEAD_S = 20.0
        _BUF_HOLD_MAX_S = 180.0  # not stocked again by then: stop, progress saved
        _BUF_NOINFO_S = 10.0     # this much film played with nothing reported:
        #                          Kodi keeps no buffer here -- the guard alone
        _BUF_FAILS_OFF = 5       # unreadable this many times running: the guard alone
        # Falling: while we read, the film playing on and Kodi's cache not
        # full, the buffer lost this much film within _BUF_FALL_SPAN_S -- the
        # provider is giving the player less than the film needs, and our
        # requests take a share of what it gives (QA 14 Nov 2026 05:08: after
        # a jump the buffer refilled to 32 s; in the 19 s we then read, 3 MB,
        # it fell to 15 s -- the low mark -- and the film never recovered).
        _BUF_FALL_S = 3.0
        _BUF_FALL_SPAN_S = 5.0
        # ...but not with this much film in hand: minutes ahead leave time to
        # see whether the fall goes on (review)
        _BUF_FALL_CEILING_S = 120.0
        _buf = {'hold': True, 'known': False, 'off': False, 'fails': 0,
                'at': None, 'm': None, 'trend': []}
        _t_start = {'at': None}      # when the reading began (monotonic)

        def _player_buffer():
            """(seconds of film ahead, cache fill %, seconds of film left) or
            None if unreadable -- also for a JSON-RPC error reply, which is not
            an empty buffer."""
            try:
                import json as _j
                import xbmc as _x
                lvl = (_x.getInfoLabel('Player.CacheLevel') or '').strip()
                reply = _j.loads(_x.executeJSONRPC(_j.dumps({
                    'jsonrpc': '2.0', 'id': 1,
                    'method': 'Player.GetProperties',
                    'params': {'playerid': 1, 'properties': [
                        'percentage', 'cachepercentage', 'totaltime']}}))
                    or '{}')
                res = reply.get('result') if isinstance(reply, dict) else None
                if not isinstance(res, dict) or 'cachepercentage' not in res:
                    return None
                tt = res.get('totaltime') or {}
                total = (((tt.get('hours') or 0) * 60 + (tt.get('minutes') or 0))
                         * 60 + (tt.get('seconds') or 0))
                pct = float(res.get('percentage') or 0.0)
                ahead = ((float(res.get('cachepercentage') or 0.0) - pct)
                         / 100.0 * total)
                rest = max(0.0, (100.0 - pct) / 100.0 * total)
                return (max(0.0, ahead), (int(lvl) if lvl.isdigit() else 0),
                        rest)
            except Exception:
                return None

        def _buffer_reading():
            """The player's buffer, asked at most about once a second (the
            check runs before every request and in every second of the pace
            sleeps). None = no reading this time; _BUF_FAILS_OFF of those in
            a row switch the gate off."""
            import time as _tt
            mono = _tt.monotonic()
            if _buf['at'] is not None and mono - _buf['at'] < 0.9:
                return _buf['m']
            m = _player_buffer()
            _buf['at'], _buf['m'] = mono, m
            if m is not None:
                _buf['fails'] = 0
                return m
            _buf['fails'] += 1
            if _buf['fails'] >= _BUF_FAILS_OFF and not _buf['off']:
                _buf['off'] = True
                _buf['hold'] = False
                kodi.log('embedded: the player\'s buffer cannot be read '
                               '-- reading with the stall guard only',
                               level='INFO')
            return None

        def _release():
            # Not even an idle connection: one we are not using is still one
            # more on the provider's count for this link. The next request
            # opens a fresh one.
            try:
                _rel = _xstats.get('release')
                if _rel:
                    _rel()
            except Exception:
                pass

        def _to_the_end(ahead, rest):
            # Kodi's cache already reaches the end of the film: from here the
            # seconds ahead ARE the rest of the film and fall with it, and
            # the cache % falls too (review: a pick in the last minutes of an
            # episode read as "falling" and gave up on a healthy provider).
            return rest > 0 and ahead >= rest - 2.0

        def _falling(p, ahead, lvl):
            """While reading: has the buffer lost _BUF_FALL_S of film in the
            last _BUF_FALL_SPAN_S, with the film playing steadily (it moved
            as far as the clock did) and Kodi's cache not full? One entry per
            fresh reading; anything else (a pause, a jump) starts it over."""
            at = _buf['at']
            tr = _buf['trend']
            if tr and tr[-1][3] == at:
                return False                 # the same reading again
            try:
                film = p.getTime()
            except Exception:
                return False
            import time as _tt
            mono = _tt.monotonic()
            # Played on = the film moved as far as the clock over the whole
            # trend, give or take 1.5 s (Kodi's play time moves in steps,
            # and a poll can be late behind a busy interpreter: review).
            if tr and abs((film - tr[0][1]) - (mono - tr[0][0])) > 1.5:
                tr[:] = []                   # stood or jumped: start over
            tr.append((mono, film, ahead, at, lvl))
            while tr and mono - tr[0][0] > _BUF_FALL_SPAN_S + 1.5:
                tr.pop(0)
            return (lvl < _BUF_READY_LEVEL and ahead < _BUF_FALL_CEILING_S
                    and mono - tr[0][0] >= _BUF_FALL_SPAN_S - 0.5
                    and tr[0][2] - ahead >= _BUF_FALL_S)

        def _buffer_stand_aside(p):
            """With the picture playing: wait here, sending nothing, while the
            player's buffer is low or falling. True = stop the extraction."""
            if _buf['off']:
                return False
            m = _buffer_reading()
            if m is None:
                return False
            ahead, lvl, rest = m
            if ahead >= 0.5 or lvl > 0:
                _buf['known'] = True
            if _to_the_end(ahead, rest):
                _buf['hold'] = False
                _buf['trend'] = []
                return False
            if _buf['hold']:
                if ahead >= _BUF_READY_S or lvl >= _BUF_READY_LEVEL:
                    _buf['hold'] = False
                    _buf['trend'] = []
                    return False
                why = 'is not stocked yet'
            elif ahead < _BUF_LOW_S and lvl < _BUF_LOW_LEVEL:
                _buf['hold'] = True
                why = 'ran low'
            elif _falling(p, ahead, lvl):
                _buf['hold'] = True
                why = 'is falling'
            else:
                return False
            seed = [(e[0], e[2], e[4]) for e in _buf['trend']]
            _buf['trend'] = []
            import time as _tt
            t_hold = _tt.monotonic()
            # Never past the extraction's own deadline: the single-extraction
            # flag is reclaimed as stale after deadline + 120 s.
            _began = _t_start['at'] if _t_start['at'] is not None else t_hold
            limit = min(_BUF_HOLD_MAX_S,
                        max(0.0, deadline_s - (t_hold - _began)))
            frozen_said = False
            try:
                film0 = p.getTime()
            except Exception:
                film0 = None
            steady_from = film0          # where the film last stood or jumped
            steady_mono = t_hold
            # (monotonic, seconds ahead, %), ~10 s -- a fall carries on
            # from the readings that showed it
            seen = seed if why == 'is falling' else []
            _release()
            kodi.log(
                'embedded: the player\'s buffer {0} ({1:.0f}s of film ahead, '
                'cache {2}%) -- connection closed, sending nothing until it '
                'holds {3:.0f}s or {4}%'.format(why, ahead, lvl, _BUF_READY_S,
                                                _BUF_READY_LEVEL),
                level='INFO')
            while True:
                _tt.sleep(1.0)
                try:
                    if not p.isPlayingVideo():
                        return True
                    if cancelled is not None and cancelled():
                        # the viewer has picked something else: the reading
                        # is not wanted (and a new pick would be refused
                        # while this one holds the single-extraction flag)
                        kodi.log('embedded: the pick was replaced while '
                                       'waiting for the player\'s buffer -- '
                                       'stopping', level='INFO')
                        return True
                    import xbmc as _x
                    paused = bool(_x.getCondVisibility('Player.Paused'))
                except Exception:
                    return True
                # A freeze sat out here is never counted after the wait.
                if paused:
                    _stall['t'] = None
                    if why == 'is not stocked yet':
                        # The pause branch takes over (reading while paused
                        # is allowed, as before -- a pick from the subtitle
                        # list starts paused); the hold stays set for the
                        # moment the picture moves again.
                        return False
                    # A viewer who pauses a picture that ran short is letting
                    # the player refill: stay out of its way (review).
                now = _tt.time()
                mono = _tt.monotonic()
                # A frozen picture here is not ours to answer for: we send
                # nothing and hold no connection. Stopping would throw away
                # the reading and give the player nothing back; it was what
                # made the extraction "not work on TorBox" (QA 13-14 Nov 2026:
                # the provider froze the picture in runs where nothing else
                # touched the link). So wait on, within the limit.
                if (not paused and _clock_frozen(p, now)
                        and not frozen_said):
                    frozen_said = True
                    kodi.log('embedded: the picture is frozen while we '
                                   'send nothing -- waiting for the provider '
                                   'to serve the player again', level='INFO')
                try:
                    film = p.getTime()
                except Exception:
                    film = None
                # Played steadily = the film moved as far as the clock since
                # the count began, give or take 1.5 s -- over the window, not
                # per poll: Kodi's play time moves in steps, and a poll can
                # take seconds behind a busy interpreter (review).
                if (film is None or steady_from is None
                        or abs((film - steady_from) - (mono - steady_mono)) > 1.5):
                    steady_from, steady_mono = film, mono   # count again
                m = _buffer_reading()
                if m is None and _buf['off']:
                    _stall['t'] = None
                    return False
                if m is not None:
                    ahead, lvl, rest = m
                    seen.append((mono, ahead, lvl))
                    seen = [s for s in seen if mono - s[0] <= 10.5]
                    if ahead >= 0.5 or lvl > 0:
                        _buf['known'] = True
                    elif not _buf['known'] and film0 is not None:
                        played = (film - film0) if film is not None else 0.0
                        if played >= _BUF_NOINFO_S:
                            _buf['off'] = True
                            _buf['hold'] = False
                            _stall['t'] = None
                            kodi.log('embedded: the player reports no '
                                           'buffer -- reading with the stall '
                                           'guard only', level='INFO')
                            return False
                    still_falling = (len(seen) > 1
                                     and mono - seen[0][0] >= _BUF_FALL_SPAN_S - 0.5
                                     and seen[0][1] - ahead >= _BUF_FALL_S
                                     and lvl < _BUF_READY_LEVEL
                                     and ahead < _BUF_FALL_CEILING_S)
                    stocked = (_to_the_end(ahead, rest)
                               or ((ahead >= _BUF_READY_S
                                    or lvl >= _BUF_READY_LEVEL)
                                   and not still_falling))
                    # Steady: in film seconds, or -- a cache too small for
                    # that much of a heavy file -- in how full it is.
                    steady = (film is not None and steady_from is not None
                              and mono - steady_mono >= _BUF_STEADY_S
                              and ((ahead >= _BUF_STEADY_AHEAD_S
                                    and ahead >= seen[0][1] - 1.0)
                                   or (lvl >= _BUF_LOW_LEVEL
                                       and lvl >= seen[0][2] - 2)))
                    if stocked or steady:
                        _buf['hold'] = False
                        # The clock may still be standing for a moment while
                        # the player picks up again: measure a freeze from
                        # here, not from one we sat out.
                        _stall['t'] = None
                        kodi.log(
                            'embedded: the player\'s buffer is {0} after '
                            '{1:.0f}s ({2:.0f}s of film ahead, cache {3}%) -- '
                            'reading'.format('back' if stocked else 'steady',
                                             mono - t_hold, ahead, lvl),
                            level='INFO')
                        return False
                if mono - t_hold > limit:
                    kodi.log(
                        'embedded: the player\'s buffer did not fill in {0:.0f}s '
                        '-- stopping the extraction for the film\'s sake'
                        .format(limit), level='WARNING')
                    return True

        # Automatic/direct modes may extract from a live debrid/HTTP stream.
        # Over HTTP the extractor uses ONE keep-alive connection, paced requests
        # + 429 backoff, and the stall-abort above, so it yields to the player
        # instead of starving it. Align-only never calls this function.
        _allow_http = bool(allow_http)
        # Carry an interrupted pass forward. On a provider that rate-limits
        # hard, a remote extraction can only ever END interrupted -- and every
        # one of those endings used to discard the whole pass, so the file never
        # finished no matter how many times it was played. With a scratch file
        # each attempt continues where the last stopped. Deliberately ONE file
        # per source language, not per title: it is bounded, and the extractor's
        # own fingerprint (byte length + track + codec + Cues layout) refuses
        # work saved from any other file, so the worst a collision can do is
        # start over -- exactly today's behaviour. Local files skip it; a local
        # pass is a single cheap sequential walk with nothing to carry.
        try:
            if reference is None:
                import xbmcgui as _g0
                _g0.Window(10000).clearProperty('povil.embedded_partial')
        except Exception:
            pass
        _resume = None
        if _remote:
            try:
                _resume = os.path.join(
                    kodi.cache_dir(),
                    'embedded_resume_{0}.bin'.format(src_lang or 'src'))
            except Exception:
                _resume = None
        # Visible progress. The corner DialogProgressBG the caller supplies is
        # not drawn over FULLSCREEN VIDEO, which is exactly where the user is
        # while this runs -- so from their seat a 5-minute extraction looks
        # identical to nothing happening at all, and the field report was
        # precisely that ("the movie plays but nothing happens"). A toast DOES
        # draw over video, so send one every _TOAST_STEP percent, and never
        # closer together than _TOAST_MIN_S so it cannot become a nuisance.
        # Percentage steps alone are not enough to be VISIBLE. Waiting for the
        # first whole step means the user still watches nothing happen for as
        # long as that step takes, and on a distant provider that is minutes:
        # field log 37c47bda reached 15.9% and 18.4% on its two attempts and so
        # never crossed a single 20% mark -- the only thing that user ever saw
        # was the message saying it had stopped. So: say so as soon as the first
        # line comes out (that is the "it is working" signal, and it is the one
        # that was missing), then every _TOAST_STEP percent, and in any case
        # never let more than _TOAST_MAX_GAP_S pass in silence while lines are
        # still arriving -- which is what covers a provider slow enough that a
        # whole step takes minutes. _TOAST_MIN_S keeps a fast provider from
        # turning all of that into a nuisance.
        _TOAST_STEP = 10
        _TOAST_MIN_S = 20.0
        _TOAST_MAX_GAP_S = 45.0
        _toast = {'pct': -1, 'at': 0.0, 'started': False}

        def _progress(done, total):
            # _status, NOT sub_utils.notify: auto-on-play runs this whole path
            # in quiet mode precisely so it stays silent, and a raw notify()
            # bypasses that and pops toasts for something the user never picked.
            if steps is not None and getattr(steps, 'on_screen', False):
                # Hebrew is on screen: the reading needs no bar and no
                # toasts (the handler closes the bar on every view, and a
                # tick here opened it again -- it blinked; review)
                return
            try:
                if total and done > 0:
                    import time as _tt
                    now = _tt.time()
                    gap = now - _toast['at']
                    pct = int(done * 100 / total)
                    step = pct - (pct % _TOAST_STEP)
                    if not _toast['started']:
                        _toast['started'], _toast['at'] = True, now
                        _status('AI: מחלץ תרגום מובנה מהסרטון — {0} שורות'
                                .format(total), time_ms=2500)
                    elif ((step > _toast['pct'] and step > 0
                           and gap >= _TOAST_MIN_S)
                          or gap >= _TOAST_MAX_GAP_S):
                        _toast['pct'] = max(step, _toast['pct'])
                        _toast['at'] = now
                        _status('AI: מחלץ תרגום מובנה — {0}% ({1}/{2})'.format(
                            pct, done, total), time_ms=2500)
            except Exception:
                pass
            if progress_cb:
                try:
                    progress_cb(done, total)
                except Exception:
                    pass

        try:
            _t_start['at'] = time.monotonic()
            def _on_track(used):
                # The track is chosen: when it is another language's text
                # track (src_lang's is a picture), say so NOW, while the first
                # Hebrew lines are being read -- not after the whole track.
                why['lang_used'] = used
                try:
                    if (embedded_extract._lang_key(used)
                            != embedded_extract._lang_key(src_lang)):
                        kodi.log(
                            'embedded_ai: the {0} embedded subtitle is a '
                            'picture, not text -- reading the {1} embedded '
                            'track instead'.format(src_lang, used), level='INFO')
                        _status('AI: הכתובית המובנית ב{0} היא תמונה ולא טקסט — '
                                'מתרגם מהכתובית המובנית ב{1}'.format(
                                    _lang_display_he(src_lang),
                                    _lang_display_he(used)), time_ms=6000)
                except Exception:
                    pass

            srt_text = embedded_extract.extract_srt(
                url, track_num=track_num, lang=src_lang,
                allow_http=_allow_http, deadline_s=deadline_s,
                abort_cb=_should_abort, progress_cb=_progress,
                resume_path=_resume, stats=_xstats,
                other_text=(_EMBEDDED_TEXT_ORDER if why is not None else None),
                on_track=(_on_track if why is not None else None),
                steps=((steps.window, steps.feed, steps.timeline, steps.moved)
                       if (steps is not None and _remote) else None),
                log=lambda m: kodi.log('embedded_extract: ' + m,
                                             level='INFO'))
        finally:
            if _win is not None:
                try:
                    _win.clearProperty(_ACTIVE)
                except Exception:
                    pass
        if why is not None and _xstats.get('why'):
            why['reason'] = _xstats.get('why')
        if reference is None and isinstance(_xstats.get('tracks'), list):
            _remember_tracks(url, _xstats['tracks'])
        if not srt_text or srt_text.count('-->') < 3:
            # Say WHICH of the two this was. "Could not extract -- try another
            # subtitle" is wrong and actively harmful when the pass actually
            # banked progress that the next attempt will continue from: the user
            # in the field read it as a dead end and gave up on a job that was
            # 23% done and would have finished.
            _done, _total = _xstats.get('done') or 0, _xstats.get('total') or 0
            if _remote and _done and _total and _done < _total:
                kodi.log(
                    'embedded: stopped at {0}/{1} cue(s) for {2} -- progress '
                    'saved, the next attempt continues from there'.format(
                        _done, _total, src_lang), level='INFO')
                _status('AI: החילוץ נעצר ב-{0}% — ההתקדמות נשמרה, בחרו שוב '
                        'כדי להמשיך'.format(int(_done * 100 / _total)),
                        time_ms=6000)
                # Tell the caller this was a PAUSE, not a dead end, so it does
                # not follow up with "try another subtitle" -- which would
                # contradict the message above and send the user away from a
                # job that is most of the way done.
                #
                # ONLY for a pick the user actually made. The auto-on-play
                # thread reaches this same function in quiet mode and has no
                # picker to inform; the flag it set there would simply sit on
                # the window until some LATER, unrelated failure read it and
                # swallowed the toast that failure needed to show. Tying the
                # flag to the same condition as the message keeps the two
                # honest, and the timestamp bounds anything that still slips
                # through -- a picker consumes it within seconds, never later.
                if not progress._QUIET and reference is None:
                    try:
                        import time as _tt
                        import xbmcgui as _g
                        _g.Window(10000).setProperty(
                            'povil.embedded_partial',
                            '{0}/{1}@{2:.0f}'.format(_done, _total,
                                                     _tt.time()))
                    except Exception:
                        pass
                return None
            kodi.log(
                'embedded: no usable text track for {0}{1}'.format(
                    src_lang, (' ({0})'.format(_xstats['why'])
                               if _xstats.get('why') else '')),
                level='INFO')
            return None
        out = os.path.join(kodi.cache_dir(),
                           'embedded_{0}.srt'.format(src_lang or 'src'))
        with open(out, 'w', encoding='utf-8') as f:
            f.write(srt_text)
        kodi.log('embedded: extracted {0} cue(s) for {1}'.format(
            srt_text.count('-->'), src_lang), level='INFO')
        return out
    except Exception as e:
        kodi.log('embedded extract failed: {0}'.format(e),
                       level='WARNING')
        return None


# --- A gender reference from inside the playing file ------------------------
#
# 15 Nov 2026, the pool's own numbers (tr_events, 14 days, 1,079 AI
# translations): 343 had no gender reference, 267 of them because no provider
# had a subtitle in any reference language at all -- fresh episodes and titles
# nobody subtitled. 187 of the 343 were translated from an EMBEDDED track, and
# a file that carries an English text track very often carries Spanish,
# Portuguese, Arabic, French ... beside it: human subtitles of the same cut,
# on the same clock. They are read like the source track was (same extractor,
# same guards for the player), one track, quietly, and only where the
# provider serves a track in seconds: a reading that would take longer than
# _EMB_REF_BUDGET_S in all is given up after _EMB_REF_PROBE_S.
#
# Blind study 008 (15 Nov 2026): a Spanish track of the file as the reference
# against no reference, 8-10-2 -- no gain, so second-tier tracks stay out
# (arabic_gender._EMBEDDED_REF_LANGS). ON since 0.3.88 for an ARABIC track,
# by the owner's word without a study of its own (Arabic is the foreign
# oracle the reference was measured on; the file's track is on the file's
# clock), and only for a translation made from the file's own track -- that
# job reads the stream already; any other translation asks the playing file
# nothing. The setting (no row in the settings screen) turns it off.
_EMB_REF_SETTING = 'embedded_reference'
_EMB_REF_DEFAULT = True
# The subtitle tracks of the file a source track was just extracted from,
# as its extraction parsed them: the reference asks no second read of the
# file's head (2 MB in one request, beside the player) to learn whether an
# Arabic track is there -- for most files it is not (review, 0.3.88).
_EMB_TRACKS = {}
_EMB_TRACKS_KEEP = 2


def _remember_tracks(url, tracks):
    try:
        _EMB_TRACKS.pop(url, None)
        _EMB_TRACKS[url] = [dict(t) for t in tracks if isinstance(t, dict)]
        while len(_EMB_TRACKS) > _EMB_TRACKS_KEEP:
            _EMB_TRACKS.pop(next(iter(_EMB_TRACKS)))
    except Exception:
        pass
_EMB_REF_DEADLINE_S = 120.0
_EMB_REF_PROBE_S = 15.0
_EMB_REF_BUDGET_S = 90.0


def _embedded_reference(info, source_lang, cancelled=None):
    """(tracks, read) for arabic_gender.begin(embedded=...), or None when the
    playing file may not be read for a reference (the setting, the embedded
    mode, no direct stream). tracks() lists the file's text subtitle tracks
    that are not `source_lang`'s; read(track) returns one as SRT text or
    None. Never raises."""
    try:
        if not settings.get_bool(_EMB_REF_SETTING, _EMB_REF_DEFAULT):
            return None
        policy = _embedded_translation_policy()
        url = _playing_video_url(info)
        if not url or not policy.get('enabled'):
            return None
        remote = '://' in url
        if remote and not policy.get('allow_http'):
            return None
        from resources.lib.fetching.embedded import embedded_extract
        own = embedded_extract._lang_key(source_lang)
    except Exception:
        return None

    def tracks():
        found = _EMB_TRACKS.get(url)
        if found is None:
            # no extraction of this file in this process (an external
            # subtitle laid on the track's cue times): the head is read
            try:
                found = embedded_extract.probe_tracks(
                    url, log=lambda m: kodi.log('embedded_extract: ' + m,
                                                      level='INFO'))
            except Exception:
                found = []
        return [t for t in found
                if t.get('is_text') and not t.get('forced')
                and not t.get('signs')
                and embedded_extract._lang_key(t.get('lang')) != own]

    slow = {'said': False}

    def read(track):
        if slow['said']:
            # the provider's pace, not the track's: the next language of the
            # same file would be given up the same way, 15 s later (QA Kodi,
            # 15 Nov 2026: French, then Italian)
            return None
        started = time.monotonic()

        def too_slow(stats):
            spent = time.monotonic() - started
            if spent < _EMB_REF_PROBE_S:
                return False
            done, total = stats.get('done') or 0, stats.get('total') or 0
            if not done or not total:
                late = spent >= 2 * _EMB_REF_PROBE_S
            else:
                late = spent * total / done > _EMB_REF_BUDGET_S
            if late and not slow['said']:
                slow['said'] = True
                kodi.log(
                    'embedded reference: {0} of {1} line(s) of the {2} track '
                    'in {3:.0f}s -- too slow here for a reference, the '
                    'other tracks of the file are not tried'.format(
                        done, total or '?', track.get('lang'), spent),
                    level='INFO')
            return late

        try:
            path = _extract_embedded_srt(
                info, embedded_extract._lang_key(track.get('lang')),
                track_num=track.get('num'), deadline_s=_EMB_REF_DEADLINE_S,
                allow_http=True, cancelled=cancelled, reference=too_slow)
            if path and os.path.isfile(path):
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    return f.read()
        except Exception as e:
            kodi.log('embedded reference: {0}'.format(e), level='INFO')
        return None

    return tracks, read
