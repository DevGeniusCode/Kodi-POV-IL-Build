"""Copy the Wizard's Hebrew POV strings into plugin.video.pov."""
import os
import shutil

import xbmc

from resources.libs.common.config import CONFIG
from resources.libs.common import logging


def sync_pov_language_file():
    """Copy Hebrew language file from wizard to plugin.video.pov as strings.po."""
    try:
        src_file = os.path.join(
            CONFIG.ADDONS,
            'plugin.program.kodipovilwizard',
            'resources',
            'text',
            'pov.resource.language.he_il.strings.po'
        )
        dst_dir = os.path.join(
            CONFIG.ADDONS,
            'plugin.video.pov',
            'resources',
            'language',
            'resource.language.he_il'
        )
        dst_file = os.path.join(dst_dir, 'strings.po')

        if os.path.exists(src_file):
            if not os.path.exists(dst_dir):
                os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            logging.log('[Language Sync] Successfully copied language file to plugin.video.pov as strings.po', level=xbmc.LOGINFO)
        else:
            logging.log('[Language Sync] Source file not found: {0}'.format(src_file), level=xbmc.LOGWARNING)
    except Exception as err:
        logging.log('[Language Sync] Failed to copy language file: {0}'.format(err), level=xbmc.LOGERROR)
