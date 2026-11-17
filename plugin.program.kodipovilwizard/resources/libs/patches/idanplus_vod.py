# -*- coding: utf-8 -*-
"""Idan Plus VOD repairs (Kan, Kan archive, i24NEWS English), called from the registry hooks.

Found by browsing and PLAYING every Idan Plus VOD provider inside a real Kodi 21.3. These did not work, each for a
reason on the provider's side of the wire:

1. Cloudflare turns Kodi away from www.kan.org.il (403 on every page fetch: it is the client fingerprint, not a header).
   The same Umbraco site answers on Kan's mobile-API host mobapi.kan.org.il, which Kodi already reaches for the JSON
   feeds, byte-identical. kan_mirror() asks that host first for www.kan.org.il pages and returns None (stock path) for
   anything but a 200 that stayed on that host. The kids site (www.kankids.org.il) is not served there, so it keeps the
   stock path; that path could not send its request because the Referer carried the Hebrew word for "season"
   verbatim and `requests` refuses a non-latin-1 header: kan_ascii() percent-encodes it like a browser does.
2. Kan moved its player: episode pages no longer carry data-hls-url / data-dash-url; the episode cards are parsed here
   (kan_cr_items) and the archive lobby's program tiles too (kan_program_tiles).
3. i24NEWS English came up empty: i24_english_shows() reads the Univtec catalog.

Every function here is fail-open from the caller's point of view: the hooks that call them are built with the registry
helper, so an error is logged and Idan Plus carries on with its stock code. The functions take the host module's
globals (g) and the calling function's locals (l) instead of importing Idan Plus modules.
"""
import re


def _v(g, l, name):
    """A name of the calling function: its local, else a module global."""
    return l[name] if name in l else g.get(name)


def kan_ascii(u):
    try:
        from requests.utils import requote_uri
        return requote_uri(u)
    except Exception:
        return u


def kan_mirror(url, ua, responseMethod):
    if responseMethod not in ('text', 'json'):
        return None
    match = re.match(r'https?://www\.kan\.org\.il(/[^#]*)?$', url or '')
    if not match:
        return None
    try:
        import requests
        session = requests.Session()
        session.max_redirects = 4
        response = session.get('https://mobapi.kan.org.il' + kan_ascii(match.group(1) or '/'),
                               headers={'User-Agent': ua, 'Accept-Encoding': 'gzip'}, timeout=(6, 20))
        if response.status_code != 200 or not response.text or not response.url.startswith('https://mobapi.kan.org.il/'):
            return None
        return response.json() if responseMethod == 'json' else response.text
    except Exception:
        return None


def i24_english_shows(g):
    """i24news.py, start of the TV-shows list for English: the Univtec catalog. Returns True when something was listed."""
    common = g['common']
    data = g['GetUnivtecData']('pages/section/6a15800ed7dd65c210d2d0c1', {'page': 1, 'limit': 100}, region='all')
    shows = [s for s in ((data or {}).get('items') or []) if s.get('id')]
    for show in shows:
        title = show.get('title') or show.get('name') or 'Unknown'
        common.addDir(name=common.GetLabelColor(title, keyColor="prColor", bold=True),
                      url='action=seasons_univtec&series_id={0}&show_name={1}'.format(show['id'], common.quote_plus(title)),
                      mode=-1, iconimage=g['GetUnivtecImage'](show, g['moduleIcon']),
                      infos={'Title': title, 'Plot': show.get('description') or ''}, module=g['module'], isFolder=True)
    return bool(shows)


def kan_cr_items(g, l):
    """kan.py, season/episode list: Kan's new "cr-item" cards. True when the page was handled (the caller returns)."""
    programId, text = _v(g, l, 'programId'), _v(g, l, 'text')
    if not (programId and 'cr-item' in text):
        return False
    common = g['common']
    seasonFilter, url = _v(g, l, 'seasonFilter'), _v(g, l, 'url')
    seen = []
    for link, name, image in re.findall(r'<a class="cr-item[^"]*"\s+href="([^"]*/p-' + programId + r'/(?:[^"/]+/)*\d+/?)"\s+aria-label="([^"]*)"(?:(?!</a>).)*?<img src="([^"]+)"', text, re.S):
        if not link.startswith('http'):
            link = (g['baseKidsUrl'] if 'kankids' in url else g['baseUrl']) + link
        if link in seen or (seasonFilter and '/s{0}/'.format(seasonFilter) not in link):
            continue
        seen.append(link)
        name = common.UnEscapeXML(name.strip())
        try:
            image = g['GetImageLink'](common.quoteNonASCII(common.UnEscapeXML(image)), name)
        except Exception:
            image = _v(g, l, 'iconimage')
        name = common.GetLabelColor(name, keyColor="chColor")
        common.addDir(name, link, 3, image, infos={"title": name, "plot": name}, module=g['module'], moreData=_v(g, l, 'bitrate'),
                      isFolder=False, isPlayable=True, urlParamsData={'catName': _v(g, l, 'catName')})
    return True


def kan_program_tiles(g, l):
    """kan.py, archive lobby: program tiles when the page has no "vod-section" blocks. True when handled (caller returns)."""
    matches = _v(g, l, 'matches')
    if not matches or '<div class="vod-section' in matches[0]:
        return False
    common = g['common']
    domain, links, catName = _v(g, l, 'domain'), _v(g, l, 'links'), _v(g, l, 'catName')
    for link, name, image in re.findall(r'<a [^>]*href="([^"]+)"[^>]*data-item-type="program"[^>]*aria-label="([^"]*)"[^>]*>(?:(?!</a>).)*?<img src="([^"]+)"', matches[0], re.S):
        if not link.startswith('http'):
            link = '{0}{1}'.format(domain, link)
        if link in links:
            continue
        links += [link]
        name = common.UnEscapeXML(name)
        try:
            image = g['GetImageLink'](common.quoteNonASCII(common.UnEscapeXML(image)), name)
        except Exception:
            image = _v(g, l, 'iconimage')
        name = common.GetLabelColor(name, keyColor="prColor", bold=True)
        common.addDir(name, link, 2, image, infos={"title": name, "plot": name}, module=g['module'],
                      moreData='kan|||{0}'.format(catName), urlParamsData={'catName': catName})
    return True
