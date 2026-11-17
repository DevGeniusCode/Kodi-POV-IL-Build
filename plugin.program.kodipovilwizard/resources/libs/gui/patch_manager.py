"""The Patch Manager: the patch registry grouped by host add-on, then by category.

    mode=patchmanager                       add-on folders (+ the General / Engine folder)
    mode=patchmanager&addon=<id>            a small group lists its patches, a big one (> SPLIT_OVER) its category folders
    mode=patchmanager&addon=<id>&cat=<c>    the patches of one category
    mode=patchmanager&addon=_general        master switch, engine run, health report, reset
    mode=togglepatch&name=<patch id>        flip one patch (the engine runs for that patch only)
    mode=patchbulk&addon=<id>[&cat=<c>]&state=on|off   enable / disable a whole folder (one scoped engine run)
    mode=patchengine&op=master|run|health|reset         the General / Engine panel actions

The data contract is the one the PatchEngine reads (patch_engine.py): the Wizard setting `patch_enabled_<patch id>`
("true" / "false"; empty = the registry's `enabled`). Nothing here edits the registry.
"""
import os

import xbmc
import xbmcaddon
import xbmcgui

from resources.libs.common import directory
from resources.libs.common import logging
from resources.libs.common.config import CONFIG

SETTING_PREFIX = 'patch_enabled_'
MASTER_SETTING = '_pov_patching_off'  # read by host_repairs/flow/pov_steps (the runtime POV patchers)
GENERAL = '_general'
DEFAULT_ADDON_ID = 'plugin.video.pov'
SPLIT_OVER = 25

# Hebrew folder names; an add-on / category the tables do not know shows its raw id and sorts after the known ones.
ADDON_LABELS = (
    ('plugin.video.pov', 'POV'),
    ('plugin.video.umbrella', 'מטריה'),
    ('plugin.video.idanplus', 'עידן פלוס'),
    ('skin.arctic.fuse.3', 'Arctic Fuse 3'),
)
CATEGORY_LABELS = (
    ('ui', 'ממשק משתמש'),
    ('ux', 'חוויית משתמש'),
    ('performance', 'ביצועים'),
    ('reliability', 'יציבות ואמינות'),
    ('crash_guard', 'מניעת קריסות'),
    ('translation', 'עברית ותרגום'),
    ('subtitles', 'כתוביות'),
    ('parental_control', 'בקרת הורים'),
    ('account_manager', 'חשבונות ושירותים'),
    ('diagnostics', 'אבחון'),
)
GENERAL_LABEL = 'כללי / מנוע'

_ON = '[COLOR springgreen][ON][/COLOR]'
_OFF = '[COLOR red][OFF][/COLOR]'


# ---------------------------------------------------------------------------
# Pure helpers (no Kodi calls; unit-testable under stubs)
# ---------------------------------------------------------------------------
def entry_addon(entry):
    return entry.get('addon_id') or DEFAULT_ADDON_ID


def effective_state(entry, override):
    """The state the engine applies: the user's override, else the registry default."""
    if override == 'true':
        return True
    if override == 'false':
        return False
    return bool(entry.get('enabled', True))


def _ordered(groups, known):
    order = [key for key, _ in known if key in groups]
    order += sorted(key for key in groups if key not in dict(known))
    return [(key, groups[key]) for key in order]


def group_by_addon(entries):
    groups = {}
    for entry in entries:
        groups.setdefault(entry_addon(entry), []).append(entry)
    return _ordered(groups, ADDON_LABELS)


def group_by_category(entries):
    groups = {}
    for entry in entries:
        groups.setdefault(entry.get('category') or 'other', []).append(entry)
    return _ordered(groups, CATEGORY_LABELS)


def label_of(table, key):
    return dict(table).get(key, key)


def needs_split(entries):
    return len(entries) > SPLIT_OVER


def scope(entries, addon_id, category=None):
    chosen = [e for e in entries if entry_addon(e) == addon_id]
    if category:
        chosen = [e for e in chosen if (e.get('category') or 'other') == category]
    return chosen


# ---------------------------------------------------------------------------
# Registry / settings access
# ---------------------------------------------------------------------------
def registry():
    try:
        from resources.libs.patches.patches_config import PATCH_CONFIG
    except Exception:
        return []
    return [e for e in PATCH_CONFIG if isinstance(e, dict) and e.get('id')]


def _overrides(entries, settings=None):
    settings = settings or xbmcaddon.Addon()
    return {e['id']: settings.getSetting(SETTING_PREFIX + e['id']) for e in entries}


def _count_on(entries, overrides):
    return sum(1 for e in entries if effective_state(e, overrides.get(e['id'], '')))


def _installed(addon_id):
    try:
        return bool(xbmc.getCondVisibility('System.HasAddon({0})'.format(addon_id)))
    except Exception:
        return True


def _run_engine(**scope_args):
    from resources.libs.patch_engine import PatchEngine
    return PatchEngine().run(**scope_args)


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------
def show(params):
    addon_id = params.get('addon')
    if not addon_id:
        return _root()
    if addon_id == GENERAL:
        return _general()
    entries = [e for e in registry() if entry_addon(e) == addon_id]
    category = params.get('cat')
    if not category and needs_split(entries):
        return _categories(addon_id, entries)
    return _patches(addon_id, scope(entries, addon_id, category), category)


def _root():
    entries = registry()
    if not entries:
        directory.add_file('לא הוגדרו טלאים.', themeit=CONFIG.THEME3)
        return
    overrides = _overrides(entries)
    for addon_id, group in group_by_addon(entries):
        name = label_of(ADDON_LABELS, addon_id)
        label = '[B]{0}[/B]  [COLOR gray]({1}/{2} פעילים)[/COLOR]'.format(name, _count_on(group, overrides), len(group))
        if not _installed(addon_id):
            label += '  [COLOR gray](לא מותקן)[/COLOR]'
        directory.add_dir(label, {'mode': 'patchmanager', 'addon': addon_id}, icon=CONFIG.ICONMAINT,
                          themeit=CONFIG.THEME1, description='{0}\n{1} טלאים'.format(addon_id, len(group)))
    directory.add_separator()
    directory.add_dir('[B]{0}[/B]'.format(GENERAL_LABEL), {'mode': 'patchmanager', 'addon': GENERAL},
                      icon=CONFIG.ICONSETTINGS, themeit=CONFIG.THEME1,
                      description='מתג ראשי, הפעלת המנוע, דוח בריאות ואיפוס העדפות')


def _bulk_rows(addon_id, category=None):
    base = {'mode': 'patchbulk', 'addon': addon_id}
    if category:
        base['cat'] = category
    directory.add_file('[COLOR springgreen]הפעל את כל הטלאים בתיקייה[/COLOR]', dict(base, state='on'),
                       icon=CONFIG.ICONMAINT, themeit=CONFIG.THEME3)
    directory.add_file('[COLOR red]כבה את כל הטלאים בתיקייה[/COLOR]', dict(base, state='off'),
                       icon=CONFIG.ICONMAINT, themeit=CONFIG.THEME3)
    directory.add_separator()


def _categories(addon_id, entries):
    overrides = _overrides(entries)
    _bulk_rows(addon_id)
    for category, group in group_by_category(entries):
        label = '[B]{0}[/B]  [COLOR gray]({1}/{2} פעילים)[/COLOR]'.format(
            label_of(CATEGORY_LABELS, category), _count_on(group, overrides), len(group))
        directory.add_dir(label, {'mode': 'patchmanager', 'addon': addon_id, 'cat': category},
                          icon=CONFIG.ICONMAINT, themeit=CONFIG.THEME1)


def _patches(addon_id, entries, category):
    if not entries:
        directory.add_file('אין טלאים בתיקייה הזו.', themeit=CONFIG.THEME3)
        return
    overrides = _overrides(entries)
    _bulk_rows(addon_id, category)
    for entry in sorted(entries, key=lambda e: (e.get('name') or e['id']).lower()):
        on = effective_state(entry, overrides.get(entry['id'], ''))
        name = entry.get('name') or entry['id']
        plot = '{0}\nTarget: {1}/{2}'.format(entry.get('description', ''), entry_addon(entry), entry.get('target_file', ''))
        directory.add_file('{0} {1}'.format(_ON if on else _OFF, name), {'mode': 'togglepatch', 'name': entry['id']},
                           description=plot, icon=CONFIG.ICONMAINT, themeit=CONFIG.THEME1)


def _general():
    entries = registry()
    overrides = _overrides(entries)
    manual = sum(1 for value in overrides.values() if value in ('true', 'false'))
    directory.add_file('{0} טלאים, {1} פעילים, {2} עקיפות ידניות'.format(
        len(entries), _count_on(entries, overrides), manual), themeit=CONFIG.THEME3)
    directory.add_separator()
    master = CONFIG.get_setting(MASTER_SETTING) == 'true'
    directory.add_file('להשאיר את POV בדיוק כפי שהיוצר שלו פרסם: {0}'.format(_ON if master else _OFF),
                       {'mode': 'patchengine', 'op': 'master'}, icon=CONFIG.ICONSETTINGS, themeit=CONFIG.THEME1,
                       description='מכבה את מתקני POV של הוויזרד בהפעלה הבאה. ראו את ההגדרה בהגדרות הוויזרד.')
    directory.add_file('הפעל את מנוע הטלאים עכשיו', {'mode': 'patchengine', 'op': 'run'},
                       icon=CONFIG.ICONMAINT, themeit=CONFIG.THEME1)
    directory.add_file('דוח בריאות המתקנים', {'mode': 'patchengine', 'op': 'health'},
                       icon=CONFIG.ICONMAINT, themeit=CONFIG.THEME1)
    directory.add_file('אפס את כל ההעדפות לברירת המחדל', {'mode': 'patchengine', 'op': 'reset'},
                       icon=CONFIG.ICONMAINT, themeit=CONFIG.THEME1)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _notify(text):
    xbmcgui.Dialog().notification(CONFIG.ADDONTITLE, text, xbmcgui.NOTIFICATION_INFO, 3000)


def toggle(patch_id):
    """Flip one patch and run the engine for that patch only."""
    entry = next((e for e in registry() if e['id'] == patch_id), None)
    settings = xbmcaddon.Addon()
    current = effective_state(entry or {}, settings.getSetting(SETTING_PREFIX + patch_id))
    settings.setSetting(SETTING_PREFIX + patch_id, 'false' if current else 'true')
    stats = _run_engine(only_ids=[patch_id])
    if stats.get('failed') or stats.get('anchor_missing'):
        _notify('הטלאי לא הוחל במלואו, ראו את הלוג')
    xbmc.executebuiltin('Container.Refresh()')


def bulk(params):
    addon_id, category, state = params.get('addon'), params.get('cat'), params.get('state')
    if state not in ('on', 'off') or not addon_id:
        return
    entries = scope(registry(), addon_id, category)
    if not entries:
        return
    word = 'להפעיל' if state == 'on' else 'לכבות'
    text = 'לבצע: {0} {1} טלאים?'.format(word, len(entries))
    if state == 'on':
        off_by_default = sum(1 for e in entries if not e.get('enabled', True))
        if off_by_default:
            text += '\n({0} מהם כבויים כברירת מחדל בבילד)'.format(off_by_default)
    if not xbmcgui.Dialog().yesno(CONFIG.ADDONTITLE, text):
        return
    settings = xbmcaddon.Addon()
    for entry in entries:
        settings.setSetting(SETTING_PREFIX + entry['id'], 'true' if state == 'on' else 'false')
    stats = _run_engine(only_ids=[e['id'] for e in entries])
    _notify('עודכנו {0} טלאים'.format(len(entries)) + (' (חלקם נכשלו)' if stats.get('failed') else ''))
    xbmc.executebuiltin('Container.Refresh()')


def _summary(stats):
    rows = (('applied', 'הוחלו'), ('upgraded', 'שודרגו'), ('skipped_current', 'מעודכנים'), ('removed', 'הוסרו'),
            ('skipped_disabled', 'כבויים'), ('optional_absent', 'תוסף אופציונלי לא מותקן'), ('missing', 'חסרים בדיסק'),
            ('anchor_missing', 'עוגן לא נמצא'), ('failed', 'נכשלו'), ('malformed', 'רשומות פגומות'))
    return '\n'.join('{0}: {1}'.format(label, stats.get(key, 0)) for key, label in rows if stats.get(key, 0))


def engine_action(params):
    op = params.get('op')
    if op == 'master':
        CONFIG.set_setting(MASTER_SETTING, 'false' if CONFIG.get_setting(MASTER_SETTING) == 'true' else 'true')
        xbmc.executebuiltin('Container.Refresh()')
    elif op == 'run':
        stats = _run_engine()
        xbmcgui.Dialog().textviewer('מנוע הטלאים', _summary(stats) or 'אין שינויים')
    elif op == 'health':
        _health_report()
    elif op == 'reset':
        _reset_overrides()


def _health_report():
    text = ''
    try:
        from resources.libs.host_repairs import patcher_health
        path = patcher_health._report_path()
        if path and os.path.isfile(path):
            with open(path, encoding='utf-8') as handle:
                text = handle.read()
    except Exception as err:
        logging.log('[PatchManager] health report unavailable: {0}'.format(err), level=xbmc.LOGWARNING)
    xbmcgui.Dialog().textviewer('דוח בריאות המתקנים', text or 'אין עדיין דוח. הוא נכתב בהפעלה של קודי.')


def _reset_overrides():
    entries = registry()
    settings = xbmcaddon.Addon()
    changed = [e['id'] for e in entries if settings.getSetting(SETTING_PREFIX + e['id']) in ('true', 'false')]
    if not changed:
        _notify('אין העדפות ידניות לאפס')
        return
    if not xbmcgui.Dialog().yesno(CONFIG.ADDONTITLE, 'לאפס {0} העדפות ולחזור לברירת המחדל של הבילד?'.format(len(changed))):
        return
    for patch_id in changed:
        settings.setSetting(SETTING_PREFIX + patch_id, '')
    stats = _run_engine(only_ids=changed)
    _notify('אופסו {0} העדפות'.format(len(changed)) + (' (חלקן נכשלו)' if stats.get('failed') else ''))
    xbmc.executebuiltin('Container.Refresh()')
