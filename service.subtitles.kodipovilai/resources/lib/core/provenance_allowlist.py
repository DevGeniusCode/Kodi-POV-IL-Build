# The provenance allowlist as the running add-on uses it (Provenance P3 / P4): the shipped config/provenance_allowlist.json merged
# with the user's setting custom_trusted_uploaders. The classifier itself (scoring_matching/provenance.py) is pure; this is the
# Kodi side that reads the setting and the file, shared by the chooser window and the download-time sidecar.

import os

from resources.lib.core import kodi, settings

_STATE = {'custom': None, 'allow': None}


def reset():
    """Forget the loaded list (the next current() reads the file and the setting again)."""
    _STATE.update(custom=None, allow=None)


def current():
    """The Allowlist (loaded once per interpreter, again when the setting changes), or None on any trouble: the rows then get only the
    studio forms and nothing else. Never raises."""
    try:
        from resources.lib.scoring_matching import provenance
        custom = settings.get_setting('custom_trusted_uploaders', '') or ''
        if _STATE['allow'] is None or _STATE['custom'] != custom:
            resources = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _STATE.update(custom=custom, allow=provenance.load_allowlist(
                os.path.join(resources, 'config', 'provenance_allowlist.json'), custom))
        return _STATE['allow']
    except Exception as e:
        kodi.log('provenance allowlist unavailable: {0}'.format(e), level='DEBUG')
        return None
