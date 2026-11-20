# exec'd by golden.py (shares its namespace: R, rec, get, _SETTINGS, _PROPS, importlib, json, os, tempfile)
# list_candidates ordering + labels on RECORDED engine tuples (Subtitle Phase 3, step 3.0)
import copy as _copy
import importlib as _il

_svc = _il.import_module('resources.lib.fetching.service')
_ENC = get('_encode_link', ['resources.lib.core.link_codec'])
_DEC = get('_decode_link', ['resources.lib.core.link_codec'])
_kodi = _il.import_module('resources.lib.core.kodi')
_sel = _il.import_module('resources.lib.core.selection')
_bridge = _il.import_module('resources.lib.fetching.bridge')
_streams = _il.import_module('resources.lib.fetching.embedded.streams')
_local = _il.import_module('resources.lib.fetching.local')
_poolc = _il.import_module('resources.lib.fetching.pool_client')
_tcache = _il.import_module('resources.lib.core.cache.translation_cache')
_rows = _il.import_module('resources.lib.ui.chooser_rows')
_vc = _il.import_module('resources.lib.core.video_context')

_NOTIFY = []
_ROWPROPS = set()
_kodi.notify = lambda *a, **k: _NOTIFY.append((a, sorted(k.items())))


import re as _re_st


def _stars(c):
    """the star rating of a row (ui/listing): from the number `pct` when the row carries it, else from the label"""
    if 'pct' in c:
        p = c['pct']
        if p is not None:
            return str(max(1, min(5, int(round(min(100, int(p)) / 20.0)))))
        return str(c.get('rating', '4'))
    m = _re_st.search(r'(\d{1,3})%', c.get('filename', 'AI Hebrew'))
    if m:
        return str(max(1, min(5, int(round(min(100, int(m.group(1))) / 20.0)))))
    return str(c.get('rating', '4'))


def _eng(kind, src, lang, filename, pct, **extra):
    """one recorded bridge.search() row"""
    pl = {'type': 'engine', 'source': src, 'language': lang, 'filename': filename,
          'download_data': {'id': 7}}
    pl.update(extra)
    return {'filename': '%s · %d%%  —  %s' % (src, pct, filename), 'language': 'he' if kind != 'other' else lang,
            'link': _ENC(pl), 'sync': 'true' if pct >= 90 else 'false', 'rating': '4',
            'is_hi': False, 'is_hd': False, '_engine_kind': {'h': 'human_he', 'm': 'mt_he', 'o': 'foreign'}[kind],
            '_pct': pct}


def _emb(lang, idx, kind=None):
    return {'filename': 'מובנה · %s' % lang, 'language': lang,
            'link': _ENC({'type': 'engine', 'embedded': True, 'stream_index': idx, 'language': lang,
                                      'source': 'embedded'}),
            'sync': 'true', 'rating': '5', 'is_hi': False, 'is_hd': False,
            '_engine_kind': kind or ('embedded_he' if lang == 'he' else 'embedded_other'), '_pct': 101}


_REL = 'Movie.2020.1080p.WEB-DL.x264-GRP'
_H = [_eng('h', 'ktuvit', 'Hebrew', 'Movie.2020.1080p.WEB-DL.x264-GRP', 100),
      _eng('h', 'wizdom', 'Hebrew', 'Movie.2020.720p.BluRay.x264-OTHER', 62),
      _eng('h', 'ktuvit', 'Hebrew', 'Movie.2020.1080p.WEB.h264-AAA', 85)]
_M = [_eng('m', 'opensubtitles', 'HebrewMachineTranslated', 'Movie.2020.HebrewMachineTranslated', 40)]
_O = [_eng('o', 'opensubtitles', 'en', 'Movie.2020.720p.BluRay.x264-OTHER', 70),
      _eng('o', 'opensubtitles', 'en', 'Movie.2020.1080p.WEB-DL.x264-GRP.SDH', 55),
      _eng('o', 'opensubtitles', 'es', 'Movie.2020.1080p.WEB-DL.x264-GRP', 95),
      _eng('o', 'subscene', 'fr', 'Movie.2020.WEB.fr', 30),
      _eng('o', 'subscene', 'pt', 'Movie.2020.WEB.pt', 90),
      _eng('o', 'subscene', 'ru', 'Movie.2020.WEB.ru', 99),
      _eng('o', 'subscene', 'en', 'Movie.2020.WEB.en.2', 70)]
_PV = [{'kind': 'ktuvit', 'hash': 'k1', 'release': 'Movie.2020.1080p.WEB-DL.x264-GRP', 'source_lang': 'he'},
       {'kind': 'ktuvit', 'hash': 'k2', 'release': 'Movie.2020.2160p.REMUX-ZZZ', 'source_lang': 'he'},
       {'kind': 'ai', 'hash': 'a1', 'release': 'Movie.2020.720p.BluRay.x264-OTHER', 'source_lang': 'en'},
       {'kind': 'ai', 'hash': 'a2', 'release': 'Movie.2020.1080p.WEB-DL.x264-GRP', 'source_lang': 'es'},
       {'kind': 'ai', 'hash': 'a3', 'release': '', 'source_lang': 'en'},
       {'kind': 'ai', 'hash': 'a4', 'release': 'https://x.example/dl?token=abc', 'source_lang': 'fr'},
       {'kind': 'ai_emb', 'hash': 'e1', 'release': 'Movie.2020.1080p.WEB-DL.x264-GRP', 'source_lang': 'en'},
       {'kind': 'ai_emb', 'hash': 'e2', 'release': 'Movie.2020.1080p.WEB-DL.x264-GRP', 'source_lang': 'es'},
       {'kind': 'ai_emb', 'hash': 'e3', 'release': 'Other.Movie.1999.DVDRip-XX', 'source_lang': 'en'}]

_TMP = tempfile.mkdtemp(prefix='golist_')
_ALONG = os.path.join(_TMP, 'Movie.2020.en.srt')
with open(_ALONG, 'w', encoding='utf-8') as _f:
    _f.write('1\n00:00:01,000 --> 00:00:02,000\nhello\n')
_ALONG_HE = os.path.join(_TMP, 'Movie.2020.he.srt')
with open(_ALONG_HE, 'w', encoding='utf-8') as _f:
    _f.write('1\n00:00:01,000 --> 00:00:02,000\nשלום\n')

_INFO = {'filepath': _TMP + '/' + _REL + '.mkv', 'imdb_id': 'tt1234567', 'tmdb_id': '55', 'season': '', 'episode': '',
         'title': 'Movie', 'picked_release': _REL}


def _run_list(name, engine=None, embedded=None, pool=None, along=None, temp=None, settings=None, quick=False,
              info=None, engine_on=True, share=False, cur=None, cached=False, fail=None):
    _SETTINGS.clear()
    _SETTINGS.update(settings or {})
    _PROPS.clear()
    del _NOTIFY[:]
    eng = _copy.deepcopy(engine or [])
    emb = _copy.deepcopy(embedded or [])
    pv = _copy.deepcopy(pool or [])
    inf = _copy.deepcopy(info if info is not None else _INFO)
    _bridge.search = (lambda i, modal_progress=True, cache_only=False:
                      (None if (cache_only and fail == 'pending') else _copy.deepcopy(eng)))
    _streams.enabled = lambda: engine_on
    _streams.embedded_candidates = lambda i: _copy.deepcopy(emb)
    _poolc.use_enabled = lambda: bool(pool is not None)
    _poolc.share_enabled = lambda: share
    _poolc.lookup = lambda i: _copy.deepcopy(pv)
    _local.find_alongside = lambda fp: list(along or [])
    _local.find_in_temp = lambda: list(temp or [])
    _real_tp = _tcache.translated_path
    if cached:
        _tcache.translated_path = lambda *a, **k: _ALONG
    try:
        if cur is not None:
            # the "currently applied" row: run once to learn the link of row `cur`, then mark it
            first = _svc.list_candidates(_copy.deepcopy(inf), modal_progress=False, quick=quick)
            _sel.set_current_subtitle(first[cur]['link'])
        out = _svc.list_candidates(inf, modal_progress=False, quick=quick)
    finally:
        _tcache.translated_path = _real_tp
    rows = []
    stars = []
    for c in out:
        d = dict(c)
        d['link'] = _DEC(d.get('link') or '')
        stars.append(_stars(c))
        d.pop('pct', None)
        rows.append(d)
    R['liststars.' + name] = json.dumps(stars)
    # what the chooser window shows for every row (Step 6: ui/chooser_rows.build, one column per field and the info
    # panel's properties), against the playing release the rows are scored with
    _cl = []
    _playing = _vc.release_name(_copy.deepcopy(inf), 'picker') or ''
    for c in out:
        try:
            _row = _rows.build(dict(c), _playing)
            _ROWPROPS.update(_row['props'])
            _cl.append(json.loads(json.dumps(_row, ensure_ascii=False).replace(
                json.dumps(ROOT.replace(os.sep, '/'))[1:-1], '<A>').replace(json.dumps(ROOT)[1:-1], '<A>')))
        except Exception as e:      # noqa
            _cl.append('EXC ' + type(e).__name__)
    R['chooserrow.' + name] = json.dumps(_cl, ensure_ascii=False, sort_keys=True)
    _txt = json.dumps({'rows': rows, 'pending': inf.get('_list_pending'), 'notify': _NOTIFY[:]},
                      ensure_ascii=False, sort_keys=True)
    R['list.' + name] = _txt.replace(json.dumps(_TMP)[1:-1], '<T>').replace(_TMP, '<T>')


_PTH = [(_ALONG, 'en')]
_run_list('hebrew_mt_foreign', engine=_H + _M + _O)
_run_list('hebrew_skip_off', engine=_H + _O, settings={'skip_if_hebrew': 'false'})
_run_list('foreign_only', engine=_O)
_run_list('foreign_only_translation_none', engine=_O, settings={'translation_mode': 'none'})
_run_list('foreign_sources_all', engine=_O, settings={'src_lang_en': 'true', 'src_lang_es': 'true', 'src_lang_fr': 'true'})
_run_list('embedded_he_with_human', engine=_H + _O, embedded=[_emb('he', 3), _emb('en', 4)])
_run_list('embedded_foreign_only', engine=_O, embedded=[_emb('en', 2), _emb('fr', 5), _emb('es', 6), _emb('und', 7)])
_run_list('embedded_foreign_translation_none', engine=_O, embedded=[_emb('en', 2), _emb('fr', 5)],
          settings={'translation_mode': 'none'})
_run_list('embedded_no_engine_gate', engine=_O, embedded=[_emb('en', 2)], engine_on=False, along=_PTH)
_run_list('pool_all_variants', engine=_H + _O, pool=_PV)
_run_list('pool_no_engine_hebrew', engine=_O, pool=_PV, embedded=[_emb('en', 2), _emb('es', 6)])
_run_list('pool_share_harvest', engine=_H, pool=_PV, share=True)
_run_list('pool_ktuvit_only_pooled', engine=[_H[1]], pool=_PV[:2])
_run_list('pool_emb_other_source_hidden', engine=_O, pool=[_PV[8], _PV[6]], embedded=[_emb('en', 2)])
_run_list('sdh_first', engine=[_O[0], _O[1], _O[6]])
_run_list('ai_offer_alongside', engine_on=False, along=_PTH)
_run_list('ai_offer_temp', engine_on=False, temp=[{'lang': 'en', 'path': _ALONG}, {'lang': 'he', 'path': _ALONG_HE}])
_run_list('hebrew_passthrough', engine_on=False, along=[(_ALONG_HE, 'he'), (_ALONG, 'en')])
_run_list('ai_offer_cached', engine_on=False, along=_PTH, cached=True)
_run_list('current_row0', engine=_H + _M + _O, cur=0)
_run_list('current_row3', engine=_H + _M + _O, cur=3)
_run_list('quick_pool_skipped', engine=_O, pool=_PV, quick=True)
_run_list('quick_engine_pending', engine=_O, pool=_PV, quick=True, fail='pending')
_run_list('empty_no_ids', engine_on=False, info={'filepath': '', 'title': 'x'})
_run_list('empty_with_ids', engine_on=False, along=[], info=dict(_INFO))
_run_list('all_together', engine=_H + _M + _O, embedded=[_emb('he', 3), _emb('en', 4)], pool=_PV, share=True,
          along=_PTH, settings={'skip_if_hebrew': 'false'})
_run_list('video_ref_li_filename', engine=_H + _O, pool=_PV,
          info={'filepath': 'https://deb.example/d/abc123tokenabc123tokenabc123token', 'imdb_id': 'tt1',
                'li_filename': '/media/' + _REL + '.mkv', 'title': 'Movie'})
_run_list('video_ref_label', engine=_H, pool=_PV, info={'filepath': '', 'imdb_id': 'tt1', 'label': _REL, 'title': 'Movie'})
_run_list('video_ref_title_only', engine=_H, pool=_PV, info={'filepath': '', 'imdb_id': 'tt1', 'title': 'Movie 2020'})

# Step 6: every list-item property the chooser window XML reads is produced for a real row (a name that only the XML
# knows would show an empty column; the contract expects an empty list here)
with open(os.path.join(ROOT, 'resources', 'skins', 'Default', '1080i', 'script.moransubs.chooser.xml'), encoding='utf-8') as _xf:
    _xml_props = set(_re_st.findall(r'ListItem\.Property\((\w+)\)', _xf.read()))
R['chooserrow.xml_props_missing'] = json.dumps(sorted(_xml_props - _ROWPROPS))
R['chooserrow.xml_props_count'] = json.dumps(len(_xml_props))
