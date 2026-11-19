# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Probes: the audio probe, a last-resort reference timestamped by Gemini.

import re
import json
import time
try:
    from resources.lib.core import kodi, selection as core_selection, settings, video_context
except Exception:
    kodi = core_selection = settings = video_context = None
from resources.lib.sync.probes.cache import _PROBE_CACHE_VERSION, _probe_cache_path, _read_probe_entry, _write_probe_cache
from resources.lib.sync.probes.remote import _playing_url, _transport_cache_key


_AUDIO_PROMPT = (
    'This is a short audio clip from a TV episode or movie. Identify every '
    'segment of human SPEECH (dialogue in any language, including dubbed '
    'speech). Return ONLY a JSON array, no other text, of objects with start '
    'and end in SECONDS relative to the beginning of THIS clip, e.g. '
    '[{"s": 1.24, "e": 3.41}, {"s": 5.03, "e": 7.75}]. TIMING PRECISION IS '
    'CRITICAL: "s" must be the exact moment the first word begins (two '
    'decimal places). Split segments at pauses longer than 0.7 seconds. '
    'Ignore music, effects and silence.')
# Second-pass sample positions (between the first pass points) used when a
# promising-but-unconfirmed peak needs more reference cues.
_AUDIO_PASS2_POSITIONS = (0.36, 0.62)


def _audio_probe_reference(info, playing, second_pass=False):
    """LAST-RESORT reference (S5): speech intervals from the playing file's
    own AUDIO, timestamped by Gemini (user's existing key). Only reached when
    there is no matching sub in any DB AND no embedded subtitle track. AAC
    audio only (Gemini accepts it as-is after ADTS wrap; AC3/DTS cannot be
    sent or decoded on device). Cached per exact local transport, never by
    release name. None when unavailable.
    second_pass=True samples ADDITIONAL positions and MERGES with the cached
    cues (used once when a promising peak failed the tight check for lack of
    reference points); it marks the cache so it never repeats."""
    from resources.lib.sync.service import _log
    try:
        if (settings.get_setting('subsync_audio', 'true') or
                'true').strip().lower() == 'false':
            return None
        api_key = (settings.get_setting('api_key', '') or '').strip()
        if not api_key:
            return None
        # A cache hit must obey the same local-only contract as extraction.
        # A release-keyed entry could belong to a different cut, or be reused
        # by a remote stream carrying the same release label.
        url = _playing_url(info)
        if not url:
            return None
        transport_key = _transport_cache_key(info)
        if not transport_key:
            return None
        rel_key = 'audio2:' + transport_key
        cpath = _probe_cache_path()
        prior = []
        if cpath:
            ent = _read_probe_entry(rel_key)
            if ent is not None and ent.get('pv') != _PROBE_CACHE_VERSION:
                ent = None   # older engine (un-rebased origin) -- redo
            if ent is not None:
                # Sorted on the way in too: entries cached before the sort
                # below would otherwise keep failing the in-order preflight.
                prior = sorted(ent.get('cues') or [], key=lambda c: c['start'])
                if not second_pass:
                    _log('audio: cache %s for %r'
                         % ('hit (%d cues)' % len(prior) if prior
                            else 'negative', rel_key))
                    return prior or None
                if ent.get('pass2'):
                    return prior or None   # already extended once -- done
        try:
            from resources.lib.fetching.embedded import mkv_probe
            from resources.lib.ai_translation.providers import gemini
        except Exception:
            return None
        segs = mkv_probe.audio_segments(
            url,
            positions=(_AUDIO_PASS2_POSITIONS if second_pass
                       else (0.22, 0.50, 0.78)),
            log=lambda m: _log('audio: ' + m))
        cues = []
        whole_seconds = [0, 0]
        api_failed = False
        if segs:
            model = (settings.get_setting('model', '') or
                     'gemini-3.5-flash-lite')
            for seg in segs[:3]:
                try:
                    txt = gemini.generate_media(
                        api_key, model, _AUDIO_PROMPT, seg['data'],
                        'audio/aac', timeout=60)
                except Exception as e:
                    # Quota/key/network failure is TRANSIENT: stop burning
                    # further calls now, and do NOT cache a negative below --
                    # tomorrow's refreshed quota should get a fresh chance.
                    _log('audio: gemini failed: %r' % e, level='WARNING')
                    api_failed = True
                    break
                m = re.search(r'\[.*\]', txt or '', re.DOTALL)
                if not m:
                    continue
                try:
                    items = json.loads(m.group(0))
                except Exception:
                    continue
                for it in items or []:
                    try:
                        s = float(it.get('s')) * 1000.0 + seg['start_ms']
                        e = float(it.get('e')) * 1000.0 + seg['start_ms']
                        if e > s:
                            cues.append({'start': int(s), 'end': int(e)})
                            whole_seconds[0] += float(it.get('s')).is_integer()
                            whole_seconds[1] += 1
                    except Exception:
                        continue
            # The model lists speech in its own order; the aligner's preflight
            # refuses a reference with one start out of order, and the cache
            # would repeat that refusal for this file every time (NG
            # subtitle-alignment 9.5: model-written references come with order
            # defects). Sort, and drop exact duplicate starts.
            _seen_starts = set()
            cues = [c for c in sorted(cues, key=lambda c: c['start'])
                    if not (c['start'] in _seen_starts or _seen_starts.add(c['start']))]
            # NG measured whole-second times from general Flash models; a reply
            # that is mostly whole seconds can rarely pass the tight check.
            _log('audio: %d speech cues from %d segment(s), %d of %d starts on a '
                 'whole second' % (len(cues), len(segs), whole_seconds[0], whole_seconds[1]))
        if api_failed and not cues:
            return None   # transient -- no negative cache, retry next time
        if second_pass:
            # MERGE with the first-pass cues (dedupe by start time).
            seen = set(c['start'] for c in prior)
            merged = list(prior)
            for c in cues:
                if c['start'] not in seen:
                    seen.add(c['start'])
                    merged.append(c)
            merged.sort(key=lambda c: c['start'])
            cues = merged
        if cpath:
            entry = {'ts': time.time(), 'cues': cues,
                     'pv': _PROBE_CACHE_VERSION}
            if second_pass:
                entry['pass2'] = True
            _write_probe_cache(rel_key, entry)
        return cues or None
    except Exception as e:
        _log('audio reference failed: %r' % e, level='WARNING')
        return None
