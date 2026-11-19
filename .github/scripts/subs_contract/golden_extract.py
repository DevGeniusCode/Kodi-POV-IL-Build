"""golden_extract.py ADDON_ROOT OUT.json (Subtitle Phase 4, step 4e): run the embedded-subtitle extractor (old module or the extract/ package) on synthetic
Matroska files and dump what it returns, so the split can be compared with the original on real parsing code."""
import importlib
import json
import os
import struct
import sys
import tempfile

root = os.path.abspath(sys.argv[1])
out = sys.argv[2]
FIX = os.environ.get('SUBS_CONTRACT_DIR') or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, FIX)
import _stubmaker
prof = tempfile.mkdtemp(prefix='smoke_')
_stubmaker.ADDON_PATH = root
_stubmaker.PROFILE = prof
_stubmaker.install()
sys.path.insert(0, root)


def vint(n):
    for ln in range(1, 9):
        if n < (1 << (7 * ln)) - 1:
            return (n | (1 << (7 * ln))).to_bytes(ln, 'big')
    raise ValueError(n)


def el(eid, payload):
    return eid + vint(len(payload)) + payload


def uint(n):
    b = n.to_bytes(max(1, (n.bit_length() + 7) // 8), 'big')
    return b


def track(num, lang, name, codec=b'S_TEXT/UTF8', forced=0):
    c = el(b'\xD7', uint(num)) + el(b'\x83', uint(17)) + el(b'\x86', codec) + el(b'\x22\xB5\x9C', lang.encode()) \
        + el(b'\x53\x6E', name.encode())
    if forced:
        c += el(b'\x55\xAA', uint(1))
    return el(b'\xAE', c)


def sblock(tracknum, rel_ms, text):
    payload = vint(tracknum) + struct.pack('>h', rel_ms) + b'\x80' + text.encode('utf-8')
    return el(b'\xA3', payload)


def mkv(tracks, blocks, cluster_ts=0, with_cues=True):
    ebml = el(b'\x1A\x45\xDF\xA3', el(b'\x42\x82', b'matroska') + el(b'\x42\x87', uint(4)) + el(b'\x42\x85', uint(2)))
    info = el(b'\x15\x49\xA9\x66', el(b'\x2A\xD7\xB1', uint(1000000)))
    tr = el(b'\x16\x54\xAE\x6B', b''.join(tracks))
    cl = el(b'\x1F\x43\xB6\x75', el(b'\xE7', uint(cluster_ts)) + b''.join(blocks))
    seg_inner_pre = info + tr
    cues = b''
    if with_cues:
        # cluster position is relative to the segment data start
        pos = len(seg_inner_pre)
        # two passes: the Cues sit after the cluster
        cp = el(b'\xBB', el(b'\xB3', uint(cluster_ts)) + el(b'\xB7', el(b'\xF7', uint(1)) + el(b'\xF1', uint(pos))))
        cues = el(b'\x1C\x53\xBB\x6B', cp)
    seg_payload = seg_inner_pre + cl + cues
    seg = b'\x18\x53\x80\x67' + vint(len(seg_payload)) + seg_payload
    return ebml + seg


work = tempfile.mkdtemp(prefix='smoke_media_')
files = {}
texts = ['Hello there.', 'I do not know what you mean.', "Where are you going?\nOut.", 'We should go.']
blocks = [sblock(1, i * 2500, t) for i, t in enumerate(texts)]
files['one_track.mkv'] = mkv([track(1, 'eng', 'English')], blocks)
files['two_tracks.mkv'] = mkv([track(1, 'eng', 'English SDH'), track(2, 'fre', 'Francais', forced=1)],
                              blocks + [sblock(2, 100, 'Bonjour')])
files['no_cues.mkv'] = mkv([track(1, 'heb', 'Hebrew')], blocks, with_cues=False)
files['ass.mkv'] = mkv([track(1, 'eng', 'ASS', codec=b'S_TEXT/ASS')],
                       [sblock(1, 0, '1,0,Default,,0,0,0,,Dialogue line one'), sblock(1, 2500, '2,0,Default,,0,0,0,,Line two')])
for k, v in files.items():
    with open(os.path.join(work, k), 'wb') as f:
        f.write(v)

R = {}
try:
    pkg = importlib.import_module('resources.lib.fetching.embedded.extract.extract')
    tr = importlib.import_module('resources.lib.fetching.embedded.extract.tracks')
    cr = importlib.import_module('resources.lib.fetching.embedded.extract.cue_reference')
    pass
except ImportError:
    pkg = tr = cr = importlib.import_module('resources.lib.fetching.embedded.embedded_extract')
    pass


def rec(key, fn, *a, **k):
    try:
        R[key] = repr(fn(*a, **k))
    except Exception as e:      # noqa
        R[key] = 'EXC %s: %s' % (type(e).__name__, str(e)[:100])


for nm in sorted(files):
    p = os.path.join(work, nm)
    rec('probe.' + nm, tr.probe_tracks, p)
    for lg in ('en', 'fr', 'he'):
        logl = []
        rec('extract_%s.%s' % (lg, nm), pkg.extract_srt, p, None, lg, log=logl.append)
        R['extractlog_%s.%s' % (lg, nm)] = repr(logl)
    rec('extract_t1.' + nm, pkg.extract_srt, p, 1)
    rec('extract_t2.' + nm, pkg.extract_srt, p, 2)
    st = {}
    rec('extract_stats.' + nm, pkg.extract_srt, p, None, 'en', stats=st)
    R['stats.' + nm] = repr(sorted((k, v) for k, v in st.items() if not callable(v)))
    rec('cue_times.' + nm, cr.cue_reference_times, p)
    rec('cue_profile.' + nm, cr.cue_reference_profile, p)
    rec('cue_multi.' + nm, cr.cue_reference_times_multi, p, ['en', 'fr'])
    rec('cut_sig.' + nm, cr.media_cut_signature, p)
for code in ('en', 'eng', 'he', 'heb', 'iw', 'fr', 'fre', 'fra', 'zh', 'chi', 'pt-BR', 'x', '', None):
    rec('lang_key.%s' % code, tr.lang_key, code)
json.dump(R, open(out, 'w', encoding='utf-8'), indent=1, sort_keys=True, ensure_ascii=False)
print('cases', len(R), 'errors', sum(1 for v in R.values() if v.startswith('EXC')))

# ---- the gender reference (Subtitle Phase 4, step 4e): the pure readers on sample lines, wherever the code keeps them
def _gr(name):
    for m in ('resources.lib.ai_translation.context.gender.roles', 'resources.lib.ai_translation.context.gender.chain',
              'resources.lib.ai_translation.context.gender.plan', 'resources.lib.ai_translation.context.gender.align',
              'resources.lib.ai_translation.context.gender_reference'):
        try:
            mod = importlib.import_module(m)
        except ImportError:
            continue
        if hasattr(mod, name):
            return getattr(mod, name)
    raise LookupError(name)


AR = ['أنتِ جميلة جدا', 'أنتَ قوي', 'أنا متعبة اليوم', 'أنا متعب جدا', 'اذهبي إلى البيت', 'اذهب إلى البيت',
      'هل أنتِ بخير؟', 'يا سارة، تعالي هنا', 'مرحبا', 'ما رأيك أنت؟']
HE = ['את יפה מאוד', 'אתה חזק', 'אני עייפה היום', 'אני עייף מאוד', 'לכי הביתה', 'לך הביתה', 'מה שלומך?', 'שלום']
ES = ['Estoy cansada hoy', 'Estoy cansado hoy', 'Eres muy guapa', 'Hola']
RU = ['Я устала сегодня', 'Я устал сегодня', 'Ты красивая', 'Привет']
for lang, lines in (('ar', AR), ('he', HE), ('es', ES), ('ru', RU), ('xx', AR[:2])):
    for i, ln in enumerate(lines):
        rec('addressee.%s.%d' % (lang, i), _gr('reference_addressee_gender'), ln, lang)
        rec('speaker.%s.%d' % (lang, i), _gr('reference_speaker_gender'), ln, lang)
for i, ln in enumerate(HE):
    rec('addresses_female.%d' % i, _gr('addresses_female'), ln)
    rec('addresses_male.%d' % i, _gr('addresses_male'), ln)
blocks = ['1\n00:00:01,000 --> 00:00:02,000\nאתה יפה מאוד', '2\n00:00:03,000 --> 00:00:04,000\nאני עייפה',
          '3\n00:00:05,000 --> 00:00:06,000\nלך הביתה', '4\n00:00:07,000 --> 00:00:08,000\nשלום']
refm = {1: 'أنتِ جميلة جدا', 2: 'أنا متعب', 3: 'اذهبي إلى البيت', 4: 'مرحبا'}
rec('wrong_entries', _gr('wrong_gender_entries'), blocks, refm, 'ar', both_directions=True)
rec('wrong_entries_F', _gr('wrong_gender_entries'), blocks, refm, 'ar')
rec('person_conflicts', _gr('person_gender_conflicts'), blocks, refm, 'ar')
rec('person_conflicts_he', _gr('person_gender_conflicts'), blocks, {1: 'את יפה', 2: 'אני עייף', 3: 'לכי', 4: 'שלום'}, 'he')
for c in ({'language': 'Hebrew'}, {'language': 'Arabic'}, {'language': 'ar'}, {'language': 'es', 'source': 'x'}, {}):
    rec('chain_lang_of.%s' % sorted(c.items()), _gr('chain_lang_of'), c)
rec('hpg_loaded', lambda: _gr('gender_reference_hpg')() is not None)
for off in (0, 400, -250):
    sa = '\n\n'.join('%d\n00:00:%02d,%03d --> 00:00:%02d,%03d\nline %d' % (i + 1, i * 3 % 60, (off % 1000 + 1000) % 1000, i * 3 % 60 + 2, 0, i)
                     for i in range(12))
    rec('align_one.%d' % off, _gr('align_one'), sa, sa, 'en') if False else None
json.dump(R, open(out, 'w', encoding='utf-8'), indent=1, sort_keys=True, ensure_ascii=False)
print('cases (with the gender readers)', len(R), 'errors', sum(1 for v in R.values() if v.startswith('EXC')))


# ---- the RTL renderers (Subtitle Phase 4, step 4f(1)): the three wrappers of delivery/rtl.py on a matrix of files
def _rtl_mod():
    return importlib.import_module('resources.lib.delivery.rtl')


RTL_SAMPLES = {}
RTL_SAMPLES['plain'] = ('1\n00:00:01,000 --> 00:00:03,000\n.שלום עולם\n\n2\n00:00:04,000 --> 00:00:06,000\n!מה קרה?\n\n'
                        '3\n00:00:07,000 --> 00:00:09,000\nHello - שלום (test)\n')
RTL_SAMPLES['clean'] = '1\n00:00:01,000 --> 00:00:03,000\nשלום עולם.\n\n2\n00:00:04,000 --> 00:00:06,000\nמה קרה?\n'
RTL_SAMPLES['arabic_leak'] = ('1\n00:00:01,000 --> 00:00:03,000\nהוא אמר "אינשאללה" (إن شاء الله) והלך\n\n'
                              '2\n00:00:04,000 --> 00:00:06,000\nשלום مرحبا עולם\n')
RTL_SAMPLES['echo'] = ('1\n00:00:01,000 --> 00:00:03,000\nWhere are you going?\nלאן אתה הולך?\n\n'
                       '2\n00:00:04,000 --> 00:00:06,000\nI am here.\nאני כאן.\n')
RTL_SAMPLES['niqqud'] = '1\n00:00:01,000 --> 00:00:03,000\nשָׁלוֹם עוֹלָם\n\n2\n00:00:04,000 --> 00:00:06,000\nבהTחשב מהר\n'
RTL_SAMPLES['glyphs'] = '1\n00:00:01,000 --> 00:00:03,000\nשׁלום תּדיעה\n\n2\n00:00:04,000 --> 00:00:06,000\nטוב.\n'
RTL_SAMPLES['stuck_cue'] = '1\n00:00:01,000 --> 00:41:22,000\nשורה אחת ארוכה מאוד\n\n2\n00:42:00,000 --> 00:42:03,000\nשורה שנייה\n'
RTL_SAMPLES['note'] = '1\n00:00:01,000 --> 00:00:03,000\nשלום (Note: translated) עולם\n\n2\n00:00:04,000 --> 00:00:06,000\n[Wait, let us fix this -> זה]\n'
RTL_SAMPLES['bom'] = '﻿' + RTL_SAMPLES['plain']
RTL_SAMPLES['empty'] = ''
RTL_SAMPLES['ass_line'] = 'Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,.שלום\n'
rtl_dir = tempfile.mkdtemp(prefix='rtl_')
rtl = _rtl_mod()


def _out(base, out_p, p):
    if out_p == p:
        return 'same'
    return 'copy:' + (open(out_p, 'rb').read().decode('utf-8') if os.path.isfile(out_p) else 'MISSING')


for sname, stext in sorted(RTL_SAMPLES.items()):
    for ext in ('.srt', '.vtt', '.ass', '.sub'):
        if ext != '.srt' and sname not in ('plain', 'stuck_cue'):
            continue
        for legacy in (False, True, 'auto'):
            base = '%s_%s_%s' % (sname, ext[1:], str(legacy))
            p = os.path.join(rtl_dir, base + ext)
            with open(p, 'wb') as f:
                f.write(stext.encode('utf-8'))
            k = 'rtl.render.%s' % base
            try:
                out_p = rtl.render_hebrew_rtl_copy(p, legacy_engine=legacy)
                R[k] = _out(base, out_p, p)
                if out_p != p:
                    R[k + '.name'] = os.path.basename(out_p).replace(base, 'B')
            except Exception as e:      # noqa
                R[k] = 'EXC %s: %s' % (type(e).__name__, str(e)[:80])
            k = 'rtl.delivery.%s' % base
            try:
                out_p = rtl.rtl_delivery_copy(p, legacy_engine=legacy)
                R[k] = _out(base, out_p, p)
                if out_p != p:
                    R[k + '.name'] = os.path.basename(out_p)[:6]
            except Exception as e:      # noqa
                R[k] = 'EXC %s: %s' % (type(e).__name__, str(e)[:80])
            for ai in (None, True, False):
                for src in (None, 'Source line one.'):
                    pp = os.path.join(rtl_dir, base + '_ai%s_%s' % (ai, 'src' if src else 'nosrc') + ext)
                    with open(pp, 'wb') as f:
                        f.write(stext.encode('utf-8'))
                    if ai is None and sname == 'plain' and src is None and legacy is False:
                        with open(pp + '.google', 'w') as f:
                            f.write('1')
                    k = 'rtl.inplace.%s.ai%s.%s' % (base, ai, 'src' if src else 'nosrc')
                    try:
                        r = rtl.reapply_rtl_fix_in_place(pp, legacy_engine=legacy, ai_output=ai, source=src, source_lang='en')
                        R[k] = repr(r) + '|' + open(pp, 'rb').read().decode('utf-8', 'replace')
                    except Exception as e:      # noqa
                        R[k] = 'EXC %s: %s' % (type(e).__name__, str(e)[:80])
# missing / unreadable files
for fn in ('missing.srt', 'dir'):
    pp = os.path.join(rtl_dir, fn)
    if fn == 'dir':
        os.makedirs(pp, exist_ok=True)
    for name in ('render_hebrew_rtl_copy', 'rtl_delivery_copy', 'reapply_rtl_fix_in_place'):
        try:
            r_ = getattr(rtl, name)(pp)
            R['rtl.bad.%s.%s' % (name, fn)] = 'path' if r_ == pp else repr(r_)
        except Exception as e:      # noqa
            R['rtl.bad.%s.%s' % (name, fn)] = 'EXC %s' % type(e).__name__
bad_bytes = os.path.join(rtl_dir, 'latin1.srt')
with open(bad_bytes, 'wb') as f:
    f.write(b'1\n00:00:01,000 --> 00:00:02,000\n\xe9\xe8\n')
for name in ('render_hebrew_rtl_copy', 'rtl_delivery_copy'):
    try:
        R['rtl.bad.%s.latin1' % name] = getattr(rtl, name)(bad_bytes) == bad_bytes
    except Exception as e:      # noqa
        R['rtl.bad.%s.latin1' % name] = 'EXC %s' % type(e).__name__
json.dump(R, open(out, 'w', encoding='utf-8'), indent=1, sort_keys=True, ensure_ascii=False)
print('cases (with the RTL matrix)', len(R), 'errors', sum(1 for v in R.values() if isinstance(v, str) and v.startswith('EXC')))


# ---- the breathers (Subtitle Phase 4, step 4f(2)): at most one 1 ms nap per 20 ms in the long loops, under a fake clock
import time as _time_real


def _breather(mod_names, attr):
    for m in mod_names:
        try:
            mod = importlib.import_module(m)
        except ImportError:
            continue
        if hasattr(mod, attr):
            return mod, getattr(mod, attr)
    raise LookupError(attr)


def _breathe_run(label, mod, fn, hook_setter=None):
    naps = [0]
    hooks = [0]
    real_mono, real_sleep = _time_real.monotonic, _time_real.sleep
    start = real_mono() + 100000.0       # the breathers keep their timer: start well after anything they saw
    clock = [start]
    _time_real.monotonic = lambda: clock[0]

    def fake_sleep(s):
        naps[0] += 1
        clock[0] += s
    _time_real.sleep = fake_sleep
    undo = hook_setter(hooks) if hook_setter else None
    try:
        for step in (0.0, 0.007, 0.019, 0.02, 0.05):
            for _ in range(120):
                clock[0] += step
                fn()
            R['breathe.%s.step%s' % (label, step)] = '%d naps, %d hooks, clock %.4f' % (naps[0], hooks[0], clock[0] - start)
    finally:
        _time_real.monotonic, _time_real.sleep = real_mono, real_sleep
        if undo:
            undo()


def _hook_estimate(hooks):
    est = importlib.import_module('resources.lib.sync.align.estimate')
    old = est.YIELD
    est.YIELD = lambda: hooks.__setitem__(0, hooks[0] + 1)
    return lambda: setattr(est, 'YIELD', old)


def _hook_deep(hooks):
    deep = importlib.import_module('resources.lib.sync.deep')
    old = deep._yield_to_viewer
    deep._yield_to_viewer = lambda: hooks.__setitem__(0, hooks[0] + 1)
    return lambda: setattr(deep, '_yield_to_viewer', old)


_m, _f = _breather(['resources.lib.sync.align.estimate'], 'breathe')
_breathe_run('estimate', _m, _f, _hook_estimate)
_m, _f = _breather(['resources.lib.sync.deep'], 'breathe')
_breathe_run('deep', _m, _f, _hook_deep)
_m, _f = _breather(['resources.lib.fetching.embedded.mp4_probe'], '_breathe')
_breathe_run('mp4_probe', _m, _f)
_m, _f = _breather(['resources.lib.fetching.embedded.extract.ebml', 'resources.lib.fetching.embedded.embedded_extract'], '_breathe')
_breathe_run('ebml', _m, _f)
json.dump(R, open(out, 'w', encoding='utf-8'), indent=1, sort_keys=True, ensure_ascii=False)
print('cases (with the breathers)', len(R), 'errors', sum(1 for v in R.values() if isinstance(v, str) and v.startswith('EXC')))
