"""What this machine can actually run, and which image model fits it. Nothing here guesses: VRAM comes from
nvidia-smi, RAM from psutil, and the recommendation is a pure function of those numbers."""
from __future__ import annotations

import shutil
import subprocess

import psutil

# model id, label, native portrait size (w, h), default steps, guidance, approx VRAM needed (GB, fp16), licence note
MODELS = {
    "sdxl": {"id": "stabilityai/stable-diffusion-xl-base-1.0", "label": "Stable Diffusion XL 1.0", "size": (832, 1216),
             "steps": 26, "guidance": 6.0, "vram_gb": 9.5, "licence": "CreativeML Open RAIL++-M (commercial use allowed)"},
    "sd15": {"id": "stable-diffusion-v1-5/stable-diffusion-v1-5", "label": "Stable Diffusion 1.5", "size": (512, 768),
             "steps": 28, "guidance": 7.0, "vram_gb": 3.8, "licence": "CreativeML Open RAIL-M (commercial use allowed)"},
    "sdxl-turbo": {"id": "stabilityai/sdxl-turbo", "label": "SDXL-Turbo (very fast, lower detail)", "size": (512, 768),
                   "steps": 4, "guidance": 0.0, "vram_gb": 6.5,
                   "licence": "Stability AI non-commercial research licence - NOT for commercial use"},
}


def gpu_info() -> dict | None:
    """First NVIDIA GPU as {name, total_gb, free_gb}, or None when there is none / no driver."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8, check=False).stdout.strip().splitlines()
        name, total, free = [x.strip() for x in out[0].split(",")]
        return {"name": name, "total_gb": round(float(total) / 1024, 1), "free_gb": round(float(free) / 1024, 1)}
    except Exception:
        return None


def ram_info() -> dict:
    vm = psutil.virtual_memory()
    return {"total_gb": round(vm.total / 1024**3, 1), "free_gb": round(vm.available / 1024**3, 1)}


def recommend(vram_gb: float | None, ram_gb: float | None = None) -> dict:
    """Pick a model from real numbers. SDXL needs ~10 GB to stay on the GPU; below that SD 1.5 is the honest choice
    (SDXL with CPU offload fits 6-8 GB cards but takes minutes per image and is not recommended)."""
    if vram_gb is None:
        return {"model": None, "reason": "No NVIDIA GPU detected. Image generation on the CPU is possible but takes "
                                         "many minutes per picture; it is off unless you enable it in Settings.",
                "offload": None}
    if vram_gb >= 10.5:
        return {"model": "sdxl", "reason": f"{vram_gb:.0f} GB VRAM: SDXL fits fully on the GPU.", "offload": "none"}
    if vram_gb >= 7.5:
        return {"model": "sd15", "reason": f"{vram_gb:.0f} GB VRAM: SD 1.5 is fast and fits comfortably; SDXL would "
                                           "need CPU offload and run several times slower.", "offload": "none"}
    if vram_gb >= 3.5:
        return {"model": "sd15", "reason": f"{vram_gb:.0f} GB VRAM: SD 1.5 with attention slicing and CPU offload.",
                "offload": "model"}
    return {"model": None, "reason": f"Only {vram_gb:.1f} GB VRAM: too little for Stable Diffusion.", "offload": None}


def snapshot() -> dict:
    g = gpu_info()
    r = ram_info()
    rec = recommend(g["total_gb"] if g else None, r["total_gb"])
    return {"gpu": g, "ram": r, "recommendation": rec, "models": MODELS}


def check_resources(model_key: str, cfg=None) -> str | None:
    """None when it is safe to load the model now, else a plain-language reason not to."""
    g, r = gpu_info(), ram_info()
    need = MODELS.get(model_key, MODELS["sd15"])["vram_gb"]
    if g is not None and g["free_gb"] < min(need, 3.5):
        return (f"Only {g['free_gb']:.1f} GB of GPU memory is free ({g['name']}). Close other GPU programs "
                "(or wait for the transcription/LLM to unload) and retry.")
    if r["free_gb"] < 2.0:
        return f"Only {r['free_gb']:.1f} GB of RAM is free; image generation needs at least 2 GB. Close other applications."
    return None
