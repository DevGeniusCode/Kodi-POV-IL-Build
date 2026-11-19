# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The sync gate hand-off of a delivered Hebrew subtitle (fail-open).

from resources.lib.core import kodi, selection as core_selection


def sync_hebrew_delivery(info, path, source_release='', embedded_timing=False,
                          selection=None, fallback_link='', fallback_links=None):
    """Run any file-based Hebrew subtitle through the ordinary timing gate.

    Local, engine, pool and AI files can all carry the timing of a different
    WEB/BluRay/cut. SubSync receives the real subtitle release (when supplied)
    and writes any release-specific correction to its own delivery copy; source
    files and canonical AI cache files are never retimed in place.

    ``embedded_timing`` is the one deliberate bypass. That pipeline has already
    rebuilt/extracted the source on the playing file's own cue skeleton, so a
    second independent retime could undo the stronger anchor. This helper is
    intentionally fail-open: an unavailable synchronizer preserves the old
    path.
    """
    if not path:
        return path
    if embedded_timing:
        # Built/extracted on this video's own cue skeleton: there is no timing
        # guess to make.  Still do not publish a final verdict until Kodi has
        # visibly registered this exact file: the caller may fail, be killed,
        # or lose the selection race before setSubtitles completes.
        try:
            expected = (selection if selection is not None else
                        core_selection.current_subtitle_selection()) or {}
            if all(expected.get(k) for k in (
                    'token', 'link_hash', 'stream_hash')):
                core_selection.stage_subtitle_delivery(
                    path, selection=expected,
                    status='confirmed', source='embedded')
        except Exception:
            pass
        return path
    try:
        from resources.lib.sync import service
        kwargs = {'selection': selection}
        if fallback_link:
            kwargs['fallback_link'] = fallback_link
        if fallback_links:
            kwargs['fallback_links'] = list(fallback_links)
        delivered, _verdict = service.process(
            info, path, (source_release or '').strip(), **kwargs)
        return delivered or path
    except Exception as exc:
        kodi.log(
            'subsync Hebrew delivery hook failed: {0}'.format(exc),
            level='WARNING')
        return path
