# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Constants of the embedded-subtitle reader: sizes of the head / cluster / range reads, HTTP windows, retry and
# pacing limits, and the codec / cut-signature numbers.


# A cut id must describe the media bytes, never a tokenised URL or a release
# label.  Three tiny samples catch same-name/same-length replacements without
# scanning the movie: beginning, middle and end, plus the authoritative file
# length.  The first sample is normally already present in the Matroska head
# read, so a remote cue probe pays only two additional 64 KiB ranges.
CUT_SIGNATURE_VERSION = b'povil-cut-v1\0'
CUT_SAMPLE_BYTES = 64 * 1024

SUB_TRACK_TYPE = 0x11

# ---- budgets ----------------------------------------------------------------
DEFAULT_HEAD_BYTES = 2 * 1024 * 1024
# extract_srt over HTTP asks for at most this much in one request. It used to
# open with one 2 MB head read and one 1.3 MB Cues read; on a provider at a bad
# minute (TorBox, QA 13 Nov 2026) such a request holds the line for seconds,
# and the caller's check of the player's buffer can only run between requests.
# In pieces it runs every 256 KB, and the head is read only as far as the
# first Cluster -- usually one piece instead of 2 MB.
HTTP_PIECE = 256 * 1024
# Once the index is in, cluster reads use this instead: a window scan (1.75 MB)
# stays one request as before, an 8 MB coalesced range goes in four. In 256 KB
# pieces a file without per-cue positions took about three times as long
# (review of 0.3.81), for a check that already runs before every request.
HTTP_PIECE_CLUSTERS = 2 * 1024 * 1024
# A remote MP4 movie index is read only when it is this small: an
# episode's moov is 1-3 MB, and playback must keep the bandwidth.
MP4_REMOTE_INDEX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_BYTES = 80 * 1024 * 1024      # surgical Cues fetch stays well under
DEFAULT_DEADLINE_S = 30.0
HTTP_TIMEOUT = 15
# The EOF probe reads only the first few bytes of its answer, but leaving the
# rest unread forces the shared keep-alive connection to be thrown away and
# rebuilt on the next read -- and storming a debrid CDN with fresh connections
# is what 429s the token. So finish a body that is small enough to finish, and
# only abandon one too big to bother with (a provider that ignores Range answers
# with the whole file, which can be tens of gigabytes).
PROBE_DRAIN_MAX = 64 * 1024
# The probe asks for a range STARTING at bytes we already hold and running past
# the point in question, so the provider has to answer a satisfiable range --
# the one form of the question it is obliged to answer precisely. _PROBE_CONTROL
# is how much known data it starts from (long enough that a provider serving the
# wrong region cannot match it by chance, short enough to be free) and
# _PROBE_AHEAD is how far past the point it reaches.
PROBE_CONTROL = 64
PROBE_AHEAD = 16
# A debrid CDN token rate-limits by request COUNT and recovers after a short
# cooldown, so a transient 429 is EXPECTED (the relpos fast path issues many
# small requests). Back off (honor Retry-After) and retry rather than tripping
# the breaker on the first 429; only give up after exhausting retries. A small
# per-request pace keeps the burst rate under the limiter in the first place
# (the field 429 fired at ~35 req/s). Sleeping is safe -- extraction runs in a
# background thread, never the player's callback.
HTTP_429_RETRIES = 5
# NOT reduced under sustained pressure, though it is tempting: retrying a read
# in place costs a fresh GET each time, so a run against a pushing-back CDN
# sends far more requests than it appears to (a field log's "60 req" was nearer
# 110 as far as the CDN was concerned -- hence the http_reqs counter). But
# exhausting these retries is exactly what trips the breaker, and that trip is
# what keeps a partial extract from being delivered. Measured: cutting them to 2
# sent 84% fewer requests and returned 0 of 50 cues.
HTTP_429_MAX_WAIT = 30.0          # cap on one backoff sleep (seconds)
# A 429 means the shared token is at its limit and the PLAYER needs that
# headroom, so back off substantially (not just enough for our own retry).
HTTP_429_BASE_WAIT = 3.0

# How many keep-alive connections the targeted (relpos) fetches may use at once.
#
# The extraction was never bandwidth-bound -- it was LATENCY-bound. One
# connection, one request at a time: pace (0.2s) + a debrid round trip (~0.35s)
# = ~0.55s per cue, and an episode's ~450 cues took about four minutes with the
# link sitting idle for most of it.
#
# The important part is what this does NOT change: the request RATE. The pace
# gate below is now shared by every connection, so the CDN still sees at most
# one request per `_pace` seconds no matter how many are in flight -- exactly
# the rate the AIMD controller converged on when it was serial. Concurrency
# only stops us paying the round trip on top of the gap we were already
# waiting. A 429 still widens the pace for everyone at once, and the
# circuit-breaker is still shared, so pushback stops all of them together.
#
# DEFAULT 1 -- OFF. The field settled this, and it settled it against me.
#
# The reasoning above has a hole, and TorBox found it in under thirty seconds.
# Keeping the same PACED SCHEDULE is not the same as keeping the same request
# RATE. Serially we asked once every pace + round trip (0.2 + 0.35 = ~1.8/s);
# with three connections the round trip stops counting and we ask once every
# pace (5/s). That IS the speedup -- a latency-bound loop cannot be made faster
# without asking more often -- and it is also three times the load, which is
# exactly what a debrid token rate-limits.
#
# What the field log showed (Rick and Morty S03E05, 572 cues): concurrency ran
# for 29 seconds, collected 5 backoffs, stood down as designed -- and by then the
# AIMD controller had ratcheted the pace from 0.20s to its 2.00s ceiling. The
# pace decays only after 25 consecutive clean requests, so recovering from that
# takes hundreds of them. The extraction was left crawling at ten times its
# normal gap: about nineteen minutes for an episode that used to take four. The
# safety machinery all worked; it just could not undo the damage the burst had
# already done.
#
# So concurrency is off until there is a provider it can be shown to help on,
# measured against that provider rather than against a stand-in. Everything it
# needs is still here and still tested -- set this to 3 to run it -- and the
# stand-down now also puts the pace back where it found it (see below), so a
# burst can no longer poison the rest of the run.
# NOTE (0.3.81): translate.py's buffer gate assumes ONE connection. Before this
# goes above 1, release_connection() must close the spares too, and the gate's
# state (_buf, _stall in _extract_embedded_srt) needs a lock: the abort
# callback would then be entered by several fetch threads at once.
HTTP_CONNS = 1

# Clusters fetched one at a time before the concurrent phase may start (see
# _healthy). Two is the normal cost; the third is slack for a file that needs
# one more before its CueTime can be proven.
CONC_SEED_MAX = 3
# Fail-fast on a token the provider rate-limits HARD (TorBox): if this many
# fetches IN A ROW each need a 429 backoff, the token is saturated and a
# ~1700-request extraction is hopeless -- crawling on keeps the token hot for
# minutes, and the movie dies the moment the user unpauses into it. Give up
# early (~20s) with a clean deferral instead. A healthy provider (Real-Debrid)
# lands clean fetches that reset the streak, so it never trips there.
HTTP_429_STREAK_MAX = 6
# ...and, with that streak reached, how long we must have collected NOTHING
# NEW before giving up. Back-pressure alone is not saturation: a provider can
# refuse half our reads while the player runs perfectly and cues keep landing,
# and abandoning there costs the user a whole play session for nothing now that
# progress is banked. Genuine saturation stops the cues, and this notices.
STALLED_PROGRESS_S = 45.0
CLUSTER_CAP_LOCAL = 32 * 1024 * 1024     # local: effectively read whole clusters
CUES_CAP = 24 * 1024 * 1024              # hard ceiling on the Cues element read

# HTTP/debrid Cues extraction (field incident 2026-07-19: a fresh-connection-per-
# read storm 429'd the CDN token and KILLED playback). The safe shape, proven in
# the wild: ONE keep-alive connection (see _Source._sess) + single-range serial
# fetches of cluster windows, coalescing nearby cluster positions into few large
# Range requests, a circuit-breaker on the first 429/5xx, a top-up for clusters
# bigger than the window, and hard byte/time caps so a spread-out file DEFERS to
# the external path rather than fetch gigabytes alongside the player.
# Per-cluster window for the WINDOW-SCAN fallback (files whose Cues carry NO
# CueRelativePosition). 1792KB, not 512KB: his live debrid telemetry (2026-06,
# real 1080p WEB-DL) showed the TRUE cluster median ~1.51MB, p99 ~1.72MB -- the
# subtitle block sits AFTER the cluster's video keyframe, so a 512KB window
# truncated nearly every cluster and forced a top-up round-trip. 1792KB covers
# p99 in one fetch; the top-up stays as the safety net for the rare outlier.
CLUSTER_WINDOW_HTTP = 1792 * 1024        # window-scan read per cue cluster
CLUSTER_TOPUP_MAX = 8 * 1024 * 1024      # top-up read cap for one big cluster
COALESCE_GAP = 1 * 1024 * 1024           # merge cluster positions within this
MAX_RANGE = 8 * 1024 * 1024              # cap per coalesced Range request
HTTP_TOTAL_CAP = 700 * 1024 * 1024       # give up (defer) past this many bytes
# CueRelativePosition FAST PATH (the common case: mkvmerge writes relpos by
# default). When a cue tells us the subtitle block's offset INSIDE its cluster
# we fetch just a tiny header (to resolve the cluster prefix + timestamp) and a
# small window AT the block, instead of pulling the whole ~1.5MB cluster. ~18x
# less data than the window scan -> far gentler on the player's bandwidth on a
# scattered remux, which is exactly the debrid case.
# Kept SMALL on purpose: a subtitle (Simple)Block/BlockGroup is well under 1 KB,
# so we only need a few KB at the target. The prior 16KB+128KB (~144 KB/cue) was
# wasteful THROUGHPUT that -- on a strict token (TorBox) sharing bandwidth with
# the player -- drained the player's buffer and stalled it (field, 2026-07-19,
# no 429: pure bandwidth contention). 8KB header + 32KB block = ~40 KB/cue, ~3.6x
# less. 32KB still comfortably covers a block + BlockGroup; a rare larger element
# just falls through to the window-scan for that one cue.
CLUSTER_HDR_READ = 8 * 1024              # header read to resolve prefix + ts
BLOCK_READ_HTTP = 32 * 1024              # window fetched AT a targeted block
# Many lines in one request. A cue fetched on its own costs one request at the
# provider's pace -- 2,376 lines of a film took ten minutes in QA (TorBox, 14
# Nov 2026) though each is a few hundred bytes. HTTP can ask for many ranges in
# one GET, and TorBox answers it (multipart/byteranges): 200 ranges of 4 KB in
# 2.6 s, where 20 single requests took 4.6 s. So once a file's layout is
# learned, the next _MULTI_RANGES blocks are fetched in one request into a
# small store that the ordinary reads are then served from, each _BLOCK_READ_
# MULTI long instead of _BLOCK_READ_HTTP (a text block is a few hundred bytes;
# one longer than that is read again on its own). A provider that answers
# anything else -- a whole file, one coalesced range, an error -- is asked one
# range a request again, as before, for the rest of the run.
# At most 200 ranges in one request, the cluster heads included: Apache's
# default MaxRanges is 200, and above it the whole file comes back -- which
# would end the batches for the run (review of 0.3.82).
MULTI_RANGES = 200
# A cluster's prefix (its ID and the size VINT) is 5 to 12 bytes, and it is
# NOT the same in every cluster of a file: South Park S04 MULTI WEB 2160p
# D5T0 has 3-byte sizes in most clusters and 4-byte ones in some. One learned
# prefix is then a byte off for those, and in QA every such cluster cost a
# relearn (58 of them) and the batch's alignment (161 reads missed the store):
# 361 lines in 48 s. So a batch also fetches the first _CLUSTER_HEAD_PEEK
# bytes of each cluster, and starts each block window at the smallest prefix
# a cluster can have; each cluster is then read at its own prefix.
CLUSTER_HEAD_PEEK = 16
PREFIX_MIN, PREFIX_MAX = 5, 12
# The window a batch reads at each line. A text block is a few hundred bytes;
# 4 KB windows made Interstellar's batches 13 MB, and inside Kodi, with the 4K
# film streaming from the same node, reading those bodies was ~60 of its 106 s
# (sampled profile, QA 14 Nov 2026). A block longer than this is read again on
# its own, so nothing is lost.
BLOCK_READ_MULTI = 1024
MULTI_SLACK = 64 * 1024                  # headers of the parts, on top of the data
MULTI_BUSY_HOLD_S = 15.0                 # after a busy answer to one: single ranges
MULTI_STORE_MAX = 4 * 1024 * 1024        # bytes kept from batches, at most
# A COLD file -- one the provider has not served these places of yet -- costs
# 40-100 ms a range, a warm one 3-4 ms (lab, 14 Nov 2026, TorBox: the same
# link read twice outside Kodi while the film played; the first read of The
# Boys 1x01 took 69.5 s, the second 5.6 s). On a cold file connections that
# SHARE small batches scale almost linearly: 1 -> 86-100 ms a range, 3 -> 43,
# 4 -> 24-31, 6 -> 21, 8 -> 15.6, every answer 206 and the film playing on.
# (One big batch per connection did not: connections land on a few back-end
# workers at random, and two on one worker wait for each other -- 0.3.83 read
# that as "no gain".) So: only on a host it was measured on, only once a batch
# has shown the file is cold, _MULTI_PARALLEL connections take batches of
# _MULTI_SHARE_RANGES from one queue; the player's buffer gate is asked twice
# a second and holds every new request while it waits; any batch that fails
# ends it for the run (review of 0.3.83).
# FOUR, not more: with 12 and 16 connections the provider answered 429 to most
# batches (lab, a cold film: 8 -> all 206), so its limit lies between 9 and
# 13 -- and it may count the account, not the link. Four leave room for the
# player (its cache opens a connection at every seek) and another device at
# home: a 429 that lands on the PLAYER closes the film (review of 0.3.84).
MULTI_PARALLEL = 4
# ...and not for this long after the buffer gate stood aside in mid-reading:
# the player was short of data a moment ago.
SHARE_AFTER_HOLD_S = 60.0
MULTI_SHARE_RANGES = 50
# The first batch of a pass, where sharing is possible and the file is not
# known to be cold: only this many ranges, so a cold stretch shows itself in a
# couple of seconds and the rest of the pass is shared (QA, 14 Nov 2026, Pulp
# Fiction: the lines beside the viewer were warm -- the player had just read
# there -- and the next 119, cold, went as one 11.9 s batch).
MULTI_PROBE_RANGES = 24
COLD_RANGE_MS = 15.0
PARALLEL_HOSTS = ('tb-cdn.st',)
# parts the store keeps between batches: what a parallel set brings, and one
MULTI_STORE_PARTS = (MULTI_PARALLEL + 1) * 200
# Between clusters, the caller's abort and progress callbacks at most this
# often. Both ask Kodi things (is it playing, paused, the clock, the selected
# subtitle), and with a 4K film on screen each such call can take tens of ms:
# QA, 14 Nov 2026, Interstellar 2160p DV -- 2,666 lines in 97 s from only 40
# requests, about 35 ms a cluster with the batches served from memory. Every
# network request still asks abort_cb first (_Source), so nothing is fetched
# behind the player's back.
CALLBACK_POLL_S = 0.25
# ONE-SHOT ceiling. When a header read IS needed, size it to swallow the first
# block too if that is cheap -- one request instead of two. Only bites on files
# whose subtitle block precedes the cluster's video keyframe; measured on a
# realistic remux the relpos is ~0.4-1.5MB, so this rarely fires and the header
# elimination below is what actually removes the second request.
ONE_SHOT_MAX = 128 * 1024
# HEADER ELIMINATION. Measured (synthetic remux, 40 clusters x 1-3 subtitle
# cues): 2.03-2.10 range requests PER CUE, because each cue paid an 8KB read at
# the cluster start purely to learn two things -- the cluster PREFIX (Cluster-ID
# length + size-VINT length, the origin CueRelativePosition is measured from)
# and the cluster TIMESTAMP. Neither has to be re-read:
#   * the prefix is a property of the MUXER, not of the cluster -- one file's
#     clusters are all within one size-VINT width of each other, so it is
#     learned once and reused;
#   * the absolute timestamp is ALREADY in the Cues index, as CueTime.
# Both are assumptions about a third party's file, so neither is trusted on
# faith: the first cluster is still read the old way and the two answers are
# CHECKED against it, and any later block that fails to parse falls back to a
# header read for that cluster (and re-learns) before the cue is handed to the
# window-scan net. Worst case is today's cost; typical case is half of it.
PREFIX_MIN = 5                 # 4-byte Cluster ID + 1-byte size VINT
PREFIX_MAX = 12                # 4-byte Cluster ID + 8-byte size VINT
# Two subtitle blocks in ONE cluster share a read when they sit this close. Kept
# deliberately small: a muxer interleaves by timestamp, so a cluster's subtitle
# blocks are typically HUNDREDS of KB apart, and pulling the video between them
# to save one request would trade a round-trip for bandwidth the player needs
# (field 2026-07-19 was a pure bandwidth-contention stall, no 429 at all).
CLUSTER_BATCH_MAX = 96 * 1024
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
