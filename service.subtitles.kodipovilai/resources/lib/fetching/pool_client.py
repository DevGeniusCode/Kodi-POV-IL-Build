# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The community pool, read side: signed requests, lookup, fetch, the sync map and the shared
# helpers (release name from a path, token test, URL and enable switches).

import json
import os
from resources.lib.core import kodi, settings
try:
    import urllib.request as _urlreq
    import urllib.parse as _urlparse
    import urllib.error as _urlerr
except ImportError:        # pragma: no cover
    _urlreq = None
    _urlparse = None
    _urlerr = None
import hmac as _hmac
import hashlib as _hashlib


POOL_URL = 'https://povil-subs-pool.moran200333.workers.dev'
# Non-secret client format tag. Old Ktuvit rows were stored after the vendored
# engine physically reversed punctuation; new rows store logical source bytes.
# The picker carries this returned source_lang into its local display repair so
# it never has to refetch/re-upload the old pool.
KTUVIT_LOGICAL_SOURCE_TAG = 'he-logical-v1'

# Build-time pool credential (set during packaging). Fails closed if un-set.
# __POOL_KEY_BEGIN__
def _pool_key():
    import base64 as _b, hashlib as _h
    try:
        import xbmcaddon
        _a = xbmcaddon.Addon().getAddonInfo('id')
    except Exception:
        _a = ''
    _s = bytes.fromhex('472891cf3cd26539c43c0eb601157b81') + _a.encode('utf-8')
    _bl = _b.b64decode('M8C6Sok63q7ytb/Ku/7ccEj99mjlpVXR5uCZD7kn2iyKVcBzezlvkaGgwBRnhFZJwA+IACP3v9HbvA==')
    _x = b''
    _k = _s
    while len(_x) < len(_bl):
        _k = _h.sha256(_k + _s).digest()
        _x += _k
    return bytes(c ^ d for c, d in zip(_bl, _x)).decode('utf-8', 'replace')
# __POOL_KEY_END__


def _anon_id():
    """Stable anonymous per-install id (shared with telemetry)."""
    try:
        from resources.lib.core import settings
        v = (settings.get_setting('_telemetry_id', '') or '').strip()
        if not v:
            import uuid
            v = uuid.uuid4().hex
            settings.set_setting('_telemetry_id', v)
        return v
    except Exception:
        return ''


def _addon_version():
    try:
        import xbmcaddon
        return xbmcaddon.Addon(
            'service.subtitles.kodipovilai').getAddonInfo('version') or ''
    except Exception:
        return ''


def sign_headers(method, path):
    """Return the request headers the Worker expects."""
    anon = _anon_id()
    try:
        msg = (method.upper() + '\n' + path + '\n' + anon).encode('utf-8')
        sig = _hmac.new(_pool_key().encode('utf-8'), msg,
                        _hashlib.sha256).hexdigest()
    except Exception:
        sig = ''
    return {'x-pov-sig': sig, 'x-pov-anon': anon, 'x-pov-v': _addon_version()}


def _post_headers(path):
    """Headers for a signed POST to `path`."""
    h = {'content-type': 'application/json', 'user-agent': _UA}
    h.update(sign_headers('POST', path))
    return h

# Cloudflare's browser-integrity check rejects plain urllib requests (HTTP
# 1010); a normal browser UA passes. Harmless for our own Worker.
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
_GET_TIMEOUT = 8


def use_enabled():
    """Pull from the pool before translating? (default off)"""
    return settings.get_bool('pool_use', False)


def share_enabled():
    """Push fresh translations to the pool? (default off)"""
    return settings.get_bool('pool_share', False)


def _is_token_like(s):
    """A debrid URL / token / bare UUID is NOT a usable release name."""
    import re as _re
    s = (s or '').strip()
    low = s.lower()
    if not s:
        return True
    if 'token=' in low or '://' in low or '?' in low or '&' in low:
        return True
    if _re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                     r'[0-9a-f]{4}-[0-9a-f]{12}', low):
        return True
    if (len(s) >= 24 and '.' not in s and ' ' not in s
            and _re.fullmatch(r'[0-9a-f-]+', low)):
        return True
    return False


def _release_from(info):
    # Prefer real release names; never share a debrid URL/token as the release
    # (that's what produced the garbage "...?token=..." names in the pool).
    for key in ('release', 'filename', 'picked_release', 'tagline', 'label'):
        rel = (info.get(key) or '').strip()
        if rel and not _is_token_like(rel):
            return rel
    # li_filename (ListItem.FileNameAndPath) and filepath are PATHS whose
    # basename is often the real release even when the player's filepath is a
    # tokenized debrid URL. _release_ready() treats li_filename as a valid
    # release (so autosub proceeds and embedded detection runs on it), so
    # _release_from MUST consult it too -- otherwise report_embedded and the
    # local merge_embedded get an empty release and silently no-op, and the
    # source screen never gets the BUILT-IN flag locally OR from the pool.
    for pathkey in ('li_filename', 'filepath'):
        fp = (info.get(pathkey) or '').strip()
        if not fp:
            continue
        base = os.path.basename(fp)
        if '.' in base:
            base = base.rsplit('.', 1)[0]
        if base and not _is_token_like(base):
            return base
    return ''


def _params(info):
    return {
        'tmdb': (info.get('tmdb_id') or '').strip(),
        'imdb': (info.get('imdb_id') or '').strip(),
        'type': 'episode' if info.get('is_episode') else 'movie',
        'season': str(info.get('season') or '0'),
        'episode': str(info.get('episode') or '0'),
        'lang': 'he',
    }


def _has_id(p):
    return bool(p.get('tmdb') or p.get('imdb'))


def _get(path, params):
    q = _urlparse.urlencode({k: v for k, v in params.items() if v})
    hdrs = {'user-agent': _UA}
    hdrs.update(sign_headers('GET', path))
    req = _urlreq.Request(POOL_URL + path + '?' + q, headers=hdrs)
    with _urlreq.urlopen(req, timeout=_GET_TIMEOUT) as r:
        return r.read()


# --- Client-side /lookup response cache -------------------------------------
# A single browse/play flow calls /lookup several times for the SAME media (the
# picker, sync_map, and the contribute pre-check). Caching the full response for
# a short window collapses those into ONE Worker request -- the main lever for
# staying under the free plan's 100k-requests/day cap. Negative results are
# cached too (briefly) so repeated misses don't re-hit the Worker. In-memory
# (per session), fail-open.
_LOOKUP_CACHE = {}
_LOOKUP_TTL = 90.0
_LOOKUP_CACHE_MAX = 40


def _lookup_cache_key(p):
    return '|'.join((p.get('tmdb') or p.get('imdb') or '', p.get('type') or '',
                     str(p.get('season') or '0'), str(p.get('episode') or '0'),
                     (p.get('lang') or 'he')))


def _lookup_raw(p):
    """Fetch (and briefly cache) the full /lookup response dict for these media
    params. Returns {} on error/miss. Shared by lookup(), sync_map() and the
    contribute pre-check so they cost ONE request per media per ~TTL."""
    import time as _t
    ck = _lookup_cache_key(p)
    now = _t.time()
    ent = _LOOKUP_CACHE.get(ck)
    if ent and (now - ent[0]) < _LOOKUP_TTL:
        return ent[1]
    cacheable = True
    try:
        data = json.loads(_get('/lookup', p).decode('utf-8'))
        if not isinstance(data, dict) or not data.get('ok'):
            data = {}
    except Exception as e:
        kodi.log('pool lookup failed: {0}'.format(e), level='DEBUG')
        data = {}
        cacheable = False   # transient/network/parse error -- a reachable "no
        #                     results" answer is cached (below) as a genuine
        #                     negative, but a transport failure must NOT poison
        #                     this now-shared cache: autosub reuses the same
        #                     _LOOKUP_CACHE, so a warm-side blip could otherwise
        #                     hide a pool subtitle from autosub for up to the TTL.
    if cacheable:
        try:
            _LOOKUP_CACHE[ck] = (now, data)
            if len(_LOOKUP_CACHE) > _LOOKUP_CACHE_MAX:
                for k, _v in sorted(_LOOKUP_CACHE.items(),
                                    key=lambda kv: kv[1][0])[:len(_LOOKUP_CACHE) - _LOOKUP_CACHE_MAX]:
                    _LOOKUP_CACHE.pop(k, None)
        except Exception:
            pass          # cache mgmt must never raise (concurrent-mutation safe)
    return data


def lookup(info):
    """Return a list of available Hebrew variants for this media, or []."""
    if _urlreq is None:
        return []
    p = _params(info)
    if not _has_id(p):
        return []
    data = _lookup_raw(p)
    if not data:
        return []
    _sync_cache_put(p, data.get('sync') or {})
    return data.get('variants') or []


def lookup_cached(info):
    """Variants for this media ONLY if the in-memory /lookup cache is already warm
    for it -- NEVER networks. Returns the variants list on a fresh cache hit
    (possibly []), or None when not cached / the cached response was a failure, so
    a caller can gate on the variant list WITHOUT triggering a /lookup request of
    its own (keeps the reuse pre-check from ever ADDING a Worker request)."""
    import time as _t
    try:
        if _urlreq is None:
            return None
        p = _params(info)
        if not _has_id(p):
            return None
        ent = _LOOKUP_CACHE.get(_lookup_cache_key(p))
        if ent and (_t.time() - ent[0]) < _LOOKUP_TTL and ent[1]:
            return ent[1].get('variants') or []
    except Exception:
        return None
    return None


# --- SubSync S3: community sync registry (client side) ----------------------
# The Worker serves per-media sync verdicts inside the /lookup response
# (`sync`: {"<sub_hash>|<normalized release>": {s, o, st, n, h, ts}}). lookup()
# stashes them here so subsync can consume the registry with ZERO extra
# requests in the common flow; sync_map() falls back to one /lookup when the
# picker's lookup didn't run (e.g. engine-only picks), throttled by TTL.

_SYNC_CACHE = {}
_SYNC_TTL = 600.0
_SYNC_CACHE_MAX = 30


def _sync_cache_key(p):
    return '|'.join((p.get('tmdb') or p.get('imdb') or '', p.get('type') or '',
                     str(p.get('season') or '0'), str(p.get('episode') or '0')))


def _sync_cache_put(p, sync):
    try:
        import time as _t
        _SYNC_CACHE[_sync_cache_key(p)] = (_t.time(), sync or {})
        if len(_SYNC_CACHE) > _SYNC_CACHE_MAX:
            oldest = sorted(_SYNC_CACHE.items(), key=lambda kv: kv[1][0])
            for k, _v in oldest[:len(_SYNC_CACHE) - _SYNC_CACHE_MAX]:
                _SYNC_CACHE.pop(k, None)
    except Exception:
        pass


def worker_norm_release(s):
    """EXACT mirror of the Worker's normRelease() -- the registry key must be
    computed identically on both sides or lookups silently miss."""
    import re as _re
    s = str(s or '').lower()
    s = _re.sub(r'\.(mkv|mp4|avi|srt)$', '', s)
    s = _re.sub(r'[\s_+/\-]+', '.', s)
    s = _re.sub(r'\.+', '.', s).strip('.')
    return s


def sync_map(info):
    """The community sync map for this media ({} when unavailable). Prefers
    the copy stashed by the picker's lookup(); otherwise ONE /lookup per TTL.
    Read side -- gated by pool_use like every other pool read."""
    try:
        if _urlreq is None or not use_enabled():
            return {}
        p = _params(info)
        if not _has_id(p):
            return {}
        import time as _t
        ent = _SYNC_CACHE.get(_sync_cache_key(p))
        if ent and (_t.time() - ent[0]) < _SYNC_TTL:
            return ent[1]
        data = _lookup_raw(p)          # request-cached; ONE /lookup per media/TTL
        sm = (data.get('sync') or {}) if data else {}
        _sync_cache_put(p, sm)
        return sm
    except Exception as e:
        kodi.log('pool sync_map failed: {0}'.format(e), level='DEBUG')
        return {}


def fetch(info, source_hash=None):
    """Return the .srt text for the exact source_hash (or newest if None),
    or None. With a hash, the Worker 404s when that exact variant is absent."""
    if _urlreq is None:
        return None
    p = _params(info)
    if not _has_id(p):
        return None
    if source_hash:
        p['hash'] = source_hash
    try:
        return _get('/sub', p).decode('utf-8')
    except Exception as e:
        kodi.log('pool fetch failed: {0}'.format(e), level='DEBUG')
        return None


def _lookup_params_from_body(body):
    return {
        'tmdb': (body.get('tmdb_id') or '').strip(),
        'imdb': (body.get('imdb_id') or '').strip(),
        'type': body.get('type') or '',
        'season': str(body.get('season') or '0'),
        'episode': str(body.get('episode') or '0'),
        'lang': body.get('lang') or 'he',
    }


# --- Persistent, throttled upload queue -------------------------------------
# A contribution is QUEUED to disk the moment it's ready and uploaded later by
# the long-lived service (drain(), driven by service.py's monitor thread). This
# is what makes a shared subtitle reliable and rate-limit-safe:
#   * Durable: the job is a file under addon_data (which a quick-update never
#     touches), so it survives the user leaving the video and a Kodi restart --
#     a short-lived search invoker can't lose it.
#   * No burst: the drainer sends ONE contribution at a time with a throttle
#     (each contribution is two Telegram messages), so even a backlog stays well
#     under the bot's ~20 msgs/min channel limit.
#   * Self-healing: a failed upload stays queued and retries next pass; a
#     success (or a server dedup) removes it; permanently-bad jobs are dropped,
#     and anything stuck is aged out so the queue can't grow without bound.

_QUEUE_MAX_AGE_SEC = 14 * 24 * 3600
