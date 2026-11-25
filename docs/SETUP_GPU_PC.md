# Running El Ruach Manager on a GPU PC (NVIDIA RTX 3060)

Developed and created by Abhishek Surya Doppalapudi.

## Before you start (once, needs internet)
1. Latest NVIDIA driver: nvidia.com/drivers. Check with `nvidia-smi`.
2. Python 3.12 (python.org, tick **Add to PATH**) and Git.
3. FFmpeg: `winget install Gyan.FFmpeg`, then open a new window.
4. Ollama: ollama.com/download (local AI for content planning).
5. If Windows says "Application Control policy has blocked this file", turn off Smart App Control
   (Windows Security > App & browser control).

## Copy the app to the PC (pick one)
- **Zip:** copy `Ruach-Content-Manager.zip` (made on the old PC) to the new PC and unzip it, for example into `C:\Ruach`.
- **Git:** clone the repository into `C:\Ruach`.

## Install (one double-click)
Double-click **install_gpu_pc.bat**. It:
1. sets up El Ruach Manager (Python packages, web UI);
2. downloads the voice/translation engine to `C:\Dev\indic-video-translator` and installs PyTorch with GPU support;
3. prints whether the GPU is visible (`CUDA is available`).

Then run **pull_models.bat** (Ollama model; for a 12 GB GPU you can also run `ollama pull qwen2.5:7b`).

## First run: download the voice models (once, needs internet)
- Make a free Hugging Face account, accept the licence on `ai4bharat/indic-parler-tts` and the IndicTrans2 pages, run `huggingface-cli login`
  (use `C:\Dev\indic-video-translator\.venv\Scripts\huggingface-cli.exe`).
- In El Ruach Manager open **YT Video Making**, plan a short script and make a narration. This downloads about 6 GB.

## Daily use
Double-click **start.bat**; it opens http://127.0.0.1:8765. Open **YT Video Making** in the sidebar:
script, scenes, narration, build video.

## Going offline afterwards
Set the Windows environment variable `HF_HUB_OFFLINE=1`. Everything then runs on this PC only.

## Moving to a PC with no internet
Do the steps above on any PC with internet, then copy the whole `C:\Ruach` and `C:\Dev\indic-video-translator` folders (they contain
the `.venv`s) plus `C:\Users\<you>\.cache\huggingface` and `...\.cache\whisper` to the same paths.

## Different folder for the engine?
Copy `.env.example` to `.env` and set `RUHA_TRANSLATOR_DIR` (and `RUHA_TRANSLATOR_PYTHON`).

## What the GPU speeds up, and what it does not (yet)
- Faster on the GPU: narration (voice), transcription, translation, dubbing.
- Still plain colour test clips: real animated video clips are not generated automatically yet. Until that is built, put your own clips in the
  project folder (`ruha_data\yt\<project>\clips\001.mp4`, `002.mp4`, ...) and the **Build video** step uses them.

## Back up
Copy the `ruha_data` folder (database, projects, videos) regularly.
