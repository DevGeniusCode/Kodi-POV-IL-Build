# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The knobs of the AI translation path: backoff schedules, per-chunk budgets, request limits and switches.
# (They were local variables and module constants of the old 3,700-line resolve().)


# Numbered entries WITHOUT timecodes (srt_wire, NG's index/text request):
# replies ~45% smaller and up to 6x faster offline -- but in Kodi Google's
# PROHIBITED_CONTENT filter blocked the denser text: 13 blocked requests vs 0
# on the same titles; Gilmore Girls chunk 13 blocked 3/3 without timecodes
# and 0/3 with them (2/3 even with the timecodes kept in the request and left
# out of the reply only). A blocked chunk falls back to a weaker reference or
# to English, so this stays OFF. True: request and reply without timecodes.
NUMBERED_REQUESTS = False

# After a gender-referenced translation, Hebrew lines the readers cannot read
# on a role the reference marks are labelled by one blind classifier request
# (SRT AI Translator NG's Method B); contradictions join the guarded repair.
# OFF since 0.3.51, as NG keeps it: measured in the QA Kodi (5 titles with a
# Hebrew reference, 23 Oct 2026, blind A/B of every line the repair changed)
# it changed 5 lines -- 2 better, 2 worse, 1 no difference -- for one extra
# request per title. The readers' own repairs: 34 changed, 17 better, 9 worse.
GENDER_CLASSIFIER = False

CLASSIFIER_MAX_LINES = 200

# Arabic roots for severe policy-trigger terms (sexual violence / torture /
# slurs). When a per-entry Arabic gender-reference line contains one of these,
# we OMIT just that line from the prompt. Rationale, validated live against the
# API: the prompt-level PROHIBITED_CONTENT block is driven by the VOLUME of
# explicit terms in one request; the English subtitle alone translates fine
# (it stays under the threshold), but the Arabic reference REPEATS those same
# explicit terms and tips it over. The Arabic is only a gender oracle, and for
# these specific lines the gender is virtually always clear from context anyway
# (e.g. an established female victim), so dropping them removes the block trigger
# while keeping gender correct (confirmed: feminine forms stayed right with the
# explicit Arabic lines stripped). The whole-chunk drop-Arabic -> bisect ->
# keep-source cascade remains as a safety net for anything that still blocks.
AR_EXPLICIT_MARKERS = (
    'اغتص', 'غتصب', 'اغتُص', 'تعذيب', 'عذّب', 'عذب', 'عاهر', 'الاغتصاب',
)

# A fallback reference for a blocked chunk is looked for briefly, and once.
# The search went on through every candidate left, up to the providers'
# whole budget (60 s of downloads), for each blocked chunk in turn: QA
# Kodi, 16 Nov 2026 -- the first reference in hand after 5 s; a chunk was
# blocked 25 s later, and 33 subtitles were downloaded and refused over
# the next 40 s while it waited. The next language's best match aligns in
# its first tries or the title's subtitles are another cut; a chunk that
# gets no fallback is isolated line by line, as before.
REF_FALLBACK_TRIES = 6

REF_FALLBACK_SECONDS = 15.0

# Safety: a single 'whole subtitle' request can't fit a very large source --
# the Hebrew output overflows the 65535-token cap and Gemini truncates it, so
# the response fails the entry-count / is-Hebrew checks and the whole thing is
# discarded as empty (with NO progressive display to show partial progress).
# This bites SDH / embedded sources especially (dense, long). Above ~80K chars
# fall back to chunked so the user gets a real, progressive translation.
WHOLE_REQUEST_MAX_CHARS = 80000

# Backoff schedule for retryable Gemini failures (503 overload,
# 500 / 502 / 504 transients). Google's published guidance is to
# wait at least a few seconds before retrying these.
# Ten minutes of them since 0.3.79 (it was four): a viewer who asked for an
# AI translation wants the AI's, and Google's text is what follows the
# last retry. The lines not translated yet stay in their source language
# meanwhile, and the default model has hundreds of requests a day.
OVERLOAD_BACKOFF = [5, 15, 30, 60, 120, 120, 120, 120]  # seconds

# Other transient (non-overload) Gemini errors get a shorter
# schedule -- they're usually content / parse / safety issues,
# not infrastructure.
GENERIC_BACKOFF = [2, 5]

# Prompt-level blocks (PROHIBITED_CONTENT) come in two flavours, confirmed by
# live A/B testing against the API:
#   * FLAKY (borderline content volume) -- the SAME prompt succeeds on a
#     retry. A few same-prompt retries catch these.
#   * PERSISTENT (high explicit-content volume in one request) -- the SAME
#     prompt blocks deterministically every time (measured 5-6/6 and 10/10).
#     Retrying the identical prompt is pure waste here.
# The block is driven by the absolute VOLUME of explicit content per request,
# and a text disclaimer does NOT help (tested: generic/specific, system-
# Instruction/contents -- all blocked). What actually escapes it is reducing
# volume: dropping the Arabic block (halves it) and bisecting (isolates the
# explicit cluster). So we retry the same prompt only ONCE to catch the flaky
# case, then degrade FAST to bisect/drop-Arabic. Rate-limiting is now handled by
# the global pacer (_gemini_rate_gate), so extra filtered retries are pure
# latency; and the per-chunk budget below guarantees a stubborn chunk keeps
# SOURCE rather than stalling the whole job (a real log showed one chunk
# bisect-storming a PROHIBITED_CONTENT block for ~5 min and never finishing,
# so the job never cached/uploaded).
FILTERED_BACKOFF = [4]

# On a blocked chunk, try ALTERNATE chain languages (Spanish after Arabic,
# then French, Russian, ...) before dropping the gender reference -- try
# EVERY aligned language available, not just one, so a stubborn block gets
# the best chance to keep per-line gender. This is bounded on its own: the
# lazy reference download budget (arabic_gender._TOTAL_DOWNLOAD_BUDGET) caps
# how many languages ever get pulled, _ref_ensure() returns False the moment
# the chain is exhausted (so the loop stops early), and the per-chunk wall-
# clock circuit-breaker below caps total time. 5 covers the core gender-
# marking set (ar/es/fr/ru/it) beyond the primary.
MAX_ALT_LEVELS = 5

NO_REF = -1                # _call_gemini ref_level meaning "English only"

# Generous per-chunk wall-clock backstop for fighting content blocks. It is a
# pure CIRCUIT-BREAKER: the structured fallback (alt language -> bisect to
# isolate -> English-only per line -> keep source) already terminates on its
# own, so this only trips on a pathological, pervasively-explicit chunk to
# stop it stalling the JOB's finalization (a real log showed one chunk
# bisect-storming a block for ~5 min, so the job never cached/uploaded). When
# it trips it translates the remainder ENGLISH-ONLY in one shot (still
# Hebrew, just no gender); source is kept only if even that is blocked.
# It runs from the chunk's first block (_BlockClock): a slow Gemini is no
# block. A chunk that never blocks has the slow cap instead -- ten
# minutes (three seconds a line past 200 lines: a whole-subtitle request)
# and 20 requests plus one per 4 lines (32 for 50 lines).
CHUNK_BLOCK_BUDGET = 120.0

CHUNK_SLOW_CAP = 600.0

CHUNK_SLOW_PER_LINE = 3.0

CHUNK_MAX_CALLS = 20

CHUNK_LINES_PER_CALL = 4

# Per-minute rate limit (HTTP 429, RPM/TPM) -- TEMPORARY, clears within ~60s.
# Back off (preferring Gemini's own retryDelay) and retry the SAME chunk so AI
# translation continues to the end instead of aborting to Google mid-movie.
# Only a genuine per-DAY 429 (gemini.QuotaExceeded) is terminal.
RATELIMIT_BACKOFF = [20, 30, 45, 60, 60]

# How long _english_only_safe may keep paying for Gemini calls PAST the
# chunk's block budget. The breaker only fires once that budget is spent,
# and finalizing the remainder needs SOME spend -- but its bisection used
# to run with no clock at all, and a persistently-filtered chunk costs
# O(2n) calls each dragging a real 4s filtered-backoff sleep: minutes past
# the budget for one pathological chunk. Past this grace, the remainder
# keeps its source per-line with zero further calls.
EO_GRACE = 90.0

# How many top-up rounds one chunk may spend chasing entries the model keeps
# dropping. Each round asks only for what is still missing, so the set
# shrinks fast; three rounds is generous and bounds the cost on a shared
# quota. Anything still missing after that keeps its source text -- visibly
# untranslated, which is the honest outcome and is what happens today for
# every one of them.
TOPUP_ROUNDS = 3

# The repairs after the translation (gender, English residual, foreign
# script) are one request each, and a transient overload used to drop
# them outright -- measured on a film: 20 gender errors the check had
# found stayed on screen because that one request met an HTTP 503.
REPAIR_BACKOFF = (5, 15)
