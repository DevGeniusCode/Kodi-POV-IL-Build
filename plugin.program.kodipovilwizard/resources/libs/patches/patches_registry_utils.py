# -*- coding: utf-8 -*-

# Registry utilities for patches_config.py (Phase 4: moved out so the registry file holds entries only).
#
#   _build_patch_hook(...)  builds every hook that calls a logic module (see "Writing a hook" in patches_config.py)
#   _*_HOOK constants       hooks shared by several patches
#   validate_registry(...)  schema / compile / module-exists checks, run by `python patches_config.py`
#
# The helper code below is moved verbatim from patches_config.py; the only change is that
# validate_registry() now receives the registry as its first argument.

PATCHES_DIR = 'special://home/addons/plugin.program.kodipovilwizard/resources/libs/patches/'
GUARD_DIR = 'special://home/addons/plugin.program.kodipovilwizard/resources/libs/profiles/'

def _build_patch_hook(module_name, custom_logic='', indent='', fail_closed=False, patch_dir=PATCHES_DIR):
    """Self-contained import-and-delegate block for a host patch.

    Every hook imports the wizard module itself, so no patch depends on a name
    another patch injected, and a drifted anchor can disable one hook without
    breaking the host add-on.

    Names that only the hook needs are unique (``_povil_dir`` and the module).
    ``sys``, ``xbmcvfs``, ``xbmc`` and ``traceback`` are reached through
    ``__import__`` on purpose: an ``import`` statement inside a host function
    makes that name a LOCAL of the whole function, so the host's own earlier
    use of ``sys``/``xbmcvfs`` would raise UnboundLocalError.

    fail_closed=False  Enrichment hooks (stamping metadata). On any error the
                       trace is logged and the host carries on.
    fail_closed=True   Enforcement hooks (route, playback, directory, source
                       guards). The trace is logged and the error is re-raised.
    patch_dir          The directory path to append to sys.path (defaults to PATCHES_DIR).
    """
    lines = [
        "try:",
        f"\t_povil_dir = __import__('xbmcvfs').translatePath('{patch_dir}')",
        "\tif _povil_dir not in __import__('sys').path:",
        "\t\t__import__('sys').path.append(_povil_dir)",
        f"\timport {module_name}"
    ]

    if custom_logic:
        for line in custom_logic.strip('\n').split('\n'):
            lines.append(("\t" + line) if line else "")

    lines.extend([
        "except Exception:",
        f"\t__import__('xbmc').log('Wizard Patch Error [{module_name}]:\\n' + __import__('traceback').format_exc(), __import__('xbmc').LOGERROR)"
    ])

    if fail_closed:
        lines.append("\traise")

    return ''.join((indent + line if line else '') + '\n' for line in lines)

def _debrid_unbound_hook(provider):
    """Module-level torrent_id default plus the guard-active log line for a debrid API module."""
    return "torrent_id = None\n" + _build_patch_hook(
        module_name="pov_debrid_guardian",
        custom_logic="pov_debrid_guardian.log_guard_active('%s')" % provider
    )


# Hooks shared by several patches (identical code, different targets).

_META_BLANK_GUARD_HOOK = (
    "\t\t\t# WIZARD: Bypass cache persistence and purge poisoned DB rows\n"
    + _build_patch_hook(
        module_name="pov_meta_handler",
        indent="\t\t\t",
        custom_logic="pov_meta_handler.clear_blank_meta()"
    )
    + "\t\t\treturn meta\n"
)

_GENRE_TRANSLATE_HOOK = _build_patch_hook(
    module_name="pov_translations",
    indent="\t\t",
    custom_logic="genre_list = pov_translations.translate_genres(genre_list)"
)

_HTTP_LAZY_HOOK = _build_patch_hook(
    module_name="pov_http_lazy",
    custom_logic="pov_http_lazy.run()"
)


# ---------------------------------------------------------------------------
# Registry validation (not run at import time; `python patches_config.py` calls it)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = (
    'id', 'name', 'description', 'category', 'priority', 'addon_id', 'enabled',
    'target_file', 'marker', 'anchor', 'action', 'hook',
)
_CATEGORIES = {
    'translation', 'ui', 'ux', 'performance', 'subtitles', 'crash_guard',
    'account_manager', 'parental_control', 'reliability', 'diagnostics', 'compatibility',
}
_PRIORITIES = {'critical', 'major', 'minor'}
_ACTIONS = {'prepend_before', 'append_after'}
# Imports in a hook that do not come from PATCHES_DIR.
_NON_PATCH_MODULES = {'sys', 'time', 're', 'threading', 'traceback', 'xbmc', 'xbmcvfs', 'xbmcaddon'}


def _compile_hook(hook):
    """Compiles a hook inside a dummy function/loop nested to the hook's own indent depth."""
    import re
    lines = hook.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    code = [l for l in lines if l.strip() and not l.lstrip().startswith('#')]
    depth = len(code[0]) - len(code[0].lstrip('\t')) if code else 0
    src = ['def _outer():']
    src += ['\t%s = None' % name for name in sorted(set(re.findall(r'nonlocal\s+(\w+)', hook)))]
    src += ['\tdef _inner():', '\t\twhile True:']
    base = '\t\t\t'
    src += [base + '\t' * i + 'if True:' for i in range(depth)]
    src += [base + line for line in lines]
    compile('\n'.join(src) + '\n', '<hook>', 'exec')


def validate_registry(patch_config, patches_dir=None):
    """Returns a list of problems found in `patch_config` (empty list means valid)."""
    import os
    import re
    problems = []
    seen_ids, seen_markers = set(), set()
    for index, patch in enumerate(patch_config):
        pid = patch.get('id', '#%d' % index)
        missing = [f for f in _REQUIRED_FIELDS if f not in patch]
        if missing:
            problems.append('%s: missing fields %s' % (pid, ', '.join(missing)))
            continue
        if pid in seen_ids:
            problems.append('%s: duplicate id' % pid)
        seen_ids.add(pid)
        key = (patch['target_file'], patch['marker'])
        if key in seen_markers:
            problems.append('%s: duplicate marker %s in %s' % (pid, patch['marker'], patch['target_file']))
        seen_markers.add(key)
        if not re.match(r'^# WIZARD_[A-Z0-9_]+_v\d+$', patch['marker']):
            problems.append('%s: marker %r does not match "# WIZARD_<ADDON>_<NAME>_v<X>"' % (pid, patch['marker']))
        if patch['category'] not in _CATEGORIES:
            problems.append('%s: unknown category %r' % (pid, patch['category']))
        if patch['priority'] not in _PRIORITIES:
            problems.append('%s: unknown priority %r' % (pid, patch['priority']))
        if patch['action'] not in _ACTIONS:
            problems.append('%s: unknown action %r' % (pid, patch['action']))
        if not isinstance(patch['enabled'], bool):
            problems.append('%s: enabled must be a bool' % pid)
        hook = patch['hook']
        if not isinstance(hook, str) or not hook.endswith('\n'):
            problems.append('%s: hook must be a string ending in a newline' % pid)
            continue
        try:
            _compile_hook(hook)
        except SyntaxError as exc:
            problems.append('%s: hook does not compile (%s, line %s)' % (pid, exc.msg, exc.lineno))
        if patches_dir:
            names = set(re.findall(r'(?:^|;)\s*(?:from|import)\s+(\w+)', hook, re.M)) - _NON_PATCH_MODULES
            for name in sorted(names):
                if not os.path.isfile(os.path.join(patches_dir, name + '.py')):
                    problems.append('%s: patch module %s.py not found in %s' % (pid, name, patches_dir))
    return problems
