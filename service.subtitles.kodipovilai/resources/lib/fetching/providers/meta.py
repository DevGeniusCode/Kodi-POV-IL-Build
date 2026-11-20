# Provider row metadata for the Hebrew provenance panel (Provenance P1, plumbing only).
#
# Pure parsers, stdlib only (no xbmc, no requests): each provider module calls one of them while it builds a
# row and puts the result in the row's optional 'meta' dict. Nothing here classifies, scores or hides
# anything; the classifier (P2) and the chooser panel (P3) read what is recorded. The facts recorded here
# come from the 20 Nov 2026 live probe (12 titles, three providers).
#
# A meta dict is JSON-safe and every key is optional (an empty value is dropped):
#   credits      one raw string, parts joined by ' | ', exactly the provider's wording
#                (Ktuvit: its red <small> lines; Wizdom: its 'credits' field), e.g.
#                'תרגום: אולפנים | הביא, תיקן וסנכרן: Yoav1610 | תואם לגרסאות WEB'. The classifier's grammar
#                (P2) splits it; this module never interprets it.
#   uploader     the uploader's name as the provider writes it ('yoav_1610', 'איש צוות', 'os-auto')
#   uploader_rank  OpenSubtitles only ('Application Developers' marks the bulk importer 'os-auto')
#   date         upload date, ISO 'YYYY-MM-DD' (each provider writes its own format, see iso_date)
#   downloads    int, the provider's download counter (Ktuvit, OpenSubtitles)
#   fixed        True when Ktuvit marks the row 'כתובית מתוקנת' (the wrench icon)
#   comments     OpenSubtitles' free-text comment of the upload (capped)
#   release_raw  OpenSubtitles' release name as the API sent it, kept ONLY when it carries 'אולפנים'
#                (the visible name still has the word removed, opensubtitles.py)
# OpenSubtitles' 'from_trusted' flag is deliberately NOT recorded (owner decision, 20 Nov 2026).

import datetime
import html
import re

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')

MAX_CREDITS = 400
MAX_NAME = 80
MAX_COMMENTS = 400
MAX_RELEASE = 200


def text(fragment):
    """HTML fragment -> plain text: tags dropped, entities decoded, white space collapsed."""
    return _WS_RE.sub(' ', html.unescape(_TAG_RE.sub('', fragment or ''))).strip()


def cap(value, limit):
    """A provider string as one trimmed line of at most `limit` characters ('' for a non-string)."""
    if not isinstance(value, str):
        return ''
    return _WS_RE.sub(' ', value).strip()[:limit]


_DMY_RE = re.compile(r'^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$')
_ISO_RE = re.compile(r'^\s*(\d{4})-(\d{2})-(\d{2})')


def iso_date(value, order):
    """A provider's date text -> 'YYYY-MM-DD', or '' when it is not a real date. `order` is the provider's own
    format and is never guessed (a day up to 12 reads both ways): 'dmy' (Ktuvit, 21/03/2026), 'mdy'
    (Wizdom, 3/22/2026) or 'iso' (OpenSubtitles, 2026-03-22T12:26:48Z)."""
    if not isinstance(value, str):
        return ''
    try:
        if order == 'iso':
            m = _ISO_RE.match(value)
            y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)
        else:
            m = _DMY_RE.match(value)
            if not m:
                return ''
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            d, mo = (a, b) if order == 'dmy' else (b, a)
        return datetime.date(y, mo, d).isoformat()
    except (ValueError, TypeError):
        return ''


def _count(value):
    try:
        n = int(str(value).strip())
    except (ValueError, TypeError):
        return None
    return n if n >= 0 else None


def clean(meta):
    """The dict without its empty values (None, '', [], False); a 0 counter is a real value and stays."""
    return {k: v for k, v in meta.items() if v is not None and v != '' and v != [] and v is not False}


# ---- Ktuvit ---------------------------------------------------------------------------------------------------------
# A row (the text between <tr> and </tr>) looks like:
#   <td><div style="float: right; ..."> [<i class='glyphicon glyphicon-wrench' title='כתובית מתוקנת'></i>] RELEASE<br />
#       <small class='text-danger'>תרגום: אולפנים</small><small class='text-secondary'> | </small>
#       <small class='text-danger'>סנכרון: x | note</small><small class='text-secondary'> | </small>
#       <small class='text-info'>הועלה ע"י: uploader</small> ...</div> ...</td>
#   <td>srt</td><td>27.89 KB</td><td>11/05/2025</td><td>1920</td><td>...download links...</td>
_SMALL_RE = re.compile(r"<small\b[^>]*\bclass=['\"]([^'\"]*)['\"][^>]*>(.*?)</small>", re.S)
_CELL_RE = re.compile(r'<td\b[^>]*>(.*?)</td>', re.S)
_FIXED_RE = re.compile(r"<i\b[^>]*(?:glyphicon-wrench|כתובית מתוקנת)[^>]*>")
_UPLOADED_RE = re.compile(r'^הועלה\s+ע["״”]?י\s*:\s*(.*)$')


def ktuvit_row(table_row):
    """The meta of one Ktuvit subtitle row. Never raises."""
    try:
        credits = []
        uploader = ''
        for css, inner in _SMALL_RE.findall(table_row):
            t = text(inner)
            if not t or 'text-secondary' in css:
                continue
            m = _UPLOADED_RE.match(t)
            if m:
                uploader = cap(m.group(1), MAX_NAME)
            elif 'text-danger' in css:
                credits.append(t)
        cells = [text(c) for c in _CELL_RE.findall(table_row)]
        date = iso_date(cells[3], 'dmy') if len(cells) > 3 else ''
        downloads = _count(cells[4]) if len(cells) > 4 else None
        return clean({
            'credits': cap(' | '.join(credits), MAX_CREDITS),
            'uploader': uploader,
            'date': date,
            'downloads': downloads,
            'fixed': bool(_FIXED_RE.search(table_row)),
        })
    except Exception:
        return {}


# ---- Wizdom ---------------------------------------------------------------------------------------------------------
def wizdom_index(releases):
    """{subtitle id (int): meta} of every subtitle in a /api/releases/<imdb> answer. A movie's 'subs' is a list
    of rows, a show's is {season: {episode: [rows]}}; both are walked the same way. Never raises."""
    out = {}

    def walk(node):
        if isinstance(node, dict):
            if 'id' in node and ('version' in node or 'credits' in node or 'date' in node):
                try:
                    key = int(node['id'])
                except (ValueError, TypeError):
                    return
                m = clean({
                    'credits': cap(node.get('credits'), MAX_CREDITS),
                    'uploader': cap(node.get('uploader'), MAX_NAME),
                    'date': iso_date(node.get('date'), 'mdy'),
                })
                if m:
                    out[key] = m
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        walk(releases.get('subs') if isinstance(releases, dict) else None)
    except Exception:
        pass
    return out


def wizdom_row(index, sub_id):
    """The meta of one Wizdom search row from the title's index ({} when it is not there)."""
    try:
        return dict(index.get(int(sub_id)) or {})
    except Exception:
        return {}


# ---- OpenSubtitles --------------------------------------------------------------------------------------------------
STUDIO_WORD = 'אולפנים'


def opensubtitles_row(attributes, release_raw):
    """The meta of one OpenSubtitles result (`attributes` of its JSON item). `release_raw` is the release name
    as the API sent it, before the provider module removes characters and the word 'אולפנים'. Never raises."""
    try:
        up = attributes.get('uploader') or {}
        return clean({
            'uploader': cap(up.get('name'), MAX_NAME),
            'uploader_rank': cap(up.get('rank'), MAX_NAME),
            'date': iso_date(attributes.get('upload_date'), 'iso'),
            'downloads': _count(attributes.get('download_count')),
            'comments': cap(attributes.get('comments'), MAX_COMMENTS),
            'release_raw': cap(release_raw, MAX_RELEASE) if STUDIO_WORD in (release_raw or '') else '',
        })
    except Exception:
        return {}
