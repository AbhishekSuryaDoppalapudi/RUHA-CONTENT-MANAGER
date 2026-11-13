# Troubleshooting

**"An Application Control policy has blocked this file"** (Windows Smart App Control / WDAC)
Windows refused to load an unsigned compiled file. Seen with SQLAlchemy 2.1's compiled extensions and with rollup/esbuild native binaries.
RUHA is set up to avoid both: `requirements.txt` pins SQLAlchemy `<2.1`, and `frontend/package.json` uses the pure-JS/WebAssembly builds of rollup and esbuild
through npm `overrides`. If another package is blocked, install a pure-Python/WASM alternative or ask your administrator to allow it - do not disable the policy.

**Ollama: "not running" in Processing Centre** - start Ollama, then `ollama pull qwen2.5:3b` (or run `pull_models.bat`). Transcription works without it, but
summaries/reels/poster/posts need it; failed planning jobs can be re-run with *Retry failed*.

**Job fails: "Only X GB of RAM is free"** - close other programs (browsers, games), or lower *Minimum free RAM* in Processing Centre. A 3 GB floor protects the 16 GB machine.

**Whisper is slow / no GPU** - Processing Centre shows whether CUDA is available. Install the current NVIDIA driver (`nvidia-smi` must work). Use a smaller model (`base`) or CPU.
The first run of each Whisper model downloads it from Hugging Face.

**FFmpeg not found** - `winget install Gyan.FFmpeg`, then reopen the terminal and `start.bat`.

**Translation says "Translator ... not found"** - set `RUHA_TRANSLATOR_DIR` (and optionally `RUHA_TRANSLATOR_PYTHON`) in `.env` to the translator project and its `.venv`.
Translated models need their own first-time Hugging Face download/licence acceptance (see the translator's README).

**Burned-in subtitles show boxes** - pick a font that supports your script in Processing Centre → Output → Subtitle font (e.g. *Nirmala UI* for Telugu/Hindi), or turn burn-in off and use the .srt files.

**Port already in use** - change `RUHA_PORT` in `.env`.

**Restoring a backup** - Settings → Backup & restore. A safety backup of the current state is made first. Backups without media contain no videos, so reels show as "missing file" until media is restored.

**Start fresh** - stop the app and delete the `ruha_data` folder (this removes all projects). The legacy Streamlit MVP's `storage/` folder is separate.
