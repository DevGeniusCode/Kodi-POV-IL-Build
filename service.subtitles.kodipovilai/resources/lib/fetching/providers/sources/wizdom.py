# Import necessary libraries
import xbmcaddon
global global_var,site_id,sub_color#global
global_var=[]
from resources.lib.fetching.providers import log
from resources.lib.fetching.providers import cache
from resources.lib.fetching.providers import meta as provider_meta
import json
import xbmcvfs
import shutil,os
from resources.lib.fetching.providers.extract_sub import extract
from resources.lib.fetching.providers.general import DEFAULT_REQUEST_TIMEOUT, ProviderHttp
_http = ProviderHttp()
import urllib
import urllib.parse
import urllib.request
#########################################

que=urllib.parse.quote_plus
Addon=xbmcaddon.Addon()
MyScriptID=Addon.getAddonInfo('id')
xbmc_tranlate_path=xbmcvfs.translatePath
__profile__ = xbmc_tranlate_path(Addon.getAddonInfo('profile'))
MyTmp = xbmc_tranlate_path(os.path.join(__profile__, 'temp_wizdom'))

########### Constants ###################
site_id='[Wizdom]'
sub_color='yellow'
WIZDOM_API_BASE_URL = "https://wizdom.xyz/api"
WIZDOM_API_SEARCH_URL = f"{WIZDOM_API_BASE_URL}/search?action=by_id"
WIZDOM_API_DOWNLOAD_URL = f"{WIZDOM_API_BASE_URL}/files/sub"
WIZDOM_API_RELEASES_URL = f"{WIZDOM_API_BASE_URL}/releases"
#########################################

def _releases_index(imdb_id):
    # {subtitle id: meta} of EVERY subtitle of the title (one call per title, not per episode; the answer is
    # 3-60 KB, 257 KB for a show of 1,300 subtitles). It raises on any failure on purpose: cache.get keeps
    # whatever a call returns, an empty dict included, for the whole 24 h, and returns None (keeping nothing)
    # when the call raises.
    response = _http.get(f"{WIZDOM_API_RELEASES_URL}/{imdb_id}", timeout=DEFAULT_REQUEST_TIMEOUT)
    response.raise_for_status()
    return provider_meta.wizdom_index(response.json())


def get_subs(video_data):

    # For settings changes to take effect.
    Addon=xbmcaddon.Addon()
    
    global global_var
    log.warning('DEBUG | [Wizdom] | Searching Wizdom')
    
    media_type = video_data['media_type']
    season = video_data['season']
    episode = video_data['episode']
    imdb_id = video_data['imdb']
    
    if not imdb_id.startswith('tt'): return []

    querystring = {}
    querystring['imdb'] = imdb_id
    
    if media_type == 'tv':
        #################################################
        # TV Shows - by imdb id + season + episode
        #################################################
        querystring['season'] = season.zfill(2)
        querystring['episode'] = episode.zfill(2)
    

    try:
        response = _http.get(WIZDOM_API_SEARCH_URL, params=querystring, timeout=DEFAULT_REQUEST_TIMEOUT)
        response.raise_for_status()  # Raise HTTPError for bad status codes (4xx, 5xx)
        wizdom_response_json = response.json()
        log.warning(f"DEBUG | [Wizdom] | Wizdom SearchSubtitles | URL: {response.url} | Status Code: {response.status_code} | wizdom_response_json={wizdom_response_json}")
    except Exception as e:
        log.warning(f'DEBUG | [Wizdom] | Wizdom SearchSubtitles | type: {type(e)} | Exception: {repr(e)}')
        return []
    
    wizdom_subtitles_list = []
    
    if wizdom_response_json:

        # Provenance metadata (credits, uploader, upload date), joined on the subtitle id. Fail open: a title
        # without it, or a failed call, only means rows without meta.
        try:
            releases_index = cache.get(_releases_index, 24, imdb_id, table='subs') or {}
        except Exception as e:
            log.warning(f'DEBUG | [Wizdom] | releases index not available | Exception: {repr(e)}')
            releases_index = {}
    
        for wizdom_subtitle in wizdom_response_json:
            
            if "id" not in wizdom_subtitle:
                continue
                
            download_data = {}
            download_data['id'] = wizdom_subtitle["id"]
            
            url = "plugin://%s/?action=download&filename=%s&id=%s&download_data=%s&source=wizdom&language=Hebrew" % (
                    MyScriptID, que(wizdom_subtitle["versioname"]), wizdom_subtitle["id"], que(json.dumps(download_data)))
          
            json_data = {'url':url,
                        'label':"Hebrew",
                        'label2':site_id+' '+wizdom_subtitle["versioname"],
                        'iconImage':"%s"%(wizdom_subtitle["score"]/2),
                        'thumbnailImage':"he",
                        'hearing_imp':'false',
                        'site_id':site_id,
                        'sub_color':sub_color,
                        'filename':wizdom_subtitle["versioname"],
                        'sync': 'false'}

            row_meta = provider_meta.wizdom_row(releases_index, wizdom_subtitle["id"])
            if row_meta:
                json_data['meta'] = row_meta
                        
            wizdom_subtitles_list.append(json_data)

    global_var = wizdom_subtitles_list
    
def download(download_data,MySubFolder):
    
    try:
        shutil.rmtree(MyTmp)
    except: pass
    xbmcvfs.mkdirs(MyTmp)
    
    id = str(download_data['id'])
    log.warning(f'DEBUG | [Wizdom] | Downloading sub id {id}...')
    
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-agent',  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36')]
    opener.addheaders = [('authority',  'wizdom.xyz')]
    opener.addheaders = [('accept',  'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7')]
    urllib.request.install_opener(opener)
    
    archive_file = os.path.join(MyTmp, f'wizdom.sub.{id}.zip')
    wizdom_sub_download_url = f"{WIZDOM_API_DOWNLOAD_URL}/{id}"
    urllib.request.urlretrieve(wizdom_sub_download_url, archive_file)
    sub_file=extract(archive_file,MySubFolder)
    return sub_file