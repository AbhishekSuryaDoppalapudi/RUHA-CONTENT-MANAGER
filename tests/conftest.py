"""Shared fixtures: a real FFmpeg-generated sample video and fake (offline) AI services."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ruha.ai.embeddings import HashEmbedder  # noqa: E402
from ruha.config import Settings  # noqa: E402
from ruha.context import Services  # noqa: E402
from ruha.main import create_app  # noqa: E402

VIDEO_SECONDS = 200
SEG = 5


def make_segments(duration=VIDEO_SECONDS):
    return [{"start": float(i * SEG), "end": float((i + 1) * SEG),
             "text": f"Segment {i}: grace is the free gift of God number {i} for every person"}
            for i in range(duration // SEG)]


class FakeTranscriber:
    name = "fake-whisper"

    def __init__(self):
        self.calls = 0
        self.fail = False

    def transcribe(self, audio_path, cfg, progress=None, log=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated whisper crash")
        if progress:
            progress(1.0)
        return make_segments(), {"language": "en", "probability": 0.99, "model": "fake", "device": "cpu"}


class FakeLLM:
    name = "fake-llm"

    def __init__(self):
        self.calls = []
        self.fail = False
        self.post_variant = 0

    def status(self, cfg):
        return {"connected": True, "models": ["fake:1b"], "active": cfg.get("llm.model"), "active_installed": True}

    def unload(self, cfg):
        pass

    def generate_json(self, prompt, cfg):
        self.calls.append(prompt[:60])
        if self.fail:
            raise RuntimeError("Ollama unreachable (simulated)")
        if "Write 1 NEW" in prompt or "Write 2 NEW" in prompt:
            self.post_variant += 1
            n = self.post_variant
            return {"posts": [{"headline": f"Fresh hope variant {n}", "body": f"A different angle number {n} about trust",
                               "caption": f"caption variant {n}", "reference": ""}]}
        return {
            "title": "Grace Sermon", "main_theme": "God's grace is free", "summary": "A talk about grace.",
            "themes": ["grace", "gift"], "key_points": ["grace is free"],
            "quotes": ["grace is the free gift of God number 3 for every person", "this sentence was never spoken"],
            "main_reels": [
                {"start": 1, "end": 46, "hook": "Grace is free", "reason": "opening", "caption": "Free grace #1", "hashtags": ["#grace"]},
                {"start": 52, "end": 100, "hook": "Receive the gift", "reason": "middle", "caption": "Gift #2"},
                {"start": 106, "end": 158, "hook": "For everyone", "reason": "end", "caption": "Everyone #3"}],
            "short_reels": [
                {"start": 162, "end": 182, "hook": "Short one", "reason": "s1", "caption": "Short #1"},
                {"start": 183, "end": 200, "hook": "Short two", "reason": "s2", "caption": "Short #2"}],
            "poster": {"headline": "Grace Is Free", "subheadline": "A gift for all",
                       "quote": "grace is the free gift of God number 3 for every person", "theme": "Grace"},
            "encouragement_posts": [
                {"headline": "You are loved", "body": "Rest in the gift you were given today.", "caption": "Loved",
                 "reference": "John 3:16"},
                {"headline": "Keep going", "body": "Every small step of faith matters to heaven.", "caption": "Go",
                 "reference": ""}],
            "hashtags": ["grace", "#faith"],
        }


class FakeTranslator:
    def __init__(self):
        self.calls = []
        self.fail = False

    def status(self):
        return {"available": True, "directory": "fake", "python": "fake", "reason": None}

    def translate(self, video_path, out_dir, *, source="Auto-detect", target="Hindi", voice="Female", dub=False,
                  keep_background=True, burn=False, resolution="Original resolution", progress=None, log=None,
                  cancel=None):
        self.calls.append({"target": target, "dub": dub})
        if self.fail:
            raise RuntimeError("translator crashed (simulated)")
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(0.5, "Translating")
        shutil.copy2(video_path, out / f"translated_{target}.mp4")
        (out / f"subtitles_{target}.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nनमस्ते\n", encoding="utf-8")
        res = {"video": str(out / f"translated_{target}.mp4"), "srt": str(out / f"subtitles_{target}.srt"),
               "status": f"Done -> {target}"}
        if dub:
            (out / "dub.wav").write_bytes(b"RIFF0000WAVE")
            res["dub_audio"] = str(out / "dub.wav")
        return res


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    p = tmp_path_factory.mktemp("media") / "sample sermon.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={VIDEO_SECONDS}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={VIDEO_SECONDS}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)],
                   check=True, capture_output=True)
    return p


@pytest.fixture()
def fakes():
    return Services(transcriber=FakeTranscriber(), llm=FakeLLM(), embedder_factory=lambda cfg: HashEmbedder(),
                    translator=FakeTranslator())


def build_client(data_dir: Path, fakes: Services) -> TestClient:
    settings = Settings(data_dir=data_dir, start_worker=False)
    settings.ensure_dirs()
    app = create_app(settings, fakes)
    return TestClient(app)


@pytest.fixture()
def client(tmp_path, fakes):
    c = build_client(tmp_path / "data", fakes)
    with c:
        ctx = c.app.state.ctx
        ctx.config.set_many({"render.width": 270, "render.height": 480, "render.quality": "draft"})
        yield c


@pytest.fixture()
def ctx(client):
    return client.app.state.ctx


def upload(client, pid, video: Path, **form):
    with open(video, "rb") as f:
        return client.post(f"/api/projects/{pid}/videos", files={"file": (video.name, f, "video/mp4")}, data=form)


@pytest.fixture()
def project_with_video(client, sample_video):
    pid = client.post("/api/projects", json={"name": "Sunday Grace", "tags": ["Sermon", "grace"]}).json()["id"]
    r = upload(client, pid, sample_video)
    assert r.status_code == 201, r.text
    return pid


@pytest.fixture()
def processed(client, ctx, project_with_video):
    """A project that went through the whole pipeline with fake AI services."""
    r = client.post(f"/api/projects/{project_with_video}/process")
    assert r.status_code == 202, r.text
    ctx.queue.drain()
    return project_with_video
