# -*- coding: utf-8 -*-
"""Hebrew text for POV's embedded strings table (modules/strings_po.py), merged once at import by the
pov_hebrew_interface patch (kodi_utils.py).

POV 6.11 reads its UI text only from that embedded English table (local_string), never from a .po file, so the
Wizard's Hebrew .po (resources/text/pov.resource.language.he_il.strings.po) has to be merged in here.
HEBREW_UI holds the hand-picked labels and always wins over the .po where both have an entry. Never translate
user list or media titles: only fixed interface IDs belong in either source.
"""
import os
import re

_PO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'text',
                        'pov.resource.language.he_il.strings.po')
_ENTRY = re.compile(r'msgctxt "#(\d+)"\r?\nmsgid "(.*)"\r?\nmsgstr "(.*)"')
_ESCAPE = re.compile(r'\\(.)')
_ESCAPES = {'n': '\n', 't': '\t'}
HEBREW_UI = {32790: 'נקודת המשך: [B]%s[/B]', 32832: 'המשך צפייה', 32833: 'התחל מהתחלה', 32450: 'חיפוש', 32028: 'סרטים', 32029: 'סדרות', 32507: 'אנשים', 32499: 'קולקציה', 32857: 'חדש', 32486: 'היסטוריה', 32537: 'עונה', 32698: 'הסרה מההיסטוריה', 32699: 'ניקוי כל ההיסטוריה', 32452: 'אנשים פופולריים', 32451: 'גילוי', 32453: 'מועדפים', 32107: 'הורדות', 32454: 'הרשימות שלי', 32455: 'השירותים שלי', 32247: 'הגדרות', 32043: 'הנצפים ביותר השבוע', 32449: 'טרנדים חדשים', 32458: 'טרנדים', 32459: 'פופולריים', 32460: 'בכורות', 32461: 'חדשים', 32463: 'שוברי קופות', 32468: 'זוכי אוסקר', 32469: 'בקרוב', 32470: 'ז׳אנרים', 32472: 'שנים', 32474: 'כי צפית...', 32475: 'נצפו', 32476: 'בתהליך צפייה', 32480: 'רשתות שידור', 32481: 'סדרות בתהליך צפייה', 32482: 'פרקים בתהליך צפייה', 32483: 'הפרקים הבאים'}


def _unescape(text):
    return _ESCAPE.sub(lambda match: _ESCAPES.get(match.group(1), match.group(1)), text) if '\\' in text else text


def merge(base):
    """POV's English table + the Hebrew .po + HEBREW_UI, as a new dict.

    A .po entry is used only when its msgid equals POV's English text for that ID and it keeps the same number
    of % placeholders (a stale .po after a POV update, or a broken placeholder, would otherwise show the wrong
    text or crash a `%` format at the call site). Entries it cannot use stay English; multi-line entries are
    not read. A missing or unreadable .po still leaves the curated labels in place.
    """
    merged = dict(base)
    try:
        with open(_PO_FILE, encoding='utf-8') as handle:
            for ident, msgid, msgstr in _ENTRY.findall(handle.read()):
                english = base.get(int(ident))
                if english is None or not msgstr:
                    continue
                msgid, msgstr = _unescape(msgid), _unescape(msgstr)
                if msgid.strip() == english.strip() and msgstr.count('%') == english.count('%'):
                    merged[int(ident)] = msgstr
    except Exception as error:
        import xbmc
        xbmc.log('Wizard Patch Error [pov_ui_labels] Hebrew .po not merged: %r' % (error,), xbmc.LOGERROR)
    merged.update(HEBREW_UI)
    return merged
