# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# handle_download stages for the link types that finish early or turn into another: an embedded Hebrew
# pick, an engine "translate from" pick (source downloaded, then an "ai" link), an embedded AI pick.

import os
from resources.lib.ui.dialogs import safe_log
from resources.lib.delivery.download.state import NEXT, timing_selection_current
try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None


def engine_embedded(S):
    """An embedded Hebrew pick: switch the stream, close the dialog, end with no item."""
    from resources.lib.core import selection as core_selection, kodi
    # Embedded Hebrew pick: there is no file to hand back. Switch Kodi's
    # subtitle stream and CLOSE the dialog, then end with no item -- copied
    # EXACTLY from DarkSubs (main.py): Dialog.Close(all,true) -> empty
    # addDirectoryItems -> xbmc.sleep(100) -> endOfDirectory(updateListing=True,
    # cacheToDisc=True). The sleep(100) is essential: it lets the dialog finish
    # closing BEFORE endOfDirectory, otherwise Kodi shows "download failed".
    if S.p and S.p.get('type') == 'engine' and S.p.get('embedded'):
        _embedded_selected = False
        try:
            from resources.lib.fetching.embedded import streams
            # Pass the picked language through. It was being dropped here and
            # only here, which left the out-of-range fallback inside
            # select_embedded re-finding a HEBREW stream for a '[מובנה] EN'
            # pick.
            if (timing_selection_current(S)
                    and streams.select_embedded(
                        S.p.get('stream_index'), lang=S.p.get('lang'))
                    and timing_selection_current(S)):
                _embedded_selected = True
                core_selection.set_subtitle_sync_status(
                    'confirmed', source='embedded',
                    selection_token=S.timing_selection.get('token') or '',
                    link_hash=S.timing_selection.get('link_hash') or '',
                    stream_hash=S.timing_selection.get('stream_hash') or '')
                kodi.notify('כתובית עברית מובנה הופעלה', time_ms=3000)
        except Exception as _e:
            safe_log('embedded select failed: {0}'.format(_e), level='WARNING')
        if not _embedded_selected:
            core_selection.abandon_subtitle_selection(S.timing_selection)
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


def engine_ai(S):
    """An engine "translate from" pick: download the source, continue as a local "ai" link."""
    from resources.lib.core import kodi, link_codec
    if S.p and S.p.get('type') == 'engine_ai':
        eng_path = None
        try:
            from resources.lib.fetching.embedded import streams
            kodi.notify('AI: מוריד את כתובית המקור...', time_ms=2500)
            eng_path = streams.download(S.p)
        except Exception as _e:
            safe_log('engine_ai source download failed: {0}'.format(_e),
                      level='ERROR')
        if eng_path and os.path.isfile(eng_path):
            S.link = link_codec.encode_link({
                'type': 'ai',
                'source_lang': S.p.get('src_lang') or 'en',
                'local_path': eng_path,
                'force_ai': True,
            })
        else:
            kodi.notify('AI: לא ניתן היה להוריד את כתובית המקור',
                              time_ms=4000)
            xbmcplugin.endOfDirectory(S.handle)
            return
    return NEXT


def embedded_ai(S):
    """An embedded AI pick: the whole job runs in the background (bg_translate_picker)."""
    from resources.lib.core import kodi
    from resources.lib.delivery.kodi_player import encode_selection_b64
    if S.p and S.p.get('type') == 'embedded_ai':
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
            _si = S.p.get('stream_index')
            if _si is not None and timing_selection_current(S):
                _seb.select_embedded(_si, lang=S.p.get('src_lang') or 'en')
        except Exception:
            pass
        try:
            # A source-track switch can yield to Kodi. If another subtitle was
            # selected meanwhile, close this old picker cleanly but do not fire
            # a stale extraction/translation job.
            if timing_selection_current(S):
                import base64 as _b64m
                _lk = _b64m.b64encode(S.link.encode('utf-8')).decode('ascii')
                _sd = _b64m.b64encode(b'').decode('ascii')
                _ss = encode_selection_b64(S.timing_selection)
                xbmc.executebuiltin(
                    'RunScript(service.subtitles.kodipovilai,'
                    'action=bg_translate_picker,link_b64={0},source_id_b64={1},'
                    'selection_b64={2})'.format(_lk, _sd, _ss))
                kodi.notify(
                    'AI: מחלץ ומתרגם תרגום מובנה ברקע', time_ms=3500)
        except Exception as _e:
            safe_log('embedded_ai bg fire failed: {0}'.format(_e),
                      level='WARNING')
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
