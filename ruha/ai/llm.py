"""Ollama client and the content-planning prompts (chunked for long transcripts)."""
from __future__ import annotations

import json
import logging
import re
import time

import requests


log = logging.getLogger("ruha.llm")


class LLMError(RuntimeError):
    """The model answered, but not usably (bad JSON, ...). ``raw`` holds its answer when there was one."""
    raw: str | None = None


class LLMUnavailable(LLMError):
    """Ollama could not be reached or timed out - temporary; a retry can succeed."""


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
    RETRY_BAD_MODEL_S = 300          # after a model failed to load, skip it this long and use the fallback

    def __init__(self):
        self._bad: dict = {}         # model -> time it failed to load (missing / does not fit in memory)
        self.last_model: str | None = None

    def _base(self, cfg):
        return str(cfg.get("llm.base_url", "http://localhost:11434")).rstrip("/")

    @staticmethod
    def _same(a, b) -> bool:
        a, b = str(a or ""), str(b or "")
        return a == b or (":" not in b and a.split(":")[0] == b) or (":" not in a and b.split(":")[0] == a)

    def status(self, cfg) -> dict:
        want, fb = cfg.get("llm.model"), cfg.get("llm.fallback_model") or ""
        try:
            r = requests.get(f"{self._base(cfg)}/api/tags", timeout=2.5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            have = lambda name: bool(name) and any(self._same(m, name) for m in models)
            effective = want if have(want) else (fb if have(fb) else None)
            return {"connected": True, "models": models, "active": want, "active_installed": have(want),
                    "fallback": fb, "fallback_installed": have(fb), "effective": effective}
        except requests.RequestException as e:
            return {"connected": False, "models": [], "active": want, "active_installed": False,
                    "fallback": fb, "fallback_installed": False, "effective": None, "error": type(e).__name__}

    def models_to_try(self, cfg) -> list:
        main, fb = cfg.get("llm.model"), cfg.get("llm.fallback_model") or ""
        order = [main] + ([fb] if fb and not self._same(fb, main) else [])
        now = time.time()
        fresh = [m for m in order if now - self._bad.get(m, 0) > self.RETRY_BAD_MODEL_S]
        return fresh or order        # everything marked bad: try them all again rather than give up

    def _call(self, model, prompt, cfg, as_json, timeout) -> str:
        body = {"model": model, "prompt": prompt, "stream": False,
                "keep_alive": cfg.get("llm.keep_alive", "2m"),
                "options": {"temperature": float(cfg.get("llm.temperature", 0.35)), "num_ctx": 8192}}
        if as_json:
            body["format"] = "json"
        r = requests.post(f"{self._base(cfg)}/api/generate", json=body, timeout=timeout)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("error", ""))
            except ValueError:
                pass
            err = requests.HTTPError(f"{r.status_code} {detail}".strip(), response=r)
            err.model_problem = r.status_code == 404 or "memory" in detail.lower() or "not found" in detail.lower()
            raise err
        return r.json()["response"]

    def generate(self, prompt: str, cfg: dict, as_json: bool = True, timeout: int = 900) -> str:
        tried, last = [], None
        for model in self.models_to_try(cfg):
            tried.append(model)
            try:
                text = self._call(model, prompt, cfg, as_json, timeout)
                self.last_model = model
                return text
            except requests.RequestException as e:
                last = e
                if not getattr(e, "model_problem", False):
                    break            # Ollama down / timeout: another model will not help; retry later (temporary)
                self._bad[model] = time.time()
                log.warning("Ollama model '%s' is not usable (%s); trying the fallback model", model, e)
        raise LLMUnavailable(f"Could not use Ollama at {self._base(cfg)} with model(s) {', '.join(map(repr, tried))}. "
                             f"Start Ollama and run `ollama pull {tried[0]}`. ({type(last).__name__}: {last})") from last

    def generate_json(self, prompt: str, cfg: dict) -> dict:
        text = self.generate(prompt, cfg, as_json=True)
        try:
            return extract_json(text)
        except LLMError as e:
            e.raw = text                       # keep the rejected answer so the planner can log it
            raise

    def unload(self, cfg: dict) -> None:
        """Ask Ollama to free the model(s) from RAM/VRAM (used before loading Whisper)."""
        for model in {cfg.get("llm.model"), cfg.get("llm.fallback_model"), self.last_model} - {None, ""}:
            try:
                requests.post(f"{self._base(cfg)}/api/generate", json={"model": model, "keep_alive": 0}, timeout=5)
            except requests.RequestException:
                pass
