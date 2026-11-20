# exec'd by golden.py after golden_list_part.py (shares its namespace). Subtitle Phase 3, step 3b:
# the "playing release" equivalence matrix. Every derivation is recorded for every fixture; the harness works
# on the tree BEFORE the unification (old locations) and AFTER it (core.video_context.release_name).
import importlib as _il2

_vc = _il2.import_module('resources.lib.core.video_context')
_NEW_API = hasattr(_vc, 'release_name')


def _res(info, policy, old):
    if _NEW_API:
        return _vc.release_name(info, policy)
    return old(info)


_chooser = _il2.import_module('resources.lib.ui.chooser')
_syncsvc = _il2.import_module('resources.lib.sync.service')
_rank = _il2.import_module('resources.lib.scoring_matching.ranking')


def _old_scored(i):
    return _bridge.detect_release_name(i)


def _old_chooser(i):
    return _chooser._video_ref(i)


def _old_sync(i):
    return _syncsvc.playing_release(i)


def _old_pool(i):
    return _poolc.release_from(i)


_FAKE = [None]


def _swap_name(name, old, new):
    """with old None: return the scorer found under `name` in the list code; else replace every module-level reference to
    `old` by `new` in the add-on's modules (the list code is split over several modules since Subtitle Phase 4)"""
    if old is None:
        for mn in ('resources.lib.fetching.service', 'resources.lib.fetching.search.ai_rows',
                   'resources.lib.fetching.search.engine', 'resources.lib.fetching.search.pool',
                   'resources.lib.fetching.search.state'):
            m = sys.modules.get(mn)
            if m is not None and hasattr(m, name):
                return getattr(m, name)
        return None
    _FAKE[0] = new
    for mn, mod in list(sys.modules.items()):
        if mn.startswith('resources.lib.') and mod is not None and getattr(mod, name, None) is old:
            setattr(mod, name, new)
    return old


def _picker(i):
    """the list_candidates `_video_ref`, captured from the REAL code through the match scorer it feeds"""
    seen = []
    name = 'match_pct'
    real = _swap_name(name, None, None)         # the scorer as it is now (it may be imported by name in several modules)
    _swap_name(name, real, lambda a, b, c='': (seen.append(a), 0)[1])
    _SETTINGS.clear()
    _PROPS.clear()
    _streams.enabled = lambda: False
    _poolc.use_enabled = lambda: True
    _poolc.share_enabled = lambda: False
    _poolc.lookup = lambda x: [{'kind': 'ktuvit', 'hash': 'h1', 'release': 'Zed.Release.2020.1080p-GRP', 'source_lang': 'he'}]
    _local.find_alongside = lambda fp: []
    _local.find_in_temp = lambda: []
    try:
        _svc.list_candidates(_copy.deepcopy(i), modal_progress=False, quick=False)
    finally:
        _swap_name(name, _FAKE[0], real)
    return seen[0] if seen else '<no call>'


def _source6(i):
    """ai_translation.service.resolve: the timing / pool fallback loop (a replica of the 5 lines, see the report)"""
    for k in ('release', 'picked_release', 'filename', 'tagline', 'label'):
        c = (i.get(k) or '').strip()
        if c:
            return c
    return ''


_REL = 'Movie.2020.1080p.WEB-DL.x264-GRP'
_V = {
    'rel': _REL,
    'rel2': 'Show.S01E02.720p.HDTV.x264-LOL',
    'pack': 'Show.S01.1080p.WEB-DL-GRP',
    'wrongep': 'Show.S01E05.720p.HDTV.x264-LOL',
    'title': 'Movie',
    'url': 'https://deb.example/d/abc?token=xyz',
    'uuid': '499157df-d49d-4c1b-96f9-920866a2354a',
    'hash': 'abcdef0123456789abcdef0123456789',
    'posix': '/media/movies/' + _REL + '.mkv',
    'stream': 'plugin://plugin.video.pov/?mode=play&token=abc',
    'heb': 'סרט.2020.1080p.WEB-DL-GRP',
    'slash': '/media/x/' + _REL + '/',
    'ws': '   ',
    'extonly': '/media/x/.mkv',
    'padded': '  ' + _REL + '  ',
    'bare': 'Movie 2020',
    'short': 'a.b',
    'empty': '',
    'none': None,
    'int': 123,
}
_KEYS = ('picked_release', 'tagline', 'label', 'li_filename', 'filepath', 'title', 'release', 'filename')
PR_CASES = []      # (id, info)
for _k in _KEYS:
    for _vn in ('rel', 'posix', 'url', 'uuid', 'hash', 'ws', 'empty', 'none', 'heb', 'padded', 'int'):
        if _vn in ('int',) and _k not in ('picked_release', 'filepath'):
            continue
        PR_CASES.append(('%s=%s' % (_k, _vn), {_k: _V[_vn]}))
_COMBO = [
    ('picked_token+tagline_rel', {'picked_release': _V['url'], 'tagline': _V['rel']}),
    ('picked_uuid+label_rel', {'picked_release': _V['uuid'], 'label': _V['rel']}),
    ('picked+tagline', {'picked_release': _V['rel'], 'tagline': _V['rel2']}),
    ('tagline+label', {'tagline': _V['heb'], 'label': _V['rel']}),
    ('label+title', {'label': _V['bare'], 'title': _V['title']}),
    ('filepath_token+li_posix', {'filepath': _V['url'], 'li_filename': _V['posix']}),
    ('filepath_posix+title', {'filepath': _V['posix'], 'title': _V['title']}),
    ('filepath_posix+li_posix2', {'filepath': _V['posix'], 'li_filename': '/x/Other.2019.720p.BluRay-ZZ.mkv'}),
    ('li_token+filepath_posix', {'li_filename': _V['stream'], 'filepath': _V['posix']}),
    ('li_hash+filepath_url+title', {'li_filename': _V['hash'], 'filepath': _V['url'], 'title': _V['title']}),
    ('title+filepath_hash', {'title': _V['title'], 'filepath': '/m/' + _V['hash'] + '.mkv'}),
    ('release+picked', {'release': _V['rel2'], 'picked_release': _V['rel']}),
    ('release_token+picked', {'release': _V['url'], 'picked_release': _V['rel']}),
    ('filename+label', {'filename': 'Sub.File.2020.he.srt', 'label': _V['rel']}),
    ('release_ws+filename', {'release': _V['ws'], 'filename': _V['rel2']}),
    ('all_real', {'picked_release': _V['rel'], 'tagline': _V['rel2'], 'label': _V['bare'], 'li_filename': _V['posix'],
                  'filepath': _V['posix'], 'title': _V['title'], 'release': _V['rel2'], 'filename': 'f.srt'}),
    ('all_empty', {'picked_release': '', 'tagline': '', 'label': '', 'li_filename': '', 'filepath': '', 'title': ''}),
    ('all_none', {'picked_release': None, 'tagline': None, 'label': None, 'li_filename': None, 'filepath': None,
                  'title': None}),
    ('slash_filepath', {'filepath': _V['slash']}),
    ('extonly_filepath+title', {'filepath': _V['extonly'], 'title': _V['title']}),
    ('short_all', {'picked_release': _V['short'], 'filepath': _V['short'], 'title': _V['short']}),
    ('scored_prefers_release_like', {'label': _V['bare'], 'tagline': 'Movie', 'filepath': _V['posix'], 'title': 'x'}),
    ('scored_tie_longer', {'label': 'Movie.2020.1080p.WEB-DL', 'tagline': 'Movie.2020.1080p.WEB-DL-GRP'}),
    ('scored_filepath_basename_slash', {'filepath': _V['slash'], 'label': 'Movie 2020'}),
]
for _cid, _inf in _COMBO:
    PR_CASES.append((_cid, _inf))
# episode context: the episode guard (picked_release naming another episode) and season-pack augmentation (sync)
for _cid, _inf in (
        ('ep:picked_wrong', {'picked_release': _V['wrongep'], 'label': _V['rel2']}),
        ('ep:picked_right', {'picked_release': _V['rel2'], 'label': _V['rel']}),
        ('ep:picked_pack', {'picked_release': _V['pack']}),
        ('ep:pack_movie_type', {'picked_release': _V['pack'], 'media_type': 'movie'}),
        ('ep:tagline_wrong_label_ok', {'tagline': _V['wrongep'], 'label': _V['rel2']}),
        ('ep:filepath_wrong', {'filepath': '/m/' + _V['wrongep'] + '.mkv', 'title': 'Show'}),
        ('ep:all_wrong', {'picked_release': _V['wrongep'], 'tagline': _V['wrongep'], 'label': _V['wrongep'],
                          'filepath': '/m/' + _V['wrongep'] + '.mkv', 'title': 'Show'}),
        ('ep:label_year_ep', {'label': 'Show.2014.205.HDTV-LOL'}),
        ('ep:pack_other_season', {'picked_release': 'Show.S03.1080p-GRP'}),
        ('ep:explicit_ep_kept', {'picked_release': 'Show.S01E02.1080p-GRP'}),
        ('ep:li_wrong_filepath_ok', {'li_filename': '/m/' + _V['wrongep'] + '.mkv', 'filepath': '/m/' + _V['rel2'] + '.mkv'}),
        ('ep:scored_wrong_filtered_everywhere', {'picked_release': _V['wrongep'], 'li_filename': '/m/' + _V['wrongep'] + '.mkv',
                                                 'label': 'Show.2014.HDTV', 'title': 'Show'}),
):
    _i = dict(_inf)
    _i.update({'season': '1', 'episode': '2', 'is_episode': 'true', 'media_type': _i.get('media_type', 'episode')})
    PR_CASES.append((_cid, _i))
PR_CASES.append(('ep:no_episode_number', dict({'picked_release': _V['wrongep']}, season='1', episode='')))
PR_CASES.append(('ep:bad_season', dict({'picked_release': _V['wrongep']}, season='x', episode='2')))

for _cid, _inf in PR_CASES:
    rec('pr.scored.' + _cid, _res, _copy.deepcopy(_inf), 'scored', _old_scored)
    rec('pr.picker.' + _cid, _picker, _inf)
    rec('pr.chooser.' + _cid, _res, _copy.deepcopy(_inf), 'chooser', _old_chooser)
    rec('pr.sync.' + _cid, _syncsvc.playing_release, _copy.deepcopy(_inf))
    rec('pr.pool.' + _cid, _res, _copy.deepcopy(_inf), 'pool', _old_pool)
    rec('pr.source.' + _cid, _res, _copy.deepcopy(_inf), 'source', _source6)

# the path cleaner and the two token tests, on a string pool
_STR = [v for v in _V.values() if isinstance(v, str)] + [
    'Movie.2020', 'x=1&y=2', 'a' * 24, 'deadbeef-' * 3, 'Movie.2020.1080p.WEB-DL.x264-GRP.mkv', '/a/b.c.d.e', 'C:/media/Movie.2020.mkv',
    'plugin://x?token=abc.mkv', 'Show.S01E02', '24chars-hex-0123456789abcdef', ' ', '\t', 'ABCDEF0123456789ABCDEF0123456789']
_rfp = get('_release_from_path', ['resources.lib.core.video_context', 'resources.lib.scoring_matching.ranking'])
if not _NEW_API:
    _tok_a = get('_looks_like_token', ['resources.lib.scoring_matching.ranking'])
    _tok_b = get('_is_token_like', ['resources.lib.fetching.pool_client'])
for _n, _s in enumerate(_STR + [None, '']):
    if _NEW_API:
        rec('pr.tok_a.%d' % _n, _vc.looks_like_token, _s)
        rec('pr.tok_b.%d' % _n, _vc.looks_like_token, _s, True)
    else:
        rec('pr.tok_a.%d' % _n, _tok_a, _s)
        rec('pr.tok_b.%d' % _n, _tok_b, _s)
    rec('pr.path.%d' % _n, _rfp, _s)
