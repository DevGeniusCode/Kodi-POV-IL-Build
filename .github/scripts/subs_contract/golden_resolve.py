"""Golden harness of ai_translation.service.resolve (Subtitle Phase 4, step 4b): recorded Gemini / Google conversations.

usage:
  golden_resolve.py ADDON_ROOT OUT.json [--only NAME,NAME] [--cover FILE.json]   run every scenario (one fresh interpreter each)
  golden_resolve.py ADDON_ROOT --scenario NAME                                     run ONE scenario, print its record as JSON

Each scenario drives the REAL resolve() through resources.lib.pipeline.resolve with:
  * a fake transport under requests.post (the Gemini REST endpoint): request in (model, prompt digest, generationConfig),
    reply out (HTTP status + body) -- per link type and per failure mode (429, 503, filtered, truncated, 404, ...)
  * stubs for the collaborators that need a network or a player (delivery gate, embedded extraction, the pool client, the
    cast lookup, telemetry, the Google Translate endpoint)
and records: the return value, every file written under the profile (sha256), every log line / toast, every HTTP request,
every progressive_cb event and every stub call. The record of one scenario is plain JSON; check_subs_contract.py compares it
with golden_resolve_expected.json.
"""
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time as _time_mod
import base64 as _b64mod

HERE = os.path.dirname(os.path.abspath(__file__))
SC_DIR = os.environ.get('SUBS_CONTRACT_DIR') or HERE


def _argv_root():
    return os.path.abspath(sys.argv[1])


# ------------------------------------------------------------------------------------------- fixtures
SRT_EN = """1
00:00:01,000 --> 00:00:03,500
JOHN: Hello there.

2
00:00:04,000 --> 00:00:06,000
<i>I don't know what you mean.</i>

3
00:00:06,500 --> 00:00:09,000
- Where are you going?
- Out. Don't wait up.

4
00:00:10,000 --> 00:00:12,000
She said it was fine,
but I'm not so sure.

5
00:00:13,000 --> 00:00:15,000
We should go now.

6
00:00:16,000 --> 00:00:18,000
It's getting late.

7
00:00:19,000 --> 00:00:21,000
Did you hear that?

8
00:00:22,000 --> 00:00:24,000
Let's get out of here.
"""

SRT_HE = """1
00:00:01,000 --> 00:00:03,500
ג'ון: שלום לך.

2
00:00:04,000 --> 00:00:06,000
אני לא יודע למה אתה מתכוון.

3
00:00:06,500 --> 00:00:09,000
- לאן אתה הולך?
- החוצה. אל תחכה לי.

4
00:00:10,000 --> 00:00:12,000
היא אמרה שזה בסדר,
אבל אני לא כל כך בטוח.
"""

SRT_AR = """1
00:00:01,000 --> 00:00:03,500
جون: مرحبا.

2
00:00:04,000 --> 00:00:06,000
لا أعرف ماذا تقصد.

3
00:00:06,500 --> 00:00:09,000
- إلى أين أنت ذاهب؟
- في الخارج. لا تنتظر.

4
00:00:10,000 --> 00:00:12,000
قالت إنه بخير،
لكنني لست متأكدا.

5
00:00:13,000 --> 00:00:15,000
يجب أن نذهب الآن.

6
00:00:16,000 --> 00:00:18,000
أصبح الوقت متأخرا.

7
00:00:19,000 --> 00:00:21,000
هل سمعت ذلك؟

8
00:00:22,000 --> 00:00:24,000
هيا نخرج من هنا.
"""

INFO = {'imdb_id': 'tt1234567', 'title': 'Test Movie', 'year': '2020', 'is_episode': False, 'tvshow': '',
        'season': '', 'episode': '', 'picked_release': 'Test.Movie.2020.1080p.WEB-DL.x264-GRP',
        'filename': 'Test.Movie.2020.1080p.WEB-DL.x264-GRP.mkv', 'release': 'Test.Movie.2020.1080p.WEB-DL.x264-GRP'}
INFO_EP = dict(INFO, is_episode=True, tvshow='Test Show', season='1', episode='2', imdb_id='tt7654321')

SEL = {'token': 'tok1', 'link_hash': 'lh1', 'stream_hash': 'sh1'}


def sha(b):
    if isinstance(b, str):
        b = b.encode('utf-8')
    return hashlib.sha256(b).hexdigest()[:16]


# ------------------------------------------------------------------------------------------- fake Gemini
HEB_MARK = 'תרגום'


def fake_translate_reply(prompt, drop=(), echo=(), start_noise=''):
    """A Gemini-like reply for a prompt: the SRT entries found in the chunk, each text replaced by Hebrew."""
    # the chunk is the last run of SRT blocks in the prompt
    blocks = re.findall(r'(?m)^(\d+)\n(\d\d:\d\d:\d\d,\d\d\d --> \d\d:\d\d:\d\d,\d\d\d)\n((?:.+\n?)+)', prompt)
    out = []
    for num, tc, txt in blocks:
        if num in drop:
            continue
        lines = txt.strip().split('\n')
        if num in echo:
            he = '\n'.join(lines)
        else:
            he = '\n'.join('%s %s: %s' % (HEB_MARK, num, ln.strip('-<>i/ ')) if False else ('%s %s.%d' % (HEB_MARK, num, i + 1))
                           for i, ln in enumerate(lines))
        out.append('%s\n%s\n%s' % (num, tc, he))
    return start_noise + '\n\n'.join(out) + '\n'


class FakeResponse(object):
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else '')
        self.headers = {}
        self.content = self.text.encode('utf-8')

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


def ok_body(text, finish='STOP'):
    return {'candidates': [{'content': {'parts': [{'text': text}]}, 'finishReason': finish}]}


def err_body(code, status, message='x', details=None):
    e = {'code': code, 'status': status, 'message': message}
    if details:
        e['details'] = details
    return {'error': e}


# ------------------------------------------------------------------------------------------- the harness object
class H(object):
    def __init__(self, root, name):
        self.root = root
        self.name = name
        self.profile = tempfile.mkdtemp(prefix='gres_prof_')
        self.work = tempfile.mkdtemp(prefix='gres_work_')
        self.rec = {'results': [], 'log': [], 'notify': [], 'http': [], 'cb': [], 'calls': []}
        self.settings = {}
        self.window_props = {}
        self.gemini_policy = None
        self.http_n = 0
        self.lock = threading.Lock()
        self.stubs = {}

    # ----- recording
    def norm(self, v):
        if isinstance(v, str):
            s = v.replace(self.profile, '<P>').replace(self.work, '<W>')
            s = re.sub(r'gres_(prof|work)_[a-z0-9_]+', r'gres_\1', s)
            s = re.sub(r'(?<![\w.])\d+\.\d{3,}(?![\w.])', 'F', s)
            s = re.sub(r'local_[0-9a-f]{16}', 'local_H', s)
            s = re.sub(r'(?<![0-9a-f])[0-9a-f]{24}(?![0-9a-f])', 'H24', s)       # selection tokens / link hashes
            if s.startswith('RunScript(') and '_b64=' in s:
                s = self.norm_runscript(s)
            return s
        if isinstance(v, dict):
            return {str(self.norm(k)): self.norm(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
        if isinstance(v, (list, tuple)):
            return [self.norm(x) for x in v]
        if isinstance(v, set):
            return sorted(self.norm(x) for x in v)
        if isinstance(v, (int, float, bool)) or v is None:
            return v
        return repr(type(v).__name__)

    def norm_runscript(self, s):
        """RunScript(id,action=x,k_b64=...): the base64 parameters are decoded (the paths in them are temp dirs)"""
        parts = []
        for piece in s[len('RunScript('):-1].split(','):
            if '=' in piece and piece.split('=', 1)[0].endswith('_b64'):
                k, v = piece.split('=', 1)
                try:
                    txt = _b64mod.b64decode(v.encode('ascii')).decode('utf-8')
                except Exception:
                    txt = v
                dec = None
                try:
                    dec = importlib.import_module('resources.lib.core.link_codec').decode_link(txt)
                except Exception:
                    dec = None
                if not dec:
                    try:
                        dec = json.loads(txt)
                    except Exception:
                        dec = None
                parts.append('%s=<%s>' % (k, json.dumps(self.norm(dec if dec else txt), sort_keys=True, ensure_ascii=False)))
            else:
                parts.append(piece)
        return 'RunScript(' + ','.join(parts) + ')'

    def note(self, key, item):
        with self.lock:
            self.rec[key].append(self.norm(item))

    def call(self, name, *a, **k):
        self.note('calls', [name, list(a), k])

    # ----- helpers for scenarios
    def src(self, name, text):
        p = os.path.join(self.work, name)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        return p

    def link(self, payload):
        enc = importlib.import_module('resources.lib.core.link_codec').encode_link
        return enc(payload)


# ------------------------------------------------------------------------------------------- bootstrap
def bootstrap(root, name):
    sys.path.insert(0, SC_DIR)
    import _stubmaker
    h = H(root, name)
    _stubmaker.ADDON_PATH = root
    _stubmaker.PROFILE = h.profile
    _stubmaker.install()
    sys.path.insert(0, root)
    import xbmc
    import xbmcaddon
    import xbmcgui

    props = h.window_props

    class Window(object):
        def __init__(self, wid=0):
            self.wid = wid

        def getProperty(self, k):
            return props.get((self.wid, k), '')

        def setProperty(self, k, v):
            props[(self.wid, k)] = v

        def clearProperty(self, k):
            props.pop((self.wid, k), None)
    xbmcgui.Window = Window
    S = h.settings

    class Addon(object):
        def __init__(self, *a, **k):
            pass

        def getAddonInfo(self, k):
            return {'path': root, 'profile': h.profile, 'id': 'service.subtitles.kodipovilai', 'version': '0.0.0',
                    'name': 'stub'}.get(k, '')

        def getSetting(self, k):
            return S.get(k, '')

        def getSettingBool(self, k):
            return S.get(k, '') == 'true'

        def getSettingInt(self, k):
            try:
                return int(S.get(k, '0'))
            except ValueError:
                return 0

        def getSettingString(self, k):
            return S.get(k, '')

        def setSetting(self, k, v):
            S[k] = v
            return True

        def getLocalizedString(self, i):
            return 'str%d' % i
    xbmcaddon.Addon = Addon
    xbmc.getInfoLabel = lambda k: ''
    xbmc.executeJSONRPC = lambda q: json.dumps({'result': {}})
    xbmc.executebuiltin = lambda *a, **k: h.call('executebuiltin', *a)
    import xbmcvfs
    ADDON_ID = 'service.subtitles.kodipovilai'

    def translate(p):
        if p.startswith('special://profile/addon_data/' + ADDON_ID):
            r = h.profile + p[len('special://profile/addon_data/' + ADDON_ID):]
        elif p.startswith('special://profile/'):
            r = os.path.join(h.work, 'userdata') + '/' + p[len('special://profile/'):]
        elif p.startswith('special://temp'):
            r = os.path.join(h.work, 'temp') + p[len('special://temp'):]
        elif p.startswith('special://home'):
            r = os.path.join(h.work, 'home') + p[len('special://home'):]
        elif p.startswith('special://'):
            r = os.path.join(h.work, 'special') + '/' + p[len('special://'):]
        else:
            r = p
        return r
    xbmcvfs.translatePath = translate
    os.makedirs(os.path.join(h.work, 'temp'), exist_ok=True)
    # a player that plays the video
    class Player(object):
        def isPlayingVideo(self):
            return h.player_playing

        def isPlaying(self):
            return h.player_playing

        def getTime(self):
            return 0.0

        def getPlayingFile(self):
            return ''
    h.player_playing = True
    xbmc.Player = Player
    # time: no real waiting -- a sleep moves a virtual clock (real elapsed time + everything slept)
    real_sleep, real_mono, real_time = _time_mod.sleep, _time_mod.monotonic, _time_mod.time
    t_start_mono, t_start = real_mono(), real_time()
    slept = [0.0]
    clock_lock = threading.Lock()

    def fake_sleep(s=0):
        with clock_lock:
            slept[0] += max(0.0, float(s))
        real_sleep(0)
    _time_mod.sleep = fake_sleep
    _time_mod.monotonic = lambda: (real_mono() - t_start_mono) + slept[0] + 1000.0
    _time_mod.time = lambda: t_start + (real_mono() - t_start_mono) + slept[0]
    return h


def import_all(root):
    """Import every module that may hold part of resolve (and what it calls), so the patching below reaches all of them."""
    lib = os.path.join(root, 'resources', 'lib')
    for sub in ('core', 'scoring_matching', 'fetching', 'sync', 'ai_translation', 'delivery', 'autosub', 'ui'):
        base = os.path.join(lib, sub)
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in ('__pycache__', 'providers', 'pyxbmct', '_libs')] if sub == 'fetching' else dn
            for f in sorted(fn):
                if f.endswith('.py') and f != '__init__.py':
                    rel = os.path.relpath(os.path.join(dp, f), root)[:-3].replace(os.sep, '.')
                    try:
                        importlib.import_module(rel)
                    except Exception as e:      # noqa
                        pass
    for m in ('resources.lib.pipeline', 'resources.lib.telemetry'):
        try:
            importlib.import_module(m)
        except Exception:
            pass


def patch_everywhere(orig, new):
    """Replace every module-level reference to `orig` (a function) in every add-on module -- also inside the dicts, lists and
    tuples that tables of handlers / steps keep at module level"""
    tuples = {}
    for mn, mod in list(sys.modules.items()):
        if not mn.startswith('resources.') or mod is None:
            continue
        for k, v in list(vars(mod).items()):
            if v is orig:
                setattr(mod, k, new)
            elif isinstance(v, dict) and any(x is orig for x in v.values()):
                for kk, vv in list(v.items()):
                    if vv is orig:
                        v[kk] = new
            elif isinstance(v, list) and any(x is orig for x in v):
                for i, x in enumerate(v):
                    if x is orig:
                        v[i] = new
            elif isinstance(v, tuple) and any(x is orig for x in v):
                nt = tuples.get(id(v))
                if nt is None:
                    nt = tuples[id(v)] = tuple(new if x is orig else x for x in v)
                setattr(mod, k, nt)


class _GR(object):
    """the gender-reference functions, wherever the code keeps them (one module before Subtitle Phase 4, a package since):
    `gr.name = fn` replaces the function in its home module and every reference to it"""

    def __setattr__(self, name, new):
        done = False
        for mn, mod in list(sys.modules.items()):
            if mn.startswith('resources.lib.ai_translation.context.gender') and mod is not None:
                orig = getattr(mod, name, None)
                if callable(orig) and getattr(orig, '__module__', None) == mn:
                    patch_everywhere(orig, new)
                    setattr(mod, name, new)
                    done = True
        if not done:
            raise AttributeError('gender reference function not found: ' + name)


def gender_ref_module():
    return _GR()


def install_stubs(h):
    kodi = importlib.import_module('resources.lib.core.kodi')
    h.settings.setdefault('log_level', 'DEBUG')
    orig_log, orig_notify = kodi.log, kodi.notify

    def log(msg, level='INFO'):
        h.note('log', '%s %s' % (level, msg))
    kodi.log = log

    def notify(msg, title=None, icon=None, time_ms=4000):
        h.note('notify', [msg, time_ms])
    kodi.notify = notify
    kodi.localised = lambda i, default='': 'L%d%s' % (i, ('|' + default) if default else '')
    import_all(h.root)
    csel = importlib.import_module('resources.lib.core.selection')
    csel.subtitle_selection_matches = lambda *a, **k: True
    csel.set_subtitle_sync_status = lambda *a, **k: (h.call('set_subtitle_sync_status', a[0] if a else '', sorted(k.items())) or True)
    csel.stage_subtitle_delivery = lambda path, **k: h.call('stage_subtitle_delivery', os.path.basename(path), sorted(k.items()))
    # the delivery gate: the delivered path is the one it is given
    gate = importlib.import_module('resources.lib.sync.delivery_gate')

    def sync_hebrew_delivery(info, path, source_release='', embedded_timing=False, selection=None, fallback_link='',
                             fallback_links=None, **kw):
        h.call('sync_hebrew_delivery', os.path.basename(path or ''), source_release, bool(embedded_timing),
               dict(selection or {}), fallback_link, fallback_links, sorted(kw))
        if path and os.path.isfile(path):
            h.call('delivered_sha', sha(open(path, 'rb').read()))
        return path
    patch_everywhere(gate.sync_hebrew_delivery, sync_hebrew_delivery)
    gate.sync_hebrew_delivery = sync_hebrew_delivery

    # the cast / title lookup (a network read)
    gp_run = importlib.import_module('resources.lib.ai_translation.proofread.gp_run')
    orig_tc = gp_run.title_cast

    def title_cast(info, imdb_id, season, episode):
        h.call('title_cast', imdb_id, season, episode)
        return ([{'name': 'Actor One', 'character': 'John', 'gender': 'male'}, {'name': 'Actress Two', 'character': 'Mary', 'gender': 'female'}], info.get('title') or 'Test Movie', info.get('year') or '2020')
    patch_everywhere(orig_tc, title_cast)
    gp_run.title_cast = title_cast

    # telemetry
    tel = importlib.import_module('resources.lib.telemetry')
    tel.report = lambda ev, *a, **k: h.call('telemetry', {k2: v for k2, v in ev.items() if k2 not in ('dur',)})

    # embedded extraction
    ex = importlib.import_module('resources.lib.fetching.embedded.extraction')
    h.emb = {'aligned': (None, None), 'extract': None, 'unreadable': None, 'reference': None, 'why': {}}

    def aligned(info, lang, progress_cb=None, allow_http=True, **kw):
        h.call('embedded_aligned_source_srt', lang, bool(allow_http), sorted(kw.items()))
        return h.emb['aligned']

    def extract(info, lang, track, progress_cb=None, allow_http=True, steps=None, cancelled=None, why=None, **kw):
        h.call('extract_embedded_srt', lang, track, bool(allow_http), steps is not None)
        if why is not None:
            why.update(h.emb['why'])
        return h.emb['extract']

    def unreadable(info, lang):
        h.call('external_for_unreadable_embedded', lang)
        return h.emb['unreadable']

    def embref(info, lang, cancelled=None):
        h.call('embedded_reference', lang)
        return h.emb['reference']
    for nm, fn in (('embedded_aligned_source_srt', aligned), ('extract_embedded_srt', extract),
                   ('external_for_unreadable_embedded', unreadable), ('embedded_reference', embref),
                   ('note_embedded_miss', lambda reason=None, *a, **k: h.call('note_embedded_miss', reason))):
        patch_everywhere(getattr(ex, nm), fn)
        setattr(ex, nm, fn)
    # streams
    streams = importlib.import_module('resources.lib.fetching.embedded.streams')
    h.streams = {'download': None}

    def sdl(payload, *a, **k):
        h.call('streams.download', payload.get('source'), payload.get('filename'))
        d = h.streams['download']
        if isinstance(d, Exception):
            raise d
        return d
    streams.download = sdl
    streams.select_embedded = lambda idx, lang='he', *a, **k: (h.call('streams.select_embedded', idx, lang) or True)
    # Google Translate transport: a call goes through requests.get too
    return h


def install_http(h):
    import requests

    def post(url, data=None, headers=None, timeout=None, **kw):
        try:
            payload = json.loads(data) if isinstance(data, (str, bytes)) else (kw.get('json') or {})
        except Exception:
            payload = {}
        prompt = ''
        try:
            prompt = payload['contents'][0]['parts'][0]['text']
        except Exception:
            pass
        with h.lock:
            h.http_n += 1
            n = h.http_n
        model = url.split('/models/')[-1].split(':')[0] if '/models/' in url else url
        rec = {'model': model, 'prompt_sha': sha(prompt), 'prompt_len': len(prompt),
               'gen': payload.get('generationConfig'), 'timeout': timeout,
               'has_key': bool((headers or {}).get('x-goog-api-key'))}
        pol = h.gemini_policy
        h.note('http', dict(rec, n=n if h.ordered_http else 0))
        if pol is None:
            return FakeResponse(200, ok_body(fake_translate_reply(prompt)))
        res = pol(n, model, prompt, payload)
        if isinstance(res, FakeResponse):
            return res
        if isinstance(res, Exception):
            raise res
        return FakeResponse(200, ok_body(res))
    requests.post = post
    import urllib.request
    import urllib.error
    import urllib.parse

    class _Raw(object):
        def __init__(self, b):
            self._b = b

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, data=None, timeout=None, *a, **k):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        if 'translate.googleapis.com' in url or 'translate.google' in url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('q', [''])[0]
            h.note('http', {'google': sha(q), 'len': len(q), 'n': 0})
            pol = getattr(h, 'google_policy', None)
            if pol is not None:
                r = pol(q)
                if isinstance(r, Exception):
                    raise r
            out = []
            for ln in q.split('\n'):
                if re.match(r'^\s*\d+\s*$', ln) or '-->' in ln or not ln.strip():
                    out.append(ln)
                else:
                    out.append('גוגל ' + str(len(ln)))
            return _Raw(json.dumps([[['\n'.join(out), q]]]).encode('utf-8'))
        h.note('http', {'urlopen': url.split('?')[0]})
        raise urllib.error.URLError('offline harness')
    urllib.request.urlopen = urlopen
    requests.get = lambda url, *a, **k: (h.note('http', {'get': url.split('?')[0]}) or FakeResponse(404, None, ''))
    h.ordered_http = True


def snapshot_files(h):
    out = {}
    for tag, base in (('P', h.profile), ('T', os.path.join(h.work, 'temp')), ('U', os.path.join(h.work, 'userdata')),
                      ('H', os.path.join(h.work, 'home')), ('S', os.path.join(h.work, 'special'))):
        for dp, dn, fn in os.walk(base):
            for f in fn:
                p = os.path.join(dp, f)
                rel = tag + ':' + os.path.relpath(p, base).replace(os.sep, '/')
                rel = re.sub(r'gpl_[0-9a-f]+', 'gpl_H', h.norm(rel))
                try:
                    out[rel] = 'present' if rel.endswith('gp_later.json') else sha(open(p, 'rb').read())
                except Exception:
                    out[rel] = 'unreadable'
    return out


def cb_recorder(h, tag='cb'):
    def cb(phase, payload=None):
        pl = {}
        for k, v in (payload or {}).items():
            if isinstance(v, str) and len(v) > 60:
                nv = h.norm(v)
                pl[k] = 'sha:%s len:%d' % (sha(nv), len(nv))
            else:
                pl[k] = v
        h.note('cb', [tag, phase, pl])
    return cb


def run_resolve(h, link, info, **kw):
    from resources.lib import pipeline
    try:
        r = pipeline.resolve(link, info, **kw)
    except BaseException as e:      # noqa
        r = 'EXC %s: %s' % (type(e).__name__, str(e)[:100])
        if os.environ.get('GRES_TB'):
            import traceback
            sys.stderr.write(traceback.format_exc())
    h.note('results', r if not isinstance(r, str) else r)
    return r


# ------------------------------------------------------------------------------------------- scenarios
SCEN = {}


def scenario(fn):
    SCEN[fn.__name__[3:]] = fn
    return fn


def ai_settings(h, **extra):
    h.settings.update({'api_key': 'KEY', 'model': 'gemini-3.5-flash-lite', 'translation_mode': 'ai',
                       'gender_proofread': 'false', 'parallel_chunks': '1', 'chunk_lines': '4',
                       'ai_paid_mode': 'false', 'gender_ref_arabic': 'false', 'subs_notifications': 'true',
                       'pool_use': 'false', 'pool_share': 'false'})
    h.settings.update(extra)


def ai_link(h, src_text=SRT_EN, **extra):
    p = h.src('source.en.srt', src_text)
    pl = {'type': 'ai', 'source_lang': 'en', 'local_path': p, 'release': 'Test.Movie.2020.1080p.WEB-DL.x264-GRP',
          'force_ai': True}
    pl.update(extra)
    return h.link(pl), p


@scenario
def sc_bad_link(h):
    run_resolve(h, 'not-a-link', INFO)
    run_resolve(h, h.link({'type': 'wat'}), INFO)


@scenario
def sc_passthrough(h):
    p = h.src('exist.he.srt', SRT_HE)
    run_resolve(h, h.link({'type': 'passthrough', 'path': p, 'release': 'Rel.X'}), INFO, selection=dict(SEL),
                fallback_link='fb', fallback_links=['a', 'b'])
    run_resolve(h, h.link({'type': 'passthrough', 'path': os.path.join(h.work, 'nope.srt')}), INFO)
    run_resolve(h, h.link({'type': 'passthrough'}), INFO)


@scenario
def sc_engine_embedded(h):
    run_resolve(h, h.link({'type': 'engine', 'embedded': True, 'stream_index': 3, 'language': 'he'}), INFO)
    run_resolve(h, h.link({'type': 'engine', 'embedded': True, 'stream_index': 4, 'language': 'en'}), INFO,
                selection=dict(SEL))


@scenario
def sc_engine_download(h):
    p = h.src('dl.he.srt', SRT_HE)
    h.streams['download'] = p
    run_resolve(h, h.link({'type': 'engine', 'source': 'ktuvit', 'language': 'Hebrew', 'filename': 'Rel.A',
                           'download_data': {'id': 1}}), INFO, selection=dict(SEL), fallback_link='fb')
    run_resolve(h, h.link({'type': 'engine', 'source': 'wizdom', 'language': 'English', 'filename': 'Rel.B'}), INFO)
    h.streams['download'] = None
    run_resolve(h, h.link({'type': 'engine', 'source': 'wizdom', 'language': 'Hebrew', 'filename': 'Rel.C'}), INFO)
    h.streams['download'] = RuntimeError('boom')
    run_resolve(h, h.link({'type': 'engine', 'source': 'wizdom', 'language': 'Hebrew', 'filename': 'Rel.D'}), INFO)


@scenario
def sc_engine_ktuvit_mirror(h):
    ai_settings(h, pool_share='true')
    pc = importlib.import_module('resources.lib.fetching.pool_client')
    pcontrib = importlib.import_module('resources.lib.ai_translation.reuse.pool_contribute')
    pc.share_enabled = lambda: True
    pcontrib.was_contributed = lambda p: False
    pcontrib.contribute_ktuvit = lambda info, text, **k: h.call('contribute_ktuvit', len(text), k)
    p = h.src('k.he.srt', SRT_HE)
    h.streams['download'] = p
    run_resolve(h, h.link({'type': 'engine', 'source': 'ktuvit', 'language': 'Hebrew', 'filename': 'Rel.K'}), INFO)


@scenario
def sc_embedded_sync(h):
    p = h.src('emb.he.srt', SRT_HE)
    h.emb['aligned'] = (p, 'he')
    run_resolve(h, h.link({'type': 'embedded_sync', 'stream_index': 2}), INFO, selection=dict(SEL))
    h.emb['aligned'] = (p, 'en')
    run_resolve(h, h.link({'type': 'embedded_sync', 'stream_index': 2}), INFO, selection=dict(SEL))
    h.emb['aligned'] = (None, None)
    run_resolve(h, h.link({'type': 'embedded_sync', 'stream_index': 2}), INFO)


@scenario
def sc_pool(h):
    ai_settings(h, pool_use='true')
    pc = importlib.import_module('resources.lib.fetching.pool_client')
    pr = importlib.import_module('resources.lib.ai_translation.reuse.pool_reuse')
    pc.use_enabled = lambda: True
    pc.share_enabled = lambda: False
    state = {'text': SRT_HE}

    def pst(info, phash):
        h.call('pool_source_text', phash)
        return (state['text'], 'sid123') if state['text'] else ('', '')
    patch_everywhere(pr.pool_source_text, pst)
    pr.pool_source_text = pst
    run_resolve(h, h.link({'type': 'pool', 'hash': 'abc', 'pool_kind': 'ai', 'release': 'Rel.P', 'source_lang': 'en'}),
                INFO, selection=dict(SEL))
    run_resolve(h, h.link({'type': 'pool', 'hash': 'abc', 'pool_kind': 'ktuvit', 'release': 'Rel.P',
                           'source_lang': 'he'}), INFO)
    state['text'] = ''
    run_resolve(h, h.link({'type': 'pool', 'hash': 'zzz', 'pool_kind': 'ai'}), INFO)


@scenario
def sc_ai_basic(h):
    ai_settings(h)
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, selection=dict(SEL), progress_cb=lambda c, t: h.note('cb', ['progress', c, t]),
                progressive_cb=cb_recorder(h), fallback_link='fb')
    # the same link again: the cache answers
    run_resolve(h, link, INFO, selection=dict(SEL), progressive_cb=cb_recorder(h, 'again'))
    run_resolve(h, link, INFO)


@scenario
def sc_ai_no_progressive(h):
    ai_settings(h)
    link, _ = ai_link(h)
    run_resolve(h, link, INFO_EP)


@scenario
def sc_ai_whole(h):
    ai_settings(h, whole_subtitle_request='true')
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_google_mode(h):
    ai_settings(h, translation_mode='google')
    link, _ = ai_link(h)
    gt = importlib.import_module('resources.lib.ai_translation.providers.google_translate')
    names = [n for n in dir(gt) if not n.startswith('_')]
    h.call('google_translate_api', [n for n in names if 'translate' in n.lower()][:6])
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_no_key(h):
    ai_settings(h, api_key='')
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_hebrew_source(h):
    ai_settings(h)
    link, _ = ai_link(h, src_text=SRT_HE)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_pref_lang_not_hebrew(h):
    ai_settings(h)
    s = importlib.import_module('resources.lib.core.settings')
    s.hebrew_subtitle_wanted = lambda: False
    pl = {'type': 'ai', 'source_lang': 'en', 'local_path': h.src('s.srt', SRT_EN), 'release': 'R.1'}
    run_resolve(h, h.link(pl), INFO)
    pl2 = dict(pl, proofread_only=True)
    run_resolve(h, h.link(pl2), INFO)


@scenario
def sc_ai_missing_source(h):
    ai_settings(h)
    pl = {'type': 'ai', 'source_lang': 'en', 'local_path': os.path.join(h.work, 'gone.srt'), 'release': 'R.1',
          'force_ai': True}
    run_resolve(h, h.link(pl), INFO)


def policy_seq(seq, then='ok'):
    """seq: list of items for the first requests (one per request); 'ok' = a good translation."""
    seen = {}
    pol_lock = threading.Lock()

    def pol(n, model, prompt, payload):
        key = sha(prompt)
        with pol_lock:
            k = seen.get(key, 0)
            seen[key] = k + 1
        item = seq[k] if k < len(seq) else then
        if item == 'ok':
            return fake_translate_reply(prompt)
        if item == '429':
            return FakeResponse(429, err_body(429, 'RESOURCE_EXHAUSTED', 'rate'))
        if item == '429day':
            return FakeResponse(429, err_body(429, 'RESOURCE_EXHAUSTED', 'q', [
                {'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                 'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel', 'quotaMetric': 'x'}]}]))
        if item == '503':
            return FakeResponse(503, err_body(503, 'UNAVAILABLE'))
        if item == '404':
            return FakeResponse(404, err_body(404, 'NOT_FOUND'))
        if item == '401':
            return FakeResponse(401, err_body(401, 'UNAUTHENTICATED', 'API key not valid'))
        if item == 'filtered':
            return FakeResponse(200, {'promptFeedback': {'blockReason': 'PROHIBITED_CONTENT'}})
        if item == 'truncated':
            return FakeResponse(200, ok_body(fake_translate_reply(prompt)[:60], 'MAX_TOKENS'))
        if item == 'short':
            return FakeResponse(200, ok_body(fake_translate_reply(prompt, drop=('3',))))
        if item == 'echo':
            return FakeResponse(200, ok_body(fake_translate_reply(prompt, echo=('1', '2', '3', '4', '5', '6', '7', '8'))))
        if item == 'junk':
            return FakeResponse(200, None, 'not json')
        if item == 'err500':
            return FakeResponse(418, None, 'teapot')
        if item == 'net':
            return Exception('Network error')
        return fake_translate_reply(prompt)
    return pol


def ai_with_policy(h, seq, then='ok', link_kw=None, settings_kw=None, info=INFO, **kw):
    sk = {'chunk_lines': '50'}
    sk.update(settings_kw or {})
    if sk.get('ai_paid_mode') == 'true':
        h.ordered_http = False      # a paid key runs the chunks in parallel: a thread race
    ai_settings(h, **sk)
    h.gemini_policy = policy_seq(seq, then)
    link, p = ai_link(h, **(link_kw or {}))
    run_resolve(h, link, info, selection=dict(SEL), progressive_cb=cb_recorder(h), **kw)
    return link


@scenario
def sc_ai_rate_limit(h):
    ai_with_policy(h, ['429', '429'])


@scenario
def sc_ai_rate_limit_forever(h):
    ai_with_policy(h, [], then='429')


@scenario
def sc_ai_daily_quota(h):
    ai_with_policy(h, ['429day'], then='429day')


@scenario
def sc_ai_overload(h):
    ai_with_policy(h, ['503', '503', '503'])


@scenario
def sc_ai_overload_forever(h):
    ai_with_policy(h, [], then='503')


@scenario
def sc_ai_overload_paid(h):
    ai_with_policy(h, ['503', '503', '503'], settings_kw={'ai_paid_mode': 'true', 'chunk_lines': '4'})


@scenario
def sc_ai_model_gone(h):
    ai_with_policy(h, ['404', '404', '404'], settings_kw={'model': 'gemini-9-ultra'})


@scenario
def sc_ai_invalid_key(h):
    ai_with_policy(h, ['401'], then='401')


@scenario
def sc_ai_filtered_once(h):
    ai_with_policy(h, ['filtered'], settings_kw={'chunk_lines': '4'})


@scenario
def sc_ai_filtered_always(h):
    ai_with_policy(h, [], then='filtered', settings_kw={'chunk_lines': '4'})


@scenario
def sc_ai_truncated(h):
    ai_with_policy(h, ['truncated', 'truncated'])


@scenario
def sc_ai_short_reply(h):
    ai_with_policy(h, ['short', 'short'])


@scenario
def sc_ai_echo_reply(h):
    ai_with_policy(h, ['echo', 'echo'])


@scenario
def sc_ai_generic_errors(h):
    ai_with_policy(h, ['err500', 'junk'])


@scenario
def sc_ai_network(h):
    ai_with_policy(h, ['net', 'net', 'net'])


@scenario
def sc_ai_parallel(h):
    h.ordered_http = False
    ai_with_policy(h, [], settings_kw={'parallel_chunks': '3', 'chunk_lines': '2', 'ai_paid_mode': 'true'})


@scenario
def sc_ai_engine_ai(h):
    ai_settings(h)
    p = h.src('eng.en.srt', SRT_EN)
    h.streams['download'] = p
    link = h.link({'type': 'engine_ai', 'source': 'opensubtitles', 'language': 'en', 'src_lang': 'en',
                   'filename': 'Eng.Rel.2020'})
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))
    h.streams['download'] = None
    run_resolve(h, link, INFO_EP)
    run_resolve(h, h.link({'type': 'engine_ai', 'proofread_only': True, 'src_lang': 'en'}), INFO)


@scenario
def sc_ai_embedded_aligned(h):
    ai_settings(h, embedded_translation_mode='auto')
    p = h.src('emb.en.srt', SRT_EN)
    h.emb['aligned'] = (p, 'en')
    link = h.link({'type': 'embedded_ai', 'src_lang': 'en', 'stream_index': 1, 'track_num': 2})
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h), selection=dict(SEL))


@scenario
def sc_ai_embedded_extract(h):
    ai_settings(h, embedded_translation_mode='direct')
    p = h.src('emb2.en.srt', SRT_EN)
    h.emb['extract'] = p
    link = h.link({'type': 'embedded_ai', 'src_lang': 'en', 'stream_index': 1, 'track_num': 2})
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h), selection=dict(SEL))


@scenario
def sc_ai_embedded_unreadable(h):
    ai_settings(h, embedded_translation_mode='direct')
    h.emb['extract'] = None
    h.emb['why'] = {'reason': 'no_text'}
    h.player_playing = True
    ext = h.src('ext.en.srt', SRT_EN)
    h.streams['download'] = ext
    h.emb['unreadable'] = {'type': 'engine_ai', 'src_lang': 'en', 'filename': 'Ext.Rel', 'source': 'x'}
    link = h.link({'type': 'embedded_ai', 'src_lang': 'fr', 'stream_index': 1, 'track_num': 2})
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))
    h.emb['why'] = {'reason': 'timeout'}
    run_resolve(h, link, INFO)
    h.emb['why'] = {'reason': 'no_index'}
    h.emb['unreadable'] = None
    run_resolve(h, link, INFO)


@scenario
def sc_ai_embedded_off(h):
    ai_settings(h, embedded_translation_mode='off')
    link = h.link({'type': 'embedded_ai', 'src_lang': 'en'})
    run_resolve(h, link, INFO)


@scenario
def sc_ai_pool_share(h):
    ai_settings(h, pool_share='true', pool_use='true')
    pc = importlib.import_module('resources.lib.fetching.pool_client')
    pcontrib = importlib.import_module('resources.lib.ai_translation.reuse.pool_contribute')
    pr = importlib.import_module('resources.lib.ai_translation.reuse.pool_reuse')
    pc.use_enabled = lambda: True
    pc.share_enabled = lambda: True
    pc.lookup = lambda info: []
    pcontrib.contribute_once = lambda info, h_, lang, text, **k: h.call('contribute_once', h_, lang, len(text), k)
    pcontrib.was_contributed = lambda p: False
    fetch = {'v': (None, False)}

    def prf(info, content_id, ar_on):
        h.call('pool_reuse_fetch', content_id, ar_on)
        return fetch['v']
    patch_everywhere(pr.pool_reuse_fetch, prf)
    pr.pool_reuse_fetch = prf
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))
    # a pooled copy exists for the other source
    fetch['v'] = (SRT_HE, False)
    link2, _ = ai_link(h, src_text=SRT_EN.replace('Hello', 'Howdy'))
    run_resolve(h, link2, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_proofread(h):
    ai_settings(h, gender_proofread='true')
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_proofread_only(h):
    ai_settings(h, gender_proofread='true')
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))        # translates
    run_resolve(h, h.link(dict(_dec(h, link), proofread_only=True)), INFO, progressive_cb=cb_recorder(h, 'po'))
    # and one with nothing cached
    link2, _ = ai_link(h, src_text=SRT_EN.replace('Hello', 'Hi'))
    run_resolve(h, h.link(dict(_dec(h, link2), proofread_only=True)), INFO, progressive_cb=cb_recorder(h, 'po2'))


def _dec(h, link):
    return importlib.import_module('resources.lib.core.link_codec').decode_link(link)


@scenario
def sc_ai_gender_ref(h):
    ai_settings(h, gender_ref_arabic='true')
    gr = gender_ref_module()

    class Plan(object):
        last_diag = 'stub'

        def __init__(self):
            self.n = 0

        def next(self, max_tries=None, max_seconds=None):
            self.n += 1
            if self.n == 1:
                return 'ar', {1: 'مرحبا', 3: 'إلى أين', 5: 'يجب أن نذهب'}
            if self.n == 2:
                return 'es', {1: 'Hola', 2: 'No sé'}
            return None, None

    def begin(info, src_text, timing_text=None, embedded=None):
        h.call('gender_reference.begin', len(src_text), bool(timing_text), embedded)
        return Plan(), {'cands': 2}
    gr.begin = begin
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))
    # blocked chunk walks the reference ladder
    h.gemini_policy = policy_seq(['filtered', 'filtered', 'filtered', 'filtered'])
    link2, _ = ai_link(h, src_text=SRT_EN.replace('Hello', 'Howdy'))
    run_resolve(h, link2, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_gender_ref_none(h):
    ai_settings(h, gender_ref_arabic='true')
    gr = gender_ref_module()
    gr.begin = lambda info, src_text, timing_text=None, embedded=None: (None, {'reason': 'no_arabic', 'cands': 0})
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_gender_regender(h):
    """the reference check finds conflicting lines and asks for them again (the guarded repair)"""
    ai_settings(h, gender_ref_arabic='true', chunk_lines='50')
    gr = gender_ref_module()

    class Plan(object):
        last_diag = 'stub'

        def next(self, max_tries=None, max_seconds=None):
            return 'ar', {1: 'مرحبا', 2: 'لا أعرف', 3: 'إلى أين', 5: 'يجب أن نذهب', 6: 'متأخر'}

    gr.begin = lambda info, src_text, timing_text=None, embedded=None: (Plan(), {'cands': 1})
    gr.wrong_gender_entries = lambda blocks, ref_map, lang, both_directions=False: [2]
    gr.person_gender_conflicts = lambda blocks, ref_map, lang: {1: [('speaker', 'F')], 3: [('addressee', 'M')]}
    gr.reference_addressee_gender = lambda text, lang: 'F'
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))
    # a repair reply that is no use, and a failing request
    h.gemini_policy = policy_seq(['ok', 'junk', 'net'])
    link2, _ = ai_link(h, src_text=SRT_EN.replace('Hello', 'Howdy'))
    run_resolve(h, link2, INFO, progressive_cb=cb_recorder(h))


@scenario
def sc_ai_cancelled(h):
    ai_settings(h)
    core_sel = importlib.import_module('resources.lib.core.selection')
    core_sel.subtitle_selection_matches = lambda *a, **k: False
    core_sel.set_subtitle_sync_status = lambda *a, **k: True
    link, _ = ai_link(h)
    run_resolve(h, link, INFO, selection=dict(SEL), progressive_cb=cb_recorder(h))


@scenario
def sc_ai_google_replacement(h):
    """a cached Google Translate file this run replaces with AI"""
    ai_settings(h)
    link, p = ai_link(h)
    tc = importlib.import_module('resources.lib.core.cache.translation_cache')
    from resources.lib.ai_translation.source_prep import source_id_for_ai
    sid = source_id_for_ai(_dec(h, link))
    path = tc.translated_path(INFO['imdb_id'], '', '', 'en', source_id=sid, tier='')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(SRT_HE)
    with open(path + '.google', 'w', encoding='utf-8') as f:
        f.write('1')
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))
    h.gemini_policy = policy_seq([], then='503')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(SRT_HE)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h, 'fail'))


@scenario
def sc_ai_hebrew_cache_junk(h):
    """a cached non-Hebrew file is discarded and redone"""
    ai_settings(h)
    link, p = ai_link(h)
    tc = importlib.import_module('resources.lib.core.cache.translation_cache')
    from resources.lib.ai_translation.source_prep import source_id_for_ai
    sid = source_id_for_ai(_dec(h, link))
    path = tc.translated_path(INFO['imdb_id'], '', '', 'en', source_id=sid, tier='')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(SRT_EN)
    run_resolve(h, link, INFO, progressive_cb=cb_recorder(h))


# =========================================================================================== entry points (default.py actions)
import base64 as _b64mod


def b64(s):
    return _b64mod.b64encode(s.encode('utf-8')).decode('ascii')


def install_entry_stubs(h):
    """xbmcplugin / xbmcgui / player recorders for the plugin entry"""
    import xbmc
    import xbmcgui
    import xbmcplugin

    for nm in ('addDirectoryItem', 'addDirectoryItems', 'endOfDirectory', 'setResolvedUrl', 'setContent',
               'addSortMethod', 'setPluginCategory'):
        def mk(name):
            def f(*a, **k):
                h.call('xbmcplugin.' + name, [x if isinstance(x, (str, int, float, bool, type(None))) else
                                                  getattr(x, 'rec', repr(type(x).__name__)) for x in a], k)
                return True
            return f
        setattr(xbmcplugin, nm, mk(nm))

    class ListItem(object):
        def __init__(self, label='', label2='', path='', offscreen=False, **k):
            self.rec = {'label': label, 'label2': label2, 'path': path}

        def setLabel(self, v):
            self.rec['label'] = v

        def setLabel2(self, v):
            self.rec['label2'] = v

        def setPath(self, v):
            self.rec['path'] = v

        def setArt(self, d):
            self.rec['art'] = dict(d)

        def setProperty(self, k, v):
            self.rec.setdefault('props', {})[k] = v

        def setInfo(self, *a, **k):
            self.rec['info'] = repr(a)[:80]

        def getLabel(self):
            return self.rec['label']

        def getPath(self):
            return self.rec['path']
    xbmcgui.ListItem = ListItem

    class Dialog(object):
        def _r(self, name, *a, **k):
            h.call('Dialog.' + name, [x if isinstance(x, (str, int, float, bool, type(None), list)) else repr(type(x).__name__)
                                      for x in a], k)
            return h.dialog_answers.get(name, False)

        def yesno(self, *a, **k):
            return self._r('yesno', *a, **k)

        def ok(self, *a, **k):
            return self._r('ok', *a, **k)

        def notification(self, *a, **k):
            return self._r('notification', *a, **k)

        def select(self, *a, **k):
            return h.dialog_answers.get('select', -1) if self._r('select', *a, **k) is not None else -1

        def textviewer(self, *a, **k):
            return self._r('textviewer', *a, **k)

        def input(self, *a, **k):
            return h.dialog_answers.get('input', '') if self._r('input', *a, **k) is not None else ''

        def multiselect(self, *a, **k):
            return h.dialog_answers.get('multiselect', None) if self._r('multiselect', *a, **k) is not None else None
    h.dialog_answers = {}
    xbmcgui.Dialog = Dialog

    class DialogProgressBG(object):
        def create(self, *a, **k):
            h.call('DialogProgressBG.create', list(a))

        def update(self, *a, **k):
            h.call('DialogProgressBG.update', [x for x in a if isinstance(x, (int, str))])

        def close(self):
            h.call('DialogProgressBG.close')

        def isFinished(self):
            return False
    xbmcgui.DialogProgressBG = DialogProgressBG
    xbmcgui.DialogProgress = DialogProgressBG

    class Player2(object):
        def isPlayingVideo(self):
            return h.player_playing

        def isPlaying(self):
            return h.player_playing

        def getTime(self):
            return 0.0

        def getPlayingFile(self):
            return 'smb://host/Test.Movie.2020.1080p.WEB-DL.x264-GRP.mkv'

        def setSubtitles(self, path):
            h.call('Player.setSubtitles', os.path.basename(path))

        def showSubtitles(self, flag):
            h.call('Player.showSubtitles', bool(flag))

        def getAvailableSubtitleStreams(self):
            return []

        def setSubtitleStream(self, i):
            h.call('Player.setSubtitleStream', i)
    xbmc.Player = Player2
    xbmc.sleep = lambda ms: h.call('xbmc.sleep', ms)
    ex0 = xbmc.executebuiltin
    xbmc.executebuiltin = lambda cmd, *a, **k: h.call('executebuiltin', cmd)
    vc = importlib.import_module('resources.lib.core.video_context')
    vc.current_video_info = lambda: dict(INFO)
    return h


def run_entry(h, query, handle=7, runscript=False):
    """drive resources.lib.entry.plugin.main() like Kodi does"""
    plug = importlib.import_module('resources.lib.entry.plugin')
    saved = list(sys.argv)
    try:
        if runscript:
            sys.argv = ['script'] + query.split('&')
        else:
            sys.argv = ['plugin://service.subtitles.kodipovilai/', str(handle), '?' + query]
        try:
            plug.main()
            h.note('results', 'main returned')
        except BaseException as e:      # noqa
            h.note('results', 'EXC %s: %s' % (type(e).__name__, str(e)[:100]))
            if os.environ.get('GRES_TB'):
                import traceback
                sys.stderr.write(traceback.format_exc())
    finally:
        sys.argv = saved


def _q(**kw):
    import urllib.parse
    return urllib.parse.urlencode(kw)


def entry_scenario(fn):
    SCEN['entry_' + fn.__name__[3:]] = fn
    return fn


@entry_scenario
def sc_dispatch(h):
    """the action table of entry/plugin.py: every action, the default, a RunScript call, an unknown action, a crash"""
    plug = importlib.import_module('resources.lib.entry.plugin')
    names = [n for n in dir(plug) if n.startswith('handle_')]
    for nm in names:
        orig = getattr(plug, nm)

        def mk(name):
            def f(*a, **k):
                h.call('handler:' + name, [x if isinstance(x, (str, int, float, bool, type(None), dict)) else '?'
                                           for x in a], k)
                if h.dispatch_raise == name:
                    raise RuntimeError('handler crash')
            return f
        rec_fn = mk(nm)
        patch_everywhere(orig, rec_fn)       # the name and, since step 4d, the entries of the action tables
        setattr(plug, nm, rec_fn)
    h.dispatch_raise = None
    acts = ['search', 'manualsearch', 'download', 'open_aistudio', 'test_connection', 'connect_gemini', 'show_gemini_usage',
            'open_tmdb_notice', 'clear_cache', 'pool_share_cache', 'pool_share_cache_run', 'purge_temp', 'translate_file',
            'bg_translate_picker', 'he_avail', 'engine_test', 'choose_subs', 'SEARCH', 'nothing_known']
    for a in acts:
        run_entry(h, _q(action=a, x='1'))
    run_entry(h, _q(x='1'))                                   # no action: search
    run_entry(h, 'action=choose_subs&mode=1', runscript=True)
    run_entry(h, 'action=he_avail&flag', runscript=True)
    h.dispatch_raise = 'handle_search'
    run_entry(h, _q(action='search'))
    run_entry(h, _q(action='search'), handle=-1)
    h.dispatch_raise = 'handle_connect_gemini'
    run_entry(h, 'action=connect_gemini', runscript=True)


@entry_scenario
def sc_download_passthrough(h):
    install_entry_stubs(h)
    ai_settings(h)
    p = h.src('exist.he.srt', SRT_HE)
    run_entry(h, _q(action='download', link=h.link({'type': 'passthrough', 'path': p, 'release': 'Rel.X'})))
    run_entry(h, _q(action='download', link=h.link({'type': 'passthrough', 'path': os.path.join(h.work, 'no.srt')})))
    run_entry(h, _q(action='download', link=''))


@entry_scenario
def sc_download_engine(h):
    install_entry_stubs(h)
    ai_settings(h)
    run_entry(h, _q(action='download', link=h.link({'type': 'engine', 'embedded': True, 'stream_index': 2, 'lang': 'he'})))
    p = h.src('dl.he.srt', SRT_HE)
    h.streams['download'] = p
    run_entry(h, _q(action='download', link=h.link({'type': 'engine', 'source': 'ktuvit', 'language': 'Hebrew',
                                                    'filename': 'Rel.A'})))
    h.streams['download'] = None
    run_entry(h, _q(action='download', link=h.link({'type': 'engine', 'source': 'ktuvit', 'language': 'Hebrew',
                                                    'filename': 'Rel.B'})))


@entry_scenario
def sc_download_engine_ai(h):
    install_entry_stubs(h)
    ai_settings(h)
    p = h.src('eng.en.srt', SRT_EN)
    h.streams['download'] = p
    link = h.link({'type': 'engine_ai', 'source': 'opensubtitles', 'language': 'en', 'src_lang': 'en', 'filename': 'E.Rel'})
    run_entry(h, _q(action='download', link=link))
    h.streams['download'] = None
    run_entry(h, _q(action='download', link=link))


@entry_scenario
def sc_download_embedded_ai(h):
    install_entry_stubs(h)
    ai_settings(h)
    run_entry(h, _q(action='download', link=h.link({'type': 'embedded_ai', 'src_lang': 'en', 'stream_index': 1,
                                                    'track_num': 2})))


@entry_scenario
def sc_download_ai(h):
    install_entry_stubs(h)
    ai_settings(h)
    link, _ = ai_link(h)
    run_entry(h, _q(action='download', link=link))
    h.settings['pov_fast_navigation'] = 'true'
    run_entry(h, _q(action='download', link=link))
    h.settings['use_builtin_engine'] = 'true'
    h.gemini_policy = policy_seq([], then='503')
    link2, _ = ai_link(h, src_text=SRT_EN.replace('Hello', 'Howdy'))
    run_entry(h, _q(action='download', link=link2))


@entry_scenario
def sc_download_pool(h):
    install_entry_stubs(h)
    ai_settings(h, pool_use='true')
    pc = importlib.import_module('resources.lib.fetching.pool_client')
    pr = importlib.import_module('resources.lib.ai_translation.reuse.pool_reuse')
    pc.use_enabled = lambda: True
    pc.share_enabled = lambda: False
    pst = lambda info, phash: (SRT_HE, 'sid9')
    patch_everywhere(pr.pool_source_text, pst)
    pr.pool_source_text = pst
    run_entry(h, _q(action='download', link=h.link({'type': 'pool', 'hash': 'abc', 'pool_kind': 'ai', 'release': 'R.P',
                                                    'source_lang': 'en'})))


@entry_scenario
def sc_translate_file(h):
    install_entry_stubs(h)
    ai_settings(h, chunk_lines='4')
    src = h.src('in.en.srt', SRT_EN)
    out = os.path.join(h.work, 'out.he.srt')
    run_entry(h, _q(action='translate_file', input_b64=b64(src), output_b64=b64(out)))
    run_entry(h, _q(action='translate_file', input_b64=b64(os.path.join(h.work, 'nope.srt')), output_b64=b64(out + '2')))
    run_entry(h, _q(action='translate_file', input_b64='', output_b64=''))
    h.gemini_policy = policy_seq([], then='503')
    run_entry(h, _q(action='translate_file', input_b64=b64(src), output_b64=b64(out + '3')))


@entry_scenario
def sc_bg_translate_picker(h):
    install_entry_stubs(h)
    ai_settings(h, chunk_lines='4')
    link, _ = ai_link(h)
    run_entry(h, _q(action='bg_translate_picker', link_b64=b64(link), source_id_b64=b64('6295ece761ff8e6b'),
                    selection_b64=''))
    run_entry(h, _q(action='bg_translate_picker', link_b64='', source_id_b64='', selection_b64=''))
    csel = importlib.import_module('resources.lib.core.selection')
    csel.subtitle_selection_matches = lambda *a, **k: False
    run_entry(h, _q(action='bg_translate_picker', link_b64=b64(link), source_id_b64='x', selection_b64=''))


@entry_scenario
def sc_bg_translate_embedded(h):
    install_entry_stubs(h)
    ai_settings(h, embedded_translation_mode='direct')
    p = h.src('emb.en.srt', SRT_EN)
    h.emb['extract'] = p
    link = h.link({'type': 'embedded_ai', 'src_lang': 'en', 'stream_index': 1, 'track_num': 2})
    run_entry(h, _q(action='bg_translate_picker', link_b64=b64(link), source_id_b64='', selection_b64=''))


@entry_scenario
def sc_service_main(h):
    """entry/service.main(): the start-up steps and migrations in their order, then the abort check"""
    svc = importlib.import_module('resources.lib.entry.service')
    mig = importlib.import_module('resources.lib.entry.migrations')

    def rec(name, ret=None):
        def f(*a, **k):
            h.call('step:' + name)
            return ret
        return f
    for nm in sorted(n for n in dir(mig) if n.startswith('maybe_')):
        fn = getattr(mig, nm)
        if callable(fn):
            r = rec(nm)
            patch_everywhere(fn, r)
            setattr(mig, nm, r)
    for nm in sorted(n for n in dir(svc) if n.startswith('_start_') or n in ('_prune_once', '_maybe_prewarm_engine',
                                                                             '_maybe_start_autosub_player')):
        r = rec(nm)
        setattr(svc, nm, r)
    svc._check_first_run_marker = rec('_check_first_run_marker', False)
    svc.main()
    h.note('results', 'main returned')
    svc._check_first_run_marker = rec('_check_first_run_marker', True)
    svc.main()
    h.note('results', 'main returned (first-run marker)')


# ------------------------------------------------------------------------------------------- runner
class _Cover(object):
    def __init__(self, base):
        self.base = base
        self.hits = {}

    def trace(self, frame, event, arg):
        fn = frame.f_code.co_filename
        if not fn.startswith(self.base):
            return None
        s = self.hits.setdefault(os.path.relpath(fn, self.base).replace(os.sep, '/'), set())

        def local(frame, event, arg):
            if event == 'line':
                s.add(frame.f_lineno)
            return local
        s.add(frame.f_lineno)
        return local


def run_one(root, name, cover=None):
    h = bootstrap(root, name)
    install_stubs(h)
    install_http(h)
    cov = None
    if cover:
        cov = _Cover(os.path.join(root, 'resources', 'lib'))
        sys.settrace(cov.trace)
        threading.settrace(cov.trace)
    try:
        SCEN[name](h)
    except BaseException as e:      # noqa
        import traceback
        h.rec['scenario_error'] = traceback.format_exc()[-600:]
    finally:
        sys.settrace(None)
        threading.settrace(None)
    # let daemon threads (backfills, proofread) finish
    deadline = _time_mod.time() + 3
    while _time_mod.time() < deadline and any(t.is_alive() and not t.daemon for t in threading.enumerate()
                                               if t is not threading.current_thread()):
        _time_mod.sleep(0.01)
    h.rec['files'] = snapshot_files(h)
    # worker threads race the main thread: lines are compared as multisets
    h.rec['log'] = sorted(h.rec['log'])
    h.rec['notify'] = sorted(h.rec['notify'], key=lambda x: json.dumps(x))
    if h.ordered_http is False:
        # which chunk lands first is a thread race: only the number of live previews is recorded
        for ev in h.rec['cb']:
            if len(ev) == 3 and ev[1] == 'chunk_ready':
                ev[2] = {'total': ev[2].get('total')}
        h.rec['http'] = sorted(h.rec['http'], key=lambda x: json.dumps(x, sort_keys=True))
        h.rec['cb'] = sorted(h.rec['cb'], key=lambda x: json.dumps(x, sort_keys=True))
    out = h.rec
    if cover:
        out = {'rec': h.rec, 'cover': {k: sorted(v) for k, v in cov.hits.items()}}
    return out


def main():
    root = _argv_root()
    args = sys.argv[2:]
    if '--scenario' in args:
        name = args[args.index('--scenario') + 1]
        cover = '--cover-inline' in args
        sys.stdout.write(json.dumps(run_one(root, name, cover), sort_keys=True))
        return
    out = args[0]
    only = None
    if '--only' in args:
        only = args[args.index('--only') + 1].split(',')
    covfile = args[args.index('--cover') + 1] if '--cover' in args else None
    from concurrent.futures import ThreadPoolExecutor
    names = [n for n in SCEN if (not only or n in only)]

    def go(n):
        cmd = [sys.executable, '-I', '-B', os.path.abspath(__file__), root, '--scenario', n]
        if covfile:
            cmd.append('--cover-inline')
        env = dict(os.environ, SUBS_CONTRACT_DIR=SC_DIR, PYTHONHASHSEED='0')
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
        try:
            return n, json.loads(p.stdout)
        except Exception:
            return n, {'runner_error': (p.stdout[-300:] + p.stderr[-600:])}
    with ThreadPoolExecutor(6) as ex:
        res = dict(ex.map(go, names))
    cover = {}
    final = {}
    for n, r in res.items():
        if covfile and 'rec' in r:
            for k, v in r['cover'].items():
                cover.setdefault(k, set()).update(v)
            r = r['rec']
        final['resolve.' + n] = json.dumps(r, sort_keys=True)
    json.dump(final, open(out, 'w', encoding='utf-8'), indent=0, sort_keys=True)
    if covfile:
        json.dump({k: sorted(v) for k, v in cover.items()}, open(covfile, 'w'))
    print('%d scenarios' % len(final))


if __name__ == '__main__':
    main()
