# -*- coding: utf-8 -*-
"""
il_release.py - runtime release-countdown injector for POV list items

Location : addons/plugin.program.kodipovilwizard/resources/libs/patches/il_release.py
Called by: the hook patched into plugin.video.pov  (resources/lib/menus/movies.py)

    il_release.apply_release_property(imdb_id, listitem)

Sets these ListItem properties (skin side: ListItem.Property(...), case-insensitive):
    IL_release        "בעוד 7 ימים" | "יוצא מחר" | "יוצא היום!" | "שוחרר!" | ""   (main, per spec)
    IL_release_state  later | soon | today | released      -> lets the skin pick a banner colour
    IL_release_days   signed integer as text ("7", "0", "-3")

Design rules
  * NEVER raises - POV's build_movie_content() has a blanket `except: pass`, so an exception
    here would silently drop the movie from the list.
  * No network on the hot path. Reads il_releases.json (stat'ed at most every few seconds,
    re-read only when the file changes) and does date math against today's date.
  * Thread-safe: POV builds list items from a thread pool.
  * If il_releases.json does not exist yet (first run) it scrapes immediately, once: a fast
    catalog-only fetch (single request) so the very first list already shows badges, then asks
    the Wizard service to upgrade to exact stream dates.
"""
import datetime
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)

try:
    import il_fetcher
except Exception:                                   # noqa: BLE001 - keep POV alive no matter what
    il_fetcher = None

PROP_TEXT = 'IL_release'
PROP_STATE = 'IL_release_state'
PROP_DAYS = 'IL_release_days'

STATE_LATER, STATE_SOON, STATE_TODAY, STATE_RELEASED = 'later', 'soon', 'today', 'released'

TXT_UPCOMING = u'בעוד %d ימים'
TXT_TOMORROW = u'יוצא מחר'
TXT_TODAY = u'יוצא היום!'
TXT_RELEASED = u'שוחרר!'

SOON_MAX_DAYS = 7               # 1..7 days -> "soon" (orange), >7 -> "later" (red)
MAX_RELEASED_AGE_DAYS = 60      # older than this -> no badge at all (None = always "שוחרר!")
STAT_INTERVAL = 5.0             # seconds between os.stat() calls on the cache file
BOOTSTRAP_TIMEOUT = 5           # first-run catalog fetch timeout (seconds)

_lock = threading.Lock()
_state = {'data': {}, 'mtime': None, 'checked': None, 'bootstrapped': False}


# --------------------------------------------------------------------------- cache access
def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _bootstrap():
    """First run: no il_releases.json yet -> scrape NOW (fast), then request the full refresh."""
    try:
        if il_fetcher.bootstrap_allowed():
            il_fetcher.refresh(exact=False, timeout=BOOTSTRAP_TIMEOUT)
            il_fetcher.request_refresh()
    except Exception:                               # noqa: BLE001
        pass


def _get_data():
    if il_fetcher is None:
        return {}
    checked = _state['checked']
    if checked is not None and time.monotonic() - checked < STAT_INTERVAL:
        return _state['data']                       # hot path: no lock, no I/O
    with _lock:
        checked = _state['checked']
        if checked is not None and time.monotonic() - checked < STAT_INTERVAL:
            return _state['data']                   # another thread refreshed it while we waited
        path = il_fetcher.cache_path()
        mtime = _mtime(path)
        if mtime is None and not _state['bootstrapped']:
            _state['bootstrapped'] = True
            _bootstrap()                            # other threads block here, then reuse result
            mtime = _mtime(path)
        if mtime is not None and mtime != _state['mtime']:
            _state['data'] = il_fetcher.load_cache()
            _state['mtime'] = mtime
        _state['checked'] = time.monotonic()
        return _state['data']


# --------------------------------------------------------------------------- date math
def describe_days(days_left):
    """days_left (int) -> (hebrew_text, state)."""
    if days_left > 1:
        return TXT_UPCOMING % days_left, (STATE_SOON if days_left <= SOON_MAX_DAYS else STATE_LATER)
    if days_left == 1:
        return TXT_TOMORROW, STATE_SOON
    if days_left == 0:
        return TXT_TODAY, STATE_TODAY
    if MAX_RELEASED_AGE_DAYS is not None and days_left < -MAX_RELEASED_AGE_DAYS:
        return u'', u''
    return TXT_RELEASED, STATE_RELEASED


def get_release_status(imdb_id, today=None):
    """-> (text, state, days_left). ('', '', None) when there is no information."""
    if not imdb_id or il_fetcher is None:
        return u'', u'', None
    iso = _get_data().get(str(imdb_id).strip())
    target = il_fetcher.parse_any_date(iso)         # accepts ISO and DD/MM/YYYY, DD.MM.YYYY
    if target is None:
        return u'', u'', None
    days_left = (target - (today or datetime.date.today())).days
    text, state = describe_days(days_left)
    return text, state, (days_left if text else None)


def apply_release_property(imdb_id, listitem):
    """Set IL_release (+ state/days) on a ListItem. Returns the text. Never raises."""
    try:
        text, state, days = get_release_status(imdb_id)
        listitem.setProperty(PROP_TEXT, text)
        if text:
            listitem.setProperty(PROP_STATE, state)
            listitem.setProperty(PROP_DAYS, str(days))
        return text
    except Exception:                               # noqa: BLE001
        return u''
