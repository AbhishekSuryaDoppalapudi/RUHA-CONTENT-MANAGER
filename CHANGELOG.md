# Changelog

## Unreleased - poster engine, fonts, audio
- **Posters, posts and reel hook/closing cards are now drawn as HTML/CSS** and captured with the installed Edge/Chrome (no extra
  download). Real typography, glow, shadows, correct Telugu/Hindi/Tamil/... shaping. Falls back to the built-in Pillow renderer
  when no browser is found or a render fails (Settings > Design > Poster renderer).
- **Open-licence fonts bundled** (`ruha/assets/fonts`: Montserrat, Playfair Display, Noto Sans for 10 scripts; OFL texts included).
- **Optional AI-generated backgrounds** (Stable Diffusion / SDXL-Turbo, local, NVIDIA GPU only; `requirements-gpu.txt`). One picture per
  sermon and theme, cached; no people or text in the picture; falls back to the built-in themed background on any failure.
- **Reel audio:** speech is loudness-normalised; hook and closing cards carry a soft ambient bed instead of silence; image reels play
  your music file (Settings > Video export) or a built-in ambient bed.

## Unreleased - content improvements
- **Output per sermon is now** 1 main reel, 2 short reels, 1 highlight poster and 2 encouragement posts.
- **Theme-oriented text:** the AI writes a short hook line in the sermon's theme (not a copied sentence). Posters and posts show only the hook plus
  the verse the speaker actually cited (verse text only if read aloud); unspoken references are dropped.
- **Theme backgrounds:** nine themes (warfare, spirit, grace, peace, hope, faith, love, strength, growth) with their own colours and drawn
  motifs (embers, flames, rays, waves, dawn, stars, bokeh, mountains), all generated locally. Setting: *Theme-matched poster and post backgrounds*.
- **Reels:** opening hook image and closing image, creative animated subtitles (bold, keyword highlight in the theme colour), subtitle language
  selectable (English default; other languages via the local LLM - quality depends on the small model).
- **Reels from images:** upload pictures on a project and build a reel with hook card, captions and closing card.
- **Paste a video link** (yt-dlp): downloads, then runs the whole pipeline. Only for videos you own or may use.
- **Finding work:** project filter and review-status filters on Library and Review; "Review this project" / "Open in library" on each project;
  the Review counter no longer counts subtitles and transcripts.
- **Planner:** overlapping reel picks are repaired instead of failing the whole plan; a project's status ignores failures superseded by a newer successful run.
- Proprietary `LICENSE`; README section on protecting the code.

## Unreleased - renamed to El Ruach
- Project name is now **El Ruach** (El Ruach Manager / El Ruach AI Video & Content Studio) in the UI, API title, docs, installer and the default poster branding.
- Internal identifiers are unchanged on purpose so existing setups keep working: Python package `ruha`, `RUHA_*` settings in `.env`, `ruha_data/`, `ruha.db`,
  the `ruha-backup-*.zip` prefix, and the repository name.
- Already-created default templates keep their old names in an existing database; rename them in Brand & Templates if you like.

## 1.0.0 - El Ruach Manager (new)
### Added
- `ruha/` FastAPI backend: projects, videos, transcripts, generated assets with versioning and per-asset approvals, persistent job queue,
  ChromaDB-backed local memory, brand/templates, content calendar, analytics, backup/restore, system/GPU status.
- SQLite schema (21 required tables + `asset_versions`) with frozen SQL migrations.
- React + TypeScript + Vite + Tailwind UI (11 screens, dark/light, responsive) served by the backend.
- Bridge to the existing El Ruach Video Translator (runs in its own venv; translator code unchanged).
- `install.bat`, `start.bat`, `update.bat`, `pull_models.bat`; docs; 67 automated tests.
### Changed
- `requirements.txt` now describes the new app; the Streamlit MVP's list moved to `requirements-legacy.txt` (`run.bat` updated).
- README replaced; previous one kept at `docs/LEGACY_STREAMLIT_MVP.md`.
### Kept
- Streamlit MVP (`app/`, `services/`, `database/`, `run.bat`, `setup_windows.bat`) is untouched and still runnable; separate `storage/` data.
### Fixed during development
- Zip-slip check missed leading `/` paths; cancellation was ignored when progress updates were throttled; temp-file handles blocked deletion on Windows;
  duplicate-post detection compared different text than it indexed; PyAV 19 broke faster-whisper (pinned 16.1.0).
