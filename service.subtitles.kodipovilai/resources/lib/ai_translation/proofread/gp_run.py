# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Gender proofread: when it is due and the request itself.

import os
import time
from resources.lib.core.cache import translation_cache
from resources.lib.ai_translation.providers import gemini
from resources.lib.core import kodi, settings
from resources.lib.ai_translation.repairs import postfix_leaks
from resources.lib.core.srt import blocks as srt_blocks
from resources.lib.ai_translation.context import tmdb_helper
from resources.lib.ai_translation.dispatch.pacing import _is_mostly_hebrew
from resources.lib.ai_translation.proofread.gp_state import _gp_done, _gp_hebrew_ref, _gp_mark, _gp_models, _gp_models_left, _gp_settle, _gp_stats, _gp_try_mark, _gp_trying
from resources.lib.ai_translation.providers.google_flow import _is_google_translated


def _proofread_due(path, source_text, progressive_cb, marker=None,
                   held=False, info=None):
    """Whether an opened translation gets the proofread now: from the list
    (a live preview to swap in), the setting on, a key, not proofread before
    (`marker`, else `path`), AI-made (not Google, not a Hebrew source passed
    through) and a model with quota left -- gemini-4-hydrogen alone for one
    made with a Hebrew gender reference (`info`: the title, for one of
    unknown origin made with a reference). `source_text` None:
    all of it but the source (before looking for one). `held`: the
    in-progress marker is the caller's own, or not a reason here."""
    try:
        if not (progressive_cb is not None
                and settings.get_bool('gender_proofread', True)
                and (settings.get_setting('api_key', '') or '').strip()
                and os.path.isfile(path) and not _gp_done(marker or path)
                and (held or not _gp_trying(marker or path))
                and not _is_google_translated(path)
                and (source_text is None
                     or (source_text
                         and not _is_mostly_hebrew(source_text, min_ratio=0.60)))):
            return False
        if _gp_hebrew_ref(path, info):
            return bool(_gp_models_left(True))
        return bool(_gp_models_left())
    except Exception:
        return False


def _proofread_stopper(source_id, cancelled):
    """stopped() for a proofread: Kodi exits, the job is obsolete, or the
    viewer moved on to another subtitle."""
    def stopped():
        try:
            import xbmc
            import xbmcgui
            if xbmc.Monitor().abortRequested() or cancelled():
                return True
            win = xbmcgui.Window(10000)
            return (win.getProperty('ai_subs.live_translate_active') != '1'
                    or win.getProperty('ai_subs.live_translate_source') != source_id)
        except Exception:
            return True
    return stopped


def _gp_why(report, fails, stopped):
    """Why no model answered a proofread, in a word the dashboard shows beside
    its dash ('' when one did): 'unusable' (one took the request, its reply of
    no use), 'stopped' (the viewer moved on), 'key' (the key refused), 'quota'
    (every model out of today's requests), 'busy' (overloaded or at its
    per-minute limit), 'time' (the proofread's time budget ran out), 'error'
    (anything else)."""
    if report.get('model'):
        return ''
    if report.get('spent'):
        return 'unusable'
    if stopped:
        return 'stopped'
    for why in ('key', 'busy', 'time'):
        if why in fails:
            return why
    if fails and all(f == 'quota' for f in fails):
        return 'quota'
    return 'error'


# A background proofread (proofread_later's job: the translation is on screen
# and nothing waits for the swap) whose every model was busy -- overloaded or
# at its per-minute limit, none of them answered -- asks again after these
# waits, doubling, while the viewer stays on the translation, and asks no new
# model past _GP_JOB_BUDGET. A model out of today's quota or refusing unread
# (a retired id) beside a busy one does not end the rounds; a reply of no use
# or a refused key does. Field, 2-3 Nov 2026: of nine automatic
# translations on play whose proofread went to the job, three were proofread;
# in the QA Kodi the job's three models all answered 503 within 75 s, and a
# translation made on play is never opened again to retry it.
# A 503 counts toward the day's quota (QA key, 3 Nov: about 16 requests to
# gemini-3.7-flash, all but one refused with 503, then QuotaExceeded), so the
# rounds cost no more than the one round they replace: the job asks every
# model once, then one model a round (in turn from the last, skipping one out
# of today's quota), _GP_BUSY_ROUNDS times -- six requests at most; four with
# 0.3.75's one model, five with 0.3.79's two (gemini-4-hydrogen, then
# gemini-3.7-flash). Still no answer: the service asks
# again later (gp_later), one request at a time.
_GP_BUSY_WAIT = 120
_GP_BUSY_WAIT_MAX = 480
_GP_BUSY_ROUNDS = 3
_GP_JOB_BUDGET = 1800


def _run_gender_proofread(blocks, src_text, api_key, model, cast, stopped,
                          background=False, rounds=True, hebrew_ref=False):
    """(blocks with gender_proofread's corrections applied, report). The
    blocks come back as they are when no model answers. `src_text` is the
    prepared source the blocks were translated from; `model` the translator's
    (the usage counter holds that one only). `background`: nothing waits for
    it (_GP_BUSY_WAIT). `rounds` False: one request a model, no busy rounds
    (gp_later's ask again). `hebrew_ref`: the translation was made with a
    Hebrew gender reference (_gp_models). Never raises."""
    report = {}
    try:
        from resources.lib.ai_translation.proofread import gender_proofread
        _models = _gp_models(hebrew_ref)
        # no new model after this: a viewer waits for the swap (a background
        # proofread has longer)
        _start = time.monotonic()
        _budget = _GP_JOB_BUDGET if background else gender_proofread.TOTAL_BUDGET
        _deadline = [_start + _budget]
        # requests a model gets in one round: one in the background, where
        # the rounds are the retry (_GP_BUSY_WAIT)
        _tries = [1 if background else 2]

        _asked = [False]   # a request went out (the markers pause after one)
        _net = [False]     # a connection failed (the queue keeps it: gp_later)
        _fails = []        # why each model did not answer (report['why'])
        _cut = [False]     # the viewer's stop cut the proofread short

        def _request(proof_model, prompt_text):
            if gender_proofread.exhausted_today(proof_model, settings):
                _fails.append('quota')
                raise gender_proofread.Skip(proof_model)
            if time.monotonic() > _deadline[0]:
                _fails.append('time')
                raise gender_proofread.Skip(proof_model)
            for attempt in range(_tries[0]):
                if stopped():
                    _cut[0] = True
                    raise RuntimeError('proofread cancelled')
                if attempt and time.monotonic() > _deadline[0]:
                    _fails.append('time')
                    raise gender_proofread.Skip(proof_model)
                _asked[0] = True
                try:
                    # The usage counter is the translator's and holds one
                    # model: counted only when this IS that model.
                    return gemini.generate(
                        api_key=api_key, model=proof_model, prompt=prompt_text,
                        temperature=1.0, max_output_tokens=65535,
                        thinking_level='medium',
                        timeout=(10, gender_proofread.REQUEST_TIMEOUT),
                        count_usage=(proof_model == model))
                except gemini.InvalidKey:
                    _fails.append('key')
                    raise           # the same key for every model
                except gemini.QuotaExceeded:
                    gender_proofread.note_exhausted(proof_model, settings)
                    _fails.append('quota')
                    raise gender_proofread.Skip(proof_model)
                except (gemini.OverloadError, gemini.RateLimited) as e:
                    if getattr(e, 'answered', False):
                        # past its deadline (504): it was working on it
                        raise gender_proofread.Spent(proof_model)
                    if attempt or _tries[0] < 2:
                        _fails.append('busy')
                        raise gender_proofread.Skip(proof_model)
                    # a stop skips the proofread, never the translation
                    _until = time.monotonic() + max(
                        5, min(getattr(e, 'retry_after', 0) or 20, 60))
                    while time.monotonic() < _until and not stopped():
                        time.sleep(0.5)
                except gemini.GeminiError as e:
                    if getattr(e, 'answered', False):
                        # it took the request (cut off, blocked, no reply in
                        # time): counted today, and the next model would be
                        # a second request for this proofread
                        raise gender_proofread.Spent(proof_model)
                    # refused unread, a retired model id: the next may answer
                    if getattr(e, 'transport', False):
                        _net[0] = True
                    _fails.append('error')
                    raise gender_proofread.Skip(proof_model)
            _fails.append('busy')
            raise gender_proofread.Skip(proof_model)

        sources = postfix_leaks.aligned_source_texts(srt_blocks.parse_blocks(src_text), blocks)
        texts = [srt_blocks.block_text_only(b) or '' for b in blocks]
        people = [(c.get('character') or c.get('name'), c.get('gender'))
                  for c in (cast or []) if isinstance(c, dict)
                  and (c.get('character') or c.get('name'))
                  and c.get('gender') in ('female', 'male')]
        new_texts, report = gender_proofread.proofread(
            sources, texts, _request, models=_models,
            cast=people, cancelled=stopped)
        _wait, _rounds = _GP_BUSY_WAIT, 1
        _turn = list(reversed(_models))
        while (background and rounds and _rounds <= _GP_BUSY_ROUNDS
               and not report.get('model') and not report.get('spent')
               and not set(_fails) - {'busy', 'quota', 'error'}
               # the first round: something was busy; a later one asked one
               # model, and its quota or refusal leaves the others to ask
               and ('busy' in _fails or _rounds > 1)
               and time.monotonic() + _wait < _start + _budget and not stopped()):
            _left = [m for m in _turn
                     if not gender_proofread.exhausted_today(m, settings)]
            if not _left:
                break
            _model = _left[(_rounds - 1) % len(_left)]
            kodi.log('gender proofread: every model busy -- asking {0} '
                           'again in {1}s'.format(_model, _wait), level='INFO')
            _until = time.monotonic() + _wait
            while time.monotonic() < _until and not stopped():
                time.sleep(0.5)
            if stopped():
                break
            del _fails[:]
            _rounds += 1
            new_texts, report = gender_proofread.proofread(
                sources, texts, _request, models=(_model,), cast=people,
                cancelled=stopped)
            _wait = min(_wait * 2, _GP_BUSY_WAIT_MAX)
        report['rounds'] = _rounds
        report['asked'] = _asked[0]
        report['net'] = _net[0]
        # a stop that ended it, not one after every model had refused
        report['why'] = _gp_why(report, _fails,
                                _cut[0] or (not _fails and stopped()))
        if report.get('applied'):
            out = []
            for block, old, new in zip(blocks, texts, new_texts):
                lines = block.split('\n')
                out.append('\n'.join(lines[:2] + [new]) if new != old else block)
            blocks = out
        kodi.log(
            'gender proofread: {0} -- {1} proposed, {2} applied, {3} refused'
            '{4}'.format(report.get('model') or 'no model answered (tried {0})'.format(
                             ', '.join(report.get('tried') or []) or 'none'),
                         report.get('proposed', 0), report.get('applied', 0),
                         report.get('refused', 0),
                         (' -- ' + report['error']) if report.get('error') else
                         (' -- {0} took it, its reply of no use: no other model'
                          .format(report['spent'])) if report.get('spent') else ''),
            level='INFO')
    except Exception as e:
        kodi.log('gender proofread skipped: {0}'.format(e), level='WARNING')
    return blocks, report


def _title_cast(info, imdb_id, season, episode):
    """(cast, title, year) for the translation prompt and the gender
    proofread."""
    # Cast metadata, cached per title -- and per EPISODE: an episode's guest
    # stars are its own, and a per-show cache lent the first translated
    # episode's guests to every later one (their roles missing from the
    # prompt). With no IMDb id the player's TMDB id is used (POV and Umbrella
    # set the show's), where there used to be no cast at all.
    _cast_tmdb = '' if imdb_id else (info.get('tmdb_id') or '').strip()
    if not _cast_tmdb.isdigit():
        _cast_tmdb = ''
    if (_cast_tmdb and info.get('is_episode')
            and str(info.get('dbid') or '').strip() not in ('', '-1', '0')):
        _cast_tmdb = ''     # a library episode's own id, not its show's
    _cast_media = 'tv' if info.get('is_episode') else 'movie'
    meta_key = imdb_id or ('tmdb{0}_{1}'.format(_cast_media, _cast_tmdb)
                           if _cast_tmdb else '')
    if meta_key and info.get('is_episode') and season and episode:
        meta_key = '{0}_S{1}E{2}'.format(meta_key, season, episode)
    meta_path = translation_cache.metadata_path(meta_key) if meta_key else None
    cast = None
    title = info.get('title') or ''
    year = info.get('year') or ''
    # Bumped cap (Oct 2026 -- minor characters were missing from
    # top-12). A cached cast with fewer than this many entries is
    # stale; treat as a cache miss so we re-fetch and store the
    # expanded list.
    MIN_CAST_FOR_CACHE = 20
    if meta_path:
        cached_meta = translation_cache.load_json(meta_path)
        if cached_meta:
            cached_cast = cached_meta.get('cast') or []
            if len(cached_cast) >= MIN_CAST_FOR_CACHE:
                cast = cached_cast
                title = cached_meta.get('title') or title
                year = cached_meta.get('year') or year
            else:
                kodi.log(
                    'Cached cast has only {0} entries -- refetching '
                    'for expanded coverage'.format(len(cached_cast)),
                    level='DEBUG')
    if cast is None:
        try:
            cast = tmdb_helper.fetch_cast(
                imdb_id=imdb_id or None, tmdb_id=_cast_tmdb or None,
                media_type=_cast_media,
                season=season, episode=episode,
            )
            t2, y2 = tmdb_helper.title_and_year(
                imdb_id=imdb_id or None, tmdb_id=_cast_tmdb or None,
                media_type=_cast_media)
            title = title or t2
            year = year or y2
            if meta_path:
                translation_cache.save_json(meta_path, {
                    'cast': cast, 'title': title, 'year': year,
                })
        except Exception as e:
            kodi.log('TMDB lookup failed: {0}'.format(e),
                           level='WARNING')
            cast = []
    return cast, title, year


def _proofread_opened(path, source_text, source_id, release, progressive_cb,
                      deliver, cancelled, info, share=None, shown_already=False,
                      mark_path=None, later=None, hebrew_ref=False):
    """An older AI translation opened from the list, proofread once.

    `path` is the Hebrew file, corrected in place; `deliver(path)` returns the
    file to show (repairs and timing applied); `share(text, stats)` hands the
    proofread file to the pool. The Hebrew goes on screen at once, through the
    same phases a fresh translation uses, and the corrected file replaces it
    with 'done'. `shown_already`: it is on screen (proofread_later), so 'done'
    swaps only a corrected file. `mark_path`: where the marker goes, when not
    next to `path`. `later`: where the pool keeps it (gp_later's 'share'), for
    a proofread no model answered and the service asks again. Returns the
    delivered path; None when nothing changed on a file shown already."""
    from resources.lib.ai_translation.proofread.gp_jobs import _gp_later_add
    if not source_text:
        # no source: the model could not tell who speaks to whom
        return None if shown_already else deliver(path)
    # on screen already: read for the id only, nothing delivered twice
    delivered = path if shown_already else deliver(path)
    try:
        with open(delivered, 'r', encoding='utf-8', errors='replace') as f:
            shown = f.read()
    except Exception:
        shown = ''
    if not shown:
        return None if shown_already else delivered
    marker = mark_path or path
    _gp_try_mark(marker, True)
    stopped = _proofread_stopper(source_id, cancelled)
    # first_ready also tells the job which translation it carries (a job with
    # no id adopts the first one it is sent); chunk_ready puts it on screen.
    first = (('first_ready', {'fallback_text': shown}),)
    if not shown_already:
        first += (('chunk_ready', {'completed': 1, 'total': 1,
                                   'merged_text': shown}),)
    for phase, payload in first:
        try:
            payload.update({'source_id': source_id, 'release': release})
            progressive_cb(phase, payload)
        except Exception as e:
            kodi.log('progressive_cb {0} (before the gender proofread of '
                           'an opened translation) raised: {1}'.format(phase, e),
                           level='WARNING')
    try:
        model = settings.get_setting('model', 'gemini-3.5-flash-lite') \
            or 'gemini-3.5-flash-lite'
        imdb_id = (info.get('imdb_id') or '').strip()
        cast = _title_cast(info, imdb_id, info.get('season') or '',
                           info.get('episode') or '')[0]
        blocks = srt_blocks.parse_blocks(translation_cache.load_text(path) or '')
        proofed, report = _run_gender_proofread(
            blocks, source_text, settings.get_setting('api_key', '') or '',
            model, cast, stopped, background=shown_already,
            hebrew_ref=hebrew_ref)
        final = None
        if proofed != blocks:
            final = srt_blocks.stitch_blocks(proofed)
            translation_cache.save_text(path, final)
            delivered = deliver(path)
            # a late round may answer once the viewer is on the next video
            if not stopped():
                kodi.notify('AI: תוקנו זכר/נקבה ב-{0} שורות'.format(
                    report.get('applied', 0)), time_ms=4000)
        # answered, nothing sent or the viewer moved on: no pause before the
        # next open; a reply of no use: a week
        _gp_settle((marker,), report, stopped)
        if not report.get('model'):
            # 3.7 busy, out of today's quota, or the viewer moved on: the
            # service asks again later
            _gp_later_add(report, path, source_text, info, share=later,
                          marker=marker)
        if report.get('model'):
            _gp_mark(marker, report)
            if share is not None:
                share(final if final is not None else (translation_cache.load_text(path) or ''),
                      _gp_stats(report))
    except Exception as e:
        final = None
        kodi.log('gender proofread of an opened translation failed: '
                       '{0}'.format(e), level='WARNING')
    if shown_already and final is None:
        # nothing to swap: the file on screen stays
        try:
            progressive_cb('done', {'success': False, 'source_id': source_id,
                                    'release': release})
        except Exception:
            pass
        return None
    try:
        progressive_cb('done', {'success': True, 'source_id': source_id,
                                'release': release, 'path': delivered})
    except Exception as e:
        kodi.log('progressive_cb done (after the gender proofread of an '
                       'opened translation) raised: ' + str(e), level='WARNING')
    return delivered
