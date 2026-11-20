# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# MoranSubs subtitle-chooser window (pyxbmct), opened from the player's
# "בחר כתוביות" button. It lists the SAME Hebrew candidates the search dialog
# offers -- WITH a Hebrew match % per real Hebrew sub -- and applies the picked
# one immediately, staying open so the user can try another. This is the
# MoranSubs replacement for DarkSubs's MySubs window (DarkSubs is disabled once
# the built-in engine is on, which left that button dead).
#
# pyxbmct is a programmatic (code-built) window, so it renders the same on EVERY
# skin -- no per-skin XML needed. Every entry point is guarded; on any failure
# show() returns False so the skin button can fall back to Kodi's native
# subtitle selector instead of a dead/black-screen button.

import os

from resources.lib.ui import chooser_rows

try:
    import xbmc
    import xbmcgui
except Exception:
    xbmc = xbmcgui = None

ADDON_ID = 'service.subtitles.kodipovilai'


def _title():
    """The brand title of the add-on's windows (sub_utils.DIALOG_TITLE)."""
    try:
        from resources.lib.core.kodi import DIALOG_TITLE
        return DIALOG_TITLE
    except Exception:
        return ADDON_ID


def _log(msg, level='INFO'):
    try:
        from resources.lib.core import kodi
        kodi.log('subs_chooser: ' + msg, level=level)
    except Exception:
        pass


# Legend: plain text + colours only (no glyphs that some skin fonts can't draw).
_LEGEND = ('[COLOR FFB8D6E8]דגל = שפת הכתובית[/COLOR]   '
           '[COLOR FFFFD700]זהב/ירוק = עברית מוכנה[/COLOR]   '
           '[COLOR FFE0A040]כתום = שפה זרה (תתורגם)[/COLOR]')


def _row_item(c, info, xbmcgui):
    """Build a list row with a REAL flag icon (resources/media/flags) for the
    subtitle's language -- images render on every skin, unlike the old box
    glyphs. Foreign-language rows also get a Hebrew language tag in the text so
    they're unmistakable. Match % goes to the right column; the row is tinted by
    kind/quality (gold/green = ready Hebrew, orange = foreign-to-be-translated,
    magenta = currently applied)."""
    import re as _re
    lang = (c.get('language') or '').strip()
    name = (c.get('filename') or '').strip()
    current = name.startswith('» נוכחית')
    # match % already embedded in the label (list_candidates computed it).
    pct = None
    m = _re.search(r'(\d{1,3})%', name)
    if m:
        try:
            pct = int(m.group(1))
        except ValueError:
            pct = None

    # Keep the FULL label text INCLUDING its inline match % -- do NOT move the %
    # to label2: pyxbmct's ControlList doesn't reliably render label2, which made
    # the % disappear on device. Only drop the "» נוכחית ·" prefix (the magenta
    # colour already marks the current sub).
    disp = name
    if current and '· ' in disp:
        disp = disp.split('· ', 1)[1]
    sync_label = (c.get('_subsync_label') or '').strip()
    if sync_label:
        disp = '{0} · {1}'.format(sync_label, disp)

    code = chooser_rows.norm_lang(lang)
    is_he = (code == 'he') or (lang.lower() in ('he', 'iw', 'heb'))
    # Foreign (non-Hebrew) row -> prepend a clear Hebrew language tag.
    he_name = chooser_rows.LANG_HE.get(code or lang[:2].lower())
    if not is_he and he_name:
        disp = '[{0}] {1}'.format(he_name, disp)

    if current:
        col = 'FFFF00FE'                    # magenta -- applied now
    elif is_he:
        if pct is not None and pct >= 80:
            col = 'FFFFD700'                # gold -- strong match / 101%
        elif pct is not None and pct >= 50:
            col = 'FF7BE38C'                # green
        else:
            col = 'FFBFE9CF'               # pale green -- ready Hebrew
    elif lang:
        col = 'FFF0A93A'                    # amber -- foreign (will translate)
    else:
        col = 'FFFFFFFF'

    # Make the match % pop: bright white, inside the row's colour.
    disp = _re.sub(r'(\d{1,3}%)', '[COLOR FFFFFFFF]\\1[/COLOR]', disp)
    label = '[B][COLOR {0}]{1}[/COLOR][/B]'.format(col, disp)
    flag = chooser_rows.flag_path('he' if is_he else lang)
    try:
        li = xbmcgui.ListItem(label)
        if flag:
            li.setArt({'icon': flag, 'thumb': flag})
        return li
    except Exception:
        # Fall back to a plain coloured string row if ListItem isn't available.
        return label


def _start_ai_apply(link, info):
    """Deliver an AI translation EXACTLY like the search dialog's fast path:
    show the English source immediately (when the source is English -- readable
    while it cooks), then translate to Hebrew in a SEPARATE background process
    (bg_translate_picker) that swaps the Hebrew in progressively.

    We fire a RunScript rather than a thread on purpose: this chooser runs in a
    short-lived script process that ends when the window closes, so a worker
    thread would be killed mid-translation. The background RunScript runs in its
    own process and survives. Fully guarded."""
    try:
        import base64
        from resources.lib.ai_translation import source_prep
        from resources.lib.fetching.embedded import streams
        from resources.lib.core import kodi, selection, link_codec
        try:
            timing_selection = selection.current_subtitle_selection(
                expected_link=link)
        except Exception:
            timing_selection = {}

        def _selection_current():
            try:
                return bool(selection.subtitle_selection_matches(
                    timing_selection.get('token') or '',
                    timing_selection.get('link_hash') or '',
                    timing_selection.get('stream_hash') or ''))
            except Exception:
                return False

        if not _selection_current():
            return
        payload = link_codec.decode_link(link) or {}
        ai_link = link
        ai_payload = payload
        # engine_ai: download the foreign SOURCE sub now, then continue as 'ai'
        # (same first step the search dialog does).
        if payload.get('type') == 'engine_ai':
            try:
                kodi.notify('AI: מוריד את כתובית המקור...', time_ms=2500)
                src_path = streams.download(payload)
            except Exception:
                src_path = None
            if not (src_path and os.path.isfile(src_path)):
                try:
                    kodi.notify('AI: לא ניתן להוריד את כתובית המקור',
                                      time_ms=4000)
                except Exception:
                    pass
                return
            if not _selection_current():
                return
            ai_payload = {
                'type': 'ai',
                'source_lang': payload.get('src_lang') or 'en',
                'local_path': src_path,
                # carry the source release so the delivered file + pool upload
                # show the full release name instead of a hash / Title.Year.
                'release': payload.get('filename') or '',
                'force_ai': True,
            }
            ai_link = link_codec.encode_link(ai_payload)
        # embedded_ai: do NOT extract here -- a scattered remux takes minutes and
        # would freeze this short-lived chooser process. Hand the embedded_ai
        # link straight to the background translator below (ai_link / ai_payload
        # stay as the embedded_ai link set above): its resolve() extracts, with a
        # corner progress bar, THEN translates.
        elif payload.get('type') == 'embedded_ai':
            # Show the embedded SOURCE track now (native, instant + already synced
            # to the video) so the user sees it while the Hebrew cooks, instead of
            # the stale sub they picked embedded to replace. Best-effort.
            try:
                if not _selection_current():
                    return
                _si = payload.get('stream_index')
                if _si is not None:
                    streams.select_embedded(
                        _si, lang=payload.get('src_lang') or 'en')
            except Exception:
                pass
        # English source -> show it immediately (broadly readable); other
        # languages get no intermediate, exactly like the fast path.
        src_lang = ai_payload.get('source_lang') or 'en'
        local_src = ai_payload.get('local_path')
        if src_lang == 'en' and local_src and os.path.isfile(local_src):
            try:
                if not _selection_current():
                    return
                selection.apply_subtitle_file(
                    local_src, selection=timing_selection,
                    abandon_on_failure=False)
            except Exception:
                pass
        # Hand the Hebrew translation to a background process that swaps it in
        # progressively (writes versioned .srt + setSubtitles), same handler the
        # search dialog's fast path uses.
        try:
            sid = source_prep.source_id_for_ai(ai_payload) or ''
        except Exception:
            sid = ''
        try:
            if not _selection_current():
                return
            import json
            lk = base64.b64encode(ai_link.encode('utf-8')).decode('ascii')
            sd = base64.b64encode(sid.encode('utf-8')).decode('ascii')
            safe_selection = {
                k: (timing_selection or {}).get(k, '')
                for k in ('token', 'link_hash', 'stream_hash')}
            ss = base64.b64encode(json.dumps(
                safe_selection, separators=(',', ':')).encode(
                    'utf-8')).decode('ascii')
            xbmc.executebuiltin(
                'RunScript(service.subtitles.kodipovilai,'
                'action=bg_translate_picker,link_b64={0},source_id_b64={1},'
                'selection_b64={2})'.format(lk, sd, ss))
            # (the chooser already recorded the original picked link as the
            # current sub, so it's marked "» נוכחית" on the next open.)
            kodi.notify('AI: מתרגם לעברית ברקע', time_ms=3000)
        except Exception as _e:
            _log('ai bg fire failed: {0}'.format(_e), level='WARNING')
    except Exception as e:
        _log('ai apply failed: {0}'.format(e), level='WARNING')


def _show_pyxbmct():
    """Legacy pyxbmct chooser -- kept as a fallback if the rich WindowXMLDialog
    can't be created on a given device/skin."""
    """Open the chooser for whatever is playing. Returns True if the window was
    shown, False on any failure (so the caller can fall back)."""
    if xbmc is None:
        return False
    try:
        if not xbmc.Player().isPlayingVideo():
            return False
    except Exception:
        return False
    try:
        from resources.lib import pipeline
        from resources.lib.core import kodi, selection, video_context
    except Exception:
        return False

    info = video_context.current_video_info()
    try:
        cands = pipeline.search(info, modal_progress=False) or []
    except Exception as e:
        _log('list_candidates failed: {0}'.format(e), level='WARNING')
        cands = []
    try:
        from resources.lib.sync import candidates
        cands = candidates.rank_picker_candidates(info, cands, cached_only=True)
    except Exception as e:
        _log('candidate timing-rank skipped: {0}'.format(e), level='DEBUG')
    items = [c for c in cands if c.get('link') and c.get('filename')]
    if not items:
        try:
            kodi.notify('לא נמצאו כתוביות לכותר הזה', time_ms=3500)
        except Exception:
            pass
        return False

    try:
        from resources.lib import pyxbmct
    except Exception as e:
        _log('pyxbmct import failed: {0}'.format(e), level='WARNING')
        return False

    class Chooser(pyxbmct.AddonDialogWindow):
        def __init__(self):
            super(Chooser, self).__init__(_title() + ' — בחר כתוביות')
            self.info = info
            self.items = items
            self.setGeometry(950, 620, 9, 2)
            head = video_context.release_name(info, 'chooser') or ''
            # Header (A): "נמצאו N כתוביות" + the title/release, with a compact
            # colour/symbol legend underneath so the row glyphs are decodable.
            n_found = len(self.items)
            htxt = '[B][COLOR deepskyblue]נמצאו {0} כתוביות[/COLOR][/B]'.format(
                n_found)
            if head:
                htxt += '[CR][COLOR FFB8D6E8]{0}[/COLOR]'.format(head)
            htxt += '[CR][COLOR FF9AA0A6]{0}[/COLOR]'.format(_LEGEND)
            self.header = pyxbmct.Label(htxt)
            self.placeControl(self.header, 0, 0, rowspan=2, columnspan=2)
            # Taller rows with a flag icon column on the left (the subtitle's
            # language); the match % stays inline in the row text (bright white).
            self.lst = pyxbmct.List(font='font13', _itemHeight=44, _space=2,
                                    _imageWidth=36, _imageHeight=36,
                                    _itemTextXOffset=12)
            self.placeControl(self.lst, 2, 0, rowspan=6, columnspan=2)
            self.lst.addItems(
                [_row_item(c, info, xbmcgui) for c in self.items])
            self.connect(self.lst, self.on_pick)
            # Full native Kodi subtitle search/download -- for users who want the
            # regular "download subtitles" flow, not just this picker. Skin-
            # agnostic (the OSD layout is untouched, so no button collision).
            self.dl = pyxbmct.Button('[B]הורדת כתוביות (Kodi)[/B]')
            self.placeControl(self.dl, 8, 0)
            self.connect(self.dl, self.on_download)
            self.btn = pyxbmct.Button('[B]סגור[/B]')
            self.placeControl(self.btn, 8, 1)
            self.connect(self.btn, self.close)
            self.connect(pyxbmct.ACTION_NAV_BACK, self.close)
            # Up/Down CYCLE within the subtitle list (wrap-around): the list's
            # own up/down navigation points back at itself so focus never leaves
            # it on a plain move, and onAction() does the actual top<->bottom
            # wrap. The action buttons are reached with Left/Right instead.
            self.lst.controlUp(self.lst)
            self.lst.controlDown(self.lst)
            self.lst.controlLeft(self.dl)
            self.lst.controlRight(self.btn)
            self.dl.controlUp(self.lst)
            self.dl.controlDown(self.lst)
            self.dl.controlRight(self.btn)
            self.dl.controlLeft(self.lst)
            self.btn.controlUp(self.lst)
            self.btn.controlDown(self.lst)
            self.btn.controlLeft(self.dl)
            self.btn.controlRight(self.lst)
            self._last_pos = 0
            self.setFocus(self.lst)

        def onAction(self, action):
            """Wrap-around for the subtitle list: Down on the last row jumps to
            the first, Up on the first row jumps to the last. Kodi runs the
            window's default navigation BEFORE this callback, so by here the
            selection has already settled; a row that was on a boundary and
            stayed there (same position as last time) means the user tried to
            step past the edge -- so we wrap. Falls through to pyxbmct for
            everything else (e.g. Back -> close)."""
            try:
                aid = action.getId()
                if (aid in (pyxbmct.ACTION_MOVE_DOWN, pyxbmct.ACTION_MOVE_UP)
                        and self.getFocusId() == self.lst.getId()):
                    n = self.lst.size()
                    cur = self.lst.getSelectedPosition()
                    if n > 1:
                        if (aid == pyxbmct.ACTION_MOVE_DOWN
                                and cur == n - 1 and self._last_pos == n - 1):
                            self.lst.selectItem(0)
                        elif (aid == pyxbmct.ACTION_MOVE_UP
                              and cur == 0 and self._last_pos == 0):
                            self.lst.selectItem(n - 1)
                    self._last_pos = self.lst.getSelectedPosition()
            except Exception:
                pass
            super(Chooser, self).onAction(action)

        def on_download(self):
            """Close the picker and open Kodi's native subtitle search/download
            dialog -- the same dialog the OSD's "download subtitle" reaches."""
            try:
                self.close()
            except Exception:
                pass
            try:
                xbmc.executebuiltin('ActivateWindow(SubtitleSearch)')
            except Exception as e:
                _log('open SubtitleSearch failed: {0}'.format(e),
                     level='WARNING')

        def _set_head(self, text):
            try:
                self.header.setLabel(text)
            except Exception:
                pass

        def on_pick(self):
            try:
                i = self.lst.getSelectedPosition()
            except Exception:
                return
            if i is None or i < 0 or i >= len(self.items):
                return
            c = self.items[i]
            self._set_head('[B]מוריד...[/B]')
            try:
                from resources.lib import pipeline
                from resources.lib.ai_translation.proofread import gp_jobs
                from resources.lib.core import link_codec
                link = c.get('link') or ''
                # Remember this as the applied sub upfront (the ORIGINAL link, so
                # it matches this same candidate next time) -- so reopening the
                # chooser shows it at the top marked "» נוכחית".
                try:
                    selection.set_current_subtitle(link, renew=True)
                except Exception:
                    pass
                try:
                    timing_selection = selection.current_subtitle_selection(
                        expected_link=link)
                except Exception:
                    timing_selection = {}

                def _still_current():
                    try:
                        return bool(selection.subtitle_selection_matches(
                            timing_selection.get('token') or '',
                            timing_selection.get('link_hash') or '',
                            timing_selection.get('stream_hash') or ''))
                    except Exception:
                        return False
                payload = link_codec.decode_link(link) or {}
                kind = payload.get('type')
                # Embedded pick: switch Kodi's stream, no file to deliver.
                if kind == 'engine' and payload.get('embedded'):
                    try:
                        from resources.lib.fetching.embedded import streams
                        if (not _still_current()
                                or not streams.select_embedded(
                                    payload.get('stream_index'),
                                    payload.get('lang') or 'he')
                                or not _still_current()):
                            self._set_head(
                                '[B][COLOR red]הפעלת התרגום המובנה נכשלה'
                                '[/COLOR][/B]')
                            selection.abandon_subtitle_selection(
                                timing_selection)
                            return
                        selection.set_subtitle_sync_status(
                            'confirmed', source='embedded',
                            selection_token=(
                                timing_selection.get('token') or ''),
                            link_hash=(
                                timing_selection.get('link_hash') or ''),
                            stream_hash=(
                                timing_selection.get('stream_hash') or ''))
                    except Exception as _e:
                        _log('embedded select failed: {0}'.format(_e),
                             level='WARNING')
                        self._set_head(
                            '[B][COLOR red]הפעלת התרגום המובנה נכשלה'
                            '[/COLOR][/B]')
                        selection.abandon_subtitle_selection(
                            timing_selection)
                        return
                    self._set_head('[B][COLOR lightgreen]הופעל תרגום '
                                   'מובנה[/COLOR][/B]')
                    return
                # AI translation (English/Spanish/German/... -> Hebrew) takes
                # 1-2 minutes, so it must NOT block the window. CLOSE the window
                # and translate in the background, applying when ready, with a
                # progress banner -- exactly like picking it from the search.
                if kind in ('engine_ai', 'ai', 'embedded_ai'):
                    self.close()
                    _start_ai_apply(link, self.info)
                    return
                # Ready Hebrew subs (passthrough / pool / human engine): quick
                # download -> apply and CLOSE the window. On failure, keep it
                # open so the user can pick another.
                path = pipeline.resolve(
                    link, self.info, selection=timing_selection)
                if (path and os.path.isfile(path) and _still_current()):
                    if selection.apply_subtitle_file(
                            path, selection=timing_selection):
                        self.close()
                        # a pooled AI translation: its gender proofread
                        gp_jobs.proofread_later(link, timing_selection)
                    elif not _still_current():
                        self.close()
                    else:
                        self._set_head(
                            '[B][COLOR red]הפעלת הכתובית נכשלה, נסה אחרת'
                            '[/COLOR][/B]')
                elif path and not _still_current():
                    self.close()
                else:
                    self._set_head('[B][COLOR red]ההורדה נכשלה, נסה '
                                   'אחרת[/COLOR][/B]')
            except Exception as e:
                _log('on_pick failed: {0}'.format(e), level='WARNING')
                self._set_head('[B][COLOR red]שגיאה[/COLOR][/B]')

    # Flag that the chooser is open on a shared window property, so the skin can
    # hide overlays (e.g. FENtastic's pause-info panel) while it's up. Kodi does
    # NOT report a pyxbmct script window via System.HasActiveModalDialog, so a
    # property is the reliable signal. Always cleared in finally.
    try:
        try:
            xbmcgui.Window(10000).setProperty('MoranSubsChooserOpen', '1')
        except Exception:
            pass
        w = Chooser()
        w.doModal()
        del w
        return True
    except Exception as e:
        _log('window failed: {0}'.format(e), level='WARNING')
        return False
    finally:
        try:
            xbmcgui.Window(10000).clearProperty('MoranSubsChooserOpen')
        except Exception:
            pass


# ---------------- rich WindowXMLDialog chooser (Tal-style, all skins) --------

def _deliver_pick(c, info, close_cb):
    """Download/apply the picked candidate (shared by the XML window). Mirrors the
    pyxbmct on_pick: embedded -> switch stream; AI -> close + background
    translate; ready Hebrew -> resolve + apply. close_cb() closes the window."""
    try:
        from resources.lib import pipeline
        from resources.lib.ai_translation.proofread import gp_jobs
        from resources.lib.core import kodi, selection, link_codec
        link = c.get('link') or ''
        try:
            selection.set_current_subtitle(link, renew=True)
        except Exception:
            pass
        try:
            timing_selection = selection.current_subtitle_selection(
                expected_link=link)
        except Exception:
            timing_selection = {}

        def _still_current():
            try:
                return bool(selection.subtitle_selection_matches(
                    timing_selection.get('token') or '',
                    timing_selection.get('link_hash') or '',
                    timing_selection.get('stream_hash') or ''))
            except Exception:
                return False
        payload = link_codec.decode_link(link) or {}
        kind = payload.get('type')
        if kind == 'engine' and payload.get('embedded'):
            try:
                from resources.lib.fetching.embedded import streams
                selected = bool(
                    _still_current()
                    and streams.select_embedded(
                        payload.get('stream_index'),
                        payload.get('lang') or 'he')
                    and _still_current())
                if selected:
                    selection.set_subtitle_sync_status(
                        'confirmed', source='embedded',
                        selection_token=(timing_selection.get('token') or ''),
                        link_hash=(timing_selection.get('link_hash') or ''),
                        stream_hash=(timing_selection.get('stream_hash') or ''))
                    kodi.notify('הופעל תרגום מובנה', time_ms=2500)
                else:
                    selection.abandon_subtitle_selection(timing_selection)
                    kodi.notify(
                        'הפעלת התרגום המובנה נכשלה', time_ms=3000)
            except Exception as _e:
                _log('embedded select failed: {0}'.format(_e), level='WARNING')
                selection.abandon_subtitle_selection(timing_selection)
            close_cb()
            return
        if kind in ('engine_ai', 'ai', 'embedded_ai'):
            close_cb()
            _start_ai_apply(link, info)
            return
        path = pipeline.resolve(link, info, selection=timing_selection)
        if path and os.path.isfile(path) and _still_current():
            if selection.apply_subtitle_file(
                    path, selection=timing_selection):
                close_cb()
                # a pooled AI translation: its gender proofread
                gp_jobs.proofread_later(link, timing_selection)
            elif not _still_current():
                close_cb()
            else:
                kodi.notify(
                    'הפעלת הכתובית נכשלה, נסה אחרת', time_ms=3500)
        elif path and not _still_current():
            close_cb()
        else:
            kodi.notify('ההורדה נכשלה, נסה אחרת', time_ms=3500)
    except Exception as e:
        _log('deliver pick failed: {0}'.format(e), level='WARNING')


def _searching_notice():
    """A small non-blocking progress bar while the list is prepared."""
    try:
        bar = xbmcgui.DialogProgressBG()
        bar.create(_title(), 'מחפש כתוביות…')
        return bar
    except Exception:
        return None


def _close_notice(bar):
    if bar is None:
        return
    try:
        bar.close()
    except Exception:
        pass


def _human_hebrew_rows(cands):
    """How many rows SubSync's picker rank could reorder (the same test as
    subsync._is_human_hebrew_candidate), without importing SubSync. The row
    on screen ("» נוכחית") is set aside by the rank, so it does not count."""
    import json
    import urllib.parse
    n = 0
    for c in cands or []:
        try:
            if (c.get('filename') or '').startswith('» נוכחית'):
                continue
            if (c.get('language') or '').strip().lower() not in ('he', 'heb', 'hebrew'):
                continue
            p = json.loads(urllib.parse.unquote(c.get('link') or '')) or {}
            kind = p.get('type')
            if (kind == 'passthrough'
                    or (kind == 'pool' and (p.get('pool_kind') or 'ai') == 'ktuvit')
                    or (kind == 'engine' and not p.get('embedded')
                        and 'Hebrew' in str(p.get('language') or '')
                        and 'MachineTranslated' not in str(p.get('language') or ''))):
                n += 1
        except Exception:
            continue
    return n


def _is_ai_row(c):
    """A row that starts an AI translation when picked."""
    import json
    import urllib.parse
    try:
        p = json.loads(urllib.parse.unquote(c.get('link') or '')) or {}
        return p.get('type') in ('ai', 'embedded_ai')
    except Exception:
        return False


_EMBEDDED_MISS_WHY = {
    'no_text': 'הכתובית המובנית בקובץ היא תמונה (PGS / VobSub) ולא טקסט',
    'no_index': 'אי אפשר לקרוא את הכתובית המובנית מהזרם',
    'not_mkv': 'הקובץ אינו Matroska: הכתוביות המובנות בו לא נקראות',
}


def _ai_context(info):
    """What the rows need to say which of them CANNOT be AI-translated (chooser_rows.no_ai_reason): a usable translator
    (translation_mode and, for Gemini, a key), the embedded-track policy, and whether this file's embedded track was
    already tried and could not be read (image-based PGS / VobSub, no index, not Matroska). Never raises; on any trouble
    it claims nothing (returns None)."""
    try:
        from resources.lib.core import settings
        ctx = {'ok': True, 'why': '', 'emb_ok': True, 'emb_why': '', 'embedded_miss': False, 'embedded_miss_why': ''}
        mode = (settings.get_setting('translation_mode', 'ai') or 'ai').strip()
        if mode == 'none':
            ctx.update(ok=False, why='תרגום AI כבוי בהגדרות')
        elif mode == 'ai' and not (settings.get_setting('api_key', '') or '').strip():
            ctx.update(ok=False, why='לא הוגדר מפתח Gemini')
        from resources.lib.ai_translation.policy import embedded_translation_policy
        from resources.lib.fetching.embedded import extraction
        policy = embedded_translation_policy()
        if not policy.get('enabled'):
            ctx.update(emb_ok=False, emb_why='תרגום כתוביות מובנות כבוי בהגדרות')
        elif policy.get('mode') == 'local_only':
            url = extraction.playing_video_url(info or {})
            if not url or url.lower().startswith(('http://', 'https://')):
                ctx.update(emb_ok=False, emb_why='מצב "מקומי בלבד": הקובץ מנוגן כזרם')
        playing = ''
        try:
            playing = xbmc.Player().getPlayingFile() or ''
        except Exception:
            playing = ''
        miss = extraction.embedded_miss_for(playing)
        if miss is not None:
            ctx.update(embedded_miss=True, embedded_miss_why=_EMBEDDED_MISS_WHY.get(
                miss, 'אי אפשר היה לקרוא את הכתובית המובנית'))
        return ctx
    except Exception as e:
        _log('ai context unavailable: {0}'.format(e), level='DEBUG')
        return None


def _chooser_items(info, quick=False):
    """The chooser's rows: list_candidates, the timing rank when it has a
    human Hebrew row to work on, and only rows that can be picked."""
    try:
        from resources.lib import pipeline
        cands = pipeline.search(info, modal_progress=False,
                                quick=quick) or []
    except Exception as e:
        _log('list_candidates failed: {0}'.format(e), level='WARNING')
        cands = []
    # The timing rank only works on human Hebrew rows (its cached-proof pass
    # labels even a single one); importing SubSync for it cost 1.9 s on that
    # box when the list held only embedded / AI rows.
    # It only reads verdicts already stored for this file (cached_only): a new
    # comparison took 7.7 s each on a Shield and held the window for 16 s.
    if _human_hebrew_rows(cands) >= 1:
        try:
            from resources.lib.sync import candidates
            cands = candidates.rank_picker_candidates(info, cands, cached_only=True)
        except Exception as e:
            _log('candidate timing-rank skipped: {0}'.format(e), level='DEBUG')
    else:
        cands = _current_first(cands)
    return [c for c in cands
            if isinstance(c, dict) and c.get('link') and c.get('filename')]


def _current_first(cands):
    """Keep the row that is on screen at the top, as the rank does."""
    rows = list(cands or [])
    current = [r for r in rows if isinstance(r, dict)
               and (r.get('filename') or '').startswith('» נוכחית')]
    if not current:
        return rows
    return [current[0]] + [r for r in rows if r is not current[0]]


def _pause_for_chooser():
    """Pause the playing video; True when this call paused it."""
    try:
        if (xbmc.Player().isPlayingVideo()
                and not xbmc.getCondVisibility('Player.Paused')):
            xbmc.Player().pause()
            return True
    except Exception:
        pass
    return False


def _resume_after_chooser(paused):
    if not paused:
        return
    try:
        if (xbmc.Player().isPlayingVideo()
                and xbmc.getCondVisibility('Player.Paused')):
            xbmc.Player().pause()  # resume
    except Exception:
        pass


_BUSY_PROP = 'moransubs.chooser_busy'


def show():
    """Open the rich MoranSubs chooser (a self-contained WindowXMLDialog that
    looks the same on every skin). Falls back to the pyxbmct chooser, then to
    Kodi's native dialog, if it can't be created.

    The video keeps playing while the list is prepared, so the OSD button can
    be pressed again meanwhile; each press is its own script process with its
    own search lock, and two presses used to run the providers twice and
    stack two windows. A press while one is preparing or open is ignored
    (True, so default.py does not open Kodi's dialog either). A stamp older
    than a minute is a process that died and does not count."""
    if xbmc is None:
        return False
    import time
    try:
        win = xbmcgui.Window(10000)
        stamp = float(win.getProperty(_BUSY_PROP) or 0)
    except Exception:
        win, stamp = None, 0
    if stamp and 0 <= time.time() - stamp < 60:
        _log('chooser: already opening -- ignoring a second press')
        return True
    try:
        if win is not None:
            win.setProperty(_BUSY_PROP, str(time.time()))
    except Exception:
        pass
    try:
        return _show()
    finally:
        try:
            if win is not None:
                win.clearProperty(_BUSY_PROP)
        except Exception:
            pass


def _show():
    if xbmc is None:
        return False
    try:
        if not xbmc.Player().isPlayingVideo():
            return False
    except Exception:
        return False
    try:
        from resources.lib.core import kodi, video_context
    except Exception:
        return False
    # The list can take seconds on a streaming box. Measured in a user's
    # video: 8 s of plain playback after the press, so it looked ignored. A
    # pause + busy spinner (0.3.33) was worse for him: the video stood still
    # for the whole wait, and his "Unpause Jumpback" add-on rewinds 4 s on
    # every resume. Then (0.3.34) every press still searched every provider
    # for 4.6 s on his box. So the window now opens on what needs no network
    # (embedded tracks, AI rows, a search already cached), and the provider
    # search and the community pool fill it in from a background thread.
    import time
    t_start = time.monotonic()
    _paused = False
    _notice = _searching_notice()
    try:
        info = video_context.current_video_info()
        items = _chooser_items(info, quick=True)
        pending = bool(info.pop('_list_pending', False))
    finally:
        _close_notice(_notice)
    _log('chooser: {0} candidate(s) {1}in {2:.1f}s from the press'.format(
        len(items), '(the source search continues in the background) '
        if pending else '', time.monotonic() - t_start))
    if not items and pending:
        items = [{'filename': 'מחפש כתוביות…', 'language': '', 'link': '',
                  '_placeholder': True}]
    if not items:
        _resume_after_chooser(_paused)
        try:
            kodi.notify('לא נמצאו כתוביות לכותר הזה', time_ms=3500)
        except Exception:
            pass
        return False

    try:
        import xbmcaddon

        class _ChooserXML(xbmcgui.WindowXMLDialog):
            closed = False
            # manual search (button 202): the typed search replaces the rows, `base` keeps the automatic list
            manual = False
            manual_gen = 0
            manual_busy = False
            base = None
            want = []       # the release words of the manual query: highlighted like the playing release's words

            def onInit(self):
                try:
                    self.setProperty('subs.brand', _title())
                    self.setProperty('subs.head', video_context.release_name(self.info, 'chooser') or '')
                    # the release the rows are compared with (the one the list was scored against)
                    self.playing = video_context.release_name(self.info, 'picker') or ''
                    self.ai_ctx = _ai_context(self.info)
                    try:
                        xbmcgui.Window(10000).setProperty(
                            'MoranSubsChooserOpen', '1')
                    except Exception:
                        pass
                    self._fill()
                    try:
                        self.setFocusId(100)
                    except Exception:
                        pass
                    if self.pending and not self.started:
                        self.started = True
                        import threading
                        t = threading.Thread(target=self._complete)
                        t.daemon = True
                        t.start()
                except Exception as e:
                    _log('xml onInit failed: {0}'.format(e), level='WARNING')

            def _fill(self, items=None):
                with self.lock:
                    self._redraw(self.items if items is None else items)

            def _redraw(self, items):
                lst = self.getControl(100)
                pos = lst.getSelectedPosition() if lst.size() else -1
                was = (self.items[pos].get('link')
                       if 0 <= pos < len(self.items) else None)
                real = [c for c in items if not c.get('_placeholder')]
                self.setProperty('subs.count', 'מחפש כתוביות נוספות…'
                                 if self.pending else
                                 'נמצאו {0} כתוביות'.format(len(real)))
                rows = []
                for c in items:
                    # One column per field (chooser_rows): the colours are baked
                    # into the property text (BBCode) because a per-row
                    # <textcolor>$INFO[...]</textcolor> is unreliable in Kodi.
                    if c.get('_placeholder'):
                        row = chooser_rows.placeholder(c['filename'])
                    else:
                        row = chooser_rows.build(
                            c, getattr(self, 'playing', ''), want=getattr(self, 'want', None),
                            ai=getattr(self, 'ai_ctx', None))
                    li = xbmcgui.ListItem(row['label'])
                    if row['flag']:
                        li.setArt({'thumb': row['flag']})
                    for key, value in row['props'].items():
                        li.setProperty(key, value)
                    rows.append(li)
                lst.reset()
                lst.addItems(rows)
                self.items = items
                keep = next((i for i, c in enumerate(items)
                             if was and c.get('link') == was), 0)
                if 0 < keep < len(rows):
                    lst.selectItem(keep)

            def _complete(self):
                """The full list (provider search + pool), into the open window."""
                import time
                t = time.monotonic()
                try:
                    items = _chooser_items(dict(self.info))
                except Exception as e:
                    _log('chooser background list failed: {0}'.format(e),
                         level='WARNING')
                    items = [c for c in (self.base['items'] if self.manual and self.base else self.items)
                             if not c.get('_placeholder')]
                _log('chooser: full list {0} candidate(s) after {1:.1f}s more'.format(
                    len(items), time.monotonic() - t))
                if self.closed:
                    return
                if not items:
                    items = [{'filename': 'לא נמצאו כתוביות לכותר הזה',
                              'language': '', 'link': '', '_placeholder': True}]
                try:
                    # pending and the rows change together, under the click lock
                    with self.lock:
                        if self.manual:
                            # a manual search is on screen: the automatic list waits behind "חזרה לרשימה"
                            self.base = {'items': items, 'pending': False,
                                         'head': (self.base or {}).get('head', '')}
                        else:
                            self.pending = False
                            self._redraw(items)
                except Exception as e:
                    _log('chooser refresh failed: {0}'.format(e), level='WARNING')

            def _start_manual(self):
                """Button 202 (VLSub style): ask for ONE free-text query (pre-filled with the playing release), search by
                it and show the rows in this window. The playing video's `info` (self.info) stays the one a pick is
                delivered with."""
                if self.manual_busy:
                    return
                from resources.lib.fetching import manual
                from resources.lib.ui import manual_prompts
                query = manual_prompts.ask(self.info)
                if not query:
                    return
                search_info = manual.build_info(self.info, query)
                with self.lock:
                    self.manual_gen += 1
                    gen = self.manual_gen
                    self.manual_busy = True
                    self.setProperty('subs.count', 'מחפש כתוביות…')
                import threading
                t = threading.Thread(target=self._run_manual, args=(gen, search_info))
                t.daemon = True
                t.start()

            def _run_manual(self, gen, search_info):
                """The manual search, off the window thread: the typed title's IMDb id, then the normal list. A result
                of a search that was replaced meanwhile (another manual search, or "חזרה לרשימה") is dropped."""
                from resources.lib.fetching import manual
                items = []
                want = []
                try:
                    try:
                        search_info, found = manual.resolve_ids(search_info)
                        if not found:
                            kodi.notify('לא נמצא כותר ב-TMDB, מחפש לפי המחרוזת', time_ms=3000)
                    except Exception as e:
                        _log('manual search: id lookup failed: {0}'.format(e), level='WARNING')
                    items = _chooser_items(search_info)
                    items, full = manual.rank_by_tokens(items, search_info.get('_query_tokens'))
                    want = list(search_info.get('_query_tokens') or [])
                    if search_info.get('_query_tokens') and items and not full:
                        kodi.notify('אין כתובית שמכילה את כל המילים; מוצגות הקרובות ביותר', time_ms=3500)
                    _log('chooser: manual search {0!r} -> {1} candidate(s), {2} with every release word'.format(
                        manual.head(search_info), len(items), full))
                except Exception as e:
                    _log('chooser: manual search failed: {0}'.format(e), level='WARNING')
                try:
                    with self.lock:
                        if self.closed or self.manual_gen != gen:
                            return
                        if not items:
                            items = [{'filename': 'לא נמצאו כתוביות לחיפוש הזה', 'language': '',
                                      'link': '', '_placeholder': True}]
                        if not self.manual:
                            self.base = {'items': self.items, 'pending': self.pending,
                                         'head': self.getProperty('subs.head')}
                        self.manual = True
                        self.want = want
                        self.pending = False
                        self.setProperty('subs.manual', '1')
                        self.setProperty('subs.head', manual.head(search_info))
                        self._redraw(items)
                    try:
                        self.setFocusId(100)
                    except Exception:
                        pass
                except Exception as e:
                    _log('chooser manual refresh failed: {0}'.format(e), level='WARNING')
                finally:
                    if self.manual_gen == gen:
                        self.manual_busy = False

            def _back_to_list(self):
                """Button 203 ("חזרה לרשימה"): leave the manual results, show the automatic list again."""
                with self.lock:
                    self.manual_gen += 1      # a manual search still running is dropped
                    self.manual_busy = False
                    base, self.base = self.base, None
                    if not self.manual or not base:
                        return
                    self.manual = False
                    self.want = []
                    self.pending = bool(base.get('pending'))
                    self.clearProperty('subs.manual')
                    self.setProperty('subs.head', base.get('head') or '')
                    self._redraw(base['items'])
                try:
                    self.setFocusId(100)
                except Exception:
                    pass

            def close(self):
                self.closed = True
                xbmcgui.WindowXMLDialog.close(self)

            def onAction(self, action):
                try:
                    if action.getId() in (9, 10, 92, 216, 247):
                        self.close()
                except Exception:
                    try:
                        self.close()
                    except Exception:
                        pass

            def onClick(self, controlId):
                try:
                    if controlId == 201:
                        self.close()
                        return
                    if controlId == 202:
                        self._start_manual()
                        return
                    if controlId == 203:
                        self._back_to_list()
                        return
                    if controlId == 100:
                        if not self.lock.acquire(timeout=2):
                            return
                        try:
                            i = self.getControl(100).getSelectedPosition()
                            item = (self.items[i] if i is not None
                                    and 0 <= i < len(self.items) else None)
                            pending = self.pending
                        finally:
                            self.lock.release()
                        if item is None or item.get('_placeholder'):
                            return
                        if pending and _is_ai_row(item):
                            try:
                                from resources.lib.core import kodi
                                kodi.notify('עוד מחפש כתוביות מוכנות -- '
                                                  'רגע ותוכל לבחור', time_ms=2500)
                            except Exception:
                                pass
                            return
                        _deliver_pick(item, self.info, self.close)
                except Exception as e:
                    _log('xml onClick failed: {0}'.format(e), level='WARNING')

        # Playback may have been stopped while the list was prepared.
        if not xbmc.Player().isPlayingVideo():
            return True
        # Pause while the chooser is open (Kodi's own subtitle dialog does
        # this) and resume on close if WE paused it.
        _paused = _pause_for_chooser()
        addon_path = xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')
        w = _ChooserXML('script.moransubs.chooser.xml', addon_path,
                        'Default', '1080i')
        import threading
        w.info = info
        w.playing = ''
        w.items = items
        w.pending = pending
        w.started = False
        w.lock = threading.Lock()
        w.doModal()
        del w
        _resume_after_chooser(_paused)
        return True
    except Exception as e:
        _log('WindowXMLDialog failed, falling back to pyxbmct: {0}'.format(e),
             level='WARNING')

    try:
        return _show_pyxbmct()
    except Exception:
        return False
    finally:
        _resume_after_chooser(_paused)
