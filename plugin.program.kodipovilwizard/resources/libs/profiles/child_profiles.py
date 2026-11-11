"""Child-profile state: skin flag, focus and native creation-lock defaults.

The child Home layout itself ships inside NOX, Estuary and FENtastic; Arctic
Fuse 3 receives it at runtime from af3_skin.
"""
import os
import tempfile
import xml.etree.ElementTree as ET

import xbmc
import xbmcgui

from resources.libs.profiles import profile_age_guard

SKINS = ('skin.povil.nox', 'skin.estuary', 'skin.fentastic', 'skin.arctic.fuse.3')
# Kodi's native settings controls are generated from negative IDs (-180).
# Positive IDs belong to dialog chrome/templates, not the editable settings.
NATIVE_SETTING_CONTROLS = range(-180, -160)


def write_xml(path, root):
    data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    if os.path.isfile(path):
        with open(path, 'rb') as source:
            if source.read() == data:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.povil-child-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def seed_skin_flags(profile):
    """Inactive profile only; retain every unrelated skin preference."""
    for ident in SKINS:
        path = os.path.join(profile, 'addon_data', ident, 'settings.xml')
        try:
            root = ET.parse(path).getroot() if os.path.isfile(path) else ET.Element('settings')
        except ET.ParseError:
            root = ET.Element('settings')  # unreadable skin settings cannot be preserved
        row = root.find("setting[@id='POVILChild']")
        if row is None:
            row = ET.SubElement(root, 'setting', id='POVILChild', type='bool')
        row.text = 'true'
        write_xml(path, root)


def focus_home():
    # Several skins set their adult defaultcontrol after onload actions.
    # The short bootstrap invocation places child focus after that step.
    if not xbmc.getCondVisibility('Skin.HasSetting(POVILChild)'):
        return
    if xbmc.Monitor().waitForAbort(0.2):
        return
    if (xbmc.getCondVisibility('Skin.HasSetting(POVILChild)') and
            xbmc.getCondVisibility('Window.IsActive(home)')):
        xbmc.executebuiltin('SetFocus(9500)')


def sync_active():
    """Never let a different skin restore an unrestricted child's Home."""
    policy = profile_age_guard.active_policy()
    wanted = policy is not None
    current = xbmc.getCondVisibility('Skin.HasSetting(POVILChild)')
    if current == wanted:
        return False
    xbmc.executebuiltin('Skin.SetBool(POVILChild)' if wanted else 'Skin.Reset(POVILChild)', True)
    return True


def preset_native_locks(state):
    """Only during an explicitly requested child-creation flow; no PIN changes.

    Set once per native lock dialog and leave it open for parental review.
    Video stays accessible to the filtered movie browser, settings/files/addon
    management and unrelated media windows get the recommended defaults.
    """
    dialog = xbmcgui.getCurrentWindowDialogId()
    if dialog == 10130 and not state.get('opened'):
        # This helper is called only from add_profile(kind=child). Kodi still
        # owns any master-code question and the parent's final Save/Cancel.
        window = xbmcgui.Window(10130)
        for ident in NATIVE_SETTING_CONTROLS:
            try:
                if window.getControl(ident).getLabel() == xbmc.getLocalizedString(20066):
                    state['opened'] = True
                    # Opening a native modal synchronously would hold this
                    # observer until the parent closes it, so it could never
                    # apply the defaults inside that dialog.
                    xbmc.executebuiltin('SendClick(10130,{})'.format(ident))
                    return True
            except RuntimeError:
                break  # native settings are generated consecutively
            except AttributeError:
                continue
        return False
    if dialog == 12000 and state.get('settings_pending'):
        if xbmc.getInfoLabel('Control.GetLabel(1)') != xbmc.getLocalizedString(20043):
            return False
        window = xbmcgui.Window(12000)
        # Native Select uses list 3 for text choices, list 6 for detailed rows.
        for ident in (3, 6):
            listing = window.getControl(ident)
            if not listing.isVisible():
                continue
            # Python ControlList.size/getListItem expose Python-owned items,
            # not Kodi's native list. Focus Kodi's All row and verify its label
            # through the native information manager before selecting it.
            xbmc.executebuiltin('SetFocus({},1,absolute)'.format(ident), True)
            if xbmc.getInfoLabel('Container({}).ListItem.Label'.format(ident)) != xbmc.getLocalizedString(593):
                return False
            xbmc.executebuiltin('Action(Select,12000)', True)
            state['settings_pending'] = False
            state['completed'] = True
            return True
        return False
    if dialog != 10131:
        state['in_locks'] = False
        return False
    if state.get('in_locks') or state.get('completed'):
        return False
    window = xbmcgui.Window(10131)
    try:
        # Kodi's shared native dialog starts at -180, with a separator -179.
        # Validate both named buttons and every radio before changing anything;
        # an unknown dialog/version is left entirely to the parent.
        if (window.getControl(-180).getLabel() != xbmc.getLocalizedString(20068) or
                window.getControl(-173).getLabel() != xbmc.getLocalizedString(20043)):
            return False
        radios = {i: window.getControl(i) for i in (-178, -177, -176, -175, -174, -172)}
        if not all(hasattr(c, 'isSelected') and hasattr(c, 'setSelected') for c in radios.values()):
            return False
        state['in_locks'] = True
        for ident in (-178, -176, -175, -174, -172):
            control = radios[ident]
            if not control.isSelected():
                control.setSelected(True)
                # Notify Kodi's settings model so the value survives Save.
                xbmc.executebuiltin('SendClick(10131,{})'.format(ident), True)
        state['settings_pending'] = True
        xbmc.executebuiltin('SendClick(10131,-173)')
    except (RuntimeError, AttributeError):
        return False
    return True