"""Start-up housekeeping before the OTA pass: auto-clean, storage guard and
POV cache repair. Each step is independently guarded."""
import xbmc

from resources.libs.common import logging


def run_startup_maintenance():
    try:
        from resources.libs import clear as _clear
        _clear.auto_clean_startup()
    except Exception as _clean_error:
        logging.log('[Auto Clean Up] failed: {0}: {1}'.format(
            type(_clean_error).__name__, _clean_error), level=xbmc.LOGWARNING)
    # Room for the updates below: a full LG TV (field log, 24 Oct 2026) failed
    # every update and had POV answer every search with no results.
    try:
        from resources.libs import storage_guard
        storage_guard.run_at_startup()
    except Exception as _storage_error:
        logging.log('[Storage] start-up check failed: ' + type(_storage_error).__name__,
                    level=xbmc.LOGWARNING)
    # A full disk can leave a POV cache damaged; a damaged results cache makes
    # every search end in "No Results" (field report, LG webOS TV, 24 Oct 2026).
    try:
        from resources.libs import pov_cache_repair
        pov_cache_repair.run_at_startup()
    except Exception as _cache_error:
        logging.log('[POV caches] start-up check failed: {0}: {1}'.format(
            type(_cache_error).__name__, _cache_error), level=xbmc.LOGWARNING)
