# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Row helpers of the subtitle list: the tidy-up of a finished row, release-name normalisation, the pool
# release test, and the order of the AI source languages.

from resources.lib.core import video_context

# Language display order (the user's requested grouping): Hebrew first
# (handled above as its own groups), then English, Spanish, then the other
# AI-source languages MoranSubs can translate from (German/French/
# Portuguese), then everything else. Used to sort + group the raw foreign
# subs AND to decide which languages get an "AI translate to Hebrew" entry.
AI_SOURCE_ORDER = {'en': 0, 'es': 1, 'de': 2, 'fr': 3, 'pt': 4}


def clean(c):
    kind = c.pop('_engine_kind', None)
    # `pct` is the match number the row's label shows ('<provider> · 85%'); a built-in
    # foreign stream shows none.
    c['pct'] = None if kind == 'embedded_other' else c.pop('_pct', None)
    c.pop('_pct', None)
    c.pop('_is_mt', None)
    return c


def norm_rel(s):
    import re as _re_nr
    return _re_nr.sub(r'[^a-z0-9]', '', (s or '').lower())


def pool_release(v):
    r = (v.get('release') or '').strip()
    # Reject debrid URL / token "releases" stored by older shares.
    return '' if (r and video_context.looks_like_token(r)) else r
