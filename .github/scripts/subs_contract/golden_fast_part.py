# exec'd by golden.py (shares its namespace: R, rec, _SETTINGS, importlib, json, os, time)
# Step 8, part B: autosub's minimum match number and the Wizdom / Ktuvit fast lane.
#   - the policy functions (settings, thresholds, the minimum filter, the fast-lane and swap rules)
#   - FastLane and run() on a VIRTUAL clock: provider results arrive at chosen times, the worker waits on the clock
#   - bridge._provider_callback with a fake engine, and the REAL engine search loop (c_get_subtitles) with fake
#     providers, to prove the on_provider_done hook fires per provider while the slower ones still run
import json as _json_f
import time

_pol = importlib.import_module('resources.lib.autosub.policy')
_fl = importlib.import_module('resources.lib.autosub.fast_lane')
_lc = importlib.import_module('resources.lib.core.link_codec')
_cs = importlib.import_module('resources.lib.core.settings')
_bridge_f = importlib.import_module('resources.lib.fetching.bridge')

_FINFO = {'season': '1', 'episode': '2'}
_PLAYING = 'Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H264-NTb'


def _frow(source, filename, pct, language='Hebrew', embedded=False, typ='engine', mt=False, **extra):
    payload = {'type': typ, 'source': source, 'language': language + ('MachineTranslated' if mt else ''),
               'filename': filename, 'download_data': {}}
    if embedded:
        payload['embedded'] = True
    payload.update(extra)
    return {'filename': 'x', 'language': 'he', 'link': _lc.encode_link(payload), 'pct': pct}


# ---- the settings and the minimum filter
for _v in ('', '0', '45', '100', '150', '-5', 'abc'):
    _SETTINGS.clear()
    if _v != '':
        _SETTINGS['autosub_min_match_pct'] = _v
    rec('fast.min_match_pct.%s' % (_v or 'unset'), _pol.min_match_pct, _cs)
for _v in ('', 'true', 'false'):
    _SETTINGS.clear()
    if _v != '':
        _SETTINGS['autosub_fast_apply'] = _v
    rec('fast.apply_enabled.%s' % (_v or 'unset'), _pol.fast_apply_enabled, _cs)
_SETTINGS.clear()
for _m in (0, 50, 90, 95, 100):
    rec('fast.threshold.%d' % _m, _pol.fast_threshold, _m)
_ROWS = [dict(pct=None), dict(pct=0), dict(pct=59), dict(pct=60), dict(pct=100), dict(pct=101)]
for _m in (0, 60, 100):
    rec('fast.filter_min.%d' % _m, _pol.filter_min, _ROWS, _m)

# ---- what the fast lane may apply (human Hebrew, straight from ktuvit / wizdom, over the threshold)
_ELIG = [
    ('ktuvit97', _frow('ktuvit', _PLAYING, 97)),
    ('wizdom90', _frow('wizdom', _PLAYING, 90)),
    ('wizdom89', _frow('wizdom', _PLAYING, 89)),
    ('opensubtitles99', _frow('opensubtitles', _PLAYING, 99)),
    ('pool_ktuvit100', _frow('', _PLAYING, 100, typ='pool', pool_kind='ktuvit')),
    ('machine_translated', _frow('ktuvit', _PLAYING, 99, mt=True)),
    ('embedded', _frow('wizdom', _PLAYING, 100, embedded=True)),
    ('english', _frow('wizdom', _PLAYING, 99, language='English')),
    ('other_episode', _frow('ktuvit', 'Show.S02E05.1080p.WEB-DL-NTb', 99)),
    ('no_pct', _frow('ktuvit', _PLAYING, None)),
    ('engine_ai', _frow('wizdom', _PLAYING, 99, typ='engine_ai')),
]
for _t in (90, 95):
    for _name, _r in _ELIG:
        rec('fast.payload.%d.%s' % (_t, _name), lambda r=_r, t=_t: bool(_pol.fast_payload(r, _FINFO, t)))

# ---- the lane on a virtual clock
_K97 = _frow('ktuvit', _PLAYING, 97)
_K100 = _frow('ktuvit', _PLAYING, 100)
_W100 = _frow('wizdom', _PLAYING, 100)
_W95 = _frow('wizdom', _PLAYING, 95)
_W97 = _frow('wizdom', 'Show.S01E02.720p.AMZN.WEB-DL.DDP5.1.H264-NTb', 97)   # same pct as _K97, a bit farther
_K97B = _frow('ktuvit', 'Show.S01E02.1080p.AMZN.WEB-DL.AAC2.0.H264-NTb', 97)


def _lane(threshold=90, expected=('ktuvit', 'wizdom')):
    return _fl.FastLane(threshold, expected, _FINFO, _PLAYING)


def _verdict(entry):
    return None if entry is None else [entry['provider'], entry['pct']]


def _timeline(events, probes, **kw):
    """events: (t, 'offer', provider, rows) | (t, 'end'); probes: times at which decide() is read."""
    lane = _lane(**kw)
    out = []
    pending = sorted([(e[0], 0, e) for e in events] + [(p, 1, p) for p in probes], key=lambda x: (x[0], x[1]))
    for t, kind, item in pending:
        if kind == 0:
            if item[1] == 'offer':
                lane.offer(item[2], item[3], t)
            else:
                lane.end_search()
        else:
            out.append([t, _verdict(lane.decide(t))])
    return out


rec('fast.lane.grace_then_apply', _timeline, [(0.3, 'offer', 'ktuvit', [_K97])], [0.3, 0.9, 1.29, 1.31])
rec('fast.lane.second_provider_100_wins', _timeline,
    [(0.3, 'offer', 'ktuvit', [_K97]), (0.8, 'offer', 'wizdom', [_W100])], [0.3, 0.79, 0.8])
rec('fast.lane.100_at_once', _timeline, [(0.2, 'offer', 'ktuvit', [_K100])], [0.2])
rec('fast.lane.other_provider_done_empty', _timeline,
    [(0.4, 'offer', 'wizdom', [_W95]), (0.5, 'offer', 'ktuvit', [])], [0.4, 0.5])
rec('fast.lane.only_wizdom_enabled', _timeline, [(0.4, 'offer', 'wizdom', [_W95])], [0.4], expected=('wizdom',))
rec('fast.lane.below_threshold_never', _timeline,
    [(0.2, 'offer', 'ktuvit', [_K97]), (0.3, 'offer', 'wizdom', [_W95])], [0.3, 5.0], threshold=98)
rec('fast.lane.threshold_is_max_90_and_minimum', lambda: [_pol.fast_threshold(m) for m in (0, 70, 93)])
rec('fast.lane.end_search_releases_grace', _timeline,
    [(0.3, 'offer', 'ktuvit', [_K97]), (0.5, 'end')], [0.4, 0.5])
rec('fast.lane.tie_closest_name_first', _timeline,
    [(0.1, 'offer', 'ktuvit', [_K97B]), (0.2, 'offer', 'wizdom', [_W97])], [0.2])
rec('fast.lane.tie_equal_prefers_ktuvit', _timeline,
    [(0.1, 'offer', 'wizdom', [_frow('wizdom', _PLAYING, 97)]), (0.2, 'offer', 'ktuvit', [_frow('ktuvit', _PLAYING, 97)])],
    [0.2])
rec('fast.lane.pool_row_ignored', _timeline,
    [(0.1, 'offer', 'ktuvit', [_frow('', _PLAYING, 100, typ='pool', pool_kind='ktuvit')])], [0.1, 3.0])


def _reject_flow():
    lane = _lane()
    lane.offer('ktuvit', [_K100], 0.1)
    lane.offer('wizdom', [_W97], 0.2)
    first = _verdict(lane.decide(0.2))
    lane.reject(lane.decide(0.2))
    second = _verdict(lane.decide(0.2))
    lane.reject(lane.decide(0.2))
    return [first, second, _verdict(lane.decide(0.2))]


rec('fast.lane.reject_then_next_source', _reject_flow)


# ---- the worker loop, with the clock and the waits virtual too
def _worker(events, apply_results, stop_at=None, **kw):
    lane = _lane(**kw)
    clock = [0.0]
    pending = sorted(events, key=lambda e: e[0])
    applied = []

    def deliver():
        while pending and pending[0][0] <= clock[0] + 1e-9:
            ev = pending.pop(0)
            if ev[1] == 'offer':
                lane.offer(ev[2], ev[3], clock[0])
            else:
                lane.end_search()

    def wait(seconds):
        clock[0] = round(clock[0] + seconds, 4)
        deliver()

    def apply_fn(entry):
        applied.append([round(clock[0], 2), entry['provider'], entry['pct']])
        return apply_results.pop(0)

    deliver()
    result = _fl.run(lane, apply_fn, lambda: clock[0], wait, lambda: stop_at is not None and clock[0] >= stop_at)
    return {'applied': applied, 'result': _verdict(result), 'clock': round(clock[0], 2)}


rec('fast.run.grace_expires', _worker, [(0.3, 'offer', 'ktuvit', [_K97]), (9.0, 'end')], [True])
rec('fast.run.second_provider_100', _worker,
    [(0.3, 'offer', 'ktuvit', [_K97]), (0.8, 'offer', 'wizdom', [_W100]), (9.0, 'end')], [True])
rec('fast.run.first_fails_second_applies', _worker,
    [(0.2, 'offer', 'ktuvit', [_K100]), (0.25, 'offer', 'wizdom', [_W97]), (9.0, 'end')], [False, True])
rec('fast.run.both_fail_gives_up', _worker,
    [(0.2, 'offer', 'ktuvit', [_K100]), (0.25, 'offer', 'wizdom', [_W97]), (9.0, 'end')], [False, False])
rec('fast.run.nothing_eligible_until_end', _worker, [(0.3, 'offer', 'ktuvit', [_frow('ktuvit', _PLAYING, 80)]), (2.0, 'end')], [])
rec('fast.run.stopped_by_caller', _worker, [(5.0, 'offer', 'ktuvit', [_K100])], [], stop_at=1.0)
rec('fast.run.min_pct_98_skips_97_applies_100', _worker,
    [(0.2, 'offer', 'ktuvit', [_K97]), (0.4, 'offer', 'wizdom', [_W100]), (9.0, 'end')], [True],
    threshold=_pol.fast_threshold(98))

# ---- the swap after the full list
_K92 = _frow('ktuvit', _PLAYING, 92)
_APPLIED = _pol._payload(_K92)
_FULL = [_K92, _frow('opensubtitles', 'Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H264-NTb.v2', 99),
         _frow('wizdom', 'Show.S01E02.720p.HDTV.x264-LOL', 98),
         _frow('', _PLAYING, 100, typ='pool', pool_kind='ai'), _frow('ktuvit', 'Show.S02E05.1080p.WEB-DL-NTb', 100)]
for _m in (0, 99, 100):
    rec('fast.swap.choice.min%d' % _m, lambda m=_m: (lambda r: None if r is None else _pol._payload(r).get('filename'))(
        _pol.swap_choice(_APPLIED, 92, _FULL, _FINFO, _PLAYING, m)))
rec('fast.swap.margin_4_is_enough', lambda: _pol.swap_choice(_APPLIED, 92, [_frow('wizdom', _PLAYING, 96)], _FINFO, _PLAYING, 0) is not None)
rec('fast.swap.margin_3_is_not', lambda: _pol.swap_choice(_APPLIED, 92, [_frow('wizdom', _PLAYING, 95)], _FINFO, _PLAYING, 0) is None)
rec('fast.swap.same_subtitle_never', lambda: _pol.swap_choice(_APPLIED, 80, [_K92], _FINFO, _PLAYING, 0) is None)
for _name, _st, _open in (('none', {}, False), ('checking', {'state': 'checking'}, False),
                          ('release_confirmed', {'state': 'confirmed', 'source': 'release'}, False),
                          ('proven_confirmed', {'state': 'confirmed', 'source': 'community'}, False),
                          ('fixed', {'state': 'fixed', 'source': 'local'}, False),
                          ('chooser_open', {'state': 'checking'}, True)):
    rec('fast.swap.blocked.' + _name, _pol.swap_blocked, _st, _open)

# ---- bridge._provider_callback with a fake engine (what one finished provider hands to the lane)
_CALLS = []


class _FakeEngine(object):
    @staticmethod
    def sort_subtitles(rows, video_data, silent=False):
        return rows


_real_rows = _bridge_f._engine_rows
_bridge_f._engine_rows = lambda sorted_subs, playing, log=True: [
    {'_engine_kind': 'human_he', '_pct': 97, 'filename': 'h'}, {'_engine_kind': 'mt_he', '_pct': 99, 'filename': 'm'},
    {'_engine_kind': 'other', '_pct': 90, 'filename': 'o'}]
try:
    _hook = _bridge_f._provider_callback(_FakeEngine, {'file_original_path': _PLAYING}, lambda n, r: _CALLS.append([n, r]))
    _hook('ktuvit', ['raw'])
    _hook('wizdom', [])
    _hook('opensubtitles', ['raw'])
    R['fast.bridge.hook_calls'] = _json_f.dumps(_CALLS, sort_keys=True)
    del _CALLS[:]

    def _boom(n, r):
        raise RuntimeError('lane failed')
    _bridge_f._provider_callback(_FakeEngine, {}, _boom)('ktuvit', ['raw'])
    R['fast.bridge.hook_swallows_errors'] = 'ok'
finally:
    _bridge_f._engine_rows = _real_rows

# ---- the REAL engine loop with fake providers: the hook fires per provider, ktuvit well before the slow one ends
_eng = importlib.import_module('resources.lib.fetching.providers.engine')


def _fake_provider(delay, rows):
    class _P(object):
        global_var = []
    p = _P()

    def get_subs(video_data, *a):
        time.sleep(delay)
        p.global_var = list(rows)
    p.get_subs = get_subs
    return p


_saved = {n: getattr(_eng, n) for n in ('ktuvit', 'wizdom', 'opensubtitles', 'yify', 'subsource', 'subscene', 'bsplayer',
                                        '_provider_on')}
_SETTINGS.clear()
_SETTINGS.update({'ktuvit': 'true', 'wizdom': 'true', 'opensubtitles': 'true', 'yify': 'false', 'subsource': 'false',
                  'subscene': 'false', 'bsplayer': 'false', 'language_hebrew': 'true', 'max_search_time': '10'})
try:
    _eng.ktuvit = _fake_provider(0.05, ['kt-row'])
    _eng.wizdom = _fake_provider(0.4, ['wz-row'])
    _eng.opensubtitles = _fake_provider(1.0, ['os-row'])
    for _n in ('yify', 'subsource', 'subscene', 'bsplayer'):
        setattr(_eng, _n, _fake_provider(0, []))
    _eng._provider_on = lambda name, skipped: True
    _seen = []
    _t0 = time.time()
    _res = _eng.c_get_subtitles({'x': 1}, on_provider_done=lambda n, r: _seen.append([n, r, time.time() - _t0]))
    R['fast.engine.hook_order'] = _json_f.dumps([[s[0], s[1]] for s in _seen])
    R['fast.engine.ktuvit_before_slowest_ends'] = repr(_seen[0][2] < _seen[-1][2] - 0.5)
    R['fast.engine.result_unchanged'] = repr(_res)
    # a hook that raises never breaks the search
    _res2 = _eng.c_get_subtitles({'x': 1}, on_provider_done=lambda n, r: 1 / 0)
    R['fast.engine.hook_error_safe'] = repr(_res2)
    # no hook: the old call
    R['fast.engine.no_hook'] = repr(_eng.c_get_subtitles({'x': 1}))
finally:
    for _n, _v in _saved.items():
        setattr(_eng, _n, _v)
    _SETTINGS.clear()
