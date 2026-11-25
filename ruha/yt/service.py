"""YT Video Making service: project folders, state, and the narration bridge."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ruha.security import redact
from ruha.yt import story_video as sv

MARK = "@@RUHA@@"
WORKER = Path(__file__).with_name("narrate_worker.py")
# Languages Indic Parler-TTS can speak (same list as the translator)
LANGUAGES = ["English", "Hindi", "Telugu", "Tamil", "Kannada", "Malayalam", "Marathi", "Bengali", "Gujarati",
             "Punjabi", "Odia", "Assamese", "Nepali", "Sanskrit", "Bodo", "Konkani", "Maithili",
             "Manipuri", "Santali"]
SIZES = ["1280x720", "1920x1080", "3840x2160"]


def work_dir(settings) -> Path:
    d = Path(settings.data_dir) / "yt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def use(settings):
    sv.set_work(work_dir(settings))
    return sv


def project_dir(settings, name: str) -> Path:
    return Path(use(settings).proj(sv.clean_name(name)))


def list_projects(settings) -> list:
    out = []
    for d in sorted(work_dir(settings).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "scenes.json"
        if d.is_dir() and f.exists():
            out.append({"name": d.name, "scenes": len(json.loads(f.read_text(encoding="utf-8")))})
    return out


def state(settings, name: str) -> dict:
    d = project_dir(settings, name)
    f = d / "scenes.json"
    scenes = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    narr = {}
    for p in d.glob("narration_*.json"):
        meta = json.loads(p.read_text(encoding="utf-8"))
        narr[p.stem[len("narration_"):]] = round(sum(m["dur"] for m in meta) / 60, 2)
    clips = len(list((d / "clips").glob("*.mp4"))) if (d / "clips").exists() else 0
    videos = [{"lang": p.stem[len(d.name) + 1:], "file": p.name,
               "srt": (d / (p.stem + ".srt")).exists()} for p in d.glob(f"{d.name}_*.mp4")]
    return {"name": d.name, "scenes": scenes, "narration": narr, "clips": clips, "videos": videos}


def narrate(settings, name, lang, voice, *, progress=None, cancel=None) -> float:
    st = settings
    app = Path(st.translator_dir) / "app.py" if st.translator_dir else None
    py = st.translator_python
    if not (app and app.exists() and py and Path(py).exists()):
        raise RuntimeError("The translator (Whisper/IndicTrans2/Parler-TTS) was not found. "
                           "Set RUHA_TRANSLATOR_DIR / RUHA_TRANSLATOR_PYTHON.")
    req = {"translator_dir": str(st.translator_dir), "yt_dir": str(WORKER.parent),
           "work_dir": str(work_dir(st)), "project": name, "lang": lang, "voice": voice}
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.Popen([str(py), "-I", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env,
                            cwd=str(st.translator_dir), creationflags=0x08000000 if os.name == "nt" else 0)
    proc.stdin.write(json.dumps(req))
    proc.stdin.close()
    result, tail = None, []
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith(MARK):
            msg = json.loads(line[len(MARK):])
            if "progress" in msg and progress:
                progress(float(msg["progress"]), msg.get("desc", ""))
            if "result" in msg:
                result = msg["result"]
            if "error" in msg:
                tail.append(msg["error"])
        else:
            tail = (tail + [line])[-12:]
        if cancel and cancel():
            proc.terminate()
            raise RuntimeError("Cancelled")
    code = proc.wait()
    if code != 0 or result is None:
        raise RuntimeError(redact("Narration failed: " + " | ".join(t for t in tail[-4:] if t)))
    return float(result["seconds"])
