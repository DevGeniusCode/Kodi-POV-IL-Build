"""On-demand build skins: NOX / Onyx / Estuary / Arctic Fuse 3 install."""
import xbmc

from resources.libs.common import logging


#####################################################
# KODI-POV-IL - ON-DEMAND SKIN INSTALL
# The build's selectable skins other than FENtastic are installed ONLY when the user picks
# them from the Skin Manager / Switch Skin, so they never bloat the base build and are NEVER
# force-pushed to every device during fresh installs or background OTA:
#
# Every on-demand skin is installed through Kodi's native InstallAddon and then updated by Kodi like any other add-on:
#
#   * Arctic Fuse 3 (skin.arctic.fuse.3) -- from jurialmunkey's index (repository.povil.upstream). Kodi pulls the skin +
#     ALL its dependencies (tmdbhelper, skinvariables, texturemaker, script.module.jurialmunkey, fonts, studio/weather
#     icons...) from upstream at mutually-compatible versions.
#
#   * NOX, Onyx and Estuary (our build edition) -- from our own index (repository.povil).

AF3_SKIN_ID = 'skin.arctic.fuse.3'


def ensure_arctic_fuse_3_installed():
    """Install Arctic Fuse 3 ON DEMAND via Kodi's native InstallAddon.

    Replaces the old four static "pack" zips. The AF3 skin and ALL of its
    dependencies (script.module.jurialmunkey, plugin.video.themoviedb.helper,
    script.skinvariables, script.texturemaker, the fonts / studio / weather
    resource addons, ...) are resolved natively from jurialmunkey's index
    (repository.povil.upstream) plus the official Kodi repo, at
    mutually-compatible upstream versions. They then keep updating
    from their own repos -- no version-pinned bundle to maintain.

    Reuses the provisioning watchdog (auto-confirms the native dependency
    Yes/No dialog, closes stray first-run popups) so the install is hands-off.
    Returns True once AF3 is present. Idempotent: a no-op if already installed.
    """
    if xbmc.getCondVisibility('System.HasAddon({0})'.format(AF3_SKIN_ID)):
        return True
    try:
        from resources.libs.gui import install_progress
        from resources.libs.provisioning import provisioner
        # Longer timeout than the content-addon default: the AF3 skin + tmdbhelper
        # + font/studio resources are a sizeable native download.
        with install_progress.window('מתקין את הסקין Arctic Fuse 3') as view:
            provisioner.provision(ids=[AF3_SKIN_ID], per_addon_timeout=240, view=view)
    except Exception as err:
        logging.log('[AF3] native install failed: {0}'.format(err), level=xbmc.LOGERROR)
    return xbmc.getCondVisibility('System.HasAddon({0})'.format(AF3_SKIN_ID))


def ensure_skin_installed(skin_id):
    """Install any catalog skin on demand (no-op when present); True once it is installed AND enabled.

    The Skin Manager and Switch Skin call this: every skin goes through Kodi's native install. A skin that is not in the
    catalog is refused.
    """
    from resources.libs.skins import skin_catalog
    skin = skin_catalog.find(skin_id)
    if skin is None:
        return False
    if skin.id == AF3_SKIN_ID:
        return ensure_arctic_fuse_3_installed()
    return ensure_repository_skin(skin.id, skin.name)


def ensure_repository_skin(ident, tag):
    """Install an on-demand build skin (NOX, Onyx, Estuary: all served by repository.povil) through Kodi's native InstallAddon,
    then enable it with its dependencies. True once it is installed AND enabled."""
    from resources.libs.provisioning import addon_state, provisioner
    from resources.libs.profiles import build_skin
    if addon_state.local_version(ident) is None:
        try:
            from resources.libs.gui import install_progress
            with install_progress.window('מתקין את הסקין {0}'.format(tag)) as view:
                provisioner.provision(ids=[ident], per_addon_timeout=240, view=view)
        except Exception as err:
            logging.log('[{}] installation failed: {}'.format(tag, type(err).__name__), level=xbmc.LOGERROR)
            return False
    return addon_state.local_version(ident) is not None and build_skin.enable_skin(ident)
