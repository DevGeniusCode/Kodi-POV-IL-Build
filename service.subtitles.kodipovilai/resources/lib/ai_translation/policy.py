# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Translation policy: the embedded-translation mode and the source languages in use.

from resources.lib.core import settings


_EMBEDDED_TRANSLATION_MODES = (
    'auto', 'align_only', 'direct', 'local_only', 'off')


def _embedded_translation_mode():
    """Return the user-facing embedded-translation strategy.

    New installs use the explicit mode selector.  The two old hidden booleans
    remain declared for compatibility with an older settings.xml; if the mode is
    absent or corrupt, derive the closest safe legacy behaviour instead of
    silently enabling a path the user had disabled.
    """
    try:
        return settings.embedded_translation_mode()
    except Exception:
        try:
            mode = (settings.get_setting(
                'embedded_translation_mode', '') or '').strip().lower()
        except Exception:
            mode = ''
        if mode in _EMBEDDED_TRANSLATION_MODES:
            return mode
        try:
            if not settings.get_bool('embedded_translate', True):
                return 'off'
            if not settings.get_bool('embedded_http_extract', True):
                return 'local_only'
        except Exception:
            pass
        return 'auto'


def _embedded_translation_policy():
    mode = _embedded_translation_mode()
    try:
        return settings.embedded_translation_policy(mode)
    except Exception:
        return {
            'mode': mode,
            'enabled': mode != 'off',
            'try_align': mode in ('auto', 'align_only', 'local_only'),
            'try_extract': mode in ('auto', 'direct', 'local_only'),
            'allow_http': mode not in ('off', 'local_only'),
        }


# Iteration order = priority order. settings.xml exposes
# checkboxes -- we filter the disabled ones out at runtime.
ALL_SOURCE_LANGS = [
    ('en', 'src_english'),
    ('es', 'src_spanish'),
    ('de', 'src_german'),
    ('fr', 'src_french'),
    ('pt', 'src_portuguese'),
]


def _enabled_sources():
    return [code for code, key in ALL_SOURCE_LANGS
            if settings.get_bool(key, code in ('en', 'es'))]
