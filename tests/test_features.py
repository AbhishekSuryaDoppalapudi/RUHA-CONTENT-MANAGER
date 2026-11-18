"""Themes, creative subtitles + language, hook/closing cards, image reels, link import and project filters."""
import io
from pathlib import Path
import shutil

import pytest
from PIL import Image

from ruha.services import design, media, subtitles, themes
from conftest import upload
from ruha.models import GeneratedAsset


def assets(client, pid, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return client.get(f"/api/assets?project_id={pid}" + (f"&{qs}" if qs else "")).json()


def file_of(ctx, aid):
    with ctx.db.session() as s:
        return s.get(GeneratedAsset, aid).file_path


def png_bytes(color=(120, 60, 200), size=(400, 300)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


# ----------------------------------- themes ----------------------------------
def test_every_theme_draws_a_distinct_background():
    sums = set()
    for name in themes.THEMES:
        img = themes.canvas((120, 160), name, "seed")
        assert img.size == (120, 160)
        sums.add(img.tobytes()[:600])
    assert len(sums) == len(themes.THEMES)


def test_theme_resolution_prefers_model_choice_then_keywords():
    assert themes.resolve("Peace", "anything") == "peace"
    assert themes.resolve("nonsense", "a battle against the enemy and darkness") == "warfare"
    assert themes.resolve(None, "") in themes.THEMES


def test_graphic_and_cards_render_with_a_theme(tmp_path):
    out = design.render_graphic(tmp_path / "p.png", "Stand firm", "", "Ephesians 6:12", "Highlight", "poster",
                                None, "centered", (540, 675), theme="warfare")
    assert Image.open(out).size == (540, 675)
    for kind in ("open", "close"):
        c = design.render_card(tmp_path / f"{kind}.png", "Your real enemy", "faith", None, kind, "John 3:16",
                               "Stay strong", size=(270, 480))
        assert Image.open(c).size == (270, 480)


# ------------------------------- creative subtitles --------------------------
def test_ass_subtitles_are_creative_and_script_aware(tmp_path):
    f = subtitles.build_ass([(0, 2, "The wonderful grace of God"), (2, 4, "దేవుని కృప అద్భుతమైనది")], tmp_path / "s.ass",
                            270, 480, {}, accent="#FFB74D")
    txt = f.read_text(encoding="utf-8-sig")
    assert "WONDERFUL" in txt and "GRACE" in txt                  # Latin text is upper-cased
    assert "Style: Latin" in txt and "Style: Indic" in txt
    assert ",Indic,," in txt and ",Latin,," in txt          # Telugu line uses the Indic font, English the Latin one
    assert "\\fad(" in txt and "\\t(0,170" in txt              # pop-in animation
    assert "&H004DB7FF&" in txt                               # keyword highlighted in the accent colour


def test_reel_render_with_cards_and_ass_is_longer_than_clip(tmp_path, sample_video):
    o = design.render_card(tmp_path / "o.png", "Hook line", "hope", None, "open", size=(270, 480))
    c = design.render_card(tmp_path / "c.png", "", "hope", None, "close", size=(270, 480))
    plain = media.render_reel(sample_video, 0, 3, tmp_path / "a.mp4", width=270, height=480, crf=32, preset="ultrafast",
                              cues=[(0, 2, "Hello wonderful world")])
    full = media.render_reel(sample_video, 0, 3, tmp_path / "b.mp4", width=270, height=480, crf=32, preset="ultrafast",
                             cues=[(0, 2, "Hello wonderful world")], open_card=o, close_card=c)
    d_plain, d_full = media.probe(plain)["duration"], media.probe(full)["duration"]
    assert d_full - d_plain > 4.0                              # 2.2 s hook + 2.6 s closing
    assert media.probe(full)["width"] == 270 and media.probe(full)["audio_tracks"] == 1


# ------------------------------- pipeline output -----------------------------
def test_pipeline_makes_one_main_two_short_one_poster_two_posts_with_themes(client, ctx, processed):
    a = assets(client, processed)
    kinds = [x["kind"] for x in a]
    assert kinds.count("main_reel") == 1 and kinds.count("short_reel") == 2
    assert kinds.count("poster") == 1 and kinds.count("social_post") == 2
    reel = [x for x in a if x["kind"] == "main_reel"][0]
    d = client.get(f"/api/assets/{reel['id']}").json()
    assert d["meta"]["visual_theme"] == "grace" and d["meta"]["language"] == "English"
    assert d["meta"]["has_hook_card"] and d["meta"]["has_closing_card"]
    poster = [x for x in a if x["kind"] == "poster"][0]
    pd = client.get(f"/api/assets/{poster['id']}").json()
    assert pd["meta"]["visual_theme"] == "grace"
    assert pd["poster"]["subheadline"] == "Segment 3"          # the verse the speaker cited
    posts = [client.get(f"/api/assets/{x['id']}").json() for x in a if x["kind"] == "social_post"]
    assert all(p["social_post"]["body"] == "" and p["social_post"]["headline"] for p in posts)   # hook only
    assert {p["social_post"]["reference"] for p in posts} == {"Segment 3", ""}


def test_subtitles_follow_the_requested_language(client, ctx, project_with_video):
    client.post(f"/api/projects/{project_with_video}/process", json={"language": "Hindi"})
    ctx.queue.drain()
    reel = [x for x in assets(client, project_with_video) if x["kind"] == "main_reel"][0]
    d = client.get(f"/api/assets/{reel['id']}").json()
    assert d["meta"]["language"] == "Hindi"
    srt = [x for x in assets(client, project_with_video, kind="subtitle") if x["slot"] == "main_1_srt"][0]
    text = open(file_of(ctx, srt["id"]), encoding="utf-8").read()
    assert "[tr]" in text                                       # went through the translation step


def test_english_is_the_default_and_needs_no_translation(client, ctx, processed):
    srt = [x for x in assets(client, processed, kind="subtitle") if x["slot"] == "main_1_srt"][0]
    text = open(file_of(ctx, srt["id"]), encoding="utf-8").read()
    assert "[tr]" not in text and "Segment" in text


# ---------------------------------- image reels ------------------------------
def test_image_reel_from_uploaded_pictures(client, ctx):
    pid = client.post("/api/projects", json={"name": "Photos"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/jobs", json={"job_type": "image_reel"}).status_code == 400   # nothing uploaded
    files = [("files", ("one.png", png_bytes(), "image/png")), ("files", ("two.png", png_bytes((20, 160, 120)), "image/png"))]
    r = client.post(f"/api/projects/{pid}/images", files=files)
    assert r.status_code == 201 and r.json()["saved"] == ["one.png", "two.png"]
    assert [i["name"] for i in client.get(f"/api/projects/{pid}/images").json()] == ["one.png", "two.png"]
    assert client.get(f"/api/projects/{pid}/images/one.png").status_code == 200
    jid = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "image_reel", "params": {
        "hook": "Peace in the storm", "captions": ["Be still", "He is near"], "seconds_per_image": 2,
        "theme": "peace", "verse": "Psalm 46:10"}}).json()["job_id"]
    ctx.queue.drain()
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "completed", job.get("error")
    reel = assets(client, pid, kind="image_reel")[0]
    d = client.get(f"/api/assets/{reel['id']}").json()
    assert d["title"] == "Peace in the storm" and d["meta"]["visual_theme"] == "peace"
    dur = media.probe(file_of(ctx, reel["id"]))["duration"]
    assert 8.5 < dur < 10.5                                     # 2 images x 2 s + 2.2 s hook + 2.6 s closing
    assert client.get(f"/api/library?type=image&project_id={pid}").json()["total"] == 1


def test_image_upload_rejects_non_images_and_traversal(client):
    pid = client.post("/api/projects", json={"name": "Photos"}).json()["id"]
    bad = client.post(f"/api/projects/{pid}/images", files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert bad.status_code == 400
    fake = client.post(f"/api/projects/{pid}/images", files=[("files", ("fake.png", b"not really a png", "image/png"))])
    assert fake.status_code == 400
    assert client.get(f"/api/projects/{pid}/images/..%2F..%2Fruha.db").status_code in (404, 422)


# ------------------------------- link import (mocked download) ---------------
def test_import_url_downloads_then_runs_the_pipeline(client, ctx, sample_video, tmp_path, monkeypatch):
    from ruha.ai import ytdl

    def fake_download(url, out_dir, max_height=720, progress=None, log=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / "abc123.mp4"
        shutil.copy2(sample_video, dst)
        return dst, "A Real Talk"
    monkeypatch.setattr(ytdl, "download", fake_download)
    assert client.post("/api/import-url", json={"url": "ftp://nope.example/x"}).status_code == 400
    assert client.post("/api/import-url", json={"url": "not a link at all"}).status_code == 400
    r = client.post("/api/import-url", json={"url": "https://www.youtube.com/watch?v=abc123", "language": "English"})
    assert r.status_code == 202
    pid = r.json()["project_id"]
    ctx.queue.drain()
    p = client.get(f"/api/projects/{pid}").json()
    assert p["name"] == "A Real Talk" and p["video"]
    kinds = [x["kind"] for x in assets(client, pid)]
    assert kinds.count("main_reel") == 1 and kinds.count("poster") == 1


# ------------------------------- finding work easily -------------------------
def test_library_and_review_filter_by_project_and_status(client, ctx, processed, sample_video):
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    upload(client, other, sample_video)
    ctx.queue.enqueue_pipeline(other)
    ctx.queue.drain()
    lib = client.get(f"/api/library?project_id={processed}").json()
    assert lib["total"] and all(i["project_id"] == processed for i in lib["items"])
    first = [x for x in assets(client, processed) if x["kind"] == "poster"][0]
    assert client.post(f"/api/assets/{first['id']}/review", json={"action": "approve"}).status_code == 200
    appr = client.get(f"/api/library?project_id={processed}&status=approved").json()
    assert [i["id"] for i in appr["items"]] == [first["id"]]
    rq = client.get("/api/dashboard").json()["review_queue"]
    # subtitles / transcripts are not reviewable, so they are not counted as "needs review"
    assert rq.get("draft", 0) == 2 * (1 + 2 + 1 + 2) - 1


# ------------------------- HTML renderer, fonts, AI backgrounds -----------------------
from ruha.services import htmlrender  # noqa: E402

needs_browser = pytest.mark.skipif(not htmlrender.available(), reason="no Edge/Chrome/Chromium installed")


def test_open_licence_fonts_are_bundled():
    for fn in ("Montserrat.ttf", "PlayfairDisplay.ttf", "NotoSansDevanagari.ttf", "NotoSansTelugu.ttf",
               "NotoSansTamil.ttf", "OFL-Montserrat.txt"):
        assert (htmlrender.FONT_DIR / fn).stat().st_size > 1000, fn


@needs_browser
def test_html_renderer_makes_poster_post_and_cards(tmp_path):
    brand = {**design.DEFAULT_BRAND, "engine": "html"}
    p = design.render_graphic(tmp_path / "p.png", "Stop fighting the wrong enemy",
                              "We do not wrestle against flesh and blood", "Ephesians 6:12", "Highlight", "poster",
                              brand, size=(540, 675), theme="warfare")
    im = Image.open(p)
    assert im.size == (540, 675)
    assert max(im.convert("L").getextrema()) > 200 and im.convert("L").getextrema()[0] < 60   # text on dark
    t = design.render_graphic(tmp_path / "te.png", "దేవుడు నిన్ను ఎప్పుడూ విడిచిపెట్టడు", "", "కీర్తనలు 23:4",
                              "ప్రోత్సాహం", "social", brand, size=(540, 675), theme="peace")
    assert Image.open(t).size == (540, 675)
    for kind in ("open", "close"):
        c = design.render_card(tmp_path / f"{kind}.png", "Hook words", "grace", brand, kind, "John 3:16", "Follow",
                               (270, 480))
        assert Image.open(c).size == (270, 480)


def test_html_failure_falls_back_to_the_builtin_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr(htmlrender, "available", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("browser crashed")
    monkeypatch.setattr(htmlrender, "render_graphic", boom)
    monkeypatch.setattr(htmlrender, "render_card", boom)
    brand = {**design.DEFAULT_BRAND, "engine": "html"}
    g = design.render_graphic(tmp_path / "g.png", "Hello", "", "", "Highlight", "social", brand, size=(270, 338),
                              theme="hope")
    c = design.render_card(tmp_path / "c.png", "Hello", "hope", brand, "open", "", "", (270, 480))
    assert Image.open(g).size == (270, 338) and Image.open(c).size == (270, 480)


def test_ai_background_is_generated_once_per_theme_and_used(client, ctx, project_with_video):
    gen = ctx.services.imagegen
    ctx.config.set_many({"design.ai_background": True})
    assert client.post(f"/api/projects/{project_with_video}/process").status_code == 202
    ctx.queue.drain()
    bgs = list((ctx.settings.data_dir / "projects").glob("*/backgrounds/*.png"))
    assert len(bgs) == 1 and bgs[0].stem == "grace"
    assert len(gen.calls) == 1                        # cached: poster, posts and cards share one image
    kinds = {a["kind"] for a in assets(client, project_with_video)}
    assert {"poster", "social_post", "main_reel"} <= kinds


def test_ai_background_failure_falls_back_to_builtin_theme(client, ctx, project_with_video):
    ctx.services.imagegen.fail = True
    ctx.config.set_many({"design.ai_background": True})
    assert client.post(f"/api/projects/{project_with_video}/process").status_code == 202
    ctx.queue.drain()
    kinds = {a["kind"] for a in assets(client, project_with_video)}
    assert {"poster", "social_post", "main_reel"} <= kinds
    assert len(ctx.services.imagegen.calls) == 1      # one failed attempt, not one per image
    assert not list((ctx.settings.data_dir / "projects").glob("*/backgrounds/*.png"))


def test_ai_prompts_describe_scenes_without_people():
    from ruha.ai import imagegen
    for name in themes.THEMES:
        pr = imagegen.prompt_for(name, "spiritual battle")
        assert name in imagegen.THEME_SCENES and len(pr) > 40
        assert "person" not in pr.lower() and "face" not in pr.lower()
    assert "face" in imagegen.NEGATIVE and "text" in imagegen.NEGATIVE


# ------------------------------- link download robustness ---------------------------
class _FakeYDL:
    attempts: list = []
    plan: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        _FakeYDL.attempts.append(self.opts.get("extractor_args"))
        outcome = _FakeYDL.plan[len(_FakeYDL.attempts) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        Path(self.opts["outtmpl"].replace("%(id)s.%(ext)s", "abc.mp4")).write_bytes(b"x" * 10)
        return {"id": "abc", "title": "T", "ext": "mp4"}

    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(id)s", "abc").replace("%(ext)s", "mp4")


def _run_download(tmp_path, monkeypatch, plan):
    import sys
    import types
    from ruha.ai import ytdl
    _FakeYDL.attempts, _FakeYDL.plan = [], plan
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_FakeYDL))
    return ytdl, ytdl.download("https://example.com/v", tmp_path, 720)


def test_download_retries_another_client_after_a_403(tmp_path, monkeypatch):
    ytdl, (path, title) = _run_download(tmp_path, monkeypatch, [Exception("HTTP Error 403: Forbidden"), "ok"])
    assert path.exists() and title == "T"
    assert _FakeYDL.attempts[0] is None and _FakeYDL.attempts[1]["youtube"]["player_client"]


def test_download_gives_a_helpful_message_when_every_client_is_refused(tmp_path, monkeypatch):
    from ruha.ai import ytdl
    with pytest.raises(ytdl.DownloadError) as e:
        _run_download(tmp_path, monkeypatch, [Exception("HTTP Error 403: Forbidden")] * 4)
    assert "upload the file" in str(e.value) and len(_FakeYDL.attempts) == 4


def test_download_does_not_retry_an_unrelated_error(tmp_path, monkeypatch):
    from ruha.ai import ytdl
    with pytest.raises(ytdl.DownloadError):
        _run_download(tmp_path, monkeypatch, [Exception("Unsupported URL")])
    assert len(_FakeYDL.attempts) == 1
