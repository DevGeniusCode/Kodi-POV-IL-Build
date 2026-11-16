"""The build "Switch Skin" flow."""
import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common.config import CONFIG
from resources.libs.skins.skin_packs import ensure_arctic_fuse_3_installed
from resources.libs.skins.skin_packs import ensure_nox_installed
from resources.libs.skins.skin_packs import ensure_onyx_installed


def switch_skin_in_gui_settings(gotoskin):
    from resources.libs.profiles import build_skin
    return build_skin.switch(gotoskin)


def build_switch_skin():
    from resources.libs.profiles import profile_age_guard
    policy = profile_age_guard.active_policy()
    if policy is not None and policy.get('blocked'):
        xbmcgui.Dialog().ok('סקין פרופיל הילדים', 'יש להשלים את בקרת ההורים במשתמש הראשי.')
        return
    if not CONFIG.get_setting('buildname'):
        logging.log_notify(CONFIG.ADDONTITLE,
                           '[COLOR {0}]לא מותקן בילד![/COLOR]'.format(CONFIG.COLOR2))
        return


    from resources.libs.profiles import build_skin
    if not build_skin.close_switch_menu():
        return
    from resources.libs.gui import window
    msg = f"הסקינים הקיימים בבילד:\n1. סקין Estuary\n2. סקין FENtastic\n3. סקין Arctic Fuse 3\n4. סקין NOX\n5. סקין Onyx"
    if policy is None:
        window.show_notification_with_extra_image(msg, 888, CONFIG.BUILD_SKIN_SWITCH_IMAGE_URL)

    skin_mapping = {
        'סקין Estuary - מראה פשוט עם כפתורים': 'skin.estuary',
        'סקין FENtastic - יפהפה': 'skin.fentastic',
        'סקין Arctic Fuse 3 - מודרני (ניסיוני)': 'skin.arctic.fuse.3',
        'סקין NOX - עברית מלאה (ניסיוני)': 'skin.povil.nox',
        'סקין Onyx - יוקרתי, מימין לשמאל (ניסיוני)': 'skin.povil.onyx'
    }

    # Get the name of the current active skin. If the user manually
    # switched to a skin not in our mapping (e.g. via Kodi's own
    # Settings -> Interface -> Skin), `next()` without a default
    # would raise StopIteration and crash the whole wizard. Default
    # to a generic Hebrew label so the dialog still renders and the
    # user can pick a known skin to recover.
    current_skin_name = next(
        (skin_name for skin_name, skin_addon_name in
         skin_mapping.items() if skin_addon_name == xbmc.getSkinDir()),
        'סקין לא מזוהה'
    )

    # Filter out the current active skin from the list
    skins_list = [skin_name for skin_name, skin_addon_name in skin_mapping.items() if skin_addon_name != xbmc.getSkinDir()]

    # Create a dialog window
    dialog = xbmcgui.Dialog()
    gotoskin_index_number = dialog.select(f"[B]סקין נוכחי: [COLOR gold]{current_skin_name}[/COLOR][/B]", skins_list)

    if gotoskin_index_number == -1:  # User cancelled the menu
        return

    selected_skin = skins_list[gotoskin_index_number]
    gotoskin = skin_mapping[selected_skin]

    yes_pressed = dialog.yesno(CONFIG.ADDONTITLE,
                       '[B][COLOR {0}]האם ברצונך להחליף סקין ל:'.format(CONFIG.COLOR2) + '\n' + '[COLOR {0}]{1}[/COLOR]?[/COLOR][/B]'.format(CONFIG.COLOR1, selected_skin),
                       nolabel='[B][COLOR red]ביטול[/COLOR][/B]',
                       yeslabel='[B][COLOR springgreen]החלף סקין[/COLOR][/B]')

    if yes_pressed:
        # Arctic Fuse 3 is installed ON DEMAND, natively, from
        # repository.jurialmunkey (Kodi resolves the skin + all its deps) the
        # first time the user switches to it. Idempotent: a no-op once present.
        if gotoskin == 'skin.arctic.fuse.3':
            if not ensure_arctic_fuse_3_installed():
                logging.log_notify(
                    CONFIG.ADDONTITLE,
                    '[COLOR {0}]Arctic Fuse 3 לא הותקן - מבטל[/COLOR]'.format(
                        CONFIG.COLOR2))
                return
        # NOX is also too big to bundle in the base build; download +
        # extract it on first switch, identical to the AF3 path.
        elif gotoskin == 'skin.povil.nox':
            if not ensure_nox_installed():
                logging.log_notify(
                    CONFIG.ADDONTITLE,
                    '[COLOR {0}]סקין NOX לא הותקן - מבטל[/COLOR]'.format(
                        CONFIG.COLOR2))
                return
        elif gotoskin == 'skin.povil.onyx':
            if not ensure_onyx_installed():
                logging.log_notify(
                    CONFIG.ADDONTITLE,
                    '[COLOR {0}]סקין Onyx לא הותקן - מבטל[/COLOR]'.format(
                        CONFIG.COLOR2))
                return

        dialogProgress = xbmcgui.DialogProgress()
        dialog_text = '[COLOR {0}][B]מחליף סקין ומגדיר את מסך הבית של:[/B][/COLOR]\n[COLOR {1}][B]{2}[/B][/COLOR]'.format(CONFIG.COLOR2, CONFIG.COLOR1, selected_skin)
        dialogProgress.create(CONFIG.ADDONTITLE, dialog_text)
        for s in range(3, -1, -1):
            dialogProgress.update(int((3 - s) / 3.0 * 100), dialog_text)
            xbmc.sleep(1000)

        dialogProgress.close()
        from resources.libs.profiles import build_profiles
        if not build_skin.wait_dialogs():
            logging.log('[Skin Switch] Deferred by dialog %s' % xbmcgui.getCurrentWindowDialogId(),
                        level=xbmc.LOGWARNING)
            dialog.ok(CONFIG.ADDONTITLE, 'סגור את החלון הפתוח ונסה להחליף סקין שוב.')
            return
        if not switch_skin_in_gui_settings(gotoskin):
            logging.log('[Skin Switch] Activation failed: ' + gotoskin, level=xbmc.LOGWARNING)
            dialog.ok(CONFIG.ADDONTITLE, 'לא ניתן להשלים את החלפת הסקין. אפשר לנסות שוב דרך הוויזרד.')

    else:
        return
