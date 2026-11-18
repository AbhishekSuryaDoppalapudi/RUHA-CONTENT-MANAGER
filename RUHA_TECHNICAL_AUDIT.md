# RUHA (El Ruach Manager) — Technical Audit

Inspection only. No source, database, configuration, model, package or service was changed. The only files written are the four audit reports. The database was opened **read-only**. Credentials and personal data are not reproduced.
Companion files: `RUHA_FEATURE_INVENTORY.md`, `RUHA_IMAGE_GENERATION_AUDIT.md`, `RUHA_ARCHITECTURE.md`.
Tags: **[observed]** = read/seen/measured; **[inferred]** = my reasoning; **[assumed]** = not checked.

---
## A. Executive summary

- RUHA is a working, local-first FastAPI + React app that turns a sermon video into a transcript, a content plan, reels (with hook/closing cards and captions), a poster and encouragement posts, with a library, approvals, versions and a vector memory. **[observed]** Real jobs have completed: 10 transcriptions, 6 reel runs, 10 poster runs, 12 social runs, 8 renders, 3 downloads.
- The **design quality is limited by architecture, not bugs**: the main pipeline has 9 procedural gradient backgrounds and one HTML template; there is no image source. **[observed]**
- The **AI image engine has never run on this machine**: no NVIDIA GPU, no torch/diffusers in the main venv, no image weights, no ComfyUI. **[observed]** The code for it is substantial and careful but unproven end to end.
- **Translation/dubbing has never produced a record** (`translations` = 0 rows) and its environment has CPU-only PyTorch. **[observed]**
- The machine inspected is a Ryzen 5 5500U laptop with 15.3 GB RAM (2.2 GB free at inspection), an AMD iGPU, no CUDA. The target RTX 3060 PC is **not** this machine. **[observed]**
- Reliability weak spots: RAM guard is overridden to 0.2 GB, 13 of 69 jobs failed (mostly cascades from one cause), a 3B LLM sometimes returns an unusable plan, 51 broad `except Exception` blocks, and a legacy Streamlit tree is still in the repo.

---
## B. Existing application architecture

See `RUHA_ARCHITECTURE.md` (includes the Mermaid diagram).
- Backend `ruha/` ≈ 8,200 lines Python across 5 packages (`api`, `ai`, `services`, `creative`, `yt`) + `handlers.py` (743), `jobs.py` (242), `models.py` (312), `db.py`, `context.py`, `config.py`.
- Frontend `frontend/src` ≈ 2,300 lines TS/TSX, 13 pages, built to `frontend/dist`.
- Tests: 11 files, ≈ 1,800 lines, all using fake AI back-ends (`tests/conftest.py`).
- Config: `.env.example`; runtime settings in table `model_settings` with defaults in `config.py` (`DEFAULT_CONFIG`, ~49 keys); env vars `RUHA_DATA_DIR`, `RUHA_HOST`, `RUHA_PORT`, `RUHA_MAX_UPLOAD_GB`, `RUHA_TRANSLATOR_DIR`, `RUHA_TRANSLATOR_PYTHON`, `RUHA_BROWSER`, `HF_HOME`.
- Dependencies: `requirements.txt` (fastapi, uvicorn, sqlalchemy<2.1, pydantic, pillow, psutil, requests, chromadb, faster-whisper, yt-dlp, deno, av==16.1.0, pytest, httpx); optional `requirements-gpu.txt` (diffusers, transformers, accelerate, safetensors) **not installed**.

---
## C. Feature inventory
See `RUHA_FEATURE_INVENTORY.md` for the full matrix. Headline: transcription, planning, reels, captions, posters, posts, library, versions, queue, memory are verified on real data; translation/dubbing, thumbnails, calendar, analytics, backups, image-reels and cutaways are implemented but not verified on real data; AI image generation is blocked by missing hardware/packages; long-form 16:9 output and publishing are not implemented.

---
## D. Image-generation engine
See `RUHA_IMAGE_GENERATION_AUDIT.md`. Short version: HTML/CSS → headless Edge screenshot over a Pillow-drawn gradient with one of 9 motifs; fixed layout; no AI pictures; Creative Studio's AI path cannot run here.

---
## E. AI model inventory

| Model | Type | Purpose | Runtime / location | Size | Hardware need | Status |
|---|---|---|---|---|---|---|
| faster-whisper **base** | ASR | transcription (set in DB; code default is `small`) | in-process, CTranslate2, **CPU int8**; HF cache `models--Systran--faster-whisper-base` | 141 MB | CPU OK; GPU optional | **Installed, used** |
| faster-whisper **tiny** | ASR | spare | HF cache | 75 MB | | Installed, not configured |
| faster-whisper **small** | ASR | code default | **not in cache** | ~460 MB | | Referenced only (would download) |
| **qwen2.5:3b** (Q4_K_M) | LLM | plan, hooks, captions, brief, subtitle translation | Ollama server, local, `~\.ollama\models` | 1.9 GB | ~3 GB RAM/VRAM | **Installed, used**; Ollama running |
| **nomic-embed-text** (137M, F16) | Embedding | memory search/dedup | Ollama | 274 MB | small | **Installed, used** |
| hash512 embedder | Embedding fallback | memory when Ollama lacks model | in code | — | none | Implemented |
| **ChromaDB** | Vector DB | memory | `ruha_data/chroma` (2.1 MB) | 2 MB | none | **Installed, used** |
| **IndicTrans2 en→indic dist-200M** | Translation | translator | HF cache | 1.06 GB | CPU slow / GPU | Installed, **never run via RUHA** |
| IndicTrans2 indic→en 200M, indic→indic 320M | Translation | translator | HF folders exist, **0 MB** | — | | **Referenced only (not downloaded)** |
| **Indic Parler-TTS** | TTS | dubbing + YT narration | HF cache | 3.6 GB | GPU strongly advised | Installed; one YT narration job completed (so it probably ran once, **[inferred]** — I did not trace the job's log) |
| flan-t5-large | NLP | Parler dependency stub | HF cache | 3 MB (config only) | | Partial |
| Stable Diffusion 1.5 / SDXL / SDXL-Turbo | Image | Creative Studio, AI backgrounds | `creative/hardware.MODELS`, `ai/imagegen.py` | 4–7 GB | NVIDIA ≥4–10 GB VRAM | **Referenced only — not installed** |
| ComfyUI | Image server | alternative back-end | expected `127.0.0.1:8188` | — | | **Not running** |

Whisper models found in the HF cache = tiny and base **[observed]**.

---
## F. GPU and hardware readiness [observed]

| Item | Result |
|---|---|
| NVIDIA GPU | **None detected** (`nvidia-smi` not found) |
| Display adapter | AMD Radeon Graphics (iGPU), `AdapterRAM` 512 MB |
| NVIDIA driver / CUDA toolkit (`nvcc`) | Not present |
| PyTorch (main venv) | **Not installed** |
| PyTorch (translator venv, Python 3.12) | 2.15.0 **+cpu**, `cuda_available=False` |
| ctranslate2 CUDA devices | not probed (no NVIDIA driver) |
| CPU | AMD Ryzen 5 5500U |
| RAM | 15.3 GB total, **2.2 GB free** at inspection |
| Disk free | C: 592 GB, F: 278 GB, E: 22.9 GB |
| ffmpeg | 9.0.2 full build; NVENC unavailable |
| Browser for rendering | Edge and Chrome present |

Missing for RTX-3060 use (**[inferred]** for the target PC): NVIDIA driver; CUDA-enabled PyTorch in both the main venv and the translator venv; `requirements-gpu.txt` packages; model downloads (SDXL ≈ 7 GB, SD 1.5 ≈ 4 GB); Ollama will use the GPU automatically. 12 GB VRAM on a 3060 (or 6 GB variant) changes `hardware.recommend`: ≥10.5 GB → SDXL on GPU, 7.5–10.5 GB → SD 1.5, 3.5–7.5 GB → SD 1.5 with offload.

---
## G. Database and memory

- **Engine:** SQLite via SQLAlchemy 2.0, WAL mode, file `ruha_data/ruha.db` (~0.6 MB + WAL). Migrations: `schema_migrations` has 1 row (`make_initial_migration.py`).
- **23 tables** (row counts): approvals 1, asset_versions 52, audit_logs 42, brand_settings 0, content_calendar 0, content_tags 0, feedback 1, generated_assets 27, job_logs 310, memory_references 110, model_settings 2, posters 2, processing_jobs 69, projects 3, reels 6, schema_migrations 1, social_posts 4, source_videos 3, subtitles 8, templates 5, transcript_segments 686, transcripts 3, translations 0.
- **Relationships:** projects → source_videos → transcripts → transcript_segments; projects → generated_assets (kind: main_reel 2, short_reel 4, poster 2, social_post 4, creative 4, subtitle 8, transcript 3) with `reels`, `posters`, `social_posts`, `subtitles`, `approvals`, `asset_versions`, `feedback`, `memory_references` hanging off assets; processing_jobs (with `depends_on_id`) → job_logs.
- **Assets by status:** 26 draft, 1 rejected, **0 approved**.
- **Persistent AI memory? Yes, genuinely.** `MemoryStore` (`services/memory.py`) writes embeddings to Chroma and a mirror row to `memory_references` (110: transcript chunks 78, themes 12, captions 6, posts 6, quotes 6, summaries 2). It is **used**: `handlers.ensure_plan` injects `prompt_context` (preferences, covered topics) into the planning prompt, `similar()` de-duplicates posts, and approve/reject outcomes are stored via `set_outcome`. Limits: the feedback signal is thin (1 feedback row, 1 approval row), so learning from preferences is mostly unexercised. **[observed]**
- Search: `MemoryStore.search` / `similar` with cosine similarity; topic recommendation `recommend_topics`.
- Processing history: `processing_jobs` + `job_logs` retain failed-job reasons.

---
## H. Code-quality findings

| # | Finding | Evidence | Risk |
|---|---|---|---|
| 1 | **RAM guard effectively disabled** | DB override `gpu.min_free_ram_gb = 0.2` (default 3.0); free RAM was 2.2 GB of 15.3 GB | Whisper/Ollama/Chrome/ffmpeg together can OOM the laptop; one job already failed with "Only 0.5 GB free" |
| 2 | Cascading job failure | 13 failed jobs; root causes: RAM (1), Ollama not running (1), plan `0 main_reels` (1); the rest "Not run because job #… failed" | Whole pipeline stops on one 3B-LLM hiccup |
| 3 | Small-LLM unreliability | `PlanError: Could not produce a valid content plan: The model returned 0 main_reels; expected 1.` (job #37) | `planner.create_plan` already retries once, feeding the rejection reason back to the model; both attempts still returned 0 main reels. **Root cause not yet investigated** (clip validation/snapping may be rejecting valid clips, or the 3B model is not returning usable clips for that transcript) |
| 4 | Duplicate helpers | `_STOP` + `_key_words` in `design.py`, `htmlrender.py`, `compose.py` (3 copies) + a different `_STOP` in `subtitles.py`; `_hex` in `design.py` and `themes.py` | Drift: highlight behaviour differs by file |
| 5 | Dead code | `design.render_graphic`: after `if theme: … return`, a second `if theme:` block (lines ~300–304) is unreachable | Confusing maintenance |
| 6 | Two image-generation abstractions | `ai/imagegen.DiffusersBackground` (older) and `creative/backends.ImageService` | Divergent model choices/licences (SDXL-Turbo is non-commercial per `hardware.MODELS`) |
| 7 | Legacy tree in repo | `app/`, `services/`, `database/`, `agents/`, `storage/`, root `main.py`, `requirements-legacy.txt`, root `index.html` | Clutter; risk of importing the wrong `services` |
| 8 | `ConfigStore.get` re-reads the whole settings table every call | `context.py` `get()` → `all()` | Many DB reads per job; minor performance |
| 9 | Broad exception handling | 51 `except Exception` in `ruha/` | Failures can be swallowed (some are deliberate fallbacks) |
| 10 | Hard-coded values | default translator path `C:\Dev\indic-video-translator`; model names; per-format sizes; font choices | Portability to the RTX PC needs env vars |
| 11 | Monolithic `handlers.py` (743 lines) | mixes planning, rendering and DB writes | Harder to test/extend |
| 12 | No authentication | localhost binding + CORS list only | Fine for local use; not safe if exposed |
| 13 | Large files | 20 GB upload cap, source files kept in `projects/`; temp render dirs cleaned in `finally` | Disk growth; no cleanup policy for old versions (`ruha_data/projects` 306 MB for 3 projects) |
| 14 | GPU/RAM management | `model_lock` + unload-before-load for LLM/Whisper/image model | Sound design; unproven on GPU |
| 15 | Logging | rotating file `ruha_data/logs/ruha.log` (2 MB × 3) with secret redaction | Adequate |
| 16 | Tests use fakes for every AI back-end | `tests/conftest.py` | Green tests ≠ real AI works; see Feature Inventory |

**Test status [observed]:** the suite was being re-run during this audit (it writes only to temporary directories); results are reported in the chat message, not in this file.

---
## I. Generated-image quality findings
See `RUHA_IMAGE_GENERATION_AUDIT.md` §5. In short: legible, correctly sized, no clipping, strong brand consistency, but templated and repetitive; no imagery; hook cards that pre-date the one-word rule are text walls; the "minimal" creative has an empty lower half; the AI-photo styles have never been produced.

---
## J. Integration map
See `RUHA_ARCHITECTURE.md` §2–3.

---
## K. Missing functionality
- Any real AI-generated imagery on this PC (hardware/packages/models).
- Verified translation/dubbing run through the manager.
- Long-form (16:9) video output with branding/captions; chaptering.
- Publishing/scheduling to Instagram/YouTube (calendar is internal only).
- Multi-user/auth.
- Per-asset template/layout choice in the main poster/post flow.
- A repair strategy beyond the single retry in `create_plan` (for example a rule-based fallback plan) and an investigation of why the plan for job #37 still ended with 0 main reels after the retry.
- Real-model integration tests (everything is faked).

---
## L. Technical risks
1. **GPU migration risk (high):** both venvs need CUDA PyTorch; versions (torch 2.15, transformers 4.46.1 in the translator, diffusers ≥0.30 in main) may conflict; untested.
2. **Memory pressure (high on this laptop):** LLM + Whisper + headless browser + ffmpeg on 15 GB with the guard lowered to 0.2 GB.
3. **LLM quality (medium):** 3B model → weak hooks, occasional invalid JSON.
4. **Licensing (medium):** `sdxl-turbo` is non-commercial (code says so); the Creative Studio default for 10.5 GB+ GPUs is SDXL base (commercial allowed).
5. **Two-environment translator (medium):** separate repo and venv, absolute default path; breakage if moved.
6. **Template sameness (product risk):** content looks identical across sermons.
7. **Dependency pinning:** `av==16.1.0`, `sqlalchemy<2.1` are workarounds; upgrades can break.

---
## M. Recommended priorities (no redesign here — ordering only)

1. **Fix the safety net:** restore `gpu.min_free_ram_gb` to a sane value on the dev laptop or run heavy jobs one at a time; investigate why the plan for job #37 returned 0 main reels even after `create_plan`'s one retry, then consider a fallback plan. (Smallest effort for the RAM failure; the plan failure needs the investigation first.)
2. **Verify, don't assume:** run one real translation + dub through the manager; run one real reel end-to-end with cutaways on a real sermon; open the new Logo & Texts tab in a browser.
3. **GPU readiness on the RTX 3060 PC:** install the NVIDIA driver, CUDA PyTorch in both venvs, `requirements-gpu.txt`; run `ImageService.status()`; generate one picture per Creative style; record benchmark (`creative.benchmark`).
4. **Raise visual quality in pipeline A:** let posters/posts/cards use Creative Studio's engine and styles instead of the single template; more than 9 backgrounds; semantic keyword highlight; stop forcing all-caps for long text.
5. **Consolidate:** one image-generation abstraction, one keyword helper, remove dead code, move legacy Streamlit tree out of the repo.
6. **Add real-model smoke tests** (tiny audio, one LLM call) alongside the fake-model suite.
7. **Then** long-form 16:9 video and publishing integrations.

---
## Observed facts vs assumptions

- Observed: everything tagged above, including GPU/RAM/disk probes, Ollama and HF model lists, DB tables and counts, file listings, and four generated images viewed directly.
- Not checked: whether the NVIDIA machine already has drivers/CUDA (it is a different PC); ctranslate2's CUDA count; the content of every UI page in a browser; contents of `.env` (not opened, may hold local settings); real translation output (none exists).
