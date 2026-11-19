# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The state of one handle_download() call (DownloadState), the stage protocol (NEXT) and the first stage:
# cancel the older timing job, remember the pick, bind it to the selection, decode the link.



NEXT = object()   # what a stage returns when the work goes on with the next stage


class DownloadState(object):
    """The state of one handle_download() call: every variable that more than one stage, or a closure of the handler,
    reads or writes. Nothing here is shared between calls."""
    __slots__ = (
        # arguments of resolve()
        'handle', 'params',
        # first set in the stage "begin"
        'info', 'link', 'p', 'timing_selection',
        # first set in the stage "deliver"
        'milestone_state', 'progress',
    )

    def __init__(self, handle, params,
                 ):
        self.handle = handle
        self.params = params



def timing_selection_current(S):
    from resources.lib.core import selection as core_selection
    try:
        return bool(core_selection.subtitle_selection_matches(
            S.timing_selection.get('token') or '',
            S.timing_selection.get('link_hash') or '',
            S.timing_selection.get('stream_hash') or ''))
    except Exception:
        return False


def begin(S):
    """Cancel the older timing job, remember the pick, bind it to its selection, decode the link."""
    from resources.lib.core import selection as core_selection, video_context, link_codec

    # Invalidate any older background timing job immediately, before handling
    # file-less embedded branches. Otherwise an old worker on this same movie
    # could finish after the click and replace the user's newer choice.
    try:
        from resources.lib.sync import jobs as _subsync
        _subsync.cancel_pending()
    except Exception:
        pass

    S.link = S.params.get('link', '')
    S.info = video_context.current_video_info()

    # Remember this as the currently-applied subtitle so the picker marks it
    # '» נוכחית' next time it opens.
    try:
        core_selection.set_current_subtitle(S.link)
    except Exception:
        pass
    try:
        S.timing_selection = core_selection.current_subtitle_selection(
            expected_link=S.link)
    except Exception:
        S.timing_selection = {}

    # If the user picked an engine "translate from <lang>" entry, download the
    # source sub NOW, then continue as a local 'ai' link. This lets the fast
    # path below deliver the source immediately (dialog closes) and translate
    # in the background -- instead of blocking the dialog open for the whole
    # 1-2 minute translation.
    try:
        S.p = link_codec.decode_link(S.link)
    except Exception:
        S.p = None
    return NEXT
