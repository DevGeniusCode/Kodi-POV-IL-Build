# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Subtitle list stages: the community pool rows (shared Hebrew and AI translations of this release).

from resources.lib.core import video_context
from resources.lib.core.link_codec import decode_link, encode_link
from resources.lib.scoring_matching.ranking import match_pct
from resources.lib.fetching.search.rows import clean, norm_rel, pool_release
from resources.lib.fetching.search.state import NEXT
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None


def pool_entry(S, v, release):
    pct = match_pct(S.video_ref, release) if release else 0
    if release and pct > 0:
        label = 'כתובית · מאגר · {0}%  —  {1}'.format(pct, release)
    elif release:
        label = 'כתובית · מאגר  —  {0}'.format(release)
    else:
        label = 'כתובית · מאגר'
    return {
        'filename': label, 'language': 'he',
        # 'release' rides in the link so resolve() can tier-check the
        # pool sub against the playing release (SubSync S2 verify/fix).
        'link': encode_link({
            'type': 'pool', 'hash': v.get('hash'),
            'release': release or '',
            'pool_kind': v.get('kind') or 'ai',
            'source_lang': v.get('source_lang') or '',
        }),
        'sync': 'false', 'rating': '5', 'is_hi': False, 'is_hd': False,
        'pct': pct if (release and pct > 0) else None,
    }


def pool(S):
    """The community pool rows."""
    # Community pool. ONE network lookup returns both kinds of shared Hebrew:
    #   - 'ktuvit': a HUMAN Ktuvit subtitle mirrored to the pool. It loads
    #               INSTANTLY from the channel and never hits Ktuvit, so when a
    #               release exists BOTH live (from the engine) and in the pool we
    #               show the POOL copy and hide the slow live one -- exactly what
    #               you asked: no need for the live Ktuvit when the pool has it.
    #               Labelled "כתובית · מאגר" so it's clearly the pool (a live one
    #               is labelled "Ktuvit"). Human, so it ranks above the AI pool.
    #   - 'ai':     a machine AI translation other users shared.
    # One lookup keeps the request count identical to the AI-only pool.
    S.pool_variants = []
    S.video_ref = ''
    if S.quick and pool_contribute is not None and pool_client.use_enabled():
        S.info['_list_pending'] = True
    elif pool_contribute is not None and pool_client.use_enabled():
        # Mirror the CONTRIBUTION-side release derivation (pool._release_from):
        # it tries basename(li_filename) BEFORE basename(filepath), because on a
        # debrid stream `filepath` is a tokenized URL while `li_filename`
        # (ListItem.FileNameAndPath) carries the real release. Without li_filename
        # here the lookup and the stored release could differ for the SAME file
        # (when picked_release/tagline/label are all blank), which would make the
        # embedded same-source gate wrongly hide the viewer's OWN item on replay.
        S.video_ref = video_context.release_name(S.info, 'picker')
        try:
            S.pool_variants = pool_client.lookup(S.info)
        except Exception:
            S.pool_variants = []



    # Map normalised release -> the pooled Ktuvit copy, so we can swap a live
    # Ktuvit result for its faster pool twin.
    _ktuvit_pool_by_rel = {}
    for v in S.pool_variants:
        if (v.get('kind') or 'ai') == 'ktuvit':
            r = pool_release(v)
            if r:
                _ktuvit_pool_by_rel.setdefault(norm_rel(r), v)

    # Engine human Hebrew, in the engine's own match-% order. For a LIVE Ktuvit
    # result that's ALSO in the pool, emit the POOL copy in its place (instant +
    # no Ktuvit hit), keeping the position so the %-ordering is preserved. Other
    # human results (Wizdom, not-yet-pooled Ktuvit) render as-is.
    _used_pool_rels = set()
    for c in S.engine_human:
        S.have_hebrew = True
        pl = decode_link(c.get('link') or '') or {}
        is_ktuvit = (pl.get('type') == 'engine'
                     and (pl.get('source') or '').strip().lower() == 'ktuvit')
        fn = c.get('filename') or ''
        rel_norm = norm_rel(fn.split('—')[-1].strip() if '—' in fn else '')
        if is_ktuvit and rel_norm and rel_norm in _ktuvit_pool_by_rel:
            v = _ktuvit_pool_by_rel[rel_norm]
            S.results.append(pool_entry(S, v, pool_release(v)))
            _used_pool_rels.add(rel_norm)
        else:
            S.results.append(clean(c))

    # Pooled Ktuvit releases the live engine did NOT return this time (e.g.
    # Ktuvit is slow/down) -- still available, instant, human.
    for rel_norm, v in _ktuvit_pool_by_rel.items():
        if rel_norm in _used_pool_rels:
            continue
        S.have_hebrew = True
        S.results.append(pool_entry(S, v, pool_release(v)))
    return NEXT
