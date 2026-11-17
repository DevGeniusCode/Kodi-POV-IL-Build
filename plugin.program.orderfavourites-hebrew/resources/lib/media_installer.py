# -*- coding: utf-8 -*-
# Global media + favourites updater for the Kodi POV IL build.
#
# This addon is the CENTRAL home of the build's media assets.
# It recursively copies the entire contents of the 'resources/media' folder
# (including flat files, icons, fonts, and any nested subfolders) into the
# global media folder (special://home/media/).
#
# On startup we (re)install those assets and refresh favourites.xml. Because the
# build is Trakt/TMDB-driven, the local favourites.xml is used ONLY for internal
# skin shortcuts (quick access to POV folders, settings, etc.) -- it holds no
# user movie/show data -- so it is safe AND necessary to OVERWRITE it (and the
# bundled media files) on every run, so UI/shortcut + media updates reach users
# even when the files already exist. Custom favourites the user added themselves
# are still preserved (favourites_generator merges them back in).
#
# Fully guarded: every failure is swallowed so this can never break Kodi startup.

import os

try:
    import xbmcvfs
except Exception:
    xbmcvfs = None

try:
    import xbmcaddon
    _ADDON = xbmcaddon.Addon('plugin.program.orderfavourites-hebrew')
except Exception:
    xbmcaddon = None
    _ADDON = None

try:
    import xbmc
except Exception:
    xbmc = None


# (source path relative to the addon root, destination special:// folder).
# Deployed into the global media folder: Kodi reads the media from there rather 
# than reaching into this addon's own folder -- avoiding cross-addon containment breaches. 
# The canonical SOURCE copy lives in the addon (resources/media/) and is overwritten 
# recursively into the global folder on startup.
_COPY_JOBS = (
    (os.path.join('resources', 'media'), 'special://home/media/'),
)

def _log(msg):
    if xbmc is None:
        return
    try:
        xbmc.log('[orderfavourites media_installer] ' + msg, xbmc.LOGINFO)
    except Exception:
        pass


def _install_assets(addon_path):
    """OVERWRITE-copy the bundled media folder recursively into the global Kodi media folder
    so updated artwork/fonts reach users even when an older copy already exists.
    Maintains the directory structure. Returns the number of files written."""
    written = 0
    for src_rel, dst_special in _COPY_JOBS:
        try:
            src_root = os.path.join(addon_path, src_rel)
            if not os.path.isdir(src_root):
                continue
            
            dst_root = xbmcvfs.translatePath(dst_special)
            
            # Walk through all directories and files recursively
            for root, dirs, files in os.walk(src_root):
                for name in files:
                    src = os.path.join(root, name)
                    
                    # Calculate the relative path to maintain directory structure
                    rel_path = os.path.relpath(src, src_root)
                    dst = os.path.join(dst_root, rel_path)
                    
                    # Ensure the destination subdirectory exists
                    dst_dir = os.path.dirname(dst)
                    if not os.path.isdir(dst_dir):
                        try:
                            os.makedirs(dst_dir)
                        except Exception:
                            pass
                            
                    try:
                        # Overwrite: remove any stale copy first so xbmcvfs.copy
                        # (which won't clobber on some platforms) always lands the
                        # fresh file.
                        if os.path.exists(dst):
                            try:
                                os.remove(dst)
                            except Exception:
                                pass
                                
                        xbmcvfs.copy(src, dst)
                        written += 1
                    except Exception:
                        pass
        except Exception:
            pass
    return written


def _refresh_favourites(addon_path):
    """Regenerate (overwrite) special://userdata/favourites.xml for the active
    skin from favourites_config.json, pushing the latest internal skin shortcuts
    + icon paths. The generator preserves any custom favourites the user added.
    Best-effort and fully guarded."""
    try:
        import importlib.util
        gen_file = os.path.join(addon_path, 'favourites_generator.py')
        if not os.path.isfile(gen_file):
            return False
        spec = importlib.util.spec_from_file_location(
            'povil_favourites_generator_startup', gen_file)
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        skin_id = ''
        if xbmc is not None:
            try:
                skin_id = xbmc.getSkinDir() or ''
            except Exception:
                skin_id = ''
        # merge=True keeps the user's own custom tiles; our canonical tiles are
        # refreshed (overwritten) so UI/shortcut/icon updates always land.
        gen.generate_favourites_xml(skin_id, merge=True, write=True)
        _log('favourites.xml refreshed for skin "{0}"'.format(skin_id or '?'))
        return True
    except Exception as e:
        _log('favourites refresh failed: {0}'.format(e))
        return False


def install_global_media_assets():
    """Install/overwrite the bundled media assets recursively and refresh favourites.xml.

    Safe to call repeatedly; every failure is swallowed.
    """
    if xbmcvfs is None or _ADDON is None:
        return 0
    try:
        addon_path = xbmcvfs.translatePath(_ADDON.getAddonInfo('path'))
    except Exception:
        return 0
    written = _install_assets(addon_path)
    if written:
        _log('installed/updated {0} global media asset(s)'.format(written))
    _refresh_favourites(addon_path)
    return written


def install_and_verify_global_media_assets():
    """Run install_global_media_assets(), then report whether the favourites file exists.

    Used by the Wizard's fresh install (and its home refresh), which needs a truthy
    answer. install_global_media_assets() returns the number of files written, which
    is 0 on a re-run, so it cannot serve as a success flag; this wrapper stays
    idempotent and answers from the result on disk (special://userdata/favourites.xml).
    """
    install_global_media_assets()
    if xbmcvfs is None:
        return False
    try:
        return os.path.isfile(xbmcvfs.translatePath('special://userdata/favourites.xml'))
    except Exception:
        return False
