# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Gender proofread: markers, statistics, references and the model list.

import json
import os
import time
from resources.lib.core import settings


# ---- gender proofread -------------------------------------------------
# One request to a stronger model corrects a translation's gender and number
# (gender_proofread.py). 0.3.58 ran it after a fresh translation only; since
# 0.3.59 an older translation gets it the first time it is opened from the
# list -- a cached one, a pooled one -- and the pool takes the corrected file
# (proofread=1: the worker replaces the pooled variant of the same source).

def _gp_marker(path):
    """Next to a translation: its proofread ran (a model answered), so
    opening it again spends no request."""
    return path + '.gp'


def _gp_done(path):
    return bool(path) and os.path.isfile(_gp_marker(path))


def _gp_mark(path, report=None):
    """The marker; with the proofread's report, it keeps the model and the
    lines it fixed for an upload offered again later (_gp_saved)."""
    stats = _gp_stats(report)
    try:
        with open(_gp_marker(path), 'w', encoding='utf-8') as f:
            f.write(json.dumps(stats) if stats else '1')
    except OSError:
        pass


def _gp_stats(report):
    """What the pool's dashboard shows of a proofread: the model that answered
    and the lines it fixed. {} when no model answered."""
    model = str((report or {}).get('model') or '')
    if not model:
        return {}
    try:
        applied = max(0, int((report or {}).get('applied') or 0))
    except (TypeError, ValueError):
        applied = 0
    return {'model': model, 'applied': applied}


def _gp_saved(path):
    """The stats a marker kept ({} from a marker written before 0.3.60)."""
    try:
        with open(_gp_marker(path), 'r', encoding='utf-8') as f:
            return _gp_stats(json.loads(f.read()))
    except Exception:
        return {}


# Not after a Hebrew gender reference (0.3.72). Two blind studies on the same
# eight TV episodes (4 Nov 2026, judged by two AI judges and audited by the
# tester): with a Hebrew subtitle as the reference, the proofread broke more
# lines than it fixed (15 to 12) -- that reference already carries the right
# gender, and the proofread second-guesses it; with an Arabic one it fixed 21
# and broke 7. A translation made here says which reference it used (the
# .gpref beside it). One of unknown origin (pooled, or made before 0.3.72)
# that was made with a reference -- a pooled "_ar" variant, an ".ar" tier
# file; a plain one had none -- counts as Hebrew-referenced when the title's
# last search found a human Hebrew subtitle: the reference search takes
# Hebrew first (an Arabic one may still have won when the Hebrew did not
# align; the search cannot tell).
# Since 0.3.88 such a translation IS proofread, by gemini-4-hydrogen alone
# (gender_proofread.HEBREW_REF_MODELS; blind study 007: 13-3-3 where the
# models above had 12-15-3 and 19-8-3). What is asked here is therefore no
# longer "proofread or not" but "which models" (_gp_models).

def _gp_ref_marker(path):
    return path + '.gpref'


def _gp_ref_mark(path, lang):
    """Beside a translation: the language of its gender reference ('-' when
    it had none, '?' when it had one of a language not known); None when
    nothing is known (the marker goes)."""
    try:
        if lang is None:
            os.remove(_gp_ref_marker(path))
            return
        with open(_gp_ref_marker(path), 'w', encoding='utf-8') as f:
            f.write(lang or '-')
    except OSError:
        pass


def _gp_ref_tier(path, pooled_hash=None):
    """Whether a translation of unknown origin was made with a gender
    reference: the pool keeps such a variant under "<hash>_ar", the cache in
    the ".ar" tier -- or in the plain one marked '?' (a pooled "_ar" variant
    taken with the reference setting off)."""
    if pooled_hash:
        return pooled_hash.endswith('_ar')
    if os.path.basename(path or '').endswith('.ar.he.srt'):
        return True
    try:
        with open(_gp_ref_marker(path), 'r', encoding='utf-8') as f:
            return f.read().strip() == '?'
    except Exception:
        return False


def _hebrew_subtitle_listed(info):
    """Whether the title's last search (the list's, or the reference search's)
    found a human Hebrew subtitle."""
    try:
        from resources.lib.ai_translation.context import gender_reference
        from resources.lib.fetching import bridge
        return any(isinstance(c, dict) and gender_reference._chain_lang_of(c) == 'he'
                   for c in bridge.reusable_results(info) or [])
    except Exception:
        return False


def _gp_hebrew_ref(path, info=None):
    """Whether the translation at `path` was made with a Hebrew gender
    reference: its .gpref when it was made here, else -- `info` given for one
    of unknown origin made with a reference (_gp_ref_tier) -- the title's
    search; else not."""
    try:
        with open(_gp_ref_marker(path), 'r', encoding='utf-8') as f:
            mark = f.read().strip()
        if mark != '?':
            return mark == 'he'
    except OSError:
        pass
    return bool(info) and _hebrew_subtitle_listed(info)


# An attempt in progress, or one no model answered: no new one for a while
# (two opens in its minutes would spend two requests; an overloaded model is
# not asked again at every open).
_GP_RETRY_SECS = 6 * 3600
# A model took the request and its reply was of no use (blocked, cut off): the
# same title would most likely come back the same, so not for a week.
_GP_SPENT_SECS = 7 * 86400


def _gp_try_marker(path):
    return path + '.gp.try'


def _gp_trying(path):
    try:
        return time.time() - os.path.getmtime(_gp_try_marker(path)) < _GP_RETRY_SECS
    except OSError:
        return False


def _gp_try_mark(path, on, hold=None):
    """The in-progress marker on (for _GP_RETRY_SECS, or `hold` seconds) or
    off."""
    try:
        if on:
            with open(_gp_try_marker(path), 'w', encoding='utf-8') as f:
                f.write('1')
            if hold:
                # _gp_trying measures from the marker's time
                t = time.time() + hold - _GP_RETRY_SECS
                os.utime(_gp_try_marker(path), (t, t))
        else:
            os.remove(_gp_try_marker(path))
    except OSError:
        pass


def _gp_settle(markers, report, stopped):
    """The in-progress markers after a proofread: off when a model answered;
    a week when one took the request and its reply was of no use -- even if
    the viewer moved on meanwhile, the request is spent; off when nothing was
    asked (every model out of quota) or the viewer moved on; else they stay
    (_GP_RETRY_SECS)."""
    if report.get('model'):
        for m in markers:
            _gp_try_mark(m, False)
    elif report.get('spent'):
        for m in markers:
            _gp_try_mark(m, True, hold=_GP_SPENT_SECS)
    elif not report.get('asked') or stopped():
        for m in markers:
            _gp_try_mark(m, False)


def _gp_models(hebrew_ref=False):
    """The proofread's models for a translation: gemini-4-hydrogen alone for
    one made with a Hebrew gender reference."""
    from resources.lib.ai_translation.proofread import gender_proofread
    return (gender_proofread.HEBREW_REF_MODELS if hebrew_ref
            else gender_proofread.MODELS)


def _gp_models_left(hebrew_ref=False):
    """Whether a proofread model (_gp_models) still has quota today -- and AI is the chosen mode at
    all: with "Google Translate" or "no translation" chosen nothing is proofread (0.3.96; a translation
    from the cache or the pool was, and the viewer saw Gemini's "busy")."""
    try:
        if (settings.get_setting('translation_mode', 'ai') or 'ai') != 'ai':
            return False
        from resources.lib.ai_translation.proofread import gender_proofread
        return any(not gender_proofread.exhausted_today(m, settings)
                   for m in _gp_models(hebrew_ref))
    except Exception:
        return False
