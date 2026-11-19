# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Autosub policy: the toast switch, what to do when there is no Hebrew (ai_when_no_hebrew),
# the ask-before-translating dialog and the sync-check overlay texts.



# 'ask' (ai_when_no_hebrew): a yes/no over the playing video that closes
# itself, as a 'no', after this many milliseconds. It never pauses playback.
_ASK_AI_CLOSE_MS = 10000

# The top overlay's status line once a subtitle is on screen, by the state of
# its timing at that moment ...
LOADED_HEADINGS = {
    'checking': 'כתובית נטענה · בודק סנכרון',
    'confirmed': 'כתובית מסונכרנת',
    'fixed': 'כתובית סונכרנה אוטומטית',
    'unverified': 'כתובית נטענה · התזמון טרם אומת',
}
# ... and once a check that was still running at that moment has ended.
CHECKED_HEADINGS = {
    'confirmed': 'כתובית מסונכרנת',
    'fixed': 'כתובית סונכרנה אוטומטית',
    'unverified': 'כתובית נטענה · הסנכרון לא אומת',
}
# What the overlay says when the pick was the video's own subtitle and it gave
# nothing (translate.embedded_miss_since: the extractor's reason).
EMBEDDED_MISS_LINES = {
    'no_index': 'את הכתובית המובנית בקובץ הזה אי אפשר לקרוא מהסטרים\n'
                'ואין כתובית אחרת לתרגם',
    'no_text': 'הכתובית המובנית בקובץ הזה היא תמונה ולא טקסט\n'
               'ואין כתובית אחרת לתרגם',
    'not_mkv': 'את הכתובית המובנית בקובץ מהסוג הזה עוד אי אפשר לקרוא\n'
               'ואין כתובית אחרת לתרגם',
    '': 'הקריאה של הכתובית המובנית לא הושלמה\n'
        'אפשר לבחור אותה שוב מרשימת הכתוביות',
}
# A status line stays this long (seconds) ...
OVERLAY_LINE_SECONDS = 5.0
# ... and "checking" stays until the check ends, this long at most.  A check
# that ends later still announces a fix with its own toast.
_SYNC_CHECK_OVERLAY_CAP = 25.0
# A status that reads empty this many times in a row is gone; one empty read
# can be a player call that failed once.
_STATUS_GONE_READS = 3


def wait_for_sync_check(read_state, still_ours, sleep,
                         cap=_SYNC_CHECK_OVERLAY_CAP, step=0.2):
    """Wait while the timing check of the subtitle on screen is running.

    Returns (state, waited).  state is what the check ended in ('confirmed',
    'fixed' or 'unverified'); '' when the cap passed or the status is no
    longer there; None when still_ours() said the overlay does not belong to
    this run any more (another pick, another film, a stop).  waited counts
    the steps slept, not the clock.
    """
    steps = max(1, int(round(cap / step)))
    gone = 0
    for done in range(steps + 1):        # one more read after the last sleep
        if not still_ours():
            return None, done * step
        state = read_state()
        if state in CHECKED_HEADINGS:
            return state, done * step
        if state == 'checking':
            gone = 0
        else:
            # No status: it left with the pick it belonged to (still_ours()
            # says so on the next round), or this one read failed.
            gone += 1
            if gone >= _STATUS_GONE_READS:
                return '', done * step
        if done < steps:
            sleep(step)
    return '', steps * step


def toasts_on(sub_utils):
    """The viewer's switch for subtitle toasts (sub_utils.notify obeys it)."""
    try:
        return (sub_utils.get_setting('subs_notifications', 'true')
                or 'true').strip().lower() != 'false'
    except Exception:
        return True


def ai_when_no_hebrew(sub_utils):
    """'auto' | 'ask' | 'manual': what the automatic subtitle does when there
    is no Hebrew at all. A user's per-video choice since 0.3.74: a translation
    costs requests, and the best foreign subtitle may not fit this release's
    timing -- another link with built-in Hebrew may be the better pick."""
    try:
        v = (sub_utils.get_setting('ai_when_no_hebrew', 'auto') or 'auto').strip().lower()
    except Exception:
        v = 'auto'
    return v if v in ('auto', 'ask', 'manual') else 'auto'


# AI rows the list shows as Hebrew -- the translation of the video's own
# foreign track, of a foreign file beside it -- are translations too
AI_ROW_TYPES = ('embedded_ai', 'ai')


def ask_ai_translation(google=False):
    """'ask': True only on an explicit yes. 'No' has the focus: an OK meant
    for the player must not spend requests."""
    try:
        import xbmcgui
        args = ('אין כתובית עברית',
                'לא נמצאה כתובית עברית לסרטון הזה. {0}[CR]'
                '(נסגר מעצמו תוך 10 שניות)'.format(
                    'לתרגם ב-Google Translate?' if google else 'לתרגם ב-AI?'))
        kw = {'nolabel': 'לא', 'yeslabel': 'לתרגם', 'autoclose': _ASK_AI_CLOSE_MS}
        try:
            return bool(xbmcgui.Dialog().yesno(
                *args, defaultbutton=xbmcgui.DLG_YESNO_NO_BTN, **kw))
        except (TypeError, AttributeError):
            return bool(xbmcgui.Dialog().yesno(*args, **kw))   # Kodi 19
    except Exception:
        return False
