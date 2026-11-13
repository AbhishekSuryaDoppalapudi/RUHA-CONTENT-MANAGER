"""faster-whisper transcription with load-on-demand / release-after-use."""
from __future__ import annotations

import gc
from pathlib import Path

import psutil


class ResourceError(RuntimeError):
    pass


def check_free_ram(min_gb: float, what: str) -> None:
    free = psutil.virtual_memory().available / 1024**3
    if free < min_gb:
        raise ResourceError(f"Only {free:.1f} GB of RAM is free; {what} needs at least {min_gb:.1f} GB. "
                            "Close other applications and retry.")


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_device(cfg_device: str, gpu_mode: str) -> tuple:
    if gpu_mode == "cpu" or cfg_device == "cpu":
        return "cpu", "int8"
    if (cfg_device in ("auto", "cuda") and gpu_mode in ("auto", "gpu")) and cuda_available():
        return "cuda", "float16"
    return "cpu", "int8"


class FasterWhisperTranscriber:
    """Loads the model only for the duration of one transcription, then frees it."""
    name = "faster-whisper"

    def transcribe(self, audio_path, cfg: dict, progress=None, log=None):
        from faster_whisper import WhisperModel
        check_free_ram(float(cfg.get("gpu.min_free_ram_gb", 3.0)), "Whisper")
        device, compute = resolve_device(cfg.get("whisper.device", "auto"), cfg.get("gpu.mode", "auto"))
        if cfg.get("whisper.compute_type", "auto") != "auto":
            compute = cfg["whisper.compute_type"]
        size = cfg.get("whisper.model", "small")
        if log:
            log(f"Loading Whisper '{size}' on {device} ({compute})")
        try:
            model = WhisperModel(size, device=device, compute_type=compute)
        except Exception as e:
            if device == "cuda":
                if log:
                    log(f"GPU load failed ({type(e).__name__}); falling back to CPU", "WARNING")
                model = WhisperModel(size, device="cpu", compute_type="int8")
                device = "cpu"
            else:
                raise
        try:
            gen, info = model.transcribe(str(audio_path), beam_size=5, vad_filter=True)
            segments, total = [], max(info.duration, 1.0)
            for seg in gen:
                text = seg.text.strip()
                if text:
                    segments.append({"start": float(seg.start), "end": float(seg.end), "text": text})
                if progress:
                    progress(min(0.99, seg.end / total))
            return segments, {"language": info.language, "probability": float(info.language_probability),
                              "model": size, "device": device}
        finally:
            del model
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass


def whisper_model_cached(size: str) -> bool:
    """True when the model files are already in the local Hugging Face cache (no download needed)."""
    import os
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return any(hub.glob(f"models--Systran--faster-whisper-{size}*")) if hub.exists() else False
