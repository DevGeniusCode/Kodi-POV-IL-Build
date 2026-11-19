# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Delivery to the player: the download handler, named delivery of a subtitle (unique name so
# Kodi reloads it), the fast first chunk and the fall-back to the next Hebrew subtitle.

import os
import shutil
try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None
from resources.lib.ui.dialogs import _safe_log, _title


def _encode_selection_b64(selection):
    """Serialize only opaque selection hashes/tokens for a RunScript baton."""
    try:
        import base64
        import json
        safe = {k: (selection or {}).get(k, '')
                for k in ('token', 'link_hash', 'stream_hash')}
        raw = json.dumps(safe, separators=(',', ':')).encode('utf-8')
        return base64.b64encode(raw).decode('ascii')
    except Exception:
        return ''


def _decode_selection_b64(value):
    try:
        import base64
        import json
        raw = base64.b64decode((value or '').encode('ascii')).decode('utf-8')
        data = json.loads(raw)
        return {k: (data.get(k) or '')
                for k in ('token', 'link_hash', 'stream_hash')}
    except Exception:
        return {}


def _handle_download(handle, params):
    """User picked one of our entries -- deliver the SRT path."""
    from resources.lib.ai_translation import service
    from resources.lib.ai_translation.proofread import gp_jobs
    from resources.lib.ai_translation import source_prep
    from resources.lib.core import kodi, selection as core_selection, settings, video_context, link_codec

    # Invalidate any older background timing job immediately, before handling
    # file-less embedded branches. Otherwise an old worker on this same movie
    # could finish after the click and replace the user's newer choice.
    try:
        from resources.lib.sync import jobs as _subsync
        _subsync.cancel_pending()
    except Exception:
        pass

    link = params.get('link', '')
    info = video_context.current_video_info()

    # Remember this as the currently-applied subtitle so the picker marks it
    # '» נוכחית' next time it opens.
    try:
        core_selection.set_current_subtitle(link)
    except Exception:
        pass
    try:
        _timing_selection = core_selection.current_subtitle_selection(
            expected_link=link)
    except Exception:
        _timing_selection = {}

    def _timing_selection_current():
        try:
            return bool(core_selection.subtitle_selection_matches(
                _timing_selection.get('token') or '',
                _timing_selection.get('link_hash') or '',
                _timing_selection.get('stream_hash') or ''))
        except Exception:
            return False

    # If the user picked an engine "translate from <lang>" entry, download the
    # source sub NOW, then continue as a local 'ai' link. This lets the fast
    # path below deliver the source immediately (dialog closes) and translate
    # in the background -- instead of blocking the dialog open for the whole
    # 1-2 minute translation.
    try:
        _p = link_codec._decode_link(link)
    except Exception:
        _p = None

    # Embedded Hebrew pick: there is no file to hand back. Switch Kodi's
    # subtitle stream and CLOSE the dialog, then end with no item -- copied
    # EXACTLY from DarkSubs (main.py): Dialog.Close(all,true) -> empty
    # addDirectoryItems -> xbmc.sleep(100) -> endOfDirectory(updateListing=True,
    # cacheToDisc=True). The sleep(100) is essential: it lets the dialog finish
    # closing BEFORE endOfDirectory, otherwise Kodi shows "download failed".
    if _p and _p.get('type') == 'engine' and _p.get('embedded'):
        _embedded_selected = False
        try:
            from resources.lib.fetching.embedded import streams
            # Pass the picked language through. It was being dropped here and
            # only here, which left the out-of-range fallback inside
            # select_embedded re-finding a HEBREW stream for a '[מובנה] EN'
            # pick.
            if (_timing_selection_current()
                    and streams.select_embedded(
                        _p.get('stream_index'), lang=_p.get('lang'))
                    and _timing_selection_current()):
                _embedded_selected = True
                core_selection.set_subtitle_sync_status(
                    'confirmed', source='embedded',
                    selection_token=_timing_selection.get('token') or '',
                    link_hash=_timing_selection.get('link_hash') or '',
                    stream_hash=_timing_selection.get('stream_hash') or '')
                kodi.notify('כתובית עברית מובנה הופעלה', time_ms=3000)
        except Exception as _e:
            _safe_log('embedded select failed: {0}'.format(_e), level='WARNING')
        if not _embedded_selected:
            core_selection.abandon_subtitle_selection(_timing_selection)
        try:
            xbmc.executebuiltin('Dialog.Close(all,true)')
            xbmcplugin.addDirectoryItems(handle, [], 0)
            xbmc.sleep(100)
            xbmcplugin.endOfDirectory(handle, updateListing=True,
                                      cacheToDisc=True)
        except Exception:
            try:
                xbmcplugin.endOfDirectory(handle)
            except Exception:
                pass
        return

    if _p and _p.get('type') == 'engine_ai':
        eng_path = None
        try:
            from resources.lib.fetching.embedded import streams
            kodi.notify('AI: מוריד את כתובית המקור...', time_ms=2500)
            eng_path = streams.download(_p)
        except Exception as _e:
            _safe_log('engine_ai source download failed: {0}'.format(_e),
                      level='ERROR')
        if eng_path and os.path.isfile(eng_path):
            link = link_codec._encode_link({
                'type': 'ai',
                'source_lang': _p.get('src_lang') or 'en',
                'local_path': eng_path,
                'force_ai': True,
            })
        else:
            kodi.notify('AI: לא ניתן היה להוריד את כתובית המקור',
                              time_ms=4000)
            xbmcplugin.endOfDirectory(handle)
            return

    if _p and _p.get('type') == 'embedded_ai':
        # Extraction of a scattered debrid remux takes minutes, so run the WHOLE
        # embedded->Hebrew job in the BACKGROUND (like an 'ai' pick) instead of
        # freezing Kodi's download dialog: close the dialog now and fire
        # bg_translate_picker with the embedded_ai link -- its resolve() extracts
        # (showing a corner progress bar) THEN translates, swapping the Hebrew in
        # when ready. No pre-extracted source id yet, so pass an empty one (the
        # bg handler adopts the id resolve() computes). Fail-open: if extraction
        # yields nothing the bg job just delivers nothing (external path remains).
        # Show the embedded SOURCE track now (native, instant + already synced to
        # the video) so the user sees it while the Hebrew cooks -- instead of
        # leaving the stale sub they picked embedded to replace. Best-effort.
        try:
            from resources.lib.fetching.embedded import streams as _seb
            _si = _p.get('stream_index')
            if _si is not None and _timing_selection_current():
                _seb.select_embedded(_si, lang=_p.get('src_lang') or 'en')
        except Exception:
            pass
        try:
            # A source-track switch can yield to Kodi. If another subtitle was
            # selected meanwhile, close this old picker cleanly but do not fire
            # a stale extraction/translation job.
            if _timing_selection_current():
                import base64 as _b64m
                _lk = _b64m.b64encode(link.encode('utf-8')).decode('ascii')
                _sd = _b64m.b64encode(b'').decode('ascii')
                _ss = _encode_selection_b64(_timing_selection)
                xbmc.executebuiltin(
                    'RunScript(service.subtitles.kodipovilai,'
                    'action=bg_translate_picker,link_b64={0},source_id_b64={1},'
                    'selection_b64={2})'.format(_lk, _sd, _ss))
                kodi.notify(
                    'AI: מחלץ ומתרגם תרגום מובנה ברקע', time_ms=3500)
        except Exception as _e:
            _safe_log('embedded_ai bg fire failed: {0}'.format(_e),
                      level='WARNING')
        try:
            xbmc.executebuiltin('Dialog.Close(all,true)')
            xbmcplugin.addDirectoryItems(handle, [], 0)
            xbmc.sleep(100)
            xbmcplugin.endOfDirectory(handle, updateListing=True,
                                      cacheToDisc=True)
        except Exception:
            try:
                xbmcplugin.endOfDirectory(handle)
            except Exception:
                pass
        return

    # Opt-in fast path for the NATIVE Kodi subtitle picker. Mirrors
    # the DarkSubs fast_first_chunk flow in _handle_translate_file:
    # deliver the English source to Kodi immediately and continue
    # translating in a separate fire-and-forget RunScript invocation
    # (the picker subprocess ends at endOfDirectory). On any internal
    # failure we fall through to the legacy slow flow below so the
    # user always gets SOMETHING.
    try:
        whole_mode = settings.get_bool('whole_subtitle_request', False)
        fast_mode = (
            settings.get_bool('fast_first_chunk', False)
            and not whole_mode
        )
    except Exception:
        fast_mode = False
    if fast_mode:
        try:
            if _try_fast_download(
                    handle, link, info, selection=_timing_selection):
                return  # endOfDirectory was called inside the helper
        except Exception as _e:
            _safe_log('fast_download outer guard caught: {0}'
                      .format(_e), level='WARNING')

    # AI translation links: CLOSE the dialog immediately and translate in the
    # background, instead of blocking the picker for the full 1-2 minute
    # translation. This matches the "בחר כתוביות" chooser window: you click,
    # the dialog closes, Hebrew swaps in progressively as it translates. The
    # picker subprocess ends at endOfDirectory, so the work is handed to a
    # separate RunScript (bg_translate_picker), exactly like the fast path's
    # background step -- just without delivering an English fallback first.
    try:
        _ai_payload = link_codec._decode_link(link)
    except Exception:
        _ai_payload = None
    if _ai_payload and _ai_payload.get('type') == 'ai':
        try:
            import base64 as _b64m
            _sid = source_prep._source_id_for_ai(_ai_payload) or ''
            _lk = _b64m.b64encode(link.encode('utf-8')).decode('ascii')
            _sd = _b64m.b64encode(_sid.encode('utf-8')).decode('ascii')
            _ss = _encode_selection_b64(_timing_selection)
            xbmc.executebuiltin(
                'RunScript(service.subtitles.kodipovilai,'
                'action=bg_translate_picker,link_b64={0},source_id_b64={1},'
                'selection_b64={2})'.format(_lk, _sd, _ss))
            try:
                kodi.notify('AI: מתרגם לעברית ברקע', time_ms=3500)
            except Exception:
                pass
        except Exception as _e:
            _safe_log('ai bg fire failed: {0}'.format(_e), level='WARNING')
        # Close the dialog now with no item -- same proven sequence as the
        # embedded-Hebrew pick (Dialog.Close -> empty -> sleep -> endOfDirectory).
        try:
            xbmc.executebuiltin('Dialog.Close(all,true)')
            xbmcplugin.addDirectoryItems(handle, [], 0)
            xbmc.sleep(100)
            xbmcplugin.endOfDirectory(handle, updateListing=True,
                                      cacheToDisc=True)
        except Exception:
            try:
                xbmcplugin.endOfDirectory(handle)
            except Exception:
                pass
        return

    # DialogProgressBG (bottom-right banner) PLUS milestone toasts.
    # The toasts at 25/50/75 % are the guaranteed-visible backup --
    # DialogProgressBG can be hidden behind a full-screen window
    # (DarkSubs MySubs, the video OSD) but toasts always layer on
    # top of everything. v0.2.42-0.2.44 tried a custom WindowDialog
    # overlay; that approach kept failing in various ways (hidden
    # behind pyxbmct, then all-black render, then thread-update
    # blackhole) so we reverted to the simple toast-driven pattern
    # that worked reliably in v0.2.41.
    progress = None
    try:
        progress = xbmcgui.DialogProgressBG()
        progress.create(_title(),
                        'AI Hebrew')
    except Exception:
        progress = None

    _milestone_state = {'last': 0}

    def report(stage, total):
        try:
            pct = int(stage * 100 / max(1, total))
            if progress is not None:
                try:
                    progress.update(
                        pct, _title(),
                        kodi.localised(32631, stage, total))
                except Exception:
                    pass
            # Milestone toasts -- always visible above any window.
            milestone = (pct // 25) * 25
            if (milestone in (25, 50, 75)
                    and milestone > _milestone_state['last']):
                _milestone_state['last'] = milestone
                try:
                    kodi.notify(
                        'AI: {0}% תורגם ({1}/{2} chunks)'.format(
                            milestone, stage, total),
                        time_ms=3500)
                except Exception:
                    pass
        except Exception:
            pass

    try:
        path = service.resolve(
            link, info, progress_cb=report, selection=_timing_selection)
    except Exception as e:
        _safe_log('resolve crashed: {0}'.format(e), level='ERROR')
        path = None
    finally:
        if progress is not None:
            try:
                progress.close()
            except Exception:
                pass

    # If the picked subtitle failed to download (e.g. Ktuvit rate-limited),
    # don't leave the user stuck on it -- automatically try the NEXT available
    # ready Hebrew subtitles and deliver the first that works.
    fallback_selection = None
    if (not (path and os.path.isfile(path))
            and _timing_selection_current()):
        try:
            fallback = _try_next_hebrew(
                link, info, owner_selection=_timing_selection)
            if fallback:
                path, link, fallback_selection = fallback
                _timing_selection = fallback_selection
        except Exception as e:
            _safe_log('next-hebrew fallback failed: {0}'.format(e),
                      level='WARNING')

    if (path and os.path.isfile(path)
            and _timing_selection_current()):
        # Internet-stream workaround: hand the SRT to the player ourselves
        # under a meaningful, unique name so it doesn't collapse into the
        # generic shared "TempSubtitle" file Kodi would create. Falls back
        # to the normal addDirectoryItem flow on local playback or failure.
        delivered = False
        try:
            delivered = _deliver_named_subtitle(
                handle, path, link, info, selection=_timing_selection)
        except Exception as _e:
            _safe_log('named subtitle delivery failed: {0}'.format(_e),
                      level='WARNING')
            delivered = False
        if delivered:
            # a pooled AI translation: its gender proofread in the background
            gp_jobs.proofread_later(link, _timing_selection)
            return  # endOfDirectory already called inside the helper
        if not _timing_selection_current():
            xbmcplugin.endOfDirectory(handle)
            return
        listitem = xbmcgui.ListItem(label=path)
        registration_before = core_selection.subtitle_sync_registration_baseline(
            path, selection=_timing_selection)
        added = xbmcplugin.addDirectoryItem(
            handle=handle, url=path, listitem=listitem, isFolder=False)
        xbmcplugin.endOfDirectory(handle)
        registration_confirmed = True
        if added is not False and registration_before is not None:
            registration_confirmed = core_selection.confirm_subtitle_sync_registration(
                path, selection=_timing_selection,
                before=registration_before)
        if added is False or not registration_confirmed:
            core_selection.abandon_subtitle_selection(_timing_selection)
        else:
            gp_jobs.proofread_later(link, _timing_selection)
        return
    core_selection.abandon_subtitle_selection(_timing_selection)
    xbmcplugin.endOfDirectory(handle)


def _sanitise_sub_name(name):
    """Make a release string safe as a filename and clean as a Kodi
    subtitle label: drop directories/extensions, replace path/reserved
    characters with dots, collapse runs, and cap the length."""
    if not name:
        return ''
    try:
        name = os.path.basename(str(name))
    except Exception:
        name = str(name)
    low = name.lower()
    for ext in ('.srt', '.ssa', '.ass', '.sub', '.vtt', '.txt'):
        if low.endswith(ext):
            name = name[:-len(ext)]
            break
    out = []
    for ch in name:
        if ch.isalnum() or ch in ' .,_-()[]':
            out.append(ch)
        else:
            out.append('.')
    cleaned = ''.join(out)
    while '..' in cleaned:
        cleaned = cleaned.replace('..', '.')
    while '  ' in cleaned:
        cleaned = cleaned.replace('  ', ' ')
    cleaned = cleaned.strip(' .')
    if len(cleaned) > 80:
        cleaned = cleaned[:80].strip(' .')
    return cleaned


def _playing_now():
    """True when Kodi still has a video player. Used to decide whether a failed
    canonical swap is dangerous. With a player up, deleting the progressive
    slots can strand the viewer on a removed file; with no player there is
    nothing pointing at them and the cleanup should proceed."""
    try:
        return bool(xbmc.Player().isPlayingVideo())
    except Exception:
        return False


def _progressive_slot_path(cache_dir, source_id, slot, release=''):
    """Path for a transient progressive-translation slot file. Kodi
    turns the basename into the subtitle label shown in the picker, so
    when we know the source RELEASE we name the slot after it (e.g.
    'Incendies.2010.720p.BluRay.a.he.srt' -> label 'Incendies...720p.BluRay.a')
    instead of the opaque 'progressive_<hash>_a' the clean addon used to
    show mid-translation. The '.a'/'.b' slot suffix is what lets us
    alternate two paths (so Kodi re-parses and the stream count stays
    capped at 2); it's only visible while translating -- the 'done'
    phase swaps in the clean '<release>.he.srt'. Falls back to the hash
    name when no release is available."""
    try:
        from resources.lib.core import kodi
        rel = kodi.safe_release_filename(release or '')
    except Exception:
        rel = ''
    if rel:
        name = '{0}.{1}.he.srt'.format(rel, slot)
    else:
        name = 'progressive_{0}_{1}.he.srt'.format(source_id, slot)
    return os.path.join(cache_dir, name)


def _progressive_cleanup_patterns(source_id, release=''):
    """Glob patterns matching every transient slot file we may have
    written for one translation -- both the hash-named fallback and the
    release-named slots, plus the legacy _v* names from older builds.
    The clean '<release>.he.srt' final is deliberately NOT matched so it
    survives the post-'done' cleanup."""
    pats = [
        'progressive_{0}_v*.he.srt'.format(source_id),
        'progressive_{0}_a.he.srt'.format(source_id),
        'progressive_{0}_b.he.srt'.format(source_id),
    ]
    try:
        from resources.lib.core import kodi
        rel = kodi.safe_release_filename(release or '')
    except Exception:
        rel = ''
    if rel:
        # safe_release_filename deliberately KEEPS brackets, and glob reads
        # '[YTS.MX]' as a character class -- so the literal slot file for a
        # bracketed release matched nothing and was never deleted. Escaping
        # makes the release part literal; the '*' in the hash-named patterns
        # above is ours and stays a wildcard.
        import glob as _g
        rel_lit = _g.escape(rel)
        pats.append('{0}.a.he.srt'.format(rel_lit))
        pats.append('{0}.b.he.srt'.format(rel_lit))
    return pats


def _subtitle_display_name(link, info, path):
    """A human-readable, filesystem-safe release name for the subtitle
    we're about to hand to the player. Kodi turns the file's basename
    into the label shown in the subtitle list, so we want the release
    name (e.g. 'Incendies.2010.720p.BluRay.x264') instead of the generic
    'TempSubtitle'. Engine/passthrough entries carry the real release
    name; AI/pool entries only have an internal cache key, so those use
    the movie/episode title. Returns '' when nothing sane is available
    (caller then falls back to the normal flow)."""
    from resources.lib.core import link_codec
    try:
        payload = link_codec._decode_link(link) or {}
    except Exception:
        payload = {}
    kind = payload.get('type')

    def _title_name():
        title = (info.get('title') or '').strip()
        if not title:
            return ''
        if info.get('is_episode') and info.get('season') and \
                info.get('episode'):
            try:
                return '{0}.S{1:02d}E{2:02d}'.format(
                    title, int(info.get('season')),
                    int(info.get('episode')))
            except (TypeError, ValueError):
                return title
        year = (info.get('year') or '').strip()
        return '{0}.{1}'.format(title, year) if year else title

    candidates = []
    # Engine (Ktuvit human / machine-translated) and passthrough carry
    # the real release name in the picked entry -- the most informative.
    if payload.get('filename'):
        candidates.append(payload['filename'])
    if kind == 'passthrough':
        candidates.append(os.path.basename(path))
    # AI / pool / fallback: the on-disk name is an internal key, so the
    # title is far more useful as a label.
    candidates.append(_title_name())
    candidates.append(os.path.basename(path))

    for c in candidates:
        cleaned = _sanitise_sub_name(c)
        if cleaned:
            return cleaned
    return ''


def _deliver_named_subtitle(handle, path, link, info, selection=None):
    """Internet-stream workaround for two Kodi-core subtitle behaviours.

    When the playing file is an HTTP(S) stream (debrid/CDN), Kodi's
    download dialog (GUIDialogSubtitles::OnDownloadComplete) ALWAYS copies
    the returned file to special://temp/TempSubtitle.he.srt -- a single
    shared name derived from the (missing) stream filename, NOT from the
    file we hand back. Two consequences the user hit:
      * every Hebrew sub shows up as the indistinguishable label
        "Hebrew - TempSubtitle (External)" (bug #2);
      * the destination path is identical every time, so repeated
        downloads overwrite the same file and Kodi dedups the external
        stream by path -- only the first one or two ever appear and the
        3rd+ silently vanish even though we report success (bug #3).

    We sidestep BOTH by copying our resolved SRT to a uniquely- and
    MEANINGFULLY-named file (the release name) and handing it straight to
    the player via setSubtitles() -- exactly how the AI/progressive path
    already delivers -- then closing the dialog with no item (the same
    sequence the embedded-Hebrew pick uses). Kodi derives the external
    stream label from the basename, so the user sees e.g.
    "Hebrew - Incendies.2010.720p.BluRay (External)" and each distinct
    release becomes its own persistent stream.

    Returns True if we delivered (endOfDirectory already called), or False
    to let the caller fall back to the normal addDirectoryItem path
    (local playback, no active player, or any failure)."""
    from resources.lib.core import kodi, selection as core_selection
    if xbmc is None:
        return False
    expected = selection or {}

    def _still_current():
        try:
            return bool(core_selection.subtitle_selection_matches(
                expected.get('token') or '',
                expected.get('link_hash') or '',
                expected.get('stream_hash') or ''))
        except Exception:
            return False

    if not _still_current():
        return False
    # Only internet streams hit the TempSubtitle path. Local playback gets
    # a sane video-derived name from Kodi (and may be stored alongside the
    # video), so leave that flow completely untouched.
    try:
        playing = (xbmc.Player().getPlayingFile() or '').strip()
    except Exception:
        playing = ''
    pl = playing.lower()
    if not (pl.startswith('http://') or pl.startswith('https://')):
        return False
    try:
        if not xbmc.Player().isPlayingVideo():
            return False
    except Exception:
        return False
    name = _subtitle_display_name(link, info, path)
    if not name:
        return False
    dest = os.path.join(kodi.cache_dir(), name + '.he.srt')
    try:
        if os.path.abspath(dest) != os.path.abspath(path):
            shutil.copyfile(path, dest)
    except OSError as _e:
        _safe_log('named subtitle copy failed: {0}'.format(_e),
                  level='WARNING')
        return False
    if not _still_current():
        return False
    if not core_selection.apply_subtitle_file(
            dest, selection=expected, fix_path=path,
            abandon_on_failure=False):
        _safe_log('named subtitle setSubtitles failed or became stale',
                  level='WARNING')
        return False
    # Close the dialog cleanly with no item -- same sequence the embedded
    # pick uses (copied from DarkSubs): the sleep(100) lets the dialog
    # finish closing before endOfDirectory so Kodi doesn't flash a
    # spurious "download failed".
    try:
        xbmc.executebuiltin('Dialog.Close(all,true)')
        xbmcplugin.addDirectoryItems(handle, [], 0)
        xbmc.sleep(100)
        xbmcplugin.endOfDirectory(handle, updateListing=True,
                                  cacheToDisc=True)
    except Exception:
        try:
            xbmcplugin.endOfDirectory(handle)
        except Exception:
            pass
    return True


def _try_next_hebrew(failed_link, info, owner_selection=None):
    """The user's picked subtitle failed -- fall back to the next ready Hebrew
    options so they aren't stuck on the failed one. Re-lists candidates and
    resolves the next non-AI, non-embedded Hebrew entries (engine human / pool /
    passthrough) until one downloads. AI (slow) and embedded (no file) are
    skipped here. Returns a path or None. Bounded so it can't hang."""
    from resources.lib.ai_translation import service
    from resources.lib.fetching import service as fetching_service
    from resources.lib.core import kodi, selection as core_selection, link_codec
    try:
        failed = link_codec._decode_link(failed_link) or {}
    except Exception:
        failed = {}
    # Skip the WHOLE source that just failed -- other subs from it (e.g. more
    # Ktuvit entries) fail identically, so jump straight to the next source.
    failed_sources = set()
    if failed.get('source'):
        failed_sources.add(failed.get('source'))
    try:
        candidates = fetching_service.list_candidates(info, modal_progress=False)
    except Exception:
        return None
    tried = 0
    owned = dict(owner_selection or {})

    def _owns_current():
        try:
            return bool(core_selection.subtitle_selection_matches(
                owned.get('token') or '', owned.get('link_hash') or '',
                owned.get('stream_hash') or ''))
        except Exception:
            return False

    for c in candidates:
        if tried >= 8:
            break
        if c.get('language') != 'he':
            continue  # only fall back to another HEBREW subtitle
        link2 = c.get('link')
        if not link2:
            continue
        try:
            p2 = link_codec._decode_link(link2) or {}
        except Exception:
            continue
        src = p2.get('source')
        if src and src in failed_sources:
            continue  # this source already failed -- don't retry its siblings
        kind = p2.get('type')
        if kind not in ('engine', 'pool', 'passthrough'):
            continue  # skip AI (slow) + foreign 'engine_ai'
        if kind == 'engine' and p2.get('embedded'):
            continue  # embedded delivers no file
        tried += 1
        # A fallback may take seconds. A manual pick made while it runs owns
        # the player, so stop instead of replacing it with the next fallback.
        if not _owns_current():
            return None
        try:
            # The fallback becomes the real selected candidate. Set it before
            # resolve() so a queued SubSync result is attached to this row.
            core_selection.set_current_subtitle(link2)
            selection = core_selection.current_subtitle_selection(
                expected_link=link2)
            owned = dict(selection or {})
            if not _owns_current():
                return None
            path = service.resolve(
                link2, info, selection=selection)
        except Exception:
            path = None
        if not _owns_current():
            return None
        if (path and os.path.isfile(path)
                and core_selection.subtitle_selection_matches(
                    selection.get('token') or '',
                    selection.get('link_hash') or '',
                    selection.get('stream_hash') or '')):
            try:
                kodi.notify('הכתובית הקודמת נכשלה — נטענה הבאה בתור',
                                  time_ms=4000)
            except Exception:
                pass
            return path, link2, selection
        if src:
            failed_sources.add(src)  # this source failed too -- skip its siblings
    return None


def _try_fast_download(handle, link, info, selection=None):
    """Native-picker fast path. Returns True on success
    (endOfDirectory was called). Returns False to mean 'fall
    through to the legacy slow flow' for any case the fast path
    cannot handle (non-AI link, source missing, cache resolution
    failure).

    Pattern: two RunScript invocations with a base64-encoded
    link baton. This invocation (the picker subprocess) writes
    the English fallback SRT, hands it to Kodi via
    endOfDirectory(), then fires a fire-and-forget RunScript
    with action=bg_translate_picker that continues the Hebrew
    translation in its own process. We can't keep working in
    this subprocess because endOfDirectory ends it."""
    import base64
    try:
        from resources.lib.ai_translation.providers import google_flow
        from resources.lib.ai_translation import source_prep
        from resources.lib.sync import delivery_gate
        from resources.lib.ai_translation.repairs import postfix_cleanup
        from resources.lib.core.srt import blocks
        from resources.lib.core import kodi, selection as core_selection, settings, link_codec
        from resources.lib.core.cache import translation_cache as _cache
    except Exception:
        return False

    payload = link_codec._decode_link(link)
    if not payload or payload.get('type') != 'ai':
        # passthrough / pool entries are short -- the existing path
        # handles them fine.
        return False

    source_lang = payload.get('source_lang') or 'en'
    local_source = payload.get('local_path')
    imdb_id = (info.get('imdb_id') or '').strip()
    season  = info.get('season') or ''
    episode = info.get('episode') or ''

    # Fast path briefly shows the SOURCE SRT to the user (until the
    # first Hebrew chunk arrives). English is broadly readable in
    # our user base; Spanish / Portuguese / German / French are not.
    # For non-English sources fall through to the legacy slow path
    # so the user only ever sees Hebrew (after a longer wait).
    # Unlike the DarkSubs path where we hardcode source_lang='en',
    # here the payload carries the actual configured source language.
    if source_lang != 'en':
        return False

    source_id = source_prep._source_id_for_ai(payload)

    # Cache hit fast path: serve cached Hebrew immediately. The
    # cache key here matches what list_candidates uses for its
    # [CACHE] marker, so a [CACHE]-marked entry resolves with
    # zero AI work.
    if source_id:
        try:
            # DELIBERATELY tier-pinned, and deliberately allowed to miss.
            # This branch hands a file straight to Kodi without any of the
            # checks resolve() applies on a cache hit -- the _is_mostly_hebrew
            # self-heal that deletes an empty/source-echoed file, the mtime
            # refresh that keeps a file in use from ageing out, the RTL
            # re-apply, and the one-shot pool backfill. Making it find a
            # translation it used to miss would turn a rare shortcut into the
            # normal path and skip all four.
            #
            # A miss is handled by resolve() in the background. Its validated
            # early return is delivered by the background handler's completion
            # path, with the same ownership and player safeguards as fresh work.
            cached = _cache.translated_path(
                imdb_id, season, episode, source_lang,
                source_id=source_id)
            # A Google Translate fallback that AI can now replace goes to
            # resolve(), which translates it with a live preview -- when
            # resolve() looks in this same plain slot, i.e. with the gender
            # reference off. With it on, resolve() reads the 'ar' slot only,
            # so this file stays the instant hit it was.
            _upgrade = (google_flow._google_cache_upgradeable(cached)
                        and not settings.get_bool('gender_ref_arabic', False))
            if os.path.isfile(cached) and not _upgrade:
                # A cached translation keeps its SOURCE subtitle's timing. Run
                # the delivery through the same release-aware gate as resolve;
                # any correction is a copy, never a rewrite of the AI cache.
                cached_delivery = delivery_gate._sync_hebrew_delivery(
                    info, cached,
                    source_release=(payload.get('release') or ''),
                    embedded_timing=bool(payload.get('embedded')),
                    selection=selection)
                listitem = xbmcgui.ListItem(label=cached_delivery)
                registration_before = (
                    core_selection.subtitle_sync_registration_baseline(
                        cached_delivery, selection=selection))
                added = xbmcplugin.addDirectoryItem(
                    handle=handle, url=cached_delivery,
                    listitem=listitem, isFolder=False)
                xbmcplugin.endOfDirectory(handle)
                registration_confirmed = True
                if added is not False and registration_before is not None:
                    try:
                        registration_confirmed = (
                            core_selection.confirm_subtitle_sync_registration(
                            cached_delivery, selection=selection,
                            before=registration_before))
                    except Exception:
                        registration_confirmed = False
                if added is False or not registration_confirmed:
                    core_selection.abandon_subtitle_selection(selection)
                    try:
                        kodi.notify(
                            'הפעלת הכתובית מהמטמון נכשלה',
                            time_ms=3500)
                    except Exception:
                        pass
                else:
                    try:
                        kodi.notify(
                            'AI: כתוביות מ-cache (תרגום קודם)',
                            time_ms=3000)
                    except Exception:
                        pass
                return True
        except Exception as _e:
            _safe_log('fast_download cache check failed: {0}'
                      .format(_e), level='WARNING')

    # No cache hit. Read the source SRT inline (instant -- local file
    # alongside the video or a temp file from another addon).
    src_text = None
    try:
        if local_source and os.path.isfile(local_source):
            with open(local_source, 'r', encoding='utf-8',
                      errors='replace') as f:
                src_text = f.read()
    except Exception as _e:
        _safe_log('fast_download source read failed: {0}'
                  .format(_e), level='WARNING')

    if not src_text:
        # Fall through; existing slow path will surface the error.
        return False

    # HI-strip guard mirroring translate.py:518-521. We're delivering
    # the English source as a fallback, and Hebrew chunks will land
    # later from the BG translation -- those run through the same
    # cleaner. If the cleaner ate too much we keep the raw source.
    try:
        cleaned = postfix_cleanup.strip_hi_annotations(src_text)
        if cleaned and blocks.count_entries(cleaned) >= max(
                1, int(blocks.count_entries(src_text) * 0.3)):
            src_text = cleaned
    except Exception:
        pass  # use raw source on cleaner failure

    # Write English fallback to cache_dir under a deterministic name
    # so repeat clicks of the same source overwrite cleanly.
    fallback_id = source_id or 'unknown'
    fallback_path = os.path.join(
        kodi.cache_dir(),
        'fast_picker_fallback_{0}.srt'.format(fallback_id))
    try:
        _tmp = fallback_path + '.aitmp'
        with open(_tmp, 'w', encoding='utf-8') as _f:
            _f.write(src_text)
        os.replace(_tmp, fallback_path)
    except OSError as _e:
        _safe_log('fast_download fallback write failed: {0}'
                  .format(_e), level='ERROR')
        return False

    # Hand the English fallback to Kodi -- the user sees subtitles
    # in seconds. endOfDirectory() ends this subprocess; the BG
    # RunScript below picks up the Hebrew translation.
    listitem = xbmcgui.ListItem(label=fallback_path)
    added = xbmcplugin.addDirectoryItem(
        handle=handle, url=fallback_path,
        listitem=listitem, isFolder=False)
    xbmcplugin.endOfDirectory(handle)
    if added is False:
        core_selection.abandon_subtitle_selection(selection)
        return True

    try:
        kodi.notify(
            'AI: כתוביות מוכנות, מתרגם ברקע', time_ms=4000)
    except Exception:
        pass

    # Fire BG translation as a separate RunScript invocation. Once
    # endOfDirectory() returns, this subprocess exits, so we hand
    # the link off in a separate process via RunScript. base64 keeps
    # the JSON-quoted link from getting mangled by RunScript's
    # comma-split parameter parsing.
    try:
        link_b64 = base64.b64encode(
            link.encode('utf-8')).decode('ascii')
        source_id_b64 = base64.b64encode(
            (source_id or '').encode('utf-8')).decode('ascii')
        selection_b64 = _encode_selection_b64(selection)
        xbmc.executebuiltin(
            'RunScript(service.subtitles.kodipovilai,'
            'action=bg_translate_picker,'
            'link_b64={0},source_id_b64={1},selection_b64={2})'.format(
                link_b64, source_id_b64, selection_b64))
    except Exception as _e:
        _safe_log('fast_download BG fire failed: {0}'.format(_e),
                  level='ERROR')
        # Subtitle was already delivered; BG didn't fire. User
        # gets English permanently for this play. Acceptable
        # degradation -- next play will still try the same fast
        # path and the cache check will short-circuit if BG
        # eventually ran on a different click.

    return True
