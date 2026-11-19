# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""Kodi's <playlisttimeout> as a Wizard setting (Maintenance -> Kodi).

`playlist_timeout` (seconds, default 0) is the single source of truth. The value lives in `userdata/advancedsettings.xml`,
inside the fence the build has always used for it:

    <!-- ======== WIZARD IL BAKED: playlisttimeout ... ======== -->
    <playlisttimeout>0</playlisttimeout>
    <!-- ======== END WIZARD IL BAKED ======== -->

The baked file ships the default (0), so a fresh install is right before this module ever runs. PlaylistTimeoutSync is polled by
the host-repair runner's idle loop (host_repairs/flow/runner.py): a change of the setting, or a config pack that replaced the
file with the baked 0, is written back into the fenced value within seconds. Kodi reads advancedsettings.xml only at start, so
a change takes effect after one Kodi restart (the setting's label says so).

Text edits only, never ElementTree: the file carries comments and fences that a parse-and-write would drop. A <playlisttimeout>
outside our fence is the owner's own and is never touched.
"""
import os
import re

try:
    import xbmc
except ImportError:
    xbmc = None

SETTING_ID = 'playlist_timeout'
DEFAULT_SECONDS = 0
KODI_DEFAULT_SECONDS = 20       # what Kodi uses when the tag is absent
MAX_SECONDS = 600

START = '<!-- ======== WIZARD IL BAKED: playlisttimeout (the Wizard setting playlist_timeout owns the value below; 0 turns off the 20 s timer behind Kodi\'s "playback failed" after backing out of a source list, playlistretries still stops a runaway playlist) ======== -->'
END = '<!-- ======== END WIZARD IL BAKED ======== -->'

_BLOCK = re.compile(
    r'(<!--\s*=+\s*WIZARD IL BAKED: playlisttimeout(?:(?!-->).)*-->\s*<playlisttimeout>)\s*([^<]*?)\s*(</playlisttimeout>\s*'
    r'<!--\s*=+\s*END WIZARD IL BAKED\s*=+\s*-->)', re.S)
_ANY_TAG = re.compile(r'<playlisttimeout[\s>]')
_CLOSE = re.compile(r'</advancedsettings\s*>')


def _log(msg, level=None):
    try:
        from resources.libs.common import logging
        logging.log('[PlaylistTimeout] ' + msg, level=level if level is not None else xbmc.LOGINFO)
    except Exception:
        pass


def parse(raw):
    """The setting's value as a clamped int of seconds; anything unreadable is the default."""
    try:
        v = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_SECONDS
    return max(0, min(MAX_SECONDS, v))


def _block(value, nl):
    return nl.join(['\t' + START, '\t<playlisttimeout>%d</playlisttimeout>' % value, '\t' + END])


def edit(text, value):
    """(new_text, reason): `text` of an advancedsettings.xml (or None when the file is absent) with the fenced value set to
    `value`. new_text is None when nothing should be written; reason says why (for the log). Pure, no I/O."""
    if text is None:
        if value == KODI_DEFAULT_SECONDS:
            return None, 'no file and the value is Kodi\'s own default'
        return '<advancedsettings>\n%s\n</advancedsettings>\n' % _block(value, '\n'), 'file created'
    nl = '\r\n' if '\r\n' in text else '\n'
    m = _BLOCK.search(text)
    if m:
        if m.group(2) == str(value):
            return None, 'already %d' % value
        return text[:m.start(2)] + str(value) + text[m.end(2):], 'value %s -> %d' % (m.group(2) or '(empty)', value)
    if _ANY_TAG.search(text):
        return None, 'a <playlisttimeout> outside the Wizard fence is the owner\'s own: left alone'
    close = None
    for close in _CLOSE.finditer(text):
        pass
    if close is None:
        return None, 'advancedsettings.xml has no closing tag: left alone'
    if value == KODI_DEFAULT_SECONDS:
        return None, 'no block and the value is Kodi\'s own default'
    return text[:close.start()] + _block(value, nl) + nl + text[close.start():], 'block inserted (%d)' % value


def _read(path):
    try:
        with open(path, 'rb') as f:
            return f.read().decode('utf-8')
    except FileNotFoundError:
        return None


def apply(value, path=None):
    """Writes `value` seconds into the fenced block. True if the file changed. Never raises. The result is parsed back as XML
    first and the old file is replaced atomically, so a mistake here cannot leave Kodi a broken advancedsettings.xml."""
    try:
        if path is None:
            from resources.libs.common.config import CONFIG
            path = CONFIG.ADVANCED
        old = _read(path)
        new, why = edit(old, value)
        if new is None:
            return False
        from xml.etree import ElementTree
        root = ElementTree.fromstring(new.encode('utf-8'))
        if root.tag != 'advancedsettings':
            raise ValueError('root is %r' % root.tag)
        tmp = path + '.wizard.tmp'
        with open(tmp, 'wb') as f:
            f.write(new.encode('utf-8'))
        os.replace(tmp, path)
        _log('advancedsettings.xml: %s (takes effect at the next Kodi start)' % why)
        return True
    except Exception as e:
        _log('could not update advancedsettings.xml: %s' % e, level=getattr(xbmc, 'LOGWARNING', None))
        return False


class PlaylistTimeoutSync(object):
    """Polled from the runner's idle loop. `read_setting` returns the raw setting string ('' if unset)."""

    def __init__(self, read_setting, path=None):
        self._read = read_setting
        self._path = path
        self._seen = None

    def _file_stamp(self):
        try:
            if self._path is None:
                from resources.libs.common.config import CONFIG
                self._path = CONFIG.ADVANCED
            st = os.stat(self._path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def poll(self):
        """Cheap when nothing changed: one setting read and one stat. Returns True if it wrote the file."""
        try:
            value = parse(self._read())
            stamp = (value, self._file_stamp())
            if stamp == self._seen:
                return False
            changed = apply(value, self._path)
            self._seen = (value, self._file_stamp())
            return changed
        except Exception:
            return False
