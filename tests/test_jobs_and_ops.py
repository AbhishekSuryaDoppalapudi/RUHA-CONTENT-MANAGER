"""Job queue behaviour, failure/retry, restart persistence, backup/restore, translator bridge."""
import json
import sys
import textwrap
import time
import zipfile
from pathlib import Path

import pytest

from conftest import FakeLLM, FakeTranscriber, FakeTranslator, build_client, upload
from ruha.ai.translator import TranslatorBridge
from ruha.config import Settings
from ruha.models import GeneratedAsset, ProcessingJob
from ruha.services import media


def jobs(client, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return client.get(f"/api/jobs?{qs}").json()["items"]


def kinds(client, pid):
    out = {}
    for a in client.get(f"/api/assets?project_id={pid}").json():
        out[a["kind"]] = out.get(a["kind"], 0) + 1
    return out


# ------------------------------ failure & retry -------------------------------
def test_stage_failure_blocks_dependents_then_retry_recovers(client, ctx, project_with_video, fakes):
    pid = project_with_video
    fakes.transcriber.fail = True
    client.post(f"/api/projects/{pid}/process")
    ctx.queue.drain()
    js = {j["job_type"]: j for j in jobs(client, project_id=pid)}
    assert js["transcription"]["status"] == "failed" and "simulated whisper crash" in js["transcription"]["error"]
    assert all(js[t]["status"] == "failed" and "Not run because" in js[t]["error"]
               for t in ("reel_generation", "poster_generation", "social_post_generation", "carousel_generation", "thumbnail_generation"))
    assert client.get(f"/api/projects/{pid}").json()["status"] == "failed"
    log = client.get(f"/api/jobs/{js['transcription']['id']}").json()["logs"]
    assert any(l["level"] == "ERROR" for l in log)

    fakes.transcriber.fail = False
    assert client.post("/api/queue/retry-failed").json()["retried"] == 6        # transcription + the five steps that depend on it
    ctx.queue.drain()
    assert all(j["status"] == "completed" for j in jobs(client, project_id=pid))
    assert kinds(client, pid)["main_reel"] == 1
    assert client.get(f"/api/projects/{pid}").json()["status"] == "review_required"
    assert client.post(f"/api/jobs/{js['transcription']['id']}/retry").status_code == 409     # already completed


def test_llm_outage_keeps_transcript_and_later_retry_finishes(client, ctx, project_with_video, fakes):
    pid = project_with_video
    fakes.llm.fail = True
    client.post(f"/api/projects/{pid}/process")
    ctx.queue.drain()
    by = {j["job_type"]: j for j in jobs(client, project_id=pid)}
    assert by["transcription"]["status"] == "completed"                      # transcript is safe
    assert by["transcription"]["result"]["plan_created"] is False
    assert by["reel_generation"]["status"] == "failed"
    assert client.get(f"/api/projects/{pid}/transcript").json()["exists"]
    assert kinds(client, pid).get("transcript") == 1
    fakes.llm.fail = False
    client.post("/api/queue/retry-failed")
    ctx.queue.drain()
    assert kinds(client, pid)["social_post"] == 2 and fakes.transcriber.calls == 1      # no re-transcription


def test_partial_reel_progress_is_kept_and_resumed(client, ctx, project_with_video, monkeypatch):
    pid = project_with_video
    real, calls = media.render_reel, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise media.MediaError("disk full (simulated)")
        return real(*a, **k)
    monkeypatch.setattr(media, "render_reel", flaky)
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    k0 = kinds(client, pid)
    assert k0.get("main_reel") == 1 and k0.get("short_reel") == 1             # first two survived the failure (3rd render crashed)
    assert jobs(client, status="failed")[0]["job_type"] == "reel_generation"
    monkeypatch.setattr(media, "render_reel", real)
    client.post("/api/queue/retry-failed")
    before = len(list(ctx.settings.projects_dir.rglob("main_1_v1.mp4")))
    ctx.queue.drain()
    k = kinds(client, pid)
    assert k["main_reel"] == 1 and k["short_reel"] == 3 and before == 1       # the fake sermon fits 3 shorts


def test_cancel_queued_and_running_jobs(client, ctx, project_with_video, fakes):
    pid = project_with_video
    j1 = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "poster_generation"}).json()["job_id"]
    assert client.post(f"/api/jobs/{j1}/cancel").status_code == 200
    assert client.get(f"/api/jobs/{j1}").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{j1}/cancel").status_code == 409
    assert client.post(f"/api/jobs/{j1}/retry").json()["retried"] == j1

    class CancellingTranscriber(FakeTranscriber):
        def transcribe(self, audio_path, cfg, progress=None, log=None):
            ctx.queue.cancel(ctx.queue.current_job)
            progress(0.5)                                        # next progress call notices the cancel
            raise AssertionError("should not get here")
    fakes.transcriber = CancellingTranscriber()
    ctx.services.transcriber = fakes.transcriber
    jt = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "transcription"}).json()["job_id"]
    ctx.queue.drain()
    assert client.get(f"/api/jobs/{jt}").json()["status"] == "cancelled"
    assert client.get(f"/api/projects/{pid}/transcript").json()["exists"] is False


def test_unknown_job_type_and_missing_video_rejected(client):
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/jobs", json={"job_type": "magic"}).status_code == 400
    assert client.post(f"/api/projects/{pid}/jobs", json={"job_type": "transcription"}).status_code == 400


def test_background_worker_honours_pause_and_resume(tmp_path, fakes, sample_video):
    # fresh app with the real worker thread enabled
    from ruha.main import create_app
    from fastapi.testclient import TestClient
    st = Settings(data_dir=tmp_path / "d2", start_worker=True)
    st.ensure_dirs()
    app = create_app(st, fakes)
    with TestClient(app) as c:
        pid = c.post("/api/projects", json={"name": "bg"}).json()["id"]
        assert c.post("/api/queue/pause").json()["paused"]
        with open(sample_video, "rb") as f:
            assert c.post(f"/api/projects/{pid}/videos", files={"file": ("bg.mp4", f, "video/mp4")}).status_code == 201
        jid = c.post(f"/api/projects/{pid}/jobs", json={"job_type": "transcription"}).json()["job_id"]
        time.sleep(3)
        assert c.get(f"/api/jobs/{jid}").json()["status"] == "queued"          # paused: nothing runs
        c.post("/api/queue/resume")
        deadline = time.time() + 60
        while time.time() < deadline and c.get(f"/api/jobs/{jid}").json()["status"] not in ("completed", "failed"):
            time.sleep(0.5)
        assert c.get(f"/api/jobs/{jid}").json()["status"] == "completed"



# ------------------------------ restart & persistence -------------------------
def test_data_survives_application_restart(tmp_path, fakes, sample_video):
    data = tmp_path / "persist"
    c1 = build_client(data, fakes)
    with c1:
        c1.app.state.ctx.config.set_many({"render.width": 270, "render.height": 480, "render.quality": "draft"})
        pid = c1.post("/api/projects", json={"name": "Persist", "tags": ["keep"]}).json()["id"]
        upload(c1, pid, sample_video)
        c1.post(f"/api/projects/{pid}/process")
        c1.app.state.ctx.queue.drain()
        asset = c1.get(f"/api/assets?project_id={pid}&kind=main_reel").json()[0]
        c1.post(f"/api/assets/{asset['id']}/review", json={"action": "approve"})
        c1.put("/api/settings", json={"values": {"whisper.model": "tiny"}})
    # ---- new process: brand-new objects on the same folder ----
    c2 = build_client(data, fakes)
    with c2:
        p = c2.get(f"/api/projects/{pid}").json()
        assert p["name"] == "Persist" and p["tags"] == ["keep"] and p["status"] == "review_required"
        assert p["video"]["duration"] > 190 and p["asset_counts"]["main_reel"] == 1
        a = c2.get(f"/api/assets/{asset['id']}").json()
        assert a["status"] == "approved" and a["has_file"] and c2.get(f"/api/assets/{asset['id']}/file").status_code == 200
        assert c2.get("/api/settings").json()["values"]["whisper.model"] == "tiny"
        assert c2.get("/api/memory/search?q=free gift of grace").json()["hits"]            # vectors persisted
        assert c2.get(f"/api/projects/{pid}/transcript").json()["exists"]
        assert c2.get("/api/dashboard").json()["metrics"]["reels_generated"] == 4


def test_interrupted_job_is_requeued_and_finishes_after_restart(tmp_path, fakes, sample_video):
    data = tmp_path / "crash"
    c1 = build_client(data, fakes)
    with c1:
        c1.app.state.ctx.config.set_many({"render.width": 270, "render.height": 480, "render.quality": "draft"})
        pid = c1.post("/api/projects", json={"name": "Crash"}).json()["id"]
        upload(c1, pid, sample_video)
        jid = c1.post(f"/api/projects/{pid}/jobs", json={"job_type": "transcription"}).json()["job_id"]
        ctx = c1.app.state.ctx
        with ctx.db.session() as s:                        # simulate a hard kill mid-job
            j = s.get(ProcessingJob, jid)
            j.status, j.progress = "running", 40
    c2 = build_client(data, fakes)
    with c2:
        ctx2 = c2.app.state.ctx          # startup already re-queued the interrupted job
        j = c2.get(f"/api/jobs/{jid}").json()
        assert j["status"] == "queued" and any("re-queued" in l["message"] for l in j["logs"])
        ctx2.queue.drain()
        assert c2.get(f"/api/jobs/{jid}").json()["status"] == "completed"


# ------------------------------ backup & restore -------------------------------
def test_backup_and_restore_roundtrip_in_place(client, ctx, processed):
    pid = processed
    reel = client.get(f"/api/assets?project_id={pid}&kind=main_reel").json()[0]
    client.post(f"/api/assets/{reel['id']}/review", json={"action": "approve"})
    b = client.post("/api/backups", json={"include_media": True}).json()
    assert b["size_bytes"] > 1000 and client.get("/api/backups").json()[0]["name"] == b["name"]
    assert client.delete(f"/api/projects/{pid}?confirm=true").status_code == 200
    assert client.get("/api/projects").json()["total"] == 0
    r = client.post(f"/api/backups/{b['name']}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["safety_backup"].endswith("pre-restore.zip")
    p = client.get(f"/api/projects/{pid}").json()
    assert p["name"] == "Sunday Grace" and p["asset_counts"]["main_reel"] == 1
    a = client.get(f"/api/assets/{reel['id']}").json()
    assert a["status"] == "approved" and a["has_file"]
    assert client.get(f"/api/assets/{reel['id']}/file").status_code == 200
    assert client.get(f"/api/videos/{p['video']['id']}/stream", headers={"Range": "bytes=0-9"}).status_code == 206
    assert client.get("/api/memory/search?q=free gift of grace").json()["hits"]      # memory rebuilt
    assert client.get("/api/system/info").json()["database"]["projects"] == 1


def test_restore_into_different_location_rebases_paths(tmp_path, fakes, client, processed):
    b = client.post("/api/backups", json={"include_media": True}).json()
    src = client.app.state.ctx.settings.backups_dir / b["name"]
    other = build_client(tmp_path / "other-machine", FakeFactory.make())
    with other:
        with open(src, "rb") as f:
            up = other.post("/api/backups/upload", files={"file": ("b.zip", f, "application/zip")})
        assert up.status_code == 201, up.text
        r = other.post(f"/api/backups/{up.json()['name']}/restore")
        assert r.status_code == 200, r.text
        ctx = other.app.state.ctx
        reel = other.get(f"/api/assets?project_id={processed}&kind=main_reel").json()[0]
        assert other.get(f"/api/assets/{reel['id']}/file").status_code == 200
        with ctx.db.session() as s:
            paths = [a.file_path for a in s.query(GeneratedAsset) if a.file_path]
        assert paths and all(str(ctx.settings.data_dir) in p for p in paths)


class FakeFactory:
    @staticmethod
    def make():
        from conftest import Services, HashEmbedder
        return Services(transcriber=FakeTranscriber(), llm=FakeLLM(), embedder_factory=lambda cfg: HashEmbedder(),
                        translator=FakeTranslator())


def test_malicious_or_invalid_backup_rejected(client, tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("manifest.json", "{}")
        z.writestr("ruha.db", "x")
        z.writestr("../../escape.txt", "pwned")
    with open(evil, "rb") as f:
        r = client.post("/api/backups/upload", files={"file": ("e.zip", f, "application/zip")})
    assert r.status_code == 400 and "Unsafe path" in r.json()["detail"]
    assert not (tmp_path.parent / "escape.txt").exists()
    r = client.post("/api/backups/upload", files={"file": ("n.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400
    assert client.post("/api/backups/ruha-backup-nonexistent.zip/restore").status_code == 400
    assert client.get("/api/backups/..%2Fruha.db/download").status_code in (404, 400)


def test_backup_without_media_is_smaller(client, processed):
    full = client.post("/api/backups", json={"include_media": True}).json()["size_bytes"]
    lite = client.post("/api/backups", json={"include_media": False}).json()["size_bytes"]
    assert lite < full


# ------------------------------ translation -----------------------------------
def test_translation_and_dubbing_jobs_register_assets(client, ctx, project_with_video, fakes):
    pid = project_with_video
    client.post(f"/api/projects/{pid}/jobs", json={"job_type": "translation", "params": {"target": "Telugu"}})
    client.post(f"/api/projects/{pid}/jobs", json={"job_type": "dubbing", "params": {"target": "Hindi", "voice": "Male"}})
    ctx.queue.drain()
    assert [c["dub"] for c in fakes.translator.calls] == [False, True]
    k = kinds(client, pid)
    assert k["translation_video"] == 2 and k["translation_subtitle"] == 2 and k["audio"] == 1
    tr = client.get(f"/api/projects/{pid}").json()["translations"]
    assert {(t["target"], t["dubbed"], t["status"]) for t in tr} == {("Telugu", False, "completed"), ("Hindi", True, "completed")}
    assert client.get("/api/library?type=translation").json()["total"] == 2
    assert client.get("/api/dashboard").json()["metrics"]["translations_completed"] == 2


def test_translator_failure_is_recorded_and_other_outputs_survive(client, ctx, processed, fakes):
    fakes.translator.fail = True
    jid = client.post(f"/api/projects/{processed}/jobs", json={"job_type": "translation", "params": {"target": "Tamil"}}).json()["job_id"]
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and "translator crashed" in j["error"]
    assert client.get(f"/api/projects/{processed}").json()["translations"][0]["status"] == "failed"
    assert kinds(client, processed)["main_reel"] == 1                       # nothing else lost


# ------------------------------ translator bridge ------------------------------
FAKE_TRANSLATOR = '''
import os, tempfile
def _translate_impl(video, src, tgt, voice, do_dub, keep_bg, burn, start, length, res, progress):
    progress(0.25, desc="Extracting")
    if tgt == "Boom":
        raise RuntimeError("model exploded")
    d = tempfile.mkdtemp()
    out = os.path.join(d, "translated_%s.mp4" % tgt)
    open(out, "wb").write(open(video, "rb").read()[:100])
    srt = os.path.join(d, "subtitles_%s.srt" % tgt)
    open(srt, "w", encoding="utf-8").write("1\\n00:00:00,000 --> 00:00:01,000\\nhi\\n")
    progress(0.9, desc="Building")
    return out, srt, None, "Done %s dub=%s" % (tgt, do_dub)
'''


def test_translator_bridge_protocol_with_stand_in_translator(tmp_path, sample_video):
    tdir = tmp_path / "translator"
    tdir.mkdir()
    (tdir / "app.py").write_text(FAKE_TRANSLATOR, encoding="utf-8")
    st = Settings(data_dir=tmp_path / "data", translator_dir=tdir, translator_python=Path(sys.executable))
    bridge = TranslatorBridge(st)
    assert bridge.status()["available"]
    seen = []
    res = bridge.translate(sample_video, tmp_path / "out", target="Telugu", progress=lambda f, d: seen.append((f, d)))
    assert res["status"] == "Done Telugu dub=False" and Path(res["video"]).exists() and Path(res["srt"]).exists()
    assert seen == [(0.25, "Extracting"), (0.9, "Building")] and "dub_audio" not in res
    with pytest.raises(RuntimeError, match="model exploded"):
        bridge.translate(sample_video, tmp_path / "out2", target="Boom")


def test_translator_bridge_reports_missing_install(tmp_path):
    st = Settings(data_dir=tmp_path, translator_dir=tmp_path / "nope", translator_python=tmp_path / "nope" / "py.exe")
    b = TranslatorBridge(st)
    assert b.status()["available"] is False
    with pytest.raises(Exception, match="not found"):
        b.translate("x.mp4", tmp_path / "o")


# ------------------------------ file access safety -----------------------------
def test_files_outside_storage_are_never_served(client, ctx, processed, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    a = client.get(f"/api/assets?project_id={processed}&kind=poster").json()[0]
    with ctx.db.session() as s:
        s.get(GeneratedAsset, a["id"]).file_path = str(secret)
    r = client.get(f"/api/assets/{a['id']}/file")
    assert r.status_code == 403 and "top secret" not in r.text
    assert client.get(f"/api/assets/{a['id']}/text").status_code == 403


def test_error_responses_do_not_leak_paths(client, ctx, processed):
    a = client.get(f"/api/assets?project_id={processed}&kind=poster").json()[0]
    Path(ctx.db.session().__enter__().get(GeneratedAsset, a["id"]).file_path).unlink()
    r = client.get(f"/api/assets/{a['id']}/file")
    assert r.status_code == 404 and "Users" not in r.text and str(ctx.settings.data_dir) not in r.text
