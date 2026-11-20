"""Which add-ons the build needs, and the state around them. Kodi does the installing and the updating.

Since the repository migration the Wizard no longer downloads, extracts or dependency-resolves add-ons (that was the retired
ModularUpdater + HeadlessInstaller). Two repository add-ons, installed by provisioning/repo_bootstrap.py, tell Kodi where our
add-ons (repository.povil) and the third-party content add-ons (repository.povil.upstream) live. From then on:

  * installs   -> Kodi's own InstallAddon(<id>): Kodi resolves the add-on and its whole dependency tree from the repositories
                  (install_native below; a watchdog accepts only the Yes/No prompt that builtin shows);
  * updates    -> Kodi's native updater (the Wizard only asks it to check at start-up: UpdateAddonRepos);
  * userdata   -> the Wizard: the versioned config pack (config_apply), merged value by value, never overwriting user settings;
  * patches    -> the PatchEngine (host_repairs), re-applied after any add-on update (host_repairs/flow/addon_update_watch.py).

What the user SEES during an install is reported through an optional `view` (gui/install_progress.py: the branded window) and the
shared ProvisionProgress `progress` tracker (the top-right banner). Both are optional and every call to them is guarded, so a
display problem can never break an install. The view protocol is four methods: plan(items), item_state(id, state, status),
item_progress(id, pct), set_stage(text).

This module holds the id lists, the .provisioned marker, the native install, the fresh-install run, the start-up check and
the healing pass.
"""
import os
import threading
import time

import xbmc
import xbmcgui

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs import config_apply
from resources.libs.provisioning import addon_state
from resources.libs.provisioning import config_retry
from resources.libs.provisioning import repo_bootstrap
from resources.libs.skins import skin_catalog

# Third-party content add-ons, provisioned by a fresh install and healed at every start. script.module.acctmgr comes from
# Zaxxon709 through our filtered index in repository.povil.upstream; the others come from repository.povil.upstream or Kodi's
# official repository.
#
# plugin.video.youtube is in BOTH: Kodi's official repository and the Umbrella index inside repository.povil.upstream (a copy with an
# "a" version suffix). Kodi installs the highest version of an id across the repositories, so it comes from whichever is newer; on a
# device checked on 20 Nov 2026 that was repository.povil.upstream (7.4.4a against Kodi's 7.4.4). The Wizard does not choose.
#
# plugin.video.otaku and plugin.video.umbrella are NOT here: they are OPTIONAL, installed on demand from the Wizard's "optional
# add-ons and skins" screen (addons/otaku.py, addons/umbrella.py). Keeping them out of this list also keeps heal_missing_addons()
# from putting them back after the user removed them. Their baked settings are still seeded by the config pack.
PROVISION_IDS = [
    'script.module.acctmgr',            # <- repository.povil.upstream (Zaxxon709, filtered index; its zips stay on their server)
    'plugin.video.pov',                 # <- repository.povil.upstream (kodifitzwell; patched at runtime)
    'plugin.video.idanplus',            # <- repository.povil.upstream (Fishenzon)
    'plugin.video.youtube',             # <- the higher version of Kodi's official repository and repository.povil.upstream (Umbrella index copy)
    'resource.language.he_il',          # <- Kodi official repository (Hebrew language pack)
    'script.xbmc.unpausejumpback',      # <- Kodi official repository (unpause jumpback)
]

CORE_PROVISION_IDS = (
    'script.module.acctmgr',
    'plugin.video.pov',
    'plugin.video.idanplus',
    'plugin.video.youtube',
    'resource.language.he_il',
)

# Our own add-ons the build cannot work without (all served by repository.povil). The default skin is added by fresh_ids().
# script.module.autocompletion and the other libraries come in as dependencies.
OWN_PROVISION_IDS = (
    'plugin.program.autocompletion',
    'plugin.program.orderfavourites-hebrew',
    'script.fentastic.helper',
    'service.subtitles.kodipovilai',
)


def fresh_ids():
    """Everything a fresh install puts on the device."""
    return list(OWN_PROVISION_IDS) + [skin_catalog.default_skin()] + list(PROVISION_IDS)


def heal_ids():
    """What the start-up healing pass keeps present (the skin is chosen by the user, so it is not healed)."""
    return list(OWN_PROVISION_IDS) + list(PROVISION_IDS)


def required_ids():
    """What must be installed and enabled for a fresh install to count as complete."""
    return list(OWN_PROVISION_IDS) + [skin_catalog.default_skin()] + list(CORE_PROVISION_IDS)


# ---- What the user sees ------------------------------------------------------------------------------------------------
DISPLAY_NAMES = {
    'repository.povil': 'מאגר הבילד',
    'repository.povil.upstream': 'מאגר תוספי התוכן',
    'plugin.video.pov': 'POV',
    'plugin.video.idanplus': 'עידן פלוס',
    'plugin.video.youtube': 'YouTube',
    'plugin.video.umbrella': 'Umbrella',
    'plugin.video.otaku': 'Otaku (אנימה)',
    'script.module.cocoscrapers': 'CocoScrapers',
    'script.module.acctmgr': 'מנהל חשבונות',
    'resource.language.he_il': 'שפה עברית',
    'script.xbmc.unpausejumpback': 'חזרה לאחור בהמשך הצפייה',
    'plugin.program.autocompletion': 'השלמה אוטומטית',
    'plugin.program.orderfavourites-hebrew': 'מועדפים ואייקונים',
    'script.fentastic.helper': 'עזר לסקין FENtastic',
    'service.subtitles.kodipovilai': 'כתוביות בעברית (AI)',
}

CONFIG_ROW = '__config__'
FINALIZE_ROW = '__finalize__'
STEP_NAMES = {CONFIG_ROW: 'הגדרות הבילד', FINALIZE_ROW: 'מסך הבית, מדיה וטלאים'}

STAGE_REPOS = 'מתקין את מאגרי הבילד'
STAGE_CONTENT = 'מתקין תוספים ({0}/{1}): {2}'
STAGE_CONFIG = 'מחיל את הגדרות הבילד'
STAGE_CHECK = 'בודק שהכול הותקן'
STAGE_FINALIZE = 'מסיים: מדיה, טלאים ומסך הבית'

# An install has no measurable progress of its own (Kodi owns it), so the share of the current add-on creeps toward its slot
# asymptotically while Kodi works and snaps to the next slot when it lands. SLOT_SECONDS is the typical install time.
SLOT_SECONDS = 20.0


def display_name(addon_id):
    """The Hebrew name shown for an add-on id (the skin catalog names the skins; anything else falls back to the id)."""
    if addon_id in DISPLAY_NAMES:
        return DISPLAY_NAMES[addon_id]
    if addon_id in STEP_NAMES:
        return STEP_NAMES[addon_id]
    try:
        skin = skin_catalog.find(addon_id)
        if skin is not None:
            return skin.name
    except Exception:
        pass
    return addon_id


def _view(view, method, *args):
    """Call a method of the view; a display problem can never break an install."""
    if view is None:
        return
    try:
        getattr(view, method)(*args)
    except Exception:
        pass


def mark_step(view, step_id, state):
    """Set the state ('active' / 'done' / 'failed') of one row of the install window (CONFIG_ROW, FINALIZE_ROW, an add-on id)."""
    _view(view, 'item_state', step_id, state)


def set_stage(progress, view, text):
    """The stage line of the window and the message of the top-right banner."""
    _track(progress, 'set_message', text)
    _view(view, 'set_stage', text)


# ---- Provisioning state marker ---------------------------------------------------------------------------------------
# A persistent file written to userdata ONLY when a fresh install has run all the way to the end (every required add-on
# present AND the config pack applied). startup.py uses it to tell a COMPLETED setup apart from one the user force-closed
# mid-provisioning, so an interrupted setup is resumed instead of being left half-built forever.
def provision_marker_path():
    return os.path.join(CONFIG.USERDATA, 'kodipovil.provisioned')


def is_provisioned():
    try:
        return os.path.exists(provision_marker_path())
    except Exception:
        return False


def mark_provisioned(version=''):
    try:
        with open(provision_marker_path(), 'w') as fh:
            fh.write(str(version or ''))
        logging.log("[Provisioning] wrote .provisioned marker (v{0})".format(version),
                    level=xbmc.LOGINFO)
    except Exception as e:
        logging.log("[Provisioning] failed writing .provisioned marker: {0}".format(e),
                    level=xbmc.LOGERROR)


def clear_provisioned():
    try:
        os.remove(provision_marker_path())
    except Exception:
        pass


# ---- Native install --------------------------------------------------------------------------------------------------
def _confirm_dependency_dialogs(stop, monitor):
    """Accept ONLY Kodi's Yes/No dialog (window 10100, control 11 = Yes, fixed by Kodi core regardless of skin): the
    InstallAddon builtin asks "install <add-on>?" and, when it has dependencies, asks again. Never closes any other dialog."""
    while not stop.is_set():
        try:
            if xbmcgui.getCurrentWindowDialogId() == 10100:
                xbmc.executebuiltin('SendClick(11)')
                if monitor.waitForAbort(0.8):
                    return
                continue
        except Exception:
            pass
        if monitor.waitForAbort(0.3):
            return


def _install_one(addon_id, timeout, monitor, on_wait=None):
    """InstallAddon(<id>) and wait for Kodi to report it installed. The wait is extended (bounded) while Kodi's own progress
    dialog is still visible. on_wait(elapsed_seconds) is called about once a second. True once the add-on is present."""
    started = time.time()
    logging.log("[Provisioning] native install {0}".format(addon_id), level=xbmc.LOGINFO)
    xbmc.executebuiltin('InstallAddon({0})'.format(addon_id))
    deadline = started + int(timeout)
    hard_deadline = started + max(int(timeout), 240)
    while time.time() < deadline:
        if monitor.waitForAbort(1):
            return False
        if addon_state.has_addon(addon_id):
            logging.log("[Provisioning] native install {0} landed after {1:.0f}s".format(
                addon_id, time.time() - started), level=xbmc.LOGINFO)
            return True
        if on_wait is not None:
            try:
                on_wait(time.time() - started)
            except Exception:
                pass
        if (xbmc.getCondVisibility('Window.IsActive(extendedprogressdialog)') and
                deadline - time.time() < 5 and deadline < hard_deadline):
            deadline = min(deadline + 10, hard_deadline)
    logging.log("[Provisioning] native install did not confirm {0} within {1}s".format(
        addon_id, timeout), level=xbmc.LOGWARNING)
    return False


def install_native(ids, per_addon_timeout=240, progress=None, view=None, ticker=None):
    """Install add-ons through Kodi's own InstallAddon. Returns the ids that are STILL missing afterwards.

    Kodi resolves each add-on and its dependency tree from the repositories (repository.povil, repository.povil.upstream, Kodi's
    official one). An add-on that is on disk but disabled is only enabled, never reinstalled. A second attempt follows a repository
    refresh (right after the bootstrap Kodi may not have loaded the index yet). Idempotent.

    progress(position, total, addon_id), when given, is called before each install. ticker(position, total, addon_id, elapsed)
    is called about once a second while Kodi installs one. view: see the module docstring; each add-on's row goes waiting ->
    installing (with a small bar) -> installed / failed, and add-ons that are already there show "already installed"."""
    ids = list(ids)
    monitor = xbmc.Monitor()
    pending = []
    for addon_id in ids:
        if addon_state.has_addon(addon_id):
            _view(view, 'item_state', addon_id, 'present')
            continue
        if addon_state.on_disk(addon_id):
            addon_state.enable_addon(addon_id)
            if addon_state.has_addon(addon_id):
                _view(view, 'item_state', addon_id, 'present')
                continue
        pending.append(addon_id)
    if not pending:
        return []

    xbmc.executebuiltin('UpdateAddonRepos', True)
    if monitor.waitForAbort(2):
        return pending

    stop = threading.Event()
    watcher = threading.Thread(target=_confirm_dependency_dialogs, args=(stop, monitor))
    watcher.daemon = True
    watcher.start()
    try:
        for position, addon_id in enumerate(pending):
            if monitor.abortRequested():
                break
            _view(view, 'item_state', addon_id, 'active')
            if progress is not None:
                try:
                    progress(position, len(pending), addon_id)
                except Exception:
                    pass

            def _wait(elapsed, _p=position, _a=addon_id):
                _view(view, 'item_progress', _a, int(100 * elapsed / (elapsed + SLOT_SECONDS)))
                if ticker is not None:
                    ticker(_p, len(pending), _a, elapsed)

            landed = _install_one(addon_id, per_addon_timeout, monitor, _wait)
            if not landed and not monitor.abortRequested():
                xbmc.executebuiltin('UpdateAddonRepos', True)
                if not monitor.waitForAbort(5):
                    landed = _install_one(addon_id, min(int(per_addon_timeout), 90), monitor, _wait)
            _view(view, 'item_state', addon_id, 'done' if landed else 'failed')
    finally:
        stop.set()
        watcher.join(5)
    return [a for a in ids if not addon_state.has_addon(a)]


def provision(ids=None, per_addon_timeout=60, progress=None, view=None):
    """Make sure the repositories exist, then install `ids` (default PROVISION_IDS) natively. True when all are present.

    Used by the optional add-ons, the skin installs, Account Manager and the healing pass. With a `view` (the branded install
    window) it plans one row per add-on and reports the stage."""
    ids = list(ids) if ids is not None else list(PROVISION_IDS)
    logging.log("[Provisioning] provisioning {0}".format(ids), level=xbmc.LOGINFO)
    _view(view, 'plan', [(i, display_name(i)) for i in ids])
    _view(view, 'set_stage', STAGE_REPOS)
    if not repo_bootstrap.ensure_repositories():
        logging.log("[Provisioning] repositories not ready; trying the install anyway", level=xbmc.LOGWARNING)

    def _step(position, total, addon_id):
        _view(view, 'set_stage', STAGE_CONTENT.format(position + 1, total, display_name(addon_id)))
        if progress is not None:
            progress(position, total, addon_id)

    missing = install_native(ids, per_addon_timeout=per_addon_timeout, progress=_step, view=view)
    _view(view, 'set_stage', STAGE_CHECK if missing else '')
    if missing:
        logging.log("[Provisioning] not provisioned this pass: {0}".format(missing), level=xbmc.LOGWARNING)
    return not missing


def heal_missing_addons():
    """Start-up healing: install whatever the build needs and is missing (a content add-on that timed out during a previous
    provisioning pass, one that was removed by accident). Skins are not healed: the user picks them. Silent: no window."""
    missing = [a for a in heal_ids() if not addon_state.has_addon(a)]
    if not missing:
        return
    logging.log("[Heal] Missing add-ons -> installing: {0}".format(missing), level=xbmc.LOGWARNING)
    try:
        provision(missing, per_addon_timeout=120)
    except Exception as e:
        logging.log("[Heal] provisioning heal failed: {0}".format(e), level=xbmc.LOGERROR)


# ---- Fresh install ---------------------------------------------------------------------------------------------------
def _track(progress, method, *args):
    """Report to the shared banner tracker; a progress problem can never break an install."""
    if progress is None:
        return
    try:
        getattr(progress, method)(*args)
    except Exception:
        pass


def _fresh_install_issues(info):
    """What is still missing from a fresh install (empty list = complete)."""
    issues = []
    for addon_id in required_ids():
        if not addon_state.has_addon(addon_id):
            issues.append('{0} (not installed)'.format(addon_id))
    version = (info or {}).get('config_version')
    applied = CONFIG.get_setting('config_applied_version')
    if not version or applied != version:
        issues.append('config ({0}, expected {1})'.format(applied or 'missing', version or 'unspecified'))
    return issues


def run_fresh_install(progress=None, view=None):
    """Hydrate a clean (or half-installed) device: repositories, every add-on of the build, the config pack, then verify.

    Returns (ok, issues). Idempotent: present add-ons are skipped and the config pack applies once, so a resumed run only does
    the missing work. The caller finishes through fresh_install.finalize() (media, patches, live defaults, home verification).
    The .provisioned marker is written here, and only when everything required is present and the config pack is applied.

    The user sees, in order: the repositories, every add-on (with its name, N/M and a bar), the build settings and, driven by
    the caller, the final touches. The banner percentage follows the phases (repos / content / config / finalize)."""
    monitor = xbmc.Monitor()
    tools.ensure_folders(CONFIG.PACKAGES)

    repo_ids = list(repo_bootstrap.REPOSITORY_IDS)
    rows = ([(i, display_name(i)) for i in repo_ids] + [(i, display_name(i)) for i in fresh_ids()] +
            [(CONFIG_ROW, display_name(CONFIG_ROW)), (FINALIZE_ROW, display_name(FINALIZE_ROW))])
    _view(view, 'plan', rows)

    set_stage(progress, view, STAGE_REPOS)
    _track(progress, 'begin_indeterminate', 'repos')
    for repo_id in repo_ids:
        _view(view, 'item_state', repo_id, 'active')
    repos_ok = repo_bootstrap.ensure_repositories()
    for repo_id in repo_ids:
        _view(view, 'item_state', repo_id, 'done' if repos_ok else 'failed')
    _track(progress, 'complete', 'repos')
    if not repos_ok:
        return False, ['repositories']

    def _step(position, total, addon_id):
        set_stage(progress, view, STAGE_CONTENT.format(position + 1, total, display_name(addon_id)))
        _track(progress, 'set_fraction', 'content', position / float(max(total, 1)))

    def _tick(position, total, addon_id, elapsed):
        slot = elapsed / (elapsed + SLOT_SECONDS)
        _track(progress, 'set_fraction', 'content', (position + slot) / float(max(total, 1)))

    missing = install_native(fresh_ids(), per_addon_timeout=240, progress=_step, view=view, ticker=_tick)
    _track(progress, 'complete', 'content')
    optional_missing = [a for a in missing if a not in required_ids()]
    if optional_missing:
        logging.log("[Provisioning] not installed, healed on a later start: {0}".format(optional_missing),
                    level=xbmc.LOGWARNING)
    if monitor.abortRequested():
        return False, ['interrupted']

    set_stage(progress, view, STAGE_CONFIG)
    _view(view, 'item_state', CONFIG_ROW, 'active')
    info = config_apply.load_config_info()
    if not info:
        _view(view, 'item_state', CONFIG_ROW, 'failed')
        return False, ['config unavailable']
    _track(progress, 'begin_indeterminate', 'config')
    try:
        config_apply.apply_config_pack(info, fresh=True, background=False, progress=progress)
    except Exception as e:
        logging.log("[Provisioning] config apply failed: {0}".format(e), level=xbmc.LOGERROR)
    _track(progress, 'complete', 'config')

    # Kodi may still be flipping the last add-ons to enabled: re-check for a few seconds, enabling what is on disk.
    set_stage(progress, view, STAGE_CHECK)
    issues = _fresh_install_issues(info)
    for _ in range(20):
        if not issues or monitor.abortRequested():
            break
        for addon_id in required_ids():
            if not addon_state.has_addon(addon_id):
                addon_state.enable_addon(addon_id)
        if monitor.waitForAbort(1):
            break
        issues = _fresh_install_issues(info)
    config_failed = any(i.startswith('config') for i in issues)
    _view(view, 'item_state', CONFIG_ROW, 'failed' if config_failed else 'done')
    if issues:
        logging.log('[Provisioning] incomplete; retry required: {0}'.format(', '.join(issues)), level=xbmc.LOGERROR)
        return False, issues
    if monitor.abortRequested():
        return False, ['interrupted']
    mark_provisioned(info.get('config_version') or CONFIG.BUILDVERSION_DEFAULT)
    return True, []


# ---- Start-up / manual check -----------------------------------------------------------------------------------------
def run_update_check(background=True):
    """The start-up pass and the manual "check for updates".

    Makes sure our repositories are installed, asks Kodi to check them (Kodi installs the add-on updates itself), and applies a
    newer config pack, waiting first for any add-on Kodi is installing or updating (config_apply). Returns True when the check
    itself could run.

    At start-up (background=True) the service never waits for Kodi's installs: when Kodi is downloading or unpacking add-ons
    (file-based check, no GUI call) the pack is handed to the host-repair runner (config_retry), which applies it once Kodi is
    quiet. Kodi stops a service it is about to replace and force-kills it after 5 s; see config_retry."""
    dialog = xbmcgui.Dialog()
    logging.log("[Update] check (background={0})".format(background), level=xbmc.LOGINFO)
    config_retry.clear()
    repo_bootstrap.ensure_repositories()
    xbmc.executebuiltin('UpdateAddonRepos')

    info = config_apply.load_config_info()
    if not info:
        if not background:
            dialog.ok(CONFIG.ADDONTITLE, "שגיאה בתקשורת מול שרת העדכונים.")
        return False

    if config_apply.config_pending(info):
        busy = addon_state.updates_in_flight() if background else []
        if busy:
            logging.log("[Update] config {0} deferred: Kodi is downloading / unpacking {1}; the runner applies it when Kodi is "
                        "quiet".format(info.get('config_version'), busy), level=xbmc.LOGINFO)
            config_retry.request()
            return True
        # Kodi's refresh was just requested: an update of a skin may start within seconds, so wait for it (a grace period).
        result = config_apply.apply_config_pack(info, fresh=False, background=background, wait_grace=WAIT_GRACE_SECONDS)
        if result.get('deferred'):
            logging.log("[Update] config {0} deferred: Kodi is still updating an add-on; {1}".format(
                info.get('config_version'), 'the runner retries when Kodi is quiet' if background else 'it is retried at the next start'),
                level=xbmc.LOGWARNING)
            if background:
                config_retry.request()
            else:
                dialog.ok(CONFIG.ADDONTITLE, "Kodi עדיין מעדכן תוספים. הגדרות הבילד יוחלו בהפעלה הבאה.")
            return True
        if result.get('skin_touched'):
            xbmc.executebuiltin('ReloadSkin()')
        if not background:
            dialog.ok(CONFIG.ADDONTITLE, "הגדרות הבילד עודכנו.")
        return True

    if not background:
        dialog.ok(CONFIG.ADDONTITLE, "הגדרות הבילד מעודכנות. Kodi מעדכן את התוספים אוטומטית ברקע.")
    return True


# How long a config apply waits after UpdateAddonRepos for an announced update to start (config_apply.wait_for_native_updates).
WAIT_GRACE_SECONDS = 30
