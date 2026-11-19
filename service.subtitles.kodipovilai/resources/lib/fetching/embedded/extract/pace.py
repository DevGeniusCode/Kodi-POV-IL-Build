# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Per-host request pacing of the HTTP reader and the memory of the pace that worked for a host.

import json as _json
import os
import re


def _pace_memory_path():
    """Where the per-provider pace memory lives, or '' when there is no Kodi to
    ask (tests, tooling). Never raises."""
    try:
        import xbmcaddon
        import xbmcvfs
        prof = xbmcvfs.translatePath(
            xbmcaddon.Addon().getAddonInfo('profile')) or ''
    except Exception:
        return ''
    if not prof:
        return ''
    try:
        if not os.path.isdir(prof):
            os.makedirs(prof)
    except OSError:
        return ''
    return os.path.join(prof, _PACE_MEMORY_FILE)


def _provider_key(url):
    """The last two labels of the host: one debrid provider hands out many
    per-store hostnames (store-033.wnam.tb-cdn.io, store-027.wnam.tb-cdn.io) and
    they all draw on the SAME token bucket, so they must share one memory."""
    m = re.match(r'^https?://([^/:?#]+)', url or '', re.I)
    if not m:
        return ''
    labels = [p for p in m.group(1).lower().split('.') if p]
    return '.'.join(labels[-2:]) if len(labels) >= 2 else ''


def pace_memory_load(url):
    """The pace to START this run at: last run's, probed 25% faster, clamped so
    it can never be below the normal starting pace. None when we know nothing."""
    key = _provider_key(url)
    path = _pace_memory_path()
    if not key or not path or not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = _json.loads(f.read()) or {}
        saved = float((data.get('hosts') or {}).get(key))
    except (IOError, OSError, ValueError, TypeError):
        return None
    if not (saved == saved) or saved <= 0:      # NaN / nonsense -> know nothing
        return None
    probe = saved * PACE_MEMORY_PROBE
    if probe <= HTTP_REQ_PACE_S:
        return None                              # nothing to remember: start normal
    return min(probe, HTTP_REQ_PACE_MAX)


def pace_memory_save(url, pace, reqs):
    """Record the pace this run ended on. Best-effort and silent."""
    key = _provider_key(url)
    path = _pace_memory_path()
    if not key or not path or reqs < _PACE_MEMORY_MIN_REQS:
        return
    try:
        pace = float(pace)
    except (TypeError, ValueError):
        return
    if not (pace == pace) or pace <= 0:
        return
    pace = max(HTTP_REQ_PACE_S, min(pace, HTTP_REQ_PACE_MAX))
    try:
        data = {}
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = _json.loads(f.read()) or {}
        hosts = data.get('hosts')
        if not isinstance(hosts, dict):
            hosts = {}
        order = [h for h in (data.get('order') or []) if h in hosts]
        hosts[key] = round(pace, 3)
        order = [h for h in order if h != key] + [key]
        while len(order) > _PACE_MEMORY_MAX_HOSTS:
            hosts.pop(order.pop(0), None)
        tmp = path + '.aitmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(_json.dumps({'hosts': hosts, 'order': order}))
        os.replace(tmp, path)
    except (IOError, OSError, ValueError, TypeError):
        try:
            os.remove(path + '.aitmp')
        except OSError:
            pass
# Gentle baseline pace. On a strict provider (TorBox) our small fast requests
# plus the player's stream tripped the per-token limit and 429'd the PLAYER,
# closing the movie. Pace hard so our added request rate stays well under the
# limit; the player-stall abort (translate.py) is the backstop if it still bites.
HTTP_REQ_PACE_S = 0.2            # STARTING gap between range requests
HTTP_REQ_PACE_MAX = 2.0         # ceiling: every 429 widens the gap toward this
#                                  (AIMD-style back-pressure -- we can't know a
#                                  provider's exact limit, so let the CDN's own
#                                  429s tune us down to a rate it tolerates)
# ...and the other half of AIMD, which was missing: the gap only ever GREW. One
# 429 near the start left every remaining request paying for it, and a burst of
# them pinned the pace at the 2.0s ceiling for the rest of the extraction -- ~28
# minutes of pure sleeping over 850 cues, on a provider that may well have
# recovered seconds later. So decay the gap back down after a clean run of
# requests.
# The floor is the STARTING pace, deliberately -- this recovers from back-
# pressure, it does NOT probe for the provider's real limit. 0.2s was chosen
# from a field incident where our request rate 429'd the PLAYER's own stream and
# CLOSED the movie; going below it to shave minutes off a lenient provider would
# risk exactly that on a strict one, and "works on every provider" is the point.
# So the pace stays inside [start, max] just as before -- it can simply come
# back now. Recovery is slower than back-off on purpose (0.9x per 25 clean
# requests vs 1.5x per 429): pacing too slow costs time, pacing too fast costs
# the movie.
HTTP_PACE_DECAY = 0.85           # multiplier applied after a clean run
HTTP_PACE_DECAY_AFTER = 12       # ...that many consecutive clean requests

# ONE congestion event must widen the pace ONCE.
#
# This is the bug that made every TorBox extraction crawl, and it is a textbook
# one. AIMD's multiplicative decrease belongs to a congestion EPOCH -- TCP halves
# the window once per round trip and deliberately ignores every further loss in
# that window, because those losses are the same event, reported again. We were
# multiplying per 429 RESPONSE. Since a single fetch retries up to
# _HTTP_429_RETRIES times and each retry that answers 429 backed off again, one
# unlucky cluster could multiply the pace by 1.5^5 = 7.6x on its own.
#
# Field log 63addde9 (Rick and Morty S06E07, BluRay REMUX, 364 cues):
#     18:16:16  first 429 after 68 requests   pace 0.20s -> 0.30s
#     18:16:49  SIXTH 429, 27 requests later  pace 1.52s -> 2.00s  (the ceiling)
# Thirty-three seconds of one congestion event, counted six times, and the run
# spent the next four minutes at the 2.00s ceiling doing 143 of 364 cues. The
# proof it was overshoot and not a genuinely slow provider is in the same log:
#     18:20:02  CDN quiet for 25 request(s) -- pace 2.00s -> 1.80s
# Twenty-five consecutive CLEAN requests at 2.00s. The tolerated pace was far
# below the ceiling we pinned ourselves to.
#
# So: after widening, refuse to widen again until we have actually MEASURED the
# new pace -- both a minimum number of completed requests and a minimum elapsed
# time at it. 429s inside that window are still honoured (Retry-After, backoff,
# retry, the streak breaker); they simply do not re-punish a pace whose effect
# is not known yet. If the new pace is genuinely still too fast, the next 429
# after the window widens it again -- which is the controller doing its job
# instead of free-falling to the ceiling on the first burst.
HTTP_PACE_EPOCH_REQS = 8         # requests to complete before widening again
HTTP_PACE_EPOCH_S = 5.0          # ...and seconds to elapse, whichever is later

# Remember what each provider tolerated, so we stop rediscovering it every time.
#
# The epoch fix above stops one congestion event from being punished six times,
# but every extraction still STARTS at 0.20s -- ~5 requests a second, above any
# plausible debrid sustained rate. A token bucket hides that: field log 63addde9
# shows 68 requests sailing through before the first refusal. So each run spends
# its burst allowance, hits the wall, and pays the whole climb again. That is why
# this has needed fixing over and over: the controller had no memory, so every
# episode re-learned the same lesson from zero.
#
# Now the pace a run converged on is written per provider (the last two labels of
# the host, so store-033.wnam.tb-cdn.io and store-027.wnam.tb-cdn.io are the same
# provider) and the next run starts there -- but 25% faster, so it keeps probing
# and a single bad night decays away over a few runs instead of pinning us slow
# forever. It can only ever start at or ABOVE _HTTP_REQ_PACE_S, never below, so
# no remembered value can make us hit a provider harder than a fresh install
# would; the worst case is starting slower than necessary and letting the normal
# decay walk it back.
_PACE_MEMORY_FILE = 'embedded_pace.json'
PACE_MEMORY_PROBE = 0.75         # start this fraction of last time's pace
_PACE_MEMORY_MAX_HOSTS = 16       # keep the file small; drop the oldest
_PACE_MEMORY_MIN_REQS = 20        # too short a run tells us nothing -- don't save
