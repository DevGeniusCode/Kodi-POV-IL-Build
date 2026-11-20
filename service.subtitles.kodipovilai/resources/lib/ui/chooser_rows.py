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
from resources.lib.scoring_matching import provenance as scoring_matching_provenance
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
PROV_BLUE = CYAN       # a provenance badge (chip and panel value): the blue of the provider names, never green / red
MATCH = 'FF35E06A'     # a word the subtitle's release shares with the playing release (resolution, source, codec ...)


def _c(color, text):
    return '[COLOR {0}]{1}[/COLOR]'.format(color, text)


# Kodi formatting tags that a stage label or a provider's file name may carry by accident ("[B]..." without its
# "[/B]"): shown as text they are noise, and an unpaired one would colour or embolden the rest of the window.
_BBCODE_RE = re.compile(r'\[/?(?:B|I|UPPERCASE|LOWERCASE|CAPITALIZE|LIGHT|CR)\]|\[/?COLOR(?:\s[^\]]*)?\]', re.I)


def strip_bbcode(text):
    """`text` without Kodi's formatting tags ([B], [/B], [I], [COLOR x], [/COLOR], [CR] ...)."""
    return _BBCODE_RE.sub('', text or '').strip()


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
def _payload_lang(payload):
    """The Hebrew name of the language a link's subtitle is written in ('' when unknown)."""
    return LANG_HE.get(norm_lang(payload.get('src_lang') or payload.get('source_lang')
                                 or payload.get('lang')), '')


def _kind(payload, label_head, is_mt):
    """(source text, kind sentence, is_ai, is_embedded). Source text is the short
    line under the release name; the kind sentence goes to the info panel (the field "סוג"): what the
    subtitle IS (its source and language), never what picking it will do."""
    typ = payload.get('type') or ''
    lang = _payload_lang(payload)
    if typ == 'passthrough':
        return 'קובץ לצד הווידאו', 'קובץ כתוביות שנמצא לצד הווידאו', False, False
    if typ == 'pool':
        kind = payload.get('pool_kind') or 'ai'
        if kind == 'ktuvit':
            return 'מאגר קהילתי', 'כתובית אנושית (מאגר קהילתי)', False, False
        if kind == 'ai_emb':
            return 'מאגר קהילתי', 'תרגום AI מכתובית מובנית' + (
                ' ב{0}'.format(lang) if lang else '') + ' (מאגר קהילתי)', True, True
        return 'מאגר קהילתי', 'תרגום AI (מאגר קהילתי)', True, False
    if typ == 'engine':
        if payload.get('embedded'):
            return 'כתובית מובנית', 'כתובית מובנית' + (' ב{0}'.format(lang) if lang else ''), False, True
        provider = _PROVIDER.get(str(payload.get('source') or '').lower(),
                                 str(payload.get('source') or '').strip() or '?')
        return provider, ('תרגום מכונה (ספק חיצוני)' if is_mt
                          else 'כתובית אנושית (ספק חיצוני)'), False, False
    if typ == 'engine_ai':
        provider = _PROVIDER.get(str(payload.get('source') or '').lower(),
                                 str(payload.get('source') or '').strip() or '?')
        return provider, 'כתובית ב{0}'.format(lang) if lang else 'כתובית בשפה זרה', True, False
    if typ == 'embedded_ai':
        return 'כתובית מובנית', 'כתובית מובנית' + (' ב{0}'.format(lang) if lang else ''), True, True
    if typ == 'embedded_sync':
        return 'כתובית מובנית', 'עברית מסונכרנת לכתובית המובנית', False, True
    if typ == 'ai':
        return 'קובץ מקומי', 'כתובית מקומית' + (' ב{0}'.format(lang) if lang else ''), True, False
    return label_head, '', False, False


def _row_title(typ, payload, embedded):
    """The title of a row that has no release name of its own, '' when the row keeps the label its stage wrote. An
    embedded track and an AI row made of one are named by what they are, once: the source line and the badges do
    not repeat it."""
    lang = _payload_lang(payload)
    paren = ' ({0})'.format(lang) if lang else ''
    if typ == 'engine' and embedded:
        return 'כתובית מובנית בקובץ' + paren
    if typ == 'embedded_ai':
        return 'תרגום לעברית מכתובית מובנית' + paren
    if typ == 'ai':
        return 'תרגום AI לעברית מקובץ כתוביות מקומי'   # the stage labels it in English
    return ''


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
# A name is cut into WHOLE tokens: the technical tags (resolution, source, video codec, audio, HDR; a compound tag such as
# 'WEB-DL' or 'DD+5.1' is ONE token), the release group, and every other alphanumeric word (title words, the year ...).
# A subtitle's token is painted green when the playing release carries the same whole token (a tag: same category and
# value, so 'WEB-DL' is never green against 'WEBRip' and 'WEB' is never painted inside 'WEB-DL'; a word: the same word).
# `want` words (the manual query) match a whole token too (every part of a compound one). Pure string work; the
# highlight never changes a number.
#
# RED (conflict) tokens: a WHOLE token of the subtitle's release that says the subtitle was made for ANOTHER timeline than
# the playing file (conflict_mask): a pre-retail capture against a retail file (CAM / TS / SCREENER ... vs WEB-DL / WEBRip /
# BluRay / DVDRip, both ways), bonus content the playing file is not (Trailer, Making.Of, Sample ...), another cut or edition
# (Extended, Director's.Cut, Unrated ...; an untagged file counts as the theatrical cut), a broadcast capture against a WEB /
# BluRay file (HDTV / PDTV / SDTV / PAL / NTSC, both ways; PAL against NTSC), another frame rate (25fps vs 23.976fps; PAL is
# 25), another streaming service (NF vs AMZN ...) and another episode (S01E02 vs S01E01; S01E01E02 vs S01E01). A token is red
# only when the playing release says something ELSE on the same point; a point the playing release does not mention is
# never a conflict (bonus content and a special cut excepted: their absence is the statement). WEB against BluRay is NOT
# red (the match % already says "source mismatch"). Display only, like the green: no number moves.
_SEP = r'[ ._-]?'
_WORD_RE = re.compile(r'[A-Za-z0-9]+')
_TOKEN_PATTERNS = (
    ('res', re.compile(r'(?<![a-z0-9])(?:4320p|2160p|1440p|1080[pi]|720p|576p|480p|4k|uhd)(?![a-z0-9])', re.I)),
    ('src', re.compile(r'(?<![a-z0-9])(?:hdcam|camrip|cam|hdts|telesync|ts|telecine|tc|dvd' + _SEP + r'scr(?:eener)?|screener|scr|r5|'
                       r'workprint|web' + _SEP + r'dl|web' + _SEP + r'rip|webdl|webrip|web|blu' + _SEP + r'ray|bdrip|brrip|'
                       r'remux|hdtv|pdtv|sdtv|dvd' + _SEP + r'rip|dvdr|hdrip)(?![a-z0-9])', re.I)),
    ('hdr', re.compile(r'(?<![a-z0-9])(?:hdr10\+|hdr10plus|hdr10|hdr|dolby' + _SEP + r'vision|dovi|dv|hlg|sdr)(?![a-z0-9+])', re.I)),
    ('codec', re.compile(r'(?<![a-z0-9])(?:x265|h' + _SEP + r'265|hevc|x264|h' + _SEP + r'264|avc|av1|xvid|vp9)(?![a-z0-9])', re.I)),
    ('audio', re.compile(r'(?<![a-z0-9])(?:truehd|atmos|dts' + _SEP + r'hd(?:' + _SEP + r'ma)?|dts' + _SEP + r'x|dts|ddp|dd\+|dd|eac3|ac3|aac|flac|opus)'
                         r'(?:' + _SEP + r'\d(?:[ .]\d)?)?(?![a-z0-9])', re.I)),
    # SxxEyy, a multi-episode file (S01E01E02, S01E01-E02, S01E01-02) is ONE token
    ('episode', re.compile(r'(?<![a-z0-9])s\d{1,2}e\d{1,3}(?:-?e\d{1,3})*(?:-\d{1,3})?(?![a-z0-9])', re.I)),
    ('fps', re.compile(r'(?<![0-9])(?<![0-9][.,])(?:23[.,]976|23[.,]98|24|25|29[.,]97|30|50|59[.,]94|60)' + _SEP + r'fps(?![a-z0-9])', re.I)),
    ('edition', re.compile(r'(?<![a-z0-9])(?:extended(?:' + _SEP + r'(?:cut|edition|version))?|director(?:[\'’]?s)(?:' + _SEP
                           + r'(?:cut|edition))?|director' + _SEP + r'cut|dircut|dc|unrated|uncut|theatrical(?:' + _SEP
                           + r'(?:cut|edition|version))?|imax(?:' + _SEP + r'edition)?|remastered|special' + _SEP + r'edition|'
                           r'criterion(?:' + _SEP + r'(?:collection|edition))?)(?![a-z0-9])', re.I)),
    ('bonus', re.compile(r'(?<![a-z0-9])(?:trailers?|teasers?|promos?|making' + _SEP + r'of|behind' + _SEP + r'the' + _SEP + r'scenes|'
                         r'featurettes?|extras|bonus|deleted' + _SEP + r'scenes|recaps?|sample)(?![a-z0-9])', re.I)),
    ('tvstd', re.compile(r'(?<![a-z0-9])(?:pal|ntsc)(?![a-z0-9])', re.I)),
)
_GROUP_TAIL = re.compile(r'(?:-|\[)([A-Za-z0-9]{2,15})\]?\s*$')
_EXT_TAIL = re.compile(r'\.(?:srt|sub|ass|ssa|vtt|mkv|mp4|avi|m4v|ts)$', re.I)
_YEAR_RE = re.compile(r'(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])')
# Short words that are also title words ("Cam.2018", "DC.League.of.Super-Pets", "TS"): a tag only AFTER a technical anchor
# (the year, SxxEyy, a resolution, a source, codec, audio or HDR tag, a frame rate). An edition or bonus word is never the
# first word of a name ("Extras.S01E01" is the show "Extras"); a title word on both sides is the same word anyway.
_AMBIGUOUS = frozenset({'cam', 'ts', 'tc', 'scr', 'r5', 'dc', 'pal', 'ntsc'})
_NOT_FIRST = frozenset({'edition', 'bonus'})
_ANCHOR_CATS = frozenset({'res', 'src', 'hdr', 'codec', 'audio', 'episode', 'fps'})


def _token_canon(cat, text):
    key = re.sub(r'[^a-z0-9+]', '', text.lower())
    if cat == 'res':
        return '2160p' if key in ('4k', 'uhd') else ('1080p' if key == '1080i' else key)
    if cat == 'src':
        if key in ('bluray', 'bdrip', 'brrip'):
            return 'bluray'
        if key in ('dvdrip', 'dvdr'):
            return 'dvd'
        return 'dvdscr' if key == 'dvdscreener' else key
    if cat == 'hdr':
        return 'dv' if key in ('dv', 'dovi', 'dolbyvision') else ('hdr10+' if key in ('hdr10+', 'hdr10plus') else key)
    if cat == 'codec':
        return {'x265': 'hevc', 'h265': 'hevc', 'x264': 'avc', 'h264': 'avc'}.get(key, key)
    if cat == 'audio':
        return key.replace('dd+', 'ddp')
    if cat == 'episode':
        nums = [int(n) for n in re.findall(r'\d+', text)]
        return 's{0}e{1}'.format(nums[0], '-'.join(str(e) for e in sorted(set(nums[1:]))))
    if cat == 'fps':
        value = key[:-3]
        return {'23976': '23.976', '2398': '23.976', '2997': '29.97', '5994': '59.94'}.get(value, value)
    if cat == 'edition':
        for head, canon in (('extended', 'extended'), ('director', 'directors'), ('dircut', 'directors'), ('dc', 'directors'),
                            ('theatrical', 'theatrical'), ('imax', 'imax'), ('special', 'special'), ('criterion', 'criterion')):
            if key.startswith(head):
                return canon
        return key
    if cat == 'bonus':
        for head, canon in (('trailer', 'trailer'), ('teaser', 'teaser'), ('promo', 'promo'), ('making', 'makingof'),
                            ('behind', 'behindthescenes'), ('featurette', 'featurette'), ('deleted', 'deletedscenes'),
                            ('recap', 'recap')):
            if key.startswith(head):
                return canon
        return key
    return key


def release_spans(name):
    """[(start, end, category, canonical value)] of the whole tokens of `name`, non-overlapping: the tech tags, the
    episode, frame rate, edition, bonus and TV-standard tags, the streaming service ('service'), the release group
    ('group') and every other word of two or more characters ('word'). The extension is not a token."""
    name = name or ''
    m = _EXT_TAIL.search(name)
    body = name[:m.start()] if m else name
    spans = []
    taken = []
    deferred = []

    def free(a, b):
        return all(b <= x or a >= y for x, y in taken)

    def take(a, b, cat, val):
        taken.append((a, b))
        spans.append((a, b, cat, val))

    for cat, rx in _TOKEN_PATTERNS:
        for m in rx.finditer(body):
            a, b = m.start(), m.end()
            key = re.sub(r'[^a-z0-9]', '', m.group(0).lower())
            if cat in _NOT_FIRST and a == 0:
                continue
            if key in _AMBIGUOUS:
                deferred.append((a, b, cat, m.group(0)))
            elif free(a, b):
                take(a, b, cat, _token_canon(cat, m.group(0)))
    anchors = [a for a, _b, c, _v in spans if c in _ANCHOR_CATS] + [y.start() for y in _YEAR_RE.finditer(body) if y.start() > 0]
    anchor = min(anchors) if anchors else None
    for a, b, cat, text in deferred:
        if anchor is not None and a > anchor and free(a, b):
            take(a, b, cat, _token_canon(cat, text))
    g = _GROUP_TAIL.search(body)
    if (g and free(g.start(1), g.end(1))
            and scoring_matching_release.is_group_token(g.group(1).lower())):
        take(g.start(1), g.end(1), 'group', g.group(1).lower())
    # a streaming service sits among the technical tags: after the first resolution / source tag, or right before a
    # source tag ("AMZN.WEB-DL"); the same rule as scoring_matching.release (so "Mad.Max" is a title)
    tech = [a for a, _b, c, _v in spans if c in ('res', 'src')]
    first_tech = min(tech) if tech else None
    src_starts = {a for a, _b, c, _v in spans if c == 'src'}
    for w in _WORD_RE.finditer(body):
        if len(w.group(0)) >= 2 and free(w.start(), w.end()):
            service = scoring_matching_release.service_token(w.group(0))
            if service and ((first_tech is not None and w.start() > first_tech)
                            or w.end() in src_starts or w.end() + 1 in src_starts):
                take(w.start(), w.end(), 'service', service)
            else:
                spans.append((w.start(), w.end(), 'word', w.group(0).lower()))
    return sorted(spans)


def _shared_spans(release, playing, want):
    p_tags, p_words = set(), set()
    for _a, _b, c, v in (release_spans(playing) if playing else ()):
        if c in ('word', 'group'):
            p_words.add(v)
        else:
            p_tags.add((c, v))
    want = {w for w in (want or ()) if w}
    out = []
    for a, b, cat, val in release_spans(release):
        hit = (val in p_words) if cat in ('word', 'group') else ((cat, val) in p_tags)
        if not hit and want:
            parts = [p.lower() for p in _WORD_RE.findall(release[a:b])]
            hit = bool(parts) and (''.join(parts) in want or all(p in want for p in parts))
        if hit:
            out.append((a, b))
    return out


def shared_mask(release, playing, want=None):
    """(mask, words): `mask` has one bool per character of `release`, True inside a WHOLE token that the playing release
    shares (a tag: same category and value; a title word, the year or the group: the same word) or that is made of
    `want` words; `words` lists those tokens as written in `release`."""
    release = release or ''
    mask = [False] * len(release)
    words = []
    for a, b in _shared_spans(release, playing, want):
        for i in range(a, b):
            mask[i] = True
        words.append(release[a:b])
    return mask, words


# the conflict facets of one token: (facet, class). Two tokens conflict when they carry the same facet with another class.
_PRE_RETAIL = frozenset({'cam', 'hdcam', 'camrip', 'ts', 'hdts', 'telesync', 'tc', 'telecine', 'scr', 'screener', 'dvdscr',
                         'r5', 'workprint'})
_RETAIL = frozenset({'webdl', 'webrip', 'web', 'bluray', 'remux', 'dvd'})
_BROADCAST = frozenset({'hdtv', 'pdtv', 'sdtv'})
_WEB_DISC = frozenset({'webdl', 'webrip', 'web', 'bluray', 'remux'})


def _facets_of(cat, val):
    out = []
    if cat == 'src':
        if val in _PRE_RETAIL:
            out.append(('tier', 'pre'))
        elif val in _RETAIL:
            out.append(('tier', 'retail'))
        if val in _BROADCAST:
            out.append(('broadcast', 'tv'))
        elif val in _WEB_DISC:
            out.append(('broadcast', 'web_disc'))
    elif cat == 'tvstd':
        out.extend((('broadcast', 'tv'), ('tvstd', val)))
        if val == 'pal':
            out.append(('fps', '25'))   # PAL is 25 fps (scoring_matching.release reads it the same way)
    elif cat in ('fps', 'service', 'episode', 'edition', 'bonus'):
        out.append((cat, val))
    return out


def _clashes(facet, cls, playing):
    """True when a subtitle token of (facet, cls) contradicts the playing release's facets {facet: {class}}."""
    have = playing.get(facet) or set()
    if cls in have:
        return False
    if facet == 'bonus':
        return True    # bonus content the playing file is not
    if facet == 'edition':
        return bool(have) or cls != 'theatrical'    # an untagged file is the theatrical cut
    return bool(have)


def _conflict_spans(release, playing):
    if not (release and playing):
        return []
    facets = {}
    for _a, _b, c, v in release_spans(playing):
        for facet, cls in _facets_of(c, v):
            facets.setdefault(facet, set()).add(cls)
    return [(a, b) for a, b, cat, val in release_spans(release)
            if any(_clashes(facet, cls, facets) for facet, cls in _facets_of(cat, val))]


def conflict_mask(release, playing):
    """(mask, words): `mask` has one bool per character of `release`, True inside a WHOLE token that contradicts the playing
    release (another source tier, bonus content, another cut / edition, a broadcast capture, another frame rate,
    streaming service or episode: the header of this section); `words` lists those tokens as written in `release`."""
    release = release or ''
    mask = [False] * len(release)
    words = []
    for a, b in _conflict_spans(release, playing):
        for i in range(a, b):
            mask[i] = True
        words.append(release[a:b])
    return mask, words


def release_mask(release, playing, want=None):
    """(mask, shared, conflicts): one colour per character of `release` (RED inside a conflict token, MATCH inside a
    shared one, None elsewhere) and the two word lists. A conflict wins over a manual-query word: it is a fact about
    the playing file."""
    release = release or ''
    mask = [None] * len(release)
    shared, conflicts = [], []
    red = _conflict_spans(release, playing)
    for a, b in red:
        for i in range(a, b):
            mask[i] = RED
        conflicts.append(release[a:b])
    for a, b in _shared_spans(release, playing, want):
        if (a, b) not in red:
            for i in range(a, b):
                mask[i] = MATCH
            shared.append(release[a:b])
    return mask, shared, conflicts


def _mask_color(flag):
    return flag if isinstance(flag, str) else (MATCH if flag else None)


def paint(text, mask, base=None, start=0):
    """`text` (a slice of the release that starts at `start`) as BBCode. `mask` has one entry per character of the
    release: True = MATCH green (shared_mask), a colour string = that colour (release_mask: RED / MATCH), falsy = `base`
    (plain when None). Segments are closed and reopened, never nested."""
    out, i, n = [], 0, len(text)

    def color_at(k):
        return _mask_color(mask[start + k]) if start + k < len(mask) else None

    while i < n:
        color = color_at(i)
        j = i
        while j < n and color_at(j) == color:
            j += 1
        piece = text[i:j]
        color = color or base
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


# the info panel's release-name box holds 4 lines (script.moransubs.chooser.xml); a longer name ends in "..."
RELEASE_WIDTH = 32
RELEASE_LINES = 4


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
            return ai.get('emb_why') or 'תרגום כתוביות מובנות כבוי'
        if ai.get('embedded_miss'):
            return ai.get('embedded_miss_why') or 'הכתובית המובנית בקובץ אינה טקסט'
    return ''


# ---- provenance (Provenance P3): the badge chip and the metadata strip under the list ----------------------------------------
# Hebrew rows of a provider only; the strip shows the provider's own metadata even when there is no badge (a row with no
# evidence stays visually clean: no "unverified" word anywhere). Every string is ONE direction: the XML holds the Hebrew
# captions, a property holds one value as the provider wrote it, so a Latin handle never mixes with a Hebrew word.
PROV_KEYS = ('p_prov', 'p_prov_src', 'p_translator', 'p_sync', 'p_uploader', 'p_date', 'p_downloads', 'p_fixed', 'has_meta')
FIXED_TEXT = 'כתובית מתוקנת'
FILE_SOURCE_TEXT = 'קובץ הכתובית'     # the source column when the badge comes from the credits read inside the downloaded file
_MAX_SYNC_TEXT = 80


def _prov_props(c, typ, is_he, is_mt, source_text, prov):
    """(props, chip): every provenance property of one row ('' when it has none) and the badge chip text ('' without a
    badge). A row that is not a Hebrew provider subtitle, a machine translation, or has no metadata gets nothing."""
    props = {k: '' for k in PROV_KEYS}
    meta = c.get('_meta')
    meta = meta if isinstance(meta, dict) else {}
    file_lines = c.get('_file_credits')      # the credit lines read from this row's file when it was downloaded (P4)
    file_lines = file_lines if isinstance(file_lines, list) else []
    if not (typ == 'engine' and is_he and not is_mt and (meta or file_lines)):
        return props, ''
    v = scoring_matching_provenance.classify(meta, prov, hebrew=True, provider=source_text,
                                             cue_infos=scoring_matching_provenance.cue_infos(file_lines))
    names = []
    for s in v['sync_values']:
        s = strip_bbcode(s)
        if s and s not in names:
            names.append(s)
    badge = v['label']
    if badge:
        top = next((r for r in v['reasons'] if r['badge'] == v['badge']), None)
        props['p_prov'] = '[B]{0}[/B]'.format(_c(PROV_BLUE, badge))
        props['p_prov_src'] = FILE_SOURCE_TEXT if (top and top['source'] == 'file') else strip_bbcode(source_text)
    props['p_translator'] = strip_bbcode(v['translator'])
    props['p_sync'] = ', '.join(names)[:_MAX_SYNC_TEXT]
    props['p_uploader'] = strip_bbcode(v['uploader'])
    props['p_date'] = v['date']
    props['p_downloads'] = '{0:,}'.format(v['downloads']) if isinstance(v['downloads'], int) else ''
    props['p_fixed'] = FIXED_TEXT if v['fixed'] else ''
    props['has_meta'] = '1' if any(props[k] for k in PROV_KEYS[:-1]) else ''
    return props, (_c(PROV_BLUE, badge) if badge else '')


def build(c, playing, want=None, ai=None, prov=None):
    """One candidate -> {'label', 'flag', 'props'}: the ListItem label, the flag
    image and every property the window XML binds (strings only, colours baked
    as BBCode like the rest of the window). `playing` is the playing release
    name ('' when unknown); `want` the release words of a manual query; `ai` the
    AI-availability context (no_ai_reason); `prov` the provenance Allowlist
    (scoring_matching.provenance; None = only the studio forms count). Never raises."""
    try:
        return _build(c, playing, want, ai, prov)
    except Exception:
        name = (c.get('filename') or '').strip() if isinstance(c, dict) else ''
        return {'label': name, 'flag': '',
                'props': {'release': _c(WHITE, name), 'has_panel': ''}}


def _build(c, playing, want=None, ai=None, prov=None):
    name = strip_bbcode(c.get('filename'))
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
    release = strip_bbcode(release)
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

    prov_props, prov_chip = _prov_props(c, typ, is_he, is_mt, source_text, prov)

    # ---- chips (at most 3, in priority order; each is ONE direction) ----
    chips = []
    if current:
        chips.append(_c(MAGENTA, 'נוכחית'))
    if prov_chip:
        chips.append(prov_chip)
    sync_label = (c.get('_subsync_label') or '').strip()
    if sync_label:
        chips.append(_c(GREEN, sync_label))
    elif typ == 'embedded_sync':
        chips.append(_c(GREEN, 'מסונכרנת למובנה'))
    # AI translation is the normal case and carries no badge; only a row that CANNOT be AI-translated says so
    no_ai = no_ai_reason(typ, is_ai, is_he, embedded, ai)
    if no_ai:
        chips.append(_c(RED, 'ללא תרגום AI'))
    if is_mt:
        chips.append(_c(AMBER, 'תרגום מכונה'))
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
    title = _row_title(typ, payload, embedded)
    if title:
        primary = title   # those stages label the row in English ("AI Hebrew (translate ...)") or by a bare "[מובנה] EN"
    mask, shared, conflicts = (release_mask(release, playing, want) if (release and primary == release and (playing or want))
                               else ([], [], []))
    painted = bool(shared or conflicts)
    shown = '[B]{0}[/B]'.format(paint(primary, mask, WHITE)) if painted else '[B]{0}[/B]'.format(_c(WHITE, primary))
    props = {
        'release': shown,
        'src': _c(CYAN, source_text) if source_text else '',
        'pct': '[B]{0}[/B]'.format(_c(color, '{0}%'.format(pct))) if pct is not None else '',
        'tier': _c(color, tier) if tier else '',
        'b1': chips[0], 'b2': chips[1], 'b3': chips[2],
        'cur': '1' if current else '',
        'has_panel': '1',
        # ---- info panel ----
        'p_release': wrap_release(release, width=RELEASE_WIDTH, max_lines=RELEASE_LINES,
                                  mask=(mask if painted else None)) if release else _c(DIM, 'ללא שם שחרור'),
        'p_why': _c(WHITE, _why(explained, pct) if explained else (
            'כתובית מובנית בקובץ הווידאו: התזמון זהה לווידאו' if embedded else
            'אין שם שחרור להשוואה')),
        'p_kind': kind_text + (' · לא ניתנת לתרגום AI: ' + no_ai if no_ai else ''),
        'p_provider': source_text if source_text else '—',
        'p_lang': lang_he or (lang.upper() if lang else '—'),
        'p_access': access_text,
    }
    props.update(prov_props)
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
