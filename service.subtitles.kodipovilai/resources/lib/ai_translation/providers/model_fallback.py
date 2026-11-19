# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The model a translation runs on and the fall-back when it does not answer.

import threading
import time
from resources.lib.core import kodi
from resources.lib.ai_translation.progress import status


# The model every install starts on, and the one a translation goes on with
# when the viewer's own choice does not answer.
FALLBACK_MODEL = 'gemini-3.5-flash-lite'
# How long a model that did not answer is passed over by the translations
# that follow (seconds), by why: 'busy' a 5xx answer (two in a row for a
# paid key), 'slow' no reply within the timeout, 'quota' its free requests for the day are spent,
# 'gone' Google does not serve it to this key (404).
_MODEL_OUT_S = {'busy': 600, 'slow': 600, 'quota': 3 * 3600, 'gone': 6 * 3600}
_MODEL_OUT_PROP = 'povil.gemini.model_out.'
# How long a chunk waits for a chosen (non-default) model's reply before it
# counts as 'slow' (seconds; gemini.REQUEST_TIMEOUT, 90, for the default
# model, which has nothing behind it).
CHOSEN_MODEL_TIMEOUT = 60


def _model_out_key(model, api_key=''):
    # per API key: another key has its own allowance and its own access
    import hashlib
    return '{0}{1}.{2}'.format(
        _MODEL_OUT_PROP, model,
        hashlib.sha256((api_key or '').encode('utf-8', 'replace')).hexdigest()[:10])


def _model_out_note(model, why, now=None, api_key=''):
    """Remember, for this Kodi session, that `model` did not answer."""
    try:
        import xbmcgui as _g
        until = (time.time() if now is None else now) + _MODEL_OUT_S[why]
        _g.Window(10000).setProperty(_model_out_key(model, api_key),
                                     '{0:.0f}|{1}'.format(until, why))
    except Exception:
        pass


def _model_out(model, now=None, api_key=''):
    """Why `model` is being passed over right now, or ''."""
    try:
        import xbmcgui as _g
        raw = _g.Window(10000).getProperty(_model_out_key(model, api_key)) or ''
        until, _sep, why = raw.partition('|')
        if why in _MODEL_OUT_S and float(until) > (
                time.time() if now is None else now):
            return why
    except Exception:
        pass
    return ''


def _model_label(model):
    """'gemini-3.8-flash' -> '3.8 Flash', as the settings list names it."""
    name = (model or '').strip()
    if name.lower().startswith('gemini-'):
        name = name[7:]
    return ' '.join(part.capitalize() for part in name.split('-')) or 'Gemini'


def model_fallback_notice(chosen, why):
    """Tell the viewer the translation is on another model than their pick.

    A model Google no longer serves is theirs to change in the settings, so
    that one is shown even during the automatic translation on play, which
    shows no other toast (the abort it replaces was shown there too). A busy
    model or a spent allowance passes on its own and stays a status."""
    message = _model_fallback_message(chosen, why)
    if why == 'gone':
        try:
            kodi.notify(message, time_ms=6000)
        except Exception:
            pass
    else:
        status(message, time_ms=5000)


def _model_fallback_message(chosen, why, fallback=None):
    old, new = _model_label(chosen), _model_label(fallback or FALLBACK_MODEL)
    return {
        'gone': 'AI: המודל {0} כבר לא זמין אצל Google — מתרגם עם {1}. '
                'אפשר להחליף מודל בהגדרות',
        'quota': 'AI: נגמרה המכסה היומית של {0} — ממשיך עם {1}',
    }.get(why, 'AI: {0} עמוס כרגע — ממשיך עם {1}').format(old, new)


class ModelFallback(object):
    """Which model one translation asks.

    A viewer who picked another model than the default got no AI at all when
    that model did not answer: 503 after 503 through a five-step backoff
    (230 s a chunk), or three 90 s timeouts, then Google Translate for
    whatever was left -- "the subtitles arrive when the film ends" (field,
    Nov 2026; that night every full Flash model answered 503 or nothing while
    Flash-Lite answered in two seconds, and three ids in the settings list
    were no longer served at all). The translation now goes on with the
    default model, at once, and the ones that follow start on it for a while
    (_MODEL_OUT_S). The default model itself has nothing to fall back to and
    keeps the behaviour it had.
    """

    def __init__(self, chosen, fallback=FALLBACK_MODEL, out=_model_out,
                 note=_model_out_note, api_key=''):
        self.chosen = (chosen or '').strip() or fallback
        self.fallback = fallback
        self._note = note
        self._api_key = api_key or ''
        self._lock = threading.Lock()
        # why this translation is not on the chosen model ('' when it is)
        self.why = (out(self.chosen, api_key=self._api_key)
                    if self.chosen != fallback else '')
        self.model = fallback if self.why else self.chosen

    def give_up(self, asked, why):
        """A request to `asked` failed for `why`. True: send it again at
        once, self.model has (or already had) another model for it. False:
        `asked` is the last model there is; the caller's own retries apply."""
        with self._lock:
            if asked != self.model:
                return True          # another chunk switched meanwhile
            if self.model == self.fallback:
                return False
            self.model, self.why = self.fallback, why
        try:
            self._note(self.chosen, why, api_key=self._api_key)
        except Exception:
            pass
        return True
