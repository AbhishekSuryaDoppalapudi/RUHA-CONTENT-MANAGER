"""The spoken-language setting must reach Whisper (an English sermon was once auto-detected as Danish)."""
import sys
import types

import pytest

from ruha.ai import whisper as W
from ruha.config import DEFAULT_CONFIG


class _Info:
    duration = 10.0
    language = "en"
    language_probability = 0.5


def _install_fake(monkeypatch, calls, lang_prob=0.5):
    class Seg:
        start, end, text = 0.0, 1.0, " hello "

    class Model:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kw):
            calls.append(kw)
            info = _Info()
            info.language_probability = lang_prob
            return iter([Seg()]), info

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(W, "check_free_ram", lambda *a, **k: None)
    monkeypatch.setattr(W, "resolve_device", lambda *a, **k: ("cpu", "int8"))


def test_default_is_english():
    assert DEFAULT_CONFIG["whisper.language"]["value"] == "en"
    assert W.language_arg(None) == "en" and W.language_arg("auto") is None and W.language_arg("HI") == "hi"


def test_fixed_language_is_passed_to_whisper(monkeypatch):
    calls = []
    _install_fake(monkeypatch, calls)
    segs, info = W.FasterWhisperTranscriber().transcribe("x.wav", {"whisper.language": "en"})
    assert calls[0]["language"] == "en" and segs[0]["text"] == "hello"
    W.FasterWhisperTranscriber().transcribe("x.wav", {})          # unset -> English, never auto
    assert calls[1]["language"] == "en"
    W.FasterWhisperTranscriber().transcribe("x.wav", {"whisper.language": "te"})
    assert calls[2]["language"] == "te"


def test_auto_passes_none_and_warns_when_unsure(monkeypatch):
    calls, logs = [], []
    _install_fake(monkeypatch, calls, lang_prob=0.4)
    W.FasterWhisperTranscriber().transcribe("x.wav", {"whisper.language": "auto"}, log=lambda m, lvl="INFO": logs.append((lvl, m)))
    assert calls[0]["language"] is None
    assert any(lvl == "WARNING" and "unsure" in m for lvl, m in logs)


def test_language_setting_validation_and_options(client):
    assert client.put("/api/settings", json={"values": {"whisper.language": "xx"}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"whisper.language": "auto"}}).status_code == 200
    assert client.put("/api/settings", json={"values": {"whisper.language": "en"}}).json()["values"]["whisper.language"] == "en"
    opts = client.get("/api/system/status").json()["whisper_languages"]
    assert opts[0]["code"] == "auto" and {"code": "en", "name": "English"} in opts
