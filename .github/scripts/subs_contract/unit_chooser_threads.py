# Unit checks of the chooser window's threading rule (21 Nov 2026, after the Kodi crash of 20 Nov 2026: an access violation in
# RetardedAsyncCallbackHandler::makePendingCalls, a queued window callback dispatched on memory a xbmcgui.ListItem had reused,
# while a background thread rebuilt list 100). Pure Python, no Kodi. Run:
#   python -I .github/scripts/subs_contract/unit_chooser_threads.py
# Exit code 0 = every check passed.
#
# The rule: the window is served by ONE thread (the script's own: `ui.chooser._run_owner_loop`, which replaced doModal()). Every
# xbmcgui.ListItem, every control call (reset / addItems / selectItem / setFocusId / getControl) and every window property is
# made on that thread; `_complete()` and `_run_manual()` run on background threads and produce plain Python data only.
#
# The fake runtime below emulates what Kodi does for a shown WindowXMLDialog: `show()` queues onInit, and
# `Monitor.waitForAbort()` runs the queued callbacks (MakePendingCalls) on the thread that called it. Every ListItem and every
# control / property call records the thread it ran on.

import collections
import os
import sys
import threading
import time
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ADDON = os.path.join(ROOT, 'service.subtitles.kodipovilai')
sys.path.insert(0, ADDON)

OWNER = threading.get_ident()      # this thread plays the script's thread (the one Kodi dispatches callbacks on)
BAD = []                           # (what, thread name) of every window object / call made off the owner thread
LISTITEMS = []                     # label of every ListItem made, in order
CREATED = []                       # every window the chooser made
BG = set()                         # thread ids the fake search ran on
ROWS_THREADS = set()               # thread ids chooser_rows.build ran on
FOCUS = []                         # setFocusId calls
GATE = threading.Event()           # releases the fake full provider search


def owner_only(what):
    if threading.get_ident() != OWNER:
        BAD.append((what, threading.current_thread().name))


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


class FakeListItem(object):
    def __init__(self, label=''):
        owner_only('ListItem()')
        self.label = label
        self.props = {}
        LISTITEMS.append(label)

    def setArt(self, art):
        owner_only('ListItem.setArt')

    def setProperty(self, k, v):
        owner_only('ListItem.setProperty')
        self.props[k] = v


class FakeList(object):
    def __init__(self):
        self.items = []
        self.sel = 0

    def reset(self):
        owner_only('list.reset')
        self.items = []

    def addItems(self, items):
        owner_only('list.addItems')
        assert all(isinstance(i, FakeListItem) for i in items)
        self.items = list(items)

    def size(self):
        owner_only('list.size')
        return len(self.items)

    def getSelectedPosition(self):
        owner_only('list.getSelectedPosition')
        return self.sel if self.items else -1

    def selectItem(self, n):
        owner_only('list.selectItem')
        self.sel = n


class FakeAction(object):
    def __init__(self, i):
        self.i = i

    def getId(self):
        return self.i


class FakeWindowXMLDialog(object):
    def __init__(self, *a, **k):
        self._props = {}
        self._list = FakeList()
        self._cb = collections.deque()
        self.shown = False
        self.close_calls = 0
        self.modal_calls = 0
        CREATED.append(self)

    def doModal(self):                  # the blocking Kodi loop that nothing of ours can run in: it must not be used
        self.modal_calls += 1
        raise AssertionError('doModal() must not be used')

    def show(self):
        owner_only('show')
        self.shown = True
        self._cb.append(('onInit', None))

    def close(self):
        owner_only('close')
        self.close_calls += 1

    def getControl(self, cid):
        owner_only('getControl')
        return self._list

    def setProperty(self, k, v):
        owner_only('window.setProperty')
        self._props[k] = v

    def getProperty(self, k):
        owner_only('window.getProperty')
        return self._props.get(k, '')

    def clearProperty(self, k):
        owner_only('window.clearProperty')
        self._props.pop(k, None)

    def setFocusId(self, cid):
        owner_only('setFocusId')
        FOCUS.append(cid)

    # what Kodi does between two slices of Monitor.waitForAbort: MakePendingCalls on the calling thread
    def dispatch(self):
        while self._cb:
            kind, arg = self._cb.popleft()
            if kind == 'onInit':
                self.onInit()
            elif kind == 'onAction':
                self.onAction(FakeAction(arg))
            elif kind == 'onClick':
                self.onClick(arg)


class FakeMonitor(object):
    """waitForAbort: one slice of waiting, the script of the scenario (the viewer's presses), then the pending callbacks."""
    driver = None
    abort = False
    ticks = 0

    def waitForAbort(self, timeout=0):
        FakeMonitor.ticks += 1
        time.sleep(0.002)
        win = CREATED[-1] if CREATED else None
        if FakeMonitor.ticks > 3000:
            FakeMonitor.abort = True
            BAD.append(('the loop did not end', 'owner'))
        elif win is not None and FakeMonitor.driver is not None:
            FakeMonitor.driver(win)
        if win is not None and not FakeMonitor.abort:
            win.dispatch()
        return FakeMonitor.abort


class _Win(object):
    def __init__(self):
        self.p = {}

    def getProperty(self, k):
        return self.p.get(k, '')

    def setProperty(self, k, v):
        self.p[k] = v

    def clearProperty(self, k):
        self.p.pop(k, None)


WIN = _Win()
_stub('xbmc', executeJSONRPC=lambda s: '{}', sleep=lambda ms: None, log=lambda *a, **k: None, LOGINFO=1, LOGDEBUG=0,
      LOGWARNING=2, LOGERROR=3, Monitor=FakeMonitor,
      Player=lambda: types.SimpleNamespace(isPlayingVideo=lambda: True, getPlayingFile=lambda: 'x.mkv'))
_stub('xbmcaddon', Addon=lambda *a, **k: types.SimpleNamespace(getSetting=lambda k: '', getAddonInfo=lambda k: ''))
_stub('xbmcvfs', translatePath=lambda p: p)
_stub('xbmcgui', Window=lambda _id: WIN, Dialog=lambda: types.SimpleNamespace(notification=lambda *a, **k: None),
      DialogProgressBG=lambda: types.SimpleNamespace(create=lambda *a: None, close=lambda: None),
      INPUT_ALPHANUM=0, INPUT_NUMERIC=1, WindowXMLDialog=FakeWindowXMLDialog, ListItem=FakeListItem)

FAILED = []


def check(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        FAILED.append(name)


# ---- the detector itself: a ListItem made off the owner thread is reported ------------------------------------------------
t = threading.Thread(target=lambda: FakeListItem('x'), name='negative-control')
t.start()
t.join()
check('detector: a ListItem made on another thread is reported', len(BAD) == 1 and BAD[0][0] == 'ListItem()')
del BAD[:], LISTITEMS[:]

from resources.lib.core import video_context
from resources.lib.fetching import manual
from resources.lib.ui import chooser, chooser_rows, manual_prompts


def cand(n, lang='he'):
    return {'filename': 'Show.S01E0%d.1080p.WEB-DL.x264-GRP' % n, 'language': lang, 'link': 'L%d' % n}


QUICK = [cand(1), cand(2)]
FULL = [cand(i) for i in range(1, 6)]
MANUAL = [cand(7), cand(8), cand(9)]


def fake_items(info, quick=False):
    if quick:
        info['_list_pending'] = True
        return list(QUICK)
    BG.add(threading.get_ident())
    if info.get('_manual'):
        return list(MANUAL)
    GATE.wait(10)
    return list(FULL)


_build = chooser_rows.build


def traced_build(*a, **k):
    ROWS_THREADS.add(threading.get_ident())
    return _build(*a, **k)


chooser_rows.build = traced_build
chooser._chooser_items = fake_items
chooser._searching_notice = lambda: None
chooser._pause_for_chooser = lambda: False
chooser._ai_context = lambda info: None
video_context.current_video_info = lambda: {'title': 'Show', 'year': '2026'}
manual_prompts.ask = lambda info, *a, **k: 'Other Film 2020'
manual.resolve_ids = lambda si: (si, True)

THREADS = []
_Thread = threading.Thread


class RecordingThread(_Thread):
    def __init__(self, *a, **k):
        _Thread.__init__(self, *a, **k)
        THREADS.append(self)


threading.Thread = RecordingThread


def run(driver):
    """One chooser session: the viewer's presses come from `driver(win)`, called once per Monitor tick."""
    del CREATED[:], BAD[:], LISTITEMS[:], FOCUS[:], THREADS[:]
    BG.clear()
    ROWS_THREADS.clear()
    GATE.clear()
    FakeMonitor.driver = staticmethod(driver)
    FakeMonitor.abort = False
    FakeMonitor.ticks = 0
    result = chooser._show()
    GATE.set()
    for th in THREADS:
        th.join(5)
    return result


def sizes(win):
    return len(win._list.items)


# ---- scenario A: automatic list, manual search, back to the list, Back (ACTION_NAV_BACK 92) ---------------------------------
STAGE = {'n': 0, 'at': 0}


def drive_a(win):
    n = STAGE['n']
    if n == 0 and win.shown and sizes(win) == 2:
        GATE.set()                                     # the full provider search ends
        STAGE['n'] = 1
    elif n == 1 and sizes(win) == 5 and not win.pending:
        win._cb.append(('onClick', 202))               # the viewer presses "manual search"
        STAGE['n'] = 2
    elif n == 2 and win.manual and sizes(win) == 3 and not win.manual_busy:
        win._cb.append(('onClick', 203))               # "back to the list"
        STAGE['n'] = 3
    elif n == 3 and not win.manual and sizes(win) == 5:
        win._cb.append(('onAction', 92))               # Back
        STAGE['n'] = 4


STAGE['n'] = 0
res = run(drive_a)
w = CREATED[0] if CREATED else None
check('A: the window opened with show() (doModal never used) and the session returned', res is True and w is not None and w.shown
      and w.modal_calls == 0)
check('A: the quick list, the full list, the manual list and the automatic list again all reached list 100 (2, 5, 3, 5 rows)',
      STAGE['n'] == 4 and w is not None and sizes(w) == 5 and w.items == FULL and not w.manual)
check('A: no ListItem, control call or window property was made off the owner thread', not BAD and len(LISTITEMS) > 0)
check('A: the provider searches (full list and manual) ran on background threads, never on the owner thread',
      len(BG) == 2 and OWNER not in BG)
check('A: the row dicts of the background lists were built on background threads (pure Python), the quick list on the owner',
      OWNER in ROWS_THREADS and len(ROWS_THREADS - {OWNER}) == 2)
check('A: ListItems = every row of every redraw (2 + 5 + 3 + 5), all made on the owner thread', len(LISTITEMS) == 15)
check('A: Back (ACTION_NAV_BACK) closed the window once and ended the loop', w.closed and w.close_calls == 1 and not FakeMonitor.abort)
check('A: focus went to list 100 on init, after the manual results and after "back"', FOCUS == [100, 100, 100])
check('A: nothing left busy', not w.manual_busy and w.base is None)

# ---- scenario B: Esc (ACTION_PREVIOUS_MENU 10) while the full search is still running; its late result is dropped -------------
STAGE['n'] = 0


def drive_b(win):
    if STAGE['n'] == 0 and win.shown and sizes(win) == 2:
        GATE.set()                                     # the background list finishes ...
        win._cb.append(('onAction', 10))               # ... as the viewer presses Esc
        STAGE['n'] = 1


res = run(drive_b)
w = CREATED[0]
check('B: Esc (ACTION_PREVIOUS_MENU) closed the window and ended the loop', w.closed and w.close_calls == 1 and res is True)
check('B: the late background list was dropped: no ListItem after the close, the list is still the quick one',
      len(LISTITEMS) == 2 and sizes(w) == 2 and not BAD)
check('B: the background thread ended, and if it built rows it did so off the owner thread',
      len(THREADS) >= 1 and all(not th.is_alive() for th in THREADS) and len(ROWS_THREADS - {OWNER}) <= 1)
_ran = []
w._post(lambda: _ran.append('late'))
w.pump()
check('B: pump() drops a job posted for a window that is already closed', _ran == [] and w.inbox.empty())

# ---- scenario C: Kodi aborts -----------------------------------------------------------------------------------------------
STAGE['n'] = 0


def drive_c(win):
    if STAGE['n'] == 0 and win.shown and sizes(win) == 2:
        FakeMonitor.abort = True
        STAGE['n'] = 1


res = run(drive_c)
w = CREATED[0]
check('C: Kodi\'s abort ended the loop and closed the window once (what doModal() does when the application quits)',
      w.closed and w.close_calls == 1 and res is True and not BAD)

# ---- scenario D: the window closes from a click on Close (201) and from a pick path (close()) -------------------------------
STAGE['n'] = 0


def drive_d(win):
    if STAGE['n'] == 0 and win.shown and sizes(win) == 2:
        win._cb.append(('onClick', 201))
        STAGE['n'] = 1


res = run(drive_d)
w = CREATED[0]
check('D: the Close button (201) ends the loop', w.closed and w.close_calls == 1 and not BAD)

# ---- the source: no doModal in the XML window path, no window lock left -------------------------------------------------------
src = open(os.path.join(ADDON, 'resources', 'lib', 'ui', 'chooser.py'), encoding='utf-8').read()
xml_part = src[src.index('def _show():'):]          # _show() is the XML window; the pyxbmct fallback is defined above it
check('source: the XML chooser never calls doModal() and has no window lock',
      'doModal' not in xml_part and 'self.lock' not in xml_part and 'w.lock' not in xml_part)

print('%d failed' % len(FAILED))
sys.exit(1 if FAILED else 0)
