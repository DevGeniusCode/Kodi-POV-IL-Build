# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The state of one resolve() call (JobState), the stage protocol (NEXT) and the first stage "begin":
# the selection that started the job, the selection-bound callbacks, the decoded link.

from resources.lib.core import kodi, selection as core_selection
from resources.lib.core.link_codec import decode_link
from resources.lib.ai_translation.progress import set_quiet


NEXT = object()   # what a stage returns when the work goes on with the next stage


class JobState(object):
    """The state of one resolve() call: every variable that more than one stage, or a closure of the job,
    reads or writes. Nothing here is shared between calls. The attribute names are the variable names of
    the old 3,700-line function without their leading underscore."""
    __slots__ = (
        # arguments of resolve()
        'extract_progress_cb', 'fallback_link', 'fallback_links', 'info', 'link', 'progress_cb', 'progressive_cb',
        'selection',
        # first set in the stage "begin"
        'episode', 'imdb_id', 'kind', 'payload', 'proofread_only', 'season', 'steps_he', 'steps_source_id',
        'timing_selection',
        # first set in the stage "prepare"
        'ar_diag', 'ar_map', 'ar_on', 'embedded_timing', 'gp_ref', 'local_source', 'pool_kind', 'ref_lang',
        'ref_lock', 'ref_plan', 'ref_stack', 'release_override', 'replacing_google', 'source_lang', 'src_release',
        'tier', 'timing_release',
        # first set in the stage "cache"
        'content_id', 'early_source_id', 'src_text', 'timing_src', 'translated',
        # first set in the stage "handoff"
        'google_prev', 'progressive_source_id',
        # first set in the stage "request"
        'api_key', 'blocks', 'cast', 'chunk_lines', 'gemini_timeout', 'max_output_tokens', 'model', 'models',
        'prompt_template', 'temperature', 'thinking_budget', 'thinking_level', 'thinking_raw', 'top_p',
        'whole_subtitle_request',
        # first set in the stage "reference"
        'final_pool_hash', 'used_ar',
        # first set in the stage "plan"
        'chunk_lock', 'chunk_stat', 'chunks', 'google_chunks', 'gp_report', 'paid_mode', 'ratelimit_notified',
        'rpm_interval', 'switch_lock', 't0', 'telemetry_done', 'total',
        # first set in the stage "dispatch"
        'busy_toasts', 'busy_waits', 'job_gave_up', 'out_blocks_by_index', 'prev_context_lines',
        # first set in the stage "merge"
        'out_blocks',
    )

    def __init__(self, link, info, progress_cb, progressive_cb,
                 extract_progress_cb, selection, fallback_link, fallback_links):
        self.link = link
        self.info = info
        self.progress_cb = progress_cb
        self.progressive_cb = progressive_cb
        self.extract_progress_cb = extract_progress_cb
        self.selection = selection
        self.fallback_link = fallback_link
        self.fallback_links = fallback_links



def selection_current(S):
    """True only while the selection that started this resolve is active."""
    try:
        return bool(
            all(S.timing_selection.get(k) for k in (
                'token', 'link_hash', 'stream_hash'))
            and core_selection.subtitle_selection_matches(
                S.timing_selection.get('token') or '',
                S.timing_selection.get('link_hash') or '',
                S.timing_selection.get('stream_hash') or ''))
    except Exception:
        return False


def translation_cancelled(S):
    """Stop an obsolete job without affecting callers without a selection."""
    return (all(S.timing_selection.get(k) for k in (
                'token', 'link_hash', 'stream_hash'))
            and not selection_current(S))


def publish_timing_status(S, state, source):
    try:
        if not all(S.timing_selection.get(k) for k in (
                'token', 'link_hash', 'stream_hash')):
            return False
        return bool(core_selection.set_subtitle_sync_status(
            state, source=source,
            selection_token=S.timing_selection.get('token') or '',
            link_hash=S.timing_selection.get('link_hash') or '',
            stream_hash=S.timing_selection.get('stream_hash') or ''))
    except Exception:
        return False


def begin(S):
    """Bind the job to the selection that started it, wrap the callbacks, decode the link."""
    # Bind every later timing result to the selection that STARTED this
    # resolve. Downloads and AI translation can take minutes; taking this
    # snapshot only inside SubSync would let an older result label, queue or
    # replace a subtitle selected in the meantime.
    try:
        S.timing_selection = (dict(S.selection) if S.selection is not None else
                             core_selection.current_subtitle_selection(
                                 expected_link=S.link)) or {}
    except Exception:
        S.timing_selection = {}

    # Progressive AI callbacks can replace the subtitle in the player. Guard
    # those too, not only the final SubSync result: a stale background process
    # may finish after the viewer chose a human/embedded subtitle.
    if S.progressive_cb is not None:
        _progressive_cb = S.progressive_cb

        def _selection_bound_progressive(phase, payload):
            try:
                if not core_selection.subtitle_selection_matches(
                        S.timing_selection.get('token') or '',
                        S.timing_selection.get('link_hash') or '',
                        S.timing_selection.get('stream_hash') or ''):
                    return
            except Exception:
                return
            return _progressive_cb(phase, payload)

        S.progressive_cb = _selection_bound_progressive

    if S.extract_progress_cb is not None:
        _extract_progress_cb = S.extract_progress_cb

        def _selection_bound_extract_progress(*args, **kwargs):
            try:
                if not core_selection.subtitle_selection_matches(
                        S.timing_selection.get('token') or '',
                        S.timing_selection.get('link_hash') or '',
                        S.timing_selection.get('stream_hash') or ''):
                    return
            except Exception:
                return
            return _extract_progress_cb(*args, **kwargs)

        S.extract_progress_cb = _selection_bound_extract_progress

    S.payload = decode_link(S.link)
    if not S.payload:
        kodi.log('resolve: bad link', level='ERROR')
        return None

    S.kind = S.payload.get('type')
    kodi.log('resolve: kind={0}'.format(S.kind), level='INFO')
    # proofread_later's job: a translation already on screen gets its gender
    # proofread and a corrected file swapped in -- never a translation.
    S.proofread_only = bool(S.payload.get('proofread_only'))
    # {start ms: Hebrew} of an embedded track shown in steps while it was read
    # (_EmbeddedSteps); empty for every other job.
    S.steps_he = {}
    # ...and the source id its views went to the screen under ('' when no view
    # was shown). From then on it is THIS job's id on screen: what the
    # handler holds, and so what "is this translation still the one being
    # watched" has to be asked about (the residual repairs, the proofread).
    S.steps_source_id = ''
    if S.proofread_only:
        set_quiet(True)     # its own process: the status toasts are not its

    S.imdb_id = (S.info.get('imdb_id') or '').strip()
    S.season  = S.info.get('season') or ''
    S.episode = S.info.get('episode') or ''
    return NEXT
