"""Permissive Kodi stub modules. Installed into sys.modules by install()."""
import sys
import types


class _Any(object):
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Any()

    def __getattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        return _Any()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __str__(self):
        return ''

    def __fspath__(self):
        return ''

    def __int__(self):
        return 0

    def __len__(self):
        return 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_STR_FUNCS = {
    'getInfoLabel', 'getAddonInfo', 'getSetting', 'translatePath', 'getLocalizedString',
    'getSettingString', 'getCondVisibility_str', 'getProperty', 'getLabel', 'getPath',
    'getRegion', 'getLanguage', 'getSkinDir',
}
_INT_ATTRS = {'LOGDEBUG': 0, 'LOGINFO': 1, 'LOGWARNING': 2, 'LOGERROR': 3, 'LOGFATAL': 4,
              'LOGNONE': 5, 'LOGNOTICE': 1, 'PLAYLIST_VIDEO': 1}


class _Mod(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        if name in _INT_ATTRS:
            return _INT_ATTRS[name]
        if name in _STR_FUNCS:
            def f(*a, **k):
                return ''
            f.__name__ = name
            return f
        if name in ('abortRequested', 'waitForAbort'):
            return lambda *a, **k: True
        if name in ('getCondVisibility', 'exists', 'isPlaying', 'isPlayingVideo'):
            return lambda *a, **k: False
        if name in ('executebuiltin', 'sleep', 'log', 'executeJSONRPC'):
            return lambda *a, **k: ''
        c = type(name, (_Any,), {})
        setattr(self, name, c)
        return c


def install():
    for n in ('xbmc', 'xbmcaddon', 'xbmcgui', 'xbmcvfs', 'xbmcplugin', 'chardet', 'telethon',
              'urllib3', 'qrcode', 'requests'):
        if n in sys.modules:
            continue
        m = _Mod(n)
        sys.modules[n] = m
    # real-ish pieces
    xa = sys.modules['xbmcaddon']

    class Addon(_Any):
        def __init__(self, *a, **k):
            pass

        def getAddonInfo(self, k):
            return {'path': ADDON_PATH, 'profile': PROFILE, 'id': 'service.subtitles.kodipovilai',
                    'version': '0.0.0', 'name': 'stub'}.get(k, '')

        def getSetting(self, k):
            return ''

        def getSettingBool(self, k):
            return False

        def getSettingInt(self, k):
            return 0

        def getSettingString(self, k):
            return ''

        def getLocalizedString(self, i):
            return ''

        def setSetting(self, *a):
            return True

    xa.Addon = Addon
    xv = sys.modules['xbmcvfs']
    xv.translatePath = lambda p: p
    xv.exists = lambda p: False
    xv.mkdirs = lambda p: True
    xv.mkdir = lambda p: True
    xv.delete = lambda p: True
    xv.listdir = lambda p: ([], [])
    rq = sys.modules['requests']

    class Session(_Any):
        pass
    rq.Session = Session
    rq.get = lambda *a, **k: _Any()
    rq.post = lambda *a, **k: _Any()
    rq.exceptions = types.SimpleNamespace(RequestException=Exception, Timeout=Exception,
                                          ConnectionError=Exception, HTTPError=Exception)
    rq.RequestException = Exception
    rq.Timeout = Exception
    rq.ConnectionError = Exception
    rq.HTTPError = Exception
    sys.modules['requests.exceptions'] = rq.exceptions
    sys.modules['requests.adapters'] = _Mod('requests.adapters')
    rq.adapters = sys.modules['requests.adapters']
    sys.modules['requests.adapters'].HTTPAdapter = type('HTTPAdapter', (_Any,), {})
    xb = sys.modules['xbmc']

    class Monitor(_Any):
        def abortRequested(self):
            return True

        def waitForAbort(self, *a):
            return True
    xb.Monitor = Monitor
    sys.modules['urllib3.util'] = _Mod('urllib3.util')
    sys.modules['urllib3.util.retry'] = _Mod('urllib3.util.retry')
    sys.modules['urllib3.exceptions'] = _Mod('urllib3.exceptions')
    sys.modules['urllib3'].exceptions = sys.modules['urllib3.exceptions']
    sys.modules['urllib3'].util = sys.modules['urllib3.util']
    ch = sys.modules['chardet']
    ch.detect = lambda b: {'encoding': 'utf-8', 'confidence': 1.0}


ADDON_PATH = ''
PROFILE = ''
