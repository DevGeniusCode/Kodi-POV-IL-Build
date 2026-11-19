# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The settings facade: typed accessors over the add-on settings, the Kodi subtitle
# language preference and the embedded-translation mode.

import threading
try:
    import xbmc
    import xbmcaddon
    import xbmcvfs
    import xbmcgui
    KODI_AVAILABLE = True
except ImportError:
    # Allows unit tests / standalone scripts to import the module
    # without a Kodi runtime.
    KODI_AVAILABLE = False
    xbmc = None
    xbmcaddon = None
    xbmcvfs = None
    xbmcgui = None
from resources.lib.core.addon import addon


_EMBEDDED_MODE_VALUES = (
    'auto', 'align_only', 'direct', 'local_only', 'off')
_embedded_mode_lock = threading.Lock()


def get_setting(key, default=''):
    try:
        v = addon().getSetting(key)
        return v if v is not None else default
    except Exception:
        return default


def get_bool(key, default=False):
    v = get_setting(key, '')
    if v == '':
        return default
    return v.lower() == 'true'


def get_int(key, default=0):
    v = get_setting(key, '')
    try:
        return int(v) if v != '' else default
    except (ValueError, TypeError):
        return default


def get_float(key, default=0.0):
    v = get_setting(key, '')
    try:
        return float(v) if v != '' else default
    except (ValueError, TypeError):
        return default


def _read_kodi_setting_value(setting):
    """Read a Kodi *system* setting via JSON-RPC (Settings.GetSettingValue).
    Returns the raw value (str/list/...) or None on any failure."""
    if not KODI_AVAILABLE or xbmc is None:
        return None
    try:
        import json
        payload = {
            'jsonrpc': '2.0', 'id': 1,
            'method': 'Settings.GetSettingValue',
            'params': {'setting': setting},
        }
        resp = json.loads(xbmc.executeJSONRPC(json.dumps(payload)))
        return (resp.get('result') or {}).get('value')
    except Exception:
        return None


# Anything that names Hebrew, whether as an ISO code or a language name.
_HEBREW_LANG_TOKENS = ('he', 'heb', 'iw', 'hebrew', 'עברית')
# Kodi 'preferred subtitle language' tokens that do NOT name a concrete
# language. When the setting holds one of these we can't conclude the user
# prefers a non-Hebrew language, so we defer to the download-languages list.
_SUBTITLE_LANG_SPECIAL = ('', 'none', 'forced_only', 'forcedonly',
                          'default', 'original', 'mediadefault')


def _is_hebrew_lang(value):
    return (value or '').strip().lower() in _HEBREW_LANG_TOKENS


def hebrew_subtitle_wanted():
    """Decide whether the user actually wants Hebrew subtitles, so the AI
    translator knows when to stay out of the way.

    Returns ``False`` ONLY when we can positively determine the user prefers
    a specific NON-Hebrew subtitle language (e.g. they set Kodi's
    "preferred subtitle language" to English). In every ambiguous or
    unreadable case we return ``True`` -- preserving the long-standing
    AI-Hebrew default so we never silently disable translation for the
    Hebrew-default majority, and so a settings-read failure can't break
    anyone. This is purely an *extra* gate: it never enables translation
    that other settings (DarkSubs auto_translate / force_ai_when_auto_translate_off)
    have already turned off."""
    try:
        # 1. "Languages to download subtitles for" (subtitles.languages).
        #    Hebrew HERE is the strongest positive signal there is -- it is
        #    exactly the list Kodi's subtitle search uses -- so it wins even
        #    when the PLAYBACK preference (locale.subtitlelanguage) names
        #    another language. Field case: a standalone user on a foreign
        #    build had locale.subtitlelanguage=English, and the old
        #    precedence silently disabled the entire addon ("offers no AI
        #    entries") even though Hebrew was in their download list.
        dl = _read_kodi_setting_value('subtitles.languages')
        if isinstance(dl, str):
            dl = [p for p in dl.replace(';', ',').split(',') if p.strip()]
        dl_has_hebrew = (isinstance(dl, (list, tuple)) and dl
                         and any(_is_hebrew_lang(x) for x in dl))
        if dl_has_hebrew:
            return True

        # 2. "Preferred subtitle language" (locale.subtitlelanguage) -- a
        #    single value naming a concrete language.
        pref = (_read_kodi_setting_value('locale.subtitlelanguage') or '')
        pref_norm = str(pref).strip().lower()
        if pref_norm and pref_norm not in _SUBTITLE_LANG_SPECIAL:
            return _is_hebrew_lang(pref_norm)

        # 3. Download list readable, non-empty and Hebrew-less (and no
        #    concrete playback preference) -> the user asked for other
        #    languages only.
        if isinstance(dl, (list, tuple)) and dl:
            return False

        # 4. Nothing conclusive -> keep the AI-Hebrew default.
        return True
    except Exception:
        return True


def set_setting(key, value):
    """Set an addon setting. Returns True if the write persisted,
    False otherwise -- some Kodi/Android combinations silently
    swallow setSetting calls (the API returns ok but the value
    never reaches settings.xml on disk). Reading back is the only
    way to know whether the save actually took. Callers that don't
    care just ignore the return value -- backward compatible."""
    str_value = str(value)
    try:
        addon().setSetting(key, str_value)
    except Exception:
        return False
    try:
        return addon().getSetting(key) == str_value
    except Exception:
        return False


def embedded_translation_mode():
    """Return and maintain the canonical embedded-subtitle strategy.

    Version 0.2.441 replaces two hidden booleans with one explained mode list.
    On the first run, preserve the exact legacy on/off + HTTP boundary:
    disabled -> ``off``; enabled but HTTP-disabled -> ``local_only``; otherwise
    ``auto``.  Once migrated, the new selector is canonical and its closest
    legacy representation is mirrored back for downgrade compatibility.
    """
    with _embedded_mode_lock:
        marker = get_setting('_embedded_mode_v1', '') == '1'
        raw = (get_setting('embedded_translation_mode', '') or '').strip().lower()

        if not marker:
            # A non-default mode can only have been explicitly selected in the
            # new UI before the service migration ran, so honour it.
            if raw in _EMBEDDED_MODE_VALUES and raw != 'auto':
                mode = raw
            elif not get_bool('embedded_translate', True):
                mode = 'off'
            elif not get_bool('embedded_http_extract', True):
                mode = 'local_only'
            else:
                mode = 'auto'
        else:
            if raw in _EMBEDDED_MODE_VALUES:
                mode = raw
            elif not get_bool('embedded_translate', True):
                mode = 'off'
            elif not get_bool('embedded_http_extract', True):
                mode = 'local_only'
            else:
                mode = 'auto'

        enabled = mode != 'off'
        allow_http = mode not in ('off', 'local_only')
        writes_ok = True
        if raw != mode:
            writes_ok = set_setting('embedded_translation_mode', mode) and writes_ok
        if get_bool('embedded_translate', True) != enabled:
            writes_ok = set_setting(
                'embedded_translate', 'true' if enabled else 'false') and writes_ok
        if get_bool('embedded_http_extract', True) != allow_http:
            writes_ok = set_setting(
                'embedded_http_extract',
                'true' if allow_http else 'false') and writes_ok
        if not marker and writes_ok:
            set_setting('_embedded_mode_v1', '1')
        return mode


def embedded_translation_policy(mode=None):
    """Map one mode to the exact runtime paths it permits."""
    if mode not in _EMBEDDED_MODE_VALUES:
        mode = embedded_translation_mode()
    return {
        'mode': mode,
        'enabled': mode != 'off',
        'try_align': mode in ('auto', 'align_only', 'local_only'),
        'try_extract': mode in ('auto', 'direct', 'local_only'),
        'allow_http': mode not in ('off', 'local_only'),
    }
