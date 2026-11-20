# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# SubSync Phase S1 -- the ONE structured release-name scorer.
#
# Replaces the three divergent difflib token scorers (subs_engine/engine.py's
# sort_subtitles, translate._match_pct, he_sub_match._score) so the subtitle
# picker %, the source-screen HEB badge and the autosub ordering all agree,
# and so the number actually reflects SYNC likelihood instead of raw token
# similarity:
#   - exact release           -> 100  (same file everywhere; synced)
#   - same group + source     -> ~90+ (de-facto synced in practice)
#   - same source class only  -> mid  (often synced, not guaranteed)
#   - cross source class      -> CAPPED LOW (WEB vs BluRay drifts by minutes;
#     the old scorer happily gave these 60-75% on token overlap alone)
#   - different edition/PROPER-> capped (different cut / re-release)
#   - different episode       -> capped very low (another file altogether)
#   - different frame rate    -> capped low (23.976 vs 25 fps drifts for good)
#
# WHAT DECIDES THE NUMBER (Step 6, owner-approved): only attributes that change
# the TIMELINE of a file -- episode, edition / cut, frame rate, source class,
# release group, streaming service. Resolution, video codec, audio format, HDR,
# bit depth and the container never move it: a 720p x264 and a 2160p HEVC encode
# of the same WEB-DL are the same master and share one timeline, so the number
# must not drop (or rise) because of them. The token tie-break ignores them too.
#
# Self-contained ON PURPOSE (like he_sub_match): POV imports our modules by
# path with only resources/lib on sys.path, so NO package-relative imports,
# no xbmc, no PTN. Pure stdlib. Deterministic on every surface.

import re
import difflib

# Tiers, strongest first. score() returns (pct, tier, reasons).
TIER_EXACT = 'exact'      # normalized names identical
TIER_GROUP = 'group'      # same release group + same source class
TIER_SOURCE = 'source'    # same source class, group unknown/different
TIER_FUZZY = 'fuzzy'      # token similarity only (source unknown somewhere)
TIER_CROSS = 'cross'      # contradicting source class / edition / proper

# Tiers safe for AUTOMATIC pick without further verification (S2 will verify
# / retime the rest against a timing oracle).
AUTO_OK_TIERS = (TIER_EXACT, TIER_GROUP)

_SEPS = re.compile(r'[\s_+/\-]+')
_EXT_RE = re.compile(
    r'\.(mkv|mp4|m4p|m4v|avi|mov|mpe?g|flv|wmv|webm|3gp|ogg|ogv|rmvb|divx|vob'
    r'|dat|mts|m2ts|ts|yuv|srt|str|sub|sup|idx|ass|ssa|vtt|smi)$', re.I)

_RES_MAP = {
    '4k': '2160p', 'uhd': '2160p', '2160': '2160p',
    '1080': '1080p', '720': '720p', '480': '480p', '576': '576p',
}
_RES_RE = re.compile(r'\b(2160p|1080p|720p|576p|480p|4k|uhd)\b', re.I)

# Source classes. Order matters only for readability; detection is by token.
_SOURCE_CLASSES = (
    ('bluray', ('bluray', 'blueray', 'bdrip', 'brrip', 'brip', 'bdremux',
                'bd25', 'bd50', 'bd66', 'bd100', 'remux')),
    ('web', ('web', 'webdl', 'webrip', 'webhd', 'webdlrip')),
    ('hdtv', ('hdtv', 'hdtvrip', 'pdtv', 'sdtv', 'tvrip', 'dsr')),
    ('dvd', ('dvdrip', 'dvdr', 'dvd', 'dvdfull')),
    ('hdrip', ('hdrip',)),
    ('cam', ('cam', 'hdcam', 'camrip', 'ts', 'hdts', 'telesync', 'tc',
             'telecine', 'screener', 'scr', 'dvdscr', 'dvdscreener')),
)

_EDITION_TOKENS = {
    'extended': 'extended', 'uncut': 'extended', 'unrated': 'unrated',
    'theatrical': 'theatrical', 'imax': 'imax', 'remastered': 'remastered',
    'directors': 'directors', 'dircut': 'directors', 'dc': None,  # ambiguous
}
_PROPER_TOKENS = ('proper', 'repack', 'rerip')

_CODEC_MAP = {
    'x264': 'h264', 'h264': 'h264', 'avc': 'h264', 'h': None,
    'x265': 'h265', 'h265': 'h265', 'hevc': 'h265',
    'av1': 'av1', 'xvid': 'xvid', 'divx': 'xvid',
}

# Tokens that are never a release group even when trailing after '-'.
_NOT_GROUP = set(
    t for _cls, toks in _SOURCE_CLASSES for t in toks
) | set(_PROPER_TOKENS) | set(_CODEC_MAP) | set(_RES_MAP) | {
    '2160p', '1080p', '720p', '576p', '480p', 'hdr', 'hdr10', 'dv', 'dovi',
    'sdr', 'atmos', 'ddp', 'dd', 'dts', 'ac3', 'eac3', 'aac', 'truehd',
    'multi', 'dual', 'audio', 'heb', 'hebrew', 'eng', 'english', 'subbed',
    'hebsub', 'hebsubs', '10bit', '8bit', 'hc', 'internal', 'complete',
    'lossless', 'clean',
}


# Hyphenated TECHNICAL tags are not release groups. Before this, the scene
# convention "token after the last '-' is the group" read 'dl' out of every
# group-less "...WEB-DL.DDP5.1.H.264" name, 'hd' out of "DTS-HD.MA" and 'ray'
# out of "Blu-ray". Two unrelated group-less WEB-DLs then shared group 'dl',
# scored TIER_GROUP, were TRUSTED as synced and skipped timing verification
# entirely -- and could even serve as a timing oracle for each other.
_HYPHEN_TECH = frozenset({
    'dl', 'rip', 'hd', 'ray', 'ma', 'vision', 'sub', 'subs', 'bit', 'es',
    'dts', 'ac3', 'aac', 'dd', 'ddp', 'hq', 'lq', 'tv', 'track', 'tracks',
    'dub', 'dubbed', 'cut', 'res', 'rd', 'ld', 'cam', 'sync', 'synced',
})
# Tokens that can only FOLLOW a hyphenated technical tag ("WEB-DL.DDP5.1"),
# never a real group (which is followed at most by language/site decoration).
_TRAILING_TECH_RE = re.compile(
    r'^(?:\d+|ddp?\d*|dd\d*|aac\d*|e?ac3|dts\d*|truehd|atmos|flac\d*|opus'
    r'|mp3|l?pcm|ma|hd|h|x|[hx]26[45]|hevc|avc|av1|xvid|\d+bit|hdr\d*|dv'
    r'|dovi|sdr|\d{3,4}p|4k|uhd|web|webdl|webrip|bluray|bdrip|brrip|remux'
    r'|hdtv|dvdrip)$')
# Streaming services. Two WEB releases from DIFFERENT services are separate
# masters: logos, intros and recaps routinely move the timeline by seconds.
# Detected only after the first technical anchor so titles like "Mad Max" or
# "Stan Lee" never read as a service.
_SERVICE_TOKENS = {
    'amzn': 'amzn', 'amazon': 'amzn', 'nf': 'nf', 'netflix': 'nf',
    'dsnp': 'dsnp', 'dsny': 'dsnp', 'dsnyp': 'dsnp', 'disney': 'dsnp',
    'hmax': 'max', 'max': 'max', 'atvp': 'atvp', 'aptv': 'atvp',
    'hulu': 'hulu', 'pcok': 'pcok', 'pmtp': 'pmtp', 'stan': 'stan',
    'crav': 'crav', 'sho': 'sho', 'roku': 'roku', 'vudu': 'vudu',
    'mubi': 'mubi', 'itunes': 'itunes', 'pmnt': 'pmtp', 'shdr': 'shdr',
}
# Editions that change the CUT. One side tagged and the other not is not proof
# of the same timeline (an untagged release is normally the theatrical cut).
_CUT_EDITIONS = frozenset({'extended', 'unrated', 'directors'})


# PRECOMPUTED ONCE, not per call. parse() used to build seven sets on every
# invocation -- one per source class plus the PROPER set -- and it is called
# twice for every scored pair. See the memo note below for why that mattered.
SOURCE_SETS = tuple((cls, frozenset(toks)) for cls, toks in _SOURCE_CLASSES)
_PROPER_SET = frozenset(_PROPER_TOKENS)
_DOTS_RE = re.compile(r'\.+')
_GROUP_RE = re.compile(r'[a-z0-9]+')
_EPISODE_RE = re.compile(r'^s\d{1,2}e\d{1,3}$')
_YEAR_RE = re.compile(r'^(?:19|20)\d{2}$')

# MEMOISED BECAUSE THE CALLER'S SHAPE IS QUADRATIC AND CANNOT EASILY STOP
# BEING SO. POV's source window scores every row against every available
# Hebrew subtitle name: seventy rows against seventeen names is 1190 scored
# pairs, and each pair re-derived the same seventeen names from scratch. A
# field log from a webOS TV showed ten seconds between the scrape finishing
# and the list appearing, scaling almost exactly with the NUMBER OF NAMES --
# three names took two seconds, six took four, seventeen took ten.
#
# Keyed on the raw string, so a name is normalised, tokenised and parsed once
# per interpreter no matter how many rows it is compared against. Bounded and
# cleared wholesale on overflow: this runs inside POV's own long-lived
# interpreter (reuselanguageinvoker), so an unbounded dict here is a leak in
# somebody else's process.
_MEMO_CAP = 4096
_NORM_MEMO = {}
_TOKS_MEMO = {}
_PARSE_MEMO = {}


def _memo_put(cache, key, value):
    if len(cache) >= _MEMO_CAP:
        cache.clear()
    cache[key] = value
    return value


def _strip_ext(name):
    return _EXT_RE.sub('', (name or '').strip())


def _normalize(name):
    s = _strip_ext(name).lower()
    s = _SEPS.sub('.', s)
    return _DOTS_RE.sub('.', s).strip('.')


def normalize(name):
    """Canonical comparison form: lowercase, extension stripped, all
    separators collapsed to single dots."""
    if not isinstance(name, str):
        return _normalize(name)
    cached = _NORM_MEMO.get(name)
    if cached is None:
        cached = _memo_put(_NORM_MEMO, name, _normalize(name))
    return cached


def _toks(name):
    """The token TUPLE, cached. Internal callers use this directly; the
    public tokens() hands out a fresh list so nobody can mutate the cache."""
    if not isinstance(name, str):
        return tuple(t for t in normalize(name).split('.') if t)
    cached = _TOKS_MEMO.get(name)
    if cached is None:
        cached = _memo_put(
            _TOKS_MEMO, name,
            tuple(t for t in normalize(name).split('.') if t))
    return cached


def tokens(name):
    return list(_toks(name))


def content_identity(name):
    """Return a conservative title identity from a release name.

    Timing proofs must never rely on source/group tags alone: two unrelated
    titles released by the same group can otherwise look like a strong match.
    Episodes bind the normalized title plus SxxExx; movies bind title plus
    year.  A title without either marker is usable only when its pre-technical
    token sequence is exact.
    """
    toks = list(_toks(name))
    if not toks:
        return None
    for index, token in enumerate(toks):
        if _EPISODE_RE.fullmatch(token):
            years = tuple(t for t in toks[:index] if _YEAR_RE.fullmatch(t))
            title = tuple(t for t in toks[:index]
                          if not _YEAR_RE.fullmatch(t))
            return ('episode', title, token,
                    years[-1] if years else '') if title else None
    for index, token in enumerate(toks):
        if _YEAR_RE.fullmatch(token) and index:
            return ('movie', tuple(toks[:index]), token)
    technical = (set(_RES_MAP) | set(_CODEC_MAP) | _PROPER_SET |
                 {t for _kind, values in _SOURCE_CLASSES for t in values} |
                 {'2160p', '1080p', '720p', '576p', '480p', 'hdr', 'hdr10',
                  'dv', 'dovi', 'sdr', 'atmos', 'ddp', 'dd', 'dts', 'ac3',
                  'eac3', 'aac', 'truehd', 'multi', 'dual', 'audio'})
    end = next((i for i, token in enumerate(toks)
                if token in technical or token.isdigit()), len(toks))
    title = tuple(toks[:end])
    return ('title', title, '') if title else None


def same_content(video_name, subtitle_name):
    """True only when both release names identify the same title/episode."""
    left = content_identity(video_name)
    right = content_identity(subtitle_name)
    if left is None or right is None:
        return False
    if left[0] != 'episode' or right[0] != 'episode':
        return left == right
    # Some subtitle releases omit the show's debut year, so absence on one
    # side is allowed.  When both name one, it distinguishes revivals sharing
    # the same title and episode number (for example Doctor Who 1963/2005).
    return bool(left[1:3] == right[1:3]
                and (not left[3] or not right[3] or left[3] == right[3]))


# SYNC-NEUTRAL tokens: they describe the encode, not the timeline. Stripped
# before the token tie-break so "1080p.x264" vs "720p.HEVC" costs nothing.
_NEUTRAL_RE = re.compile(
    r'^(?:\d{3,4}p|\d{3,4}x\d{3,4}|[hx]26[45]|\d+bit|hdr\d*|hdr10plus|dv|dovi'
    r'|sdr|ddp?\d*|ddplus|e?ac3|aac\d*|dts|dtshd|dtsx|truehd|atmos|flac\d*|opus'
    r'|mp3|l?pcm|ma|hd|hevc|avc|av1|xvid|divx|uhd|4k|multi|dual|audio)$')
_AUDIO_HEAD_RE = re.compile(r'^(?:dd|aac|e?ac3|dts|truehd|atmos|flac|opus)')
_CORE_MEMO = {}


def _core_tokens(toks):
    """The token tuple without the sync-neutral tags (resolution, codec, audio,
    HDR, bit depth). Split forms ('h', '264'; 'ddp5', '1') go with them."""
    cached = _CORE_MEMO.get(toks)
    if cached is not None:
        return cached
    out = []
    n = len(toks)
    for i, t in enumerate(toks):
        prev = toks[i - 1] if i else ''
        if t in ('h', 'x') and i + 1 < n and toks[i + 1] in ('264', '265'):
            continue
        if t in ('264', '265') and prev in ('h', 'x'):
            continue
        if len(t) == 1 and t.isdigit() and _AUDIO_HEAD_RE.match(prev):
            continue
        if _NEUTRAL_RE.match(t):
            continue
        out.append(t)
    return _memo_put(_CORE_MEMO, toks, tuple(out))


# Frame rate named in a release or subtitle name ("23.976fps", "25 fps", "PAL").
# A frame-rate mismatch makes a subtitle drift for the whole file, so it is a
# sync-determining attribute. Names rarely carry it; absent on either side is
# UNKNOWN, never a mismatch.
_FPS_RE = re.compile(
    r'(?<!\d)(?<!\d[.,])(23[.,]976|23[.,]98|24|25|29[.,]97|30|50|59[.,]94|60)[\s._-]?fps',
    re.I)
_FPS_MEMO = {}


def _fps_of(name):
    """'23.976' / '24' / '25' / ... or '' when the name does not say."""
    if not isinstance(name, str):
        return ''
    cached = _FPS_MEMO.get(name)
    if cached is not None:
        return cached
    value = ''
    m = _FPS_RE.search(name.replace('_', ' '))
    if m:
        value = m.group(1).replace(',', '.')
        if value == '23.98':
            value = '23.976'
    else:
        toks = _toks(name)
        anchor = next((i for i, t in enumerate(toks)
                       if _RES_RE.fullmatch(t)
                       or any(t in c for _k, c in SOURCE_SETS)), None)
        # PAL is 25 fps; only read it among the technical tags ("DVD.PAL"),
        # so a title word is never taken for it.
        if anchor is not None and 'pal' in toks[anchor:]:
            value = '25'
    return _memo_put(_FPS_MEMO, name, value)


def _episode_of(toks):
    """The SxxEyy tag of a name ('' when it has none)."""
    for t in toks:
        if _EPISODE_RE.fullmatch(t):
            return t
    return ''


_SIZE_TOKEN_RE = re.compile(r'\d+(?:kb|mb|gb|tb)')


def is_group_token(cand):
    """True when the lower-case token `cand` can be a release group: a short alphanumeric word that is not a
    technical tag, a language / audio descriptor (DUAL, LOSSLESS, CLEAN ...), a number or a file size (550MB, 4GB)."""
    return bool(cand and cand not in _NOT_GROUP and cand not in _HYPHEN_TECH
                and not cand.isdigit() and not _SIZE_TOKEN_RE.fullmatch(cand)
                and 2 <= len(cand) <= 20 and _GROUP_RE.fullmatch(cand))


def _parse(name):
    toks = _toks(name)
    tokset = set(toks)
    out = {'resolution': '', 'source': '', 'group': '', 'codec': '',
           'edition': '', 'proper': False, 'service': ''}

    m = _RES_RE.search(normalize(name))
    if m:
        r = m.group(1).lower()
        out['resolution'] = _RES_MAP.get(r, r if r.endswith('p') else '')

    for cls, cls_tokens in SOURCE_SETS:
        if tokset & cls_tokens:
            out['source'] = cls
            break
    # 'web' + 'dl'/'rip' appear as separate tokens after normalization;
    # the plain 'web' token above already catches them.

    for i, t in enumerate(toks):
        c = _CODEC_MAP.get(t)
        if c:
            out['codec'] = c
            break
        # Split forms: 'H.264' / 'x 265' normalize to two tokens ('h','264').
        if t in ('h', 'x') and i + 1 < len(toks) and toks[i + 1] in ('264', '265'):
            out['codec'] = 'h' + toks[i + 1]
            break

    for t in toks:
        e = _EDITION_TOKENS.get(t)
        if e:
            out['edition'] = e
            break

    out['proper'] = bool(tokset & _PROPER_SET)

    # A service tag sits among the technical tags: after the resolution or
    # source ("1080p.AMZN.WEB-DL") or directly before the source
    # ("AMZN.WEB-DL.1080p"). An episode title after SxxEyy ("Max.Out") or a
    # movie title ("Mad.Max") is never a service.
    is_source = [any(t in cls_tokens for _c, cls_tokens in SOURCE_SETS)
                 for t in toks]
    first_tech = next((i for i, t in enumerate(toks)
                       if _RES_RE.fullmatch(t) or is_source[i]), None)
    for i, t in enumerate(toks):
        service = _SERVICE_TOKENS.get(t)
        if not service:
            continue
        if ((first_tech is not None and i > first_tech)
                or (i + 1 < len(toks) and is_source[i + 1])):
            out['service'] = service
            break

    # Group: the token after the LAST '-' in the raw name (scene convention
    # "...-NTb"), if it isn't a known technical tag. Fall back to the last
    # normalized token under the same filter.
    raw = _strip_ext(name)
    cand = ''
    from_hyphen = False
    head, _dash, rest = raw.rpartition('-')
    # "Show_s01e02_-_Episode_Title" / "Show - 1x02 - Title": a hyphen with a
    # separator on either side introduces an episode title, never a group.
    spaced = bool(_dash) and (head[-1:] in ' ._' or rest[:1] in ' ._')
    if _dash and not spaced:
        tail = _SEPS.sub('.', rest.strip().lower())
        parts = [p for p in re.split(r'[.\[\]()]+', tail) if p]
        if parts:
            cand = parts[0]
            from_hyphen = True
            # "WEB-DL.DDP5.1.H.264": the hyphen joined a technical tag; the
            # name carries no group at all.
            if (cand in _HYPHEN_TECH
                    or any(_TRAILING_TECH_RE.fullmatch(p) for p in parts[1:])):
                cand = ''
    if not cand and toks and not from_hyphen:
        # Group-less scene-style names ("From.S04E10.1080p.ColdFilm") carry
        # the group as the last token AFTER a technical tag. Without one, the
        # last token is a title word: "The.Flash.S01E02.Fastest.Man.Alive"
        # must not get group 'alive' and then be trusted by containment.
        tech = [i for i, t in enumerate(toks)
                if _RES_RE.fullmatch(t) or t in _CODEC_MAP
                or any(t in cls_tokens for _c, cls_tokens in SOURCE_SETS)]
        if tech and tech[-1] < len(toks) - 1:
            cand = toks[-1]
    if is_group_token(cand):
        out['group'] = cand
    return out


def _parse_c(name):
    """The cached dict itself. Internal, READ-ONLY callers only."""
    if not isinstance(name, str):
        return _parse(name)
    cached = _PARSE_MEMO.get(name)
    if cached is None:
        cached = _memo_put(_PARSE_MEMO, name, _parse(name))
    return cached


def parse(name):
    """Structured fields out of a release name. Every field may be '' when
    not present -- callers must treat '' as UNKNOWN, never as a mismatch.

    A COPY of the cached dict: the cache is shared across every caller in the
    process, and one caller assigning into the result would silently rewrite
    what every other caller sees. Nothing does that today; the copy is what
    keeps that true."""
    return dict(_parse_c(name))


def _token_ratio(a_tokens, b_tokens):
    """Token similarity over the CORE tokens (sync-neutral tags removed)."""
    a_tokens, b_tokens = _core_tokens(a_tokens), _core_tokens(b_tokens)
    if not a_tokens or not b_tokens:
        return 0.0
    try:
        return difflib.SequenceMatcher(None, a_tokens, b_tokens).ratio()
    except Exception:
        return 0.0


_SCORE_MEMO = {}


def score(video_name, sub_name):
    """Structured match: (pct 0-100, tier, reasons list). Either name empty
    -> (0, TIER_FUZZY, []).

    The pair is memoised as well as its two halves, because the same release
    routinely appears in a source list several times over -- one row per host
    offering it -- and each of those rows is scored against the same list of
    subtitle names. The reasons list is copied out, for the same reason parse()
    copies its dict."""
    if not (video_name or '').strip() or not (sub_name or '').strip():
        return 0, TIER_FUZZY, []

    memo_key = None
    if isinstance(video_name, str) and isinstance(sub_name, str):
        memo_key = (video_name, sub_name)
        hit = _SCORE_MEMO.get(memo_key)
        if hit is not None:
            return hit[0], hit[1], list(hit[2])

    pct, tier, reasons = _score(video_name, sub_name)
    if memo_key is not None:
        _memo_put(_SCORE_MEMO, memo_key, (pct, tier, tuple(reasons)))
    return pct, tier, reasons


def best_pct(video_name, sub_names, stop_at=100, floor=0):
    """max(match_pct(video_name, n) for n in sub_names), computed without
    doing work that cannot change the answer.

    EXACTLY EQUAL to the max it replaces when floor=0: a pair is skipped only
    when the branch it lands in provably cannot exceed the running maximum,
    and a skipped pair cannot be the maximum.

    `floor` starts that running maximum higher, for callers that only care
    whether anything clears a bar. floor=79 is the useful one: only the exact,
    containment and same-group branches can return 80 or more, and none of the
    three needs the token ratio -- so "does any of these releases carry a
    built-in Hebrew track" costs no difflib passes at all instead of one per
    candidate. Below the bar it returns 0 rather than the true maximum, which
    is what asking with a floor means."""
    # THE SAME EMPTY-NAME GUARD score() APPLIES, because this calls _score()
    # directly and _score() does not have it. Without this line a whitespace
    # name scored 10 here and 0 through match_pct -- caught by the randomised
    # equivalence test, which is the only reason it is not in the shipped
    # build: eight disagreements in seven hundred trials, all of them a blank.
    try:
        if not (video_name or '').strip():
            return 0
    except AttributeError:
        return 0
    best = 0 if floor <= 0 else floor
    seen_any = False
    for n in sub_names or []:
        try:
            if not (n or '').strip():
                continue
        except AttributeError:
            continue
        key = None
        if isinstance(video_name, str) and isinstance(n, str):
            key = (video_name, n)
            hit = _SCORE_MEMO.get(key)
            if hit is not None:
                seen_any = True
                if hit[0] > best:
                    best = hit[0]
                    if best >= stop_at:
                        break
                continue
        r = _score(video_name, n, floor=best)
        if r is None:
            continue
        seen_any = True
        if key is not None:
            _memo_put(_SCORE_MEMO, key, (r[0], r[1], tuple(r[2])))
        if r[0] > best:
            best = r[0]
            if best >= stop_at:
                break
    if not seen_any and floor <= 0:
        return 0
    return 0 if best <= floor else best


# The most a branch can ever return. Used ONLY to skip work that provably
# cannot change a maximum -- never to change what a branch returns. Each is
# read straight off the branch below it: min(25, ...), min(35, ...),
# min(40, ...); same-source at 55+6+int(ratio*15); fuzzy at
# int(10 + ratio*55).
#
# The last two are stated one HIGHER than they can actually reach, and that
# is deliberate. difflib's ratio() returns 1.0 only for equal sequences, and
# by the time either branch runs the identical-name case has already
# returned -- so the real maxima are 78 and 64, measured over forty thousand
# adversarial pairs. Rounding a ceiling UP is always safe here (it skips
# less); rounding one down would silently lose a match. Round 11 measured
# both, and the constants stay conservative on purpose.
_CEIL_EPISODE = 15
_CEIL_FPS = 30
_CEIL_EDITION = 25
_CEIL_SOURCE = 35
_CEIL_PROPER = 40
_CEIL_SAME_SOURCE = 79
_CEIL_FUZZY = 65


def _score(video_name, sub_name, floor=0):
    """The scorer. `floor` is a caller's running maximum: any branch that
    provably cannot exceed it returns None instead of finishing, and the token
    ratio -- a difflib pass, and by far the most expensive thing here -- is
    computed only if a branch that needs it is actually reached.

    None means "cannot beat `floor`", never "no match". Only best_pct() passes
    a floor; score() does not, so the public result is unchanged."""
    va, sa = normalize(video_name), normalize(sub_name)
    if va == sa:
        return 100, TIER_EXACT, ['identical release']

    v, s = _parse_c(video_name), _parse_c(sub_name)
    vt, st = _toks(video_name), _toks(sub_name)
    ratio = -1.0
    reasons = []

    # HARD contradictions first -- these caps are the whole point: the old
    # token scorer let a WEB sub score 70% against a BluRay source.
    # Another episode is another file: nothing else about the names matters.
    v_ep, s_ep = _episode_of(vt), _episode_of(st)
    if v_ep and s_ep and v_ep != s_ep:
        if floor >= _CEIL_EPISODE:
            return None
        ratio = _token_ratio(vt, st)
        pct = min(15, int(ratio * 25))
        return pct, TIER_CROSS, [
            'different episode ({0} vs {1})'.format(v_ep.upper(), s_ep.upper())]
    if v['edition'] and s['edition'] and v['edition'] != s['edition']:
        if floor >= _CEIL_EDITION:
            return None
        ratio = _token_ratio(vt, st)
        pct = min(25, int(ratio * 40))
        return pct, TIER_CROSS, ['different edition/cut']
    if v['source'] and s['source'] and v['source'] != s['source']:
        if floor >= _CEIL_SOURCE:
            return None
        ratio = _token_ratio(vt, st)
        pct = min(35, int(ratio * 45))
        return pct, TIER_CROSS, [
            'source mismatch ({0} vs {1})'.format(v['source'], s['source'])]
    if v['proper'] != s['proper'] and (v['source'] and s['source']):
        if floor >= _CEIL_PROPER:
            return None
        ratio = _token_ratio(vt, st)
        pct = min(40, int(ratio * 55))
        return pct, TIER_CROSS, ['PROPER/REPACK mismatch']
    # Extended/unrated/director's on ONE side only: the untagged release is
    # normally the theatrical cut, so the same group/name proves nothing.
    if ((v['edition'] in _CUT_EDITIONS) != (s['edition'] in _CUT_EDITIONS)):
        if floor >= _CEIL_EDITION:
            return None
        ratio = _token_ratio(vt, st)
        pct = min(25, int(ratio * 40))
        return pct, TIER_CROSS, ['possibly different cut/edition']
    v_fps, s_fps = _fps_of(video_name), _fps_of(sub_name)
    if v_fps and s_fps and v_fps != s_fps:
        if floor >= _CEIL_FPS:
            return None
        ratio = _token_ratio(vt, st)
        pct = min(30, int(ratio * 40))
        return pct, TIER_CROSS, [
            'frame rate mismatch ({0} vs {1} fps)'.format(v_fps, s_fps)]

    same_source = bool(v['source'] and s['source'])  # equal if both set here
    same_group = bool(v['group'] and s['group'] and v['group'] == s['group'])
    other_service = bool(v['service'] and s['service']
                         and v['service'] != s['service'])
    same_service = bool(v['service'] and s['service']
                        and v['service'] == s['service'])

    # Containment: one normalized name fully inside the other (after the
    # contradiction guards above). Covers provider-decorated variants of the
    # SAME release ("...ColdFilm" vs "...ColdFilm.rus"). Requires the shorter
    # side to be a real identity: enough tokens AND its own release group. A
    # group-less prefix ("Show.S01E01.1080p.WEB") is shared by every release
    # of that episode, so it is a same-source match at best, never a trusted
    # one.
    shorter, longer = (va, sa) if len(va) <= len(sa) else (sa, va)
    if shorter in longer and not other_service:
        sp = v if shorter == va else s
        # "Show.S01E02.Episode.Title" names no release at all, however its
        # last word parses; trust needs a technical identity on that side.
        if (len(shorter.split('.')) >= 4 and sp['group']
                and (sp['resolution'] or sp['source'] or sp['codec'])):
            return 96, TIER_GROUP, ['release name contained']

    if same_group and not other_service:
        # Same release group is the strongest identity signal. With the same
        # source class it's a near-certain sync; with source UNKNOWN on one or
        # both sides (groups like ColdFilm often ship no WEB/BluRay tag at
        # all) it's still group-level trust -- contradicting sources already
        # returned TIER_CROSS above.
        # Resolution / codec / container do not move this number (see the
        # header): the same group + source is one timeline at any encode.
        pct = 94 if same_source else 90
        reasons.append('same group + source' if same_source
                       else 'same group (source untagged)')
        if same_service:
            pct += 3
            reasons.append('same streaming service ({0})'.format(
                v['service'].upper()))
        return min(pct, 99), TIER_GROUP, reasons

    if same_source:
        if floor >= _CEIL_SAME_SOURCE:
            return None
        pct = 55
        reasons.append('same source class ({0})'.format(v['source']))
        if same_service:
            pct += 6
            reasons.append('same streaming service ({0})'.format(
                v['service'].upper()))
        if v['group'] and s['group'] and v['group'] != s['group']:
            pct -= 8
            reasons.append('different group')
        if other_service:
            pct -= 10
            reasons.append('different streaming service (%s vs %s)'
                           % (v['service'], s['service']))
        if ratio < 0:
            ratio = _token_ratio(vt, st)
        pct += int(ratio * 15)   # token tie-break WITHIN the tier
        return max(20, min(pct, 84)), TIER_SOURCE, reasons

    # Source unknown on at least one side (and groups don't match): token
    # similarity only, bounded so it can never outrank a structural match.
    if floor >= _CEIL_FUZZY:
        return None
    if ratio < 0:
        ratio = _token_ratio(vt, st)
    pct = int(10 + ratio * 55)
    return min(pct, 79), TIER_FUZZY, reasons


def match_pct(video_name, sub_name):
    """Drop-in int replacement for the three legacy scorers."""
    try:
        return score(video_name, sub_name)[0]
    except Exception:
        return 0


def match_tier(video_name, sub_name):
    try:
        return score(video_name, sub_name)[1]
    except Exception:
        return TIER_FUZZY


_SIM_MEMO = {}


def similarity(video_name, sub_name):
    """How close the FULL names are, 0-1000 (1000 = the same spelling). Unlike
    match_pct it counts EVERYTHING: resolution, codec, audio, HDR and the exact
    filename. It is a TIE-BREAK only (candidates with the same pct, e.g. two 97%
    matches of one group); it never feeds pct and is never shown as a match %.
    Never raises."""
    try:
        if not (video_name or '').strip() or not (sub_name or '').strip():
            return 0
        key = (video_name, sub_name) if (isinstance(video_name, str)
                                         and isinstance(sub_name, str)) else None
        if key is not None:
            hit = _SIM_MEMO.get(key)
            if hit is not None:
                return hit
        va, sa = normalize(video_name), normalize(sub_name)
        if va == sa:
            value = 1000
        else:
            tok = difflib.SequenceMatcher(
                None, _toks(video_name), _toks(sub_name)).ratio()
            chars = difflib.SequenceMatcher(None, va, sa).ratio()
            value = min(999, int(round((0.7 * tok + 0.3 * chars) * 1000)))
        if key is not None:
            _memo_put(_SIM_MEMO, key, value)
        return value
    except Exception:
        return 0


def explain(video_name, sub_name):
    """Why a subtitle release got its number, for the chooser's info panel.

    Returns {'pct', 'tier', 'reasons', 'facts'}. `facts` lists the attributes
    in this order -- the sync-deciding ones first (episode, edition, frame
    rate, source, group, streaming service), then the sync-neutral ones
    (resolution, codec) -- as (key, video value, subtitle value, state), where
    state is 'match', 'mismatch', 'unknown' (one side does not say) or
    'neutral' (shown for information; it never moves the number). Never raises.
    """
    try:
        pct, tier, reasons = score(video_name, sub_name)
        v, s = _parse_c(video_name), _parse_c(sub_name)
        vt, st = _toks(video_name), _toks(sub_name)
        pairs = (
            ('episode', _episode_of(vt).upper(), _episode_of(st).upper()),
            ('edition', v['edition'], s['edition']),
            ('fps', _fps_of(video_name), _fps_of(sub_name)),
            ('source', v['source'], s['source']),
            ('group', v['group'], s['group']),
            ('service', v['service'], s['service']),
        )
        facts = []
        for key, a, b in pairs:
            if key == 'edition':
                # an untagged release is the theatrical cut: a cut edition on
                # ONE side only is a contradiction (the scorer caps it too)
                if a and b:
                    state = 'match' if a == b else 'mismatch'
                elif a in _CUT_EDITIONS or b in _CUT_EDITIONS:
                    state = 'mismatch'
                else:
                    state = 'unknown'
            else:
                state = ('unknown' if not (a and b)
                         else 'match' if a == b else 'mismatch')
            facts.append((key, a, b, state))
        for key in ('resolution', 'codec'):
            facts.append((key, v[key], s[key], 'neutral'))
        return {'pct': pct, 'tier': tier, 'reasons': list(reasons),
                'facts': facts}
    except Exception:
        return {'pct': 0, 'tier': TIER_FUZZY, 'reasons': [], 'facts': []}


def best(video_name, sub_names):
    """(best_pct, best_name) across a list of candidate release names."""
    best_pct, best_name = 0, ''
    for n in sub_names or []:
        p = match_pct(video_name, n)
        if p > best_pct:
            best_pct, best_name = p, n
    return best_pct, best_name


# A synthesized "release" (subs_filename_publisher fallback builds
# Title.SxxExx.QUALITYp.mkv when POV published nothing) must never be treated
# as a real release identity: it has no group/source, so exact/group tiers
# against it are meaningless -- and it must never anchor a timing oracle (S2).
def is_synthetic(name):
    p = parse(name)
    return not p['group'] and not p['source']
