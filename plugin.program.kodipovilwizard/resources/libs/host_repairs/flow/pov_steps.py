# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""POV-targeted repair steps, plus the POV-patching master switch.

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.
"""
import os

try:
    import xbmc
except ImportError:
    xbmc = None

from resources.libs.host_repairs.flow.common import ADDON_ID, _translate_path


def _maybe_repair_addon_settings_integrity():
    """Recover torn source-stack settings before another add-on writes them."""
    try:
        from resources.libs.host_repairs import (addon_settings_integrity, kodi_utils,
                                   source_settings_reload)
    except Exception:
        return
    try:
        # If Kodi was killed inside our prior Umbrella/Coco reconstruction,
        # restore only the add-ons recorded by that cycle before doing any new
        # work. The record is written before the first disable.
        source_settings_reload.heal_interrupted_cycle()
        results = addon_settings_integrity.ensure_integrity()
        repaired = [item for item in results
                    if item.get('status', '').startswith('repaired_')]
        for item in results:
            status = item.get('status', '')
            if status in ('backup_failed', 'write_failed', 'too_large',
                          'healthy_snapshot_failed', 'error'):
                kodi_utils.log(
                    'settings recovery: {0}={1}; file left in place and the '
                    'next start will retry'.format(item.get('addon'), status),
                    level='WARNING')
            elif status == 'changed_before_install':
                kodi_utils.log(
                    'settings recovery: {0} changed while recovery was '
                    'preparing; the newer writer won and was left untouched'
                    .format(item.get('addon')), level='INFO')
        for item in repaired:
            # Counts and booleans only.  A debrid token must never enter kodi.log.
            kodi_utils.log(
                'settings recovery: {0}={1}, recovered {2} setting(s), '
                'account restored={3}'.format(
                    item.get('addon'), item.get('status'),
                    item.get('salvaged', 0),
                    bool(item.get('account_restored'))),
                level='WARNING')

        pov_repaired = any(
            item.get('addon') == 'plugin.video.pov'
            and item.get('status', '').startswith('repaired_')
            for item in results)
        if pov_repaired:
            # Kodi already tried to load the malformed file before this service
            # began, so its CAddon object still holds defaults.  The existing
            # idle-safe cycle makes it re-read the repaired values this session;
            # if the box never becomes quiet, the owed-cycle record retries at
            # the next start, where the file is already healthy.
            try:
                from resources.libs.host_repairs import pov_reload
                pov_reload.note_patched()
            except Exception:
                pass

        # Umbrella and Coco can also have been constructed before this service
        # replaced their malformed file. Reconstruct only a target whose file
        # was repaired, later and behind the same idle gate used by POV.
        source_settings_reload.note_repaired(
            item.get('addon') for item in repaired)

        repaired_ids = set(item.get('addon') for item in repaired)
        synced = addon_settings_integrity.sync_alldebrid_from_account_manager(
            skip_addons=repaired_ids)
        for item in synced:
            if item.get('failed'):
                kodi_utils.log(
                    'Account Manager AllDebrid re-sync did not persist for {0}'
                    .format(item.get('addon')), level='WARNING')
            elif item.get('changed'):
                kodi_utils.log(
                    'Account Manager AllDebrid re-synced to {0}'
                    .format(item.get('addon')), level='INFO')
    except Exception as exc:
        try:
            kodi_utils.log('settings integrity recovery failed: {0}'.format(exc),
                           level='WARNING')
        except Exception:
            pass


# ---------------------------------------------------------------------------
# One switch that stops this add-on touching plugin.video.pov at all.
#
# We rewrite about twenty of POV's own source files on every startup. That is a
# standing bet that POV's internals still look the way they did when each patch
# was written, and POV updates itself from its own repository whenever its
# author publishes -- so the bet can be lost at any time, on the user's device,
# with no warning and no error: the patch still applies, and something
# downstream quietly stops working. When that happens the first thing anyone
# needs is a way to find out whether it was us, in one step, without a rebuild
# and without guesswork.
#
# Turning this on makes every POV patcher a no-op from the next start. It does
# not undo edits already on disk -- but POV rewrites its own files whenever it
# updates, so reinstalling POV from its repository restores a clean copy
# immediately, and with this on it stays clean.
# ---------------------------------------------------------------------------
POV_PATCHING_OFF_SETTING = '_pov_patching_off'


_POV_SKIP_LOGGED = False


def _skip_pov_patchers():
    """True when POV patching is switched off. Says so once per start, so the
    reason a device is behaving differently is in its log."""
    try:
        from resources.libs.host_repairs import kodi_utils
        off = (kodi_utils.get_setting(POV_PATCHING_OFF_SETTING, '')
               or '').strip().lower() == 'true'
    except Exception:
        return False
    if not off:
        return False
    global _POV_SKIP_LOGGED
    if not _POV_SKIP_LOGGED:
        _POV_SKIP_LOGGED = True
        try:
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.log(
                'POV patching is switched OFF in settings -- leaving '
                'plugin.video.pov exactly as its own author shipped it. '
                'Reinstall POV from its repository to drop any edits already '
                'on disk.', level='WARNING')
        except Exception:
            pass
    return True


def _ensure_pov_enabled():
    """Switch POV back on if OUR OWN CYCLE left it off. Not otherwise.

    POV is THE content add-on: installed but disabled means every home row and
    every "My Movies/My Shows" tile is empty and nothing plays, on all skins.
    pov_reload retries inside its own cycle, but a cycle that is interrupted --
    the box is switched off mid-update, the process is killed -- leaves POV off
    with nothing to bring it back. This is that net.

    IT USED TO HEAL UNCONDITIONALLY, AND THAT WAS A SILENT SETTINGS CHANGE.
    "POV is off" has two causes and this could not tell them apart, so it
    treated the user's own choice as damage and undid it. Worse, it undid it
    invisibly and early: hot_reload's first act is to cycle this service, so a
    fresh main() -- and this function with it -- runs to completion before the
    wizard's own POV checks are ever reached. A user who switched POV off found
    it back on after any update, with nothing on screen and nothing in the log
    to say why. The wizard's _cycle_addon refuses to do exactly this, in as many
    words: "re-enabling something somebody turned off by hand is not ours to
    do". This now honours the same rule.

    So it acts only on evidence. pov_reload writes a record before it disables
    POV and clears it only once POV can be constructed again; that record, and
    the wizard's pending_enable list for the add-ons IT cycles, are the only
    things that make a disabled POV ours to fix.
    """
    if xbmc is None:
        return
    try:
        from resources.libs.host_repairs import pov_reload
        ours = pov_reload.cycle_left_pov_off()
    except Exception:
        ours = False
    if not ours:
        return
    try:
        import json as _json
        # WAIT FOR JSON-RPC. This runs early in startup, and a single
        # unanswered call used to be indistinguishable from "POV is fine" --
        # no exception, no log, no retry until the next full restart. The
        # wizard's own heal polls for readiness for the same reason.
        get = _json.dumps({
            'jsonrpc': '2.0', 'id': 1,
            'method': 'Addons.GetAddonDetails',
            'params': {'addonid': 'plugin.video.pov',
                       'properties': ['enabled']},
        })
        addon = {}
        monitor = xbmc.Monitor()
        for attempt in range(20):
            data = _json.loads(xbmc.executeJSONRPC(get) or '{}')
            addon = (data.get('result') or {}).get('addon') or {}
            if 'enabled' in addon:
                break
            if monitor.waitForAbort(0.5):
                return
        if 'enabled' not in addon:
            # Still no answer. The record stays, so the next start tries again.
            xbmc.log('[' + ADDON_ID + '] POV is recorded as left off by our '
                     'cycle, but Kodi is not answering yet; keeping the record',
                     level=xbmc.LOGWARNING)
            return
        if addon.get('enabled'):
            # Somebody already switched it back on. Nothing to do, and the
            # record has served its purpose.
            pov_reload.clear_cycle_record()
            return
        en = _json.dumps({
            'jsonrpc': '2.0', 'id': 1,
            'method': 'Addons.SetAddonEnabled',
            'params': {'addonid': 'plugin.video.pov', 'enabled': True},
        })
        xbmc.executeJSONRPC(en)
        data = _json.loads(xbmc.executeJSONRPC(get) or '{}')
        back = ((data.get('result') or {}).get('addon') or {}).get('enabled')
        if back:
            pov_reload.clear_cycle_record()
            xbmc.log('[' + ADDON_ID + '] re-enabled POV after an interrupted '
                     'cycle of ours', level=xbmc.LOGINFO)
        else:
            # KEEP THE RECORD ON A FAILED ENABLE. Clearing it here is how a
            # temporary problem becomes a permanent one: the evidence goes and
            # nothing ever tries again.
            xbmc.log('[' + ADDON_ID + '] POV would not switch back on; the '
                     'record stays for the next start', level=xbmc.LOGWARNING)
    except Exception:
        pass


def _maybe_default_pov_autoplay():
    """One-shot: set POV "Automatically Resume Playback" to Always, so picking
    up an in-progress item resumes from where you stopped (no resume/start-over
    prompt). Marker-gated; only flips settings still on POV's old default, so a
    later manual change sticks. Does NOT enable Auto Play -- the source/servers
    dialog must still appear so the user chooses the source. Touches ONLY the
    two auto_resume settings; never Trakt/debrid/anything else."""
    if xbmc is None:
        return
    try:
        from resources.libs.host_repairs import kodi_utils
        import xbmcaddon
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_pov_autoplay_default_v1', '') == '1':
            return
        try:
            pov = xbmcaddon.Addon('plugin.video.pov')
        except Exception:
            return  # POV not installed (standalone AI install) -> retry later
        def _flip(key, oldval, newval):
            try:
                if (pov.getSetting(key) or '').strip().lower() == oldval:
                    pov.setSetting(key, newval)
            except Exception:
                pass
        # Automatically Resume Playback: 0=Never, 1=Always, 2=Autoplay Only.
        # NOTE: we deliberately do NOT touch auto_play_* -- the source dialog
        # must keep showing so the user picks the source themselves.
        _flip('auto_resume_movie', '0', '1')
        _flip('auto_resume_episode', '0', '1')
        kodi_utils.set_setting('_pov_autoplay_default_v1', '1')
        kodi_utils.log('POV always-resume default applied (v1)', level='INFO')
    except Exception as e:
        try:
            kodi_utils.log('POV resume default migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_revert_pov_autoplay():
    """One-shot fix: an earlier build (0.2.158) wrongly turned POV Auto Play ON
    by default, which skipped the source/servers dialog even on first watch.
    Turn it back OFF so the dialog always shows. Marker-gated; sets the value
    back to POV's own default (false). Users who genuinely want Auto Play can
    re-enable it in POV settings."""
    if xbmc is None:
        return
    try:
        from resources.libs.host_repairs import kodi_utils
        import xbmcaddon
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_pov_autoplay_revert_v2', '') == '1':
            return
        try:
            pov = xbmcaddon.Addon('plugin.video.pov')
        except Exception:
            return
        for key in ('auto_play_movie', 'auto_play_episode'):
            try:
                if (pov.getSetting(key) or '').strip().lower() == 'true':
                    pov.setSetting(key, 'false')
            except Exception:
                pass
        kodi_utils.set_setting('_pov_autoplay_revert_v2', '1')
        kodi_utils.log('POV Auto Play reverted to off (v2)', level='INFO')
    except Exception as e:
        try:
            kodi_utils.log('POV autoplay revert failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_revert_pov_always_resume():
    """One-shot: undo our earlier always-resume override. A prior migration set
    POV "Automatically Resume Playback" to Always (auto_resume=1) for one-click
    continue -- but that makes POV resume even when the user explicitly picks
    "Play from start" from the context menu (it jumps back to the stop point).
    Set the two auto_resume settings back to POV's default (0 = ask), so the
    resume prompt appears AND "Play from start" really starts from 0. Marker-
    gated; only reverts a value still on OUR forced '1', so a later manual
    choice (e.g. a user who genuinely wants Always) sticks."""
    if xbmc is None:
        return
    try:
        from resources.libs.host_repairs import kodi_utils
        import xbmcaddon
    except Exception:
        return
    try:
        if kodi_utils.get_setting('_pov_resume_revert_v1', '') == '1':
            return
        try:
            pov = xbmcaddon.Addon('plugin.video.pov')
        except Exception:
            return  # POV not installed (standalone AI install) -> retry later
        for key in ('auto_resume_movie', 'auto_resume_episode'):
            try:
                if (pov.getSetting(key) or '').strip() == '1':
                    pov.setSetting(key, '0')
            except Exception:
                pass
        kodi_utils.set_setting('_pov_resume_revert_v1', '1')
        kodi_utils.log('POV always-resume reverted to ask '
                       '("Play from start" fix)', level='INFO')
    except Exception as e:
        try:
            kodi_utils.log('POV resume revert failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_install_pov_gemini_icon():
    """Put the subtitle add-on's gemini.png into POV's own media folder.

    Account Manager's service list (patches/pov_myservices: the "gemini-ai" row) draws its icon from POV's
    resources/skins/Default/media/gemini.png, a file POV does not ship. Idempotent (byte compare), atomic (temp file
    + replace), silent when POV, the subtitle add-on or the source file is missing, and off with the POV
    patching master switch. A POV update erases the copy, so flow/addon_update_watch runs this step again.
    Returns True when it wrote the file."""
    try:
        if _skip_pov_patchers():
            return False
        src = _translate_path('special://home/addons/service.subtitles.kodipovilai/resources/media/gemini.png')
        media = _translate_path('special://home/addons/plugin.video.pov/resources/skins/Default/media')
        if not src or not media or not os.path.isfile(src) or not os.path.isdir(media):
            return False
        dst = os.path.join(media, 'gemini.png')
        with open(src, 'rb') as fh:
            data = fh.read()
        if os.path.isfile(dst):
            with open(dst, 'rb') as fh:
                if fh.read() == data:
                    return False
        tmp = dst + '.tmp'
        with open(tmp, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, dst)
        try:
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.log('POV media: gemini.png installed for the Account Manager list', level='INFO')
        except Exception:
            pass
        return True
    except Exception:
        return False
