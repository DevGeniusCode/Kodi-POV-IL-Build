"""Start-up pass: repositories present, Kodi asked to check them, newer config pack applied, missing add-ons healed."""
import xbmc

from resources.libs.common import logging


def run_startup_sync():
    try:
        from resources.libs.provisioning import provisioner
        provisioner.run_update_check(background=True)   # repositories, UpdateAddonRepos, newer config pack
        provisioner.heal_missing_addons()               # strict HasAddon enforcement -> install missing

    except Exception as _sync_err:
        logging.log("[BuildSync] Startup sync failed: {0}".format(_sync_err),
                    level=xbmc.LOGERROR)
