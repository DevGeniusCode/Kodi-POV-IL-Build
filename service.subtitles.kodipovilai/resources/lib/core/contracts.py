# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The data contracts of the subtitle pipeline (ARCHITECTURE_SUBTITLES.md, section 1.7.4).
#
# ADDED IN PHASE 2, NOT WIRED YET: nothing imports this module until Phase 3, when
# pipeline.py starts to pass these objects between the feature packages instead of the
# implicit dicts of today (candidate rows, link payloads, the selection triple).
# Plain dataclasses, stdlib only, no Kodi imports.

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class VideoContext:
    """The playing video, built once (core.video_context)."""
    imdb_id: str = ''
    tmdb_id: str = ''
    title: str = ''
    year: str = ''
    season: str = ''
    episode: str = ''
    tvshow: str = ''
    playing_release: str = ''     # the ONE derivation: picked_release > tagline > label > li_filename > filepath
    file_url: str = ''
    is_local: bool = False
    is_live: bool = False
    raw: Dict[str, Any] = field(default_factory=dict, compare=False, repr=False)   # sub_utils.current_video_info()


@dataclass
class LinkPayload:
    """The decoded subtitle link (core.link_codec): one of the seven link types."""
    type: str = ''                # passthrough | pool | engine | engine_ai | embedded_ai | embedded_sync | ai
    fields: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    """One row of the subtitle list."""
    kind: str = ''                # human_he | mt_he | pool_he | pool_ai | pool_ai_emb | embedded_he | embedded_foreign
                                  # | foreign | ai_offer | local
    language: str = ''
    release: str = ''
    provider: str = ''
    pct: Optional[int] = None     # a number, never parsed back from a label
    tier: Optional[str] = None
    is_sdh: bool = False
    is_hi: bool = False
    is_cached: bool = False
    link: Optional[LinkPayload] = None    # encoded only at the UI edge
    sync_state: str = ''
    sync_label: str = ''


@dataclass
class ProviderResult:
    """What fetching.provider_adapter turns an engine tuple into."""
    provider: str = ''
    language: str = ''
    filename: str = ''
    pct: int = 0
    hearing_impaired: bool = False
    download: Dict[str, Any] = field(default_factory=dict)   # opaque, handed back to the adapter's download()


@dataclass(frozen=True)
class Selection:
    """The selection triple kept on Window(10000) properties (core.selection)."""
    token: str = ''
    link_hash: str = ''
    stream_hash: str = ''


@dataclass
class SyncVerdict:
    """The outcome of the sync gate (fail-open)."""
    status: str = 'unknown'       # confirmed | fixable | unknown | mismatch | skipped
    path: str = ''
    applied: bool = False
    offset_ms: float = 0.0
    scale: float = 1.0
    source: str = ''


@dataclass
class DeliveryResult:
    """What delivery reports back to the pipeline."""
    path: str = ''
    delivered: bool = False
    cancelled: bool = False
    reason: str = ''


@dataclass
class SearchOptions:
    """Switches of one search."""
    quick: bool = False
    modal_progress: bool = True
    extra: List[str] = field(default_factory=list)
