"""faster-whisper transcription with load-on-demand / release-after-use."""
from __future__ import annotations

import gc
from pathlib import Path

import psutil


class ResourceError(RuntimeError):
    pass


# Spoken-language choices offered in Settings (Whisper language codes). "auto" lets Whisper guess, which is
# unreliable on accented speech - an English sermon was once detected as Danish - so a fixed language is the default.
WHISPER_LANGUAGES = {
    "en": "English", "hi": "Hindi", "te": "Telugu", "ta": "Tamil", "kn": "Kannada", "ml": "Malayalam",
    "bn": "Bengali", "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu",
    "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
    "ru": "Russian", "ar": "Arabic", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "id": "Indonesian", "sw": "Swahili", "tl": "Filipino",
}
LOW_CONFIDENCE = 0.80


def language_arg(value) -> str | None:
    """Setting value -> the `language` argument for faster-whisper (None = auto-detect)."""
    v = str(value or "en").strip().lower()
    return None if v == "auto" else v


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


def model_for(cfg: dict, device: str) -> str:
    """Whisper model for the device: the big one (``whisper.model``) on the GPU, the small CPU one otherwise."""
    if device == "cuda":
        return str(cfg.get("whisper.model") or "large-v3-turbo")
    return str(cfg.get("whisper.cpu_model") or "small")


def compute_for(cfg: dict, device: str, default: str) -> str:
    """``whisper.compute_type`` applies on the GPU only (float16 by default); the CPU always uses int8."""
    chosen = cfg.get("whisper.compute_type", "auto")
    if device == "cuda" and chosen not in (None, "", "auto"):
        return str(chosen)
    return default


class FasterWhisperTranscriber:
    """Loads the model only for the duration of one transcription, then frees it."""
    name = "faster-whisper"

    def transcribe(self, audio_path, cfg: dict, progress=None, log=None):
        from faster_whisper import WhisperModel
        check_free_ram(float(cfg.get("gpu.min_free_ram_gb", 3.0)), "Whisper")
        device, compute = resolve_device(cfg.get("whisper.device", "auto"), cfg.get("gpu.mode", "auto"))
        compute = compute_for(cfg, device, compute)
        size = model_for(cfg, device)
        if log:
            log(f"Loading Whisper '{size}' on {device} ({compute})")
        try:
            model = WhisperModel(size, device=device, compute_type=compute)
        except Exception as e:
            if device == "cuda":
                size = model_for(cfg, "cpu")        # the big model is too slow on the CPU: use the CPU one
                if log:
                    log(f"GPU load failed ({type(e).__name__}); falling back to CPU with '{size}'", "WARNING")
                model = WhisperModel(size, device="cpu", compute_type="int8")
                device = "cpu"
            else:
                raise
        try:
            lang = language_arg(cfg.get("whisper.language", "en"))
            want_words = bool(cfg.get("whisper.word_timestamps", True))
            gen, info = model.transcribe(str(audio_path), beam_size=5, vad_filter=True, language=lang,
                                         word_timestamps=want_words)
            if log:
                log(f"Spoken language: {info.language} ({'fixed in Settings' if lang else 'auto-detected, confidence %.0f%%' % (info.language_probability * 100)})")
                if lang is None and info.language_probability < LOW_CONFIDENCE:
                    log(f"Language detection is unsure ({info.language}, {info.language_probability:.0%}). "
                        "Set the spoken language in Settings > Models if the transcript looks wrong.", "WARNING")
            segments, total = [], max(info.duration, 1.0)
            for seg in gen:
                text = seg.text.strip()
                if text:
                    item = {"start": float(seg.start), "end": float(seg.end), "text": text}
                    words = [{"w": w.word, "s": round(float(w.start), 2), "e": round(float(w.end), 2)}
                             for w in (getattr(seg, "words", None) or []) if str(w.word).strip()]
                    if words:
                        item["words"] = words                  # word timestamps drive the caption styles
                    segments.append(item)
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
