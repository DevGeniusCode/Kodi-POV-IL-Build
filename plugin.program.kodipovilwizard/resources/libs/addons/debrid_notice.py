# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""RESTORED FIX (Phase 3, step 4): the debrid-expiry notice settings dialog.

The handler was lost from the subtitle add-on's default.py in the merge of 2026-11-07 (merge 4e117cf) while six callers
(af3_tools, the favourites config, favourites.xml, two skin-variable baselines, the AF3 home patcher) still invoked it.
The code below is taken unchanged from git history (6bbc468:service.subtitles.kodipovilai/default.py) and is now served by
Router.dispatch for mode=debrid_notice_settings.
"""
import xbmcaddon
import xbmcgui


DEBRID_NOTICE_SERVICES = (
    ('Real-Debrid', 'rd.expires'),
    ('TorBox', 'tb.expires'),
    ('Premiumize', 'pm.expires'),
    ('AllDebrid', 'ad.expires'),
)


DEBRID_NOTICE_VALUES = (
    ('בכל כניסה לקודי', '0'),
    ('יום אחד לפני סיום', '1'),
    ('3 ימים לפני סיום', '3'),
    ('7 ימים לפני סיום', '7'),
    ('14 ימים לפני סיום', '14'),
    ('30 ימים לפני סיום', '30'),
    ('60 ימים לפני סיום', '60'),
    ('90 ימים לפני סיום', '90'),
    ('180 ימים לפני סיום', '180'),
    ('365 ימים לפני סיום', '365'),
)


def _pov_addon():
    try:
        return xbmcaddon.Addon('plugin.video.pov')
    except Exception:
        return None


def _notice_value_label(value):
    for label, stored in DEBRID_NOTICE_VALUES:
        if str(value) == stored:
            return label
    return '{0} ימים לפני סיום'.format(value)


def _handle_debrid_notice_settings(_params):
    """Build UI for debrid expiry notification thresholds.

    POV stores the values as rd/tb/pm/ad.expires, but some POV builds do
    not expose those settings in Addon.OpenSettings(). This dialog writes
    the same POV settings directly so the startup notifier can read them.
    """
    pov = _pov_addon()
    if pov is None:
        xbmcgui.Dialog().ok(
            'התראות מנוי',
            'לא נמצא plugin.video.pov. התקן/עדכן את הבילד ונסה שוב.')
        return

    dialog = xbmcgui.Dialog()
    service_rows = []
    for label, key in DEBRID_NOTICE_SERVICES:
        try:
            current = pov.getSetting(key) or '0'
        except Exception:
            current = '0'
        service_rows.append('{0}  -  {1}'.format(
            label, _notice_value_label(current)))

    idx = dialog.select('הגדרת התראות מנוי', service_rows)
    if idx < 0:
        return

    service_label, key = DEBRID_NOTICE_SERVICES[idx]
    options = [
        '{0} ({1})'.format(label, stored)
        for label, stored in DEBRID_NOTICE_VALUES
    ]
    value_idx = dialog.select(
        'מתי להתריע עבור {0}?'.format(service_label), options)
    if value_idx < 0:
        return

    value = DEBRID_NOTICE_VALUES[value_idx][1]
    try:
        pov.setSetting(key, value)
        # Kodi's legacy slider silently rejects values outside its schema.
        # setSetting returns None even on rejection, so verify the read-back.
        if pov.getSetting(key) != value:
            dialog.ok(
                'התראות מנוי',
                'הבחירה לא נשמרה. יש להשלים את עדכון הבילד, לסגור ולפתוח את Kodi ולבחור שוב.')
            return
    except Exception as e:
        xbmcgui.Dialog().ok(
            'התראות מנוי',
            'שמירת ההגדרה נכשלה: {0}'.format(e))
        return

    if value == '0':
        msg = '{0}: התראה בכל כניסה לקודי'.format(service_label)
    else:
        msg = '{0}: התראה רק כשנותרו עד {1} ימים'.format(
            service_label, value)
    try:
        from resources.libs.host_repairs import kodi_utils
        kodi_utils.notify(msg, title='התראות מנוי', time_ms=5000)
    except Exception:
        dialog.notification('התראות מנוי', msg, time=5000)
