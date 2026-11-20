# Unit checks of the provenance CLASSIFIER (Provenance P2, 20 Nov 2026): core/srt/credits.py (normaliser + credit
# grammar), scoring_matching/provenance.py (the four positive badges) and the draft config/provenance_allowlist.json.
# Data: the trimmed live captures of fixtures/provenance/ (P1) and the 250 most frequent credit lines of the 2019-20
# Telegram corpus (telegram_credit_lines.json, with the badge recorded after review).
# Pure Python, no Kodi and no stubs: both modules are stdlib only. Run:
#   python -I .github/scripts/subs_contract/unit_provenance.py
# Exit code 0 = every check passed.

import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
ADDON = os.path.join(ROOT, 'service.subtitles.kodipovilai')
FIXD = os.path.join(HERE, 'fixtures', 'provenance')
ALLOW_JSON = os.path.join(ADDON, 'resources', 'config', 'provenance_allowlist.json')
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


# ---- 0. both modules are stdlib only -----------------------------------------------------------------------------------
def imports_of(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            found.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            found.add(('.' * n.level) + (n.module or '') + ':' + ','.join(a.name for a in n.names))
    return found


LIB = os.path.join(ADDON, 'resources', 'lib')
cred_imports = imports_of(os.path.join(LIB, 'core', 'srt', 'credits.py'))
prov_imports = imports_of(os.path.join(LIB, 'scoring_matching', 'provenance.py'))
check('credits.py imports only re and unicodedata (imports: %s)' % sorted(cred_imports), cred_imports <= {'re', 'unicodedata'})
check('provenance.py imports only json and core.srt.credits (imports: %s)' % sorted(prov_imports),
      prov_imports <= {'json', 'resources.lib.core.srt:credits'})

from resources.lib.core.srt import credits as cr  # noqa: E402
from resources.lib.core.srt import cues  # noqa: E402
from resources.lib.fetching.providers import meta as pm  # noqa: E402
from resources.lib.scoring_matching import provenance as pv  # noqa: E402

# ---- 1. normalising -----------------------------------------------------------------------------------------------------
check('normalize strips bidi controls, BOM and markup, unifies quotes, drops stray ?',
      cr.normalize('‫Afenla :תרגום‬‏') == 'Afenla :תרגום'
      and cr.normalize('<i>תורגם ע״י</i> ﻿X') == 'תורגם ע"י X' and cr.normalize('?עברית:? ?גלעד קשר?') == 'עברית: גלעד קשר'
      and cr.normalize(None) == '' and cr.normalize('  a \n b ') == 'a b')
check('normalize keeps a question mark between letters', cr.normalize('a?b') == 'a?b')
check('name_key: case, spaces, dots, underscores, hyphens do not matter; Hebrew quotes do not either',
      cr.name_key('yoav_1610') == cr.name_key('Yoav1610') == 'yoav1610' and cr.name_key('The Duke') == 'theduke'
      and cr.name_key('DownRev.subs') == cr.name_key('downrev subs') and cr.name_key('NightRider') == cr.name_key('Nightrider')
      and cr.name_key('טרנס טייטלס בע"מ') == cr.name_key("טרנס טייטלס בע״מ"))
check('tokens and has_sequence match a name whole, never a substring',
      cr.has_sequence(cr.tokens('תורגם על ידי Ofer Mev'), cr.tokens('Ofer Mev'))
      and not cr.has_sequence(cr.tokens('Ofer Mevi'), cr.tokens('Ofer Mev')) and not cr.has_sequence(cr.tokens('Yoavx'), cr.tokens('Yoav'))
      and not cr.has_sequence((), ('a',)) and not cr.has_sequence(('a',), ()))
check('is_credit_word: the final-letter spellings are recognised (ם ן)',
      all(cr.is_credit_word(w) for w in ('תורגם', 'תרגום', 'סונכרן', 'סנכרון', 'סינכרון', 'תיקון', 'הובא', 'וסנכרן')) and not cr.is_credit_word('צוות'))
check('the grammar knows every Hebrew word of the older cues._CREDIT_RE filter (it may know more; that filter is untouched)',
      all(cr.is_credit_word(w) and cues._CREDIT_RE.search(w) for w in ('תורגם', 'תרגום', 'סונכרן', 'סנכרון', 'כתוביות', 'הובא')))

# ---- 2. metadata credits ---------------------------------------------------------------------------------------------------
check('label_roles: translation, sync family, mixed labels, studio labels',
      cr.label_roles('תרגום') == {'translation'} and cr.label_roles('הביא, תיקן וסנכרן') == {'fetch', 'fix', 'sync'}
      and cr.label_roles('תרגום וסנכרון') == {'translation', 'sync'} and cr.label_roles('סנכרון ותיקון') == {'sync', 'fix'}
      and cr.label_roles('תרגום כתוביות') == {'translation'} and cr.label_roles('עברית') == {'translation'}
      and cr.label_roles('סנכרון לגרסה זו') == {'sync'})
check('label_roles: a note is not a credit label (compat remark, time, empty)',
      cr.label_roles('תואם לגרסאות בלוריי באורך 03') is None and cr.label_roles('') is None and cr.label_roles('פורמט') is None
      and cr.label_roles('כתוביות') is None)
creds, notes = cr.parse_credits('תרגום: אולפנים | הביא, תיקן וסנכרן: Yoav1610 | תואם לגרסאות בלוריי באורך 03:00:22 | פורמט ANSI')
check('parse_credits: two credits and two notes; a time inside a note is not split on its colon',
      [c.label for c in creds] == ['תרגום', 'הביא, תיקן וסנכרן'] and [c.names for c in creds] == [('אולפנים',), ('Yoav1610',)]
      and notes == ['תואם לגרסאות בלוריי באורך 03:00:22', 'פורמט ANSI'] and creds[0].value == 'אולפנים')
check('parse_credits: junk in, empty out', cr.parse_credits(None) == ([], []) and cr.parse_credits('') == ([], []) and cr.parse_credits(5) == ([], []))
check('split_names: , & / + and a standalone ו split; a parenthesis gives the whole, the outer and the inner name',
      cr.split_names('a, b & c') == ['a', 'b', 'c'] and cr.split_names('TheInitiative (HDSubs)') == ['TheInitiative (HDSubs)', 'TheInitiative', 'HDSubs']
      and cr.split_names('') == [] and cr.split_names('Mad_Clown') == ['Mad_Clown'])
by_ear = cr.parse_credits('תורגם משמיעה: X')[0]
check('parse_credits: a by-ear label is flagged', len(by_ear) == 1 and by_ear[0].by_ear)

# ---- 3. free text: a cue, a comment, either text order ---------------------------------------------------------------------
check('scan_line: the visual and the logical order of one credit give the same roles',
      cr.scan_line('Afenla :תרגום וסנכרון').roles == cr.scan_line('תרגום וסנכרון: Afenla').roles == {'translation', 'sync'}
      and cr.scan_line('Ofer Mev תורגם על ידי').roles == cr.scan_line('תורגם על ידי Ofer Mev').roles == {'translation'})
check('scan_line: the studio forms (production, subtitle credit, Hebrew credit, source tag, studio translation, qualifier)',
      cr.scan_line(':הפקת תרגום וכתוביות אולפני נ.ל.ס').studio_forms == ('production',)
      and cr.scan_line('תרגום והפקת כתוביות אולפני אלרום').studio_forms == ('production',)
      and cr.scan_line('תרגום כתוביות: שלומית בכר').studio_forms == ('subtitle_credit',)
      and cr.scan_line('עברית: יעל אדמי').studio_forms == ('hebrew_credit',)
      and cr.scan_line('Title: Furie Year: # Duration: #:#:# Subtitles Source: Netflix').studio_forms == ('source_tag',)
      and cr.scan_line('* תרגום אולפנים * The.Shawshank').studio_forms == ('studio_translation',)
      and 'qualifier' in cr.scan_line('תרגום: רינת זוהר-מנחם (נטפליקס) / מתוקן').studio_forms)
check('scan_line: dialogue and notices are not credits (title card, "התרגום מתחיל", the word צוות, a long line, a brand)',
      not cr.scan_line('- סדרה מקורית של NETFLIX -').credit_like and not cr.scan_line('- NETFLIX מציגה -').credit_like
      and not cr.scan_line('("התרגום מתחיל אחרי "בפרקים הקודמים)').credit_like and not cr.scan_line('.זו לא קבוצת משחק, זה צוות שטח').credit_like
      and not cr.scan_line('Disney Yes HOT').credit_like and cr.scan_line('תורגם ' + 'א' * 200) is None and cr.scan_line('') is None)
check('scan_line: a sync-only line has no translation role (UniHebSubs "הוכנו ... וסונכרנו", a fetch line)',
      cr.scan_line('הכתוביות הובאו, נערכו וסונכרנו ע"י UniHebSubs').roles == {'fetch', 'fix', 'sync'}
      and cr.scan_line('הובא וסונכרן ע"י קופיקו הבלש').roles == {'fetch', 'sync'})
check('scan_line: a by-ear line, with the logical and the visual spelling',
      cr.scan_line('תורגם וסונכרן משמיעה ע"י Qsubs מצוות WorkBook').by_ear and cr.scan_line('תרגום משמיעה: X').by_ear
      and not cr.scan_line('סונכרן משמיעה').by_ear)
check('scan_line: English role words of an upload comment', cr.scan_line('Resynched By X').roles == {'sync'}
      and cr.scan_line('translated by X').roles == {'translation'})
texts = ['cue %d' % i for i in range(40)]
check('edge_texts: the first and the last 8 of a long file, a short file whole',
      cr.edge_texts(texts) == texts[:8] + texts[-8:] and cr.edge_texts(texts[:16]) == texts[:16] and cr.edge_texts(None) == [])
infos = cr.scan_cues(['x'] * 12 + ['תרגום: SnoWhite'] + ['x'] * 12 + ['y'] * 5 + ['תורגם על ידי Tornado'])
check('scan_cues: only the edge cues are read (a credit in the middle of the file is not looked at)',
      len(infos) == 16 and any('Tornado' in i.norm for i in infos) and not any('SnoWhite' in i.norm for i in infos))

# ---- 4. the allowlist --------------------------------------------------------------------------------------------------------
A = pv.build_allowlist({
    'studios': ['אולפני אלרום', {'name': 'SDI Media Group', 'aliases': ['SDI'], 'evidence': 'x'}],
    'teams': [{'name': 'Qsubs', 'aliases': ['Qsubs.net']}],
    'translators': ['SnoWhite', 'TB', {'name': 'Proposed One', 'status': 'proposed'}, 5, None, {'aliases': ['x']}],
    'uploaders': ['NightRider', {'name': 'os-auto'}],
    'never': ['Anonymous', 'os-auto', 5],
}, custom='Some One, , Dixie;ab\nab')
check('build_allowlist: names, aliases (display = the entry name), proposed / short / bad entries skipped, never, custom',
      'snowhite' in A.keys['translators'] and 'tb' not in A.keys['translators'] and 'proposedone' not in A.keys['translators']
      and A.keys['studios']['sdi'] == 'SDI Media Group' and A.keys['teams']['qsubsnet'] == 'Qsubs'
      and A.never == {'anonymous', 'osauto'} and set(A.custom) == {'someone', 'dixie'}
      and ('translators', 'TB') in A.skipped and ('translators', 'Proposed One') in A.skipped)
check('build_allowlist and load_allowlist never raise on junk',
      isinstance(pv.build_allowlist(None), pv.Allowlist) and isinstance(pv.build_allowlist({'studios': 'x', 'teams': [[]]}), pv.Allowlist)
      and pv.load_allowlist(os.path.join(HERE, 'no_such_file.json')).keys['teams'] == {})
SHIPPED = pv.load_allowlist(ALLOW_JSON)
raw = json.load(open(ALLOW_JSON, encoding='utf-8'))
check('the shipped allowlist loads, every category is filled, and every entry says where it comes from',
      all(SHIPPED.keys[c] for c in ('studios', 'teams', 'translators', 'uploaders', 'syncers')) and SHIPPED.never
      and all(isinstance(e, dict) and e.get('evidence') for c in ('studios', 'teams', 'translators', 'uploaders', 'syncers') for e in raw[c]))
check('the shipped allowlist holds the owner-approved names (20 Nov 2026)',
      all(cr.name_key(n) in SHIPPED.keys['teams'] for n in ('Qsubs', 'UniHebSubs', 'Torec', 'DownRev.subs', 'Extreme'))
      and all(cr.name_key(n) in SHIPPED.keys['translators'] for n in ('SnoWhite', 'Afenla', 'Tornado', 'יואב שילוח'))
      and all(cr.name_key(n) in SHIPPED.keys['uploaders'] for n in ('NightRider', 'minime', 'dixie', 'The Duke', 'Yoav1610', 'הנהלה', 'איש צוות', 'מערכת'))
      and all(cr.name_key(n) in SHIPPED.keys['studios'] for n in ('אולפני אלרום', 'טרנס טייטלס', 'אולפני נ.ל.ס', 'SDI Media Group', 'אולפנים'))
      and all(cr.name_key(n) in SHIPPED.never for n in ('os-auto', 'אלמוני', 'אנונימי')))
check('the shipped allowlist keeps suggestions out until the owner removes "proposed" (avi054, varod, Just_Good, PetaBit)',
      not {'avi054', 'varod', 'justgood'} & set(SHIPPED.keys['uploaders']) and 'petabit' not in SHIPPED.keys['teams']
      and sum(1 for c in ('teams', 'translators', 'uploaders') for e in raw[c] if e.get('status') == 'proposed') == len(
          [1 for c, n in SHIPPED.skipped if n]))
check('no shipped name is shorter than the minimum, and no name is both a team and a translator',
      all(len(k) >= pv.MIN_KEY for c in SHIPPED.keys.values() for k in c) and not set(SHIPPED.keys['teams']) & set(SHIPPED.keys['translators']))


# ---- 5. classify: the rules -----------------------------------------------------------------------------------------------
def cls(meta, allow=SHIPPED, hebrew=True, provider='Ktuvit', cues_=None):
    return pv.classify(meta, allow, hebrew=hebrew, provider=provider, cue_infos=cues_)


def badge(meta, **kw):
    return cls(meta, **kw)['badge']


check('the four badges and their Hebrew labels', pv.BADGES == ('VERIFIED_STUDIO', 'VERIFIED_TEAM', 'VERIFIED_TRANSLATOR', 'TRUSTED_UPLOADER')
      and [pv.BADGE_LABELS[b] for b in pv.BADGES] == ['תרגום אולפנים', 'צוות תרגום מוביל', 'מתרגם מוכר', 'מעלה בולט'])
check('Hebrew only: a non-Hebrew row, no meta and junk all give an empty verdict',
      badge({'credits': 'תרגום: אולפנים'}, hebrew=False) == '' and cls(None)['badge'] == '' and cls('x')['reasons'] == []
      and cls({}) == dict(pv.empty_verdict(), provider='Ktuvit') and cls({'credits': 5, 'uploader': 7, 'downloads': 'x'})['badge'] == '')
check('translator field "אולפנים" -> studio; the reason names the provider, the field and the raw credit',
      cls({'credits': 'תרגום: אולפנים'})['reasons'] == [{'badge': 'VERIFIED_STUDIO', 'source': 'Ktuvit', 'field': 'credits',
                                                       'text': 'תרגום: אולפנים', 'match': 'אולפנים'}])
check('a studio company or a studio label in the translator role -> studio',
      badge({'credits': 'תרגום: אולפני אלרום'}) == 'VERIFIED_STUDIO' and badge({'credits': 'תרגום כתוביות: שלומית בכר'}) == 'VERIFIED_STUDIO'
      and badge({'credits': 'עברית: יעל אדמי'}) == 'VERIFIED_STUDIO' and badge({'credits': 'הפקת כתוביות: ויז\'נטקסט'}) == 'VERIFIED_STUDIO')
check('a known team in the translator role -> team; so is a team account as uploader',
      badge({'credits': 'תרגום: TheInitiative (HDSubs)'}) == 'VERIFIED_TEAM' and badge({'credits': 'תרגום: Qsubs'}) == 'VERIFIED_TEAM'
      and badge({'credits': 'תרגום: אלמוני', 'uploader': 'unihebsubs'}) == 'VERIFIED_TEAM')
check('an allowlisted translator -> translator, also among several names; case and separators do not matter',
      badge({'credits': 'תרגום: sno_white'}) == 'VERIFIED_TRANSLATOR' and badge({'credits': 'תרגום: Somebody, Afenla'}) == 'VERIFIED_TRANSLATOR'
      and badge({'credits': 'תרגום וסנכרון: Afenla'}) == 'VERIFIED_TRANSLATOR' and badge({'credits': 'תרגום: יואב שילוח'}) == 'VERIFIED_TRANSLATOR')
check('a translated-by-ear credit -> translator, with the flag', cls({'credits': 'תורגם משמיעה: Somebody'})['badge'] == 'VERIFIED_TRANSLATOR'
      and cls({'credits': 'תורגם משמיעה: Somebody'})['by_ear'] and not cls({'credits': 'תרגום: Somebody'})['by_ear'])
check('an allowlisted uploader (or staff, ranks and case aside) -> trusted uploader; an unknown one -> nothing',
      badge({'uploader': 'NightRider'}) == 'TRUSTED_UPLOADER' and badge({'uploader': 'yoav_1610'}) == 'TRUSTED_UPLOADER'
      and badge({'uploader': 'איש צוות'}) == 'TRUSTED_UPLOADER' and badge({'uploader': 'D N A'}) == '' and badge({'uploader': 'avi054'}) == '')
check('precedence: studio > team > translator > uploader, and every evidence is listed',
      badge({'credits': 'תרגום: אולפנים', 'uploader': 'NightRider'}) == 'VERIFIED_STUDIO'
      and [r['badge'] for r in cls({'credits': 'תרגום: אולפנים', 'uploader': 'NightRider'})['reasons']] == ['VERIFIED_STUDIO', 'TRUSTED_UPLOADER']
      and badge({'credits': 'תרגום: Afenla', 'uploader': 'unihebsubs'}) == 'VERIFIED_TEAM'
      and badge({'credits': 'תרגום: Afenla', 'uploader': 'NightRider'}) == 'VERIFIED_TRANSLATOR')
check('sync / fetch / fix credits NEVER give a badge, even with an allowlisted name; they are returned apart for the panel',
      badge({'credits': 'סנכרון: Afenla'}) == '' and badge({'credits': 'הביא, תיקן וסנכרן: SnoWhite'}) == ''
      and badge({'credits': 'תיקון: אריאל אפרתי | הביא: Qsubs'}) == ''
      and cls({'credits': 'תרגום: אלמוני | סנכרון: SnoWhite | הביא ותיקן: OfficialSubsIL'})['sync'] == ['סנכרון: SnoWhite', 'הביא ותיקן: OfficialSubsIL']
      and cls({'credits': 'תרגום: אלמוני | סנכרון: אריאל אפרתי'})['known_syncers'] == ['אריאל אפרתי'])
check('the placeholders in "never" are not names ("תרגום: אלמוני", an Anonymous uploader)',
      badge({'credits': 'תרגום: אלמוני'}) == '' and badge({'credits': 'תרגום: אנונימי'}) == '' and badge({'uploader': 'Anonymous'}) == '')
check('an unknown translator, a note and a fixed icon give no badge, and nothing negative is said',
      cls({'credits': 'תרגום: dorrr | התרגום אינו מיטבי, מומלץ להמתין', 'fixed': True})['badge'] == ''
      and cls({'credits': 'תרגום: dorrr | התרגום אינו מיטבי, מומלץ להמתין', 'fixed': True})['fixed'] is True
      and cls({'credits': 'תרגום: dorrr | התרגום אינו מיטבי, מומלץ להמתין'})['notes'] == ['התרגום אינו מיטבי, מומלץ להמתין'])
check('the panel fields: translator (raw), uploader, date, downloads, provider',
      (lambda v: (v['translator'], v['uploader'], v['date'], v['downloads'], v['provider']))(
          cls({'credits': 'תרגום: TheInitiative (HDSubs)', 'uploader': 'x', 'date': '2018-03-11', 'downloads': 3646}))
      == ('TheInitiative (HDSubs)', 'x', '2018-03-11', 3646, 'Ktuvit'))
# OpenSubtitles: the importer, from_trusted, comments and the release tag
check('os-auto and the "Application Developers" rank are never a trusted uploader (not even when the user adds the name)',
      badge({'uploader': 'os-auto', 'uploader_rank': 'Application Developers'}) == ''
      and badge({'uploader': 'NightRider', 'uploader_rank': 'Application Developers'}) == ''
      and badge({'uploader': 'os-auto'}, allow=pv.build_allowlist({'uploaders': ['os-auto'], 'never': ['os-auto']})) == ''
      and badge({'uploader': 'os-auto'}, allow=pv.build_allowlist({}, custom='os-auto')) == '')
check('from_trusted is not a signal (a meta that carried it would still give nothing)',
      badge({'uploader': 'Anonymous', 'from_trusted': True, 'trusted': True}, provider='OpenSubtitles') == '')
check('release_raw with the studio word -> studio; without it nothing',
      badge({'release_raw': '* תרגום אולפנים * The.Movie.2020'}, provider='OpenSubtitles') == 'VERIFIED_STUDIO'
      and badge({'release_raw': 'אולפנים The.Movie.2020'}, provider='OpenSubtitles') == 'VERIFIED_STUDIO'
      and badge({'release_raw': 'The.Movie.2020.אולפניםX'}, provider='OpenSubtitles') == '')
check('comments: "(אולפנים)" / "(נטפליקס)" after a translator, a studio form; a sync-only comment is a sync credit, not a badge',
      badge({'comments': 'תרגום: יסמין דור (אולפנים) / *מתוקן*'}, provider='OpenSubtitles') == 'VERIFIED_STUDIO'
      and badge({'comments': 'תרגום: רינת זוהר-מנחם (נטפליקס) / מתוקן'}, provider='OpenSubtitles') == 'VERIFIED_STUDIO'
      and badge({'comments': 'תוקן וסונכרן לגרסה זו**'}, provider='OpenSubtitles') == ''
      and cls({'comments': 'Resynched By IHCGamerTube'}, provider='OpenSubtitles')['sync'] == ['Resynched By IHCGamerTube'])
check('comments: a translator or a team in a translation line counts, in a sync line it does not',
      badge({'comments': 'תורגם על ידי SnoWhite'}, provider='OpenSubtitles') == 'VERIFIED_TRANSLATOR'
      and badge({'comments': 'סונכרן על ידי SnoWhite'}, provider='OpenSubtitles') == ''
      and badge({'comments': 'סונכרן ע"י Qsubs מצוות ZIPC'}, provider='OpenSubtitles') == '')
# the user's own handles
U = pv.build_allowlist({}, custom='Friend One, some_handle')
check('custom_trusted_uploaders: a handle is a trusted uploader and, as a translator, a known translator',
      badge({'uploader': 'friend_one'}, allow=U) == 'TRUSTED_UPLOADER' and badge({'credits': 'תרגום: Some Handle'}, allow=U) == 'VERIFIED_TRANSLATOR'
      and badge({'comments': 'תורגם על ידי Friend One'}, allow=U, provider='OpenSubtitles') == 'VERIFIED_TRANSLATOR'
      and badge({'credits': 'סנכרון: Friend One'}, allow=U) == '')
check('without any allowlist only the studio forms count (a missing file never breaks a row)',
      badge({'credits': 'תרגום: אולפנים', 'uploader': 'NightRider'}, allow=None) == 'VERIFIED_STUDIO'
      and badge({'credits': 'תרגום: SnoWhite', 'uploader': 'NightRider'}, allow=None) == '')
# the downloaded file's credit cues (P4 will feed these; the classifier is ready now)
C = lambda *lines: [cr.scan_line(x) for x in lines]   # noqa: E731
check('file credit cues: both text orders and bidi marks give the same badge',
      badge({}, cues_=C('Afenla :תרגום וסנכרון')) == badge({}, cues_=C('תרגום וסנכרון: Afenla')) == badge({}, cues_=C('‫Afenla :תרגום‬‏')) == 'VERIFIED_TRANSLATOR'
      and badge({}, cues_=C('Ofer Mev תורגם על ידי')) == 'VERIFIED_TRANSLATOR' and badge({}, cues_=C(':הפקת כתוביות אולפני אלרום')) == 'VERIFIED_STUDIO'
      and badge({}, cues_=C('Title: X Year: 2019 Subtitles Source: Netflix')) == 'VERIFIED_STUDIO'
      and cls({}, cues_=C('תרגום: SnoWhite'))['reasons'][0]['source'] == 'file')
check('file credit cues: a sync-only team line and a plug give nothing; a team attribution without a verb gives the team',
      badge({}, cues_=C('סונכרן על-ידי Qsubs מצוות ZIPC')) == '' and badge({}, cues_=C('WwW . Qsubs . net')) == ''
      and badge({}, cues_=C('הכתוביות הובאו, נערכו וסונכרנו ע"י UniHebSubs')) == ''
      and badge({}, cues_=C('Qsubs מצוות')) == 'VERIFIED_TEAM' and badge({}, cues_=C('תורגם וסונכרן על-ידי Qsubs מצוות dvodvo123')) == 'VERIFIED_TEAM'
      and badge({}, cues_=C('SDI Media Group :כתוביות')) == 'VERIFIED_STUDIO')
check('file credit cues: a by-ear line is translator evidence and sets the flag',
      cls({}, cues_=C('תורגם משמיעה ע"י Somebody'))['by_ear'] and badge({}, cues_=C('תורגם משמיעה ע"י Somebody')) == 'VERIFIED_TRANSLATOR')
check('file credit cues: dialogue never counts (a name in a long line, a team word in a sentence)',
      badge({}, cues_=C('SnoWhite ' + 'דיבור ' * 40)) == '' and badge({}, cues_=C('.זו לא קבוצת משחק, זה צוות שטח')) == '')

# ---- 6. the fixture rows (trimmed live captures of the three providers) ------------------------------------------------------
row_re = re.compile(r'<tr>(.+?)</tr>', re.S)


def ktuvit_metas(fname):
    return [pm.ktuvit_row(tr) for tr in row_re.findall(fx_bytes(fname).decode('utf-8')) if 'data-subtitle-id' in tr]


tl = [cls(m) for m in ktuvit_metas('ktuvit_episode_tlou_s02e05.html')]
check('Ktuvit TLOU S02E05: "אולפנים" on both rows; the second uploader is Ktuvit staff (reason listed, not the badge)',
      [v['badge'] for v in tl] == ['VERIFIED_STUDIO'] * 2 and [r['badge'] for r in tl[1]['reasons']] == ['VERIFIED_STUDIO', 'TRUSTED_UPLOADER']
      and tl[0]['sync'] == ['הביא וסנכרן: avi054'] and tl[1]['uploader'] == 'מערכת')
ir = [cls(m) for m in ktuvit_metas('ktuvit_movie_irishman_table.html')]
check('Ktuvit The Irishman: five studio rows; the UniHebSubs account is also listed as the team; the fixed icon is carried',
      [v['badge'] for v in ir] == ['VERIFIED_STUDIO'] * 5 and [any(r['badge'] == 'VERIFIED_TEAM' for r in v['reasons']) for v in ir] == [False, True, True, False, False]
      and [v['fixed'] for v in ir] == [False, False, False, True, False] and ir[1]['sync'] == ['סנכרון: UniHebSubs'])
op = [cls(m) for m in ktuvit_metas('ktuvit_movie_oppenheimer_table.html')]
check('Ktuvit Oppenheimer: two studio rows, and the two "dorrr" rows get no badge (an unknown translator is not a negative)',
      [v['badge'] for v in op] == ['VERIFIED_STUDIO', 'VERIFIED_STUDIO', '', ''] and op[2]['translator'] == 'dorrr' and op[2]['reasons'] == [])
got = [cls(m) for m in ktuvit_metas('ktuvit_series_got_s01e01_first3.html')]
check('Ktuvit Game of Thrones: "TheInitiative (HDSubs)" is one reason, the team, and the raw translator text is kept',
      [v['badge'] for v in got] == ['VERIFIED_TEAM'] * 3 and all(len(v['reasons']) == 1 and v['reasons'][0]['match'] == 'HDSubs' for v in got)
      and got[0]['translator'] == 'TheInitiative (HDSubs)')


def wizdom_verdicts(tag):
    idx = pm.wizdom_index(fx_json('wizdom_releases_%s.json' % tag))
    return [(s['versioname'], cls(pm.wizdom_row(idx, s['id']), provider='Wizdom')) for s in fx_json('wizdom_search_%s.json' % tag)]


zv = wizdom_verdicts('zootopia2')
check('Wizdom Zootopia 2: four studio rows, NightRider listed as the uploader, Yoav1610 only a sync credit',
      [v['badge'] for _, v in zv] == ['VERIFIED_STUDIO'] * 4 and all(v['uploader'] == 'NightRider' and v['sync'] == ['הביא, תיקן וסנכרן: Yoav1610'] for _, v in zv)
      and all(any(r['badge'] == 'TRUSTED_UPLOADER' for r in v['reasons']) for _, v in zv))
sv = dict(wizdom_verdicts('severance'))
ntb = [v for k, v in sv.items() if k.endswith('NTb')][0]
rep = [v for k, v in sv.items() if 'REPACK' in k][0]
check('Wizdom Severance: a row with no translator but an allowlisted uploader is a trusted uploader; a row with no credits gets nothing',
      ntb['badge'] == 'TRUSTED_UPLOADER' and ntb['translator'] == '' and rep['badge'] == '' and rep['reasons'] == []
      and sorted(v['badge'] for v in sv.values()) == ['', 'TRUSTED_UPLOADER', 'VERIFIED_STUDIO', 'VERIFIED_STUDIO'])
os_rows = {it['attributes']['uploader']['name']: cls(pm.opensubtitles_row(it['attributes'], it['attributes'].get('release') or ''), provider='OpenSubtitles')
           for it in fx_json('opensubtitles_search_mixed.json')['data']}
check('OpenSubtitles fixture: the mirror account, the trusted member and the studio comment are studio; the importer, an anonymous '
      'from_trusted upload, an unknown uploader and a resync comment give nothing',
      {k: v['badge'] for k, v in os_rows.items()} == {'varod': 'VERIFIED_STUDIO', 'os-auto': '', 'Just_Good': 'VERIFIED_STUDIO',
                                                       'explosiveskull': 'VERIFIED_STUDIO', 'Anonymous': '', 'almog11': '', 'qtamir1': ''}
      and os_rows['qtamir1']['sync'] == ['Resynched By IHCGamerTube'])
check('every verdict is plain JSON (it can sit in a window property)',
      all(json.loads(json.dumps(v)) == v for v in tl + ir + op + got + [x for _, x in zv] + list(os_rows.values())))

# ---- 7. the Telegram corpus: the 250 most frequent credit lines of files known to be human -------------------------------------
tg = fx_json('telegram_credit_lines.json')['lines']
rec = []
for e in tg:
    info = cr.scan_line(e['line'])
    rec.append(cls({}, provider='file', cues_=[info] if info else [])['badge'])
check('Telegram lines: %d lines, every recorded badge reproduced' % len(tg), len(tg) == 250 and rec == [e['badge'] for e in tg])
check('Telegram lines: a line the corpus marks sync / fetch only NEVER gets a badge (%d lines)' % sum(1 for e in tg if e['corpus_guess'] == 'sync_fetch_only'),
      all(not e['badge'] for e in tg if e['corpus_guess'] == 'sync_fetch_only'))
check('Telegram lines: the studio lines get the studio badge, except the language-editing credit "עריכה לשונית" (deliberately not counted)',
      all(e['badge'] == 'VERIFIED_STUDIO' or e['line'].lstrip(':. ').startswith('עריכה לשונית')
          for e in tg if e['corpus_guess'] == 'studio_or_official'))
check('Telegram lines: no machine-translation or sync-only line is a team / translator badge by accident (a badge needs a translation role, a team marker or a studio form)',
      all((not e['badge']) or (cr.scan_line(e['line']).roles & {'translation'} or cr.scan_line(e['line']).studio_forms
                                or cr.scan_line(e['line']).team_marker or e['badge'] == 'VERIFIED_STUDIO')
          for e in tg))

print('\n%d failed' % len(FAILED))
sys.exit(1 if FAILED else 0)
