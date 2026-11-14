# Changelog

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
