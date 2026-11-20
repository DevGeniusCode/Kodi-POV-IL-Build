# Credit grammar of subtitle credits (Provenance P2).
#
# Pure, stdlib only (no xbmc, no other module of this add-on). One normaliser and one grammar serve both places a
# credit shows up:
#   * a provider's metadata string, 'תרגום: אולפנים | הביא, תיקן וסנכרן: Yoav1610 | תואם לגרסאות WEB'
#     (Ktuvit's red lines, Wizdom's `credits`): parse_credits()
#   * the free text of a credit cue, a release name or an upload comment, in the order a cp1255 file stores it
#     (visual: 'Afenla :תרגום וסנכרון') or the logical order ('תרגום וסנכרון: Afenla'): scan_line() / scan_cue()
# It finds ROLES and FORMS and exposes tokens for name matching; it never decides who is trusted (that is
# scoring_matching/provenance.py, which holds the allowlist).
#
# Roles: 'translation' (a translator or a translation team), and the sync family 'sync' / 'fix' / 'fetch'. A sync-family role
# is never evidence of who translated: a machine translation can be synced, fixed and fetched by anybody.
#
# cues._CREDIT_RE (core/srt/cues.py) is the older, narrower filter that drops credit cues before alignment; it stays
# as it is. The contract (unit_provenance) asserts this grammar knows every Hebrew word that filter knows.

import re
import unicodedata

STUDIO_WORD = 'אולפנים'
SYNC_FAMILY = frozenset(('sync', 'fix', 'fetch'))
TEAM_MARKERS = frozenset(('מצוות', 'צוות', 'חבר', 'חברי'))   # 'Qsubs מצוות', 'Qsubs חברי צוות': a team attribution without a verb
MAX_CREDIT_LINE = 160     # a credit line is short; longer text is dialogue
EDGE_CUES = 8             # a credit is in the first or the last 8 cues of a file (the Telegram corpus: 90.7% in the first / last 3)

# ---- normalising --------------------------------------------------------------------------------------------------------
_BIDI_RE = re.compile('[​-‏‪-‮⁦-⁩﻿]')
_NIQQUD_RE = re.compile('[֑-ׇ]')
_TAG_RE = re.compile(r'<[^>]+>|\{\\[^}]*\}')
_WS_RE = re.compile(r'\s+')
_QMARK_RE = re.compile(r'(?<!\w)\?+|\?+(?!\w)')     # a cp1255 round trip leaves '?' where a bidi mark was
_QUOTES = {'״': '"', '“': '"', '”': '"', '׳': "'", '‘': "'", '’': "'"}


def normalize(text):
    """One line of credit text, ready for matching: NFKC, markup and bidi controls (U+200B-F, U+202A-E, U+2066-9,
    BOM) and niqqud removed, curly / Hebrew quotes unified, stray '?' dropped, white space collapsed."""
    if not isinstance(text, str):
        return ''
    s = unicodedata.normalize('NFKC', text)
    s = _TAG_RE.sub(' ', s)
    s = _BIDI_RE.sub('', s)
    s = _NIQQUD_RE.sub('', s)
    for k, v in _QUOTES.items():
        s = s.replace(k, v)
    s = _QMARK_RE.sub(' ', s)
    return _WS_RE.sub(' ', s).strip()


_TOKEN_RE = re.compile(r'[^\W_]+')


def tokens(text):
    """Case-folded letter / digit runs of the normalised text ('DownRev.subs-צוות' -> downrev, subs, צוות)."""
    return tuple(_TOKEN_RE.findall(normalize(text).casefold()))


def name_key(text):
    """The comparison key of a name: normalised, case-folded, letters and digits only
    ('yoav_1610' and 'Yoav1610' are one key, 'The Duke' is 'theduke')."""
    return ''.join(tokens(text))


def has_sequence(haystack, needle):
    """Whether the token tuple `needle` occurs in `haystack` as consecutive tokens (a name is matched whole)."""
    n = len(needle)
    if not n or n > len(haystack):
        return False
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


# ---- role words ----------------------------------------------------------------------------------------------------------
# Word level (prefix 'ו' = and). The same alternatives, wrapped in word boundaries, find the roles in a line.
_TRANS_W = r'ו?(?:תורגם|תורגמו|תורגמה|תרגום|תרגם|תרגמו|תירגם|translated|translation)'
_SYNC_W = r'ו?(?:סונכרן|סונכרנו|סונכרנה|סנכרן|סינכרן|סנכרון|סינכרון|סנכון|resync(?:ed|hed)?|sync(?:ed|hed)?)'
_FIX_W = r'ו?(?:תיקון|תיקונים|תיקן|תוקן|הגהה|הגהות|נערכו|נערך|עריכה|corrected|fixed)'
_FETCH_W = r'ו?(?:הובא|הובאו|הביא|נקרע|קודד|הוספת)'
_ROLE_WORDS = (('translation', re.compile(_TRANS_W + '$', re.I)), ('sync', re.compile(_SYNC_W + '$', re.I)),
               ('fix', re.compile(_FIX_W + '$', re.I)), ('fetch', re.compile(_FETCH_W + '$', re.I)))
_GLUE = frozenset(('לגרסה', 'לגרסא', 'לגירסה', 'זו', 'הזאת', 'כתוביות', 'לעברית', 'מחודש', 'לשונית', 'שורות', 'גם',
                   'משמיעה', 'משמיע', 'על', 'ידי', 'ע"י', 'by'))
_B = r'(?<!\w)'
_E = r'(?!\w)'
_LINE_ROLES = (('translation', re.compile(_B + _TRANS_W + _E, re.I)), ('sync', re.compile(_B + _SYNC_W + _E, re.I)),
               ('fix', re.compile(_B + _FIX_W + _E, re.I)), ('fetch', re.compile(_B + _FETCH_W + _E, re.I)))
_BY_EAR_RE = re.compile(_B + r'(?:משמיעה|משמיע)' + _E)

# Forms only a studio or an official source writes (never a bare brand word: 'Disney', 'Yes', 'HOT' are noise, and so is the
# Netflix title card 'סדרה מקורית של NETFLIX').
_STUDIO_FORMS = (
    ('production', re.compile(_B + r'ו?הפקת\s+(?:תרגום|כתוביות)')),
    ('subtitle_credit', re.compile(_B + r'תרגום\s+(?:כתוביות|כותרות)' + _E)),
    ('hebrew_credit', re.compile(_B + r'עברית\s*:')),
    ('source_tag', re.compile(r'subtitles?\s+source\s*:', re.I)),
    ('studio_translation', re.compile(_B + r'תרגום\s*:?\s*' + STUDIO_WORD + _E)),
)
_QUALIFIER_RE = re.compile(r'\((?:' + STUDIO_WORD + r'|נטפליקס)\)')     # 'תרגום: <name> (אולפנים)' / '(נטפליקס)'
# The label phrases that name the studio form on their own ('תרגום כתוביות: <name>'): a metadata label.
STUDIO_LABELS = frozenset(('תרגום כתוביות', 'תרגום כותרות', 'הפקת תרגום', 'הפקת כתוביות', 'הפקת תרגום וכתוביות',
                           'תרגום והפקת כתוביות', 'עברית'))


def _word_roles(token):
    return {role for role, rx in _ROLE_WORDS if rx.match(token)}


def label_roles(label):
    """The roles named by a credit LABEL ('תרגום', 'הביא, תיקן וסנכרן', 'תיקון וסנכרון'), or None when the label is
    not a credit label (a note such as 'תואם לגרסאות בלוריי באורך 03' is not). Every word must be a role word or glue."""
    n = normalize(label).strip(' .:-')
    if not n:
        return None
    if n in STUDIO_LABELS:
        return {'translation'}
    roles = set()
    for t in re.split(r'[\s,/]+', n):
        t = t.strip('.:-')
        if not t:
            continue
        r = _word_roles(t)
        if r:
            roles |= r
        elif t.casefold() not in _GLUE:
            return None
    return roles or None


def is_credit_word(word):
    """Whether one word is a role word (translated, synced, fixed, fetched) or a glue word of a credit label."""
    t = normalize(word).strip(' .:-')
    return bool(t) and (bool(_word_roles(t)) or t.casefold() in _GLUE)


# ---- a metadata string -----------------------------------------------------------------------------------------------------
_NAME_SPLIT_RE = re.compile(r'\s*(?:,|&|/|;|\+|(?<=\s)ו(?=\s)|\band\b)\s*', re.I)
_PAREN_RE = re.compile(r'^(.*?)\s*\(([^()]+)\)\s*$')


def split_names(value):
    """The names of a credit value, as written: 'TheInitiative (HDSubs)' gives the whole text, 'TheInitiative' and
    'HDSubs'; 'a, b & c' gives three."""
    v = normalize(value).strip(' .-')
    out = []
    for part in _NAME_SPLIT_RE.split(v) if v else []:
        part = part.strip(' .-')
        if not part:
            continue
        out.append(part)
        m = _PAREN_RE.match(part)
        if m:
            out.extend(x.strip() for x in (m.group(1), m.group(2)) if x.strip())
    return out


class Credit(object):
    """One labelled credit of a metadata string."""
    __slots__ = ('label', 'roles', 'value', 'names', 'raw', 'by_ear', 'studio')

    def __init__(self, label, roles, value, names, raw, by_ear, studio):
        self.label, self.roles, self.value, self.names, self.raw = label, frozenset(roles), value, tuple(names), raw
        self.by_ear, self.studio = by_ear, studio

    def __repr__(self):
        return 'Credit(%r, %s, %r)' % (self.label, sorted(self.roles), self.names)


def parse_credits(text):
    """(credits, notes) of a metadata string whose parts are separated by '|' (or a line break). A part is a credit when it
    is 'label: value' and the label is a credit label; any other part is a note (a compatibility remark, a format)."""
    credits, notes = [], []
    if not isinstance(text, str):
        return credits, notes
    for seg in re.split(r'\s*[|\n]\s*', text):
        n = normalize(seg)
        if not n:
            continue
        label, sep, value = n.partition(':')
        roles = label_roles(label) if sep else None
        if not roles:
            notes.append(n)
            continue
        label = label.strip(' .:-')
        credits.append(Credit(label, roles, value.strip(), split_names(value), n, bool(_BY_EAR_RE.search(label)),
                              normalize(label) in STUDIO_LABELS))
    return credits, notes


# ---- free text: a cue, a release name, an upload comment ---------------------------------------------------------------------
class LineInfo(object):
    """What a short text says: its tokens (for name matching), the roles it names and the studio forms it contains."""
    __slots__ = ('norm', 'tokens', 'roles', 'by_ear', 'studio_forms', 'team_marker')

    def __init__(self, norm, toks, roles, by_ear, studio_forms):
        self.norm, self.tokens, self.roles = norm, toks, frozenset(roles)
        self.by_ear, self.studio_forms = by_ear, tuple(studio_forms)
        self.team_marker = bool(TEAM_MARKERS & set(toks))

    @property
    def credit_like(self):
        return bool(self.roles or self.studio_forms)

    def __repr__(self):
        return 'LineInfo(%r, %s, ear=%s, forms=%s)' % (self.norm[:40], sorted(self.roles), self.by_ear, self.studio_forms)


def scan_line(text, limit=MAX_CREDIT_LINE):
    """A short text -> LineInfo, or None when it is empty or longer than `limit` (dialogue, not a credit)."""
    n = normalize(text)
    if not n or len(n) > limit:
        return None
    roles = {role for role, rx in _LINE_ROLES if rx.search(n)}
    forms = [name for name, rx in _STUDIO_FORMS if rx.search(n)]
    if 'translation' in roles and _QUALIFIER_RE.search(n):
        forms.append('qualifier')
    return LineInfo(n, tokens(n), roles, bool('translation' in roles and _BY_EAR_RE.search(n)), forms)


def scan_cue(cue_text):
    """One cue's text (its lines are joined: a credit may wrap) -> LineInfo or None."""
    return scan_line(' '.join(str(cue_text or '').splitlines()))


def edge_texts(cue_texts, n=EDGE_CUES):
    """The first and the last `n` cue texts of a file, in order, without repeats (a short file is read whole)."""
    texts = list(cue_texts or [])
    if len(texts) <= 2 * n:
        return texts
    return texts[:n] + texts[-n:]


def scan_cues(cue_texts, n=EDGE_CUES):
    """The LineInfo of every non-empty short cue among the first and last `n`."""
    out = []
    for t in edge_texts(cue_texts, n):
        info = scan_cue(t)
        if info is not None:
            out.append(info)
    return out
