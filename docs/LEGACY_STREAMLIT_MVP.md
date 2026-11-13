# Local AI Content Studio (Windows MVP)

A local-first video repurposing application. Upload a local video and generate:
- 3 main vertical reels
- 2 short vertical reels
- 1 highlight poster
- 2 encouragement post graphics
- Full timestamped transcript (JSON + TXT)
- Content plan, captions and hashtags

## Important scope notes

This is a runnable MVP, not a promise of perfect one-click creative quality. It uses:
- faster-whisper for local transcription
- Ollama for local transcript analysis and content planning
- FFmpeg for vertical clip rendering
- Pillow for locally generated text-based graphics
- SQLite for project history

The first version creates SRT subtitle files alongside reels; it does not burn subtitles into the video by default. This avoids font/path issues on Windows and keeps subtitle text editable. A later iteration can add subtitle burn-in, speaker-aware layouts, face tracking, branded templates and local image-generation models.

## Hardware profile

Designed to start conservatively on Windows with 16 GB RAM and an RTX 3060:
- Start with Whisper `small`, `int8`, device `auto`.
- Start with Ollama `qwen2.5:3b`.
- Close other GPU-heavy applications.
- If CUDA/VRAM errors occur, select Whisper `cpu`.
- A 7B LLM may work with quantization but is not the default for this RAM profile.

RTX 3060 cards exist in different VRAM configurations. Check with `nvidia-smi` before tuning GPU workloads.

## Prerequisites

1. Windows 10/11
2. Python 3.11 (64-bit)
3. FFmpeg and FFprobe installed and available in PATH
4. Ollama installed
5. Enough free disk space for source videos, models and rendered outputs

Install FFmpeg and Ollama using their official installers. The app does not download third-party models automatically except when you explicitly pull them.

## Setup

1. Extract this ZIP to a folder such as `C:\AI\local_ai_content_agent`.
2. Install Python 3.11, FFmpeg and Ollama.
3. Open the project folder.
4. Run `setup_windows.bat` to check dependencies and pull the default Ollama model.
5. Run `run.bat`.
6. Open the Streamlit address shown in the terminal (usually http://localhost:8501).
7. Upload a local MP4/MOV/MKV and click Generate.

`run.bat` creates `.venv` and installs Python requirements the first time.

## Offline operation

Initial setup requires internet to install Python packages and download model weights. After dependencies and models are downloaded, the actual transcription and content-generation workflow runs locally. Disable network access only after setup and model downloads are complete.

## Output structure

Each job is stored under:

`storage/projects/<project_id>/`

- `audio/source.wav`
- `transcript/transcript.json`
- `transcript/transcript.txt`
- `analysis/content_plan.json`
- `analysis/content_plan.md`
- `reels/main_1.mp4` ... `main_3.mp4`
- `reels/short_1.mp4` ... `short_2.mp4`
- matching `.srt` subtitle files and clip metadata
- `posters/highlight_poster.png`
- `social_posts/encouragement_1.png`
- `social_posts/encouragement_2.png`
- `social_posts/captions_and_posts.md`

## Known limitations to address in the next iteration

- Clip timestamps are LLM-selected and must be reviewed before publishing.
- Current video crop is center-crop; it does not yet track faces or reframe speakers.
- Subtitles are supplied as SRT sidecars, not burned into the MP4.
- Posters are typographic templates, not AI-generated photographic artwork.
- No automatic YouTube downloading is included; input is a local file as requested.
- Translation and voice dubbing are not part of this content-agent MVP.
- A very long transcript is truncated for the LLM prompt; transcript files remain complete. We should add chunked analysis and merge in the next iteration for long-form reliability.

## Troubleshooting

### Ollama connection error
Run:
`ollama serve`
Then in another terminal:
`ollama pull qwen2.5:3b`

### FFmpeg not found
Confirm:
`ffmpeg -version`
`ffprobe -version`
If not found, add FFmpeg's `bin` folder to Windows PATH and reopen PowerShell.

### CUDA / Whisper error
Set Whisper device to `cpu` in the sidebar for a reliable fallback. The app still runs locally, but transcription will be slower.

### Python version
Check:
`py -3.11 --version`

## Database

SQLite database:
`storage/content_agent.db`

The database tracks projects, generated assets and feedback. The current MVP stores approval feedback schema but does not yet have a full feedback-learning loop.
