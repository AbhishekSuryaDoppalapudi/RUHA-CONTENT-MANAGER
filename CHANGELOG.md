# Changelog

## Unreleased - better captions
- **Keyword highlight by meaning, not length:** names of God (God, Jesus, Lord, Spirit ...) first, then Bible book names and verse numbers
  ("John 3:16" stays in one piece), then the words the AI marked as key for that clip (only words that really occur in it), then strong
  nouns and verbs (grace, forgive, surrender ...). Filler (articles, pronouns, "just", "really", contractions, -ly adverbs ...) is never
  highlighted, so a phrase of only filler is shown plain.
- **Four caption templates**, chosen per project (and a default in Settings): *Stacked* (the original), *Word pop* (one big word, the
  previous one small above), *Karaoke* (a 3-5 word line, the spoken word in the brand colour) and *Classic* (two plain bottom lines).
- **Real word timestamps** from faster-whisper (new setting `whisper.word_timestamps`, on by default; stored per transcript segment by a
  new migration). Captions appear when their first word is spoken. Older transcripts, and translated captions, spread the words of a line
  over its time as before - re-transcribe a project to get word times.
- **Platform safe areas:** captions stay inside the safe area of each video format (the 9:16 bottom 430 px stays clear of the app's buttons).
- **Indian-language captions** are set in a Noto Sans font per script (Devanagari, Bengali, Gurmukhi, Gujarati, Oriya, Tamil, Telugu,
  Kannada, Malayalam, Arabic), copied next to the subtitle file, with each line in its own script's font. All four templates work for them.

## Unreleased - speaker-aware reels, video formats, -14 LUFS
- **Speaker-aware reframing** (replaces the centre crop): the speaker's face is found on 5 frames a second (MediaPipe Face Detection;
  OpenCV YuNet is added when MediaPipe misses faces and used alone when MediaPipe cannot start), the crop follows him with a dead zone and a
  moving average so it does not jitter, and the speaker is never cut off. Stretches of a second or more with no face show the whole frame
  centred over a blurred, zoomed copy ("blur fill"). Tested on a clip where the speaker walks across the stage and leaves it.
- **Video formats** 9:16 (1080x1920), 4:5 (1080x1350), 1:1 (1080x1080) and 16:9 (1920x1080), from `creative/formats.py`: choose several in
  Settings; every reel is rendered in each (captions, hook/closing cards and cutaways are made for each size). The first is the reel's main
  file, the others appear as a format switch on the reel and can be downloaded.
- **Encoding:** NVENC (`h264_nvenc`) whenever it really works (checked with a test encode, not just "FFmpeg has it"), libx264 otherwise or if a
  GPU encode fails (the old fallback left a stray argument behind and could not have worked). Loudness is normalised to **-14 LUFS** in two
  passes (was one pass at -15).
- Settings: `reel.formats`, `reel.face_tracking`, `reel.face_detector`. `scripts/reframe_check.py` tries it on any video and builds a
  walking-speaker demo. Face models ship in `ruha/assets/models` (nothing is downloaded).

## Unreleased - desktop models, one translator copy, dub a reel
- **Models for an RTX 3060 12 GB:** planner default `qwen2.5:7b` with a selectable fallback (`qwen2.5:3b`, used automatically when the
  7b model is not installed or does not fit; a failed model is skipped for 5 minutes); Whisper `large-v3-turbo` on CUDA in float16,
  with a separate CPU model (`small`) used when there is no GPU or the GPU model cannot be loaded. Both are selectable in Settings.
  A stored old default (`small`, `qwen2.5:3b`) is moved to the new default once; models you picked yourself are left alone.
- **One translator copy** (`C:\Dev\indic-video-translator`, set with `RUHA_TRANSLATOR_DIR` in `.env`): RUHA's workers use
  `dubbing_engine` (dubbing) and the new `compat.py` (YouTube narration + the old `_translate_impl`/`translate`/`speak`/`trim_silence`/`LANGS`
  names). RUHA no longer imports the translator's Gradio `app.py`.
- **Dub or translate one finished reel** (`asset_id` on a translation/dubbing job; a "What to translate" list in the dialog).
- `scripts/e2e_full_sermon.py`: real-model end-to-end run with pre-flight check, per-step timings, hook check and a JSON report.

## Unreleased - Creative Studio (Creative Engine 2.0)
- **New Creative Studio page** (sidebar): content source, editable creative brief written from the transcript, eight design engines,
  eight formats (reel cover, feed, square, YouTube thumbnail, carousel, poster, hook image), three-concept comparison, full-size preview with
  quality-check detail, headline editor, regenerate, download, approve/reject, history. Nothing is ever auto-approved.
- **Two-stage pipeline:** a text-free picture from a local image model (Stable Diffusion via diffusers, or ComfyUI), then professional typography
  drawn on a transparent layer in the browser. Models run one at a time and are unloaded afterwards; hardware gate + benchmark.
- **Quality control on real pixels:** fits, safe margins, clipping, contrast against the picture beneath, duplicates, scripture integrity.
- No image model installed: the app says so plainly; only the typographic engine runs (labelled as typographic, not AI artwork).
- See `docs/CREATIVE_STUDIO.md` for the audit of the old engine, architecture, model evaluation and what is/is not verified.

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
