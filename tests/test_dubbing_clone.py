"""Voice-cloning dubbing: consent gate, parameter routing, worker engine selection, bridge capability check.
The real engine (torch, OpenVoice, IndicF5) is never loaded - it is replaced by fakes."""
import json
import sys
import textwrap
import types

import pytest

from ruha.ai import translator_worker as W
from ruha.ai.translator import TranslatorBridge


def _job(client, pid, **params):
    return client.post(f"/api/projects/{pid}/jobs", json={"job_type": "dubbing", "params": {"target": "Telugu", **params}})


# ---------------------------------------------------------------- API gate
def test_cloning_needs_consent_and_a_dubbing_job(client, project_with_video):
    pid = project_with_video
    assert _job(client, pid, clone=True).status_code == 400
    assert _job(client, pid, clone=True, clone_consent="yes").status_code == 400       # must be literally true
    r = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "translation",
                                                       "params": {"target": "Hindi", "clone": True, "clone_consent": True}})
    assert r.status_code == 400                                                         # subtitles-only job can't clone
    assert _job(client, pid, voice="Male").status_code == 202                           # plain dubbing unaffected


def test_cloned_dub_is_recorded_and_labelled(client, ctx, project_with_video, fakes):
    pid = project_with_video
    assert _job(client, pid, voice="Male", clone=True, clone_consent=True).status_code == 202
    ctx.queue.drain()
    call = fakes.translator.calls[-1]
    assert call["clone"] is True and call["dub"] is True and call["source_hint"] in (None, "English")
    tr = client.get(f"/api/projects/{pid}").json()["translations"][0]
    assert tr["status"] == "completed"
    assets = client.get(f"/api/assets?project_id={pid}&kind=translation_video").json()
    assert assets and assets[0]["meta"]["voice_cloned"] is True and assets[0]["meta"]["ai_generated_voice"] is True


def test_handler_refuses_clone_without_consent(client, ctx, project_with_video, fakes):
    """Defence in depth: even a job enqueued directly (bypassing the API) can't clone without consent."""
    jid = ctx.queue.enqueue("dubbing", project_with_video, {"target": "Hindi", "clone": True})
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and "permission" in j["error"] and not fakes.translator.calls


# ---------------------------------------------------------------- bridge capability check
def _translator_dir(tmp_path, *, engine, venv_clone):
    (tmp_path / "app.py").write_text("")
    if engine:
        (tmp_path / "dubbing_engine.py").write_text("")
    if venv_clone:
        (tmp_path / ".venv-clone").mkdir()
    py = tmp_path / "python.exe"
    py.write_text("")
    return types.SimpleNamespace(translator_dir=tmp_path, translator_python=py)


@pytest.mark.parametrize("engine,venv,expected", [(True, True, True), (True, False, False), (False, True, False)])
def test_cloning_capability_needs_engine_and_voice_env(tmp_path, engine, venv, expected):
    st = TranslatorBridge(_translator_dir(tmp_path, engine=engine, venv_clone=venv)).status()
    assert st["cloning"] is expected and st["engine"] == ("shared" if engine else "legacy")


def test_bridge_refuses_clone_when_not_set_up(tmp_path):
    from ruha.ai.translator import TranslatorUnavailable
    b = TranslatorBridge(_translator_dir(tmp_path, engine=True, venv_clone=False))
    with pytest.raises(TranslatorUnavailable, match="cloning"):
        b.translate(tmp_path / "v.mp4", tmp_path / "out", clone=True, dub=True)


# ---------------------------------------------------------------- worker engine selection
def _fake_dir(tmp_path, *, shared):
    out = tmp_path / "t"
    out.mkdir()
    if shared:
        (out / "dubbing_engine.py").write_text(textwrap.dedent('''
            import os
            SOURCE_CHOICES = ["Auto-detect", "English", "Hindi"]
            CALLS = []
            def translate_video(video, targets, **kw):
                CALLS.append((targets, kw))
                d = kw["out_dir"]; os.makedirs(d, exist_ok=True)
                v, s = os.path.join(d, "o.mp4"), os.path.join(d, "o.srt")
                open(v, "wb").write(b"v"); open(s, "w").write("1")
                return {targets: {"video": v, "srt": s, "notes": ["note one"], "lines": 3},
                        "_info": {"seconds": 5, "source_language": "English"}}
        '''))
    else:
        (out / "app.py").write_text(textwrap.dedent('''
            import os, tempfile
            def _translate_impl(video, src, tgt, voice, dub, keep, burn, cs, cl, res, progress):
                d = tempfile.mkdtemp(); v = os.path.join(d, "l.mp4"); s = os.path.join(d, "l.srt")
                open(v, "wb").write(b"v"); open(s, "w").write("1")
                return v, s, None, "legacy ok"
        '''))
    return out


def _req(tdir, tmp_path, **kw):
    return {"translator_dir": str(tdir), "video": "x.mp4", "out_dir": str(tmp_path / "dest"), "source": "Auto-detect",
            "target": "Telugu", "voice": "Male", "dub": True, "keep_background": True, "burn": False,
            "resolution": "Original resolution", "clone": False, "source_hint": None, **kw}


@pytest.fixture
def fresh_modules():
    saved = {k: sys.modules.pop(k, None) for k in ("dubbing_engine", "app")}
    cwd = __import__("os").getcwd()
    path = list(sys.path)
    yield
    __import__("os").chdir(cwd)
    sys.path[:] = path
    for k, v in saved.items():
        sys.modules.pop(k, None)
        if v is not None:
            sys.modules[k] = v


def test_worker_uses_shared_engine_and_passes_clone_and_source(tmp_path, fresh_modules):
    tdir = _fake_dir(tmp_path, shared=True)
    res = W.run(_req(tdir, tmp_path, clone=True, source_hint="English"))
    import dubbing_engine as eng
    target, kw = eng.CALLS[-1]
    assert target == "Telugu" and kw["voice"] == "clone" and kw["base_gender"] == "male" and kw["source"] == "English"
    assert res["engine"] == "shared" and res["notes"] == ["note one"] and (tmp_path / "dest" / "o.mp4").exists()


def test_worker_standard_voice_and_explicit_source_wins(tmp_path, fresh_modules):
    tdir = _fake_dir(tmp_path, shared=True)
    W.run(_req(tdir, tmp_path, source="Hindi", source_hint="English"))
    import dubbing_engine as eng
    kw = eng.CALLS[-1][1]
    assert kw["voice"] == "standard" and kw["source"] == "Hindi"


def test_worker_falls_back_to_legacy_and_refuses_clone_there(tmp_path, fresh_modules):
    tdir = _fake_dir(tmp_path, shared=False)
    res = W.run(_req(tdir, tmp_path))
    assert res["engine"] == "legacy" and res["status"] == "legacy ok" and (tmp_path / "dest" / "l.mp4").exists()
    sys.modules.pop("app", None)
    with pytest.raises(RuntimeError, match="no voice cloning"):
        W.run(_req(tdir, tmp_path, clone=True))
