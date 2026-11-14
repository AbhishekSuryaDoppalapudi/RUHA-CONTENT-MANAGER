# Implementation checklist & verification status

Legend: **T** = covered by the automated suite (67 tests, all passing; real FFmpeg + SQLite, fake AI models) ·
**R** = verified against a real component by hand · **U** = implemented but *not yet verified on the real component* · **-** = not built

## Verified on real components
| Item | Status |
|---|---|
| faster-whisper transcription (`tiny`, CPU, real synthesised speech) | **R** - correct transcript, language detected |
| FFmpeg probing, thumbnails, reel cutting/cropping, subtitle burn-in | **R/T** |
| Server start, UI served, SPA routes, status endpoint; UI renders with brand theme | **R** (headless Edge screenshot of the dashboard) |
| Windows Application Control workarounds (SQLAlchemy 2.0, wasm rollup/esbuild) | **R** |

## Not yet verified on the real component (do this on the RTX 3060 PC)
| Item | Status |
|---|---|
| Ollama content planning (`qwen2.5:3b`) - quality of reel choices, JSON reliability | **U** (Ollama not installed on the dev machine; logic tested with a fake LLM, incl. retry, chunking, guards) |
| Ollama embeddings (`nomic-embed-text`) | **U** (offline hashing embedder is tested and is the fallback) |
| CUDA / GPU Whisper, NVENC encoding, VRAM readout | **U** (no NVIDIA GPU on the dev machine) |
| Translator bridge against the real El Ruach Video Translator models (Telugu etc.) | **U** (protocol tested with a stand-in `_translate_impl`; real IndicTrans2/Parler-TTS not run) |
| Intro/outro concatenation, watermark overlay, custom fonts, non-Latin poster text | **U** (code paths written; not exercised by tests) |
| Poster/post rendering of Indic scripts (Pillow without Raqm shapes complex scripts imperfectly) | **U** - known limitation |
| `install.bat` on a clean machine; portable-Node download inside it | **U** (the same steps were done manually) |
| Browser interaction of every button (only the dashboard was screenshot-checked) | **U** |

## Feature checklist
| # | Requirement | Status |
|---|---|---|
| 3 | Dashboard: metrics from DB, recent activity, processing overview, quick actions | T + R(empty state) |
| 4 | Projects: create/edit/delete(confirm)/duplicate/archive/search/filter status+date+type/sort/thumbnails/tags/history | T (UI: U) |
| 5 | Videos: upload, preview, metadata, thumbnail, replace, delete, never overwrite, MP4/MOV/MKV/AVI | T |
| 6 | AI content: 3 main + 2 short reels, poster, 2 posts, editable/previewable/downloadable | T (fake LLM) |
| 7 | Library: grid/list, search, filters, sort, tags, bulk zip, versions | T (UI: U) |
| 8 | Approval: approve/reject/revise/comment, per-asset, reasons, versions, regenerate | T |
| 9 | Memory: SQLite + Chroma, search, duplicate/similar detection, topics, recommendations, outcomes, inspect/export/delete | T (hash embedder) |
| 10 | Processing Centre: status of Whisper/Ollama/GPU/RAM/CPU/disk/FFmpeg, config, pause/resume/retry, sequential loading | T/R (GPU: U) |
| 11 | Persistent queue: logs, retry, cancel, restart recovery, partial-output retention | T |
| 12 | Brand & templates | T (media-heavy parts: U) |
| 13 | Calendar: month/week/day, statuses, campaigns, reminders (no auto-publishing, by design) | T (UI: U) |
| 14 | Analytics + CSV/JSON export, no invented engagement data | T |
| 15 | Settings screens incl. backup/restore, logging, privacy, updates | T |
| 16 | Schema: 21 required tables, FKs, indexes, migrations, transactions | T |
| 18 | Security: validation, sanitising, path confinement, zip-slip, localhost-only | T |
| 20 | Tests listed in the brief (creation, upload, transcript, assets, approval, failure/retry, restart, persistence, backup/restore, deletion) | T |
| - | Python 3.11 exactly | - dev machine has 3.13 only; code supports 3.11-3.13 |
| - | Multi-user | - (single owner; actor columns allow it later) |
| - | Social-media publishing / platform metrics | - by design |
| - | Scheduled/automatic backups | - (manual only) |
| - | Translator "story video" tab integration | - |

## Known limitations
* Translations started from the manager are not logged in the translator's own Excel record (it is bypassed on purpose).
* Burned-in subtitles use FFmpeg/libass; the font must contain the script's glyphs.
* Reels are centre-cropped (no face tracking).
* The queue has one worker, so jobs run one after another (deliberate, for memory).
* A project on the legacy Streamlit MVP is not migrated automatically.
