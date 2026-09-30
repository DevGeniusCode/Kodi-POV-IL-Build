# -*- coding: utf-8 -*-
"""
il_fetcher.py - Israeli online-release date fetcher + cache
for plugin.program.kodipovilwizard

Location : addons/plugin.program.kodipovilwizard/resources/libs/patches/il_fetcher.py
Output   : special://profile/addon_data/plugin.program.kodipovilwizard/il_releases.json
              {"tt22084616": "2026-10-06", ...}          <- ISO dates, keyed by IMDb id
           special://profile/addon_data/plugin.program.kodipovilwizard/il_releases_meta.json
              bookkeeping only (which dates are exact, when the last full refresh ran)

Strategy
  1. Catalog endpoint  -> list of IMDb ids + a relative status in the name "(בעוד 7 ימים)".
  2. Stream endpoint   -> exact Digital date per movie ("Digital - 29/09/2026")   [preferred]
  3. If (2) fails / is throttled / has no Digital date -> convert the relative status
     from (1) into an absolute date (fallback).  Absolute dates are cached, so the
     countdown stays correct between refreshes.

Integration (Wizard service.py):
    import il_fetcher
    il_fetcher.start_service(monitor)        # background thread, refreshes every 12h

First run / missing il_releases.json:
    il_release.py does a fast catalog-only scrape immediately (one HTTP request) and asks the
    service (via a Home-window property) to upgrade to exact dates.

NOTE: datetime.strptime is deliberately NOT used anywhere. In Kodi's embedded Python it can fail
with "Failed to import _strptime" when first called from several threads at once
(POV builds list items in threads).
"""
import datetime
import json
import os
import re
import threading
import time
import urllib.request

ADDON_ID = 'plugin.program.kodipovilwizard'

FEED_BASE = 'https://stremio7rd-movies-online-dates.vercel.app'
CATALOG_URL = FEED_BASE + '/catalog/movie/movies-online-coming-this-month.json'
STREAM_URL = FEED_BASE + '/stream/movie/{imdb_id}.json'

CACHE_FILE = 'il_releases.json'
META_FILE = 'il_releases_meta.json'

REFRESH_INTERVAL = 12 * 3600      # full refresh every 12h
RETRY_INTERVAL = 15 * 60          # wait after a failed refresh
SERVICE_TICK = 30                 # seconds between scheduler checks
HTTP_TIMEOUT = 10
REQUEST_DELAY = 0.3               # politeness delay between stream requests
MAX_STREAM_FAILURES = 3           # consecutive failures -> stop hitting stream endpoint this run
BOOTSTRAP_COOLDOWN = 600          # don't retry the "first run" scrape more often than this
USER_AGENT = 'Mozilla/5.0 (Kodi; POV-IL-Release)'

HOME_WINDOW_ID = 10000
PROP_REFRESH_REQUEST = 'IL_release.refresh_request'
PROP_BOOTSTRAP_TS = 'IL_release.bootstrap_ts'

_IMDB_RE = re.compile(r'^tt\d{5,10}$')
_ISO_RE = re.compile(r'^\s*(\d{4})-(\d{1,2})-(\d{1,2})')
_DMY_RE = re.compile(r'^\s*(\d{1,2})[./](\d{1,2})[./](\d{4})\s*$')
_DIGITAL_RE = re.compile(r'Digital\s*-\s*(\d{1,2})/(\d{1,2})/(\d{4})', re.I)

# "(בעוד 7 ימים)", "(✅ לפני 14 ימים)", "(בעוד יומיים)", "(בעוד שבוע)" ...
_UNIT_DAYS = {
    u'יום': 1, u'ימים': 1, u'יומיים': 2,
    u'שבוע': 7, u'שבועות': 7, u'שבועיים': 14,
    u'חודש': 30, u'חודשים': 30, u'חודשיים': 60,
}
_REL_RE = re.compile(
    u'(בעוד|לפני)\\s+(?:(\\d+)\\s+)?'
    u'(יומיים|שבועיים|חודשיים|ימים|יום|שבועות|שבוע|חודשים|חודש)')
_STATUS_RE = re.compile(r'\(([^()]*)\)\s*$')

_refresh_lock = threading.Lock()
_next_attempt = 0.0
_service_thread = None
_fallback_props = {}   # used when running outside Kodi (tests / CLI)


# --------------------------------------------------------------------------- logging / paths
def _log(msg, warn=False):
    try:
        import xbmc
        xbmc.log('[IL-Release] %s' % msg, xbmc.LOGWARNING if warn else xbmc.LOGINFO)
    except ImportError:
        print('[IL-Release] %s' % msg)


def data_dir():
    d = os.environ.get('IL_RELEASE_DATA_DIR')
    if not d:
        try:
            import xbmcvfs
            d = xbmcvfs.translatePath('special://profile/addon_data/%s/' % ADDON_ID)
        except ImportError:
            d = os.path.join(os.path.expanduser('~'), '.il_release')
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def cache_path():
    return os.path.join(data_dir(), CACHE_FILE)


def meta_path():
    return os.path.join(data_dir(), META_FILE)


def _read_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json_atomic(path, obj):
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=1)
    os.replace(tmp, path)          # atomic: readers never see a half-written file


def load_cache():
    """Return {imdb_id: 'YYYY-MM-DD'} (empty dict when missing/corrupt)."""
    data = _read_json(cache_path(), {})
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


# --------------------------------------------------------------------------- parsing
def _make_date(y, m, d):
    try:
        return datetime.date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def parse_any_date(value):
    """'2026-09-29' | '29/09/2026' | '29.09.2026' -> datetime.date | None (no strptime)."""
    if not value:
        return None
    s = str(value)
    m = _ISO_RE.match(s)
    if m:
        return _make_date(*m.groups())
    m = _DMY_RE.match(s)
    if m:
        d, mo, y = m.groups()
        return _make_date(y, mo, d)
    return None


def parse_digital_date(description):
    """Extract the Digital release date from a stream description -> ISO string | None."""
    m = _DIGITAL_RE.search(description or '')
    if not m:
        return None
    d, mo, y = m.groups()
    date = _make_date(y, mo, d)
    return date.isoformat() if date else None


def parse_relative_status(name, today=None):
    """Fallback: '<title> (בעוד 7 ימים)' -> absolute ISO date | None."""
    m = _STATUS_RE.search(name or '')
    if not m:
        return None
    status = m.group(1)
    delta = None
    r = _REL_RE.search(status)
    if r:
        sign = 1 if r.group(1) == u'בעוד' else -1
        num = int(r.group(2)) if r.group(2) else 1
        delta = sign * num * _UNIT_DAYS[r.group(3)]
    elif u'מחר' in status:
        delta = 1
    elif u'אתמול' in status:
        delta = -1
    elif u'היום' in status:          # "✅ יצא היום"
        delta = 0
    if delta is None:
        return None
    today = today or datetime.date.today()
    return (today + datetime.timedelta(days=delta)).isoformat()


# --------------------------------------------------------------------------- network
def _http_get_json(url, timeout=HTTP_TIMEOUT, retries=0):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:                      # noqa: BLE001 - network can fail in many ways
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_exact_date(imdb_id, timeout=HTTP_TIMEOUT):
    """Digital release date from the stream endpoint -> ISO | None (no Digital date published)."""
    data = _http_get_json(STREAM_URL.format(imdb_id=imdb_id), timeout)
    for stream in (data.get('streams') or []):
        iso = parse_digital_date(stream.get('description'))
        if iso:
            return iso
    return None


def _sleep(seconds, abort):
    end = time.time() + seconds
    while time.time() < end:
        if abort():
            return
        time.sleep(0.1)


# --------------------------------------------------------------------------- refresh
def refresh(exact=True, abort=None, timeout=HTTP_TIMEOUT, today=None):
    """
    Download the feed and rewrite il_releases.json.
      exact=True  : catalog + one stream request per movie (slow, ~0.3s each) - service use
      exact=False : catalog only, dates estimated from the relative status - instant "scrape now"
    Never raises. Returns {'ok': bool, 'count': int, 'exact': int, 'mode': str, 'error': str}.
    On any failure the previous cache is left untouched.
    """
    if not _refresh_lock.acquire(False):
        return {'ok': False, 'error': 'busy'}
    try:
        return _refresh(exact, abort or (lambda: False), timeout, today or datetime.date.today())
    except Exception as e:                           # noqa: BLE001
        _log('refresh crashed: %r' % e, warn=True)
        return {'ok': False, 'error': repr(e)}
    finally:
        _refresh_lock.release()


def _refresh(exact, abort, timeout, today):
    mode = 'full' if exact else 'fast'
    try:
        catalog = _http_get_json(CATALOG_URL, timeout, retries=2 if exact else 0)
    except Exception as e:                           # noqa: BLE001
        _log('catalog fetch failed: %s' % e, warn=True)
        return {'ok': False, 'error': 'catalog: %s' % e}

    metas = [m for m in (catalog.get('metas') or [])
             if isinstance(m, dict) and _IMDB_RE.match(str(m.get('id', '')))]
    if not metas:                                    # empty/garbled feed must not wipe a good cache
        _log('catalog empty - keeping previous cache', warn=True)
        return {'ok': False, 'error': 'empty catalog'}

    prev = load_cache()
    prev_meta = _read_json(meta_path(), {})
    prev_exact = set(prev_meta.get('exact') or [])

    result, exact_ids = {}, []
    use_streams, failures = exact, 0

    for meta in metas:
        if abort():
            return {'ok': False, 'error': 'aborted'}
        imdb_id = meta['id']
        estimate = parse_relative_status(meta.get('name'), today)
        iso, lookup_ok = None, False

        if use_streams:
            try:
                iso = fetch_exact_date(imdb_id, timeout)
                lookup_ok, failures = True, 0
            except Exception as e:                   # noqa: BLE001
                failures += 1
                _log('stream lookup failed for %s: %s' % (imdb_id, e), warn=True)
                if failures >= MAX_STREAM_FAILURES:
                    use_streams = False
                    _log('stream endpoint unavailable/throttled - using catalog fallback', warn=True)
            if use_streams:
                _sleep(REQUEST_DELAY, abort)

        is_exact = bool(iso)
        if not iso:
            if not lookup_ok and imdb_id in prev_exact and imdb_id in prev:
                iso, is_exact = prev[imdb_id], True       # lookup unavailable: keep last exact date
            else:
                iso = estimate                            # fallback: relative status from catalog
        if not iso and imdb_id in prev:
            iso, is_exact = prev[imdb_id], imdb_id in prev_exact
        if not iso:
            continue

        result[imdb_id] = iso
        if is_exact:
            exact_ids.append(imdb_id)
        if is_exact and estimate and lookup_ok:
            a, b = parse_any_date(iso), parse_any_date(estimate)
            if a and b and abs((a - b).days) > 2:
                _log('%s: stream date %s differs from catalog status (%s)' % (imdb_id, iso, estimate))

    _write_json_atomic(cache_path(), result)
    _write_json_atomic(meta_path(), {
        'fetched_at': time.time(),
        'full_at': time.time() if exact else prev_meta.get('full_at', 0),
        'mode': mode,
        'count': len(result),
        'exact': sorted(exact_ids),
    })
    _log('%s refresh OK: %d movies (%d exact)' % (mode, len(result), len(exact_ids)))
    return {'ok': True, 'count': len(result), 'exact': len(exact_ids), 'mode': mode}


# --------------------------------------------------------------------------- scheduling
def is_stale(max_age=REFRESH_INTERVAL):
    """True when the cache is missing or the last *full* refresh is older than max_age."""
    if not os.path.exists(cache_path()):
        return True
    full_at = _read_json(meta_path(), {}).get('full_at') or 0
    return (time.time() - full_at) >= max_age


def _home():
    try:
        import xbmcgui
        return xbmcgui.Window(HOME_WINDOW_ID)
    except ImportError:
        return None


def _get_prop(name):
    w = _home()
    return w.getProperty(name) if w else _fallback_props.get(name, '')


def _set_prop(name, value):
    w = _home()
    if w:
        w.setProperty(name, value)
    else:
        _fallback_props[name] = value


def request_refresh():
    """Called from the plugin process: ask the Wizard service to run a full refresh now."""
    _set_prop(PROP_REFRESH_REQUEST, str(time.time()))


def bootstrap_allowed(cooldown=BOOTSTRAP_COOLDOWN):
    """Cross-process gate so an offline box doesn't retry the first-run scrape on every list."""
    try:
        last = float(_get_prop(PROP_BOOTSTRAP_TS) or 0)
    except ValueError:
        last = 0
    if time.time() - last < cooldown:
        return False
    _set_prop(PROP_BOOTSTRAP_TS, str(time.time()))
    return True


def service_tick(abort=None):
    """One scheduler step. Safe to call from any existing Wizard service loop."""
    global _next_attempt
    if time.time() < _next_attempt:
        return None
    if _get_prop(PROP_REFRESH_REQUEST) or is_stale():
        _set_prop(PROP_REFRESH_REQUEST, '')
        res = refresh(exact=True, abort=abort)
        _next_attempt = 0.0 if res.get('ok') else time.time() + RETRY_INTERVAL
        return res
    return None


def start_service(monitor=None):
    """Start the background refresher thread (idempotent). Call from Wizard's service.py."""
    global _service_thread
    if _service_thread is not None and _service_thread.is_alive():
        return _service_thread

    def _run():
        try:
            import xbmc
            mon = monitor or xbmc.Monitor()
            aborted, wait = mon.abortRequested, mon.waitForAbort
        except ImportError:
            ev = threading.Event()
            aborted, wait = ev.is_set, ev.wait
        while not aborted():
            try:
                service_tick(abort=aborted)       # first pass runs immediately: no cache -> scrape now
            except Exception as e:                # noqa: BLE001
                _log('service tick failed: %r' % e, warn=True)
            if wait(SERVICE_TICK):
                break

    _service_thread = threading.Thread(target=_run, name='il_release_fetcher', daemon=True)
    _service_thread.start()
    return _service_thread


if __name__ == '__main__':          # manual run:  python il_fetcher.py [--fast]
    import sys
    print(refresh(exact='--fast' not in sys.argv))
