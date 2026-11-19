# MoranSubs (`service.subtitles.kodipovilai`): architecture report and refactoring blueprint

**Status:** Subtitle Phase 0 COMPLETE (decision, last upstream merge, this document). **Subtitle Phase 1 COMPLETE (19 Nov 2026, wrap-up included):** dead code, DarkSubs / Wizard leftovers and the broken Telegram source deleted (Part 5), `settings.xml` reorganized into 8 categories (93 settings, 7 keys newly declared), every string id inside Kodi's add-on range (`33000`-`33008` moved to `32630`-`32638`), `kodi_utils.py` renamed `sub_utils.py`, one dialog-title constant, statistics in Part 6. **Subtitle Phase 2 COMPLETE (19 Nov 2026, one autonomous session):** the add-on now has the tree of §1.7.2 (72 -> 138 Python files, `default.py` and `service.py` are 27 / 26-line routers, no shim left behind), verified by AST identity, a golden harness and entry-point scenarios; the splits of `resolve` and a few other long functions were NOT done and are reported in Part 4 / Part 6. The owner revised the roadmap on 19 Nov 2026 (decision record below): **Phase 3** (core hygiene, deduplication starting with the playing release, the single route through `pipeline.py`, the contract test) is next, and its revised prompt is in `PHASE_2_AND_3_PROMPTS.md`; **Phase 4** (the deep splits of `resolve` and the other long functions, `embedded_extract` and `gender_reference`, `he_sub_match.py` and the cross-add-on integrations) is planned there. Everything below that names a pre-Phase-2 module (`translate`, `subsync`, `sub_utils`, ...) describes the code as it was; §1.2 and §1.8.1 give the new locations.
**Phase 4 PARTIAL (19 Nov 2026, one autonomous session; details in Part 4 "Phase 4 result"):** `resolve` (3,646 lines), `list_candidates` (604), `handle_download` (343) are dispatchers over stage modules with an explicit state object (`ai_translation/job/`, `fetching/search/`, `delivery/download/`); `embedded_extract.py` (4,986) and `gender_reference.py` (1,819) are split into 14 + 7 modules and lost their single-file standalone design; the two `entry/` tables exist; one RTL core, one `make_breather`, one tolerant `decode_link_safe`; `he_sub_match.py` keeps its structure (its two private names became public). NOT DONE: the `he_sub_match` facade, the other six functions over 300 lines, `_classify` percentage, quick toggles (owner switch NOT APPROVED). Add-on: 140 -> 185 Python files, 9 functions left over 300 lines (11 before), no file over 1,500 lines (3 before).
**Snapshot:** the analysis was made at `main` @ `0a6ce978` (add-on 0.3.74). On 2026-11-19 the last upstream merge (`update-migration` @ `96efe59a`) took the add-on to **0.3.96**; there will be no further upstream merges.
**Method:** static reading of every entry point and module, an AST import graph, a cross-module private-call scan, `pyflakes` and `vulture` (run from the scratchpad), a settings declared/read diff, git history, an upstream-divergence diff and per-file churn measurements. Nothing ran inside Kodi, so every behavioural claim about Kodi has to be checked in Kodi.

> ### Phase 0 decision record (owner, 2026-11-19)
>
> - **We decoupled from MoranSubs. We own the subtitle core.** No more `update-migration` merges, no sync script, no PR watcher. Architectural refactoring is **strictly encouraged** (`CLAUDE.md`, rule "SUBTITLE ADD-ON: DECOUPLED").
> - This **supersedes**: the Option C recommendation (§0), the two-zone principle and the `resources/povil/` overlay (§1.7), the upstream boundary in the diagrams, Part 3's sync automation and the old phase table. Parts 1 and 2 remain the analysis. Their line numbers refer to the 0.3.74 snapshot; after the 0.3.96 merge, re-locate code by function name (`translate.py` grew by 2,334 lines).
> - **`settings.xml`** is reorganized into categories with visual comment blocks (`<!-- ========= SYNC ========= -->`), following §2.4. Setting ids stay unchanged.
> - **Dead code and leftovers may be deleted.** Every inactive feature and setting removed is listed in the **Deletion Report** (Part 5), so the owner can decide what to revive.
> - **Statistics:** at the end of each phase the agent updates **Refactoring Statistics** (Part 6): code cleaned, deleted, moved and optimized.
> - **The last merge** (0.3.74 → 0.3.96) took upstream's subtitle-core work and kept our side of every conflict. Every conflict was a block we had moved to the Wizard. Two items were dropped: a new AF3 host-repair patcher and a trailer guard tied to upstream's Wizard. Upstream Onyx 1.0.10-1.0.20 was **not** taken. `CLAUDE.md` lists every bypassed feature under "SKIPPED UPSTREAM FEATURES".

> ### Phase 2-4 roadmap decision record (owner, 2026-11-19, after Phase 2)
>
> - **Deferred to Phase 4:** the split of `ai_translation/service.py:resolve` (with the recorded Gemini / Google conversation golden harness it needs) and of the other functions over 300 lines; the split of `fetching/embedded/embedded_extract.py` and `ai_translation/context/gender_reference.py` (their standalone / stdlib-only design stays intact through Phase 3); `he_sub_match.py` as a thin facade and every change to the Wizard, the skins and the POV / Umbrella patches. **`he_sub_match.py` stays byte-identical through Phase 3.**
> - **Included in Phase 3:** the removal of the `sa` argument from the 3 `gender_reference` window helpers is accepted; private helpers imported by other modules become public (`_home_window`, `_gp_*`, ...); `core/utils/` is audited and removed from the tree if nothing needs it; the `core/kodi.py` <-> `core/settings.py` cycle is broken; deduplication starts with the 5 "playing release" derivations (Priority #1), then the RTL renderers, SRT parsers, caches and the duplicate `_decode_link` / `_breathe`.
> - **Why Phase 3 and Phase 4 are separate (owner rationale):** the remaining work is split into Phase 3 (internal deduplication and cleanup) and Phase 4 (monolith decomposition and cross-add-on integrations) to keep the blast radius of each change small, and so that Phase 3's deduplication shrinks the giant functions (the duplicate SRT, RTL and release-detection helpers `resolve` calls) before Phase 4 slices them.
> - **Phase 4 roadmap:** (1) `he_sub_match.py` (1,281 lines, imported by bare name by POV and Umbrella from their own interpreters, through the Wizard patches `patches_config`, `pov_prewarm`, `pov_sub_match_v2`, `umbrella_he_match`; the Wizard repairs such as `host_repairs/flow/umbrella_steps.py` and the skin hooks sit around it) becomes a thin facade with the exact public API (`prewarm`, `release_names`, `embedded_names`, `confirmed_releases`, `label_prefix`) delegating to `scoring_matching/` and `fetching/`, followed by a dedicated audit of the Wizard patches, skin integrations and POV / Umbrella hooks; (2) `resolve` (3,650 lines, 27 closures capturing 1-28 variables) gets a deterministic golden harness with recorded Gemini / Google conversations, an explicit state / context object instead of the shared closure state, and is split along its link-type branches; (3) `embedded_extract.py` (4,986) and `gender_reference.py` (1,819) lose the single-file standalone constraint and are split into modular sub-files (free of `xbmc*` imports where clean, ordinary package imports otherwise). Phase 3 step 3.0 also checks whether anything outside the add-on imports `embedded_extract` or `gender_reference` directly (on 19 Nov 2026 nothing does).
> - **Consequence:** the lifecycle of §1.7.3 needs `resolve` split, so it moves to Phase 4; in Phase 3 `pipeline.py` is the single route to the two whole functions. The Phase 2 golden harness never held the `list_candidates` ordering case (only the entry-point search scenario covers it); Phase 3 step 3.0 adds it.

---

## 0. Executive summary

1. **The add-on is ≈55.8k lines of Python after the 0.3.96 merge** (≈50.4k at 0.3.74; not counting the bundled `pysrt`, `parse_torrent_title` and `pyxbmct`, 3.5k). Seven files hold 60% of it: `translate.py` 9,706 · `subsync.py` 5,577 · `embedded_extract.py` 4,984 · `sync_align.py` 3,374 · `default.py` 3,348 · `srt.py` 3,292 · `subs_engine_bridge.py` 1,949.
2. **One function is the AI-translation pipeline.** `translate.resolve()` runs from line 6033 to the end of the file (3,674 lines at 0.3.96, more than 30 nested closures; `_extract_embedded_srt` 807 and `list_candidates` 616 are next). It handles every link type, cache, the community pool, the Gemini and Google translators, gender references, repairs, proofreading, telemetry, pool upload and the sync hand-off. Most of the coupling lives here.
3. **The three pillars depend on each other in a cycle.** `translate` → `subsync` → `translate`. `subsync` → `gemini` (an audio-transcription timing probe). `translate` → `sync_align` and `arabic_gender` (reference alignment). `sub_utils` → `subs_engine_bridge` → `sub_utils`. More than 25 private helpers are called across modules, for example `translate._decode_link` 15 times from other files.
4. **Several subsystems exist more than once:**
   - 4 SRT parsers: `srt.py`, `subs_engine/srt.py`, the bundled `pysrt`, `sync_align.parse_srt`.
   - 2 timing aligners: `arabic_gender` and `sync_align`.
   - At least 6 caches with their own TTL and pruning rules.
   - 3 machine translators: `google_translate.py`, plus the Google, Bing and Yandex translators in `subs_engine/engine.py`.
   - 5 places that work out the playing release name, which every match % depends on.
5. **The match % is passed around as display text.** `default._handle_search` reads the star rating back out of the Hebrew label with a regex (`(\d{1,3})%`). The labels themselves are built inline in `translate.list_candidates`.
6. **The sync engine has no user-facing settings at all.** `subsync_verify` (the master switch), `subsync_audio`, `subsync_probe` and `subsync_autorank` are read in code but not declared in `settings.xml`. Six other settings are read without being declared.
7. **Leftovers from the Wizard and from DarkSubs are still present:**
   - `DEBRID_NOTICE_*` tables.
   - Calls to modules that no longer exist: `dark_subs_integration`, `source_memory`.
   - `service.py` helpers with no callers and a stale comment about the old Wizard repair pass.
   - Five declared settings that nothing reads, among them `keep_darksubs` and `force_ai_when_auto_translate_off`, which still appear in the settings screen.
   - A dead Bing/Yandex translator stack in the vendored engine.
   - 27 "Kodi POV IL" dialog titles.
8. **(History, before the Phase 0 decision.) There were two upstreams, and only one of them mattered:**
   - **(a) MoranSubs.** Its commits are replayed onto `update-migration` and then merged. It is very active: 27 releases in 3 weeks, `translate.py` changed 43 times since 15 Sep.
   - **(b) DarkSubs.** Its fetch engine was copied into `subs_engine/` in June 2026 (`f91dccbb`) and has been edited locally since then. That upstream is gone; DarkSubs was removed from the build at `18435d90`.
   - Every retained file under `resources/lib/` is **byte-identical to MoranSubs 0.3.74**. Our fork changed only `default.py`, `service.py`, `settings.xml` and the two `strings.po` files.
9. **Upstream churn is in the pipeline, not in the scrapers.** Provider files in `subs_engine/sources/` hardly change upstream. `translate.py`, `subsync.py`, `srt.py`, `sync_align.py` and `arabic_gender.py` change all the time. Moving only the providers behind an adapter therefore removes very few future merge conflicts. Splitting the big pipeline files does make future upstream merges expensive.
10. **Resolved in Phase 0:** the old boundary rule (*"never rename, move or reorganize a core subtitle file"*) was replaced in `CLAUDE.md` by "SUBTITLE ADD-ON: DECOUPLED". Points 8-9 are history now.

> **Strategy (decided in Phase 0):** full decoupling. The target tree is §1.7, the execution plan is Part 4 (three phases), and every phase updates Part 5 (Deletion Report) and Part 6 (Refactoring Statistics).

---

## Part 1: end-to-end pipeline, coupling and target architecture

### 1.1 Entry points (the public surface)

| Kind | Trigger | File · function | Boundary-pinned? |
|---|---|---|---|
| Kodi subtitle module (`xbmc.subtitle.module`) | Kodi's subtitle dialog: `action=search` / `manualsearch` / `download` | `default.py` · `main()` → `_handle_search`, `_handle_manualsearch`, `_handle_download` | **Yes** (`default.py` path) |
| Kodi service (`xbmc.service`) | Kodi start | `service.py` · `main()` | **Yes** (`service.py` path) |
| RunScript (skins) | `RunScript(service.subtitles.kodipovilai,action=choose_subs)` (18 call sites in skins) | `default._handle_choose_subs` → FENtastic: `ActivateWindow(SubtitleSearch)`, others: `subs_chooser.show()` | **Yes** |
| RunScript (internal hand-off) | the download step fires itself | `action=bg_translate_picker` → `default._handle_bg_translate_picker`; `action=translate_file` → `_handle_translate_file` | **Yes** |
| RunScript (POV badge warm) | POV source window | `action=he_avail` → `he_sub_match.run_warm` | **Yes** |
| RunScript (settings buttons) | `settings.xml` action rows | `open_aistudio`, `test_connection`, `connect_gemini` (also frozen Wizard patch `pov_myservices`), `show_gemini_usage`, `open_tmdb_notice`, `clear_cache`, `pool_share_cache(_run)`, `purge_temp`, `engine_test` (the Telegram buttons `connect_telegram`, `logout_telegram`, `telegram_test` were removed at the end of Phase 1) | **Yes** (action names) |
| Bare-name import from other interpreters | POV / Umbrella hooks append `…/resources/lib` to `sys.path` | `he_sub_match.py`: `prewarm`, `release_names`, `embedded_names`, `confirmed_releases`, `label_prefix`; stdlib only at module level | **Yes**: file must stay at `resources/lib/he_sub_match.py` |
| Window properties read by other processes | skins / POV / Wizard | `subs.player_filename` (`subs_filename_publisher`), `MoranSubsChooserOpen`, `povil.embedded_*`, `moransubs.*` | **Yes** (byte-identical) |
| Settings read by the Wizard | Wizard `host_repairs/kodi_utils.subs_get_*` | `use_builtin_engine`, `pov_fast_navigation` | **Yes** (ids) |
| Dead inbound call (known defect) | Wizard `profiles/build_skin.py:252` | `action=prepare_build_skin` → falls into `unknown action` | n/a |

### 1.2 Current flow (as built, after Phase 2)

The flow is unchanged; only the homes of the steps moved (the previous diagram, with the pre-Phase-2 file names, is in git at `7392e320`). `pipeline.py` exists as a stub and is not called yet: `entry/` still calls the feature packages directly.

**After Phase 4:** the three big functions are dispatchers over stage modules: `ai_translation.service.resolve` -> `ai_translation/job/` (`state.py` + `links/*` for the link types + `ai/*` for the AI flow), `fetching.service.list_candidates` -> `fetching/search/`, `delivery.kodi_player.handle_download` -> `delivery/download/`. Each stage is `stage(S)` over one state object and returns `NEXT` to continue or the final value. `entry/plugin.py` and `entry/migrations.py` run tables. The step names above are still the stage names.

```
 Kodi hooks (PINNED):  default.py (27 lines)  ->  entry/plugin.main        service.py (26 lines)  ->  entry/service.main
                          |                                                    |
   user opens the subtitle dialog / skin button                   video starts -> autosub/player.AutoSubPlayer.onAVStarted
                          |                                                    |  gates: autosub/policy + engine on, skip_autosub, live
        entry/plugin: _parse_query, then by action:                           v
          search        -> ui/listing._handle_search                  autosub/player.autosub_on_play
          download      -> delivery/kodi_player._handle_download       (the automatic pick and its application)
          choose_subs   -> entry/actions -> ui/chooser.show (XML or pyxbmct window)       |
          translate_file / bg_translate_picker -> ai_translation/background               |
          settings buttons -> ui/settings_actions/{gemini,cache,engine} · entry/actions   |
                          \______________________________________________________________/
                                              |
                                              v
        fetching/service.list_candidates(info)   (the search side; 605 lines, not split)
          fetching/local ........................ files beside the video / in temp
          fetching/embedded/streams ............. playing streams (snapshot, embedded rows, select_embedded)
          fetching/bridge.search ................ search cache (fetching/search_cache) -> fetching/provider_adapter
               -> fetching/providers/engine.c_get_subtitles -> providers/sources/{ktuvit,wizdom,opensubtitles,yify,
                  subsource,subscene,bsplayer}.get_subs -> sort_subtitles -> scoring_matching/release (match %)
          fetching/pool_client.lookup ........... community pool (read side); pool_harvest queues Ktuvit mirrors
          scoring_matching/ranking .............. match %, same-source test, SDH / gender-source rank, language names
          ai_translation/reuse/sdh_{registry,pool} SDH knowledge · core/cache/translation_cache "[CACHE]" rows
          scoring_matching/badges._finalize ..... badge write-through (he_sub_match) + "currently applied" mark
                                              |  list[dict] (the label carries the %)
                                              v
        ui/listing: ListItem rows -> the user picks -> plugin://...?action=download&link=<json>  (core/link_codec)
                                              |
        delivery/kodi_player._handle_download: sync cancel (sync/jobs) · core/selection token triple
          engine+embedded -> fetching/embedded/streams.select_embedded      engine_ai -> bridge.download -> 'ai' link
          embedded_ai / ai -> RunScript(bg_translate_picker) -> ai_translation/background
          fast_first_chunk -> _try_fast_download                            others -> ai_translation/service.resolve
                                              |
        ai_translation/service.resolve(link, info, ...)   (the download side; 3,650 lines, not split)
          passthrough · pool (reuse/pool_reuse, fetching/pool_client.fetch) · embedded_sync · engine (bridge.download)
          embedded_ai (fetching/embedded/extraction -> embedded_extract, embedded_steps) · ai
          AI: source_prep -> core/cache lookup -> reuse/pool_reuse -> providers/google_flow (Google mode) -> context/{prompt,tmdb_helper,
              gender_reference} -> dispatch/{pacing,srt_wire} -> providers/{gemini,model_fallback} -> repairs/{english_residual,
              script_residual,gender_guard,he_person_gender,leak_rescue,postfix_*} -> proofread/{gp_state,gp_run,gp_jobs}
              -> telemetry -> reuse/pool_contribute
          every file -> delivery/rtl (_rtl_delivery_copy) -> sync/delivery_gate._sync_hebrew_delivery -> sync/service.process
                                              |
        sync/service.process: sync/verdicts (cache + community) · sync/oracles (fetching/bridge.search_all_languages_for_timing)
          · sync/align/{estimate,verify,regions,piecewise,retime} · sync/probes/{remote,audio,cache,bundle} (mkv_probe, mp4_probe,
          embedded cues, Gemini audio probe) · sync/validators + sync/deep (deep verify) · sync/jobs (queue + drainer)
                                              |
        delivery/kodi_player: _try_next_hebrew -> _deliver_named_subtitle (player.setSubtitles, unique name) or addDirectoryItem
          -> core/selection.confirm_subtitle_sync_registration -> ai_translation/proofread/gp_jobs.proofread_later

 entry/service threads: he_warm drainer (he_sub_match.run_warm) · sync drainer (sync/jobs.drain_queue_once) · sync delay watch
   (sync/verdicts.finalize_delay_session) · pool queue drainer (ai_translation/reuse/pool_contribute.drain, fetching/pool_harvest) ·
   shared SDH refresh · ui/filename_publisher.SubsFilenamePublisher · gp_later runner · daily cache prune · entry/migrations (15 `_maybe_*`)
```

**As built after Phase 3 (19 Nov 2026).** Everything outside `fetching/` and `ai_translation/` reaches `list_candidates` and `resolve` only through `resources/lib/pipeline.py` (`pipeline.search(info, modal_progress=True, quick=False)`, `pipeline.resolve(link, info, **kwargs)`); the contract test fails if another module imports `fetching.service` or `ai_translation.service`. `core/addon.py` is the leaf that holds the add-on handle; `core.kodi` and `core.settings` import it and no longer each other. The playing release is derived in ONE place, `core.video_context.release_name(info, policy)`; every caller passes its own named policy (table in Part 4). Every row of `list_candidates` carries `pct` (the number its label shows, `None` when the label shows none) and `ui/listing.py` takes the star count from it.

### 1.3 Stage-by-stage trace (files, functions, data)

| # | Stage | File · function (line) | Input → output | Notes / coupling |
|---|---|---|---|---|
| 1 | Invocation | `default.py` · `main` (3279), `_parse_query` (38) | argv → `action`, params | Handles both plugin and RunScript call styles |
| 2 | Opt-out gate | `default._handle_search` (112) | `Window(10000).skip_autosub` (fresh < 90 s) | Hardcoded 90 s |
| 3 | DarkSubs hook (dead) | `default._handle_search` (138-145), `_handle_test_connection` (2081) | imports `dark_subs_integration` | **Module missing**, the call sits in `try` |
| 4 | Video context | `sub_utils.current_video_info` + `subs_engine_bridge._release_ready` | player labels → `info` dict | Waits up to 15×200 ms; calls a private function across modules |
| 5 | Candidate assembly | `translate.list_candidates` (1001-1616) | `info` → `list[candidate]` | About 600 lines: fetch, rank, label and policy in one function |
| 5a | Local files | `local_subs.find_alongside`, `find_in_temp` (+ `language_detect`) | path → `(path, lang)` | Hebrew from temp deliberately refused |
| 5b | Embedded streams | `subs_engine_bridge.embedded_candidates` (1169), snapshot `povil.embedded_snap` | player streams → embedded rows (`_pct` 101) | |
| 5c | Provider search | `subs_engine_bridge.search` (331) → `_search_inner` (672) → `engine.c_get_subtitles` (408) → `sources/*.get_subs` → `engine.sort_subtitles` (82) | `video_data` → tuples `(label, label2, icon, thumb, url, percent, sync, hi, filename, site_id)` → candidates tagged `_engine_kind` ∈ {human_he, mt_he, other} | Providers report results by appending to a module-global `global_var`; search lock; 24 h disk cache; 120 s "recent" window property; providers that are down are skipped for 600 s |
| 5d | Synthetic rows | `list_candidates` (1139) | `embedded_sync` row | Hebrew label text hardcoded |
| 5e | Ktuvit harvest | `pool.enqueue_harvest` | queue file | Side effect inside a list builder |
| 5f | Community pool | `pool.lookup` (+ `_match_pct`) | `info` → variants | |
| 5g | Ranking and labels | `list_candidates` 1185-1594, `_is_sdh_ext` (376), `_gender_src_rank` (370) | → ordered rows | Match % is formatted into the label |
| 5h | Finalize | `_finalize` (833) → `he_sub_match.merge_names`; `_mark_current` (802) → `sub_utils.get_subtitle_sync_status` | → rows + "» נוכחית" | The list builder also writes the badge cache |
| 6 | Render | `default._handle_search` 172-207 | rows → `ListItem`s | **Star rating is regex-parsed out of the label**; sync label appended |
| 7 | Pick / selection | `default._handle_download` (210); `sub_utils.current_subtitle_selection` | link → selection `{token, link_hash, stream_hash}` | A race guard that gets passed through every layer |
| 8 | Dispatch by link type | `_handle_download` 247-436 | link payload `type` | 7 link types (§1.4) |
| 9 | Resolve | `translate.resolve` (3951-7372) | link → SRT path | The single largest coupling point |
| 9a | Delivery copy | `_rtl_delivery_copy` (632), `_reapply_rtl_fix_in_place` (549) | file → RTL copy | `subs_engine_bridge._render_hebrew_rtl_copy` is a second RTL renderer |
| 9b | Sync gate | `_sync_hebrew_delivery` (688) → `subsync.process` (1686) | path, release → retimed path + verdict | Fail-open; `embedded_timing` bypass |
| 9c | AI translation | `resolve` 4469-7372 | source SRT → Hebrew SRT | §1.5 lists its sub-stages |
| 10 | Deliver | `_deliver_named_subtitle` (697), `_try_next_hebrew` (792), `addDirectoryItem` | path → Kodi | Registration confirmed through `sub_utils.confirm_subtitle_sync_registration` |
| 11 | Post-delivery | `translate.proofread_later` (3616) | link → background job | |
| B1 | Background AI | `default._handle_bg_translate_picker` (1108) → `translate.resolve(progressive_cb=…)` | chunks → `player.setSubtitles` swaps | About 460 lines of UI/progress closures in `default.py` |
| B2 | Autosub | `autosub_service.autosub_on_play` (68-750) | play event → applied subtitle | Calls `list_candidates`, `subsync.rank_ready_candidates` / `diverse_human_alternatives` / `prefetch_autosub_reference`, `translate.resolve`; `ai_when_no_hebrew` policy |
| B3 | Chooser | `subs_chooser.show` → `_ChooserXML` (`script.moransubs.chooser.xml`) / `_show_pyxbmct` | rows → `_deliver_pick` | Classifies rows by decoding links (`translate._decode_link`) |
| B4 | Service | `service.main` (1098) | — | Threads listed in the diagram; 13 one-shot `_maybe_*` migrations (plus 2 start-up hooks) |

### 1.4 Data contracts that exist today (implicit)

**Candidate row** (`list_candidates` output): `filename` (display label, includes the %), `language`, `link` (URL-quoted JSON), `sync`, `rating`, `is_hi`, `is_hd`, private `_engine_kind`, `_pct`, `_is_mt`, `_subsync_state`, `_subsync_label`, `is_cached`.

**Link payload** (`translate._encode_link` / `_decode_link`, JSON in the URL):

| `type` | Produced by | Consumed by | Key fields |
|---|---|---|---|
| `passthrough` | local alongside file | `resolve` | `path`, `release` |
| `pool` | `pool.lookup` | `resolve` | `hash`, `release`, `pool_kind` (ktuvit/ai/ai_emb), `source_lang` |
| `engine` | bridge `_encode_engine_link` | `resolve`, `_handle_download` (embedded) | `source`, `language`, `filename`, provider download fields, `embedded`, `stream_index`, `lang` |
| `engine_ai` | `list_candidates` (foreign + AI on) | `_handle_download` → becomes `ai` | engine fields + `src_lang` |
| `embedded_ai` | `list_candidates` | `bg_translate_picker` → `resolve` | `src_lang`, `stream_index` |
| `embedded_sync` | `list_candidates` | `resolve` | `lang`, `stream_index` |
| `ai` | local / engine_ai | `bg_translate_picker` → `resolve` | `source_lang`, `local_path`, `force_ai`, `proofread_only` |

**Provider module contract** (the vendored engine, `engine.c_get_subtitles` / `download_sub`): module attributes `global_var: list`, `site_id: str`, `sub_color: str`; functions `get_subs(video_data[, all_lang_override])` (appends tuples to `global_var`), `download(download_data, MySubFolder) -> path`. `video_data` keys: `imdb, IMDBNumber, imdb_UniqueID, tmdb, title, OriginalTitle, TVShowTitle, year, season, episode, media_type, media_type_videoInfoTag, file_original_path, Tagline, Tagline_From_Fen, mpaa, is_local_media_playing, state`.

**Selection triple** `{token, link_hash, stream_hash}`. It is created in `sub_utils`, re-checked by `default`, `translate`, `subsync`, `autosub_service` and `subs_chooser`, and stored in Window(10000) properties.

### 1.5 Coupling audit

#### 1.5.1 Pillar × file matrix (● main owner, ○ also implements part of it)

| File | Fetching | Match % | AI translation | Sync | Cache | Delivery / UI | Settings / migrations |
|---|---|---|---|---|---|---|---|
| `default.py` | ○ engine test | ○ star parse | ○ 460-line bg job + Gemini pairing UI | ○ cancel / registration | ○ clear cache | ● | ○ |
| `service.py` | | | ○ model-bump migrations | ○ drainers, delay watch | ○ prune | ○ publisher | ● 13 migrations |
| `translate.py` | ○ list build, harvest | ○ `_match_pct`, labels | ● | ○ `_sync_hebrew_delivery`, `_embedded_aligned_source_srt`, calls `subsync._oracle_candidates` / `_download_oracle` | ○ tiers / markers | ○ labels, RTL copies | ○ policy reads |
| `subs_engine_bridge.py` | ● | ○ `_rating_for`, release detect | | ○ timing search | ○ 3 caches | ○ RTL render, `select_embedded` | ○ engine defaults |
| `subs_engine/engine.py` | ● | ○ `sort_subtitles` | ○ dead Bing / Yandex / Google MT | | ○ | | |
| `subsync.py` | ○ oracle downloads | ○ tier ranks | ○ **Gemini audio probe**, imports `translate` | ● | ○ verdict / probe caches | ○ `status_line` | |
| `sync_align.py` | | | | ● | | | |
| `arabic_gender.py` | ○ reference fetch through the engine | | ● | ○ **its own aligner** (`_TOL`, `_MAXOFF`, windows) | | | |
| `release_match.py` | | ● | | ○ `AUTO_OK_TIERS` | ○ memo | | |
| `he_sub_match.py` | ○ pool / Wizdom / OS / Ktuvit warm | ○ badge % | | | ○ 3 caches | ○ badge labels | |
| `sub_utils.py` | | | | ○ sync status, staged fixes | | ● selection / registration | ● settings |
| `pool.py` | ○ pool fetch, harvest | | ○ contribute, proofread variants | ○ `/sync` registry | ○ lookup cache | | |
| `srt.py` | | | ● post-processing (3.3k lines) | ○ `_block_start` / `_block_times` | | ○ RTL | |
| `subs_chooser.py` | ○ | ○ | ○ `_start_ai_apply` | ○ | | ● | |

#### 1.5.2 Concrete tangles (with evidence)

1. **`resolve()` as the god function** (`translate.py:3951-7372`). Its closures cover selection guarding, Gemini retries and rate pacing, content-block fallbacks, gender verification, repairs, proofread scheduling, telemetry counters and pool upload. None of them can be tested or replaced without loading all 7,372 lines.
2. **Import cycles:** `translate ⇄ subsync`, `translate ⇄ arabic_gender` (arabic_gender imports translate), `sub_utils ⇄ subs_engine_bridge` (a lazy import inside a function of `sub_utils.py`), `subsync → translate`, `he_sub_match → subs_engine_bridge`. They only work because the imports sit inside functions.
3. **Private helpers called across modules** (scan): `translate._decode_link` ×15, `pool._post` ×5, `srt._block_start` ×4, `pool._release_from` ×4, `translate._source_id_for_ai` ×3, `translate._match_pct` ×3, `pool._is_token_like` ×3, `subsync._oracle_candidates` / `_download_oracle` (from translate), `subs_engine_bridge._release_ready` ×2, `sync_align._preflight_srt` / `_breathe`, `subsync._migrate_probe_cache` / `_sync_registry_release` (from service). These are the real interfaces, and none of them is declared as one.
4. **Duplicated subsystems:**
   - **SRT parsing/writing:** `srt.py` (3,292 lines), `subs_engine/srt.py` (511), `_libs/pysrt`, `sync_align.parse_srt` / `dialogue_cues`.
   - **Timing alignment:** `arabic_gender` (`_TOL=500`, `_MAXOFF=600000`, window voting) and `sync_align` (`_TOL=500`, `_MAXOFF=250000`, its own header says it "generalizes the aligner from arabic_gender.py").
   - **Caches:** `cache.py` (translations), `subs_engine/cache.py` (SQLite, Genesis), bridge `Cached_subs_v2` + 24 h search cache + 120 s recent property, `subsync` verdicts / probe cache / queue, `he_sub_match` engine / pool / warm caches, `pool` lookup / sync memo.
   - **Machine translation:** `google_translate.py`, plus `engine.google_machine_translate` / `bing_*` / `yandex_*` (vendored, unused).
   - **"Which release is playing":** `subs_engine_bridge._detect_release_name`, `translate.list_candidates` `_video_ref`, `subs_chooser._video_ref`, `subsync.playing_release`, `pool._release_from`. The match % agrees across screens only while these five stay in step.
   - **RTL rendering:** `translate._rtl_delivery_copy` / `_reapply_rtl_fix_in_place` and `subs_engine_bridge._render_hebrew_rtl_copy`.
5. **Display strings carry data.** The % is formatted into Hebrew labels in `translate.list_candidates` / `_pool_entry`. `default.py:180` parses it back for the stars, and `_finalize` filters rows by label prefix (`startswith('תרגום')`). A label change is a behaviour change.
6. **Sync depends on AI.** `subsync._audio_probe_reference` (733) needs `api_key` and calls Gemini, so the sync engine cannot be shipped or tested without the AI module.
7. **AI depends on fetching and sync.** `arabic_gender` searches providers through the engine to get gender references and aligns them itself. `translate._embedded_aligned_source_srt` (2232) calls subsync internals.
8. **`sub_utils` is three modules in one.** It holds the settings facade, the logging/notify toolkit, and the whole subtitle-selection / registration / sync-status state machine (about 35 functions on Window(10000) properties).
9. **`default.py` is three modules in one.** It holds the router, the delivery / background-translation runtime (`_handle_bg_translate_picker`, `_handle_translate_file`, about 1,100 lines) and the settings-action UIs (Gemini pairing window, engine test).
10. **Side effects inside the read path.** `list_candidates` queues Ktuvit harvest jobs and writes the badge cache. `_handle_search` used to patch DarkSubs.

#### 1.5.3 AI translation sub-stages inside `resolve()` (what has to be separated)

`source acquisition` (pool / engine / embedded extract) → `source preparation` (`_prepare_source`, HI strip, SDH learning) → `cache & pool reuse` (`cache.translated_path`, content hash, `_pool_reuse_fetch`) → `translator selection` (`translation_mode`: ai / google / none) → `context` (`prompt.build`, `tmdb_helper` cast, `arabic_gender` reference stack) → `chunking` (`srt.chunk_blocks`, `srt_wire`) → `dispatch` (`_translate_one`, `_call_gemini`, rate gate, block clock, retry and backoff) → `repairs` (`english_residual`, `script_residual`, `gender_guard`, `he_person_gender`, Google fill) → `stitch & post-fix` (about 25 `srt.*` passes, RTL) → `persist` (`cache.save_text`, markers `.gp*`) → `proofread` (`gender_proofread`, deferred job) → `report` (`telemetry`, `pool.contribute_once`) → `sync hand-off`.

### 1.6 Dead code and legacy remnants (deletion approved in Phase 0; every removal goes into the Deletion Report, Part 5)

**Wizard remnants (our fork's files)**

| Location | What | Evidence |
|---|---|---|
| `default.py:2975-2993` | `DEBRID_NOTICE_SERVICES`, `DEBRID_NOTICE_VALUES` | The debrid dialog moved to the Wizard (`addons/debrid_notice.py`); nothing in the add-on reads them |
| `default.py:2996` | `_translate_path()` | Not called (vulture); left over from the removed debrid / Tonight handlers |
| `default.py:3005-3008`, `2972-2974` | blank gaps where handlers were cut | cosmetic |
| `service.py:79` `_without`, `service.py:84` `_walk_all` | skin-stamp / tree-walk helpers of the moved repair pass | Not called |
| `service.py:97-113` | comment about `_run_build_startup_repairs`, `pov_reload`, `REPAIRS_DONE_PROPERTY`, "the wizard's hot_reload" | Describes code that now lives in the Wizard |
| `service.py:46-51` | `_BACKGROUND_SETTLE_*` comment cites the "patch-health tree scan" | `patcher_health` moved to the Wizard |
| `service.py:162-181`, `1141`, `1263` | `_prune_source_memory_once` imports `source_memory` | **Module missing** (known defect); logs a WARNING once a day |
| `service.py:927-966` | `_maybe_default_builtin_engine` docstring "Build-edition only … SLIM_SERVICE" | Stale (kept on purpose for clean merges, Open Notes) |
| 27 dialog titles | `'Kodi POV IL'` in `default.py`, `kodi_utils.py:356`, `gemini_pair.py:107` | Brand of the build, not of the add-on (cosmetic) |
| `settings.xml` | `_addon_update_mode_seeded`, `_lists_sort_recent_v1`, `_subtitle_outline_migration_v1`, `_something` (template id) | Declared, never read: markers of moved Wizard features |

**DarkSubs remnants**

| Location | What | Evidence |
|---|---|---|
| `default.py:138-145`, `2081-2082` | `dark_subs_integration.maybe_patch_darksubs()` | Module not shipped; DarkSubs removed (`18435d90`) |
| `settings.xml` `keep_darksubs` (visible), `force_ai_when_auto_translate_off` (visible), `_darksubs_takeover_done`, `_darksubs_hook_last_fire`, `_darksubs_hook_last_outcome`, `_darksubs_autoenable_done`, `_darksubs_nag_done`, `_force_ai_autoenable_done` | settings | **None is read by code.** The two visible ones are switches that do nothing |
| `translate.py:1606` | empty-list toast "בחר כתובית באנגלית מ-DarkSubs…" | Tells the user to use an add-on that is no longer installed |
| `translate.py` module header (lines 4-20) | "we do NOT touch OpenSubtitles ourselves — DarkSubs handles sourcing" | No longer true: the built-in engine is the default |
| `subs_engine/engine.py:740-1130` | `bing_*`, `yandex_*`, `google_machine_translate`, `machine_translate_subs`, `download_sub`, `Bing` class | Unused by the bridge (vulture: `bing_api_machine_translate`, `download_sub`) |
| `subs_engine/general.py` | `take_title_from_focused_item`, `save_file_name`, `get_db_data`; `subs_engine/srt.py` `compose` | Unused |
| `subs_engine/sources/telegram_impl.py` (deleted at the end of Phase 1, Part 5 rows 46-50) | `telegram_helper_window`, `async_upload_subtitle`; imported a missing `telegram_api` | Was the only baseline import failure |
| `subs_engine/sources/opensubtitles.py:472` | `get_random_key` | Unused |
| `local_subs.find_in_temp` | "SRTs dropped by other subtitle add-ons (DarkSubs…)" | Still useful for other add-ons; keep, but update the comment |
| repo `userdata/addon_data/service.subtitles.All_Subs/`, `all_subs_plus/` | empty local folders (untracked) | Local leftovers, not in git |

**Unused code inside the former upstream files** (ours now: in scope for Phase 1)

`translate._build_prev_context_by_idx` (2956); `subsync._remote_cue_reference` (687), `_probe_reference_cues` (1322), `proven_pool_ai_fallback` (1649), `_pending_key` (4317); `arabic_gender.prepare` (1749) plus unreachable code at 632; `prompt.build_arabic_gender_block` (406); `cache.total_size_mb`; `embedded_extract.probe_tracks`; `mp4_probe.subtitle_reference_source`; `kodi_utils.subtitle_sync_status_label`, `progress_dialog`; `gemini_pair.get_lan_ip`.
**Not dead:** `he_sub_match.prewarm` / `embedded_names` / `confirmed_releases` / `label_prefix` look unused here but POV / Umbrella hooks call them. Event methods (`onAVStarted`, `onClick`, `do_GET`) are vulture false positives.

**Unused imports and variables** (pyflakes): `default.py:1588` (`kodi_utils`), `2284` (`srt`), `3167` (`engine`, deliberate import probe); `kodi_utils.py:9` `sys`; `release_match.py:21` `os`; `autosub_service.py:906` (deliberate preimport); `service.py:395-396` (deliberate preimport of `opensubtitles` / `ktuvit`), `service.py:545` `xbmcgui`; `subs_chooser.py:118` `payload`; `translate.py:4196` redefined `_pshare`; plus 40 hits inside the vendored engine (`xbmcplugin`, `pkgutil`, `threading`, `quote*`, `DEFAULT_REQUEST_TIMEOUT`, `struct`, `random`, unused `Addon` locals, f-strings without placeholders, invalid escape sequences `\s` / `\d` in `cache.py:51`, `general.py:193-195`, `ktuvit.py:296`, which become SyntaxErrors in a future Python).

**Known defects already in `CLAUDE.md`** (still present): the `prepare_build_skin` inbound action has no handler; `source_memory.py`, `dark_subs_integration.py` and `fentastic_search_patcher.py` are missing; `manifest.json` pins the subtitle zip at 0.3.50 while the source is 0.3.74; `telegram_impl` needed `telegram_api` (the whole Telegram source was deleted at the end of Phase 1).

### 1.7 Target modular architecture

#### 1.7.1 Principles (decoupled)

- **One zone: we own everything.** Feature packages live under **`resources/lib/`**, so every module keeps the `resources.lib.` import root it already uses. New folders are namespace packages with no empty `__init__.py` (the `CLAUDE.md` rule). The vendored DarkSubs engine (`subs_engine/`) is ours too: it moves to `fetching/providers/`, and its absolute imports are rewritten as part of the move.
- **Pinned (external callers, never moved or renamed):**
  - `default.py` and `service.py` (`addon.xml` hooks);
  - `resources/lib/he_sub_match.py` (imported by bare name by POV / Umbrella hooks; stdlib-only at module level; API `prewarm`, `release_names`, `embedded_names`, `confirmed_releases`, `label_prefix`);
  - the chooser XML file name `script.moransubs.chooser.xml`, every `action=` name, every window property (`subs.player_filename`, `MoranSubsChooserOpen`, `povil.embedded_*`, `moransubs.*`);
  - every `settings.xml` id (the Wizard reads `use_builtin_engine` and `pov_fast_navigation`; stored values are kept by id).
- **Import direction:** `entry` → `pipeline` → feature packages → `core`.
  - A feature never calls another feature's private helpers.
  - Cross-feature data travels in the contracts of `core/contracts.py`.
  - An import that sits inside a function to break a cycle stays inside a function until the cycle is gone.
- **No shims:** a moved module leaves no alias behind. Every importer is updated in the same step, and the old path is deleted.
- **Moves are mechanical:** code is copied by line ranges, never retyped. AST identity is checked after canonicalizing the import prefix. Behaviour changes are a separate, owner-approved step.

#### 1.7.2 Proposed tree

```
service.subtitles.kodipovilai/
├── default.py                        # PINNED: thin router → resources.lib.entry.plugin.dispatch(argv)
├── service.py                        # PINNED: thin → resources.lib.entry.service.run()
└── resources/
    ├── settings.xml                  # 8 categories with visual comment blocks (§2.4); ids unchanged
    ├── skins/Default/1080i/script.moransubs.chooser.xml   # PINNED file name
    └── lib/
        ├── pipeline.py               # the orchestrator (§1.7.3)
        ├── he_sub_match.py           # PINNED (bare-name import, stdlib-only)
        ├── entry/
        │   ├── plugin.py             # search / manualsearch / download → pipeline
        │   ├── actions.py            # RunScript action table (names unchanged)
        │   ├── service.py            # threads + scheduler (drainers, prune, gp_later runner)
        │   └── migrations.py         # the one-shot _maybe_* migrations as one table
        ├── core/
        │   ├── addon.py              # LEAF (Phase 3): ADDON_ID + the one cached xbmcaddon.Addon; core/kodi and core/settings both rest on it
        │   ├── contracts.py          # VideoContext, Candidate, LinkPayload, Selection, DeliveryResult, SyncVerdict (not wired yet)
        │   ├── link_codec.py         # ex translate._encode_link / _decode_link
        │   ├── video_context.py      # current_video_info + release_name(info, policy) (6 named policies) + looks_like_token (Phase 3, step 3b)
        │   ├── selection.py          # token triple, ownership checks (ex sub_utils)
        │   ├── settings.py           # typed accessors + defaults table (one place)
        │   ├── kodi.py               # log / notify / window props (ex sub_utils)
        │   ├── srt/                  # ONE parser + writer (ex srt.py core, sync_align.parse_srt); post-fix passes by concern
        │   └── cache/                # translation_cache + profile_dir.profile_subdir (the one resolver of the two engine caches); the other caches keep their own eviction (Phase 3 result)
        ├── fetching/
        │   ├── service.py            # search(ctx) -> list[ProviderResult]; download(result) -> path
        │   ├── provider_adapter.py   # ONLY importer of fetching/providers (the adapter boundary)
        │   ├── providers/            # ex subs_engine/: engine.py, sources/*, _libs/* (pysrt, parse_torrent_title)
        │   ├── bridge.py             # ex subs_engine_bridge.py (normalisation, caching, UTF-8) until absorbed
        │   ├── local.py              # ex local_subs.py
        │   ├── embedded/             # embedded_extract, embedded_mp4, mkv_probe, mp4_probe, select_embedded
        │   ├── pool_client.py        # community pool read side (ex pool.py parts + translate harvest)
        │   └── health.py             # provider-down state, timeouts
        ├── scoring_matching/
        │   ├── release.py            # ex release_match.py: score / tier / pct
        │   ├── ranking.py            # ordering rules (Hebrew first, SDH, gender rank, language order)
        │   └── badges.py             # source-screen badge write-through (calls he_sub_match)
        ├── ai_translation/
        │   ├── service.py            # translate(source, ctx, policy, progress) -> TranslationResult
        │   ├── offers.py             # the "translate X to Hebrew" rows of list_candidates
        │   ├── source_prep.py        # HI strip, SDH learn, language check
        │   ├── context/              # prompt.py, cast, gender references (arabic_gender)
        │   ├── dispatch.py           # chunking, parallel dispatch, retries, rate gate, block clock
        │   ├── providers/            # gemini.py, gemini_pair.py, google_translate.py
        │   ├── repairs/              # english_residual, script_residual, gender_guard, he_person_gender
        │   ├── proofread/            # gender_proofread.py, gp_later.py, the deferred jobs
        │   ├── quota.py              # ex gemini_quota.py
        │   ├── reuse.py              # translation cache + pool reuse / contribute (sdh_pool, sdh_registry)
        │   └── background.py         # bg_translate_picker / translate_file handlers
        ├── sync/
        │   ├── service.py            # verify_and_fix(path, ctx, selection) -> SyncVerdict (ex subsync.process)
        │   ├── align/                # sync_align split: estimate, verify, piecewise, regions, retime
        │   ├── oracles.py            # reference subtitles (through fetching.service, not the engine)
        │   ├── probes/               # file timing, embedded cues, audio (audio takes an injected transcriber)
        │   ├── verdicts.py           # verdict cache + community registry
        │   └── jobs.py               # deep-verify queue + drainer
        ├── delivery/
        │   ├── kodi_player.py        # named delivery, registration confirm, next-Hebrew fallback
        │   └── rtl.py                # ONE RTL renderer
        ├── autosub/                  # ex autosub_service.py: player.py (events) + policy.py (gates, choice)
        ├── ui/
        │   ├── listing.py            # Candidate -> ListItem (stars from Candidate.pct, not from the label)
        │   ├── quick_toggles.py      # §2.2
        │   ├── chooser.py            # ex subs_chooser.py (XML name and MoranSubsChooserOpen unchanged)
        │   ├── filename_publisher.py # ex subs_filename_publisher.py (subs.player_filename unchanged)
        │   └── settings_actions/     # gemini pairing, engine test, cache actions
        ├── pyxbmct/                  # bundled, chooser fallback (unchanged)
        └── telemetry.py
```

**Phase 4 additions to the tree (as built):**

```
        ├── ai_translation/
        │   ├── service.py            # resolve() = dispatcher over STAGES (75 lines)
        │   ├── job/                  # state.py (JobState, NEXT, begin) · links/{passthrough,pool,embedded_sync,engine,embedded_ai,engine_ai} · ai/{prepare,cache,handoff,request,reference,plan,calls,dispatch,gender,finish,tuning}
        │   └── context/gender/       # ex gender_reference.py: align, chain, patterns_ar, patterns_he, patterns_other, plan, roles
        ├── fetching/
        │   ├── search/               # list_candidates stages: state, engine, pool, ai_rows, rows (service.py = dispatcher, 40 lines)
        │   └── embedded/extract/     # ex embedded_extract.py: clusters, cue_reference, cues, ebml, extract, extract_http, extract_mp4, http_source, matroska, pace, resume, srt_rows, tracks, tuning
        └── delivery/
            └── download/             # handle_download stages: state (DownloadState), links, deliver
```

`core/kodi.py` holds `make_breather`; `core/srt/cues.py` holds `MIN_CUES`, `STATUS_UNKNOWN`; `core/link_codec.py` holds `decode_link_safe`; `delivery/rtl.py` holds `rtl_text`.

#### 1.7.3 The orchestrator (`resources/lib/pipeline.py`)

```python
# One readable lifecycle. Every step is a feature service behind a contract.
def search(ctx: VideoContext, opts: SearchOptions) -> list[Candidate]:
    raw   = fetching.search(ctx, quick=opts.quick)              # local + embedded + providers + pool
    rows  = scoring_matching.rank(ctx, raw, policy=settings.policy())
    rows += ai_translation.offers(ctx, rows, policy)            # "translate X to Hebrew" rows (no work yet)
    scoring_matching.publish_badges(ctx, rows)                  # side effect made explicit
    return selection.mark_current(rows)

def resolve(link: LinkPayload, ctx: VideoContext, sel: Selection,
            progress: Progress | None = None) -> DeliveryResult:
    src = fetching.acquire(link, ctx, sel)                      # path / embedded stream / pool text
    if src.needs_translation:
        if not sel.is_current(): return DeliveryResult.cancelled()
        src = ai_translation.translate(src, ctx, sel, progress)  # returns a path + metadata
    verdict = sync.verify_and_fix(src, ctx, sel)                # fail-open, bypass for embedded timing
    return delivery.deliver(verdict.path, ctx, sel, followups=[ai_translation.proofread_later])
```

In Phase 2 every feature service starts as a **pure delegation** to the moved function (for example `fetching.search` → `bridge.search` + `local` + `pool_client.lookup`), and `default.py` / `translate.list_candidates` / `resolve` are split along those seams by line ranges. That is a behaviour-neutral structural step. Logic changes (for example one `playing_release` instead of five) are separate, owner-approved steps. **Roadmap (19 Nov 2026):** the lifecycle above needs `resolve` split, so it is built in Phase 4 (step 4d); until then `pipeline.py` is the single route to the whole `list_candidates` and `resolve` (Phase 3 step 3d), and the playing-release unification (Phase 3 step 3b) keeps each caller's result wherever the copies disagree.

#### 1.7.4 Contracts (sketch)

```python
@dataclass(frozen=True)
class VideoContext:   # built once by core.video_context
    imdb_id: str; tmdb_id: str; title: str; year: str
    season: str; episode: str; tvshow: str
    playing_release: str          # the ONE derivation (picked_release > tagline > label > li_filename > filepath)
    file_url: str; is_local: bool; is_live: bool
    raw: dict                     # original sub_utils.current_video_info() for adapters

@dataclass
class Candidate:
    kind: str                     # human_he | mt_he | pool_he | pool_ai | pool_ai_emb | embedded_he | embedded_foreign | foreign | ai_offer | local
    language: str; release: str; provider: str
    pct: int | None; tier: str | None          # numbers, never parsed back from a label
    is_sdh: bool; is_hi: bool; is_cached: bool
    link: LinkPayload             # encoded only at the UI edge
    sync_state: str = ''; sync_label: str = ''

@dataclass
class ProviderResult:             # fetching.provider_adapter normalises engine tuples into this
    provider: str; language: str; filename: str; pct: int; hearing_impaired: bool
    download: dict                # opaque, handed back to the adapter's download()

@dataclass
class SyncVerdict:
    status: str                   # confirmed | fixable | unknown | mismatch | skipped
    path: str; applied: bool; offset_ms: float = 0; scale: float = 1.0; source: str = ''
```

#### 1.7.5 Proposed pipeline (decoupled; the adapter boundary is the providers)

```
 Kodi hooks (PINNED): default.py · service.py · RunScript actions · skin XML · he_sub_match.py · window properties
        │
        ▼
 entry/plugin.py · entry/actions.py · entry/service.py
        │
        ▼
 pipeline.py ── search() ───────────────────────────────┐── resolve() ───────────────────────────────────────┐
        │                                                │                                                    │
        ▼                                                ▼                                                    ▼
 ┌─ fetching ──────────┐   ┌─ scoring_matching ─┐   ┌─ ai_translation ─────┐   ┌─ sync ─────┐   ┌─ delivery ───┐
 │ service · local     │   │ release (1 scorer) │   │ offers · source_prep │   │ align      │   │ kodi_player  │
 │ embedded · pool     │──►│ ranking            │──►│ context · dispatch   │──►│ probes     │──►│ rtl          │
 │ health · bridge     │   │ badges             │   │ repairs · proofread  │   │ verdicts   │   │ registration │
 └─────────┬───────────┘   └────────────────────┘   └──────────────────────┘   └────────────┘   └──────────────┘
           │                       ui/ (listing, quick_toggles, chooser)  ·  autosub/ (player, policy)
           │          core: contracts · link_codec · video_context · selection · settings · kodi · utils · srt · cache
 ══════════╪══════════════════ PROVIDER ADAPTER BOUNDARY (fetching/provider_adapter.py) ═════════════════════════
           ▼
   fetching/providers/  (ex subs_engine: engine.py · sources/{ktuvit, wizdom, opensubtitles, yify, subsource,
                         subscene, bsplayer} · _libs/{pysrt, parse_torrent_title})
   contract: global_var · site_id · get_subs(video_data[, all_lang_override]) · download(download_data, MySubFolder)
```

**Phase 3 note for the pipeline.** Everything outside `fetching/` and `ai_translation/` reaches `list_candidates` and `resolve` only through `resources/lib/pipeline.py` (`pipeline.search(info, modal_progress=True, quick=False)`, `pipeline.resolve(link, info, **kwargs)`); the contract test fails if another module imports `fetching.service` or `ai_translation.service`. `core/addon.py` is the leaf that holds the add-on handle; `core.kodi` and `core.settings` import it and no longer each other. The playing release is derived in ONE place, `core.video_context.release_name(info, policy)`; every caller passes its own named policy (table in Part 4). Every row of `list_candidates` carries `pct` (the number its label shows, `None` when the label shows none) and `ui/listing.py` takes the star count from it.

### 1.8 File-by-file mapping

Legend: **M** move the whole file as-is (imports rewritten) · **S** split by line ranges · **D** delete (Deletion Report) · **P** pinned.

| Current file (lines at 0.3.96) | Phase 2 action | Target |
|---|---|---|
| `default.py` (3,348) | P + S | stays as a ≈50-line router; `_handle_search` → `ui/listing.py`; `_handle_download`, `_deliver_named_subtitle`, `_try_next_hebrew`, `_try_fast_download` → `delivery/kodi_player.py`; `_handle_bg_translate_picker` / `_handle_translate_file` → `ai_translation/background.py`; Gemini pairing (`_PairWindow`, `_gemini_*`), engine test, cache buttons → `ui/settings_actions/*`; the action table → `entry/actions.py` |
| `service.py` (1,323) | P + S | stays thin; threads → `entry/service.py`; the 16 one-shot `_maybe_*` → `entry/migrations.py`; drainers → each feature's `jobs.py` |
| `translate.py` (9,706) | S | `_encode/_decode_link` → `core/link_codec.py`; `list_candidates` → `fetching.service` + `scoring_matching/ranking.py` + `ai_translation/offers.py`; `_finalize` / `_mark_current` → `core/selection.py`; `resolve` → `pipeline.resolve` + `ai_translation/{source_prep,reuse,context,dispatch,repairs,proofread}`; `_gp_*` / `gp_later_tick` → `ai_translation/proofread/`; `_rtl_*` → `delivery/rtl.py`; harvest → `fetching/pool_client.py`; `_extract_embedded_srt` (807) → `fetching/embedded/` |
| `subsync.py` (5,577) | S | `sync/{service,oracles,probes,verdicts,jobs}.py`; the audio probe takes a `transcriber` callable (removes the direct Gemini import) |
| `sync_align.py` (3,374) | S | `sync/align/{estimate,verify,piecewise,regions,retime}.py`; `parse_srt` → `core/srt` |
| `arabic_gender.py` (1,890) | M | `ai_translation/context/gender_reference.py` (its private aligner stays until measured against `sync/align`) |
| `srt.py` (3,292) | S | `core/srt/{parse,write,chunk}.py` + `ai_translation/repairs/postfix_*.py` (≈25 passes grouped: punctuation / RTL, leaks, glyphs, timing) |
| `subs_engine_bridge.py` (1,949) | M then S | `fetching/bridge.py`; stream snapshot → `fetching/embedded/`; RTL → `delivery/rtl.py`; caches → `core/cache` |
| `subs_engine/**` | M | `fetching/providers/**` (absolute imports rewritten; `provider_adapter.py` is the only importer) |
| `embedded_extract.py` (4,984), `embedded_mp4.py`, `mkv_probe.py`, `mp4_probe.py` | M | `fetching/embedded/` (`mkv_probe` / `mp4_probe` shared with `sync/probes` through a contract) |
| `release_match.py` | M | `scoring_matching/release.py` |
| `he_sub_match.py` (1,328) | **P** | stays; may call `scoring_matching` lazily, never at module level |
| `sub_utils.py` (1,375; renamed from `kodi_utils.py` at the end of Phase 1, so it is not confused with POV's `kodi_utils`) | S | `core/{settings,kodi,selection}.py` |
| `autosub_service.py` (1,124) | S | `autosub/{player,policy}.py` |
| `subs_chooser.py` (1,071), `subs_filename_publisher.py` | M | `ui/chooser.py`, `ui/filename_publisher.py` (strings unchanged; update `CLAUDE.md` in the same step) |
| `gemini.py`, `gemini_pair.py`, `google_translate.py`, `gemini_quota.py` | M | `ai_translation/providers/*`, `ai_translation/quota.py` |
| `prompt.py`, `srt_wire.py`, `english_residual.py`, `script_residual.py`, `gender_guard.py`, `gender_proofread.py`, `gp_later.py`, `he_person_gender.py` | M | `ai_translation/{context,dispatch,repairs,proofread}/` |
| `pool.py`, `sdh_pool.py`, `sdh_registry.py`, `telemetry.py` | M / S | `fetching/pool_client.py` (read) + `ai_translation/reuse.py` (contribute); `telemetry.py` |
| `cache.py`, `local_subs.py`, `language_detect.py`, `tmdb_helper.py` | M | `core/cache/`, `fetching/local.py`, `ai_translation/context/` |
| `pyxbmct/` | keep | unchanged |

#### 1.8.1 Phase 2 result per row (19 Nov 2026)

**done** = moved / split as mapped, no shim, AST identity checked · **partial** = moved, but a part stays as it was · **not done** = left in place, with the reason.

| Row (pre-Phase-2 file) | Status | Where it is now / why not |
|---|---|---|
| `default.py` | done | 27-line router; `_handle_search` -> `ui/listing`; `_handle_download` + named delivery + fast chunk + next-Hebrew -> `delivery/kodi_player`; `bg_translate_picker` / `translate_file` -> `ai_translation/background`; Gemini pairing, engine test, cache buttons -> `ui/settings_actions/{gemini,engine,cache}`; `main` / `_parse_query` -> `entry/plugin` (the action table of `entry/actions.py` is NOT built: `main` keeps its if/elif chain) |
| `service.py` | done | 26-line router; threads -> `entry/service`; the 15 `_maybe_*` migrations -> `entry/migrations` as plain functions (not a table; the 3 start hooks stay in `entry/service`); the drainers stay in `entry/service` (they were not moved into each feature's `jobs.py`) |
| `translate.py` | partial | gone. `_encode/_decode_link` -> `core/link_codec`; helpers -> `scoring_matching/{ranking,badges}`, `ai_translation/{policy,progress,source_prep,dispatch/pacing,providers/{google_flow,model_fallback},repairs/leak_rescue,reuse/pool_reuse,embedded_steps,proofread/{gp_state,gp_run,gp_jobs}}`, `delivery/rtl`, `sync/delivery_gate`, `fetching/{pool_harvest,embedded/extraction}`. `list_candidates` (605) -> `fetching/service` and `resolve` (3,650) -> `ai_translation/service` WHOLE: 4 closures with no free variable were extracted verbatim (`_clean`, `_norm_rel`, `_pool_release`, `_accepted_before`); the other 26 closures of `resolve` capture 1-28 variables of the call, so splitting along the link-type branches needs a state object and a golden harness with recorded Gemini / Google replies, which does not exist (deferred to Phase 4 by the owner, 19 Nov 2026). `_mark_current` / `_finalize` went to `scoring_matching/badges` (not `core/selection`: they are list post-processing, and core must not import `he_sub_match`) |
| `subsync.py` | done | `sync/{service,verdicts,oracles,candidates,delivery_record,validators,deep,jobs,common}`, `sync/probes/{remote,audio,cache,bundle}`; the audio probe still imports Gemini directly (an injected `transcriber` would change a signature: reported) |
| `sync_align.py` | done | `sync/align/{common,estimate,verify,regions,piecewise,retime}`; `parse_srt` and the preflight -> `core/srt/cues` |
| `arabic_gender.py` | partial | moved whole to `ai_translation/context/gender_reference` (1,819 lines, over the size goal): it is stdlib-only and loadable on its own by design (its standalone-load fallbacks), so splitting it would add package imports; its private aligner stays (a duplicate of `sync/align`); the split is Phase 4 (owner, 19 Nov 2026) |
| `srt.py` | done | `core/srt/{blocks,timing,classify}`, `ai_translation/repairs/postfix_{rtl,glyphs,cleanup,leaks}` |
| `subs_engine_bridge.py` | done | `fetching/bridge` + `fetching/embedded/streams` + `fetching/{search_cache,subs_cache}` + `delivery/rtl` (the bridge RTL renderer sits beside `translate`'s two: three renderers, not merged) |
| `subs_engine/**` | done | `fetching/providers/**` (the engine's `__init__.py` and `_libs` moved with it; `darksubs_opensubtitles_api.json` is read through `__file__` beside `opensubtitles.py`, which moved with it); `fetching/provider_adapter.py` is the only module outside `providers/` that imports it (all 7 providers expose `global_var`, `site_id`, `get_subs`, `download`) |
| `embedded_extract.py`, `embedded_mp4.py`, `mkv_probe.py`, `mp4_probe.py` | done (moved) | `fetching/embedded/`; `embedded_extract` (4,986 lines) is NOT split: it is stdlib-only and loadable on its own by design (it loads `embedded_mp4` by file path); the split is Phase 4 (owner, 19 Nov 2026, the design stays through Phase 3); `_extract_embedded_srt` came from `translate` -> `fetching/embedded/extraction` |
| `release_match.py` | done | `scoring_matching/release` (still stdlib-only, no package imports: `he_sub_match`'s flat fallback imports it as `scoring_matching.release`) |
| `he_sub_match.py` | P | stays; only its lazy imports changed paths |
| `sub_utils.py` | done | `core/{kodi,settings,selection,video_context}` |
| `autosub_service.py` | done | `autosub/{player,policy}` (`autosub_on_play` is still 799 lines) |
| `subs_chooser.py`, `subs_filename_publisher.py` | done | `ui/chooser`, `ui/filename_publisher` (strings unchanged; `CLAUDE.md` updated) |
| `gemini.py`, `gemini_pair.py`, `google_translate.py`, `gemini_quota.py` | done | `ai_translation/providers/*`, `ai_translation/quota` |
| `prompt.py`, `srt_wire.py`, `english_residual.py`, `script_residual.py`, `gender_guard.py`, `gender_proofread.py`, `gp_later.py`, `he_person_gender.py` | done | `ai_translation/{context,dispatch,repairs,proofread}/` |
| `pool.py`, `sdh_pool.py`, `sdh_registry.py`, `telemetry.py` | done | `fetching/{pool_client,pool_harvest}`, `ai_translation/reuse/{pool_contribute,sdh_pool,sdh_registry}`; `telemetry.py` stays |
| `cache.py`, `local_subs.py`, `language_detect.py`, `tmdb_helper.py` | done | `core/cache/translation_cache`, `fetching/local`, `ai_translation/context/{language_detect,tmdb_helper}` |
| `pyxbmct/` | keep | unchanged |
| `core/utils/`, `ui/quick_toggles.py`, `core/video_context` as the ONE derivation | Phase 3 | `core/utils/` was audited in Phase 3 step 3c and NOT created (nothing qualifies: the two candidates, `breathe` and the timecode helpers, differ in hooks, timers or semantics, see the Phase 3 result in Part 4); quick toggles stay open (owner switch NOT APPROVED); the playing release is `core.video_context.release_name(info, policy)` with 6 named policies |


**Phase 4 update of the rows above (19 Nov 2026).**

| Row | Status after Phase 4 |
|---|---|
| `translate.py` (the `resolve` part) | done: dispatcher + `ai_translation/job/*` (18 modules) |
| `translate.list_candidates` | done: dispatcher + `fetching/search/*` (5 modules) |
| `default._handle_download` | done: `delivery/kodi_player.handle_download` dispatcher + `delivery/download/*` (3 modules) |
| `arabic_gender.py` / `gender_reference.py` | done: `ai_translation/context/gender/*` (7 modules); the private SRT parser and aligner stay (not merged) |
| `embedded_extract.py` | done: `fetching/embedded/extract/*` (14 modules) |
| `he_sub_match.py` | P: the structure is unchanged; `dbg` / `warm_queue_dir` are public (facade NOT DONE) |
| `entry/actions.py` table, `migrations.py` table | done as tables inside `entry/plugin.py` and `entry/migrations.py` (no `entry/actions.py` file exists) |
| `_extract_embedded_srt`, `autosub_on_play`, `_handle_translate_file`, `_handle_bg_translate_picker`, `_extract_cues_http`, `_deep_verify`, `_embedded_aligned_source_srt`, `on_phase` | not done (Part 4, "Phase 4 result", step 4c) |

---

## Part 2: settings UX, quick toggles and missing settings

### 2.1 How the selection screens are built today

| Surface | Built by | What can be added |
|---|---|---|
| **Kodi's native subtitle dialog** (`DialogSubtitles`; FENtastic opens it from "בחר כתוביות", and every skin can open it) | `xbmc.subtitle.module`: `default._handle_search` adds `ListItem(label=language, label2=text)`, `art.icon` = star count, `art.thumb` = flag code, properties `sync` / `hearing_imp`, url `plugin://…/?action=download&link=…`. Kodi owns the window: an add-on **cannot add buttons**, only rows. Clicking a row always calls `action=download`. | Pseudo-rows whose `link` is a UI action. The add-on already knows how to finish a download call with no file and no "download failed" message (`Dialog.Close(all,true)` → empty `addDirectoryItems` → `sleep(100)` → `endOfDirectory(updateListing=True, cacheToDisc=True)`, `default.py:287-291`). |
| **MoranSubs chooser** (other skins) | `subs_chooser._ChooserXML` = `xbmcgui.WindowXMLDialog` over `resources/skins/Default/1080i/script.moransubs.chooser.xml` (list id 100, button 202 "הורדת כתוביות (Kodi)", button 201 "סגור"); fallback `_show_pyxbmct` | Real controls: radio buttons and a settings button; the list can be refilled in place (`_fill` / `_complete` already do async refresh) |
| **Settings screen** | `resources/settings.xml` (v1 format, 56 visible and 49 hidden settings) | §2.4 |

### 2.2 Proposed design (Kodi-native, reliable)

**Rule:** toggles are a **UI concern only**. They are added in `default._handle_search` (or `ui/listing.py` later), **never in `translate.list_candidates`**, so autosub, the chooser data path, the badge cache and `_finalize` never see them.

**A. Native dialog: a "control strip" of rows at the bottom of the list**

| Row (label2, Hebrew) | Shows | Click does | Setting |
|---|---|---|---|
| `⚙ הגדרות MoranSubs` | — | close dialog → `Addon.OpenSettings(service.subtitles.kodipovilai)` | — |
| `סנכרון אוטומטי: [B]פעיל[/B]` / `[B]כבוי[/B]` | state | flip → close → reopen dialog | `subsync_verify` (new UI for an existing key) |
| `תרגום AI: [B]Gemini[/B]` / `Google` / `כבוי` | state | cycle `ai → google → none → ai` | `translation_mode` |
| `תרגום AI כשאין עברית: אוטומטי` / `שאל` / `ידני` | state | cycle | `ai_when_no_hebrew` |
| `מקור מגדר (ערבית): פעיל` / `כבוי` | state | flip | `gender_ref_arabic` |
| `מאגר קהילתי: פעיל` / `כבוי` | state | flip | `pool_use` |

Implementation outline (about 60 lines; in Phase 3 it lives in `ui/quick_toggles.py` + `ui/listing.py`, the dispatch in `entry/plugin.py`):

1. A new link type `{'type': 'ui', 'op': 'toggle', 'key': 'subsync_verify'}` (or `'op': 'settings'`). Rows get `art.icon='0'` (no stars), `thumb=''` (no flag) and a `[COLOR grey]` prefix so they read as controls.
2. In `_handle_download`, check `type == 'ui'` first, before `subsync.cancel_pending()`, `set_current_subtitle` and the selection snapshot, so a toggle never cancels the subtitle that is playing.
3. Apply it through `sub_utils.set_setting`. Run the proven close sequence. Then `AlarmClock(moransubs_reopen,ActivateWindow(SubtitleSearch),00:00:01,silent)` reopens the dialog after Kodi has finished tearing it down, which runs a fresh `action=search` with the new state. For `op=settings`: `Addon.OpenSettings(...)` after the close, with no reopen.
4. Gate the strip with a new setting `show_quick_toggles` (default on), plus a per-row list so a skin designer can hide it.
5. Labels come from `strings.po` (new ids in the 33xxx range) and **never contain `%`**, because the star regex would read it.

**B. MoranSubs chooser: a toggle panel**

- Add to `script.moransubs.chooser.xml` (same file name; add controls only) a vertical group above buttons 202 / 201: `radiobutton` ids 210-214 (sync, AI, ask-AI, gender reference, pool) and a `button` id 203 "⚙ הגדרות". Hebrew labels, same textures as 201 / 202, `onleft=100` navigation.
- In `_ChooserXML.onInit`, set each radio button's `setSelected(state)`. In `onClick(210..214)`, flip the setting, update the control, then `self._fill()` (quick list) followed by the existing `_complete()` background refresh, so the list changes in place with no close / reopen. `203` → `self.close()` → `Addon.OpenSettings(...)`.
- pyxbmct fallback: the same radio buttons through `pyxbmct.RadioButton`.

**C. Which toggles actually change the list (so the refresh is meaningful)**

| Toggle | Effect on the list | Effect on delivery |
|---|---|---|
| `translation_mode` | `engine_ai` / `embedded_ai` / local AI rows appear or disappear; foreign subs are shown raw when `none` | translator choice |
| `pool_use` | pool rows appear or disappear | reuse |
| `use_builtin_engine` | the whole provider section | — (not offered as a quick toggle: it is the build's master switch and the Wizard reads it) |
| `subsync_verify` | the sync label on "» נוכחית" | retiming on the next pick |
| `gender_ref_arabic`, `ai_when_no_hebrew` | none | the next AI job / next autosub |

**Risks to check in Kodi:**
- The reopen through `AlarmClock` on FENtastic / AF3 `DialogSubtitles` variants.
- That the cached `xbmcaddon.Addon` instance (`core.addon.addon()`) sees a value another process wrote (service thread vs plugin invocation). The code comment says it does; a toggle depends on it.
- That a pseudo-row never wins Kodi's own "first result" auto-select. Kodi does not auto-pick from this list, but autosub must keep using `list_candidates`.

### 2.3 Missing settings audit

#### 2.3.1 Read in code but not declared (they work, but are invisible and unprotected)

| Key | Read at | Default in code | Proposed |
|---|---|---|---|
| `subsync_verify` | `subsync.py:216` | `true` | **Sync Engine › master switch** (visible) |
| `subsync_audio` | `subsync.py:744` | `true` | Sync › "use AI audio probe (uses Gemini quota)" |
| `subsync_probe` | `subsync.py:462` | `true` | Sync › "read timing from the playing file" |
| `subsync_autorank` | `subsync.py:1250,1469` | `true` | Sync › "rank automatic choice by verified timing" |
| `gemini_rpm` | `translate.py:5531` | `0` (= per-model cap) | AI › Performance (expert) |
| `prev_context_lines` | `translate.py` | code default | AI › Performance (expert) |
| `he_match_ktuvit` | `he_sub_match.py:785` | `true` | Matching › "badge may ask Ktuvit when nothing else has Hebrew" |
| `he_embedded_report` | `pool.py:805` | `true` | Pool › "report built-in Hebrew tracks" |
| `telegram_first_and_last_name` | (the Telegram source, deleted at the end of Phase 1) | — | removed |
| `locale.subtitlelanguage`, `subtitles.languages` | `service.py` | Kodi GUI settings | not ours (correct as is) |

#### 2.3.2 Declared but dead (see §1.6)

`keep_darksubs` and `force_ai_when_auto_translate_off` (both **visible**), plus 12 hidden markers. The ids must be kept or retired deliberately: the values stay in users' `settings.xml` files. Deleting the declaration only hides them, which is harmless.

#### 2.3.3 Hardcoded behaviour without a setting (selected; "Expose" = visible, "Expert" = level 3, "Const" = keep in code but move to one constants table)

| Feature | Value | Where | Proposal |
|---|---|---|---|
| Search | release wait 15×200 ms after auto-advance | `default.py:156` | Const |
| Search | `skip_autosub` freshness 90 s | `default.py:121`, `autosub_service` | Const |
| Providers | provider-down skip 600 s, strike window 1,800 s | `subs_engine/engine.py:332-333` | Expert: "skip an unreachable provider for N minutes" |
| Providers | search-result cache 24 h; recent-search reuse 120 s; cached downloads 200 files | `subs_engine_bridge.py:469,501,1350` | Expert (`engine_cache_hours`); Const |
| Providers | Ktuvit harvest 3 per pass, 6 s throttle; pool harvest 8 tries; queue max age 14 d | `translate.py:865-866`, `pool.py` | Const |
| Providers | yify User-Agent / Referer, Ktuvit poll 100 ms | sources | Const |
| Matching | release tiers and ceilings (`_CEIL_EDITION` 25, `_CEIL_SOURCE` 35, `_CEIL_PROPER` 40, `_CEIL_SAME_SOURCE` 79, `_CEIL_FUZZY` 65) | `release_match.py` | Const (they are the algorithm). Expose only a **"hide subtitles below N %"** filter (new, default 0) |
| Matching | star mapping `round(pct/20)` clamped 1-5 | `default.py:182` | Const |
| Matching | language order en, es, de, fr, pt; SDH first; gender rank of sources | `translate.py:1190`, `:361` | Expose "AI source language order" (already partly via `src_*`) |
| Matching | badge TTLs 7 d / 24 h, 120 names, timeouts 2.5 / 3.5 s | `he_sub_match.py` | Const |
| AI | Gemini request timeout 90 s | `gemini.py` | Expert |
| AI | free-tier RPM caps 14 (Lite) / 4 (Flash) | `translate.py:201-215` | covered by `gemini_rpm` |
| AI | chunk budgets (2 min from the first block, 10 min, 20 requests + 1 per 4 lines, 3 s per line past 200) | `resolve()` closures | Const |
| AI | proofread models `gemini-3.7-flash`, `3.8-flash`, `3.6-flash` | `gender_proofread.py:34` | Expert: "proofread model" (select) |
| AI | proofread retry 6 h, spent 7 d, busy rounds 3 × 2 / 4 / 8 min, job budget 30 min | `translate.py:3165-3311` | Const |
| AI | gender reference chain `he, ar, hi, es, ru, pt, pl, uk, fr, it…`; 10 per language; 50 downloads; 60 s deadline; 20 s provider wait | `arabic_gender.py` | Expose "reference languages" (multi-select or order); rest Const |
| AI | model bump migrations (`_maybe_bump_gemini_model*`, `_maybe_tune_gemini3_defaults`) | `service.py:742-900` | Const; the model list in `settings.xml` is the source |
| AI | Google fallback when Gemini fails | `translate.py` (`_GOOGLE_FALLBACK_WHY`) | Expose "when Gemini fails: Google / leave English" |
| Sync | `MIN_VOTE` 0.65, `MIN_OVERLAP` 0.80, `CONFIRM_OFFSET_MS` 350, `MAX_PLAUSIBLE_OFFSET_MS` 240 s, scale range | `sync_align.py` | Const (measured); Expert: "maximum automatic shift (seconds)" |
| Sync | community auto-apply max offset 6 s; 400 verdicts; probe cache 60 entries; negative probe TTL 6 h | `subsync.py` | Expert: "trust community timing up to N s" |
| Sync | audio probe positions, `_AUDIO_MAX_OFFSET_MS` 180 s | `subsync.py` | Const |
| Embedded | head 2 MiB, max 80 MiB, deadline 30 s, HTTP 15 s, 429 retries 5 | `embedded_extract.py` | Expert: "max bytes read from a stream" |
| Autosub | ask-AI auto-close 10 s; live detection grace 13 s | `autosub_service.py:29` | Expose "ask timeout (s)" |
| Pool | GET 8 s, POST 25 s, lookup TTL 90 s, min 15 entries, min Hebrew ratio 0.5 | `pool.py` | Const |
| Cache | prune interval 24 h; 80% low-water mark | `service.py`, `cache.py` | Const |
| Display | all Hebrew row labels ("כתובית · מאגר", "תרגום AI…", "» נוכחית") | `translate.py`, `subs_chooser.py` | move to `strings.po` (not a setting) |
| Debug | engine debug log follows hidden `show_debug` | `subs_engine/log.py` | map to `log_level=DEBUG` |

### 2.4 Proposed `settings.xml` structure (ids kept; **NEW** = new id)

Kodi reads `level` 0 = basic, 1 = standard, 2 = advanced, 3 = expert, 4 = internal (hidden). Every existing id keeps its name and type, so stored values and the Wizard's two reads keep working.

```
1. כללי / General                                   (category id="general")
   ├─ use_builtin_engine            bool  L0  true   "Built-in sources engine" (Wizard reads it)
   ├─ engine_autosub                bool  L0  true   "Pick a subtitle automatically on play"
   ├─ ai_when_no_hebrew             enum  L0  auto   auto | ask | manual
   ├─ autosub_ask_timeout      NEW  int   L2  10     seconds
   ├─ autosub_excluded_addons       str   L2  plugin.video.idanplus
   ├─ subs_notifications            bool  L0  true
   └─ show_quick_toggles       NEW  bool  L1  true   rows / panel of §2.2

2. ספקים ואיתור / Providers & Fetching              (category id="engine", kept)
   ├─ group sources: ktuvit, wizdom, opensubtitles, yify, subsource, subscene, bsplayer (bool L0)
   ├─ max_search_time               int   L1  10
   ├─ provider_down_minutes    NEW  int   L3  10
   ├─ engine_cache_hours       NEW  int   L3  24
   ├─ embedded_translation_mode     enum  L2  auto   (moved here from Advanced)
   ├─ embedded_max_mb          NEW  int   L3  80
   ├─ actions: engine_test
   └─ group engine_internal (L4, unchanged ids: language_*, all_lang, OS*, show_debug, _engine_defaults_v …)

3. התאמה ותצוגה / Matching & Display                (category id="matching" NEW)
   ├─ show_subtitle_match           bool  L0  true   (moved from Advanced; % on POV sources)
   ├─ min_match_pct            NEW  int   L2  0      hide human subtitles below this %
   ├─ he_match_ktuvit          NEW* bool  L3  true   (*declares an existing key)
   ├─ skip_if_hebrew                bool  L0  true   "Do not offer AI when Hebrew exists"
   └─ rtl_punct_mode                enum  L1  rtl_base (moved from Translation)

4. תרגום AI / AI Translation                         (categories "api" + "translation", merged into one category with groups)
   ├─ group key:      info_key, qr_pair, api_key, test_connection, show_gemini_usage
   ├─ group engine:   translation_mode (ai|google|none) L0 · model L0 · ai_paid_mode L1
   ├─ group sources:  src_english … src_portuguese (bool L0)
   ├─ group gender:   gender_ref_arabic L0 · gender_ref_languages NEW (L2, ordered list, default he,ar,es,ru)
   │                  · gender_proofread L0 · proofread_model NEW (L3: auto|gemini-3.7-flash|…)
   ├─ group fallback: gemini_fail_fallback NEW (L2: google|keep_source)
   ├─ group perf (L3): whole_subtitle_request · chunk_lines · parallel_chunks · fast_first_chunk
   │                   · gemini_rpm NEW* · prev_context_lines NEW* · gemini_timeout NEW (90)
   ├─ group sampling (L3): temperature · top_p · thinking_budget
   └─ group tmdb: info_tmdb_notice

5. סנכרון / Sync Engine                              (category id="sync" NEW)
   ├─ subsync_verify           NEW* bool  L0  true   master switch
   ├─ subsync_probe            NEW* bool  L1  true   timing from the playing file
   ├─ subsync_audio            NEW* bool  L2  true   AI audio probe (uses Gemini quota)
   ├─ subsync_autorank         NEW* bool  L2  true   automatic choice prefers verified timing
   ├─ subsync_max_shift_s      NEW  int   L3  240
   └─ subsync_community_max_s  NEW  int   L3  6

6. מאגר קהילתי / Community Pool                      (category id="pool", kept)
   ├─ pool_use · pool_share (bool L0) · pool_share_cache (action)
   └─ he_embedded_report       NEW* bool  L2  true

7. מטמון / Cache                                      (category id="cache", kept)
   └─ cache_size_mb · cache_ttl_days · clear_cache · purge_temp

8. מתקדם / Advanced & Debug                          (category id="advanced", kept)
   ├─ log_level L1 · pov_fast_navigation L3 (Wizard reads it: keep id and level)
   └─ group markers (L4): every `_…` one-shot marker (unchanged ids); remove the dead ones only with approval
```

Example of one new group (Kodi settings v1 format, matching the file's existing style):

```xml
<category id="sync" label="33100"> <!-- סנכרון -->
  <group id="sync_main">
    <setting id="subsync_verify" type="boolean" label="33101" help="33102">
      <level>0</level>
      <default>true</default>
      <control type="toggle"/>
    </setting>
    <setting id="subsync_audio" type="boolean" label="33103" help="33104">
      <level>2</level>
      <default>true</default>
      <dependencies>
        <dependency type="enable" setting="subsync_verify">true</dependency>
      </dependencies>
      <control type="toggle"/>
    </setting>
  </group>
</category>
```

**Caveats:**
- Declaring a key that code already reads changes nothing for users: the default stays `true`, the same as the code default.
- Moving a setting between categories keeps its stored value, because Kodi stores values by id.
- The reorganization is approved (Phase 0). Each category gets a visual comment block, for example `<!-- ========= SYNC ========= -->`.

---

## Part 3: upstream (closed in Phase 0)

- **History.** MoranSubs commits were replayed onto `update-migration` and merged into `main`. The last merge (2026-11-19) went from `77880b8` (0.3.74) to `96efe59a` (0.3.96, 36 commits). The DarkSubs engine was vendored into `subs_engine/` at `f91dccbb` (2026-06-21). That upstream no longer exists.
- **How the last merge was resolved:**
  - **Taken:** the subtitle core from 0.3.75 to 0.3.96 (`translate`, `subsync`, `sync_align`, `embedded_extract`, `arabic_gender`, `gemini`, `gender_proofread`, `subs_engine_bridge`, the new `embedded_mp4.py` and `gp_later.py`, the gp_later runner and the `_maybe_retire_gemini_31_flash` migration in `service.py`); FENtastic 1.0.34; Estuary 4.0.9; the `script.fentastic.helper` lazy import.
  - **Our side of every conflict:** 8 hunks in `service.py` (2,587 upstream lines not taken) and 3 in `settings.xml` (92 lines). All were blocks we had moved to the Wizard: host repairs, DarkSubs, POV / Umbrella / Idan Plus / skin patchers, and their markers.
  - **Reverted to ours:** 4 Wizard `host_repairs` files that git's rename detection had patched, and the whole Onyx skin.
  - **Dropped:** `af3_osd_tap_close_patcher.py`, and the `povil.tr.file` trailer guard in `autosub_service.py`.
  - `CLAUDE.md` "SKIPPED UPSTREAM FEATURES" lists all of these for a future decision.
- **From now on:** no upstream remote, no sync script, no PR watcher. The contract smoke test of the old §3.3 survives as our own regression check (Phase 3, step 3d).

## Part 4: execution plan (decoupled)

Each phase is split into steps. After each step the agent stops, writes `output.txt`, gives a commit snippet, and the owner reviews and commits (`CLAUDE.md` WORKFLOW). Every phase ends by updating Part 5 (Deletion Report) and Part 6 (Refactoring Statistics).

| Phase | Goal | Steps | Behaviour change | Exit criteria |
|---|---|---|---|---|
| **1. Cleanup, settings and Deletion Report** | A smaller, honest codebase and a settings screen that matches the code | 1a baseline snapshot and stub harness (scratchpad) · 1b dead-code deletion (§1.6, plus whatever a fresh vulture / pyflakes / caller scan proves dead) · 1c `settings.xml` reorganized into the 8 categories of §2.4 with comment blocks; the 9 read-but-undeclared keys declared with their code defaults; dead settings removed; `strings.po` en / he · 1d Deletion Report and statistics | Only the approved deletions and the visible settings screen | import baseline unchanged (except deleted modules); no new pyflakes message; every remaining `get_setting` key is declared; Part 5 and Part 6 filled in |
| **2. Core refactoring and splitting** | The §1.7.2 tree; no file over ≈1,500 lines and no function over ≈300 lines except where reported | 2a `core/` (contracts, link_codec, settings / kodi / selection out of `sub_utils`, `srt`, `cache`) · 2b `fetching/` (`subs_engine` → `fetching/providers`, bridge, embedded, local, pool_client, `provider_adapter`) · 2c `scoring_matching/` · 2d `sync/` (split `subsync`, `sync_align`) · 2e `ai_translation/` without `resolve` · 2f `translate.list_candidates` and `resolve` split (the riskiest step, last) · 2g `ui/`, `delivery/`, `autosub/`, `entry/` + `migrations.py`; `default.py` / `service.py` become thin | None (moves by line ranges, AST identity) | per step: AST identity of every moved def, import baseline, pyflakes, a repo-wide caller grep, no shim; `translate.py` deleted at the end |
| **3. Hygiene, deduplication, wiring and tests** (DONE 19 Nov 2026, 3f not approved) | The core is honest, the duplicate derivations are gone, one route reaches the two big functions, a safety net stays in the repo | 3.0 preparation (snapshot, sha256 of `he_sub_match.py`, import lists of the standalone modules, the missing `list_candidates` ordering golden case) · 3a core hygiene: break the `kodi` <-> `settings` cycle (leaf `core/addon.py`), make every cross-module private name public (about 319 names in 63 modules; the frozen `pool_client._lookup_raw` stays), record the accepted `sa` change · 3b the playing release, ONE derivation (5 copies plus the path / token helpers), per-caller results kept and every divergence reported · 3c other deduplication in order (RTL renderers, SRT parsers, caches, `_decode_link`, `_breathe`) only where provably identical, then the `core/utils/` audit · 3d `pipeline.py` as the single route (no lifecycle yet) · 3e the match % as a number (the label regex deleted) · 3f quick toggles (§2.2; owner switch, default NOT APPROVED) · 3g the committed contract test + the pure-function golden harness + CI · 3h the Kodi checklist · 3i statistics, docs and the Phase 4 prompt | None, except 3f (and 3e, which must not be visible) | contract test green in CI; the verification kit identical to the baseline (138 of 138 imports, 311+ golden cases, 26 + 19 scenarios); `he_sub_match.py` byte-identical; the module-level imports of `embedded_extract`, `gender_reference` and `release` unchanged; no cross-module private import; Part 6 has the Phase 3 row |
| **4. Deep splits and cross-add-on integrations** (PARTIAL 19 Nov 2026, see "Phase 4 result") | The functions and files Phase 2 left whole, the lifecycle of §1.7.3, the stdlib-only modules, `he_sub_match.py` and the integrations outside the add-on | 4a the golden harness for `resolve` / `list_candidates` (recorded Gemini / Google conversations, mocked network and clock; it must fail on a mutation) · 4b split `resolve` (state object, stages of §1.5.3) · 4c the other functions over 300 lines · 4d the lifecycle of §1.7.3 in `pipeline.py` with the contracts, the action table and the migrations table · 4e `embedded_extract` and `gender_reference` (the single-file standalone constraint is dropped; decided 19 Nov 2026) · 4f `he_sub_match.py` as a thin facade (exact public API, logic delegated to `scoring_matching/` and `fetching/`) and the Wizard / skin / POV / Umbrella references (owner switch, each a separate labelled change) · 4g the TOTAL statistics | None (structural), 4f touches other add-ons only with approval | the 4a harness identical before and after every split; no function over about 300 lines except reported; Part 6 has the Phase 4 and TOTAL rows |

**Phase 2 result (19 Nov 2026).** 2a, 2b, 2c, 2d, 2e and 2g are DONE; 2f is PARTLY done. Details per file are in §1.8.1. What was not done, and why:
- `resolve` was not split along its link-type branches (3,650 lines in `ai_translation/service.py`), nor `list_candidates` (605), `_extract_embedded_srt` (807), `autosub_on_play` (799), `_handle_translate_file` (690), `_handle_bg_translate_picker` (458), `_extract_cues_http` (395), `_deep_verify` (379), `_embedded_aligned_source_srt` (343), `_handle_download` (343) and the nested `on_phase` (314). Their closures capture the mutable state of the call; the only free-variable-free closures (4 in `translate`, 1 in `embedded_extract`) were extracted. The golden harness of this phase (311 cases on pure functions, the selection state machine, `sync_align` fixture pairs and 19 entry-point actions with 1,061 recorded Kodi calls for a search) cannot show identical output for a recorded Gemini / Google conversation, which is what the failure policy asks for before touching `resolve`.
- Files over 1,500 lines: `embedded_extract.py` 4,986 and `gender_reference.py` 1,819 (both stdlib-only by design) and `ai_translation/service.py` 3,757 (`resolve`).
- `core/utils/`, the `entry/actions.py` table and a table form of `migrations.py`: not built (nothing to put in `utils`; the other two would rewrite control flow).
- Not part of the mapping but changed: nothing about behaviour (see `CLAUDE.md` for the few signature edits).

**Roadmap after Phase 2 (owner, 19 Nov 2026).** Everything listed above as not done is Phase 4 (see the decision record at the top and the Phase 4 plan in `PHASE_2_AND_3_PROMPTS.md`), except `core/utils/` (audited in Phase 3 step 3c) and the two tables of `entry/` (Phase 4 step 4d).

**Phase 3 result (19 Nov 2026; one autonomous session in two parts).** 3.0, 3a, 3b, 3c (partly, by design), 3d, 3e, 3g, 3h and 3i are DONE; 3f is skipped (owner switch NOT APPROVED, the design of §2.2 stays the one open feature). Verified after every step with the Phase 2 kit plus the new harness: stub import 140 of 140 (`core/addon.py` and `core/cache/profile_dir.py` are new), `compile()` with warnings as errors, pyflakes 4 (unchanged), the cross-module API scan clean, the AST identity of every moved or renamed definition, 1,188 golden cases (311 of Phase 2 + 29 `list_candidates` ordering scenarios + the playing-release matrix and its token / path cases + star scenarios), 26 Gemini-helper and 19 entry scenarios identical, `he_sub_match.py` byte-identical (sha256 in the fixtures) and the module-level imports of `embedded_extract`, `gender_reference` and `release` unchanged. The safety net now stays in the repo: `.github/scripts/check_subs_contract.py` + `.github/scripts/subs_contract/` + `.github/workflows/subs-contract.yml` (runs on `windows-latest`: the golden expectations were recorded with `ntpath`).

- **3a (core hygiene).** (i) New leaf `core/addon.py`; `core.kodi.log` / `notify` read `settings.get_setting` through a module-level import, so no function-level import of this add-on remains in `core/kodi.py` or `core/settings.py` and the top-level graph of `core/` is acyclic. The cached `xbmcaddon.Addon` semantics are unchanged (one instance per process, Appendix B item 2). (ii) 316 top-level names that another module imported or read lost their leading underscore, in 62 modules (e.g. `link_codec.encode_link`, `kodi.home_window`, `gp_state.*`). 8 names collided with an existing name and got a qualified one: `cues_median`, `dialogs_title`, `pool_client_params`, `piecewise_local_spread`, `piecewise_local_windows`, `validators_maps_agree`, `gender_reference_hpg`, `gender_proofread_today`. Still private, on purpose: `fetching/pool_client._lookup_raw` (frozen, read by `he_sub_match`) and `_dbg` / `_warm_queue_dir` of `he_sub_match.py` (read by `entry/service.py`; the file is byte-identical until Phase 4). The only rebound global (`ai_translation/progress.QUIET`) is read through the module. (iii) ACCEPTED: Phase 2 removed the `sa` (the `sync_align` module) argument from `_windowed_built`, `_windowed_plan` and `_gate_windows` of `gender_reference`; the helpers import what they use. Nothing else changed in that file.
- **3b (the playing release).** Six copies (the five of the plan plus the timing / pool fallback loop in `ai_translation.service.resolve`) became `core.video_context.release_name(info, policy)` and one token test `looks_like_token(s, blank=False)`. The copies DISAGREE, so every policy returns exactly what its caller returned before (owner decision: keep all six policies; consolidating them is a later, deliberate change). 120 fixtures were recorded before the change and are identical after it (`pr.*` golden cases).

| Policy | Caller | Rule |
|---|---|---|
| `picker` | `fetching.service.list_candidates` (the match % of the picker, the pool rows, the same-source gate) | first TRUTHY of picked_release, tagline, label (unstripped), then `li_filename` as a release name (basename without extension, '' for a token), `basename(filepath)`, title |
| `chooser` | `ui.chooser` (window header) | first truthy of picked_release, tagline, label, title |
| `sync` | `sync.service.playing_release` (the sync gate; its season-pack completion and synthetic-name guard stay there) | first of picked_release, tagline, label, `basename(filepath)`, title that is truthy and does not name another episode, stripped (`basename(filepath)` is evaluated even when an earlier value wins) |
| `scored` | `fetching.bridge.build_video_data`, `fetching.search_cache` | every candidate (picked_release unless it names another episode, basename of filepath and li_filename WITH extension, label, tagline, title; other-episode names dropped) is scored by `_release_score`, the best wins, ties go to the longer string |
| `pool` | `pool_contribute`, `embedded.streams` (source-screen reports) | first stripped, non-token value of release, filename, picked_release, tagline, label, then `li_filename`, `filepath` as release names |
| `source` | `ai_translation.service.resolve` (timing / pool fallback) | first stripped non-empty value of release, picked_release, filename, tagline, label (no token test, the caller filters afterwards) |

  The six agree on 37 of the 120 fixtures and disagree on 83. Pairs (fixtures that differ): picker  vs chooser differ on 22 fixtures; e.g. ep:filepath_wrong, ep:li_wrong_filepath_ok, extonly_filepath+title; picker  vs sync   differ on 44 fixtures; e.g. ep:all_wrong, ep:filepath_wrong, ep:label_year_ep; picker  vs scored  differ on 37 fixtures; e.g. ep:all_wrong, ep:filepath_wrong, ep:label_year_ep; picker  vs pool   differ on 53 fixtures; e.g. all_real, ep:filepath_wrong, extonly_filepath+title; picker  vs source  differ on 56 fixtures; e.g. all_real, ep:filepath_wrong, ep:li_wrong_filepath_ok; chooser vs sync   differ on 41 fixtures; e.g. ep:all_wrong, ep:filepath_wrong, ep:label_year_ep; chooser vs scored  differ on 43 fixtures; e.g. ep:all_wrong, ep:label_year_ep, ep:li_wrong_filepath_ok; chooser vs pool   differ on 56 fixtures; e.g. all_real, ep:filepath_wrong, ep:li_wrong_filepath_ok; chooser vs source  differ on 39 fixtures; e.g. all_real, ep:filepath_wrong, extonly_filepath+title; sync   vs scored  differ on 35 fixtures; e.g. ep:all_wrong, ep:filepath_wrong, ep:picked_pack; sync   vs pool   differ on 49 fixtures; e.g. all_real, ep:all_wrong, ep:filepath_wrong; sync   vs source  differ on 51 fixtures; e.g. all_real, ep:all_wrong, ep:label_year_ep; scored  vs pool   differ on 61 fixtures; e.g. all_real, ep:all_wrong, ep:filepath_wrong; scored  vs source  differ on 62 fixtures; e.g. all_real, ep:all_wrong, ep:filepath_wrong; pool   vs source  differ on 33 fixtures; e.g. ep:filepath_wrong, ep:li_wrong_filepath_ok, filename=hash. Examples worth a decision: (1) picked_release naming ANOTHER episode (`ep:picked_wrong`): `picker`, `chooser`, `pool` and `source` keep it, `sync` and `scored` skip it, so the picker's match % can be computed against the wrong episode's release while the sync gate and the engine query use the right one. (2) picked_release / label that is a debrid URL: `picker`, `chooser`, `scored` and `source` return the URL, `sync` and `pool` return ''. (3) `release` only: `pool` and `source` use it, the other four ignore it. (4) filepath `/m/Movie.2020.1080p-GRP.mkv`: `scored`, `sync` and `picker` return the basename WITH `.mkv`, `pool` without. (5) a whitespace-only picked_release: `picker` and `chooser` return it unstripped, the others ''. Not reachable in the installed add-on and therefore removed: the light token fallback of the old `ranking.release_from_path` when `pool_client` could not be imported (`pool_client` only imports `core`).
- **3c (other deduplication; owner-confirmed 19 Nov 2026: merged only what is provably identical).** MERGED: the timecode-pair regex fragment (`core/srt/blocks.TC_PAIR`, used by `TIMECODE_RE`, `_TIME_PAIR_RE` and `cues.TIME_RE`; 72 regex cases identical) and `core/cache/profile_dir.profile_subdir` (the two engine caches; same path and failure behaviour). KEPT SIDE BY SIDE, with the reason:

| Duplicate | Why it is not one function | Phase 4 pattern |
|---|---|---|
| `to_ms` (`core/srt/cues`) vs `_tc_ms` (`core/srt/blocks`) | `to_ms` reads the fraction digits as a fraction (`"1,5"` -> 1500 ms), `_tc_ms` as plain milliseconds (`"1,5"` -> 1005 ms). One of them is probably a bug for 1-2 digit fractions (reported, not fixed) | owner decides which reading is right, then one function |
| `ms_to_stamp` vs `fmt_tc` | `ms_to_stamp` rounds (`int(round(ms))`), `fmt_tc` truncates: they differ for non-integer milliseconds | same |
| `sync/oracles.decode_link` vs `core/link_codec.decode_link` | `except Exception` vs `(ValueError, TypeError)`: a dict / list link gives `None` in one and `AttributeError` in the other; JSON nested beyond the recursion limit gives `None` vs `RecursionError` | core function + the broad `except Exception` wrapper in `oracles` |
| the 3 RTL renderers of `delivery/rtl.py` | the common part is already one function (`postfix_rtl.fix_rtl_punctuation`); they differ in the input gate (`.srt` / `.vtt` only vs any file), decoding (strict `utf-8-sig` vs `errors='replace'`), the extra passes (glyph normalisation, cue-duration clamp, Arabic / leak strips gated by provenance), the destination (`<name>.povil-rtl.<ext>`, in place, `cache/local_<sha1>.he.srt`) and the write semantics (`newline=''` + fsync, `.aitmp`, no cleanup) | one pure text core + 3 I/O wrappers |
| `_breathe` in `sync/align/estimate` (with a `YIELD` hook), `sync/deep` (`_yield_to_viewer()`), `fetching/embedded/mp4_probe`, `embedded_extract` | the 1 ms nap every 20 ms is the same, the hooks and the per-module timers are not | one factory / function in `core/kodi.py` |
| cache eviction (`subs_cache` wipe-all cap, `translation_cache` TTL + LRU to 80 %, `search_cache` / probe / verdict TTLs) | different policies and constants | none planned |
| other timecode regexes (`language_detect`, `srt_wire`, `english_residual`, `script_residual`, `gender_reference._TIME_RE`) | different anchors or strict 3-digit milliseconds | the SRT core of Phase 4 |

  `core/utils/` was audited and is NOT created (and is removed from §1.7.2, §1.8 and `CLAUDE.md`): the only candidates are the table above. Layering note (owner decision 3): `core/srt/cues.py` imports `sync.align.common` (`MIN_CUES`, `STATUS_UNKNOWN`) and, inside functions, `sync.align.estimate.breathe`. Moving them into `core/` with zero behaviour change is NOT possible: `breathe` carries the estimate module's own timer and its `YIELD` hook, and moving the two constants would need a re-export from `sync.align.common` (a shim). Left for Phase 4 (unified `breathe`).
- **3d (single route).** `pipeline.search` / `pipeline.resolve` (one line each, exact signatures) are the only way `ai_translation/background`, `autosub/player`, `delivery/kodi_player`, `sync/jobs`, `ui/chooser` and `ui/listing` reach the two big functions. The chooser's `translate` argument (a module passed through `_row_item`, `_classify`, `_chooser_items` and the window) is gone; `_row_item(c, info, xbmcgui)`, `_classify(c, info)`, `_chooser_items(info, quick=False)`. The lifecycle of §1.7.3 waits for the `resolve` split.
- **3e (the match % as a number).** Every `list_candidates` row carries `pct`; `ui/listing.py` takes the star count from it and the regex that read `(\d{1,3})%` back out of the Hebrew label is deleted. Stars are identical on all 29 scenarios (the harness computes them both ways). Known residual: `ui/chooser._classify` still reads the number from the label (display and tint; changing it was outside the step), and a label with no percentage that contains `<digits>%` inside a release name used to give stars through the regex and now falls back to the row rating (not reachable in the scenarios).
- **3f (quick toggles).** Not implemented (owner switch NOT APPROVED); no `show_quick_toggles` setting and no new labels.

**Unified Core + Thin Wrappers (the pattern for the remaining duplicates; owner decision 19 Nov 2026, Phase 4).** Where copies share a core but differ in I/O or policy, extract ONE pure function that owns the shared transformation and keep thin wrappers that own only what differs; the wrappers pass explicit named options, never copies of the logic, and each wrapper keeps its exact result (a golden matrix per wrapper on identical inputs, recorded BEFORE the extraction). Applications: (1) RTL: a single pure text-transformation core in `delivery/rtl.py` (text in, text out: glyph normalisation, `fix_rtl_punctuation`, optional clamp, optional provenance-gated strips) behind the 3 delivery / file wrappers (`render_hebrew_rtl_copy`, `rtl_delivery_copy`, `reapply_rtl_fix_in_place`), which keep their gates, decoding, destinations and write semantics; (2) `_breathe`: one `breathe` in `core/kodi.py` (a factory or an optional hook, each caller keeping its own timer unless a measurement shows one shared timer is equivalent) once `embedded_extract.py` is unlocked in Phase 4, which also removes the `core/srt/cues` -> `sync.align` inversion; (3) `gender_reference`'s private SRT parser and aligner merge into `core/srt/` and `sync/align/` once its single-file constraint is dropped, with `to_ms` / `_tc_ms` and `ms_to_stamp` / `fmt_tc` decided by the owner first; (4) `decode_link`: the core function plus the broad-except wrapper in `oracles`.

**Phase 4 result (19 Nov 2026; one autonomous session; owner adjustments: the `he_sub_match` facade and audit switch APPROVED, the quick-toggles switch NOT APPROVED, so 4h is skipped; step 4a ran AFTER 4f).** Method for every split: a snapshot of the pre-Phase-4 tree (`snap0`), a scope analyser that moves the function body into stage functions `stage(S)` over one `__slots__` state class (every shared variable becomes `S.<attr>`; closures become module functions with a leading `S`), and an identity checker that UNDOES the rewrite on the AST and compares it with the original function (it failed the step on any difference). The behaviour net is a golden harness at the HTTP level (a fake Gemini REST server and Google `urlopen`, a virtual clock, one subprocess per scenario, normalised temp paths / tokens), recorded from the ORIGINAL tree and replayed on every step.

| Step | Status | Result |
|---|---|---|
| 4.0 preparation | DONE | snapshot, sha256 of `he_sub_match.py`, the import lists, the golden-resolve harness (56 scenarios) and the golden-extract harness (628 cases), both installed in the repo (`.github/scripts/subs_contract/golden_resolve.py`, `golden_extract.py` + `*_expected.json`) and wired into `check_subs_contract.py` |
| 4b split `resolve` | DONE | `ai_translation/service.py` (3,753 -> 75 lines): `resolve` is the dispatcher over `STAGES`. 18 modules in `ai_translation/job/` (4,115 lines, largest 765): `state.py` (`JobState`, 76 slots, `NEXT`, `begin`), `links/{passthrough,pool,embedded_sync,engine,embedded_ai,engine_ai}`, `ai/{prepare,cache,handoff,request,reference,plan,calls,dispatch,gender,finish,tuning}`. 27 closures became module functions |
| 4c other functions over 300 lines | PARTIAL | DONE: `list_candidates` (604) -> dispatcher + `fetching/search/{state,engine,pool,ai_rows,rows}` (`SearchState`, 23 slots); `handle_download` (343) -> `DOWNLOAD_STAGES` in `delivery/kodi_player.py` + `delivery/download/{state,links,deliver}` (`DownloadState`, 8 slots). NOT DONE (9 functions still over 300): `extract_embedded_srt` 808, `autosub_on_play` 798, `handle_translate_file` 690, `handle_bg_translate_picker` 458, `extract_cues_http` 395, `deep_verify` 379, `embedded_aligned_source_srt` 343, the `finish` stage of the `resolve` split 332 (it was part of `resolve` before), `on_phase` 314 (a nested closure). Reason: golden first. No harness reaches them (`autosub_on_play` needs the player event loop, the others subprocess / ffmpeg / MKV / HTTP-range behaviour), and some call module-private helpers that would first need public renames; a split without a net would only be verified by the AST identity check |
| 4d entry tables | DONE | `entry/plugin.py`: `HANDLE_ACTIONS` (3 actions that need the Kodi handle: search, manualsearch, download) and `PARAMS_ACTIONS` (14); `entry/migrations.py`: `BEFORE_PREWARM` (4) and `AFTER_PREWARM` (10) tuples run by two loops (the order of the original calls is kept; the golden `service.main` order scenario is identical) |
| 4e split the standalone modules | DONE | `fetching/embedded/embedded_extract.py` (4,986) -> `fetching/embedded/extract/` (14 modules, 5,057 lines, largest `http_source.py` 1,244); `ai_translation/context/gender_reference.py` (1,819) -> `ai_translation/context/gender/` (7 modules, 1,836 lines, largest 579). The `__file__`-relative standalone loaders (`_mp4`, `_gg`, `gender_reference_hpg`) became plain imports (owner dropped the standalone constraint). `baseline.json` lost both standalone entries |
| 4f Unified Core + Thin Wrappers | PARTIAL | (1) ONE RTL text core `delivery/rtl.rtl_text` behind the 3 wrappers (golden matrix identical). (2) ONE `core/kodi.make_breather` replaces the 3 `_breathe` copies (each module keeps its own timer and hook); `MIN_CUES` / `STATUS_UNKNOWN` moved to `core/srt/cues`, so `core/` no longer imports `sync/`. (3) `gender/align._to_ms` was merged into `core/srt/cues.to_ms` (provably the same for every input its regex can hand it); the gender SRT parser and aligner were NOT merged (the parsers differ in what they accept: reported). (4) `core/link_codec.decode_link_safe` is the broad-`except` wrapper; `sync/oracles` has no copy of the decoder any more |
| 4a `he_sub_match` facade and audit | PARTIAL | DONE: `_dbg` -> `dbg` and `_warm_queue_dir` -> `warm_queue_dir` (public, the two baseline exceptions are gone; `entry/service.py` updated), a stale comment of the Wizard's `host_repairs/flow/umbrella_steps.py` corrected; the five API names, the module-level imports (stdlib only) and the path are unchanged. NOT DONE: the facade split. Reason: POV and Umbrella import the file by bare name from their own interpreters, where only `resources/lib` is on `sys.path` and the name `resources` already belongs to POV's own package; a facade that imports `scoring_matching.*` / `fetching.*` from there would need the same fallback import tricks the file already contains, and its standalone duplicate scoring logic (the flat fallback of `release`) would still have to stay. Needs a Kodi test in both interpreters first |
| 4g owner questions | DONE (as questions) | listed in the Phase 4 report: `to_ms` / `_tc_ms`, `ms_to_stamp` / `fmt_tc`, the six `release_name` policies (defaults applied: keep all), plus the new ones below |
| 4h quick toggles | SKIPPED | owner switch NOT APPROVED (the one open feature, §2.2) |
| 4i chooser `_classify` percentage | NOT DONE | the number cannot simply come from `pct`: two embedded rows carry the percentage 101 that only the label shows; reverted, `ui/chooser.py` is byte-identical to Phase 3 |
| 4j statistics and docs | DONE | this part, Part 6, Appendix C, `CLAUDE.md` |

**What the verification covers, and what it does not.** Identical to the pre-Phase-4 tree on: 1,217 golden cases (Phase 3 net, 29 new `classify.*` cases), 628 golden-extract cases, 56 golden-resolve scenarios (the original tree and the refactored tree produce the same records; `resolve` branches reached: about 58% of its statements, `handle_download` 58-78% per new module). The rest of the moved code is verified by the AST identity check only (the rewrite is undone and the function compared with the original). Stub import 152 of 152, `compile()` with warnings as errors, pyflakes 4 (the same four messages), the cross-module API scan clean, `check_subs_contract.py` 62 checks, CRLF on every file. Kodi has NOT been tested (Appendix C).

**Phase 4 findings (reported, not fixed).** (1) `sync/probes/remote.py` has a fallback branch for a missing `reader` that cannot run (dead code, left in place). (2) `to_ms` (fraction digits read as a fraction, `"1,5"` -> 1500) and `_tc_ms` (plain milliseconds, `"1,5"` -> 1005) still differ for 1-2 digit fractions; one of them is probably wrong. (3) The percentage 101 of two embedded rows exists only in their labels (4i). (4) The Phase 3 files `.github/scripts/check_subs_contract.py`, `.github/scripts/subs_contract/` and `.github/workflows/subs-contract.yml` were never committed (`git status` shows them as untracked): the Phase 4 commit adds them.

**Rules for every phase:** `CLAUDE.md` "SUBTITLE ADD-ON: DECOUPLED" (no shims; pinned public surface; no Wizard names), STRUCTURAL REFACTORING ONLY for behaviour (report bugs, do not fix them silently), CRLF, `output.txt`, a commit snippet per step.

## Part 5: Deletion Report

Each phase appends its rows here. A row is an inactive feature, setting or block of code that was **removed**. "Revive" says what bringing it back would take, so the owner can decide. The bypassed upstream features of the Phase 0 merge are listed in `CLAUDE.md` "SKIPPED UPSTREAM FEATURES", not here. Names and paths in the rows are those of the code when it was deleted: `kodi_utils.py` was renamed `sub_utils.py` at the end of Phase 1, and the Telegram files no longer exist.

| # | Phase | Item | Kind (feature / setting / code / comment) | Where it was (path · function, lines) | Lines removed | Why it was inactive (evidence) | Revive: what it takes | Owner decision |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | DarkSubs hook call in `_handle_search` (`dark_subs_integration.maybe_patch_darksubs`) | feature | `default.py` · _handle_search · lines 129-145 | 18 | module `resources/lib/dark_subs_integration.py` is not shipped (the import raised ImportError into the surrounding try/except, so the block only logged a DEBUG line per search); DarkSubs was removed from the build (18435d90) | re-add `dark_subs_integration.py` (git show 77880b8:service.subtitles.kodipovilai/resources/lib/dark_subs_integration.py) and the call | pending |
| 2 | 1 | DarkSubs hook call in `_handle_test_connection` (`dark_subs_integration.maybe_patch_darksubs`) | feature | `default.py` · _handle_test_connection · lines 2074-2084 | 11 | same missing module; the try/except swallowed the ImportError silently | see the previous row | pending |
| 3 | 1 | `DEBRID_NOTICE_SERVICES` / `DEBRID_NOTICE_VALUES` tables + `_translate_path()` + blank gaps of cut handlers | code | `default.py` · module level (after `_handle_purge_temp`) · lines 2971-3002 | 35 | the debrid dialog moved to the Wizard (`addons/debrid_notice.py` carries its own copy of both tables); no reader in the add-on (grep, vulture 60%); `_translate_path` had no caller (the Wizard has its own copy in flow/common.py) | copy the tables back from plugin.program.kodipovilwizard/resources/libs/addons/debrid_notice.py | pending |
| 4 | 1 | `_without()` and `_walk_all()` (helpers of the moved repair pass) | code | `service.py` · module level · lines 79-93 | 17 | no caller in the add-on (vulture 60%, grep); the Wizard keeps its own copies in host_repairs/flow/common.py, used by skin_steps.py | copy from plugin.program.kodipovilwizard/resources/libs/host_repairs/flow/common.py | pending |
| 5 | 1 | `_prune_source_memory_once()` and its 2 call sites (start-up and the 24 h loop) | feature | `service.py` · _prune_source_memory_once · lines 162-181 | 20 | imports `resources.lib.source_memory`, a module that is not shipped (ImportError -> logged a WARNING once a day); the feature (remembered-sources store of the old POV hook) lives in the Wizard (`patches/pov_source_remember.py`, its own `source_memory/` folder) | re-add `source_memory.py` (git show 77880b8:service.subtitles.kodipovilai/resources/lib/source_memory.py) and the function | pending |
| 6 | 1 | `TEMP_PURGE_VERSION` constant + comment (one-shot "purge old temp subs" rollout) | code | `service.py` · module level · lines 204-213 | 11 | no reader anywhere (vulture 60%, grep); the routine that used it is gone and its marker `_temp_purge_done` is read by nothing | re-add a one-shot that calls `local_subs.purge_temp_subs()` once per version | pending |
| 7 | 1 | DarkSubs machine-translate stack: `bing_web_*`, `google_send_translate`, `google_machine_translate`, `yandex_*`, `bing_*` key feed + `bing_api_machine_translate`, `machine_translate_subs` | feature | `resources/lib/subs_engine/engine.py` · 13 functions after `get_subtitles` · lines 740-1129 | 371 | only reachable from `download_sub` (itself unreachable) through `machine_translate_subs`; no caller in the add-on, the Wizard, skins or userdata (grep); `google_translate.py` (the live Google fallback) is a separate module; the Bing key feed host (kodi7rd.github.io) no longer exists; the lazy imports `resources.modules.auto_translate.*` point at a folder that is not shipped | copy the 13 functions back from git (0.3.96) and restore `resources/modules/auto_translate` (bing_web, googletrans); needs a caller (a bridge download step) | pending |
| 8 | 1 | `download_sub()` (DarkSubs download entry of the vendored engine) | feature | `resources/lib/subs_engine/engine.py` · download_sub · lines 1131-1245 | 115 | no caller: the bridge has its own `_download_inner` (`subs_engine_bridge.py`, its comment says it mirrors it) and calls `module.download()` directly; grep for `download_sub` finds only telegram helper names; it also held the only call sites of `telegram.upload_subtitle_to_telegram` and the only readers of the hidden settings `auto_translate`, `auto_fix_sub_punctuation`, `auto_remove_hi_tags`, `subtitle_trans_cache` | copy back from git (0.3.96) and call it from the bridge download path | pending |
| 9 | 1 | `fix_sub_punctuation_text`, `fix_sub_punctuation_and_write`, `remove_hi_tags_and_write` (engine-side subtitle cleanup) | code | `resources/lib/subs_engine/engine.py` · section "PUNCTUATION FIX" · lines 599-735 | 134 | cascade: their only callers were `download_sub` and `machine_translate_subs`; the add-on does its own punctuation / HI cleanup (`srt.py`, `translate.py`) | copy back from git (0.3.96); they run only from `download_sub` | pending |
| 10 | 1 | `class Bing` (Microsoft ajax translator used by `yandex_machine_translate`) | code | `resources/lib/subs_engine/engine.py` · Bing · lines 55-80 | 26 | only constructed inside the removed `yandex_machine_translate`; its endpoint (api.microsofttranslator.com ajax.svc) is retired | copy back from git (0.3.96) | pending |
| 11 | 1 | `general.take_title_from_focused_item()` | code | `resources/lib/subs_engine/general.py` · take_title_from_focused_item · lines 268-284 | 17 | no caller anywhere (repo-wide grep, vulture 60%) | copy back from git (0.3.96) | pending |
| 12 | 1 | `general.save_file_name()` (writes the `list_subs_cache` table of `database.db`) | code | `resources/lib/subs_engine/general.py` · save_file_name · lines 665-690 | 26 | no caller anywhere (repo-wide grep, vulture 60%); its only reader `get_db_data` is also unused | copy back from git (0.3.96) | pending |
| 13 | 1 | `general.get_db_data()` (reads the `list_subs_cache` table) | code | `resources/lib/subs_engine/general.py` · get_db_data · lines 691-730 | 40 | no caller anywhere (repo-wide grep, vulture 60%) | copy back from git (0.3.96) | pending |
| 14 | 1 | `subs_engine/srt.compose()` | code | `resources/lib/subs_engine/srt.py` · compose · lines 439-476 | 38 | no caller (the module `subs_engine/srt.py` itself has no importer at all, see Open Questions); the add-on uses `resources/lib/srt.py` | copy back from git (0.3.96) | pending |
| 15 | 1 | `opensubtitles.get_random_key()` | code | `resources/lib/subs_engine/sources/opensubtitles.py` · get_random_key · lines 472-474 | 3 | no caller; the only mention is a commented-out line in `get_subs`; the live paths call `get_api_keys(shuffle_keys=True)` | copy back from git (0.3.96) | pending |
| 16 | 1 | `telegram_impl.telegram_helper_window()` (DarkSubs "Connect" info dialog) | code | `resources/lib/subs_engine/sources/telegram_impl.py` · telegram_helper_window · lines 338-340 | 3 | no caller anywhere (repo-wide grep, vulture 60%); the add-on has its own Telegram login actions in default.py | the whole file went at the end of Phase 1 (row 46) | superseded by row 46 |
| 17 | 1 | `telegram_impl.async_upload_subtitle()` (+ its "UPLOAD" section banner) | code | `resources/lib/subs_engine/sources/telegram_impl.py` · lines 481-498 | 18 | no caller; the only other mention is a commented-out `asyncio.run(...)` line in `upload_subtitle_to_telegram` | the whole file went at the end of Phase 1 (row 46) | superseded by row 46 |
| 18 | 1 | `translate._build_prev_context_by_idx()` | code | `resources/lib/translate.py` · _build_prev_context_by_idx · lines 4748-4763 | 16 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%); the live path builds prev-context inline in `resolve` | copy back from git (0.3.96) | pending |
| 19 | 1 | `subsync._remote_cue_reference()` | code | `resources/lib/subsync.py` · _remote_cue_reference · lines 691-695 | 5 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 20 | 1 | `subsync._probe_reference_cues()` | code | `resources/lib/subsync.py` · _probe_reference_cues · lines 1326-1328 | 3 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 21 | 1 | `subsync.proven_pool_ai_fallback()` | code | `resources/lib/subsync.py` · proven_pool_ai_fallback · lines 1653-1685 | 33 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 22 | 1 | `subsync._pending_key()` | code | `resources/lib/subsync.py` · _pending_key · lines 4623-4624 | 2 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 23 | 1 | `arabic_gender.prepare()` (old gender-reference entry) | code | `resources/lib/arabic_gender.py` · prepare · lines 1873-1890 | 18 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%); `translate` calls the chain iterator, its own log line "gender-ref prepare crashed" is a label only | copy back from git (0.3.96) | pending |
| 24 | 1 | `prompt.build_arabic_gender_block()` | code | `resources/lib/prompt.py` · build_arabic_gender_block · lines 406-408 | 3 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 25 | 1 | `cache.total_size_mb()` | code | `resources/lib/cache.py` · total_size_mb · lines 274-278 | 5 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 26 | 1 | `mp4_probe.subtitle_reference_source()` | code | `resources/lib/mp4_probe.py` · subtitle_reference_source · lines 306-318 | 13 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 27 | 1 | `kodi_utils.subtitle_sync_status_label()` | code | `resources/lib/kodi_utils.py` · subtitle_sync_status_label · lines 938-939 | 2 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 28 | 1 | `kodi_utils.progress_dialog()` | code | `resources/lib/kodi_utils.py` · progress_dialog · lines 1378-1385 | 8 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 29 | 1 | `gemini_pair.get_lan_ip()` (single-IP variant) | code | `resources/lib/gemini_pair.py` · get_lan_ip · lines 476-480 | 5 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%); the pairing server uses `get_lan_ips()` | copy back from git (0.3.96) | pending |
| 30 | 1 | `he_sub_match._seed_from_pool()` | code | `resources/lib/he_sub_match.py` · _seed_from_pool · lines 881-929 | 49 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) (the Wizard patches load he_sub_match by bare name but only call its public API) | copy back from git (0.3.96) | pending |
| 31 | 1 | `mkv_probe` reader method `abs_pos()` | code | `resources/lib/mkv_probe.py` · _Buf.abs_pos · lines 234-235 | 2 | no caller anywhere (repo-wide grep incl. skins, userdata, the Wizard and its patches; vulture 60%) | copy back from git (0.3.96) | pending |
| 32 | 1 | unreachable weaker-gate tail after the `try/except` of the gender-reference gate (both branches return) | code | `resources/lib/arabic_gender.py` · map-gate function (the one ending with "hardened aligner failed; reference declined") · lines 640-661 | 22 | vulture 100%: code after a `try` whose body and `except` both return; its comment says it was kept for old external imports, and no module imports it | copy the tail back from git (0.3.96) | pending |
| 33 | 1 | `pool.POOL_API_KEY` constant (a credential-like string, never sent) | code | `resources/lib/pool.py` · module level · lines 36-36 | 1 | vulture 60%; grep: the only occurrence in the repo is the definition (no header, no request uses it) | do not revive: deprecated on purpose in 0.2.290 (43f5d114): the Worker now requires HMAC signing (`pool.sign_headers`, `_pool_key()`); the constant was kept only "so old references don't break" | pending |
| 34 | 1 | `arabic_gender._estimate_map()` (old global-vote aligner) | code | `resources/lib/arabic_gender.py` · _estimate_map · lines 136-148 | 13 | cascade: the only caller was the unreachable weaker-gate tail removed from the gender-reference gate (repo-wide grep: no other reference) | copy back from git (0.3.96) | pending |
| 35 | 1 | `arabic_gender._overlap_rate()` | code | `resources/lib/arabic_gender.py` · _overlap_rate · lines 151-168 | 18 | cascade: the only caller was the unreachable weaker-gate tail removed from the gender-reference gate (repo-wide grep: no other reference) | copy back from git (0.3.96) | pending |
| 36 | 1 | `keep_darksubs` (visible switch "Keep DarkSubs enabled") | setting | `resources/settings.xml` · 0.3.96 lines 629-636 | 8 | nothing reads it (grep: add-on, Wizard, skins, userdata); DarkSubs is not part of the build, so the switch did nothing | re-declare it and have the service honour it (needs DarkSubs back in the build) | pending |
| 37 | 1 | `force_ai_when_auto_translate_off` (visible switch "Translate via AI even when DarkSubs auto_translate is OFF") | setting | `resources/settings.xml` · 0.3.96 lines 77-81 | 5 | only two comments mention it; nothing reads it; the setting it overrode (DarkSubs auto_translate) no longer exists | re-declare it; `translate` / `autosub_service` would need to read it again | pending |
| 38 | 1 | DarkSubs bookkeeping markers `_darksubs_takeover_done`, `_darksubs_hook_last_fire`, `_darksubs_hook_last_outcome`, `_darksubs_autoenable_done`, `_darksubs_nag_done` | setting (hidden marker) | `resources/settings.xml` · 0.3.96 lines 402-411, 412-418, 419-425, 923-933, 941-947 | 42 | no reader and no writer anywhere (grep); the DarkSubs hook and nag were removed | re-declare when a DarkSubs-style hook is written again | pending |
| 39 | 1 | `_force_ai_autoenable_done` marker | setting (hidden marker) | `resources/settings.xml` · 0.3.96 lines 934-940 | 7 | no reader or writer (grep); the auto-enable it tracked is gone | re-declare | pending |
| 40 | 1 | `_addon_update_mode_seeded`, `_lists_sort_recent_v1` markers (moved Wizard features) | setting (hidden marker) | `resources/settings.xml` · 0.3.96 lines 496-507, 342-349 | 20 | markers of features that moved to the Wizard in Phase 3 (add-on update mode repair, POV list sort); no reader or writer in this add-on | re-declare | pending |
| 41 | 1 | `_subtitle_outline_migration_v1`, `_temp_purge_done`, `_gemini_model_bump_v1` markers | setting (hidden marker) | `resources/settings.xml` · 0.3.96 lines 487-495, 299-306, 350-357 | 25 | no reader or writer in the add-on (grep); the box-to-outline migration moved to the Wizard, the temp purge routine and the v1 model bump no longer exist | re-declare together with the migration | pending |
| 42 | 1 | `OSuser`, `OSpass` (hidden OpenSubtitles login) | setting (hidden) | `resources/settings.xml` · 0.3.96 lines 812-817, 818-823 | 12 | the only mentions are commented-out lines in `opensubtitles.get_subs`; the provider authenticates with the shared API keys | re-declare and read them in the OpenSubtitles provider | pending |
| 43 | 1 | `strings.po` entries 32108, 32544, 32545, 32220, 32507, 32107, 32250-32254 (8 entries x 2 languages; the DarkSubs / Wyzie labels and the retired "Behavior" and "Built-in engine (DarkSubs)" headings) | setting label | `resources/language/*/strings.po` · en_gb and he_il | 0 | no longer referenced by settings.xml, code, skins or the Wizard (grep); 32108 / 32544 / 32545 belonged to the removed switches, 32220 / 32507 to groups and categories that were merged, 32107 / 32250-32254 to features that no longer exist | restore the entries from git together with the setting that uses them | pending |
| 44 | 1 | Unused imports, locals, constants and unreachable statements (22 items) | code | `import xbmcaddon` (unused name); unused `import xbmcgui` in the SubSync delay-watch loop; unused imports / globals of the vendored engine left by the machine-translate removal (`random`, `xbmcplugin`, `pkgutil`, `sys`, `shutil`, `threading`, duplicate `urllib.parse` imports, `CachedSubFolder`, `TransFolder`, `user_dataDir`, `cache`, `DEFAULT_REQUEST_TIMEOUT`, `futures`, `trans_result`, `iconx`, `MyScriptID`, a stale chardet comment); unused local `source_dir` in `c_get_subtitles` (+ module `addonPath`); unused imports `unquote_plus`, `quote` in `subs_engine/general.py`; unused import `struct` in `opensubtitles.py`; unused local `Addon` (and its stale comment) in `opensubtitles.get_subs`; unused import `random` in `telegram_impl.py`; unused import `extract` in `telegram_impl.py`; unused import `logging` in `subs_engine/cache.py`; unused import `sys` in `subscene.py`; unused import `re` in `subsource.py`; write-only counter `refined_any` in `sync_align`; unused constant `OPENSUBTITLES_SEARCH_FALLBACK_VERSION`; unreachable `pass` after a `return` in `ktuvit.get_matching_ktuvit_name`; unused local import `kodi_utils` in `_handle_open_aistudio`; unused local import `srt` in `_handle_translate_file`; unused import `sys` in `kodi_utils.py`; dead `payload` decode block in `subs_chooser._row_item`; unused local `start_p` in `mkv_probe`; unused exception name `e` in `translate.resolve` (OverloadError handler); unused import `os` in `release_match.py` | 43 | pyflakes / vulture, each re-checked by reading the code and a repo-wide grep; nothing reads them | re-add the name if a feature needs it | pending |
| 45 | 1 | Stale comments and docstring paragraphs about moved or removed code (10 items) | comment | comment block "THE REPAIR PASS RUNS INLINE ON MAIN" (about `_run_build_startup_repairs`, `pov_reload`, `REPAIRS_DONE_PROPERTY`, the Wizard's hot_reload); `patcher_health` tree-scan clause of the `_BACKGROUND_SETTLE_*` comment; stale "Build-edition only ... SLIM_SERVICE" paragraph of the `_maybe_default_builtin_engine` docstring; "build startup repairs" wording of the main() comment; reference to the non-existent `_ensure_darksubs_enabled()` in a main() comment; comment block about the AF3 first-launch dialog and "POV's modular debrid hook" in main(); section comment lines and blank lines between the removed functions; translate.py module header: "DarkSubs handles all sourcing" (no longer true); local_subs.py header: "DarkSubs" named as a temp-dir source; orphaned comment about the POV seasons view id marker (no setting follows it) | 67 | they described the Wizard repair pass, DarkSubs, the SLIM_SERVICE edition or functions that no longer exist | n/a | n/a |
| 46 | 1 | The Telegram source: `telegram.py` (the engine wrapper: `login`, `logout`, `diagnose`, `get_subs`, `download`) and `telegram_impl.py` (Telethon user client, channel search, bot upload, login window) | feature | `resources/lib/subs_engine/sources/telegram.py` (153 lines) · `telegram_impl.py` (632 lines, after 25 lines went in rows 16-17 and 44) | 785 | could not load: `telegram_impl` imported `telegram_api`, which did not ship (it was part of DarkSubs' bundled `telethon_dependencies`, deleted with DarkSubs in `18435d90`), and Telethon was never vendored; the provider default was OFF (`telegram` = false, force-off in `_ENGINE_DEFAULTS`); it was the only baseline import failure | restore a vendored Telethon, your own Telegram API credentials and a decision on the channel; then `git show 4bb3184d:<path>` for both files and the rows below | owner: **deleted for good** (unneeded) |
| 47 | 1 | Telegram handlers and routes of `default.py`: `_handle_connect_telegram`, `_handle_telegram_test`, `_handle_logout_telegram`; the `action=connect_telegram` / `logout_telegram` / `telegram_test` branches of `main()`; the `('telegram', 'telegram')` entry of the provider list in `_handle_engine_test` | feature | `default.py` · lines 2953-3014 (handlers), 3271-3276 (routes), 3126 (tuple) | 61 (file 3,283 → 3,222) | only the Telegram settings buttons called them (repo-wide grep: no skin, Wizard, patch or userdata names these actions) | `git show 4bb3184d:service.subtitles.kodipovilai/default.py` | owner: deleted |
| 48 | 1 | Telegram settings: `telegram` (visible toggle, default false), `connect_telegram` / `logout_telegram` / `telegram_test` (visible actions in the group `telegram_tools`), `telegram_first_and_last_name` (hidden, written after a login) | setting | `resources/settings.xml` · Providers & Fetching (sources and Telegram groups) and Advanced & Debug (hidden engine group) | 65 (file 1,080 → 1,015) | their only readers / callers were in the deleted source; declared 98 → 93 (visible 60 → 56, hidden 38 → 37) | restore the blocks from git together with the source | owner: deleted |
| 49 | 1 | `strings.po` labels `32513` (Telegram), `32519` (help), `32528`, `32529`, `32530`, `32533` (6 entries x 2 languages) | setting label | `resources/language/*/strings.po` · en_gb and he_il | 0 (entries 149 → 143 each) | referenced only by the five Telegram settings and the `telegram_tools` group (grep: settings.xml, code, skins, the Wizard) | restore with the settings | owner: deleted |
| 50 | 1 | Telegram wiring of the vendored engine and the bridge: the `telegram` import, the `[Telegram]` site-order entry, the `format_website_source_name` branch, the `global_var` reset entry and the search thread in `engine.py`; `'telegram': 'false'` in `_ENGINE_DEFAULTS`, `'telegram'` in `_SOURCE_MODULES`, in the cache signature of the result cache and `[Telegram]` in `_PROVIDER_LABEL` in `subs_engine_bridge.py`; the stale `All_Subs` id in a `subs_chooser.py` comment | code | `subs_engine/engine.py` (7 lines; 542 → 535) · `subs_engine_bridge.py` (4 lines) | 11 | the provider could not run and had no toggle; the engine bucket variable `telegram_hebrew_machine_translated_subtitles` was renamed `hebrew_machine_translated_subtitles` (same logic) | restore with the source | owner: deleted |

**Phase 1 notes on the table**
- Line numbers are those of the 0.3.96 files before Phase 1 (`git show 4bb3184d:<path>` gives the code). "Revive: copy back from git (0.3.96)" means exactly that commit.
- Not a removal, so not a row: 17 string literals with an invalid escape sequence (`\s`, `\d`, `\.`, `\-`, `\i`) were made raw strings in `subs_engine/cache.py` (1), `general.py` (2), `sources/ktuvit.py` (1), `_libs/parse_torrent_title/{extras,patterns,post}.py` (3 + 7 + 2) and `pyxbmct/addonwindow.py` (1). Each conversion is proven byte-identical: the `ast.dump` of every file is the same before and after. The Python 3.13 `SyntaxWarning` list is now empty.
- Settings: 105 declared (55 visible, 50 hidden) became 93 (56 visible, 37 hidden): 15 dead ids removed (rows above) and the 5 Telegram ids removed in the wrap-up (row 48); 7 visible keys declared (`subsync_verify`, `subsync_probe`, `subsync_audio`, `subsync_autorank`, `prev_context_lines`, `he_match_ktuvit`, `he_embedded_report`; the hidden `telegram_first_and_last_name` declared in the first pass went with Telegram). 50 surviving ids changed category or group; no id, type, default, level, constraint, dependency or action changed.
- Left on purpose (not deleted, listed in the Phase 1 report as open questions): the whole `subs_engine/srt.py` module (no importer), the DarkSubs empty-list toast in `translate.list_candidates`, `gemini_rpm` (readers disagree on its default). (The "Kodi POV IL" dialog titles and the Telegram source were settled in the follow-up and the wrap-up.)

**Phase 1 follow-up (19 Nov 2026), after the first review**
- **Label ids:** the 5 labels of `ai_when_no_hebrew` moved out of the forbidden 33000+ range: `33009`..`33013` -> `32610`..`32614` in `settings.xml` and both `strings.po` files (texts unchanged). Nothing in `settings.xml` is above 32626 now. The 9 runtime strings `33000`-`33008` (read through `sub_utils.localised`, 13 call sites) are still in that range: see the open item in `CLAUDE.md`.
- **One brand title:** `sub_utils.DIALOG_TITLE = 'Kodi POV IL'` is now the only place of the brand title. `default.py` (36 uses through the `_title()` helper), `subs_chooser.py` (2), `autosub_service.py` (1), `subs_engine_bridge.py` (1), `subs_engine/sources/telegram.py` (1), `subs_engine/general.py` (the engine's toast title, was the add-on name from `addon.xml`), `gemini_pair.py` (the pairing page `<title>`, a `__DIALOG_TITLE__` placeholder resolved when the handler is built) and `sub_utils.notify` (default title) all read it. Titles that said "MoranSubs" now say "Kodi POV IL" (a visible change the owner asked for). `telegram_impl.py` has no brand string (its dialogs have their own Hebrew titles). Not touched on purpose: window property names (`MoranSubsChooserOpen`), user agents, log prefixes, thread names, comments.
- **Forensics on the Part 5 items** (against the upstream repo history; full evidence in the Phase 1 follow-up report): no deleted item was a live, recoverable feature. `POOL_API_KEY` was deprecated on purpose in 0.2.290 (HMAC signing replaced the `x-api-key` header); `_remote_cue_reference` / `_probe_reference_cues` lost their call sites in the "exact-cut" sync rewrite (upstream `2ae4230f`); `_build_prev_context_by_idx` in upstream `ede224d6`; `_seed_from_pool` in `81314ec5`; the machine-translate stack was replaced by `google_translate.py` (Bing / Yandex were never wired into MoranSubs); `download_sub` and the Telegram upload were DarkSubs-only paths. Two pieces of context are worth keeping: the Telegram source depended on DarkSubs' bundled `telethon_dependencies` (199 files incl. an obfuscated `telegram_api.py`, deleted with DarkSubs in `18435d90`), and `source_memory.prune()` was never carried to the Wizard's `pov_source_remember` (the remembered-sources folder is no longer capped).

**Phase 1 wrap-up (19 Nov 2026), owner decisions**
- **Runtime strings:** the 9 strings `33000`-`33008` moved to `32630`-`32638` in both `strings.po` files (texts and CRLF unchanged) and the 12 call sites (`default.py` 8, `translate.py` 4; the earlier note said 13) read the new ids. No string id >= 33000 remains anywhere in the add-on (`settings.xml`, code, both `.po`). Two of the nine have no caller (`32630` "AI Hebrew (translated from {0})", `32636` "No source subtitles found..."); they were moved as asked, not deleted.
- **Telegram removed** (rows 46-50): zero `telegram` / `All_Subs` names remain in code, settings or `.po`, except comments that describe the community pool Worker (it relays through a Telegram channel: `pool.py`, `service.py`, `translate.py`, one comment in `default.py`), which are about the server, not about a dependency. `LICENSE.txt` keeps its DarkSubs notice and `changelog.txt` its history. Left in place: the label tests for `HebrewMachineTranslated` in `subs_engine/engine.py`, `subs_engine_bridge.py` and `arabic_gender.py` (only the Telegram source ever produced that language tag, so the branches are now inert; a Phase 2/3 candidate, not deleted because a deletion needs its own review).
- **Cache key:** the result-cache signature now covers 8 flags instead of 9 (the `telegram` flag is gone), so the first search after the update misses the old cached lists once.
- **Rename:** `resources/lib/kodi_utils.py` -> `resources/lib/sub_utils.py` (1,375 lines, `DIALOG_TITLE` and every helper unchanged). 802 references in 18 files were updated (`default.py`, `service.py` and `resources/lib/*.py`, `subs_engine/general.py`); the module itself did not change (it never named itself). The old name is gone (no alias). `he_sub_match.py` keeps working: its lazy imports name `sub_utils` now.
- **FENtastic `700068` (read-only check of `skin.fentastic`):** `700068` is a **string id of the skin's own `language/*/strings.po`** ("Show widget menu name colored border"), used once as `$LOCALIZE[700068]` in `xml/SkinSettings.xml`. It is not a `<control id>` and not an add-on string. The skin's `strings.po` holds 240 ids (168 in 31000-32999, 72 from 700010 up); 107 `$LOCALIZE[700xxx]` uses exist. The only `700xxx` control ids are 6 buttons in `Includes_VideoOsd3.xml` (`700450`, `700451`, `700452`, `700454`, `700455`, `700460`). A skin's strings are its own table, separate from an add-on's 30000-32999 range; the rule of this repository applies to add-ons' `strings.po` / `settings.xml` only. Nothing in the subtitle add-on uses a `700xxx` id.

**Phase 2 notes (19 Nov 2026).** Phase 2 deleted no feature, setting or function (a structural phase), so it adds no row. What it left on purpose, for the owner:
- Inert leftovers of Phase 1, untouched: the `HebrewMachineTranslated` label tests (now in `fetching/providers/engine.py`, `fetching/bridge.py`, `ai_translation/context/gender_reference.py`), `fetching/providers/srt.py` (no importer; MIT notice in `LICENSE.txt`), the orphan string ids 32103, 32231, 32504, 32505, 32534-32537, 32630, 32636, the DarkSubs empty-list toast of the list builder (`fetching/service.list_candidates`), the `gemini_rpm` default conflict.
- New small leftovers: the unused constant `ADDON_ID` in `default.py` (it moved nowhere because nothing uses it), the unused locals `engine` (`ui/settings_actions/engine.py`) and `key` (`fetching/embedded/streams.py`, 2 places) and the redefined `_pshare` (`ai_translation/service.py`), all already flagged by pyflakes before the phase.
- Private helpers called across modules keep their underscore (`core.kodi._home_window`, `ai_translation.proofread.gp_state._gp_*`, ...): the optional rename to public names was not done, so every caller stays byte-identical.

**Phase 3 deletions and changes (19 Nov 2026; nothing user-visible).** Deleted as code: `fetching/bridge.detect_release_name` and `_REL_PATTERNS` / `_release_score` (moved into `core/video_context`, the former as the `scored` policy), `ui/chooser._video_ref`, `fetching/pool_client.release_from` and `is_token_like`, `scoring_matching/ranking.looks_like_token` and `release_from_path` (all replaced by `core.video_context.release_name` / `looks_like_token` / `release_from_path`), `fetching/search_cache` / `subs_cache` inline profile-folder code (now `core/cache/profile_dir`), the regex that read the match % out of the Hebrew label in `ui/listing.py`, the `translate` argument of the chooser, and the function-level imports of `core.settings` / `core.kodi` in `core/kodi.py` / `core/settings.py`. Added: `core/addon.py`, `core/cache/profile_dir.py`, `release_name`, `pct` on every row. 316 names lost their leading underscore (Part 4, Phase 3 result).


**Phase 4 deletions and changes (19 Nov 2026; nothing user-visible, no feature, setting or label removed).** Deleted as code: `fetching/embedded/embedded_extract.py` and `ai_translation/context/gender_reference.py` (split into the packages of Part 4; no function dropped), the `__file__`-relative standalone loaders `_mp4`, `_gg` and `gender_reference_hpg` (plain imports now), the three `_breathe` copies (`make_breather`), `sync/oracles.decode_link` (`decode_link_safe`), `gender/align._to_ms` (`core.srt.cues.to_ms`), the per-module copies of `MIN_CUES` / `STATUS_UNKNOWN` (moved to `core/srt/cues`), the entry `if`-chains of `entry/plugin.py` and `entry/migrations.py` (tables). Names changed in a frozen file: `he_sub_match._dbg` -> `dbg`, `_warm_queue_dir` -> `warm_queue_dir` (the file's public API of 5 names is unchanged). The state classes carry exactly the variables the stages share (76 / 23 / 8 slots; the kit checks that every `S.<attr>` is a slot, every slot is written and none is unused).

## Part 6: Refactoring Statistics

Measured from the repo, never estimated. Method:
- Python lines: `find service.subtitles.kodipovilai -name '*.py' | xargs cat | wc -l`, split into own code and bundled (`subs_engine/_libs`, `pyxbmct`).
- Files and functions: an AST count with nested functions included.
- Largest file and function: an AST span.
- Diff totals: `git diff --shortstat <phase-start>..HEAD -- service.subtitles.kodipovilai`.
- Moved lines: the line spans of the defs moved, checked by AST identity.
- pyflakes: the same file set every time (default.py, service.py, `resources/lib/*.py`, without the engine and pyxbmct).
- Settings: declared ids, split into visible and hidden (level 4).

**Baseline (end of Phase 0, add-on 0.3.96):**

| Metric | Value |
|---|---|
| Python files | 74 |
| Python lines (own / bundled / total) | 55,837 / 3,546 / 59,383 |
| `subs_engine/` lines | 8,363 |
| Functions (nested included, own code) | 1,567 |
| Largest files | `translate.py` 9,706 · `subsync.py` 5,577 · `embedded_extract.py` 4,984 · `sync_align.py` 3,374 · `default.py` 3,348 · `srt.py` 3,292 |
| Largest functions | `translate.resolve` 3,674 · `translate._extract_embedded_srt` 807 · `translate.list_candidates` 616 · `translate._embedded_aligned_source_srt` 340 |
| `settings.xml` | 1,104 lines · 106 declared (55 visible, 51 hidden) · 9 keys read but not declared |
| One-shot migrations in `service.py` | 16 |
| pyflakes messages (own code) | 13 |
| Stub import (scratchpad harness, `xbmc*` / `chardet` / `telethon` / `pyxbmct` / `urllib3` / `qrcode` stubbed; `requests` absent) | 59 of 72 modules; the 13 failures are the bundled `pyxbmct`, the engine core and sources (they need `requests` or real paths) and `telegram_impl`; identical before and after the merge |

**After Phase 1 (measured with the same method at the end of the wrap-up; the harness of Phase 1 stubs `requests` too, so the stub-import line counts 72 modules and all of them import):**

| Metric | Baseline (Phase 0, as measured at the start of Phase 1) | After Phase 1 |
|---|---|---|
| Python files | 74 | 72 |
| Python lines (own / bundled / total) | 55,845 / 3,546 / 59,391 (the 59,383 above was counted with `cat`, 8 lines fewer) | 53,737 / 3,546 / 57,283 |
| `subs_engine/` lines | 8,371 | 6,713 |
| Functions (nested included, own code) | 1,567 | 1,490 |
| Largest files | `translate.py` 9,706 · `subsync.py` 5,577 · `embedded_extract.py` 4,984 | `translate.py` 9,687 · `subsync.py` 5,526 · `embedded_extract.py` 4,984 (`default.py` 3,283 → 3,222) |
| Largest functions | `resolve` 3,674 · `_extract_embedded_srt` 807 · `autosub_on_play` 790 · `_handle_translate_file` 687 · `list_candidates` 616 | unchanged |
| `settings.xml` | 1,104 lines · 105 declared (55 visible, 50 hidden) · 9 keys read but not declared | 1,015 lines · 93 declared (56 visible, 37 hidden) · 1 key read but not declared (`gemini_rpm`, conflicting defaults) |
| pyflakes messages (own code, same file set) | 15 (the 13 above was measured before the last merge fixes) | 7 (all deliberate or intentional-pattern: 2 pre-imports in `service.py`, 1 in `autosub_service.py`, the import probe in `default.py`, 2 unused `key` locals in the bridge, the `_pshare` default) |
| vulture candidates (>= 60%, whole add-on incl. bundled) | 102 | 64 (the rest: Kodi event methods, he_sub_match public API, bundled libraries, unused locals in the vendored providers) |
| Stub import | 73 of 74 | **72 of 72 (100%)**: the only failure, `telegram_impl`, was deleted |
| `compile()` | 74 of 74 | 72 of 72 |
| Label ids in `settings.xml` | highest 33013 (5 ids >= 33000) | settings.xml highest 32626; `strings.po` highest 32638 (none >= 33000 anywhere: the 9 runtime strings moved to 32630-32638) |
| `strings.po` entries (each language) | 143 | 143 (6 Telegram labels removed; 17 added in the first pass, 11 orphans removed; no duplicates, same ids in en_gb and he_il) |
| Hard-coded dialog titles ('Kodi POV IL' / 'MoranSubs') | 43 literals (26 'Kodi POV IL', 17 'MoranSubs') | 1 (`sub_utils.DIALOG_TITLE`) |

**After Phase 2 (same method; the stub-import line counts every `.py` file under the add-on, `service` as importing when it starts its main loop):**

| Metric | After Phase 1 | After Phase 2 |
|---|---|---|
| Python files | 72 | 138 (default.py and service.py included; 64 paths of the old tree no longer exist, 130 paths are new: moved, split or added; 8 paths are unchanged) |
| Python lines (own / bundled / total) | 53,737 / 3,546 / 57,283 | 54,759 / 3,546 / 58,305 (the +1,022 are headers, import blocks and the 3 added modules) |
| `fetching/providers/` (ex `subs_engine/`) lines | 6,713 | 6,713 (moved, not touched) |
| Functions (nested included, own code) | 1,490 | 1,497 |
| Largest files | `translate.py` 9,687 · `subsync.py` 5,526 · `embedded_extract.py` 4,984 | `embedded_extract.py` 4,986 · `ai_translation/service.py` 3,757 · `gender_reference.py` 1,819 · `extraction.py` 1,473 · `sync/validators.py` 1,349 |
| Files over 1,500 lines | 9 | 3 (all reported) |
| Largest functions | `resolve` 3,674 · `_extract_embedded_srt` 807 · `autosub_on_play` 790 · `_handle_translate_file` 687 · `list_candidates` 616 | `resolve` 3,650 · `_extract_embedded_srt` 807 · `autosub_on_play` 799 · `_handle_translate_file` 690 · `list_candidates` 605 |
| Functions over 300 lines | 11 | 11 (none split, 4 closures extracted; see Part 4) |
| `settings.xml` / `strings.po` | 1,015 lines · 93 settings / 143 entries each | untouched |
| pyflakes messages (own code, same file set) | 7 | 4 (the two `service.py` pre-imports and the engine probe of `autosub_service` now go through `provider_adapter`) |
| Stub import | 72 of 72 | **138 of 138** |
| `compile()` (warnings as errors) | 72 of 72 | 138 of 138 |
| Golden cases / helper scenarios / entry actions | n/a | 311 / 26 / 19, identical to the pristine tree |
| Shims / empty `__init__.py` in new folders | n/a | 0 / 0 |

**After Phase 4 (same method, own code = everything outside `fetching/providers/_libs` and `pyxbmct`; line counts with `\n`, nonblank counts for the moved / kept comparison):**

| Metric | After Phase 2 | After Phase 4 |
|---|---|---|
| Python files | 138 | 185 (140 -> 185 against the Phase 3 tree; 121 paths unchanged, 89 byte-identical, 47 new, 2 gone) |
| Python lines (own / bundled / total) | 54,759 / 3,546 / 58,305 | 55,572 / 3,546 / 59,118 (Phase 3 tree: 54,754 own) |
| Functions (nested included, own code) | 1,497 | 1,530 |
| Largest files | `embedded_extract.py` 4,986 · `ai_translation/service.py` 3,757 · `gender_reference.py` 1,819 · `extraction.py` 1,473 · `sync/validators.py` 1,349 | `fetching/embedded/extraction.py` 1,474 · `sync/validators.py` 1,350 · `he_sub_match.py` 1,281 · `fetching/embedded/extract/http_source.py` 1,244 · `ai_translation/background.py` 1,188 |
| Files over 1,500 lines | 3 | 0 |
| Largest functions | `resolve` 3,650 · `extract_embedded_srt` 807 · `autosub_on_play` 799 · `handle_translate_file` 690 · `list_candidates` 605 | `extract_embedded_srt` 808 · `autosub_on_play` 798 · `handle_translate_file` 690 · `handle_bg_translate_picker` 458 · `extract_cues_http` 395 |
| Functions over 300 lines / over 100 lines | 11 / 80 | 9 / 87 |
| New packages (modules, lines, largest) | n/a | `ai_translation/job` 18 / 4,115 / 765 · `fetching/search` 5 / 757 / 341 · `fetching/embedded/extract` 14 / 5,057 / 1,244 · `ai_translation/context/gender` 7 / 1,836 / 579 · `delivery/download` 3 / 444 / 220 |
| Nonblank lines | 49,829 (Phase 3 tree) | 50,495: 46,725 kept verbatim, 3,104 no longer exist verbatim (moved bodies re-indented or rewritten to `S.<attr>`, import blocks), 3,770 new (headers, import blocks, state classes, dispatchers, tables) |
| pyflakes messages (own code, same file set) | 4 | 4 (the same four) |
| Stub import | 138 of 138 | **152 of 152** (the contract script counts the modules that are not bundled) |
| `compile()` (warnings as errors) | 138 of 138 | 185 of 185 |
| Golden cases / scenarios | 311 / 26 / 19 | 1,217 (Phase 3 net, +29 `classify.*`) + 628 extract + 56 resolve, identical to the pristine tree; `check_subs_contract.py` 62 checks |
| `he_sub_match.py` | sha256 of Phase 3 | 1,281 lines, sha256 `61b54457d554...` (two renames, baseline updated in the same change) |
| Shims / empty `__init__.py` in new folders | 0 / 0 | 0 / 0 |

**Per phase** (each phase adds a row; the "Total" row is filled at the end of Phase 4):

| Phase | Lines deleted | Lines moved | Lines added | Files (before → after) | Largest file | Largest function | Dead settings removed / declared | pyflakes | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 0. Decoupling merge | 345 (subtitle add-on) | 0 | 5,855 (upstream 0.3.75-0.3.96) | 72 → 74 | `translate.py` 9,706 | `resolve` 3,674 | 0 / +1 (`_gemini_model_bump_v4`) | 13 → 13 | not taken: 2,587 `service.py` + 92 `settings.xml` upstream lines, 132-line AF3 patcher, 11-line trailer guard |
| 1. Cleanup and settings (wrap-up included) | measured against the 0.3.96 tree (`git diff --no-index --numstat`): 2,217 Python lines deleted, 918 added; own Python 55,845 → 53,737 (net -2,108) · `settings.xml` 1,104 → 1,015 · `strings.po` 143 → 143 entries (rewritten) | 0 Python (the only move is the `kodi_utils.py` → `sub_utils.py` rename, 802 references); `settings.xml`: all blocks re-ordered, 50 changed category | 918 Python (17 raw-string conversions, 3 pre-import / probe comments, `DIALOG_TITLE` and its helpers, rewritten imports) · `strings.po` 17 new labels | 74 → 72 | `translate.py` 9,687 | `resolve` 3,674 | 20 removed (15 dead + 5 Telegram) / 7 declared | 15 → 7 | `engine.py` 1,245 → 535; Telegram source (785 lines) deleted; 1,567 → 1,490 functions; Python 3.13 SyntaxWarnings 17 → 0; stub import 72 of 72 (100%); no string id >= 33000 |
| 2. Core refactoring | 1,764 nonblank lines no longer exist verbatim (rewritten import lines and the per-file headers replaced by new ones; no function, setting or feature deleted) | 50,131 nonblank lines moved or kept as they were (of 51,895 before) | 2,659 nonblank lines (import blocks and headers of 66 new files, the 3 added modules `core/contracts` 100 + `fetching/provider_adapter` 48 + `pipeline` 23, the 5 loader functions of the adapter, 5 small edits listed in `CLAUDE.md`) | 72 -> 138 | `fetching/embedded/embedded_extract.py` 4,986 (was `translate.py` 9,687) | `resolve` 3,650 (was 3,674) | 0 / 0 | 7 -> 4 | method: multiset of stripped nonblank lines, old tree vs new tree; 1,490 -> 1,497 functions; stub import 72 of 72 -> 138 of 138; 311 golden cases, 26 helper scenarios and 19 entry actions identical; the 64 old paths that disappeared are the 34 flat modules (all but `he_sub_match` and `telemetry`) and the 30 files of `subs_engine/` |
| 3. Hygiene, deduplication, wiring and tests | 2,073 nonblank lines no longer exist verbatim (call sites rewritten for the public names, the moved / replaced derivations, the label regex) | 50,717 nonblank lines kept or moved as they were (of 52,790 before) | 2,066 (public names at 594 use sites, `release_name`, `pct`, 2 new modules); plus outside the add-on: the contract test, its fixtures and the workflow | 138 → 140 | `embedded_extract.py` 4,986 (unchanged: Phase 4) | `resolve` 3,646 (was 3,650); `list_candidates` 604 (was 605) | 0 / 0 | 4 → 4 | 316 cross-module private names made public (8 qualified); 6 playing-release copies -> 1 function with 6 named policies (83 of 120 fixtures differ between policies: reported); 2 duplicate groups merged, 7 kept side by side with the reason; `core/utils/` not created; single route through `pipeline.py`; `pct` on every row; contract test (62 checks) + CI; stub import 140 of 140; 1,188 golden cases |
| 4. Deep splits and cross-add-on integrations (PARTIAL) | 3,104 nonblank lines no longer exist verbatim (moved bodies re-indented or rewritten to `S.<attr>`, import blocks; the 3 `_breathe`, `oracles.decode_link`, `gender/align._to_ms`, 2 standalone-load blocks) | 46,725 nonblank lines kept or moved as they were (of 49,829 before) | 3,770 (47 new modules: headers, imports, state classes, dispatchers, tables); plus outside the add-on: `golden_resolve` (56), `golden_extract` (628) and the contract wiring | 140 -> 185 | `extraction.py` 1,474 (was `embedded_extract.py` 4,986; no file over 1,500) | `extract_embedded_srt` 808 (was `resolve` 3,646) | 0 / 0 | 4 -> 4 | `resolve`, `list_candidates`, `handle_download` split (3 state objects), `embedded_extract` and `gender_reference` split, entry tables, one RTL core, one `make_breather`, `decode_link_safe`; NOT DONE: `he_sub_match` facade, 8 functions over 300 lines, 4i, 4h (owner switch) |
| **Total (Phases 0-4)** | own Python lines 55,845 (Phase 0 baseline) -> 55,572 (net -273); per phase: see the rows above (Phase 1 -2,108, Phase 2 +1,022, Phase 3 -5, Phase 4 +818) | moved and split in Phases 2 and 4 (Phase 4: 46,725 of the 49,829 nonblank lines kept verbatim) | headers, imports, state classes, dispatchers, tables (rows above) | 74 -> 185 | `translate.py` 9,706 -> `extraction.py` 1,474 | `resolve` 3,674 -> `extract_embedded_srt` 808 | settings: 106 -> 93 declared (56 visible, 37 hidden), 15 dead removed, 7 declared | 13 -> 4 | functions over 300 lines 11 -> 9, files over 1,500 lines 9 (Phase 1) -> 0; stub import 59 of 72 -> 152 of 152; open: facade, 8 functions, quick toggles |

---

## Appendix A: size and dependency facts

- Python lines by file (top): see §0. At 0.3.96: `settings.xml` 1,104 lines, 106 declared settings (55 visible, 51 hidden); 16 one-shot migrations in `service.py`; 74 Python files, 1,567 functions (nested included).
- Internal import graph (AST): `translate` imports 25 internal modules; `default` 17; `service` 13; `subsync` 10; `subs_engine/engine` 12 (its sources); `he_sub_match` 6 (lazily, POV-safe).
- pyflakes: 52 unused imports / variables, 5 `global` without assignment, 17 f-strings without placeholders, 4 invalid escape sequences (SyntaxWarning), 4 redefinitions. vulture (≥ 60%): 50 candidates, about 30 real after removing event-method and external-API false positives.

## Appendix B: items to verify in Kodi (when the relevant phase runs)

1. The native-dialog toggle row: close + `AlarmClock` reopen on FENtastic, AF3 and Estuary; no "download failed" message.
2. A setting written by the plugin process is seen by the service thread through the cached `xbmcaddon.Addon` (`core.addon.addon()`).
3. The chooser radio buttons work with a remote (focus order 100 → 210-214 → 203 → 202 → 201).

## Appendix C: Kodi checklist for Phase 3 (nothing below was tested in Kodi)

1. Service start: the log shows the add-on's lines at the configured `log_level` and a toast appears (the cycle break, `core/addon.py`); a setting written by the plugin process is seen by the service thread through the cached handle (Appendix B item 2).
2. Search with the 7 providers on a movie and an episode: the list opens, the rows keep their order (Hebrew first, then AI rows), the stars of the native dialog (FENtastic / AF3 / Estuary) follow the percentage; a row without a percentage (AI offer, built-in foreign stream) keeps its old star count.
3. The SAME title: the match % on the picker rows, the source-screen badge (POV / Umbrella through `he_sub_match`) and the autosub pick agree (the point of 3b; the policies still differ for a wrong-episode `picked_release` and a debrid-URL `label`, see Part 4).
4. The chooser window (button "בחר כתוביות"): opens from the player, shows the header release name, rows with flags and %, the pick applies, the XML chooser and the pyxbmct fallback both work (3d removed the `translate` argument); close and reopen.
5. Download / `translate_file` / `bg_translate_picker` / `connect_gemini` and the settings buttons (Gemini, pool, cache, engine test): each action runs once (`pipeline.resolve` route); an AI translation end to end, a sync retime, an embedded MP4 subtitle, the `gp_later` queue after a busy proofread, deep verify.
6. Autosub on play for a title with and without Hebrew, a debrid stream (token-like `filepath`) and a local file.
7. Phase 1 leftovers: the 9 runtime strings (ids 32630-32638) in dialogs and toasts, the 8 settings categories, the `ai_when_no_hebrew` labels, the dialog title "Kodi POV IL" everywhere.
8. Pool: the community pool lookup and a contribution use the `pool` policy (a debrid URL is never shared as a release name); the engine search cache and the cached-subtitles folder still land in `engine_cache/` and the cached-subs folder of the profile.
9. Quick toggles (3f) are NOT in this build: nothing to check.

## Appendix C (Phase 4): Kodi checklist for the flows Phase 4 changed (nothing below was tested in Kodi)

1. AI translation end to end (`resolve` stages): an English source translated to Hebrew with Gemini; a Google fallback (Gemini key empty); a cache hit (second run); a pool hit; an `embedded_ai` pick (background job through `bg_translate_picker`); an engine "translate from" pick; the gender-reference path for a title with Arabic / Hebrew gendered dialogue; progress bar and toasts in order; cancel in the middle.
2. Search (`list_candidates` stages): the 7 providers on a movie and an episode, rows in the same order as before the update (Hebrew, then AI offers), pool rows, local files, the embedded "built-in" rows; an empty result.
3. `download` (`DownloadState` stages): a Hebrew subtitle; an embedded Hebrew pick (stream switch, no item); an "ai" pick (dialog closes at once, translation in the background); the fast first-chunk path (setting on); the delivery check ("registration") and the next-Hebrew fallback when a download fails.
4. Embedded extraction (the 14-module package): an MKV on a local file and on an HTTP-range debrid URL; an MP4 with a `mov_text` track; a 429 retry; a resume after a cancel; log lines at the configured level.
5. Gender reference and proofread: a title with a cast list; the `gp_later` queue after a busy proofread.
6. Entry tables: every `action=` still routes (search, manualsearch, download, translate_file, bg_translate_picker, he_avail, choose_subs, connect_gemini, the settings buttons); service start runs the one-shot migrations in their old order (4 before the prewarm, 10 after) and shows no new toast.
7. RTL: the 3 delivery paths (named subtitle delivery, an in-place fix of a local `.srt`, the cached `he.srt`) render punctuation and glyphs as before; a `.vtt`.
8. `he_sub_match` in POV and in Umbrella: a source-screen badge appears on the first open and after a warm-up (the file's API is unchanged; only `dbg` / `warm_queue_dir` were renamed, used by the service's warm-queue drainer).
9. Sync: a retime on a pair with a known offset and a deep verify (they use the shared `make_breather`: the player must not stutter during a long probe).
10. Quick toggles (4h) are NOT in this build: nothing to check.
