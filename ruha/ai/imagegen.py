"""Optional AI-generated backgrounds (Stable Diffusion family, fully local) for posters, posts and reel cards.

Needs an NVIDIA GPU and the extra packages in requirements-gpu.txt (torch, diffusers, transformers, accelerate).
The model (default ``stabilityai/sdxl-turbo``, one-step generation) downloads once from Hugging Face and then runs
offline. Pictures never contain text: the app draws all text itself, so spelling is always correct. Any failure
makes the caller fall back to the built-in themed background."""
from __future__ import annotations

import gc
import logging
from pathlib import Path

log = logging.getLogger("ruha.imagegen")

# Scenes only - no people or faces, no text (the app overlays the words).
THEME_SCENES = {
    "warfare": "dark stormy battlefield sky, glowing embers and smoke, ominous red and black clouds, cinematic",
    "spirit": "rising golden flames and sparks in darkness, wind swept fire, purple night sky, cinematic",
    "grace": "warm golden sunrays breaking through clouds over soft hills, glowing light, serene",
    "peace": "calm still lake at dawn, soft mist, gentle teal light, tranquil reflections",
    "hope": "sunrise over distant mountains, orange and violet sky, new day, light on the horizon",
    "faith": "vast starry night sky above a lone path, deep blue, glowing stars, quiet awe",
    "love": "soft warm pink and rose bokeh lights, gentle glow, tender atmosphere",
    "strength": "towering rugged mountain peak above storm clouds, cold steel blue light, powerful",
    "growth": "young green sprouts in soft morning light, fresh leaves, dew, lush bokeh",
}
STYLE = "digital painting, atmospheric, dramatic lighting, high detail, empty space in the centre for text"
NEGATIVE = "text, letters, words, watermark, logo, people, person, face, hands, crowd, deformed, blurry, low quality"


def prompt_for(theme: str, topic: str = "") -> str:
    scene = THEME_SCENES.get(theme) or THEME_SCENES["faith"]
    topic = " ".join((topic or "").split())[:80]
    return f"{scene}, {STYLE}" + (f", mood of: {topic}" if topic else "")


class DiffusersBackground:
    """Loads the pipeline only while generating, then frees the GPU for Whisper / Ollama."""
    name = "diffusers"

    def available(self, cfg) -> tuple:
        try:
            import torch
            import diffusers  # noqa: F401
        except Exception:
            return False, "AI backgrounds need: pip install -r requirements-gpu.txt"
        if not torch.cuda.is_available():
            return False, "AI backgrounds need an NVIDIA GPU (none found)"
        return True, ""

    def generate(self, prompt: str, out, cfg, size=(576, 1024)) -> Path:
        ok, why = self.available(cfg)
        if not ok:
            raise RuntimeError(why)
        import torch
        from diffusers import AutoPipelineForText2Image
        model = cfg.get("design.ai_model") or "stabilityai/sdxl-turbo"
        pipe = None
        try:
            pipe = AutoPipelineForText2Image.from_pretrained(model, torch_dtype=torch.float16, variant="fp16")
            pipe.enable_attention_slicing()
            pipe.to("cuda")
            steps = 1 if "turbo" in model.lower() else 25
            image = pipe(prompt=prompt, negative_prompt=NEGATIVE, num_inference_steps=steps,
                         guidance_scale=0.0 if "turbo" in model.lower() else 6.0,
                         width=size[0], height=size[1]).images[0]
            out = Path(out)
            out.parent.mkdir(parents=True, exist_ok=True)
            image.save(out, "PNG")
            return out
        finally:
            del pipe
            gc.collect()
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
