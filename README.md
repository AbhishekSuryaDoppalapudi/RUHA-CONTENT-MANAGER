# El Ruach Manager

**Create. Manage. Inspire.** - the local content-management system for *El Ruach AI Video & Content Studio*.

Upload a video â†’ transcribe it â†’ get 3 highlight reels, 2 short reels, a poster and 2 encouragement posts â†’
review and approve each asset â†’ schedule it â†’ search everything you have ever produced. Translation and dubbing
use the existing **El Ruach Video Translator**. Everything runs on your own computer: no cloud AI, no paid APIs,
nothing is uploaded anywhere.

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, SQLite (WAL, foreign keys, SQL migrations), Pydantic |
| AI (all local) | faster-whisper Â· Ollama (default `qwen2.5:3b`) Â· ChromaDB + local embeddings |
| Media | FFmpeg / FFprobe, Pillow |
| Frontend | React, TypeScript, Vite, Tailwind CSS, Lucide |
| Translation / dubbing | existing El Ruach Video Translator (IndicTrans2 + Parler-TTS), called through a bridge |

## Install (Windows 10/11)

1. Install **Python 3.11+ (64-bit)** and **FFmpeg** (`winget install Gyan.FFmpeg`).
2. Install **Ollama** from <https://ollama.com/download> (needed for content planning; transcription works without it).
3. Double-click **`install.bat`**. It creates `.venv`, installs packages, downloads a portable Node.js *only if you have none*
   (to build the web UI), builds the UI and reports anything still missing.
4. Double-click **`pull_models.bat`** once (downloads `qwen2.5:3b` and the `nomic-embed-text` embedding model).
5. Double-click **`start.bat`** - the browser opens on <http://127.0.0.1:8765>.

Your data lives in `ruha_data\` (or `RUHA_DATA_DIR` from `.env`). The first Whisper run downloads the chosen model
(`small` â‰ˆ 460 MB) from Hugging Face; after that everything works offline.

## Using it

1. **Projects â†’ New project**, give it a name and (optionally) pick a video. Originals are never modified or overwritten.
2. On the project page click **Generate content**. The queue runs: transcription â†’ analysis â†’ reels â†’ poster â†’ social posts.
   Watch live progress in **Processing Centre**. Closing the app is safe - unfinished jobs resume on restart and finished outputs are kept.
3. **Review**: open any asset, edit its title/caption/hashtags (or the text on a graphic), approve, reject (reason required),
   request a revision, comment, trim-and-re-render a reel, or regenerate a post. Every change is a new version you can restore.
   Approving one asset never approves the project.
4. **Content Library** for grid/list browsing, search, tags and bulk zip download. **AI Memory** to search every earlier
   transcript, quote and caption, see topics already covered, and inspect/export/delete what is stored.
5. **Calendar** to plan publication (manual - no social account is connected). **Brand & Templates** for logo, colours,
   fonts, watermark, intro/outro. **Analytics** for real production statistics (CSV/JSON export).
6. **Settings â†’ Backup & restore** for full backups (database + brand + project files).

Translate / dub: *Dashboard â†’ Translate video* (or a project's **Translate / dub** button). It runs the existing translator
in its own Python environment (`RUHA_TRANSLATOR_DIR`, default `C:\Dev\indic-video-translator`) and files the results
(video, translated subtitles, dub audio) into the project.

## Hardware notes (RTX 3060 / 16 GB RAM)

* Only **one heavy model is loaded at a time**: a single sequential worker, Ollama is asked to unload before Whisper loads,
  Whisper is released after every transcription, and a job refuses to start Whisper when less than the configured free RAM
  (default 3 GB) is available instead of freezing the PC.
* Defaults: Whisper `small`, LLM `qwen2.5:3b`, reels 1080Ã—1920 H.264 (CRF 23). Change them in **Processing Centre**.
* GPU use is automatic when CUDA is available; **CPU only** can be forced.

## Tests

```
.venv\Scripts\python -m pytest
```
Real FFmpeg renders and a real SQLite database are used; only the AI models (Whisper, Ollama, translator) are replaced by
deterministic fakes. See `docs/CHECKLIST.md` for exactly what is and is not verified on real models.

## More

* `docs/ARCHITECTURE.md` - how it fits together, integration with the translator, schema, job/memory design
* `docs/TROUBLESHOOTING.md` - common problems
* `docs/CHECKLIST.md` - implementation checklist with honest verification status Â· `CHANGELOG.md`
* `docs/LEGACY_STREAMLIT_MVP.md` - the earlier Streamlit prototype, still runnable with `run.bat`
* API docs: <http://127.0.0.1:8765/docs> while the app is running

Developed and created by **Abhishek Surya Doppalapudi**.

The standalone look-and-feel page is index.html (open it directly in a browser); it is separate from this application.

