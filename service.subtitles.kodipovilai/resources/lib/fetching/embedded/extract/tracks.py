# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Track listing and choice: language keys, SDH / signs tracks, the picker of the track to read.

import re
from resources.lib.fetching.embedded.extract.tuning import DEFAULT_HEAD_BYTES
from resources.lib.fetching.embedded.extract.http_source import Source
from resources.lib.fetching.embedded.extract.matroska import is_bitmap_codec, is_text_codec, parse_head, sub_tracks
from resources.lib.fetching.embedded.extract.ebml import noop


# ---- public API -------------------------------------------------------------
def track_rows(tracks):
    """probe_tracks()'s rows for a parsed head's tracks."""
    out = []
    for t in sub_tracks(tracks):
        out.append({'num': t['num'], 'codec': t['codec'],
                    'lang': (t['lang'] or '').lower(),
                    'forced': bool(t['forced']),
                    'is_text': is_text_codec(t['codec']),
                    'name': t.get('name') or '',
                    'sdh': _track_is_sdh(t),
                    'signs': _track_is_signs(t),
                    'lang_explicit': bool(t.get('lang_explicit', True))})
    return out


def probe_tracks(url_or_path, head_bytes=DEFAULT_HEAD_BYTES, log=None):
    """Return the embedded subtitle tracks as
        [{'num','codec','lang','forced','is_text','name','sdh','signs',
          'lang_explicit'}, ...]
    or [] when the file isn't Matroska / has none / can't be read. Cheap: reads
    only the head. Never raises."""
    _log = log or noop
    try:
        src = Source(url_or_path)
        if not src.total:
            return []
        _seg, _scale, tracks, _seeks = parse_head(src, head_bytes, _log)
        return track_rows(tracks)
    except Exception as e:
        _log('probe_tracks failed: %s' % e)
        return []


# ISO 639 language-code equivalences. Kodi and the subtitle providers hand us a
# 2-letter ISO 639-1 code (e.g. 'es'), but a Matroska TrackEntry's Language
# element almost always carries the 3-letter ISO 639-2/B (bibliographic) code
# (e.g. 'spa'). For ~20 languages the 2-letter code is NOT a prefix of the
# 3-letter one -- 'es'!='spa', 'de'!='ger', 'nl'!='dut', 'ja'!='jpn', 'sv'!=
# 'swe', 'el'!='gre', 'zh'!='chi', 'cs'!='cze', 'ro'!='rum', 'sk'!='slo',
# 'is'!='ice', ... -- so a naive `track_lang.startswith(pref)` SILENTLY fails to
# match the track and the embedded translation falls through to another language
# (or the wrong cache). Canonicalize BOTH the requested code and the track code
# to a single ISO 639-1 key before comparing. Each row lists every code that
# means the same language (639-1, 639-2/B, 639-2/T, plus a couple of legacy
# aliases); every code in the row maps to the row's first (2-letter) entry.
# Languages where the 2-letter code IS a prefix of the 3-letter one (en/eng,
# fr/fre, it/ita, ru/rus, pt/por, ...) still resolve -- through this table or the
# startswith() fallback that is kept as a safety net for any code not listed.
_ISO639_ROWS = (
    ('en', 'eng'), ('es', 'spa'), ('fr', 'fre', 'fra'), ('de', 'ger', 'deu'),
    ('it', 'ita'), ('pt', 'por', 'pob', 'pb'), ('nl', 'dut', 'nld'),
    ('ru', 'rus'), ('pl', 'pol'), ('cs', 'cze', 'ces'), ('sk', 'slo', 'slk'),
    ('sl', 'slv'), ('ro', 'rum', 'ron'), ('el', 'gre', 'ell'), ('hu', 'hun'),
    ('fi', 'fin'), ('sv', 'swe'), ('da', 'dan'),
    # Norwegian + Bokmal/Nynorsk all fold to 'no': the add-on never distinguishes
    # them (it always requests generic 'no'), and a track tagged 'nob'/'nno' must
    # still match a 'no' request the way the old prefix code did ('nob'.startswith
    # ('no')). Keeping them as separate canonicals would silently drop those tags.
    ('no', 'nor', 'nb', 'nob', 'nn', 'nno'),
    ('is', 'ice', 'isl'), ('tr', 'tur'), ('ar', 'ara'),
    ('he', 'heb', 'iw'), ('fa', 'per', 'fas'), ('hi', 'hin'), ('ja', 'jpn'),
    ('ko', 'kor'), ('zh', 'chi', 'zho'), ('th', 'tha'), ('vi', 'vie'),
    ('id', 'ind'), ('ms', 'may', 'msa'), ('uk', 'ukr'), ('bg', 'bul'),
    ('sr', 'srp', 'scc'), ('hr', 'hrv', 'scr'), ('bs', 'bos'),
    ('mk', 'mac', 'mkd'), ('sq', 'alb', 'sqi'), ('et', 'est'), ('lv', 'lav'),
    ('lt', 'lit'), ('ka', 'geo', 'kat'), ('hy', 'arm', 'hye'), ('az', 'aze'),
    ('kk', 'kaz'), ('eu', 'baq', 'eus'), ('gl', 'glg'), ('ca', 'cat'),
    ('cy', 'wel', 'cym'), ('ga', 'gle'), ('af', 'afr'), ('sw', 'swa'),
    ('ta', 'tam'), ('te', 'tel'), ('ml', 'mal'), ('kn', 'kan'), ('bn', 'ben'),
    ('mr', 'mar'), ('gu', 'guj'), ('pa', 'pan'), ('ur', 'urd'), ('ne', 'nep'),
    ('si', 'sin'), ('my', 'bur', 'mya'), ('km', 'khm'), ('lo', 'lao'),
    ('bo', 'tib', 'bod'), ('mn', 'mon'), ('mi', 'mao', 'mri'),
)
_ISO639_CANON = {alias: row[0] for row in _ISO639_ROWS for alias in row}


def lang_key(code):
    """Canonical ISO 639-1 key for a language code, so 'es' and 'spa' (and 'de'/
    'ger', 'ja'/'jpn', ...) compare equal. Strips any region/script suffix
    ('pt-BR' -> 'pt', 'zh_Hans' -> 'zh') and lowercases; returns the mapped
    2-letter code, or the cleaned input when the code isn't in the table (so an
    unknown/exotic code still self-compares)."""
    c = (code or '').strip().lower()
    if not c:
        return ''
    for sep in ('-', '_'):
        if sep in c:
            c = c.split(sep, 1)[0]
    return _ISO639_CANON.get(c, c)


def _name_is_sdh(name):
    """True when a TrackName clearly marks a hearing-impaired / SDH track. Matches
    ONLY whole tokens ("english (sdh)" -> ['english','sdh']) -- never a bare
    substring, so "hi" inside "Highlander" can't match. Bare "hi"/"cc" are
    deliberately NOT markers (too ambiguous); the authoritative FlagHearingImpaired
    catches flag-tagged tracks regardless of name."""
    toks = [t for t in re.split(r'[^a-z0-9]+', (name or '').lower()) if t]
    if 'sdh' in toks:
        return True
    for i in range(len(toks) - 1):
        if toks[i] == 'hearing' and toks[i + 1] == 'impaired':
            return True
    return False


def _track_is_sdh(t):
    """A subtitle track is SDH if the authoritative Matroska flag is set, or its
    name says so. SDH tracks carry the FULL dialogue (nothing dropped for the
    hearing), so they translate more completely -- preferred by _pick_track."""
    return bool(t.get('hearing_impaired')) or _name_is_sdh(t.get('name'))


def _track_is_signs(t):
    """A text track named as signs/songs only (anime releases leave many
    unflagged): the dialogue is not in it."""
    toks = [x for x in re.split(r'[^a-z0-9]+', (t.get('name') or '').lower()) if x]
    return any(x in ('sign', 'signs', 'song', 'songs', 'forced') for x in toks)


def choose_track(src, subs, track_num, lang, other_text, on_track, _log):
    """The track extract_srt reads: _pick_track's, or -- when the picked
    language's subtitle is a picture -- the best other text track
    (`other_text`), reported through `on_track`; src._stats['why'] =
    'no_text' when there is none. Shared by the Matroska and MP4 paths."""
    track = pick_track(subs, track_num, lang, prefer_sdh=True)
    if track is None and track_num is None and lang:
        want_key = lang_key(lang)
        if any(is_bitmap_codec(t['codec']) and not t['forced']
               and lang_key(t['lang']) == want_key for t in subs):
            # The picked language's subtitle here is a picture, not text
            # (owner's log, 14 Nov 2026: South Park S04E04 MULTI WEB
            # 2160p, English was S_HDMV/PGS, French was text; the pick
            # ended in "could not extract"). The owner: no OCR -- read
            # another TEXT track of the file, in the language best for the
            # AI (`other_text`), and say so. Signs-only tracks last,
            # forced ones and Hebrew never. A forced-only text track or a
            # language that is not here at all is not a picture: as before.
            texts = [t for t in subs
                     if is_text_codec(t['codec']) and not t['forced']
                     and lang_key(t['lang']) not in ('he', want_key)]
            if other_text and texts:
                order = [lang_key(c) for c in other_text]

                def _rank(t):
                    k = lang_key(t['lang'])
                    # a track with no language of its own defaults to
                    # 'eng': its real language is unknown -- after the
                    # tagged ones (review)
                    return (_track_is_signs(t),
                            not t.get('lang_explicit', True),
                            order.index(k) if k in order else len(order),
                            not _track_is_sdh(t), t['num'])
                texts.sort(key=_rank)
                track = texts[0]
                if on_track is not None:
                    try:
                        on_track(lang_key(track['lang']) or track['lang'])
                    except Exception:
                        pass
                _log('the %s subtitle is a picture (the file has: %s) -- '
                     'reading the %s text track #%s instead' % (
                         lang, ', '.join('#%s %s [%s]' % (
                             t['num'], t['codec'] or '?', t['lang'])
                             for t in subs),
                         track['lang'], track['num']))
            elif src._stats is not None:
                src._stats['why'] = 'no_text'
    return track


# A file's ONLY subtitle track, flagged forced (QA Kodi, 19 Nov 2026: The Beast
# 2026 WEB-DL Dual Audio, one S_TEXT/ASS [en] track, forced, and the log said "no
# matching text track"): pick_track never offers a forced track, so the embedded
# source was refused. Releases that mux their one full English track with the
# forced bit set are common, and a REAL forced track (a few dozen foreign-line
# cues) is told apart only by its size, which is known after the read. So
# extract_srt reads such a lone track and delivers it only from this many cues up
# (the same 200 as the MP4 path's "signs / forced" rule in extract_mp4).
LONE_FORCED_MIN_CUES = 200


def lone_forced_track(subs, track_num, lang):
    """The file's single subtitle track when it is a text track flagged forced
    and its language is the one asked for (or is not known); else None. Only
    extract_srt uses it, and only after pick_track found nothing: the timing
    probes (cue_reference) keep skipping forced tracks."""
    if track_num is not None or len(subs) != 1:
        return None
    t = subs[0]
    if not (t['forced'] and is_text_codec(t['codec'])):
        return None
    if (lang and t.get('lang_explicit', True)
            and lang_key(t['lang']) != lang_key(lang)):
        return None
    return t


def pick_track(subs, track_num, lang, prefer_sdh=False):
    # prefer_sdh: order an SDH/hearing-impaired track FIRST among equally-tagged
    # matches. Turn it on ONLY when the track's own TEXT is what we translate
    # (extract_srt) -- SDH carries the complete dialogue. Keep it OFF (default)
    # when the track is used merely as a TIMING skeleton to align an EXTERNAL sub
    # (cue_reference_times*): an SDH track's extra sound-description cues have no
    # match in a plain external subtitle and would only depress the align overlap/
    # vote ratios, risking a previously-synced sub failing the gate.
    _sdh_key = ((lambda t: not _track_is_sdh(t)) if prefer_sdh else (lambda t: 0))
    if track_num is not None:
        for t in subs:
            if t['num'] == track_num:
                return t
        return None
    if lang:
        want = lang_key(lang)
        pref = (lang or '').strip().lower()[:2]

        def _lang_match(tl):
            tl = (tl or '').strip().lower()
            if not tl:
                return False
            # Canonical ISO 639-1/2B/2T equivalence (es<->spa, de<->ger, ...).
            if want and lang_key(tl) == want:
                return True
            # Prefix fallback ONLY for a code we don't recognise. A RECOGNISED
            # code whose canonical differs from `want` is a definitively
            # different language, so we must NOT prefix-match it -- otherwise a
            # request for 'es' would wrongly grab Estonian 'est', or 'ar' would
            # grab Armenian 'arm' (a latent bug in the old startswith-only path).
            base = tl.split('-', 1)[0].split('_', 1)[0]
            if base in _ISO639_CANON:
                return False
            return bool(pref) and tl.startswith(pref)

        # Forced/signs-only tracks are excluded from auto-pick (same rule as the
        # no-lang branch below): a sparse signs-only sub is a worse deliverable
        # than falling through to the external subtitle search. This matters now
        # that an untagged forced track defaults to 'eng' and would otherwise
        # match the prefix.
        cand = [t for t in subs if is_text_codec(t['codec'])
                and not t['forced']
                and _lang_match(t['lang'])]
        # Order among matched tracks: (1) an explicitly-tagged language beats one
        # that only defaulted to 'eng' (stronger language-confidence); (2) among
        # equally-tagged tracks prefer the SDH/hearing-impaired one -- it has the
        # complete dialogue, so it translates more fully (and, later, is the best
        # gender source); (3) then track order.
        cand.sort(key=lambda t: (not t.get('lang_explicit', True),
                                 _sdh_key(t), t['num']))
        if cand:
            return cand[0]
        # No language match. When the file carries exactly ONE non-forced text
        # track it is almost certainly the stream Kodi surfaced (its tag may be
        # 'und' or otherwise not our prefix); use it rather than failing the
        # whole extraction -- BUT only when that lone track's language is genuinely
        # unknown, not when it is explicitly a DIFFERENT known language. Handing
        # back an explicit 'eng' text track for a Spanish request (e.g. the file's
        # only text sub is English while the Spanish track is a bitmap PGS) would
        # mislabel the source language and translate the wrong text. A tag is
        # "genuinely unknown" when it isn't a recognised ISO code (und/mis/...) OR
        # it only DEFAULTED to 'eng' (absent Language element, lang_explicit=False)
        # -- that track's real language is unknown, so it stays eligible.
        texts = [t for t in subs
                 if is_text_codec(t['codec']) and not t['forced']]
        if len(texts) == 1:
            only = texts[0]
            tl = (only['lang'] or '').strip().lower()
            base = tl.split('-', 1)[0].split('_', 1)[0]
            # We only reach here when `only` did NOT lang-match (a matching track
            # would already have been returned via `cand` above). Use it anyway
            # when its language is genuinely unknown -- an unrecognised tag
            # (und/mis/...) or one that merely DEFAULTED to 'eng' (absent Language
            # element, lang_explicit=False) -- but NOT when it is an explicit,
            # recognised, DIFFERENT language (that would mislabel the source).
            if base not in _ISO639_CANON or not only.get('lang_explicit', True):
                return only
        return None
    cand = [t for t in subs if is_text_codec(t['codec']) and not t['forced']]
    cand.sort(key=lambda t: (_sdh_key(t), t['num']))   # SDH first (if prefer_sdh), then order
    return cand[0] if cand else None
