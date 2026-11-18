"""On-demand build skins: NOX / Onyx / Estuary / Arctic Fuse 3 install."""
import xbmc

from resources.libs.common import logging


#####################################################
# KODI-POV-IL - ON-DEMAND SKIN INSTALL
# The build's selectable skins other than FENtastic are installed ONLY when the user picks
# them from the Skin Manager / Switch Skin, so they never bloat the base build and are NEVER
# force-pushed to every device during fresh installs or background OTA:
#
#   * Arctic Fuse 3 (skin.arctic.fuse.3) -- installed NATIVELY via Kodi's
#     InstallAddon from repository.jurialmunkey (shipped in our manifest, so it
#     lands on every device for dependency resolution). Kodi pulls the skin +
#     ALL its dependencies (tmdbhelper, skinvariables, texturemaker,
#     script.module.jurialmunkey, fonts, studio/weather icons...) from upstream
#     at mutually-compatible versions, and they keep updating from their own
#     repos. This replaces the old four static "pack" zips (deps/skin/fonts/
#     studios) -- no hardcoded dist links, no version-pinned bundles.
#
#   * NOX, Onyx and Estuary -- installed from the verified modular manifest (ensure_modular_skin); an INSTALLED
#     on-demand skin is kept up to date by the background updater like any other add-on.

# NOX (skin.povil.nox) floor version for ensure_nox_installed.
NOX_SKIN_VERSION = '1.0.17'

AF3_SKIN_ID = 'skin.arctic.fuse.3'


def ensure_arctic_fuse_3_installed():
    """Install Arctic Fuse 3 ON DEMAND via Kodi's native InstallAddon.

    Replaces the old four static "pack" zips. The AF3 skin and ALL of its
    dependencies (script.module.jurialmunkey, plugin.video.themoviedb.helper,
    script.skinvariables, script.texturemaker, the fonts / studio / weather
    resource addons, ...) are resolved natively from repository.jurialmunkey
    (shipped in our manifest, so it is already on the device) plus the official
    Kodi repo, at mutually-compatible upstream versions. They then keep updating
    from their own repos -- no version-pinned bundle to maintain.

    Reuses the provisioning watchdog (auto-confirms the native dependency
    Yes/No dialog, closes stray first-run popups) so the install is hands-off.
    Returns True once AF3 is present. Idempotent: a no-op if already installed.
    """
    if xbmc.getCondVisibility('System.HasAddon({0})'.format(AF3_SKIN_ID)):
        return True
    try:
        from resources.libs.modular_updater import ModularUpdater
        # Longer timeout than the content-addon default: the AF3 skin + tmdbhelper
        # + font/studio resources are a sizeable native download.
        ModularUpdater(background=False).post_install_provisioning(
            per_addon_timeout=240, ids=[AF3_SKIN_ID])
    except Exception as err:
        logging.log('[AF3] native install failed: {0}'.format(err), level=xbmc.LOGERROR)
    return xbmc.getCondVisibility('System.HasAddon({0})'.format(AF3_SKIN_ID))


def ensure_skin_installed(skin_id):
    """Install any catalog skin on demand (no-op when present); True once it is installed AND enabled.

    The Skin Manager and Switch Skin call this: Arctic Fuse 3 goes through Kodi's native install, every other skin through
    the verified modular manifest. A skin that is not in the catalog is refused.
    """
    from resources.libs.skins import skin_catalog
    skin = skin_catalog.find(skin_id)
    if skin is None:
        return False
    if skin.id == AF3_SKIN_ID:
        return ensure_arctic_fuse_3_installed()
    return ensure_modular_skin(skin.id, skin.floor, skin.name)


def ensure_nox_installed():
    """Use the verified modular skin, never the obsolete static pack."""
    return ensure_modular_skin('skin.povil.nox', NOX_SKIN_VERSION, 'NOX')


def ensure_modular_skin(ident, floor, tag):
    """Install an on-demand build skin from the verified modular manifest."""
    from resources.libs.modular_updater import ModularUpdater
    from resources.libs.profiles import build_skin
    updater = ModularUpdater(background=True)
    if updater._version_tuple(updater.get_local_version(ident)) >= updater._version_tuple(floor):
        return build_skin.enable_skin(ident)
    manifest = updater._load_manifest() or {}
    entry = (manifest.get('addons') or {}).get(ident)
    if not entry or updater._version_tuple(entry.get('version')) < updater._version_tuple(floor):
        return False
    if not entry.get('zip') or not entry.get('sha256'):
        logging.log('[{}] the manifest entry has no zip / checksum'.format(tag), level=xbmc.LOGERROR)
        return False
    entry = dict(entry, id=ident)
    try:
        # The same headless engine as every other install: download, sha256 check, extract (a bad checksum extracts nothing).
        from resources.libs.headless_installer import HeadlessInstaller
        installed = HeadlessInstaller().install_jobs([entry])
        if ident not in installed:
            logging.log('[{}] modular installation did not complete'.format(tag), level=xbmc.LOGERROR)
            return False
        updater._register_and_enable(installed)
        return (updater.get_local_version(ident) == entry['version'] and
                build_skin.enable_skin(ident))
    except Exception as err:
        logging.log('[{}] modular installation failed: {}'.format(tag, type(err).__name__), level=xbmc.LOGERROR)
        return False
