# Unit checks of the subtitle add-on's "VLSub / season pack / chooser badges" work (20 Nov 2026).
# Pure Python, no Kodi: the xbmc modules are stubbed by the caller or not needed. Run:
#   python -I .github/scripts/subs_contract/unit_session.py
# Exit code 0 = every check passed.

import os
import sys
import tempfile
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ADDON = os.path.join(ROOT, 'service.subtitles.kodipovilai')
sys.path.insert(0, ADDON)


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


class _Win(object):
    def __init__(self):
        self.p = {}

    def getProperty(self, k):
        return self.p.get(k, '')

    def setProperty(self, k, v):
        self.p[k] = v

    def clearProperty(self, k):
        self.p.pop(k, None)


WIN = _Win()
_stub('xbmc', executeJSONRPC=lambda s: '{}', sleep=lambda ms: None, log=lambda *a, **k: None, LOGINFO=1, LOGDEBUG=0,
      LOGWARNING=2, LOGERROR=3, Player=lambda: types.SimpleNamespace(isPlayingVideo=lambda: True, getPlayingFile=lambda: 'x.mkv'))
_stub('xbmcaddon', Addon=lambda *a, **k: types.SimpleNamespace(getSetting=lambda k: '', getAddonInfo=lambda k: ''))
_stub('xbmcvfs', translatePath=lambda p: p)
_stub('xbmcgui', Window=lambda _id: WIN, Dialog=object, INPUT_ALPHANUM=0, INPUT_NUMERIC=1)

FAILED = []


def check(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        FAILED.append(name)


# ---- 1. manual (VLSub) query ----------------------------------------------------------------------------------------
from resources.lib.fetching import manual

p = manual.parse_query('The.Odyssey.2026.1080p.WEB-DL.DDP5.1.x265-FLUX')
check('manual: title / year / tokens split', p['title'] == 'The Odyssey' and p['year'] == '2026' and 'flux' in p['tokens']
      and '1080p' in p['tokens'] and not p['keyword_only'])
p = manual.parse_query('Breaking Bad S02E03 720p BluRay')
check('manual: show + SxxEyy', p['title'] == 'Breaking Bad' and (p['season'], p['episode']) == ('2', '3'))
check('manual: a lone ALL-CAPS word is a release word', manual.parse_query('FLUX')['keyword_only'])
check('manual: a normal-case word is a title', not manual.parse_query('Inception')['keyword_only'])
check('manual: a title that is a year stays a title', manual.parse_query('2012')['title'] == '2012')
playing = {'title': 'The Odyssey', 'year': '2026', 'imdb_id': 'tt1', 'tmdb_id': '5',
           'picked_release': 'The.Odyssey.2026.1080p.WEB-DL.x265-flux'}
kw = manual.build_info(playing, 'flux')
check('manual: keyword search keeps the playing ids and title',
      kw['imdb_id'] == 'tt1' and kw['title'] == 'The Odyssey' and kw['_query_keyword_only'] and kw['_manual'])
same = manual.build_info(playing, 'The Odyssey 2026 FLUX')
other = manual.build_info(playing, 'Other Film 2020')
check('manual: same title keeps the ids, another title clears them', same['imdb_id'] == 'tt1' and other['imdb_id'] == ''
      and other['_query'] == 'Other Film 2020' and other['year'] == '2020')
ep = manual.build_info({'tvshow': 'Show', 'is_episode': True, 'season': '1', 'episode': '2', 'imdb_id': 'tt9'},
                       'Show S01E03 720p')
check('manual: another episode of the show clears the ids, keeps the show',
      ep['is_episode'] and ep['season'] == '1' and ep['episode'] == '3' and ep['imdb_id'] == '' and ep['tvshow'] == 'Show')
check('manual: default query is the release name', manual.default_query(playing, 'The.Odyssey.2026.1080p') == 'The.Odyssey.2026.1080p')
check('manual: head line', manual.head(kw) == 'חיפוש ידני: flux')
rows = [{'filename': 'A - Foo.720p.WEB'}, {'filename': 'B - Foo.1080p.WEB-DL.x265-FLUX'}, {'filename': 'C - Foo.1080p.BluRay'}]
ranked, full = manual.rank_by_tokens(rows, ['flux', '1080p'])
check('manual: rows with every release word come first, nothing is dropped',
      ranked[0]['filename'].startswith('B') and full == 1 and len(ranked) == 3 and ranked[1]['filename'].startswith('C'))

# the typed text reaches the engine and keys the caches
from resources.lib.fetching import bridge, search_cache
vd = bridge.build_video_data(other)
check('bridge: video_data carries the typed text and the manual flags', vd['query'] == 'Other Film 2020' and vd['manual']
      and not vd['keyword_only'])
check('bridge: an automatic search has no query', bridge.build_video_data(playing)['query'] == ''
      and not bridge.build_video_data(playing)['manual'])
k_auto = search_cache._cache_key(dict(playing))
k_man = search_cache._cache_key(dict(same))
k_man2 = search_cache._cache_key(manual.build_info(playing, 'The Odyssey 2026 WEB-DL'))
check('search cache: a manual query is never the automatic list, another text is another list',
      k_auto != k_man and k_man != k_man2)

# ---- 2. green release words + the negative AI badge -------------------------------------------------------------------------
from resources.lib.ui import chooser_rows as rows_mod

pl = 'The.Odyssey.2026.1080p.WEB-DL.DDP5.1.x265-FLUX'
cand = 'The Odyssey 2026 2160p WEBRip DD+5.1 HEVC HDR-FLUX.srt'
mask, words = rows_mod.shared_mask(cand, pl)
check('rows: whole shared tokens are found (title words, year, audio, codec, group); a different resolution and a '
      'different source tag (WEBRip vs WEB-DL) are not green',
      words == ['The', 'Odyssey', '2026', 'DD+5.1', 'HEVC', 'FLUX'])
painted = rows_mod.paint(cand, mask, 'FFFFFFFF')
check('rows: green words are painted with MATCH, others with the base colour',
      '[COLOR %s]FLUX[/COLOR]' % rows_mod.MATCH in painted and '2160p' in painted
      and '[COLOR %s]2160p' % rows_mod.MATCH not in painted)
check('rows: wrapped release keeps the paint', 'COLOR %s' % rows_mod.MATCH in rows_mod.wrap_release(cand, 28, 5, mask)
      and rows_mod.wrap_release(cand, 28, 5, mask).count('[CR]') >= 1)
check('rows: a manual query word is green too', rows_mod.shared_mask('Foo.Flux.720p', pl, ['flux'])[1] == ['Flux'])

# whole tokens only: no prefix of a mismatched tag is painted
_pl_rip = 'The.Odyssey.2026.1080p.WEBRip.DDP5.1.x265-FLUX'
_dl = 'The.Odyssey.2026.1080p.WEB-DL.DDP5.1.x265-FLUX.srt'
_m_dl, _w_dl = rows_mod.shared_mask(_dl, _pl_rip)
check('rows: WEB-DL against a WEBRip file is painted by NONE of its characters (no green "WEB" with a white "-DL")',
      'WEB-DL' not in _w_dl and not any(_m_dl[_dl.index('WEB-DL'):_dl.index('WEB-DL') + 6])
      and _w_dl == ['The', 'Odyssey', '2026', '1080p', 'DDP5.1', 'x265', 'FLUX'])
check('rows: the same tag in two spellings is one token (WEB-DL / WEB.DL, H.264 / x264)',
      rows_mod.shared_mask('Foo.Bar.WEB.DL.x264', 'Foo.Bar.WEB-DL.H.264')[1] == ['Foo', 'Bar', 'WEB.DL', 'x264'])
check('rows: a query word that is only PART of a compound tag does not paint it, all its parts do',
      rows_mod.shared_mask('Foo.WEB-DL', '', ['web'])[1] == []
      and rows_mod.shared_mask('Foo.WEB-DL', '', ['web', 'dl'])[1] == ['WEB-DL'])
_wr = rows_mod.wrap_release(_dl[:-4], 14, 5, _m_dl)
check('rows: every wrapped line of the info panel closes its own colour tags (no tag is cut across [CR])',
      '[CR]' in _wr and all(_ln.count('[COLOR ') == _ln.count('[/COLOR]') and _ln.count('[COLOR ') >= 1
                            for _ln in _wr.split('[CR]')))
check('rows: stray Kodi formatting tags are stripped from a display title',
      rows_mod.strip_bbcode('[B]X.720p[/B]') == 'X.720p' and rows_mod.strip_bbcode('A[/B] [COLOR red]b[/COLOR]') == 'A b'
      and rows_mod.strip_bbcode('[YTS.MX] Movie') == '[YTS.MX] Movie')
from resources.lib.scoring_matching import release as _rel
check('release group: a file size and a generic descriptor are never the group',
      all(_rel.parse('Movie.2020.1080p.BluRay.x264-' + t)['group'] == '' for t in ('550MB', '4GB', 'LOSSLESS', 'CLEAN', 'DUAL'))
      and _rel.parse('Movie.2020.1080p.BluRay.x264.1.4GB')['group'] == ''
      and _rel.parse('Movie.2020.1080p.BluRay.x264-FLUX')['group'] == 'flux'
      and not _rel.is_group_token('550mb') and _rel.is_group_token('ntb'))
check('rows: a group-less size / descriptor tail is no green "group"',
      rows_mod.shared_mask('Movie.1080p-550MB', 'Movie.1080p-550MB')[1] == ['Movie', '1080p', '550MB']
      and not any(s[2] == 'group' for s in rows_mod.release_spans('Movie.1080p-LOSSLESS')))

# ---- 2b. red conflict tokens (0.3.109): a whole token that contradicts the playing file's timeline ---------------------------
def _red(sub, pl):
    return rows_mod.conflict_mask(sub, pl)[1]


def _cols(sub, pl, want=None):
    m, sh, co = rows_mod.release_mask(sub, pl, want)
    return sh, co


_WEB = 'Movie.2020.1080p.WEB-DL.x264-GRP'
check('red: the red is the window\'s own warning red (the "ללא תרגום AI" chip), not a new colour',
      rows_mod.RED == 'FFE5645A')
check('red: pre-retail captures against a retail file (CAM, HDCAM, TS, TELESYNC, HDTS, TC, TELECINE, SCR, SCREENER, DVDSCR, R5, '
      'WORKPRINT)',
      all(_red('Movie.2020.%s.x264-GRP' % t, _WEB) == [t] for t in
          ('CAM', 'HDCAM', 'TS', 'TELESYNC', 'HDTS', 'TC', 'TELECINE', 'SCR', 'SCREENER', 'DVDSCR', 'R5', 'WORKPRINT')))
check('red: ... against every retail tag (WEB-DL, WEBRip, BluRay, DVDRip)',
      all(_red('Movie.2020.HDCAM.x264', 'Movie.2020.1080p.%s.x264-GRP' % t) == ['HDCAM']
          for t in ('WEB-DL', 'WEBRip', 'BluRay', 'DVDRip')))
check('red: symmetric, a retail subtitle against a CAM / TS file paints the retail tag',
      _red(_WEB, 'Movie.2020.HDCAM.x264-GRP') == ['WEB-DL'] and _red('Movie.2020.1080p.BluRay.x264', 'Movie.2020.TS.x264') == ['BluRay']
      and _red('Movie.2020.DVDRip.x264', 'Movie.2020.TELESYNC') == ['DVDRip'])
check('red: never when both sides say the same (CAM vs CAM is green), never without a playing release',
      _cols('Movie.2020.CAM.x264-GRP', 'Movie.2020.CAM.x264-GRP') == (['Movie', '2020', 'CAM', 'x264', 'GRP'], [])
      and _red('Movie.2020.CAM.x264', '') == [] and _red('', _WEB) == [])
check('red: a short title word is no tag ("Cam.2018", "DC.League...", "TS" before the year), the ".ts" extension is no TS',
      _red('Cam.2018.1080p.WEB-DL-GRP', 'Cam.2018.1080p.NF.WEB-DL.x264-GRP') == []
      and _red('DC.League.of.Super-Pets.2022.1080p.WEB-DL', 'Movie.2022.1080p.WEB-DL') == []
      and _red('TS.Movie.2020.1080p.WEB-DL', _WEB) == [] and _red('Movie.2020.1080p.WEB-DL.x264-GRP.ts', _WEB) == [])
check('red: bonus content the playing file is not (Trailer, Teaser, Promo, Making.Of, Behind.The.Scenes, Featurette, Extras, '
      'Bonus, Deleted.Scenes, Recap, Sample), each as ONE whole token',
      all(_red('Movie.2020.%s.1080p.WEB-DL' % t, _WEB) == [t] for t in
          ('Trailer', 'Teaser', 'Promo', 'Making.Of', 'Behind.The.Scenes', 'Featurette', 'Extras', 'Bonus', 'Deleted.Scenes',
           'Recap', 'Sample')))
check('red: bonus content is green when the playing file is the same bonus, and the show "Extras" is a title',
      _cols('Movie.2020.Trailer.1080p', 'Movie.2020.Trailer.720p') == (['Movie', '2020', 'Trailer'], [])
      and _red('Extras.S01E01.720p.HDTV', 'Extras.S01E01.720p.HDTV-GRP') == [])
check('red: a special cut against an untagged file (Extended, Extended.Cut, Director\'s.Cut, DC, Unrated, Uncut, IMAX, Remastered, '
      'Special.Edition, Criterion)',
      all(_red('Movie.2020.%s.1080p.BluRay' % t, 'Movie.2020.1080p.BluRay.x264-GRP') == [t] for t in
          ('Extended', 'Extended.Cut', "Director's.Cut", 'DC', 'Unrated', 'Uncut', 'IMAX', 'Remastered', 'Special.Edition',
           'Criterion')))
check('red: two different editions, both ways; the same edition in two spellings is green',
      _red("Movie.2020.Director's.Cut.1080p.BluRay", 'Movie.2020.Extended.1080p.BluRay') == ["Director's.Cut"]
      and _red('Movie.2020.Extended.1080p.BluRay', "Movie.2020.Director's.Cut.1080p.BluRay") == ['Extended']
      and _cols('Movie.2020.Extended.Cut.1080p', 'Movie.2020.EXTENDED.720p') == (['Movie', '2020', 'Extended.Cut'], []))
check('red: Theatrical is the untagged cut (not red against an untagged file), red against another edition',
      _red('Movie.2020.Theatrical.1080p.BluRay', 'Movie.2020.1080p.BluRay') == []
      and _red('Movie.2020.Theatrical.1080p.BluRay', 'Movie.2020.Extended.1080p.BluRay') == ['Theatrical'])
check('red: broadcast captures against a WEB / BluRay file (HDTV, PDTV, SDTV, PAL, NTSC)',
      all(_red('Show.S01E01.%s.x264' % t, 'Show.S01E01.1080p.WEB-DL.x264-GRP') == [t]
          for t in ('HDTV', 'PDTV', 'SDTV', 'PAL', 'NTSC'))
      and _red('Show.S01E01.HDTV.x264', 'Show.S01E01.1080p.BluRay.x264') == ['HDTV'])
check('red: symmetric, a WEB / BluRay subtitle against an HDTV file; PAL against NTSC; HDTV vs PDTV is not red',
      _red('Show.S01E01.1080p.BluRay.x264', 'Show.S01E01.720p.HDTV.x264-LOL') == ['BluRay']
      and _red('Show.S01E01.1080p.WEB-DL', 'Show.S01E01.PAL.x264') == ['WEB-DL']
      and _red('Movie.2020.PAL.DVDRip', 'Movie.2020.NTSC.DVDRip') == ['PAL']
      and _red('Show.S01E01.PDTV.x264', 'Show.S01E01.HDTV.x264') == [])
check('red: WEB against BluRay / DVDRip is not red (not a listed conflict; the match % already caps it)',
      _red('Movie.2020.1080p.BluRay.x264-OTHER', _WEB) == [] and _red('Movie.2020.DVDRip-XX', _WEB) == [])
check('red: another frame rate (25fps vs 23.976fps / 24fps), both ways; PAL is 25 fps; the same rate is green',
      _red('Movie.2020.1080p.WEB-DL.25fps', 'Movie.2020.1080p.WEB-DL.23.976fps') == ['25fps']
      and _red('Movie.2020.1080p.WEB-DL.23.976fps', 'Movie.2020.1080p.WEB-DL.25fps') == ['23.976fps']
      and _red('Movie.2020.1080p.WEB-DL.25fps', 'Movie.2020.1080p.WEB-DL.24fps') == ['25fps']
      and _red('Movie.2020.DVDRip.PAL', 'Movie.2020.DVDRip.23.976fps') == ['PAL']
      and _cols('Movie.2020.WEB.23.98fps', 'Movie.2020.WEB.23.976fps')[1] == []
      and '23.98fps' in _cols('Movie.2020.WEB.23.98fps', 'Movie.2020.WEB.23.976fps')[0])
check('red: another streaming service (NF vs AMZN, DSNP, ATVP, MAX, HULU, PCOK, PMTP), both ways',
      all(_red('Show.S01E01.1080p.%s.WEB-DL' % t, 'Show.S01E01.1080p.NF.WEB-DL-GRP') == [t]
          for t in ('AMZN', 'DSNP', 'ATVP', 'MAX', 'HULU', 'PCOK', 'PMTP'))
      and _red('Show.S01E01.1080p.NF.WEB-DL', 'Show.S01E01.1080p.AMZN.WEB-DL') == ['NF'])
check('red: a service only among the technical tags ("Mad.Max" is a title), an unknown service is no conflict, aliases agree',
      _red('Mad.Max.2015.1080p.AMZN.WEB-DL', 'Mad.Max.2015.1080p.NF.WEB-DL') == ['AMZN']
      and _red('Show.S01E01.1080p.AMZN.WEB-DL', 'Show.S01E01.1080p.WEB-DL') == []
      and _cols('Movie.2020.1080p.AMAZON.WEB-DL', 'Movie.2020.1080p.AMZN.WEB-DL') == (
          ['Movie', '2020', '1080p', 'AMAZON', 'WEB-DL'], []))
check('red: another episode, and a multi-episode file against a single episode (S01E01E02, S01E01-E02, S01E01-02), both ways',
      _red('Show.S01E02.1080p.WEB-DL', 'Show.S01E01.1080p.WEB-DL') == ['S01E02']
      and _red('Show.S01E01E02.1080p.WEB-DL', 'Show.S01E01.1080p.WEB-DL') == ['S01E01E02']
      and _red('Show.S01E01-E02.1080p.WEB-DL', 'Show.S01E01.1080p.WEB-DL') == ['S01E01-E02']
      and _red('Show.S01E01-02.1080p.WEB-DL', 'Show.S01E01.1080p.WEB-DL') == ['S01E01-02']
      and _red('Show.S01E01.1080p.WEB-DL', 'Show.S01E01E02.1080p.WEB-DL') == ['S01E01'])
check('red: the same episode in two spellings is green (S1E1 / S01E01, S01E01E02 / S01E01-E02)',
      _cols('Show.S1E1.720p', 'Show.S01E01.1080p') == (['Show', 'S1E1'], [])
      and _cols('Show.S01E01E02.720p', 'Show.S01E01-E02.1080p') == (['Show', 'S01E01E02'], []))
_cm, _cw = rows_mod.conflict_mask('Show.S01E02.HDTV.x264-LOL', 'Show.S01E01.1080p.WEB-DL.x264-GRP')
check('red: whole tokens only (every character of the token, none outside it)',
      _cw == ['S01E02', 'HDTV'] and ''.join(ch for ch, f in zip('Show.S01E02.HDTV.x264-LOL', _cm) if f) == 'S01E02HDTV')
_mk, _sh, _co = rows_mod.release_mask('Show.S01E02.HDTV.x264-LOL', 'Show.S01E01.1080p.WEB-DL.x264-GRP')
check('red + green in one name: conflict tokens red, shared tokens green, the rest uncoloured',
      _sh == ['Show', 'x264'] and _co == ['S01E02', 'HDTV']
      and rows_mod.paint('Show.S01E02.HDTV.x264-LOL', _mk, rows_mod.WHITE) ==
      '[COLOR {g}]Show[/COLOR][COLOR {w}].[/COLOR][COLOR {r}]S01E02[/COLOR][COLOR {w}].[/COLOR][COLOR {r}]HDTV[/COLOR]'
      '[COLOR {w}].[/COLOR][COLOR {g}]x264[/COLOR][COLOR {w}]-LOL[/COLOR]'.format(g=rows_mod.MATCH, r=rows_mod.RED, w=rows_mod.WHITE))
check('red: a conflict wins over a manual-query word (it is a fact about the playing file)',
      _cols('Show.S01E01.HDTV.x264', 'Show.S01E01.1080p.WEB-DL', ['hdtv']) == (['Show', 'S01E01'], ['HDTV']))
check('red: an identical release has no red at all, and the green of the old boolean mask is unchanged',
      _cols(_WEB, _WEB)[1] == [] and rows_mod.paint('A.B', [True, False, True], None) ==
      '[COLOR %s]A[/COLOR].[COLOR %s]B[/COLOR]' % (rows_mod.MATCH, rows_mod.MATCH))
_long = "Movie.Title.2020.Director's.Cut.1080p.HDTV.x264-GRP"
_lm = rows_mod.release_mask(_long, 'Movie.Title.2020.1080p.WEB-DL.x264-GRP')[0]
_lw = rows_mod.wrap_release(_long, 14, 5, _lm)
check('red: every wrapped line of the info panel closes its own colour tags, red ones included',
      _lw.count('[CR]') >= 2 and ('COLOR %s' % rows_mod.RED) in _lw
      and all(_ln.count('[COLOR ') == _ln.count('[/COLOR]') >= 1 for _ln in _lw.split('[CR]'))
      and rows_mod.strip_bbcode(_lw).replace('[CR]', '') == _long)
check('red: a red token cut by the 2-line limit ("...") keeps its tags balanced',
      all(_ln.count('[COLOR ') == _ln.count('[/COLOR]')
          for _ln in rows_mod.wrap_release(_long, rows_mod.RELEASE_WIDTH, 1, _lm).split('[CR]')))
from resources.lib.core.link_codec import encode_link as _enc_red
_rrow = {'filename': 'Ktuvit · 30%  —  Show.S01E02.HDTV.x264-LOL', 'language': 'he', 'pct': 30,
         'link': _enc_red({'type': 'engine', 'source': 'ktuvit', 'filename': 'Show.S01E02.HDTV.x264-LOL', 'language': 'Hebrew'})}
_rb = rows_mod.build(_rrow, 'Show.S01E01.1080p.WEB-DL.x264-GRP')
_ok = rows_mod.build(dict(_rrow, link=_enc_red({'type': 'engine', 'source': 'ktuvit', 'filename': 'Show.S01E01.1080p.WEB-DL.x264-GRP',
                                                 'language': 'Hebrew'})), 'Show.S01E01.1080p.WEB-DL.x264-GRP')
check('red: a chooser row paints its conflicts red in the release column and the panel, the % is the row\'s own',
      '[COLOR %s]S01E02[/COLOR]' % rows_mod.RED in _rb['props']['release']
      and '[COLOR %s]HDTV[/COLOR]' % rows_mod.RED in _rb['props']['p_release']
      and '30%' in _rb['props']['pct'] and 'COLOR %s' % rows_mod.RED not in _ok['props']['release']
      and 'COLOR %s' % rows_mod.RED not in _ok['props']['p_release'])
_sc = _rel.score('Show.S01E01.1080p.WEB-DL.x264-GRP', 'Show.S01E02.HDTV.x264-LOL')
check('red: the scorer still reads the pair as before (another episode, capped at 15); the paint moves no number',
      _sc[1] == _rel.TIER_CROSS and _sc[0] <= 15 and _sc[2] == ['different episode (S01E01 vs S01E02)'])
check('release: service_token reads the service table only', _rel.service_token('AMAZON') == 'amzn'
      and _rel.service_token('nf') == 'nf' and _rel.service_token('Movie') == '' and _rel.service_token(None) == '')

from resources.lib.core.link_codec import encode_link


def _row(link, name, lang='en', pct=80):
    return {'filename': name, 'language': lang, 'link': encode_link(link), 'pct': pct}


ai_on = {'ok': True, 'why': '', 'emb_ok': True, 'emb_why': '', 'embedded_miss': False, 'embedded_miss_why': ''}
ai_off = dict(ai_on, ok=False, why='תרגום AI כבוי בהגדרות')
ai_miss = dict(ai_on, embedded_miss=True, embedded_miss_why='הכתובית המובנית בקובץ היא תמונה (PGS / VobSub) ולא טקסט')
engine_ai = _row({'type': 'engine_ai', 'source': 'opensubtitles', 'filename': 'X.720p.WEB-DL', 'src_lang': 'en'},
                 'תרגום AI לעברית · 80%  —  X.720p.WEB-DL')
engine_en = _row({'type': 'engine', 'source': 'opensubtitles', 'filename': 'X.720p.WEB-DL', 'language': 'English'},
                 'OpenSubtitles · 80%  —  X.720p.WEB-DL')
engine_he = _row({'type': 'engine', 'source': 'ktuvit', 'filename': 'X.720p.WEB-DL', 'language': 'Hebrew'},
                 'Ktuvit · 90%  —  X.720p.WEB-DL', lang='he', pct=90)
emb_ai = _row({'type': 'embedded_ai', 'src_lang': 'en', 'stream_index': 1}, 'תרגום מובנה → עברית (AI) · אנגלית', lang='he')
pool_ai = _row({'type': 'pool', 'pool_kind': 'ai', 'hash': 'h', 'release': 'X.720p'}, 'תרגום AI · מאגר קהילתי · 70%  —  X.720p', lang='he')


def chips(c, ai):
    r = rows_mod.build(c, pl, ai=ai)
    return ' '.join([r['props']['b1'], r['props']['b2'], r['props']['b3'], r['props']['p_kind']])


NEG = 'ללא תרגום AI'
check('badge: an AI-translatable row carries NO "תרגום AI" badge and no negative one',
      'תרגום AI' not in ' '.join(rows_mod.build(engine_ai, pl, ai=ai_on)['props'][k] for k in ('b1', 'b2', 'b3'))
      and NEG not in chips(engine_ai, ai_on))
check('badge: AI off -> the AI row and the raw foreign row say "ללא תרגום AI"', NEG in chips(engine_ai, ai_off)
      and NEG in chips(engine_en, ai_off))
check('badge: a Hebrew row never carries it', NEG not in chips(engine_he, ai_off))
check('badge: a pooled translation needs no translator', NEG not in chips(pool_ai, ai_off))
check('badge: an embedded track already known to be a picture is marked', NEG in chips(emb_ai, ai_miss)
      and 'PGS' in rows_mod.build(emb_ai, pl, ai=ai_miss)['props']['p_kind'])
check('badge: no context claims nothing', NEG not in chips(engine_en, None))

# ---- 2b. wording of embedded subtitles and of the "type" field ------------------------------------------------------------------
emb_he = _row({'type': 'engine', 'embedded': True, 'stream_index': 3, 'lang': 'he'}, 'תרגום מובנה בעברית · 101%', lang='he', pct=101)
emb_en = _row({'type': 'engine', 'embedded': True, 'stream_index': 4, 'lang': 'en'}, '[מובנה] EN', lang='en', pct=0)
pool_emb = _row({'type': 'pool', 'pool_kind': 'ai_emb', 'hash': 'h', 'release': 'X.720p', 'source_lang': 'en'},
                'תרגום מובנה AI (אנגלית) · מאגר קהילתי · 100%  —  X.720p', lang='he', pct=100)
_all_text = ''
for _c in (engine_ai, engine_en, engine_he, emb_ai, pool_ai, emb_he, emb_en, pool_emb):
    _r = rows_mod.build(_c, pl, ai=ai_on)
    _all_text += _r['label'] + ' ' + ' '.join(str(v) for v in _r['props'].values()) + ' '
check('wording: "מסלול" (track) is not shown by any row or panel field', 'מסלול' not in _all_text)
_pe = rows_mod.build(emb_en, pl, ai=ai_on)
_ph = rows_mod.build(emb_he, pl, ai=ai_on)
_pa = rows_mod.build(emb_ai, pl, ai=ai_on)
check('wording: the original embedded subtitle is "כתובית מובנית בקובץ (<lang>)", source "כתובית מובנית", type "כתובית מובנית ב<lang>"',
      _pe['label'] == 'כתובית מובנית בקובץ (אנגלית)' and 'כתובית מובנית' in _pe['props']['src']
      and _pe['props']['p_kind'] == 'כתובית מובנית באנגלית' and _ph['label'] == 'כתובית מובנית בקובץ (עברית)')
check('wording: the AI row of an embedded subtitle is "תרגום לעברית מכתובית מובנית (<lang>)"',
      _pa['label'] == 'תרגום לעברית מכתובית מובנית (אנגלית)' and _pa['props']['p_kind'] == 'כתובית מובנית באנגלית')
check('wording: no redundant "מובנה" chip on an embedded row (the title and the source already say it)',
      all('מובנה' not in _r['props'][k] for _r in (_pe, _ph, _pa) for k in ('b1', 'b2', 'b3')))
check('wording: the "type" field of an untranslated foreign row is its source and language, never "תרגום AI ..."',
      rows_mod.build(engine_ai, pl, ai=ai_on)['props']['p_kind'] == 'כתובית באנגלית'
      and rows_mod.build(engine_en, pl, ai=ai_on)['props']['p_kind'] == 'כתובית אנושית (ספק חיצוני)'
      and 'תרגום AI' not in rows_mod.build(emb_ai, pl, ai=ai_on)['props']['p_kind'])
check('wording: "תרגום AI (מאגר קהילתי)" stays for rows fetched already translated from the community pool',
      rows_mod.build(pool_ai, pl, ai=ai_on)['props']['p_kind'] == 'תרגום AI (מאגר קהילתי)'
      and rows_mod.build(pool_emb, pl, ai=ai_on)['props']['p_kind'] == 'תרגום AI מכתובית מובנית באנגלית (מאגר קהילתי)')
_stray = rows_mod.build(_row({'type': 'engine_ai', 'source': 'yify', 'src_lang': 'en'}, '[B]תרגום AI · 80%  —  X.720p.WEB-DL[/B]'), pl)
check('wording: a stray [B] / [/B] of a stage label never reaches the window',
      '[/B]' not in _stray['props']['p_release'] and _stray['props']['release'].count('[B]') == 1
      and _stray['props']['release'].count('[/B]') == 1)

# ---- 3. season pack ------------------------------------------------------------------------------------------------------------
from resources.lib.fetching import season_pack

tmp = tempfile.mkdtemp()
pack_dir = os.path.join(tmp, 'dl')
os.makedirs(os.path.join(pack_dir, 'Show.S01.WEB'))
for n in (1, 2, 3):
    with open(os.path.join(pack_dir, 'Show.S01.WEB', 'Show.S01E0%d.WEB.srt' % n), 'w', encoding='utf-8') as f:
        f.write('1\n00:00:01,000 --> 00:00:02,000\nשלום מספר %d\n' % n)
check('pack: episode tags are read from names', season_pack.episode_of('Show.S01E03.WEB.srt') == (1, 3)
      and season_pack.episode_of('Show 2x07.srt') == (2, 7) and season_pack.episode_of('Show.1080p.srt') is None)
eps = season_pack.scan_folder(pack_dir)
check('pack: a folder with several episodes is a pack, one file is not', season_pack.is_pack(eps) and len(eps) == 3
      and not season_pack.is_pack({(1, 1): 'x'}))
check('pack: next episode is the next number, else the first of the next season',
      season_pack.next_episode(eps, 1, 2) == (1, 3) and season_pack.next_episode(eps, 1, 3) is None
      and season_pack.next_episode({(1, 9): 'a', (2, 1): 'b'}, 1, 9) == (2, 1))
stored = {}
info = {'tvshow': 'Show', 'is_episode': True, 'season': '1', 'episode': '2', 'imdb_id': 'tt1'}
res = season_pack.handle_download(os.path.join(pack_dir, 'Show.S01.WEB', 'Show.S01E01.WEB.srt'), pack_dir, info, 'Hebrew',
                                  cache_store=lambda path, t: stored.__setitem__(t, path), folder=os.path.join(tmp, 'sp'))
check('pack: the PLAYING episode\'s file is delivered, not the provider\'s first one',
      res and res['file'].endswith('S01E02.WEB.srt') and res['tag'] == 'S01E02')
check('pack: the current and the next episode are cached per episode', set(stored) == {'S01E02', 'S01E03'})
nxt = dict(info, episode='3')
kept = season_pack.lookup_for(nxt, folder=os.path.join(tmp, 'sp'))
check('pack: the next episode\'s Hebrew file is found for the next episode (and only for it)',
      kept and 'מספר 3' in open(kept['path'], encoding='utf-8').read()
      and season_pack.lookup_for(dict(info, episode='5'), folder=os.path.join(tmp, 'sp')) is None
      and season_pack.lookup_for(dict(info, tvshow='Other'), folder=os.path.join(tmp, 'sp')) is None)
check('pack: a movie or a single file is not touched',
      season_pack.handle_download('x.srt', pack_dir, {'title': 'Film'}, 'Hebrew') is None)
foreign = season_pack.handle_download('x.srt', pack_dir, info, 'English', cache_store=lambda p, t: None, folder=os.path.join(tmp, 'sp2'))
check('pack: a foreign-language pack is cached per episode but not offered as a Hebrew local row',
      foreign and season_pack.lookup_for(nxt, folder=os.path.join(tmp, 'sp2')) is None)

check('pack: HI / SDH / CC copies are recognised by name, ordinary words are not',
      all(season_pack.is_hearing_impaired(n) for n in ('Show.S01E01.HI.srt', 'Show.S01E01.sdh.srt', 'Show S01E01 CC.srt',
                                                       'Show.S01E01.Hearing.Impaired.srt'))
      and not any(season_pack.is_hearing_impaired(n) for n in ('Show.S01E01.WEB.srt', 'Chi.Show.S01E01.srt', 'Show.S01E01.1080p.srt')))
dup = os.path.join(tmp, 'dup')
os.makedirs(os.path.join(dup, 'a'))
for fn in ('Show.S01E01.720p.HDTV.x264-LOL.HI.srt', 'Show.S01E01.1080p.WEB.H264-FLUX.srt', 'Show.S01E01.720p.HDTV.x264-LOL.srt',
           'Show.S01E02.1080p.WEB.H264-FLUX.srt'):
    with open(os.path.join(dup, 'a', fn), 'w', encoding='utf-8') as f:
        f.write('1\n00:00:01,000 --> 00:00:02,000\n' + fn + '\n')
check('pack: two files for one episode: the non-HI file of the playing release wins',
      season_pack.scan_folder(dup, 'Show.S01E01.720p.HDTV.x264-LOL')[(1, 1)].endswith('Show.S01E01.720p.HDTV.x264-LOL.srt')
      and season_pack.scan_folder(dup, 'Show.S01E01.1080p.WEB.H264-FLUX')[(1, 1)].endswith('Show.S01E01.1080p.WEB.H264-FLUX.srt'))
check('pack: without a playing release a non-HI file still beats the HI copy',
      not season_pack.is_hearing_impaired(season_pack.scan_folder(dup)[(1, 1)]))
hi_only = os.path.join(tmp, 'hi_only')
os.makedirs(hi_only)
for fn in ('Show.S01E01.WEB.HI.srt', 'Show.S01E02.WEB.srt'):
    open(os.path.join(hi_only, fn), 'w', encoding='utf-8').write('x')
check('pack: an HI file is still used when it is the only one for the episode',
      season_pack.scan_folder(hi_only, 'Show.S01E01.WEB')[(1, 1)].endswith('Show.S01E01.WEB.HI.srt'))
check('pack: playing_release is a string (empty when nothing is known)', isinstance(season_pack.playing_release({}), str))

# ---- 4. standalone (no library metadata) --------------------------------------------------------------------------------------
from resources.lib.core import video_context as vc

pr = vc.parse_release('Show.Name.S02E05.1080p.WEB-DL.DDP5.1.x265-FLUX.mkv')
check('standalone: an episode file name gives show / season / episode',
      pr == {'title': 'Show Name', 'year': '', 'season': '2', 'episode': '5', 'is_episode': True})
pr = vc.parse_release('/media/Film.Name.2019.1080p.BluRay.x264-GRP.mkv')
check('standalone: a movie file name gives title / year', pr and pr['title'] == 'Film Name' and pr['year'] == '2019'
      and not pr['is_episode'])
check('standalone: a debrid token is no name', vc.parse_release('https://x/y?token=abc') is None)
info = {'imdb_id': '', 'tmdb_id': '', 'dbid': '', 'title': 'Film.Name.2019.1080p.BluRay.x264-GRP', 'year': '', 'season': '',
        'episode': '', 'tvshow': '', 'is_episode': False, 'filepath': 'smb://nas/Film.Name.2019.1080p.BluRay.x264-GRP.mkv',
        'li_filename': '', 'picked_release': ''}
vc._enrich_without_metadata(info)
check('standalone: a plain file gets a real title and year', info['title'] == 'Film Name' and info['year'] == '2019')
lib = {'imdb_id': 'tt1', 'tmdb_id': '', 'dbid': '', 'title': 'Library.Title.2001.1080p', 'year': '2001', 'season': '',
       'episode': '', 'tvshow': '', 'is_episode': False, 'filepath': 'x.mkv', 'li_filename': '', 'picked_release': ''}
vc._enrich_without_metadata(lib)
check('standalone: a video that has ids is left exactly as Kodi gave it', lib['title'] == 'Library.Title.2001.1080p')
ep = {'imdb_id': '', 'tmdb_id': '', 'dbid': '', 'title': 'Show.Name.S02E05.720p.HDTV.x264-GRP', 'year': '', 'season': '',
      'episode': '', 'tvshow': '', 'is_episode': False, 'filepath': '', 'li_filename': '', 'picked_release': ''}
vc._enrich_without_metadata(ep)
check('standalone: an episode file gets show, season and episode', ep['tvshow'] == 'Show Name' and ep['season'] == '2'
      and ep['episode'] == '5' and ep['is_episode'])
vc.ids_cache_put(ep, 'tt777', '2015')
ep2 = dict(ep, tvshow='', season='', episode='', title='Show.Name.S02E06.720p.HDTV.x264-GRP', is_episode=False)
vc._enrich_without_metadata(ep2)
check('standalone: an id found once is carried by the next episode of the show (no network)',
      ep2['imdb_id'] == 'tt777' and ep2['year'] == '2015' and ep2['episode'] == '6')

# ---- 2. the service names the threads that outlive it (core/threads.py, 20 Nov 2026) ------------------------------------
import threading

from resources.lib.core import threads as svc_threads

_lines = []
svc_threads.kodi.log = lambda msg, level='INFO': _lines.append((level, msg))
_stop = threading.Event()
_t = threading.Thread(target=_stop.wait, name='subs-straggler-test', daemon=True)
_t.start()
_left = svc_threads.log_survivors(wait=0.2)
check('threads: a thread that does not end is counted, daemon or not', _left == 1)
check('threads: it is named, flagged daemon and shown with the frame it waits in',
      len(_lines) == 1 and _lines[0][0] == 'WARNING' and '"subs-straggler-test" (daemon)' in _lines[0][1]
      and 'threading.py' in _lines[0][1])
_stop.set()
_t.join(2)
_lines[:] = []
check('threads: silent when every thread ended', svc_threads.log_survivors(wait=0.2) == 0 and not _lines)

# ---- 5. B1: the applied auto-sync is published as window properties (subs.sync.offset / subs.sync.mode) ------------------
import json

from resources.lib.core import selection as core_selection
from resources.lib.sync import common as sync_common
from resources.lib.sync import jobs as sync_jobs
from resources.lib.sync import verdicts as sync_verdicts

core_selection.notify = lambda *a, **k: None          # the staged toast is not what is tested here


def fixable(offset_ms=-2500.0, scale=1.0, mode='global'):
    return {'status': 'FIXABLE', 'offset_ms': offset_ms, 'scale': scale, 'mode': mode, 'applied': True}


d = sync_common.describe_fix(fixable(-2500.0))
check('badge: a subtitle moved EARLIER by the fix reads as Kodi\'s +delay (+2.5s), mode offset',
      d == {'mode': 'offset', 'label': '+2.5s', 'text': '+2.5 שנ׳'})
check('badge: a subtitle moved LATER reads as a minus (-1.2s)', sync_common.describe_fix(fixable(1200.0))['label'] == '-1.2s')
check('badge: a scaled fix says שינוי קצב, a piecewise one says מקטעים',
      sync_common.describe_fix(fixable(0.0, scale=1.001))['label'] == 'שינוי קצב'
      and sync_common.describe_fix(fixable(0.0, mode='piecewise'))['label'] == 'מקטעים'
      and sync_common.describe_fix(fixable(0.0, scale=1.001))['mode'] == 'scale'
      and sync_common.describe_fix(fixable(0.0, mode='piecewise'))['mode'] == 'piecewise')
check('badge: a zero shift has no label', sync_common.describe_fix(fixable(0.0))['label'] == '')
check('badge: the offset hint (a refined offset) wins over the verdict',
      sync_common.describe_fix(fixable(-2500.0), offset_hint=-1000.0)['label'] == '+1.0s')

# every toast builder says the same words
_toasts = []
sync_jobs.kodi.notify = lambda msg, **k: _toasts.append(msg)
for v in (fixable(-2500.0), fixable(1200.0), fixable(0.0, scale=1.001), fixable(0.0, mode='piecewise'), fixable(0.0)):
    want = sync_common.sync_notice(v)
    _toasts[:] = []
    sync_jobs._announce(v, fresh=True)
    check('toast: _announce / fix_notice / status_line agree (%s)' % want,
          sync_jobs.fix_notice(v) == want and sync_jobs.status_line(v) == want and _toasts == [want])
check('toast: the sentence keeps the old text for a plain shift',
      sync_common.sync_notice(fixable(-2500.0)) == 'הכתובית סונכרנה אוטומטית (+2.5 שנ׳)'
      and sync_common.sync_notice(fixable(0.0)) == 'הכתובית סונכרנה אוטומטית')

TMP5 = tempfile.mkdtemp()
ORIG = os.path.join(TMP5, 'orig.he.srt')
FIXED = os.path.join(TMP5, 'orig.abc.synced.he.srt')
ORIG_TEXT = '1\n00:00:01,000 --> 00:00:02,000\nשלום\n'
open(ORIG, 'w', encoding='utf-8').write(ORIG_TEXT)
open(FIXED, 'w', encoding='utf-8').write(ORIG_TEXT.replace('00:00:01,000', '00:00:03,500'))
SIG = 'cut1:' + 'a' * 32
KEY = sync_verdicts.cache_key(ORIG_TEXT, 'Show.S01E01.1080p', SIG)
sync_verdicts._verdict_path = lambda: os.path.join(TMP5, 'subsync_verdicts.json')


class FakePlayer(object):
    set_calls = []

    def isPlayingVideo(self):
        return True

    def isPlaying(self):
        return True

    def getPlayingFile(self):
        return 'x.mkv'

    def getAvailableSubtitleStreams(self):
        return ['he']

    def setSubtitles(self, path):
        FakePlayer.set_calls.append(path)

    def showSubtitles(self, on):
        pass

    def setSubtitleStream(self, i):
        pass


sys.modules['xbmc'].Player = FakePlayer
sys.modules['xbmc'].getInfoLabel = lambda s: ''


def select(link):
    core_selection.set_current_subtitle(link, renew=True)
    return core_selection.current_subtitle_selection()


def publish_fix(sel, verdict, path=FIXED, original=ORIG, keys=(KEY,)):
    """stage + confirm = what service.process / the tournament / background do for a fix Kodi has registered"""
    badge = sync_common.fix_badge(verdict, original, list(keys))
    ok = core_selection.stage_subtitle_sync_fix(path, selection=sel, source='cache', badge=badge)
    return ok and core_selection.confirm_subtitle_sync_fix(path, selection=sel)


def props():
    return WIN.getProperty('subs.sync.offset'), WIN.getProperty('subs.sync.mode')


sel = select('LINK-A')
check('props: nothing is published before a fix', props() == ('', ''))
check('props: a confirmed fix publishes the offset and the mode', publish_fix(sel, fixable(-2500.0)) and props() == ('+2.5s', 'offset'))
check('props: the undo record holds the original file and the verdict key',
      core_selection.get_sync_undo(sel) == {'v': 1, 'original': ORIG, 'keys': [KEY]})
check('props: the status is "fixed"', core_selection.get_subtitle_sync_status().get('state') == 'fixed')
core_selection.set_subtitle_sync_status('confirmed', source='local')
check('props: a later non-fixed status clears them', props() == ('', '') and core_selection.get_sync_undo(sel) == {})
publish_fix(sel, fixable(0.0, mode='piecewise'))
check('props: a piecewise fix publishes מקטעים / piecewise', props() == ('מקטעים', 'piecewise'))
core_selection.clear_subtitle_sync_status()
check('props: clear_subtitle_sync_status clears them', props() == ('', '') and core_selection.get_sync_undo(sel) == {})
publish_fix(sel, fixable(0.0, scale=1.001))
check('props: a scaled fix publishes שינוי קצב / scale', props() == ('שינוי קצב', 'scale'))
sel2 = select('LINK-B')
check('props: picking another subtitle clears them (and the old selection cannot undo)',
      props() == ('', '') and core_selection.get_sync_undo(sel) == {})
publish_fix(sel2, fixable(-2500.0))
check('props: re-selecting the SAME subtitle keeps them', (core_selection.set_current_subtitle('LINK-B') or True) and props() == ('+2.5s', 'offset'))
check('props: a stale selection cannot publish a badge over the current one',
      core_selection.stage_subtitle_sync_fix(FIXED, selection=sel, source='cache') is False and props() == ('+2.5s', 'offset'))

# ---- 6. B2: "בטל סנכרון אוטומטי" (sync/unsync.py, action=unsync) ---------------------------------------------------------
from resources.lib.sync import delivery_record, unsync
from resources.lib.sync import service as sync_service

# nothing may reach the community pool: spy on the writer and check the module's own source
_pool_calls = []
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    pool_contribute.report_sync = lambda *a, **k: _pool_calls.append(('report_sync', a))
except Exception:
    pass
_said = []
unsync._say = lambda msg: _said.append(msg)

sel = select('LINK-U')
INFO = {'imdb_id': 'tt1', 'title': 'Show', 'season': '1', 'episode': '1'}
delivery_record.record_delivery(INFO, 'Show.S01E01.1080p', KEY, 1.0, -2500.0, cut_signature=SIG, selection=sel)
publish_fix(sel, fixable(-2500.0))
check('unsync: the fix is live before the cancel', props() == ('+2.5s', 'offset')
      and json.loads(WIN.getProperty('subsync.delivered.' + sel['token']))['offset'] == -2500.0)
FakePlayer.set_calls[:] = []
check('unsync: the cancel succeeds', unsync.cancel_auto_sync(announce=True) is True)
check('unsync: the ORIGINAL file went back through setSubtitles (the slot replacement)', FakePlayer.set_calls == [ORIG])
check('unsync: the subs.sync.* properties and the FIXED status are gone',
      props() == ('', '') and core_selection.get_subtitle_sync_status() == {} and core_selection.get_sync_undo(sel) == {})
_rec = json.loads(WIN.getProperty('subsync.delivered.' + sel['token']))
check('unsync: the delivery record is reset to scale 1.0 / offset 0 (same key, same cut)',
      _rec['scale'] == 1.0 and _rec['offset'] == 0.0 and _rec['key'] == KEY and _rec['cut_signature'] == SIG)
check('unsync: a local veto is stored under the sub_hash|cut_signature key',
      sync_verdicts.is_vetoed(KEY) and KEY.endswith('|' + SIG)
      and sync_verdicts.load_verdicts()[KEY]['status'] == sync_verdicts.STATUS_USER_VETO)
check('unsync: the viewer is told (a toast)', _said == [unsync.MSG_DONE])
_src = open(unsync.__file__, encoding='utf-8').read()
check('unsync: nothing went to the community pool (spy) and the module imports no pool / report code (source)',
      not _pool_calls and not any(n in _src for n in ('pool_contribute', 'pool_client', 'report_sync', 'sync_registry')))

# the veto holds: a replay of the same cut is delivered as it is, nothing is queued, a late background verdict is ignored
_queued = []
sync_service.enqueue_deep = lambda *a, **k: _queued.append(a) or True
sync_service.known_cut_signature = lambda info, playing: SIG
sync_service.playing_release = lambda info: 'Show.S01E01.1080p'
sel3 = select('LINK-U')                       # a fresh selection of the same row (a replay)
out, verdict = sync_service.process(INFO, ORIG, '', selection=sel3)
check('veto: a replay delivers the original and reports the veto, no fix, no deep job',
      out == ORIG and verdict.get('status') == sync_verdicts.STATUS_USER_VETO and not _queued)
sync_verdicts.store_verdict(KEY, {'status': 'FIXABLE', 'scale': 1.0, 'offset_ms': -2500.0, 'mode': 'global'})
check('veto: a late background FIXABLE verdict does not undo it', sync_verdicts.is_vetoed(KEY))
sync_verdicts.VERDICT_VERSION += 1
check('veto: an engine version bump does not forget it', sync_verdicts.is_vetoed(KEY))
sync_verdicts.VERDICT_VERSION -= 1
_filler = {'k%d' % i: {'status': 'CONFIRMED', 'ts': 1e9 + i, 'v': sync_verdicts.VERDICT_VERSION} for i in range(450)}
_data = sync_verdicts.load_verdicts()
_data.update(_filler)
json.dump(_data, open(sync_verdicts._verdict_path(), 'w'))
sync_verdicts.store_verdict('one-more', {'status': 'CONFIRMED'})
check('veto: the cache trim keeps vetoes before anything else', sync_verdicts.is_vetoed(KEY)
      and len(sync_verdicts.load_verdicts()) <= 401)
check('veto: only the viewer\'s own confirmed manual delay replaces it',
      sync_verdicts.store_human_verdict({'mode': 'global', 'cache_key': KEY, 'cut_signature': SIG, 'status': 'FIXABLE',
                                         'scale': 1.0, 'offset_ms': -500.0}) and not sync_verdicts.is_vetoed(KEY))

# the cancel refuses when it cannot be exact
sel4 = select('LINK-V')
_said[:] = []
check('unsync: with no automatic sync on screen it says so and changes nothing',
      unsync.cancel_auto_sync() is False and _said == [unsync.MSG_NONE])
publish_fix(sel4, fixable(-2500.0))
open(ORIG, 'w', encoding='utf-8').write(ORIG_TEXT + '\n2\n00:00:05,000 --> 00:00:06,000\nאחר\n')   # a later download reused the name
FakePlayer.set_calls[:] = []
_said[:] = []
check('unsync: when the original was replaced by another download it refuses (no veto, nothing delivered, badge stays)',
      unsync.cancel_auto_sync() is False and _said == [unsync.MSG_FAILED] and not FakePlayer.set_calls
      and props() == ('+2.5s', 'offset') and not sync_verdicts.is_vetoed(KEY))

# wiring: the RunScript action and the chooser button
_plugin = open(os.path.join(ADDON, 'resources', 'lib', 'entry', 'plugin.py'), encoding='utf-8').read()
check("wiring: action=unsync is routed to entry.actions.handle_unsync", "'unsync': handle_unsync" in _plugin
      and 'handle_unsync' in open(os.path.join(ADDON, 'resources', 'lib', 'entry', 'actions.py'), encoding='utf-8').read())
import xml.dom.minidom
_dom = xml.dom.minidom.parse(os.path.join(ADDON, 'resources', 'skins', 'Default', '1080i', 'script.moransubs.chooser.xml'))
_btn = [c for c in _dom.getElementsByTagName('control') if c.getAttribute('id') == '206']
_xml206 = _btn[0].toxml() if _btn else ''
check('wiring: chooser button 206 runs action=unsync and shows only while subs.sync.mode is set',
      len(_btn) == 1 and 'action=unsync' in _xml206 and 'Window(Home).Property(subs.sync.mode)' in _xml206)

print('')
print('FAILED: %d' % len(FAILED) if FAILED else 'ALL CHECKS PASSED')
sys.exit(1 if FAILED else 0)
