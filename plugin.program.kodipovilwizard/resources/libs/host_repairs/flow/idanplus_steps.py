# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
"""Idan Plus repair steps.

Moved verbatim from service.subtitles.kodipovilai/service.py (Phase 3, step 3); only the import prefix differs.
"""
def _maybe_patch_idanplus_channels():
    """Heal + harden Idan Plus (plugin.video.idanplus) channel loading.

    A corrupt/partial displayChannels.json makes idanplus read its channel
    map as a list and crash ("'list' object has no attribute 'items'"), so
    no channel loads or plays and the addon can't self-repair. We move a
    corrupt file aside (idanplus then rebuilds it from the remote list) and,
    best-effort, harden common.py so a future corruption degrades to a
    rebuild instead of a crash. No-op when idanplus isn't installed;
    idempotent + safe every startup."""
    try:
        from resources.libs.host_repairs import idanplus_channels_patcher, kodi_utils
    except Exception:
        return
    try:
        status = idanplus_channels_patcher.ensure_patched()
        if status != 'no_target':
            kodi_utils.log(
                'idanplus_channels_patcher: {0}'.format(status),
                level='INFO')
    except Exception as e:
        try:
            kodi_utils.log(
                'idanplus_channels_patcher run failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


def _maybe_fix_idanplus_youtube_id():
    """Idan Plus hands YouTube the word "watch" instead of a video id.

    A field log showed five YouTube player clients each refusing the same
    request with "This video is unavailable", and the id in every one of them
    was the literal string 'watch'. GetYouTube reads the id out of the URL
    PATH and truncates at '?', which is exactly where it lives in the ordinary
    youtube.com/watch?v= form.

    And the add-on builds that url itself: Kan's mobile API returns a BARE id
    and kan.py wraps it into watch?v= before handing it over, so GetYouTube
    fails to unwrap its own construction. This is not a regression and not
    something Kan changed -- every Kan item of that type has always failed.

    The injected line only fires where the add-on produced something that
    cannot be a YouTube id (eleven characters of YouTube's own charset), which
    is the signature of the failure, so no url it already resolved correctly
    can reach it. And when Idan Plus fixes this itself, the anchor stops
    matching, nothing is touched, and the log says so once per boot -- which
    is the signal to retire the patcher. Two cleverer mechanisms for deciding
    WHY the shape changed were tried and both failed review; the module
    records what they were and how.

    DELIBERATELY NOT behind _skip_pov_patchers(). That switch says to leave
    plugin.video.pov as its author shipped it; this writes to
    plugin.video.idanplus, a different add-on, and gating it on the POV switch
    would silently tie two unrelated decisions together.
    """
    try:
        from resources.libs.host_repairs import idanplus_youtube_id_patcher, kodi_utils
        st = idanplus_youtube_id_patcher.ensure_patched()
        if st in ('unmatched', 'compile_failed', 'write_failed',
                  'revert_failed', 'read_failed'):
            kodi_utils.log(
                'idanplus_youtube_id_patcher: ' + st, level='WARNING')
    except Exception as e:
        try:
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.log(
                'idanplus_youtube_id_patcher failed: {0}'.format(e),
                level='WARNING')
        except Exception:
            pass


def _maybe_fix_idanplus_vod():
    """Kan VOD, the Kan archive and i24NEWS English stopped working.

    All on the providers' side: Kan's Cloudflare answers Kodi 403 on every
    www.kan.org.il page while its mobile-API host serves the same pages, Kan
    moved its player into a JSON config, its archive lobby was redesigned,
    and i24NEWS retired the API host its English catalog used. The module
    header has the evidence for each. Every injection is insert-only and
    reverts byte-for-byte; a file whose shape changed is left alone and
    reported here.

    Not behind _skip_pov_patchers(), for the same reason as the YouTube-id
    fix above: this writes to plugin.video.idanplus, not POV.
    """
    try:
        from resources.libs.host_repairs import idanplus_vod_patcher, kodi_utils
        st = idanplus_vod_patcher.ensure_patched()
        if st in ('unmatched', 'compile_failed', 'write_failed',
                  'revert_failed', 'read_failed'):
            kodi_utils.log('idanplus_vod_patcher: ' + st, level='WARNING')
    except Exception as e:
        try:
            from resources.libs.host_repairs import kodi_utils
            kodi_utils.log('idanplus_vod_patcher failed: {0}'.format(e),
                           level='WARNING')
        except Exception:
            pass
