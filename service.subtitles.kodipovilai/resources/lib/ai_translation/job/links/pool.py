# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Link type "pool": a community-pool entry picked from the list (fetch, deliver, proofread).

import os
from functools import partial
from resources.lib.core.cache import translation_cache
from resources.lib.ai_translation.repairs import postfix_glyphs
from resources.lib.core import kodi
from resources.lib.ai_translation.progress import status
from resources.lib.ai_translation.proofread.gp_jobs import pool_gp_base, pool_job_id, pool_variant_proofread
from resources.lib.ai_translation.proofread.gp_run import proofread_due, proofread_opened
from resources.lib.ai_translation.proofread.gp_state import gp_done, gp_hebrew_ref, gp_mark, gp_marker, gp_ref_tier, gp_try_mark
from resources.lib.ai_translation.reuse.pool_reuse import pool_pick_emb_source, pool_pick_source, pool_quality_ok, pool_source_text
from resources.lib.delivery.rtl import reapply_rtl_fix_in_place
from resources.lib.sync.delivery_gate import sync_hebrew_delivery
from resources.lib.ai_translation.job.state import NEXT, translation_cancelled
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None


def pool(S):
    """Link type "pool"."""
    if S.kind == 'pool':
        # A community-pool entry the user picked from the dialog. Fetch the
        # exact shared Hebrew SRT (by source hash) and hand it to Kodi.
        if pool_contribute is None:
            return None
        _phash = S.payload.get('hash') or ''
        _pkind = S.payload.get('pool_kind') or 'ai'
        _gp_base = pool_gp_base(_phash) if _pkind != 'ktuvit' else ''
        # Proofread in the pool since this copy was fetched (another viewer's
        # proofread replaced it): the kept copy is stale, fetched again -- and
        # replaced only once the new one is in hand.
        _refreshed = False
        if (_gp_base and os.path.isfile(_gp_base) and not gp_done(_gp_base)
                and pool_variant_proofread(S.info, _phash, network=S.proofread_only)):
            _fresh = pool_client.fetch(S.info, _phash) or ''
            if _fresh:
                try:
                    translation_cache.save_text(_gp_base, _fresh)
                    gp_mark(_gp_base)
                    _refreshed = True
                except Exception:
                    pass
        _had_copy = bool(_gp_base) and os.path.isfile(_gp_base)
        text, sid = pool_source_text(S.info, _phash)
        if not text:
            status('AI: לא נמצאה כתובית במאגר', time_ms=4000)
            return None
        if (_gp_base and not _had_copy and not gp_done(_gp_base)
                and pool_variant_proofread(S.info, _phash, network=False)):
            gp_mark(_gp_base)      # fetched just now, and proofread already
        _praw = os.path.join(kodi.cache_dir(),
                             'pool_{0}.source.srt'.format(sid))
        out = os.path.join(kodi.cache_dir(), 'pool_{0}.he.srt'.format(sid))
        # Old Ktuvit pool rows were stored after the vendored engine had
        # physically moved trailing punctuation. Repair them locally on
        # this fetched copy only. New logical rows carry a private format
        # tag in source_lang; AI rows were never owned by that engine.
        _psrc = S.payload.get('source_lang') or ''
        _legacy_ktuvit = (
            _pkind == 'ktuvit'
            and _psrc != pool_client.KTUVIT_LOGICAL_SOURCE_TAG)

        def _pool_deliver(raw_path):
            with open(raw_path, 'r', encoding='utf-8', errors='replace') as f:
                shown = f.read()
            with open(out, 'w', encoding='utf-8') as f:
                f.write(shown)
            reapply_rtl_fix_in_place(
                out, legacy_engine=(True if _legacy_ktuvit else 'auto'),
                ai_output=postfix_glyphs.may_carry_arabic_leak(pool_kind=_pkind))
            # SubSync S2: pool variants carry the release of their SOURCE sub;
            # if that doesn't match the playing release, verify/fix timing
            # against a release-matched oracle. Fail-open.
            return sync_hebrew_delivery(
                S.info, out, source_release=S.payload.get('release') or '',
                selection=S.timing_selection, fallback_link=S.fallback_link,
                fallback_links=S.fallback_links)

        try:
            if S.proofread_only and _refreshed and os.path.isfile(_praw):
                # someone else's proofread came in for the copy on screen
                out = _pool_deliver(_praw)
                try:
                    S.progressive_cb('done', {
                        'success': True, 'source_id': pool_job_id(_gp_base),
                        'release': S.payload.get('release') or '', 'path': out})
                except Exception:
                    pass
                return out
            if S.proofread_only:
                # The gender proofread needs the subtitle it was translated
                # from: the engine's cached search has it when it carries the
                # same release and hashes the same -- an embedded one is
                # looked for its own way.
                # A variant made with a reference ("_ar"): the title's search
                # tells whether it was a Hebrew one (_gp_hebrew_ref).
                _gp_info = S.info if gp_ref_tier(_praw, _phash) else None
                if not (_gp_base and os.path.isfile(_praw)
                        and proofread_due(_praw, None, S.progressive_cb,
                                           marker=_gp_base, info=_gp_info)):
                    return None
                # held through the look (an embedded one downloads for up to
                # 45 s): a second job for this row meanwhile does not ask too
                gp_try_mark(_gp_base, True)
                if _pkind == 'ai_emb':
                    _psource = pool_pick_emb_source(S.info, _phash, _psrc, _praw)
                else:
                    _psource = pool_pick_source(S.info, _phash,
                                                 S.payload.get('release') or '')
                if _psource is False:
                    return None     # part of the list did not download: the
                                    # in-progress pause stands (six hours)
                if _psource is None:
                    gp_try_mark(_gp_base, False)
                    return None     # the look failed: try again another time
                if not proofread_due(_praw, _psource, S.progressive_cb,
                                      marker=_gp_base, held=True, info=_gp_info):
                    gp_try_mark(_gp_base, False)
                    if _psource == '':
                        # looked, and it is not in the list: a week's pause
                        kodi.log('gender proofread: no source subtitle for '
                                       'this pooled translation', level='INFO')
                        try:
                            with open(_gp_base + '.gp.nosrc', 'w', encoding='utf-8') as f:
                                f.write('1')
                        except OSError:
                            pass
                    return None
                _pshare = None
                if pool_client.share_enabled():
                    def _pshare(text_, stats=None):
                        if pool_quality_ok(_psource, text_):
                            pool_contribute.contribute_once(
                                S.info, _phash, _psrc or 'en', text_,
                                marker_path=gp_marker(_gp_base),
                                release_override=S.payload.get('release') or None,
                                kind=('ai_emb' if _pkind == 'ai_emb' else 'ai'),
                                proofread=stats or True)
                return proofread_opened(
                    _praw, _psource, pool_job_id(_gp_base),
                    S.payload.get('release') or '', S.progressive_cb, _pool_deliver,
                    partial(translation_cancelled, S), S.info, share=_pshare,
                    shown_already=True, mark_path=_gp_base,
                    later={'hash': _phash, 'lang': _psrc or 'en',
                           'release': S.payload.get('release') or '',
                           'kind': ('ai_emb' if _pkind == 'ai_emb' else 'ai'),
                           'ref_hash': _phash},
                    hebrew_ref=gp_hebrew_ref(_praw, _gp_info))
            if not os.path.isfile(_praw):
                with open(_praw, 'w', encoding='utf-8') as f:
                    f.write(text)
            out = _pool_deliver(_praw)
            status('כתוביות מהמאגר הקהילתי', time_ms=4000)
            return out
        except OSError:
            return None
    return NEXT
