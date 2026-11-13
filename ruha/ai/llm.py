"""Ollama client and the content-planning prompts (chunked for long transcripts)."""
from __future__ import annotations

import json
import re

import requests


class LLMError(RuntimeError):
    pass


def extract_json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            raise LLMError("The local model did not return valid JSON.")
        try:
            return json.loads(m.group(0))
        except ValueError as e:
            raise LLMError("The local model returned malformed JSON.") from e


class OllamaLLM:
    name = "ollama"

    def _base(self, cfg):
        return str(cfg.get("llm.base_url", "http://localhost:11434")).rstrip("/")

    def status(self, cfg) -> dict:
        try:
            r = requests.get(f"{self._base(cfg)}/api/tags", timeout=2.5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            want = cfg.get("llm.model")
            return {"connected": True, "models": models, "active": want,
                    "active_installed": any(m == want or m.split(":")[0] == str(want).split(":")[0] and ":" not in str(want)
                                            for m in models)}
        except requests.RequestException as e:
            return {"connected": False, "models": [], "active": cfg.get("llm.model"),
                    "active_installed": False, "error": type(e).__name__}

    def generate(self, prompt: str, cfg: dict, as_json: bool = True, timeout: int = 900) -> str:
        body = {"model": cfg.get("llm.model"), "prompt": prompt, "stream": False,
                "keep_alive": cfg.get("llm.keep_alive", "2m"),
                "options": {"temperature": float(cfg.get("llm.temperature", 0.35)), "num_ctx": 8192}}
        if as_json:
            body["format"] = "json"
        try:
            r = requests.post(f"{self._base(cfg)}/api/generate", json=body, timeout=timeout)
            r.raise_for_status()
            return r.json()["response"]
        except requests.RequestException as e:
            raise LLMError(f"Could not use Ollama at {self._base(cfg)} with model "
                           f"'{cfg.get('llm.model')}'. Start Ollama and run `ollama pull "
                           f"{cfg.get('llm.model')}`. ({type(e).__name__})") from e

    def generate_json(self, prompt: str, cfg: dict) -> dict:
        return extract_json(self.generate(prompt, cfg, as_json=True))

    def unload(self, cfg: dict) -> None:
        """Ask Ollama to free the model from RAM/VRAM (used before loading Whisper)."""
        try:
            requests.post(f"{self._base(cfg)}/api/generate",
                          json={"model": cfg.get("llm.model"), "keep_alive": 0}, timeout=5)
        except requests.RequestException:
            pass
