"""One translator copy (C:\\Dev\\indic-video-translator): RUHA's workers use dubbing_engine / compat, never the
old Gradio app.py. compat.py provides the old function names on top of dubbing_engine."""
import ast
import importlib.util
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPAT = Path(os.getenv("RUHA_TRANSLATOR_DIR", r"C:\Dev\indic-video-translator")) / "compat.py"
needs_compat = pytest.mark.skipif(not COMPAT.exists(), reason="translator folder with compat.py not on this machine")


# ------------------------------------------------------------------ a fake dubbing_engine so compat loads without torch
class _Tok:
    def __init__(self, calls, tag):
        self.calls, self.tag = calls, tag

    def __call__(self, text, return_tensors=None):
        self.calls.append((self.tag, text))
        return types.SimpleNamespace(input_ids=text, attention_mask=text, to=lambda dev: types.SimpleNamespace(
            input_ids=text, attention_mask=text))


def make_engine(calls):
    class Out:
        def float(self): return self
        def cpu(self): return self
        def numpy(self): return np.zeros((1, 2400), dtype=np.float32)

    model = types.SimpleNamespace(
        generate=lambda **kw: Out(), config=types.SimpleNamespace(sampling_rate=24000))
    eng = types.ModuleType("dubbing_engine")
    eng.LANGS = {"English": "eng_Latn", "Telugu": "tel_Telu", "Hindi": "hin_Deva"}
    eng.DEVICE = "cpu"
    eng.np = np
    eng.torch = types.SimpleNamespace(manual_seed=lambda n: calls.append(("seed", n)),
                                      no_grad=lambda: types.SimpleNamespace(__enter__=lambda s: None, __exit__=lambda s, *a: None))
    # a real context manager for no_grad
    import contextlib
    eng.torch.no_grad = contextlib.nullcontext
    eng.sf = types.SimpleNamespace(write=lambda path, audio, sr: calls.append(("write", path, audio.shape, sr)))
    eng._load_parler = lambda: {"model": model, "tok": _Tok(calls, "text"), "desc_tok": _Tok(calls, "desc")}
    eng.translate = lambda texts, src, tgt, batch_size=16: calls.append(("translate", list(texts), src, tgt, batch_size)) or [f"T:{t}" for t in texts]
    eng.run = lambda cmd, cwd=None: calls.append(("ffmpeg", cmd))

    def translate_video(video, targets, **kw):
        calls.append(("translate_video", video, targets, kw))
        kw["progress"](0.5, "halfway")
        return {"_info": {"seconds": 7, "source_language": "English"},
                targets: {"video": "/out/v.mp4", "srt": "/out/s.srt", "notes": ["a note"], "lines": 3}}
    eng.translate_video = translate_video
    return eng


@pytest.fixture()
def compat(monkeypatch):
    calls = []
    if not COMPAT.exists():
        pytest.skip("translator folder with compat.py not on this machine")
    monkeypatch.setitem(sys.modules, "dubbing_engine", make_engine(calls))
    spec = importlib.util.spec_from_file_location("ruha_test_compat", COMPAT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.calls = calls
    monkeypatch.setattr(mod, "has_audio", lambda p: True)
    return mod


# ------------------------------------------------------------------ names RUHA needs are all provided
def _attrs_used(path, names):
    out = set()
    for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in names:
            out.add(node.attr)
    return out


def test_every_old_name_ruha_uses_is_in_compat(compat):
    used = (_attrs_used(ROOT / "ruha/yt/story_video.py", {"engine"})
            | _attrs_used(ROOT / "ruha/ai/translator_worker.py", {"translator"}))
    assert {"translate", "speak", "trim_silence", "LANGS", "_translate_impl"} <= used      # the scan really found them
    missing = sorted(n for n in used if not hasattr(compat, n))
    assert missing == [], f"compat.py lacks: {missing}"


@needs_compat
def test_compat_does_not_import_the_gradio_app():
    imported = set()
    for node in ast.walk(ast.parse(COMPAT.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "dubbing_engine" in imported and not imported & {"app", "gradio", "scripture", "access_log"}


# ------------------------------------------------------------------ behaviour
def test_translate_and_langs_pass_through(compat):
    assert compat.LANGS["Telugu"] == "tel_Telu"
    assert compat.translate(["hi"], "eng_Latn", "tel_Telu", 4) == ["T:hi"]
    assert ("translate", ["hi"], "eng_Latn", "tel_Telu", 4) in compat.calls


def test_speak_writes_audio_with_the_named_speaker(compat, tmp_path):
    out = str(tmp_path / "a.wav")
    assert compat.speak("Namaste", "Female", out, "Telugu") == out
    desc = [c[1] for c in compat.calls if c[0] == "desc"][0]
    assert desc.startswith("Lalitha's voice") and ("write", out, (2400,), 24000) in compat.calls
    compat.calls.clear()
    compat.speak("Namaste", "Male", out, "Telugu")
    assert [c[1] for c in compat.calls if c[0] == "desc"][0].startswith("Prakash's voice")
    compat.calls.clear()
    compat.speak("x", "Male", out, "Konkani")                       # no own speaker -> Hindi voice
    assert [c[1] for c in compat.calls if c[0] == "desc"][0].startswith("Rohit's voice")


def test_trim_silence_cuts_both_ends(compat, tmp_path):
    pydub = pytest.importorskip("pydub")
    from pydub import AudioSegment
    from pydub.generators import Sine
    audio = AudioSegment.silent(500) + Sine(440).to_audio_segment(duration=400).apply_gain(-6) + AudioSegment.silent(500)
    src, dst = str(tmp_path / "in.wav"), str(tmp_path / "out.wav")
    audio.export(src, format="wav")
    compat.trim_silence(src, dst)
    assert 300 <= len(AudioSegment.from_wav(dst)) <= 500


def test_translate_impl_old_signature_and_result(compat):
    seen = []
    out = compat._translate_impl("v.mp4", "English", "Telugu", "Female", True, True, False,
                                 progress=lambda f, desc="": seen.append((f, desc)))
    assert out == ("/out/v.mp4", "/out/s.srt", None, "Done in 7s. Spoken language: English -> Telugu, 3 lines. a note")
    _, video, target, kw = [c for c in compat.calls if c[0] == "translate_video"][0]
    assert (video, target) == ("v.mp4", "Telugu")
    assert kw["voice"] == "standard" and kw["base_gender"] == "female" and kw["dub"] and kw["keep_background"]
    assert kw["burn_subtitles"] is False and kw["source"] == "English"
    assert seen == [(0.5, "halfway")]                                # Gradio-style progress(frac, desc=...) works


def test_translate_impl_clone_and_plain_progress(compat):
    seen = []
    compat._translate_impl("v.mp4", "Auto-detect", "Hindi", "Male", True, False, True, clone_voice=True,
                           progress=lambda f, m: seen.append((f, m)))      # plain positional callback also works
    kw = [c for c in compat.calls if c[0] == "translate_video"][0][3]
    assert kw["voice"] == "clone" and kw["base_gender"] == "male" and seen == [(0.5, "halfway")]


def test_translate_impl_clip_is_cut_then_removed(compat):
    compat._translate_impl("v.mp4", "English", "Telugu", "Male", False, True, True, clip_start=5, clip_len=20)
    cut = [c[1] for c in compat.calls if c[0] == "ffmpeg"][0]
    assert cut[cut.index("-ss") + 1] == "5.000" and cut[cut.index("-t") + 1] == "20.000"
    used = [c[1] for c in compat.calls if c[0] == "translate_video"][0]
    assert used != "v.mp4" and not os.path.exists(used)               # trimmed copy was translated, then deleted


def test_translate_impl_input_errors(compat, monkeypatch):
    with pytest.raises(ValueError, match="Upload a video"):
        compat._translate_impl("", "English", "Telugu", "Male", True, True, True)
    monkeypatch.setattr(compat, "has_audio", lambda p: False)
    with pytest.raises(ValueError, match="no audio"):
        compat._translate_impl("v.mp4", "English", "Telugu", "Male", True, True, True)


# ------------------------------------------------------------------ RUHA side
def _folder(tmp_path, *files):
    d = tmp_path / "tr"
    d.mkdir()
    for f in files:
        (d / f).write_text(f"NAME = {f!r}\n", encoding="utf-8")
    return d


def test_narration_prefers_compat_when_engine_present(tmp_path, monkeypatch):
    from ruha.yt import narrate_worker as nw
    d = _folder(tmp_path, "dubbing_engine.py", "compat.py", "app.py")
    monkeypatch.syspath_prepend(str(d))
    for m in ("compat", "app", "dubbing_engine"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    assert nw.load_engine(str(d)).NAME == "compat.py"


def test_narration_falls_back_to_old_app_for_old_installs(tmp_path, monkeypatch):
    from ruha.yt import narrate_worker as nw
    d = _folder(tmp_path, "app.py")
    monkeypatch.syspath_prepend(str(d))
    for m in ("compat", "app"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    assert nw.load_engine(str(d)).NAME == "app.py"


def test_bridge_status_single_copy(tmp_path):
    from ruha.ai.translator import TranslatorBridge
    py = tmp_path / "python.exe"
    py.write_text("")
    mk = lambda d: TranslatorBridge(types.SimpleNamespace(translator_dir=d, translator_python=py)).status()
    shared_only = mk(_folder(tmp_path, "dubbing_engine.py", "compat.py"))          # no app.py needed any more
    assert shared_only["available"] and shared_only["engine"] == "shared" and shared_only["compat"]
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "app.py").write_text("")
    old = mk(tmp_path / "old")
    assert old["available"] and old["engine"] == "legacy" and not old["compat"]
    (tmp_path / "empty").mkdir()
    assert not mk(tmp_path / "empty")["available"]


def test_env_setting_points_the_bridge(tmp_path, monkeypatch):
    from ruha.config import load_settings
    monkeypatch.setenv("RUHA_TRANSLATOR_DIR", str(tmp_path / "one-copy"))
    monkeypatch.delenv("RUHA_TRANSLATOR_PYTHON", raising=False)
    s = load_settings(data_dir=tmp_path / "data")
    assert s.translator_dir == tmp_path / "one-copy"
    assert s.translator_python == tmp_path / "one-copy" / ".venv" / "Scripts" / "python.exe"


def test_env_example_documents_the_single_copy():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "RUHA_TRANSLATOR_DIR=C:\\Dev\\indic-video-translator" in text and "compat" in text
    assert not (ROOT / "tests" / "indic-video-translator").exists() and not (ROOT / "external" / "indic-video-translator").exists()
