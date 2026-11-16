"""Shared on-demand pack engine: download, extract and register zip packs
(NOX skin, Umbrella), plus the pack/addon on-disk and version helpers."""
import os

import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs.common.config import CONFIG
from resources.libs import db
from resources.libs import extract
from resources.libs.downloader import Downloader


NOX_PACK_BASE_URL = "https://github.com/MoranTheKing/Kodi-POV-IL/raw/main/dist"


def _addon_on_disk(addon_id):
    """True when addons/<id>/addon.xml is really there. Never raises."""
    try:
        import xbmcvfs
        return xbmcvfs.exists(xbmcvfs.translatePath(
            'special://home/addons/{0}/addon.xml'.format(addon_id)))
    except Exception:
        return False


def _af3_register_pack_in_db(pack):
    """Register + enable a pack's addons in Kodi's Addons DB. Safe to
    call repeatedly (INSERT OR IGNORE + UPDATE enabled). It needs no pack
    zip on disk, which is what lets it heal users whose files were already
    extracted by an older code path.

    IT DOES NEED THE FILES. This used to register the whole static
    addon_ids list unconditionally, trusting that the "already current"
    fast path above (_af3_pack_current) only skips the download after
    checking every file was really there. It didn't -- it checked ONE
    sentinel file. A device with only the sentinel present and the rest of
    the pack's addons entirely absent got every one of them written into
    Kodi's DB as installed and enabled, this function reported success, the
    caller wrote the "done" marker, and the device was never corrected
    again. Telling Kodi an add-on exists when it does not is worse than
    telling it nothing -- Kodi then resolves dependencies against a lie.

    So: register what is actually on disk, and REPORT FAILURE for anything
    that is not, so the caller does not mark the job done."""
    wanted = list(pack.get('addon_ids') or ())
    present = [addon_id for addon_id in wanted if _addon_on_disk(addon_id)]
    absent = [addon_id for addon_id in wanted if addon_id not in present]
    if absent:
        logging.log(
            'DEBUG | _ensure_packs_installed | '
            'NOT registering {0} -- not on disk: {1}'.format(
                pack['name'], absent))
    if not present:
        return False
    try:
        db.addon_database(present, 1, True)
        logging.log(
            'DEBUG | _ensure_packs_installed | '
            'DB enabled: {0}'.format(present))
        return not absent
    except Exception as e:
        logging.log(
            'DEBUG | _ensure_packs_installed | '
            'DB enable failed for {0}: {1}'.format(
                pack['name'], str(e)))
        return False


def _af3_pack_installed(sentinel):
    try:
        import xbmcvfs
        return xbmcvfs.exists(xbmcvfs.translatePath(sentinel))
    except Exception:
        return False


def _af3_read_addon_version(addon_xml):
    try:
        import re
        import xbmcvfs
        path = xbmcvfs.translatePath(addon_xml)
        with open(path, 'r', encoding='utf-8') as fh:
            text = fh.read(600)
        # IMPORTANT: skip the XML declaration's version (<?xml version="1.0"?>)
        # and read the <addon> tag's version instead. Anchoring on 'version='
        # alone matches the declaration first, so every addon looked like
        # "1.0" -- which made the deps-pack version gate think a stale
        # jurialmunkey 0.2.28 was already current (1.0 >= 0.2.35) and skip the
        # upgrade. Search from the '<addon' tag so we get the real version.
        anchor = text.find('<addon')
        search_from = anchor if anchor >= 0 else 0
        match = re.search(r'\bversion="([^"]+)"', text[search_from:])
        return match.group(1) if match else ''
    except Exception:
        return ''


def _version_tuple(ver):
    """Best-effort numeric version tuple for comparison. Non-numeric
    parts degrade to 0 so a malformed version never raises."""
    parts = []
    for chunk in str(ver).split('.'):
        num = ''.join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def _af3_pack_current(pack):
    if not _af3_pack_installed(pack['sentinel']):
        return False
    # THE SENTINEL VOUCHES FOR ITSELF, NOT FOR THE WHOLE PACK. Checking only
    # the sentinel file and skipping the download for everything else is how
    # a device ends up with the sentinel present, the rest of the pack's
    # addons absent, and nothing that will ever repair it: registration
    # (_af3_register_pack_in_db) correctly refuses to vouch for files that
    # aren't there, but this fast path would then keep skipping the
    # re-download on every subsequent boot for the exact same reason.
    # Correctly-reported failure that never self-corrects is still never
    # self-correcting -- so every addon_id in the pack has to actually be on
    # disk before we call it current.
    missing = [addon_id for addon_id in (pack.get('addon_ids') or ())
               if not _addon_on_disk(addon_id)]
    if missing:
        logging.log(
            'skin/content pack is incomplete, forcing reinstall: {0} '
            'missing={1}'.format(pack['name'], missing))
        return False
    expected = pack.get('expected_version')
    if not expected:
        return True
    current = _af3_read_addon_version(pack['sentinel'])
    # "Current" means installed >= expected. A newer installed version is
    # fine (don't force a needless downgrade/re-extract); only an OLDER or
    # missing version triggers a reinstall. Falls back to exact-match if
    # either version can't be parsed.
    try:
        if _version_tuple(current) >= _version_tuple(expected):
            return True
    except Exception:
        if current == expected:
            return True
    logging.log(
        'skin pack version too old, forcing reinstall: {0} '
        'current={1} expected>={2}'.format(
            pack['name'], current or 'missing', expected))
    return False


def _ensure_packs_installed(packs, downloading_label, ready_label):
    """Download + extract any supplemental skin packs that aren't already
    on disk. Returns True if all packs are present at the end; False if any
    failed. Best-effort: shows a progress dialog with per-pack labels;
    on failure, surfaces a Hebrew notification and bails.

    Reuses the wizard's existing Downloader + extract.all machinery
    -- the same code that powers quick_update and Fresh Install --
    so progress / cancel / error reporting all behave the same way.
    Generic over a packs list so NOX (and any future on-demand
    skin) share one battle-tested install path, including the critical
    Addons-DB register step that prevents the silent Estuary fallback."""
    try:
        all_ok = True
        dialog_progress = xbmcgui.DialogProgress()
        dialog_progress.create(CONFIG.ADDONTITLE, downloading_label)

        for i, pack in enumerate(packs, start=1):
            if dialog_progress.iscanceled():
                dialog_progress.close()
                return False

            label = '[COLOR {0}][B]{1}/{2}[/B][/COLOR] - {3}'.format(
                CONFIG.COLOR1, i, len(packs), pack['name'])
            dialog_progress.update(
                int((i - 1) / len(packs) * 100), label)

            if _af3_pack_current(pack):
                # Files already on disk (this user switched to AF3
                # before, possibly with the old DB-less code). Skip the
                # 50-60 MB re-download/extract -- but STILL re-register
                # in the Addons DB so the retroactive fix lands. This is
                # the path that heals everyone already stuck on the
                # Estuary fallback.
                logging.log(
                    'skin pack files present, skipping download but '
                    're-registering in DB: {0}'.format(pack['name']))
                if not _af3_register_pack_in_db(pack):
                    all_ok = False
                continue

            lib = os.path.join(CONFIG.PACKAGES, pack['filename'])
            try:
                if os.path.exists(lib):
                    os.remove(lib)
            except Exception:
                pass

            response = tools.open_url(pack['url'], check=True)
            if not response:
                dialog_progress.close()
                logging.log_notify(
                    CONFIG.ADDONTITLE,
                    '[COLOR {0}]חבילת הסקין לא זמינה: {1}[/COLOR]'.format(
                        CONFIG.COLOR2, pack['name']))
                logging.log(
                    'DEBUG | _ensure_packs_installed | '
                    '{0} not reachable: {1}'.format(
                        pack['name'], pack['url']))
                return False

            try:
                Downloader().download(pack['url'], lib)
            except Exception as e:
                dialog_progress.close()
                logging.log(
                    'DEBUG | _ensure_packs_installed | '
                    'download failed for {0}: {1}'.format(
                        pack['name'], str(e)))
                logging.log_notify(
                    CONFIG.ADDONTITLE,
                    '[COLOR {0}]כשל בהורדת חבילת הסקין![/COLOR]'.format(
                        CONFIG.COLOR2))
                return False

            xbmc.sleep(300)
            if not os.path.exists(lib) or os.path.getsize(lib) == 0:
                dialog_progress.close()
                logging.log_notify(
                    CONFIG.ADDONTITLE,
                    '[COLOR {0}]חבילת הסקין ריקה: {1}[/COLOR]'.format(
                        CONFIG.COLOR2, pack['name']))
                return False

            extract_title = (
                '[COLOR {0}][B]מתקין:[/B][/COLOR] [COLOR {1}]{2}[/COLOR]'
                .format(CONFIG.COLOR2, CONFIG.COLOR1, pack['name']))
            try:
                extract.all(lib, CONFIG.HOME, title=extract_title)
            except Exception as e:
                dialog_progress.close()
                logging.log(
                    'DEBUG | _ensure_packs_installed | '
                    'extract failed for {0}: {1}'.format(
                        pack['name'], str(e)))
                logging.log_notify(
                    CONFIG.ADDONTITLE,
                    '[COLOR {0}]כשל בחילוץ חבילת הסקין![/COLOR]'.format(
                        CONFIG.COLOR2))
                all_ok = False

            # CRITICAL: register every addon in this pack in Kodi's
            # Addons DB and mark it enabled. extract.all only writes
            # files to disk -- it does NOT tell Kodi the addons exist.
            # Without this, AF3 and its dependencies (skinvariables,
            # texturemaker, tmdbhelper, the two resource.* addons, the
            # weather icons, the cjk font) sit on disk but are 'not
            # installed' from Kodi's POV. When the skin is then set to
            # AF3, Kodi finds the dependencies unmet, refuses to load
            # the skin, and SILENTLY FALLS BACK TO skin.estuary -- the
            # "it says switched but I get the simple skin" bug. This
            # mirrors what quick_update / Fresh Install do after their
            # own extract.all calls.
            if not _af3_register_pack_in_db(pack):
                all_ok = False

            try:
                os.remove(lib)
            except Exception:
                pass

        # Force Kodi to scan the freshly-extracted addon folders so the
        # dependency graph is satisfiable in THIS session as well as
        # after the restart. Without the scan, the addon manager's
        # in-memory view is stale and the skin load on next boot can
        # still race the DB read on some Android builds.
        try:
            xbmc.executebuiltin('UpdateLocalAddons')
            xbmc.sleep(2500)
        except Exception:
            pass

        dialog_progress.update(100, ready_label)
        xbmc.sleep(800)
        dialog_progress.close()
        return all_ok

    except Exception as e:
        try:
            dialog_progress.close()
        except Exception:
            pass
        logging.log(
            'DEBUG | _ensure_packs_installed | '
            'unexpected exception: {0}'.format(str(e)))
        logging.log_notify(
            CONFIG.ADDONTITLE,
            '[COLOR {0}]שגיאה בהתקנת חבילת סקין[/COLOR]'.format(
                CONFIG.COLOR2))
        return False
