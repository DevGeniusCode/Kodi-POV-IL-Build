# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Source preparation: the source id for the cache key, the prepared source and its content hash.

import os
from resources.lib.ai_translation.repairs import postfix_cleanup
from resources.lib.core.srt import blocks as srt_blocks, timing


def _source_id_for_ai(payload):
    """Stable identifier for one source SRT, used as part of the
    cache key. Local files get content-hashed because Kodi reuses
    temp paths like TempSubtitle.0.srt across movies -- the filename
    alone is NOT a reliable identifier. Returns '' if we can't
    compute one cheaply (caller will fall back to content-hash after
    the SRT is in memory)."""
    local_path = payload.get('local_path')
    if local_path and os.path.isfile(local_path):
        try:
            import hashlib as _hashlib
            h = _hashlib.sha1()
            with open(local_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()[:16]
        except (IOError, OSError):
            return ''
    return ''


def _prepared_source_reader(local_source):
    """A callable returning the prepared text of the source subtitle at
    `local_source` ('' when it cannot be read), for a lazy `source`."""
    def read():
        try:
            with open(local_source, 'r', encoding='utf-8', errors='replace') as f:
                return _prepare_source(f.read())
        except Exception:
            return ''
    return read


def _prepare_source(raw_src, merge=True):
    """Strip hearing-impaired SOUND cues from a source SRT (but KEEP ALL-CAPS
    speaker prefixes like 'MABEL:' -- the AI uses them to look up the character's
    gender in the cast block, then drops the tag from its Hebrew output), and only
    if the cleaner left at least 30% of the entries (otherwise keep the raw text).
    This is the SAME transform the main translate path applies before hashing --
    factored out so the content hash is computed identically here and in the
    backfill path, guaranteeing both produce the same source_hash and the pool
    never stores two copies of one translation. (Keeping speaker prefixes changes
    the hash ONLY for subs that carry them -- i.e. SDH -- so those re-translate
    once to gain the per-line gender; prefix-free subs hash identically to before.)"""
    # One entry per on-screen caption, without WebVTT class spans; both are
    # no-ops (byte-identical) for a subtitle that has neither.
    raw_src = postfix_cleanup.strip_vtt_class_tags(raw_src)
    if merge:
        raw_src = timing.merge_same_time_cues(raw_src)
    cleaned = postfix_cleanup.strip_hi_annotations(raw_src, keep_speaker_prefixes=True)
    if cleaned and srt_blocks.count_entries(cleaned) >= max(
            1, int(srt_blocks.count_entries(raw_src) * 0.3)):
        return cleaned
    return raw_src


def _content_hash(text):
    """sha1[:16] of the (already prepared) source text -- the pool's
    source_hash / dedup key."""
    import hashlib as _h
    return _h.sha1(text.encode('utf-8', errors='replace')).hexdigest()[:16]
