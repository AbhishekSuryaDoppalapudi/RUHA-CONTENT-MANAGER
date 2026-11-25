"""Image-generation back-ends. Stage 1 of the creative pipeline: a text-free picture. Never any lettering - the app
draws all words itself (compose.py), so spelling is always right.

* DiffusersBackend - Stable Diffusion 1.5 / SDXL run in-process with diffusers (needs requirements-gpu.txt)
* ComfyBackend     - a ComfyUI server you already run on this PC (http://127.0.0.1:8188)
* ImageService     - chooses between them from the settings and reports honestly when neither is available
Models are loaded only for a generation batch and unloaded afterwards (one heavy model at a time)."""
from __future__ import annotations

import gc
import io
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image

from ruha.creative import hardware

log = logging.getLogger("ruha.creative")


class ImageBackendError(RuntimeError):
    pass


class DiffusersBackend:
    name = "diffusers"

    def __init__(self):
        self.pipe = None
        self.model_key = None

    def available(self, cfg) -> tuple:
        try:
            import torch
            import diffusers  # noqa: F401
        except Exception:
            return False, "Local image generation is not installed. Run: .venv\\Scripts\\python.exe -m pip install -r requirements-gpu.txt"
        if not torch.cuda.is_available() and not cfg.get("creative.allow_cpu", False):
            return False, ("No NVIDIA GPU is available to PyTorch. Enable 'Allow CPU image generation' in Settings "
                           "to run on the CPU (very slow), or use a ComfyUI server.")
        return True, ""

    def _model_key(self, cfg) -> str:
        want = (cfg.get("creative.model") or "auto").lower()
        if want in hardware.MODELS:
            return want
        g = hardware.gpu_info()
        rec = hardware.recommend(g["total_gb"] if g else None)
        return rec["model"] or "sd15"

    def load(self, cfg):
        ok, why = self.available(cfg)
        if not ok:
            raise ImageBackendError(why)
        import torch
        from diffusers import AutoPipelineForText2Image
        key = self._model_key(cfg)
        if self.pipe is not None and self.model_key == key:
            return self.pipe
        self.unload()
        spec = hardware.MODELS[key]
        cuda = torch.cuda.is_available()
        kw = {"torch_dtype": torch.float16 if cuda else torch.float32}
        if cuda:
            kw["variant"] = "fp16"
        try:
            pipe = AutoPipelineForText2Image.from_pretrained(spec["id"], **kw)
        except Exception:
            kw.pop("variant", None)                                  # some repos have no fp16 variant files
            pipe = AutoPipelineForText2Image.from_pretrained(spec["id"], **kw)
        if cuda:
            g = hardware.gpu_info()
            if (cfg.get("creative.offload") == "model") or (g and g["total_gb"] < spec["vram_gb"] + 0.5):
                pipe.enable_model_cpu_offload()
            else:
                pipe.to("cuda")
            pipe.enable_vae_tiling()
        pipe.enable_attention_slicing()
        self.pipe, self.model_key = pipe, key
        return pipe

    def info(self, cfg) -> dict:
        key = self._model_key(cfg)
        return {"backend": self.name, "model": hardware.MODELS[key]["id"], "model_key": key}

    def generate(self, prompt: str, negative: str, size: tuple, seed: int, cfg, progress=None) -> Image.Image:
        import torch
        pipe = self.load(cfg)
        spec = hardware.MODELS[self.model_key]
        w, h = (int(size[0]) // 8 * 8, int(size[1]) // 8 * 8)
        gen_dev = "cuda" if torch.cuda.is_available() else "cpu"
        gen = torch.Generator(device=gen_dev).manual_seed(int(seed))
        steps = int(cfg.get("creative.steps") or spec["steps"])

        def cb(p, i, t, kw):                                         # diffusers step callback
            if progress:
                progress(min(1.0, (i + 1) / max(1, steps)))
            return kw
        res = pipe(prompt=prompt, negative_prompt=negative, width=w, height=h, num_inference_steps=steps,
                   guidance_scale=spec["guidance"], generator=gen, callback_on_step_end=cb)
        return res.images[0].convert("RGB")

    def unload(self):
        self.pipe, self.model_key = None, None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


class ComfyBackend:
    """Talks to a running ComfyUI (the standard txt2img graph). Models stay managed by ComfyUI."""
    name = "comfyui"

    def __init__(self, urlopen=urllib.request.urlopen):
        self._open = urlopen

    def _url(self, cfg) -> str:
        return (cfg.get("creative.comfyui_url") or "http://127.0.0.1:8188").rstrip("/")

    def _get(self, url, timeout=15):
        with self._open(url, timeout=timeout) as r:
            return r.read()

    def available(self, cfg) -> tuple:
        try:
            self._get(self._url(cfg) + "/system_stats", timeout=3)
            return True, ""
        except Exception as e:
            return False, f"ComfyUI is not reachable at {self._url(cfg)} ({e.__class__.__name__})"

    def _checkpoint(self, cfg) -> str:
        want = cfg.get("creative.comfy_checkpoint")
        if want:
            return want
        info = json.loads(self._get(self._url(cfg) + "/object_info/CheckpointLoaderSimple"))
        names = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        if not names:
            raise ImageBackendError("ComfyUI has no checkpoints installed (put a Stable Diffusion model in models/checkpoints)")
        return names[0]

    def info(self, cfg) -> dict:
        return {"backend": self.name, "model": cfg.get("creative.comfy_checkpoint") or "(ComfyUI default checkpoint)",
                "model_key": "comfyui"}

    def generate(self, prompt: str, negative: str, size: tuple, seed: int, cfg, progress=None) -> Image.Image:
        base = self._url(cfg)
        w, h = (int(size[0]) // 8 * 8, int(size[1]) // 8 * 8)
        steps = int(cfg.get("creative.steps") or 26)
        wf = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self._checkpoint(cfg)}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                                                       "latent_image": ["4", 0], "seed": int(seed), "steps": steps,
                                                       "cfg": 6.5, "sampler_name": "dpmpp_2m", "scheduler": "karras",
                                                       "denoise": 1.0}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "ruach"}},
        }
        body = json.dumps({"prompt": wf, "client_id": str(uuid.uuid4())}).encode()
        req = urllib.request.Request(base + "/prompt", data=body, headers={"Content-Type": "application/json"})
        try:
            with self._open(req, timeout=20) as r:
                pid = json.loads(r.read())["prompt_id"]
        except urllib.error.HTTPError as e:
            raise ImageBackendError(f"ComfyUI rejected the request: {e.read()[:300]!r}") from e
        deadline = time.time() + float(cfg.get("creative.timeout_s") or 900)
        while time.time() < deadline:
            hist = json.loads(self._get(f"{base}/history/{pid}"))
            if pid in hist:
                for node in hist[pid].get("outputs", {}).values():
                    for im in node.get("images", []):
                        q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                                    "type": im.get("type", "output")})
                        return Image.open(io.BytesIO(self._get(f"{base}/view?{q}", timeout=60))).convert("RGB")
                raise ImageBackendError("ComfyUI finished without producing an image")
            time.sleep(1.0)
        raise ImageBackendError("ComfyUI took too long to produce the image")

    def unload(self):
        pass


class ImageService:
    """Chooses a back-end from settings: auto = ComfyUI if it answers, else local diffusers, else nothing."""
    name = "image-service"

    def __init__(self, comfy: ComfyBackend | None = None, diffusers: DiffusersBackend | None = None):
        self.comfy = comfy or ComfyBackend()
        self.diffusers = diffusers or DiffusersBackend()

    def _pick(self, cfg):
        mode = (cfg.get("creative.backend") or "auto").lower()
        reasons = []
        order = {"comfyui": [self.comfy], "diffusers": [self.diffusers], "none": []}.get(mode, [self.comfy, self.diffusers])
        for b in order:
            ok, why = b.available(cfg)
            if ok:
                return b, ""
            reasons.append(f"{b.name}: {why}")
        if mode == "none":
            return None, "Image generation is switched off in Settings."
        return None, "No image-generation model is available. " + " | ".join(reasons)

    def status(self, cfg) -> dict:
        b, why = self._pick(cfg)
        snap = hardware.snapshot()
        out = {"available": b is not None, "reason": why, "hardware": snap}
        if b is not None:
            out.update(b.info(cfg))
        return out

    def generate(self, prompt, negative, size, seed, cfg, progress=None) -> tuple:
        """Returns (image, info). Raises ImageBackendError with a plain message when nothing is available."""
        b, why = self._pick(cfg)
        if b is None:
            raise ImageBackendError(why)
        if b.name == "diffusers":
            key = b._model_key(cfg)
            problem = hardware.check_resources(key, cfg)
            if problem:
                raise ImageBackendError(problem)
        return b.generate(prompt, negative, size, seed, cfg, progress), b.info(cfg)

    def unload(self):
        self.diffusers.unload()
        self.comfy.unload()

    @staticmethod
    def new_seed() -> int:
        return random.randint(1, 2**31 - 1)
