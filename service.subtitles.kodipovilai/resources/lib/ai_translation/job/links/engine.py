# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Link type "engine": a subtitle of the built-in sources engine (or an embedded Hebrew track) as it is.

import os
from resources.lib.core import kodi
from resources.lib.ai_translation.progress import status
from resources.lib.sync.delivery_gate import sync_hebrew_delivery
from resources.lib.ai_translation.job.state import NEXT, publish_timing_status, selection_current
# Community subtitle pool (optional, gated by settings, OFF by default).
# Imported defensively: a problem here must never break translation.
try:
    from resources.lib.ai_translation.reuse import pool_contribute
    from resources.lib.fetching import pool_client, pool_harvest
except Exception:
    pool_contribute = pool_client = pool_harvest = None


def engine(S):
    """Link type "engine"."""
    if S.kind == 'engine':
        # Embedded Hebrew pick: just switch Kodi's subtitle stream, there
        # is no file to deliver (mirrors DarkSubs's [LOC] selection).
        if S.payload.get('embedded'):
            try:
                from resources.lib.fetching.embedded import streams
                _elang = S.payload.get('lang') or 'he'
                if (selection_current(S)
                        and streams.select_embedded(
                            S.payload.get('stream_index'), _elang)
                        and selection_current(S)):
                    publish_timing_status(S, 'confirmed', 'embedded')
                    status('הופעל תרגום מובנה' + (
                        ' בעברית' if _elang == 'he' else ''), time_ms=4000)
            except Exception as e:
                kodi.log('resolve embedded select failed: {0}'
                               .format(e), level='WARNING')
            return None
        # A human (or machine-translated) Hebrew subtitle the user
        # picked from the built-in sources engine. Download it directly
        # via the vendored provider -- no AI translation needed, it's
        # already Hebrew. Gated: download() returns None if the engine
        # gate is off.
        try:
            from resources.lib.delivery import rtl
            from resources.lib.fetching.embedded import streams
            path = streams.download(S.payload)
        except Exception as e:
            kodi.log('resolve engine download failed: {0}'.format(e),
                           level='ERROR')
            path = None
        if path and os.path.isfile(path):
            # Ktuvit backup mirror: every HUMAN Ktuvit Hebrew sub a user
            # downloads is pushed once to the pool (kind='ktuvit'), so it
            # survives Ktuvit going offline and loads instantly afterwards.
            # Only genuine Ktuvit human subs (not machine-translated, not other
            # providers); fire-and-forget; gated by pool_share; the server +
            # the ".shared" marker guarantee no duplicate uploads.
            try:
                _src = (S.payload.get('source') or '').strip().lower()
                _lang = S.payload.get('language') or ''
                _pool_source_path = (
                    rtl.source_path_for_delivery(path))
                if (pool_contribute is not None and pool_client.share_enabled()
                        and _src == 'ktuvit' and 'Hebrew' in _lang
                        and 'MachineTranslated' not in _lang
                        and not pool_contribute.was_contributed(_pool_source_path)):
                    _ktext = ''
                    try:
                        with open(_pool_source_path, 'r', encoding='utf-8',
                                  errors='replace') as _kf:
                            _ktext = _kf.read()
                    except Exception:
                        _ktext = ''
                    if _ktext:
                        pool_contribute.contribute_ktuvit(
                            S.info, _ktext,
                            release=(S.payload.get('filename') or ''),
                            marker_path=_pool_source_path,
                            logical_source=(
                                rtl.is_logical_source(
                                    _pool_source_path)))
                        kodi.log(
                            'ktuvit pool mirror: enqueued "{0}"'.format(
                                S.payload.get('filename') or ''), level='INFO')
                else:
                    kodi.log(
                        'ktuvit pool mirror: not enqueued (share={0}, '
                        'src={1}, lang={2}, already_shared={3})'.format(
                            (pool_client.share_enabled() if pool_contribute else False),
                            _src, _lang, (pool_contribute.was_contributed(
                                _pool_source_path)
                                          if pool_contribute else False)),
                        level='INFO')
            except Exception as e:
                kodi.log('ktuvit pool mirror failed: {0}'.format(e),
                               level='WARNING')
            # SubSync S2: when this sub's release doesn't match the playing
            # release, verify -- and if a confident linear map exists, FIX --
            # its timing against a release-matched oracle sub (any language).
            # Fail-open: any problem delivers the file exactly as before.
            if 'Hebrew' in (S.payload.get('language') or ''):
                path = sync_hebrew_delivery(
                    S.info, path,
                    source_release=S.payload.get('filename') or '',
                    selection=S.timing_selection, fallback_link=S.fallback_link,
                    fallback_links=S.fallback_links)
            status('כתוביות עברית מ-{0}'.format(
                S.payload.get('source') or 'מקור'), time_ms=4000)
            return path
        kodi.notify('לא ניתן היה להוריד את הכתובית', time_ms=4000)
        return None
    return NEXT
