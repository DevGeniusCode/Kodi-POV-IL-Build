# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# AI stages, 4 of 10 -- request: translator choice (Google or Gemini), cast, model, sampling and thinking
# settings, the prompt template and the source blocks.

from resources.lib.ai_translation.providers import gemini
from resources.lib.core import kodi, settings
from resources.lib.ai_translation.context import prompt
from resources.lib.core.srt import blocks as srt_blocks
from resources.lib.ai_translation.dispatch.pacing import thinking_params
from resources.lib.ai_translation.proofread.gp_run import title_cast
from resources.lib.ai_translation.providers.google_flow import google_translate_and_save
from resources.lib.ai_translation.providers.model_fallback import model_fallback_notice, ModelFallback
from resources.lib.ai_translation.job.ai.prepare import deliver
from resources.lib.ai_translation.job.ai.tuning import NUMBERED_REQUESTS, WHOLE_REQUEST_MAX_CHARS
from resources.lib.ai_translation.job.state import NEXT


def thinking_for(S, for_model):
    return thinking_params(S.thinking_raw, for_model)


def request(S):
    """Translator choice, cast, model, sampling and thinking settings, prompt, source blocks."""
    # Translator selection. 'google' (the user picked it in settings) ->
    # translate with Google Translate now and skip Gemini entirely. Google
    # output is machine quality, so it is never shared to the pool. 'ai'
    # (default) falls through to the Gemini path below (which guides the user
    # to connect a key if none is set). translation_mode 'none' never reaches
    # here (list_candidates hands back raw foreign subs instead).
    _translation_mode = settings.get_setting('translation_mode', 'ai') or 'ai'
    # Milestone (cheap, INFO): fires once we're past the language check and are
    # committed to actually translating (as opposed to the Hebrew-passthrough
    # above). Reports which translator we're about to use, so a future report of
    # "said it was translating but nothing happened" is diagnosable from here on.
    kodi.log('translate step: language ok, mode={0}'.format(
        _translation_mode), level='INFO')
    if _translation_mode == 'google':
        return deliver(S, google_translate_and_save(
            S.src_text, S.source_lang, S.translated, S.info))

    # Bisection markers (temporary, cheap): a report showed the translation thread
    # going silent between 'Starting translation' and the dispatch summary, never
    # reaching the executor. These pin WHICH pre-dispatch step hangs.
    kodi.log('translate step: resolving cast metadata', level='INFO')
    S.cast, title, year = title_cast(S.info, S.imdb_id, S.season, S.episode)

    kodi.log('translate step: cast ready ({0} members); building '
                   'prompt'.format(len(S.cast or [])), level='INFO')
    # Prompt + chunk + translate via Gemini.
    S.api_key = settings.get_setting('api_key', '')
    if not S.api_key:
        kodi.notify(kodi.localised(32632))
        return None
    S.models = ModelFallback(
        settings.get_setting('model', 'gemini-3.5-flash-lite')
        or 'gemini-3.5-flash-lite', api_key=S.api_key)
    S.model = S.models.model
    if S.models.why:
        kodi.log('model {0} passed over ({1}, seen in this session): '
                       'translating with {2}'.format(
                           S.models.chosen, S.models.why, S.model), level='INFO')
        model_fallback_notice(S.models.chosen, S.models.why)
    # Gemini tuning (validated A/B on 2.5/3.1): keep temperature at Google's
    # recommended default 1.0 (lowering it degrades Gemini 3 reasoning), use
    # thinking_level MEDIUM (HIGH burns the output budget -> truncation +
    # garbling and is no more accurate; MEDIUM finishes clean, ~8x cheaper,
    # best gender accuracy). top_p (default 0.95) has NO effect on the
    # prompt-level safety block -- verified live: a blocked prompt blocks
    # identically with top_p unset / 0.9 / 0.95, because the block is decided
    # on the INPUT, before sampling -- but it does shape output quality.
    #
    # BOTH ARE READ UNCONDITIONALLY AND PASSED UNCONDITIONALLY. Whether they
    # reach Google is gemini.sampling_params_supported()'s call, made per
    # model in the one place that builds the request: Google deprecated
    # temperature / top_p / top_k from Gemini 3.5 on ("ignored" now, HTTP 400
    # in a future generation), so from 3.5 up the request carries neither.
    # Reading them here anyway keeps the stored settings intact for a user who
    # switches back to 2.5 or 3.1, and keeps this function ignorant of a rule
    # that belongs next to the payload.
    S.temperature = settings.get_float('temperature', 1.0)
    S.top_p = settings.get_float('top_p', 0.95)
    if gemini.sampling_params_supported(S.model):
        kodi.log('gemini sampling: temperature={0} top_p={1} (model {2} '
                       'still honours them)'.format(S.temperature, S.top_p, S.model),
                       level='INFO')
    else:
        kodi.log('gemini sampling: temperature/top_p NOT sent -- {0} is '
                       'Gemini 3.5 or newer, where Google deprecated them'
                       .format(S.model), level='INFO')
    S.thinking_raw = (settings.get_setting('thinking_budget', 'medium')
                    or 'medium').strip().lower()

    S.thinking_level, S.thinking_budget = thinking_for(S, S.model)
    S.whole_subtitle_request = settings.get_bool(
        'whole_subtitle_request', False)
    if S.whole_subtitle_request and len(S.src_text) > WHOLE_REQUEST_MAX_CHARS:
        kodi.log(
            'whole-subtitle request disabled for this source: {0} chars > {1} '
            'cap -- a single request would truncate; using chunked mode'.format(
                len(S.src_text), WHOLE_REQUEST_MAX_CHARS), level='WARNING')
        S.whole_subtitle_request = False
    S.max_output_tokens = 65535 if S.whole_subtitle_request else 16384
    S.gemini_timeout = 300 if S.whole_subtitle_request else None
    S.chunk_lines = settings.get_int('chunk_lines', 50)

    S.prompt_template = prompt.build(
        source_lang=S.source_lang,
        title=title,
        year=year,
        cast=S.cast,
        is_episode=S.info.get('is_episode', False),
        tvshow=S.info.get('tvshow', ''),
        season=S.season,
        episode=S.episode,
        numbered=NUMBERED_REQUESTS,
    )

    S.blocks = srt_blocks.parse_blocks(S.src_text)
    if not S.blocks:
        kodi.log('Source SRT had no parseable blocks',
                       level='WARNING')
        return None
    kodi.log('translate step: {0} blocks parsed, gender_ref={1} -- '
                   'setting up chunks'.format(len(S.blocks), S.ar_on), level='INFO')
    return NEXT
