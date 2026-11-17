# -*- coding: utf-8 -*-
"""Glue between Umbrella's source flow and the subtitle add-on's he_sub_match (called from registry hooks).

Same shared brain that feeds POV's source window, so a title warmed from one add-on shows its Hebrew-subtitle match
badge at once in the other. he_sub_match lives in the subtitle add-on (stdlib-only at module level) and is imported by
path: Umbrella runs these hooks in ITS interpreter, so no package imports are possible.

  prewarm(meta)            start the background warm at the START of the scrape, so the % shows on the first visit.
  prefixer(meta)           once per source window: a function that returns the coloured badge for a release name.
  publish_source(item, u)  the picked source resolved: publish its release name and url (Window(10000)) for the
                           subtitle matcher; Umbrella publishes nothing itself and an opaque CDN url scores 0%.
"""
import sys

_SUBS_LIB = 'special://home/addons/service.subtitles.kodipovilai/resources/lib'


def _he_sub_match():
    import xbmcvfs
    path = xbmcvfs.translatePath(_SUBS_LIB)
    if path not in sys.path:
        sys.path.append(path)
    import he_sub_match
    return he_sub_match


def prewarm(meta):
    try:
        _he_sub_match().prewarm(meta)
    except Exception:
        pass


def prefixer(meta):
    try:
        he = _he_sub_match()
        names = he.release_names(meta)
        embedded = he.embedded_names(meta)
        synced = he.confirmed_releases(meta)
    except Exception:
        return lambda name: ''

    def prefix(name):
        try:
            return he.label_prefix(name, names, embedded, name, synced)
        except Exception:
            return ''
    return prefix


def publish_source(item, url):
    try:
        import xbmcgui
        name = (item.get('name') or '') if isinstance(item, dict) else ''
        if name:
            window = xbmcgui.Window(10000)
            window.setProperty('subs.player_filename', name)
            window.setProperty('pov_picked_source_name', name)
            window.setProperty('pov_picked_source_url', url or '')
    except Exception:
        pass
