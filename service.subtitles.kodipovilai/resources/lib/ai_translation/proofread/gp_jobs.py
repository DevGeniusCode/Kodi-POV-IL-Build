# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Gender proofread, later: the deferred jobs, their tick and the pool proofread variants.

import json
import os
import time
from resources.lib.core.cache import translation_cache
from resources.lib.core import kodi, settings
from resources.lib.core.srt import blocks as srt_blocks
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client
except Exception:
    pool_contribute = pool_client = None
from resources.lib.core.link_codec import _decode_link, _encode_link
from resources.lib.ai_translation.proofread.gp_run import _run_gender_proofread, _title_cast
from resources.lib.ai_translation.proofread.gp_state import _gp_done, _gp_hebrew_ref, _gp_mark, _gp_marker, _gp_models_left, _gp_ref_tier, _gp_settle, _gp_stats, _gp_try_mark, _gp_try_marker, _gp_trying
from resources.lib.ai_translation.reuse.pool_reuse import _pool_local_id, _pool_quality_ok
import shutil
from resources.lib.ai_translation.reuse.pool_reuse import _embedded_source_path
from resources.lib.ai_translation.source_prep import _content_hash, _prepare_source


# A proofread no model answered, asked again from the service (0.3.75):
# gp_later keeps the queue and its pacing; here what is queued and how one is
# asked. Queued for these reasons only (a reply of no use, a refused key or a
# refusal unread would come back the same) -- and a connection that failed
# (report 'net': no network yet after a box woke up).
_GP_LATER_WHY = ('busy', 'time', 'quota', 'stopped')


def _gp_later_retry(report):
    report = report or {}
    return (report.get('why') in _GP_LATER_WHY
            or (report.get('why') == 'error' and bool(report.get('net'))))


def _gp_later_file():
    return os.path.join(kodi.cache_dir(), 'gp_later.json')


def _gp_later_today():
    try:
        from resources.lib.ai_translation.proofread import gender_proofread
        return gender_proofread._today()
    except Exception:
        return ''


def _gp_later_src(marker):
    """Where the queue keeps a copy of the subtitle a translation was made
    from (the source search's copy may be gone by the time it is asked)."""
    import hashlib
    return os.path.join(kodi.cache_dir(), 'gpl_{0}.src.srt'.format(
        hashlib.sha1(marker.encode('utf-8')).hexdigest()[:20]))


def _gp_later_forget(entry):
    try:
        os.remove(entry.get('src') or '')
    except OSError:
        pass


def _gp_later_markers(entry):
    return [entry['marker']] + [c for c in entry.get('copies') or [] if c]


def _gp_try_age(path):
    """Seconds since the in-progress marker was set (inf without one; below
    zero while it holds a reply of no use for a week)."""
    try:
        return time.time() - os.path.getmtime(_gp_try_marker(path))
    except OSError:
        return float('inf')


# An in-progress marker more than this far in the future holds a reply of no
# use (a week, _GP_SPENT_SECS); one a little ahead is a clock that went back
# (a box without a clock of its own, before its time sync): only held.
_GP_LATER_SPENT_AHEAD = 86400


def _gp_later_alive(entry):
    """Still worth asking: its files there, not proofread meanwhile (any of
    its copies counts), not waiting out a reply of no use."""
    marks = _gp_later_markers(entry)
    return (os.path.isfile(entry.get('path') or '') and os.path.isfile(entry.get('src') or '')
            and not any(_gp_done(m) for m in marks)
            and min(_gp_try_age(m) for m in marks) >= -_GP_LATER_SPENT_AHEAD)


def _gp_later_add(report, path, source_text, info, share=None, marker=None,
                  copies=()):
    """Queue a proofread no model answered (_gp_later_retry), for the service
    to ask again (gp_later_tick). `share`: where the pool keeps the
    translation ({'hash', 'lang', 'release', 'kind'}, and 'ref_hash': the
    pooled variant's own hash, for the Hebrew-reference rule), None when it
    does not; `copies`: other files holding the same translation. Never
    raises."""
    try:
        report = report or {}
        if report.get('model') or report.get('spent') or not _gp_later_retry(report):
            return
        marker = marker or path
        if not (source_text and path and os.path.isfile(path)) or _gp_done(marker):
            return
        from resources.lib.ai_translation.proofread import gp_later
        share = dict(share or {})
        ref_hash = share.pop('ref_hash', None) or None
        src = _gp_later_src(marker)
        with open(src, 'w', encoding='utf-8') as f:
            f.write(source_text)
        entry = {
            'path': path, 'marker': marker, 'src': src, 'ref_hash': ref_hash,
            'copies': [c for c in copies or () if c and c != path],
            'info': {k: v for k, v in (info or {}).items()
                     if isinstance(k, str)
                     and (v is None or isinstance(v, (str, int, float, bool)))},
            'share': share if share.get('hash') else None}
        pushed = []

        def change(state):
            pushed.extend(gp_later.add(state, entry))
            return None, True
        gp_later.update(_gp_later_file(), change, _gp_later_today())
        for old in pushed:
            _gp_later_forget(old)
        kodi.log('gender proofread: no answer ({0}) -- queued, asked again '
                       'later'.format(report.get('why')), level='INFO')
    except Exception as e:
        kodi.log('gender proofread queue add failed: {0}'.format(e),
                       level='DEBUG')


class _GpLaterGone(Exception):
    """A queued translation's files went between the pick and the request."""


def gp_later_tick(should_cancel=None):
    """The service's turn: asks gemini-3.7-flash again for one queued
    proofread when one is due (gp_later's pacing). The corrected file replaces
    the translation where it is kept (and its copies), with their markers, and
    goes to the pool when sharing is on. Returns what happened, in a word (''
    when nothing was due). Never raises."""
    try:
        from resources.lib.ai_translation.proofread import gp_later
        queue = _gp_later_file()
        if not os.path.isfile(queue):
            return ''
        today = _gp_later_today()
        api_key = (settings.get_setting('api_key', '') or '').strip()
        ready = bool(settings.get_bool('gender_proofread', True) and api_key
                     and _gp_models_left())
        now = time.time()
        dropped = []

        # 1. under the lock, no network: the dead out, the one due picked
        def pick(state):
            dropped.extend(gp_later.prune(state, now, _gp_later_alive))
            entry = gp_later.due(state, now, today) if ready else None
            if entry is not None and min(_gp_try_age(m) for m in
                                         _gp_later_markers(entry)) < gp_later.FRESH_TRY:
                # an attempt for it may be running now (opened again, the job)
                gp_later.hold(entry, now)
                return ('held', None), True
            return ('', dict(entry) if entry else None), bool(dropped)
        word, entry = gp_later.update(queue, pick, today)
        for e in dropped:
            _gp_later_forget(e)
        if entry is None:
            return word
        marker, path = entry['marker'], entry['path']
        info = entry.get('info') or {}
        share = entry.get('share') or None
        # 2. outside the lock: the Hebrew-reference rule again (the file may
        # have been made again since: gemini-4-hydrogen alone then, 0.3.88),
        # and someone else's proofread in the pool (asked only with the pool
        # in use, as the list does)
        drop = ''
        hebrew = bool(
            _gp_hebrew_ref(path, info if _gp_ref_tier(path, entry.get('ref_hash')) else None)
            or any(_gp_hebrew_ref(c) for c in entry.get('copies') or []
                   if c and os.path.isfile(c)))
        if (share and pool_contribute is not None and pool_client.use_enabled()
                and _pool_variant_proofread(info, share['hash'])):
            drop = 'pooled'     # the next open from the list takes theirs
        # its one model out of today's quota while another has some: it
        # waits its turn again and the queue's other entries are not held up
        wait = hebrew and not drop and not _gp_models_left(True)

        # 3. under the lock: dropped, or claimed -- the request counts now
        def settle(state):
            mine = gp_later.find(state, marker)
            if mine is None:
                return '', False
            if drop:
                gp_later.remove(state, marker)
                return drop, True
            if wait:
                gp_later.hold(mine, time.time())
                return 'quota', True
            t = time.time()
            if (mine.get('next') or 0) > t or not gp_later.can_send(state, t, today):
                return '', False
            gp_later.claim(state, mine, t, today)
            return 'claimed', True
        word = gp_later.update(queue, settle, today)
        if word != 'claimed':
            if drop and word == drop:
                _gp_later_forget(entry)
            return word
        markers = [marker] + [c for c in entry.get('copies') or [] if c and os.path.isfile(c)]
        report = {}
        try:
            source_text = translation_cache.load_text(entry['src']) or ''
            blocks = srt_blocks.parse_blocks(translation_cache.load_text(path) or '')
            if not (source_text and blocks):
                # gone since the pick (the cache's own clean-up): nothing sent
                raise _GpLaterGone()
            model = settings.get_setting('model', 'gemini-3.5-flash-lite') \
                or 'gemini-3.5-flash-lite'
            cast = _title_cast(info, (info.get('imdb_id') or '').strip(),
                               info.get('season') or '', info.get('episode') or '')[0]

            def stopped():
                return bool(should_cancel and should_cancel())
            for m in markers:
                _gp_try_mark(m, True)
            proofed, report = _run_gender_proofread(
                blocks, source_text, api_key, model, cast, stopped,
                background=True, rounds=False, hebrew_ref=hebrew)
            _gp_settle(markers, report, stopped)
            if report.get('model'):
                final = srt_blocks.stitch_blocks(proofed) if proofed != blocks else None
                for m in markers:
                    if final is not None:
                        translation_cache.save_text(m if m != marker else path, final)
                    _gp_mark(m, report)
                if share and pool_contribute is not None and pool_client.share_enabled():
                    text = final if final is not None else (translation_cache.load_text(path) or '')
                    if _pool_quality_ok(source_text, text):
                        pool_contribute.contribute_once(
                            info, share['hash'], share.get('lang') or 'en', text,
                            marker_path=_gp_marker(marker),
                            release_override=share.get('release') or None,
                            kind=share.get('kind') or 'ai',
                            proofread=_gp_stats(report) or True)
        except _GpLaterGone:
            report = {'why': 'gone'}
        except Exception as e:
            kodi.log('gender proofread from the queue failed: {0}'.format(e),
                           level='WARNING')
        why = 'answered' if report.get('model') else (report.get('why') or 'error')
        keep = why != 'answered' and _gp_later_retry(report)

        # 4. under the lock: the outcome
        def outcome(state):
            if not report.get('asked'):
                gp_later.refund(state, today)
            mine = gp_later.find(state, marker)
            if not keep or mine is None:
                gp_later.remove(state, marker)
                return True, True
            gp_later.retry_later(mine, time.time())
            return False, True
        try:
            if gp_later.update(queue, outcome, today):
                _gp_later_forget(entry)
        except Exception as e:
            # the claim keeps it away for its retry time; the request counted
            kodi.log('gender proofread queue save failed: {0}'.format(e),
                           level='WARNING')
        kodi.log('gender proofread from the queue: {0}'.format(why), level='INFO')
        return why
    except Exception as e:
        kodi.log('gender proofread queue failed: {0}'.format(e), level='WARNING')
        return 'error'


def _pool_gp_base(source_hash):
    """A pooled variant's immutable local copy (_pool_source_text): the file
    its proofread corrects and its marker hangs on."""
    sid = _pool_local_id(source_hash)
    if not sid:
        return ''
    return os.path.join(kodi.cache_dir(), 'pool_{0}.source.srt'.format(sid))


def proofread_later(link, selection):
    """A pooled translation picked from the list, or one the automatic
    translation on play applied, is shown with no live preview to swap a
    corrected file into. Its gender proofread then runs in a job of its own
    (bg_translate_picker, the link marked proofread_only: it proofreads and
    swaps, it never translates). Fired only when one is likely due; the job
    checks again. Never raises."""
    try:
        payload = _decode_link(link or '') or {}
        kind = payload.get('type')
        if kind == 'embedded_ai' and not payload.get('proofread_only'):
            # made here moments ago: the translation of its kept source
            payload = _EMBEDDED_RESOLVED.get(link) or {}
            kind = payload.get('type')
        if kind not in ('pool', 'engine_ai', 'ai') or payload.get('proofread_only'):
            return
        if kind == 'pool' and (payload.get('pool_kind') or 'ai') == 'ktuvit':
            return
        if not (settings.get_bool('gender_proofread', True)
                and (settings.get_setting('api_key', '') or '').strip()
                and _gp_models_left()):
            return
        source_id = ''
        if kind == 'pool':
            base = _pool_gp_base(payload.get('hash'))
            if not base or _gp_done(base) or _gp_trying(base) or _gp_nosrc(base):
                return
            source_id = _pool_job_id(base)
        import base64
        import xbmc

        def b64(s):
            return base64.b64encode(s.encode('utf-8')).decode('ascii')
        sel = {k: (selection or {}).get(k, '')
               for k in ('token', 'link_hash', 'stream_hash')}
        xbmc.executebuiltin(
            'RunScript(service.subtitles.kodipovilai,'
            'action=bg_translate_picker,link_b64={0},source_id_b64={1},'
            'selection_b64={2})'.format(
                b64(_encode_link(dict(payload, proofread_only=True))),
                b64(source_id), b64(json.dumps(sel, separators=(',', ':')))))
    except Exception as e:
        kodi.log('proofread_later failed: {0}'.format(e), level='DEBUG')


_GP_NOSRC_SECS = 7 * 86400


def _gp_nosrc(gp_base):
    """A pooled variant whose source subtitle was not in the list lately."""
    try:
        return time.time() - os.path.getmtime(gp_base + '.gp.nosrc') < _GP_NOSRC_SECS
    except OSError:
        return False


def _pool_job_id(gp_base):
    """The live-translation id of a pooled variant's proofread job."""
    return 'pool_' + os.path.basename(gp_base)[len('pool_'):-len('.source.srt')]


def _pool_variant_proofread(info, pool_hash, network=True):
    """Whether the pool's variant `pool_hash` is proofread (gp). Uses the
    lookup the list already made; asks the Worker only with `network`."""
    if pool_contribute is None or not pool_hash:
        return False
    try:
        variants = pool_client.lookup_cached(info)
        if variants is None and network:
            variants = pool_client.lookup(info)
        return any(isinstance(v, dict) and v.get('hash') == pool_hash
                   and v.get('gp') for v in variants or [])
    except Exception:
        return False


def _pool_proofread_text(info, pool_hash):
    """The pool's copy of `pool_hash` when someone already proofread it,
    else ''."""
    if _pool_variant_proofread(info, pool_hash):
        try:
            return pool_client.fetch(info, pool_hash) or ''
        except Exception:
            pass
    return ''


# An embedded_ai link resolved in this process -> the 'ai' payload it became,
# its source at the kept copy. The automatic translation on play (autosub)
# resolves an embedded track with no live preview, so no proofread ran; its
# proofread_later job takes this payload and proofreads that translation
# without reading the track again (field, 2 Nov 2026: most embedded
# translations without a proofread, none of them proofread later).
_EMBEDDED_RESOLVED = {}


def _remember_embedded(link, payload):
    from resources.lib.ai_translation.proofread.gp_jobs import _EMBEDDED_RESOLVED_KEEP
    try:
        _EMBEDDED_RESOLVED.pop(link, None)
        _EMBEDDED_RESOLVED[link] = dict(payload)
        while len(_EMBEDDED_RESOLVED) > _EMBEDDED_RESOLVED_KEEP:
            _EMBEDDED_RESOLVED.pop(next(iter(_EMBEDDED_RESOLVED)))
    except Exception:
        pass


def _keep_embedded_source(path):
    """Keep the source of an embedded translation (the video's own text track,
    or an external subtitle retimed onto its cues) under its content hash:
    `path` is overwritten by the next embedded translation in that language,
    and a pooled copy of this translation is proofread against it later
    (_pool_pick_emb_source). The copy's path ('' when none); the same bytes,
    so the translation it keys is the same one. Never raises."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            raw = f.read()
        cid = _content_hash(_prepare_source(raw))
        if cid:
            kept = _embedded_source_path(cid)
            shutil.copyfile(path, kept)
            return kept
    except Exception as e:
        kodi.log('embedded source copy failed: {0}'.format(e),
                       level='DEBUG')
    return ''
_EMBEDDED_RESOLVED_KEEP = 4
