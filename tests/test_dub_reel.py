"""Dub / translate ONE finished reel (asset_id) instead of the whole sermon."""
import pytest


def _reels(client, pid):
    return [a for a in client.get(f"/api/assets?project_id={pid}&kind=main_reel,short_reel").json() if a["has_file"]]


def _file(ctx, aid):
    from ruha.models import GeneratedAsset
    with ctx.db.session() as s:
        return s.get(GeneratedAsset, aid).file_path


def _source(ctx, pid):
    from ruha.models import SourceVideo
    with ctx.db.session() as s:
        return s.query(SourceVideo).filter_by(project_id=pid, is_current=True).one().path


def _job(client, pid, job_type="dubbing", **params):
    return client.post(f"/api/projects/{pid}/jobs", json={"job_type": job_type, "params": {"target": "Telugu", **params}})


def test_reel_is_the_file_that_gets_dubbed(client, ctx, processed, fakes):
    reel = _reels(client, processed)[0]
    r = _job(client, processed, voice="Male", asset_id=reel["id"])
    assert r.status_code == 202, r.text
    ctx.queue.drain()
    call = fakes.translator.calls[-1]
    assert call["video"] == _file(ctx, reel["id"]) != _source(ctx, processed)
    assert call["dub"] is True and call["target"] == "Telugu"
    out = client.get(f"/api/assets?project_id={processed}&kind=translation_video").json()
    assert out and reel["title"] in out[0]["title"] and out[0]["meta"]["source_asset_id"] == reel["id"]
    assert client.get(f"/api/jobs/{r.json()['job_id']}").json()["status"] == "completed"


def test_without_asset_id_the_whole_sermon_is_used(client, ctx, processed, fakes):
    assert _job(client, processed).status_code == 202
    ctx.queue.drain()
    assert fakes.translator.calls[-1]["video"] == _source(ctx, processed)


def test_subtitles_only_translation_of_a_reel(client, ctx, processed, fakes):
    reel = _reels(client, processed)[1]
    assert _job(client, processed, "translation", asset_id=reel["id"]).status_code == 202
    ctx.queue.drain()
    assert fakes.translator.calls[-1]["dub"] is False and fakes.translator.calls[-1]["video"] == _file(ctx, reel["id"])


@pytest.mark.parametrize("bad", [999999, "abc", 0])
def test_unknown_reel_is_rejected_up_front(client, processed, bad):
    assert _job(client, processed, asset_id=bad).status_code == 400


def test_only_reels_and_only_this_projects(client, ctx, processed, sample_video):
    poster = client.get(f"/api/assets?project_id={processed}&kind=poster").json()[0]
    r = _job(client, processed, asset_id=poster["id"])
    assert r.status_code == 400 and "reel or video" in r.json()["detail"]
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    from tests.conftest import upload
    assert upload(client, other, sample_video).status_code == 201
    r = _job(client, other, asset_id=_reels(client, processed)[0]["id"])
    assert r.status_code == 400 and "does not belong" in r.json()["detail"]


def test_reel_without_a_file_fails_the_job_cleanly(client, ctx, processed, fakes):
    reel = _reels(client, processed)[0]
    jid = ctx.queue.enqueue("dubbing", processed, {"target": "Telugu", "asset_id": reel["id"]})
    import os
    os.remove(_file(ctx, reel["id"]))
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and "no video file" in j["error"] and not fakes.translator.calls
    assert not j["params"].get("_auto_retries")                        # a missing file is permanent: no automatic retry
