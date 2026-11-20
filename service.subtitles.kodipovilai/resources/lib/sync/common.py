# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Sync: constants and helpers shared by the sync modules.



STATUS_NO_ORACLE = 'NO_ORACLE'
STATUS_ALTERNATE_APPLIED = 'ALTERNATE_APPLIED'
# Same-source oracles (BluRay/DVD) are the SAME disc master -> framerate is
# identical, so their alignment is pinned to identity scale: a full scale search
# on a cross-language oracle finds spurious FPS fits (field: scale=0.999/+560s).
# Cross-language cue segmentation also depresses the coarse vote (field: a real
# -926ms offset scored only 61%), so the vote floor is relaxed for this path --
# the graduated tight gate (sync_align) stays the real correctness guard.
ORACLE_SOURCE_SCALES = (1.0,)
# A majority (>50%) of cues must still agree on the offset, but not more: a
# different-subber sub segments its lines differently, so the correct small
# offset ran as low as 54% vote in the field (S01E04 -677ms) -- the real
# correctness guard is the graduated tight/overlap gate, not the coarse vote.
ORACLE_SOURCE_MIN_VOTE = 0.50


MIN_REMOTE_CUES = 8
AUDIO_SCALES = (1.0,)
AUDIO_MAX_OFFSET_MS = 180000
yield_state = {'checked': 0.0, 'seen_playing': False, 'gone': None, 'waited': 0.0}

# Keys that must survive the JSON round-trip for bridge.search /
# playing_release to work in the service process.
INFO_KEYS = ('imdb_id', 'tmdb_id', 'dbid', 'season', 'episode', 'title', 'tvshow',
              'tvshowtitle', 'year', 'filepath', 'picked_release', 'tagline',
              'label', 'media_type', 'is_episode')


# ---- the applied-fix description: ONE place for the toast text and the on-screen badge ---------------------------
SYNC_NOTICE = 'הכתובית סונכרנה אוטומטית'
MODE_OFFSET = 'offset'
MODE_SCALE = 'scale'
MODE_PIECEWISE = 'piecewise'


def describe_fix(verdict, offset_hint=None):
    """What an applied FIXABLE verdict did to the subtitle, in Kodi's own convention: ``+`` = the subtitle
    appears LATER (the sign of Kodi's subtitle-delay slider; the verdict's offset_ms moves the other way).
    Returns {'mode': offset | scale | piecewise,
             'label': '+2.5s' / '-1.2s' (Latin, one text direction: it goes in its own skin control) or the
                      Hebrew word 'שינוי קצב' / 'מקטעים', or '' for a zero shift,
             'text':  the same for a Hebrew sentence ('+2.5 שנ׳': the unit keeps the toast in one RTL run)}.
    Pure: no Kodi, no I/O."""
    verdict = verdict or {}
    try:
        off = float(offset_hint if offset_hint is not None else verdict.get('offset_ms') or 0.0)
        scale = float(verdict.get('scale') or 1.0)
    except (TypeError, ValueError):
        off, scale = 0.0, 1.0
    if verdict.get('mode', 'global') != 'global':
        return {'mode': MODE_PIECEWISE, 'label': 'מקטעים', 'text': 'מקטעים'}
    if scale != 1.0:
        return {'mode': MODE_SCALE, 'label': 'שינוי קצב', 'text': 'שינוי קצב'}
    if not off:
        return {'mode': MODE_OFFSET, 'label': '', 'text': ''}
    seconds = -off / 1000.0
    return {'mode': MODE_OFFSET, 'label': '{0:+.1f}s'.format(seconds), 'text': '{0:+.1f} שנ׳'.format(seconds)}


def sync_notice(verdict, offset_hint=None):
    """The toast / overlay sentence of an applied fix (every builder uses this one)."""
    text = describe_fix(verdict, offset_hint)['text']
    return SYNC_NOTICE + (' ({0})'.format(text) if text else '')


def fix_badge(verdict, original, keys, offset_hint=None):
    """The record published with a confirmed fix: the badge (offset label + mode) and what "בטל סנכרון אוטומטי"
    needs: the unshifted file and the verdict-cache keys (sub_hash|cut_signature) to veto."""
    described = describe_fix(verdict, offset_hint)
    unique = []
    for key in keys or ():
        if key and key not in unique:
            unique.append(str(key))
    return {'offset': described['label'], 'mode': described['mode'],
            'original': str(original or ''), 'keys': unique}
