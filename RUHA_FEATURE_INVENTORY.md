# RUHA — Feature Inventory and Status

Inspection only. Evidence sources: source code, the SQLite file (read-only), files on disk, and screenshots of generated images. "Verified" means there is **real evidence on this PC** (completed jobs in `processing_jobs`, real output files, or my own run with real ffmpeg). Unit tests with fake AI back-ends are **not** counted as real verification of an AI feature.

Status key: **VERIFIED** = IMPLEMENTED AND VERIFIED · **UNVERIFIED** = IMPLEMENTED BUT NOT VERIFIED · **PARTIAL** · **UI-ONLY** · **MISSING** = NOT IMPLEMENTED · **BLOCKED** = BLOCKED BY DEPENDENCY

## A. Video translation

| Feature | Status | Source | Evidence / limits |
|---|---|---|---|
| Video upload / import | VERIFIED | `api/projects.py`, `services/projects.py` | 3 source videos in DB; 20 GB cap (`RUHA_MAX_UPLOAD_GB`) |
| YouTube link download | VERIFIED | `ai/ytdl.py`, `handlers.h_download` | 3 `video_download` jobs completed; yt-dlp + deno |
| Language detection | VERIFIED (English only) | `ai/whisper.py` | All 3 transcripts `en`; non-English never run here |
| Source-language selection | UNVERIFIED | `handlers.h_translate` param `source` (default "Auto-detect") | passed to translator |
| Target-language selection | UNVERIFIED | 15 languages in `LANG_CODES`, 19 in `yt/service.LANGUAGES` | |
| Translation engine (IndicTrans2) | UNVERIFIED | `ai/translator.py`, translator_worker, `C:\Dev\indic-video-translator\app.py` | `translations` table has **0 rows**; only the en→indic 200M model is cached; indic→en and indic→indic folders are empty (0 MB) |
| Audio dubbing (Indic Parler-TTS) | UNVERIFIED / BLOCKED for speed | same | model cached (3.6 GB); translator torch is CPU-only, so dubbing would be very slow |
| Subtitle generation (SRT/ASS) | VERIFIED | `services/subtitles.py` | 16 SRT files; stacked ASS captions rendered into real video and viewed |
| Subtitle translation for reels | UNVERIFIED | `handlers._translate_lines` (LLM) | never run on real non-English output |
| Subtitle burning | VERIFIED | `media.render_reel` (`ass` filter) | pixel-diff test + frame viewed |
| Video rendering | VERIFIED | `services/media.py` | 8 `video_rendering` + 6 `reel_generation` jobs done; 17 mp4 files |
| Export / download | VERIFIED | `api/assets.py` (FileResponse, range) | |

## B. Video creation

| Feature | Status | Source | Notes |
|---|---|---|---|
| Clip selection | VERIFIED (quality varies) | `services/planner.py` (`_snap`, chunk map/reduce) | one run returned 0 main reels (`PlanError`) |
| Reel generation (main + shorts) | VERIFIED | `handlers.h_reels` | 1 main + 2 short per plan; 9:16 1080×1920 H.264/AAC |
| Hook / closing cards | VERIFIED | `handlers._reel_cards`, `htmlrender.render_card` | old cards still show sentence hooks until re-rendered |
| One-word hook rule | UNVERIFIED on real LLM | `planner.one_word_hook` | unit-tested; enforced in code |
| Stacked captions (new) | VERIFIED (render + frame viewed) | `subtitles.py` | only on newly rendered reels |
| Animated cutaways (new) | UNVERIFIED on real project | `cutaways.py`, `media.render_reel` | synthetic-video tests + one frame viewed; not yet run on a real sermon |
| Image-slideshow reel | UNVERIFIED | `handlers.h_image_reel`, `media.render_image_reel` | tests only; no `image_reel` asset in DB |
| Intro/outro, watermark, music | UNVERIFIED | `media.render_reel`, `brand.py` | tests only |
| Video composition beyond reels (16:9 long video) | MISSING | — | no landscape/full-video output |
| YT story video (narrate + scenes) | VERIFIED once | `yt/service.py`, `yt/story_video.py` | 1 `yt_narrate` + 1 `yt_video` job completed |
| GPU encoding (NVENC) | BLOCKED | `media.nvenc_available` | no NVIDIA GPU |

## C. AI content generation

| Feature | Status | Source | Notes |
|---|---|---|---|
| Transcript summarisation | VERIFIED | `planner.py` + Ollama qwen2.5:3b | plan JSON stored in `transcripts.plan_json` |
| Content/sermon analysis (themes, quotes, key points) | VERIFIED | same | quotes verbatim-checked; scripture refs validated against speech |
| Hook generation | VERIFIED (3B quality) | planner | |
| Caption + hashtag generation | VERIFIED | planner | |
| Encouragement posts | VERIFIED | `handlers.h_social`, de-dup via memory | 12 `social_post_generation` jobs done |
| Sermon poster | VERIFIED | `handlers.h_poster` | |
| Instagram graphics (feed/square/story) | PARTIAL | `design.py` templates | single template look; Creative Studio gives 4 formats (minimal style only, in practice) |
| YouTube thumbnails | UNVERIFIED | `creative/formats.py` `thumbnail` 1280×720 | no thumbnail asset has been generated |
| Creative Studio — typographic style (`minimal`) | VERIFIED | `creative/*` | 4 assets generated |
| Creative Studio — 7 photo styles | BLOCKED | `creative/backends.py` | no torch/diffusers, no NVIDIA GPU, no ComfyUI |
| AI backgrounds (older path) | BLOCKED | `ai/imagegen.py`, `design.ai_background` (off) | same |
| Per-project logo + text templates (new) | VERIFIED by tests; UI not viewed in browser | `services/brand.py`, `components/BrandKit.tsx` | |

## D. Management

| Feature | Status | Source | Notes |
|---|---|---|---|
| Project management | VERIFIED | `api/projects.py` | 3 projects in DB (one is a test project) |
| Asset library, filtering, delete | VERIFIED | `api/assets.py`, `Library.tsx` | 27 assets |
| Generated content history / versions | VERIFIED | `asset_versions` (52 rows) | rollback code present |
| Database persistence | VERIFIED | SQLite WAL, 23 tables | migrations: 1 applied |
| Processing queue, cancel, retry, resume | VERIFIED | `jobs.py` | failed jobs recorded with reasons; dependent jobs auto-skipped |
| Approval workflow | PARTIAL | `api/assets.py`, `Review.tsx`, `approvals` | 1 approval row; no publishing integration |
| Feedback loop | PARTIAL | `feedback` table | 1 row; outcomes fed to memory via `set_outcome` |
| Content calendar | UNVERIFIED | `Calendar.tsx`, `content_calendar` | 0 rows |
| Analytics / insights | UNVERIFIED | `services/insights.py`, `Analytics.tsx` | derived from existing records only |
| AI memory (Chroma) | VERIFIED | `services/memory.py` | 110 references; used by planner (`prompt_context`) and de-dup (`similar`); see technical audit §6 |
| Settings / preferences | VERIFIED | `context.ConfigStore`, `Settings.tsx` | only 2 overrides saved (`whisper.model=base`, `gpu.min_free_ram_gb=0.2`) |
| Brand & templates | VERIFIED | `brand.py` | 5 default templates seeded |
| Backups / restore | UNVERIFIED | `services/backup.py` | `ruha_data/backups` is empty |
| Publishing to Instagram/YouTube | MISSING | — | export only; no platform API |
| Multi-user / login | MISSING | — | single-user, localhost only (`security.py` path guards) |

## Summary counts

VERIFIED ≈ 26 · UNVERIFIED ≈ 14 · PARTIAL ≈ 4 · BLOCKED ≈ 4 · MISSING ≈ 3 · UI-only placeholders: none identified in the code I read (I read the handlers, services and API; I did not open every UI page in a browser, so a screen that looks complete is not counted as proof).
