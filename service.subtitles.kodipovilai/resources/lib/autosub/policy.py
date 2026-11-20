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
# The top overlay is ONE label, 150 px high, vertically centred, and the engine's own underscore line sits under the
# text: more than three lines of text push the first and the last one out of the box. A banner is therefore at most
# three lines: the heading, the subtitle's name (cut at BANNER_NAME_MAX characters, two wrapped lines at most) and ONE
# line that holds the match percentage and the cache note.
BANNER_NAME_MAX = 90


def banner_name(payload, row):
    """The subtitle's name for the top banner, whatever kind of row it is: the link's own `filename`, else its `release`
    (a pool row carries only that), else the tail of the row's label ("<provider> · 85%  —  <release>"), else the label
    itself. '' only when the row has nothing at all. Pure."""
    payload = payload if isinstance(payload, dict) else {}
    for key in ('filename', 'release'):
        value = str(payload.get(key) or '').strip()
        if value:
            return value
    label = str((row or {}).get('filename') or '').strip()
    for sep in ('  —  ', ' — ', '—'):
        if sep in label:
            tail = label.split(sep, 1)[1].strip()
            if tail:
                return tail
    return label


def banner_pct(pct):
    """The match number of the chosen row as an int 0-100, None when the row has none (an embedded track, a row with no
    release name, the 101 of the embedded marker)."""
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        return None
    pct = int(pct)
    return pct if 0 < pct <= 100 else None


def banner_text(heading, name, pct, from_cache):
    """The top banner once a subtitle is on screen: the heading, the subtitle's name (on a cache hit too), and one line
    with the match percentage and the cache note. Pure."""
    lines = [heading]
    name = (name or '').strip()
    if len(name) > BANNER_NAME_MAX:
        name = name[:BANNER_NAME_MAX - 3] + '...'
    if name:
        lines.append(name)
    info = []
    pct = banner_pct(pct)
    if pct is not None:
        info.append('אחוזי התאמה %d%%' % pct)
    if from_cache:
        info.append('נטענה מהקאש')
    if info:
        lines.append(' · '.join(info))
    return '[COLOR lightblue]' + '\n'.join(lines) + '[/COLOR]'


# The timing check of the subtitle on screen is announced with two corner
# toasts (no overlay over the picture): one when it starts, one when it ends.
SYNC_CHECK_START_TOAST_MS = 2500
SYNC_CHECK_END_TOAST_MS = 3000
# The wait for the end of the check is bounded by this many seconds.  A check
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


# ---- Step 8: the minimum match number and the Wizdom / Ktuvit fast lane ----------------------------------------
# The two Israeli providers whose strong match autosub applies without waiting for the slower sources (the same
# names fetching.bridge.FAST_PROVIDERS hands to the fast lane).
FAST_SOURCES = ('ktuvit', 'wizdom')
# The lowest match number the fast lane ever accepts (the setting autosub_min_match_pct can only raise it).
FAST_MIN_PCT = 90
# A first hit below 100 waits this long for the other Israeli provider, so a 100 arriving a moment later wins
# without a swap.
FAST_GRACE_S = 1.0
# A swap after the full list needs a candidate at least this many points above the applied one (the same
# "clear win" margin as sync.candidates.rank_ready_candidates).
SWAP_MARGIN = 4


def min_match_pct(settings):
    """autosub_min_match_pct, 0..100 (0 = no limit, the behaviour before Step 8)."""
    try:
        return max(0, min(100, int(settings.get_int('autosub_min_match_pct', 0))))
    except Exception:
        return 0


def fast_apply_enabled(settings):
    """autosub_fast_apply (default on)."""
    try:
        return bool(settings.get_bool('autosub_fast_apply', True))
    except Exception:
        return True


def fast_threshold(min_pct):
    """The effective match number of the fast lane: max(90, the user's minimum)."""
    return max(FAST_MIN_PCT, int(min_pct or 0))


def row_pct(row):
    """The row's match number, None when it has none (embedded, a file beside the video, an AI action)."""
    value = (row or {}).get('pct')
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def passes_min(row, min_pct):
    """False only for a row that HAS a match number below the minimum; rows without one cannot be judged and pass."""
    if not min_pct:
        return True
    pct = row_pct(row)
    return pct is None or pct >= min_pct


def filter_min(rows, min_pct):
    """(kept rows, number skipped): the rows autosub may pick at the minimum match number, in their order."""
    kept = [row for row in rows or [] if passes_min(row, min_pct)]
    return kept, len(rows or []) - len(kept)


def _payload(row):
    from resources.lib.core.link_codec import decode_link_safe
    payload = decode_link_safe((row or {}).get('link') or '') or {}
    return payload if isinstance(payload, dict) else {}


def fast_payload(row, info, threshold):
    """The link payload of a row the fast lane may apply, else None: a human Hebrew subtitle that came straight from
    the 'ktuvit' or 'wizdom' provider (not a pooled copy, not machine translated, not the video's own track), whose
    match number reaches `threshold` and whose name does not point at another episode."""
    from resources.lib.sync import candidates
    payload = _payload(row)
    if payload.get('type') != 'engine' or payload.get('embedded'):
        return None
    if str(payload.get('source') or '').strip().lower() not in FAST_SOURCES:
        return None
    if not candidates.is_human_hebrew_candidate(payload):
        return None
    pct = row_pct(row)
    if pct is None or pct < threshold:
        return None
    if candidates.names_other_episode(payload.get('filename') or '', info):
        return None
    return payload


def same_subtitle(a, b):
    """True when two link payloads are the same subtitle (a provider row and its twin in the full list)."""
    keys = ('type', 'source', 'filename', 'release', 'path', 'hash')
    return all((a.get(k) or '') == (b.get(k) or '') for k in keys)


def swap_choice(applied_payload, applied_pct, rows, info, playing, min_pct):
    """The ONE row that is strictly better than the subtitle the fast lane applied, or None: a human Hebrew subtitle
    at least SWAP_MARGIN points above it, over the minimum, not another episode; the closest full name wins a tie."""
    from resources.lib.scoring_matching.ranking import release_similarity
    from resources.lib.sync import candidates
    best = None
    for row in rows or []:
        payload = _payload(row)
        if not payload or same_subtitle(payload, applied_payload):
            continue
        if not candidates.is_human_hebrew_candidate(payload):
            continue
        pct = row_pct(row)
        if pct is None or pct < int(applied_pct or 0) + SWAP_MARGIN or not passes_min(row, min_pct):
            continue
        rel = payload.get('filename') or payload.get('release') or ''
        if candidates.names_other_episode(rel, info):
            continue
        key = (pct, release_similarity(playing, rel))
        if best is None or key > best[0]:
            best = (key, row)
    return best[1] if best else None


def swap_blocked(status, chooser_open):
    """Why a swap must not happen now, '' when it may: the viewer opened the chooser, or the applied subtitle already
    has a timing verdict that SubSync proved (a confirmation that only came from its release name is not one)."""
    if chooser_open:
        return 'chooser open'
    status = status or {}
    if status.get('state') in ('confirmed', 'fixed') and (status.get('source') or '') != 'release':
        return 'timing proven'
    return ''
