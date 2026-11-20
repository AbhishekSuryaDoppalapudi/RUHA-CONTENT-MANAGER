"""Bridge to the existing El Ruach Video Translator (Whisper + IndicTrans2 + Parler-TTS).

The translator keeps its own virtualenv (torch, transformers, gradio). To avoid duplicating
or modifying it, the manager runs ``translator_worker.py`` inside the translator's Python
and calls the shared dubbing engine (or, for older installs, ``_translate_impl``). Progress and the result are exchanged
as JSON lines on stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ruha.security import redact

WORKER = Path(__file__).with_name("translator_worker.py")
MARK = "@@RUHA@@"


class TranslatorUnavailable(RuntimeError):
    pass


class TranslatorBridge:
    def __init__(self, settings):
        self.settings = settings

    def status(self) -> dict:
        d, py = self.settings.translator_dir, self.settings.translator_python
        shared = bool(d and (Path(d) / "dubbing_engine.py").exists())
        legacy = bool(d and (Path(d) / "app.py").exists())
        ok = bool((shared or legacy) and py and Path(py).exists())
        return {"available": ok, "directory": str(d) if d else None,
                "engine": "shared" if shared else "legacy",
                "compat": bool(d and (Path(d) / "compat.py").exists()),
                # cloning needs the shared engine plus its separate voice environment (.venv-clone)
                "cloning": bool(ok and shared and (Path(d) / ".venv-clone").exists()),
                "python": str(py) if py else None,
                "reason": None if ok else "Translator folder or its Python (.venv) was not found. "
                                           "Set RUHA_TRANSLATOR_DIR / RUHA_TRANSLATOR_PYTHON."}

    def translate(self, video_path, out_dir, *, source="Auto-detect", target="Hindi", voice="Female",
                  dub=False, keep_background=True, burn=False, resolution="Original resolution",
                  clone=False, source_hint=None, progress=None, log=None, cancel=None) -> dict:
        st = self.status()
        if not st["available"]:
            raise TranslatorUnavailable(st["reason"])
        if clone and not st["cloning"]:
            raise TranslatorUnavailable("Voice cloning is not set up on this machine (it needs the shared dubbing "
                                        "engine and its .venv-clone environment). Use a standard voice or finish "
                                        "the cloning setup.")
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        req = {"translator_dir": str(self.settings.translator_dir), "video": str(video_path),
               "out_dir": str(out_dir), "source": source, "target": target, "voice": voice,
               "dub": dub, "keep_background": keep_background, "burn": burn, "resolution": resolution,
               "clone": bool(clone), "source_hint": source_hint}
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = subprocess.Popen([str(self.settings.translator_python), "-I", str(WORKER)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env,
                                cwd=str(self.settings.translator_dir),
                                creationflags=0x08000000 if os.name == "nt" else 0)
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
            raise RuntimeError(redact("Translator failed: " + " | ".join(t for t in tail[-4:] if t)))
        return result
