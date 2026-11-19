# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Settings action: the engine test (loads the engine, runs every enabled provider).

try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None
from resources.lib.ui.dialogs import _title


def _handle_engine_test(_params):
    """Diagnostic for the built-in sources engine. Runs a real search for
    the currently-playing video and reports, per provider, how many results
    came back (or the exact exception), plus the final Hebrew candidates the
    bridge would surface. This is the tool to find out WHY the engine shows
    nothing -- import failure, a provider erroring, or genuinely no Hebrew
    subs for this title."""
    try:
        from resources.lib.fetching import bridge
        from resources.lib.fetching import bridge as fetching_bridge
        from resources.lib.core import settings, video_context
    except Exception as e:
        try:
            xbmcgui.Dialog().ok(_title(), 'Internal error: {0}'.format(e))
        except Exception:
            pass
        return

    lines = []
    on = settings.get_bool('use_builtin_engine', False)
    lines.append('מתג מנוע מובנה: ' + ('דלוק ✓' if on else 'כבוי ✗'))
    if not on:
        lines.append('')
        lines.append('הדלק את "השתמש במנוע המקורות המובנה" ונסה שוב.')
        _engine_test_show(lines)
        return

    info = video_context.current_video_info()
    vd = fetching_bridge.build_video_data(info)
    lines.append('imdb: ' + (vd.get('imdb') or '-')
                 + ' | tmdb: ' + (vd.get('tmdb') or '-'))
    lines.append('כותרת: ' + (vd.get('title') or '-')[:50])
    lines.append('סוג: ' + vd.get('media_type', '-')
                 + ' | עונה/פרק: ' + str(vd.get('season') or '-')
                 + '/' + str(vd.get('episode') or '-'))
    if not (vd.get('imdb') or vd.get('tmdb') or vd.get('title')):
        lines.append('')
        lines.append('אין מטא-דאטה מהנגן. הרץ בזמן שסרט/פרק מתנגן.')

    # Populate the engine's internal settings before importing it (general.py
    # reads max_search_time at module load -> would crash on '').
    try:
        bridge.ensure_engine_settings()
    except Exception:
        pass

    # Try to import the engine -- the single most likely failure point.
    # (Deliberate import probe: `engine` is kept only to show whether the import works.)
    engine = None
    try:
        from resources.lib.fetching import provider_adapter
        engine = provider_adapter.load_engine()
        lines.append('')
        lines.append('טעינת המנוע: ✓')
    except Exception as e:
        lines.append('')
        lines.append('טעינת המנוע נכשלה ✗:')
        lines.append('  ' + repr(e)[:200])
        _engine_test_show(lines)
        return

    # Run each ENABLED provider directly (sequential, so we can attribute
    # results/errors precisely) and report counts.
    providers = [
        ('ktuvit', 'ktuvit'), ('wizdom', 'wizdom'),
        ('opensubtitles', 'opensubtitles'),
        ('yify', 'yify'), ('subsource', 'subsource'),
        ('subscene', 'subscene'), ('bsplayer', 'bsplayer'),
    ]
    lines.append('')
    lines.append('תוצאות לפי ספק:')
    import time as _t
    for setting_id, modname in providers:
        if not settings.get_bool(setting_id, False):
            lines.append('  {0}: (כבוי)'.format(modname))
            continue
        try:
            mod = provider_adapter.load_provider(modname)
        except Exception as e:
            lines.append('  {0}: יבוא נכשל - {1}'.format(
                modname, repr(e)[:80]))
            continue
        try:
            mod.global_var = []
        except Exception:
            pass
        t0 = _t.time()
        try:
            try:
                mod.get_subs(vd)
            except TypeError:
                mod.get_subs(vd, False)
            n = len(getattr(mod, 'global_var', []) or [])
            lines.append('  {0}: {1} תוצאות ({2:.1f}s)'.format(
                modname, n, _t.time() - t0))
        except Exception as e:
            lines.append('  {0}: שגיאה - {1}'.format(
                modname, repr(e)[:80]))

    # Final: what the bridge would surface (Hebrew only, after filtering).
    try:
        cands = bridge.search(info)
        lines.append('')
        lines.append('כתוביות עברית שיוצגו: {0}'.format(len(cands)))
        for c in cands[:6]:
            lines.append('  • ' + (c.get('filename') or '')[:70])
    except Exception as e:
        lines.append('')
        lines.append('bridge.search נכשל: ' + repr(e)[:150])

    _engine_test_show(lines)


def _engine_test_show(lines):
    body = '\n'.join(lines)
    try:
        xbmcgui.Dialog().textviewer('בדיקת מנוע מקורות', body)
    except Exception:
        try:
            xbmcgui.Dialog().ok('בדיקת מנוע מקורות', body)
        except Exception:
            pass
