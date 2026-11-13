# Changelog

## 1.0.0 - RUHA Manager (new)
### Added
- `ruha/` FastAPI backend: projects, videos, transcripts, generated assets with versioning and per-asset approvals, persistent job queue,
  ChromaDB-backed local memory, brand/templates, content calendar, analytics, backup/restore, system/GPU status.
- SQLite schema (21 required tables + `asset_versions`) with frozen SQL migrations.
- React + TypeScript + Vite + Tailwind UI (11 screens, dark/light, responsive) served by the backend.
- Bridge to the existing RUHA Video Translator (runs in its own venv; translator code unchanged).
- `install.bat`, `start.bat`, `update.bat`, `pull_models.bat`; docs; 67 automated tests.
### Changed
- `requirements.txt` now describes the new app; the Streamlit MVP's list moved to `requirements-legacy.txt` (`run.bat` updated).
- README replaced; previous one kept at `docs/LEGACY_STREAMLIT_MVP.md`.
### Kept
- Streamlit MVP (`app/`, `services/`, `database/`, `run.bat`, `setup_windows.bat`) is untouched and still runnable; separate `storage/` data.
### Fixed during development
- Zip-slip check missed leading `/` paths; cancellation was ignored when progress updates were throttled; temp-file handles blocked deletion on Windows;
  duplicate-post detection compared different text than it indexed; PyAV 19 broke faster-whisper (pinned 16.1.0).
