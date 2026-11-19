"""Start-up check: low free memory or a bloated cache -> a Hebrew question that offers to clear the cache.

Field background (24 Oct 2026, LG webOS TV, POV 6.10.22): a full device left POV's providerscache.db damaged and every
search ended in "No Results". Two start-up steps already cover the disk side: storage_guard (old add-on zips, a Hebrew notice
under 200 MB free) and pov_cache_repair (a damaged POV cache is deleted). This one covers what they do not: a device short of
RAM, and caches that grew large without being damaged, and it asks before it clears.

Runs from boot/maintenance_pass after the GUI is up (the dialog needs a parent window). It asks at most once per
ASK_EVERY_S, and again only after DECLINED_EVERY_S when the answer was no; a clean check is repeated daily. It stays silent
while something plays, in a child profile, when the setting `startup_cache_check` is off, and when the device is below
storage_guard's free-space line (that guard already speaks, and one dialog per boot is enough).

Triggers (see assess):
  * the cache is CACHE_BIG_BYTES or more: the Wizard's clearable caches (clear.get_cache_size) plus POV's four cache databases;
  * free RAM is under LOW_RAM_MB AND at least MIN_OFFER_BYTES can be cleared (clearing a disk cache does not hand RAM back
    directly, so with nothing to clear there is no point in asking).

"Clear" is the Maintenance menu's Clear Cache (clear.clear_cache(over=True): the build's kept folders are left alone) plus POV's
results caches (providerscache.db, debridcache.db: what POV's own "clear external scrapers cache" empties; POV recreates them).
"""
import os
import re
import time

import xbmc
import xbmcgui
import xbmcvfs

from resources.libs.common import logging
from resources.libs.common.config import CONFIG

MB = 1024 * 1024
LOW_RAM_MB = 100
CACHE_BIG_BYTES = 400 * MB
MIN_OFFER_BYTES = 50 * MB
CHECK_EVERY_S = 24 * 3600
ASK_EVERY_S = 3 * 24 * 3600
DECLINED_EVERY_S = 7 * 24 * 3600

SETTING_ENABLED = 'startup_cache_check'
SETTING_DUE = 'resource_check_due'
POV_RESULT_DBS = ('providerscache.db', 'debridcache.db')

_UNITS = {'': 1.0 / MB, 'B': 1.0 / MB, 'KB': 1.0 / 1024, 'MB': 1.0, 'GB': 1024.0, 'TB': 1024.0 * 1024}
_LABEL = re.compile(r'^\s*(\d+(?:[.,]\d+)?)\s*([KMGT]?B)?\s*$', re.I)


def parse_mb(label):
    """Kodi's memory labels ('812MB', '1.5 GB', '2048 MB') as whole megabytes; None when it is not one."""
    m = _LABEL.match(label or '')
    if not m:
        return None
    return int(float(m.group(1).replace(',', '.')) * _UNITS[(m.group(2) or '').upper()])


def free_ram_mb():
    try:
        return parse_mb(xbmc.getInfoLabel('System.Memory(free)'))
    except Exception:
        return None


def _pov_folder():
    return xbmcvfs.translatePath('special://profile/addon_data/plugin.video.pov/')


def pov_cache_bytes(folder=None):
    from resources.libs import pov_cache_repair
    folder = folder or _pov_folder()
    total = 0
    for name in pov_cache_repair.CACHE_DBS:
        try:
            total += os.path.getsize(os.path.join(folder, name))
        except OSError:
            pass
    return total


def cache_bytes():
    from resources.libs import clear
    try:
        general = int(clear.get_cache_size() or 0)
    except Exception as e:
        logging.log('[Resource check] cache size unavailable: {0}'.format(e), level=xbmc.LOGWARNING)
        general = 0
    return general + pov_cache_bytes()


def assess(free_mb, cache, disk_free=None, disk_line=None):
    """'cache', 'ram', 'both' or None. Pure. `disk_free` under `disk_line` means storage_guard owns this boot's message."""
    if disk_free is not None and disk_line is not None and disk_free < disk_line:
        return None
    big = cache >= CACHE_BIG_BYTES
    low = free_mb is not None and free_mb < LOW_RAM_MB and cache >= MIN_OFFER_BYTES
    if big and low:
        return 'both'
    return 'cache' if big else ('ram' if low else None)


def message(reason, free_mb, cache):
    """Three short lines: the build's dialog cuts a fourth one (see storage_guard.warn)."""
    size = int(round(cache / float(MB)))
    if reason == 'ram':
        first = 'הזיכרון הפנוי במכשיר נמוך (נשארו {0}MB).'.format(free_mb)
        second = 'ניקוי המטמון והפעלה מחדש של Kodi עשויים לעזור.'
    else:
        ram = ' והזיכרון הפנוי נמוך ({0}MB)'.format(free_mb) if reason == 'both' else ''
        first = 'המטמון תופס כ-{0}MB{1}.'.format(size, ram)
        second = 'מטמון גדול עלול להאט את POV ולגרום לתקלות.'
    return '{0}\n{1}\nלנקות את המטמון עכשיו?'.format(first, second)


def _remove_pov_results_caches(folder=None):
    folder = folder or _pov_folder()
    removed = 0
    for name in POV_RESULT_DBS:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError as e:
            logging.log('[Resource check] {0} could not be removed: {1}'.format(name, e), level=xbmc.LOGWARNING)
            continue
        for suffix in ('-journal', '-wal', '-shm'):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
    return removed


def clear_now():
    """The same Clear Cache as the Maintenance menu, plus POV's results caches. Returns the bytes freed (0 if unknown)."""
    before = cache_bytes()
    from resources.libs import clear
    clear.clear_cache(over=True)
    _remove_pov_results_caches()
    return max(0, before - cache_bytes())


def _due(now):
    try:
        return now >= float(CONFIG.get_setting(SETTING_DUE) or 0)
    except (TypeError, ValueError):
        return True


def _set_due(now, seconds):
    CONFIG.set_setting(SETTING_DUE, str(int(now + seconds)))


def run_at_startup(now=None):
    """Never raises. Returns what it did: None (nothing to say), 'cleared' or 'declined'."""
    try:
        if CONFIG.get_setting(SETTING_ENABLED) == 'false':
            return None
        from resources.libs.profiles import hooks as profile_hooks
        if not profile_hooks.notifications_allowed():
            return None
        try:
            if xbmc.Player().isPlaying():
                return None
        except Exception:
            pass
        now = time.time() if now is None else now
        if not _due(now):
            return None
        from resources.libs import storage_guard
        started = time.time()
        free_mb = free_ram_mb()
        cache = cache_bytes()
        reason = assess(free_mb, cache, storage_guard.free_bytes(CONFIG.HOME), storage_guard.LOW_BYTES)
        logging.log('[Resource check] free RAM {0} MB, cache {1} MB -> {2} ({3:.1f}s)'.format(
            free_mb, int(cache / MB), reason or 'nothing to offer', time.time() - started), level=xbmc.LOGINFO)
        if reason is None:
            _set_due(now, CHECK_EVERY_S)
            return None
        if xbmcgui.Dialog().yesno(CONFIG.ADDONTITLE, message(reason, free_mb, cache),
                                  nolabel='לא עכשיו', yeslabel='נקה מטמון'):
            freed = clear_now()
            _set_due(now, ASK_EVERY_S)
            xbmcgui.Dialog().notification(
                CONFIG.ADDONTITLE, 'המטמון נוקה: שוחררו כ-{0}MB'.format(int(freed / MB)), time=6000)
            return 'cleared'
        _set_due(now, DECLINED_EVERY_S)
        return 'declined'
    except Exception as e:
        logging.log('[Resource check] failed: {0}: {1}'.format(type(e).__name__, e), level=xbmc.LOGWARNING)
        return None
