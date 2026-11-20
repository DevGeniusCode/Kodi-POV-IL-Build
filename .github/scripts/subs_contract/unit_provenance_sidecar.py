# Unit checks of the download-time provenance sidecar (Provenance P4, 20 Nov 2026): the pure file helpers of core/srt/credits.py,
# scoring_matching/provenance.relevant_lines, fetching/provenance_sidecar.py (key, scan, store, prune, attach) and its hooks in
# fetching/bridge.download_inner (a fresh download, a cache hit, a season pack's members), against a fake provider. Run:
#   python -I .github/scripts/subs_contract/unit_provenance_sidecar.py
# Exit code 0 = every check passed.

import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
ADDON = os.path.join(ROOT, 'service.subtitles.kodipovilai')
sys.path.insert(0, HERE)
import _stubmaker  # noqa: E402

PROFILE = tempfile.mkdtemp(prefix='prov_sidecar_')
_stubmaker.ADDON_PATH = ADDON
_stubmaker.PROFILE = PROFILE
_stubmaker.install()
sys.path.insert(0, ADDON)

FAILED = []


def check(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        FAILED.append(name)


from resources.lib.core.link_codec import encode_link  # noqa: E402
from resources.lib.core.srt import credits as cr  # noqa: E402
from resources.lib.fetching import bridge, provider_adapter, provenance_sidecar as sc  # noqa: E402
from resources.lib.scoring_matching import provenance as pv  # noqa: E402
from resources.lib.ui import chooser_rows as rows  # noqa: E402

ALLOW = pv.load_allowlist(os.path.join(ADDON, 'resources', 'config', 'provenance_allowlist.json'))


def srt(cues):
    out = []
    for i, text in enumerate(cues, 1):
        s = 10 + i * 3
        out.append('%d\r\n00:%02d:%02d,000 --> 00:%02d:%02d,500\r\n%s\r\n' % (i, s // 60, s % 60, (s + 2) // 60, (s + 2) % 60, text))
    return '\r\n'.join(out)


def dialogue(n):
    return ['שורת דיבור מספר %d' % i for i in range(n)]


def write(path, cues, enc='cp1255'):
    with open(path, 'wb') as f:
        f.write(srt(cues).encode(enc))
    return path


# ---- 1. the pure file helpers ------------------------------------------------------------------------------------------------------
check('decode_bytes: cp1255 (a Hebrew file), BOM UTF-8, UTF-16, plain UTF-8, empty, undefined cp1255 bytes',
      cr.decode_bytes('שלום עולם'.encode('cp1255')) == 'שלום עולם' and cr.decode_bytes('שלום'.encode('utf-8-sig')) == 'שלום'
      and cr.decode_bytes('שלום'.encode('utf-16')) == 'שלום' and cr.decode_bytes('שלום'.encode('utf-8')) == 'שלום'
      and cr.decode_bytes(b'') == '' and isinstance(cr.decode_bytes(b'\xff\x81\xd9abc'), str))
check('decode_bytes reads at most MAX_FILE_BYTES', len(cr.decode_bytes(b'a' * (cr.MAX_FILE_BYTES + 100))) == cr.MAX_FILE_BYTES)
texts = cr.cue_texts('﻿' + srt(['שלום', 'שורה א\nשורה ב', '', 'סוף']))
check('cue_texts: the text of every cue, lines joined, empty cues and junk blocks skipped',
      texts == ['שלום', 'שורה א שורה ב', 'סוף'] and cr.cue_texts('') == [] and cr.cue_texts('no cues here\n\nat all') == [] and cr.cue_texts(None) == [])
check('the yod spellings תירגום / תירגם are translation verbs, in a metadata label, a cue and every studio form',
      cr.scan_line('תירגום: SnoWhite').roles == {'translation'} and cr.scan_line('תירגם על ידי Afenla').roles == {'translation'}
      and cr.scan_line('SnoWhite :תירגום').roles == {'translation'} and cr.label_roles('תירגום') == {'translation'}
      and cr.label_roles('תירגום וסנכרון') == {'translation', 'sync'} and cr.scan_line('תירגום כתוביות: דנה כהן').studio_forms == ('subtitle_credit',)
      and cr.scan_line('הפקת תירגום וכתוביות אולפני אלרום').studio_forms == ('production',)
      and cr.scan_line('תירגום: אולפנים').studio_forms == ('studio_translation',) and cr.is_credit_word('תירגום') and cr.is_credit_word('ותירגם')
      and cr.parse_credits('תירגום: Afenla | תירגום כתוביות: דנה')[0][1].studio)
check('and the spellings give the same badges as the plain ones',
      pv.classify({'credits': 'תירגום: SnoWhite'}, ALLOW, hebrew=True)['badge'] == 'VERIFIED_TRANSLATOR'
      and pv.classify({'credits': 'תירגום: אולפנים'}, ALLOW, hebrew=True)['badge'] == 'VERIFIED_STUDIO'
      and pv.classify({}, ALLOW, hebrew=True, cue_infos=[cr.scan_line('Afenla :תירגום וסנכרון')])['badge'] == 'VERIFIED_TRANSLATOR'
      and pv.classify({'credits': 'תירגום: SnoWhite'}, ALLOW, hebrew=False)['badge'] == '')

# ---- 2. relevant_lines: credits only, never the dialogue -----------------------------------------------------------------------------------
cues40 = dialogue(40)
cues40[-1] = 'Afenla :תרגום וסנכרון'
cues40[20] = 'תורגם על ידי SnoWhite'                      # the middle of the file: not looked at
cues40[1] = 'SDI Media Group :כתוביות'                    # no role word: kept because the allowlist names it
rl = pv.relevant_lines(cues40, ALLOW)
check('relevant_lines: the first / last 8 cues only; a credit, a studio name; no dialogue, no middle cue',
      rl == ['SDI Media Group :כתוביות', 'Afenla :תרגום וסנכרון'] and not any('דיבור' in x for x in rl))
check('relevant_lines: without an allowlist only credit-like lines; junk in, nothing out',
      pv.relevant_lines(cues40, None) == ['Afenla :תרגום וסנכרון'] and pv.relevant_lines(None, ALLOW) == [] and pv.relevant_lines(5, ALLOW) == [])
check('relevant_lines: a user handle in the user\'s allowlist is kept',
      pv.relevant_lines(['x', 'שלום Friend One'], pv.build_allowlist({}, custom='Friend One')) == ['שלום Friend One'])
check('cue_infos: the stored lines read back with the same roles; junk ignored',
      [i.roles for i in pv.cue_infos(['Afenla :תרגום וסנכרון', 5, '', None])] == [frozenset({'translation', 'sync'})])

# ---- 3. the key -----------------------------------------------------------------------------------------------------------------------
KT = {'type': 'engine', 'source': 'ktuvit', 'language': 'Hebrew', 'filename': 'Show.S01.1080p.WEB',
      'download_data': {'Ktuvit_Page_ID': 'PAGE', 'subtitle_download_data': json.dumps({'request': {'FilmID': 'PAGE', 'SubtitleID': 'ABC123'}})}, 'hi': 'false'}
check('engine_key: Ktuvit SubtitleID, Wizdom id, OpenSubtitles file id, a member tag, a hash for the rest, nothing without a source',
      sc.engine_key(KT) == 'ktuvit:ABC123' and sc.engine_key(KT, 'S01E02') == 'ktuvit:ABC123:S01E02'
      and sc.engine_key({'source': 'Wizdom', 'download_data': {'id': 348419}}) == 'wizdom:348419'
      and sc.engine_key({'source': 'opensubtitles', 'download_data': {'id': '9001'}}) == 'opensubtitles:9001'
      and re.fullmatch(r'yify:n-[0-9a-f]{16}', sc.engine_key({'source': 'yify', 'language': 'Hebrew', 'filename': 'X'}))
      and sc.engine_key({'source': 'yify', 'language': 'Hebrew', 'filename': 'X'}) != sc.engine_key({'source': 'yify', 'language': 'Hebrew', 'filename': 'Y'})
      and sc.engine_key({}) == '' and sc.engine_key(None) == '' and re.fullmatch(r'ktuvit:n-.{16}', sc.engine_key({'source': 'ktuvit', 'download_data': {'subtitle_download_data': 'junk'}})))

# ---- 4. recording and reading --------------------------------------------------------------------------------------------------------------
D = tempfile.mkdtemp(prefix='sidecar_dir_')
f1 = write(os.path.join(D, 'a.srt'), cues40)
check('record: stores the credit lines of a downloaded file under its key, in the folder given',
      sc.record(KT, f1, folder=D, allow=ALLOW) and sc.lookup(KT, folder=D) == ['SDI Media Group :כתוביות', 'Afenla :תרגום וסנכרון']
      and os.path.isfile(os.path.join(D, 'credits.json')) and not [x for x in os.listdir(D) if x.endswith('.tmp')])
raw_json = open(os.path.join(D, 'credits.json'), 'rb').read().decode('utf-8')
check('the sidecar never keeps subtitle text: only the credit lines, a time, the cue count and a content hash',
      'דיבור' not in raw_json and 'SnoWhite' not in raw_json and set(json.loads(raw_json)['e']['ktuvit:ABC123']) == {'t', 'h', 'n', 'l'}
      and json.loads(raw_json)['e']['ktuvit:ABC123']['n'] == 40)
f2 = write(os.path.join(D, 'b.srt'), dialogue(30))
KT2 = dict(KT, download_data={'subtitle_download_data': json.dumps({'request': {'SubtitleID': 'NOCREDITS'}})})
check('a file with no credit line is recorded as scanned (an empty list), a file never scanned is None',
      sc.record(KT2, f2, folder=D, allow=ALLOW) and sc.lookup(KT2, folder=D) == [] and sc.lookup(dict(KT, source='wizdom'), folder=D) is None)
before = json.loads(open(os.path.join(D, 'credits.json'), 'rb').read().decode('utf-8'))['e']['ktuvit:ABC123']
f1b = write(os.path.join(D, 'a2.srt'), dialogue(40))
check('overwrite=False keeps an entry that is already there, the default replaces it',
      not sc.record(KT, f1b, folder=D, allow=ALLOW, overwrite=False) and sc.lookup(KT, folder=D) == before['l']
      and sc.record(KT, f1b, folder=D, allow=ALLOW) and sc.lookup(KT, folder=D) == [])
check('a file that is not a subtitle, a missing file, a missing folder and junk never raise and write nothing',
      not sc.record(KT, os.path.join(D, 'nope.srt'), folder=D, allow=ALLOW) and not sc.record({}, f1, folder=D, allow=ALLOW)
      and not sc.record(KT, f1, folder=os.path.join(D, 'no', 'such'), allow=ALLOW) and sc.lookup({}, folder=D) is None and sc.lookup(None) is None
      and not sc.record(KT, write(os.path.join(D, 'junk.srt'), []), folder=D, allow=ALLOW))
open(os.path.join(D, 'credits.json'), 'wb').write(b'{broken json')
check('a damaged sidecar file is replaced, not fatal', sc.lookup(KT, folder=D) is None and sc.record(KT, f1, folder=D, allow=ALLOW)
      and sc.lookup(KT, folder=D) == ['SDI Media Group :כתוביות', 'Afenla :תרגום וסנכרון'])
sc.MAX_ENTRIES, sc.KEEP_AFTER_PRUNE = 5, 3
P = tempfile.mkdtemp(prefix='sidecar_prune_')
for i in range(8):
    sc.record(dict(KT, download_data={'subtitle_download_data': json.dumps({'request': {'SubtitleID': 'K%d' % i}})}), f1, folder=P, allow=ALLOW)
    ents = json.loads(open(os.path.join(P, 'credits.json'), 'rb').read().decode('utf-8'))['e']
    for k in ents:                     # distinct times: the oldest are the first ones written
        ents[k]['t'] = int(k[8:]) if k.startswith('ktuvit:K') else 0
    open(os.path.join(P, 'credits.json'), 'wb').write(json.dumps({'v': 1, 'e': ents}).encode('utf-8'))
kept = set(json.loads(open(os.path.join(P, 'credits.json'), 'rb').read().decode('utf-8'))['e'])
check('the sidecar is bounded: past MAX_ENTRIES the oldest go first', len(kept) <= 5 and 'ktuvit:K7' in kept and 'ktuvit:K0' not in kept)
sc.MAX_ENTRIES, sc.KEEP_AFTER_PRUNE = 4000, 3600

check('record_many: a pack\'s members are written together, each under its tag',
      sc.record_many(KT, {'S01E01': f1, 'S01E02': f2}, folder=P, allow=ALLOW) == 2 and sc.lookup(KT, 'S01E01', folder=P) == ['SDI Media Group :כתוביות', 'Afenla :תרגום וסנכרון']
      and sc.lookup(KT, 'S01E02', folder=P) == [] and sc.record_many(KT, {}, folder=P) == 0)
check('lookup: a member is tried first, then the file itself',
      sc.lookup(dict(KT), 'S01E09', folder=P) == sc.lookup(KT, folder=P))

# ---- 5. attach: the stored lines on the rows of a later list ---------------------------------------------------------------------------------
def row(payload, lang='he', **extra):
    c = {'filename': 'Ktuvit · 90%  —  Show', 'language': lang, 'link': encode_link(payload)}
    c.update(extra)
    return c


W = tempfile.mkdtemp(prefix='sidecar_attach_')
sc.record(KT, f1, folder=W, allow=ALLOW)
cands = [row(KT), row(KT, lang='en'), row(dict(KT, type='pool')), row(dict(KT, embedded=True)), row(dict(KT, source='wizdom')), {'junk': 1}, 'x']
n = sc.attach(cands, folder=W)
check('attach: only Hebrew provider rows whose file was scanned get `_file_credits`; the others are untouched',
      n == 1 and cands[0]['_file_credits'] == ['SDI Media Group :כתוביות', 'Afenla :תרגום וסנכרון'] and not any('_file_credits' in c for c in cands[1:] if isinstance(c, dict))
      and sc.attach([], folder=W) == 0 and sc.attach(None, folder=W) == 0 and sc.attach(cands, folder=tempfile.mkdtemp()) == 0)
check('attach: a season pack row gets its playing episode\'s credits',
      sc.attach([c2 := row(KT)], playing_tag='S01E01', folder=P) == 1 and c2['_file_credits'][-1] == 'Afenla :תרגום וסנכרון')
built = rows.build(cands[0], 'Show', prov=ALLOW)['props']
check('end to end: a row with credits read from its file shows the badge and "קובץ הכתובית" as the source',
      ('מתרגם מוכר' in built['p_prov'] or 'תרגום אולפנים' in built['p_prov']) and built['p_prov_src'] == 'קובץ הכתובית')

# ---- 6. the hooks in bridge.download_inner (a fake provider, the real cache and sidecar) ---------------------------------------------------------
SUBDIR = tempfile.mkdtemp(prefix='sidecar_dl_')
FAKE = {'files': None, 'calls': 0}


class General(object):
    MySubFolder = SUBDIR


class Provider(object):
    @staticmethod
    def download(download_data, folder):
        FAKE['calls'] += 1
        first = None
        for name, cues in FAKE['files']:
            p = write(os.path.join(folder, name), cues, 'cp1255')
            first = first or p
        return first


bridge._provider_module = lambda source: Provider
provider_adapter.load_general = lambda: General
ITEM = {'info': {}}
bridge._playing_info = lambda: dict(ITEM['info'])


def payload(name, sid, language='Hebrew', source='ktuvit'):
    return {'type': 'engine', 'source': source, 'language': language, 'filename': name, 'hi': 'false',
            'download_data': {'subtitle_download_data': json.dumps({'request': {'SubtitleID': sid}})} if source == 'ktuvit' else {'id': sid}}


def sidecar_entries():
    p = os.path.join(PROFILE, 'provenance', 'credits.json')
    return json.loads(open(p, 'rb').read().decode('utf-8'))['e'] if os.path.isfile(p) else {}


c1 = dialogue(30)
c1[0] = 'הפקת כתוביות אולפני אלרום'
FAKE['files'] = [('Movie.2020.srt', c1)]
pl = payload('Movie.2020.1080p.WEB-DL', 'DL1')
out = bridge.download_inner(pl, for_delivery=False)
check('a fresh download scans the file once: the entry is under the provider and subtitle id, with the credit line',
      out and os.path.isfile(out) and sidecar_entries().get('ktuvit:DL1', {}).get('l') == ['הפקת כתוביות אולפני אלרום'])
check('and the chooser side then reads it (lookup / attach with the real default folder)',
      sc.lookup(pl) == ['הפקת כתוביות אולפני אלרום'] and sc.attach([c := row(pl)]) == 1 and c['_file_credits'] == ['הפקת כתוביות אולפני אלרום'])
calls = FAKE['calls']
out2 = bridge.download_inner(pl, for_delivery=False)
check('a cache hit downloads nothing and does not overwrite the entry', FAKE['calls'] == calls and bridge.LAST_DOWNLOAD_FROM_CACHE
      and sidecar_entries()['ktuvit:DL1']['l'] == ['הפקת כתוביות אולפני אלרום'])
os.remove(os.path.join(PROFILE, 'provenance', 'credits.json'))
bridge.download_inner(pl, for_delivery=False)
check('a cache hit of a file never scanned (stored before the sidecar existed) is scanned once, from disk', FAKE['calls'] == calls
      and sidecar_entries().get('ktuvit:DL1', {}).get('l') == ['הפקת כתוביות אולפני אלרום'])
FAKE['files'] = [('English.srt', c1)]
bridge.download_inner(payload('Movie.2020.English', 'EN1', language='English'), for_delivery=False)
check('only Hebrew downloads are scanned', 'ktuvit:EN1' not in sidecar_entries())
real_record = sc.record


def boom(*a, **k):
    raise RuntimeError('sidecar down')


sc.record = boom
FAKE['files'] = [('Movie.2020.srt', c1)]
out3 = bridge.download_inner(payload('Movie.2020.Other', 'DL2'), for_delivery=False)
sc.record = real_record
check('a failing sidecar never touches a download', out3 and os.path.isfile(out3) and 'ktuvit:DL2' not in sidecar_entries())

# a season pack: every member under its own tag, the file itself under no key
e1, e2, e3 = dialogue(30), dialogue(30), dialogue(30)
e1[-1] = 'תורגם על ידי Tornado'
e2[-1] = 'Qsubs מצוות'
FAKE['files'] = [('Show.S01E01.srt', e1), ('Show.S01E02.srt', e2), ('Show.S01E03.srt', e3)]
ITEM['info'] = {'is_episode': True, 'tvshow': 'Show', 'season': '1', 'episode': '1'}
plp = payload('Show.S01.1080p.WEB.pack', 'PACK1')
outp = bridge.download_inner(plp, for_delivery=False)
ents = sidecar_entries()
check('a season pack: the credits of every member are recorded under its episode tag, none under the pack itself',
      outp and ents.get('ktuvit:PACK1:S01E01', {}).get('l') == ['תורגם על ידי Tornado'] and ents.get('ktuvit:PACK1:S01E02', {}).get('l') == ['Qsubs מצוות']
      and ents.get('ktuvit:PACK1:S01E03', {}).get('l') == [] and 'ktuvit:PACK1' not in ents)
check('and the chooser reads the playing episode\'s member (and the next one when that episode plays)',
      sc.lookup(plp, 'S01E01') == ['תורגם על ידי Tornado'] and sc.lookup(plp, 'S01E02') == ['Qsubs מצוות']
      and pv.classify({}, ALLOW, hebrew=True, cue_infos=pv.cue_infos(sc.lookup(plp, 'S01E02')))['badge'] == 'VERIFIED_TEAM')
ITEM['info'] = {}

# ---- 7. nothing runs during a search -----------------------------------------------------------------------------------------------------------
users = {}
for dp, dn, fn in os.walk(os.path.join(ADDON, 'resources', 'lib')):
    dn[:] = [d for d in dn if d != '__pycache__']
    for f in fn:
        if f.endswith('.py'):
            text = open(os.path.join(dp, f), encoding='utf-8').read()
            for fname in ('record(', 'record_many(', 'attach('):
                if 'provenance_sidecar.' + fname in text:
                    users.setdefault(fname, set()).add(f)
check('the sidecar is written only from the download funnel (bridge.py) and read only by the chooser (chooser.py); search code never touches it',
      users == {'record(': {'bridge.py'}, 'record_many(': {'bridge.py'}, 'attach(': {'chooser.py'}})
check('the sidecar lives in its own profile folder, away from Cached_subs_v2 (which is wiped past 200 files)',
      sc.SUBDIR == 'provenance' and os.path.isfile(os.path.join(PROFILE, 'provenance', 'credits.json'))
      and not os.path.isfile(os.path.join(PROFILE, 'Cached_subs_v2', 'credits.json')))
check('stdlib-only classifier modules stay free of Kodi imports', not re.search(r'^\s*(?:import|from)\s+(?:xbmc|requests)', open(os.path.join(
    ADDON, 'resources', 'lib', 'core', 'srt', 'credits.py'), encoding='utf-8').read() + open(os.path.join(
        ADDON, 'resources', 'lib', 'scoring_matching', 'provenance.py'), encoding='utf-8').read(), re.M))

print('\n%d failed' % len(FAILED))
sys.exit(1 if FAILED else 0)
