# MoranSubs by MoranTheKing - https://github.com/MoranTheKing/Kodi-POV-IL
# SPDX-License-Identifier: GPL-3.0-or-later (additional terms: LICENSE.txt)
# The state of one list_candidates() call (SearchState), the stage protocol (NEXT) and the first two stages:
# the preferred-language gate and the video fields; the local files and the Hebrew subtitle alongside the video.

import os
from resources.lib.core import kodi, settings
from resources.lib.fetching import local
from resources.lib.core.link_codec import encode_link
from resources.lib.ai_translation.policy import enabled_sources


NEXT = object()   # what a stage returns when the work goes on with the next stage


class SearchState(object):
    """The state of one list_candidates() call: every variable that more than one stage, or a closure of the
    search, reads or writes. Nothing here is shared between calls. The attribute names are the variable names of
    the old single function without their leading underscore."""
    __slots__ = (
        # arguments of resolve()
        'info', 'modal_progress', 'quick', 'on_provider',
        # first set in the stage "context"
        'episode', 'filepath', 'imdb_id', 'season', 'sources', 'tmdb_id',
        # first set in the stage "local_files"
        'alongside', 'have_hebrew', 'in_temp', 'results',
        # first set in the stage "engine"
        'engine_embedded', 'engine_human', 'engine_mt', 'engine_other',
        # first set in the stage "ai_prep"
        'embedded_foreign',
        # first set in the stage "pool"
        'pool_variants', 'video_ref',
        # first set in the stage "ai_variants"
        'ai_translation_on', 'ai_variants',
        # first set in the stage "ai_entries"
        'ai_entries',
    )

    def __init__(self, info, modal_progress, quick, on_provider=None):
        self.on_provider = on_provider   # autosub's fast lane (bridge.search), None for every other caller
        self.info = info
        self.modal_progress = modal_progress
        self.quick = quick



def context(S):
    """The preferred-language gate and the fields of the playing video."""
    # The user's preferred subtitle language used to be able to switch this
    # whole add-on off: the gate returned [] -- not "no AI entries" as the old
    # message claimed, but NO ENTRIES AT ALL, including the plain Hebrew subs
    # and the shared pool. Field reports: "no subtitles for anything", on a
    # title that had hundreds the week before, with nothing in the log but this
    # line. All it took was Kodi's download-languages list holding English and
    # not Hebrew -- a setting most people have never opened.
    #
    # Someone who opens a HEBREW subtitle add-on wants Hebrew. So: try to heal
    # the setting first (add Hebrew, keep whatever else is there), and if that
    # does not take, carry on anyway. Offering entries the user can ignore is a
    # far smaller harm than showing them an empty dialog.
    if not settings.hebrew_subtitle_wanted():
        kodi.log(
            'list_candidates: Kodi\'s subtitle language preference does not '
            'name Hebrew -- listing anyway. Opening a Hebrew subtitle add-on '
            'IS the request for Hebrew, and an empty dialog is a dead end. '
            'Add Hebrew to Settings > Player > Language > "languages to '
            'download subtitles for" to silence this.', level='INFO')

    S.filepath = S.info.get('filepath') or ''
    S.imdb_id = (S.info.get('imdb_id') or '').strip()
    S.tmdb_id = (S.info.get('tmdb_id') or '').strip()
    S.season  = S.info.get('season') or ''
    S.episode = S.info.get('episode') or ''
    S.sources = enabled_sources()
    return NEXT


def local_files(S):
    """Local subtitle files and the Hebrew one alongside the video."""
    # Collect candidate source files from the two filesystem
    # locations we look at. By-language dicts, so first match wins
    # per language.
    S.alongside = {}
    # A manual search (fetching/manual.py) is for another title: the subtitle files next to the PLAYING file are not its rows.
    manual = bool(S.info.get('_manual'))
    for path, lang in ([] if manual else local.find_alongside(S.filepath)):
        if lang and lang not in S.alongside:
            S.alongside[lang] = path

    S.in_temp = {}
    for entry in ([] if manual else local.find_in_temp()):
        lang = entry['lang']
        if not lang or lang in S.in_temp:
            continue
        # CRITICAL: never accept Hebrew from the temp dir as a
        # passthrough candidate. The file Kodi keeps there
        # (typically TempSubtitle.he.srt) is whatever was
        # selected last -- which means after translating movie
        # A, opening the subtitle dialog for movie B would
        # surface movie A's Hebrew SRT as if it were a match
        # for movie B. Only trust local files alongside the video
        # for Hebrew passthrough.
        if lang == 'he':
            continue
        S.in_temp[lang] = entry['path']

    S.results = []

    # A Hebrew subtitle of THIS episode that came out of a season pack the viewer used for the previous episode
    # (fetching/season_pack.py): offered at once, before any provider answers; not for a manual search.
    if not manual and 'he' not in S.alongside:
        try:
            from resources.lib.fetching import season_pack
            kept = season_pack.lookup_for(S.info)
            if kept:
                S.alongside['he'] = kept['path']
        except Exception:
            pass

    # 1. Hebrew passthrough -- if there's already a Hebrew SRT we
    #    can hand to Kodi, no need to translate anything.
    S.have_hebrew = False
    if 'he' in S.alongside:
        S.have_hebrew = True
        S.results.append({
            'filename': os.path.basename(S.alongside['he']),
            'language': 'he',
            'link': encode_link({
                'type': 'passthrough', 'path': S.alongside['he'],
                'release': os.path.basename(S.alongside['he']),
            }),
            'sync': 'true',
            'rating': '5',
            'is_hi': False, 'is_hd': False, 'pct': None,
        })
    return NEXT
