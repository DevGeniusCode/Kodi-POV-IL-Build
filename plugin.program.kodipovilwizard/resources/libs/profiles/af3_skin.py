"""Runtime profile UI for Arctic Fuse 3, the one build skin we do not ship.

NOX, Estuary and FENtastic carry these changes in their own source. AF3 is
installed on demand and updated over the network by Kodi, which overwrites
them, so patch() re-applies them idempotently. Delete this module, and its
callers in hooks.py and build_skin.py, if AF3 is ever baked in or dropped.

What it applies (see skin_xml_runtime_modifications_spec.md for the contract):
  Home.xml         profile bootstrap onload; adult controls wrapped in group
                   9879; the shared child layout (group 9880); child focus.
  LoginScreen.xml  replaced by the branded native-list-52 picker design.
  Fonts            the shared layouts' font names resolved against AF3.
"""
import copy
import os
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcvfs

from resources.libs.profiles import child_profiles, profile_age_guard

WIZARD = 'plugin.program.kodipovilwizard'
SKIN = 'skin.arctic.fuse.3'
FOLDERS = ('xml', '1080i', '720p')
BOOTSTRAP = 'RunScript(special://home/addons/' + WIZARD + '/profile_bootstrap.py)'
FOCUS = 'SetFocus(9500)'
# Logical fonts of the shared layouts and the size each should resolve to.
FONT_TARGETS = {'font60': 60, 'font37': 37, 'font13': 30, 'font12': 25}
# AF3 declares its fonts in parameterized includes instead of a fontset.
INCLUDE_FONTS = {'font60': 'font_title_midi', 'font37': 'font_head_bold',
                 'font13': 'font_main', 'font12': 'font_mini'}
_FONT_EXCLUDE = ('icon', 'awesome', 'codec', 'nick-titling')


def patch():
    """Apply the profile UI to AF3 if installed. Idempotent; never raises
    for a skin file it cannot read or parse (that skin is retried next call).

    Reloads the skin when AF3 is active in a child profile and its Home
    changed, so the child layout appears without a restart.
    """
    addons = xbmcvfs.translatePath('special://home/addons/')
    home_changed = False
    for folder in FOLDERS:
        base = os.path.join(addons, SKIN, folder)
        home = os.path.join(base, 'Home.xml')
        if os.path.isfile(home):
            try:
                home_changed = _patch_home(home) or home_changed
            except (OSError, ValueError, ET.ParseError):
                xbmc.log('[Profiles] AF3 Home hook deferred', xbmc.LOGWARNING)
        login = os.path.join(base, 'LoginScreen.xml')
        if os.path.isfile(login):
            try:
                _patch_login(login)
            except (OSError, ValueError, ET.ParseError):
                xbmc.log('[Profiles] AF3 login design deferred', xbmc.LOGWARNING)
    if (home_changed and xbmc.getSkinDir() == SKIN and
            profile_age_guard.active_policy() is not None):
        xbmc.executebuiltin('ReloadSkin()')


def _patch_home(path):
    """Keep the adult Home behind the profile flag and add the child Home."""
    root = ET.parse(path).getroot()
    controls = root.find('controls')
    if controls is None:
        return False  # not the window structure this patch knows
    if controls.find("control[@id='9879']") is None:
        adult = ET.Element('control', type='group', id='9879')
        ET.SubElement(adult, 'visible').text = '!Skin.HasSetting(POVILChild)'
        for row in list(controls):
            controls.remove(row)
            adult.append(row)
        controls.append(adult)
    stale = controls.find("control[@id='9880']")
    if stale is not None:
        controls.remove(stale)
    controls.append(_child_control(path))
    if not any(row.text == FOCUS for row in root.findall('onload')):
        row = ET.Element('onload', condition='Skin.HasSetting(POVILChild)')
        row.text = FOCUS
        root.insert(0, row)
    if not any('profile_bootstrap.py' in (row.text or '') for row in root.findall('onload')):
        row = ET.Element('onload')
        row.text = BOOTSTRAP
        root.insert(0, row)
    return child_profiles.write_xml(path, root)


def _patch_login(path):
    """Replace presentation only; native list 52 still owns loading and PINs."""
    original = ET.parse(path).getroot()
    if original.find(".//control[@id='52']") is None:
        return False  # unknown third-party login contract
    design = ET.parse(_template('ProfileLogin.xml')).getroot()
    # Keep the skin's own startup actions.
    for index, action in enumerate(original.findall('onload')):
        design.insert(index, action)
    _adapt_fonts(design, os.path.dirname(path))
    return child_profiles.write_xml(path, design)


def _template(name):
    return os.path.join(xbmcaddon.Addon(WIZARD).getAddonInfo('path'),
                        'resources', 'skins', 'Default', '1080i', name)


def _child_control(path):
    child = copy.deepcopy(ET.parse(_template('ChildHome.xml')).getroot().find('controls/control'))
    for button in child.findall(".//control[@type='button']"):
        ET.SubElement(button, 'textcolor').text = 'FFFFFFFF'
        ET.SubElement(button, 'focusedcolor').text = 'FFF0D394'
    _adapt_fonts(child, os.path.dirname(path))
    return child


def _skin_fonts(path):
    if not os.path.isfile(path):
        return []
    return [(f.findtext('name'), float(f.findtext('size', '30')))
            for f in ET.parse(path).findall('.//fontset/font')
            if not any(term in (f.findtext('name', '') + f.findtext('filename', '')).lower()
                       for term in _FONT_EXCLUDE)]


def _adapt_fonts(layout, folder):
    """Resolve the shared layouts' font names against AF3's own fonts."""
    fonts = _skin_fonts(os.path.join(folder, 'Font.xml'))
    includes = os.path.join(folder, 'Includes_Font.xml')
    if fonts:
        for row in layout.iter('font'):
            wanted = FONT_TARGETS.get(row.text)
            if wanted is not None:
                row.text = min(fonts, key=lambda f: (abs(f[1] - wanted), f[0] != row.text))[0]
    elif os.path.isfile(includes):
        # Use only names the skin declares, not a nonexistent font60.
        available = {f.findtext('name') for f in ET.parse(includes).findall('.//font')}
        for row in layout.iter('font'):
            if INCLUDE_FONTS.get(row.text) in available:
                row.text = INCLUDE_FONTS[row.text]
