"""Hardware/model status for the AI Processing Centre."""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time

import psutil

from ruha.ai.whisper import WHISPER_LANGUAGES, cuda_available, model_for, resolve_device, whisper_model_cached
from ruha.creative import formats as video_formats
from ruha.services import capfonts, media, reframe, subtitles

_gpu_cache = {"t": 0.0, "v": None}
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo", "distil-large-v3"]


def gpu_info(ttl: float = 2.0) -> dict:
    if time.monotonic() - _gpu_cache["t"] < ttl and _gpu_cache["v"] is not None:
        return _gpu_cache["v"]
    info = {"available": False, "gpus": [], "driver": None}
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            p = subprocess.run([exe, "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,driver_version",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                               creationflags=0x08000000 if sys.platform == "win32" else 0)
            if p.returncode == 0:
                for line in p.stdout.strip().splitlines():
                    n, tot, used, util, temp, drv = [x.strip() for x in line.split(",")]
                    info["gpus"].append({"name": n, "vram_total_mb": int(float(tot)), "vram_used_mb": int(float(used)),
                                         "utilization": float(util), "temperature_c": float(temp)})
                    info["driver"] = drv
                info["available"] = bool(info["gpus"])
        except Exception as e:  # nvidia-smi present but failing
            info["error"] = type(e).__name__
    _gpu_cache.update(t=time.monotonic(), v=info)
    return info


class ProbeCache:
    """The slow readings (Ollama, GPU, FFmpeg/NVENC, Whisper, translator, embedder) cached for ``ttl`` seconds and
    refreshed on a background thread, so the status call never waits for them.

    ``background=False`` (tests, no worker) computes inline every time, so nothing is ever stale there."""

    def __init__(self, compute, ttl: float = 30.0, background: bool = True):
        self._compute, self.ttl, self.background = compute, ttl, background
        self._lock = threading.Lock()
        self._value, self._at, self._busy = None, 0.0, False
        self._first = threading.Event()

    def _refresh(self):
        try:
            value = self._compute()
        except Exception:                      # a failing probe must never break the status page
            value = self._value
        with self._lock:
            if value is not None:
                self._value, self._at = value, time.monotonic()
            self._busy = False
        self._first.set()

    def get(self, wait: float = 0.05):
        """Latest probes (None only before the very first reading finishes, after waiting at most ``wait`` s)."""
        if not self.background:
            return self._compute()
        with self._lock:
            stale = self._value is None or time.monotonic() - self._at >= self.ttl
            start = stale and not self._busy
            if start:
                self._busy = True
        if start:
            threading.Thread(target=self._refresh, name="ruha-status-probes", daemon=True).start()
        if self._value is None:
            self._first.wait(wait)
        return self._value

    def warm(self):
        self.get(wait=0.0)


def _slow_probes(app) -> dict:
    cfg = app.config.all()
    llm = app.services.llm.status(cfg) if hasattr(app.services.llm, "status") else {"connected": False}
    cuda = cuda_available()
    device = resolve_device(cfg.get("whisper.device", "auto"), cfg.get("gpu.mode", "auto"))[0] if cuda else "cpu"
    size = model_for(cfg, device)
    try:
        cached = whisper_model_cached(size)
    except Exception:
        cached = False
    try:
        import faster_whisper
        fw = {"installed": True, "version": getattr(faster_whisper, "__version__", "")}
    except Exception:
        fw = {"installed": False, "version": ""}
    try:
        emb = app.memory.embedder().name
    except Exception:
        emb = None
    return {"llm": llm, "size": size, "cached": cached, "fw": fw, "emb": emb, "gpu": gpu_info(ttl=0),
            "cuda": cuda, "device": device, "ffmpeg": {"available": media.ffmpeg_available(), "nvenc": media.nvenc_available()},
            "translator": app.services.translator.status(),
            "faces": _face_status(cfg)}


def _face_status(cfg) -> dict:
    try:
        return reframe.detector_status(str(cfg.get("reel.face_detector", "auto")))
    except Exception as e:                                          # the status page must never break on the tracker
        return {"active": None, "detectors": {}, "error": f"{type(e).__name__}: {e}"}


_PENDING = {"llm": {"connected": False, "models": [], "active": None, "active_installed": False, "checking": True},
            "size": None, "cached": False, "fw": {"installed": False, "version": ""}, "emb": None,
            "gpu": {"available": False, "gpus": [], "driver": None, "checking": True}, "cuda": False,
            "ffmpeg": {"available": False, "nvenc": False, "checking": True},
            "translator": {"available": False, "directory": None, "python": None, "checking": True},
            "faces": {"active": None, "detectors": {}, "checking": True}}
_PENDING["device"] = "cpu"


def probe_cache(app) -> ProbeCache:
    pc = getattr(app, "_probe_cache", None)
    if pc is None:
        pc = ProbeCache(lambda: _slow_probes(app), background=bool(getattr(app.settings, "start_worker", False)))
        try:
            app._probe_cache = pc
        except Exception:
            pass
    return pc


def system_status(app) -> dict:
    cfg = app.config.all()
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(app.settings.data_dir)
    pr = {**_PENDING, "size": model_for(cfg, "cpu"), "device": "cpu", **(probe_cache(app).get() or {})}
    current = app.queue.current_job
    return {
        "whisper": {**pr["fw"], "model": pr["size"], "device": pr["device"], "gpu_model": cfg.get("whisper.model"),
                    "cpu_model": cfg.get("whisper.cpu_model"), "compute_type": cfg.get("whisper.compute_type"),
                    "device_setting": cfg.get("whisper.device"),
                    "cuda_available": pr["cuda"], "model_cached": pr["cached"],
                    "loaded": current is not None and False,
                    "status": ("ready" if pr["fw"]["installed"] else "not installed")
                              + ("" if pr["cached"] else " (model downloads on first use)")},
        "ollama": pr["llm"],
        "embedding": {"active": pr["emb"], "provider_setting": cfg.get("embedding.provider")},
        "gpu": pr["gpu"],
        "ram": {"total_gb": round(vm.total / 1024**3, 1), "used_gb": round((vm.total - vm.available) / 1024**3, 1),
                "available_gb": round(vm.available / 1024**3, 1), "percent": vm.percent},
        "cpu": {"percent": psutil.cpu_percent(interval=None), "cores": psutil.cpu_count(logical=True)},
        "disk": {"total_gb": round(disk.total / 1024**3, 1), "free_gb": round(disk.free / 1024**3, 1),
                 "used_percent": round((disk.total - disk.free) / disk.total * 100, 1)},
        "ffmpeg": pr["ffmpeg"],
        "translator": pr["translator"],
        "reel_video": {"formats": cfg.get("reel.formats"), "face_tracking": bool(cfg.get("reel.face_tracking", True)),
                       "face_detector": pr["faces"], "encoder": "h264_nvenc" if (pr["ffmpeg"].get("nvenc")
                       and cfg.get("gpu.mode", "auto") != "cpu") else "libx264", "loudness_lufs": -14},
        "queue": {"paused": app.queue.paused(), "current_job": current},
        "model_loading": {"policy": "sequential - one heavy model at a time",
                          "active_job_id": current},
        "active_llm": cfg.get("llm.model"),
        "fallback_llm": cfg.get("llm.fallback_model"),
        "whisper_models": WHISPER_MODELS,
        "video_formats": [[k, v["label"]] for k, v in video_formats.VIDEO_FORMATS.items()],
        "caption_templates": [{"id": k, **v} for k, v in subtitles.TEMPLATE_INFO.items()],
        "caption_fonts": {"latin": capfonts.LATIN["small"][0],
                          "scripts": {k: capfonts.available(k) for k in capfonts.SCRIPTS}},
        "whisper_languages": [{"code": "auto", "name": "Auto-detect (can be wrong on accented speech)"},
                              *({"code": c, "name": n} for c, n in WHISPER_LANGUAGES.items())],
    }
