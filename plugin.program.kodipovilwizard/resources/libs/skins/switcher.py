"""The build "Switch Skin" flow: the quick picker (skins' power menus, main menu) and `switch_to` (the Skin Manager)."""
import xbmc
import xbmcgui

from resources.libs.common import logging
from resources.libs.common.config import CONFIG
from resources.libs.skins import skin_catalog
from resources.libs.skins.skin_packs import ensure_skin_installed


def switch_skin_in_gui_settings(gotoskin):
    from resources.libs.profiles import build_skin
    return build_skin.switch(gotoskin)


def _start_checks():
    """False (after telling the user why) when a skin switch must not start now."""
    from resources.libs.profiles import profile_age_guard
    policy = profile_age_guard.active_policy()
    if policy is not None and policy.get('blocked'):
        xbmcgui.Dialog().ok('סקין פרופיל הילדים', 'יש להשלים את בקרת ההורים במשתמש הראשי.')
        return False
    if not CONFIG.get_setting('buildname'):
        logging.log_notify(CONFIG.ADDONTITLE,
                           '[COLOR {0}]לא מותקן בילד![/COLOR]'.format(CONFIG.COLOR2))
        return False

    from resources.libs.profiles import build_skin
    return build_skin.close_switch_menu()


def build_switch_skin():
    from resources.libs.profiles import profile_age_guard
    if not _start_checks():
        return
    policy = profile_age_guard.active_policy()
    from resources.libs.gui import window
    names = '\n'.join('{0}. סקין {1}'.format(number, skin.name) for number, skin in enumerate(skin_catalog.SKINS, 1))
    msg = "הסקינים הקיימים בבילד:\n" + names
    if policy is None:
        window.show_notification_with_extra_image(msg, 888, CONFIG.BUILD_SKIN_SWITCH_IMAGE)

    # Get the name of the current active skin. If the user manually
    # switched to a skin not in our table (e.g. via Kodi's own
    # Settings -> Interface -> Skin), `next()` without a default
    # would raise StopIteration and crash the whole wizard. Default
    # to a generic Hebrew label so the dialog still renders and the
    # user can pick a known skin to recover.
    current = skin_catalog.find(xbmc.getSkinDir())
    current_skin_name = skin_catalog.picker_label(current) if current else 'סקין לא מזוהה'

    # Filter out the current active skin from the list. A skin that is not installed yet is marked: picking it
    # downloads it (on demand).
    choices = [skin for skin in skin_catalog.SKINS if skin.id != xbmc.getSkinDir()]
    labels = [skin_catalog.picker_label(skin) +
              ('' if skin_catalog.is_installed(skin.id) else ' [COLOR gray](יורד לפי דרישה)[/COLOR]')
              for skin in choices]

    # Create a dialog window
    dialog = xbmcgui.Dialog()
    gotoskin_index_number = dialog.select(f"[B]סקין נוכחי: [COLOR gold]{current_skin_name}[/COLOR][/B]", labels)

    if gotoskin_index_number == -1:  # User cancelled the menu
        return

    _switch(choices[gotoskin_index_number], confirm=True)


def switch_to(skin_id, confirm=True):
    """Switch to a catalog skin (installing it first if needed). The Skin Manager's entry point."""
    skin = skin_catalog.find(skin_id)
    if skin is None or not _start_checks():
        return False
    return _switch(skin, confirm)


def _switch(skin, confirm):
    selected_skin = skin_catalog.picker_label(skin)
    dialog = xbmcgui.Dialog()
    if confirm:
        yes_pressed = dialog.yesno(CONFIG.ADDONTITLE,
                           '[B][COLOR {0}]האם ברצונך להחליף סקין ל:'.format(CONFIG.COLOR2) + '\n' + '[COLOR {0}]{1}[/COLOR]?[/COLOR][/B]'.format(CONFIG.COLOR1, selected_skin),
                           nolabel='[B][COLOR red]ביטול[/COLOR][/B]',
                           yeslabel='[B][COLOR springgreen]החלף סקין[/COLOR][/B]')
        if not yes_pressed:
            return False

    # Skins other than the default are installed ON DEMAND, the first time they are picked, through Kodi's native install:
    # Arctic Fuse 3 from jurialmunkey's index (Kodi resolves the skin + all its deps), the others from repository.povil.
    # Idempotent: a no-op once present.
    if not ensure_skin_installed(skin.id):
        logging.log_notify(
            CONFIG.ADDONTITLE,
            '[COLOR {0}]{1} לא הותקן - מבטל[/COLOR]'.format(CONFIG.COLOR2, skin.name))
        return False

    dialogProgress = xbmcgui.DialogProgress()
    dialog_text = '[COLOR {0}][B]מחליף סקין ומגדיר את מסך הבית של:[/B][/COLOR]\n[COLOR {1}][B]{2}[/B][/COLOR]'.format(CONFIG.COLOR2, CONFIG.COLOR1, selected_skin)
    dialogProgress.create(CONFIG.ADDONTITLE, dialog_text)
    for s in range(3, -1, -1):
        dialogProgress.update(int((3 - s) / 3.0 * 100), dialog_text)
        xbmc.sleep(1000)

    dialogProgress.close()
    from resources.libs.profiles import build_profiles
    from resources.libs.profiles import build_skin
    if not build_skin.wait_dialogs():
        logging.log('[Skin Switch] Deferred by dialog %s' % xbmcgui.getCurrentWindowDialogId(),
                    level=xbmc.LOGWARNING)
        dialog.ok(CONFIG.ADDONTITLE, 'סגור את החלון הפתוח ונסה להחליף סקין שוב.')
        return False
    if not switch_skin_in_gui_settings(skin.id):
        logging.log('[Skin Switch] Activation failed: ' + skin.id, level=xbmc.LOGWARNING)
        dialog.ok(CONFIG.ADDONTITLE, 'לא ניתן להשלים את החלפת הסקין. אפשר לנסות שוב דרך הוויזרד.')
        return False
    return True
