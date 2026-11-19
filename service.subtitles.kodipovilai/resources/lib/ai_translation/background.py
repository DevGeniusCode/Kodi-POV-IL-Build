# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Background translation: the picker hand-off (action=bg_translate_picker), the translate-file
# handler (action=translate_file) and the job ownership marks.

import os
try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None
from resources.lib.delivery.kodi_player import decode_selection_b64, playing_now, progressive_cleanup_patterns, progressive_slot_path
from resources.lib.ui.dialogs import safe_log, dialogs_title


def _new_translation_job():
    import uuid
    token = uuid.uuid4().hex
    win = xbmcgui.Window(10000)
    win.setProperty('ai_subs.live_translate_job', token)
    return token


def _owns_translation_job(token):
    return xbmcgui.Window(10000).getProperty('ai_subs.live_translate_job') == token


def _clear_translation_job(token):
    if _owns_translation_job(token):
        win = xbmcgui.Window(10000)
        win.clearProperty('ai_subs.live_translate_active')
        win.clearProperty('ai_subs.live_translate_source')
        win.clearProperty('ai_subs.live_translate_job')


def handle_bg_translate_picker(params):
    """Fired by _handle_download after delivering the English
    fallback subtitle to Kodi. Calls translate.resolve() with a
    progressive_cb that writes versioned .vN.srt files and swaps
    them in via xbmc.Player().setSubtitles() as Hebrew chunks
    complete. No sentinel handshake here -- DarkSubs is not
    involved in the native-picker path."""
    import base64
    try:
        from resources.lib.ai_translation import service
        from resources.lib.core import kodi, selection as core_selection, video_context, link_codec
    except Exception as e:
        safe_log('bg_translate_picker import failed: {0}'.format(e),
                  level='ERROR')
        return

    def _b64(b):
        try:
            return base64.b64decode(
                b.encode('ascii')).decode('utf-8')
        except Exception:
            return ''

    link = _b64(params.get('link_b64', ''))
    expected_source_id = _b64(params.get('source_id_b64', ''))
    _timing_selection = decode_selection_b64(
        params.get('selection_b64', ''))
    if not link:
        safe_log('bg_translate_picker: missing link',
                  level='WARNING')
        return

    # A delayed RunScript for selection A may start after the viewer has
    # already chosen B.  Do not let A claim the live-translation job, clear or
    # replace B's subtitle.  The next explicit pick can translate A again.
    def _selection_is_current():
        try:
            return bool(core_selection.subtitle_selection_matches(
                _timing_selection.get('token') or '',
                _timing_selection.get('link_hash') or '',
                _timing_selection.get('stream_hash') or ''))
        except Exception:
            return False

    try:
        if not _selection_is_current():
            safe_log('bg_translate_picker: stale selection discarded',
                      level='INFO')
            return
    except Exception:
        return

    info = video_context.current_video_info()

    # Set Window props upfront so chunk_ready gates pass. The
    # DarkSubs flow sets these inside the first_ready callback,
    # but here first_ready is a no-op (the English fallback was
    # already delivered to Kodi by _handle_download) -- so we
    # need to set the flags before resolve() can emit chunk_ready.
    xbmcgui.Window(10000).setProperty(
        'ai_subs.live_translate_active', '1')
    xbmcgui.Window(10000).setProperty(
        'ai_subs.live_translate_source', expected_source_id)

    _job_token = _new_translation_job()
    _ver = {'n': 0}
    _completion = {'seen': False}
    try:
        _initial_media = xbmc.Player().getPlayingFile() or ''
    except Exception:
        _initial_media = ''

    # Corner progress bar for the (long) embedded-extraction phase. Created
    # lazily on the first extract-progress tick and closed when translation
    # starts or resolve() returns. DialogProgressBG is non-modal -- it shows in
    # the corner and never blocks playback or input. A normal 'ai' pick has no
    # extraction phase, so the bar simply never appears for it.
    _ebar = {'d': None}

    def _extract_progress(done, total, label=None):
        # `label` (optional) lets the resolve-side phases narrate what's
        # happening -- the align path reports "reading timing / finding source /
        # syncing", the full-text extract omits it and keeps the default. Two-arg
        # callers (embedded_extract.extract_srt) still work unchanged.
        try:
            msg = label or 'מכין תרגום מובנה...'
            if _ebar['d'] is None:
                _ebar['d'] = xbmcgui.DialogProgressBG()
                _ebar['d'].create(dialogs_title(), msg)
            pct = int(done * 100 / total) if total else 0
            _ebar['d'].update(pct, dialogs_title(), '{0} {1}%'.format(msg, pct))
        except Exception:
            pass

    def _close_ebar():
        try:
            if _ebar['d'] is not None:
                _ebar['d'].close()
                _ebar['d'] = None
        except Exception:
            pass

    def on_phase(phase, payload):
        try:
            if (not _owns_translation_job(_job_token)
                    or not _selection_is_current()):
                return
            if phase == 'done':
                _completion['seen'] = True
            if phase in ('chunk_ready', 'done'):
                win = xbmcgui.Window(10000)
                if (win.getProperty('ai_subs.live_translate_active') != '1'
                        or not payload.get('source_id')
                        or win.getProperty('ai_subs.live_translate_source')
                        != payload['source_id']):
                    return
            # embedded_ai: we fired bg WITHOUT a pre-extracted source id, so
            # adopt the id resolve() computed (the first phase that carries one)
            # -- setting the live prop makes the chunk_ready gate below match, so
            # the progressive line-by-line swap works for embedded picks too.
            if (not xbmcgui.Window(10000).getProperty(
                    'ai_subs.live_translate_source')
                    and payload.get('source_id')):
                xbmcgui.Window(10000).setProperty(
                    'ai_subs.live_translate_source', payload['source_id'])
            # Any translation phase means extraction has finished -> drop the bar.
            if phase in ('first_ready', 'chunk_ready', 'done'):
                _close_ebar()
            # We tolerate first_ready -- it's a no-op here, the
            # fallback is already on Kodi. We just guard against
            # an unexpected source_id mismatch (sanity check, should
            # never happen but cheap to verify).
            if phase == 'first_ready':
                _got = payload.get('source_id', '')
                if (expected_source_id
                        and _got != expected_source_id):
                    safe_log(
                        'bg_translate_picker: source_id mismatch '
                        '(expected {0}, got {1})'.format(
                            expected_source_id, _got),
                        level='WARNING')
                return
            if phase == 'chunk_ready':
                # Same dual-gate as the DarkSubs path: active flag
                # AND source_id match. If the user picked a different
                # subtitle while we're translating, a stale chunk
                # from THIS translation must not clobber the new
                # pick's subtitles.
                if (xbmcgui.Window(10000).getProperty(
                        'ai_subs.live_translate_active') != '1'
                        or xbmcgui.Window(10000).getProperty(
                            'ai_subs.live_translate_source')
                        != payload['source_id']):
                    return
                # Alternating-slot write (same approach as DarkSubs
                # path). Caps Kodi subtitle-stream accumulation at 2
                # during translation instead of 1 per chunk -- user
                # reported "10/10" piling up in the picker.
                slot = 'b' if _ver.get('slot', 'b') == 'a' else 'a'
                _ver['slot'] = slot
                ver_path = progressive_slot_path(
                    kodi.cache_dir(), payload['source_id'], slot,
                    payload.get('release') or '')
                _tmp = ver_path + '.aitmp'
                with open(_tmp, 'w', encoding='utf-8') as _f:
                    _f.write(payload['merged_text'])
                os.replace(_tmp, ver_path)
                if (not _owns_translation_job(_job_token)
                        or not _selection_is_current()):
                    return
                try:
                    if xbmc.Player().isPlayingVideo():
                        if (not _owns_translation_job(_job_token)
                                or not _selection_is_current()):
                            return
                        core_selection.apply_subtitle_file(
                            ver_path, selection=_timing_selection,
                            abandon_on_failure=False)
                except Exception as _e:
                    safe_log(
                        'bg_translate_picker setSubtitles raised: '
                        '{0}'.format(_e), level='DEBUG')
                return
            if phase == 'done':
                # Same robust canonical-swap pattern as the DarkSubs
                # path: copy canonical bytes to a fresh _final.he.srt
                # path to defeat Kodi's parse cache; don't gate on
                # isPlayingVideo (let the try/except handle a paused
                # player); only delete .vN files when the swap
                # actually landed.
                _canonical_swap_succeeded = False
                if payload.get('success'):
                    try:
                        # USE THE PATH resolve() REPORTS. Recomputing it here
                        # was wrong twice over: translated_path() was called
                        # without tier=, while resolve() writes with tier='ar'
                        # whenever a gender reference was found (the default,
                        # and force-enabled by migration), and the source
                        # language was hardcoded 'en'. So this file did not
                        # exist on most jobs, the swap below was skipped, and
                        # the viewer kept the last progressive SLOT file --
                        # partly source text if the translation was
                        # interrupted. The recompute stays only as a fallback
                        # for a payload from an older resolve().
                        canonical = payload.get('path') or ''
                        if not canonical:
                            from resources.lib.core.cache import translation_cache as _cache
                            canonical = _cache.translated_path(
                                (info.get('imdb_id') or '').strip(),
                                info.get('season') or '',
                                info.get('episode') or '',
                                'en',
                                source_id=payload['source_id'])
                        if os.path.isfile(canonical):
                            # Name the delivered file after the source RELEASE so
                            # Kodi shows the full release name (not a hash); fall
                            # back to the source-id when no release is known.
                            _rel = kodi.safe_release_filename(
                                payload.get('release') or '')
                            _final_name = (
                                _rel + '.he.srt' if _rel
                                else 'progressive_{0}_final.he.srt'.format(
                                    payload['source_id']))
                            _final_path = os.path.join(
                                kodi.cache_dir(), _final_name)
                            try:
                                with open(canonical, 'rb') as _src_f:
                                    _bytes = _src_f.read()
                                _tmp_final = _final_path + '.aitmp'
                                with open(_tmp_final, 'wb') as _dst_f:
                                    _dst_f.write(_bytes)
                                os.replace(_tmp_final, _final_path)
                            except OSError as _we:
                                safe_log(
                                    'bg_translate_picker done copy '
                                    'failed: {0}'.format(_we),
                                    level='WARNING')
                                _final_path = None
                            if (_final_path and _owns_translation_job(_job_token)
                                    and _selection_is_current()):
                                try:
                                    p = xbmc.Player()
                                    # setSubtitles() POSTS to the
                                    # VideoPlayer thread and returns
                                    # before the stream is registered,
                                    # so "it did not raise" is not
                                    # evidence anything happened. The
                                    # stream list read on the next line
                                    # was the PRE-add list, so picking
                                    # its last entry selected the last
                                    # progressive SLOT -- which the
                                    # cleanup below then deleted,
                                    # pinning the viewer to a stream
                                    # whose file is gone. Count first,
                                    # wait for it to grow, then claim.
                                    try:
                                        _before = len(
                                            p.getAvailableSubtitleStreams() or [])
                                    except Exception:
                                        _before = -1
                                    if (not _owns_translation_job(_job_token)
                                            or not _selection_is_current()):
                                        return
                                    if payload.get('_cached_media'):
                                        # Copying the final file can race a
                                        # movie change. Re-check at delivery,
                                        # not only before entering done.
                                        if (p.getPlayingFile() or '') != payload['_cached_media']:
                                            return
                                    p.setSubtitles(_final_path)
                                    p.showSubtitles(True)
                                    if not _selection_is_current():
                                        return
                                    _grew = False
                                    if _before >= 0:
                                        for _ in range(20):      # <= 1s
                                            xbmc.sleep(50)
                                            if (not _owns_translation_job(
                                                    _job_token)
                                                    or not _selection_is_current()):
                                                return
                                            try:
                                                _streams = (
                                                    p.getAvailableSubtitleStreams()
                                                    or [])
                                            except Exception:
                                                break
                                            if len(_streams) > _before:
                                                # Pin our stream so Kodi
                                                # does not auto-revert to
                                                # a pre-existing Hebrew
                                                # SRT.
                                                try:
                                                    if (not _owns_translation_job(
                                                            _job_token)
                                                            or not _selection_is_current()):
                                                        return
                                                    if (payload.get('_cached_media')
                                                            and (p.getPlayingFile() or '') != payload['_cached_media']):
                                                        return
                                                    p.setSubtitleStream(
                                                        len(_streams) - 1)
                                                    _grew = True
                                                except Exception:
                                                    _grew = False
                                                break
                                    _registered_and_pinned = _grew
                                    if not _grew and not playing_now():
                                        # Nobody to strand: with no player
                                        # there is no stream pointing at a
                                        # slot file, so deleting is safe --
                                        # and is the outcome we want, or the
                                        # slots pile up on every job that
                                        # outlives playback.
                                        _grew = True
                                    elif not _grew:
                                        safe_log(
                                            'bg_translate_picker: Kodi did not '
                                            'register the final subtitle '
                                            'stream -- keeping the progressive '
                                            'slots rather than deleting a file '
                                            'the player may still be on',
                                            level='WARNING')
                                    _canonical_swap_succeeded = _grew
                                    if (_registered_and_pinned
                                            and _selection_is_current()):
                                        core_selection.mark_subtitle_delivery_applied(
                                            canonical,
                                            selection=_timing_selection)
                                        core_selection.confirm_subtitle_sync_fix(
                                            canonical,
                                            selection=_timing_selection)
                                except Exception as _se:
                                    safe_log(
                                        'bg_translate_picker done '
                                        'setSubtitles raised: {0}'
                                        .format(_se), level='DEBUG')
                    except Exception as _e:
                        safe_log(
                            'bg_translate_picker done canonical '
                            'swap failed: {0}'.format(_e),
                            level='DEBUG')
                # Cleanup ONLY when the canonical swap succeeded.
                if (_canonical_swap_succeeded
                        and _owns_translation_job(_job_token)
                        and _selection_is_current()):
                    try:
                        import glob as _glob
                        # Patterns cover legacy (_v*), the hash-named
                        # _a/_b slots AND the release-named slots. The
                        # clean <release>.he.srt _final stays as the
                        # canonical stream Kodi is on.
                        _patterns = progressive_cleanup_patterns(
                            payload['source_id'],
                            payload.get('release') or '')
                        for _pat in _patterns:
                            for _stale in _glob.glob(
                                    os.path.join(
                                        kodi.cache_dir(),
                                        _pat)):
                                try:
                                    os.remove(_stale)
                                except OSError:
                                    pass
                    except Exception:
                        pass
                _clear_translation_job(_job_token)
                return
        except Exception as _e:
            safe_log(
                'bg_translate_picker on_phase({0}) raised: '
                '{1}'.format(phase, _e), level='WARNING')

    _resolved = None
    try:
        _resolved = service.resolve(
            link, info, progressive_cb=on_phase,
            extract_progress_cb=_extract_progress,
            selection=_timing_selection)
        # Cache and community early returns do not emit progressive phases.
        # Deliver their validated result through the same final-swap path,
        # before clearing this job's ownership. Never replace a newer pick or
        # attach a completed result to a different movie.
        if (_resolved and not _completion['seen']
                and os.path.isfile(_resolved)
                and _owns_translation_job(_job_token)
                and _selection_is_current()):
            try:
                _current_media = xbmc.Player().getPlayingFile() or ''
            except Exception:
                _current_media = ''
            _win = xbmcgui.Window(10000)
            _source = _win.getProperty('ai_subs.live_translate_source') or ''
            if (_initial_media and _current_media == _initial_media
                    and _win.getProperty('ai_subs.live_translate_active') == '1'
                    and (not expected_source_id or _source == expected_source_id)):
                if not _source:
                    # Embedded cache hits have no extracted source id yet.
                    # This identifier belongs only to the delivery job; it
                    # never changes a translation cache key or pool tier.
                    import hashlib as _hashlib
                    _source = 'cached_' + _hashlib.sha256(
                        _resolved.encode('utf-8')).hexdigest()[:16]
                    _win.setProperty('ai_subs.live_translate_source', _source)
                on_phase('done', {'success': True, 'path': _resolved,
                                  'source_id': _source, '_cached_media': _initial_media})
    except Exception as e:
        safe_log(
            'bg_translate_picker resolve crashed: {0}'.format(e),
            level='ERROR')
    finally:
        # Belt-and-suspenders -- the done phase clears these too,
        # but on a resolve() crash before done we still want the
        # active flag cleared so a follow-up pick isn't gated by
        # a stale source_id. Also drop the extraction bar if it's
        # still up (e.g. extraction failed -> no translation phase fired).
        _close_ebar()
        try:
            _clear_translation_job(_job_token)
        except Exception:
            pass
    # The picker/chooser already closed, so a failed background job would
    # otherwise be SILENT -- the user explicitly picked this sub and must know it
    # didn't land (and to try another). resolve() returns a path only on success;
    # a falsy result means extraction/translation deferred or failed. Not for a
    # gender-proofread job (translate.proofread_later): its subtitle is on
    # screen already, and None only means nothing was corrected.
    if not _resolved and not (link_codec.decode_link(link) or {}).get(
            'proofread_only'):
        try:
            # An extraction that STOPPED PART-WAY is not a failure -- it banked
            # its progress and the next pick continues from there, and it has
            # already told the user exactly that. Following it with "try another
            # subtitle" would contradict it and send the user away from a job
            # that is most of the way done (field report, 0.2.446).
            _partial = ''
            try:
                _raw = xbmcgui.Window(10000).getProperty(
                    'povil.embedded_partial') or ''
                xbmcgui.Window(10000).clearProperty('povil.embedded_partial')
                # Honour it only if it belongs to THIS attempt. The flag is a
                # window property, so a value left behind by an earlier run
                # would otherwise suppress a genuine failure toast much later,
                # for an unrelated video. A real one is written seconds ago.
                if '@' in _raw:
                    import time as _t
                    if _t.time() - float(_raw.rsplit('@', 1)[1]) <= 120:
                        _partial = _raw
            except Exception:
                _partial = ''
            if not _partial:
                _pl = link_codec.decode_link(link) or {}
                _msg = ('AI: לא ניתן היה לחלץ תרגום מובנה — נסו כתובית אחרת'
                        if _pl.get('type') == 'embedded_ai'
                        else 'AI: לא ניתן היה לתרגם — נסו כתובית אחרת')
                kodi.notify(_msg, time_ms=4500)
        except Exception:
            pass


def handle_translate_file(params):
    """Translate an SRT file to Hebrew on disk.

    Invoked by the DarkSubs engine.py hook via RunScript when the
    user picks a non-Hebrew subtitle from DarkSubs and has a Gemini
    key set. Reads input, translates, writes output, then touches
    a `.ai_done` sentinel next to the output so DarkSubs knows to
    pick it up instead of falling through to Google Translate.

    Params (base64-encoded so they survive RunScript's parameter
    parsing intact -- paths can contain commas, parens, quotes):
      input_b64  : path to source SRT
      output_b64 : path to write Hebrew SRT
    """
    import base64
    try:
        from resources.lib.ai_translation import service
        from resources.lib.core import kodi, selection as core_selection, settings, video_context
    except Exception as e:
        safe_log('translate_file: import failed: {0}'.format(e),
                  level='ERROR')
        return

    def _decode(b):
        try:
            return base64.b64decode(b.encode('ascii')).decode('utf-8')
        except Exception:
            return ''

    in_path = _decode(params.get('input_b64', ''))
    out_path = _decode(params.get('output_b64', ''))
    if not in_path or not out_path:
        safe_log('translate_file: missing input/output paths',
                  level='WARNING')
        return
    if not os.path.isfile(in_path):
        safe_log(
            'translate_file: input not found: {0}'.format(in_path),
            level='WARNING')
        return

    # Read source SRT.
    try:
        with open(in_path, 'r', encoding='utf-8', errors='replace') as f:
            src_text = f.read()
    except OSError as e:
        safe_log('translate_file: read failed: {0}'.format(e),
                  level='ERROR')
        return

    if not src_text.strip():
        safe_log('translate_file: source empty', level='WARNING')
        return

    # We don't have video info here (the hook is running inside
    # DarkSubs's process), so synthesize what we can. Cast metadata
    # and proper title come from VideoPlayer InfoLabels if the
    # video is currently playing; otherwise we degrade gracefully.
    info = video_context.current_video_info()

    # The source SRT's basename is the subtitle's real release name (e.g.
    # "Show.2026.1080p.WEBRip.x265-GRP") -- a far better Telegram filename than
    # the video's tokenized stream path. Pass it as the pool release; the Worker
    # falls back to the TMDB title if it doesn't look like a real release.
    try:
        info['release'] = os.path.splitext(os.path.basename(in_path))[0]
    except Exception:
        pass

    # Reuse the core orchestration: translate via a temp link payload
    # that points at the source file we already have. resolve() does
    # its own caching, chunking, Gemini calls, etc.
    import json
    import urllib.parse
    payload = {
        'type': 'ai',
        'source_lang': 'en',  # DarkSubs's auto_translate only fires
                              # on non-Hebrew; English is by far the
                              # common case and the prompt is robust
                              # to a misidentified source language.
        'local_path': in_path,
    }
    link = urllib.parse.quote(
        json.dumps(payload, ensure_ascii=False))
    # DarkSubs invoked this delivery outside MoranSubs's own picker, so bind
    # the timing job here. Without this, a status from an earlier subtitle
    # could be mistaken for the translation being produced now.
    core_selection.set_current_subtitle(link)
    _timing_selection = core_selection.current_subtitle_selection(
        expected_link=link)

    def _timing_selection_current():
        try:
            return bool(core_selection.subtitle_selection_matches(
                _timing_selection.get('token') or '',
                _timing_selection.get('link_hash') or '',
                _timing_selection.get('stream_hash') or ''))
        except Exception:
            return False

    if not _timing_selection_current():
        return

    translated_path = None
    # Same toast-milestone-driven pattern as _handle_download.
    # DialogProgressBG may be hidden behind DarkSubs's MySubs
    # picker, but the 25/50/75 % toast notifications always layer
    # above any window so the user gets clear progress feedback.
    progress = None
    try:
        progress = xbmcgui.DialogProgressBG()
        progress.create(dialogs_title(),
                        'תרגום AI מתחיל...')
    except Exception:
        progress = None

    _milestone_state = {'last': 0}

    def report(stage, total):
        try:
            from resources.lib.core import kodi as _ku
            pct = int(stage * 100 / max(1, total))
            if progress is not None:
                try:
                    progress.update(
                        pct, dialogs_title(),
                        _ku.localised(32631, stage, total))
                except Exception:
                    pass
            milestone = (pct // 25) * 25
            if (milestone in (25, 50, 75)
                    and milestone > _milestone_state['last']):
                _milestone_state['last'] = milestone
                try:
                    _ku.notify(
                        'AI: {0}% תורגם ({1}/{2} chunks)'.format(
                            milestone, stage, total),
                        time_ms=3500)
                except Exception:
                    pass
        except Exception:
            pass

    # Opt-in fast-first-chunk: write English to disk + touch sentinel
    # as soon as resolve() has read the source, so DarkSubs releases
    # in seconds. Hebrew swaps in via Player().setSubtitles() as each
    # chunk lands. Default OFF so the legacy "wait for full Hebrew"
    # behavior is unchanged unless the user opts in.
    whole_mode = settings.get_bool('whole_subtitle_request', False)
    fast_mode = (
        settings.get_bool('fast_first_chunk', False)
        and not whole_mode
    )

    # Pre-flight: the fast path briefly shows the SOURCE SRT to the
    # user (until the first Hebrew chunk arrives). That's acceptable
    # when the source is English -- most users can skim it for the
    # 10-15 seconds it takes Hebrew to start landing. But for
    # Spanish / Portuguese / German / French sources it's just
    # disorienting characters the user can't read. Detect the actual
    # language of the source text and downgrade non-English to the
    # slow path. NOTE: the payload above hardcodes source_lang='en'
    # because the AI prompt is robust to a misidentified language,
    # so we can't trust payload['source_lang'] here -- we have to
    # peek at the bytes.
    if fast_mode:
        try:
            from resources.lib.ai_translation.context import language_detect as _ld
            _peek_lang = _ld.detect(src_text[:8000])
            if _peek_lang and _peek_lang != 'en':
                fast_mode = False
                safe_log(
                    'translate_file: fast mode skipped (detected '
                    'src lang = {0}, not en)'.format(_peek_lang),
                    level='INFO')
        except Exception as _e:
            # Detection failure: err on the safe side and use slow.
            fast_mode = False
            safe_log(
                'translate_file: lang detect failed, falling '
                'back to slow: {0}'.format(_e), level='WARNING')

    if not fast_mode:
        try:
            translated_path = service.resolve(
                link, info, progress_cb=report,
                selection=_timing_selection)
        except Exception as e:
            safe_log('translate_file: resolve crashed: {0}'.format(e),
                      level='ERROR')
        finally:
            if progress is not None:
                try:
                    progress.close()
                except Exception:
                    pass

        if not translated_path or not os.path.isfile(translated_path):
            safe_log('translate_file: resolve returned nothing',
                      level='WARNING')
            return

        if not _timing_selection_current():
            return

        # translate.resolve() may have queued a deep timing job for this exact
        # file. Capture Kodi's current stream count before DarkSubs sees the
        # sentinel; the service worker is not allowed to verify/swap until the
        # hook has visibly registered and selected this foreground delivery.
        _delivery_before = core_selection.subtitle_sync_registration_baseline(
            translated_path, selection=_timing_selection)

        # Copy translated content to the output path DarkSubs expects.
        try:
            with open(translated_path, 'r', encoding='utf-8',
                      errors='replace') as f:
                hebrew = f.read()
            # Belt-and-suspenders: re-apply the display AND timing repairs
            # right before delivery. resolve() does this on cache hits too, but
            # applying it again here catches the case where the cache file
            # slipped through (e.g., a write race or a file the migration hasn't
            # reached yet) -- which is precisely when an unrepaired runaway cue
            # would reach the player, so the timing bound belongs here as much
            # as the punctuation one.
            try:
                from resources.lib.ai_translation.repairs import postfix_glyphs, postfix_leaks, postfix_rtl
                from resources.lib.core.srt import timing
                # One provenance answer, used by both text-DELETING repairs:
                # the Arabic strip and the source-echo strip. The glyph fold is
                # not gated -- it rewrites a character as itself -- but it must
                # run before fix_rtl_punctuation, which re-adds the RLE/PDF it
                # removes.
                _ai = postfix_glyphs.may_carry_arabic_leak(translated_path)
                _hb = postfix_glyphs.strip_leaked_arabic(hebrew) if _ai else hebrew
                if _ai:
                    _hb = postfix_leaks.strip_source_echo(_hb)
                hebrew = timing.clamp_cue_durations(
                    postfix_rtl.fix_rtl_punctuation(postfix_glyphs.normalize_glyphs(_hb)))
            except Exception:
                pass
            # Write atomically: temp file in same dir, then rename. This
            # avoids a half-written file being picked up by the hook.
            tmp_out = out_path + '.aitmp'
            with open(tmp_out, 'w', encoding='utf-8') as f:
                f.write(hebrew)
            os.replace(tmp_out, out_path)
        except OSError as e:
            safe_log('translate_file: write failed: {0}'.format(e),
                      level='ERROR')
            return

        if not _timing_selection_current():
            return

        # Touch the sentinel last -- the hook polls for it. Only after
        # the output is complete on disk.
        _sentinel_ready = False
        try:
            open(out_path + '.ai_done', 'w').close()
            _sentinel_ready = True
        except OSError as e:
            safe_log('translate_file: sentinel write failed: {0}'
                      .format(e), level='WARNING')
        if (_sentinel_ready and _delivery_before is not None
                and not core_selection.confirm_subtitle_sync_registration(
                translated_path, selection=_timing_selection,
                before=_delivery_before, timeout_ms=2000)):
            core_selection.abandon_subtitle_selection(_timing_selection)
        return

    # ---- fast_first_chunk path ------------------------------------
    # progressive_cb gets called from resolve() at three phases:
    #   first_ready  -- English source is ready; write it to out_path
    #                   and touch the sentinel so DarkSubs releases
    #                   immediately. User sees subtitles in ~3s
    #                   instead of 1-3 min.
    #   chunk_ready  -- a Hebrew chunk landed; write a versioned
    #                   .vN.srt to cache_dir and swap in via
    #                   Player().setSubtitles() so the user sees
    #                   Hebrew creep in as chunks complete.
    #   done         -- translation finished (success or failure);
    #                   we clear the active-translation Window prop.
    #                   On success the canonical cache file is
    #                   already saved by resolve() so the NEXT pick
    #                   shows [CACHE]. On failure we leave the
    #                   English on disk and the progressive .vN
    #                   files for TTL prune to clean up later --
    #                   we explicitly do NOT save a partial Hebrew
    #                   file to canonical cache.
    _job_token = _new_translation_job()
    _ver = {'n': 0, 'last_path': None}

    def on_phase(phase, payload):
        try:
            if (not _owns_translation_job(_job_token)
                    or not _timing_selection_current()):
                return
            if phase in ('chunk_ready', 'done'):
                win = xbmcgui.Window(10000)
                if (win.getProperty('ai_subs.live_translate_active') != '1'
                        or not payload.get('source_id')
                        or win.getProperty('ai_subs.live_translate_source')
                        != payload['source_id']):
                    return
            if phase == 'first_ready':
                _tmp = out_path + '.aitmp'
                with open(_tmp, 'w', encoding='utf-8') as _f:
                    _f.write(payload['fallback_text'])
                os.replace(_tmp, out_path)
                if (not _owns_translation_job(_job_token)
                        or not _timing_selection_current()):
                    return
                xbmcgui.Window(10000).setProperty(
                    'ai_subs.live_translate_active', '1')
                xbmcgui.Window(10000).setProperty(
                    'ai_subs.live_translate_source', payload['source_id'])
                try:
                    open(out_path + '.ai_done', 'w').close()
                except OSError as e:
                    safe_log('translate_file fast: sentinel write '
                              'failed: {0}'.format(e), level='WARNING')
                # Belt-and-suspenders: load the fallback ourselves
                # instead of trusting DarkSubs's post-hook code to
                # do it. Reports from users on v0.2.49 showed the
                # picker closing + our toast firing but no subtitle
                # ever appearing on screen -- consistent with
                # DarkSubs's caller either filtering on Hebrew chars
                # or relying on a code path that doesn't fire on the
                # fast return. Also force subtitle display on so a
                # disabled subtitle stream from a previous playback
                # doesn't suppress us.
                #
                # Brief polling because at this exact moment Kodi may
                # still be mid-handoff -- DarkSubs's hook just
                # returned, the player might be a few hundred ms away
                # from isPlayingVideo() flipping True. Up to 3s of
                # 250ms checks gives us a reasonable shot at landing
                # the setSubtitles call without blocking the
                # remainder of translation behind it.
                try:
                    _attempts = 0
                    while _attempts < 12:
                        if (not _owns_translation_job(_job_token)
                                or not _timing_selection_current()):
                            return
                        if xbmc.Player().isPlayingVideo():
                            core_selection.apply_subtitle_file(
                                out_path, selection=_timing_selection,
                                abandon_on_failure=False)
                            break
                        xbmc.sleep(250)
                        _attempts += 1
                except Exception as e:
                    safe_log(
                        'translate_file fast first_ready '
                        'setSubtitles raised: {0}'.format(e),
                        level='DEBUG')
                kodi.notify('AI: כתוביות מוכנות, מתרגם ברקע',
                                  time_ms=4000)
            elif phase == 'chunk_ready':
                # Two gates: active flag AND source_id match.
                # If the user picked a different subtitle while we're
                # translating, the new pick overwrites
                # 'ai_subs.live_translate_source'. A late chunk_ready
                # from the previous translation would otherwise swap
                # the player to its own .vN file, clobbering the new
                # pick. The source_id check pins each chunk_ready to
                # its originating translation.
                if (xbmcgui.Window(10000).getProperty(
                        'ai_subs.live_translate_active') != '1'
                        or xbmcgui.Window(10000).getProperty(
                            'ai_subs.live_translate_source')
                        != payload['source_id']):
                    return  # stale or user moved on; stop swapping
                # Alternate between two slot files instead of writing
                # a fresh v1, v2, v3... per chunk. Each setSubtitles
                # call ADDS a stream to Kodi's player list -- with N
                # chunks the user saw N Hebrew entries pile up in the
                # subtitle picker (the 10/10 screenshot). Cycling
                # 'a' and 'b' caps the streams at 2 during streaming
                # (plus _final after done) instead of N.
                slot = 'b' if _ver.get('slot', 'b') == 'a' else 'a'
                _ver['slot'] = slot
                ver_path = progressive_slot_path(
                    kodi.cache_dir(), payload['source_id'], slot,
                    payload.get('release') or '')
                _tmp = ver_path + '.aitmp'
                with open(_tmp, 'w', encoding='utf-8') as _f:
                    _f.write(payload['merged_text'])
                os.replace(_tmp, ver_path)
                _ver['last_path'] = ver_path
                if (not _owns_translation_job(_job_token)
                        or not _timing_selection_current()):
                    return
                try:
                    if xbmc.Player().isPlayingVideo():
                        if (not _owns_translation_job(_job_token)
                                or not _timing_selection_current()):
                            return
                        core_selection.apply_subtitle_file(
                            ver_path, selection=_timing_selection,
                            abandon_on_failure=False)
                except Exception as e:
                    safe_log(
                        'translate_file fast: setSubtitles raised: '
                        '{0}'.format(e), level='DEBUG')
            elif phase == 'done':
                # On success: swap to the FINAL canonical Hebrew so
                # the user isn't stranded on the last .vN file (which
                # may still hold English placeholders for chunks that
                # completed AFTER our final chunk_ready setSubtitles
                # call). Copy the canonical bytes to a FRESH versioned
                # path before calling setSubtitles -- Kodi caches the
                # parsed SRT per-path, so reusing the canonical path
                # directly may no-op silently on some Kodi builds.
                # The fresh `_final.he.srt` name guarantees a reload.
                _canonical_swap_succeeded = False
                if payload.get('success'):
                    try:
                        # USE THE PATH resolve() REPORTS. Recomputing it here
                        # was wrong twice over: translated_path() was called
                        # without tier=, while resolve() writes with tier='ar'
                        # whenever a gender reference was found (the default,
                        # and force-enabled by migration), and the source
                        # language was hardcoded 'en'. So this file did not
                        # exist on most jobs, the swap below was skipped, and
                        # the viewer kept the last progressive SLOT file --
                        # partly source text if the translation was
                        # interrupted. The recompute stays only as a fallback
                        # for a payload from an older resolve().
                        canonical = payload.get('path') or ''
                        if not canonical:
                            from resources.lib.core.cache import translation_cache as _cache
                            canonical = _cache.translated_path(
                                (info.get('imdb_id') or '').strip(),
                                info.get('season') or '',
                                info.get('episode') or '',
                                'en',
                                source_id=payload['source_id'])
                        if os.path.isfile(canonical):
                            # Name the delivered file after the source RELEASE so
                            # Kodi shows the full release name (not a hash); fall
                            # back to the source-id when no release is known.
                            _rel = kodi.safe_release_filename(
                                payload.get('release') or '')
                            _final_name = (
                                _rel + '.he.srt' if _rel
                                else 'progressive_{0}_final.he.srt'.format(
                                    payload['source_id']))
                            _final_path = os.path.join(
                                kodi.cache_dir(), _final_name)
                            try:
                                with open(canonical, 'rb') as _src_f:
                                    _bytes = _src_f.read()
                                _tmp_final = _final_path + '.aitmp'
                                with open(_tmp_final, 'wb') as _dst_f:
                                    _dst_f.write(_bytes)
                                os.replace(_tmp_final, _final_path)
                            except OSError as _we:
                                safe_log(
                                    'translate_file fast done copy '
                                    'failed: {0}'.format(_we),
                                    level='WARNING')
                                _final_path = None
                            if (_final_path and _owns_translation_job(_job_token)
                                    and _timing_selection_current()):
                                # NOT gated on isPlayingVideo -- if
                                # the user paused mid-translation,
                                # setSubtitles is still useful for
                                # the resume.
                                #
                                # try/except is NOT the only guard we
                                # need, which is what this comment used
                                # to claim. setSubtitles() posts to the
                                # VideoPlayer thread and returns before
                                # the stream is registered, so it not
                                # raising proves nothing -- and reading
                                # the stream list on the next line
                                # returned the PRE-add list, so the
                                # "most-recently-added stream" picked
                                # below was really the last progressive
                                # SLOT, which the cleanup then deleted.
                                # Count before, wait for the count to
                                # grow, and only then claim success.
                                try:
                                    p = xbmc.Player()
                                    try:
                                        _before = len(
                                            p.getAvailableSubtitleStreams() or [])
                                    except Exception:
                                        _before = -1
                                    if (not _owns_translation_job(_job_token)
                                            or not _timing_selection_current()):
                                        return
                                    p.setSubtitles(_final_path)
                                    p.showSubtitles(True)
                                    if not _timing_selection_current():
                                        return
                                    # Explicit stream selection: when
                                    # the user already had a Hebrew
                                    # SRT loaded BEFORE picking
                                    # English for AI translation,
                                    # Kodi's language preference can
                                    # auto-revert to the FIRST Hebrew
                                    # stream after our setSubtitles
                                    # adds a second one. Forcing the
                                    # most-recently-added stream
                                    # (always ours) pins the active
                                    # selection to the translation we
                                    # just produced.
                                    _grew = False
                                    if _before >= 0:
                                        for _ in range(20):      # <= 1s
                                            xbmc.sleep(50)
                                            if (not _owns_translation_job(
                                                    _job_token)
                                                    or not _timing_selection_current()):
                                                return
                                            try:
                                                _streams = (
                                                    p.getAvailableSubtitleStreams()
                                                    or [])
                                            except Exception:
                                                break
                                            if len(_streams) > _before:
                                                try:
                                                    if (not _owns_translation_job(
                                                            _job_token)
                                                            or not _timing_selection_current()):
                                                        return
                                                    p.setSubtitleStream(
                                                        len(_streams) - 1)
                                                    _grew = True
                                                except Exception:
                                                    _grew = False
                                                break
                                    if not _grew and not playing_now():
                                        # Nobody to strand: with no player
                                        # there is no stream pointing at a
                                        # slot file, so deleting is safe --
                                        # and is the outcome we want, or the
                                        # slots pile up on every job that
                                        # outlives playback.
                                        _grew = True
                                    elif not _grew:
                                        safe_log(
                                            'translate_file: Kodi did not '
                                            'register the final subtitle '
                                            'stream -- keeping the progressive '
                                            'slots rather than deleting a file '
                                            'the player may still be on',
                                            level='WARNING')
                                    _registered_and_pinned = _grew
                                    _canonical_swap_succeeded = _grew
                                    if (_registered_and_pinned
                                            and _timing_selection_current()):
                                        core_selection.mark_subtitle_delivery_applied(
                                            canonical,
                                            selection=_timing_selection)
                                        core_selection.confirm_subtitle_sync_fix(
                                            canonical,
                                            selection=_timing_selection)
                                except Exception as _se:
                                    safe_log(
                                        'translate_file fast done '
                                        'setSubtitles raised: {0}'
                                        .format(_se), level='DEBUG')
                    except Exception as _e:
                        safe_log(
                            'translate_file fast done canonical '
                            'swap failed: {0}'.format(_e),
                            level='DEBUG')
                # Cleanup ONLY when the canonical swap succeeded. On
                # failure / abort the user's only Hebrew lives in the
                # .vN files; deleting them would strand Kodi pointing
                # at a removed file = no subtitles for the rest of
                # playback. The 180-day TTL prune sweeps them up
                # eventually if we don't.
                if (_canonical_swap_succeeded
                        and _owns_translation_job(_job_token)
                        and _timing_selection_current()):
                    try:
                        import glob as _glob
                        # Patterns cover legacy (_v*), the hash-named
                        # _a/_b slots AND the release-named slots. The
                        # clean <release>.he.srt _final stays as the
                        # canonical stream Kodi is on.
                        _patterns = progressive_cleanup_patterns(
                            payload['source_id'],
                            payload.get('release') or '')
                        for _pat in _patterns:
                            for _stale in _glob.glob(
                                    os.path.join(
                                        kodi.cache_dir(),
                                        _pat)):
                                try:
                                    os.remove(_stale)
                                except OSError:
                                    pass
                    except Exception:
                        pass
                _clear_translation_job(_job_token)
        except Exception as _e:
            safe_log(
                'translate_file fast on_phase({0}) raised: {1}'.format(
                    phase, _e), level='WARNING')

    translated_path = None
    try:
        try:
            translated_path = service.resolve(
                link, info, progress_cb=report,
                progressive_cb=on_phase,
                selection=_timing_selection)
        except Exception as e:
            safe_log(
                'translate_file fast: resolve crashed: {0}'.format(e),
                level='ERROR')

        # Sentinel safety net. Two paths land us here without a
        # touched .ai_done:
        #   (1) resolve() hit an early cache return (translate.py
        #       cache-hit branches return BEFORE emitting first_ready)
        #       -- translated_path holds the cached Hebrew file.
        #   (2) first_ready threw on the fallback SRT write (disk
        #       full, permission denied) -- translated_path may
        #       still be None or, on later resolve() success, a path.
        # Either way, if no sentinel exists yet, DarkSubs would
        # poll for 300s and then give up. We avoid that by writing
        # whatever Hebrew we have to out_path and touching the
        # sentinel now.
        sentinel_path = out_path + '.ai_done'
        _delivery_before = None
        if not os.path.isfile(sentinel_path):
            try:
                if (translated_path
                        and os.path.isfile(translated_path)):
                    _delivery_before = (
                        core_selection.subtitle_sync_registration_baseline(
                            translated_path, selection=_timing_selection))
                    with open(translated_path, 'r',
                              encoding='utf-8',
                              errors='replace') as _f:
                        _content = _f.read()
                    try:
                        from resources.lib.ai_translation.repairs import postfix_glyphs, postfix_leaks, postfix_rtl
                        from resources.lib.core.srt import timing
                        # Same shape as the delivery repair above, same reasons.
                        _ai = postfix_glyphs.may_carry_arabic_leak(translated_path)
                        _cb = (postfix_glyphs.strip_leaked_arabic(_content)
                               if _ai else _content)
                        if _ai:
                            _cb = postfix_leaks.strip_source_echo(_cb)
                        _content = timing.clamp_cue_durations(
                            postfix_rtl.fix_rtl_punctuation(
                                postfix_glyphs.normalize_glyphs(_cb)))
                    except Exception:
                        pass
                    _tmp_out = out_path + '.aitmp'
                    with open(_tmp_out, 'w',
                              encoding='utf-8') as _f:
                        _f.write(_content)
                    os.replace(_tmp_out, out_path)
                # Touch sentinel even if we couldn't write a usable
                # output -- letting DarkSubs's hook return quickly
                # is strictly better than the 300s hang.
                open(sentinel_path, 'w').close()
                if (_delivery_before is not None
                        and translated_path
                        and _timing_selection_current()):
                    if not core_selection.confirm_subtitle_sync_registration(
                        translated_path, selection=_timing_selection,
                            before=_delivery_before, timeout_ms=2000):
                        core_selection.abandon_subtitle_selection(
                            _timing_selection)
            except OSError as _e:
                safe_log(
                    'translate_file fast: post-resolve sentinel '
                    'recovery failed: {0}'.format(_e),
                    level='ERROR')
    finally:
        if progress is not None:
            try:
                progress.close()
            except Exception:
                pass
        try:
            _clear_translation_job(_job_token)
        except Exception:
            pass
