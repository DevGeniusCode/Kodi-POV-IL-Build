# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The rows of the subtitle chooser window (script.moransubs.chooser.xml), as DATA: one candidate of the list
# -> the strings the window shows. No Kodi imports, so it runs under the stubs and in the contract tests.
#
# WHY A COLUMN PER FIELD. A row used to be one label that mixed Hebrew badges, a "85%" and a Latin release
# filename; Kodi's bidirectional text layout then reordered the pieces and the release name collided with the
# badges. Every string built here is ONE direction (Latin release / provider, or Hebrew badge / word) and the
# XML puts each in its own fixed column, so the order never depends on the bidi algorithm. The same
# properties feed the info panel on the right: it reads them from the FOCUSED row
# (Container(100).ListItem.Property(...)), so the panel needs no code on focus changes.
#
# Where the numbers come from: the row's own `pct` (what the list ranked by); the breakdown (why it matched)
# comes from scoring_matching.release.explain(playing release, subtitle release).

import os
import re

from resources.lib.core.link_codec import decode_link_safe
from resources.lib.scoring_matching import release as scoring_matching_release

# ---- language flags (images render on every skin) -----------------------------------------------------------------
# resources/media/flags, 32x32 PNGs (resources/lib/ui/chooser_rows.py -> three dirnames up is resources/).
FLAG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'media', 'flags')

_LANG2FLAG = {
    'he': 'he', 'iw': 'he', 'heb': 'he',
    'en': 'en', 'eng': 'en',
    'es': 'es', 'spa': 'es',
    'de': 'de', 'ger': 'de', 'deu': 'de',
    'fr': 'fr', 'fre': 'fr', 'fra': 'fr',
    'pt': 'pt', 'por': 'pt', 'pb': 'pb', 'pob': 'pb',
    'ru': 'ru', 'rus': 'ru',
    'ar': 'ar', 'ara': 'ar',
    'it': 'it', 'ita': 'it',
    'nl': 'nl', 'dut': 'nl', 'nld': 'nl',
    'pl': 'pl', 'pol': 'pl',
    'tr': 'tr', 'tur': 'tr',
    'zh': 'zh', 'chi': 'zh', 'zho': 'zh',
    'ja': 'ja', 'jpn': 'ja',
    'ko': 'ko', 'kor': 'ko',
    'hi': 'hi', 'hin': 'hi',
    'ro': 'ro', 'rum': 'ro', 'ron': 'ro',
    'sv': 'sv', 'swe': 'sv',
    'cs': 'cs', 'cze': 'cs', 'ces': 'cs',
    'da': 'da', 'dan': 'da',
    'fi': 'fi', 'fin': 'fi',
    'no': 'no', 'nor': 'no',
    'el': 'el', 'gre': 'el', 'ell': 'el',
    'hu': 'hu', 'hun': 'hu',
    'uk': 'uk', 'ukr': 'uk',
}
LANG_HE = {
    'he': 'עברית',
    'en': 'אנגלית', 'es': 'ספרדית', 'de': 'גרמנית', 'fr': 'צרפתית',
    'pt': 'פורטוגזית', 'pb': 'פורטוגזית', 'ru': 'רוסית', 'ar': 'ערבית',
    'it': 'איטלקית', 'nl': 'הולנדית', 'pl': 'פולנית', 'tr': 'טורקית',
    'zh': 'סינית', 'ja': 'יפנית', 'ko': 'קוריאנית', 'hi': 'הינדית',
    'ro': 'רומנית', 'sv': 'שוודית', 'cs': "צ'כית", 'da': 'דנית',
    'fi': 'פינית', 'no': 'נורווגית', 'el': 'יוונית', 'hu': 'הונגרית',
    'uk': 'אוקראינית',
}


def norm_lang(lang):
    code = (lang or '').strip().lower()
    return _LANG2FLAG.get(code) or _LANG2FLAG.get(code[:2]) or ''


def flag_path(lang):
    code = norm_lang(lang) or 'unknown'
    path = os.path.join(FLAG_DIR, code + '.png')
    if os.path.isfile(path):
        return path
    fallback = os.path.join(FLAG_DIR, 'unknown.png')
    return fallback if os.path.isfile(fallback) else ''


# ---- palette: one place, so every colour of the window agrees -----------------------------------------------------
GOLD = 'FFFFD700'      # a strong match (80 and up)
GREEN = 'FF7BE38C'     # a medium match (50 and up)
PALE = 'FFBFE9CF'      # ready Hebrew, weak match
AMBER = 'FFF0A93A'     # foreign language (will be translated)
MAGENTA = 'FFFF00FE'   # the subtitle applied now
WHITE = 'FFF2F5F8'
DIM = 'FF9AA0A6'
RED = 'FFE5645A'
CYAN = 'FF8FE0FF'
MATCH = 'FF35E06A'     # a word the subtitle's release shares with the playing release (resolution, source, codec ...)


def _c(color, text):
    return '[COLOR {0}]{1}[/COLOR]'.format(color, text)


# ---- the label of a row ---------------------------------------------------------------------------------------------
_PCT_RE = re.compile(r'(\d{1,3})%')
_TAG_RE = re.compile(r'^(?:\[[^\]]*\]\s*)+')

_PROVIDER = {
    'ktuvit': 'Ktuvit', 'wizdom': 'Wizdom', 'opensubtitles': 'OpenSubtitles',
    'yify': 'YIFY', 'subsource': 'SubSource', 'subscene': 'Subscene',
    'bsplayer': 'BSPlayer',
}


def _split_label(name):
    """(current, cached, pct, head, release) out of a candidate's display label
    '<kind> · 85%  —  <release>' (the label the list stages build)."""
    disp = (name or '').strip()
    current = disp.startswith('» נוכחית')
    if current and '· ' in disp:
        disp = disp.split('· ', 1)[1]
    cached = disp.startswith('[CACHE]')
    if cached:
        disp = disp[len('[CACHE]'):].strip()
    match = _PCT_RE.search(disp)
    pct = int(match.group(1)) if match else None
    head, release = disp, ''
    for sep in ('  —  ', ' — ', '—'):
        if sep in disp:
            head, release = disp.split(sep, 1)
            break
    return current, cached, pct, head.strip(), release.strip()


# ---- kind of a row (Hebrew), by link type ----------------------------------------------------------------------------
def _kind(payload, label_head, is_mt):
    """(source text, kind sentence, is_ai, is_embedded). Source text is the short
    line under the release name; the kind sentence goes to the info panel."""
    typ = payload.get('type') or ''
    lang = LANG_HE.get(norm_lang(payload.get('src_lang') or payload.get('source_lang')
                                 or payload.get('lang')), '')
    if typ == 'passthrough':
        return 'קובץ לצד הווידאו', 'קובץ כתוביות שנמצא לצד הווידאו', False, False
    if typ == 'pool':
        kind = payload.get('pool_kind') or 'ai'
        if kind == 'ktuvit':
            return 'מאגר קהילתי', 'כתובית אנושית (מאגר קהילתי)', False, False
        if kind == 'ai_emb':
            return 'מאגר קהילתי', 'תרגום AI ממסלול מובנה' + (
                ' ב{0}'.format(lang) if lang else '') + ' (מאגר קהילתי)', True, True
        return 'מאגר קהילתי', 'תרגום AI (מאגר קהילתי)', True, False
    if typ == 'engine':
        if payload.get('embedded'):
            return 'מסלול מובנה', 'מסלול כתוביות בתוך קובץ הווידאו', False, True
        provider = _PROVIDER.get(str(payload.get('source') or '').lower(),
                                 str(payload.get('source') or '').strip() or '?')
        return provider, ('תרגום מכונה (ספק חיצוני)' if is_mt
                          else 'כתובית אנושית (ספק חיצוני)'), False, False
    if typ == 'engine_ai':
        provider = _PROVIDER.get(str(payload.get('source') or '').lower(),
                                 str(payload.get('source') or '').strip() or '?')
        return provider, 'תרגום AI לעברית מכתובית' + (
            ' ב{0}'.format(lang) if lang else ''), True, False
    if typ == 'embedded_ai':
        return 'מסלול מובנה', 'תרגום AI לעברית ממסלול מובנה' + (
            ' ({0})'.format(lang) if lang else ''), True, True
    if typ == 'embedded_sync':
        return 'מסלול מובנה', 'עברית מסונכרנת למסלול המובנה', False, True
    if typ == 'ai':
        return 'קובץ מקומי', 'תרגום AI לעברית מקובץ כתוביות מקומי', True, False
    return label_head, '', False, False


_TIER_HE = {
    scoring_matching_release.TIER_EXACT: 'שם זהה',
    scoring_matching_release.TIER_GROUP: 'קבוצה זהה',
    scoring_matching_release.TIER_SOURCE: 'מקור דומה',
    scoring_matching_release.TIER_FUZZY: 'דמיון חלקי',
    scoring_matching_release.TIER_CROSS: 'לא תואם',
}


def _tier_word(pct, explained):
    if pct is None:
        return ''
    if pct > 100:
        return 'מסונכרנת למובנה'
    if explained and explained.get('pct') == pct:
        return _TIER_HE.get(explained.get('tier'), '')
    # the row's number came from another reading of the playing name: say it by the number
    if pct >= 90:
        return 'קבוצה זהה'
    if pct >= 56:
        return 'מקור דומה'
    return 'דמיון חלקי' if pct > 40 else 'לא תואם'


def _pct_color(pct, current, is_he, foreign):
    if current:
        return MAGENTA
    if foreign:
        return AMBER
    if not is_he:
        return WHITE
    if pct is not None and pct >= 80:
        return GOLD
    if pct is not None and pct >= 50:
        return GREEN
    return PALE


# ---- the breakdown ------------------------------------------------------------------------------------------------------
_REASON_HE = (
    ('identical release', 'שם השחרור זהה לקובץ שלך'),
    ('release name contained', 'שם השחרור כלול בשם הכתובית'),
    ('same group + source', 'אותה קבוצת שחרור ואותו סוג מקור'),
    ('same group (source untagged)', 'אותה קבוצת שחרור (סוג המקור לא צוין)'),
    ('same source class', 'אותו סוג מקור, קבוצת שחרור לא זהה'),
    ('same streaming service', 'אותו שירות סטרימינג'),
    ('different streaming service', 'שירות סטרימינג שונה'),
    ('different group', 'קבוצת שחרור שונה'),
    ('source mismatch', 'סוג מקור שונה (תזמון עלול לסטות)'),
    ('different edition/cut', 'מהדורה / גרסת חיתוך שונה'),
    ('possibly different cut/edition', 'ייתכן שמדובר בגרסת חיתוך אחרת'),
    ('PROPER/REPACK mismatch', 'שחרור PROPER / REPACK שונה'),
    ('different episode', 'פרק אחר'),
    ('frame rate mismatch', 'קצב פריימים שונה'),
)


def _why(explained, pct):
    """One Hebrew line on why the subtitle got its number. Said only when the
    explanation and the row's number are the same reading of the playing name
    (the engine scores the file path and the tagline too, and keeps the best)."""
    if not explained:
        return ''
    if explained.get('pct') != pct:
        return ''
    reasons = explained.get('reasons')
    if not reasons:
        return 'דמיון בין השמות בלבד (חסר סוג מקור או קבוצה)'
    out = []
    for r in reasons or []:
        for key, he in _REASON_HE:
            if r.startswith(key):
                out.append(he)
                break
    return ' · '.join(out[:3])


_FACT_LABELS = ('episode', 'edition', 'fps', 'source', 'group', 'service')
_SOURCE_PRETTY = {'web': 'WEB', 'bluray': 'BluRay', 'hdtv': 'HDTV', 'dvd': 'DVD',
                  'hdrip': 'HDRip', 'cam': 'CAM'}


def _pretty(key, value):
    if not value:
        return ''
    if key == 'source':
        return _SOURCE_PRETTY.get(value, value.upper())
    if key in ('group', 'service'):
        return value.upper()
    if key == 'edition':
        return value.capitalize()
    if key == 'fps':
        return value + ' fps'
    return value


def _fact(key, a, b, state):
    """(value text, status word, status colour) of one fact row. The value is
    ONE direction (Latin): the shared value, or 'yours vs subtitle's'."""
    pa, pb = _pretty(key, a), _pretty(key, b)
    if state == 'match':
        return pa, 'תואם', GREEN
    if state == 'mismatch':
        return '{0} vs {1}'.format(pa or '—', pb or '—'), 'שונה', RED
    if pa or pb:
        return '{0} vs {1}'.format(pa or '—', pb or '—'), 'לא ידוע', DIM
    return '—', 'לא צוין', DIM


# ---- the words a release shares with the playing release -------------------------------------------------------------------
# Resolution, source, video codec, audio, HDR and release group, found by pattern in both names; a subtitle's word is
# painted green when the playing release carries the same (category, value). `want` words (the manual query) match any
# alphanumeric run equal to them. Pure string work; the highlight never changes a number.
_SEP = r'[ ._-]?'
_TOKEN_PATTERNS = (
    ('res', re.compile(r'(?<![a-z0-9])(?:4320p|2160p|1440p|1080[pi]|720p|576p|480p|4k|uhd)(?![a-z0-9])', re.I)),
    ('src', re.compile(r'(?<![a-z0-9])(?:web' + _SEP + r'dl|web' + _SEP + r'rip|webdl|webrip|web|blu' + _SEP + r'ray|bdrip|brrip|'
                       r'remux|hdtv|pdtv|dvd' + _SEP + r'rip|dvdr|hdrip)(?![a-z0-9])', re.I)),
    ('hdr', re.compile(r'(?<![a-z0-9])(?:hdr10\+|hdr10plus|hdr10|hdr|dolby' + _SEP + r'vision|dovi|dv|hlg|sdr)(?![a-z0-9+])', re.I)),
    ('codec', re.compile(r'(?<![a-z0-9])(?:x265|h' + _SEP + r'265|hevc|x264|h' + _SEP + r'264|avc|av1|xvid|vp9)(?![a-z0-9])', re.I)),
    ('audio', re.compile(r'(?<![a-z0-9])(?:truehd|atmos|dts' + _SEP + r'hd(?:' + _SEP + r'ma)?|dts' + _SEP + r'x|dts|ddp|dd\+|dd|eac3|ac3|aac|flac|opus)'
                         r'(?:' + _SEP + r'\d(?:[ .]\d)?)?(?![a-z0-9])', re.I)),
)
_GROUP_TAIL = re.compile(r'(?:-|\[)([A-Za-z0-9]{2,15})\]?\s*$')
_EXT_TAIL = re.compile(r'\.(?:srt|sub|ass|ssa|vtt|mkv|mp4|avi|m4v|ts)$', re.I)


def _token_canon(cat, text):
    key = re.sub(r'[^a-z0-9+]', '', text.lower())
    if cat == 'res':
        return '2160p' if key in ('4k', 'uhd') else ('1080p' if key == '1080i' else key)
    if cat == 'src':
        if key.startswith('web'):
            return 'web'
        if key in ('bluray', 'bdrip', 'brrip'):
            return 'bluray'
        if key in ('dvdrip', 'dvdr'):
            return 'dvd'
        return key
    if cat == 'hdr':
        return 'dv' if key in ('dv', 'dovi', 'dolbyvision') else ('hdr10+' if key in ('hdr10+', 'hdr10plus') else key)
    if cat == 'codec':
        return {'x265': 'hevc', 'h265': 'hevc', 'x264': 'avc', 'h264': 'avc'}.get(key, key)
    if cat == 'audio':
        return key.replace('dd+', 'ddp')
    return key


def release_spans(name):
    """[(start, end, category, canonical value)] of the tech words and the release group of `name`, non-overlapping."""
    name = name or ''
    spans = []
    taken = []

    def free(a, b):
        return all(b <= x or a >= y for x, y in taken)

    for cat, rx in _TOKEN_PATTERNS:
        for m in rx.finditer(name):
            if free(m.start(), m.end()):
                taken.append((m.start(), m.end()))
                spans.append((m.start(), m.end(), cat, _token_canon(cat, m.group(0))))
    body = name
    m = _EXT_TAIL.search(body)
    if m:
        body = body[:m.start()]
    g = _GROUP_TAIL.search(body)
    if g and free(g.start(1), g.end(1)):
        spans.append((g.start(1), g.end(1), 'group', g.group(1).lower()))
    return sorted(spans)


def shared_mask(release, playing, want=None):
    """(mask, words): `mask` has one bool per character of `release`, True inside a word that the playing release shares
    (same category and value) or that is one of the `want` words; `words` lists those words as written in `release`."""
    release = release or ''
    mask = [False] * len(release)
    words = []
    playing_set = {(c, v) for _a, _b, c, v in release_spans(playing)} if playing else set()
    for a, b, cat, val in release_spans(release):
        if (cat, val) in playing_set:
            for i in range(a, b):
                mask[i] = True
            words.append(release[a:b])
    want = {w for w in (want or ()) if w}
    if want:
        for m in re.finditer(r'[A-Za-z0-9]+', release):
            if re.sub(r'[^a-z0-9]', '', m.group(0).lower()) in want:
                if not all(mask[m.start():m.end()]):
                    words.append(m.group(0))
                for i in range(m.start(), m.end()):
                    mask[i] = True
    return mask, words


def paint(text, mask, base=None, start=0):
    """`text` (a slice of the release that starts at `start`) as BBCode: shared words in MATCH green, the rest in `base`
    (plain when None). Segments are closed and reopened, never nested."""
    out, i, n = [], 0, len(text)

    def flag_at(k):
        return bool(mask[start + k]) if start + k < len(mask) else False

    while i < n:
        flag = flag_at(i)
        j = i
        while j < n and flag_at(j) == flag:
            j += 1
        piece = text[i:j]
        color = MATCH if flag else base
        out.append(_c(color, piece) if color else piece)
        i = j
    return ''.join(out)


# ---- wrapping of a long release name (Kodi's textbox wraps at spaces only) -----------------------------------------------
def wrap_lines(release, width=28, max_lines=5):
    """The release name broken at its own separators into lines of at most `width` characters (see wrap_release)."""
    release = (release or '').strip()
    if not release:
        return []
    lines, cur = [], ''
    for part in re.findall(r'[^.\-_ ]+[.\-_ ]?|[.\-_ ]', release):
        if cur and len(cur) + len(part) > width:
            lines.append(cur)
            cur = ''
        cur += part
        while len(cur) > width:
            lines.append(cur[:width])
            cur = cur[width:]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:max(1, width - 3)] + '...'
    return lines


def wrap_release(release, width=28, max_lines=5, mask=None):
    """The release name broken at its own separators into lines of at most
    `width` characters, joined with Kodi's [CR]. Never cuts the text short
    unless it needs more than `max_lines` lines. With a `mask` (shared_mask of the
    same, already stripped, release) the shared words are painted green."""
    lines = wrap_lines(release, width, max_lines)
    if not mask:
        return '[CR]'.join(lines)
    out, pos = [], 0
    total = len((release or '').strip())
    for line in lines:
        cut = line.endswith('...') and pos + len(line) - 3 < total
        body = line[:-3] if cut else line
        out.append(paint(body, mask, None, pos) + ('...' if cut else ''))
        pos += len(body)
    return '[CR]'.join(out)


def _access(c, rel):
    """Hebrew accessibility line: SDH (speaker labels) / HI (sound cues) / plain."""
    try:
        from resources.lib.scoring_matching.ranking import is_sdh_ext
        if is_sdh_ext(c, rel or ''):
            return 'SDH', 'SDH — כולל תוויות דוברים'
    except Exception:
        pass
    if c.get('is_hi'):
        return 'HI', 'HI — כולל תיאורי צלילים'
    return '', 'רגילה'


def _timing(c, typ, embedded):
    """(value, status word, colour) of the timing row: the proof SubSync stored
    for this row, or the fact that an embedded track IS the video's own timing."""
    label = (c.get('_subsync_label') or '').strip()
    state = (c.get('_subsync_state') or '').strip()
    if label and state in ('confirmed', 'fixable', 'fixed'):
        return label, 'מאומת', GREEN
    if typ in ('embedded_sync',) or (typ == 'engine' and embedded):
        return 'זהה לתזמון הווידאו', 'מאומת', GREEN
    if label:
        return label, 'לא ידוע', DIM
    return '—', 'לא נבדק', DIM


# ---- the row ---------------------------------------------------------------------------------------------------------------
def no_ai_reason(typ, is_ai, is_he, embedded, ai):
    """'' when the row can be (or needs no) AI translation, else the Hebrew reason it cannot. `ai` is the chooser's
    context (ui/chooser._ai_context): {'ok': a translator is usable, 'why': what is missing, 'emb_ok': an embedded track
    may be translated, 'emb_why', 'embedded_miss': this file's embedded track was tried and could not be read}. Without
    a context nothing is claimed. A Hebrew row needs no translation, so it never carries the badge."""
    if not ai:
        return ''
    if typ == 'pool':
        return ''   # an already translated copy of the community pool: delivering it calls no translator
    if typ == 'embedded_ai':
        if ai.get('embedded_miss'):
            return ai.get('embedded_miss_why') or 'הכתובית המובנית בקובץ אינה טקסט'
        if not ai.get('ok'):
            return ai.get('why') or ''
        return ''
    if is_ai:
        return '' if ai.get('ok') else (ai.get('why') or '')
    if is_he:
        return ''
    # a raw foreign row (a provider's, a local file's or an embedded track): it is offered as it is because no AI row
    # stands for it
    if not ai.get('ok'):
        return ai.get('why') or ''
    if embedded:
        if not ai.get('emb_ok'):
            return ai.get('emb_why') or 'תרגום מסלולים מובנים כבוי'
        if ai.get('embedded_miss'):
            return ai.get('embedded_miss_why') or 'הכתובית המובנית בקובץ אינה טקסט'
    return ''


def build(c, playing, want=None, ai=None):
    """One candidate -> {'label', 'flag', 'props'}: the ListItem label, the flag
    image and every property the window XML binds (strings only, colours baked
    as BBCode like the rest of the window). `playing` is the playing release
    name ('' when unknown); `want` the release words of a manual query; `ai` the
    AI-availability context (no_ai_reason). Never raises."""
    try:
        return _build(c, playing, want, ai)
    except Exception:
        name = (c.get('filename') or '').strip() if isinstance(c, dict) else ''
        return {'label': name, 'flag': '',
                'props': {'release': _c(WHITE, name), 'has_panel': ''}}


def _build(c, playing, want=None, ai=None):
    name = (c.get('filename') or '').strip()
    lang = (c.get('language') or '').strip()
    payload = decode_link_safe(c.get('link') or '') or {}
    if not isinstance(payload, dict):
        payload = {}
    current, cached, label_pct, head, label_rel = _split_label(name)
    typ = payload.get('type') or ''
    is_mt = '[תרגום מכונה]' in head
    source_text, kind_text, is_ai, embedded = _kind(payload, _TAG_RE.sub('', head), is_mt)

    # The release name: the link's own (never shortened), else the label's tail.
    release = (payload.get('filename') or payload.get('release') or '').strip() \
        if typ != 'passthrough' else (payload.get('release') or '').strip()
    if not release or release == lang:
        release = label_rel
    pct = c.get('pct') if isinstance(c.get('pct'), int) else label_pct
    if not pct:
        pct = None   # 0 means the playing name could not be read, not a real zero

    code = norm_lang(lang)
    is_he = code == 'he'
    foreign = bool(lang) and not is_he
    # AI rows deliver Hebrew whatever their source language: foreign colouring
    # is for the SOURCE language shown on those rows.
    color = _pct_color(pct, current, is_he, foreign)
    lang_he = LANG_HE.get(code or lang[:2].lower(), '')

    explained = None
    if playing and release:
        explained = scoring_matching_release.explain(playing, release)
    tier = _tier_word(pct, explained) if pct is not None else ''

    # ---- chips (at most 3, in priority order; each is ONE direction) ----
    chips = []
    if current:
        chips.append(_c(MAGENTA, 'נוכחית'))
    sync_label = (c.get('_subsync_label') or '').strip()
    if sync_label:
        chips.append(_c(GREEN, sync_label))
    elif typ == 'embedded_sync' or (typ == 'engine' and embedded and is_he):
        chips.append(_c(GREEN, 'מסונכרנת למובנה' if typ == 'embedded_sync' else 'מובנה'))
    # AI translation is the normal case and carries no badge; only a row that CANNOT be AI-translated says so
    no_ai = no_ai_reason(typ, is_ai, is_he, embedded, ai)
    if no_ai:
        chips.append(_c(RED, 'ללא תרגום AI'))
    if is_mt:
        chips.append(_c(AMBER, 'תרגום מכונה'))
    elif embedded and typ == 'engine' and not is_he:
        chips.append(_c(DIM, 'מובנה'))
    if foreign and lang_he:
        chips.append(_c(AMBER, 'מקור: ' + lang_he))
    access_short, access_text = _access(c, release)
    if access_short:
        chips.append(_c(WHITE, access_short))
    if cached:
        chips.append(_c(GREEN, 'מהקאש'))
    chips = chips[:3] + [''] * (3 - len(chips[:3]))

    # A row with no release name keeps the label its stage wrote ("תרגום מובנה → עברית (AI) · אנגלית").
    head_clean = _TAG_RE.sub('', _PCT_RE.sub('', head)).strip(' ·')
    primary = release or (head_clean if len(head_clean) > 3 else '') or kind_text or name
    if typ in ('ai', 'embedded_ai') and kind_text:
        primary = kind_text   # those stages label the row in English ("AI Hebrew (translate ...)")
    mask, shared = shared_mask(release, playing, want) if (release and primary == release and (playing or want)) else ([], [])
    shown = '[B]{0}[/B]'.format(paint(primary, mask, WHITE)) if shared else '[B]{0}[/B]'.format(_c(WHITE, primary))
    props = {
        'release': shown,
        'src': _c(CYAN, source_text) if source_text else '',
        'pct': '[B]{0}[/B]'.format(_c(color, '{0}%'.format(pct))) if pct is not None else '',
        'tier': _c(color, tier) if tier else '',
        'b1': chips[0], 'b2': chips[1], 'b3': chips[2],
        'cur': '1' if current else '',
        'has_panel': '1',
        # ---- info panel ----
        'p_release': wrap_release(release, mask=(mask if shared else None)) if release else _c(DIM, 'ללא שם שחרור'),
        'p_why': _c(WHITE, _why(explained, pct) if explained else (
            'מסלול מובנה בקובץ הווידאו: התזמון זהה לווידאו' if embedded else
            'אין שם שחרור להשוואה')),
        'p_kind': kind_text + (' · לא ניתנת לתרגום AI: ' + no_ai if no_ai else ''),
        'p_provider': source_text if source_text else '—',
        'p_lang': lang_he or (lang.upper() if lang else '—'),
        'p_access': access_text,
    }
    facts = {f[0]: f for f in (explained['facts'] if explained else [])}
    for key in _FACT_LABELS:
        f = facts.get(key)
        value, word, col = _fact(key, f[1], f[2], f[3]) if f else ('—', 'לא צוין', DIM)
        props['f_' + key] = value
        props['fs_' + key] = _c(col, word)
    value, word, col = _timing(c, typ, embedded)
    props['f_timing'] = value
    props['fs_timing'] = _c(col, word)

    return {'label': primary, 'flag': flag_path('he' if is_he else lang), 'props': props}


def placeholder(text):
    """A row that only says something ('searching...'): no panel."""
    return {'label': text, 'flag': '',
            'props': {'release': _c(DIM, text), 'has_panel': ''}}
