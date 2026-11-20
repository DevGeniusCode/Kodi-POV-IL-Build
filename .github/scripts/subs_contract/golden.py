"""golden harness of the subtitle add-on (Subtitle Phase 3, step 3g): runs pure / stub-friendly functions on
fixtures and dumps their results. usage: golden.py ADDON_ROOT OUT.json (python -I -B). Used by
check_subs_contract.py, which compares the dump with golden_expected.json. Expected values were recorded on Windows
(os.path is ntpath), so the CI job runs on windows-latest."""
import importlib
import json
import os
import sys
import tempfile

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(sys.argv[1])
OUT = sys.argv[2]
sys.path.insert(0, SP)
import _stubmaker
PROFILE = tempfile.mkdtemp(prefix='gprof_')
_stubmaker.ADDON_PATH = ROOT
_stubmaker.PROFILE = PROFILE
_stubmaker.install()
sys.path.insert(0, ROOT)

# ---- stateful stubs: window properties and settings
import xbmcgui
import xbmcaddon
import xbmc

_PROPS = {}
_SETTINGS = {}


class Window(object):
    def __init__(self, wid=0):
        self.wid = wid

    def getProperty(self, k):
        return _PROPS.get((self.wid, k), '')

    def setProperty(self, k, v):
        _PROPS[(self.wid, k)] = v

    def clearProperty(self, k):
        _PROPS.pop((self.wid, k), None)


xbmcgui.Window = Window


class Addon(object):
    def __init__(self, *a, **k):
        pass

    def getAddonInfo(self, k):
        return {'path': ROOT, 'profile': PROFILE, 'id': 'service.subtitles.kodipovilai',
                'version': '0.0.0', 'name': 'stub'}.get(k, '')

    def getSetting(self, k):
        return _SETTINGS.get(k, '')

    def getSettingBool(self, k):
        return _SETTINGS.get(k, '') == 'true'

    def getSettingInt(self, k):
        try:
            return int(_SETTINGS.get(k, '0'))
        except ValueError:
            return 0

    def getSettingString(self, k):
        return _SETTINGS.get(k, '')

    def setSetting(self, k, v):
        _SETTINGS[k] = v
        return True

    def getLocalizedString(self, i):
        return 'str%d' % i


xbmcaddon.Addon = Addon
xbmc.getInfoLabel = lambda k: ''
xbmc.executeJSONRPC = lambda q: json.dumps({'result': {}})


# renames.json: the golden cases look functions up by their pre-Phase-3 names ('module|old' -> new)
_REN = {}
try:
    _REN = json.load(open(os.path.join(SP, 'renames.json'), encoding='utf-8'))
except Exception:      # noqa
    pass


def get(name, mods):
    for m in mods:
        try:
            mod = importlib.import_module(m)
        except Exception as e:      # noqa
            continue
        for nm in (name, _REN.get(m + '|' + name)):
            if nm and hasattr(mod, nm):
                return getattr(mod, nm)
    raise LookupError('%s not found in %s' % (name, mods))


R = {}


def rec(key, fn, *a, **k):
    try:
        v = fn(*a, **k)
        R[key] = repr(v)
    except Exception as e:      # noqa
        R[key] = 'EXC ' + type(e).__name__ + ': ' + str(e)[:120]


# --------------------------------------------------------------------------- fixtures
SRT_EN = """1
00:00:01,000 --> 00:00:03,500
JOHN: Hello there.
[door slams]

2
00:00:04,000 --> 00:00:06,000
<i>I don't know what you mean.</i>

3
00:00:06,500 --> 00:00:09,000
- Where are you going?
- Out. Don't wait up.

4
00:00:10,000 --> 00:00:12,000
♪ La la la ♪

5
00:00:13,000 --> 00:00:16,000
{\\an8}She said it was fine,
but I'm not so sure.

6
00:00:17,000 --> 00:00:17,500
Hm.

7
00:00:18,000 --> 00:00:21,000
(SIGHS) We should go.

8
00:00:22,000 --> 00:00:24,000
It's late.
"""

SRT_HE = """1
00:00:01,000 --> 00:00:03,500
ג'ון: שלום לך.
[דלת נטרקת]

2
00:00:04,000 --> 00:00:06,000
<i>אני לא יודע למה אתה מתכוון.</i>

3
00:00:06,500 --> 00:00:09,000
- לאן אתה הולך?
- החוצה. אל תחכה לי.

4
00:00:10,000 --> 00:00:12,000
♪ לה לה לה ♪

5
00:00:13,000 --> 00:00:16,000
היא אמרה שזה בסדר,
אבל אני לא כל כך בטוח.

6
00:00:17,000 --> 00:00:17,500
המ.

7
00:00:18,000 --> 00:00:21,000
(נאנח) אנחנו צריכים ללכת.

8
00:00:22,000 --> 00:00:24,000
.מאוחר
"""

SRT_HE_MESSY = """1
00:00:01,000 --> 00:00:03,500
!שלום לך, ג'ון

2
00:00:04,000 --> 00:00:06,000
אני ‎לא‏ יודע (Note: translated) למה אתה מתכוון?.

3
00:00:06,500 --> 00:00:09,000
שָׁלוֹם עולם, hello wörld и привет ابراهيم

4
00:00:10,000 --> 00:00:12,000
אל תחכה לי...


5
00:00:13,000 --> 00:00:16,000
<font color="#fff">היא אמרה</font> {\\an8}שזה בסדר, ♪
"""

LINKS = [
    {'type': 'passthrough', 'path': 'special://temp/x.srt', 'release': 'Movie.2020.1080p.WEB-DL-GRP'},
    {'type': 'pool', 'hash': 'abc123', 'release': 'Rel', 'pool_kind': 'ktuvit', 'source_lang': 'he'},
    {'type': 'engine', 'source': 'ktuvit', 'language': 'Hebrew', 'filename': 'שם.srt', 'id': 5, 'embedded': False},
    {'type': 'engine_ai', 'source': 'opensubtitles', 'language': 'English', 'src_lang': 'en', 'filename': 'a b.srt'},
    {'type': 'embedded_ai', 'src_lang': 'en', 'stream_index': 2},
    {'type': 'embedded_sync', 'lang': 'heb', 'stream_index': 1},
    {'type': 'ai', 'source_lang': 'en', 'local_path': 'C:\\\\t\\\\x.srt', 'force_ai': True, 'proofread_only': False},
]

CODEC = ['resources.lib.translate', 'resources.lib.core.link_codec']
for i, p in enumerate(LINKS):
    enc = get('_encode_link', CODEC)
    dec = get('_decode_link', CODEC)
    rec('link.enc.%d' % i, enc, p)
    rec('link.rt.%d' % i, lambda p=p: dec(enc(p)) == p)
rec('link.dec.bad', get('_decode_link', CODEC), '%7Bnot json')
rec('link.dec.none', get('_decode_link', CODEC), None)

SRT = ['resources.lib.srt', 'resources.lib.core.srt.blocks', 'resources.lib.core.srt.timing',
       'resources.lib.core.srt.classify', 'resources.lib.ai_translation.repairs.postfix_rtl',
       'resources.lib.ai_translation.repairs.postfix_glyphs', 'resources.lib.ai_translation.repairs.postfix_cleanup',
       'resources.lib.ai_translation.repairs.postfix_leaks']

parse_blocks = get('parse_blocks', SRT)
en = parse_blocks(SRT_EN)
he = parse_blocks(SRT_HE)
mes = parse_blocks(SRT_HE_MESSY)
rec('srt.parse.en', parse_blocks, SRT_EN)
rec('srt.parse.he', parse_blocks, SRT_HE)
rec('srt.parse.messy', parse_blocks, SRT_HE_MESSY)
for nm in ('count_entries',):
    rec('srt.' + nm, get(nm, SRT), SRT_EN)
rec('srt.chunk', lambda: list(get('chunk_blocks', SRT)(en, 3)))
rec('srt.block_text_only', get('block_text_only', SRT), en[0])
rec('srt.stitch', get('stitch_blocks', SRT), en)
rec('srt.missing', get('missing_blocks', SRT), en, he[:6])
rec('srt.align', get('align_blocks', SRT), en, he, he[:5])
rec('srt.restore_timings', get('restore_block_timings', SRT), en, he)
rec('srt.clamp', get('clamp_cue_durations', SRT), SRT_EN)
rec('srt.clamp.max', get('clamp_cue_durations', SRT), SRT_EN, 1500)
rec('srt.merge_same_time', get('merge_same_time_cues', SRT), SRT_EN)
for nm in ('looks_hebrew', 'untranslated_line_ratio', 'sdh_content_stats', 'is_sdh_content'):
    for tag, t in (('en', SRT_EN), ('he', SRT_HE), ('messy', SRT_HE_MESSY)):
        rec('srt.%s.%s' % (nm, tag), get(nm, SRT), t)
for nm in ('repair_music_mojibake', 'fix_rtl_punctuation', 'fold_foreign_in_hebrew_word', 'fold_stray_latin_letter',
           'strip_leaked_arabic', 'normalize_glyphs', 'strip_vtt_class_tags', 'strip_hi_annotations',
           'strip_leaked_speaker_prefix', 'strip_niqqud', 'fix_pronoun_stutter', 'mend_line_breaks',
           'strip_source_echo'):
    for tag, t in (('en', SRT_EN), ('he', SRT_HE), ('messy', SRT_HE_MESSY)):
        rec('srt.%s.%s' % (nm, tag), get(nm, SRT), t)
rec('srt.fix_rtl.legacy', get('fix_rtl_punctuation', SRT), SRT_HE_MESSY, mode='strict', legacy_engine=True)
rec('srt.strip_hi.keep', get('strip_hi_annotations', SRT), SRT_EN, keep_speaker_prefixes=True)
rec('srt.strip_he_prefix', get('strip_hebrew_speaker_prefix', SRT), SRT_HE, SRT_EN)
rec('srt.drop_sound_cues', get('drop_untranslated_sound_cues', SRT), SRT_HE, SRT_EN)
for b in he + mes:
    rec('srt.drop_model_notes.%s' % b.get('index', id(b)) if isinstance(b, dict) else 'x', get('drop_model_notes', SRT), b)
rec('srt.reasoning_final', get('reasoning_final_answer', SRT), 'Let me think. Draft: אני בסדר\nFinal: אני בסדר מאוד', 'I am fine')
rec('srt.reasoning_free', get('reasoning_free_draft', SRT), 'Draft 1: שלום\nBetter: שלום עולם', 'Hello world')
rec('srt.may_carry_reasoning', get('may_carry_reasoning', SRT), 'Let me think about this. Draft: x')
rec('srt.leaked_cue', get('leaked_cue_answer', SRT), '3\nשלום', 'Hello')
rec('srt.restyle', get('restyle_like', SRT), '<i>שלום</i>', 'שלום')
rec('srt.aligned_source', get('aligned_source_texts', SRT), en, he)
rec('srt.src_echo_idx', get('generated_source_echo_indices', SRT), en, he)
rec('srt.neg_loss_idx', get('generated_negation_loss_indices', SRT), en, he)
rec('srt.editorial_idx', get('generated_editorial_leak_indices', SRT), en, he)
rec('srt.output_blocks', get('generated_output_blocks', SRT), en, SRT_HE)
rec('srt.output_blocks.fill', get('generated_output_blocks', SRT), en, SRT_HE, fill_missing=True)

# ---- release_match
RM = ['resources.lib.release_match', 'resources.lib.scoring_matching.release']
PAIRS = [
    ('Movie.2020.1080p.WEB-DL.DDP5.1.H264-GRP', 'Movie.2020.1080p.WEB-DL.DDP5.1.H264-GRP'),
    ('Movie.2020.1080p.WEB-DL.DDP5.1.H264-GRP', 'Movie.2020.720p.WEB-DL.x264-GRP'),
    ('Movie.2020.1080p.BluRay.x264-AAA', 'Movie.2020.1080p.WEB-DL.x264-AAA'),
    ('Show.S01E02.1080p.WEB.h264-NTb', 'Show S01E02 WEBRip NTb'),
    ('Show.S01E02.PROPER.1080p.WEB.h264-NTb', 'Show.S01E02.1080p.WEB.h264-NTb'),
    ('Movie.2020.EXTENDED.1080p.BluRay.x264-AAA', 'Movie.2020.1080p.BluRay.x264-AAA'),
    ('', 'Movie.2020.1080p'),
    ('Some.Title.2019.2160p.UHD.BluRay.REMUX.HDR.HEVC-FGT', 'Some Title 2019 2160p BluRay x265 10bit-RARBG'),
    # Step 6: sync-determining attributes drive the number, sync-neutral ones (resolution, codec, audio, container) do not
    ('Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H264-NTb', 'Show.S01E03.1080p.AMZN.WEB-DL.DDP5.1.H264-NTb'),
    ('Show.S01E02.1080p.AMZN.WEB-DL.H264-NTb', 'Show.S01E02.720p.AMZN.WEB-DL.x265-NTb'),
    ('Movie.2020.1080p.AMZN.WEB-DL.H264-ABC', 'Movie.2020.1080p.AMZN.WEB-DL.H264-XYZ'),
    ('Movie.2020.1080p.AMZN.WEB-DL.H264-ABC', 'Movie.2020.1080p.NF.WEB-DL.H264-XYZ'),
    ('Movie.2020.DVDRip.25fps-AAA', 'Movie.2020.DVDRip.23.976fps-AAA'),
    ('Movie.2020.DVD.PAL-AAA', 'Movie.2020.DVD.23.976fps-AAA'),
    ('Movie.2020.1080p.WEB.H264-ABC', 'Movie.2020.720p.WEB.x265-XYZ'),
    ('Movie.2020.1080p.WEB-DL.H264-GRP', 'Movie 2020 720p x265'),
    ('Movie.2020.1080p.AMZN.WEB-DL.DDP5.1.H264-GRP', 'Movie.2020.2160p.AMZN.WEB-DL.DDP5.1.HEVC-GRP.mkv'),
]
for i, (a, b) in enumerate(PAIRS):
    rec('rm.score.%d' % i, get('score', RM), a, b)
    rec('rm.normalize.%d' % i, get('normalize', RM), a)
    rec('rm.parse.%d' % i, get('parse', RM), b)
    rec('rm.best_pct.%d' % i, get('best_pct', RM), a, [b])
    rec('rm.explain.%d' % i, get('explain', RM), a, b)
    rec('rm.similarity.%d' % i, get('similarity', RM), a, b)
    rec('rm.synthetic.%d' % i, get('is_synthetic', RM), a)

# ---- settings / selection state machine (dict-backed Window and addon stubs)
SU = ['resources.lib.sub_utils', 'resources.lib.core.settings', 'resources.lib.core.kodi',
      'resources.lib.core.selection', 'resources.lib.core.video_context']
_SETTINGS.update({'k_str': 'hello', 'k_true': 'true', 'k_false': 'false', 'k_int': '42', 'k_float': '1.5',
                  'embedded_translation_mode': 'translate'})
rec('set.get_setting', get('get_setting', SU), 'k_str', 'd')
rec('set.get_setting.default', get('get_setting', SU), 'nope', 'dflt')
rec('set.get_bool', get('get_bool', SU), 'k_true', False)
rec('set.get_bool.false', get('get_bool', SU), 'k_false', True)
rec('set.get_int', get('get_int', SU), 'k_int', 0)
rec('set.get_float', get('get_float', SU), 'k_float', 0.0)
rec('set.set', get('set_setting', SU), 'k_new', 'v1')
rec('set.get_after_set', get('get_setting', SU), 'k_new', '')
rec('set.embedded_mode', get('embedded_translation_mode', SU))
rec('set.embedded_policy', get('embedded_translation_policy', SU))
rec('kodi.localised', get('localised', SU), 32630)
rec('kodi.safe_release', get('safe_release_filename', SU), 'Movie: 2020/1080p?*.mkv')
rec('kodi.title', lambda: get('DIALOG_TITLE', SU))
rec('vc.conflicts', get('release_conflicts_with_episode', SU), 'Show.S01E02.1080p', 'S01', 'E03')
rec('vc.conflicts2', get('release_conflicts_with_episode', SU), 'Show.2x05.1080p', '2', '5')

LINK1 = 'link-one'
LINK2 = 'link-two'
rec('sel.set1', get('set_current_subtitle', SU), LINK1)
rec('sel.get1', get('get_current_subtitle', SU))
rec('sel.sel1', lambda: sorted(get('current_subtitle_selection', SU)().keys()))
rec('sel.matches', lambda: get('subtitle_selection_matches', SU)(get('current_subtitle_selection', SU)().get('token'),
                                                                 get('current_subtitle_selection', SU)().get('link_hash'),
                                                                 get('current_subtitle_selection', SU)().get('stream_hash')))
rec('sel.matches.bad', get('subtitle_selection_matches', SU), 'zzz', '', '')
rec('sel.status.set', get('set_subtitle_sync_status', SU), 'verified')
rec('sel.status.get', get('get_subtitle_sync_status', SU))
rec('sel.set2', get('set_current_subtitle', SU), LINK2, renew=True)
rec('sel.get2', get('get_current_subtitle', SU))
rec('sel.status.after', get('get_subtitle_sync_status', SU))
rec('sel.clear', get('clear_subtitle_sync_status', SU))
rec('sel.identity', get('get_current_subtitle_identity', SU))
rec('sel.candidate_identity', get('subtitle_candidate_identity', SU), {'link': LINK1, 'filename': 'x', 'language': 'he'})


# ---- sync_align on fixture SRT pairs
SYNC = ['resources.lib.sync_align', 'resources.lib.core.srt.cues', 'resources.lib.sync.align.estimate',
        'resources.lib.sync.align.verify', 'resources.lib.sync.align.piecewise', 'resources.lib.sync.align.regions',
        'resources.lib.sync.align.retime', 'resources.lib.sync.align.common']


def _lcg(seed):
    x = seed
    while True:
        x = (1103515245 * x + 12345) % (2 ** 31)
        yield x


def make_srt(n, seed, shift=0, split_at=None, split_shift=0, scale=1.0, words=True):
    g = _lcg(seed)
    t = 2000
    out = []
    for i in range(n):
        gap = 400 + next(g) % 2600
        dur = 900 + next(g) % 2600
        start = t + gap
        end = start + dur
        t = end
        s = int(start * scale) + shift + (split_shift if split_at is not None and i >= split_at else 0)
        e = int(end * scale) + shift + (split_shift if split_at is not None and i >= split_at else 0)

        def ts(ms):
            ms = max(0, ms)
            return '%02d:%02d:%02d,%03d' % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
        out.append('%d\n%s --> %s\nLine number %d of the test file\nsecond row %d\n' % (i + 1, ts(s), ts(e), i, next(g) % 97))
    return '\n'.join(out)


REF = make_srt(70, 7)
CAND_SAME = make_srt(70, 7)
CAND_SHIFT = make_srt(70, 7, shift=2400)
CAND_NEG = make_srt(70, 7, shift=-1800)
CAND_SCALE = make_srt(70, 7, shift=500, scale=1.0417)
CAND_SPLIT = make_srt(70, 7, shift=300, split_at=35, split_shift=4200)
CAND_OTHER = make_srt(70, 99)
parse_srt = get('parse_srt', SYNC)
rec('sync.parse_srt', parse_srt, REF[:600])
rec('sync.dialogue_cues', get('dialogue_cues', SYNC), REF[:900])
ref_cues = parse_srt(REF)
for nm, cand in (('same', CAND_SAME), ('shift', CAND_SHIFT), ('neg', CAND_NEG), ('scale', CAND_SCALE),
                 ('split', CAND_SPLIT), ('other', CAND_OTHER)):
    cc = parse_srt(cand)
    rec('sync.estimate.' + nm, get('estimate', SYNC), ref_cues, cc)
    rec('sync.verify.' + nm, get('verify', SYNC), REF, cand)
    rec('sync.verify_cues.' + nm, get('verify_cues', SYNC), ref_cues, cand)
    rec('sync.verify_cue_lists.' + nm, get('verify_cue_lists', SYNC), ref_cues, cc)
    rec('sync.verify_and_fix.' + nm, get('verify_and_fix', SYNC), REF, cand)
    rec('sync.coverage.' + nm, get('coverage_profile', SYNC), ref_cues, cc)
    rec('sync.timeline_conflict.' + nm, get('timeline_conflict', SYNC), ref_cues, cand)
rec('sync.retime', get('retime', SYNC), CAND_SHIFT, 1.0, -2400)
rec('sync.retime.scale', get('retime', SYNC), CAND_SCALE, 0.96, -480)
rec('sync.overlap_rate', get('overlap_rate', SYNC), ref_cues, parse_srt(CAND_SHIFT), 1.0, 2400)
rec('sync.pick_oracle', get('pick_oracle', SYNC),
    [{'release': 'Movie.2020.1080p.WEB-DL-GRP', 'text': 'x', 'language': 'en'},
     {'release': 'Movie.2020.720p.BluRay-AAA', 'text': 'y', 'language': 'en'}], 'Movie.2020.1080p.WEB-DL-GRP')
for k in ('STATUS_CONFIRMED', 'STATUS_FIXABLE', 'STATUS_UNKNOWN'):
    rec('sync.const.' + k, lambda k=k: get(k, SYNC))


# ---- translate helpers (found wherever they live: resolved by scanning for the top-level def)
import ast as _ast


def _index():
    idx = {}
    for dp, dn, fn in os.walk(os.path.join(ROOT, 'resources', 'lib')):
        dn[:] = [d for d in dn if d not in ('__pycache__', '_libs', 'pyxbmct')]
        for f in fn:
            if not f.endswith('.py'):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, ROOT)[:-3].replace(os.sep, '.')
            if rel.endswith('.__init__'):
                rel = rel[:-9]
            if rel.startswith(('resources.lib.fetching.providers.', 'resources.lib.subs_engine.')) or rel in ('resources.lib.fetching.providers', 'resources.lib.subs_engine'):
                continue
            try:
                t = _ast.parse(open(p, encoding='utf-8').read())
            except Exception:
                continue
            for st in t.body:
                if isinstance(st, (_ast.FunctionDef, _ast.ClassDef)):
                    idx.setdefault(st.name, []).append(rel)
    for k, new_name in _REN.items():
        m, old_name = k.split('|')
        if m in idx.get(new_name, []):
            idx.setdefault(old_name, []).append(m)
    return idx


_IDX = _index()


def anyfn(name, prefer=()):
    mods = _IDX[name]
    for p in prefer:
        for m in mods:
            if p in m:
                return get(name, [m])
    return get(name, mods[:1])


for tag, v in (('a', 'https://x.example/dl?token=abc'), ('b', '499157df-d49d-4c1b-96f9-920866a2354a'),
               ('c', 'Movie.2020.1080p.WEB-DL-GRP'), ('d', 'a' * 30), ('e', '')):
    rec('tr.looks_like_token.' + tag, anyfn('_looks_like_token'), v)
for i, (a, b) in enumerate(PAIRS):
    rec('tr.match_pct.%d' % i, anyfn('_match_pct'), a, b)
    rec('tr.is_same_source.%d' % i, anyfn('_is_same_source'), a, b)
for i, p in enumerate(('C:/media/Movie.2020.1080p.WEB-DL-GRP.mkv', 'plugin://x?token=abc.mkv', '/a/b.c.d.e', '')):
    rec('tr.release_from_path.%d' % i, anyfn('_release_from_path'), p)
for code in ('en', 'he', 'xx', None):
    rec('tr.lang_display.%s' % code, anyfn('_lang_display'), code)
    rec('tr.lang_display_he.%s' % code, anyfn('_lang_display_he'), code)
for code in ('ar', 'EN', 'es-419', None, ''):
    rec('tr.gender_rank.%s' % code, anyfn('_gender_src_rank'), code)
for i, rel in enumerate(('Movie.2020.SDH.1080p', 'Movie.Hearing.Impaired.2020', 'Highlander.1986', '')):
    rec('tr.is_sdh_ext.%d' % i, anyfn('_is_sdh_ext'), {}, rel)
rec('tr.emb_mode', anyfn('_embedded_translation_mode'))
rec('tr.emb_policy', anyfn('_embedded_translation_policy'))
rec('tr.enabled_sources', anyfn('_enabled_sources'))
for tag, t in (('he', 'שלום עולם זה טקסט בעברית לגמרי ' * 3), ('en', 'hello world this is english ' * 3), ('mix', 'שלום hello world שלום'), ('empty', '')):
    rec('tr.mostly_hebrew.' + tag, anyfn('_is_mostly_hebrew'), t)
    rec('tr.content_hash.' + tag, anyfn('_content_hash'), t)
    rec('tr.has_hebrew.' + tag, anyfn('_has_hebrew'), t)
rec('tr.answered', anyfn('_answered'), en, SRT_HE)
rec('tr.untranslated_chunk.en', anyfn('_is_untranslated_chunk_response'), en, en)
rec('tr.untranslated_chunk.he', anyfn('_is_untranslated_chunk_response'), en, he)
for raw in ('minimal', 'high', '1024', '', 'garbage'):
    for mdl in ('gemini-3.5-flash', 'gemini-2.5-flash-lite', 'gemini-3.8-flash'):
        rec('tr.thinking.%s.%s' % (raw, mdl), anyfn('_thinking_params'), raw, mdl)
rec('tr.dispatch_order', anyfn('_chunk_dispatch_order'), [en[:2], en[2:4], en[4:6], en[6:]], 41000)
rec('tr.dispatch_order.none', anyfn('_chunk_dispatch_order'), [en[:2], en[2:4]], 'bad')
rec('tr.subchunk_ctx', anyfn('_source_context_for_subchunk'), [en[:4], en[4:]], 1, en[4:], 2)
rec('tr.model_label', anyfn('_model_label'), 'gemini-3.5-flash-lite')
rec('tr.model_label.none', anyfn('_model_label'), '')
rec('tr.model_out_key', anyfn('_model_out_key'), 'gemini-3.5-flash', 'KEY')
rec('tr.srt_start_ms', anyfn('_srt_start_ms'), '1\n00:01:02,500 --> 00:01:04,000\nhi')
rec('tr.unique_starts', anyfn('_unique_starts'), ['1\n00:00:01,000 --> 00:00:02,000\na', '2\n00:00:01,000 --> 00:00:02,000\nb', '3\n00:00:05,000 --> 00:00:06,000\nc'])
rec('tr.block_with_text', anyfn('_block_with_text'), '1\n00:00:01,000 --> 00:00:02,000\na', 'שלום')
rec('tr.ms_label', anyfn('_ms_label'), 125000)
rec('tr.preview_clean', anyfn('_preview_clean'), he[:3], SRT_EN)
rec('tr.source_id', anyfn('_source_id_for_ai'), {'local_path': ''})
rec('tr.pool_job_id', anyfn('_pool_job_id'), '/x/pool_abc123.source.srt')
rec('tr.encode_embedded_marker', anyfn('is_external_stream'), 'plugin://x/movie.he.srt')
rec('tr.stream_code', anyfn('_stream_code'), 'heb')
rec('tr.iso639_1', anyfn('_iso639_1'), 'heb')
rec('tr.looks_machine_translated', anyfn('_looks_machine_translated'), 'Movie.HebrewMachineTranslated.srt')
rec('tr.rating_for', anyfn('_rating_for'), 87, 'human')
rec('tr.release_score', anyfn('_release_score'), 'Movie.2020.1080p.WEB-DL.x264-GRP')
rec('tr.encode_engine_link', anyfn('_encode_engine_link'), {'source': 'ktuvit', 'language': 'Hebrew', 'filename': 'a.srt', 'download_data': {'id': 1}, 'url': 'x'}, False)
rec('tr.parse_download_url', anyfn('_parse_download_url'), 'plugin://service.subtitles.kodipovilai/?action=download&link=abc')

exec(compile(open(os.path.join(SP, 'golden_list_part.py'), encoding='utf-8').read(), 'golden_list_part.py', 'exec'))
exec(compile(open(os.path.join(SP, 'golden_pr_part.py'), encoding='utf-8').read(), 'golden_pr_part.py', 'exec'))
exec(compile(open(os.path.join(SP, 'golden_fast_part.py'), encoding='utf-8').read(), 'golden_fast_part.py', 'exec'))
# LAST: it replaces functions of several modules by fakes
exec(compile(open(os.path.join(SP, 'golden_autosub_part.py'), encoding='utf-8').read(), 'golden_autosub_part.py', 'exec'))

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(R, f, ensure_ascii=False, indent=0, sort_keys=True)
print('golden cases:', len(R), 'errors:', sum(1 for v in R.values() if v.startswith('EXC')))
