################################################################################
#      Copyright (C) 2019 drinfernoo                                           #
#                                                                              #
#  This Program is free software; you can redistribute it and/or modify        #
#  it under the terms of the GNU General Public License as published by        #
#  the Free Software Foundation; either version 2, or (at your option)         #
#  any later version.                                                          #
#                                                                              #
#  This Program is distributed in the hope that it will be useful,             #
#  but WITHOUT ANY WARRANTY; without even the implied warranty of              #
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the                #
#  GNU General Public License for more details.                                #
#                                                                              #
#  You should have received a copy of the GNU General Public License           #
#  along with XBMC; see the file COPYING.  If not, write to                    #
#  the Free Software Foundation, 675 Mass Ave, Cambridge, MA 02139, USA.       #
#  http://www.gnu.org/copyleft/gpl.html                                        #
################################################################################

import xbmc
import xbmcgui

import os

from resources.libs.common.config import CONFIG


def wizard_update():
    # KODI-POV-IL - the wizard is updated by Kodi like any other add-on (repository.povil); the legacy
    # build.txt self-update is gone. The 'wizardupdate' menu mode now just runs the update check.
    from resources.libs.common import logging
    try:
        from resources.libs.provisioning import provisioner
        provisioner.run_update_check(background=False)
    except Exception as err:
        logging.log("[Wizard Update] update check failed: {0}".format(err), level=xbmc.LOGERROR)


def addon_updates(do=None):
    """'set': Kodi checks for nothing while the Wizard wipes or removes add-ons. 'reset': back to the build's mode (NOTIFY: only the
    Wizard's update window installs updates, see provisioning/update_mode.py). Never back to "install automatically"."""
    from resources.libs.provisioning import update_mode
    if do == 'set':
        update_mode.set_mode(update_mode.NEVER)
    elif do == 'reset':
        update_mode.set_mode(update_mode.NOTIFY)


def toggle_addon_updates():
    """Maintenance -> Toggle Addon Updates: the Wizard's update window (NOTIFY), or no update check at all (NEVER)."""
    from resources.libs.common import logging
    from resources.libs.provisioning import update_mode

    options = [('Wizard updates (recommended)', update_mode.NOTIFY), ('Never check for updates', update_mode.NEVER)]
    selected = xbmcgui.Dialog().select(CONFIG.ADDONTITLE, [name for name, _value in options])
    if selected < 0:
        return
    name, value = options[selected]
    update_mode.set_mode(value)
    logging.log_notify(CONFIG.ADDONTITLE, 'Updates changed to "{0}"'.format(name))
