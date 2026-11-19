# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The Ktuvit harvest: a queue of subtitles to mirror to the pool, and its slow drainer.

import json
import os
import time
from resources.lib.core import kodi
from resources.lib.fetching.pool_client import _QUEUE_MAX_AGE_SEC
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None


# --- Ktuvit harvest queue ---------------------------------------------------
# A SECOND queue, separate from the upload queue above. On every search,
# list_candidates drops a tiny job here for EVERY human Ktuvit subtitle it sees
# (just the media info + the engine download link) -- a fast local write, NO
# Ktuvit hit during playback. The long-lived service then downloads them from
# Ktuvit GENTLY (a couple at a time, throttled, retrying failures across
# sessions and days) and feeds each into the upload queue. This is what makes
# the backup eventually COMPLETE -- every release of a title ends up in the
# pool -- without hammering Ktuvit (which rate-/quota-limits downloads, the very
# reason a fast in-session grab only ever got a random subset) and without
# depending on the user staying on the video. The actual download lives in
# translate.py (which can import the engine); this module owns only the storage.

# Drop a harvest job after this many failed download attempts (Ktuvit removed
# it / permanently refuses), so a dead job can't retry forever.
_HARVEST_MAX_TRIES = 8


def _harvest_queue_dir():
    try:
        d = os.path.join(kodi.cache_dir(), 'pool_harvest_queue')
        if not os.path.isdir(d):
            os.makedirs(d)
        return d
    except Exception:
        return None


def _harvest_job_id(info, payload):
    """Unique per (media + this exact Ktuvit subtitle), so the same release is
    queued once. The engine download_data carries Ktuvit's FilmID+SubtitleID."""
    import hashlib
    dd = payload.get('download_data') or {}
    raw = '{0}:{1}:s{2}:e{3}|{4}'.format(
        info.get('tmdb_id') or info.get('imdb_id') or '',
        'episode' if info.get('is_episode') else 'movie',
        info.get('season') or '0', info.get('episode') or '0',
        json.dumps(dd, sort_keys=True, ensure_ascii=False))
    return hashlib.sha1(raw.encode('utf-8', 'replace')).hexdigest()[:24]


def enqueue_harvest(info, payload):
    """Queue ONE Ktuvit subtitle for background download+share. Fast, durable,
    idempotent, no network. Caller gates on share_enabled()."""
    if not info or not payload:
        return
    d = _harvest_queue_dir()
    if not d:
        return
    try:
        p = os.path.join(d, _harvest_job_id(info, payload) + '.json')
        if os.path.exists(p):
            return
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'info': info, 'payload': payload, 'tries': 0}, f)
        os.replace(tmp, p)
    except Exception as e:
        try:
            kodi.log('harvest enqueue failed: {0}'.format(e),
                           level='DEBUG')
        except Exception:
            pass


def harvest_queue_len():
    d = _harvest_queue_dir()
    if not d:
        return 0
    try:
        return sum(1 for fn in os.listdir(d) if fn.endswith('.json'))
    except Exception:
        return 0


def harvest_jobs(limit=None):
    """NEWEST-first list of (path, job-dict). Newest-first so the title the user
    just played is mirrored before an older backlog (a big title's 20+ releases
    must not make a freshly-played movie wait behind them). Drops unreadable /
    aged-out jobs."""
    d = _harvest_queue_dir()
    if not d:
        return []
    try:
        files = [os.path.join(d, fn) for fn in os.listdir(d)
                 if fn.endswith('.json')]
    except Exception:
        return []
    files.sort(key=lambda p: (os.path.getmtime(p) if os.path.exists(p) else 0),
               reverse=True)
    now = time.time()
    out = []
    for fp in files:
        try:
            if now - os.path.getmtime(fp) > _QUEUE_MAX_AGE_SEC:
                os.remove(fp)
                continue
        except OSError:
            pass
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                out.append((fp, json.load(f)))
        except Exception:
            try:
                os.remove(fp)
            except OSError:
                pass
        if limit and len(out) >= limit:
            break
    return out


def harvest_job_failed(fp, job):
    """Record a failed download attempt; drop the job once it's clearly dead."""
    try:
        tries = int(job.get('tries') or 0) + 1
    except Exception:
        tries = 1
    if tries >= _HARVEST_MAX_TRIES:
        remove_harvest_job(fp)
        return
    try:
        job['tries'] = tries
        tmp = fp + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(job, f)
        os.replace(tmp, fp)
    except Exception:
        pass


def remove_harvest_job(fp):
    try:
        os.remove(fp)
    except OSError:
        pass


# How gently to pull queued Ktuvit subs from Ktuvit on each background pass.
# A few per pass, spaced out, so we never hammer Ktuvit's rate/quota limits
# (the thing that made a fast in-session grab miss most releases). On-device
# logs show downloads at this rate succeed (failed=0), and a failure just stays
# queued and retries, so it's safe to keep the pool filling at a useful pace.
_HARVEST_PER_PASS = 3
_HARVEST_DOWNLOAD_THROTTLE = 6.0


def process_harvest_queue(should_cancel=None):
    """Download a couple of queued Ktuvit subs and feed them into the upload
    queue. Gentle (few per pass, throttled) + retrying (a failed download stays
    queued and is retried on a later pass / day, until it succeeds or is
    declared dead). Runs on the long-lived service. Returns how many were fed to
    the upload queue."""
    if pool_contribute is None:
        return 0
    try:
        if not pool_client.share_enabled():
            return 0
    except Exception:
        return 0
    jobs = pool_harvest.harvest_jobs()
    if not jobs:
        return 0
    try:
        from resources.lib.delivery import rtl
        from resources.lib.fetching.embedded import streams
        if not streams.enabled():
            return 0
    except Exception:
        return 0

    def _norm(s):
        # Exact parity with the Worker's release normalization: notably strips
        # a trailing .srt, so an already-pooled "GROUP" row matches the queued
        # provider filename "GROUP.srt" and avoids both refetch and re-upload.
        return _ktuvit_release_key(s)

    pooled_cache = {}

    def _pooled_for(info):
        key = '{0}:{1}:{2}'.format(
            info.get('tmdb_id') or info.get('imdb_id') or '',
            info.get('season') or '0', info.get('episode') or '0')
        if key not in pooled_cache:
            s = set()
            try:
                for v in pool_client.lookup(info):
                    if (v.get('kind') or 'ai') == 'ktuvit':
                        r = (v.get('release') or '').strip()
                        if r:
                            s.add(_norm(r))
            except Exception:
                pass
            pooled_cache[key] = s
        return pooled_cache[key]

    fed = downloaded = 0
    for fp, job in jobs:
        if downloaded >= _HARVEST_PER_PASS:
            break
        if should_cancel is not None:
            try:
                if should_cancel():
                    break
            except Exception:
                pass
        info = job.get('info') or {}
        payload = job.get('payload') or {}
        rel = payload.get('filename') or ''
        # Already shared by anyone? then this job is done -- no Ktuvit hit.
        if rel and _norm(rel) in _pooled_for(info):
            pool_harvest.remove_harvest_job(fp)
            continue
        try:
            # Harvest/share the immutable provider/cache source, never the
            # playback-only RTL copy. This preserves existing hashes and avoids
            # any re-upload caused solely by display punctuation.
            path = streams.download(
                payload, for_delivery=False)
        except Exception as e:
            kodi.log('ktuvit harvest: download failed "{0}": {1}'.format(
                rel, str(e)[:120]), level='INFO')
            pool_harvest.harvest_job_failed(fp, job)
            downloaded += 1
            _sleep_harvest(should_cancel)
            continue
        downloaded += 1
        if not path or not os.path.isfile(path):
            pool_harvest.harvest_job_failed(fp, job)
            _sleep_harvest(should_cancel)
            continue
        if pool_contribute.was_contributed(path):
            pool_harvest.remove_harvest_job(fp)
            continue
        text = ''
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            text = ''
        if text:
            try:
                pool_contribute.contribute_ktuvit(info, text, release=rel,
                                       marker_path=path,
                                       logical_source=(
                                           rtl
                                           .is_logical_source(path)))
                fed += 1
            except Exception:
                pass
        pool_harvest.remove_harvest_job(fp)
        _sleep_harvest(should_cancel)
    if fed:
        kodi.log('ktuvit harvest: fed {0} sub(s) to the upload queue '
                       '({1} left)'.format(fed, pool_harvest.harvest_queue_len()),
                       level='INFO')
    return fed


def _ktuvit_release_key(value):
    """Canonical Ktuvit release key shared by harvest and pool de-dup."""
    if pool_contribute is None:
        return ''
    return pool_client.worker_norm_release(value)


def _sleep_harvest(should_cancel):
    waited = 0.0
    while waited < _HARVEST_DOWNLOAD_THROTTLE:
        if should_cancel is not None:
            try:
                if should_cancel():
                    return
            except Exception:
                pass
        time.sleep(0.5)
        waited += 0.5
