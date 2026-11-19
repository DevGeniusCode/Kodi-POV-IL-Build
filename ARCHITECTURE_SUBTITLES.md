# MoranSubs (`service.subtitles.kodipovilai`): architecture report and refactoring blueprint

**Status:** Subtitle Phase 0 COMPLETE (decision, last upstream merge, this document). Phases 1-3 (Part 4) are the execution plan.
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

---

## 0. Executive summary

1. **The add-on is ≈55.8k lines of Python after the 0.3.96 merge** (≈50.4k at 0.3.74; not counting the bundled `pysrt`, `parse_torrent_title` and `pyxbmct`, 3.5k). Seven files hold 60% of it: `translate.py` 9,706 · `subsync.py` 5,577 · `embedded_extract.py` 4,984 · `sync_align.py` 3,374 · `default.py` 3,348 · `srt.py` 3,292 · `subs_engine_bridge.py` 1,949.
2. **One function is the AI-translation pipeline.** `translate.resolve()` runs from line 6033 to the end of the file (3,674 lines at 0.3.96, more than 30 nested closures; `_extract_embedded_srt` 807 and `list_candidates` 616 are next). It handles every link type, cache, the community pool, the Gemini and Google translators, gender references, repairs, proofreading, telemetry, pool upload and the sync hand-off. Most of the coupling lives here.
3. **The three pillars depend on each other in a cycle.** `translate` → `subsync` → `translate`. `subsync` → `gemini` (an audio-transcription timing probe). `translate` → `sync_align` and `arabic_gender` (reference alignment). `kodi_utils` → `subs_engine_bridge` → `kodi_utils`. More than 25 private helpers are called across modules, for example `translate._decode_link` 15 times from other files.
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
| RunScript (settings buttons) | `settings.xml` action rows | `open_aistudio`, `test_connection`, `connect_gemini` (also frozen Wizard patch `pov_myservices`), `show_gemini_usage`, `open_tmdb_notice`, `clear_cache`, `pool_share_cache(_run)`, `purge_temp`, `engine_test`, `connect_telegram`, `logout_telegram`, `telegram_test` | **Yes** (action names) |
| Bare-name import from other interpreters | POV / Umbrella hooks append `…/resources/lib` to `sys.path` | `he_sub_match.py`: `prewarm`, `release_names`, `embedded_names`, `confirmed_releases`, `label_prefix`; stdlib only at module level | **Yes**: file must stay at `resources/lib/he_sub_match.py` |
| Window properties read by other processes | skins / POV / Wizard | `subs.player_filename` (`subs_filename_publisher`), `MoranSubsChooserOpen`, `povil.embedded_*`, `moransubs.*` | **Yes** (byte-identical) |
| Settings read by the Wizard | Wizard `host_repairs/kodi_utils.subs_get_*` | `use_builtin_engine`, `pov_fast_navigation` | **Yes** (ids) |
| Dead inbound call (known defect) | Wizard `profiles/build_skin.py:252` | `action=prepare_build_skin` → falls into `unknown action` | n/a |

### 1.2 Current flow (as built)

```
                         ┌─────────────────────────── KODI ───────────────────────────┐
   video starts ──► service.py (daemon)                  user opens subtitle dialog /
                    │  AutoSubPlayer.onAVStarted          skin "בחר כתוביות" button
                    ▼                                                │
         autosub_service.autosub_on_play()          default.py main() ─┬─ search ─► _handle_search
            gates: engine on · engine_autosub ·                         ├─ download ─► _handle_download
            skip_autosub · excluded add-ons · live                      └─ choose_subs ─► subs_chooser.show()
                    │                                                │            (WindowXMLDialog → pyxbmct)
                    └──────────────┬─────────────────────────────────┘                    │
                                   ▼                                                      │
          ┌──────────── translate.list_candidates(info) ─────────────┐◄─────────────────────┘
          │ local_subs.find_alongside / find_in_temp                   │
          │ subs_engine_bridge.embedded_candidates (player streams)    │
          │ subs_engine_bridge.search ─► subs_engine.engine            │  ◄── FETCHING
          │     c_get_subtitles (thread per provider, provider-down    │      (vendored DarkSubs)
          │     skip) ─► sources/{ktuvit,wizdom,opensubtitles,yify,    │
          │     subsource,subscene,bsplayer,telegram}.get_subs         │
          │     ─► sort_subtitles ─► release_match.match_pct           │  ◄── MATCH %
          │ pool.lookup (community: ktuvit / ai / ai_emb variants)     │
          │ translate._match_pct ─► release_match                      │  ◄── MATCH %
          │ SDH: sdh_registry / sdh_pool / srt.is_sdh_content           │
          │ cache.translated_path → "[CACHE]" rows                     │
          │ Hebrew labels + ordering built inline                      │
          │ _finalize ─► he_sub_match.merge_names · _mark_current      │
          └───────────────────────────┬───────────────────────────────┘
                                      ▼  list[dict] (label carries the %)
           default: ListItem(label=lang, label2=label, rating parsed from "%" in label)
                                      │  user picks → plugin://…?action=download&link=<json>
                                      ▼
   _handle_download: subsync.cancel_pending · kodi_utils.current_subtitle_selection (token triple)
       ├─ engine+embedded ─► subs_engine_bridge.select_embedded ─► Dialog.Close
       ├─ engine_ai ─► subs_engine_bridge.download ─► becomes 'ai' link
       ├─ embedded_ai / ai ─► RunScript(bg_translate_picker) ─► Dialog.Close
       ├─ fast_first_chunk ─► _try_fast_download (English now, Hebrew later)
       └─ passthrough / pool / engine / embedded_sync ─► translate.resolve()  (synchronous)
                                      ▼
   ┌──────────────────────────── translate.resolve() (≈3,400 lines) ───────────────────────────┐
   │ per kind: passthrough · pool (pool.fetch, Ktuvit pool twin) · embedded_sync               │
   │ engine (subs_engine_bridge.download) · embedded_ai (embedded_extract) · ai                │
   │ AI ─► source prep (strip HI, SDH learn) ─► cache lookup (content hash, tier) ─► pool reuse │ ◄── AI TRANSLATION
   │     ─► Google mode? ─► prompt.build + tmdb_helper cast ─► arabic_gender references         │
   │        (fetches reference subs THROUGH the engine, aligns them = its own aligner)          │ ◄── SYNC (2nd aligner)
   │     ─► srt.chunk_blocks / srt_wire ─► parallel _translate_one / _call_gemini ─► gemini     │
   │     ─► repairs: english_residual · script_residual · gender_guard · he_person_gender       │
   │        · google fallback ─► stitch ─► srt.* post-fixes, RTL ─► cache.save_text             │
   │     ─► gender_proofread (stronger model, or deferred via proofread_later)                  │
   │     ─► telemetry ─► pool.contribute_once                                                   │
   │ every file ─► _rtl_delivery_copy ─► _sync_hebrew_delivery ─► subsync.process()             │ ◄── SYNC
   └──────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                              ▼
   subsync.process: verdict cache · community verdict (pool.sync_map) · oracle candidates
     (subs_engine_bridge.search_all_languages_for_timing) · sync_align.verify / apply_verdict ·
     probes (mkv_probe · mp4_probe · embedded_extract cues · Gemini audio probe) · deep job queue
                                              ▼
   default: _try_next_hebrew fallback ─► _deliver_named_subtitle (player.setSubtitles, unique
            name) or addDirectoryItem ─► kodi_utils.confirm_subtitle_sync_registration ─►
            translate.proofread_later

   service.py background threads: he_warm drainer (he_sub_match.run_warm) · subsync drainer
   (drain_queue_once → run_deep_job) · subsync delay watch (finalize_delay_session /
   store_human_verdict) · pool queue drainer (pool.drain, translate.process_harvest_queue) ·
   shared SDH refresh · SubsFilenamePublisher · daily cache prune · 13 one-shot migrations
```

### 1.3 Stage-by-stage trace (files, functions, data)

| # | Stage | File · function (line) | Input → output | Notes / coupling |
|---|---|---|---|---|
| 1 | Invocation | `default.py` · `main` (3279), `_parse_query` (38) | argv → `action`, params | Handles both plugin and RunScript call styles |
| 2 | Opt-out gate | `default._handle_search` (112) | `Window(10000).skip_autosub` (fresh < 90 s) | Hardcoded 90 s |
| 3 | DarkSubs hook (dead) | `default._handle_search` (138-145), `_handle_test_connection` (2081) | imports `dark_subs_integration` | **Module missing**, the call sits in `try` |
| 4 | Video context | `kodi_utils.current_video_info` + `subs_engine_bridge._release_ready` | player labels → `info` dict | Waits up to 15×200 ms; calls a private function across modules |
| 5 | Candidate assembly | `translate.list_candidates` (1001-1616) | `info` → `list[candidate]` | About 600 lines: fetch, rank, label and policy in one function |
| 5a | Local files | `local_subs.find_alongside`, `find_in_temp` (+ `language_detect`) | path → `(path, lang)` | Hebrew from temp deliberately refused |
| 5b | Embedded streams | `subs_engine_bridge.embedded_candidates` (1169), snapshot `povil.embedded_snap` | player streams → embedded rows (`_pct` 101) | |
| 5c | Provider search | `subs_engine_bridge.search` (331) → `_search_inner` (672) → `engine.c_get_subtitles` (408) → `sources/*.get_subs` → `engine.sort_subtitles` (82) | `video_data` → tuples `(label, label2, icon, thumb, url, percent, sync, hi, filename, site_id)` → candidates tagged `_engine_kind` ∈ {human_he, mt_he, other} | Providers report results by appending to a module-global `global_var`; search lock; 24 h disk cache; 120 s "recent" window property; providers that are down are skipped for 600 s |
| 5d | Synthetic rows | `list_candidates` (1139) | `embedded_sync` row | Hebrew label text hardcoded |
| 5e | Ktuvit harvest | `pool.enqueue_harvest` | queue file | Side effect inside a list builder |
| 5f | Community pool | `pool.lookup` (+ `_match_pct`) | `info` → variants | |
| 5g | Ranking and labels | `list_candidates` 1185-1594, `_is_sdh_ext` (376), `_gender_src_rank` (370) | → ordered rows | Match % is formatted into the label |
| 5h | Finalize | `_finalize` (833) → `he_sub_match.merge_names`; `_mark_current` (802) → `kodi_utils.get_subtitle_sync_status` | → rows + "» נוכחית" | The list builder also writes the badge cache |
| 6 | Render | `default._handle_search` 172-207 | rows → `ListItem`s | **Star rating is regex-parsed out of the label**; sync label appended |
| 7 | Pick / selection | `default._handle_download` (210); `kodi_utils.current_subtitle_selection` | link → selection `{token, link_hash, stream_hash}` | A race guard that gets passed through every layer |
| 8 | Dispatch by link type | `_handle_download` 247-436 | link payload `type` | 7 link types (§1.4) |
| 9 | Resolve | `translate.resolve` (3951-7372) | link → SRT path | The single largest coupling point |
| 9a | Delivery copy | `_rtl_delivery_copy` (632), `_reapply_rtl_fix_in_place` (549) | file → RTL copy | `subs_engine_bridge._render_hebrew_rtl_copy` is a second RTL renderer |
| 9b | Sync gate | `_sync_hebrew_delivery` (688) → `subsync.process` (1686) | path, release → retimed path + verdict | Fail-open; `embedded_timing` bypass |
| 9c | AI translation | `resolve` 4469-7372 | source SRT → Hebrew SRT | §1.5 lists its sub-stages |
| 10 | Deliver | `_deliver_named_subtitle` (697), `_try_next_hebrew` (792), `addDirectoryItem` | path → Kodi | Registration confirmed through `kodi_utils.confirm_subtitle_sync_registration` |
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

**Selection triple** `{token, link_hash, stream_hash}`. It is created in `kodi_utils`, re-checked by `default`, `translate`, `subsync`, `autosub_service` and `subs_chooser`, and stored in Window(10000) properties.

### 1.5 Coupling audit

#### 1.5.1 Pillar × file matrix (● main owner, ○ also implements part of it)

| File | Fetching | Match % | AI translation | Sync | Cache | Delivery / UI | Settings / migrations |
|---|---|---|---|---|---|---|---|
| `default.py` | ○ engine test, Telegram | ○ star parse | ○ 460-line bg job + Gemini pairing UI | ○ cancel / registration | ○ clear cache | ● | ○ |
| `service.py` | | | ○ model-bump migrations | ○ drainers, delay watch | ○ prune | ○ publisher | ● 13 migrations |
| `translate.py` | ○ list build, harvest | ○ `_match_pct`, labels | ● | ○ `_sync_hebrew_delivery`, `_embedded_aligned_source_srt`, calls `subsync._oracle_candidates` / `_download_oracle` | ○ tiers / markers | ○ labels, RTL copies | ○ policy reads |
| `subs_engine_bridge.py` | ● | ○ `_rating_for`, release detect | | ○ timing search | ○ 3 caches | ○ RTL render, `select_embedded` | ○ engine defaults |
| `subs_engine/engine.py` | ● | ○ `sort_subtitles` | ○ dead Bing / Yandex / Google MT | | ○ | | |
| `subsync.py` | ○ oracle downloads | ○ tier ranks | ○ **Gemini audio probe**, imports `translate` | ● | ○ verdict / probe caches | ○ `status_line` | |
| `sync_align.py` | | | | ● | | | |
| `arabic_gender.py` | ○ reference fetch through the engine | | ● | ○ **its own aligner** (`_TOL`, `_MAXOFF`, windows) | | | |
| `release_match.py` | | ● | | ○ `AUTO_OK_TIERS` | ○ memo | | |
| `he_sub_match.py` | ○ pool / Wizdom / OS / Ktuvit warm | ○ badge % | | | ○ 3 caches | ○ badge labels | |
| `kodi_utils.py` | | | | ○ sync status, staged fixes | | ● selection / registration | ● settings |
| `pool.py` | ○ pool fetch, harvest | | ○ contribute, proofread variants | ○ `/sync` registry | ○ lookup cache | | |
| `srt.py` | | | ● post-processing (3.3k lines) | ○ `_block_start` / `_block_times` | | ○ RTL | |
| `subs_chooser.py` | ○ | ○ | ○ `_start_ai_apply` | ○ | | ● | |

#### 1.5.2 Concrete tangles (with evidence)

1. **`resolve()` as the god function** (`translate.py:3951-7372`). Its closures cover selection guarding, Gemini retries and rate pacing, content-block fallbacks, gender verification, repairs, proofread scheduling, telemetry counters and pool upload. None of them can be tested or replaced without loading all 7,372 lines.
2. **Import cycles:** `translate ⇄ subsync`, `translate ⇄ arabic_gender` (arabic_gender imports translate), `kodi_utils ⇄ subs_engine_bridge` (`kodi_utils.py:1311`), `subsync → translate`, `he_sub_match → subs_engine_bridge`. They only work because the imports sit inside functions.
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
8. **`kodi_utils` is three modules in one.** It holds the settings facade, the logging/notify toolkit, and the whole subtitle-selection / registration / sync-status state machine (about 35 functions on Window(10000) properties).
9. **`default.py` is three modules in one.** It holds the router, the delivery / background-translation runtime (`_handle_bg_translate_picker`, `_handle_translate_file`, about 1,100 lines) and the settings-action UIs (Gemini pairing window, Telegram, engine test).
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
| `subs_engine/sources/telegram_impl.py` | `telegram_helper_window`, `async_upload_subtitle`; imports a missing `telegram_api` | Baseline import failure (known) |
| `subs_engine/sources/opensubtitles.py:472` | `get_random_key` | Unused |
| `local_subs.find_in_temp` | "SRTs dropped by other subtitle add-ons (DarkSubs…)" | Still useful for other add-ons; keep, but update the comment |
| repo `userdata/addon_data/service.subtitles.All_Subs/`, `all_subs_plus/` | empty local folders (untracked) | Local leftovers, not in git |

**Unused code inside the former upstream files** (ours now: in scope for Phase 1)

`translate._build_prev_context_by_idx` (2956); `subsync._remote_cue_reference` (687), `_probe_reference_cues` (1322), `proven_pool_ai_fallback` (1649), `_pending_key` (4317); `arabic_gender.prepare` (1749) plus unreachable code at 632; `prompt.build_arabic_gender_block` (406); `cache.total_size_mb`; `embedded_extract.probe_tracks`; `mp4_probe.subtitle_reference_source`; `kodi_utils.subtitle_sync_status_label`, `progress_dialog`; `gemini_pair.get_lan_ip`.
**Not dead:** `he_sub_match.prewarm` / `embedded_names` / `confirmed_releases` / `label_prefix` look unused here but POV / Umbrella hooks call them. Event methods (`onAVStarted`, `onClick`, `do_GET`) are vulture false positives.

**Unused imports and variables** (pyflakes): `default.py:1588` (`kodi_utils`), `2284` (`srt`), `3167` (`engine`, deliberate import probe); `kodi_utils.py:9` `sys`; `release_match.py:21` `os`; `autosub_service.py:906` (deliberate preimport); `service.py:395-396` (deliberate preimport of `opensubtitles` / `ktuvit`), `service.py:545` `xbmcgui`; `subs_chooser.py:118` `payload`; `translate.py:4196` redefined `_pshare`; plus 40 hits inside the vendored engine (`xbmcplugin`, `pkgutil`, `threading`, `quote*`, `DEFAULT_REQUEST_TIMEOUT`, `struct`, `random`, unused `Addon` locals, f-strings without placeholders, invalid escape sequences `\s` / `\d` in `cache.py:51`, `general.py:193-195`, `ktuvit.py:296`, which become SyntaxErrors in a future Python).

**Known defects already in `CLAUDE.md`** (still present): the `prepare_build_skin` inbound action has no handler; `source_memory.py`, `dark_subs_integration.py` and `fentastic_search_patcher.py` are missing; `manifest.json` pins the subtitle zip at 0.3.50 while the source is 0.3.74; `telegram_impl` needs `telegram_api`.

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
        │   ├── contracts.py          # VideoContext, Candidate, LinkPayload, Selection, DeliveryResult, SyncVerdict
        │   ├── link_codec.py         # ex translate._encode_link / _decode_link
        │   ├── video_context.py      # ONE playing-release derivation (replaces the 5 copies)
        │   ├── selection.py          # token triple, ownership checks (ex kodi_utils)
        │   ├── settings.py           # typed accessors + defaults table (one place)
        │   ├── kodi.py               # log / notify / window props (ex kodi_utils)
        │   ├── utils/                # small shared helpers (text, time, files)
        │   ├── srt/                  # ONE parser + writer (ex srt.py core, sync_align.parse_srt); post-fix passes by concern
        │   └── cache/                # one cache API (namespaces, TTL, size cap; ex cache.py + the private caches)
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
        │   └── settings_actions/     # gemini pairing, telegram, engine test, cache actions
        ├── pyxbmct/                  # bundled, chooser fallback (unchanged)
        └── telemetry.py
```

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

In Phase 2 every feature service starts as a **pure delegation** to the moved function (for example `fetching.search` → `bridge.search` + `local` + `pool_client.lookup`), and `default.py` / `translate.list_candidates` / `resolve` are split along those seams by line ranges. That is a behaviour-neutral structural step. Logic changes (for example one `playing_release` instead of five) are separate, owner-approved steps.

#### 1.7.4 Contracts (sketch)

```python
@dataclass(frozen=True)
class VideoContext:   # built once by core.video_context
    imdb_id: str; tmdb_id: str; title: str; year: str
    season: str; episode: str; tvshow: str
    playing_release: str          # the ONE derivation (picked_release > tagline > label > li_filename > filepath)
    file_url: str; is_local: bool; is_live: bool
    raw: dict                     # original kodi_utils.current_video_info() for adapters

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
                         subscene, bsplayer, telegram} · _libs/{pysrt, parse_torrent_title})
   contract: global_var · site_id · get_subs(video_data[, all_lang_override]) · download(download_data, MySubFolder)
```

### 1.8 File-by-file mapping

Legend: **M** move the whole file as-is (imports rewritten) · **S** split by line ranges · **D** delete (Deletion Report) · **P** pinned.

| Current file (lines at 0.3.96) | Phase 2 action | Target |
|---|---|---|
| `default.py` (3,348) | P + S | stays as a ≈50-line router; `_handle_search` → `ui/listing.py`; `_handle_download`, `_deliver_named_subtitle`, `_try_next_hebrew`, `_try_fast_download` → `delivery/kodi_player.py`; `_handle_bg_translate_picker` / `_handle_translate_file` → `ai_translation/background.py`; Gemini pairing (`_PairWindow`, `_gemini_*`), Telegram, engine test, cache buttons → `ui/settings_actions/*`; the action table → `entry/actions.py` |
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
| `kodi_utils.py` (1,385) | S | `core/{settings,kodi,selection}.py` |
| `autosub_service.py` (1,124) | S | `autosub/{player,policy}.py` |
| `subs_chooser.py` (1,071), `subs_filename_publisher.py` | M | `ui/chooser.py`, `ui/filename_publisher.py` (strings unchanged; update `CLAUDE.md` in the same step) |
| `gemini.py`, `gemini_pair.py`, `google_translate.py`, `gemini_quota.py` | M | `ai_translation/providers/*`, `ai_translation/quota.py` |
| `prompt.py`, `srt_wire.py`, `english_residual.py`, `script_residual.py`, `gender_guard.py`, `gender_proofread.py`, `gp_later.py`, `he_person_gender.py` | M | `ai_translation/{context,dispatch,repairs,proofread}/` |
| `pool.py`, `sdh_pool.py`, `sdh_registry.py`, `telemetry.py` | M / S | `fetching/pool_client.py` (read) + `ai_translation/reuse.py` (contribute); `telemetry.py` |
| `cache.py`, `local_subs.py`, `language_detect.py`, `tmdb_helper.py` | M | `core/cache/`, `fetching/local.py`, `ai_translation/context/` |
| `pyxbmct/` | keep | unchanged |

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
3. Apply it through `kodi_utils.set_setting`. Run the proven close sequence. Then `AlarmClock(moransubs_reopen,ActivateWindow(SubtitleSearch),00:00:01,silent)` reopens the dialog after Kodi has finished tearing it down, which runs a fresh `action=search` with the new state. For `op=settings`: `Addon.OpenSettings(...)` after the close, with no reopen.
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
- That the cached `xbmcaddon.Addon` instance (`kodi_utils.addon()`) sees a value another process wrote (service thread vs plugin invocation). The code comment says it does; a toggle depends on it.
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
| `telegram_first_and_last_name` | `telegram_impl.py` | — | hidden (engine internal) |
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
   ├─ group sources: ktuvit, wizdom, opensubtitles, yify, subsource, subscene, bsplayer, telegram (bool L0)
   ├─ max_search_time               int   L1  10
   ├─ provider_down_minutes    NEW  int   L3  10
   ├─ engine_cache_hours       NEW  int   L3  24
   ├─ embedded_translation_mode     enum  L2  auto   (moved here from Advanced)
   ├─ embedded_max_mb          NEW  int   L3  80
   ├─ actions: engine_test, connect_telegram, logout_telegram, telegram_test
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
| **2. Core refactoring and splitting** | The §1.7.2 tree; no file over ≈1,500 lines and no function over ≈300 lines except where reported | 2a `core/` (contracts, link_codec, settings / kodi / selection out of `kodi_utils`, `srt`, `cache`) · 2b `fetching/` (`subs_engine` → `fetching/providers`, bridge, embedded, local, pool_client, `provider_adapter`) · 2c `scoring_matching/` · 2d `sync/` (split `subsync`, `sync_align`) · 2e `ai_translation/` without `resolve` · 2f `translate.list_candidates` and `resolve` split (the riskiest step, last) · 2g `ui/`, `delivery/`, `autosub/`, `entry/` + `migrations.py`; `default.py` / `service.py` become thin | None (moves by line ranges, AST identity) | per step: AST identity of every moved def, import baseline, pyflakes, a repo-wide caller grep, no shim; `translate.py` deleted at the end |
| **3. Wiring, testing and final statistics** | `pipeline.py` drives the lifecycle through contracts; tests guard it; the docs tell the truth | 3a `pipeline.py` orchestrator wired from `entry/` · 3b `Candidate.pct` as a number: stars come from it, the label regex is deleted · 3c quick toggles (§2.2; the one new feature; the owner confirms first) · 3d `.github/scripts/check_subs_contract.py` (stdlib, no Kodi) + golden fixtures (link codec, `release_match` scores, `list_candidates` ordering, `sync_align` on fixture pairs) + CI step · 3e Kodi test checklist for the owner · 3f final statistics, `CLAUDE.md` / this document updated | 3b: none visible; 3c: new UI rows / buttons | contract test green in CI; behaviour tests under stubs; the checklist delivered; Part 6 has the total |

**Rules for every phase:** `CLAUDE.md` "SUBTITLE ADD-ON: DECOUPLED" (no shims; pinned public surface; no Wizard names), STRUCTURAL REFACTORING ONLY for behaviour (report bugs, do not fix them silently), CRLF, `output.txt`, a commit snippet per step.

## Part 5: Deletion Report

Each phase appends its rows here. A row is an inactive feature, setting or block of code that was **removed**. "Revive" says what bringing it back would take, so the owner can decide. The bypassed upstream features of the Phase 0 merge are listed in `CLAUDE.md` "SKIPPED UPSTREAM FEATURES", not here.

| # | Phase | Item | Kind (feature / setting / code / comment) | Where it was (path · function, lines) | Lines removed | Why it was inactive (evidence) | Revive: what it takes | Owner decision |
|---|---|---|---|---|---|---|---|---|
| — | — | *(empty until Phase 1)* | | | | | | |

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

**Per phase** (each phase adds a row; the "Total" row is filled at the end of Phase 3):

| Phase | Lines deleted | Lines moved | Lines added | Files (before → after) | Largest file | Largest function | Dead settings removed / declared | pyflakes | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 0. Decoupling merge | 345 (subtitle add-on) | 0 | 5,855 (upstream 0.3.75-0.3.96) | 72 → 74 | `translate.py` 9,706 | `resolve` 3,674 | 0 / +1 (`_gemini_model_bump_v4`) | 13 → 13 | not taken: 2,587 `service.py` + 92 `settings.xml` upstream lines, 132-line AF3 patcher, 11-line trailer guard |
| 1. Cleanup and settings | | | | | | | | | |
| 2. Core refactoring | | | | | | | | | |
| 3. Wiring and tests | | | | | | | | | |
| **Total** | | | | | | | | | |

---

## Appendix A: size and dependency facts

- Python lines by file (top): see §0. At 0.3.96: `settings.xml` 1,104 lines, 106 declared settings (55 visible, 51 hidden); 16 one-shot migrations in `service.py`; 74 Python files, 1,567 functions (nested included).
- Internal import graph (AST): `translate` imports 25 internal modules; `default` 17; `service` 13; `subsync` 10; `subs_engine/engine` 12 (its sources); `he_sub_match` 6 (lazily, POV-safe).
- pyflakes: 52 unused imports / variables, 5 `global` without assignment, 17 f-strings without placeholders, 4 invalid escape sequences (SyntaxWarning), 4 redefinitions. vulture (≥ 60%): 50 candidates, about 30 real after removing event-method and external-API false positives.

## Appendix B: items to verify in Kodi (when the relevant phase runs)

1. The native-dialog toggle row: close + `AlarmClock` reopen on FENtastic, AF3 and Estuary; no "download failed" message.
2. A setting written by the plugin process is seen by the service thread through the cached `xbmcaddon.Addon` (`kodi_utils.addon()`).
3. The chooser radio buttons work with a remote (focus order 100 → 210-214 → 203 → 202 → 201).
