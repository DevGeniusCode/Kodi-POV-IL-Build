"""On-demand build skins: NOX / Onyx / Arctic Fuse 3 install and auto-update."""
import os

import xbmc

from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs.common.config import CONFIG
from resources.libs.packs.pack_installer import NOX_PACK_BASE_URL
from resources.libs.packs.pack_installer import _af3_pack_current


#####################################################
# KODI-POV-IL - ON-DEMAND SKIN INSTALL
# Two of the build's selectable skins are installed ONLY when the user picks
# them from Switch Skin, so they never bloat the base build and are NEVER
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
#   * NOX (skin.povil.nox) -- a rebranded + scrubbed Estuary MOD that is NOT on
#     any public repo, so it is still shipped as a single on-demand pack zip in
#     dist/ and downloaded+extracted on first switch (see _ensure_packs_installed).

# KODI-POV-IL - NOX skin. A rebranded + scrubbed Estuary MOD (~24 MB) whose home
# menu was remapped to our POV / idanplus / otaku addons. It is NOT on any public
# repo, so (unlike AF3) it cannot be resolved natively -- it is shipped as a
# single on-demand pack zip in dist/ and downloaded only when the user picks it
# from Switch Skin, so it never bloats the base build. Its only hard dependency,
# script.fentastic.helper, already ships in the build.


NOX_SKIN_VERSION = '1.0.17'
NOX_PACKS = [
    {
        'name': 'סקין NOX',
        'url': '{0}/Kodi-POV-IL-NOX-skin-pack.zip'.format(NOX_PACK_BASE_URL),
        'filename': 'nox_skin_pack.zip',
        'sentinel': 'special://home/addons/skin.povil.nox/addon.xml',
        'expected_version': NOX_SKIN_VERSION,
        'addon_ids': ['skin.povil.nox'],
    },
]

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
    entry = dict(entry, id=ident)
    tools.ensure_folders(CONFIG.PACKAGES)
    package = os.path.join(CONFIG.PACKAGES, '%s-modular.zip' % tag.lower())
    try:
        updater._download_background_zip(entry['zip'], package, entry)
        updater._install_verified_module(package, entry, entry['sha256'])
        xbmc.executebuiltin('UpdateLocalAddons')
        xbmc.Monitor().waitForAbort(0.5)
        return (updater.get_local_version(ident) == entry['version'] and
                build_skin.enable_skin(ident))
    except Exception as err:
        logging.log('[{}] modular installation failed: {}'.format(tag, type(err).__name__), level=xbmc.LOGERROR)
        return False


def auto_update_active_skin_pack():
    """Refresh an on-demand skin pack (currently NOX) when the user is already
    ON that skin and a newer version has been published. The pack is otherwise
    only (re)installed when picked from Switch Skin, so without this an existing
    NOX user never gets skin updates from a normal quick_update -- they had to
    switch away and back. Idempotent: the version gate (_af3_pack_current) means
    we only re-download when the on-disk skin is actually OLDER than what we now
    ship, so it does NOT re-download every boot, and it does NOT re-download when
    the user merely toggles skins. Re-extracting overwrites only the skin's addon
    files under addons/skin.povil.nox -- never userdata/addon_data skin settings,
    so the user's favourites order and skin tweaks are preserved."""
    try:
        active = CONFIG.SKIN or ''
        if 'skin.povil.nox' not in active:
            return
        pack = NOX_PACKS[0]
        if _af3_pack_current(pack):
            return  # on-disk version already current; no re-download
        logging.log(
            '[Skin Auto Update] NOX is active and the installed pack is behind '
            '{0}; refreshing it now.'.format(pack.get('expected_version')),
            level=xbmc.LOGINFO)
        if ensure_nox_installed():
            xbmc.sleep(800)
            try:
                xbmc.executebuiltin('ReloadSkin()')
            except Exception:
                pass
    except Exception as e:
        logging.log('[Skin Auto Update] failed: {0}'.format(e),
                    level=xbmc.LOGERROR)
