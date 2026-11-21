# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Settings actions of the Gemini key: connect (menu, QR pairing, typed key), test, usage, TMDb notice.

try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcvfs
except ImportError:
    # Allow `python -m default --action=search` for local debug.
    xbmc = xbmcgui = xbmcplugin = xbmcvfs = None
from resources.lib.ui.dialogs import dialogs_title


# ---- RunScript handlers (settings buttons) --------------------------

def handle_open_aistudio(_params):
    """Open the AI Studio key-creation page in the user's browser
    when they tap "Get a free Gemini API key" in settings."""
    url = 'https://aistudio.google.com/apikey'
    try:
        xbmc.executebuiltin('System.Exec("xdg-open {0}")'.format(url))
    except Exception:
        pass
    # Always show a fallback dialog with the URL so users on
    # platforms without a usable browser (Fire TV, Shield) can copy
    # it down manually.
    try:
        xbmcgui.Dialog().ok(
            dialogs_title(),
            'פתח בדפדפן:\n{0}\n\nצור API key (חינמי), העתק, '
            'והדבק בשדה "Gemini API Key" בהגדרות.'.format(url),
        )
    except Exception:
        pass



def handle_connect_gemini(_params):
    """Full Gemini auth flow invoked from POV's My Services menu
    (or from anywhere via RunScript). Provides two onboarding
    paths -- pair-from-phone via local HTTP server, or type the
    key directly -- and validates against Gemini's /models
    endpoint INLINE before writing to settings, so a bad key
    never lands in the addon's persistent state."""
    try:
        from resources.lib.ai_translation.providers import gemini, gemini_pair
        from resources.lib.core import settings
    except Exception as e:
        try:
            xbmcgui.Dialog().ok(dialogs_title(),
                                'Internal error: {0}'.format(e))
        except Exception:
            pass
        return

    current = (settings.get_setting('api_key', '') or '').strip()
    if current:
        _gemini_menu_existing(gemini, gemini_pair, current)
    else:
        _gemini_menu_new(gemini, gemini_pair)


def _gemini_menu_existing(gemini, gemini_pair, current_key):
    """User clicked Gemini in My Services and already has a key
    set. Offer Test / Usage / Replace / Remove."""
    from resources.lib.core import kodi, settings
    options = [
        '🔍 בדוק חיבור (Test connection)',
        '📊 ניצול היום (Daily usage)',
        '🔄 החלף key (Replace)',
        '❌ מחק key (Remove)',
    ]
    try:
        choice = xbmcgui.Dialog().select(
            'Gemini AI - מה לעשות?', options)
    except Exception:
        choice = -1
    if choice < 0:
        return
    if choice == 0:
        _test_key_show_result(gemini, current_key)
        return
    if choice == 1:
        _show_gemini_usage()
        return
    if choice == 2:
        # Don't clear the existing key here -- if the user cancels
        # mid-flow (closes the QR dialog, dismisses the keyboard,
        # taps outside the screen) they'd lose a working key with
        # no replacement. The new key, once validated, overwrites
        # the old one via set_setting in _test_save_or_retry, so
        # replace happens atomically on success; on cancel the old
        # key stays put.
        _gemini_menu_new(gemini, gemini_pair)
        return
    if choice == 3:
        confirm = xbmcgui.Dialog().yesno(
            dialogs_title(), 'למחוק את ה-Gemini API key?')
        if confirm:
            settings.set_setting('api_key', '')
            kodi.notify('Gemini key נמחק', time_ms=3000)


def _show_gemini_usage():
    """Render the daily quota status in a Dialog().ok(). Used by
    the 'ניצול היום' menu entry and the runscript action."""
    try:
        from resources.lib.ai_translation import quota
        from resources.lib.core import settings
    except Exception as e:
        try:
            xbmcgui.Dialog().ok(dialogs_title(),
                                'Internal error: {0}'.format(e))
        except Exception:
            pass
        return
    try:
        # Report against the CURRENTLY selected model (not just the last one
        # translated), and short-circuit in paid mode where the free daily cap
        # doesn't apply (otherwise a paid user on regular Flash could see an
        # alarming "147/20" against a limit that isn't theirs).
        paid = settings.get_bool('ai_paid_mode', False)
        model = (settings.get_setting('model', 'gemini-3.5-flash-lite')
                 or 'gemini-3.5-flash-lite')
        if paid:
            body = ('מצב מהיר (למשתמשי Gemini API בתשלום) פעיל.\n'
                    'הגבלות המכסה החינמית אינן חלות, ולכן אין ספירת ניצול יומי.\n\n'
                    'מודל נוכחי: {0}').format(model)
        else:
            body = quota.format_status_long(model)
    except Exception as e:
        body = 'שגיאה בקריאת הנתונים: {0}'.format(e)
    try:
        xbmcgui.Dialog().ok('Gemini AI - ניצול היום', body)
    except Exception:
        pass


def handle_show_gemini_usage(_params):
    """RunScript entry point so the dialog can be opened from
    anywhere, e.g. a remote shortcut."""
    _show_gemini_usage()


def _gemini_menu_new(gemini, gemini_pair):
    """No key set yet. Let the user pick onboarding method."""
    
    options = [
        '📱 התאמה מטלפון / מכשיר אחר (QR + URL)',
        '⌨️ הזנת ה-key ידנית כאן',
    ]
    try:
        choice = xbmcgui.Dialog().select(
            'Gemini AI - איך להתחבר?', options)
    except Exception:
        choice = -1
    if choice < 0:
        return
    if choice == 0:
        _gemini_pair_flow(gemini, gemini_pair)
        return
    if choice == 1:
        _gemini_type_flow(gemini)


# ---- Pairing dialog: script.moransubs.pair.xml ------------------------

_PAIR_TIMEOUT = 300             # seconds the pairing page waits for the key
_QR_FILE_PREFIX = 'moransubs_pair_qr_'


def _L(strid):
    """A strings.po label (ids 32740-32754 belong to the pairing dialog)."""
    from resources.lib.core import kodi
    return kodi.localised(strid)


def _png_from_matrix(matrix, border=2, target=380):
    """A grey-scale PNG of a QR module matrix, stdlib only (zlib + struct): no PIL needed. One module is a whole
    number of pixels (as many as fit `target` px with the `border` quiet-zone modules); the image is built from byte
    strings row by row, not pixel by pixel, so it takes a few milliseconds even on a Shield."""
    import struct
    import zlib
    n = len(matrix) + 2 * border
    scale = max(3, target // n)
    white, black = b'\xff' * scale, b'\x00' * scale
    side = [white] * border
    blank = b'\x00' + b'\xff' * (n * scale)         # filter byte 0 + one all-white pixel row
    rows = [blank] * (border * scale)
    for mrow in matrix:
        line = b'\x00' + b''.join(side + [black if c else white for c in mrow] + side)
        rows.extend([line] * scale)
    rows.extend([blank] * (border * scale))
    side_px = n * scale

    def chunk(tag, data):
        body = tag + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', side_px, side_px, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 6))
            + chunk(b'IEND', b''))


def _remove_qr_files(path=None):
    """Delete this session's QR PNG, or (no path) every leftover of a session that died."""
    import glob
    import os
    try:
        if path:
            paths = [path]
        else:
            paths = glob.glob(os.path.join(xbmcvfs.translatePath('special://temp/'), _QR_FILE_PREFIX + '*.png'))
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
    except Exception:
        pass


def _make_qr_png(data):
    """Render `data` as a QR code in special://temp and return the PNG path, rendered locally by script.module.qrcode
    (the matrix) + _png_from_matrix. '' when that module is not there or anything fails: the dialog then shows the
    address only. Never raises."""
    import os
    import time
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except Exception:
        return ''
    try:
        qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=1, border=0)
        qr.add_data(data)
        qr.make(fit=True)
        png = _png_from_matrix(qr.get_matrix())
        _remove_qr_files()
        # A new name every session: Kodi's texture cache keys on the path, a reused name could show an old code.
        path = os.path.join(xbmcvfs.translatePath('special://temp/'),
                            '{0}{1}.png'.format(_QR_FILE_PREFIX, int(time.time() * 1000)))
        with open(path, 'wb') as f:
            f.write(png)
        return path
    except Exception:
        return ''


class _PairDialog(xbmcgui.WindowXMLDialog):
    """The pairing dialog, a 1080i WindowXMLDialog (script.moransubs.pair.xml): title bar, a QR card, three steps, a progress
    bar with the countdown, and two focusable buttons (cancel, "connection problems?" which swaps the steps for the help text).
    Every text is a strings.po label read in the XML; the few variable ones (address, countdown, the note in the QR card, the
    help-button label) and the visibility switches are window properties, so Python touches only two controls (QR image,
    progress bar). All of it runs on the script thread. The dialog opens BEFORE the server starts: until set_details() the
    QR card says "preparing".

    Closes on Back / Esc (the help panel first, if it is open) or the cancel button; the caller also closes it when the key
    arrived."""

    XML_FILE = 'script.moransubs.pair.xml'
    CTRL_QR = 101
    CTRL_PROGRESS = 104
    CTRL_CANCEL = 200
    CTRL_PROBLEMS = 201
    ACTION_PREVIOUS_MENU = 10
    ACTION_NAV_BACK = 92
    ACTION_STOP = 13

    def __init__(self, *args, **kwargs):
        xbmcgui.WindowXMLDialog.__init__(self)
        self.cancelled = False
        self._ready = False         # onInit has run, the controls exist
        self._help = False
        self._details = None        # (qr_path, url, no_lan) once the server is up
        self._percent = 100
        self._time = ''

    def set_details(self, qr_path, url, no_lan):
        self._details = (qr_path, url, no_lan)
        if self._ready:
            self._apply()

    def update_time(self, seconds_left, total):
        mm, ss = divmod(int(max(0, seconds_left)), 60)
        self._time = '{0:02d}:{1:02d}'.format(mm, ss)
        self._percent = int(100 * max(0, seconds_left) / float(total or 1))
        if self._ready:
            self._apply_time()

    def onInit(self):
        self._ready = True
        self._apply()
        self._apply_time()

    def onAction(self, action):
        if action.getId() in (self.ACTION_PREVIOUS_MENU, self.ACTION_NAV_BACK, self.ACTION_STOP):
            if self._help:
                self._help = False
                self._apply()
                return
            self.cancelled = True
            self.close()
            return
        xbmcgui.WindowXMLDialog.onAction(self, action)

    def onClick(self, control_id):
        if control_id == self.CTRL_CANCEL:
            self.cancelled = True
            self.close()
        elif control_id == self.CTRL_PROBLEMS:
            self._help = not self._help
            self._apply()

    def _apply(self):
        self.setProperty('pair.help', '1' if self._help else '')
        self.setProperty('pair.helplabel', _L(32748) if self._help else _L(32747))
        if self._details is None:
            self.setProperty('pair.ready', '')
            self.setProperty('pair.qr', '')
            self.setProperty('pair.nolan', '')
            self.setProperty('pair.url', '')
            self.setProperty('pair.qrnote', _L(32744))
            return
        qr_path, url, no_lan = self._details
        self.setProperty('pair.ready', '1')
        self.setProperty('pair.url', url)
        self.setProperty('pair.nolan', '1' if no_lan else '')
        if qr_path:
            self._safe(self.CTRL_QR, lambda c: c.setImage(qr_path, False))
            self.setProperty('pair.qr', '1')
            self.setProperty('pair.qrnote', '')
        else:
            self.setProperty('pair.qr', '')
            self.setProperty('pair.qrnote', _L(32749))

    def _apply_time(self):
        self.setProperty('pair.time', self._time)
        self._safe(self.CTRL_PROGRESS, lambda c: c.setPercent(float(self._percent)))

    def _safe(self, control_id, fn):
        """Call fn(control); a control the skin file does not have is ignored."""
        try:
            fn(self.getControl(control_id))
        except Exception:
            pass


def _run_pair_dialog(gemini_pair):
    """Open the pairing dialog at once, start the PairServer behind it, render the QR, then poll for the submitted key until
    it arrives / the user cancels / Kodi aborts / the 5-minute deadline, and shut the server down. Returns the submitted key
    ('' = cancelled, timed out or failed; a failure has been shown)."""
    import time
    from resources.lib.core import addon as core_addon
    try:
        window = _PairDialog(_PairDialog.XML_FILE, core_addon.addon().getAddonInfo('path'), 'Default', '1080i')
        window.show()
    except Exception as e:
        xbmcgui.Dialog().ok(
            dialogs_title(),
            'נכשלה פתיחת חלון ההתאמה: {0}\n\n'
            'אפשר לחזור לתפריט ולבחור "הזנה ידנית" במקום.'.format(str(e)[:80]))
        return ''

    ps = None
    qr_path = ''
    key = ''
    error = ''
    try:
        ps = gemini_pair.PairServer()
        # The URL carries the session's secret segment, so the QR is the only place a LAN device can learn it.
        primary = ps.url_lan or ps.url_local
        qr_path = _make_qr_png(primary)
        window.set_details(qr_path, primary, ps.url_lan is None)

        monitor = xbmc.Monitor()
        deadline = time.monotonic() + _PAIR_TIMEOUT
        while not (window.cancelled or monitor.abortRequested()):
            key = ps.received_key()
            if key:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            window.update_time(remaining, _PAIR_TIMEOUT)
            xbmc.sleep(250)
        if not key and not window.cancelled and ps is not None:
            key = ps.received_key()
    except Exception as e:
        error = str(e)[:80]
    finally:
        if ps is not None:
            ps.shutdown()
        try:
            window.close()
        except Exception:
            pass
        del window
        if qr_path:
            _remove_qr_files(qr_path)

    if error:
        xbmcgui.Dialog().ok(
            dialogs_title(),
            'נכשלה הפעלת שרת התאמה: {0}\n\n'
            'אפשר לחזור לתפריט ולבחור "הזנה ידנית" במקום.'.format(error))
        return ''
    return key


def _gemini_pair_flow(gemini, gemini_pair):
    """Open the pairing dialog (it starts the local pair server itself), wait for the key typed on the phone, validate."""
    key = _run_pair_dialog(gemini_pair)
    if not key:
        return  # user cancelled, timeout or failure (already shown)
    _test_save_or_retry(gemini, key, retry_cb=None)


def _gemini_type_flow(gemini):
    """Original typed-input flow, but with inline validation
    before save and a retry loop on failure."""
    
    xbmcgui.Dialog().ok(
        'Gemini AI - איך משיגים API key',
        'כדי שתרגום ה-AI יעבוד צריך API key חינמי של Gemini:\n\n'
        '1) פתח בדפדפן (במחשב/טלפון):\n'
        '   https://aistudio.google.com/apikey\n\n'
        '2) התחבר עם חשבון Google. לחץ Create API key.\n\n'
        '3) העתק את המחרוזת והדבק במסך הבא.\n\n'
        'התוכנית החינמית מאפשרת ~500 בקשות ביום של Flash Lite.')
    while True:
        try:
            key = (xbmcgui.Dialog().input('Gemini API Key:') or '').strip()
        except Exception:
            key = ''
        if not key:
            return
        ok = _test_save_or_retry(gemini, key,
                                  retry_cb='loop')
        if ok != 'retry':
            return


def _test_save_or_retry(gemini, api_key, retry_cb):
    """Run gemini.test_key on the supplied key. On success: save
    to settings + show success + nudge TMDB. On failure: show the
    specific reason and (if retry_cb='loop') ask whether to try
    again, returning 'retry' if yes."""
    from resources.lib.core import kodi, settings
    kodi.notify('Gemini: בודק...', time_ms=2000)
    err = None
    try:
        matched = gemini.test_key(api_key)
    except gemini.InvalidKey as e:
        err = 'ה-key נדחה ע"י Gemini ({0})'.format(str(e)[:80])
    except gemini.GeminiError as e:
        err = 'בדיקה נכשלה: {0}'.format(str(e)[:80])
    except Exception as e:
        err = 'שגיאה בלתי צפויה: {0}'.format(str(e)[:80])

    if err is None:
        # Success -- save the key, show confirmation. TMDB no
        # longer needs nudging: the addon ships with a bundled
        # TMDB key, so the user is fully set up the moment Gemini
        # connects.
        saved = settings.set_setting('api_key', api_key)
        if not saved:
            # Kodi silently rejected our setSetting -- happens on
            # some Kodi/Android combos where the addon UI doesn't
            # commit to settings.xml. Surface the failure instead
            # of showing a false success dialog.
            xbmcgui.Dialog().ok(
                'Gemini AI - שמירה נכשלה',
                'ה-key אומת בהצלחה מול Gemini, אבל Kodi לא שמר '
                'אותו בקובץ ההגדרות.\n\n'
                'נסה לסגור את Kodi לחלוטין ולהפעיל מחדש, ואז לחזור '
                'לכאן ולהריץ שוב את ההתאמה.')
            return 'cancel'
        xbmcgui.Dialog().ok(
            'Gemini AI',
            '✓ החיבור הצליח. מודל: {0}\n\n'
            'מוכן לתרגם. אין צורך בהגדרות נוספות.'.format(matched))
        return 'ok'

    # Failure. DON'T save. Optionally offer retry.
    if retry_cb == 'loop':
        retry = xbmcgui.Dialog().yesno(
            'Gemini AI - בדיקה נכשלה',
            err + '\n\nלנסות שוב?',
            nolabel='ביטול', yeslabel='נסה שוב')
        return 'retry' if retry else 'cancel'
    xbmcgui.Dialog().ok('Gemini AI - בדיקה נכשלה', err)
    return 'cancel'


def _test_key_show_result(gemini, api_key):
    """Re-test an existing key and show the result in a dialog.
    Does NOT change the saved key either way (this is the
    "🔍 Test connection" entry point from the existing-key
    menu)."""
    from resources.lib.core import kodi
    kodi.notify('Gemini: בודק...', time_ms=2000)
    try:
        matched = gemini.test_key(api_key)
        xbmcgui.Dialog().ok(
            'Gemini AI',
            '✓ החיבור תקין. מודל: {0}'.format(matched))
    except gemini.InvalidKey as e:
        xbmcgui.Dialog().ok(
            'Gemini AI',
            '✗ ה-key נדחה ע"י Gemini: {0}'.format(str(e)[:120]))
    except gemini.GeminiError as e:
        xbmcgui.Dialog().ok(
            'Gemini AI',
            '✗ בדיקה נכשלה: {0}'.format(str(e)[:120]))
    except Exception as e:
        xbmcgui.Dialog().ok(
            'Gemini AI',
            '✗ שגיאה בלתי צפויה: {0}'.format(str(e)[:120]))


def handle_test_connection(_params):
    """User clicked "Test connection" in settings."""
    try:
        from resources.lib.ai_translation.providers import gemini
        from resources.lib.core import kodi, settings
    except Exception as e:
        xbmcgui.Dialog().ok(dialogs_title(), 'Internal error: {0}'.format(e))
        return

    api_key = settings.get_setting('api_key', '')
    model   = settings.get_setting('model', 'gemini-3.5-flash-lite') \
              or 'gemini-3.5-flash-lite'

    if not api_key:
        xbmcgui.Dialog().ok(dialogs_title(), kodi.localised(32632))
        return

    try:
        matched = gemini.test_key(api_key, model=model)
        xbmcgui.Dialog().ok(dialogs_title(),
                            kodi.localised(32633, matched))
    except gemini.InvalidKey as e:
        xbmcgui.Dialog().ok(dialogs_title(),
                            kodi.localised(32634, str(e)[:120]))
    except gemini.GeminiError as e:
        xbmcgui.Dialog().ok(dialogs_title(),
                            kodi.localised(32634, str(e)[:120]))
    except Exception as e:
        xbmcgui.Dialog().ok(dialogs_title(),
                            kodi.localised(32634, str(e)[:120]))


def handle_open_tmdb_notice(_params):
    """Explain the current TMDB state. Since v0.2.13 the addon
    ships with a bundled fallback key from the upstream
    tmdbhelper project, so gender-aware translation works out of
    the box. Connecting a personal key remains optional and
    unchanged -- a user key, if present, takes precedence over
    the bundled one."""
    try:
        from resources.lib.ai_translation.context import tmdb_helper
    except Exception as e:
        xbmcgui.Dialog().ok(dialogs_title(), 'Internal error: {0}'.format(e))
        return

    try:
        using_bundled = tmdb_helper.using_bundled_key()
    except Exception:
        using_bundled = True

    if using_bundled:
        status_line = ('✓ TMDB עובד אוטומטית (key מובנה).\n'
                       'אין צורך לעשות כלום — תרגום AI כבר יודע '
                       'לבחור צורות זכר/נקבה לפי הדמויות.\n\n')
    else:
        status_line = ('✓ נמצא TMDB API key אישי דרך תוסף TMDB '
                       'Helper. הוא בשימוש במקום ה-key המובנה.\n\n')

    body = (
        status_line +
        'תרגום AI משתמש ב-TMDB כדי לזהות את מין כל דמות (זכר / '
        'נקבה) ולבחור צורות עברית נכונות.\n\n'
        'אופציונלי: אם תרצה להשתמש ב-key משלך (למשל אם ה-key '
        'המשותף נחסם זמנית, או אם אתה משתמש ב-TMDB Helper '
        'באופן כללי), פתח את הגדרות TMDB Helper וחבר key אישי. '
        'הוא יוחל אוטומטית מאותו רגע, בלי '
        'restart, וידרוס את ה-key המובנה.'
    )
    xbmcgui.Dialog().ok(dialogs_title() + ' — TMDB', body)
