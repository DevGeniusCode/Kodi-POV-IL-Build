# -*- coding: utf-8 -*-
"""
il_release.py - runtime release-badge injector for POV list items

Location : addons/plugin.program.kodipovilwizard/resources/libs/patches/il_release.py
Called by: the hook patched into plugin.video.pov  (resources/lib/menus/movies.py)

    il_release.apply_release_property(imdb_id, listitem, premiered, year, il_dates)

Badge cascade ("Exclusion Principle": a badge only means "NOT streamable yet")
  1. Digital feed cache (il_releases.json, keyed by IMDb id)         - exact, Israel-specific
        future / today -> countdown ("בעוד 7 ימים" | "יוצא מחר" | "יוצא היום!")
        past           -> NO badge (already out - stop, do not fall through)
  2. TMDB per-type dates stored in POV's meta by the metadata.py patch (`meta['il_dates']`,
     extracted from the `release_dates` block POV already downloads - zero extra requests)
        digital date = EARLIEST type-4 date in ANY region (a WEB-DL exists worldwide as soon as
            one country releases digitally):  future -> countdown, past -> NO badge
        theatrical date (types 2+3: first of DATE_REGIONS that has one, else earliest worldwide):
            future (<= COMING_SOON_MAX_DAYS)       -> "בקרוב"
            today .. THEATERS_WINDOW_DAYS ago      -> "בקולנוע"
            older                                  -> NO badge
        no theatrical entry at all (streaming original): `premiered` is NOT a theatrical date,
            so only a FUTURE premiere is shown ("בקרוב"); never "בקולנוע".
  3. Legacy fallback (meta cached before the metadata.py patch, or extraction failed):
     treat `premiered` as the theatrical date, exactly as in v2. No date but a strictly-future
     year -> "בקרוב". Nothing usable -> NO badge.

ListItem properties set (skin side: ListItem.Property(...), case-insensitive).
Properties are ONLY set when there is a badge; a fresh ListItem already reads as empty.
    IL_release        badge text
    IL_release_state  later | soon | today | theaters | coming   -> lets the skin pick a colour
    IL_release_days   signed integer as text. Digital: days until release (>= 0).
                      theaters: <= 0 (days since premiere, negated). coming: > 0 (days until premiere).

Design rules
  * NEVER raises - POV's build_movie_content() has a blanket `except: pass`, so an exception
    here would silently drop the movie from the list.
  * No network on the hot path. Reads il_releases.json (stat'ed at most every few seconds,
    re-read only when the file changes) and does plain date math. TMDB data comes from POV's
    own meta dict - zero additional requests.
  * Thread-safe: POV builds list items from a thread pool. Shared state is only ever replaced
    by reference (never mutated in place); everything else is local to the call.
  * datetime.strptime is deliberately NOT used (see il_fetcher.py: _strptime import race in
    Kodi's embedded Python when first called from several threads at once).
  * If il_releases.json does not exist yet (first run) it scrapes immediately, once: a fast
    catalog-only fetch (single request), then asks the Wizard service for exact stream dates.
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

STATE_LATER, STATE_SOON, STATE_TODAY = 'later', 'soon', 'today'
STATE_THEATERS, STATE_COMING = 'theaters', 'coming'

TXT_UPCOMING = u'עוד %d יום'
TXT_TOMORROW = u'יוצא מחר'
TXT_TODAY = u'יוצא היום!'
TXT_THEATERS = u'בקולנוע'
TXT_COMING = u'בקרוב'

SOON_MAX_DAYS = 7               # digital: 1..7 days -> "soon" (orange), >7 -> "later" (red)
THEATERS_WINDOW_DAYS = 75       # premiered within the last N days (and not in cache) -> "בקולנוע"
COMING_SOON_MAX_DAYS = 365      # premiere further away than this -> no badge (None = no cap)
USE_TMDB_DIGITAL = True         # also trust TMDB's earliest worldwide digital date (type 4) for titles missing from the feed
DATE_REGIONS = ('IL', 'US')     # region priority for the THEATRICAL date (digital is always worldwide)
DATES_VERSION = 2               # schema of meta['il_dates']; other/missing versions are ignored (legacy path)
STAT_INTERVAL = 5.0             # seconds between os.stat() calls on the cache file
BOOTSTRAP_TIMEOUT = 5           # first-run catalog fetch timeout (seconds)

_NO_BADGE = (u'', u'', None)

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
            _state['data'] = il_fetcher.load_cache()    # whole-dict swap: readers never see partial state
            _state['mtime'] = mtime
        _state['checked'] = time.monotonic()
        return _state['data']


# --------------------------------------------------------------------------- date helpers
def _to_date(value):
    """'YYYY-MM-DD[...]' | date | datetime -> datetime.date | None.  No regex, no strptime."""
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    s = str(value).strip()
    if len(s) < 10 or s[4] != '-' or s[7] != '-':
        return None
    try:
        return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, TypeError):
        return None


def _to_year(value):
    """4-digit year from int / '2027' / '2027-05-01' / 'None' / '' -> int | None."""
    if not value:
        return None
    try:
        y = int(str(value).strip()[:4])
    except (ValueError, TypeError):
        return None
    return y if 1888 <= y <= 2200 else None


# --------------------------------------------------------------------------- badge logic
def describe_days(days_left):
    """Digital countdown: days_left (int) -> (hebrew_text, state). Past dates -> ('', '')."""
    if days_left > 1:
        return TXT_UPCOMING % days_left, (STATE_SOON if days_left <= SOON_MAX_DAYS else STATE_LATER)
    if days_left == 1:
        return TXT_TOMORROW, STATE_SOON
    if days_left == 0:
        return TXT_TODAY, STATE_TODAY
    return u'', u''                                 # Exclusion Principle: already out


def _digital_status(imdb_id, today):
    """-> (found, (text, state, days)). found=True means the cache decided - do NOT fall through."""
    if not imdb_id or il_fetcher is None:
        return False, _NO_BADGE
    iso = _get_data().get(str(imdb_id).strip())
    if not iso:
        return False, _NO_BADGE
    target = il_fetcher.parse_any_date(iso)         # accepts ISO and DD/MM/YYYY, DD.MM.YYYY
    if target is None:
        return False, _NO_BADGE                     # garbage entry -> treat as "not in cache"
    days_left = (target - today).days
    text, state = describe_days(days_left)
    return True, ((text, state, days_left) if text else _NO_BADGE)


def _window_status(release, today):
    """Theatrical date -> coming soon / in theaters / nothing."""
    days_left = (release - today).days
    if days_left > 0:
        if COMING_SOON_MAX_DAYS is not None and days_left > COMING_SOON_MAX_DAYS:
            return _NO_BADGE
        return TXT_COMING, STATE_COMING, days_left
    if days_left >= -THEATERS_WINDOW_DAYS:
        return TXT_THEATERS, STATE_THEATERS, days_left
    return _NO_BADGE                                # old release: assume it is on digital by now


def _year_status(year, premiered, today):
    """No usable full date: a strictly-future year is still a reliable "not out yet" signal."""
    y = _to_year(year) or _to_year(premiered)
    if y and y > today.year and (COMING_SOON_MAX_DAYS is None or y - today.year <= 1):
        return TXT_COMING, STATE_COMING, None
    return _NO_BADGE


def _legacy_status(premiered, year, today):
    """v2 behaviour: `premiered` (TMDB primary release_date) is treated as the theatrical date."""
    release = _to_date(premiered)
    if release is not None:
        return _window_status(release, today)
    return _year_status(year, premiered, today)


def _tmdb_status(il_dates, premiered, year, today):
    """TMDB cascade for movies that are not in the digital feed cache."""
    if not isinstance(il_dates, dict) or il_dates.get('v') != DATES_VERSION:
        return _legacy_status(premiered, year, today)
    if USE_TMDB_DIGITAL:
        digital = _to_date(il_dates.get('digital'))
        if digital is not None:
            days_left = (digital - today).days
            text, state = describe_days(days_left)
            return (text, state, days_left) if text else _NO_BADGE
    theatrical = _to_date(il_dates.get('theatrical'))
    if theatrical is not None:
        return _window_status(theatrical, today)
    # TMDB knows release entries but none is theatrical -> `premiered` may be a streaming date
    release = _to_date(premiered)
    if release is not None:
        if release > today:
            return _window_status(release, today)  # future premiere: "בקרוב" (never "בקולנוע")
        return _NO_BADGE
    return _year_status(year, premiered, today)


def get_release_status(imdb_id, premiered=None, year=None, il_dates=None, today=None):
    """-> (text, state, days). ('', '', None) = no badge."""
    today = today or datetime.date.today()
    found, status = _digital_status(imdb_id, today)
    if found:
        return status
    return _tmdb_status(il_dates, premiered, year, today)


# --------------------------------------------------------------------------- TMDB extraction
def extract_dates(data, regions=None):
    """TMDB movie payload (with append_to_response=release_dates) -> compact dict | None.

    Called ONCE per movie from the metadata.py patch, right before POV caches `meta`:
        {'v': DATES_VERSION, 'theatrical': 'YYYY-MM-DD' | '', 'digital': 'YYYY-MM-DD' | ''}
    theatrical = earliest type 2/3 date (limited/wide) for the first region in `regions` that has
                 one, else the earliest worldwide.
    digital    = earliest type 4 date across ALL regions (regions are deliberately ignored: once any
                 country releases digitally, a WEB-DL is available to scrapers everywhere).
    Returns None when TMDB has no release entries at all (= unknown, caller falls back to legacy).
    Never raises.
    """
    try:
        results = (data.get('release_dates') or {}).get('results')
        if not isinstance(results, list):
            return None
        regions = regions or DATE_REGIONS
        by_region, seen = {}, False
        for res in results:
            code = res.get('iso_3166_1')
            theatrical = digital = ''
            for rd in (res.get('release_dates') or ()):
                d = str(rd.get('release_date') or '')[:10]
                if _to_date(d) is None:
                    continue
                seen = True
                rtype = rd.get('type')
                if rtype in (2, 3):
                    if not theatrical or d < theatrical:        # ISO strings sort chronologically
                        theatrical = d
                elif rtype == 4:
                    if not digital or d < digital:
                        digital = d
            by_region[code] = (theatrical, digital)
        if not seen:
            return None
        theatrical = next((by_region[r][0] for r in regions if r in by_region and by_region[r][0]), '')
        if not theatrical:
            theatrical = min((v[0] for v in by_region.values() if v[0]), default='')
        digital = min((v[1] for v in by_region.values() if v[1]), default='')
        return {'v': DATES_VERSION, 'theatrical': theatrical, 'digital': digital}
    except Exception:                               # noqa: BLE001
        return None


def apply_release_property(imdb_id, listitem, premiered=None, year=None, il_dates=None):
    """Set IL_release (+ state/days) on a ListItem. Returns the text. Never raises.

    `premiered` / `year` / `il_dates` are optional so older hooks (v1 / v2) keep working.
    Nothing is written when there is no badge, so a stale older hook running after a newer one
    can never erase or overwrite a badge with an empty value.
    """
    try:
        text, state, days = get_release_status(imdb_id, premiered, year, il_dates)
        if text:
            listitem.setProperty(PROP_TEXT, text)
            listitem.setProperty(PROP_STATE, state)
            if days is not None:
                listitem.setProperty(PROP_DAYS, str(days))
        return text
    except Exception:                               # noqa: BLE001
        return u''