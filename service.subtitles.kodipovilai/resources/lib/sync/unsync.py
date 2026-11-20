# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# "בטל סנכרון אוטומטי": put the unshifted subtitle back and keep the automatic fix away from this subtitle and cut.

import hashlib
import os
try:
    import xbmcgui
except ImportError:
    xbmcgui = None
from resources.lib.core import kodi, selection as core_selection
from resources.lib.sync.delivery_record import current_delivery_record, record_delivery
from resources.lib.sync.service import log, selection_snapshot
from resources.lib.sync.verdicts import store_veto


MSG_NONE = 'אין סנכרון אוטומטי לביטול'
MSG_DONE = 'הסנכרון האוטומטי בוטל'
MSG_FAILED = 'לא ניתן לבטל את הסנכרון האוטומטי'


def _say(msg):
    """Always shown, whatever the subtitle-notifications switch says: it answers a button the viewer pressed."""
    try:
        if xbmcgui is not None:
            xbmcgui.Dialog().notification(kodi.DIALOG_TITLE, msg, xbmcgui.NOTIFICATION_INFO, 3500, False)
    except Exception:
        pass


def _text_hash(path):
    """The sub_hash half of a verdict key (verdicts.cache_key): sha1 of the subtitle text."""
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    return hashlib.sha1(text.encode('utf-8', 'replace')).hexdigest()[:16]


def cancel_auto_sync(selection=None, announce=True):
    """Restore the original (unshifted) copy of the CURRENT subtitle and veto its automatic sync.

    1. the original file goes back through core.selection.apply_subtitle_file (Kodi replaces its external
       subtitle slot), only while the exact selection and stream are still live;
    2. only then: the ``subs.sync.*`` window properties and the FIXED status are cleared, the delivery
       record is reset to scale 1.0 / offset 0 and every verdict key of the fix (sub_hash|cut_signature)
       gets a local veto (verdicts.store_veto), so replaying the same cut is delivered as it is.
    Local only: nothing is reported to the community pool. Returns True when the original is back."""
    try:
        selection = selection_snapshot() if selection is None else selection
        undo = core_selection.get_sync_undo(selection)
        status = core_selection.get_subtitle_sync_status() or {}
        if not undo or status.get('state') != 'fixed':
            if announce:
                _say(MSG_NONE)
            return False
        original = undo.get('original') or ''
        keys = [k for k in (undo.get('keys') or []) if k]
        try:
            digest = _text_hash(original) if original and os.path.isfile(original) else ''
        except Exception:
            digest = ''
        # The file must still be the one the fix was made from (generic names such as TempSubtitle.he.srt are
        # reused by later downloads), else restoring it would put another subtitle on screen.
        if not digest or not any(k.split('|', 1)[0] == digest for k in keys):
            log('unsync: the original subtitle is gone or was replaced', level='WARNING')
            if announce:
                _say(MSG_FAILED)
            return False

        record = current_delivery_record()
        core_selection.stage_subtitle_delivery(original, selection=selection, status='', source='user')
        if not core_selection.apply_subtitle_file(original, selection=selection, abandon_on_failure=False):
            core_selection.clear_subtitle_delivery(original, selection=selection)
            log('unsync: Kodi did not take the original subtitle back', level='WARNING')
            if announce:
                _say(MSG_FAILED)
            return False

        for key in keys:
            store_veto(key)
        core_selection.clear_subtitle_sync_status()
        if record:
            record_delivery(record.get('info') or {}, record.get('playing') or '',
                            record.get('key') or keys[0], 1.0, 0.0,
                            cut_signature=record.get('cut_signature') or '',
                            mode='global', selection=selection)
        log('unsync: the viewer cancelled the automatic sync (%d key(s) vetoed)' % len(keys))
        if announce:
            _say(MSG_DONE)
        return True
    except Exception as e:
        log('unsync failed: %r' % e, level='WARNING')
        if announce:
            _say(MSG_FAILED)
        return False
