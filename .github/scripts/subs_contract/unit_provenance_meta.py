# Unit checks of the provenance PLUMBING (Provenance P1, 20 Nov 2026): the provider row metadata of
# service.subtitles.kodipovilai/resources/lib/fetching/providers/meta.py and its path to the chooser's candidates
# (Ktuvit / Wizdom / OpenSubtitles row -> engine.sort_subtitles tuple -> bridge._engine_rows '_meta').
# The fixtures are trimmed raw captures of the live providers (fixtures/provenance/, probe of 20 Nov 2026).
# Pure Python, no Kodi: the xbmc / requests modules are the contract's own stubs. Run:
#   python -I .github/scripts/subs_contract/unit_provenance_meta.py
# Exit code 0 = every check passed.

import ast
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
ADDON = os.path.join(ROOT, 'service.subtitles.kodipovilai')
FIXD = os.path.join(HERE, 'fixtures', 'provenance')
sys.path.insert(0, HERE)
import _stubmaker  # noqa: E402

PROFILE = tempfile.mkdtemp(prefix='prov_meta_')
_stubmaker.ADDON_PATH = ADDON
_stubmaker.PROFILE = PROFILE
_stubmaker.install()
sys.path.insert(0, ADDON)

FAILED = []


def check(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        FAILED.append(name)


def fx_bytes(name):
    with open(os.path.join(FIXD, name), 'rb') as f:
        return f.read()


def fx_json(name):
    return json.loads(fx_bytes(name).decode('utf-8'))


# ---- 1. the pure module ---------------------------------------------------------------------------------------------
META_PY = os.path.join(ADDON, 'resources', 'lib', 'fetching', 'providers', 'meta.py')
tree = ast.parse(open(META_PY, encoding='utf-8').read())
imports = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Import):
        imports.update(a.name.split('.')[0] for a in n.names)
    elif isinstance(n, ast.ImportFrom):
        imports.add((n.module or '').split('.')[0])
check('meta.py is stdlib only (imports: %s)' % sorted(imports), imports <= {'datetime', 'html', 're'})

from resources.lib.fetching.providers import meta as pm  # noqa: E402

check('iso_date: Ktuvit day/month/year', pm.iso_date('11/05/2025', 'dmy') == '2025-05-11')
check('iso_date: Wizdom month/day/year (a day up to 12 is never guessed)',
      pm.iso_date('1/22/2025', 'mdy') == '2025-01-22' and pm.iso_date('3/4/2026', 'mdy') == '2026-03-04'
      and pm.iso_date('3/4/2026', 'dmy') == '2026-04-03')
check('iso_date: OpenSubtitles ISO with time', pm.iso_date('2026-03-22T12:26:48Z', 'iso') == '2026-03-22')
check('iso_date: not a date -> empty', all(pm.iso_date(v, o) == '' for v, o in (
    ('13/13/2025', 'dmy'), ('31/02/2025', 'dmy'), ('', 'iso'), (None, 'dmy'), ('soon', 'mdy'), ('2025-13-01', 'iso'), (5, 'iso'))))
check('clean drops empties but keeps a 0 counter',
      pm.clean({'a': '', 'b': None, 'c': [], 'd': False, 'e': 0, 'f': 'x', 'g': True}) == {'e': 0, 'f': 'x', 'g': True})
check('cap: one trimmed line, bounded', pm.cap('  a \n  b ', 10) == 'a b' and pm.cap('x' * 50, 5) == 'xxxxx' and pm.cap(None, 5) == '')
check('ktuvit_row never raises on junk', pm.ktuvit_row('') == {} and pm.ktuvit_row(None) == {} and pm.ktuvit_row('<td>') == {})
check('wizdom_index never raises on junk', pm.wizdom_index(None) == {} and pm.wizdom_index({'subs': 5}) == {}
      and pm.wizdom_index([]) == {} and pm.wizdom_index({'subs': [{'id': 'x', 'date': '1/1/2020'}]}) == {})
check('opensubtitles_row never raises on junk', pm.opensubtitles_row(None, None) == {} and pm.opensubtitles_row({}, '') == {})

# ---- 2. Ktuvit: the real module on the raw HTML ---------------------------------------------------------------------
from resources.lib.fetching.providers.sources import ktuvit  # noqa: E402


def legacy_rows(raw):
    """The row extraction as it was before the metadata existed (same regexes, copied), to prove names and ids are unchanged."""
    out = []
    for tr in re.compile('<tr>(.+?)</tr>', re.DOTALL).findall(raw.decode('utf-8')):
        got = re.compile(r'<div style="float.+?>\s*(?:<i.*?</i>\s*)?(.*?)<br />.+?data-subtitle-id="(.+?)"', re.DOTALL).findall(tr)
        if got:
            name = got[0][0].strip().replace('\n', '').replace('\r', '').replace('\t', '').replace(' ', '.')
            out.append((''.join(c for c in name if c not in '\\/:*?"<>|\''), got[0][1]))
    return out


def sub_id_of(row):
    """The Ktuvit subtitle id inside a row's download url."""
    from urllib.parse import parse_qs, urlsplit
    data = json.loads(parse_qs(urlsplit(row['url']).query)['download_data'][0])
    return json.loads(data['subtitle_download_data'])['request']['SubtitleID']


for fname in ('ktuvit_episode_tlou_s02e05.html','ktuvit_movie_irishman_table.html', 'ktuvit_movie_oppenheimer_table.html',
              'ktuvit_series_got_s01e01_first3.html'):
    raw = fx_bytes(fname)
    rows = ktuvit.extract_subtitles_list_and_build_subtitles_list(raw, 'PAGEID')
    old = legacy_rows(raw)
    got = [(r['filename'], sub_id_of(r)) for r in rows]
    check('ktuvit %s: %d rows, release names and subtitle ids identical to the pre-metadata extraction' % (fname, len(rows)),
          got == old and len(rows) > 0)
    check('ktuvit %s: every row has meta with credits, date and downloads' % fname,
          all(r.get('meta', {}).get('credits') and r['meta'].get('date') and 'downloads' in r['meta'] for r in rows))
    check('ktuvit %s: meta is JSON-safe' % fname, json.loads(json.dumps(rows)) == rows)

tl = ktuvit.extract_subtitles_list_and_build_subtitles_list(fx_bytes('ktuvit_episode_tlou_s02e05.html'), 'PAGEID')
check('ktuvit TLOU S02E05 row 1: translator studio + fetch/sync credit + note, uploader, date, downloads',
      tl[0]['meta'] == {'credits': 'תרגום: אולפנים | הביא וסנכרן: avi054 | תואם לגרסאות WEB באורך 00:44:34',
                        'uploader': 'avi054', 'date': '2025-05-11', 'downloads': 1920})
check('ktuvit TLOU S02E05 row 2: a staff uploader and a sync-only second credit',
      tl[1]['meta'] == {'credits': 'תרגום: אולפנים | סנכרון: גרוגו', 'uploader': 'מערכת', 'date': '2025-05-11', 'downloads': 1299})
ir = ktuvit.extract_subtitles_list_and_build_subtitles_list(fx_bytes('ktuvit_movie_irishman_table.html'), 'PAGEID')
check('ktuvit Irishman: 5 rows, only the wrench-icon row is "fixed", and its name is unchanged',
      len(ir) == 5 and [bool(r['meta'].get('fixed')) for r in ir] == [False, False, False, True, False]
      and ir[3]['filename'] == 'The.Irishman.2019.1080p.NF.WEBRip.DDP5.1.Atmos.x264-CM')
check('ktuvit Irishman fixed row meta',
      ir[3]['meta'] == {'credits': 'תרגום: אולפנים | תיקון: אריאל אפרתי', 'uploader': 'greatoz', 'date': '2019-12-05',
                        'downloads': 1657, 'fixed': True})
got3 = ktuvit.extract_subtitles_list_and_build_subtitles_list(fx_bytes('ktuvit_series_got_s01e01_first3.html'), 'PAGEID')
check('ktuvit GoT: a row without an uploader has no uploader key; a team in parentheses is kept as written',
      [r['meta'] for r in got3] == [
          {'credits': 'תרגום: TheInitiative (HDSubs)', 'date': '2018-03-11', 'downloads': 3646},
          {'credits': 'תרגום: TheInitiative (HDSubs)', 'date': '2018-03-10', 'downloads': 1018},
          {'credits': 'תרגום: TheInitiative (HDSubs)', 'date': '2018-03-10', 'downloads': 855}])
op = ktuvit.extract_subtitles_list_and_build_subtitles_list(fx_bytes('ktuvit_movie_oppenheimer_table.html'), 'PAGEID')
check('ktuvit Oppenheimer: a note with a time ("באורך 03:00:22") stays inside credits, not split on its colon',
      op[2]['meta'] == {'credits': 'תרגום: dorrr | סנכרון: Blade Trinity | התרגום אינו מיטבי, מומלץ להמתין | תואם לגרסאות בלוריי באורך 03:00:22',
                        'uploader': 'blade_trinity', 'date': '2023-11-10', 'downloads': 3628})

# ---- 3. Wizdom: the real module with a fake network -----------------------------------------------------------------
from resources.lib.fetching.providers.sources import wizdom  # noqa: E402

CALLS = []
FAIL_RELEASES_FOR = set()


class Resp(object):
    def __init__(self, data, status=200):
        self._d, self.status_code, self.url = data, status, 'fake'

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception('HTTP %d' % self.status_code)

    def json(self):
        return self._d


def fake_get(url, params=None, timeout=None, **kw):
    CALLS.append(url)
    if '/api/search' in url:
        return Resp(fx_json('wizdom_search_severance.json' if (params or {}).get('imdb') == 'tt11280740'
                            else 'wizdom_search_zootopia2.json'))
    imdb = url.rsplit('/', 1)[1]
    if imdb in FAIL_RELEASES_FOR:
        return Resp({}, 500)
    return Resp(fx_json('wizdom_releases_severance.json' if imdb == 'tt11280740' else 'wizdom_releases_zootopia2.json'))


sys.modules['requests'].get = fake_get
movie = {'media_type': 'movie', 'season': '', 'episode': '', 'imdb': 'tt26443597'}
wizdom.get_subs(movie)
zrows = list(wizdom.global_var)
by_id = {int(re.search(r'[?&]id=(\d+)', r['url']).group(1)): r for r in zrows}
check('wizdom movie: 4 rows, every one joined to its meta', len(zrows) == 4 and all('meta' in r for r in zrows))
check('wizdom movie: credits / uploader / date as Wizdom wrote them (date month/day/year -> ISO)',
      by_id[348419]['meta'] == {'credits': 'תרגום: אולפנים | הביא, תיקן וסנכרן: Yoav1610', 'uploader': 'NightRider', 'date': '2026-03-22'}
      and by_id[347167]['meta']['date'] == '2026-01-28')
n_rel = sum(1 for c in CALLS if '/releases/' in c)
wizdom.get_subs(movie)
check('wizdom: the /releases answer is cached for 24 h (one call for two searches of the title)',
      n_rel == 1 and sum(1 for c in CALLS if '/releases/' in c) == 1)
check('wizdom: the visible row is unchanged (label2 / filename / url shape)',
      all(r['label2'].startswith('[Wizdom] ') and r['filename'] and r['url'].startswith('plugin://') for r in zrows))
tv = {'media_type': 'tv', 'season': '01', 'episode': '01', 'imdb': 'tt11280740'}
wizdom.get_subs(tv)
sev = {int(re.search(r'[?&]id=(\d+)', r['url']).group(1)): r for r in wizdom.global_var}
check('wizdom show: the index of a {season: {episode: [rows]}} answer joins too',
      338552 in sev and sev[338552]['meta'] == {'credits': 'תרגום: אולפנים | הביא: F-U-Z | תיקון וסנכרון: Yoav1610',
                                                'uploader': 'NightRider', 'date': '2025-01-22'})
FAIL_RELEASES_FOR.add('tt9999991')
bad = {'media_type': 'movie', 'season': '', 'episode': '', 'imdb': 'tt9999991'}
wizdom.get_subs(bad)
first = sum(1 for c in CALLS if c.endswith('/tt9999991'))
check('wizdom: a failed /releases call gives rows without meta, search rows still come back',
      len(wizdom.global_var) == 4 and not any('meta' in r for r in wizdom.global_var) and first == 1)
wizdom.get_subs(bad)
check('wizdom: a failure is not cached (the next search asks again)', sum(1 for c in CALLS if c.endswith('/tt9999991')) == 2)

# ---- 4. OpenSubtitles: the real module with the search answer faked -------------------------------------------------
from resources.lib.fetching.providers.sources import opensubtitles  # noqa: E402

osdata = fx_json('opensubtitles_search_mixed.json')['data']
check('fixture sanity: the OpenSubtitles fixture really carries from_trusted = true rows',
      sum(1 for it in osdata if it['attributes'].get('from_trusted')) >= 3)
opensubtitles.api_search_subtitles = lambda video_data, override: osdata
opensubtitles.get_subs({'media_type': 'movie', 'imdb': 'tt0111161', 'OriginalTitle': 'x', 'season': '', 'episode': ''})
orows = list(opensubtitles.global_var)
by_up = {it['attributes']['uploader']['name']: r for it, r in zip(osdata, orows)}
check('opensubtitles: one row per result, each with meta', len(orows) == len(osdata) == 7 and all('meta' in r for r in orows))
check('opensubtitles: from_trusted is not recorded (no meta key names it, and no meta value is a bare True)',
      not any('trust' in k.lower() for r in orows for k in r['meta']) and not any(v is True for r in orows for v in r['meta'].values()))
vm = by_up['varod']['meta']
check('opensubtitles: "אולפנים" is recorded (release_raw) before the visible name loses it',
      vm.get('release_raw', '').startswith('* תרגום אולפנים *') and 'אולפנים' not in by_up['varod']['filename']
      and 'The.Shawshank.Redemption' in by_up['varod']['filename'])
check('opensubtitles: uploader, rank, ISO date and downloads of a mirror upload',
      (vm['uploader'], vm['uploader_rank'], vm['date'], vm['downloads']) == ('varod', 'Administrator', '2025-10-09', 524))
check('opensubtitles: the os-auto bulk importer is identifiable by name and rank',
      by_up['os-auto']['meta']['uploader'] == 'os-auto' and by_up['os-auto']['meta']['uploader_rank'] == 'Application Developers'
      and 'release_raw' not in by_up['os-auto']['meta'])
check('opensubtitles: a free-text credit stays in comments, capped',
      'אולפני אלרום' in by_up['Just_Good']['meta']['comments'] and len(by_up['Just_Good']['meta']['comments']) <= pm.MAX_COMMENTS)
check('opensubtitles: an anonymous upload keeps its name and rank, a row with no credit has no credit keys',
      by_up['Anonymous']['meta']['uploader_rank'] == 'anonymous'
      and not {'credits', 'release_raw'} & set(by_up['almog11']['meta']))
check('opensubtitles: visible names are what the old code produced (characters removed, word stripped, trimmed)',
      all(r['filename'] == ''.join(c for c in (it['attributes']['release'] or it['attributes']['files'][0]['file_name'])
                                    if c not in '\\/:*?"<>|\'').replace('תרגום אולפנים', '').replace('אולפנים', '').strip()
          for it, r in zip(osdata, orows)))

# ---- 5. engine tuple and the bridge's candidate ---------------------------------------------------------------------
from resources.lib.fetching.providers import engine  # noqa: E402
from resources.lib.fetching import bridge  # noqa: E402

video = {'file_original_path': 'The.Irishman.2019.1080p.NF.WEBRip.DDP5.1.Atmos.x264-CM.mkv', 'Tagline': ''}
plain = {k: v for k, v in got3[0].items() if k != 'meta'}   # a provider row that has no meta (another release: the bridge drops exact duplicates)
sorted_rows = engine.sort_subtitles(ir + [plain], video, silent=True)
check('sort_subtitles: every tuple has 11 fields, the 11th is the provider meta ({} when the row has none)',
      len(sorted_rows) == 6 and all(len(t) == 11 for t in sorted_rows)
      and sum(1 for t in sorted_rows if t[10] == {}) == 1 and sum(1 for t in sorted_rows if t[10].get('fixed')) == 1)
check('sort_subtitles: the first ten fields and the order rule are untouched (percent, then provider order)',
      all(isinstance(t[5], int) and isinstance(t[4], str) and t[9] == '[Ktuvit]' for t in sorted_rows)
      and [t[5] for t in sorted_rows] == sorted((t[5] for t in sorted_rows), reverse=True))
cands = bridge._engine_rows(sorted_rows, video['file_original_path'], log=False)
# (the fixture holds two rows with the same release name, the Unicode and the ANSI file: the bridge has always kept the first)
check('bridge: a row with meta carries it as _meta, a row without has no _meta key (6 tuples -> 5 candidates after the existing '
      'same-name de-duplication)',
      len(cands) == 5 and sum(1 for c in cands if '_meta' in c) == 4 and sum(1 for c in cands if '_meta' not in c) == 1
      and all(c['_meta'] for c in cands if '_meta' in c))
fixed_c = [c for c in cands if c.get('_meta', {}).get('fixed')]
check('bridge: the fixed row keeps its meta and its release name in the label',
      len(fixed_c) == 1 and 'greatoz' == fixed_c[0]['_meta']['uploader'] and 'x264-CM' in fixed_c[0]['filename'])
check('bridge: candidates stay plain JSON (the 24 h search cache stores them)', json.loads(json.dumps(cands)) == cands)
old_style = bridge._engine_rows([t[:10] for t in sorted_rows], video['file_original_path'], log=False)
check('bridge: a 10-field tuple (an old caller) still works and gives no _meta',
      len(old_style) == 5 and not any('_meta' in c for c in old_style)
      and [{k: v for k, v in c.items() if k != '_meta'} for c in cands] == old_style)

print('\n%d failed' % len(FAILED))
sys.exit(1 if FAILED else 0)
