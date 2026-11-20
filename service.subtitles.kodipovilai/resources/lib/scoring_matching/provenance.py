# Provenance classifier of HEBREW subtitle rows (Provenance P2).
#
# Pure, stdlib only (it imports core.srt.credits, itself stdlib only). It reads the `meta` dict a provider recorded
# for a row (fetching/providers/meta.py) and, for a file already downloaded, the credit cues of its first and last
# cues (credits.scan_cues), and says which of four POSITIVE badges the evidence supports, highest first:
#
#   VERIFIED_STUDIO      'תרגום אולפנים'     a studio / official credit form, or the translator field 'אולפנים'
#   VERIFIED_TEAM        'צוות תרגום מוביל'  a known translation team in the translator role (or a team account as uploader)
#   VERIFIED_TRANSLATOR  'מתרגם מוכר'        an allowlisted translator, or an explicit translated-by-ear credit
#   TRUSTED_UPLOADER     'מעלה בולט'         an allowlisted uploader (or one the user added)
#
# Rules that are not negotiable here (owner decisions of 20 Nov 2026):
#   * Hebrew rows only: the caller passes hebrew=True, anything else gets an empty verdict.
#   * Positive only: there is no "unverified" or "AI" state; a row without evidence has badge ''.
#   * Display only: the verdict never hides, reorders or blocks anything; it is data for the chooser's panel.
#   * Sync / fetch / fix credits NEVER give a badge (a machine translation can be synced); they are returned apart,
#     for the panel ('sync').
#   * OpenSubtitles' 'from_trusted' flag is not a signal, and the bulk importer 'os-auto' (rank 'Application Developers')
#     is never a trusted uploader.
#   * A claim is the provider's: every reason names the provider (and the field) it came from.

import json

from resources.lib.core.srt import credits as cr

VERIFIED_STUDIO = 'VERIFIED_STUDIO'
VERIFIED_TEAM = 'VERIFIED_TEAM'
VERIFIED_TRANSLATOR = 'VERIFIED_TRANSLATOR'
TRUSTED_UPLOADER = 'TRUSTED_UPLOADER'
BADGES = (VERIFIED_STUDIO, VERIFIED_TEAM, VERIFIED_TRANSLATOR, TRUSTED_UPLOADER)      # highest first
BADGE_LABELS = {
    VERIFIED_STUDIO: 'תרגום אולפנים',
    VERIFIED_TEAM: 'צוות תרגום מוביל',
    VERIFIED_TRANSLATOR: 'מתרגם מוכר',
    TRUSTED_UPLOADER: 'מעלה בולט',
}
IMPORTER_NAME = 'os-auto'                   # OpenSubtitles' bulk importer of 2008-2019 files: 52 of 85 rows of the 20 Nov probe
IMPORTER_RANK = 'Application Developers'     # its rank; both are excluded here whatever the allowlist or the user's handles say
MIN_KEY = 3                                 # a shorter name is too common to match whole ('TB')
_CATEGORIES = ('studios', 'teams', 'translators', 'uploaders', 'syncers')


# ---- the allowlist ---------------------------------------------------------------------------------------------------------
class Allowlist(object):
    """The names the classifier trusts. Each category maps a name key (credits.name_key) to its display name, and keeps the
    token tuples of its names for matching inside free text. Built by build_allowlist / load_allowlist."""

    def __init__(self):
        self.keys = {c: {} for c in _CATEGORIES}
        self.name_tokens = {c: [] for c in _CATEGORIES}
        self.never = set()
        self.custom = {}           # key -> display, the user's own handles (a trusted uploader or translator)
        self.skipped = []          # (category, name) not loaded: too short, or marked 'proposed'

    def add(self, category, name, display=None):
        key = cr.name_key(name)
        if len(key) < MIN_KEY:
            self.skipped.append((category, name))
            return
        self.keys[category][key] = display or name
        self.name_tokens[category].append((cr.tokens(name), display or name))


def _split_custom(text):
    out = []
    for part in str(text or '').replace('\n', ',').replace(';', ',').split(','):
        part = part.strip()
        if part:
            out.append(part)
    return out


def build_allowlist(data, custom=''):
    """An Allowlist from the parsed JSON (see config/provenance_allowlist.json) plus the user's comma-separated handles.
    An entry is a name or {'name', 'aliases', ...}; one with 'status': 'proposed' is a suggestion and is not loaded.
    Never raises on a bad file: a bad entry is skipped."""
    allow = Allowlist()
    data = data if isinstance(data, dict) else {}
    for category in _CATEGORIES:
        for entry in data.get(category) or []:
            try:
                if isinstance(entry, dict):
                    if entry.get('status') == 'proposed':
                        allow.skipped.append((category, entry.get('name')))
                        continue
                    names = [entry.get('name')] + list(entry.get('aliases') or [])
                else:
                    names = [entry]
                display = names[0] if isinstance(names[0], str) else None
                for name in names:
                    if isinstance(name, str):
                        allow.add(category, name, display)
            except Exception:
                continue
    for name in data.get('never') or []:
        if isinstance(name, str) and cr.name_key(name):
            allow.never.add(cr.name_key(name))
    for name in _split_custom(custom):
        key = cr.name_key(name)
        if len(key) >= MIN_KEY:
            allow.custom[key] = name
    return allow


def load_allowlist(path, custom=''):
    """build_allowlist of the JSON file at `path`; an unreadable file gives an empty allowlist (only the studio forms,
    which need no list, still count)."""
    try:
        with open(path, 'rb') as f:
            data = json.loads(f.read().decode('utf-8'))
    except Exception:
        data = {}
    return build_allowlist(data, custom)


# ---- the verdict -----------------------------------------------------------------------------------------------------------
def empty_verdict():
    return {'badge': '', 'label': '', 'reasons': [], 'translator': '', 'sync': [], 'known_syncers': [], 'notes': [],
            'uploader': '', 'by_ear': False, 'fixed': False, 'date': '', 'downloads': None, 'provider': ''}


def _reason(badge, source, field, text, match=''):
    return {'badge': badge, 'source': source, 'field': field, 'text': text[:160], 'match': match}


def _names_evidence(allow, names, raw, source, field, out):
    """The badges the NAMES of a translation credit support (a studio, a team, an allowlisted / custom translator); each
    reason carries the allowlist's own display name of the match."""
    for name in names:
        key = cr.name_key(name)
        if not key or key in allow.never:
            continue
        if key == cr.name_key(cr.STUDIO_WORD):
            out.append(_reason(VERIFIED_STUDIO, source, field, raw, cr.STUDIO_WORD))
        elif key in allow.keys['studios']:
            out.append(_reason(VERIFIED_STUDIO, source, field, raw, allow.keys['studios'][key]))
        elif key in allow.keys['teams']:
            out.append(_reason(VERIFIED_TEAM, source, field, raw, allow.keys['teams'][key]))
        elif key in allow.keys['translators']:
            out.append(_reason(VERIFIED_TRANSLATOR, source, field, raw, allow.keys['translators'][key]))
        elif key in allow.custom:
            out.append(_reason(VERIFIED_TRANSLATOR, source, field, raw, allow.custom[key]))


def _line_evidence(allow, info, source, field, out):
    """The badges one scanned free-text line supports. A studio form or name counts on any line; a translator needs a
    translation role in the same line, and a team one too, or a team marker ('מצוות', 'חברי צוות': an attribution whose verb
    is on the neighbouring line) with no sync-family role. A sync / fetch / fix line naming a team is never evidence."""
    for form in info.studio_forms:
        out.append(_reason(VERIFIED_STUDIO, source, field, info.norm, form))
    for toks, name in allow.name_tokens['studios']:
        if cr.has_sequence(info.tokens, toks):
            out.append(_reason(VERIFIED_STUDIO, source, field, info.norm, name))
    translated = 'translation' in info.roles
    if translated or (info.team_marker and not info.roles & cr.SYNC_FAMILY):
        for toks, name in allow.name_tokens['teams']:
            if cr.has_sequence(info.tokens, toks):
                out.append(_reason(VERIFIED_TEAM, source, field, info.norm, name))
    if not translated:
        return
    named = list(allow.name_tokens['translators']) + [(cr.tokens(n), n) for n in allow.custom.values()]
    for toks, name in named:
        if cr.has_sequence(info.tokens, toks):
            out.append(_reason(VERIFIED_TRANSLATOR, source, field, info.norm, name))
    if info.by_ear:
        out.append(_reason(VERIFIED_TRANSLATOR, source, field, info.norm, 'by_ear'))


def _dedupe(reasons):
    """One reason per badge, source and field (a studio named twice in one comment is one reason), in the order found."""
    seen, out = set(), []
    for r in reasons:
        k = (r['badge'], r['source'], r['field'])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _pick(reasons):
    for badge in BADGES:
        if any(r['badge'] == badge for r in reasons):
            return badge
    return ''


def classify(meta, allow, *, hebrew, provider='', cue_infos=None):
    """The verdict of one row. `meta` is the provider's meta dict ({} / None when it has none), `allow` an Allowlist (None =
    empty), `hebrew` whether the row is a Hebrew subtitle (anything else gets an empty verdict), `provider` its display name
    for the reasons, `cue_infos` the credits.scan_cues() of the downloaded file when known (P4).

    Returns a dict: badge (one of BADGES or ''), label (its Hebrew text), reasons (every evidence found, each with badge /
    source / field / text / match), and for the panel: translator (the raw translator value), sync (the raw sync / fetch /
    fix credits), known_syncers, notes, uploader, by_ear, fixed, date, downloads, provider. Never raises."""
    v = empty_verdict()
    try:
        if not hebrew or not isinstance(meta, dict):
            return v
        allow = allow if isinstance(allow, Allowlist) else Allowlist()
        v['provider'] = provider
        v['uploader'] = str(meta.get('uploader') or '')
        v['fixed'] = bool(meta.get('fixed'))
        v['date'] = str(meta.get('date') or '')
        v['downloads'] = meta.get('downloads') if isinstance(meta.get('downloads'), int) else None
        reasons = []

        credits_list, notes = cr.parse_credits(meta.get('credits'))
        v['notes'] = notes
        for c in credits_list:
            if 'translation' in c.roles:
                if not v['translator']:
                    v['translator'] = c.value
                if c.studio:
                    reasons.append(_reason(VERIFIED_STUDIO, provider, 'credits', c.raw, c.label))
                _names_evidence(allow, c.names, c.raw, provider, 'credits', reasons)
                if c.by_ear:
                    v['by_ear'] = True
                    reasons.append(_reason(VERIFIED_TRANSLATOR, provider, 'credits', c.raw, 'by_ear'))
            if c.roles & cr.SYNC_FAMILY:
                v['sync'].append(c.raw)
                for name in c.names:
                    if cr.name_key(name) in allow.keys['syncers']:
                        v['known_syncers'].append(name)

        # free text: an upload comment, the release name as the API sent it
        text = meta.get('comments')
        if isinstance(text, str) and text:
            info = cr.scan_line(text, limit=len(text) + 1)
            if info is not None:
                _line_evidence(allow, info, provider, 'comments', reasons)
                if info.roles & cr.SYNC_FAMILY and 'translation' not in info.roles:
                    v['sync'].append(info.norm[:120])
        raw_release = meta.get('release_raw')
        if isinstance(raw_release, str) and cr.STUDIO_WORD in cr.tokens(raw_release):
            reasons.append(_reason(VERIFIED_STUDIO, provider, 'release', raw_release, cr.STUDIO_WORD))

        # the downloaded file's credit cues (P4)
        for info in cue_infos or ():
            _line_evidence(allow, info, 'file', 'cues', reasons)
            if info.by_ear:
                v['by_ear'] = True

        # the uploader: a trusted one (the user's own handles included); a team account counts as the team
        uploader = v['uploader']
        ukey = cr.name_key(uploader)
        if (uploader and ukey and ukey not in allow.never and ukey != cr.name_key(IMPORTER_NAME)
                and str(meta.get('uploader_rank') or '') != IMPORTER_RANK):
            if ukey in allow.keys['teams']:
                reasons.append(_reason(VERIFIED_TEAM, provider, 'uploader', uploader, uploader))
            elif ukey in allow.keys['uploaders'] or ukey in allow.custom:
                reasons.append(_reason(TRUSTED_UPLOADER, provider, 'uploader', uploader, uploader))

        v['reasons'] = _dedupe(reasons)
        v['badge'] = _pick(reasons)
        v['label'] = BADGE_LABELS.get(v['badge'], '')
    except Exception:
        return empty_verdict()
    return v
