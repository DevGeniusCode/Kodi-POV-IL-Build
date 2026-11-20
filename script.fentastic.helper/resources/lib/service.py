import xbmc, xbmcgui
from threading import Thread
from xbmcvfs import translatePath
import json
import os
import time

logger = xbmc.log

# Kodi POV IL update window (Wizard, provisioning/update_mode.py): this file exists, and is refreshed every few seconds, while
# Kodi installs add-on updates. Kodi may stop this service at any moment then (to replace it, or the skin), and a stop that
# finds the loop INSIDE a GUI call can wedge Kodi's GUI for good (20 Nov 2026 log: "didn't stop in 5 seconds", then a frozen
# screen). So while it exists the loop makes no GUI call at all, only waitForAbort. A plain file, not a window property:
# reading a property takes the GUI lock. A file not refreshed for UPDATE_FLAG_STALE seconds is a leftover and is ignored.
UPDATE_FLAG = os.path.join(translatePath("special://temp/"), "addon_update_window.flag")
UPDATE_FLAG_STALE = 90.0


def updates_installing():
    try:
        age = time.time() - os.path.getmtime(UPDATE_FLAG)
    except OSError:
        return False
    return -5.0 <= age < UPDATE_FLAG_STALE
empty_ratings = {
    "metascore": "",
    "tomatoMeter": "",
    "tomatoUserMeter": "",
    "tomatoImage": "",
    "imdbRating": "",
    "popularRating": "",
    "tmdbRating": "",
}


class RatingsService(xbmc.Monitor):
    def __init__(self):
        xbmc.Monitor.__init__(self)
        # Imported at the first rating that is asked for, not at Kodi's start:
        # it brings requests and sqlite with it, this service starts under
        # every skin, and the ratings are shown by FENtastic alone.
        self.mdblist_api = None
        self.last_set_imdb_id = None
        self.window = xbmcgui.Window
        self.get_window_id = xbmcgui.getCurrentWindowId
        self.get_infolabel = xbmc.getInfoLabel
        self.get_visibility = xbmc.getCondVisibility
        # The loop below runs every 0.2-0.45 s while the viewer scrolls Home / Videos and every Kodi GUI call takes the GUI lock, so what
        # rarely changes is asked for once in a while, not every cycle: the Window objects (one per window id), the skin folder and
        # the skin string holding the MDbList key. Settings changes drop all three at once (onSettingsChanged).
        self._windows = {}
        self._windows_at = 0.0
        self._skin = None
        self._skin_at = 0.0
        self._key = None
        self._key_at = 0.0

    WINDOW_TTL = 10.0   # seconds a cached Window object is reused (a skin reload re-creates windows: never keep one long)
    SKIN_TTL = 3.0      # seconds the skin folder is trusted
    KEY_TTL = 10.0      # seconds the skin string with the API key is trusted
    IDLE_WAIT = 0.45    # seconds between two cycles while the focused IMDb id has not changed

    def _drop_caches(self):
        self._windows = {}
        self._windows_at = 0.0
        self._skin = None
        self._key = None

    def onSettingsChanged(self):
        self._drop_caches()

    def _window_for(self, window_id):
        now = time.monotonic()
        if now - self._windows_at > self.WINDOW_TTL:
            self._windows = {}
            self._windows_at = now
        win = self._windows.get(window_id)
        if win is None:
            win = self._windows[window_id] = self.window(window_id)
        return win

    def _skin_dir(self):
        now = time.monotonic()
        if self._skin is None or now - self._skin_at > self.SKIN_TTL:
            skin = xbmc.getSkinDir()
            if skin != self._skin:
                self._windows = {}      # another skin: its windows are other objects
            self._skin = skin
            self._skin_at = now
        return self._skin

    def _api_key_label(self):
        now = time.monotonic()
        if self._key is None or now - self._key_at > self.KEY_TTL:
            self._key = self.get_infolabel("Skin.String(mdblist_api_key)")
            self._key_at = now
        return self._key

    def onNotification(self, sender, method, data):
        if sender == "xbmc":
            if method in ("GUI.OnScreensaverActivated", "System.OnSleep"):
                self.window(self.get_window_id()).setProperty("pause_services", "true")
                logger("###FENtastic: Device is Asleep, PAUSING Ratings Service", 1)
            elif method in ("GUI.OnScreensaverDeactivated", "System.OnWake"):
                self.window(self.get_window_id()).clearProperty("pause_services")
                logger("###FENtastic: Device is Awake, RESUMING Ratings Service", 1)

    def listitem_monitor(self):
        # A cached Window object can go stale (a skin reload re-creates windows): such a call raises, and the loop must not die
        # with it. Drop the caches and start the cycles again.
        while not self.abortRequested():
            try:
                self._cycles()
                return
            except Exception as exc:
                self._drop_caches()
                logger("###FENtastic: Ratings loop error, restarting it: %r" % (exc,), 1)
                if self.waitForAbort(2):
                    return

    def _cycles(self):
        while not self.abortRequested():
            if updates_installing():
                self.waitForAbort(1)
                continue
            window_id = self.get_window_id()
            win = self._window_for(window_id)
            if win.getProperty("pause_services") == "true":
                self.waitForAbort(2)
                continue
            if self._skin_dir() != "skin.fentastic":
                self.waitForAbort(15)
                continue
            api_key = self._api_key_label()

            # KODI-RD-IL
            if api_key == 'use_my_custom_list':
                MDBLIST_API_KEYS = {
                    "darksubsil1-api_key": "csq5pq5vhf4r1snagff7qukcu",
                    "darksubsil2-api_key": "m1azvroj6xgt6x05urkmd9m9u",
                    "darksubsil3-api_key": "dbm160enk1t0shknn3r2bh003",
                    "darksubsil4-api_key": "n5p3p7mvdb9tmdq49mrp59tdu",
                    "darksubsil5-api_key": "5608d5y1dkhogghe4ly4nkjr9",
                    "darksubsil6-api_key": "blgzz5pfmfb973mgy1lp4n3s8",
                    "darksubsil7-api_key": "9wcsvxj6gjv7io6p9pcqfeqbw",
                    "darksubsil8-api_key": "udrqs9ni2gdf6qvtnytnodrxt",
                    "darksubsil9-api_key": "j49h9i7aohe7g4pdpl4o5rp38",
                    "darksubsil10-api_key": "kq4ur9et7hceu2h0e00l2hw0j"
                }
                import random
                api_key = random.choice(list(MDBLIST_API_KEYS.values()))
            ############

            if not api_key:
                self.waitForAbort(10)
                continue
            if not self.get_visibility(
                "Window.IsVisible(videos) | Window.IsVisible(home) | Window.IsVisible(11121)"
            ):
                self.waitForAbort(2)
                continue
            if self.get_visibility("Container.Scrolling"):
                self.waitForAbort(0.2)
                continue
            if self.get_visibility("Skin.HasSetting(TrailerPlaying)"):
                self.waitForAbort(3)
                while xbmc.Player().isPlaying():
                    if self.waitForAbort(0.5):
                        break
                xbmc.executebuiltin("Skin.ToggleSetting(TrailerPlaying)")
                self.waitForAbort(0.2)
                continue
            imdb_id = self.get_infolabel("ListItem.IMDBNumber")
            set_property = win.setProperty
            get_property = win.getProperty
            cached_ratings = get_property(f"fentastic.cachedRatings.{imdb_id}")
            if not imdb_id or not imdb_id.startswith("tt"):
                was_set = self.last_set_imdb_id is not None
                for k, v in empty_ratings.items():
                    set_property("fentastic.%s" % k, v)
                self.last_set_imdb_id = None
                # still no rated item focused (nothing changed): the slow beat; the cycle right after a rated item was left stays quick
                self.waitForAbort(0.2 if was_set else self.IDLE_WAIT)
                continue
            if imdb_id == self.last_set_imdb_id:
                self.waitForAbort(self.IDLE_WAIT)
                continue
            if cached_ratings:
                result = json.loads(cached_ratings)
                for k, v in result.items():
                    set_property("fentastic.%s" % k, v)
                self.last_set_imdb_id = imdb_id
                self.waitForAbort(0.2)
                continue
            Thread(target=self.set_ratings, args=(api_key, imdb_id), daemon=True).start()
            self.waitForAbort(0.2)

    def set_ratings(self, api_key, imdb_id):
        window_id = self.get_window_id()
        if self.mdblist_api is None:
            from modules.MDbList import MDbListAPI
            self.mdblist_api = MDbListAPI
        result = self.mdblist_api().fetch_info({"imdb_id": imdb_id}, api_key)
        # The fetch can take seconds: no GUI call when Kodi has asked this service to stop or is installing updates meanwhile.
        if result and not self.abortRequested() and not updates_installing():
            set_property = self.window(window_id).setProperty
            set_property(f"fentastic.cachedRatings.{imdb_id}", json.dumps(result))
            for k, v in result.items():
                set_property("fentastic.%s" % k, v)


logger("###FENtastic: Ratings Service Started", 1)
RatingsService().listitem_monitor()
logger("###FENtastic: RatingsService Finished", 1)
