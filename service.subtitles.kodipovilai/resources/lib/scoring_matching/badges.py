# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The last step of every list: the badge cache write-through for the source screen
# (he_sub_match) and the "currently applied" marking.

from resources.lib.core import selection as core_selection
from resources.lib.core.link_codec import decode_link
from resources.lib.core.video_context import looks_like_token


# ---- search ----------------------------------------------------------

def _mark_current(results):
    """Mark the currently-applied subtitle with '» נוכחית' and float it to the
    top (mirrors DarkSubs's 'כתובית נוכחית'). Prefer the exact candidate link,
    then its private stable identity when a provider refreshed transport data."""
    try:
        cur = core_selection.get_current_subtitle()
        if not cur:
            return results
        match = next((i for i, c in enumerate(results)
                      if c.get('link') == cur), None)
        if match is None:
            cur_id = core_selection.get_current_subtitle_identity()
            if cur_id:
                match = next((
                    i for i, c in enumerate(results)
                    if core_selection.subtitle_candidate_identity(
                        c.get('link') or '') == cur_id), None)
        if match is not None:
            c = results[match]
            status = core_selection.get_subtitle_sync_status(cur)
            if status:
                c['_subsync_state'] = status.get('state') or ''
                c['_subsync_label'] = status.get('label') or ''
            c['filename'] = '» נוכחית · ' + (c.get('filename') or '')
            c['rating'] = '5'
            results.insert(0, results.pop(match))
    except Exception:
        pass
    return results


def finalize(info, results):
    """Write-through the real Hebrew releases this live search found into the
    source-screen badge cache (so the poster % matches the picker), then apply
    the 'currently applied' marking. Every list_candidates return goes through
    here."""
    try:
        he_names = []
        for c in results:
            if c.get('language') != 'he':
                continue
            pl = decode_link(c.get('link') or '') or {}
            t = pl.get('type')
            # Real downloadable Hebrew releases only -- skip embedded streams
            # and the synthetic "AI translate" / "current" display entries.
            if t in ('passthrough', 'pool', 'engine') and not pl.get('embedded'):
                nm = (c.get('filename') or '').strip()
                if nm and not looks_like_token(nm) and '» נוכחית' not in nm \
                        and not nm.startswith('תרגום'):
                    he_names.append(nm)
        if he_names:
            from resources.lib import he_sub_match
            he_sub_match.merge_names(info, he_names)
    except Exception:
        pass
    return _mark_current(results)
