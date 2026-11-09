"""Build defaults for Kodi-owned profiles, without copying personal accounts.

Kodi owns profiles.xml and the in-memory profile list. This module never edits
that file: native creation/deletion/locks remain authoritative. Only missing
build files are seeded, and only beneath the registered profile directory.

Defaults come from the hash-verified config pack that the engine retains
(``config_apply.open_verified_pack``). This module receives that pack as an
argument and imports nothing from the engine.
"""
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile

WIZARD = 'plugin.program.kodipovilwizard'
DEFAULT_SKIN = 'skin.povil.nox'
NAVIGATOR = 'addon_data/plugin.video.pov/navigator.db'
SHORTCUT_RECEIPT = 'addon_data/plugin.video.pov/povil-shortcuts-defaults-v1.json'

# Everything a seed can raise for a bad profile folder, pack or POV database.
SEED_ERRORS = (OSError, ValueError, KeyError, ET.ParseError, sqlite3.Error, zipfile.BadZipFile)


def _seed_shortcuts(profile, pack):
    """Add the build's missing POV shortcut folders; never copy accounts,
    history or whole databases."""
    if NAVIGATOR not in pack:
        return 0
    receipt = _inside(profile, SHORTCUT_RECEIPT)
    if os.path.isfile(receipt):
        return 0
    database = _inside(profile, NAVIGATOR)
    os.makedirs(os.path.dirname(database), exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(database), suffix='.db', delete=False) as handle:
            pending = handle.name
            handle.write(pack.read(NAVIGATOR))
        with closing(sqlite3.connect(pending)) as conn:
            defaults = conn.execute('SELECT list_name,list_type,list_contents FROM navigator '
                                    'WHERE list_type=?', ('shortcut_folder',)).fetchall()
        existed = os.path.isfile(database)
        with closing(sqlite3.connect(database, timeout=1)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            if os.path.isfile(receipt):
                return 0
            conn.execute('CREATE TABLE IF NOT EXISTS navigator (list_name text, list_type text, '
                         'list_contents text, UNIQUE(list_name,list_type))')
            if [r[1] for r in conn.execute('PRAGMA table_info(navigator)')] != [
                    'list_name', 'list_type', 'list_contents']:
                raise ValueError('Unknown POV navigator schema')
            current = {(r[0], r[1]) for r in conn.execute('SELECT list_name,list_type FROM navigator')}
            updates = [row for row in defaults if (row[0], row[1]) not in current]
            if updates and existed:
                backup = _inside(profile, 'addon_data/plugin.video.pov/navigator.before-build-shortcuts-v1.db')
                if not os.path.isfile(backup):
                    with closing(sqlite3.connect(database)) as reader:
                        with closing(sqlite3.connect(backup)) as dest:
                            reader.backup(dest)
            conn.executemany('INSERT OR IGNORE INTO navigator VALUES (?,?,?)', updates)
            conn.commit()
        write_missing(profile, SHORTCUT_RECEIPT, json.dumps(
            dict(version=1, folders=[row[0] for row in updates]), ensure_ascii=False).encode('utf8'))
        return len(updates)
    finally:
        if pending and os.path.isfile(pending):
            os.remove(pending)


def registered_profiles(master):
    root = ET.parse(os.path.join(master, 'profiles.xml')).getroot()
    result = []
    for element in root.findall('profile'):
        result.append(dict(id=int(element.findtext('id', '-1')),
                           name=element.findtext('name', ''),
                           directory=element.findtext('directory', ''),
                           lockmode=int(element.findtext('lockmode', '0')),
                           protected=(int(element.findtext('lockmode', '0')) > 0 and
                                      element.findtext('lockcode', '') not in ('', '-')),
                           locksettings=int(element.findtext('locksettings', '0')),
                           lockfiles=element.findtext('lockfiles') == 'true',
                           lockaddonmanager=element.findtext('lockaddonmanager') == 'true'))
    return result


def profile_path(master, profile):
    if profile['id'] == 0:
        return os.path.realpath(master)
    directory = profile['directory'].replace('\\', '/')
    if not directory or '://' in directory:
        raise ValueError('Unsupported profile directory')
    base = os.path.realpath(master)
    dest = os.path.realpath(os.path.join(base, directory))
    if os.path.commonpath([base, dest]) != base or dest == base:
        raise ValueError('Profile directory is outside userdata')
    return dest


def _inside(root, relative):
    if (not relative or '\\' in relative or ':' in relative or
            relative.startswith('/') or any(p in ('', '.', '..') for p in relative.split('/'))):
        raise ValueError('Invalid build profile path')
    base = os.path.realpath(root)
    dest = os.path.realpath(os.path.join(base, *relative.split('/')))
    if os.path.commonpath([base, dest]) != base or dest == base:
        raise ValueError('Build profile path escapes its directory')
    return dest


def write_missing(root, relative, content):
    """Create without replacing a concurrent/user file; clean partial failures."""
    path = _inside(root, relative)
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    path = _inside(root, relative)  # validate again after mkdir (symlinks)
    fd, tmp = tempfile.mkstemp(prefix='.profile-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        except OSError:
            # Android emulated storage can prohibit hardlinks. Exclusive
            # creation still never overwrites an existing account/settings.
            try:
                fh = open(path, 'xb')
            except FileExistsError:
                return False
            try:
                with fh:
                    fh.write(content)
                    fh.flush()
                    os.fsync(fh.fileno())
            except BaseException:
                os.unlink(path)
                raise
        return True
    finally:
        os.unlink(tmp)


def _replace(path, content):
    """Atomically replace an existing file with ``content`` (bytes)."""
    fd, temporary = tempfile.mkstemp(prefix='.profile-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def seed_profile(master, profile, pack, build, prepare_login=False):
    """Repair a secondary profile using shipped defaults, never master tokens.

    ``pack`` is an opened ``config_apply.VerifiedPack``; ``build`` supplies the
    installed identity (``buildname``, ``buildversion``). Excludes
    watched/history/cache DBs and per-device advancedsettings. The receipt is
    written last, only when the master build is provisioned. An interrupted
    seed can resume, preserving every existing target setting.
    """
    target = profile_path(master, profile)
    if profile['id'] == 0:
        return False
    if not os.path.isfile(os.path.join(master, 'kodipovil.provisioned')):
        raise ValueError('Complete the master build installation first')
    gui = pack.guisettings()
    if gui is None:
        raise ValueError('The build config pack has no guisettings.xml')
    os.makedirs(target, exist_ok=True)
    initializing = not os.path.isfile(os.path.join(target, 'kodipovil.profile_addons_ready'))
    completing_home = not os.path.isfile(os.path.join(target, 'kodipovil.profile_home_ready'))
    # Keep installed device settings supplied by native "copy from master";
    # fresh native settings are also respected. Apply only build defaults.
    for name in pack.names():
        allowed = (name == 'favourites.xml' or name == 'sources.xml' or
                   name.startswith('keymaps/') or
                   name == 'addon_data/script.fentastic.helper/cpath_cache.db' or
                   (name.startswith('addon_data/') and name.endswith('/settings.xml')))
        if not allowed:
            continue
        content = pack.read(name)
        if name == 'favourites.xml':
            favourites = ET.fromstring(content)
            tile = ET.Element('favourite', {
                'name': '[B]מי צופה? — פרופילים[/B]',
                'thumb': 'special://home/addons/' + WIZARD + '/resources/skins/Default/media/DefaultUser.png'})
            tile.text = 'RunPlugin(plugin://' + WIZARD + '/?mode=profiles)'
            if not any('mode=profiles' in (row.text or '') for row in favourites):
                favourites.insert(1, tile)
            content = ET.tostring(favourites, encoding='utf-8', xml_declaration=True)
            existing = _inside(target, name)
            # Kodi can create an empty file before our first seed.
            # An established profile's deliberate deletions remain intact.
            if initializing and os.path.isfile(existing) and not ET.parse(existing).getroot().findall('favourite'):
                _replace(existing, content)
        write_missing(target, name, content)
    # Native "start fresh" does not save guisettings until first login.
    # Seed the verified build defaults before that login, so the new
    # profile starts in NOX with Hebrew/subtitles instead of bare Kodi.
    # Existing native/user settings are never replaced.
    if completing_home:
        write_missing(target, 'kodipovil.profile_gui_defaults.xml', gui)
    write_missing(target, 'guisettings.xml', gui)
    ET.parse(_inside(target, 'guisettings.xml'))
    if completing_home and prepare_login:
        # Only the master calls this before native LoadProfile. Set the
        # inactive profile's initial skin before Kodi reads its settings,
        # instead of first showing the copied/stock Estuary interface.
        path = _inside(target, 'guisettings.xml')
        root = ET.parse(path).getroot()
        row = root.find("setting[@id='lookandfeel.skin']")
        if row is None:
            row = ET.SubElement(root, 'setting', {'id': 'lookandfeel.skin'})
        row.text = DEFAULT_SKIN
        row.attrib.pop('default', None)
        _replace(path, ET.tostring(root, encoding='utf-8', xml_declaration=True))
    _seed_shortcuts(target, pack)
    # Kodi uses the target add-on settings on next LoadProfile. Do not clone
    # the master's complete wizard settings (queues, update receipts, flags).
    defaults = dict(buildname=build['buildname'], installed='true',
                    buildversion=build['buildversion'],
                    config_applied_version=str(pack.version),
                    build_skin_switch_notifcation_dismiss='true')
    settings = ET.Element('settings', {'version': '2'})
    for key, value in defaults.items():
        ET.SubElement(settings, 'setting', {'id': key}).text = value
    relative = 'addon_data/' + WIZARD + '/settings.xml'
    existing = _inside(target, relative)
    if initializing and os.path.isfile(existing):
        root = ET.parse(existing).getroot()
        for key, value in defaults.items():
            row = root.find("setting[@id='{}']".format(key))
            if row is None:
                row = ET.SubElement(root, 'setting', {'id': key})
            row.text = value
        # Only small build bootstrap flags, never accounts or preferences.
        _replace(existing, ET.tostring(root, encoding='utf-8', xml_declaration=True))
    else:
        write_missing(target, relative, ET.tostring(settings, encoding='utf-8', xml_declaration=True))
    write_missing(target, 'kodipovil.provisioned', str(pack.version).encode('utf-8'))
    return True