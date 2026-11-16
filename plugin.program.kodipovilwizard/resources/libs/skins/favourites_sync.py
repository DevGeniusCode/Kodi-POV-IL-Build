"""Rewrites favourites.xml for the skin being switched to."""
from resources.libs.common import logging


#########################################################################################################
# KODI-RD-IL - BUILD SKIN SWITCH
def update_favourites_xml_file(gotoskin):
    """Regenerate userdata/favourites.xml for the target skin.

    KODI-POV-IL: replaces the old static copy of
    media/builds_favourites_xml/<skin>/favourites.xml with a dynamic,
    JSON-driven generation. We load favourites_generator from the
    plugin.program.orderfavourites-hebrew add-on (by file path, so there are no
    cross-add-on package-name clashes) and call generate_favourites_xml(skin),
    which builds the skin's canonical tile set from favourites_config.json AND
    merges in any custom favourites the user added.

    Graceful by design: if the generator add-on is missing or anything goes
    wrong, we log it and leave the existing favourites.xml untouched. We always
    return True so a generation hiccup never aborts the skin switch itself.
    """
    try:
        import os as _os
        import xbmcaddon
        import xbmcvfs

        try:
            addon_path = xbmcvfs.translatePath(
                xbmcaddon.Addon('plugin.program.orderfavourites-hebrew')
                .getAddonInfo('path'))
        except Exception as _addon_err:
            logging.log("DEBUG | update_favourites_xml_file | orderfavourites "
                        "add-on unavailable ({0}); leaving favourites untouched"
                        .format(_addon_err))
            return True

        module_file = _os.path.join(addon_path, 'favourites_generator.py')
        if not _os.path.isfile(module_file):
            logging.log("DEBUG | update_favourites_xml_file | generator not found "
                        "at {0}; leaving favourites untouched".format(module_file))
            return True

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'povil_favourites_generator', module_file)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        if generator.generate_favourites_xml(gotoskin) is None:
            logging.log("DEBUG | update_favourites_xml_file | invalid existing "
                        "favourites; kept the file unchanged for skin {0}"
                        .format(gotoskin))
            return True
        logging.log("DEBUG | update_favourites_xml_file | regenerated favourites "
                    "for skin {0}".format(gotoskin))
        return True
    except Exception as e:
        logging.log("DEBUG | update_favourites_xml_file | generation failed ({0}); "
                    "leaving existing favourites.xml untouched".format(e))
        return True
