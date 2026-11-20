"""scripts/e2e_full_sermon.py drives a REAL server over HTTP. Here it runs against a live server (uvicorn in a thread)
whose AI services are fakes, so the script's plumbing - upload, pipeline, reel dub, timings, hook check, report - is
verified without GPUs or models."""
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from ruha.config import Settings
from ruha.main import create_app

_REAL_SLEEP = time.sleep
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_full_sermon.py"


def _load():
    spec = importlib.util.spec_from_file_location("e2e_full_sermon", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def live(tmp_path, fakes):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    settings = Settings(data_dir=tmp_path / "data", start_worker=True, port=port)
    settings.ensure_dirs()
    app = create_app(settings, fakes)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started
    app.state.ctx.config.set_many({"render.width": 270, "render.height": 480, "render.quality": "draft", "design.engine": "pillow",
                                                    "reel.face_tracking": False})
    yield f"http://127.0.0.1:{port}", app.state.ctx
    server.should_exit = True
    th.join(timeout=10)


def test_script_runs_full_flow_and_reports(live, sample_video, monkeypatch, capsys, tmp_path):
    url, ctx = live
    mod = _load()
    monkeypatch.setattr(mod.time, "sleep", lambda s: _REAL_SLEEP(0.2))
    monkeypatch.setattr("sys.argv", ["e2e", str(sample_video), "--url", url, "--target", "Telugu", "--limit-min", "5"])
    with pytest.raises(SystemExit) as e:
        mod.main()
    out = capsys.readouterr().out
    assert e.value.code == 0, out
    for needle in ("PRE-FLIGHT", "PIPELINE", "DUB ONE REEL", "TIMINGS", "Transcribe (Whisper)", "Dub one reel", "HOOKS",
                   "No errors."):
        assert needle in out, needle
    report = sorted(SCRIPT.parent.glob("e2e_report_*.json"))[-1]
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    finally:
        report.unlink()
    types = [s["type"] for s in data["steps"]]
    assert types[0] == "transcription" and types[-1] == "dubbing" and all(s["status"] == "completed" for s in data["steps"])
    assert data["hooks"] and all(h["ok"] for h in data["hooks"])           # every hook is a 3-8 word phrase
    assert data["dubbed_video"]["meta"]["source_asset_id"] and data["errors"] == []


def test_script_reports_failures_with_nonzero_exit(live, sample_video, fakes, monkeypatch, capsys):
    url, ctx = live
    fakes.translator.fail = True                                           # the dub crashes
    mod = _load()
    monkeypatch.setattr(mod.time, "sleep", lambda s: _REAL_SLEEP(0.2))
    ctx.config.set_many({"jobs.auto_retries": 0})
    monkeypatch.setattr("sys.argv", ["e2e", str(sample_video), "--url", url, "--limit-min", "5"])
    with pytest.raises(SystemExit) as e:
        mod.main()
    out = capsys.readouterr().out
    for f in SCRIPT.parent.glob("e2e_report_*.json"):
        f.unlink()
    assert e.value.code == 1 and "ERRORS" in out and "translator crashed" in out


def test_script_refuses_clone_without_consent(sample_video, monkeypatch):
    mod = _load()
    monkeypatch.setattr("sys.argv", ["e2e", str(sample_video), "--clone"])
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert "permission" in str(e.value)
