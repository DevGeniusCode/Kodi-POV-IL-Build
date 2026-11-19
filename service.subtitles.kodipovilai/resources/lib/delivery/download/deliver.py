# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# handle_download stages for the AI picks and the delivery: the fast first chunk, the background
# translation hand-off, and the slow flow that resolves the link and delivers the file.

import os
from functools import partial
from resources.lib.ui.dialogs import dialogs_title, safe_log
from resources.lib.delivery.download.state import NEXT, timing_selection_current
try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None


def report(S, stage, total):
    from resources.lib.core import kodi
    try:
        pct = int(stage * 100 / max(1, total))
        if S.progress is not None:
            try:
                S.progress.update(
                    pct, dialogs_title(),
                    kodi.localised(32631, stage, total))
            except Exception:
                pass
        # Milestone toasts -- always visible above any window.
        milestone = (pct // 25) * 25
        if (milestone in (25, 50, 75)
                and milestone > S.milestone_state['last']):
            S.milestone_state['last'] = milestone
            try:
                kodi.notify(
                    'AI: {0}% תורגם ({1}/{2} chunks)'.format(
                        milestone, stage, total),
                    time_ms=3500)
            except Exception:
                pass
    except Exception:
        pass


def fast(S):
    """The opt-in fast path of the native picker (English now, Hebrew in the background)."""
    from resources.lib.core import settings
    from resources.lib.delivery.kodi_player import try_fast_download
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
            if try_fast_download(
                    S.handle, S.link, S.info, selection=S.timing_selection):
                return  # endOfDirectory was called inside the helper
        except Exception as _e:
            safe_log('fast_download outer guard caught: {0}'
                      .format(_e), level='WARNING')
    return NEXT


def ai_background(S):
    """An "ai" link: close the dialog now, translate in a background RunScript."""
    from resources.lib.ai_translation import source_prep
    from resources.lib.core import kodi, link_codec
    from resources.lib.delivery.kodi_player import encode_selection_b64
    # AI translation links: CLOSE the dialog immediately and translate in the
    # background, instead of blocking the picker for the full 1-2 minute
    # translation. This matches the "בחר כתוביות" chooser window: you click,
    # the dialog closes, Hebrew swaps in progressively as it translates. The
    # picker subprocess ends at endOfDirectory, so the work is handed to a
    # separate RunScript (bg_translate_picker), exactly like the fast path's
    # background step -- just without delivering an English fallback first.
    try:
        _ai_payload = link_codec.decode_link(S.link)
    except Exception:
        _ai_payload = None
    if _ai_payload and _ai_payload.get('type') == 'ai':
        try:
            import base64 as _b64m
            _sid = source_prep.source_id_for_ai(_ai_payload) or ''
            _lk = _b64m.b64encode(S.link.encode('utf-8')).decode('ascii')
            _sd = _b64m.b64encode(_sid.encode('utf-8')).decode('ascii')
            _ss = encode_selection_b64(S.timing_selection)
            xbmc.executebuiltin(
                'RunScript(service.subtitles.kodipovilai,'
                'action=bg_translate_picker,link_b64={0},source_id_b64={1},'
                'selection_b64={2})'.format(_lk, _sd, _ss))
            try:
                kodi.notify('AI: מתרגם לעברית ברקע', time_ms=3500)
            except Exception:
                pass
        except Exception as _e:
            safe_log('ai bg fire failed: {0}'.format(_e), level='WARNING')
        # Close the dialog now with no item -- same proven sequence as the
        # embedded-Hebrew pick (Dialog.Close -> empty -> sleep -> endOfDirectory).
        try:
            xbmc.executebuiltin('Dialog.Close(all,true)')
            xbmcplugin.addDirectoryItems(S.handle, [], 0)
            xbmc.sleep(100)
            xbmcplugin.endOfDirectory(S.handle, updateListing=True,
                                      cacheToDisc=True)
        except Exception:
            try:
                xbmcplugin.endOfDirectory(S.handle)
            except Exception:
                pass
        return
    return NEXT


def deliver(S):
    """The slow flow: progress, resolve the link, deliver the file."""
    from resources.lib import pipeline
    from resources.lib.ai_translation.proofread import gp_jobs
    from resources.lib.core import selection as core_selection
    from resources.lib.delivery.kodi_player import deliver_named_subtitle, try_next_hebrew
    # DialogProgressBG (bottom-right banner) PLUS milestone toasts.
    # The toasts at 25/50/75 % are the guaranteed-visible backup --
    # DialogProgressBG can be hidden behind a full-screen window
    # (DarkSubs MySubs, the video OSD) but toasts always layer on
    # top of everything. v0.2.42-0.2.44 tried a custom WindowDialog
    # overlay; that approach kept failing in various ways (hidden
    # behind pyxbmct, then all-black render, then thread-update
    # blackhole) so we reverted to the simple toast-driven pattern
    # that worked reliably in v0.2.41.
    S.progress = None
    try:
        S.progress = xbmcgui.DialogProgressBG()
        S.progress.create(dialogs_title(),
                        'AI Hebrew')
    except Exception:
        S.progress = None

    S.milestone_state = {'last': 0}

    try:
        path = pipeline.resolve(
            S.link, S.info, progress_cb=partial(report, S), selection=S.timing_selection)
    except Exception as e:
        safe_log('resolve crashed: {0}'.format(e), level='ERROR')
        path = None
    finally:
        if S.progress is not None:
            try:
                S.progress.close()
            except Exception:
                pass

    # If the picked subtitle failed to download (e.g. Ktuvit rate-limited),
    # don't leave the user stuck on it -- automatically try the NEXT available
    # ready Hebrew subtitles and deliver the first that works.
    fallback_selection = None
    if (not (path and os.path.isfile(path))
            and timing_selection_current(S)):
        try:
            fallback = try_next_hebrew(
                S.link, S.info, owner_selection=S.timing_selection)
            if fallback:
                path, S.link, fallback_selection = fallback
                S.timing_selection = fallback_selection
        except Exception as e:
            safe_log('next-hebrew fallback failed: {0}'.format(e),
                      level='WARNING')

    if (path and os.path.isfile(path)
            and timing_selection_current(S)):
        # Internet-stream workaround: hand the SRT to the player ourselves
        # under a meaningful, unique name so it doesn't collapse into the
        # generic shared "TempSubtitle" file Kodi would create. Falls back
        # to the normal addDirectoryItem flow on local playback or failure.
        delivered = False
        try:
            delivered = deliver_named_subtitle(
                S.handle, path, S.link, S.info, selection=S.timing_selection)
        except Exception as _e:
            safe_log('named subtitle delivery failed: {0}'.format(_e),
                      level='WARNING')
            delivered = False
        if delivered:
            # a pooled AI translation: its gender proofread in the background
            gp_jobs.proofread_later(S.link, S.timing_selection)
            return  # endOfDirectory already called inside the helper
        if not timing_selection_current(S):
            xbmcplugin.endOfDirectory(S.handle)
            return
        listitem = xbmcgui.ListItem(label=path)
        registration_before = core_selection.subtitle_sync_registration_baseline(
            path, selection=S.timing_selection)
        added = xbmcplugin.addDirectoryItem(
            handle=S.handle, url=path, listitem=listitem, isFolder=False)
        xbmcplugin.endOfDirectory(S.handle)
        registration_confirmed = True
        if added is not False and registration_before is not None:
            registration_confirmed = core_selection.confirm_subtitle_sync_registration(
                path, selection=S.timing_selection,
                before=registration_before)
        if added is False or not registration_confirmed:
            core_selection.abandon_subtitle_selection(S.timing_selection)
        else:
            gp_jobs.proofread_later(S.link, S.timing_selection)
        return
    core_selection.abandon_subtitle_selection(S.timing_selection)
    xbmcplugin.endOfDirectory(S.handle)
    return NEXT
