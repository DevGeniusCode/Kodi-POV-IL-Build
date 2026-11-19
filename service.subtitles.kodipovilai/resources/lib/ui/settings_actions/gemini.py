# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Settings actions of the Gemini key: connect (menu, QR pairing, typed key), test, usage, TMDb notice.

import urllib.parse
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


class _PairWindow(xbmcgui.WindowDialog):
    """Full-screen-ish dialog showing a real scannable QR image
    (fetched from qrserver.com), the URL as fallback text, and a
    countdown. The previous implementation used DialogProgress
    which is text-only -- the QR was a URL printed as text, which
    is useless for non-technical users.

    Closes on Back/Esc (cancellation) or via close() called
    externally when the main flow detects the key arrived."""

    ACTION_PREVIOUS_MENU = 10
    ACTION_NAV_BACK = 92
    ACTION_STOP = 13

    def __init__(self, *args, **kwargs):
        # WindowDialog quirk: don't pass args to super, just init state
        self.cancelled = False
        self._countdown_lbl = None

    def setup(self, qr_url, url_lines, instructions_header,
              title_text='Gemini AI - התאמה מטלפון'):
        # WindowDialog coordinate space is 1280x720 by default.
        # Layout:
        #   y=0-720    full-screen semi-opaque dark background
        #   y=30-90    title
        #   y=120-500  QR image (380x380, centered)
        #   y=520-650  instruction text + URL fallback
        #   y=670-700  countdown + cancel hint

        # Dim background so QR is readable and Kodi behind is muted.
        bg_path = ('special://home/addons/service.subtitles.kodipovilai/'
                   'resources/lib/icons/dark_bg.png')
        bg = xbmcgui.ControlImage(0, 0, 1280, 720, bg_path,
                                  colorDiffuse='EE000000', aspectRatio=2)
        self.addControl(bg)

        # Title bar. Passed in, because this window is shared by the Gemini and
        # MDBList pair flows and used to say "Gemini AI" in both -- so someone
        # connecting MDBList scanned a QR headed Gemini, opened a page headed
        # MDBList, and reasonably concluded one of the two was wrong.
        title = xbmcgui.ControlLabel(
            340, 30, 600, 60,
            '[B][COLOR=FFFFD166]' + title_text + '[/COLOR][/B]',
            alignment=2 | 4, font='font30')
        self.addControl(title)

        # QR image (large, centered) -- this is the real fix. Kodi
        # fetches the URL on first display and caches the PNG.
        qr_size = 380
        qr_x = (1280 - qr_size) // 2
        qr = xbmcgui.ControlImage(qr_x, 110, qr_size, qr_size, qr_url,
                                  aspectRatio=2)
        self.addControl(qr)

        # Instructions + URL fallback. Bigger font (font14 vs font13)
        # because some users read this from across the room before
        # typing the URL into their phone manually. The instructions
        # header is now BOLD red to draw attention to the
        # "include the yellow port" warning -- common failure mode
        # for OEM Android scanners that truncate URLs at colons.
        # We also include Android-Chrome-specific troubleshooting
        # because modern Chrome (113+) defaults to "Always use
        # secure connections" which refuses HTTP loads to private
        # IPs -- failure is silent for the user, browser just
        # spins or shows an error page. iOS doesn't do this so
        # iPhone users typically don't hit it.
        instr = xbmcgui.ControlTextBox(120, 480, 1040, 210, font='font13')
        self.addControl(instr)
        text = '[B]סרוק את ה-QR עם המצלמה של הטלפון[/B] '
        text += '(אפליקציית מצלמה רגילה — לא צריך אפליקציה מיוחדת).\n\n'
        text += ('[B][COLOR=FFBF7F7F]' + instructions_header
                 + ':[/COLOR][/B]\n')
        for line in url_lines:
            text += '   • ' + line + '\n'
        text += ('\n[B][COLOR=FFFFD166]ה-Chrome של אנדרואיד '
                 'לא נפתח?[/COLOR][/B] כבה ב-Chrome: '
                 'Settings → Privacy → "Always use secure '
                 'connections", או נסה דפדפן אחר (Firefox/Brave/'
                 'Samsung Internet). או חזור ל-Kodi ובחר "הזנה '
                 'ידנית".\n')
        text += ('[B][COLOR=FFFFD166]באייפון קיבלת 400?[/COLOR][/B] '
                 'הסתכל ב-fingerprint בעמוד "ה-key נשלח" וודא '
                 'שתואם בדיוק למפתח שהעתקת מ-AI Studio. אם תואם '
                 'אבל עדיין נדחה — המפתח עצמו לא תקין; צור חדש.')
        instr.setText(text)

        # Countdown / cancel hint
        self._countdown_lbl = xbmcgui.ControlLabel(
            340, 668, 600, 30, '',
            alignment=2 | 4, font='font12')
        self.addControl(self._countdown_lbl)

    def update_countdown(self, seconds_left):
        if self._countdown_lbl is None:
            return
        try:
            mm, ss = divmod(int(max(0, seconds_left)), 60)
            self._countdown_lbl.setLabel(
                '[COLOR=FFB7C4CF]ממתין לקבלת ה-key... '
                '({0:02d}:{1:02d} עד פג תוקף)  •  '
                'לביטול: Back[/COLOR]'.format(mm, ss))
        except Exception:
            pass

    def onAction(self, action):
        if action.getId() in (
            self.ACTION_PREVIOUS_MENU,
            self.ACTION_NAV_BACK,
            self.ACTION_STOP,
        ):
            self.cancelled = True
            self.close()


def _run_pair_qr(ps, title_text='Gemini AI - התאמה מטלפון'):
    """Show a scannable QR + URL fallback for an ALREADY-CREATED PairServer,
    poll for the submitted key until it arrives / the user cancels / a 5-min
    deadline, then shut the server down. Returns the submitted key (or '').
    Shared verbatim by the Gemini and MDBList pair flows."""
    import time as _time

    # Primary URL: prefer LAN IP (works for other devices on the
    # same WiFi AND on the same device's browser via localhost
    # because the pair server binds 0.0.0.0). Fall back to
    # localhost-only when LAN detection failed (e.g. cellular).
    primary = ps.url_lan or ps.url_local
    qr_url = ('https://api.qrserver.com/v1/create-qr-code/'
              '?size=380x380&qzone=1&data=' +
              _url_quote(primary))

    # The QR encodes the full URL with port -- we've verified this
    # with a real QR decoder. The text fallback below the QR is what
    # we worry about, because some Android OEM camera apps (Samsung
    # Bixby Vision, Xiaomi MIUI scanner) display the URL truncated
    # at the colon -- the user sees "http://10.0.0.5" and types
    # that, missing the port. So we render the port in a bright
    # accent colour and add an explicit note about it.
    def _highlight_port(url):
        # url is 'http://host:port' or 'http://[v6]:port' etc.
        # Find the LAST colon (port separator). If there's no port
        # we just return the URL as-is.
        if url.count(':') < 2:
            return url
        host_part, port_part = url.rsplit(':', 1)
        # 8-digit AARRGGBB with FF alpha -- the old 6-digit form (ffd166) was
        # parsed by Kodi as 0x00ffd166 (alpha 00 = fully TRANSPARENT), which
        # made the port render invisible; the URL then looked portless. Bold
        # too, so the port stays visible even if a skin mangles the colour.
        return '{0}[B][COLOR=FFFFD166]:{1}[/COLOR][/B]'.format(host_part, port_part)

    # Show EVERY detected LAN IP. On devices with multiple network
    # interfaces (Android TV with WiFi+Ethernet, laptop with VPN+WiFi)
    # the single default-route IP we used to pick can be on a
    # different subnet from the user's phone -- the phone tries to
    # reach it, fails with "address not found", and the user assumes
    # the addon is broken. Listing all candidates lets them try each.
    lan_urls = ps.url_lans or []
    if lan_urls:
        url_lines = []
        if len(lan_urls) == 1:
            url_lines.append('מטלפון אחר ב-WiFi:  '
                             + _highlight_port(lan_urls[0]))
        else:
            url_lines.append('מטלפון אחר ב-WiFi (נסה כל אחת עד שאחת '
                             'תיפתח):')
            for u in lan_urls:
                url_lines.append('     • ' + _highlight_port(u))
        url_lines.append('מאותו מכשיר:  '
                         + _highlight_port(ps.url_local))
        instructions_header = (
            'או פתח את אחת מהכתובות בדפדפן (חובה כולל החלק הצהוב — '
            'הפורט)')
    else:
        url_lines = (
            'פתח בדפדפן:  ' + _highlight_port(ps.url_local),
            '(לא זוהתה כתובת LAN -- נגיש רק מאותו מכשיר)',
        )
        instructions_header = (
            'או פתח את הכתובת בדפדפן (חובה כולל החלק הצהוב — '
            'הפורט)')

    deadline = _time.time() + 300  # 5 min cap
    window = None
    try:
        window = _PairWindow()
        window.setup(qr_url, url_lines, instructions_header, title_text)
        window.show()

        while _time.time() < deadline:
            if window.cancelled:
                break
            key = ps.received_key()
            if key:
                break
            window.update_countdown(deadline - _time.time())
            xbmc.sleep(500)
    finally:
        try:
            if window:
                window.close()
                del window
        except Exception:
            pass
        ps.shutdown()

    return ps.received_key()


def _gemini_pair_flow(gemini, gemini_pair):
    """Spin up the local pair server, show a scannable QR image in
    a custom window, poll for the submitted key, validate."""
    
    try:
        ps = gemini_pair.PairServer()
    except Exception as e:
        xbmcgui.Dialog().ok(
            dialogs_title(),
            'נכשלה הפעלת שרת התאמה: {0}\n\n'
            'אפשר לחזור לתפריט ולבחור "הזנה ידנית" במקום.'
            .format(str(e)[:80]))
        return
    key = _run_pair_qr(ps)
    if not key:
        return  # user cancelled or timeout
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


def _url_quote(s):
    try:
        return urllib.parse.quote(s, safe='')
    except Exception:
        return s


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
