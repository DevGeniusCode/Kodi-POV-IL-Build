
import xbmc,xbmcgui,time
from resources.lib.fetching.providers import log
import json
import os
import xbmcaddon
from resources.lib.fetching.providers.general import Thread
global break_all
break_all=False
_lingering_provider_threads=[]
# True once a search (any pass, including the all-language retry) stopped at
# max_search_time or was refused while a timed-out provider still ran: its
# result is incomplete. The bridge resets it before each search.
last_search_cut_short=False


class ProviderSearchBusy(RuntimeError):
    """A previous timed-out provider still owns the shared result buffers."""


from resources.lib.fetching.providers.sources import ktuvit
from resources.lib.fetching.providers.sources import wizdom
from resources.lib.fetching.providers.sources import opensubtitles
from resources.lib.fetching.providers.sources import yify
from resources.lib.fetching.providers.sources import subsource
from resources.lib.fetching.providers.sources import subscene
from resources.lib.fetching.providers.sources import bsplayer
def sort_subtitles(f_result,video_data,silent=False):

    # For settings changes to take effect.
    Addon=xbmcaddon.Addon()
    
    from resources.lib.fetching.providers import general
    if not silent and (Addon.getSetting("enable_autosub_notifications")=='true' or not xbmc.Player().isPlaying()):
        # If searching subtitles from context menu - will show the message.
        general.show_msg = f"מסדר כתוביות 0/{len(f_result)}"
                     
    # Define the specific order for json_value['site_id']. In case of multiple subtitles with same percent - sort also by site_id using this order:
    site_id_order = ['[Ktuvit]', '[Wizdom]', '[OpenSubtitles]', '[YIFY]', '[SubSource]', '[Subscene]', '[BSPlayer]']
    
    # Get video quality from Kodi
    quality = f"{xbmc.getInfoLabel('VideoPlayer.VideoResolution')}p" if xbmc.Player().isPlaying() else None
    if quality and quality.lower()=="4kp":
        quality = "2160p"
    
    # List of release names
    release_names = ['blueray','bluray','blu-ray','bdrip','brrip','brip',
                      'hdtv','hdtvrip','pdtv','tvrip','hdrip','hd-rip',
                      'web','web-dl','web dl','web-dlrip','webrip','web-rip',
                      'dvdr','dvd-r','dvd-rip','dvdrip','cam','hdcam','cam-rip','camrip','screener','dvdscr','dvd-full',
                      'telecine','hdts','telesync']
    
    hebrew_subtitles = []
    hebrew_machine_translated_subtitles = []
    english_subtitles = []
    other_languages_subtitles = []
    count = 0
    
    ###############################################################################################
    # Define functions before the for loop
    def append_subtitles(subtitles_list, json_value, percent):
        return subtitles_list.append((
            json_value['label'],
            '[COLOR %s]' % json_value['sub_color'] + json_value['label2'] + '[/COLOR]',
            json_value['iconImage'],
            json_value['thumbnailImage'],
            json_value['url'],
            percent,
            json_value['sync'],
            json_value['hearing_imp'],
            json_value['filename'],
            json_value['site_id'],
            # 10: the provider's optional provenance metadata (providers/meta.py), {} when it has none. Only
            # bridge._engine_rows reads the tuple, by index.
            json_value.get('meta') or {}
        ))
      
      
    def remove_subtitle_or_video_extension_if_exists(file_name):
        
        # Define a list of common video file extensions
        video_file_extensions = ['mkv', 'mp4', 'm4p', 'avi', 'mov', 'mpeg', 'mpg', 'flv', 'wmv', 'm4v', 'webm', '3gp', 'ogg', 'ogv', 'rmvb', 'divx', 'vob', 'dat', 'mts', 'm2ts', 'ts', 'yuv']
        # Define a list of common video file extensions
        subtitle_file_extensions = ['srt', 'str', 'sub', 'sup', 'idx', 'ass', 'ssa', 'vtt', 'smi']
        
        # Extract the basename of the file from the path
        file_name_without_extension, file_extension = os.path.splitext(file_name)
    
        # Remove the dot and check the extension
        file_extension = file_extension.lstrip('.')
        
        # If the extension is in the list of video extensions, remove it
        if file_extension in video_file_extensions or file_extension in subtitle_file_extensions:
            return file_name_without_extension
            
        return file_name
        
        
    def clean_string_and_create_array(file_name):
    
        file_name = remove_subtitle_or_video_extension_if_exists(file_name)
        
        cleaned_file_name = (
            file_name.strip()
            .replace("_", ".")
            .replace(" ", ".")
            .replace("+", ".")
            .replace("/", ".")
            .replace("-", ".")
        )
        return [x.strip().lower() for x in cleaned_file_name.split(".") if x != '']
        
        
    def similar(w1, w2):
        from difflib import SequenceMatcher
        s = SequenceMatcher(None, w1, w2)
        return int(round(s.ratio()*100))
        
        
    def calculate_sync_percentage(video_name_array, subtitle_name_array):

        # Check and add missing quality
        if quality and quality not in video_name_array and quality in subtitle_name_array:
            video_name_array.append(quality)

        # Check for release names and extend arrays
        for release_name in release_names:
            if release_name in video_name_array and release_name in subtitle_name_array:
                video_name_array.extend([release_name] * 3)
                subtitle_name_array.extend([release_name] * 3)

        # Calculate similarity
        percent = similar(video_name_array, subtitle_name_array)

        return percent
    ###############################################################################################
    
    # SubSync S1: the ONE structured release scorer (exact=100, same
    # group+source ~90, cross-source capped low). When importable, it scores
    # the RAW strings and replaces the legacy token-similarity below, so the
    # engine's %, the picker's % and the source-screen badge all agree.
    try:
        from resources.lib.scoring_matching import release as _release_match
    except Exception:
        _release_match = None

    # Clean video file_original_path
    video_file_original_path_array = clean_string_and_create_array(video_data['file_original_path'])
    # Clean video Tagline
    video_tagline_array = clean_string_and_create_array(video_data['Tagline'])

    for result_value in f_result:
    
        if not silent and (Addon.getSetting("enable_autosub_notifications")=='true' or not xbmc.Player().isPlaying()):
            # If searching subtitles from context menu - will show the message.
            general.show_msg = f"מסדר כתוביות {count}/{len(f_result)}"
            count += 1
          
        json_value = json.loads(json.dumps(result_value))
        
        ################### Calculate Sync Percentage #################################################
        if _release_match is not None:
            # Structured scorer on the raw strings (also avoids the legacy
            # path's cross-iteration array mutation).
            percent = max(
                _release_match.match_pct(video_data['file_original_path'],
                                         json_value['filename']),
                _release_match.match_pct(video_data['Tagline'],
                                         json_value['filename']))
        else:
            # Clean subtitle name
            subtitle_name_array = clean_string_and_create_array(json_value['filename'])

            # Video file_original_path sync percentage
            percent_from_file_original_path = calculate_sync_percentage(video_file_original_path_array, subtitle_name_array)

            # Video Tagline sync percentage
            percent_from_video_tagline = calculate_sync_percentage(video_tagline_array, subtitle_name_array)

            percent = max(percent_from_file_original_path, percent_from_video_tagline)
        ###############################################################################################
        
        
        ################### Append subtitle to corresponding language subs list #######################
        if 'language=HebrewMachineTranslated' in json_value['url'] or 'HebrewMachineTranslated' in json_value['label']:
            append_subtitles(hebrew_machine_translated_subtitles, json_value, percent)
            
        elif 'language=Hebrew' in json_value['url'] or 'Hebrew' in json_value['label']:
            append_subtitles(hebrew_subtitles, json_value, percent)
       
        elif 'language=English' in json_value['url'] or 'English' in json_value['label']:
            append_subtitles(english_subtitles, json_value, percent)
           
        else:
            append_subtitles(other_languages_subtitles, json_value, percent)
        ###############################################################################################
    
    
    ################### Final Subtitles Sorting ###################################################
    def custom_sort(subtitles_list, site_id_order, by_language_name=False):
        # Sort also by language name (x[0]) if by_language_name is True
        if by_language_name:
            return sorted(subtitles_list, key=lambda x: (x[0], -x[5], site_id_order.index(x[9]) if x[9] in site_id_order else len(site_id_order)))
        # Sort by percent (-x[5]) and site_id (x[9]) only
        return sorted(subtitles_list, key=lambda x: (-x[5], site_id_order.index(x[9]) if x[9] in site_id_order else len(site_id_order)))

    # Sort languages
    hebrew_subtitles = custom_sort(hebrew_subtitles, site_id_order)
    hebrew_machine_translated_subtitles = custom_sort(hebrew_machine_translated_subtitles, site_id_order)
    english_subtitles = custom_sort(english_subtitles, site_id_order)
    other_languages_subtitles = custom_sort(other_languages_subtitles, site_id_order, by_language_name=True)

    # Combine all sorted subtitles list
    sorted_subtitles = []
    sorted_subtitles = hebrew_subtitles + hebrew_machine_translated_subtitles + english_subtitles + other_languages_subtitles
    ###############################################################################################

    return sorted_subtitles

# Prettify website source name for subs search dialog
def format_website_source_name(source):
    if source == "ktuvit":
        return "Ktuvit"
    if source == "wizdom":
        return "Wizdom"
    if source == "opensubtitles":
        return "OpenSubtitles"
    if source == "yify":
        return "YIFY"
    if source == "subsource":
        return "SubSource"
    if source == "subscene":
        return "Subscene"
    if source == "bsplayer":
        return "BSPlayer"
    return source


def _join_provider_threads(threads, timeout_s):
    """Join provider workers within one shared deadline; return survivors."""
    deadline = time.monotonic() + max(0.0, float(timeout_s or 0.0))
    for worker in list(threads or []):
        if not worker.is_alive():
            continue
        remaining = max(0.0, deadline - time.monotonic())
        if remaining:
            worker.join(remaining)
    return [worker for worker in (threads or []) if worker.is_alive()]


def _provider_workers_ready(timeout_s=0.25):
    """Fail closed while a timed-out provider from an older search is alive."""
    global _lingering_provider_threads
    _lingering_provider_threads = _join_provider_threads(
        _lingering_provider_threads, timeout_s)
    return not _lingering_provider_threads


def _quarantine_provider_threads(threads, timeout_s=0.5):
    """Remember workers that ignored the search timeout until they really end."""
    global _lingering_provider_threads
    _lingering_provider_threads = _join_provider_threads(threads, timeout_s)
    return not _lingering_provider_threads


# A provider that is down holds every search to max_search_time and, still
# running when the search is cut, makes the next one wait or be refused (Ktuvit
# on 2 Nov 2026: its own server unreachable, 522 after ~20 s, every search held
# to the limit for the whole outage). What it finds after the cut is lost
# anyway. A provider still running at the cut of two searches within
# _DOWN_STRIKE_SECS is skipped for _DOWN_SECS; the first search after that
# tries it again, and outliving that one skips it again at once; one that ends
# in time is clear. One slow answer alone skips nothing: Ktuvit carries most
# human Hebrew. Nor does a cut that no provider beat WITH RESULTS: then the
# device's own network is slow, and skipping every provider would leave
# searches empty. A timing pass that would skip one is temporary instead. Kept
# in a Kodi window property -- searches run in several interpreters.
_DOWN_PROP = 'moransubs.provider_down'
_DOWN_SECS = 600
_DOWN_STRIKE_SECS = 1800


def _down_state():
    try:
        state = json.loads(xbmcgui.Window(10000).getProperty(_DOWN_PROP) or '{}')
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def _provider_down(name, now=None):
    """Whether `name` is skipped now (marked down, the time not over)."""
    now = time.time() if now is None else now
    try:
        return float((_down_state().get(name) or {}).get('until') or 0) > now
    except Exception:
        return False


def _note_provider_outcomes(outlived, ended, answered=(), now=None):
    """After a search: the providers still running at its cut, those that
    ended in time, and of those the ones that answered with results. Never
    raises."""
    now = time.time() if now is None else now
    try:
        state = _down_state()
        for name in ended:
            state.pop(name, None)
        # Only a provider that answered with results shows the network was
        # fine: one that ends at once with nothing (no IMDb id, nothing
        # playing, never connected) never went out. A cut that no answer
        # beat is the device's own network (a link buffering the video):
        # nobody is marked.
        for name in (outlived if answered else ()):
            prev = state.get(name) or {}
            recent = now - float(prev.get('last') or 0) < _DOWN_STRIKE_SECS
            strikes = int(prev.get('strikes') or 0) + 1 if recent else 1
            state[name] = {'strikes': strikes, 'last': now,
                           'until': now + _DOWN_SECS if strikes >= 2 else 0}
            if strikes >= 2:
                log.warning('DEBUG | c_get_subtitles | %s outlived %d searches '
                            'in a row: skipped for %d s' % (name, strikes, _DOWN_SECS))
        xbmcgui.Window(10000).setProperty(_DOWN_PROP, json.dumps(state))
    except Exception as e:
        log.warning('DEBUG | c_get_subtitles | provider state not kept: %r' % e)


def _provider_on(name, skipped):
    """A provider's turn in this search: not while it is marked down (then
    named in `skipped`)."""
    if _provider_down(name):
        log.warning('DEBUG | c_get_subtitles | %s skipped: down (outlived its '
                    'last searches)' % name)
        skipped.append(name)
        return False
    return True


def _finish_timing_timeout(threads, all_sources, timeout_s=0.5):
    """Return only a complete timing result after the timeout grace period.

    A partial all-language result is unsafe for SubSync: the missing worker may
    be the one that carries the independent release family needed to prove the
    correction.  If every worker finishes during the bounded grace period,
    rebuild the result after the joins; otherwise make the whole timing search
    retryable instead of caching a misleading partial snapshot.
    """
    if not _quarantine_provider_threads(threads, timeout_s):
        raise ProviderSearchBusy('timing provider search remained incomplete')
    result = []
    for _scraper, source in all_sources:
        result += source.global_var
    return result
        
def c_get_subtitles(video_data, all_lang_override=False, timing_only=False, on_provider_done=None):
    """on_provider_done(name, rows) (Step 8, autosub's fast lane): called once for each provider the
    moment its thread has ended, with a copy of what it found, while the others may still run.
    It runs in this loop's thread and must not raise (an exception is swallowed)."""
    global last_search_cut_short
        
    log.warning(f"DEBUG | c_get_subtitles | START | all_lang_override={all_lang_override} | timing_only={timing_only}")
    
    # For settings changes to take effect.
    Addon=xbmcaddon.Addon()
    from resources.lib.fetching.providers import general

    log.warning("DEBUG | c_get_subtitles | Searching for:")
    log.warning(f"DEBUG | c_get_subtitles | video_data={video_data}")

    thread=[]
    all_sources=[]

    # Provider modules publish into shared ``global_var`` lists. The legacy
    # Thread.exit() method does not terminate a Python worker; after a timeout
    # that worker can wake up and overwrite a later search. Never reset or
    # launch providers until every quarantined worker has actually ended.
    if not _provider_workers_ready():
        last_search_cut_short = True
        log.warning('DEBUG | c_get_subtitles | Refusing overlapping search; '
                    'a timed-out provider worker is still alive')
        if timing_only:
            raise ProviderSearchBusy('timed-out provider worker still alive')
        return []

    for source in [ktuvit, wizdom, opensubtitles, yify, subsource, subscene, bsplayer]:
        source.global_var = []
    _skipped = []   # providers left out as down (_provider_on)
    
    # Determine wether to search hebrew language
    search_language_hebrew_bool = (Addon.getSetting('language_hebrew') == 'true' or Addon.getSetting("all_lang") == 'true' or all_lang_override)
    
    # Israeli subtitles sources
    
    if not timing_only and Addon.getSetting('ktuvit')=='true' and search_language_hebrew_bool and _provider_on('ktuvit', _skipped):
        thread.append(Thread(ktuvit.get_subs,video_data))
        all_sources.append(('ktuvit',ktuvit))
        
    if not timing_only and Addon.getSetting('wizdom')=='true' and search_language_hebrew_bool and _provider_on('wizdom', _skipped):
        thread.append(Thread(wizdom.get_subs,video_data))
        all_sources.append(('wizdom',wizdom))
        
    # Global subtitles sources
        
    if Addon.getSetting('opensubtitles')=='true' and _provider_on('opensubtitles', _skipped):
        thread.append(Thread(opensubtitles.get_subs,video_data, all_lang_override))
        all_sources.append(('opensubtitles',opensubtitles))
        
    if Addon.getSetting('yify')=='true' and _provider_on('yify', _skipped):
        thread.append(Thread(yify.get_subs,video_data, all_lang_override))
        all_sources.append(('yify',yify))
        
    if Addon.getSetting('subsource')=='true' and _provider_on('subsource', _skipped):
        thread.append(Thread(subsource.get_subs,video_data, all_lang_override))
        all_sources.append(('subsource',subsource))
        
    if Addon.getSetting('subscene')=='true' and _provider_on('subscene', _skipped):
        thread.append(Thread(subscene.get_subs,video_data, all_lang_override))
        all_sources.append(('subscene',subscene))
    
    # BSPlayer answers for the hash of the PLAYING file: not for a manual search of another title (typed words on the
    # playing video, keyword_only, still use it)
    _bsplayer_fits = not (video_data.get('manual') and not video_data.get('keyword_only'))
    if not timing_only and _bsplayer_fits and Addon.getSetting('bsplayer')=='true' and search_language_hebrew_bool and _provider_on('bsplayer', _skipped):
        thread.append(Thread(bsplayer.get_subs,video_data))
        all_sources.append(('bsplayer',bsplayer))
        

    # A timing pass is all or nothing (_finish_timing_timeout): one without a
    # provider is as incomplete as one cut short -- temporary, retried later,
    # never kept as the all-language result.
    if timing_only and _skipped:
        last_search_cut_short = True
        raise ProviderSearchBusy('provider down: ' + ', '.join(_skipped))

    for td in thread:
      xbmc.sleep(100)
      td.start()
      

    tt={}
    for i in range (0,40): 
      tt[i]="red"
    start_time = time.time()
    num_live=0
    break_all=False
    ExcludeTime = int(Addon.getSetting('max_search_time') or 10)
    # A timing-only pass can legitimately have no eligible global provider
    # enabled. Keep that a clean empty result instead of referencing the loop's
    # accumulator before it has ever been assigned.
    f_result=[]
    _notified=set()   # providers on_provider_done has already been told about

    while 1:
        elapsed_time = time.time() - start_time
        still_alive=0
        for threads in thread:
              
              if break_all:
                 
                 break
                                
              num_live=0
              string_dp=''

              still_alive=0
              for yy in range(0,len(thread)):
                if not thread[yy].is_alive():
                  num_live=num_live+1
                  tt[yy]="lightgreen"
                  if on_provider_done is not None and yy not in _notified:
                    _notified.add(yy)
                    try:
                      on_provider_done(all_sources[yy][0], list(all_sources[yy][1].global_var))
                    except Exception:
                      pass
                else:
                  still_alive=1
                  tt[yy]="red"
              
             
              
              zz=0
              f_result=[]
              for scraper,items in all_sources:
                 f_result=f_result+items.global_var
                 string_dp=string_dp+(' %s:[COLOR %s]%s[/COLOR] '%(format_website_source_name(scraper),tt[zz],len( items.global_var)))
                 zz=zz+1
              if not timing_only and (Addon.getSetting("enable_autosub_notifications")=='true' or not xbmc.Player().isPlaying()):
                # If searching subtitles from context menu - will show the message.
                general.show_msg=' אנא המתן '+ time.strftime("%H:%M:%S", time.gmtime(elapsed_time))+'\n'+ string_dp
              
        if still_alive==0:
            _note_provider_outcomes([], [name for name, _src in all_sources])
            break
        
        if  elapsed_time>ExcludeTime: 
            break_all=True
            last_search_cut_short = True
            try:
                if timing_only:
                    try:
                        f_result = _finish_timing_timeout(thread, all_sources)
                    except ProviderSearchBusy:
                        log.warning('DEBUG | c_get_subtitles | Incomplete timing '
                                    'search discarded; provider worker quarantined')
                        raise
                elif not _quarantine_provider_threads(thread):
                    log.warning('DEBUG | c_get_subtitles | Timed-out provider '
                                'worker quarantined; later searches will abstain '
                                'until it exits')
            finally:
                # who outlived the cut, who ended in time (_provider_down)
                _note_provider_outcomes(
                    [name for (name, _src), td in zip(all_sources, thread) if td.is_alive()],
                    [name for (name, _src), td in zip(all_sources, thread) if not td.is_alive()],
                    [name for (name, src), td in zip(all_sources, thread)
                     if not td.is_alive() and src.global_var])
            break
        xbmc.sleep(10)
        
    #################################################### Retry all languages START ####################################################
    # If:
    # 1. no subtitles found.
    # 2. retry_search_with_all_langs setting is true.
    # 3. all_lang is set to false.
    if not f_result and Addon.getSetting('retry_search_with_all_langs')=='true' and Addon.getSetting('all_lang')=='false' and not all_lang_override:
        if Addon.getSetting("enable_autosub_notifications")=='true':
            from resources.lib.fetching.providers.general import notify
            notify("לא נמצאו כתוביות. מחפש שוב בכל השפות...")
        log.warning("DEBUG | c_get_subtitles | No results found, retrying with all_lang=true...")
        f_result = c_get_subtitles(video_data, all_lang_override=True, on_provider_done=on_provider_done)
    #################################################### Retry all languages END ####################################################
    
    if not timing_only and (Addon.getSetting("enable_autosub_notifications")=='true' or not xbmc.Player().isPlaying()):
        # If searching subtitles from context menu - will show the message.
        general.show_msg="מסדר כתוביות"
    
    return f_result
    
def get_subtitles(video_data, on_provider_done=None):
    # For settings changes to take effect.
    Addon=xbmcaddon.Addon()
    from resources.lib.fetching.providers import general

    if Addon.getSetting("enable_autosub_notifications")=='true' or not xbmc.Player().isPlaying():
        # If searching subtitles from context menu - will show the message.
        general.show_msg='מחפש כתוביות'
    
    f_result=c_get_subtitles(video_data, on_provider_done=on_provider_done)
    xbmc.sleep(100)
    return f_result
