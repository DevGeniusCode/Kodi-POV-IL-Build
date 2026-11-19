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
