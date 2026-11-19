# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# AI stages, 6 of 10 -- plan: telemetry state, per-chunk counters, the chunk plan, the pacing of requests.

import threading
import time
from resources.lib.core import kodi, settings
from resources.lib.core.srt import blocks as srt_blocks
from resources.lib.ai_translation.dispatch.pacing import gemini_rpm_interval
from resources.lib.ai_translation.proofread.gp_state import gp_models_left, gp_stats
from resources.lib.ai_translation.job.state import NEXT


def count(S, kind, n=1):
    try:
        with S.chunk_lock:
            S.chunk_stat[kind] = S.chunk_stat.get(kind, 0) + n
    except Exception:
        pass


def gp_event_note(S, ok, note):
    """The telemetry's word on a translation with no proofread model
    (_gp_why); 'off' with the setting off, 'later' on a path without a
    live preview (proofread_later's job, or the next open from the
    list, does it) -- 'quota' there when no model has quota left for
    that job; '' otherwise."""
    try:
        if not ok or note or S.gp_report.get('model'):
            return ''
        if not settings.get_bool('gender_proofread', True):
            return 'off'
        if S.progressive_cb is None:
            # the job proofread_later starts asks only a model with quota
            # (after a Hebrew reference: gemini-4-hydrogen alone)
            if S.gp_ref == 'he':
                return 'later' if gp_models_left(True) else 'quota'
            return 'later' if gp_models_left() else 'quota'
        return S.gp_report.get('why') or ''
    except Exception:
        return ''


def emit(S, ok, note=''):
    if S.telemetry_done[0]:
        return
    S.telemetry_done[0] = True
    try:
        from resources.lib import telemetry
        method = ('ai_ar' if (S.ar_on and S.used_ar)
                  else ('ai_fallback' if S.ar_on else 'ai_plain'))
        # reason: WHY it ended up on this method (esp. fallback). For
        # ai_plain the option is off; otherwise take arabic_gender's reason
        # (ok / no_arabic / no_align / crash). The alignment diag (scale/
        # vote/overlap) for a near-miss goes in 'note'.
        if method == 'ai_plain':
            reason = 'option_off'
        else:
            reason = str(S.ar_diag.get('reason') or '')
        ev_note = note or ('' if reason in ('ok', '') else S.ar_diag.get('diag', ''))
        if (not ev_note and reason == 'ok'
                and str(S.ar_diag.get('diag') or '').startswith('windowed')):
            # aligned by the reference's own windowed fallback (0.3.64,
            # arabic_gender): the dashboard counts it against the gate
            ev_note = 'windowed reference'
        telemetry.report({
            'type': 'episode' if S.info.get('is_episode') else 'movie',
            'title': (S.info.get('tvshow') or S.info.get('title') or '')[:120],
            'season': str(S.info.get('season') or ''),
            'episode': str(S.info.get('episode') or ''),
            'year': str(S.info.get('year') or ''),
            'src': S.source_lang or '',
            'method': method,
            # Embedded-sourced (translated from the video's OWN subtitle
            # track -> pooled as 'ai_emb'). It still goes through the exact
            # same AI + gender pipeline, so `method` above is its real
            # gender path; this flag just lets the dashboard mark it as
            # "תרגום מובנה" wherever it already appears (recent / by-method /
            # top titles), without splitting it out of the method stats.
            'emb': 1 if S.pool_kind == 'ai_emb' else 0,
            'reason': reason,
            'ar_cands': int(S.ar_diag.get('cands') or 0),
            'dur': max(0, int(time.time() - S.t0)),
            'ok': 1 if ok else 0,
            'note': str(ev_note or '')[:80],
            'hinted': len(S.ar_map or {}),
            'ref_lang': (S.ref_lang if (S.ar_on and S.used_ar) else ''),
            'model': S.model,
            'think': str(S.thinking_level or S.thinking_budget or ''),
            # Per-chunk outcome (entry counts): translated WITH Arabic,
            # translated after DROPPING Arabic, and kept as source; plus the
            # number of prompt-block events hit and the total chunk count.
            'ent_ar': int(S.chunk_stat.get('ar', 0)),
            'ent_alt': int(S.chunk_stat.get('alt', 0)),
            'ent_noar': int(S.chunk_stat.get('noar', 0)),
            'ent_src': int(S.chunk_stat.get('src', 0)),
            'blocks': int(S.chunk_stat.get('blocks', 0)),
            'chunks': int(S.total or 0),
            # the gender proofread (0.3.61): the model that answered and
            # the lines it fixed, for the dashboard's Recent activity
            'gp_model': gp_stats(S.gp_report).get('model', ''),
            'gp_fixed': gp_stats(S.gp_report).get('applied', 0)
            if S.gp_report.get('model') else None,
            # and why none did (0.3.67): read from what already happened
            'gp_note': gp_event_note(S, ok, note),
        })
    except Exception:
        pass


def plan(S):
    """Telemetry state, counters, the chunk plan, request pacing."""
    # Anonymous usage telemetry (fire-and-forget, fully guarded). One event per
    # AI translation outcome, recording the METHOD so we can see what share uses
    # the new Arabic-gender path (ai_ar) vs fell back to plain (ai_fallback) vs
    # never had it on (ai_plain), plus success/failure. _telemetry_done guards
    # against double-emit across the multiple return paths below.
    S.telemetry_done = [False]
    S.t0 = time.time()  # translation-duration clock for telemetry

    # Per-chunk outcome counters (entries, not chunks) for telemetry, so the
    # dashboard can show -- of a translation that hit a block on some chunk --
    # how many entries went through WITH the Arabic prompt, how many fell back
    # to English-only (Arabic dropped), and how many were kept as source. Updated
    # from parallel worker threads, so guard with a lock.
    import threading as _threading
    S.chunk_lock = _threading.Lock()
    S.chunk_stat = {'ar': 0, 'alt': 0, 'noar': 0, 'src': 0, 'blocks': 0}

    # The gender proofread's report (_proofread_gender, below), for the marker,
    # the pool upload and the telemetry event; empty until a model answers.
    S.gp_report = {}

    if S.whole_subtitle_request:
        S.chunks = [S.blocks]
        kodi.notify(
            'AI: מתרגם את כל הכתוביות בפעימה אחת. זה יכול לקחת כמה דקות.',
            time_ms=5000)
    else:
        S.chunks = list(srt_blocks.chunk_blocks(S.blocks, per_chunk=S.chunk_lines))
    S.total = len(S.chunks)
    # The cached Google file cut like the chunks, for the live preview's
    # pending chunks -- only when it lines up entry for entry.
    S.google_chunks = None
    if S.google_prev:
        _gblocks = srt_blocks.parse_blocks(S.google_prev)
        if len(_gblocks) == sum(len(_c) for _c in S.chunks):
            S.google_chunks, _at = [], 0
            for _c in S.chunks:
                S.google_chunks.append(_gblocks[_at:_at + len(_c)])
                _at += len(_c)
    # Global request pacing: keep Gemini request starts under the free RPM cap
    # so we (almost) never hit the per-minute limit in the first place -- no 429s
    # to retry, no wasted requests, no toast spam. The cap is MODEL-AWARE: ~14 for
    # Flash-Lite (free ~15 RPM) but only ~4 for regular Flash (free ~5 RPM), so
    # picking regular Flash no longer 429-storms at Flash-Lite's pace.
    # `ai_paid_mode` (a paid Gemini plan has thousands of RPM) disables the pacing
    # entirely, since the free cap only slows a paid key down.
    # Paid tier: no pacing unless gemini_rpm is pinned. Free tier: the model's
    # free ceiling, a lower pinned gemini_rpm is honoured (gemini_rpm_interval).
    S.paid_mode = settings.get_bool('ai_paid_mode', False)
    S.rpm_interval = gemini_rpm_interval(S.paid_mode, S.model)
    # Show the "rate limited" toast at most ONCE per job (shared across chunks),
    # not once per chunk per retry.
    S.ratelimit_notified = [False]

    S.switch_lock = threading.Lock()
    return NEXT
