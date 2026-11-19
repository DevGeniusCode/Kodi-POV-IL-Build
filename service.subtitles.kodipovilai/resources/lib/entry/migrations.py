# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The one-shot start-up migrations (_maybe_*): each runs once per install, marker-gated, and a
# later manual choice of the viewer sticks.

import os
try:
    import xbmc
except ImportError:
    xbmc = None


# Version tag of the "re-apply fix_rtl_punctuation to every cached
# translated SRT" rollout. Translations cached before v0.1.6 didn't
# get the post-processor run on them, and even later caches may
# have slipped through if the regex didn't catch a specific edge.
# Bump this whenever fix_rtl_punctuation gains coverage and we want
# existing caches to benefit without the user manually clearing.
# Bump when fix_rtl_punctuation gains coverage that needs to flow
# through to already-cached translations.
#   v1 -- initial post-processor, simple-text leading-punct only
#   v2 -- HTML-tag-wrapped and dialogue-dash variants
#   v3 -- direction flipped: default is now 'reverse' (move punct
#         to line start) since the original 'auto' direction was
#         based on a wrong assumption about Kodi's BiDi behaviour
#   v4 -- reverse-mode dialogue dash fix: move leading "- " to the
#         logical line end so Kodi renders it on the right side.
#   v5 -- cue-timing repair: bound runaway cue durations. A mistyped timestamp
#         in an AI translation could leave one line frozen on screen for the
#         rest of the episode; this walk is the only mechanism that repairs an
#         ALREADY-cached translation without the user replaying that title.
#   v6: strip Arabic the AI leaked from the gender reference into a Hebrew
#       line -- see srt.strip_leaked_arabic. NOT every file here is ours: the
#       Google Translate fallback saves into this directory too, so the repair
#       is gated per file by srt.may_carry_arabic_leak.
#   v7: explicit RTL-base controls and style-run normalization.
#   v8: restore archived physical-order ellipses, dialogue marks and paired
#       closing quote/bracket punctuation before wrapping for Kodi.
CACHE_RTL_FIX_VERSION = '8'


def _maybe_repair_rtl_cache():
    """One-shot walk of cache/translated/, re-applying the current display and
    TIMING repairs to each file. Catches up translations that got cached before
    a post-processor was in place or before it handled a specific edge case.
    Marker-gated so it only runs once per CACHE_RTL_FIX_VERSION bump -- which is
    why the constant must be bumped whenever a new repair is added here, or
    every existing install skips the backfill forever."""
    try:
        from resources.lib.ai_translation.repairs import postfix_glyphs, postfix_rtl
        from resources.lib.core.srt import timing
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting('_rtl_fix_done', '') == \
                CACHE_RTL_FIX_VERSION:
            return
        translated_dir = os.path.join(
            kodi.cache_dir(), 'translated')
        n_scanned = n_repaired = 0
        if os.path.isdir(translated_dir):
            for fn in os.listdir(translated_dir):
                if not fn.endswith('.srt'):
                    continue
                p = os.path.join(translated_dir, fn)
                n_scanned += 1
                try:
                    with open(p, 'r', encoding='utf-8',
                              errors='replace') as f:
                        content = f.read()
                except OSError:
                    continue
                # cache/translated/ is NOT all our own output: the Google
                # Translate fallback saves here too, marked by a '.google'
                # sidecar. srt.may_carry_arabic_leak is the one place that rule
                # lives -- see it before adding a repair path.
                body = (postfix_glyphs.strip_leaked_arabic(content)
                        if postfix_glyphs.may_carry_arabic_leak(p) else content)
                fixed = timing.clamp_cue_durations(
                    postfix_rtl.fix_rtl_punctuation(
                        body, legacy_engine='auto'))
                if fixed == content:
                    continue
                tmp = p + '.aitmp'
                try:
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(fixed)
                    os.replace(tmp, p)
                    n_repaired += 1
                except OSError:
                    try: os.remove(tmp)
                    except OSError: pass
        settings.set_setting('_rtl_fix_done', CACHE_RTL_FIX_VERSION)
        kodi.log(
            'RTL cache repair v{0}: scanned {1}, repaired {2}'.format(
                CACHE_RTL_FIX_VERSION, n_scanned, n_repaired),
            level='INFO')
    except Exception as e:
        try:
            kodi.log(
                'RTL cache repair failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


def _maybe_default_fast_first_chunk():
    """One-shot: flip `fast_first_chunk` from the old default-off to
    the new default-on for existing users. Gated by a marker so it
    fires once per install; if the user later turns it off manually
    we don't re-flip on subsequent startups."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting(
                '_fast_first_chunk_default_v2', '') == '1':
            return
        # Only flip users currently on the old default 'false' --
        # leaves any explicit 'true' alone.
        if settings.get_setting('fast_first_chunk',
                                  'false') == 'false':
            settings.set_setting('fast_first_chunk', 'true')
            kodi.log(
                'fast_first_chunk flipped to True (default v2 '
                'migration)', level='INFO')
        settings.set_setting('_fast_first_chunk_default_v2', '1')
    except Exception as e:
        try:
            kodi.log(
                'fast_first_chunk migration failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


def _maybe_migrate_embedded_translation_mode():
    """One-shot bridge from the two hidden booleans to the explained mode list."""
    try:
        from resources.lib.core import kodi, settings
        mode = settings.embedded_translation_mode()
        kodi.log(
            'embedded translation mode ready: {0}'.format(mode), level='INFO')
    except Exception as e:
        try:
            kodi.log(
                'embedded mode migration failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


def _maybe_default_pool_on():
    """One-shot: turn the community pool ON (both pull and share) for existing
    users who are still on the old default-off. Gated by a marker so it fires
    once per install; if the user later turns either toggle off manually we
    don't re-enable on subsequent startups. New installs get it on via the
    settings.xml defaults; this covers everyone who installed before the
    default flip."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting('_pool_default_on_v1', '') == '1':
            return
        # Only flip toggles still on the old default 'false'; leave an explicit
        # choice (already 'true') alone.
        for key in ('pool_use', 'pool_share'):
            if settings.get_setting(key, 'false') == 'false':
                settings.set_setting(key, 'true')
        settings.set_setting('_pool_default_on_v1', '1')
        kodi.log('community pool enabled by default (migration v1)',
                       level='INFO')
    except Exception as e:
        try:
            kodi.log('pool default-on migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_force_gender_ref_arabic():
    """One-shot: turn the Arabic-gender-reference setting (gender_ref_arabic) ON
    for EVERYONE -- including users who previously had it off. It tested clean
    (gender accuracy ~27% -> ~90%+, no quality regression, full fallback when no
    Arabic aligns), so we want it on by default for the whole base.

    Unlike the gentle pool migration this forces 'true' unconditionally (not just
    when still on the old default). It is still marker-gated so it fires ONCE:
    if a user deliberately turns it off afterwards, that choice sticks and we
    don't re-enable on the next startup."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting('_gender_ref_on_v1', '') == '1':
            return
        settings.set_setting('gender_ref_arabic', 'true')
        settings.set_setting('_gender_ref_on_v1', '1')
        kodi.log('Arabic gender reference enabled for everyone '
                       '(migration v1)', level='INFO')
    except Exception as e:
        try:
            kodi.log('gender_ref_arabic force-on migration failed: '
                           '{0}'.format(e), level='WARNING')
        except Exception:
            pass


def _maybe_tune_gemini3_defaults():
    """One-shot: move existing users to the validated Gemini 3 translation
    settings -- temperature 1.0 (Google's recommended default; 0.2 was our old
    default and degrades Gemini 3 reasoning) and thinking_level MEDIUM (the old
    'disabled'/0 left it at the expensive HIGH default, which truncates and
    garbles long chunks). Only flips values still on the OLD defaults, so a user
    who deliberately picked something else keeps it. Marker-gated -> fires once;
    a later manual change sticks."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting('_gemini3_tune_v1', '') == '1':
            return
        # temperature: bump 0.2 (old default) -> 1.0; leave any other choice.
        try:
            t = float(settings.get_setting('temperature', '') or '0.2')
        except (TypeError, ValueError):
            t = 0.2
        if abs(t - 0.2) < 0.005:
            settings.set_setting('temperature', '1.0')
        # thinking: '' / '0' / 'disabled' (old default -> HIGH) -> 'medium'.
        th = (settings.get_setting('thinking_budget', '') or '0').strip().lower()
        if th in ('', '0', 'disabled'):
            settings.set_setting('thinking_budget', 'medium')
        settings.set_setting('_gemini3_tune_v1', '1')
        kodi.log('Gemini 3 defaults tuned (temp 1.0 + thinking medium, '
                       'migration v1)', level='INFO')
    except Exception as e:
        try:
            kodi.log('gemini3 tune migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_bump_gemini_model():
    """One-shot: move existing users off the superseded default Gemini models to
    their newer, same-quota successors -- gemini-3.1-flash-lite -> 3.5-flash-lite
    (the free 500/day default) and 3.5-flash / 3.6-flash -> 3.8-flash (the paid
    regular-Flash pick; it was 3.7 until Google superseded that too, and this
    map points at the CURRENT pick rather than at a model the picker no longer
    offers -- landing a device on a dropped id and relying on the next
    migration to move it again is a correctness argument that depends on the
    order two functions happen to be called in). Both are drop-in upgrades (identical free-tier quota,
    better quality), so we rewrite the STORED model once. Only those two exact
    old ids are bumped; any other deliberate choice (3.1-flash, 2.5-*) is left
    alone, and an empty setting is left empty (translate falls back to the new
    default). Marker-gated -> fires once; a later manual pick sticks."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        # v2, NOT v1. The v1 marker is already '1' on every device that took
        # the 3.5 -> 3.6 bump, so reusing it would make this migration a no-op
        # for exactly the users who need it -- the ones already on 3.6. A new
        # id per bump is the only thing that makes a once-only migration
        # repeatable across releases.
        if settings.get_setting('_gemini_model_bump_v2', '') == '1':
            return
        cur = (settings.get_setting('model', '') or '').strip()
        new = {'gemini-3.1-flash-lite': 'gemini-3.5-flash-lite',
               'gemini-3.5-flash': 'gemini-3.8-flash',
               'gemini-3.6-flash': 'gemini-3.8-flash'}.get(cur)
        if new:
            settings.set_setting('model', new)
            kodi.log('Gemini model bumped {0} -> {1} (migration v2)'.format(
                cur, new), level='INFO')
        settings.set_setting('_gemini_model_bump_v2', '1')
    except Exception as e:
        try:
            kodi.log('gemini model bump migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_bump_gemini_model_38():
    """One-shot: move existing users off gemini-3.7-flash to gemini-3.8-flash.

    Google shipped 3.8 Flash as the current regular-Flash model and reclassified
    3.7 as "previous-generation"; the two sit on identical rate limits in every
    table on Google's own page (same TPM, same TPD, same free RPD), so this is a
    drop-in upgrade exactly like the 3.5/3.6 -> 3.7 bump before it. 3.7 keeps
    working, so nothing here is urgent -- but the picker no longer offers it,
    and leaving a stored id the list cannot show is how a settings screen ends
    up displaying a blank model.

    A NEW marker id, never a reused one: `_gemini_model_bump_v2` is already '1'
    on every device that took the previous bump, so reusing it would no-op this
    migration for precisely the users who need it. That mistake is written up in
    _maybe_bump_gemini_model() above; this is the same rule applied again.

    RETIRED, not "everything newer". The set below is every regular-Flash id
    the picker no longer offers. 3.6 and 3.5 Flash are in it because they were
    v2's job and v2 only fires once: a device that already ran v2 has left them,
    but listing them here means this migration is correct on its own rather than
    correct only because it happens to run after v2 in the same boot. Ordering
    is a fact about one file; a complete set is a fact about the migration.

    Every choice the picker still shows -- 3.5-flash-lite, 3.1-flash, either
    2.5 -- is a deliberate pick and is left alone, and an empty setting stays
    empty so translate.py's own default applies."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting('_gemini_model_bump_v3', '') == '1':
            return
        retired = ('gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-3.5-flash')
        cur = (settings.get_setting('model', '') or '').strip()
        if cur in retired:
            settings.set_setting('model', 'gemini-3.8-flash')
            kodi.log('Gemini model bumped {0} -> gemini-3.8-flash '
                           '(migration v3)'.format(cur), level='INFO')
        settings.set_setting('_gemini_model_bump_v3', '1')
    except Exception as e:
        try:
            kodi.log('gemini 3.8 model bump migration failed: {0}'
                           .format(e), level='WARNING')
        except Exception:
            pass


def _maybe_retire_gemini_31_flash():
    """One-shot: a stored 'gemini-3.1-flash' becomes the default model.

    Google has no model of that id (HTTP 404 for every key, Nov 2026), and
    0.3.78 took it out of the picker; a stored id the list cannot show is a
    blank model row (see _maybe_bump_gemini_model_38). translate.py falls
    back to the default model on a 404 anyway, so this only makes the setting
    say what is already happening. The two 2.5 ids stay: Google still serves
    them to older keys. A new marker, as every bump before it."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting('_gemini_model_bump_v4', '') == '1':
            return
        cur = (settings.get_setting('model', '') or '').strip()
        if cur == 'gemini-3.1-flash':
            settings.set_setting('model', 'gemini-3.5-flash-lite')
            kodi.log('Gemini model gemini-3.1-flash -> '
                           'gemini-3.5-flash-lite: Google no longer has it '
                           '(migration v4)', level='INFO')
        settings.set_setting('_gemini_model_bump_v4', '1')
    except Exception as e:
        try:
            kodi.log('gemini 3.1 retire migration failed: {0}'
                           .format(e), level='WARNING')
        except Exception:
            pass


def _maybe_lower_chunk_lines():
    """One-shot: move existing users to the smaller 50-line translation chunk.
    Live testing showed big chunks (100+) of graphically-explicit dialogue trip
    Google's prompt-level PROHIBITED_CONTENT block, while 50-line chunks stay
    under the threshold and translate cleanly -- with NO loss of gender accuracy
    or quality (the Arabic gender oracle is per-entry and the cast/context carry
    the rest). Only lowers values still at the OLD defaults (>=100 -> 50); a user
    who deliberately picked something smaller keeps it. Marker-gated -> once."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting('_chunk_lines_50_v1', '') == '1':
            return
        try:
            cur = int(settings.get_setting('chunk_lines', '') or '100')
        except (TypeError, ValueError):
            cur = 100
        if cur >= 100:
            settings.set_setting('chunk_lines', '50')
            kodi.log('chunk_lines lowered {0} -> 50 (block-avoidance '
                           'migration v1)'.format(cur), level='INFO')
        settings.set_setting('_chunk_lines_50_v1', '1')
    except Exception as e:
        try:
            kodi.log('chunk_lines migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass




def _maybe_force_pool_share():
    """One-shot rollout: turn community-pool SHARING on for EVERYONE, to grow the
    shared Hebrew pool as fast as possible. With pool_share on, every human
    Ktuvit Hebrew sub for a played title is mirrored to the pool in the
    background (the harvest), and AI translations are shared too -- so the pool
    fills for all users. Force-enabled once via a fresh marker (overriding a
    prior opt-out); a later MANUAL opt-out AFTER this run sticks. New installs
    already default on via settings.xml."""
    try:
        from resources.lib.core import kodi, settings
        if settings.get_setting('_pool_share_force_v1', '') == '1':
            return
        settings.set_setting('pool_share', 'true')
        settings.set_setting('_pool_share_force_v1', '1')
        kodi.log('pool_share force-enabled for everyone (rollout v1)',
                       level='INFO')
    except Exception as e:
        try:
            kodi.log('pool_share force migration failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_default_builtin_engine():
    """One-shot rollout: move EVERYONE from DarkSubs to MoranSubs's own built-in
    engine. Turns use_builtin_engine ON exactly once (and seeds engine_autosub
    ON so auto-search-and-apply works like DarkSubs did). Marker-gated, so a
    later manual opt-out STICKS -- if the user turns the engine (or autosub) off
    afterwards we never force it back on, on this or any future startup.

    Must run BEFORE _maybe_set_default_subtitle_service() and the _engine_on
    read in main(), so the rest of THIS startup already treats the engine as on
    (MoranSubs default)."""
    try:
        from resources.lib.core import kodi, settings
    except Exception:
        return
    try:
        if settings.get_setting('_builtin_engine_rollout_v2', '') == '1':
            return
        # Flip the master engine toggle on (covers users still on the old
        # default 'false', AND users where it drifted off so DarkSubs came back
        # with no translation -- re-forced once via the v2 marker).
        if settings.get_setting('use_builtin_engine', 'false') != 'true':
            settings.set_setting('use_builtin_engine', 'true')
        # Auto-search & apply on play, like DarkSubs's autosub. Defaults to
        # 'true' already (and was hidden while the engine was off), so this is
        # normally a no-op; flip only if a tester explicitly turned it off.
        if settings.get_setting('engine_autosub', 'true') == 'false':
            settings.set_setting('engine_autosub', 'true')
        settings.set_setting('_builtin_engine_rollout_v2', '1')
        kodi.log('built-in engine enabled for everyone (rollout v2)',
                       level='INFO')
    except Exception as e:
        try:
            kodi.log('builtin engine rollout failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass


def _maybe_set_default_subtitle_service():
    """When the engine is on, make MoranSubs the default subtitle service for
    movies + TV, so Kodi auto-runs it and pre-selects it when the subtitle
    dialog opens (the services list order itself is fixed by Kodi, but the
    default is what opens/searches first). Only writes on a mismatch; only
    when the engine is on (we don't override the user's choice otherwise)."""
    from resources.lib.entry.service import ADDON_ID
    if xbmc is None:
        return
    try:
        from resources.lib.core import settings
        if not settings.get_bool('use_builtin_engine', False):
            return
    except Exception:
        return
    try:
        import json as _json
        for sid in ('subtitles.tv', 'subtitles.movie'):
            getq = _json.dumps({
                'jsonrpc': '2.0', 'id': 1,
                'method': 'Settings.GetSettingValue',
                'params': {'setting': sid},
            })
            cur = (_json.loads(xbmc.executeJSONRPC(getq) or '{}')
                   .get('result') or {}).get('value')
            if cur == ADDON_ID:
                continue
            setq = _json.dumps({
                'jsonrpc': '2.0', 'id': 1,
                'method': 'Settings.SetSettingValue',
                'params': {'setting': sid, 'value': ADDON_ID},
            })
            xbmc.executeJSONRPC(setq)
        xbmc.log('[{0}] set as default subtitle service (engine on)'
                 .format(ADDON_ID), level=xbmc.LOGINFO)
    except Exception:
        pass

def _maybe_seed_hebrew_subtitle_prefs():
    """One-shot: make Kodi's subtitle-language settings match the intent of
    installing a HEBREW subtitles addon. On foreign builds / clean Kodi the
    defaults point elsewhere, and the addon's language gate then disables
    itself ("preferred subtitle language is not Hebrew") -- the user installed
    a Hebrew subtitle addon and gets NOTHING (field log, Arctic Zephyr user).
    Adds Hebrew to "languages to download subtitles for" (keeping the user's
    other entries) and sets the preferred subtitle language to Hebrew when it
    currently names a different concrete language. Marker-gated: runs once,
    so a later manual change by the user STICKS."""
    if xbmc is None:
        return
    try:
        from resources.lib.core import kodi, settings
        if settings.get_setting('_he_subs_prefs_v1', '') == '1':
            return
    except Exception:
        return
    import json as _json

    def _get(sid):
        try:
            q = _json.dumps({'jsonrpc': '2.0', 'id': 1,
                             'method': 'Settings.GetSettingValue',
                             'params': {'setting': sid}})
            return (_json.loads(xbmc.executeJSONRPC(q) or '{}')
                    .get('result') or {}).get('value')
        except Exception:
            return None

    def _set(sid, value):
        try:
            q = _json.dumps({'jsonrpc': '2.0', 'id': 1,
                             'method': 'Settings.SetSettingValue',
                             'params': {'setting': sid, 'value': value}})
            xbmc.executeJSONRPC(q)
        except Exception:
            pass

    try:
        def _is_he(v):
            return str(v).strip().lower() in (
                'he', 'iw', 'heb', 'hebrew', 'עברית')
        dl = _get('subtitles.languages')
        pref = _get('locale.subtitlelanguage')
        if dl is None and pref is None:
            # JSON-RPC gave us nothing (transient failure at service start?).
            # Do NOT write the marker -- retry on the next service start, so
            # one bad boot can't leave the user unseeded forever.
            return
        if isinstance(dl, str):
            # Some builds return the list as a CSV string; seed in kind.
            parts = [p for p in dl.replace(';', ',').split(',') if p.strip()]
            if not any(_is_he(x) for x in parts):
                _set('subtitles.languages',
                     ','.join(parts + ['Hebrew']) if parts else 'Hebrew')
        elif isinstance(dl, list) and not any(_is_he(x) for x in dl):
            _set('subtitles.languages', dl + ['Hebrew'])
        elif dl in (None, []):
            _set('subtitles.languages', ['Hebrew'])
        specials = ('', 'none', 'forced_only', 'forcedonly', 'original',
                    'default', 'mediadefault')
        if (isinstance(pref, str)
                and pref.strip().lower() not in specials
                and not _is_he(pref)):
            _set('locale.subtitlelanguage', 'Hebrew')
        settings.set_setting('_he_subs_prefs_v1', '1')
        kodi.log('Hebrew subtitle preferences seeded (one-shot)',
                       level='INFO')
    except Exception:
        pass
