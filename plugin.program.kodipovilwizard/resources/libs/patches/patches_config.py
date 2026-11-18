# -*- coding: utf-8 -*-

# Registry schema for Engine v2 patches
#
# Required fields per patch:
#   id, name, description, category, priority, addon_id, enabled,
#   target_file, marker, anchor, action, hook
#
# category : translation | ui | ux | performance | subtitles | crash_guard |
#            account_manager | parental_control | reliability | diagnostics |
#            compatibility
# priority : critical | major | minor
# action   : prepend_before | append_after   (additive-only; "replace" is forbidden)
# marker   : "# WIZARD_<ADDON>_<NAME>_v<X>"
# A description ending in [NEEDS_MODULE_CHECK] is queued for Phase 2 module inspection.
#
# Writing a hook
# --------------
# * A hook that imports a module from PATCHES_DIR is built with _build_patch_hook().
#   The helper emits one self-contained try/except block (sys.path setup, import,
#   custom logic), so a patch never depends on a name injected by another patch and
#   a failing patch is logged to the Kodi log instead of breaking the host add-on.
#   If the hook must still do something when the patch module fails (return a value,
#   bail out), put that statement AFTER the helper call, at the same indent.
# * A hook that needs no patch module (pure code substitution) stays a plain string.
# * Indentation: the engine dedents a hook and re-indents it to the ANCHOR line. A hook that is
#   `append_after` a `def` / `class` / `if` line (anchor ends with ":") must therefore start with a
#   column-0 comment line ("# Preserve the native function body below."): the comment stops the dedent
#   from stripping the body, which stays authored with its own absolute indentation. Without it the
#   body lands at column 0 under the `def` and the engine's compile guard rejects the WHOLE host file.
# * Hot paths (code that runs per item / per string / per cache write) get a plain inline hook or a
#   one-time install at import, never an import-and-delegate block on every call.
# * Identical hooks shared by several patches live in the _*_HOOK constants of
#   patches_registry_utils.py (together with _build_patch_hook and the validator).
# * Entries are never reordered; new ones are appended inside the banner of their domain
#   (or at the end of the list when no domain fits).
#
# Run `python patches_config.py [PATCHES_DIR]` to validate the registry (schema,
# unique ids/markers, every hook compiles; with PATCHES_DIR also that every
# imported patch module exists).

try:
    from resources.libs.patches.patches_registry_utils import (
        _build_patch_hook, _spaces, _TORRENT_ID_UNBOUND_HOOK, _META_BLANK_GUARD_HOOK,
        _GENRE_TRANSLATE_HOOK, GUARD_DIR, PATCHES_DIR, validate_registry)
except ImportError:  # run directly: python patches_config.py
    from patches_registry_utils import (
        _build_patch_hook, _spaces, _TORRENT_ID_UNBOUND_HOOK, _META_BLANK_GUARD_HOOK,
        _GENRE_TRANSLATE_HOOK, GUARD_DIR, PATCHES_DIR, validate_registry)


PATCH_CONFIG = [
    # =======================================================================================
    # ================= ONYX SKIN SUPPORT: RIGHT-TO-LEFT ROWS & CLEAR LOGOS =================
    # =======================================================================================
    {
        "id": "pov_rtl_rows",
        "name": "Right-to-left rows for the Onyx skin",
        "description": "Runs first in POV's entry script, ahead of the saved-list blocks, so a saved list is reversed right-to-left like any other row (Onyx skin only).",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/router.py",
        "marker": "# WIZARD_POV_RTL_ROWS_v1",
        "anchor": "if __name__ == '__main__':",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="povil_rtl",
            custom_logic="povil_rtl.install(pov=True)"
        )
    },
    {
        "id": "idanplus_rtl_rows",
        "name": "Right-to-left Idan Plus rows for the Onyx skin",
        "description": "Reverses Idan Plus rows right-to-left for the Onyx skin.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "default.py",
        "marker": "# WIZARD_IDANPLUS_RTL_ROWS_v1",
        "anchor": "from sys import argv",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="povil_rtl",
            custom_logic="povil_rtl.install()"
        )
    },
    {
        "id": "umbrella_rtl_rows",
        "name": "Right-to-left Umbrella lists for the Onyx skin",
        "description": "Reverses Umbrella lists right-to-left for the Onyx skin.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "umbrella.py",
        "marker": "# WIZARD_UMBRELLA_RTL_ROWS_v1",
        "anchor": "from resources.lib.modules import router",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="povil_rtl",
            custom_logic="povil_rtl.install(umbrella=True)"
        )
    },
    {
        "id": "pov_logo_language",
        "name": "Clear logo in the meta language",
        "description": "Picks the clear logo in the build's language (Hebrew logos with Hebrew posters) when POV asks for a logo.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/indexers/metadata.py",
        "marker": "# WIZARD_POV_LOGO_LANGUAGE_v1",
        "anchor": "if not path: return ''",
        "action": "prepend_before",
        "hook": (
            "if key_type == 'logo':\n"
            + _build_patch_hook(
                module_name="pov_logo_lang",
                indent="\t",
                custom_logic="path = pov_logo_lang.pick(self.data, self.lang, path)"
            )
        )
    },
    {
        "id": "pov_logo_for_ui",
        "name": "Logo that suits a Hebrew screen (clearlogo + povil_logo art)",
        "description": "A Hebrew title with only a non-Hebrew logo gets no clearlogo / tvshow.clearlogo, so skins fall back to the Hebrew title text; povil_logo (Onyx) carries the same choice.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/indexers/metadata.py",
        "marker": "# WIZARD_POV_LOGO_FOR_UI_v3",
        "anchor": "if meta_get('mediatype') == 'movie': return art",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_logo_lang",
            custom_logic="clearlogo = pov_logo_lang.apply(art, meta, clearlogo)"
        )
    },
    # ======================================================================================
    # ================= DIRECTORY CLOCK, SAVED-LIST REPLAY & ROUTE TIMING ==================
    # ======================================================================================
    {
        "id": "pov_directory_clock",
        "name": "Directory entry latency clock",
        "description": "Stamps a perf_counter start time when POV's entry module loads, feeding the directory latency measurements. [NEEDS_MODULE_CHECK]",
        "category": "diagnostics",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/entry.py",
        "marker": "# WIZARD_POV_DIRECTORY_CLOCK_v1",
        "anchor": "from threading import Thread",
        "action": "prepend_before",
        "hook": (
            "import time as _povil_clock\n"
            "_povil_entry_started = _povil_clock.perf_counter()\n"
        )
    },
    {
        "id": "pov_directory_latency",
        "name": "Saved-list replay and slow-route timing",
        "description": "Wraps POV's routing() once: replays saved lists (pov_list_snapshot) and logs the timing of slow catalogue routes. Disabling it turns saved lists off.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/entry.py",
        "marker": "# WIZARD_POV_DIRECTORY_LATENCY_v1",
        "anchor": "class Router:",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_directory_latency",
            custom_logic="pov_directory_latency.install(globals())"
        )
    },
    # ========================================================================================
    # ================= ROUTER: SAVED-LIST SNAPSHOT, PLAY GUARD & TV HISTORY =================
    # ========================================================================================
    {
        "id": "pov_list_snapshot_early",
        "name": "Saved lists served before POV is imported",
        "description": "Serves saved list snapshots from router.py before the heavy POV modules are imported, so widgets paint faster.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/router.py",
        "marker": "# WIZARD_POV_SNAPSHOT_EARLY_v3",
        "anchor": "from modules.entry import Router",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_list_snapshot",
            custom_logic=(
                # router.py imports no sys: reach it through __import__ (the helper's own convention).
                "if pov_list_snapshot.serve_early(__import__('sys').argv):\n"
                "\traise SystemExit"
            )
        )
    },
    {
        "id": "pov_play_guard",
        "name": "Queued taps do not start several episodes",
        "description": "Drops duplicate queued play requests before POV starts them, so rapid taps cannot launch several episodes.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/router.py",
        "marker": "# WIZARD_POV_PLAY_GUARD_v3",
        "anchor": "from modules.entry import Router",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_play_guard",
            custom_logic=(
                "if pov_play_guard.duplicate(__import__('sys').argv):\n"
                "\traise SystemExit"
            )
        )
    },
    {
        "id": "pov_play_guard_end",
        "name": "A finished play request ends its notice timer",
        "description": "Signals the play guard when a router request finishes, ending its notice timer.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/router.py",
        "marker": "# WIZARD_POV_PLAY_GUARD_END_v2",
        "anchor": "Router()()",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_play_guard",
            custom_logic="pov_play_guard.request_over()"
        )
    },
    {
        "id": "pov_linear_tv_history",
        "name": "Linear native TV history grouping",
        "description": "Serves TV watch history as a linear, show-grouped list from pov_watch_history (falls back to the native query on error).",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/watched_cache.py",
        "marker": "# WIZARD_POV_LINEAR_TV_HISTORY_v2",
        "anchor": "\t\tcommand = GET_MOVIE_SHOW % ('media_id, title, last_played, season, episode')",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_watch_history",
            indent="\t\t",
            custom_logic="return pov_watch_history.tv_history(globals(), watched_indicators)"
        )
    },
    # =========================================================================================
    # ================= IDAN PLUS: CHILD-PROFILE SOURCE GUARD & LIST SNAPSHOT =================
    # =========================================================================================
    {
        "id": "idanplus_child_source",
        "name": "Child profile unclassified live source guard",
        "description": "Blocks unclassified live sources in Idan Plus while a child profile is active.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "target_file": "default.py",
        "marker": "# WIZARD_IDANPLUS_CHILD_SOURCE_v1",
        "anchor": "from resources.main import route",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            fail_closed=True,
            patch_dir=GUARD_DIR,
            custom_logic=(
                "if not profile_age_guard.unclassified_source_allowed():\n"
                "\traise SystemExit()"
            )
        )
    },
    {
        "id": "idanplus_list_snapshot",
        "name": "Saved Idan Plus home rows",
        "description": "Serves saved Idan Plus home rows from a snapshot before the add-on router loads.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "target_file": "default.py",
        "marker": "# WIZARD_IDANPLUS_SNAPSHOT_v3",
        "anchor": "from resources.main import route",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_list_snapshot",
            custom_logic=(
                "if pov_list_snapshot.idanplus(__import__('sys').argv):\n"
                "\traise SystemExit"
            )
        )
    },
    # ===========================================================================================
    # ================= PARENTAL CONTROL: CHILD-PROFILE GUARDS (POV & UMBRELLA) =================
    # ===========================================================================================
    {
        "id": "pov_profile_movie_metadata",
        "name": "Child movie classification",
        "description": "Stamps child-profile age classification onto movie list items.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/movies.py",
        "marker": "# WIZARD_POV_PROFILE_MOVIE_META_v2",
        "anchor": "\t\t\tself.append((url_params, listitem, False))",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            patch_dir=GUARD_DIR,
            indent="\t\t\t",
            custom_logic="profile_age_guard.stamp_item(listitem, meta)"
        )
    },
    {
        "id": "pov_profile_tv_metadata",
        "name": "Child series classification",
        "description": "Stamps child-profile age classification onto series list items.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/tvshows.py",
        "marker": "# WIZARD_POV_PROFILE_TV_META_v2",
        "anchor": "\t\t\tself.append((url_params, listitem, self.is_folder))",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            patch_dir=GUARD_DIR,
            indent="\t\t\t",
            custom_logic="profile_age_guard.stamp_item(listitem, meta)"
        )
    },
    {
        "id": "pov_profile_episode_metadata",
        "name": "Child episode and season classification",
        "description": "Stamps child-profile age classification onto episode and season list items.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/metadata.py",
        "marker": "# WIZARD_POV_PROFILE_EPISODE_META_v2",
        "anchor": "\tinfotag = listitem.getVideoInfoTag(offscreen=True)",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            patch_dir=GUARD_DIR,
            indent="\t",
            custom_logic="profile_age_guard.stamp_item(listitem, meta or {})"
        )
    },
    {
        "id": "umbrella_profile_metadata",
        "name": "Child content classification",
        "description": "Stamps child-profile age classification onto Umbrella list items right after the MPAA tag is set.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "target_file": "resources/lib/modules/control.py",
        "marker": "# WIZARD_UMBRELLA_PROFILE_META_v2",
        "anchor": "\t\t\tinfo_tag.setMpaa(meta_get('mpaa') or '')",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            patch_dir=GUARD_DIR,
            indent="\t\t\t",
            custom_logic="profile_age_guard.stamp_item(item, meta)"
        )
    },
    {
        "id": "pov_profile_directory_guard",
        "name": "Child profile directory guard",
        "description": "Installs the child-profile directory guard when POV's entry module loads.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/entry.py",
        "marker": "# WIZARD_POV_PROFILE_DIRECTORY_v1",
        "anchor": "from modules import kodi_utils, settings",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            fail_closed=True,
            patch_dir=GUARD_DIR,
            custom_logic="profile_age_guard.install_directory_guard()"
        )
    },
    {
        "id": "pov_profile_route_guard",
        "name": "Child profile account route guard",
        "description": "Rejects account-related routes for child profiles before POV dispatches them.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/entry.py",
        "marker": "# WIZARD_POV_PROFILE_ROUTE_v2",
        "anchor": "\tparams = kodi_utils.parsed_query(sys_obj.argv[2])",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            indent="\t",
            fail_closed=True,
            patch_dir=GUARD_DIR,
            custom_logic=(
                "if not profile_age_guard.route_allowed(params, 'pov'):\n"
                "\treturn"
            )
        )
    },
    {
        "id": "pov_profile_play_guard",
        "name": "Child profile playback guard",
        "description": "Blocks POV playback of content not permitted for the active child profile.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/player.py",
        "marker": "# WIZARD_POV_PROFILE_PLAY_v1",
        "anchor": "\t\tif not url: return",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            indent="\t\t",
            fail_closed=True,
            patch_dir=GUARD_DIR,
            custom_logic=(
                "if not profile_age_guard.permit_play(meta or {}, 'pov', url):\n"
                "\treturn"
            )
        )
    },
    {
        "id": "umbrella_profile_directory_guard",
        "name": "Child profile directory guard",
        "description": "Installs the child-profile directory guard when Umbrella's control module loads.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "target_file": "resources/lib/modules/control.py",
        "marker": "# WIZARD_UMBRELLA_PROFILE_DIRECTORY_v1",
        "anchor": "import xbmcvfs",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            fail_closed=True,
            patch_dir=GUARD_DIR,
            custom_logic="profile_age_guard.install_directory_guard()"
        )
    },
    {
        "id": "umbrella_profile_route_guard",
        "name": "Child profile account route guard",
        "description": "Rejects account-related routes for child profiles before Umbrella dispatches them.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "target_file": "resources/lib/modules/router.py",
        "marker": "# WIZARD_UMBRELLA_PROFILE_ROUTE_v1",
        "anchor": "\taction = params.get('action')",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            indent="\t",
            fail_closed=True,
            patch_dir=GUARD_DIR,
            custom_logic=(
                "if not profile_age_guard.route_allowed(params, 'umbrella'):\n"
                "\treturn"
            )
        )
    },
    {
        "id": "umbrella_profile_play_guard",
        "name": "Child profile playback guard",
        "description": "Blocks Umbrella playback of content not permitted for the active child profile and cancels playback.",
        "category": "parental_control",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "target_file": "resources/lib/modules/player.py",
        "marker": "# WIZARD_UMBRELLA_PROFILE_PLAY_v1",
        "anchor": "\t\t\tposter, thumb, season_poster, fanart, banner, clearart, clearlogo, discart, meta = self.getMeta(meta)",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="profile_age_guard",
            indent="\t\t\t",
            fail_closed=True,
            patch_dir=GUARD_DIR,
            custom_logic=(
                "_age_meta = dict(meta or {}, mediatype=self.media_type, tmdb=self.tmdb)\n"
                "if not profile_age_guard.permit_play(_age_meta, 'umbrella', url):\n"
                "\treturn control.cancelPlayback()"
            )
        )
    },
    # ===================================================================================
    # ================= PAGINATION, LOCAL FAVOURITES & HEBREW INTERFACE =================
    # ===================================================================================
    {
        "id": "pov_merged_personal_pagination",
        "name": "Merged personal lists paginate after ordering",
        "description": "Lets merged personal lists use the 'povil_merge_all' page marker so pagination happens after ordering.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/utils.py",
        "marker": "# WIZARD_POV_MERGED_PERSONAL_PAGINATION_v1",
        "anchor": "\tif not item_list: return item_list, page",
        "action": "prepend_before",
        "hook": (
            "\tif page == 'povil_merge_all': return item_list, 1\n"
        )
    },
    {
        "id": "pov_trakt_personal_newest",
        "name": "trakt personal lists newest first",
        "description": "Sorts the user's personal Trakt lists newest-first.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/trakt_api.py",
        "marker": "# WIZARD_POV_TRAKT_PERSONAL_NEWEST_v2",
        "anchor": "\treturn trakt_cache.cache_trakt_object(_get_trakt_paginated_list, string, url)",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t",
            custom_logic=(
                "if list_type == 'my_lists':\n"
                "\treturn pov_mdblist_patch_logic.newest_personal_items(trakt_cache.cache_trakt_object(_get_trakt_paginated_list, string, url))"
            )
        )
    },
    {
        "id": "pov_mdblist_personal_newest",
        "name": "mdblist personal lists newest first",
        "description": "Sorts the user's personal MDBList lists newest-first.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBLIST_PERSONAL_NEWEST_v2",
        "anchor": "\treturn mdbl_cache.cache_mdbl_object(_get_mdbl_paginated_list, string, url)['items']",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t",
            custom_logic=(
                "if list_type == 'my_lists':\n"
                "\turl = pov_mdblist_patch_logic.personal_mdbl_url(url + '&povil_personal=1')\n"
                "\treturn mdbl_cache.cache_mdbl_object(_get_mdbl_paginated_list, string + '_newest_v1', url)['items']"
            )
        )
    },
    {
        "id": "pov_hebrew_interface",
        "name": "Persistent Hebrew POV interface",
        "description": "Merges the Wizard's Hebrew .po (every string whose English text still matches POV's) and the curated labels in pov_ui_labels.HEBREW_UI (they win) into POV's embedded strings table once, at import. POV 6.11 never reads a .po itself.",
        "category": "translation",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/kodi_utils.py",
        "marker": "# WIZARD_POV_HEBREW_INTERFACE_v3",
        "anchor": "from .strings_po import strings_po",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_ui_labels",
            custom_logic="strings_po = __import__('types').MappingProxyType(pov_ui_labels.merge(strings_po))"
        )
    },
    {
        "id": "pov_local_favorites_recent",
        "name": "Newest local favorites first",
        "description": "Returns local POV favorites newest-first (ORDER BY rowid DESC).",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/local_api.py",
        "marker": "# WIZARD_POV_LOCAL_FAVORITES_RECENT_v1",
        "anchor": "\tpath = 'SELECT tmdb_id, title FROM favorites WHERE db_type = ?'",
        "action": "prepend_before",
        "hook": (
            "\tdata = call_local('SELECT tmdb_id, title FROM favorites WHERE db_type = ? ORDER BY rowid DESC', (mediatype,))\n"
            "\tif page_no == 'all': return data\n"
            "\treturn [{'media_id': str(i['tmdb_id']), 'title': i['title']} for i in data], 1\n"
        )
    },
    # =========================================================
    # ================= CACHE & DEBRID SAFETY =================
    # =========================================================
    {
        "id": "pov_cache_empty_prevention",
        "name": "POV Empty Cache Prevention",
        "description": "Prevents an empty result from being written to the main cache (an empty row would be served for 24 h).",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/main_cache.py",
        "marker": "# WIZARD_POV_CACHE_EMPTY_v2",
        "anchor": "\tmaincache.set(string, result, expiration)",
        "action": "prepend_before",
        "hook": (
            "\tif not result: return result\n"
        )
    },
    {
        "id": "pov_debrid_unbound_guard",
        "name": "Debrid Unbound Locals Guard",
        "description": "Initializes files and torrent_id at the top of the function to prevent UnboundLocalError in the except block.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/debrid.py",
        "marker": "# WIZARD_POV_DEBRID_RESOLVE_GUARD_v2",
        "anchor": "from modules.source_utils import supported_video_extensions, seas_ep_filter, extras_filter",
        "action": "prepend_before",
        "hook": (
            "\tfiles = None; torrent_id = None\n"
        )
    },
    {
        "id": "pov_results_cache_guard",
        "name": "Results survive a failed results-cache write",
        "description": "Wraps ExternalProvidersCache.set so a malformed or full providerscache.db costs only the cache, not every provider's results. Module scope, so a Wizard that is gone must never stop sources.py from importing.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/sources.py",
        "marker": "# WIZARD_POV_RESULTS_CACHE_GUARD_v1",
        "anchor": "from caches.providers_cache import ExternalProvidersCache",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_cache_guard",
            custom_logic="pov_cache_guard.guard(ExternalProvidersCache)"
        )
    },
    {
        "id": "pov_keep_build_files",
        "name": "POV clean-up keeps the build's files",
        "description": "remove_old_databases keeps the build's povil* files and navigator.before-* menu backups in POV's data folder.",
        "category": "reliability",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/cache.py",
        "marker": "# WIZARD_POV_KEEP_BUILD_FILES_v1",
        "anchor": "	current_dbs = kodi_utils.current_dbs()",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_keep_files",
            custom_logic="current_dbs = pov_keep_files.keep(current_dbs)"
        )
    },
    # ===================================================================================
    # ================= NAVIGATOR: VISIBILITY, SHORTCUTS & TRANSLATIONS =================
    # ===================================================================================
    {
        "id": "wizard_pov_visibility_mgr_navigator",
        "name": "Dynamic Visibility Manager (Widget Folders)",
        "description": "Shadows the shortcut-folder contents in memory and hides personal Trakt/TMDB/MDBList rows when the service is not connected (also injects the MDBList row when connected). Never writes navigator.db; any failure leaves the list untouched.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_VISIBILITY_MGR_v3",
        "anchor": "\t\tcontents = nc.get_shortcut_folder_contents(list_name)",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_visibility_mgr",
            indent="\t\t",
            custom_logic="contents = pov_visibility_mgr.filter_navigator_list(contents, list_name)"
        )
    },
    {
        "id": "pov_genre_icons_rewrite",
        "name": "POV Distinct Genre Icons",
        "description": "Renders genre shortcut entries with custom icons from special://home/media/povil_icons.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_GENRE_ICONS_v2",
        "anchor": "\t\t\tlist_name = '%s: %s %s' % (lst_ins.upper(), genre, ls(32470))",
        "action": "append_after",
        "hook": (
            "\t\t\ttry:\n"
            "\t\t\t\tfrom xbmcvfs import translatePath\n"
            "\t\t\t\t_povil_icon = translatePath('special://home/media/povil_icons/%s' % value[1])\n"
            "\t\t\t\tself._add_item({'mode': mode, 'action': action, 'genre_id': value[0], 'name': genre}, _povil_icon, list_name=list_name)\n"
            "\t\t\t\tcontinue\n"
            "\t\t\texcept Exception:\n"
            "\t\t\t\tpass\n"
        )
    },
    {
        "id": "pov_shortcut_absolute_paths",
        "name": "POV Shortcut Folder Absolute Paths",
        "description": "Keeps absolute special://, http and resource:// icon paths intact for shortcut-folder items.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_SHORTCUT_PATHS_v2",
        "anchor": "\t\t\t\t\ticon = item_get('iconImage') if item_get('network_id', '') != '' else '%s%s' % (icon_path, item_get('iconImage'))",
        "action": "append_after",
        "hook": (
            "\t\t\t\t\tif icon and str(item_get('iconImage') or '').startswith(('special://', 'http', 'resource://')):\n"
            "\t\t\t\t\t\ticon = item_get('iconImage')\n"
        )
    },
    {
        "id": "pov_shortcut_fanart_shadow",
        "name": "POV Shortcut Fanart Shadow Fix",
        "description": "Uses the genre icon as fanart for genre shortcuts instead of the folder fanart.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_FANART_SHADOW_v4",
        "anchor": "\t\t\t\t\tlistitem.setLabel(name)",
        "action": "prepend_before",
        "hook": (
            "\t\t\t\t\ttry:\n"
            "\t\t\t\t\t\tnonlocal fanart\n"
            "\t\t\t\t\t\tfanart = icon if 'genres/' in str(icon or '') else self.params_get('fanart')\n"
            "\t\t\t\t\texcept Exception:\n"
            "\t\t\t\t\t\tpass\n"
        )
    },
    {
        "id": "pov_anime_trakt_strings",
        "name": "POV Anime Trakt Strings Translation",
        "description": "Translates hardcoded English strings in Trakt lists to Hebrew.",
        "category": "translation",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_ANIME_TRAKT_STRINGS_v2",
        "anchor": "\t\tcal_str, ani_str, drp_str = 'Trakt Calendar', 'Anime Calendar', 'Dropped TV Shows'",
        "action": "append_after",
        "hook": (
            "\t\tani_str, drp_str = 'לוח שידורי אנימה', 'סדרות שנזנחו'\n"
        )
    },
    {
        "id": "pov_anime_years_breadcrumbs",
        "name": "POV Anime Years Breadcrumbs Translation",
        "description": "Translates dynamic breadcrumb titles for Anime Years.",
        "category": "translation",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_ANIME_YEARS_BREADCRUMBS_v2",
        "anchor": "\t\t\tlist_name = 'ANIME %s: %s %s' % (lst_ins.upper(), str(i), ls(32460))",
        "action": "append_after",
        "hook": (
            "\t\t\tlist_name = '%s: %s' % (('סרטי אנימה' if menu_type == 'movie' else 'סדרות אנימה'), str(i))\n"
        )
    },
    {
        "id": "pov_anime_genres_breadcrumbs",
        "name": "POV Anime Genres Breadcrumbs Translation",
        "description": "Translates dynamic breadcrumb titles for Anime Genres.",
        "category": "translation",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_ANIME_GENRES_BREADCRUMBS_v2",
        "anchor": "\t\t\tlist_name = 'ANIME %s: %s %s' % (lst_ins.upper(), genre, ls(32470))",
        "action": "append_after",
        "hook": (
            "\t\t\tlist_name = '%s: %s' % (('סרטי אנימה' if menu_type == 'movie' else 'סדרות אנימה'), genre)\n"
        )
    },
    {
        "id": "pov_genre_dict_main_loop_translation",
        "name": "POV Genre Dict Main Loop Translation",
        "description": "Translates genre names in the navigator genre list (main loop) via pov_translations.",
        "category": "translation",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_GENRE_DICT_MAIN_LOOP_v2",
        "anchor": "\t\tself._add_item({'mode': 'navigator.multiselect_genres', 'genre_list': json.dumps(genre_list), 'menu_type': menu_type, 'exclude_external': 'true', 'name': ls(32846)}, 'genres.png', isFolder=False)",
        "action": "append_after",
        "hook": _GENRE_TRANSLATE_HOOK
    },
    {
        "id": "pov_genre_dict_multiselect_translation",
        "name": "POV Genre Dict Multiselect Translation",
        "description": "Translates genre names in the multiselect genre dialog via pov_translations.",
        "category": "translation",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/navigator.py",
        "marker": "# WIZARD_POV_GENRE_DICT_MULTISELECT_v3",
        "anchor": "\t\tgenre_list = dict(sorted(json.loads(genre_list).items()))",
        "action": "append_after",
        "hook": _GENRE_TRANSLATE_HOOK
    },
    # ===========================================================================================
    # ================= TMDB & METADATA: TIMEOUTS, NETWORKS & BLANK-META GUARDS =================
    # ===========================================================================================
    {
        "id": "pov_tmdb_timeout_widen",
        "name": "POV TMDB Timeout Widen",
        "description": "Widens the TMDB request timeout from POV's 5 s to 8 s so per-item fetches survive slow/mobile networks (POV derives list_timeout = timeout ** 2 = 64 s for non-GET calls).",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/tmdb_api.py",
        "marker": "# WIZARD_POV_TMDB_TIMEOUT_v3",
        "anchor": "timeout = 5",
        "action": "append_after",
        "hook": (
            "timeout = 8  # WIZARD: Widened for mobile per-item fetch reliability\n"
        )
    },
    {
        "id": "pov_movie_networks_providers_fix",
        "name": "POV Movie Networks Providers Fix",
        "description": "Rebuilds the TMDB movie networks discover endpoint to use streaming watch providers (US, flatrate) instead of production companies.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/tmdb_api.py",
        "marker": "# WIZARD_POV_MOVIE_NETWORKS_v6",
        "anchor": "\turl += '&sort_by=popularity.desc&certification_country=US&with_companies=%s' % network_id",
        "action": "append_after",
        "hook": (
            "\tendpoint = '/discover/movie?language=en-US&region=US&page=%s' % page_no\n"
            "\tendpoint += '&sort_by=popularity.desc&certification_country=US&with_watch_providers=%s&watch_region=US&with_watch_monetization_types=flatrate' % network_id\n"
            "\turl = '/3' + endpoint\n"
            "\tstring = 'tmdb_movie_providers_v5_US_%s_%s' % (network_id, page_no)\n"
        )
    },
    {
        "id": "pov_movie_meta_blank_guard",
        "name": "POV Movie Meta Blank Guard",
        "description": "A blank movie metadata entry (transient TMDb failure) is returned but no longer cached for 2 days.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/metadata.py",
        "marker": "# WIZARD_POV_MOVIE_META_GUARD_v4",
        "anchor": "\t\t\tmetacache_set('movie', id_type, meta, EXPIRES_2_DAYS)",
        "action": "prepend_before",
        "hook": _META_BLANK_GUARD_HOOK
    },
    {
        "id": "pov_tvshow_meta_blank_guard",
        "name": "POV TVShow Meta Blank Guard",
        "description": "A blank TV show metadata entry (transient TMDb failure) is returned but no longer cached for 2 days.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/metadata.py",
        "marker": "# WIZARD_POV_TVSHOW_META_GUARD_v4",
        "anchor": "\t\t\tmetacache_set('tvshow', id_type, meta, EXPIRES_2_DAYS)",
        "action": "prepend_before",
        "hook": _META_BLANK_GUARD_HOOK
    },
    # =========================================================================
    # ================= MY SERVICES, ACCOUNT MANAGER & TORBOX =================
    # =========================================================================
    {
        "id": "pov_repeat_timer_resiliency",
        "name": "RepeatTimer Crash Prevention",
        "description": "Wraps the RepeatTimer callback in try/except so a failing callback cannot kill the timer thread.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/myservices.py",
        "marker": "# WIZARD_POV_REPEAT_TIMER_v1",
        "anchor": "\t\t\tself.function(*self.args, **self.kwargs)",
        "action": "prepend_before",
        "hook": (
            "\t\t\ttry:\n"
            "\t\t\t\tself.function(*self.args, **self.kwargs)\n"
            "\t\t\texcept Exception:\n"
            "\t\t\t\tpass\n"
            "\t\t\tcontinue\n"
        )
    },
    {
        "id": "pov_acctmgr_integration",
        "name": "POV Account Manager & Services Integration",
        "description": "Intercepts POV's My Services to route Debrid/Trakt to Account Manager and inject Gemini AI UI.",
        "category": "account_manager",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/myservices.py",
        "marker": "# WIZARD_POV_SERVICES_ACCTMGR_v2",
        "anchor": "class RepeatTimer(Timer):",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_myservices",
            custom_logic="authorize = pov_myservices.get_authorize_override(authorize)"
        ) + "\n"
    },
    {
        "id": "pov_torbox_api_user_stats",
        "name": "TorBox Stats API Addition",
        "description": "Adds a user_stats() method to the TorBox API client for the usage UI.",
        "category": "account_manager",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/torbox_api.py",
        "marker": "# WIZARD_POV_TORBOX_API_STATS_v3",
        "anchor": "\tdef torrent_info(self, request_id, path='torrents'):",
        "action": "prepend_before",
        "hook": (
            "\tdef user_stats(self):\n"
            "\t\tparams = {'general': 'true', 'bandwidth': 'true', 'bandwidth_grouping': 'day'}\n"
            "\t\treturn self.api('get', '/v1/api/user/stats', params=params)\n"
            "\n"
        )
    },
    {
        "id": "pov_torbox_usage_ui",
        "name": "TorBox 30-Day Usage UI",
        "description": "Shows a 30-day TorBox usage text viewer from the account info dialog.",
        "category": "account_manager",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/debrids/tb_cloud.py",
        "marker": "# WIZARD_POV_TORBOX_USAGE_UI_v4",
        "anchor": "\t\t\tappend('[B]Downloaded[/B]: %s' % account_info['total_downloaded'])",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_torbox_usage",
            indent="\t\t\t",
            custom_logic=(
                "kodi_utils.hide_busy_dialog()\n"
                "return pov_torbox_usage.show_textviewer_and_exit(self, account_info)"
            )
        )
    },
    # ===============================================
    # ================= TRAKT CACHE =================
    # ===============================================
    {
        "id": "pov_trakt_empty_cache_fix",
        "name": "Trakt Empty Cache Prevention",
        "description": "Prevents transient empty Trakt API responses from being permanently cached.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/trakt_cache.py",
        "marker": "# WIZARD_POV_TRAKT_EMPTY_CACHE_v4",
        "anchor": "dbcur.execute(TC_BASE_SET, (string, json.dumps(result)))",
        "action": "prepend_before",
        "hook": (
            "if not result: return result\n"
        )
    },
    # =============================================================
    # ================= VIEW MODE & ADD-ON WINDOW =================
    # =============================================================
    {
        "id": "wizard_pov_view_mode_fix",
        "name": "Persistent View Mode Patcher",
        "description": "Fixes the intermittent bug where navigating to a new page resets the view to a plain list instead of the user's chosen view.",
        "category": "ui",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/kodi_utils.py",
        "marker": "# WIZARD_POV_VIEW_MODE_FIX_v2",
        "anchor": "\t\tfor _ in range(60):",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_view_mode",
            indent="\t\t",
            custom_logic=(
                "pov_view_mode.force_view(view_id, content)\n"
                "return"
            )
        )
    },
    {
        "id": "pov_addon_window_import",
        "name": "POV Addon Window Patcher (Import Scope)",
        "description": "Waits out Kodi's unknown-addon window on import to prevent background service death.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/kodi_utils.py",
        "marker": "# WIZARD_POV_ADDON_WINDOW_IMPORT_v3",
        "anchor": "from xbmcaddon import Addon",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_addon_window",
            custom_logic=(
                "pov_addon_window.wait_for_window()\n"
                "Addon = pov_addon_window.patient_addon(Addon)"
            )
        )
    },
    # =======================================================
    # ================= DISCOVER & MY LISTS =================
    # =======================================================
    {
        "id": "wizard_pov_combined_discover",
        "name": "Unified Discover Builder",
        "description": "Injects a unified Movie+TV search and trending data source for skin integrations like AF3.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/tmdb.py",
        "marker": "# WIZARD_POV_COMBINED_DISCOVER_v4",
        "anchor": "\t\treturn tmdb_api.list_details(self.list_id)",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_combined_discover",
            indent="\t\t",
            custom_logic=(
                "_povil_res = pov_combined_discover.handle_fetch(self.params)\n"
                "if _povil_res is not None:\n"
                "\treturn _povil_res"
            )
        )
    },
    {
        "id": "wizard_pov_my_lists_movies",
        "name": "My Lists Dispatch Hook (Movies)",
        "description": "Dispatches 'My Lists' population for movie menus via pov_my_lists.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/movies.py",
        "marker": "# WIZARD_POV_MY_LISTS_MOVIES_v4",
        "anchor": "\t\t\texcept ValueError: page_no = params_get('new_page')",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_my_lists",
            indent="\t\t\t",
            custom_logic="pov_my_lists.maybe_populate_movies(self, page_no)"
        )
    },
    {
        "id": "wizard_pov_my_lists_tvshows",
        "name": "My Lists Dispatch Hook (TV Shows)",
        "description": "Dispatches 'My Lists' population for TV show menus via pov_my_lists.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/tvshows.py",
        "marker": "# WIZARD_POV_MY_LISTS_TVSHOWS_v4",
        "anchor": "\t\t\texcept ValueError: page_no = params_get('new_page')",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_my_lists",
            indent="\t\t\t",
            custom_logic="pov_my_lists.maybe_populate_tvshows(self, page_no)"
        )
    },
    # ==================================================================
    # ================= DEBRID STATUS TOASTS (SERVICE) =================
    # ==================================================================
    {
        "id": "pov_debrid_status",
        "name": "Debrid Expiry Notification Override",
        "description": "Dynamically disables POV's upstream generic debrid notification in memory to prevent duplication with the custom build's Hebrew UI toasts.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/service.py",
        "marker": "# WIZARD_POV_DEBRID_STATUS_v3",
        "anchor": "\tSettingsMonitor()()",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_debrid_status",
            indent="\t",
            custom_logic="pov_debrid_status.run(locals())"
        )
    },
    {
        "id": "pov_custom_debrid_toasts",
        "name": "Custom Debrid Startup Toasts",
        "description": "Fires Hebrew-localized, icon-aware Debrid subscription status notifications when Kodi starts, replacing the generic upstream toasts.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/service.py",
        "marker": "# WIZARD_POV_CUSTOM_DEBRID_TOASTS_v3",
        "anchor": "\tSettingsMonitor()()",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_custom_debrid_toasts",
            indent="\t",
            custom_logic=(
                "import threading\n"
                "threading.Thread(target=pov_custom_debrid_toasts.run, args=(locals(),)).start()"
            )
        )
    },
    # ======================================================
    # ================= FAVOURITES REFRESH =================
    # ======================================================
    {
        "id": "wizard_fav_refresh_manage",
        "name": "Favorites Refresh (List Manager)",
        "description": "Forces the UI container to refresh immediately when adding a title to TMDB/Trakt/MDBList, preventing stale views.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/list_helper.py",
        "marker": "# WIZARD_FAV_REFRESH_MANAGE_v2",
        "anchor": "return self.execute_toggle(choice, action_add)",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_fav_refresh_manage",
            custom_logic="return pov_fav_refresh_manage.run(locals())"
        )
    },
    {
        "id": "wizard_fav_refresh_dialog",
        "name": "Favorites Refresh (Local Dialogs)",
        "description": "Forces the UI container to refresh immediately when adding/removing a title to POV-local favorites, preventing stale views. Guards against search crashes.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/dialogs.py",
        "marker": "# WIZARD_FAV_REFRESH_DIALOG_v4",
        "anchor": "if refresh: ku.container_refresh()",
        "action": "prepend_before",
        "hook": (
            "\trefresh = 'search' not in (__import__('xbmc').getInfoLabel('Container.FolderPath') or '').lower()\n"
        )
    },
    # ==================================================================
    # ================= DEBRID API GUARDS & ERROR LOGS =================
    # ==================================================================
    {
        "id": "pov_ad_unbound_guard",
        "name": "POV AllDebrid Unbound Guard",
        "description": "Prevents crash in except block by ensuring torrent_id is bound.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/alldebrid_api.py",
        "marker": "# WIZARD_POV_AD_UNBOUND_GUARD_v3",
        "anchor": "from modules.source_utils import supported_video_extensions",
        "action": "append_after",
        "hook": _TORRENT_ID_UNBOUND_HOOK
    },
    {
        "id": "pov_rd_unbound_guard",
        "name": "POV RealDebrid Unbound Guard",
        "description": "Prevents crash in except block by ensuring torrent_id is bound.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/realdebrid_api.py",
        "marker": "# WIZARD_POV_RD_UNBOUND_GUARD_v3",
        "anchor": "from modules.source_utils import supported_video_extensions",
        "action": "append_after",
        "hook": _TORRENT_ID_UNBOUND_HOOK
    },
    {
        "id": "pov_tb_unbound_guard",
        "name": "POV TorBox Unbound Guard",
        "description": "Prevents crash in except block by ensuring path and torrent_id are bound.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/torbox_api.py",
        "marker": "# WIZARD_POV_TB_UNBOUND_GUARD_v4",
        "anchor": "\t\tfrom modules.source_utils import supported_video_extensions",
        "action": "append_after",
        "hook": (
            "\t\tpath = None\n"
            "\t\ttorrent_id = None\n"
        )
    },
    {
        "id": "pov_torbox_url_fix_indexers",
        "name": "POV TorBox Malformed URL Fix (Indexers)",
        "description": "Normalizes malformed TorBox download URLs returned by the API via pov_torbox_url_fix.safe_url (fails open to the raw response).",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/torbox_api.py",
        "marker": "# WIZARD_POV_TORBOX_URL_FIX_v6",
        "anchor": "\t\tif user_ip: params['user_ip'] = user_ip",
        "action": "append_after",
        "hook": (
            "\t\t_povil_raw = self.api('get', path, params=params)\n"
            + _build_patch_hook(
                module_name="pov_torbox_url_fix",
                indent="\t\t",
                custom_logic="return pov_torbox_url_fix.safe_url(_povil_raw)"
            )
            # Fail open to the raw response so the native API call below is not repeated.
            + "\t\treturn _povil_raw\n"
        )
    },
    {
        "id": "pov_ad_error_log",
        "name": "POV AllDebrid Error Log",
        "description": "Logs AllDebrid HTTP 200 soft-errors before POV swallows them.",
        "category": "diagnostics",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/alldebrid_api.py",
        "marker": "# WIZARD_POV_AD_ERROR_LOG_v3",
        "anchor": "\t\tresponse = response.json()",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_debrid_guardian",
            indent="\t\t",
            custom_logic="pov_debrid_guardian.log_debrid_error('alldebrid', response)"
        )
    },
    {
        "id": "pov_tb_error_log",
        "name": "POV TorBox Error Log",
        "description": "Logs TorBox HTTP 200 soft-errors before POV swallows them.",
        "category": "diagnostics",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/torbox_api.py",
        "marker": "# WIZARD_POV_TB_ERROR_LOG_v3",
        "anchor": "\t\tresponse = response.json()",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_debrid_guardian",
            indent="\t\t",
            custom_logic="pov_debrid_guardian.log_debrid_error('torbox', response)"
        )
    },
    {
        "id": "pov_pm_error_log",
        "name": "POV Premiumize Error Log",
        "description": "Logs Premiumize HTTP 200 soft-errors before POV swallows them.",
        "category": "diagnostics",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/premiumize_api.py",
        "marker": "# WIZARD_POV_PM_ERROR_LOG_v3",
        "anchor": "\t\t\treturn response.json()",
        "action": "prepend_before",
        # The helper's try block returns the parsed body itself, so the response is parsed once; when
        # the helper fails (logged) execution falls through to the native `return response.json()`.
        "hook": _build_patch_hook(
            module_name="pov_debrid_guardian",
            indent="\t\t\t",
            custom_logic=(
                "_povil_body = response.json()\n"
                "pov_debrid_guardian.log_debrid_error('premiumize', _povil_body, response.url)\n"
                "return _povil_body"
            )
        )
    },
    # =================================================================================================
    # ================= SOURCES: DEBRID TIMEOUT, RANKING, PREWARM & REMEMBERED SOURCE =================
    # =================================================================================================
    {
        "id": "pov_debrid_timeout",
        "name": "Debrid Timeout Resiliency",
        "description": "Prevents source erasure when a debrid provider times out by injecting unconfirmed tuples.",
        "category": "reliability",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/sources.py",
        "marker": "# WIZARD_POV_DEBRID_TIMEOUT_v3",
        "anchor": "self.monitor(threads, debrid_format, True)",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_debrid_timeout",
            indent="\t\t\t",
            custom_logic="pov_debrid_timeout.run(self, threads, torrent_sources)"
        )
    },
    {
        "id": "pov_provider_rank",
        "name": "Unknown Provider Crash Guard",
        "description": "Intercepts sort ranking to prevent KeyErrors on unregistered 3rd party providers.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/sources.py",
        "marker": "# WIZARD_POV_PROVIDER_RANK_v2",
        "anchor": "return self.source.provider_sort_ranks[account_type] or 11",
        "action": "prepend_before",
        "hook": (
            "\t\treturn self.source.provider_sort_ranks.get(account_type) or 11\n"
        )
    },
    {
        "id": "pov_heb_prewarm",
        "name": "Hebrew Subtitles Prewarm",
        "description": "Triggers background subtitle cache pre-warming concurrently with video scraping.",
        "category": "subtitles",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/sources.py",
        "marker": "# WIZARD_POV_HEB_PREWARM_v2",
        "anchor": "results = self.get_sources()",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_prewarm",
            indent="\t\t",
            custom_logic="pov_prewarm.run(self.meta)"
        )
    },
    {
        "id": "pov_playback_capture",
        "name": "Source Name Stash & Playback Capture",
        "description": "Stashes played source names into Window properties and captures source for re-selection.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/sources.py",
        "marker": "# WIZARD_POV_PLAYBACK_CAPTURE_v3",
        "anchor": "return POVPlayer().run(link, item.get('name'), self.meta, progress_media)",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_source_remember",
            indent="\t\t\t",
            custom_logic="pov_source_remember.run_capture(self, item, link)"
        )
    },
    {
        "id": "pov_reorder_sources",
        "name": "Remember Source Auto-Pick Reorder",
        "description": "Moves previously picked sources to the top of the UI list with a visual marker.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/sources.py",
        "marker": "# WIZARD_POV_REORDER_SOURCES_v3",
        "anchor": "window_style = results_xml_style()",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_source_remember",
            indent="\t\t",
            custom_logic="pov_source_remember.run_reorder(self, results)"
        )
    },
    # =============================================================================
    # ================= DEBRID RESOLVE: DIAGNOSTICS & ERROR GUARD =================
    # =============================================================================
    {
        "id": "pov_resolve_diag",
        "name": "Debrid Resolve Diagnostics",
        "description": "Expands opaque selected_files exceptions to include file counts and filter rejection details.",
        "category": "diagnostics",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/debrid.py",
        "marker": "# WIZARD_POV_RESOLVE_DIAG_v2",
        "anchor": "if not selected_files: raise Exception('selected_files failed')",
        "action": "prepend_before",
        # Kept as a raw hook on purpose: pov_resolve_diag.run() most likely raises the
        # expanded exception itself, and _build_patch_hook() would swallow it (log only).
        # Convert once the module has been checked.
        "hook": (
            "import sys, xbmcvfs;\n"
            "p = xbmcvfs.translatePath('special://home/addons/plugin.program.kodipovilwizard/resources/libs/patches/');\n"
            "sys.path.append(p) if p not in sys.path else None;\n"
            "import pov_resolve_diag;\n"
            "pov_resolve_diag.run(self, files, selected_files)\n"
        )
    },
    {
        "id": "pov_debrid_error_guard",
        "name": "Debrid Timeout Error Guard",
        "description": "Safely wraps direct provider cache checks, returning a tuple on timeout to preserve checked states.",
        "category": "reliability",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/debrid.py",
        "marker": "# WIZARD_POV_DEBRID_ERROR_GUARD_v2",
        "anchor": "if self.debrid in ('rd', 'realdebrid'):",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_debrid_error_guard",
            custom_logic=(
                "_wiz_res = pov_debrid_error_guard.run(self, unchecked_hashes)\n"
                "if _wiz_res is not None:\n"
                "\treturn _wiz_res"
            )
        )
    },
    # ======================================================================
    # ================= TRAKT RE-AUTH & SHARED HTTP SESSION ================
    # ======================================================================
    {
        "id": "pov_trakt_reauth_recovery",
        "name": "Trakt 401 Reauth Recovery",
        "description": "Runs Trakt 401 re-authentication recovery before the activity sync.",
        "category": "account_manager",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/trakt_api.py",
        "marker": "# WIZARD_POV_TRAKT_REAUTH_v4",
        "anchor": "def trakt_sync_activities(force_update=False, init_callback=None, monitor=None):",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_trakt_reauth",
            custom_logic="pov_trakt_reauth.run(__import__('sys').modules[__name__])"
        )
    },
    {
        "id": "pov_shared_http",
        "name": "Deferred shared HTTP session (all POV indexers)",
        "description": "Installs the verified, lazily loaded shared HTTP session (pov_http_session) once, at the top of kodi_utils.py, before any indexer imports modules.session: TMDb, Trakt, MDBList and every debrid client share it and `requests` is imported only when a request is sent.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/kodi_utils.py",
        "marker": "# WIZARD_POV_SHARED_HTTP_v1",
        "anchor": "import xbmc, xbmcgui, xbmcplugin, xbmcvfs",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_http_session",
            custom_logic="pov_http_session.install(__import__('xbmcvfs').translatePath('special://home/addons/plugin.video.pov/resources/lib/modules/session.py'), name='modules.session')"
        )
    },
    # =================================================================================
    # ================= SOURCE WINDOW: QUALITY LABEL & SUBTITLE MATCH =================
    # =================================================================================
    {
        "id": "pov_source_quality_fix",
        "name": "POV Source Quality Badge Fix",
        "description": "Corrects SD badges for sources that are actually HD/4K based on release names and re-sorts them.",
        "category": "ui",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/windows/sources.py",
        "marker": "# WIZARD_POV_SOURCE_QUALITY_v2",
        "anchor": "for count, item in enumerate(self.results, 1):",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_source_quality_v2",
            custom_logic="pov_source_quality_v2.run(locals())"
        )
    },
    {
        "id": "pov_subtitle_match_percent",
        "name": "POV Subtitle Match Percentage",
        "description": "Prepends Hebrew subtitle match percentage to the source size label.",
        "category": "subtitles",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/windows/sources.py",
        "marker": "# WIZARD_POV_SUB_MATCH_v2",
        "anchor": "set_property('tikiskins.size_label', get('size_label', 'N/A'))",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_sub_match_v2",
            custom_logic="pov_sub_match_v2.run(locals())"
        )
    },
    {
        "id": "pov_subtitle_match_refresh",
        "name": "POV Late Subtitle Badges",
        "description": "Spawns a background worker on UI init to poll for unready/late subtitle metadata.",
        "category": "subtitles",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/windows/sources.py",
        "marker": "# WIZARD_POV_SUB_MATCH_REFRESH_v1",
        "anchor": "self.win.addItems(self.item_list)",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_sub_match_v2",
            custom_logic="pov_sub_match_v2.refresh_when_ready(self)"
        )
    },
    # ================================================================
    # ================= NAVIGATOR CACHE & AIOSTREAMS =================
    # ================================================================
    {
        "id": "pov_animated_networks",
        "name": "Animated Networks Icons Toggle",
        "description": "Dynamically swaps static network shortcut icons to animated ones based on Fentastic's no_slide_animations setting.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/navigator_cache.py",
        "marker": "# WIZARD_POV_ANIMATED_NETWORKS_v1",
        "anchor": "navigator_cache = NavigatorCache()",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_animated_networks",
            custom_logic="pov_animated_networks.run(navigator_cache)"
        )
    },
    {
        "id": "pov_aiostreams_credentials_fix",
        "name": "POV AIOStreams Credentials Guard",
        "description": "Prevents AIOStreams from swallowing all scrapes when enabled but missing user credentials.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/settings.py",
        "marker": "# WIZARD_POV_AIOSTREAMS_FIX_v5",
        "anchor": "\tsettings.extend(item[1] for item in (",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_aiostreams_fix",
            indent="\t",
            custom_logic="settings = pov_aiostreams_fix.enforce_credentials(settings)"
        )
    },
    # ===========================================
    # ================= MDBLIST =================
    # ===========================================
    {
        "id": "mdblist_api_redact_and_reauth_prep",
        "name": "MDBList API Redact Log",
        "description": "Redacts the API key from the logged MDBList error and keeps the original exception for the 401 retry.",
        "category": "account_manager",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_REAUTH_PREP_v4",
        "anchor": "\t\tlogger('mdblist error', str(e))",
        "action": "prepend_before",
        # `e` becomes the redacted TEXT (logger() only str()s it); the original exception stays in
        # _povil_orig_e for mdblist_api_reauth_retry, which is appended after the logger line.
        "hook": (
            "\t\t_povil_orig_e = e\n"
            "\t\te = __import__('re').sub(r'apikey=[^&\\s]+', 'apikey=***', str(e))\n"
        )
    },
    {
        "id": "mdblist_api_reauth_retry",
        "name": "MDBList API 401 Reauth Retry",
        "description": "Transparently recovers from expired MDBList tokens on 401.",
        "category": "account_manager",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_REAUTH_RETRY_v4",
        "anchor": "\t\tlogger('mdblist error', str(e))",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t\t",
            custom_logic=(
                "_povil_ai_retry = pov_mdblist_patch_logic.handle_401_reauth(_povil_orig_e, path, params, json, method, locals().get('response'))\n"
                "if _povil_ai_retry is not None:\n"
                "\treturn _povil_ai_retry"
            )
        )
    },
    {
        "id": "mdblist_expiry_auth_mode_guard",
        "name": "MDBList expiry auth mode guard",
        "description": "Repairs MDBList API-key auth mode before an OAuth token refresh is triggered on expiry.",
        "category": "account_manager",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_EXPIRY_AUTH_v2",
        "anchor": "\tif interval + current >= expires: mdbl_refresh()",
        "action": "prepend_before",
        "hook": (
            "\tif interval + current >= expires:\n"
            + _build_patch_hook(
                module_name="pov_mdblist_patch_logic",
                indent="\t\t",
                custom_logic=(
                    "if pov_mdblist_patch_logic.repair_api_key_auth():\n"
                    "\treturn"
                )
            )
        )
    },
    {
        "id": "mdblist_cache_verified_reads",
        "name": "MDBList cache verified reads",
        "description": "Routes MDBList cache reads through verified reads (pov_mdblist_patch_logic.cache_list).",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/mdbl_cache.py",
        "marker": "# WIZARD_POV_MDBL_CACHE_READS_v2",
        "anchor": "\twith MDBLCache() as mc:",
        "action": "prepend_before",
        # cache_list() raises MDBListUnavailable for a failed read, and that exception must reach POV's callers
        # (they show the notice instead of an empty list). A helper-wrapped call would log it and fall through
        # to the native code, which re-fetches and caches the failed result. So only the IMPORT is guarded
        # (the helper); the call itself is made outside the try block.
        "hook": (
            _build_patch_hook(module_name="pov_mdblist_patch_logic", indent="\t")
            + "\tif 'pov_mdblist_patch_logic' in locals():\n"
            "\t\treturn pov_mdblist_patch_logic.cache_list(function, string, url)\n"
        )
    },
    {
        "id": "mdblist_response_pagination",
        "name": "MDBList bucketed response pagination",
        "description": "Paginates MDBList list responses in buckets.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_RESPONSE_PAGES_v2",
        "anchor": "\t\tif isinstance(result, list):",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t\t",
            custom_logic="result = pov_mdblist_patch_logic.list_response_pagination(result, response)"
        )
    },
    {
        "id": "mdblist_collection_parent_projection",
        "name": "MDBList collection parent shows",
        "description": "Projects MDBList collection items to their parent shows.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_COLLECTION_PARENTS_v2",
        "anchor": "\tresults = results['movies' if mediatype in ('movie', 'movies') else 'shows']",
        "action": "prepend_before",
        "hook": (
            "\tif list_type == 'collection':\n"
            + _build_patch_hook(
                module_name="pov_mdblist_patch_logic",
                indent="\t\t",
                custom_logic="return pov_mdblist_patch_logic.collection_items(results, mediatype)"
            )
        )
    },
    {
        "id": "mdblist_complete_pagination",
        "name": "MDBList complete pagination",
        "description": "Fetches complete MDBList lists across pages up to MAX_LIST_ITEMS.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_PAGES_v2",
        "anchor": "\tparams = {'limit': 1000}",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t",
            custom_logic="return pov_mdblist_patch_logic.paginated_list(url, MAX_LIST_ITEMS)"
        )
    },
    {
        "id": "mdblist_manager_read_failure",
        "name": "MDBList manager read failure",
        "description": "Catches MDBList-unavailable errors in the list manager and shows a notification instead of crashing.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/mdblist.py",
        "marker": "# WIZARD_POV_MDBL_MANAGER_READ_v1",
        "anchor": "class MdbListManager(list_helper.BaseListManager):",
        "action": "append_after",
        # Kept as a raw hook on purpose: the except clause needs the patch module's
        # exception class, and a helper-wrapped super().manage() could run twice
        # (once inside the try, again as a fallback) if the native flow raised.
        "hook": (
            "# Guard only MDBList reads; keep native mutations.\n"
            "\tdef manage(self):\n"
            "\t\timport sys, xbmcvfs; p = xbmcvfs.translatePath('special://home/addons/plugin.program.kodipovilwizard/resources/libs/patches/'); sys.path.append(p) if p not in sys.path else None; import pov_mdblist_patch_logic\n"
            "\t\ttry: return super().manage()\n"
            "\t\texcept pov_mdblist_patch_logic.MDBListUnavailable:\n"
            "\t\t\treturn pov_mdblist_patch_logic.notify_unavailable()\n"
            "\n"
        )
    },
    {
        "id": "mdblist_api_scrobble_stop",
        "name": "MDBList Watch Scrobble Fix",
        "description": "Clears MDBList paused state when marking as watched.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_SCROBBLE_STOP_v3",
        "anchor": "\tsuccess = result[result_key][success_key] > 0",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t",
            custom_logic="pov_mdblist_patch_logic.scrobble_stop_if_watched(action, key, media, media_id, season, episode)"
        )
    },
    {
        "id": "mdblist_api_add_to_list_guard",
        "name": "MDBList Add to List Guard",
        "description": "Prevents crash on 404 when adding to list.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_ADD_LIST_GUARD_v3",
        "anchor": "\tif result['added']['movies'] + result['added']['shows'] == 0: return kodi_utils.notify_failed()",
        "action": "prepend_before",
        "hook": (
            "\tif not result:\n"
            "\t\treturn kodi_utils.notify_failed()\n"
        )
    },
    {
        "id": "mdblist_api_add_to_coll_guard",
        "name": "MDBList Add to Collection Guard",
        "description": "Prevents crash on 404 when adding to collection.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_ADD_COLL_GUARD_v4",
        "anchor": "\tif result['updated']['movies'] + result['updated']['shows'] == 0: return kodi_utils.notify_failed()",
        "action": "prepend_before",
        "hook": (
            "\tif not result:\n"
            "\t\treturn kodi_utils.notify_failed()\n"
        )
    },
    {
        "id": "mdblist_api_sync_guard",
        "name": "MDBList Sync Tuple Crash Fix",
        "description": "Prevents reset_activity tuple indices string crash.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_SYNC_GUARD_v4",
        "anchor": "\tcached = mdbl_cache.reset_activity(latest)",
        "action": "append_after",
        # POV 6.11 validates `latest` itself; reset_activity() can still return None (its DB read failed).
        "hook": (
            "\tif not isinstance(cached, dict):\n"
            "\t\ttry:\n"
            "\t\t\tmdbl_cache.clear_all_mdbl_cache_data(refresh=False)\n"
            "\t\texcept Exception:\n"
            "\t\t\tpass\n"
            "\t\treturn 'failed'\n"
        )
    },
    {
        "id": "mdblist_api_like_routes",
        "name": "MDBList Like API Routes",
        "description": "Injects missing MDBList like/unlike context menu logic routes.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_LIKE_ROUTES_v3",
        "anchor": "def delete_mdbl_list(params):",
        "action": "prepend_before",
        "hook": (
            "# Preserve the native function body below.\n"
            "def mdbl_like_a_list(params):\n"
            + _build_patch_hook(
                module_name="pov_mdblist_patch_logic",
                indent="\t",
                custom_logic="pov_mdblist_patch_logic.like_a_list(params)"
            )
            + "\n"
            + "def mdbl_unlike_a_list(params):\n"
            + _build_patch_hook(
                module_name="pov_mdblist_patch_logic",
                indent="\t",
                custom_logic="pov_mdblist_patch_logic.unlike_a_list(params)"
            )
            + "\n"
        )
    },
    {
        "id": "mdblist_api_account_heal",
        "name": "MDBList Account State Heal",
        "description": "Repairs broken MDBList OAuth states by recovering missing usernames.",
        "category": "account_manager",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_ACCOUNT_HEAL_v3",
        "anchor": "\tif not get_setting('mdblist_user', ''): return 'no account'",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t",
            custom_logic="pov_mdblist_patch_logic.heal_mdblist_account_if_needed()"
        )
    },
    {
        "id": "mdblist_menu_manager_watchlist",
        "name": "MDBList Manager Watchlist Only",
        "description": "Hides collection to prevent crashes and stabilize Hebrew IDs.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/mdblist.py",
        "marker": "# WIZARD_POV_MDBL_WATCHLIST_ONLY_v2",
        "anchor": "\t\tchoices = [(i.lower(), i, '', self.icon) for i in (watchl_str, coll_str)]",
        "action": "append_after",
        "hook": (
            "\t\tchoices = [('watchlist', watchl_str, '', self.icon)]\n"
        )
    },
    {
        "id": "mdblist_menu_pre_search",
        "name": "MDBList Search History & Flow",
        "description": "Adds intermediate search screens and fixes blank cancellation flow.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/mdblist.py",
        "marker": "# WIZARD_POV_MDBL_PRE_SEARCH_v2",
        "anchor": "\treturn SearchMdblLists(params).build()",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t",
            custom_logic=(
                "_ai_params = pov_mdblist_patch_logic.pre_search_check(params)\n"
                "if _ai_params is None:\n"
                "\treturn\n"
                "params.update(_ai_params)"
            )
        )
    },
    {
        "id": "mdblist_menu_like_append",
        "name": "MDBList Like Menu Option",
        "description": "Appends Like/Unlike to MDBList context menus natively.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/mdblist.py",
        "marker": "# WIZARD_POV_MDBL_MENU_LIKE_v2",
        "anchor": "\t\t\t\tcm_append((add2menu_str, 'RunPlugin(%s)' % build_url({'mode': 'menu_editor.add_external', 'name': name, 'iconImage': 'mdblist.png'})))",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_mdblist_patch_logic",
            indent="\t\t\t\t",
            custom_logic="pov_mdblist_patch_logic.append_like_menu(cm_append, list_type, list_id)"
        )
    },
    {
        "id": "pov_mdblist_sort_default",
        "name": "MDBList Watchlist/Collection Sort Default",
        "description": "Intercepts list sorting to default to 'Date Added' (recency) if unconfigured.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/settings.py",
        "marker": "# WIZARD_POV_MDBL_SORT_DEFAULT_v2",
        "anchor": "def metadata_user_info():",
        "action": "prepend_before",
        "hook": (
            "_povil_orig_lists_sort_order = lists_sort_order\n"
            "\n"
            "def lists_sort_order(setting, mediatype=None):\n"
            + _build_patch_hook(
                module_name="pov_mdblist_patch_logic",
                indent="\t",
                custom_logic="return pov_mdblist_patch_logic.lists_sort_order_override(_povil_orig_lists_sort_order, setting, mediatype)"
            )
            + "\treturn _povil_orig_lists_sort_order(setting, mediatype)\n"
            + "\n"
        )
    },
    # =================================================
    # ================= WIDGET BUDGET =================
    # =================================================
    {
        "id": "pov_widget_budget_movies",
        "name": "POV Widget Rendering Budget (Movies)",
        "description": "Limits the scope of background metadata tasks for movies when rendering on the home screen to optimize speeds.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/movies.py",
        "marker": "# WIZARD_POV_WIDGET_BUDGET_MOVIES_v3",
        "anchor": "\t\t\tkodi_utils.add_items(__handle__, worker())",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_widget_budget",
            indent="\t\t\t",
            custom_logic="worker = pov_widget_budget.wrap_worker(self, worker)"
        )
    },
    {
        "id": "pov_widget_budget_tvshows",
        "name": "POV Widget Rendering Budget (TV Shows)",
        "description": "Limits the scope of background metadata tasks for TV Shows when rendering on the home screen to optimize speeds.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/tvshows.py",
        "marker": "# WIZARD_POV_WIDGET_BUDGET_TVSHOWS_v3",
        "anchor": "\t\t\tkodi_utils.add_items(__handle__, self.worker())",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_widget_budget",
            indent="\t\t\t",
            custom_logic="self.worker = pov_widget_budget.wrap_worker(self, self.worker)"
        )
    },
    {
        "id": "pov_next_episode_widget_budget",
        "name": "POV Next Episode Widget Budget",
        "description": "Wraps the next-episode widget worker with the widget rendering budget.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/episodes.py",
        "marker": "# WIZARD_POV_NEXT_EPISODE_BUDGET_v1",
        "anchor": "if self.list: kodi_utils.add_items(__handle__, self.worker())",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_widget_budget",
            indent="\t\t",
            custom_logic="self.worker = pov_widget_budget.wrap_next_episode_worker(self, self.worker)"
        )
    },
    # ========================================================================
    # ================= WATCHED CACHE & NEXT-EPISODE HISTORY =================
    # ========================================================================
    {
        "id": "pov_bookmark_refresh_order",
        "name": "Fix Bookmark UI Refresh Race Condition",
        "description": "Refreshes the UI only after provider progress has been written, fixing the bookmark refresh race.",
        "category": "ux",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/watched_cache.py",
        "marker": "# WIZARD_POV_BOOKMARK_REFRESH_LAST_v3",
        "anchor": "\t\tdbcur.execute(SET_BM, (mediatype, tmdb_id, season, episode, str(resume_point), str(curr_time), last_played, 0, title))",
        "action": "append_after",
        "hook": (
            "\t\ttry:\n"
            "\t\t\tif watched_indicators in (1, 2):\n"
            "\t\t\t\t_povil_func = mdbl_progress if watched_indicators == 2 else trakt_progress\n"
            "\t\t\t\t_povil_func('set_progress', mediatype, tmdb_id, resume_point, season, episode, refresh=True)\n"
            "\t\t\tif refresh == 'true': kodi_utils.widget_refresh() if kodi_utils.external_browse() else kodi_utils.container_refresh()\n"
            "\t\t\treturn\n"
            "\t\texcept Exception:\n"
            "\t\t\tpass\n"
        )
    },
    {
        "id": "pov_watched_lazy_mock",
        "name": "Lazy Import Watch Cache (Mock Setup)",
        "description": "DISABLED pending a 32-bit device measurement. Mocks heavy provider modules in the watched cache so they can be imported lazily.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": False,
        "target_file": "resources/lib/caches/watched_cache.py",
        "marker": "# WIZARD_POV_WATCHED_LAZY_MOCK_v3",
        "anchor": "from caches.mdbl_cache import clear_mdbl_collection_watchlist_data",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_watched_lazy_imports",
            custom_logic="pov_watched_lazy_imports.mock_modules()"
        )
    },
    {
        "id": "pov_watched_lazy_proxy",
        "name": "Lazy Import Watch Cache (Proxy Shadows)",
        "description": "DISABLED pending a 32-bit device measurement. Applies lazy proxies for provider functions in the watched cache globals.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": False,
        "target_file": "resources/lib/caches/watched_cache.py",
        "marker": "# WIZARD_POV_WATCHED_LAZY_PROXY_v3",
        "anchor": "from indexers.trakt_api import trakt_get_hidden_items, trakt_official_status, trakt_progress, trakt_watched_unwatched",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_watched_lazy_imports",
            custom_logic="pov_watched_lazy_imports.apply_proxies(globals())"
        )
    },
    {
        "id": "pov_next_episode_constructor",
        "name": "Scope the unused next-episode constructor history read",
        "description": "Scopes the next-episode constructor's watched-history read through pov_watch_history.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/episodes.py",
        "marker": "# WIZARD_POV_NEXT_CONSTRUCTOR_v1",
        "anchor": "class Menu(Episodes):",
        "action": "append_after",
        # Kept as a raw hook on purpose: this replaces __init__, and a swallowed failure
        # would leave the menu object half-initialised instead of failing loudly.
        "hook": (
            "# Keep the inherited constructor unchanged outside this scoped read.\n"
            "\tdef __init__(self, params):\n"
            "\t\timport sys, xbmcvfs\n"
            "\t\tp = xbmcvfs.translatePath('special://home/addons/plugin.program.kodipovilwizard/resources/libs/patches/')\n"
            "\t\tif p not in sys.path: sys.path.append(p)\n"
            "\t\timport pov_watch_history\n"
            "\t\tpov_watch_history.next_constructor(self, super().__init__, params, ws.get_watched_info_tv)\n"
            "\n"
        )
    },
    {
        "id": "pov_indexed_next_history",
        "name": "Seek the last watched episode through the existing native index",
        "description": "Finds the last watched episode through the native index (pov_next_history) instead of a full scan.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/watched_cache.py",
        "marker": "# WIZARD_POV_INDEXED_NEXT_HISTORY_v3",
        "anchor": "def get_next_episodes(watched_indicators):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            + _build_patch_hook(
                module_name="pov_next_history",
                indent="\t",
                custom_logic="return pov_next_history.next_episodes(globals(), watched_indicators)"
            )
        )
    },
    # =======================================================
    # ================= STABLE LIST ACTIONS =================
    # =======================================================
    {
        "id": "pov_tmdb_stable_list_actions",
        "name": "Stable provider action IDs: tmdb",
        "description": "Uses stable, non-localized action IDs for TMDB list actions.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/tmdb.py",
        "marker": "# WIZARD_POV_TMDB_LIST_ACTIONS_v2",
        "anchor": "\t\treturn [(i.lower(), i, '', self.icon) for i in (watchl_str, fav_str)]",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_list_actions",
            indent="\t\t",
            custom_logic="return pov_list_actions.default_choices(self, globals(), 'tmdb')"
        )
    },
    {
        "id": "pov_trakt_stable_list_actions",
        "name": "Stable provider action IDs: trakt",
        "description": "Uses stable, non-localized action IDs for Trakt list actions.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/trakt.py",
        "marker": "# WIZARD_POV_TRAKT_LIST_ACTIONS_v1",
        "anchor": "\t\tchoices = [(i.lower(), i, '', self.icon) for i in (watchl_str, fav_str, coll_str)]",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_list_actions",
            indent="\t\t",
            custom_logic="return pov_list_actions.default_choices(self, globals(), 'trakt')"
        )
    },
    # =====================================================
    # ================= CONTINUE WATCHING =================
    # =====================================================
    {
        "id": "pov_persistent_continue_exclusions",
        "name": "Honor local removals with every watched provider",
        "description": "Honors local continue-watching removals across every watched provider.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/watched_cache.py",
        "marker": "# WIZARD_POV_CONTINUE_EXCLUSIONS_v3",
        "anchor": "def get_dropped_info_tv(watched_indicators):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            + _build_patch_hook(
                module_name="pov_continue_watching",
                indent="\t",
                custom_logic="return pov_continue_watching.dropped_info(globals(), watched_indicators)"
            )
        )
    },
    {
        "id": "pov_continuation_service_prefetch",
        "name": "Prepare exclusion snapshots before opening continuation",
        "description": "Prepares continue-watching exclusion snapshots in the service thread before the continuation list opens.",
        "category": "performance",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/entry.py",
        "marker": "# WIZARD_POV_CONTINUE_PREFETCH_v1",
        "anchor": "\t\tfor i in threads: i.start()",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_continue_watching",
            indent="\t\t",
            custom_logic="threads = (*threads, Thread(target=pov_continue_watching.service_prefetch, args=(self,)))"
        )
    },
    {
        "id": "pov_continuation_extra_sources",
        "name": "Apply exclusions to bookmarks and optional watchlists",
        "description": "Applies continue-watching exclusions to bookmark and optional watchlist episode sources.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/episodes.py",
        "marker": "# WIZARD_POV_CONTINUE_SOURCES_v1",
        "anchor": "\t\tif callable(func): func(params_get)",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="pov_continue_watching",
            indent="\t\t",
            custom_logic="pov_continue_watching.filter_episode_sources(self)"
        )
    },
    {
        "id": "pov_episode_focused_info",
        "name": "Keep episode title and synopsis available on focused previews",
        "description": "Keeps episode title and synopsis available on focused previews.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/episodes.py",
        "marker": "# WIZARD_POV_EPISODE_FOCUSED_INFO_v2",
        "anchor": "\t\t\tself.append((url_params, listitem, False))",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_episode_info",
            indent="\t\t\t",
            custom_logic="pov_episode_info.complete(listitem, meta, item, ep_name)"
        )
    },
    {
        "id": "pov_continuation_episode_actions",
        "name": "Restore season marking and explicit continuation removal",
        "description": "Restores season marking and explicit continuation removal in the episode context menu.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/episodes.py",
        "marker": "# WIZARD_POV_CONTINUE_ACTIONS_v2",
        "anchor": "\t\t\tcm.sort(key=lambda k: k[0])",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_continue_watching",
            indent="\t\t\t",
            custom_logic="pov_continue_watching.append_episode_actions(locals(), build_url, run_plugin)"
        )
    },
    {
        "id": "pov_continuation_show_actions",
        "name": "Keep continuation removal available on show cards",
        "description": "Keeps continuation removal available in show card context menus.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/tvshows.py",
        "marker": "# WIZARD_POV_CONTINUE_SHOW_ACTIONS_v2",
        "anchor": "\t\t\tcm.sort(key=lambda k: k[0])",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_continue_watching",
            indent="\t\t\t",
            custom_logic="pov_continue_watching.append_show_actions(locals(), build_url, run_plugin)"
        )
    },
    {
        "id": "pov_continuation_show_sources",
        "name": "Apply explicit exclusions to in-progress show directories",
        "description": "Applies explicit continuation exclusions to in-progress show directories.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/tvshows.py",
        "marker": "# WIZARD_POV_CONTINUE_SHOW_SOURCES_v2",
        "anchor": "\t\t\tkodi_utils.add_items(__handle__, self.worker())",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="pov_continue_watching",
            indent="\t\t\t",
            custom_logic="pov_continue_watching.filter_show_sources(self)"
        )
    },
    {
        "id": "pov_continuation_season_picker",
        "name": "Choose a season from a show card before native marking",
        "description": "Lets the user choose a season from a show card before native season marking.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/watched_cache.py",
        "marker": "# WIZARD_POV_CONTINUE_SEASON_PICKER_v3",
        "anchor": "def mark_as_watched_unwatched_season(params):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            + "\tif str(params.get('season')) == 'choose':\n"
            + _build_patch_hook(
                module_name="pov_continue_watching",
                indent="\t\t",
                custom_logic="return pov_continue_watching.choose_season(params, globals())"
            )
            # A failed picker must not fall through to native marking with season='choose'.
            + "\t\treturn\n"
        )
    },
    {
        "id": "pov_continuation_local_toggle",
        "name": "Persist local continuation removal and refresh widgets",
        "description": "Persists local continuation removal and refreshes widgets.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/dialogs.py",
        "marker": "# WIZARD_POV_CONTINUE_LOCAL_TOGGLE_v1",
        "anchor": "def dropped_choice(params):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            + _build_patch_hook(
                module_name="pov_continue_watching",
                indent="\t",
                custom_logic="return pov_continue_watching.dropped_choice(params, globals())"
            )
        )
    },
    {
        "id": "pov_debrid_cache_upsert",
        "name": "Update repeated debrid cache hashes without uniqueness errors",
        "description": "Switches debrid cache bulk inserts to INSERT OR REPLACE to avoid uniqueness errors on repeated hashes.",
        "category": "crash_guard",
        "priority": "critical",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/debrid_cache.py",
        "marker": "# WIZARD_POV_DEBRID_CACHE_UPSERT_v1",
        "anchor": "SET_MANY = 'INSERT INTO debrid_data VALUES (?, ?, ?, ?)'",
        "action": "append_after",
        "hook": (
            "SET_MANY = 'INSERT OR REPLACE INTO debrid_data VALUES (?, ?, ?, ?)'\n"
        )
    },
    {
        "id": "pov_trakt_continue_snapshot_invalidation",
        "name": "Invalidate the continuation snapshot with native dropped activity",
        "description": "Invalidates the continuation snapshot when Trakt dropped-list activity changes.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/trakt_cache.py",
        "marker": "# WIZARD_POV_TRAKT_CONTINUE_INVALIDATE_v1",
        "anchor": "def clear_trakt_hidden_data(list_type):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            + _build_patch_hook(
                module_name="pov_continue_watching",
                indent="\t",
                custom_logic=(
                    "if list_type == 'dropped':\n"
                    "\tpov_continue_watching.invalidate('trakt')"
                )
            )
        )
    },
    {
        "id": "pov_trakt_verified_continue_toggle",
        "name": "Verify cloud dropped writes and preserve immediate local exclusions",
        "description": "Verifies Trakt dropped-list writes and preserves immediate local exclusions.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/trakt_api.py",
        "marker": "# WIZARD_POV_TRAKT_CONTINUE_TOGGLE_v4",
        "anchor": "def hide_unhide_trakt_items(action, mediatype, media_id, list_type):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            + _build_patch_hook(
                module_name="pov_continue_watching",
                indent="\t",
                custom_logic=(
                    "if mediatype in ('show', 'shows', 'tvshow') and list_type == 'dropped':\n"
                    "\treturn pov_continue_watching.remote_toggle(globals(), 'trakt', action, mediatype, media_id, list_type)"
                )
            )
        )
    },
    {
        "id": "pov_mdbl_continue_snapshot_invalidation",
        "name": "Invalidate the continuation snapshot with native dropped activity",
        "description": "Invalidates the continuation snapshot when MDBList dropped-list activity changes.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/caches/mdbl_cache.py",
        "marker": "# WIZARD_POV_MDBL_CONTINUE_INVALIDATE_v1",
        "anchor": "def clear_mdbl_hidden_data(list_type):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            + _build_patch_hook(
                module_name="pov_continue_watching",
                indent="\t",
                custom_logic=(
                    "if list_type == 'dropped':\n"
                    "\tpov_continue_watching.invalidate('mdblist')"
                )
            )
        )
    },
    {
        "id": "pov_mdbl_verified_continue_toggle",
        "name": "Verify cloud dropped writes and preserve immediate local exclusions",
        "description": "Verifies MDBList dropped-list writes and preserves immediate local exclusions.",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/indexers/mdblist_api.py",
        "marker": "# WIZARD_POV_MDBL_CONTINUE_TOGGLE_v3",
        "anchor": "def hide_unhide_mdbl_items(action, mediatype, media_id, list_type):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            + _build_patch_hook(
                module_name="pov_continue_watching",
                indent="\t",
                custom_logic=(
                    "if mediatype in ('show', 'shows', 'tvshow') and list_type == 'dropped':\n"
                    "\treturn pov_continue_watching.remote_toggle(globals(), 'mdblist', action, mediatype, media_id, list_type)"
                )
            )
        )
    },
    # ==========================================================================================
    # ================= IL RELEASE COUNTDOWN (FENTASTIC "ModernInfoChip" BADGE) =================
    # ==========================================================================================
    # The skin reads ListItem.Property(IL_release / IL_release_state / IL_release_days). These three
    # entries feed it (il_release.py; il_fetcher.py is started by startup.py). They were lost in the
    # merge 4e117cf7 (2026-11-07) and are restored here with the same ids.
    {
        "id": "wizard_il_release",
        "name": "IL release countdown badge (movies)",
        "description": "Sets the IL_release listitem properties (days until the movie reaches official VOD) read by the FENtastic info bar.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/menus/movies.py",
        "marker": "# WIZARD_IL_RELEASE_v3",
        "anchor": "\t\t\tlistitem.setProperties(props)",
        "action": "append_after",
        # Runs once per movie row: after the first import il_release is found in sys.modules.
        "hook": (
            "try:\n"
            "\t_il = __import__('sys').modules.get('il_release')\n"
            "\tif _il is None:\n"
            "\t\t_povil_dir = __import__('xbmcvfs').translatePath('%s')\n"
            "\t\tif _povil_dir not in __import__('sys').path:\n"
            "\t\t\t__import__('sys').path.append(_povil_dir)\n"
            "\t\timport il_release as _il\n"
            "\t_il.apply_release_property(imdb_id, listitem, meta_get('premiered'), meta_get('year'), meta_get('il_dates'))\n"
            "except Exception:\n"
            "\t__import__('xbmc').log('Wizard Patch Error [il_release]:\\n' + __import__('traceback').format_exc(), __import__('xbmc').LOGERROR)\n"
        ) % PATCHES_DIR
    },
    {
        "id": "wizard_il_dates_extract",
        "name": "IL release dates: read TMDb release_dates",
        "description": "Extracts the per-type release dates POV already downloads (no extra request) for the IL release badge.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/indexers/metadata.py",
        "marker": "# WIZARD_IL_DATES_EXTRACT_v2",
        "anchor": "\t\tpremiered = parser.get_text('release_date')",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="il_release",
            indent="\t\t",
            custom_logic="_il_dates = il_release.extract_dates(data)"
        )
    },
    {
        "id": "wizard_il_dates_store",
        "name": "IL release dates: keep them in the movie meta",
        "description": "Stores the extracted release dates in the cached movie meta (meta['il_dates']).",
        "category": "ui",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/indexers/metadata.py",
        "marker": "# WIZARD_IL_DATES_STORE_v2",
        "anchor": "\t\t\t'mpaa': parser.get_mpaa('release_dates', user_info['mpaa_region']),",
        "action": "append_after",
        # locals().get(): a failed extract (logged by its helper) leaves _il_dates unbound.
        "hook": (
            "'il_dates': locals().get('_il_dates'),\n"
        )
    },
    # ======================================================================================================
    # ================= IDAN PLUS: CHANNELS, YOUTUBE ID & KAN / i24NEWS VOD (formerly runtime patchers) ====
    # ======================================================================================================
    {
        "id": "idanplus_channels_items_guard",
        "name": "Idan Plus: items() tolerates a non-dict",
        "description": "A corrupt displayChannels.json is read as a list; items() then crashes. Hands a non-dict back unchanged.",
        "category": "crash_guard",
        "priority": "major",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/common.py",
        "marker": "# WIZARD_IDANPLUS_ITEMS_GUARD_v1",
        "anchor": "def items(d):",
        "action": "append_after",
        "hook": (
            "# Preserve the native function body below.\n"
            "\tif not isinstance(d, dict):\n"
            "\t\treturn d\n"
        )
    },
    {
        "id": "idanplus_display_channels_guard",
        "name": "Idan Plus: corrupt displayChannels.json is dropped and rebuilt",
        "description": "ReadList() returns [] on any read error, which crashes every channel read. A non-dict result removes the file and returns {} so Idan Plus rebuilds it from the remote list.",
        "category": "crash_guard",
        "priority": "major",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/common.py",
        "marker": "# WIZARD_IDANPLUS_DISPLAY_CHANNELS_v1",
        "anchor": "\treturn ReadList(displayChannelsFile)",
        "action": "prepend_before",
        "hook": (
            "\t_povil_channels = ReadList(displayChannelsFile)\n"
            "\tif isinstance(_povil_channels, dict): return _povil_channels\n"
            "\ttry: os.remove(displayChannelsFile)\n"
            "\texcept Exception: pass\n"
            "\treturn {}\n"
        )
    },
    {
        "id": "idanplus_youtube_id",
        "name": "Idan Plus: Kan YouTube links resolve",
        "description": "GetYouTube takes the id from the URL path ('watch'); when that is not a YouTube id, read it from the v= parameter.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/common.py",
        "marker": "# WIZARD_IDANPLUS_YOUTUBE_ID_v1",
        "anchor": "\treturn '{0}/play/?video_id={1}'.format(youtubePlugin, video_id)",
        "action": "prepend_before",
        "hook": (
            "\tif not re.match(r'^[0-9A-Za-z_-]{11}$', video_id or ''): video_id = (re.findall(r'[?&]v=([0-9A-Za-z_-]{11})', url) or [video_id])[0]\n"
        )
    },
    {
        "id": "idanplus_vod_kan_mirror",
        "name": "Idan Plus: Kan pages through the mobile-API host",
        "description": "Cloudflare answers 403 to Kodi on www.kan.org.il; the same site answers on mobapi.kan.org.il. Also percent-encodes the Referer (a Hebrew 'season' crashes `requests`). TO CHECK ON A DEVICE: Idan Plus 4.0.3 says 'Fix Kan11-VODs'.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/common.py",
        "marker": "# WIZARD_IDANPLUS_VOD_MIRROR_v1",
        "anchor": "\tbrowseReferer = referer or home",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="idanplus_vod",
            indent="\t",
            custom_logic=(
                "browseReferer = idanplus_vod.kan_ascii(browseReferer)\n"
                "_povil_page = idanplus_vod.kan_mirror(url, ua, responseMethod)\n"
                "if _povil_page is not None:\n"
                "\treturn _povil_page"
            )
        )
    },
    {
        "id": "idanplus_vod_kan_titles",
        "name": "Idan Plus: Kan episodes without a title use the analytics title",
        "description": "Kan's mobile feed leaves some episode titles empty; fall back to extensions.analyticsCustomProperties.episode_title.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/kan.py",
        "marker": "# WIZARD_IDANPLUS_VOD_TITLES_v1",
        "anchor": "    season_filter = int(season_filter) if season_filter else None",
        "action": "append_after",
        "hook": (
            "entries = [dict(e, title=e.get('title') or ((e.get('extensions') or {}).get('analyticsCustomProperties') or {}).get('episode_title') or '') for e in entries]\n"
        )
    },
    {
        "id": "idanplus_vod_kan_episodes",
        "name": "Idan Plus: Kan's new episode cards",
        "description": "Kan moved its player: episode pages list their episodes as 'cr-item' cards; parse those.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/kan.py",
        "marker": "# WIZARD_IDANPLUS_VOD_EPISODES_v1",
        "anchor": "    body = re.compile('<main id=\"main\"(.*?)</main>', re.S).findall(text)",
        "action": "prepend_before",
        "hook": _spaces(_build_patch_hook(
            module_name="idanplus_vod",
            indent="\t",
            custom_logic=(
                "if idanplus_vod.kan_cr_items(globals(), locals()):\n"
                "\treturn"
            )
        ))
    },
    {
        "id": "idanplus_vod_kan_archive",
        "name": "Idan Plus: Kan archive lobby program tiles",
        "description": "The archive lobby no longer has 'vod-section' blocks; list its program tiles.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/kan.py",
        "marker": "# WIZARD_IDANPLUS_VOD_ARCHIVE_v1",
        # Two lines (the blank line before it too): the series line alone also occurs in an earlier function.
        "anchor": "    \n    series = re.compile('<div class=\"vod-section(.*?)<div class=\"section-content\">', re.S).findall(matches[0])",
        "action": "prepend_before",
        "hook": _spaces(_build_patch_hook(
            module_name="idanplus_vod",
            indent="\t",
            custom_logic=(
                "if idanplus_vod.kan_program_tiles(globals(), locals()):\n"
                "\treturn"
            )
        ))
    },
    {
        "id": "idanplus_vod_i24_shows",
        "name": "Idan Plus: i24NEWS English shows",
        "description": "i24NEWS English came up empty; list the Univtec catalog.",
        "category": "reliability",
        "priority": "minor",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/i24news.py",
        "marker": "# WIZARD_IDANPLUS_VOD_I24_SHOWS_v1",
        "anchor": "    \"\"\"Display Hebrew TV shows from the current i24NEWS website API.\"\"\"",
        "action": "append_after",
        "hook": _spaces(_build_patch_hook(
            module_name="idanplus_vod",
            indent="\t",
            custom_logic=(
                "if language == 'en':\n"
                "\treturn idanplus_vod.i24_english_shows(globals())"
            )
        ))
    },
    {
        "id": "idanplus_vod_i24_catalog",
        "name": "Idan Plus: i24NEWS English catalog",
        "description": "Shows the Univtec catalog for English as it already does for Hebrew.",
        "category": "reliability",
        "priority": "minor",
        "addon_id": "plugin.video.idanplus",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/i24news.py",
        "marker": "# WIZARD_IDANPLUS_VOD_I24_CATALOG_v1",
        "anchor": "    if language == 'he' and ShowCatalogUnivtec(language):",
        "action": "prepend_before",
        "hook": (
            "if language == 'en' and ShowCatalogUnivtec(language): return\n"
        )
    },
    # ============================================================================================
    # ================= UMBRELLA: SOURCE FLOW, HEBREW MATCH BADGE & MDBLIST (formerly runtime) ====
    # ============================================================================================
    {
        "id": "umbrella_prewarm",
        "name": "Umbrella: warm the Hebrew subtitle match at the start of the scrape",
        "description": "Fires he_sub_match's background warm when the scrape starts, so the match % shows on the first visit to a title.",
        "category": "performance",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/modules/sources.py",
        "marker": "# WIZARD_UMBRELLA_PREWARM_v1",
        "anchor": "\t\t\tcontent = 'movie' if tvshowtitle is None else 'episode'",
        "action": "prepend_before",
        "hook": _build_patch_hook(
            module_name="umbrella_he_match",
            indent="\t\t\t",
            custom_logic="umbrella_he_match.prewarm(self.meta)"
        )
    },
    {
        "id": "umbrella_source_name",
        "name": "Umbrella: publish the picked release name for the subtitle matcher",
        "description": "Umbrella publishes nothing, and an opaque CDN url scores every subtitle 0%. Publishes the picked source's name and url on Window(10000).",
        "category": "ux",
        "priority": "major",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/modules/sources.py",
        "marker": "# WIZARD_UMBRELLA_SOURCE_NAME_v1",
        "anchor": "\t\t\t\t\tif not self.url: continue",
        "action": "append_after",
        "hook": _build_patch_hook(
            module_name="umbrella_he_match",
            indent="\t\t\t\t\t",
            custom_logic="umbrella_he_match.publish_source(resolve_items[i], self.url)"
        )
    },
    {
        "id": "umbrella_match_setup",
        "name": "Umbrella: Hebrew subtitle match, once per source window",
        "description": "Loads the available Hebrew release names for the title once per window (he_sub_match).",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/windows/source_results.py",
        "marker": "# WIZARD_UMBRELLA_MATCH_SETUP_v1",
        "anchor": "\t\t\tfor count, item in enumerate(self.results, 1):",
        "action": "prepend_before",
        "hook": (
            "_sm_pfx = lambda _n: ''\n"
            + _build_patch_hook(
                module_name="umbrella_he_match",
                custom_logic="_sm_pfx = umbrella_he_match.prefixer(self.meta)"
            )
        )
    },
    {
        "id": "umbrella_match_row",
        "name": "Umbrella: Hebrew subtitle match badge on every source row",
        "description": "Prefixes the coloured badge to the size_label property, which is the first token of the info line in every layout.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/windows/source_results.py",
        "marker": "# WIZARD_UMBRELLA_MATCH_ROW_v1",
        "anchor": "listitem.setProperty('umbrella.size_label', size_label)",
        "action": "append_after",
        "hook": (
            "if '_sm_pfx' in locals():\n"
            "\tlistitem.setProperty('umbrella.size_label', _sm_pfx(item.get('name') or '') + size_label)\n"
        )
    },
    {
        "id": "umbrella_mdbl_sync_activities",
        "name": "Umbrella: MDBList watched-sync needs usable activities",
        "description": "Stops the watched sync when the activities read is empty or malformed, so the cursor is not advanced on a failed read.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/modules/mdblist.py",
        "marker": "# WIZARD_UMBRELLA_MDBL_ACTIVITIES_v1",
        "anchor": "\t\t# A new cursor key forces one complete snapshot on upgrade. Legacy builds",
        "action": "prepend_before",
        "hook": (
            "activities = _activities_dict(activities)\n"
            "if not activities or not (getServerTime(activities) or getWatchedActivity(activities)): return\n"
        )
    },
    {
        "id": "umbrella_mdbl_sync_page",
        "name": "Umbrella: MDBList watched-sync needs a real page",
        "description": "A failed or malformed page (no 'pagination') must not advance the sync cursor: the next run asks for the same window again.",
        "category": "reliability",
        "priority": "major",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/modules/mdblist.py",
        "marker": "# WIZARD_UMBRELLA_MDBL_PAGE_v1",
        "anchor": "\t\t\t# Do not advance the cursor when the request failed; the next service",
        "action": "prepend_before",
        "hook": (
            "if not isinstance(data, dict) or 'pagination' not in data: return\n"
        )
    },
    {
        "id": "umbrella_mdbl_token_adopt",
        "name": "Umbrella: adopt the MDBList token POV refreshed",
        "description": "Umbrella reads the token once at import. A 401 is usually a newer token in settings (POV refreshes and the Wizard mirrors it): adopt it and retry.",
        "category": "account_manager",
        "priority": "major",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/modules/mdblist.py",
        "marker": "# WIZARD_UMBRELLA_MDBL_TOKEN_v1",
        "anchor": "\t\trefresh_token = getSetting('mdblist.refresh.token')",
        "action": "prepend_before",
        "hook": (
            "# Somebody else may already have refreshed this account: in this build POV owns the\n"
            "# refreshing, with the client id the refresh token was issued to. The header was read at\n"
            "# import, so a newer token in settings is the ordinary reason for a 401.\n"
            "_povil_token = getSetting('mdblist.token')\n"
            "if _povil_token and session.headers.get('Authorization') != 'Bearer %s' % _povil_token:\n"
            "\tsession.headers.update({'Authorization': 'Bearer %s' % _povil_token})\n"
            "\treturn True\n"
        )
    },
    {
        "id": "umbrella_mdbl_token_quiet",
        "name": "Umbrella: no 're-authenticate in settings' dialog for MDBList",
        "description": "MDBList is connected in POV in this build, not in Umbrella, and POV refreshes and retries on its own 401. Logs instead of showing the dialog (the native dialog line becomes unreachable).",
        "category": "account_manager",
        "priority": "minor",
        "addon_id": "plugin.video.umbrella",
        "enabled": True,
        "optional": True,
        "target_file": "resources/lib/modules/mdblist.py",
        "marker": "# WIZARD_UMBRELLA_MDBL_QUIET_v1",
        "anchor": "\t\t\tcontrol.notification(title='MDBList', message='MDBList session expired",
        "action": "prepend_before",
        "hook": (
            "log_utils.log('MDBList 401 and no usable token here; POV owns the refresh', level=log_utils.LOGDEBUG)\n"
            "return None\n"
        )
    },
    # ===================================================================================================
    # ================= ARCTIC FUSE 3: TOUCH CLEANUP & PLOT AUTOSCROLL (formerly af3_home_patcher) =======
    # ===================================================================================================
    {
        "id": "af3_touch_cleanup_videoinfo",
        "name": "AF3: clear the home info state when the video info dialog closes",
        "description": "Touch navigation: closing the dialog must clear the Home properties it set (InfoPanel, SubGroup, TMDbHelper context menu / widget container, CurrentID), or the next tap lands in a stale state.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "skin.arctic.fuse.3",
        "enabled": True,
        "optional": True,
        "target_file": "1080i/DialogVideoInfo.xml",
        "marker": "# WIZARD_AF3_TOUCH_VIDEOINFO_v1",
        "anchor": "<window",
        "action": "append_after",
        "hook": (
            "<onunload>ClearProperty(InfoPanel.FullSwitch,Home)</onunload>\n"
            "<onunload>ClearProperty(SubGroup.IsVisible,Home)</onunload>\n"
            "<onunload>ClearProperty(TMDbHelper.ContextMenu,Home)</onunload>\n"
            "<onunload>ClearProperty(TMDbHelper.WidgetContainer,Home)</onunload>\n"
            "<onunload>ClearProperty(CurrentID)</onunload>\n"
        )
    },
    {
        "id": "af3_touch_cleanup_contextmenu",
        "name": "AF3: clear the home info state when the context menu closes",
        "description": "Touch navigation: closing the dialog must clear the Home properties it set (InfoPanel, SubGroup, TMDbHelper context menu / widget container, CurrentID), or the next tap lands in a stale state.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "skin.arctic.fuse.3",
        "enabled": True,
        "optional": True,
        "target_file": "1080i/DialogContextMenu.xml",
        "marker": "# WIZARD_AF3_TOUCH_CONTEXTMENU_v1",
        "anchor": "<window",
        "action": "append_after",
        "hook": (
            "<onunload>ClearProperty(InfoPanel.FullSwitch,Home)</onunload>\n"
            "<onunload>ClearProperty(SubGroup.IsVisible,Home)</onunload>\n"
            "<onunload>ClearProperty(TMDbHelper.ContextMenu,Home)</onunload>\n"
            "<onunload>ClearProperty(TMDbHelper.WidgetContainer,Home)</onunload>\n"
            "<onunload>ClearProperty(CurrentID)</onunload>\n"
        )
    },
    {
        "id": "af3_touch_cleanup_infooptions",
        "name": "AF3: clear the home info state when the info options closes",
        "description": "Touch navigation: closing the dialog must clear the Home properties it set (InfoPanel, SubGroup, TMDbHelper context menu / widget container, CurrentID), or the next tap lands in a stale state.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "skin.arctic.fuse.3",
        "enabled": True,
        "optional": True,
        "target_file": "1080i/Custom_1172_Dialog_InfoOptions.xml",
        "marker": "# WIZARD_AF3_TOUCH_INFOOPTIONS_v1",
        "anchor": "<window",
        "action": "append_after",
        "hook": (
            "<onunload>ClearProperty(InfoPanel.FullSwitch,Home)</onunload>\n"
            "<onunload>ClearProperty(SubGroup.IsVisible,Home)</onunload>\n"
            "<onunload>ClearProperty(TMDbHelper.ContextMenu,Home)</onunload>\n"
            "<onunload>ClearProperty(TMDbHelper.WidgetContainer,Home)</onunload>\n"
            "<onunload>ClearProperty(CurrentID)</onunload>\n"
        )
    },
    {
        "id": "af3_touch_cleanup_tmdbhelper",
        "name": "AF3: clear the home info state when the TMDbHelper window closes",
        "description": "Touch navigation: closing the dialog must clear the Home properties it set (InfoPanel, SubGroup, TMDbHelper context menu / widget container, CurrentID), or the next tap lands in a stale state.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "skin.arctic.fuse.3",
        "enabled": True,
        "optional": True,
        "target_file": "1080i/Custom_1190_TMDbHelper.xml",
        "marker": "# WIZARD_AF3_TOUCH_TMDBHELPER_v1",
        "anchor": "<window",
        "action": "append_after",
        "hook": (
            "<onunload>ClearProperty(InfoPanel.FullSwitch,Home)</onunload>\n"
            "<onunload>ClearProperty(SubGroup.IsVisible,Home)</onunload>\n"
            "<onunload>ClearProperty(TMDbHelper.ContextMenu,Home)</onunload>\n"
            "<onunload>ClearProperty(TMDbHelper.WidgetContainer,Home)</onunload>\n"
            "<onunload>ClearProperty(CurrentID)</onunload>\n"
        )
    },
    {
        "id": "af3_plot_autoscroll",
        "name": "AF3: the plot on the info screen scrolls on its own",
        "description": "Autoscroll (2 s delay, 8 s per pass, 5 s repeat) for the main plot text, in line with the other skins.",
        "category": "ui",
        "priority": "minor",
        "addon_id": "skin.arctic.fuse.3",
        "enabled": True,
        "optional": True,
        "target_file": "1080i/Includes_Info.xml",
        "marker": "# WIZARD_AF3_PLOT_AUTOSCROLL_v1",
        "anchor": "<font>font_main_plot</font>",
        "action": "prepend_before",
        "hook": (
            "<autoscroll delay=\"2000\" time=\"8000\" repeat=\"5000\">true</autoscroll>\n"
        )
    },

    # =====================================================================
    # ================= POV CONTEXT MENU: LIST MANAGERS ===================
    # =====================================================================
    {
        "id": "pov_cm_hide_unconnected_movies",
        "name": "Movie context menu: hide list managers of unconnected services",
        "description": "The list-manager entries of the context menu (Trakt, MDBList, TMDb lists) are hidden for a service that is not connected: the menu shows only what can work. POV drops every entry whose sort key is 0.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/movies.py",
        "marker": "# WIZARD_POV_CM_HIDE_UNCONNECTED_MOVIES_v1",
        "anchor": "\t\tself.cm_sort = settings.context_menu_sort()",
        "action": "append_after",
        "hook": (
            "try:\n"
            "\tself.cm_sort = dict(self.cm_sort, **{_k: 0 for _k, _s in "
            "(('trakt', 'trakt.token'), ('mdblist', 'mdblist.token'), ('tmdblist', 'tmdb.token')) "
            "if not kodi_utils.get_setting(_s)})\n"
            "except Exception:\n"
            "\tpass\n"
        )
    },
    {
        "id": "pov_cm_hide_unconnected_tvshows",
        "name": "Show context menu: hide list managers of unconnected services",
        "description": "The list-manager entries of the context menu (Trakt, MDBList, TMDb lists) are hidden for a service that is not connected: the menu shows only what can work. POV drops every entry whose sort key is 0.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/menus/tvshows.py",
        "marker": "# WIZARD_POV_CM_HIDE_UNCONNECTED_TVSHOWS_v1",
        "anchor": "\t\tself.cm_sort = settings.context_menu_sort()",
        "action": "append_after",
        "hook": (
            "try:\n"
            "\tself.cm_sort = dict(self.cm_sort, **{_k: 0 for _k, _s in "
            "(('trakt', 'trakt.token'), ('mdblist', 'mdblist.token'), ('tmdblist', 'tmdb.token')) "
            "if not kodi_utils.get_setting(_s)})\n"
            "except Exception:\n"
            "\tpass\n"
        )
    },

    # =====================================================================
    # ============= POV REUSE-LANGUAGE-INVOKER MISMATCH DIALOG ============
    # =====================================================================
    {
        "id": "pov_invoker_mismatch_silent",
        "name": "Reuse Language Invoker: sync addon.xml without the mismatch dialog",
        "description": "After a POV update POV's own reuseLanguageInvokerCheck finds addon.xml (true) against the build's setting (false), rewrites addon.xml and asks, in English, whether to reload the profile. The rewrite is kept; only the question and the reload are skipped, so the value takes effect at the next Kodi start.",
        "category": "ux",
        "priority": "minor",
        "addon_id": "plugin.video.pov",
        "enabled": True,
        "target_file": "resources/lib/modules/entry.py",
        "marker": "# WIZARD_POV_INVOKER_MISMATCH_SILENT_v1",
        "anchor": "\tif refresh and kodi_utils.confirm_dialog(text=text):",
        "action": "prepend_before",
        "hook": (
            "# The Wizard owns this flag (host_repairs/pov_language_invoker_guard); addon.xml was already rewritten above.\n"
            "refresh = False\n"
        )
    }
]


if __name__ == '__main__':
    import sys
    _problems = validate_registry(PATCH_CONFIG, sys.argv[1] if len(sys.argv) > 1 else None)
    for _problem in _problems:
        print(_problem)
    print('%d patches checked, %d problem(s)' % (len(PATCH_CONFIG), len(_problems)))
