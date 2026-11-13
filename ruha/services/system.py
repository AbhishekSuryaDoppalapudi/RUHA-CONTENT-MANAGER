"""Hardware/model status for the AI Processing Centre."""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

import psutil

from ruha.ai.whisper import cuda_available, whisper_model_cached
from ruha.services import media

_gpu_cache = {"t": 0.0, "v": None}
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "turbo"]


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


def system_status(app) -> dict:
    cfg = app.config.all()
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(app.settings.data_dir)
    llm = app.services.llm.status(cfg) if hasattr(app.services.llm, "status") else {"connected": False}
    size = cfg.get("whisper.model", "small")
    try:
        cached = whisper_model_cached(size)
    except Exception:
        cached = False
    try:
        import faster_whisper
        fw = {"installed": True, "version": getattr(faster_whisper, "__version__", "")}
    except Exception:
        fw = {"installed": False, "version": ""}
    current = app.queue.current_job
    try:
        emb = app.memory.embedder().name
    except Exception:
        emb = None
    return {
        "whisper": {**fw, "model": size, "device_setting": cfg.get("whisper.device"),
                    "cuda_available": cuda_available(), "model_cached": cached,
                    "loaded": current is not None and False,
                    "status": ("ready" if fw["installed"] else "not installed")
                              + ("" if cached else " (model downloads on first use)")},
        "ollama": llm,
        "embedding": {"active": emb, "provider_setting": cfg.get("embedding.provider")},
        "gpu": gpu_info(),
        "ram": {"total_gb": round(vm.total / 1024**3, 1), "used_gb": round((vm.total - vm.available) / 1024**3, 1),
                "available_gb": round(vm.available / 1024**3, 1), "percent": vm.percent},
        "cpu": {"percent": psutil.cpu_percent(interval=0.1), "cores": psutil.cpu_count(logical=True)},
        "disk": {"total_gb": round(disk.total / 1024**3, 1), "free_gb": round(disk.free / 1024**3, 1),
                 "used_percent": round((disk.total - disk.free) / disk.total * 100, 1)},
        "ffmpeg": {"available": media.ffmpeg_available(), "nvenc": media.nvenc_available()},
        "translator": app.services.translator.status(),
        "queue": {"paused": app.queue.paused(), "current_job": current},
        "model_loading": {"policy": "sequential - one heavy model at a time",
                          "active_job_id": current},
        "active_llm": cfg.get("llm.model"),
        "whisper_models": WHISPER_MODELS,
    }
