"""First-run bootstrap of OUR two repository add-ons.

A user can install the build two ways: add the GitHub Pages URL as a source and install repository.povil from it, or install the
Wizard from a single zip. In the second case nothing tells Kodi where our add-ons live, so the first start of the Wizard installs
repository.povil (our own add-ons) and repository.povil.upstream (POV, Idan Plus, Umbrella ... from their developers) from the
stable "<id>-latest.zip" files of the site, each verified against its "<id>-latest.zip.sha256" before anything is extracted.

This is the ONLY place where the Wizard extracts an add-on zip itself: Kodi cannot install a repository add-on silently, and a
repository is what lets Kodi install and update everything else natively. download_zip / extract_zip / _sha256_matches were
moved here from the retired HeadlessInstaller.
"""
import os
import zipfile

import xbmc

from resources.libs.common.config import CONFIG
from resources.libs.common import logging
from resources.libs.common import tools
from resources.libs.provisioning import addon_state

_LOGINFO = xbmc.LOGINFO
_LOGWARNING = xbmc.LOGWARNING
_LOGERROR = xbmc.LOGERROR

REPOSITORY_IDS = ('repository.povil', 'repository.povil.upstream')

# How long to wait for Kodi to report a freshly enabled repository as installed.
ENABLE_WAIT_SECONDS = 15


def _log(msg, level=None):
    try:
        logging.log('[RepoBootstrap] ' + msg,
                    level=level if level is not None else _LOGINFO)
    except Exception:
        pass


def download_zip(url, dest, on_progress=None, should_abort=None):
    """Stream a url to dest, reporting integer percent via on_progress(pct).
    Returns True on a non-empty file. Opens NO dialog."""
    on_progress = on_progress or (lambda pct: None)
    should_abort = should_abort or (lambda: False)
    response = None
    try:
        folder = os.path.split(dest)[0]
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
        response = tools.open_url(url, stream=True)
        if not response:
            return False
        total = response.headers.get('content-length')
        with open(dest, 'wb') as f:
            if total is None:
                f.write(response.content)
                on_progress(100)
            else:
                total = int(total)
                downloaded = 0
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if should_abort():
                        return False
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    on_progress(min(100, int(downloaded * 100 / total)) if total else 0)
        return os.path.exists(dest) and os.path.getsize(dest) > 0
    except Exception as e:
        _log("download error for {0}: {1}".format(url, e), level=_LOGERROR)
        return False
    finally:
        try:
            if response is not None and hasattr(response, 'close'):
                response.close()
        except Exception:
            pass


def extract_zip(zip_path, dest_dir, on_progress=None):
    """Extract a single-addon zip into dest_dir reporting (current, total) files.
    Single-addon zips are rooted at the addon id and contain no userdata, so a
    plain extract (no KEEP* build logic, no competing dialog) is correct."""
    on_progress = on_progress or (lambda cur, tot: None)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            members = zf.namelist()
            total = len(members) or 1
            for i, member in enumerate(members, 1):
                zf.extract(member, dest_dir)
                on_progress(i, total)
        return True
    except Exception as e:
        _log("extract error for {0}: {1}".format(zip_path, e), level=_LOGERROR)
        return False


def _sha256_matches(path, want):
    """True when the file's sha256 equals `want`. Any failure counts as a mismatch."""
    try:
        from resources.libs import config_apply
        got = config_apply.sha256_file(path)
        return str(got).lower() == str(want).lower()
    except Exception:
        return False



def _latest_url(addon_id):
    return '{0}/{1}-latest.zip'.format(CONFIG.REPO_BASE_URL, addon_id)


def _expected_sha256(zip_url):
    """The hex digest published next to a zip ("<zip>.sha256"), or None."""
    response = tools.open_url(zip_url + '.sha256')
    if not response:
        return None
    try:
        words = response.text.split()
        return words[0].lower() if words else None
    except Exception:
        return None


def _zip_holds_only(zip_path, addon_id):
    """True when every entry sits under '<addon_id>/' and addon.xml is there (a repository zip is one add-on folder)."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
        return (bool(names) and (addon_id + '/addon.xml') in names and
                all(n.startswith(addon_id + '/') for n in names))
    except Exception:
        return False


def install_repository(addon_id):
    """Download '<id>-latest.zip', verify it against its .sha256, extract it into special://home/addons, register and enable it.

    Nothing is extracted unless the digest matches and the zip holds exactly one add-on folder named after the id.
    Returns True once the add-on is on disk."""
    url = _latest_url(addon_id)
    want = _expected_sha256(url)
    if not want:
        _log('no checksum published for {0}'.format(url), level=_LOGERROR)
        return False
    tools.ensure_folders(CONFIG.PACKAGES)
    dest = os.path.join(CONFIG.PACKAGES, os.path.basename(url))
    tools.remove_file(dest)
    try:
        if not download_zip(url, dest):
            _log('download failed: {0}'.format(url), level=_LOGERROR)
            return False
        if not _sha256_matches(dest, want):
            _log('checksum mismatch for {0}; nothing extracted'.format(url), level=_LOGERROR)
            return False
        if not _zip_holds_only(dest, addon_id):
            _log('{0} does not hold exactly the {1} folder; nothing extracted'.format(url, addon_id), level=_LOGERROR)
            return False
        if not extract_zip(dest, CONFIG.ADDONS):
            return False
    finally:
        tools.remove_file(dest)
    addon_state.register_and_enable([addon_id])
    return addon_state.on_disk(addon_id)


def ensure_repositories():
    """Both repository add-ons are installed and enabled. True when they are.

    Installs what is absent (from the site), enables what is on disk but disabled, then asks Kodi to load them and to refresh the
    repositories so InstallAddon can find the content add-ons. Idempotent; never raises."""
    try:
        changed = False
        for addon_id in REPOSITORY_IDS:
            if addon_state.has_addon(addon_id):
                continue
            if addon_state.on_disk(addon_id):
                _log('{0} is on disk but not enabled; enabling'.format(addon_id), level=_LOGWARNING)
                addon_state.enable_addon(addon_id)
                changed = True
                continue
            _log('installing {0} from the site'.format(addon_id))
            if install_repository(addon_id):
                changed = True
            else:
                _log('could not install {0}'.format(addon_id), level=_LOGERROR)
        if changed:
            xbmc.executebuiltin('UpdateLocalAddons')
            monitor = xbmc.Monitor()
            for _ in range(ENABLE_WAIT_SECONDS):
                if all(addon_state.has_addon(i) for i in REPOSITORY_IDS) or monitor.waitForAbort(1):
                    break
            xbmc.executebuiltin('UpdateAddonRepos')
    except Exception as err:
        _log('repository bootstrap failed: {0}'.format(err), level=_LOGERROR)
    return all(addon_state.has_addon(i) for i in REPOSITORY_IDS)
