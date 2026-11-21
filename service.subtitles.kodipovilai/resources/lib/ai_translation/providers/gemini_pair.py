# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# Local HTTP pair server for the Gemini API key.
#
# When the user picks "Pair from another device" in Connect
# Services, this module:
#   1. Picks a free port on the local machine and a random per-session
#      secret path segment (10 hex chars).
#   2. Starts a tiny http.server on 0.0.0.0:<port> bound to all
#      interfaces (so it's reachable from LAN AND localhost).
#   3. Returns the URL(s) + a shared dict the caller can poll for
#      the submitted key. Every URL carries the secret segment
#      (http://<ip>:<port>/<secret>): the QR code encodes it, so scanning
#      costs the user nothing, but another device on the LAN that only
#      finds the open port gets a 404 and can neither read the form nor
#      overwrite the key.
#   4. Serves GET /<secret> -- a minimal RTL Hebrew HTML form that takes
#      an API key. Serves POST /<secret> -- stashes the key into
#      the shared dict and returns a "thanks" page.
#   5. Caller calls shutdown() when done (key received or timed
#      out) to free the port.
#
# Why localhost matters: a user running Kodi on their PHONE with
# cellular data has no LAN at all. Showing them a 192.168 URL is
# useless. But they CAN open http://localhost:<port>/<secret> in the
# phone's browser -- the HTTP server runs on the same device and
# is reachable via the loopback interface regardless of network
# state. So we always show the localhost URL as the fallback when no
# LAN address was found.
#
# Start-up speed: http.server.HTTPServer.server_bind() calls
# socket.getfqdn(), a reverse-DNS lookup that blocked for ~9.5 s on a
# machine with a virtual / link-local adapter. _ThreadingServer overrides
# server_bind so the server is up in milliseconds. The LAN address comes
# from Kodi (xbmc.getIPAddress()), with UDP "connect" probes (no packets
# sent, no DNS, no subprocess) as the fallback.
#
# No third-party deps -- only Python stdlib (http.server, socket,
# threading, secrets). Validated against py3.8+ which is what Kodi 21
# ships.

import http.server
import re
import secrets
import socket
import socketserver
import threading
import unicodedata
import urllib.parse


# Gemini API keys come in two formats Google issues from AI Studio:
#   * Legacy "AIza..." -- 39 ASCII chars, alphanumeric + dash + underscore.
#   * New "AQ.AbcDef..." (rolled out late 2025) -- ~50 ASCII chars and
#     INCLUDES PERIODS as part of the key body. Without `.` in the
#     allow-list below the sanitizer would strip every period and ship
#     `AQAbcDef...` to Google -- which is rejected as malformed (400).
# The filter stays tight on every OTHER non-key codepoint (curly
# quotes, NBSP, ZWSP, RTL marks etc. that iOS Safari inserts) so a
# corrupted paste still gets cleaned up; we just add `.` to the
# allow-set so new-format keys pass through intact.
_KEY_CHARSET_RE = re.compile(r'[^A-Za-z0-9_\-\.]')

# iOS Smart Punctuation replaces ASCII hyphen-minus (U+002D) with
# typographically "pretty" dash variants -- em-dash for `--`, sometimes
# en-dash for stand-alone `-`. Those are SEPARATE Unicode codepoints
# from U+002D and NFKC normalisation does NOT collapse them back.
# Without explicit handling, the allow-list above would STRIP them
# entirely and an iPhone-pasted key like `AIza...XYZ-ABC` would
# arrive at our server as `AIza...XYZABC` -- Google rejects it as
# malformed. Mapping every dash-shaped Unicode codepoint back to
# ASCII `-` BEFORE the allow-list preserves the key.
_DASH_LIKE = (
    '‐'  # hyphen
    '‑'  # non-breaking hyphen
    '‒'  # figure dash
    '–'  # en dash         <- iOS often picks this
    '—'  # em dash         <- iOS for "--"
    '―'  # horizontal bar
    '⁃'  # hyphen bullet
    '−'  # minus sign
    '﹘'  # small em dash
    '﹣'  # small hyphen-minus
    '－'  # full-width hyphen-minus
)
_DASH_TO_ASCII = str.maketrans({c: '-' for c in _DASH_LIKE})


def _sanitize_key(raw):
    """Strip every byte iOS Safari might have inserted into a pasted
    API key. Order matters: NFKC normalises full-width / smart-quoted
    variants to their ASCII equivalents BEFORE we filter, so e.g. a
    smart-quoted "AIza..." becomes plain quotes (then stripped) and
    a full-width A maps to ASCII A. THEN we coerce every dash-shaped
    codepoint back to ASCII `-` (iOS Smart Punctuation substitution
    that NFKC misses). Finally the allow-list keeps only the charset
    Gemini keys actually use."""
    if not raw:
        return ''
    # Normalize unicode confusables to their ASCII canonical form.
    normalised = unicodedata.normalize('NFKC', raw)
    # Convert iOS smart-dashes (em-dash, en-dash, etc.) back to '-'.
    # Without this, the allow-list would drop them and shorten the
    # key, which Google rejects.
    normalised = normalised.translate(_DASH_TO_ASCII)
    # Remove ASCII whitespace plus NBSP, ZWSP, BOM, RTL/LTR marks etc.
    # _KEY_CHARSET_RE catches all of these by allow-listing only the
    # chars Gemini keys actually use.
    cleaned = _KEY_CHARSET_RE.sub('', normalised)
    return cleaned


# Form served on GET /<secret>. Plain UTF-8 HTML with inline CSS, RTL.
# Submitted back to the SAME url (action="") as application/x-www-form-urlencoded,
# so the secret segment is never repeated in the markup and the page works with
# or without a trailing slash.
# <meta name="referrer" content="no-referrer">: the page lives at the LAN address of the
# Kodi box; the AI Studio link below must not hand that address to Google in the Referer header.
_HTML_FORM = '''<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>__DIALOG_TITLE__ - Gemini API Key</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, Arial, sans-serif;
           margin: 0; padding: 24px; background: #101820; color: #f6f1df; }
    .card { max-width: 540px; margin: 32px auto; padding: 28px;
            background: #172635; border: 1px solid #34495e;
            border-radius: 16px; }
    h1 { margin: 0 0 8px 0; color: #ffd166; font-size: 1.4rem; }
    p { line-height: 1.6; color: #b7c4cf; font-size: 0.95rem; }
    label { display: block; margin-top: 16px; font-weight: bold; }
    a.cta { display: block; margin: 10px 0 4px 0; padding: 14px; text-align: center;
            font-size: 1.1rem; font-weight: bold; text-decoration: none;
            color: #101820; background: #ffd166; border-radius: 8px; }
    a.cta:active { background: #e0b54a; }
    .dom { text-align: center; margin: 4px 0 0 0; font-size: 0.85rem; }
    input[type=password] { width: 100%; box-sizing: border-box; margin-top: 8px;
                       padding: 14px; font-size: 1.1rem; direction: ltr;
                       background: #0a0f15; color: #f6f1df;
                       border: 1px solid #34495e; border-radius: 8px; }
    button { width: 100%; margin-top: 20px; padding: 14px; font-size: 1.1rem;
             font-weight: bold; color: #101820; background: #ffd166;
             border: none; border-radius: 8px; cursor: pointer; }
    button:disabled { opacity: .5; cursor: default; }
    button:active { background: #e0b54a; }
    #fp { direction: ltr; text-align: center; margin-top: 10px; color: #f6f1df; }
    #msg { margin-top: 14px; padding: 12px; border-radius: 8px; font-size: 0.95rem;
           line-height: 1.5; display: none; }
    .ok { background: #16351f; color: #a7e0b0; display: block !important; }
    .err { background: #351a1a; color: #e6a6a6; display: block !important; }
    .info { background: #14202c; color: #b7c4cf; display: block !important; }
    .small { font-size: 0.85rem; color: #b7c4cf; margin-top: 12px; }
    bdi { direction: ltr; unicode-bidi: isolate; color: #f6f1df; }
  </style>
</head>
<body>
  <div class="card">
    <h1>חיבור Gemini AI</h1>
    <p>1. צור מפתח API חינמי ב-Google AI Studio (נפתח בלשונית חדשה):</p>
    <a class="cta" href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer">Google AI Studio</a>
    <p class="dom"><bdi dir="ltr">aistudio.google.com/apikey</bdi></p>
    <p>
      2. העתק את המפתח, חזור לכאן והדבק אותו בשדה. נוודא מול Google
      שהמפתח תקין, ורק אז נשלח אותו ל-Kodi.
    </p>
    <form method="POST" action="" id="f">
      <label for="key">Gemini API Key</label>
      <!--
        iOS/WebKit note: type=password suppresses the smart-punctuation /
        autocorrect that silently corrupt a pasted key on iOS (every iOS
        browser is WebKit). We also read the RAW clipboard on paste (bypassing
        field-level mangling) and validate the key against Google FROM THE PHONE
        before submitting -- so a corrupted / bad key is caught here with a clear
        message instead of a confusing 401 back in Kodi. The server still
        sanitizes what it receives.
      -->
      <input type="password" id="key" name="key"
             autocomplete="off" autocapitalize="off"
             autocorrect="off" spellcheck="false"
             inputmode="verbatim"
             placeholder="AIza... או AQ.Ab8..." required>
      <div id="fp"></div>
      <button type="submit" id="go">בדוק ושלח ל-Kodi</button>
    </form>
    <div id="msg"></div>
    <p class="small">
      טיפ: בטלפון לחץ "Copy" ב-AI Studio, אז כאן בשדה למעלה
      לחץ פעם ארוכה והדבק.
    </p>
  </div>
<script>
"use strict";
// sanitize() is byte-parity with the server-side _sanitize_key (proven).
var DASH = /[‐‑‒–—―⁃−﹘﹣－]/g;
function sanitize(raw) {
  if (!raw) return '';
  return raw.normalize('NFKC').replace(DASH, '-').replace(/[^A-Za-z0-9_.-]/g, '');
}
function fingerprint(k) {
  var h = k.length >= 8 ? k.slice(0, 8) : k;
  var t = k.length >= 16 ? k.slice(-8) : '';
  return h + (t ? '…' + t : '') + '   (' + k.length + ' chars)';
}
var elKey = document.getElementById('key');
var elFp = document.getElementById('fp');
var elMsg = document.getElementById('msg');
var form = document.getElementById('f');
var btn = document.getElementById('go');
function msg(t, c) { elMsg.textContent = t; elMsg.className = c; }
// Read the RAW clipboard on paste, before iOS/WebKit can mangle it in the field.
elKey.addEventListener('paste', function (e) {
  try {
    var raw = (e.clipboardData || window.clipboardData).getData('text/plain');
    if (raw) { e.preventDefault(); elKey.value = sanitize(raw); elFp.textContent = fingerprint(elKey.value); }
  } catch (err) { /* fall back to the field value */ }
});
elKey.addEventListener('input', function () { elFp.textContent = elKey.value ? fingerprint(sanitize(elKey.value)) : ''; });
// Validate the key against Google FROM THE PHONE (x-goog-api-key header so AQ.
// keys work). Classify by STATUS, not r.ok: 400/401/403 = truly bad key (false);
// 429 / 5xx / network / timeout = ambiguous (null) -> submit anyway and let Kodi
// decide. (Mirrors gemini.py's InvalidKey-vs-RateLimited/Overload distinction, so
// a valid key hitting a transient rate limit is never mislabeled "bad key".)
function validateKey(key) {
  var ctrl = (typeof AbortController === 'function') ? new AbortController() : null;
  var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 9000) : null;
  var opts = { method: 'GET', headers: { 'x-goog-api-key': key }, cache: 'no-store' };
  if (ctrl) opts.signal = ctrl.signal;
  return fetch('https://generativelanguage.googleapis.com/v1beta/models', opts)
    .then(function (r) {
      if (timer) clearTimeout(timer);
      if (r.ok) return true;
      if (r.status === 400 || r.status === 401 || r.status === 403) return false;
      return null;
    })
    .catch(function () { if (timer) clearTimeout(timer); return null; });
}
form.addEventListener('submit', function (e) {
  e.preventDefault();
  var key = sanitize(elKey.value || '');
  elKey.value = key;
  elFp.textContent = key ? fingerprint(key) : '';
  if (!key) { msg('הזן API key.', 'err'); return; }
  if (typeof fetch !== 'function') { form.submit(); return; }   // ancient browser: let Kodi validate
  btn.disabled = true;
  elKey.readOnly = true;                                         // lock the field during the async check
  msg('בודק את המפתח מול Google…', 'info');
  validateKey(key).then(function (ok) {
    if (ok === false) {
      msg('Google דחה את המפתח הזה. העתק אותו שוב במלואו מ-AI Studio ונסה שוב ' +
          '(ודא שאין רווח או תו חסר). אם הוא נכון אבל עדיין נדחה — צור מפתח חדש.', 'err');
      btn.disabled = false; elKey.readOnly = false; return;
    }
    if (ok === null) { msg('לא הצלחנו לאמת מול Google (בעיית רשת?) — שולח בכל זאת, Kodi יבדוק.', 'info'); }
    else { msg('המפתח תקין ✓  שולח ל-Kodi…', 'info'); }
    elKey.value = key;                                           // re-assert the exact validated value
    form.submit();   // readOnly fields ARE submitted; native POST, no submit-event re-fire
  });
});
</script>
</body>
</html>
'''

# Echoes a fingerprint of the received key so the user can verify
# nothing got corrupted in transit (iOS autocorrect, etc.). Format
# is FIRST-FOUR…LAST-FOUR + length, e.g. "AIza…9xY7 (39 chars)".
# We deliberately do NOT show the full key (the page might be
# screenshotted/photographed by support or shoulder-surfed).
# The "back" links use href="" (the current URL): the form lives at the secret url this page was posted to.
_HTML_DONE_OK = '''<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>נשלח</title>
<style>body{{font-family:Arial,sans-serif;background:#101820;color:#f6f1df;
text-align:center;padding:60px 20px}}h1{{color:#7fbf7f;font-size:2rem}}
p{{color:#b7c4cf}}code{{background:#0a0f15;padding:6px 12px;border-radius:6px;
display:inline-block;margin:8px;direction:ltr;font-size:1.1rem}}</style></head>
<body><h1>✓ ה-key נשלח ל-Kodi</h1>
<p>השרת קיבל:</p>
<p><code>{fingerprint}</code></p>
<p>ודא ש-{first4} ו-{last4} תואמים את ה-key המקורי שהעתקת.
אם לא — לחץ <a href="" style="color:#ffd166">חזור</a> והדבק שוב.</p>
<p>אפשר לסגור את הדף ולחזור ל-Kodi.</p></body></html>
'''

_HTML_DONE_EMPTY = '''<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>שגיאה</title>
<style>body{font-family:Arial,sans-serif;background:#101820;color:#f6f1df;
text-align:center;padding:60px 20px}h1{color:#bf7f7f;font-size:2rem}
p{color:#b7c4cf}a{color:#ffd166}</style></head>
<body><h1>שדה ריק</h1>
<p>לא הוזן key. <a href="">חזור לטופס</a>.</p></body></html>
'''

# A pasted key is ~60 bytes; a form post well past this is not ours.
_MAX_BODY = 8192


def _dialog_title():
    """The brand title (sub_utils.DIALOG_TITLE); imported lazily to keep this module stdlib-only."""
    try:
        from resources.lib.core.kodi import DIALOG_TITLE
        return DIALOG_TITLE
    except Exception:
        return ''


def _make_handler(state, secret):
    """Build a request-handler class that closes over `state`
    (a dict the caller polls for the submitted key) and the session
    `secret`: only /<secret> (and /<secret>/) answers; every other
    path is a 404, so a LAN device that finds the open port cannot
    read the form or post a key."""
    html_form = _HTML_FORM.replace('__DIALOG_TITLE__', _dialog_title())
    route = '/' + secret

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 10   # a stalled client never parks a handler thread for long

        def _send(self, code, body, ctype='text/html; charset=utf-8'):
            data = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)

        def _route_ok(self):
            path = (self.path or '').split('?', 1)[0]
            return path == route or path == route + '/'

        def do_GET(self):
            if self._route_ok():
                self._send(200, html_form)
                return
            self._send(404, '<h1>404</h1>')

        def do_POST(self):
            if not self._route_ok():
                self._send(404, '<h1>404</h1>')
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > _MAX_BODY:
                self._send(400, '<h1>400</h1>')
                return
            try:
                raw = self.rfile.read(length).decode('utf-8',
                                                     errors='replace')
            except Exception:
                self._send(400, '<h1>400</h1>')
                return
            fields = urllib.parse.parse_qs(raw)
            submitted = (fields.get('key', [''])[0] or '').strip()
            # Aggressive sanitisation: iOS Safari can sneak smart
            # quotes / non-breaking spaces / zero-width chars into a
            # pasted API key even with autocorrect=off. The Gemini
            # API rejects those with 400 and the user sees no clue
            # why. _sanitize_key normalises NFKC then keeps only the
            # ASCII charset Google's keys actually use.
            key = _sanitize_key(submitted)
            if not key:
                self._send(200, _HTML_DONE_EMPTY)
                return
            # Stash for the caller's polling loop. We deliberately
            # don't validate here -- the caller does that with the
            # full Gemini test_key flow so the user sees the
            # error in the Kodi UI, not in the browser.
            state['received_key'] = key
            # Build a fingerprint the user can sanity-check against
            # the key they actually copied. Show first 8 + last 8
            # chars: a 39-char Gemini key has its middle 23 hidden,
            # which is plenty for shoulder-surf safety while giving
            # the user enough material to confirm no mid-string
            # corruption survived our sanitiser.
            head = (key[:8] if len(key) >= 8 else key) or '?'
            tail = (key[-8:] if len(key) >= 16 else '') or '...'
            fingerprint = '{0}…{1}   ({2} chars)'.format(
                head, tail, len(key))
            self._send(200, _HTML_DONE_OK.format(
                fingerprint=fingerprint, first4=head, last4=tail))

        # Silence stderr access-log spam.
        def log_message(self, fmt, *args):
            pass

    return Handler


class _ThreadingServer(socketserver.ThreadingMixIn,
                       http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        """HTTPServer.server_bind() ends with socket.getfqdn(host): a reverse-DNS lookup that took ~9.5 s on a
        machine with a virtual / link-local adapter, with the dialog not even open yet. Nothing here reads
        server_name, so bind the socket and record the address without any lookup."""
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def _is_private_lan_ip(ip):
    """True for the RFC 1918 ranges + 100.64/10 (CGNAT-ish, occasionally
    seen on home routers). Excludes 127/8 (loopback), 169.254/16
    (link-local that means "DHCP failed")."""
    if not ip or ip.startswith('127.') or ip.startswith('169.254.'):
        return False
    if ip.startswith('10.') or ip.startswith('192.168.'):
        return True
    if ip.startswith('172.'):
        try:
            second = int(ip.split('.')[1])
            return 16 <= second <= 31
        except (ValueError, IndexError):
            return False
    if ip.startswith('100.'):
        try:
            second = int(ip.split('.')[1])
            return 64 <= second <= 127
        except (ValueError, IndexError):
            return False
    return False


def _probe_outbound_ip(target):
    """Open a UDP socket "connected" to `target` (no packets sent)
    and read the local address the OS picked. Returns '' on failure."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        s.connect((target, 1))
        return s.getsockname()[0]
    except Exception:
        return ''
    finally:
        s.close()


def kodi_lan_ip():
    """The LAN address Kodi itself reports for the active interface (xbmc.getIPAddress()), or '' when it is not a
    private LAN address (loopback, 'Busy', empty, a public address) or Kodi is not there."""
    try:
        import xbmc
        ip = (xbmc.getIPAddress() or '').strip()
    except Exception:
        return ''
    return ip if _is_private_lan_ip(ip) else ''


def get_lan_ips():
    """The LAN IPv4 address(es) the phone should try, best first. Kodi's own address when it has one (one entry,
    the interface Kodi is actually on). Otherwise the fallback: UDP "connect" probes to the default route and the
    private ranges -- no packets, no DNS, no subprocess, about a millisecond each -- keeping the private addresses
    they find (a multi-NIC box may list more than one)."""
    ip = kodi_lan_ip()
    if ip:
        return [ip]
    found = []
    for target in ('8.8.8.8', '10.0.0.1', '192.168.1.1', '172.16.0.1',
                   '100.64.0.1'):
        ip = _probe_outbound_ip(target)
        if ip and ip not in found and _is_private_lan_ip(ip):
            found.append(ip)
    return found


def find_free_port(preferred=(8765, 8766, 8767, 8768, 8769)):
    """Try a small set of preferred ports first (so the firewall
    prompt is consistent across runs). Fall back to an
    OS-allocated port if all are taken."""
    for port in preferred:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('', port))
            s.close()
            return port
        except OSError:
            continue
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class PairServer:
    """Tiny wrapper so the caller can `with PairServer() as ps:`
    style use it. After construction:
      ps.port           -- the bound port number
      ps.secret         -- the random per-session path segment
      ps.lan_ip         -- detected primary LAN IP or None
      ps.lan_ips        -- list of LAN IPv4 addresses (Kodi's own one, or
                           the probe fallback's)
      ps.url_lan        -- 'http://<lan_ip>:<port>/<secret>' or None
                           (primary URL -- first entry of url_lans)
      ps.url_lans       -- list of 'http://<ip>:<port>/<secret>' for every IP
      ps.url_local      -- 'http://localhost:<port>/<secret>'
      ps.received_key() -- returns the submitted key or '' if none yet
      ps.shutdown()     -- stops the server thread (idempotent)
    """

    def __init__(self):
        self._state = {'received_key': None}
        self.secret = secrets.token_hex(5)
        self.port = find_free_port()
        self.lan_ips = get_lan_ips()
        self.lan_ip = self.lan_ips[0] if self.lan_ips else None
        handler = _make_handler(self._state, self.secret)
        self._server = _ThreadingServer(('', self.port), handler)
        # poll_interval 0.1: shutdown() returns within ~0.1 s once the key has arrived.
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={'poll_interval': 0.1}, daemon=True)
        self._thread.start()
        self.url_lans = ['http://{0}:{1}/{2}'.format(ip, self.port,
                                                     self.secret)
                         for ip in self.lan_ips]
        self.url_lan = self.url_lans[0] if self.url_lans else None
        self.url_local = 'http://localhost:{0}/{1}'.format(self.port,
                                                           self.secret)
        self._closed = False

    def received_key(self):
        return self._state.get('received_key') or ''

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()
