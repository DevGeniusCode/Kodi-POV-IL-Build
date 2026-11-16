"""FENtastic-specific data preservation: the helper databases and skin XMLs kept across a wipe,
and the home-widget layout a quick update must leave alone."""
import os
import shutil

import xbmc

from resources.libs.common.config import CONFIG
from resources.libs.common import logging


def backup_fentasticdata():
    try:
        # Verify that FENtastic + Helper addon is installed.
        isFENtasticExists = xbmc.getCondVisibility('System.HasAddon(skin.fentastic)') and xbmc.getCondVisibility('System.HasAddon(script.fentastic.helper)')
        keep_enabled = getattr(CONFIG, 'KEEPFENTASTICDATA', 'false') == 'true'
        logging.log(f"[Install/Wipe] BEFORE Wipe - Is KEEPFENTASTICDATA Enabled: {keep_enabled} | isFENtasticExists: {isFENtasticExists}")

        if keep_enabled and isFENtasticExists:

            # Helper .db files directory Path
            fentastic_helper_db_files_dir = os.path.join(os.path.abspath(CONFIG.ADDON_DATA), 'script.fentastic.helper')
            logging.log(f"[Install/Wipe] FENtastic Helper .db files SRC: {fentastic_helper_db_files_dir}")
            
            # My_Builds/fentastic_helper_db_files directory Path        
            kodi_my_builds_fentastic_helper_db_files_dir = os.path.join(os.path.abspath(CONFIG.HOME), 'My_Builds', 'fentastic_helper_db_files')
            logging.log(f"[Install/Wipe] FENtastic Helper .db files DEST: {kodi_my_builds_fentastic_helper_db_files_dir}")
            
            # Create the destination directory if it does not exist
            os.makedirs(kodi_my_builds_fentastic_helper_db_files_dir, exist_ok=True)
            
            # Copy all FENtastic helper .db files to My_Builds/fentastic_helper_db_files dir for temp location - before wipe
            if os.path.exists(fentastic_helper_db_files_dir):
                for item in os.listdir(fentastic_helper_db_files_dir):
                    if item.endswith(".db"):
                        src_path = os.path.join(fentastic_helper_db_files_dir, item)
                        dst_path = os.path.join(kodi_my_builds_fentastic_helper_db_files_dir, item)
                        shutil.copy2(src_path, dst_path)
                        logging.log(f"[Install/Wipe] Copying FENtastic helper .db file {item} to {dst_path}")

            ######################################################################################################################
            
            # FENtastic skin XMLs files directory Path
            fentastic_skin_xmls_files_dir = os.path.join(os.path.abspath(CONFIG.ADDONS), 'skin.fentastic', 'xml')
            logging.log(f"[Install/Wipe] FENtastic skin XMLs files SRC: {fentastic_skin_xmls_files_dir}")

            # My_Builds/fentastic_skin_xmls_files directory Path
            kodi_my_builds_fentastic_skin_xmls_files_dir = os.path.join(os.path.abspath(CONFIG.HOME), 'My_Builds', 'fentastic_skin_xmls_files')
            logging.log(f"[Install/Wipe] FENtastic skin XMLs files DEST: {kodi_my_builds_fentastic_skin_xmls_files_dir}")

            # Create the destination directory if it does not exist
            os.makedirs(kodi_my_builds_fentastic_skin_xmls_files_dir, exist_ok=True)

            # Copy only .xml files starting with the name "script-fentastic-" to My_Builds/fentastic_skin_xmls_files dir for temp location - before wipe
            if os.path.exists(fentastic_skin_xmls_files_dir):
                for item in os.listdir(fentastic_skin_xmls_files_dir):
                    if item.startswith("script-fentastic-") and item.endswith(".xml"):
                        src_path = os.path.join(fentastic_skin_xmls_files_dir, item)
                        dst_path = os.path.join(kodi_my_builds_fentastic_skin_xmls_files_dir, item)
                        shutil.copy2(src_path, dst_path)
                        logging.log(f"[Install/Wipe] Copying FENtastic skin XML file {item} to {dst_path}")

        else:
            logging.log("[Install/Wipe] BEFORE Wipe - Skipping Saving FENtastic Design data.")

    except Exception as e:
        logging.log(f"[Install/Wipe] BEFORE Wipe - backup_fentasticdata ERROR: {e}")


def restore_fentasticdata():
    try:
        # Verify that FENtastic + Helper addon is installed.
        isFENtasticExists = xbmc.getCondVisibility('System.HasAddon(skin.fentastic)') and xbmc.getCondVisibility('System.HasAddon(script.fentastic.helper)')
        keep_enabled = getattr(CONFIG, 'KEEPFENTASTICDATA', 'false') == 'true'
        logging.log(f"[Install/Wipe] AFTER Wipe - Is KEEPFENTASTICDATA Enabled: {keep_enabled} | isFENtasticExists: {isFENtasticExists}")

        # AFTER WIPE
        if keep_enabled and isFENtasticExists:

            # Helper .db files directory Path
            fentastic_helper_db_files_dir = os.path.join(os.path.abspath(CONFIG.ADDON_DATA), 'script.fentastic.helper')

            # My_Builds/fentastic_helper_db_files directory Path
            kodi_my_builds_fentastic_helper_db_files_dir = os.path.join(os.path.abspath(CONFIG.HOME), 'My_Builds', 'fentastic_helper_db_files')

            if os.path.exists(kodi_my_builds_fentastic_helper_db_files_dir):
                # Create userdata/addons_data/script.fentastic.helper directory (doesn't exist after wipe)
                os.makedirs(fentastic_helper_db_files_dir, exist_ok=True)

                # Move all FENtastic helper .db files from My_Builds/fentastic_helper_db_files to FENtastic helper databases dir
                for item in os.listdir(kodi_my_builds_fentastic_helper_db_files_dir):
                    src_path = os.path.join(kodi_my_builds_fentastic_helper_db_files_dir, item)
                    dst_path = os.path.join(fentastic_helper_db_files_dir, item)
                    shutil.move(src_path, dst_path)
                    logging.log(f"[Install/Wipe] Moving FENtastic Helper {item} file to {dst_path}")

                # Remove empty My_Builds/fentastic_helper_db_files dir after .db files move.
                if not os.listdir(kodi_my_builds_fentastic_helper_db_files_dir):
                    os.rmdir(kodi_my_builds_fentastic_helper_db_files_dir)
                    logging.log(f"[Install/Wipe] Deleting unnecessary {kodi_my_builds_fentastic_helper_db_files_dir} directory.")

            ######################################################################################################################

            # FENtastic skin XMLs files directory Path
            fentastic_skin_xmls_files_dir = os.path.join(os.path.abspath(CONFIG.ADDONS), 'skin.fentastic', 'xml')
            logging.log(f"[Install/Wipe] FENtastic skin XMLs files SRC: {fentastic_skin_xmls_files_dir}")

            # My_Builds/fentastic_skin_xmls_files directory Path
            kodi_my_builds_fentastic_skin_xmls_files_dir = os.path.join(os.path.abspath(CONFIG.HOME), 'My_Builds', 'fentastic_skin_xmls_files')
            logging.log(f"[Install/Wipe] FENtastic skin XMLs files DEST: {kodi_my_builds_fentastic_skin_xmls_files_dir}")

            if os.path.exists(kodi_my_builds_fentastic_skin_xmls_files_dir):
                # Create the destination directory if it does not exist
                os.makedirs(fentastic_skin_xmls_files_dir, exist_ok=True)

                # Move all FENtastic Skin .xml files from My_Builds/fentastic_skin_xmls_files to FENtastic Skin XMLs dir
                for item in os.listdir(kodi_my_builds_fentastic_skin_xmls_files_dir):
                    src_path = os.path.join(kodi_my_builds_fentastic_skin_xmls_files_dir, item)
                    dst_path = os.path.join(fentastic_skin_xmls_files_dir, item)
                    shutil.move(src_path, dst_path)
                    logging.log(f"[Install/Wipe] Moving FENtastic Skin XML file {item} to {dst_path}")

                # Remove empty My_Builds/fentastic_skin_xmls_files dir after .xml files move.
                if not os.listdir(kodi_my_builds_fentastic_skin_xmls_files_dir):
                    os.rmdir(kodi_my_builds_fentastic_skin_xmls_files_dir)
                    logging.log(f"[Install/Wipe] Deleting unnecessary {kodi_my_builds_fentastic_skin_xmls_files_dir} directory.")

        else:
            logging.log("[Install/Wipe] AFTER Wipe - Skipping Saving FENtastic Design data.")

    except Exception as e:
        logging.log(f"[Install/Wipe] AFTER Wipe - restore_fentasticdata ERROR: {e}")


def preserve_widget_layout(_out, file, ignore):
    """True if this archive member is a FENtastic home-widget file that must
    be left alone.

    `file` is the member name already split on '/'. Kept as a named function
    so it can be tested directly instead of being restated somewhere else.

    The home widgets are rendered from script-fentastic-widget_*.xml, which
    FENtastic's widget editor rewrites from the user's saved skin settings.
    Overwriting them on every quickfix reverted the home to our default until
    the user next touched a widget, so a quick update leaves them alone.

    TWO conditions, and both matter:

      ignore is not None
        A deliberate (re)install -- Theme Install, Install Skin, a Restore the
        user chose not to wipe first -- passes ignore=None and MUST lay down
        the defaults it was asked to install. Preserving a stale layout there
        would be the opposite of what the user asked for.

      the file is already on disk
        The fresh-build auto-install in startup.py passes ignore=True too, to
        bypass the skip of the wizard's own files -- so on a brand-new install
        the first condition alone skipped all five, and there was nothing to
        preserve: they simply never arrived. The skin then failed every widget
        include ("Skin has invalid include: MovieWidgets") and Movies, TV shows
        and Idan+ came up empty. A quick update could not repair it either,
        for the same reason. Nothing is preserved by refusing to write a file
        that is not there.
    """
    return (ignore is not None
            and file[0] == 'addons' and len(file) >= 4
            and file[1] == 'skin.fentastic' and file[2] == 'xml'
            and file[-1].startswith('script-fentastic-widget_')
            and os.path.isfile(os.path.join(_out, *file)))
