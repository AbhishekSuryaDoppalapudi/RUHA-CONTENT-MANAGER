# Architecture & integration plan

## Stage 1-3: what existed and what was reused

| Existing piece | Location | Decision |
|---|---|---|
| **Streamlit MVP** (`app/`, `services/`, `database/`, `run.bat`) - linear pipeline, tiny SQLite | this folder | **Kept untouched and runnable** (`run.bat`, own `storage/` + `content_agent.db`). Its prompt rules (3+2 reels, 15-30 s shorts, never invent quotes/references) and FFmpeg crop approach were carried into the new planner/renderer. |
| **RUHA Video Translator** (Gradio, Whisper + IndicTrans2 + Parler-TTS, own venv with torch) | `C:\Dev\indic-video-translator` | **Not copied or modified.** `ruha/ai/translator.py` runs `ruha/ai/translator_worker.py` inside the translator's own Python and calls its existing `_translate_impl(...)`. Progress/results come back as JSON lines. Torch/transformers never enter the manager's environment. |
| Translator's e-mail verification + Excel log (`access_log.process`) | translator | **Bypassed deliberately**: the manager is a single-owner local tool and calls `_translate_impl` directly, so translations started from the manager are *not* written to the translator's own `records/translation_log.xlsx`. The manager records them in its own database instead. |
| Translator "story video" tab | translator | Not integrated (out of scope for this module); still works standalone. |

## Process model

```
 Browser (React UI)  ──REST──►  FastAPI (ruha/main.py, 127.0.0.1 only)
                                    │
        ┌───────────────┬──────────┼───────────────┬──────────────┐
     SQLite (WAL)    Job queue   ChromaDB      Files (ruha_data/)  AI services
   21 tables+versions  1 worker   vectors       per-project folders  whisper · ollama
                         │                                          · translator bridge
                         └── handlers: transcription → plan → reels → poster → posts, render, translate/dub
```

* `ruha/context.py` - `AppContext` owns settings, DB, runtime config, **replaceable AI services** (tests inject fakes) and the queue.
* `ruha/api/*` - REST routers (documented at `/docs`). Pydantic validates inputs; all file serving goes through `ensure_within`.
* `ruha/services/*` - projects, assets (versioning + approvals), media (FFmpeg), design (Pillow), planner (LLM prompts + guards),
  memory, brand, backup, insights, system status.
* `ruha/jobs.py` + `ruha/handlers.py` - the queue and job implementations.

## Database

`ruha/migrations/0001_initial.sql` (generated from `ruha/models.py`, then frozen). Later changes must be new numbered `.sql` files; the runner
applies each once inside a transaction and records it in `schema_migrations`. SQLite runs with `foreign_keys=ON`, WAL and a busy timeout.

Tables: `projects`, `source_videos`, `transcripts`, `transcript_segments`, `translations`, `generated_assets`, `reels`, `posters`,
`social_posts`, `subtitles`, `processing_jobs`, `job_logs`, `approvals`, `feedback`, `content_tags`, `templates`, `brand_settings`,
`content_calendar`, `model_settings`, `memory_references`, `audit_logs` (+ `asset_versions` for version history).
Deleting a project cascades to everything below it; file deletion happens after the database commit.

Single-user for now: actors are recorded as `owner` in `approvals`/`audit_logs`/`asset_versions`, so adding a `users` table later is additive.

## Job queue & resumability

* Jobs are rows; one worker thread runs them **sequentially** (this is also the "one heavy model at a time" guarantee).
* `depends_on_id` chains the pipeline; a failed/cancelled dependency fails its dependents with a clear message and *Retry* re-queues them.
* Handlers commit each output as it is produced and skip finished work (existing transcript, existing reel slots, ...), so a crash or failure
  never discards completed outputs. On startup, jobs left `running` are re-queued (`recover_interrupted`).
* Cancellation is checked at every progress report; pause/resume is persisted in settings.

## Memory

SQLite (`memory_references`) is the source of truth; ChromaDB holds vectors for it. Embeddings come from an Ollama embedding model when
available, otherwise a deterministic offline hashing embedder (lexical similarity). Switching embedders is safe: `reindex()` rebuilds vectors
from the stored text (also done automatically after a backup restore). Learning is **retrieval-only**: approved/rejected examples, rejection
notes, your preferences and recent posts are injected into prompts; nothing is trained or fine-tuned.

## Faithfulness guards (planner)

Timestamps are snapped to transcript segment boundaries and length-bounded (main 30-95 s, short 12-35 s); overlapping reels are rejected;
quotes not found verbatim in the transcript are dropped; a scripture/source reference survives only if its name is actually spoken in the video;
new posts that closely resemble earlier ones trigger one regeneration with an avoid-list, else are flagged.

## Security

Binds to 127.0.0.1; uploads validated by extension *and* ffprobe, streamed with a size cap, names sanitised, files stored under the
project workspace (never overwriting); every served/zipped/restored path is confined to the data directory; backup archives are checked for
zip-slip; settings validate values (the Ollama URL must be localhost); logs and API errors strip user-profile paths and tokens.
