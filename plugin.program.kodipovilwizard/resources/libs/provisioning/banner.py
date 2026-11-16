"""The persistent, non-modal "please wait" banner for the whole fresh-install /
provisioning run, driven by the shared ProvisionProgress tracker."""
import xbmc
import xbmcgui

from resources.libs.common.config import CONFIG
from resources.libs.common import logging


# Optional: a missing/broken progress helper must never crash startup or abort the
# install -- the banner then simply stays a static "please wait" message.
try:
    from resources.libs.provision_progress import ProvisionProgress
except Exception:
    ProvisionProgress = None


# Polite Hebrew wait message shown for the whole provisioning run.
PROVISION_WAIT_MSG_HE = 'אנא המתינו עד להשלמת ההתקנה וטעינת מסך הבית.'


def _make_provisioning_banner():
    """Create a persistent, NON-MODAL top banner for the entire fresh-install /
    provisioning run.

    A ``DialogProgressBG`` renders in Kodi's top-right corner and -- unlike a
    modal dialog or a transient notification -- STAYS VISIBLE behind every
    subsequent install dialog / first-run popup until we explicitly close it (or
    Kodi shuts down at the end of setup). It never grabs focus or input, so there
    is NO global watchdog and nothing to fight. Returns the dialog object, which
    the caller MUST keep a reference to (letting it be garbage-collected would
    close it), or ``None`` if it could not be created.

    It starts at 0%. From then on the ONLY thing that updates it is the shared
    ProvisionProgress tracker (see _make_provisioning_progress).
    """
    try:
        banner = xbmcgui.DialogProgressBG()
        banner.create(CONFIG.ADDONTITLE, PROVISION_WAIT_MSG_HE)
        banner.update(0, CONFIG.ADDONTITLE, PROVISION_WAIT_MSG_HE)
        return banner
    except Exception:
        return None


def _close_provisioning_banner(banner):
    try:
        if banner is not None:
            banner.close()
    except Exception:
        pass


def _make_provisioning_progress():
    """Create the banner and wrap it in the shared, monotonic ProvisionProgress
    tracker (its ticker thread is the single banner writer and also drives the
    indeterminate steps: dependency resolution, config apply, finalize).

    Returns (progress_or_None, banner_or_None). Keep BOTH references alive for the
    whole run. If the tracker is unavailable the plain banner is still shown."""
    banner = _make_provisioning_banner()
    if ProvisionProgress is None:
        return None, banner
    try:
        progress = ProvisionProgress(banner, CONFIG.ADDONTITLE, PROVISION_WAIT_MSG_HE)
        progress.start()
        return progress, banner
    except Exception as err:
        logging.log("[Fresh Build Auto Install] progress tracker unavailable: {0}".format(err),
                    level=xbmc.LOGWARNING)
        return None, banner


def _progress_call(progress, method, *args):
    """Call a tracker method; a progress problem must never break the install."""
    if progress is None:
        return
    try:
        getattr(progress, method)(*args)
    except Exception:
        pass


def _close_progress(progress, banner):
    """Bail-out path: stop the tracker AND close the banner."""
    if progress is not None:
        try:
            progress.close()
        except Exception:
            pass
    _close_provisioning_banner(banner)
