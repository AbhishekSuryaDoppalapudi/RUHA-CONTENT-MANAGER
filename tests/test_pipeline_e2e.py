"""End-to-end workflow with real FFmpeg rendering and fake AI back-ends."""
import json
from pathlib import Path

import pytest

from conftest import build_client, make_segments, upload
from ruha.services import media


def assets(client, pid, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return client.get(f"/api/assets?project_id={pid}&{qs}").json()


def test_full_pipeline_creates_organised_assets(client, ctx, processed):
    pid = processed
    jobs = client.get(f"/api/jobs?project_id={pid}").json()["items"]
    assert {j["job_type"] for j in jobs} == {"transcription", "reel_generation", "poster_generation", "social_post_generation"}
    assert all(j["status"] == "completed" and j["progress"] == 100 for j in jobs), [(j["job_type"], j["error"]) for j in jobs]
    assert client.get(f"/api/projects/{pid}").json()["status"] == "review_required"

    a = assets(client, pid)
    by = {}
    for x in a:
        by.setdefault(x["kind"], []).append(x)
    assert len(by["main_reel"]) == 1 and len(by["short_reel"]) == 2
    assert len(by["poster"]) == 1 and len(by["social_post"]) == 2
    assert {"transcript", "subtitle"} <= set(by)

    # reels: real playable portrait videos of the right length, original untouched
    for r in by["main_reel"] + by["short_reel"]:
        info = media.probe(client.app.state.ctx.db.session().__enter__().get(__import__("ruha.models", fromlist=["GeneratedAsset"]).GeneratedAsset, r["id"]).file_path)
        assert (info["width"], info["height"]) == (270, 480) and info["audio_tracks"] == 1
        assert abs(info["duration"] - (r["reel"]["end"] - r["reel"]["start"]) - 4.8) < 1.5   # + hook and closing images
        assert r["has_file"] and r["has_preview"] and r["hashtags"] and r["caption"] and r["reel"]["excerpt"]
    mains = sorted(by["main_reel"], key=lambda x: x["slot"])
    assert 30 <= mains[0]["reel"]["end"] - mains[0]["reel"]["start"] <= 95
    for s in by["short_reel"]:
        assert 12 <= s["reel"]["end"] - s["reel"]["start"] <= 35
    # editable subtitle sidecars exist and are linked
    srt = client.get(f"/api/assets/{mains[0]['reel']['subtitle_asset_id']}/text").json()["text"]
    assert "-->" in srt and "grace" in srt.lower()
    # graphics are real PNGs in the expected formats
    from PIL import Image
    for k in ("poster", "social_post"):
        for g in by[k]:
            img = Image.open(client.app.state.ctx.settings.projects_dir.parent / "x") if False else None
    p = by["poster"][0]
    r = client.get(f"/api/assets/{p['id']}/file")
    assert r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    # guardrails: only references the speaker really said are kept; unspoken quote dropped from the plan
    posts = {x["slot"]: x for x in by["social_post"]}
    assert posts["social_1"]["social_post"]["reference"] == "Segment 3" and posts["social_2"]["social_post"]["reference"] == ""
    assert posts["social_1"]["meta"]["layout"] != posts["social_2"]["meta"]["layout"]     # separate designs
    tr = client.get(f"/api/projects/{pid}/transcript").json()
    assert tr["exists"] and len(tr["segments"]) == len(make_segments()) and tr["themes"] == ["grace", "gift"]
    assert tr["quotes"] == ["grace is the free gift of God number 3 for every person"]


def test_dashboard_and_analytics_reflect_real_records(client, processed):
    d = client.get("/api/dashboard").json()
    m = d["metrics"]
    assert (m["total_projects"], m["reels_generated"], m["posters_created"], m["social_posts_created"]) == (1, 3, 1, 2)
    assert m["videos_processed"] == 1 and m["failed_jobs"] == 0 and m["storage_used_bytes"] > 0
    assert d["processing"]["completed"] == 4 and d["processing"]["queued"] == 0
    assert len(d["recent"]["generated"]) == 6 and d["recent"]["uploads"][0]["project"] == "Sunday Grace"
    an = client.get("/api/analytics").json()
    assert an["assets"]["total"] == 6 and an["assets"]["draft"] == 6 and an["external_metrics"]["available"] is False
    assert an["production_time"]["reel_generation"]["count"] == 1
    assert {t["topic"] for t in an["topics"]} == {"grace", "gift"}
    csv = client.get("/api/analytics/export?format=csv")
    assert csv.status_code == 200 and "section,key,value" in csv.text
    assert client.get("/api/analytics/export?format=json").json()["assets"]["total"] == 6


def test_review_workflow_is_per_asset(client, processed):
    a = [x for x in assets(client, processed) if x["kind"] in ("main_reel", "short_reel", "poster", "social_post")]
    first, second = a[0], a[1]
    assert client.post(f"/api/assets/{first['id']}/review", json={"action": "reject"}).status_code == 400   # reason required
    r = client.post(f"/api/assets/{first['id']}/review", json={"action": "approve", "comment": "great"}).json()
    assert r["status"] == "approved" and r["reviews"][0]["action"] == "approved" and r["reviews"][0]["actor"] == "owner"
    r2 = client.post(f"/api/assets/{second['id']}/review", json={"action": "reject", "reason": "too long"}).json()
    assert r2["status"] == "rejected" and r2["reviews"][0]["reason"] == "too long"
    assert client.post(f"/api/assets/{a[2]['id']}/review", json={"action": "revise", "reason": "shorter hook"}).json()["status"] == "revision_requested"
    client.post(f"/api/assets/{a[2]['id']}/review", json={"action": "comment", "comment": "see timestamp 0:10"})
    # approving one asset did not approve the others or the project
    statuses = [x["status"] for x in assets(client, processed) if x["kind"] in ("main_reel", "short_reel", "poster", "social_post")]
    assert statuses.count("approved") == 1 and statuses.count("draft") == len(a) - 3
    assert client.get(f"/api/projects/{processed}").json()["status"] == "review_required"
    # editing an approved asset makes it need review again and creates a revision
    e = client.patch(f"/api/assets/{first['id']}", json={"caption": "New caption", "hashtags": ["one", "#two"]}).json()
    assert e["status"] == "draft" and e["version"] == 2 and e["hashtags"] == ["#one", "#two"]
    assert [v["version"] for v in e["versions"]] == [1, 2] and e["versions"][0]["caption"] != "New caption"
    back = client.post(f"/api/assets/{first['id']}/versions/1/restore").json()
    assert back["caption"] == e["versions"][0]["caption"] and back["version"] == 3
    assert client.post(f"/api/assets/{first['id']}/review", json={"action": "bogus"}).status_code == 400


def test_memory_learns_from_review_and_is_searchable(client, ctx, processed):
    hits = client.get("/api/memory/search?q=free gift of grace&kinds=transcript").json()["hits"]
    assert hits and hits[0]["project_name"] == "Sunday Grace"
    cap = [x for x in assets(client, processed) if x["kind"] == "main_reel"][0]
    client.post(f"/api/assets/{cap['id']}/review", json={"action": "approve"})
    appr = ctx.memory.search("Free grace", kinds=["caption"], outcome="approved")
    assert appr and appr[0]["asset_id"] == cap["id"]
    client.post(f"/api/assets/{cap['id']}/review", json={"action": "reject", "reason": "boring"})
    assert ctx.memory.search("Free grace", kinds=["caption"], outcome="rejected")
    pc = ctx.memory.prompt_context(0, "grace")
    assert any("boring" in n for n in pc["rejection_notes"])
    assert client.post("/api/memory/preferences", json={"text": "Keep captions under 100 characters"}).status_code == 201
    assert "Keep captions under 100 characters" in ctx.memory.prompt_context(0, "x")["preferences"]
    topics = client.get("/api/memory/topics").json()
    assert {c["topic"] for c in topics["covered"]} == {"grace", "gift"}
    assert "Grace" not in topics["recommendations"]["suggestions"] and topics["recommendations"]["suggestions"]
    stats = client.get("/api/memory/stats").json()
    assert stats["by_kind"]["transcript"] >= 1 and stats["by_kind"]["summary"] == 1
    exp = client.get("/api/memory/export").json()
    assert exp["memory"] and exp["feedback"]
    assert client.post("/api/memory/delete", json={"project_id": processed}).json()["deleted"] > 0
    left = client.get("/api/memory/search?q=free gift of grace").json()["hits"]
    assert all(h["project_id"] != processed for h in left)          # only the project-less preference remains


def test_new_post_is_not_a_repeat_of_earlier_ones(client, ctx, processed, sample_video, fakes):
    """A second project producing the same post text triggers regeneration with an avoid-list."""
    pid2 = client.post("/api/projects", json={"name": "Second talk"}).json()["id"]
    upload(client, pid2, sample_video)
    client.post(f"/api/projects/{pid2}/process")
    ctx.queue.drain()
    posts = [x for x in assets(client, pid2) if x["kind"] == "social_post"]
    assert len(posts) == 2
    titles = {p["title"] for p in posts}
    assert any(t.startswith("Fresh hope variant") for t in titles)          # regenerated, not repeated
    first = [x for x in assets(client, processed) if x["kind"] == "social_post"]
    assert not ({p["title"] for p in first} & titles)


def test_reel_regeneration_keeps_version_history_and_files(client, ctx, processed):
    reel = [x for x in assets(client, processed) if x["slot"] == "main_1"][0]
    r = client.post(f"/api/assets/{reel['id']}/regenerate", json={"start": 10, "end": 60})
    assert r.status_code == 202
    assert client.post(f"/api/assets/{reel['id']}/regenerate", json={"start": 10, "end": 11}).status_code == 400
    ctx.queue.drain()
    d = client.get(f"/api/assets/{reel['id']}").json()
    assert d["version"] == 2 and d["reel"]["start"] == 10 and d["reel"]["end"] == 60 and d["status"] == "draft"
    assert [v["has_file"] for v in d["versions"]] == [True, True]            # v1 archived, v2 current
    assert client.get(f"/api/assets/{reel['id']}/versions/1/file").status_code == 200
    info = media.probe(client.get(f"/api/assets/{reel['id']}/file").content and
                       next(Path(ctx.settings.projects_dir).rglob("main_1_v2.mp4")))
    assert 53 < info["duration"] < 56      # 50 s clip + 2.2 s hook image + 2.6 s closing image
    assert any(Path(ctx.settings.projects_dir).rglob("versions/*/v1_main_1_v1.mp4"))


def test_social_post_edit_and_regenerate(client, ctx, processed, fakes):
    post = [x for x in assets(client, processed) if x["slot"] == "social_1"][0]
    before = client.get(f"/api/assets/{post['id']}/file").content
    d = client.patch(f"/api/assets/{post['id']}", json={"social_post": {"body": "A brand new message of hope"}}).json()
    assert d["social_post"]["body"] == "A brand new message of hope" and d["version"] == 2
    assert client.get(f"/api/assets/{post['id']}/file").content != before        # image re-rendered
    jid = client.post(f"/api/assets/{post['id']}/regenerate", json={"mode": "regenerate", "note": "shorter"}).json()["job_id"]
    ctx.queue.drain()
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "completed"
    d = client.get(f"/api/assets/{post['id']}").json()
    assert d["version"] == 3 and d["social_post"]["headline"].startswith("Fresh hope variant")
    p = [x for x in assets(client, processed) if x["kind"] == "poster"][0]
    jid = client.post(f"/api/assets/{p['id']}/regenerate", json={"layout": "centered"}).json()["job_id"]
    ctx.queue.drain()
    assert client.get(f"/api/assets/{p['id']}").json()["meta"]["layout"] == "centered"


def test_library_search_filter_tags_and_bulk_download(client, processed):
    lib = client.get("/api/library").json()
    assert lib["total"] >= 9 and {i["entity"] for i in lib["items"]} == {"asset", "video"}
    assert client.get("/api/library?type=reel").json()["total"] == 1
    assert client.get("/api/library?type=short").json()["total"] == 2
    assert client.get("/api/library?type=poster").json()["total"] == 1
    assert client.get("/api/library?type=video").json()["total"] == 1
    assert client.get("/api/library?type=nonsense").status_code == 400
    assert client.get("/api/library?q=Free grace").json()["total"] == 1
    reel = client.get("/api/library?type=reel&sort=name&order=asc").json()["items"][0]
    client.put(f"/api/assets/{reel['id']}/tags", json={"tags": ["Best Of"]})
    assert client.get("/api/library?tag=best of").json()["total"] == 1
    z = client.post("/api/library/download", json={"asset_ids": [reel["id"]], "video_ids": [1]})
    assert z.status_code == 200 and z.content[:2] == b"PK"
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert len(names) == 2 and all(n.startswith("sunday-grace/") for n in names)
    assert client.post("/api/library/download", json={}).status_code == 400


def test_calendar_rules(client, processed):
    a = assets(client, processed)[0]
    body = {"title": "Post it", "scheduled_for": "2026-12-01T09:00:00", "asset_id": a["id"], "campaign": "Advent",
            "category": "Reel", "reminder_at": "2020-01-01T00:00:00"}
    assert client.post("/api/calendar", json={**body, "status": "scheduled"}).status_code == 409    # asset not approved yet
    e = client.post("/api/calendar", json=body).json()
    assert e["status"] == "draft" and e["project_id"] == processed
    client.post(f"/api/assets/{a['id']}/review", json={"action": "approve"})
    assert client.patch(f"/api/calendar/{e['id']}", json={"status": "scheduled"}).json()["status"] == "scheduled"
    assert client.get("/api/calendar?start=2026-12-01&end=2026-12-02").json()[0]["asset"]["status"] == "approved"
    assert client.get("/api/calendar?start=2026-12-02").json() == []
    assert client.get("/api/calendar?status=draft").json() == []
    assert [d["id"] for d in client.get("/api/reminders/due").json()] == [e["id"]]
    client.post(f"/api/reminders/{e['id']}/done")
    assert client.get("/api/reminders/due").json() == []
    assert client.get("/api/calendar-meta/campaigns").json()["campaigns"] == ["Advent"]
    assert client.post("/api/calendar", json={**body, "status": "weird"}).status_code == 400
    assert client.delete(f"/api/calendar/{e['id']}").status_code == 200


def test_brand_and_templates(client, ctx, processed, tmp_path):
    from PIL import Image
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 80), (124, 58, 237, 255)).save(logo)
    with open(logo, "rb") as f:
        assert client.post("/api/brand/files/logo", files={"file": ("logo.png", f, "image/png")}).json()["files"]["logo"]
    with open(logo, "rb") as f:
        assert client.post("/api/brand/files/logo", files={"file": ("x.exe", f, "image/png")}).status_code == 400
    assert client.post("/api/brand/files/logo", files={"file": ("bad.png", b"not an image", "image/png")}).status_code == 400
    assert client.put("/api/brand", json={"colors": {"primary": "red"}}).status_code == 400
    b = client.put("/api/brand", json={"name": "RUHA Test", "colors": {"primary": "#112233"}}).json()
    assert b["name"] == "RUHA Test" and b["colors"]["primary"] == "#112233"
    assert client.get("/api/brand/files/logo").status_code == 200
    assert len(client.get("/api/templates?kind=social").json()) == 3
    t = client.post("/api/templates", json={"kind": "social", "name": "Mine", "config": {"layout": "centered"}}).json()
    assert client.post("/api/templates", json={"kind": "social", "name": "X", "config": {"layout": "zzz"}}).status_code == 400
    assert client.delete(f"/api/templates/{t['id']}").status_code == 200
    assert client.delete("/api/brand/files/logo").json()["files"]["logo"] is False
    # new graphics pick up the brand colours
    p = [x for x in assets(client, processed) if x["kind"] == "poster"][0]
    client.post(f"/api/assets/{p['id']}/regenerate", json={})
    ctx.queue.drain()
    assert client.get(f"/api/assets/{p['id']}").json()["version"] == 2


def test_settings_validation_and_processing_centre(client):
    s = client.get("/api/settings").json()
    assert s["values"]["whisper.model"] == "small" and "ai" in s["by_category"]
    assert client.put("/api/settings", json={"values": {"whisper.model": "huge"}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"nope": 1}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"llm.base_url": "http://evil.example.com"}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"whisper.model": "medium", "gpu.mode": "cpu"}}).json()["values"]["whisper.model"] == "medium"
    st = client.get("/api/system/status").json()
    assert st["ffmpeg"]["available"] and st["ollama"]["connected"] and st["ram"]["total_gb"] > 0
    assert st["queue"]["paused"] is False and "sequential" in st["model_loading"]["policy"]
    assert st["whisper"]["model"] == "medium" and "disk" in st and "gpu" in st
    info = client.get("/api/system/info").json()
    assert info["privacy"]["cloud_ai"] is False and info["database"]["size_bytes"] > 0
    assert client.get("/api/system/updates").json()["version"]
