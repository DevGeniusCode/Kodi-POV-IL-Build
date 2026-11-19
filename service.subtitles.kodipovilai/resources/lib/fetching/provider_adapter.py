# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# THE provider adapter boundary. This is the ONLY module of the add-on that imports
# fetching/providers (the vendored DarkSubs engine: engine.py, general.py and the seven
# provider modules under sources/). Everything else asks for the engine and for a provider
# through the loaders below. The loaders stay lazy on purpose: the engine's cold import is
# heavy (13-20 s measured on a 32-bit box), so nothing here imports it at module level.
#
# The provider contract: every module under providers/sources/ exposes
#   global_var   list that get_subs() appends result tuples to
#   site_id      the provider's id in the engine's labels
#   get_subs     get_subs(video_data[, all_lang_override])
#   download     download(download_data, MySubFolder) -> path

import importlib
import sys

PROVIDERS = ('ktuvit', 'wizdom', 'opensubtitles', 'yify', 'subsource', 'subscene', 'bsplayer')
PROVIDER_CONTRACT = ('global_var', 'site_id', 'get_subs', 'download')

_PKG = 'resources.lib.fetching.providers'


def load_engine():
    """The engine module (imports it on first use)."""
    return importlib.import_module(_PKG + '.engine')


def load_general():
    """The engine's shared helpers module (imports it on first use)."""
    return importlib.import_module(_PKG + '.general')


def load_provider(name):
    """One provider module by its source name; raises when the name is unknown or the import fails."""
    if name not in PROVIDERS:
        raise ImportError('unknown provider %r' % (name,))
    return importlib.import_module(_PKG + '.sources.' + name)


def loaded_engine():
    """The engine module if something already imported it, else None (never imports)."""
    return sys.modules.get(_PKG + '.engine')


def contract_gaps(module):
    """Names of the provider contract that `module` does not expose."""
    return [n for n in PROVIDER_CONTRACT if not hasattr(module, n)]
