#!/usr/bin/env python3
"""Contract smoke test of the subtitle add-on `service.subtitles.kodipovilai` (lightweight, manual-maintenance form).

Stdlib only, no Kodi. Static checks read the source (ast / xml / po): provider contract, frozen names, pinned strings,
no private cross-module names, single route, settings / strings consistency. The dynamic part imports every module under
its own xbmc stubs (one fresh interpreter per module) and runs subs_contract/unit_chooser_threads.py (the chooser's
one-owner-thread rule). The golden harness and the other unit files were removed on purpose (a behaviour change no longer
needs a re-recorded 680 KB fixture); he_sub_match / standalone-module facts live in subs_contract/baseline.json.

usage: python .github/scripts/check_subs_contract.py [--static]      (--static skips the interpreter-based part)
Exit status 1 when any check fails.
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ADDON = os.path.join(REPO, 'service.subtitles.kodipovilai')
LIB = os.path.join(ADDON, 'resources', 'lib')
FIX = os.path.join(HERE, 'subs_contract')
BASE = json.load(open(os.path.join(FIX, 'baseline.json'), encoding='utf-8'))
FAILS = []
PASSES = []


def check(name, ok, detail=''):
    (PASSES if ok else FAILS).append(name)
    print('%s  %s%s' % ('ok  ' if ok else 'FAIL', name, ('  -> ' + str(detail)) if (detail and not ok) else ''))
    return ok


def rd(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8')


# ------------------------------------------------------------------ the module list
def vendored(rel):
    r = '/' + rel.replace(os.sep, '/')
    return '/_libs/' in r or '/pyxbmct/' in r or r.startswith('/resources/lib/fetching/providers/')


FILES = {}      # dotted module -> relative path (non vendored, under the add-on)
ALL_PY = []     # every .py of the add-on (relative, '/' separated)
for dp, dn, fn in os.walk(ADDON):
    dn[:] = [d for d in dn if d != '__pycache__']
    for f in fn:
        if f.endswith('.py'):
            rel = os.path.relpath(os.path.join(dp, f), ADDON).replace(os.sep, '/')
            ALL_PY.append(rel)
            if not vendored(rel):
                m = rel[:-3].replace('/', '.')
                FILES[m[:-9] if m.endswith('.__init__') else m] = rel
TREES = {m: ast.parse(rd(os.path.join(ADDON, rel))) for m, rel in FILES.items()}
MODSET = set(FILES)
for m in list(FILES):
    parts = m.split('.')
    for i in range(1, len(parts)):
        MODSET.add('.'.join(parts[:i]))


def mod_level_imports(tree):
    out = []

    def walk(body):
        for n in body:
            if isinstance(n, ast.Import):
                out.append('import ' + ', '.join(a.name + (' as ' + a.asname if a.asname else '') for a in n.names))
            elif isinstance(n, ast.ImportFrom):
                out.append('from %s%s import %s' % ('.' * n.level, n.module or '', ', '.join(
                    a.name + (' as ' + a.asname if a.asname else '') for a in n.names)))
            elif isinstance(n, (ast.Try, ast.If)):
                walk(n.body)
                for h in getattr(n, 'handlers', []):
                    walk(h.body)
                walk(n.orelse)
                walk(getattr(n, 'finalbody', []))
    walk(tree.body)
    return out


def top_defs(tree):
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def sig(fn):
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args]
    return names + (['*' + a.vararg.arg] if a.vararg else []) + [x.arg for x in a.kwonlyargs] + (
        ['**' + a.kwarg.arg] if a.kwarg else [])


def top_assigned(tree):
    out = set()
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


# ------------------------------------------------------------------ providers
PROV = os.path.join(LIB, 'fetching', 'providers', 'sources')
srcs = sorted(f[:-3] for f in os.listdir(PROV) if f.endswith('.py') and f != '__init__.py')
check('7 provider modules in fetching/providers/sources', len(srcs) == 7, srcs)
for s in srcs:
    t = ast.parse(rd(os.path.join(PROV, s + '.py')))
    d, a = top_defs(t), top_assigned(t)
    ok = ('global_var' in a and 'site_id' in a and 'get_subs' in d and 'download' in d
          and sig(d['get_subs'])[:1] == ['video_data'] and sig(d['download'])[:2] == ['download_data', 'MySubFolder'])
    check('provider %s exposes global_var, site_id, get_subs(video_data...), download(download_data, MySubFolder...)' % s, ok)
bad = []
for m, rel in FILES.items():
    if m == 'resources.lib.fetching.provider_adapter':
        continue
    for n in ast.walk(TREES[m]):
        if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('resources.lib.fetching.providers'):
            bad.append(m)
        if isinstance(n, ast.ImportFrom) and n.module == 'resources.lib.fetching' and any(a.name == 'providers' for a in n.names):
            bad.append(m)
        if isinstance(n, ast.Import) and any(a.name.startswith('resources.lib.fetching.providers') for a in n.names):
            bad.append(m)
check('fetching/provider_adapter.py is the only importer of fetching/providers', not bad, bad)
check('there is no Telegram module', not [r for r in ALL_PY if 'telegram' in r.lower()])

# ------------------------------------------------------------------ the standalone modules and he_sub_match
STD = getattr(sys, 'stdlib_module_names', None)
files = {'he_sub_match': 'he_sub_match.py', 'release': 'scoring_matching/release.py'}
for k, f in files.items():
    data = open(os.path.join(LIB, f), 'rb').read()
    imps = mod_level_imports(ast.parse(data.decode('utf-8')))
    check('module-level imports of %s unchanged' % f, imps == BASE[k]['module_level_imports'], imps)
    if k == 'he_sub_match':
        check('he_sub_match.py sha256 equals the recorded one', hashlib.sha256(data).hexdigest() == BASE[k]['sha256'])
t = TREES['resources.lib.he_sub_match']
check('he_sub_match keeps its 5 API names', all(n in top_defs(t) for n in
      ('prewarm', 'release_names', 'embedded_names', 'confirmed_releases', 'label_prefix')))
for k in ('he_sub_match', 'release'):
    tops = set()
    for line in mod_level_imports(TREES['resources.lib.he_sub_match' if k == 'he_sub_match' else 'resources.lib.scoring_matching.release']):
        tops.add(re.split(r'[ ,.]', line.split(' ', 1)[1].replace('from ', ''))[0])
    check('%s imports only the standard library at module level' % k,
          STD is None or all(x in STD for x in tops), tops)

# ------------------------------------------------------------------ frozen names
for ref, name, want in BASE['frozen_signatures']:
    d = top_defs(TREES[ref]).get(name)
    check('frozen name %s.%s(%s)' % (ref, name, ', '.join(want)), d is not None and sig(d) == want, d and sig(d))
check('core.kodi.DIALOG_TITLE exists', 'DIALOG_TITLE' in top_assigned(TREES['resources.lib.core.kodi']))

# ------------------------------------------------------------------ private names across modules
def alias_map(tree):
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            for a in n.names:
                full = n.module + '.' + a.name
                if full in FILES:
                    out.setdefault(a.asname or a.name, set()).add(full)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name in FILES:
                    out.setdefault(a.asname or a.name.split('.')[0], set()).add(a.name)
    return out


def is_priv(s):
    return s.startswith('_') and not (s.startswith('__') and s.endswith('__')) and s.strip('_')


allowed = set(tuple(x) for x in BASE['private_exceptions'])
found = []
for m, tree in TREES.items():
    if m == 'resources.lib.he_sub_match':
        continue    # frozen file: its function-scope aliases (_pool = pool_client / pool_contribute) are ambiguous
    am = alias_map(tree)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module in FILES and n.module != m:
            for a in n.names:
                if is_priv(a.name) and (n.module, a.name) not in allowed:
                    found.append('%s imports %s._%s' % (m, n.module, a.name.lstrip('_')))
        elif isinstance(n, ast.Attribute) and is_priv(n.attr) and isinstance(n.value, ast.Name):
            for home in am.get(n.value.id, ()):
                if home != m and (home, n.attr) not in allowed:
                    found.append('%s reads %s.%s' % (m, home, n.attr))
check('no module imports or reads a _private name of another module (known exceptions: %d)' % len(allowed), not found, found[:8])

# ------------------------------------------------------------------ layering / naming
cyc = {}
core = {m: t for m, t in TREES.items() if m.startswith('resources.lib.core.')}


def top_internal(tree):
    out = set()

    def visit(body):
        for n in body:
            if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                if n.module == 'resources.lib.core':
                    out.update('resources.lib.core.' + a.name for a in n.names)
                else:
                    out.add(n.module)
                    out.update(n.module + '.' + a.name for a in n.names)
            elif isinstance(n, (ast.Try, ast.If)):
                visit(n.body)
                for h in getattr(n, 'handlers', []):
                    visit(h.body)
                visit(n.orelse)
    visit(tree.body)
    return {x for x in out if x in core}


graph = {m: top_internal(t) for m, t in core.items()}
state = {}
cycle = []


def dfs(v, path):
    state[v] = 1
    for w in graph[v]:
        if state.get(w) == 1:
            cycle.append(path + [v, w])
        elif w not in state:
            dfs(w, path + [v])
    state[v] = 2


for v in graph:
    if v not in state:
        dfs(v, [])
check('core/ has no import cycle (module-level imports)', not cycle, cycle[:1])
fl = []
for f in ('kodi', 'settings'):
    for fn in ast.walk(TREES['resources.lib.core.' + f]):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('resources'):
                    fl.append((f, n.lineno))
check('no function-level import of this add-on inside core/kodi.py and core/settings.py', not fl, fl)
check('no module named kodi_utils or sub_utils', not [r for r in ALL_PY if os.path.basename(r) in ('kodi_utils.py', 'sub_utils.py')])
check('core/utils/ does not exist', not os.path.isdir(os.path.join(LIB, 'core', 'utils')))
named = []
for rel in ALL_PY + ['addon.xml', 'resources/settings.xml']:
    if vendored(rel):
        continue
    txt = rd(os.path.join(ADDON, rel))
    for bad_name in ('plugin.program.kodipovilwizard', 'uservar', 'povil_icons'):
        if bad_name in txt:
            named.append((rel, bad_name))
check('nothing names the Wizard, uservar or povil_icons', not named, named[:5])
lit = []
for rel in ALL_PY:
    if vendored(rel) or rel == 'resources/lib/core/kodi.py':
        continue
    if "'Kodi POV IL'" in rd(os.path.join(ADDON, rel)) or '"Kodi POV IL"' in rd(os.path.join(ADDON, rel)):
        lit.append(rel)
check("no dialog-title literal 'Kodi POV IL' outside core/kodi.py (DIALOG_TITLE)", not lit, lit)
route = []
for m, t in TREES.items():
    if m == 'resources.lib.pipeline':
        continue
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom) and n.level == 0:
            if n.module in ('resources.lib.fetching.service', 'resources.lib.ai_translation.service'):
                route.append(m)
            if n.module in ('resources.lib.fetching', 'resources.lib.ai_translation') and any(a.name == 'service' for a in n.names):
                route.append(m)
check('single route: only pipeline.py imports fetching.service / ai_translation.service', not route, route)

# ------------------------------------------------------------------ pinned strings
alltext = '\n'.join(rd(os.path.join(ADDON, r)) for r in ALL_PY if not vendored(r)) + rd(
    os.path.join(ADDON, 'resources', 'settings.xml'))
for s in BASE['pinned_strings']:
    check('pinned string %r present' % s, s in alltext)
check('chooser XML name unchanged', os.path.isfile(os.path.join(ADDON, 'resources', 'skins', 'Default', '1080i', 'script.moransubs.chooser.xml')))
ax = rd(os.path.join(ADDON, 'addon.xml'))
check('addon.xml hooks: xbmc.subtitle.module = default.py, xbmc.service = service.py',
      'point="xbmc.subtitle.module" library="default.py"' in ax and 'point="xbmc.service" library="service.py"' in ax)

# ------------------------------------------------------------------ settings.xml and strings.po
sx = ET.parse(os.path.join(ADDON, 'resources', 'settings.xml')).getroot()
declared = {e.get('id') for e in sx.iter('setting') if e.get('id')}
for s in BASE['wizard_read_settings']:
    check('settings id the Wizard reads is declared: %s' % s, s in declared)
# Module-level constants that name a setting (SETTING_COUNT, EXHAUSTED_SETTING, _EMB_REF_SETTING, ...): a name
# containing SETTING whose value is a string. Their values count as setting ids, and a call that passes the
# constant (SETTING_DATE, EXHAUSTED_SETTING) is resolved to the value.
SETTING_CONSTS = {}
for m, t in TREES.items():
    for n in t.body:
        if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                and 'SETTING' in n.targets[0].id and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)):
            SETTING_CONSTS[n.targets[0].id] = n.value.value
read = set(SETTING_CONSTS.values())
for m, t in TREES.items():
    for n in ast.walk(t):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ('get_setting', 'get_bool', 'get_int', 'get_float', 'set_setting')
                and n.args):
            a0 = n.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                read.add(a0.value)
            elif isinstance(a0, ast.Name) and a0.id in SETTING_CONSTS:
                read.add(SETTING_CONSTS[a0.id])
undeclared = sorted(x for x in read if x not in declared and x not in BASE['settings_known_undeclared'])
check('every setting id read or written in code, literal or through a *SETTING* constant (%d constants), is declared in settings.xml '
      '(known exceptions: %s)' % (len(SETTING_CONSTS), BASE['settings_known_undeclared']),
      not undeclared, undeclared)
labels = set()
for el in sx.iter():
    for k in ('label', 'help'):
        v = el.get(k)
        if v and v.isdigit():
            labels.add(int(v))
    if el.tag in ('label', 'help') and (el.text or '').strip().isdigit():
        labels.add(int(el.text.strip()))
po = {}
for lang in ('resource.language.en_gb', 'resource.language.he_il'):
    t = rd(os.path.join(ADDON, 'resources', 'language', lang, 'strings.po'))
    po[lang] = {}
    for blk in re.split(r'\n\s*\n', t.replace('\r\n', '\n')):
        mm = re.search(r'msgctxt "#(\d+)"', blk)
        if mm:
            po[lang][int(mm.group(1))] = (blk, '')
en, he = po['resource.language.en_gb'], po['resource.language.he_il']
check('strings.po: the same ids in en_gb and he_il', set(en) == set(he), sorted(set(en) ^ set(he))[:5])
check('every label id of settings.xml exists (placeholder %s excepted)' % BASE['placeholder_label_ids'],
      all(i in en for i in labels if i not in BASE['placeholder_label_ids']), sorted(i for i in labels if i not in en))
check('no string id >= 33000 (strings.po and settings.xml)', not [i for i in set(en) | labels if i >= 33000])
pcts = sorted(set(i for i, (b, c) in list(en.items()) + list(he.items()) if '%' in b or '%' in c))
check('no % in a label (ids below the baseline list are known)', set(pcts) <= set(BASE['labels_with_percent']), pcts)
check('runtime strings 32630-32638 present', all(i in en for i in range(32630, 32639)))

# ------------------------------------------------------------------ dynamic part
if '--static' not in sys.argv:
    sys.path.insert(0, FIX)
    code = ("import sys,os\nsys.path.insert(0,%r)\nimport _stubmaker\n_stubmaker.ADDON_PATH=%r\n_stubmaker.PROFILE=%r\n"
            "_stubmaker.install()\nsys.path.insert(0,%r)\nimport importlib\nimportlib.import_module(%r)\n")

    def imp(m):
        prof = tempfile.mkdtemp(prefix='contract_prof_')
        try:
            p = subprocess.run([sys.executable, '-I', '-B', '-W', 'error::SyntaxWarning', '-c', code % (FIX, ADDON, prof, ADDON, m)],
                               capture_output=True, text=True, timeout=120, cwd=prof)
            return m, p.returncode, p.stderr[-300:]
        except subprocess.TimeoutExpired:
            return m, -1, 'timeout'
    mods = sorted(FILES)
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(imp, mods))
    badm = [r for r in res if r[1] != 0]
    check('every module under resources/ imports under its own xbmc stubs (%d of %d; the known-failure list is empty)'
          % (len(res) - len(badm), len(res)), not badm, badm[:3])
    # the chooser window's threading rule (21 Nov 2026, after the Kodi crash of 20 Nov 2026): one owner thread makes every ListItem and
    # every control call, the background searches only produce plain data; show() + a Monitor loop instead of doModal() (fake Kodi runtime)
    p = subprocess.run([sys.executable, '-I', '-B', '-W', 'ignore', os.path.join(FIX, 'unit_chooser_threads.py')],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    bad = [l for l in p.stdout.splitlines() if l.startswith('FAIL')]
    check('unit_chooser_threads: ListItems and control calls only on the window thread, Back / Esc / abort end the loop (%d checks)'
          % sum(1 for l in p.stdout.splitlines() if l.startswith(('ok ', 'FAIL'))), p.returncode == 0 and not bad,
          bad[:3] or (p.stderr or '')[-300:])

print('\n%d checks passed, %d failed' % (len(PASSES), len(FAILS)))
sys.exit(1 if FAILS else 0)
