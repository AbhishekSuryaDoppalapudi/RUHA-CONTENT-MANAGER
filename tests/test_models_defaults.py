"""Model defaults for the RTX 3060 desktop: Ollama qwen2.5:7b (3b fallback), faster-whisper large-v3-turbo on CUDA
float16 (small on CPU) - all selectable in Settings."""
import json
import sys
import types

import pytest
import requests

from ruha.ai import llm as L
from ruha.ai import whisper as W
from ruha.config import DEFAULT_CONFIG
from ruha.models import ModelSetting


# ------------------------------------------------------------------ defaults + validation
def test_defaults():
    d = {k: v["value"] for k, v in DEFAULT_CONFIG.items()}
    assert d["llm.model"] == "qwen2.5:7b" and d["llm.fallback_model"] == "qwen2.5:3b"
    assert d["whisper.model"] == "large-v3-turbo" and d["whisper.cpu_model"] == "small"
    assert d["whisper.compute_type"] == "auto" and d["whisper.device"] == "auto"


def test_settings_validation(client):
    ok = lambda v: client.put("/api/settings", json={"values": v}).status_code
    assert ok({"whisper.model": "large-v3-turbo", "whisper.cpu_model": "small", "whisper.compute_type": "float16"}) == 200
    assert ok({"whisper.cpu_model": "huge"}) == 400
    assert ok({"whisper.compute_type": "float8"}) == 400
    assert ok({"llm.model": "qwen2.5:7b", "llm.fallback_model": "qwen2.5:3b"}) == 200
    assert ok({"llm.fallback_model": ""}) == 200            # fallback can be switched off
    assert ok({"llm.model": "  "}) == 400
    assert ok({"llm.fallback_model": 5}) == 400
    vals = client.get("/api/settings").json()["values"]
    assert vals["whisper.model"] == "large-v3-turbo" and vals["llm.fallback_model"] == ""


def test_status_reports_models_and_devices(client):
    s = client.get("/api/system/status").json()
    assert s["fallback_llm"] == "qwen2.5:3b" and s["active_llm"] == "qwen2.5:7b"
    w = s["whisper"]
    assert w["gpu_model"] == "large-v3-turbo" and w["cpu_model"] == "small" and w["device"] in ("cuda", "cpu")
    assert w["model"] == ("large-v3-turbo" if w["device"] == "cuda" else "small")
    for m in ("large-v3-turbo", "small", "large-v3"):
        assert m in s["whisper_models"]


# ------------------------------------------------------------------ whisper: model by device
def test_model_and_precision_by_device():
    cfg = {"whisper.model": "large-v3-turbo", "whisper.cpu_model": "small", "whisper.compute_type": "auto"}
    assert W.model_for(cfg, "cuda") == "large-v3-turbo" and W.model_for(cfg, "cpu") == "small"
    assert W.compute_for(cfg, "cuda", "float16") == "float16"
    assert W.compute_for({**cfg, "whisper.compute_type": "int8_float16"}, "cuda", "float16") == "int8_float16"
    assert W.compute_for({**cfg, "whisper.compute_type": "float32"}, "cpu", "int8") == "int8"   # CPU is always int8
    assert W.model_for({}, "cuda") == "large-v3-turbo" and W.model_for({}, "cpu") == "small"


class _Info:
    duration, language, language_probability = 4.0, "en", 0.99


def _fake_whisper(monkeypatch, loads, fail_on_cuda=False):
    class Seg:
        start, end, text = 0.0, 1.0, " hi "

    class Model:
        def __init__(self, size, device, compute_type):
            loads.append((size, device, compute_type))
            if fail_on_cuda and device == "cuda":
                raise RuntimeError("CUDA out of memory")

        def transcribe(self, path, **kw):
            return iter([Seg()]), _Info()

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


CFG = {"gpu.min_free_ram_gb": 1.0, "whisper.model": "large-v3-turbo", "whisper.cpu_model": "small",
       "whisper.compute_type": "auto", "whisper.device": "auto", "gpu.mode": "auto", "whisper.language": "en"}


def test_gpu_uses_large_turbo_float16(monkeypatch):
    loads = []
    _fake_whisper(monkeypatch, loads)
    monkeypatch.setattr(W, "cuda_available", lambda: True)
    segs, info = W.FasterWhisperTranscriber().transcribe("x.wav", CFG)
    assert loads == [("large-v3-turbo", "cuda", "float16")] and info["model"] == "large-v3-turbo" and info["device"] == "cuda"


def test_cpu_uses_small_int8(monkeypatch):
    loads = []
    _fake_whisper(monkeypatch, loads)
    monkeypatch.setattr(W, "cuda_available", lambda: False)
    _, info = W.FasterWhisperTranscriber().transcribe("x.wav", CFG)
    assert loads == [("small", "cpu", "int8")] and info["device"] == "cpu"


def test_gpu_failure_falls_back_to_cpu_small(monkeypatch):
    loads, logs = [], []
    _fake_whisper(monkeypatch, loads, fail_on_cuda=True)
    monkeypatch.setattr(W, "cuda_available", lambda: True)
    _, info = W.FasterWhisperTranscriber().transcribe("x.wav", CFG, log=lambda m, lvl="INFO": logs.append((lvl, m)))
    assert loads == [("large-v3-turbo", "cuda", "float16"), ("small", "cpu", "int8")]
    assert info["model"] == "small" and info["device"] == "cpu"
    assert any(lvl == "WARNING" and "falling back to CPU" in m for lvl, m in logs)


def test_forced_cpu_setting_uses_cpu_model(monkeypatch):
    loads = []
    _fake_whisper(monkeypatch, loads)
    monkeypatch.setattr(W, "cuda_available", lambda: True)
    W.FasterWhisperTranscriber().transcribe("x.wav", {**CFG, "gpu.mode": "cpu", "whisper.cpu_model": "base"})
    assert loads == [("base", "cpu", "int8")]


# ------------------------------------------------------------------ planner model with fallback
class _Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        if isinstance(self._b, Exception):
            raise self._b
        return self._b


def _ollama(monkeypatch, behaviour):
    """behaviour(model) -> _Resp or an exception; records every model that was asked."""
    asked = []

    def post(url, json=None, timeout=None):
        if json.get("keep_alive") == 0:
            return _Resp(200, {})
        asked.append(json["model"])
        out = behaviour(json["model"])
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(L.requests, "post", post)
    return asked


LCFG = {"llm.model": "qwen2.5:7b", "llm.fallback_model": "qwen2.5:3b", "llm.base_url": "http://localhost:11434"}
OK = lambda t: _Resp(200, {"response": t})


def test_main_model_used_when_it_works(monkeypatch):
    asked = _ollama(monkeypatch, lambda m: OK(f"from {m}"))
    assert L.OllamaLLM().generate("hi", LCFG, as_json=False) == "from qwen2.5:7b" and asked == ["qwen2.5:7b"]


def test_missing_model_uses_fallback_and_is_remembered(monkeypatch):
    asked = _ollama(monkeypatch, lambda m: _Resp(404, {"error": "model 'qwen2.5:7b' not found"}) if "7b" in m else OK("small answer"))
    llm = L.OllamaLLM()
    assert llm.generate("hi", LCFG, as_json=False) == "small answer"
    assert asked == ["qwen2.5:7b", "qwen2.5:3b"]
    llm.generate("again", LCFG, as_json=False)
    assert asked[2:] == ["qwen2.5:3b"]                       # the 7b is not retried straight away


def test_out_of_memory_uses_fallback(monkeypatch):
    err = _Resp(500, {"error": "model requires more system memory (6.1 GiB) than is available (3.0 GiB)"})
    asked = _ollama(monkeypatch, lambda m: err if "7b" in m else OK("ok"))
    assert L.OllamaLLM().generate("hi", LCFG, as_json=False) == "ok" and asked == ["qwen2.5:7b", "qwen2.5:3b"]


def test_ollama_down_is_temporary_and_does_not_try_fallback(monkeypatch):
    asked = _ollama(monkeypatch, lambda m: requests.ConnectionError("refused"))
    with pytest.raises(L.LLMUnavailable):
        L.OllamaLLM().generate("hi", LCFG, as_json=False)
    assert asked == ["qwen2.5:7b"]
    from ruha.jobs import is_temporary
    assert is_temporary(L.LLMUnavailable("x"))


def test_fallback_can_be_turned_off(monkeypatch):
    asked = _ollama(monkeypatch, lambda m: _Resp(404, {"error": "not found"}))
    with pytest.raises(L.LLMUnavailable) as e:
        L.OllamaLLM().generate("hi", {**LCFG, "llm.fallback_model": ""}, as_json=False)
    assert asked == ["qwen2.5:7b"] and "ollama pull qwen2.5:7b" in str(e.value)


def test_both_models_missing_explains_both(monkeypatch):
    _ollama(monkeypatch, lambda m: _Resp(404, {"error": "not found"}))
    with pytest.raises(L.LLMUnavailable) as e:
        L.OllamaLLM().generate("hi", LCFG, as_json=False)
    assert "qwen2.5:7b" in str(e.value) and "qwen2.5:3b" in str(e.value)


def test_llm_status_shows_effective_model(monkeypatch):
    def get(url, timeout=None):
        return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"models": [{"name": "qwen2.5:3b"}]})
    monkeypatch.setattr(L.requests, "get", get)
    st = L.OllamaLLM().status(LCFG)
    assert st["active_installed"] is False and st["fallback_installed"] is True and st["effective"] == "qwen2.5:3b"
    monkeypatch.setattr(L.requests, "get", lambda url, timeout=None: types.SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"models": [{"name": "qwen2.5:7b"}, {"name": "qwen2.5:3b"}]}))
    assert L.OllamaLLM().status(LCFG)["effective"] == "qwen2.5:7b"


def test_unload_frees_both_models(monkeypatch):
    sent = []
    monkeypatch.setattr(L.requests, "post", lambda url, json=None, timeout=None: sent.append(json["model"]))
    L.OllamaLLM().unload(LCFG)
    assert set(sent) == {"qwen2.5:7b", "qwen2.5:3b"}


# ------------------------------------------------------------------ one-time migration of stored old defaults
def _set(ctx, key, value):
    with ctx.db.session() as s:
        row = s.get(ModelSetting, key)
        if row is None:
            s.add(ModelSetting(key=key, category="ai", value_json=json.dumps(value)))
        else:
            row.value_json = json.dumps(value)


def test_migration_moves_only_old_defaults_and_only_once(ctx):
    with ctx.db.session() as s:
        s.query(ModelSetting).filter(ModelSetting.key == "app.model_defaults_v2").delete()
    _set(ctx, "whisper.model", "small")          # the old default
    _set(ctx, "llm.model", "mistral:7b")         # chosen by the owner: must stay
    assert ctx.config.migrate_model_defaults() == ["whisper.model"]
    cfg = ctx.config.all()
    assert cfg["whisper.model"] == "large-v3-turbo" and cfg["llm.model"] == "mistral:7b"
    _set(ctx, "whisper.model", "small")          # chosen again on purpose after the migration
    assert ctx.config.migrate_model_defaults() == [] and ctx.config.all()["whisper.model"] == "small"


def test_migration_old_3b_becomes_7b(ctx):
    with ctx.db.session() as s:
        s.query(ModelSetting).filter(ModelSetting.key == "app.model_defaults_v2").delete()
    _set(ctx, "llm.model", "qwen2.5:3b")
    assert "llm.model" in ctx.config.migrate_model_defaults() and ctx.config.all()["llm.model"] == "qwen2.5:7b"
