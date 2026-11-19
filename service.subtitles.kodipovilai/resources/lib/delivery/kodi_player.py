# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Delivery to the player: the download handler, named delivery of a subtitle (unique name so
# Kodi reloads it), the fast first chunk and the fall-back to the next Hebrew subtitle.

import os
import shutil
try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None
from resources.lib.ui.dialogs import safe_log
from resources.lib.delivery.download.state import NEXT, DownloadState, begin
from resources.lib.delivery.download.links import engine_embedded, engine_ai, embedded_ai
from resources.lib.delivery.download.deliver import fast, ai_background, deliver


def encode_selection_b64(selection):
    """Serialize only opaque selection hashes/tokens for a RunScript baton."""
    try:
        import base64
        import json
        safe = {k: (selection or {}).get(k, '')
                for k in ('token', 'link_hash', 'stream_hash')}
        raw = json.dumps(safe, separators=(',', ':')).encode('utf-8')
        return base64.b64encode(raw).decode('ascii')
    except Exception:
        return ''


def decode_selection_b64(value):
    try:
        import base64
        import json
        raw = base64.b64decode((value or '').encode('ascii')).decode('utf-8')
        data = json.loads(raw)
        return {k: (data.get(k) or '')
                for k in ('token', 'link_hash', 'stream_hash')}
    except Exception:
        return {}


DOWNLOAD_STAGES = (
    begin,
    engine_embedded,
    engine_ai,
    embedded_ai,
    fast,
    ai_background,
    deliver,
)


def handle_download(handle, params):
    """User picked one of our entries -- deliver the SRT path."""
    S = DownloadState(handle, params)
    for stage in DOWNLOAD_STAGES:
        result = stage(S)
        if result is not NEXT:
            return result


def _sanitise_sub_name(name):
    """Make a release string safe as a filename and clean as a Kodi
    subtitle label: drop directories/extensions, replace path/reserved
    characters with dots, collapse runs, and cap the length."""
    if not name:
        return ''
    try:
        name = os.path.basename(str(name))
    except Exception:
        name = str(name)
    low = name.lower()
    for ext in ('.srt', '.ssa', '.ass', '.sub', '.vtt', '.txt'):
        if low.endswith(ext):
            name = name[:-len(ext)]
            break
    out = []
    for ch in name:
        if ch.isalnum() or ch in ' .,_-()[]':
            out.append(ch)
        else:
            out.append('.')
    cleaned = ''.join(out)
    while '..' in cleaned:
        cleaned = cleaned.replace('..', '.')
    while '  ' in cleaned:
        cleaned = cleaned.replace('  ', ' ')
    cleaned = cleaned.strip(' .')
    if len(cleaned) > 80:
        cleaned = cleaned[:80].strip(' .')
    return cleaned


def playing_now():
    """True when Kodi still has a video player. Used to decide whether a failed
    canonical swap is dangerous. With a player up, deleting the progressive
    slots can strand the viewer on a removed file; with no player there is
    nothing pointing at them and the cleanup should proceed."""
    try:
        return bool(xbmc.Player().isPlayingVideo())
    except Exception:
        return False


def progressive_slot_path(cache_dir, source_id, slot, release=''):
    """Path for a transient progressive-translation slot file. Kodi
    turns the basename into the subtitle label shown in the picker, so
    when we know the source RELEASE we name the slot after it (e.g.
    'Incendies.2010.720p.BluRay.a.he.srt' -> label 'Incendies...720p.BluRay.a')
    instead of the opaque 'progressive_<hash>_a' the clean addon used to
    show mid-translation. The '.a'/'.b' slot suffix is what lets us
    alternate two paths (so Kodi re-parses and the stream count stays
    capped at 2); it's only visible while translating -- the 'done'
    phase swaps in the clean '<release>.he.srt'. Falls back to the hash
    name when no release is available."""
    try:
        from resources.lib.core import kodi
        rel = kodi.safe_release_filename(release or '')
    except Exception:
        rel = ''
    if rel:
        name = '{0}.{1}.he.srt'.format(rel, slot)
    else:
        name = 'progressive_{0}_{1}.he.srt'.format(source_id, slot)
    return os.path.join(cache_dir, name)


def progressive_cleanup_patterns(source_id, release=''):
    """Glob patterns matching every transient slot file we may have
    written for one translation -- both the hash-named fallback and the
    release-named slots, plus the legacy _v* names from older builds.
    The clean '<release>.he.srt' final is deliberately NOT matched so it
    survives the post-'done' cleanup."""
    pats = [
        'progressive_{0}_v*.he.srt'.format(source_id),
        'progressive_{0}_a.he.srt'.format(source_id),
        'progressive_{0}_b.he.srt'.format(source_id),
    ]
    try:
        from resources.lib.core import kodi
        rel = kodi.safe_release_filename(release or '')
    except Exception:
        rel = ''
    if rel:
        # safe_release_filename deliberately KEEPS brackets, and glob reads
        # '[YTS.MX]' as a character class -- so the literal slot file for a
        # bracketed release matched nothing and was never deleted. Escaping
        # makes the release part literal; the '*' in the hash-named patterns
        # above is ours and stays a wildcard.
        import glob as _g
        rel_lit = _g.escape(rel)
        pats.append('{0}.a.he.srt'.format(rel_lit))
        pats.append('{0}.b.he.srt'.format(rel_lit))
    return pats


def _subtitle_display_name(link, info, path):
    """A human-readable, filesystem-safe release name for the subtitle
    we're about to hand to the player. Kodi turns the file's basename
    into the label shown in the subtitle list, so we want the release
    name (e.g. 'Incendies.2010.720p.BluRay.x264') instead of the generic
    'TempSubtitle'. Engine/passthrough entries carry the real release
    name; AI/pool entries only have an internal cache key, so those use
    the movie/episode title. Returns '' when nothing sane is available
    (caller then falls back to the normal flow)."""
    from resources.lib.core import link_codec
    try:
        payload = link_codec.decode_link(link) or {}
    except Exception:
        payload = {}
    kind = payload.get('type')

    def _title_name():
        title = (info.get('title') or '').strip()
        if not title:
            return ''
        if info.get('is_episode') and info.get('season') and \
                info.get('episode'):
            try:
                return '{0}.S{1:02d}E{2:02d}'.format(
                    title, int(info.get('season')),
                    int(info.get('episode')))
            except (TypeError, ValueError):
                return title
        year = (info.get('year') or '').strip()
        return '{0}.{1}'.format(title, year) if year else title

    candidates = []
    # Engine (Ktuvit human / machine-translated) and passthrough carry
    # the real release name in the picked entry -- the most informative.
    if payload.get('filename'):
        candidates.append(payload['filename'])
    if kind == 'passthrough':
        candidates.append(os.path.basename(path))
    # AI / pool / fallback: the on-disk name is an internal key, so the
    # title is far more useful as a label.
    candidates.append(_title_name())
    candidates.append(os.path.basename(path))

    for c in candidates:
        cleaned = _sanitise_sub_name(c)
        if cleaned:
            return cleaned
    return ''


def deliver_named_subtitle(handle, path, link, info, selection=None):
    """Internet-stream workaround for two Kodi-core subtitle behaviours.

    When the playing file is an HTTP(S) stream (debrid/CDN), Kodi's
    download dialog (GUIDialogSubtitles::OnDownloadComplete) ALWAYS copies
    the returned file to special://temp/TempSubtitle.he.srt -- a single
    shared name derived from the (missing) stream filename, NOT from the
    file we hand back. Two consequences the user hit:
      * every Hebrew sub shows up as the indistinguishable label
        "Hebrew - TempSubtitle (External)" (bug #2);
      * the destination path is identical every time, so repeated
        downloads overwrite the same file and Kodi dedups the external
        stream by path -- only the first one or two ever appear and the
        3rd+ silently vanish even though we report success (bug #3).

    We sidestep BOTH by copying our resolved SRT to a uniquely- and
    MEANINGFULLY-named file (the release name) and handing it straight to
    the player via setSubtitles() -- exactly how the AI/progressive path
    already delivers -- then closing the dialog with no item (the same
    sequence the embedded-Hebrew pick uses). Kodi derives the external
    stream label from the basename, so the user sees e.g.
    "Hebrew - Incendies.2010.720p.BluRay (External)" and each distinct
    release becomes its own persistent stream.

    Returns True if we delivered (endOfDirectory already called), or False
    to let the caller fall back to the normal addDirectoryItem path
    (local playback, no active player, or any failure)."""
    from resources.lib.core import kodi, selection as core_selection
    if xbmc is None:
        return False
    expected = selection or {}

    def _still_current():
        try:
            return bool(core_selection.subtitle_selection_matches(
                expected.get('token') or '',
                expected.get('link_hash') or '',
                expected.get('stream_hash') or ''))
        except Exception:
            return False

    if not _still_current():
        return False
    # Only internet streams hit the TempSubtitle path. Local playback gets
    # a sane video-derived name from Kodi (and may be stored alongside the
    # video), so leave that flow completely untouched.
    try:
        playing = (xbmc.Player().getPlayingFile() or '').strip()
    except Exception:
        playing = ''
    pl = playing.lower()
    if not (pl.startswith('http://') or pl.startswith('https://')):
        return False
    try:
        if not xbmc.Player().isPlayingVideo():
            return False
    except Exception:
        return False
    name = _subtitle_display_name(link, info, path)
    if not name:
        return False
    dest = os.path.join(kodi.cache_dir(), name + '.he.srt')
    try:
        if os.path.abspath(dest) != os.path.abspath(path):
            shutil.copyfile(path, dest)
    except OSError as _e:
        safe_log('named subtitle copy failed: {0}'.format(_e),
                  level='WARNING')
        return False
    if not _still_current():
        return False
    if not core_selection.apply_subtitle_file(
            dest, selection=expected, fix_path=path,
            abandon_on_failure=False):
        safe_log('named subtitle setSubtitles failed or became stale',
                  level='WARNING')
        return False
    # Close the dialog cleanly with no item -- same sequence the embedded
    # pick uses (copied from DarkSubs): the sleep(100) lets the dialog
    # finish closing before endOfDirectory so Kodi doesn't flash a
    # spurious "download failed".
    try:
        xbmc.executebuiltin('Dialog.Close(all,true)')
        xbmcplugin.addDirectoryItems(handle, [], 0)
        xbmc.sleep(100)
        xbmcplugin.endOfDirectory(handle, updateListing=True,
                                  cacheToDisc=True)
    except Exception:
        try:
            xbmcplugin.endOfDirectory(handle)
        except Exception:
            pass
    return True


def try_next_hebrew(failed_link, info, owner_selection=None):
    """The user's picked subtitle failed -- fall back to the next ready Hebrew
    options so they aren't stuck on the failed one. Re-lists candidates and
    resolves the next non-AI, non-embedded Hebrew entries (engine human / pool /
    passthrough) until one downloads. AI (slow) and embedded (no file) are
    skipped here. Returns a path or None. Bounded so it can't hang."""
    from resources.lib import pipeline
    from resources.lib.core import kodi, selection as core_selection, link_codec
    try:
        failed = link_codec.decode_link(failed_link) or {}
    except Exception:
        failed = {}
    # Skip the WHOLE source that just failed -- other subs from it (e.g. more
    # Ktuvit entries) fail identically, so jump straight to the next source.
    failed_sources = set()
    if failed.get('source'):
        failed_sources.add(failed.get('source'))
    try:
        candidates = pipeline.search(info, modal_progress=False)
    except Exception:
        return None
    tried = 0
    owned = dict(owner_selection or {})

    def _owns_current():
        try:
            return bool(core_selection.subtitle_selection_matches(
                owned.get('token') or '', owned.get('link_hash') or '',
                owned.get('stream_hash') or ''))
        except Exception:
            return False

    for c in candidates:
        if tried >= 8:
            break
        if c.get('language') != 'he':
            continue  # only fall back to another HEBREW subtitle
        link2 = c.get('link')
        if not link2:
            continue
        try:
            p2 = link_codec.decode_link(link2) or {}
        except Exception:
            continue
        src = p2.get('source')
        if src and src in failed_sources:
            continue  # this source already failed -- don't retry its siblings
        kind = p2.get('type')
        if kind not in ('engine', 'pool', 'passthrough'):
            continue  # skip AI (slow) + foreign 'engine_ai'
        if kind == 'engine' and p2.get('embedded'):
            continue  # embedded delivers no file
        tried += 1
        # A fallback may take seconds. A manual pick made while it runs owns
        # the player, so stop instead of replacing it with the next fallback.
        if not _owns_current():
            return None
        try:
            # The fallback becomes the real selected candidate. Set it before
            # resolve() so a queued SubSync result is attached to this row.
            core_selection.set_current_subtitle(link2)
            selection = core_selection.current_subtitle_selection(
                expected_link=link2)
            owned = dict(selection or {})
            if not _owns_current():
                return None
            path = pipeline.resolve(
                link2, info, selection=selection)
        except Exception:
            path = None
        if not _owns_current():
            return None
        if (path and os.path.isfile(path)
                and core_selection.subtitle_selection_matches(
                    selection.get('token') or '',
                    selection.get('link_hash') or '',
                    selection.get('stream_hash') or '')):
            try:
                kodi.notify('הכתובית הקודמת נכשלה — נטענה הבאה בתור',
                                  time_ms=4000)
            except Exception:
                pass
            return path, link2, selection
        if src:
            failed_sources.add(src)  # this source failed too -- skip its siblings
    return None


def try_fast_download(handle, link, info, selection=None):
    """Native-picker fast path. Returns True on success
    (endOfDirectory was called). Returns False to mean 'fall
    through to the legacy slow flow' for any case the fast path
    cannot handle (non-AI link, source missing, cache resolution
    failure).

    Pattern: two RunScript invocations with a base64-encoded
    link baton. This invocation (the picker subprocess) writes
    the English fallback SRT, hands it to Kodi via
    endOfDirectory(), then fires a fire-and-forget RunScript
    with action=bg_translate_picker that continues the Hebrew
    translation in its own process. We can't keep working in
    this subprocess because endOfDirectory ends it."""
    import base64
    try:
        from resources.lib.ai_translation.providers import google_flow
        from resources.lib.ai_translation import source_prep
        from resources.lib.sync import delivery_gate
        from resources.lib.ai_translation.repairs import postfix_cleanup
        from resources.lib.core.srt import blocks
        from resources.lib.core import kodi, selection as core_selection, settings, link_codec
        from resources.lib.core.cache import translation_cache as _cache
    except Exception:
        return False

    payload = link_codec.decode_link(link)
    if not payload or payload.get('type') != 'ai':
        # passthrough / pool entries are short -- the existing path
        # handles them fine.
        return False

    source_lang = payload.get('source_lang') or 'en'
    local_source = payload.get('local_path')
    imdb_id = (info.get('imdb_id') or '').strip()
    season  = info.get('season') or ''
    episode = info.get('episode') or ''

    # Fast path briefly shows the SOURCE SRT to the user (until the
    # first Hebrew chunk arrives). English is broadly readable in
    # our user base; Spanish / Portuguese / German / French are not.
    # For non-English sources fall through to the legacy slow path
    # so the user only ever sees Hebrew (after a longer wait).
    # Unlike the DarkSubs path where we hardcode source_lang='en',
    # here the payload carries the actual configured source language.
    if source_lang != 'en':
        return False

    source_id = source_prep.source_id_for_ai(payload)

    # Cache hit fast path: serve cached Hebrew immediately. The
    # cache key here matches what list_candidates uses for its
    # [CACHE] marker, so a [CACHE]-marked entry resolves with
    # zero AI work.
    if source_id:
        try:
            # DELIBERATELY tier-pinned, and deliberately allowed to miss.
            # This branch hands a file straight to Kodi without any of the
            # checks resolve() applies on a cache hit -- the _is_mostly_hebrew
            # self-heal that deletes an empty/source-echoed file, the mtime
            # refresh that keeps a file in use from ageing out, the RTL
            # re-apply, and the one-shot pool backfill. Making it find a
            # translation it used to miss would turn a rare shortcut into the
            # normal path and skip all four.
            #
            # A miss is handled by resolve() in the background. Its validated
            # early return is delivered by the background handler's completion
            # path, with the same ownership and player safeguards as fresh work.
            cached = _cache.translated_path(
                imdb_id, season, episode, source_lang,
                source_id=source_id)
            # A Google Translate fallback that AI can now replace goes to
            # resolve(), which translates it with a live preview -- when
            # resolve() looks in this same plain slot, i.e. with the gender
            # reference off. With it on, resolve() reads the 'ar' slot only,
            # so this file stays the instant hit it was.
            _upgrade = (google_flow.google_cache_upgradeable(cached)
                        and not settings.get_bool('gender_ref_arabic', False))
            if os.path.isfile(cached) and not _upgrade:
                # A cached translation keeps its SOURCE subtitle's timing. Run
                # the delivery through the same release-aware gate as resolve;
                # any correction is a copy, never a rewrite of the AI cache.
                cached_delivery = delivery_gate.sync_hebrew_delivery(
                    info, cached,
                    source_release=(payload.get('release') or ''),
                    embedded_timing=bool(payload.get('embedded')),
                    selection=selection)
                listitem = xbmcgui.ListItem(label=cached_delivery)
                registration_before = (
                    core_selection.subtitle_sync_registration_baseline(
                        cached_delivery, selection=selection))
                added = xbmcplugin.addDirectoryItem(
                    handle=handle, url=cached_delivery,
                    listitem=listitem, isFolder=False)
                xbmcplugin.endOfDirectory(handle)
                registration_confirmed = True
                if added is not False and registration_before is not None:
                    try:
                        registration_confirmed = (
                            core_selection.confirm_subtitle_sync_registration(
                            cached_delivery, selection=selection,
                            before=registration_before))
                    except Exception:
                        registration_confirmed = False
                if added is False or not registration_confirmed:
                    core_selection.abandon_subtitle_selection(selection)
                    try:
                        kodi.notify(
                            'הפעלת הכתובית מהמטמון נכשלה',
                            time_ms=3500)
                    except Exception:
                        pass
                else:
                    try:
                        kodi.notify(
                            'AI: כתוביות מ-cache (תרגום קודם)',
                            time_ms=3000)
                    except Exception:
                        pass
                return True
        except Exception as _e:
            safe_log('fast_download cache check failed: {0}'
                      .format(_e), level='WARNING')

    # No cache hit. Read the source SRT inline (instant -- local file
    # alongside the video or a temp file from another addon).
    src_text = None
    try:
        if local_source and os.path.isfile(local_source):
            with open(local_source, 'r', encoding='utf-8',
                      errors='replace') as f:
                src_text = f.read()
    except Exception as _e:
        safe_log('fast_download source read failed: {0}'
                  .format(_e), level='WARNING')

    if not src_text:
        # Fall through; existing slow path will surface the error.
        return False

    # HI-strip guard mirroring translate.py:518-521. We're delivering
    # the English source as a fallback, and Hebrew chunks will land
    # later from the BG translation -- those run through the same
    # cleaner. If the cleaner ate too much we keep the raw source.
    try:
        cleaned = postfix_cleanup.strip_hi_annotations(src_text)
        if cleaned and blocks.count_entries(cleaned) >= max(
                1, int(blocks.count_entries(src_text) * 0.3)):
            src_text = cleaned
    except Exception:
        pass  # use raw source on cleaner failure

    # Write English fallback to cache_dir under a deterministic name
    # so repeat clicks of the same source overwrite cleanly.
    fallback_id = source_id or 'unknown'
    fallback_path = os.path.join(
        kodi.cache_dir(),
        'fast_picker_fallback_{0}.srt'.format(fallback_id))
    try:
        _tmp = fallback_path + '.aitmp'
        with open(_tmp, 'w', encoding='utf-8') as _f:
            _f.write(src_text)
        os.replace(_tmp, fallback_path)
    except OSError as _e:
        safe_log('fast_download fallback write failed: {0}'
                  .format(_e), level='ERROR')
        return False

    # Hand the English fallback to Kodi -- the user sees subtitles
    # in seconds. endOfDirectory() ends this subprocess; the BG
    # RunScript below picks up the Hebrew translation.
    listitem = xbmcgui.ListItem(label=fallback_path)
    added = xbmcplugin.addDirectoryItem(
        handle=handle, url=fallback_path,
        listitem=listitem, isFolder=False)
    xbmcplugin.endOfDirectory(handle)
    if added is False:
        core_selection.abandon_subtitle_selection(selection)
        return True

    try:
        kodi.notify(
            'AI: כתוביות מוכנות, מתרגם ברקע', time_ms=4000)
    except Exception:
        pass

    # Fire BG translation as a separate RunScript invocation. Once
    # endOfDirectory() returns, this subprocess exits, so we hand
    # the link off in a separate process via RunScript. base64 keeps
    # the JSON-quoted link from getting mangled by RunScript's
    # comma-split parameter parsing.
    try:
        link_b64 = base64.b64encode(
            link.encode('utf-8')).decode('ascii')
        source_id_b64 = base64.b64encode(
            (source_id or '').encode('utf-8')).decode('ascii')
        selection_b64 = encode_selection_b64(selection)
        xbmc.executebuiltin(
            'RunScript(service.subtitles.kodipovilai,'
            'action=bg_translate_picker,'
            'link_b64={0},source_id_b64={1},selection_b64={2})'.format(
                link_b64, source_id_b64, selection_b64))
    except Exception as _e:
        safe_log('fast_download BG fire failed: {0}'.format(_e),
                  level='ERROR')
        # Subtitle was already delivered; BG didn't fire. User
        # gets English permanently for this play. Acceptable
        # degradation -- next play will still try the same fast
        # path and the cache check will short-circuit if BG
        # eventually ran on a different click.

    return True
