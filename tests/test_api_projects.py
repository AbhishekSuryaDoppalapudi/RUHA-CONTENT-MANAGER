"""API tests: projects, video upload/validation, file serving and path safety."""
import io
from pathlib import Path

from conftest import upload


def test_health_and_empty_dashboard(client):
    assert client.get("/api/health").json()["status"] == "ok"
    m = client.get("/api/dashboard").json()["metrics"]
    assert m["total_projects"] == 0 and m["reels_generated"] == 0 and m["failed_jobs"] == 0   # real, not demo, data


def test_project_crud_filters_and_sorting(client):
    a = client.post("/api/projects", json={"name": "Alpha", "description": "first", "content_type": "sermon",
                                           "tags": ["Faith", "faith", " hope "]}).json()
    b = client.post("/api/projects", json={"name": "Beta", "content_type": "interview"}).json()
    assert a["status"] == "draft" and a["tags"] == ["faith", "hope"]
    assert Path(client.app.state.ctx.settings.projects_dir / f"{a['id']:05d}-alpha").is_dir()
    assert client.post("/api/projects", json={"name": "  "}).status_code in (400, 422)

    assert [p["name"] for p in client.get("/api/projects?q=alp").json()["items"]] == ["Alpha"]
    assert [p["name"] for p in client.get("/api/projects?tag=hope").json()["items"]] == ["Alpha"]
    assert [p["name"] for p in client.get("/api/projects?content_type=interview").json()["items"]] == ["Beta"]
    assert [p["name"] for p in client.get("/api/projects?sort=name&order=asc").json()["items"]] == ["Alpha", "Beta"]
    assert client.get("/api/projects?created_from=2999-01-01").json()["total"] == 0
    assert client.get("/api/projects?created_from=bad").status_code == 400

    r = client.patch(f"/api/projects/{a['id']}", json={"name": "Alpha 2", "description": "x", "tags": ["new"]})
    assert r.json()["name"] == "Alpha 2" and r.json()["tags"] == ["new"]
    assert client.patch(f"/api/projects/{a['id']}", json={"status": "nonsense"}).status_code == 400
    assert client.get("/api/projects/9999").status_code == 404
    hist = client.get(f"/api/projects/{a['id']}/history").json()
    assert {h["action"] for h in hist} >= {"create", "update"}


def test_archive_unarchive_and_duplicate(client, project_with_video):
    pid = project_with_video
    assert client.post(f"/api/projects/{pid}/archive").json()["status"] == "archived"
    assert client.get("/api/projects").json()["total"] == 0                       # hidden by default
    assert client.get("/api/projects?status=archived").json()["total"] == 1
    assert client.post(f"/api/projects/{pid}/unarchive").json()["status"] == "uploaded"
    dup = client.post(f"/api/projects/{pid}/duplicate").json()
    assert dup["name"].endswith("(copy)") and dup["video"] and dup["id"] != pid
    assert Path(client.app.state.ctx.settings.projects_dir).exists()


def test_delete_requires_confirmation_and_removes_files(client, project_with_video):
    pid = project_with_video
    ws = next(client.app.state.ctx.settings.projects_dir.iterdir())
    assert ws.exists()
    assert client.delete(f"/api/projects/{pid}").status_code == 400
    assert ws.exists()
    assert client.delete(f"/api/projects/{pid}?confirm=true").status_code == 200
    assert not ws.exists() and client.get(f"/api/projects/{pid}").status_code == 404


def test_upload_extracts_metadata_and_thumbnail(client, project_with_video):
    p = client.get(f"/api/projects/{project_with_video}").json()
    v = p["video"]
    assert v["width"] == 640 and v["height"] == 360 and 199 < v["duration"] < 201
    assert v["audio_tracks"] == 1 and v["video_codec"] == "h264" and v["size_bytes"] > 0 and len(v["sha256"]) == 64
    assert p["status"] == "uploaded" and p["has_thumbnail"]
    assert client.get(f"/api/projects/{project_with_video}/thumbnail").headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/videos/{v['id']}/thumbnail").status_code == 200


def test_video_stream_supports_range(client, project_with_video):
    vid = client.get(f"/api/projects/{project_with_video}").json()["video"]["id"]
    r = client.get(f"/api/videos/{vid}/stream", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def test_upload_rejects_bad_files(client):
    pid = client.post("/api/projects", json={"name": "Bad"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/videos", files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert r.status_code == 400 and "Unsupported" in r.json()["detail"]
    r = client.post(f"/api/projects/{pid}/videos", files={"file": ("fake.mp4", io.BytesIO(b"not a video at all"), "video/mp4")})
    assert r.status_code == 400 and "Not a valid video" in r.json()["detail"]
    r = client.post(f"/api/projects/{pid}/videos", files={"file": ("empty.mp4", io.BytesIO(b""), "video/mp4")})
    assert r.status_code == 400
    assert list(client.app.state.ctx.settings.tmp_dir.iterdir()) == []             # no leaked temp files
    assert client.get(f"/api/projects/{pid}").json()["video"] is None


def test_upload_size_limit(client, sample_video):
    ctx = client.app.state.ctx
    ctx.settings.max_upload_bytes = 1000
    pid = client.post("/api/projects", json={"name": "Big"}).json()["id"]
    r = upload(client, pid, sample_video)
    assert r.status_code == 400 and "limit" in r.json()["detail"]


def test_malicious_filename_is_sanitised_and_stays_in_workspace(client, sample_video):
    pid = client.post("/api/projects", json={"name": "Trav"}).json()["id"]
    with open(sample_video, "rb") as f:
        r = client.post(f"/api/projects/{pid}/videos", files={"file": ("../../../evil.mp4", f, "video/mp4")})
    assert r.status_code == 201
    ctx = client.app.state.ctx
    files = [p for p in ctx.settings.data_dir.rglob("evil*.mp4")]
    assert len(files) == 1 and ctx.settings.projects_dir in files[0].parents
    assert not (ctx.settings.data_dir.parent / "evil.mp4").exists()


def test_second_upload_needs_replace_and_originals_are_never_overwritten(client, project_with_video, sample_video):
    pid = project_with_video
    first = client.get(f"/api/projects/{pid}").json()["video"]
    r = upload(client, pid, sample_video)
    assert r.status_code == 409
    r = upload(client, pid, sample_video, replace="true")
    assert r.status_code == 201
    p = client.get(f"/api/projects/{pid}").json()
    paths = sorted((Path(client.app.state.ctx.settings.projects_dir)).rglob("sample_sermon*.mp4"))
    assert len(paths) == 2                                            # old original kept, new one beside it
    assert p["video"]["id"] != first["id"]
    assert r.json()["duplicates"] == [] or isinstance(r.json()["duplicates"], list)


def test_duplicate_video_detected_across_projects(client, project_with_video, sample_video):
    pid2 = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    r = upload(client, pid2, sample_video)
    assert [d["project_id"] for d in r.json()["duplicates"]] == [project_with_video]


def test_delete_video_removes_file(client, project_with_video):
    v = client.get(f"/api/projects/{project_with_video}").json()["video"]
    assert client.delete(f"/api/videos/{v['id']}").status_code == 400
    assert client.delete(f"/api/videos/{v['id']}?confirm=true").status_code == 200
    assert not list(client.app.state.ctx.settings.projects_dir.rglob("sample_sermon*.mp4"))
    p = client.get(f"/api/projects/{project_with_video}").json()
    assert p["video"] is None and p["status"] == "draft"


def test_process_requires_video(client):
    pid = client.post("/api/projects", json={"name": "NoVid"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/process").status_code == 400
