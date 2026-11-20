# Unit checks of the provenance UI (Provenance P3, 20 Nov 2026): the badge chip and the metadata properties of
# ui/chooser_rows.build, the strip of script.moransubs.chooser.xml that reads them, the setting custom_trusted_uploaders
# (settings.xml + both strings.po) and the chooser's allowlist loader. Run:
#   python -I .github/scripts/subs_contract/unit_provenance_ui.py
# Exit code 0 = every check passed.

import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
ADDON = os.path.join(ROOT, 'service.subtitles.kodipovilai')
RES = os.path.join(ADDON, 'resources')
sys.path.insert(0, HERE)
import _stubmaker  # noqa: E402

_stubmaker.ADDON_PATH = ADDON
_stubmaker.PROFILE = tempfile.mkdtemp(prefix='prov_ui_')
_stubmaker.install()
sys.path.insert(0, ADDON)

FAILED = []


def check(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        FAILED.append(name)


def read(*parts):
    with open(os.path.join(RES, *parts), 'rb') as f:
        return f.read().decode('utf-8')


from resources.lib.core.link_codec import encode_link  # noqa: E402
from resources.lib.scoring_matching import provenance as pv  # noqa: E402
from resources.lib.ui import chooser_rows as rows  # noqa: E402

ALLOW = pv.load_allowlist(os.path.join(RES, 'config', 'provenance_allowlist.json'))
PLAYING = 'The.Irishman.2019.1080p.NF.WEBRip.DDP5.1.Atmos.x264-CM'
KT = {'credits': 'תרגום: אולפנים | הביא וסנכרן: avi054 | תואם לגרסאות WEB', 'uploader': 'yoav_1610', 'date': '2025-05-11', 'downloads': 1920}


def cand(meta=None, typ='engine', source='ktuvit', lang='he', pct=90, mt=False, current=False, release=PLAYING):
    head = ('[תרגום מכונה] ' if mt else '') + '{0} · {1}%'.format('Ktuvit', pct)
    name = ('» נוכחית · ' if current else '') + '{0}  —  {1}'.format(head, release)
    c = {'filename': name, 'language': lang, 'pct': pct,
         'link': encode_link({'type': typ, 'source': source, 'language': 'Hebrew', 'filename': release, 'download_data': {}, 'hi': 'false'})}
    if meta is not None:
        c['_meta'] = meta
    return c


def props(c, prov=ALLOW):
    return rows.build(c, PLAYING, prov=prov)['props']


EMPTY = {k: '' for k in rows.PROV_KEYS}
check('the provenance property names', rows.PROV_KEYS == ('p_prov', 'p_prov_src', 'p_translator', 'p_sync', 'p_uploader', 'p_date',
                                                           'p_downloads', 'p_fixed', 'has_meta'))

# ---- 1. the properties of a Hebrew provider row -------------------------------------------------------------------------------
p = props(cand(KT))
check('studio row: the badge, its provider, the translator, sync names only, uploader, ISO date, downloads with a thousands separator',
      '[B][COLOR FFBFE9CF]תרגום אולפנים[/COLOR][/B]' == p['p_prov'] and p['p_prov_src'] == 'Ktuvit' and p['p_translator'] == 'אולפנים'
      and p['p_sync'] == 'avi054' and p['p_uploader'] == 'yoav_1610' and p['p_date'] == '2025-05-11' and p['p_downloads'] == '1,920'
      and p['p_fixed'] == '' and p['has_meta'] == '1')
check('the fixed icon becomes the Hebrew word, nothing else changes',
      props(cand(dict(KT, fixed=True)))['p_fixed'] == 'כתובית מתוקנת' and props(cand(dict(KT, fixed=True)))['p_prov'] == p['p_prov'])
check('every row carries every provenance key (the contract needs the XML properties produced by a real row)',
      all(k in props(c) for c in (cand(KT), cand(), cand(typ='pool'), cand(lang='en', meta=KT)) for k in rows.PROV_KEYS))
check('a sync credit is shown as names only, several credits joined, repeats dropped',
      props(cand({'credits': 'תרגום: אלמוני | סנכרון: Afenla | הביא ותיקן: Afenla | תיקון: אריאל אפרתי'}))['p_sync'] == 'Afenla, אריאל אפרתי'
      and len(props(cand({'credits': 'תרגום: x | סנכרון: ' + 'n' * 200}))['p_sync']) <= 80)
check('provider text cannot inject Kodi formatting (BBCode is stripped)',
      props(cand({'credits': 'תרגום: [COLOR red]Bad[/COLOR] | סנכרון: [B]x[/B]', 'uploader': '[CR]up'}))['p_translator'] == 'Bad'
      and props(cand({'credits': 'סנכרון: [B]x[/B]'}))['p_sync'] == 'x' and props(cand({'uploader': '[CR]up'}))['p_uploader'] == 'up')

# ---- 2. positive only: nothing for a row without evidence, nothing negative ever ----------------------------------------------------
q = props(cand({'credits': 'תרגום: dorrr | התרגום אינו מיטבי', 'uploader': 'D N A', 'date': '2025-01-01', 'downloads': 5}), prov=ALLOW)
check('a row with metadata but no evidence: no badge, no source column, the raw fields still shown',
      q['p_prov'] == '' and q['p_prov_src'] == '' and q['p_translator'] == 'dorrr' and q['p_uploader'] == 'D N A' and q['has_meta'] == '1')
check('a row without any metadata: all keys empty, the strip stays hidden', props(cand()) == dict(props(cand()), **EMPTY)
      and props(cand())['has_meta'] == '' and props(cand({}))['has_meta'] == '')
src = read('lib', 'ui', 'chooser_rows.py') + read('skins', 'Default', '1080i', 'script.moransubs.chooser.xml')
check('positive only: the Hebrew word for "unverified" appears nowhere (rows code, XML), and a row without evidence renders none of it',
      'לא מאומת' not in src and 'מאומת' not in ''.join(''.join(props(cand(m)).values()) for m in (
          {'credits': 'תרגום: dorrr'}, {'uploader': 'D N A'}, {'credits': 'תרגום: אלמוני', 'date': '2025-01-01'}, {})))
check('Hebrew provider rows only: other languages, AI, pool, embedded and machine-translated rows get nothing',
      props(cand(KT, lang='en')) == dict(props(cand(KT, lang='en')), **EMPTY) and props(cand(KT, typ='engine_ai', lang='en'))['p_prov'] == ''
      and props(cand(KT, typ='pool'))['p_prov'] == '' and props(cand(KT, typ='embedded_ai'))['has_meta'] == ''
      and props(cand(KT, mt=True))['p_prov'] == '' and props(cand(KT, mt=True))['has_meta'] == '')

# ---- 3. the chip -----------------------------------------------------------------------------------------------------------------------
r = rows.build(cand(KT), PLAYING, prov=ALLOW)['props']
check('the badge is a chip in b1 when nothing precedes it; the other chips follow',
      'תרגום אולפנים' in r['b1'] and r['b2'] != r['b1'])
cur = rows.build(cand(KT, current=True), PLAYING, prov=ALLOW)['props']
check('chip priority: current first, then the badge', 'נוכחית' in cur['b1'] and 'תרגום אולפנים' in cur['b2'])
check('every one of the four badges shows as its own chip',
      all(lab in rows.build(cand(m), PLAYING, prov=ALLOW)['props']['b1'] for lab, m in (
          ('תרגום אולפנים', {'credits': 'תרגום: אולפנים'}), ('צוות תרגום מוביל', {'credits': 'תרגום: Qsubs'}),
          ('מתרגם מוכר', {'credits': 'תרגום: SnoWhite'}), ('מעלה בולט', {'uploader': 'NightRider'}))))
check('a row with no badge has no provenance chip', 'מעלה' not in ''.join(props(cand({'uploader': 'D N A'}))[k] for k in ('b1', 'b2', 'b3')))
check('without an allowlist only the studio forms count',
      'תרגום אולפנים' in props(cand(KT), prov=None)['p_prov'] and props(cand({'uploader': 'NightRider'}), prov=None)['p_prov'] == '')
check('the custom handles of the setting are honoured by the row',
      props(cand({'uploader': 'my_friend'}), prov=pv.build_allowlist({}, custom='My Friend'))['p_prov'].endswith('מעלה בולט[/COLOR][/B]'))
check('a row never fails because of its metadata (junk meta, junk allowlist)',
      all(isinstance(rows.build(cand(m), PLAYING, prov=a)['props'], dict) for m in ({'credits': 5, 'uploader': [1], 'downloads': 'x'}, 'x', [], {'fixed': 1})
          for a in (None, ALLOW, 'junk')))

# ---- 4. the right panel (script.moransubs.chooser.xml) -------------------------------------------------------------------------------
xml_text = read('skins', 'Default', '1080i', 'script.moransubs.chooser.xml')
XROOT = ET.fromstring(xml_text.encode('utf-8'))
xml_props = set(re.findall(r'ListItem\.Property\((\w+)\)', xml_text))
check('the panel reads exactly the provenance properties (every key of the rows, none unknown)',
      set(rows.PROV_KEYS) <= xml_props and {k for k in xml_props if k.startswith('p_') and k not in (
          'p_why', 'p_release', 'p_provider', 'p_kind', 'p_lang', 'p_access')} == set(rows.PROV_KEYS) - {'has_meta'})
check('the quality block shows only when the focused row has metadata and each row only when its value is set',
      'Container(100).ListItem.Property(has_meta)' in xml_text and all(
          '!String.IsEmpty(Container(100).ListItem.Property(%s))' % k in xml_text for k in rows.PROV_KEYS))
check('the Hebrew captions of the quality block are static XML text (one direction per control)',
      all(('<label>%s</label>' % cap) in xml_text for cap in ('[B]איכות ומקור הכתובית[/B]', 'אימות מקור', 'לפי', 'מתרגם', 'סנכרון / תיקון',
                                                              'הועלה ע"י', 'תאריך', 'הורדות', 'תיקון')))
order = [xml_text.index(s) for s in ('[B]שם השחרור[/B]', 'Property(p_release)', '[B]איכות ומקור הכתובית[/B]', 'Property(p_prov)', 'Property(p_fixed)',
                                     'Property(f_episode)', 'Property(f_timing)', '[B]פרטי הכתובית[/B]', 'Property(p_access)')]
check('panel order: release name -> quality and provenance -> the match rows -> subtitle details', order == sorted(order))
check('the removed panel labels are gone ("התאמה לקובץ שלך", its sub-label) and the match rows stay',
      'התאמה לקובץ שלך' not in xml_text and 'ערכים: הקובץ שלך מול הכתובית' not in xml_text
      and all(('<label>%s</label>' % cap) in xml_text for cap in ('פרק', 'מהדורה', 'קצב פריימים', 'סוג מקור', 'קבוצת שחרור', 'שירות סטרימינג', 'תזמון')))
check('header: the "בחירת כתוביות" title and the "found N subtitles" text are gone, the counter and the searching hint stay',
      'בחירת כתוביות' not in xml_text and 'Container(100).CurrentItem' in xml_text and 'Window.Property(subs.count)' in xml_text
      and "'נמצאו {0} כתוביות'.format(len(real))" not in read('lib', 'ui', 'chooser.py')
      and "self.setProperty('subs.count', 'מחפש כתוביות נוספות…' if self.pending else '')" in read('lib', 'ui', 'chooser.py'))
lst = [e for e in XROOT.iter('control') if e.get('id') == '100'][0]
check('the list has its 780 pixels back and no strip is left under it',
      lst.find('height').text == '780' and 'PROVENANCE STRIP' not in xml_text)
gls = [e for e in XROOT.iter('control') if e.get('type') == 'grouplist']
kids = gls[-1].findall('control') if gls else []
heights = [int(k.find('height').text) for k in kids]
top, avail = int(gls[-1].find('top').text), int(gls[-1].find('height').text)
check('right panel: ONE vertical grouplist whose rows are fixed-height groups of the panel width',
      len(gls) == 1 and gls[0].find('orientation').text == 'vertical' and all(k.get('type') == 'group' and k.find('width').text == '560' for k in kids)
      and len(kids) >= 20)
panel_group = [e for e in XROOT.iter('control') if e.get('type') == 'group' and e.find('left') is not None and e.find('left').text == '1320'][0]
panel_h = int(re.search(r'<left>1320</left><top>134</top><width>560</width><height>(\d+)</height>', xml_text).group(1))
check('right panel: the worst case (every row shown, %d of %d pixels) fits inside the panel, which ends at y=962' % (sum(heights), avail),
      sum(heights) <= avail and top + avail <= panel_h
      and int(panel_group.find('top').text) + top + sum(heights) <= 962)
check('the existing controls are untouched (ids 100 201-206, the match number block, the legend)',
      all('id="%d"' % i in xml_text for i in (100, 201, 202, 203, 204, 205, 206)) and 'Property(p_why)' in xml_text and 'Property(tier)' in xml_text
      and 'התאמה גבוהה' in xml_text)
long_name = 'The.Last.of.Us.S02E05.Feel.Her.Love.2160p.UHD.BluRay.HDR10.10bit.AC-3.TrueHD7.1.Atmos.HEVC-d3g.heb'
p_rel = props(cand({}, release=long_name))['p_release']
check('the release name takes 2 lines at most and ends in "..." when it is longer', p_rel.count('[CR]') <= 1 and p_rel.endswith('...')
      and rows.RELEASE_LINES == 2 and props(cand({}))['p_release'].count('[CR]') <= 1)

# ---- 5. the setting ----------------------------------------------------------------------------------------------------------------------
sx = ET.fromstring(read('settings.xml').encode('utf-8'))
cat = [c for c in sx.iter('category') if c.get('id') == 'matching'][0]
st = [s for s in cat.iter('setting') if s.get('id') == 'custom_trusted_uploaders']
check('custom_trusted_uploaders: one string setting in Matching & Display, empty by default, empty allowed, an edit control',
      len(st) == 1 and st[0].get('type') == 'string' and st[0].find('default').text in (None, '')
      and st[0].find('constraints/allowempty').text == 'true' and st[0].find('control').get('type') == 'edit'
      and st[0].get('label') == '32656' and st[0].get('help') == '32657')
for lang in ('en_gb', 'he_il'):
    po = read('language', 'resource.language.%s' % lang, 'strings.po')
    check('strings.po %s: labels 32656 and 32657 exist, none above them is used' % lang,
          'msgctxt "#32656"' in po and 'msgctxt "#32657"' in po and not [i for i in re.findall(r'msgctxt "#(\d+)"', po) if int(i) > 32657])
po_he = read('language', 'resource.language.he_il', 'strings.po')
check('the Hebrew label and help are Hebrew', re.search(r'msgctxt "#32656"\s+msgid "[^"]+"\s+msgstr "[֐-׿ ]+"', po_he) is not None
      and re.search(r'msgctxt "#32657"[\s\S]+?msgstr "[^"]*[֐-׿]{3}', po_he) is not None)
check('the allowlist loader reads the setting by its literal id (the contract then proves it is declared)',
      "get_setting('custom_trusted_uploaders'" in read('lib', 'core', 'provenance_allowlist.py'))

# ---- 6. the allowlist loader (Kodi side) -----------------------------------------------------------------------------------------------
from resources.lib.core import provenance_allowlist as loader  # noqa: E402
from resources.lib.core import settings  # noqa: E402
from resources.lib.ui import chooser  # noqa: E402

VALUE = {'v': ''}
settings.get_setting = lambda key, default='': VALUE['v'] if key == 'custom_trusted_uploaders' else default
loader.reset()
a1 = loader.current()
check('provenance_allowlist.current loads the shipped file (teams, translators, uploaders)',
      isinstance(a1, pv.Allowlist) and 'qsubs' in a1.keys['teams'] and 'snowhite' in a1.keys['translators'] and 'nightrider' in a1.keys['uploaders']
      and not a1.custom)
check('provenance_allowlist.current keeps the loaded list while the setting is unchanged', loader.current() is a1)
VALUE['v'] = 'Some One, another_handle'
a2 = loader.current()
check('provenance_allowlist.current reloads and merges the user handles when the setting changes',
      a2 is not a1 and set(a2.custom) == {'someone', 'anotherhandle'} and 'qsubs' in a2.keys['teams'])
check('a badge appears for a handle the user added (the whole path: setting -> allowlist -> row)',
      rows.build(cand({'uploader': 'Some_One'}), PLAYING, prov=a2)['props']['p_prov'] != '' and
      rows.build(cand({'uploader': 'Some_One'}), PLAYING, prov=a1)['props']['p_prov'] == '')
check('the chooser window asks the shared loader (no private copy is left)',
      'provenance_allowlist.current()' in read('lib', 'ui', 'chooser.py') and '_prov_allowlist' not in read('lib', 'ui', 'chooser.py')
      and hasattr(chooser, 'provenance_allowlist'))
orig_load = pv.load_allowlist


def boom(*a, **k):
    raise RuntimeError('x')


pv.load_allowlist = boom
loader.reset()
check('provenance_allowlist.current never raises (an unreadable file gives None, the rows still build)',
      loader.current() is None and isinstance(rows.build(cand(KT), PLAYING, prov=None)['props'], dict))
pv.load_allowlist = orig_load
loader.reset()

# ---- 6b. credits read from the downloaded file (P4) in a row -----------------------------------------------------------------------------
FILE = ['תורגם על ידי Afenla', 'הפקת כתוביות אולפני אלרום']
pf = props(dict(cand(), _file_credits=['תורגם על ידי Afenla']))
check('a row with no provider metadata but credits read from its file: the badge, "קובץ הכתובית" as the source, has_meta',
      'מתרגם מוכר' in pf['p_prov'] and pf['p_prov_src'] == 'קובץ הכתובית' and pf['has_meta'] == '1'
      and 'מתרגם מוכר' in pf['b1'])
check('file credits can raise the badge above the metadata\'s, and the metadata keeps its own fields',
      'תרגום אולפנים' in props(dict(cand({'credits': 'תרגום: dorrr', 'uploader': 'nobody', 'date': '2025-01-01'}), _file_credits=FILE))['p_prov']
      and props(dict(cand({'credits': 'תרגום: dorrr', 'date': '2025-01-01'}), _file_credits=FILE))['p_translator'] == 'dorrr')
check('when the metadata gives the same badge its provider stays the source',
      props(dict(cand(KT), _file_credits=FILE))['p_prov_src'] == 'Ktuvit')
check('an empty list of file credits (scanned, none found) changes nothing',
      props(dict(cand(KT), _file_credits=[])) == props(cand(KT)) and props(dict(cand(), _file_credits=[])) == props(cand()))
check('file credits never badge a non-Hebrew, machine-translated or AI row, and junk is ignored',
      props(dict(cand(lang='en'), _file_credits=FILE))['p_prov'] == '' and props(dict(cand(mt=True), _file_credits=FILE))['p_prov'] == ''
      and props(dict(cand(typ='engine_ai', lang='en'), _file_credits=FILE))['p_prov'] == ''
      and props(dict(cand(), _file_credits='x'))['p_prov'] == '' and props(dict(cand(), _file_credits=[5, None]))['p_prov'] == '')

# ---- 7. the whole chain on a real provider row ------------------------------------------------------------------------------------------------
fx = os.path.join(HERE, 'fixtures', 'provenance')
ktuvit_html = open(os.path.join(fx, 'ktuvit_movie_irishman_table.html'), 'rb').read()
from resources.lib.fetching.providers.sources import ktuvit  # noqa: E402
from resources.lib.fetching.providers import engine  # noqa: E402
from resources.lib.fetching import bridge  # noqa: E402
prow = ktuvit.extract_subtitles_list_and_build_subtitles_list(ktuvit_html, 'PAGE')
cands = bridge._engine_rows(engine.sort_subtitles(prow, {'file_original_path': PLAYING + '.mkv', 'Tagline': ''}, silent=True), PLAYING, log=False)
built = [rows.build(c, PLAYING, prov=ALLOW)['props'] for c in cands]
check('end to end (Ktuvit HTML -> provider row -> engine tuple -> candidate -> chooser row): every Hebrew row has a studio badge and its metadata',
      len(built) == 4 and all(b['p_prov'] and 'תרגום אולפנים' in b['b1'] + b['b2'] + b['b3'] and b['p_translator'] == 'אולפנים' and b['p_date']
                              and b['p_downloads'] for b in built))
check('end to end: the fixed row shows the Hebrew word', [b['p_fixed'] for b in built].count('כתובית מתוקנת') == 1)

print('\n%d failed' % len(FAILED))
sys.exit(1 if FAILED else 0)
