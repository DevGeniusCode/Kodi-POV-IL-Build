# -*- coding: utf-8 -*-
"""Deterministic categorizer and sorter for ``skin.fentastic``'s ``settings.xml``.

Kodi's skin-settings serializer strips XML comments and writes the settings in its own (hash) order, so two exports of
the same configuration can differ on every line. ``format_settings`` rewrites the text into one canonical layout:

  * the ``<setting>`` elements are grouped under comment blocks (``CATEGORIES``, in that order, empty ones left out);
  * inside a block they are sorted by ``id`` (case-insensitive, then exact), so the output is 100% deterministic;
  * every ``<setting>`` element is copied as the ORIGINAL TEXT (escapes such as ``&apos;`` stay as Kodi wrote them);
    nothing is re-serialized, and the result is parsed back and compared with the input before it is returned.

This module is stdlib-only and imports no ``xbmc*`` module at module level: the add-on action
(``RunScript(script.fentastic.helper,mode=sort_settings)``, ``custom_actions.sort_settings``) and the repository tool
(``.github/scripts/format_fentastic_settings.py``) both load THIS file, so there is one implementation.

Adding a setting: ``_EXACT`` is checked first, then the ordered ``_RULES`` (first match wins); a setting that nothing
knows goes to the last category (``Performance & Misc``). Add an id to ``_EXACT`` (or a pattern to ``_RULES``) to place it.
"""

import re
import xml.etree.ElementTree as ET

SKIN_ID = "skin.fentastic"
INDENT = "    "
RULE_WIDTH = 76

GENERAL = "General & Layout"
HOME = "Home Screen & Widgets"
HUBS = "Hubs & Shortcuts"
SEARCH = "Search & Results"
OSD = "OSD & Video Player"
RATINGS = "Ratings, Media Flags & Badges"
SUBTITLES = "Subtitles & Language"
THEME = "Colors, Fonts & Theme"
MISC = "Performance & Misc"

# (title, what the block holds): the order here is the order in the file.
CATEGORIES = (
    (GENERAL, "Skin-wide layout, view and profile display switches."),
    (HOME, "Home screen look, category widgets, weather and the widget menu."),
    (HUBS, "Main-menu buttons (hide / show) and the labels of the custom shortcuts."),
    (SEARCH, "Search window behaviour, the search provider and which result rows are shown."),
    (OSD, "The player OSD, pause window, close behaviour and trailers."),
    (RATINGS, "Rating sources, circle ratings, media flags and studio badges."),
    (SUBTITLES, "Subtitle search and the subtitle chooser button."),
    (THEME, "Backgrounds, fanart, brightness, overlay and TMDb Helper image effects."),
    (MISC, "Animations, internal state and anything not placed above."),
)
_ORDER = [title for title, _doc in CATEGORIES]

# Exact ids, checked before the patterns (case-insensitive).
_EXACT = {
    "autoscroll": GENERAL,
    "uselandscape": GENERAL,
    "landscapeinfoscroll": GENERAL,
    "touchmode": GENERAL,
    "enable.alternateinfo": GENERAL,
    "show_profilename": GENERAL,
    "show_profileavatar": GENERAL,
    "show_musicvideoposter": GENERAL,
    "noiconsilhouettes": GENERAL,
    "headerimagetype": GENERAL,
    "headertexttype": GENERAL,
    "home_no_categories_widget": HOME,
    "category_widget_delay": HOME,
    "category_widget_display_delay": HOME,
    "show_widget_menu_border": HOME,
    "show_weatherinfo": HOME,
    "hideraindrops": HOME,
    "removenetflixbuttonsbg": HOME,
    "backwindowopecitychange": HOME,
    "backwindowopecitychange.40": HOME,
    "disableidan": HUBS,
    "disable.widgetlabelratings": RATINGS,
    "disable.colorstudio": RATINGS,
    "mdblist_api_key": RATINGS,
    "hide_mediaflags": RATINGS,
    "subtitlesearch": SUBTITLES,
    "choosesubtitlesbuttonopenskodiwindow": SUBTITLES,
    "chooseosdplayer": OSD,
    "__chooseplayer": OSD,
    "playerclosedelay": OSD,
    "playerplaylist_osd": OSD,
    "showplayerpausewindow": OSD,
    "oneclickclose": OSD,
    "trailerplaying": OSD,
    "tmdbhelper.service": MISC,
    "firsttimerun": MISC,
    "databasestatus": MISC,
    "numeric": MISC,
    "zoomtoggle": MISC,
    "nofocusanimations": MISC,
    "no_slide_animations": MISC,
}

# Ordered (pattern, category) rules, tried on the lower-cased id after ``_EXACT``.
_RULES = tuple((re.compile(pattern), title) for pattern, title in (
    (r"^homemenuno.*button$", HUBS),
    (r"^menu\w*label$", HUBS),
    (r"^search|_search_|searchwindow|searchresultswindow", SEARCH),
    (r"results$", SEARCH),
    (r"^osd", OSD),
    (r"^enable\.oneclick", OSD),
    (r"^disable\.\w*rating", RATINGS),
    (r"^circle_", RATINGS),
    (r"^(rating|badge|mediaflag)", RATINGS),
    (r"^subtitle", SUBTITLES),
    (r"^tmdbhelper\.", THEME),
    (r"fanart|background|brightness|overlay|font|color", THEME),
))


class SettingsFormatError(Exception):
    """The input cannot be reformatted without risking a lost or altered setting."""


def category_of(setting_id):
    """Category title for a setting id (deterministic; unknown ids land in ``Performance & Misc``)."""
    key = setting_id.lower()
    if key in _EXACT:
        return _EXACT[key]
    for pattern, title in _RULES:
        if pattern.search(key):
            return title
    return MISC


_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SETTING = re.compile(r"<setting\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*?(?:/>|>.*?</setting\s*>)", re.S)
_DECL = re.compile(r"^\s*(<\?xml[^>]*\?>)", re.S)
_ROOT_OPEN = re.compile(r"<settings\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>")


def _detect_eol(text, default):
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    return default


def _parse(text, label):
    try:
        return ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise SettingsFormatError("%s is not valid XML: %s" % (label, exc))


def _signature(root):
    """Order-independent description of every setting: nothing may differ between input and output."""
    return sorted((el.get("id"), tuple(sorted(el.attrib.items())), el.text or "",
                   tuple(ET.tostring(child, encoding="unicode") for child in el))
                  for el in root.findall("setting"))


def _block(title, doc):
    rule = INDENT + "<!-- " + "=" * RULE_WIDTH + " -->"
    return [rule, INDENT + "<!-- " + title + " -->", INDENT + "<!-- " + doc + " -->", rule]


def _render(text, eol):
    """Comments dropped, every ``<setting>`` copied as text, grouped and sorted; no verification."""
    declaration = _DECL.match(text)
    stripped = _COMMENT.sub("", text[declaration.end():] if declaration else text)
    root_open = _ROOT_OPEN.search(stripped)
    if not root_open or "</settings>" not in stripped:
        raise SettingsFormatError("no <settings> element found")
    body = stripped[root_open.end():stripped.rindex("</settings>")]
    elements = [m.group(0) for m in _SETTING.finditer(body)]
    leftover = _SETTING.sub("", body).strip()
    if leftover:
        raise SettingsFormatError("content that is not a <setting> inside <settings>: %r" % leftover[:60])

    groups = {title: [] for title in _ORDER}
    for raw in elements:
        setting_id = _parse(raw, "setting").get("id") or ""
        groups[category_of(setting_id)].append((setting_id.lower(), setting_id, raw))

    lines = [declaration.group(1)] if declaration else []
    lines.append(root_open.group(0))
    header = len(lines)
    for title, doc in CATEGORIES:
        members = sorted(groups[title])
        if not members:
            continue
        if len(lines) > header:
            lines.append("")
        lines.extend(_block(title, doc))
        lines.extend(INDENT + raw.strip() for _key, _id, raw in members)
    lines.append("</settings>")
    return eol.join(lines) + eol


def format_settings(text, eol=None):
    """Return ``text`` as the canonical, categorized and sorted ``settings.xml`` (raises ``SettingsFormatError``).

    ``eol`` defaults to the input's own line ending (CRLF when it has none). Before returning, the result is parsed
    back and compared setting by setting with the input (id, attributes, value), and formatted a second time to prove
    the layout is stable.
    """
    if text.startswith("﻿"):
        text = text[1:]
    eol = eol or _detect_eol(text, "\r\n")
    source = _parse(text, "input")
    if source.tag != "settings":
        raise SettingsFormatError("root element is <%s>, expected <settings>" % source.tag)
    for child in source:
        if child.tag != "setting":
            raise SettingsFormatError("unexpected element <%s> inside <settings>" % child.tag)
    ids = [el.get("id") for el in source.findall("setting")]
    if not all(ids):
        raise SettingsFormatError("a <setting> has no id")
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SettingsFormatError("duplicate setting id(s): %s" % ", ".join(duplicates))

    result = _render(text, eol)
    if _signature(_parse(result, "output")) != _signature(source):
        raise SettingsFormatError("verification failed: the output differs from the input in its settings")
    if _render(result, eol) != result:
        raise SettingsFormatError("verification failed: formatting the output again changed it")
    return result


def format_file(path, write=True, eol=None):
    """Format ``path`` in place (atomic replace, UTF-8). Returns ``(changed, setting_count)``."""
    import os

    with open(path, "rb") as handle:
        original = handle.read().decode("utf-8")
    formatted = format_settings(original, eol=eol)
    changed = formatted != original
    if changed and write:
        temp = path + ".sorting"
        with open(temp, "wb") as handle:
            handle.write(formatted.encode("utf-8"))
        os.replace(temp, path)
    return changed, formatted.count("<setting ")
