"""Start-up OTA pass: manifest version bumps, then strict HasAddon healing."""
import xbmc

from resources.libs.common import logging


def run_startup_ota():
    try:
        from resources.libs.modular_updater import ModularUpdater
        _mu = ModularUpdater(background=True)
        _mu.run_update_check()        # version bumps for installed addons + config
        _mu.heal_missing_addons()     # strict HasAddon enforcement -> install missing

    except Exception as _modular_err:
        logging.log("[ModularUpdater] Startup check failed: {0}".format(_modular_err),
                    level=xbmc.LOGERROR)
