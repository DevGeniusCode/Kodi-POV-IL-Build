"""Start-up notices: the remote build notification and the first-launch
skin-switch notice."""
import xbmc

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.gui import window


def show_notification():
    note_id, msg = window.split_notify(CONFIG.NOTIFICATION)
    if note_id:
        try:
            remote_id = int(note_id)
            current_id = int(CONFIG.NOTEID or 0)
        except (TypeError, ValueError):
            logging.log('[Notifications] Invalid note ID: {0}'.format(note_id),
                        level=xbmc.LOGINFO)
            return
        if remote_id == current_id:
            if CONFIG.NOTEDISMISS == 'false':
                window.show_notification(msg)
            else:
                logging.log('[Notifications] No new notifications.', level=xbmc.LOGINFO)
        elif remote_id > current_id:
            logging.log('[Notifications] Showing notification {0}'
                        .format(note_id))
            CONFIG.set_setting('noteid', note_id)
            CONFIG.set_setting('notedismiss', 'false')
            window.show_notification(msg)
    else:
        logging.log('[Notifications] Notifications file at {0} not formatted correctly.'
                    .format(CONFIG.NOTIFICATION),
                    level=xbmc.LOGINFO)


def first_launch_skin_notice():
    # KODI-RD-IL - FIRST BUILD LAUNCH BUILD SKIN SWITCH NOTIFICATION
    logging.log('[Startup] first-launch notice gate: buildname={0}, dismissed={1}'.format(
        bool(CONFIG.get_setting('buildname')),
        CONFIG.get_setting('build_skin_switch_notifcation_dismiss')),
        level=xbmc.LOGINFO)
    if CONFIG.get_setting('buildname') and CONFIG.get_setting('build_skin_switch_notifcation_dismiss') == 'false':
        window.show_contact(CONFIG.CONTACT)
        CONFIG.set_setting('build_skin_switch_notifcation_dismiss', 'true')
        msg = f"על מנת להחליף סקין יש ללחוץ: כפתור כיבוי --> החלף סקין.\nהסקינים הקיימים בבילד:\n1. סקין Estuary\n2. סקין FENtastic\n3. סקין Arctic Fuse 3\n4. סקין NOX\n5. סקין Onyx"
        window.show_notification_with_extra_image(msg, 888, CONFIG.BUILD_SKIN_SWITCH_IMAGE)
    #####################################

    logging.log('[Startup] first-launch notice gate complete', level=xbmc.LOGINFO)
