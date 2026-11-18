"""Maintenance -> "התקנה מחדש של הבילד נקי" (mode=clean_reinstall).

Wipes Kodi's home (install.wipe(), the same engine as Fresh Start) and lets the build provision itself again at the next
start: fresh_install.begin() writes the "install started" marker, startup.py finds no `kodipovil.provisioned` marker and runs
the whole install (repositories, add-ons through Kodi's native InstallAddon, config pack, finalize).

Order, and why:
  1. master profile only (the markers live in the active profile's userdata; the child-profile gate of the router already
     keeps a kids profile out) and nothing playing;
  2. PRE-FLIGHT before anything is deleted: config.json loads and every file the Wizard itself downloads answers (our index,
     both repository zips, the config pack). A device that is offline would otherwise be left with no add-ons and no way to
     install them. (The wipe removes the repository add-ons too; the next start bootstraps them again.);
  3. the user picks what to keep, then confirms twice;
  4. QUIESCE: the POV update watcher stands down (BUSY_PROPERTY), Kodi's add-on auto-update is switched off by install.wipe()
     (it restores it afterwards), and the skin is left alone on purpose (install.wipe() -> skin_to_default is a documented
     no-op: the wipe ends with a hard kill, and the next start provisions the default skin);
  5. install.wipe() with the keep flags set in MEMORY only (never written to the Wizard's settings): POV / Account Manager /
     Umbrella are excluded as whole folders by install.wipe(), that is how the existing keep switches work;
  6. clean-up the wipe cannot do: POV leftovers, Addons DB rows of add-ons that are gone, the Wizard's own state that survives
     in its (excluded) folder: the retained config pack, the build settings and every `kodipovil.*` marker;
  7. fresh_install.begin(), Kodi's add-on updates back to what they were, and the same hard kill as Fresh Start.
"""
import glob
import os
import shutil
import sqlite3
from urllib.request import ProxyHandler, Request, build_opener

import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs.common.config import CONFIG

BUSY_PROPERTY = 'kodipovil.clean_reinstall'  # Window(10000): background repairs stand down while it is set
POV_ID = 'plugin.video.pov'
WIZARD_ID = 'plugin.program.kodipovilwizard'
PREFLIGHT_TIMEOUT = 8

# The Wizard's "keep" switches; a clean reinstall decides every one of them itself.
_KEEP_FLAGS = ('KEEPACCTMGR', 'KEEPPOVDATA', 'KEEPUMBRELLADATA', 'KEEPFENTASTICDATA', 'KEEPFAVS', 'KEEPSOURCES',
               'KEEPPROFILES', 'KEEPPLAYERCORE', 'KEEPADVANCED', 'KEEPGUISETTINGS', 'KEEPREPOS', 'KEEPSUPER',
               'KEEPWHITELIST', 'KEEPADDONS33DB')

# (label, the flags it turns on). Nothing is pre-selected: a clean reinstall is clean unless the user says otherwise.
KEEP_CHOICES = (
    ('חשבונות והיסטוריה: Account Manager ו-POV (נשמרים התוספים והנתונים שלהם)', ('KEEPACCTMGR', 'KEEPPOVDATA')),
    ('נתוני Umbrella (נשמרים התוסף והנתונים שלו)', ('KEEPUMBRELLADATA',)),
    ('רשימת הפרופילים (profiles.xml; התוכן של כל פרופיל נבנה מחדש)', ('KEEPPROFILES',)),
)

POV_LEFTOVERS = ('povil_dropped_*.json', 'povil_dropped_*.json.lock.db', 'povil-*.json', 'navigator.before-*.db')


# ---------------------------------------------------------------- guards and pre-flight

def _master_profile():
    return os.path.normcase(os.path.normpath(CONFIG.PROFILE)) == os.path.normcase(os.path.normpath(CONFIG.MASTERPROFILE))


def _reachable(url):
    """True when the first byte of the file comes back (a ranged GET: a signed release URL rejects a HEAD)."""
    try:
        request = Request(url, headers={'User-Agent': getattr(CONFIG, 'USER_AGENT', 'Kodi POV IL'), 'Range': 'bytes=0-0'})
        with build_opener(ProxyHandler({})).open(request, timeout=PREFLIGHT_TIMEOUT) as response:
            response.read(1)
        return True
    except Exception as exc:
        logging.log('[Clean reinstall] unreachable {0}: {1}'.format(url, type(exc).__name__), level=xbmc.LOGWARNING)
        return False


def preflight_targets(info):
    """(files the new install downloads as (label, url), problems found in config.json itself).

    The Wizard downloads our index, both repository zips (the wipe removes them; the next start bootstraps them again) and the
    config pack, which must carry a checksum because the pack is verified before it is applied. The add-ons themselves are
    installed by Kodi from the repositories (the upstream-health workflow watches the third-party indexes).
    """
    from resources.libs.provisioning import repo_bootstrap
    base = CONFIG.REPO_BASE_URL
    targets = [('addons.xml', base + '/addons.xml')]
    for addon_id in repo_bootstrap.REPOSITORY_IDS:
        targets.append((addon_id, '{0}/{1}-latest.zip'.format(base, addon_id)))
    problems = []
    if info.get('url') and info.get('sha256'):
        targets.append(('config', info['url']))
    else:
        problems.append('config')
    return targets, problems


def preflight():
    """'' when everything the new install needs can be reached, else the reason (Hebrew), before anything is deleted."""
    from resources.libs import config_apply
    info = config_apply.load_config_info()
    if not info:
        return 'לא ניתן להגיע לשרת העדכונים של הבילד. יש לבדוק את החיבור לאינטרנט ולנסות שוב.'
    targets, problems = preflight_targets(info)
    if problems:
        return 'מידע ההתקנה של הבילד אינו שלם: {0}'.format(', '.join(problems))
    progress = xbmcgui.DialogProgress()
    progress.create(CONFIG.ADDONTITLE, 'בודק שכל הרכיבים זמינים להורדה...')
    try:
        for index, (label, url) in enumerate(targets):
            progress.update(int(100 * index / max(len(targets), 1)), label)
            if progress.iscanceled():
                return 'הבדיקה בוטלה.'
            if not _reachable(url):
                return 'הרכיב {0} אינו זמין להורדה כרגע. לא נמחק דבר. יש לנסות שוב מאוחר יותר.'.format(label)
    finally:
        progress.close()
    return ''


# ---------------------------------------------------------------- the questions

def ask_keep():
    """The flags to keep (a set of KEEP* names), or None when the user backs out."""
    chosen = xbmcgui.Dialog().multiselect('מה לשמור? (ללא בחירה: התקנה נקייה לגמרי)', [label for label, _ in KEEP_CHOICES])
    if chosen is None:
        return None
    flags = set()
    for index in chosen:
        flags.update(KEEP_CHOICES[index][1])
    return flags


def _confirmed(keep):
    dialog = xbmcgui.Dialog()
    kept = [label.split(':')[0].split(' (')[0] for label, flags in KEEP_CHOICES if set(flags) <= keep]
    summary = 'יישמר: {0}'.format(', '.join(kept)) if kept else 'לא יישמר דבר.'
    if not dialog.yesno(CONFIG.ADDONTITLE,
                        'כל התוספים, ההגדרות והנתונים של Kodi יימחקו, והבילד יותקן מחדש מאפס.[CR]{0}[CR]ההתקנה '
                        'מחדש דורשת חיבור לאינטרנט ולוקחת מספר דקות. להמשיך?'.format(summary),
                        nolabel='ביטול', yeslabel='המשך'):
        return False
    return dialog.yesno(CONFIG.ADDONTITLE,
                        '[COLOR red][B]אישור אחרון[/B][/COLOR][CR]הפעולה אינה הפיכה. Kodi ייסגר בסיומה, וההתקנה תתחיל '
                        'בהפעלה הבאה. למחוק ולהתקין מחדש?',
                        nolabel='ביטול', yeslabel='מחק והתקן מחדש')


# ---------------------------------------------------------------- the clean-up after the wipe

def _remove_pov_leftovers(keep_accounts):
    """POV's snapshots, dropped-show caches and navigator backups. Kept with the accounts, gone otherwise."""
    if keep_accounts:
        return 0
    removed = 0
    base = os.path.join(CONFIG.ADDON_DATA, POV_ID)
    for folder in (base, os.path.join(base, 'databases')):
        for pattern in POV_LEFTOVERS:
            for path in glob.glob(os.path.join(folder, pattern)):
                try:
                    os.remove(path)
                    removed += 1
                except OSError as exc:
                    logging.log('[Clean reinstall] could not remove {0}: {1}'.format(path, exc), level=xbmc.LOGWARNING)
    return removed


def stale_addon_ids(installed_ids, exists):
    """The rows of the Addons DB whose add-on is gone: not under special://home/addons nor the system add-ons."""
    return [i for i in installed_ids if i != WIZARD_ID and not exists(i)]


def _purge_addons_db():
    """Delete the rows of removed add-ons from Kodi's Addons DB, so a reinstalled add-on does not inherit its old state."""
    from resources.libs import db
    name = db.latest_db('Addons')
    path = os.path.join(CONFIG.DATABASE, name) if name else ''
    if not path or not os.path.isfile(path):
        return 0
    connection = sqlite3.connect(path, timeout=5)
    try:
        ids = [row[0] for row in connection.execute('SELECT addonID FROM installed')]
        stale = stale_addon_ids(ids, lambda i: os.path.isdir(os.path.join(CONFIG.ADDONS, i)) or
                                os.path.isdir(os.path.join(CONFIG.KODIADDONS, i)))
        connection.executemany('DELETE FROM installed WHERE addonID = ?', [(i,) for i in stale])
        connection.commit()
        return len(stale)
    finally:
        connection.close()


def _reset_state():
    """What survives the wipe inside the Wizard's own (excluded) folder, and the markers that say "installed"."""
    for path in glob.glob(os.path.join(CONFIG.USERDATA, 'kodipovil.*')):
        try:
            os.remove(path)
        except OSError as exc:
            logging.log('[Clean reinstall] could not remove marker {0}: {1}'.format(path, exc), level=xbmc.LOGWARNING)
    try:
        from resources.libs import config_apply
        shutil.rmtree(config_apply._retained_dir(), ignore_errors=True)  # the new install retains a fresh verified pack
    except Exception as exc:
        logging.log('[Clean reinstall] retained config pack: {0}'.format(exc), level=xbmc.LOGWARNING)
    CONFIG.clear_setting('build')
    CONFIG.clear_setting('install')
    for key, value in (('installed', 'false'), ('config_applied_version', ''), ('fresh_build_auto_install_done', ''),
                       ('notedismiss', 'false'), ('build_skin_switch_notifcation_dismiss', 'false')):
        CONFIG.set_setting(key, value)


def _step(label, function, *args):
    """Run one clean-up step; a failure is logged and never stops the reinstall (the wipe has already happened)."""
    try:
        result = function(*args)
        logging.log('[Clean reinstall] {0}: {1}'.format(label, result), level=xbmc.LOGINFO)
    except Exception as exc:
        logging.log('[Clean reinstall] {0} failed: {1}'.format(label, exc), level=xbmc.LOGERROR)


# ---------------------------------------------------------------- the flow

def _wipe_with(keep):
    """install.wipe() with exactly these keep flags (in memory only); returns its result.

    install.wipe() appends its exclusions to the shared CONFIG.EXCLUDES list, so an earlier Fresh Start that was cancelled in
    this session would leave folders protected: the list is reset to the Wizard alone for the wipe and put back afterwards.
    """
    from resources.libs import install
    saved = {name: getattr(CONFIG, name, 'false') for name in _KEEP_FLAGS}
    excludes = list(CONFIG.EXCLUDES)
    for name in _KEEP_FLAGS:
        setattr(CONFIG, name, 'true' if name in keep else 'false')
    CONFIG.EXCLUDES[:] = [WIZARD_ID]
    try:
        return install.wipe()
    finally:
        CONFIG.EXCLUDES[:] = excludes
        for name, value in saved.items():
            setattr(CONFIG, name, value)


def run():
    dialog = xbmcgui.Dialog()
    if not _master_profile():
        dialog.ok(CONFIG.ADDONTITLE, 'התקנה מחדש נקייה זמינה רק במשתמש הראשי.')
        return False
    if xbmc.Player().isPlaying():
        dialog.ok(CONFIG.ADDONTITLE, 'יש לעצור את ההפעלה לפני התקנה מחדש.')
        return False
    reason = preflight()
    if reason:
        dialog.ok(CONFIG.ADDONTITLE, reason)
        return False
    keep = ask_keep()
    if keep is None or not _confirmed(keep):
        return False

    from resources.libs import fresh_install
    from resources.libs import update
    window = xbmcgui.Window(10000)
    window.setProperty(BUSY_PROPERTY, '1')
    try:
        logging.log('[Clean reinstall] start, keeping {0}'.format(sorted(keep) or 'nothing'), level=xbmc.LOGINFO)
        try:
            finished = _wipe_with(keep)
        except Exception as exc:
            logging.log('[Clean reinstall] wipe failed: {0}'.format(exc), level=xbmc.LOGERROR)
            finished = False
        keep_accounts = 'KEEPPOVDATA' in keep
        _step('POV leftovers removed', _remove_pov_leftovers, keep_accounts)
        _step('stale Addons DB rows removed', _purge_addons_db)
        _step('state reset', _reset_state)
        # Even a wipe the user cancelled half way needs the install to resume at the next start.
        fresh_install.begin()
        update.addon_updates('reset')
    finally:
        window.clearProperty(BUSY_PROPERTY)
    if finished is False:
        dialog.ok(CONFIG.ADDONTITLE, 'המחיקה הופסקה באמצע. Kodi ייסגר, והבילד יושלם בהפעלה הבאה.')
    else:
        dialog.ok(CONFIG.ADDONTITLE, 'הניקוי הסתיים. Kodi ייסגר עכשיו, והבילד יותקן מחדש בהפעלה הבאה '
                                     '(נדרש חיבור לאינטרנט).')
    tools.kill_kodi(over=True)
    return True
