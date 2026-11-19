# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The subtitle list of the Kodi subtitle dialog (action=search / manualsearch).

import urllib.parse
from resources.lib.ui.dialogs import safe_log


def handle_search(handle, params):
    """List available subtitles. Kodi calls this when the user opens
    the subtitle search dialog."""
    # A playback source can opt out of automatic subtitle search by setting the
    # 'skip_autosub' window property (e.g. content already in the target
    # language). One-shot, honoured only when fresh (this playback), then cleared.
    from default import ADDON_ID
    try:
        import xbmcgui, xbmcplugin, time
        _ts = xbmcgui.Window(10000).getProperty('skip_autosub')
        if _ts and (time.time() - float(_ts)) < 90:
            xbmcgui.Window(10000).clearProperty('skip_autosub')
            xbmcplugin.endOfDirectory(handle)
            return
    except Exception:
        pass
    from resources.lib import pipeline
    from resources.lib.core import video_context

    info = video_context.current_video_info()
    # Right after an auto-advance to the next episode the player metadata is
    # still transitioning, so the release name (used for the sync-%) is briefly
    # empty -- searching now would show 0% until the user exits and re-enters.
    # Wait briefly (bounded, only when the engine is on and the release isn't
    # ready yet) so the FIRST open already shows correct percentages.
    try:
        from resources.lib.fetching import bridge as _seb
        from resources.lib.fetching.embedded import streams
        if streams.enabled():
            for _ in range(15):  # up to ~3s
                if _seb.release_ready(info):
                    break
                import xbmc as _xbmc
                _xbmc.sleep(200)
                info = video_context.current_video_info()
    except Exception:
        pass
    safe_log('search: ' + repr({k: v for k, v in info.items() if v}))

    try:
        candidates = pipeline.search(info)
    except Exception as e:
        safe_log('list_candidates crashed: {0}'.format(e), level='ERROR')
        candidates = []

    for c in candidates:
        try:
            label = c.get('filename', 'AI Hebrew')
            # Star rating (0-5) reflects the match % so Tal's DialogSubtitles
            # shows a meaningful star count (101%/100% -> 5 stars, 60% -> 3...).
            # No % (pct is None: e.g. an AI-translate-from-foreign action) -> use
            # the candidate's own rating, defaulting high (it's a good option).
            _pct = c.get('pct')
            if _pct is not None:
                _stars = int(round(min(100, int(_pct)) / 20.0))
                _stars = max(1, min(5, _stars))
                _rating = str(_stars)
            else:
                _rating = str(c.get('rating', '4'))
            sync_label = (c.get('_subsync_label') or '').strip()
            if sync_label:
                label = '{0} · {1}'.format(label, sync_label)
            listitem = xbmcgui.ListItem(label=c.get('language', 'he'),
                                        label2=label)
            listitem.setArt({'icon': _rating,
                             'thumb': c.get('language', 'he')})
            listitem.setProperty('sync', c.get('sync', 'false'))
            listitem.setProperty('hearing_imp',
                                 'true' if c.get('is_hi') else 'false')
            url = ('plugin://{0}/?action=download&link={1}'
                   .format(ADDON_ID,
                           urllib.parse.quote(c.get('link', ''), safe='')))
            xbmcplugin.addDirectoryItem(handle=handle, url=url,
                                        listitem=listitem,
                                        isFolder=False)
        except Exception as e:
            safe_log('addDirectoryItem failed: {0}'.format(e),
                      level='WARNING')

    xbmcplugin.endOfDirectory(handle)


def handle_manualsearch(handle, params):
    # Kodi sometimes invokes manualsearch when the user types a
    # query in the search dialog. We treat it the same as search;
    # the title/year still flow through getInfoLabel.
    handle_search(handle, params)
