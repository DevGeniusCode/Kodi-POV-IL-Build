# exec'd by golden.py LAST (it replaces functions of several modules by fakes and does not put them back).
# Step 8, part B: autosub_on_play end to end, with a fake player, a fake provider search that calls on_provider at
# chosen moments, fake resolve / apply: which subtitle is applied, when the fast lane or the full list decided, and
# what the minimum match number (autosub_min_match_pct) and the guarded swap do. The log lines are recorded with
# their seconds removed; the real clock only orders the events (gaps of 0.1 s and more).
import re as _re_au
import time as _time_au
import urllib.parse as _up_au

import xbmc as _xbmc_au
import xbmcgui as _xbmcgui_au

_PLAYING_AU = 'special://x/Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H264-NTb.mkv'
_REL_AU = 'Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H264-NTb'


class _PlayerAu(object):
    def getPlayingFile(self): return _PLAYING_AU
    def isPlayingVideo(self): return True
    def isPlaying(self): return True
    def getTotalTime(self): return 3000
    def getAvailableSubtitleStreams(self): return ['eng']
    def setSubtitles(self, p): pass
    def showSubtitles(self, v): pass
    def getTime(self): return 5


_xbmc_au.Player = _PlayerAu
_xbmc_au.getInfoLabel = lambda label: ''
_xbmc_au.getCondVisibility = lambda cond: False

_cs_au = importlib.import_module('resources.lib.core.settings')
_sel_au = importlib.import_module('resources.lib.core.selection')
_vc_au = importlib.import_module('resources.lib.core.video_context')
_kodi_au = importlib.import_module('resources.lib.core.kodi')
_pipe_au = importlib.import_module('resources.lib.pipeline')
_bridge_au = importlib.import_module('resources.lib.fetching.bridge')
_adapter_au = importlib.import_module('resources.lib.fetching.provider_adapter')
_player_au = importlib.import_module('resources.lib.autosub.player')
_cand_au = importlib.import_module('resources.lib.sync.candidates')
_bundle_au = importlib.import_module('resources.lib.sync.probes.bundle')
_gp_au = importlib.import_module('resources.lib.ai_translation.proofread.gp_jobs')
_prog_au = importlib.import_module('resources.lib.ai_translation.progress')
_emb_au = importlib.import_module('resources.lib.fetching.embedded.streams')
_enc_au = importlib.import_module('resources.lib.core.link_codec').encode_link
_dec_au = importlib.import_module('resources.lib.core.link_codec').decode_link

_INFO_AU = {'imdb_id': 'tt1', 'tmdb_id': '1', 'title': 'Show', 'tvshow': 'Show', 'season': '1', 'episode': '2',
            'is_episode': True, 'filepath': _PLAYING_AU, 'picked_release': _REL_AU}
_cs_au.hebrew_subtitle_wanted = lambda: True
_vc_au.current_video_info = lambda: dict(_INFO_AU)
_bridge_au.release_ready = lambda info: True
_bridge_au.ensure_engine_settings = lambda: None


class _GeneralAu(object):
    break_all = False
    with_dp = False
    show_msg = ''

    @staticmethod
    def show_results(show_dp=True):
        pass


_adapter_au.load_general = lambda: _GeneralAu
_LOG_AU = []
_kodi_au.log = lambda msg, level='INFO': _LOG_AU.append(msg)
_kodi_au.notify = lambda *a, **k: None
_prog_au.set_quiet = lambda v: None
_cand_au.rank_ready_candidates = lambda info, cands, **k: cands
_cand_au.diverse_human_alternatives = lambda c, **k: []
_bundle_au.prefetch_autosub_reference = lambda info, cands: None
_gp_au.proofread_later = lambda *a, **k: None
_emb_au.note_playback_streams = lambda *a, **k: None
_TMP_AU = tempfile.mkdtemp(prefix='goau_')


def _row_au(source, filename, pct):
    return {'filename': 'x', 'language': 'he', 'pct': pct,
            'link': _enc_au({'type': 'engine', 'source': source, 'language': 'Hebrew', 'filename': filename,
                             'download_data': {}})}


_K100 = _row_au('ktuvit', _REL_AU, 100)
_K97 = _row_au('ktuvit', _REL_AU + '.k97', 97)
_K92 = _row_au('ktuvit', _REL_AU + '.k92', 92)
_W100 = _row_au('wizdom', _REL_AU + '.w100', 100)
_O99 = _row_au('opensubtitles', _REL_AU + '.o99', 99)
_O100 = _row_au('opensubtitles', _REL_AU + '.o100', 100)
_O60 = _row_au('opensubtitles', 'Show.S01E02.HDTV-LOL', 60)
_EVENTS_AU = []


def _autosub_run(script, full, settings=None, status=None, chooser_open=False, fail=()):
    _SETTINGS.clear()
    _SETTINGS.update({'use_builtin_engine': 'true', 'engine_autosub': 'true', 'ktuvit': 'true', 'wizdom': 'true',
                      'ai_when_no_hebrew': 'manual'})
    _SETTINGS.update(settings or {})
    _PROPS.clear()
    if chooser_open:
        _PROPS[(10000, 'MoranSubsChooserOpen')] = '1'
    del _LOG_AU[:]
    del _EVENTS_AU[:]
    _player_au.STATE.update({'last_file': None, 'busy': False, 'active_file': ''})

    def search(info, modal_progress=True, quick=False, on_provider=None):
        t0 = _time_au.monotonic()
        for at, provider, rows in script:
            _time_au.sleep(max(0.0, at - (_time_au.monotonic() - t0)))
            if on_provider:
                on_provider(provider, [dict(r) for r in rows])
        _time_au.sleep(0.3)
        return [dict(r) for r in full]
    _pipe_au.search = search

    def resolve(link, info, selection=None, fallback_links=None, **k):
        _time_au.sleep(0.05)
        name = (_dec_au(link) or {}).get('filename')
        if name in fail:
            _EVENTS_AU.append('resolve failed ' + name.replace(_REL_AU, ''))
            return None
        path = os.path.join(_TMP_AU, 'sub%d.srt' % (len(_EVENTS_AU) + 1))
        with open(path, 'w') as f:
            f.write('1\n00:00:01,000 --> 00:00:02,000\nhi\n')
        _EVENTS_AU.append('resolve ' + name.replace(_REL_AU, ''))
        return path
    _pipe_au.resolve = resolve
    _sel_au.apply_subtitle_file = lambda path, selection=None, **k: (_EVENTS_AU.append('apply') or True)
    _sel_au.get_subtitle_sync_status = lambda link=None, stream_url=None: status or {}
    _player_au.autosub_on_play()
    log = [_re_au.sub(r'after [0-9.]+s', 'after Ns', m) for m in _LOG_AU if m.startswith('autosub')]
    return _json_f.dumps({'log': log, 'events': list(_EVENTS_AU)}, ensure_ascii=False)


# the fast lane applies a strong direct hit at once, the full list changes nothing
R['fast.autosub.fast_100'] = _autosub_run([(0.1, 'ktuvit', [_K100]), (0.2, 'wizdom', [])], [_K100, _O99])
# grace: a 97 waits for the other Israeli provider, whose 100 then wins without a swap
R['fast.autosub.grace_second_100_wins'] = _autosub_run(
    [(0.1, 'ktuvit', [_K97]), (0.5, 'wizdom', [_W100])], [_K97, _W100])
# grace over (1.0 s): the 92 is applied, the 100 that arrives later takes it over once, after the full list
R['fast.autosub.grace_over_then_swap'] = _autosub_run(
    [(0.1, 'ktuvit', [_K92]), (1.6, 'wizdom', [_W100])], [_K92, _W100])
# the same without the fast lane (autosub_fast_apply off): the old way, one apply after the full list
R['fast.autosub.fast_apply_off'] = _autosub_run(
    [(0.1, 'ktuvit', [_K100])], [_K100], settings={'autosub_fast_apply': 'false'})
# one provider disabled: not waited for
R['fast.autosub.only_wizdom_on'] = _autosub_run(
    [(0.1, 'wizdom', [_row_au('wizdom', _REL_AU + '.w95', 95)])], [_row_au('wizdom', _REL_AU + '.w95', 95)],
    settings={'ktuvit': 'false'})
# the minimum match number
R['fast.autosub.min98_fast_skips_97'] = _autosub_run(
    [(0.1, 'ktuvit', [_K97]), (0.2, 'wizdom', [])], [_K97, _O99], settings={'autosub_min_match_pct': '98'})
R['fast.autosub.min98_list_takes_99'] = _autosub_run(
    [(0.1, 'ktuvit', [_K97])], [_K97, _O99, _O60], settings={'autosub_min_match_pct': '98', 'autosub_fast_apply': 'false'})
R['fast.autosub.min100_nothing_applied'] = _autosub_run(
    [(0.1, 'ktuvit', [_K97]), (0.2, 'wizdom', [])], [_K97, _O99], settings={'autosub_min_match_pct': '100'})
R['fast.autosub.min0_old_behaviour_takes_first'] = _autosub_run(
    [], [_O60, _O99], settings={'autosub_fast_apply': 'false'})
R['fast.autosub.min70_filters_the_60'] = _autosub_run(
    [], [_O60, _O99], settings={'autosub_fast_apply': 'false', 'autosub_min_match_pct': '70'})
# the guarded swap: blocked by a proven timing verdict, by the open chooser, or when the swap itself fails
R['fast.autosub.swap_blocked_proven_timing'] = _autosub_run(
    [(0.1, 'ktuvit', [_K92]), (0.2, 'wizdom', [])], [_K92, _O100], status={'state': 'confirmed', 'source': 'community'})
R['fast.autosub.swap_allowed_release_confirmed'] = _autosub_run(
    [(0.1, 'ktuvit', [_K92]), (0.2, 'wizdom', [])], [_K92, _O100], status={'state': 'confirmed', 'source': 'release'})
R['fast.autosub.swap_blocked_chooser_open'] = _autosub_run(
    [(0.1, 'ktuvit', [_K92]), (0.2, 'wizdom', [])], [_K92, _O100], chooser_open=True)
R['fast.autosub.swap_fails_keeps_fast'] = _autosub_run(
    [(0.1, 'ktuvit', [_K92]), (0.2, 'wizdom', [])], [_K92, _O100], fail=(_REL_AU + '.o100',))
R['fast.autosub.no_swap_under_margin'] = _autosub_run(
    [(0.1, 'ktuvit', [_K92]), (0.2, 'wizdom', [])], [_K92, _row_au('opensubtitles', _REL_AU + '.o95', 95)])
# the fast lane's first apply fails (download): the full list is tried the old way
R['fast.autosub.fast_apply_fails_list_continues'] = _autosub_run(
    [(0.1, 'ktuvit', [_K100]), (0.2, 'wizdom', [])], [_K100, _O99], fail=(_REL_AU,))
