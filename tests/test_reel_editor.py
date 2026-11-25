"""Reel Editor: the word-level transcript, reels made of chosen segments, caption corrections, preview / approve / render,
and dubbing only for approved reels."""
import json
import subprocess

import pytest

from conftest import upload
from ruha.services import media, reel_editor as ed, transcripts


def duration_of(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


@pytest.fixture()
def sermon(client, ctx, project_with_video):
    """A sermon that is transcribed (fake Whisper) and nothing else."""
    r = client.post(f"/api/projects/{project_with_video}/process", json={"steps": ["transcription"]})
    assert r.status_code == 202, r.text
    ctx.queue.drain()
    return project_with_video


def make(client, pid, segments, **kw):
    r = client.post(f"/api/sermons/{pid}/reels", json={"title": "Test reel", "segments": segments, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def run_job(client, ctx, job_id):
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{job_id}").json()
    assert j["status"] == "completed", j
    return j


# ----------------------------------------------------------------- 1. transcript
def test_the_transcript_has_words_with_times_and_is_read_from_storage(client, sermon, fakes):
    t = client.get(f"/api/sermons/{sermon}/transcript")
    assert t.status_code == 200
    d = t.json()
    assert d["sentences"] and d["cached"] and d["duration"] == 200.0
    w = d["sentences"][0]["words"][0]
    assert {"id", "w", "start", "end", "confidence", "estimated"} <= set(w) and w["id"] == "0:0"
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(d["sentences"][0]["words"], d["sentences"][0]["words"][1:]))
    calls = fakes.transcriber.calls
    assert client.get(f"/api/sermons/{sermon}/transcript").json() == d      # a second request: same data...
    assert fakes.transcriber.calls == calls == 1                            # ...and nothing was transcribed again


def test_a_second_project_with_the_same_file_reuses_the_transcript(client, ctx, sermon, sample_video, fakes):
    other = client.post("/api/projects", json={"name": "Same sermon again"}).json()["id"]
    assert upload(client, other, sample_video).status_code == 201
    client.post(f"/api/projects/{other}/process", json={"steps": ["transcription"]})
    ctx.queue.drain()
    assert fakes.transcriber.calls == 1                                     # the same bytes: not transcribed twice
    assert client.get(f"/api/sermons/{other}/transcript").json()["sentences"]


def test_word_times_use_the_stored_ones_and_mark_estimates():
    spoken = {"idx": 3, "start": 0.0, "end": 2.0, "text": "Hello there.", "words": [
        {"w": " Hello", "s": 0.1, "e": 0.5, "p": 0.98}, {"w": " there.", "s": 0.6, "e": 1.0, "p": 0.4}]}
    guessed = {"idx": 4, "start": 2.0, "end": 4.0, "text": "No word times here", "words": None}
    ws = transcripts.words_of(spoken)
    assert [(w["id"], w["start"], w["confidence"], w["estimated"]) for w in ws] == [("3:0", 0.1, 0.98, False), ("3:1", 0.6, 0.4, False)]
    g = transcripts.words_of(guessed)
    assert len(g) == 4 and all(w["estimated"] for w in g) and g[0]["start"] == 2.0 and g[-1]["end"] == 4.0
    assert [s["text"] for s in transcripts.sentences_of([spoken, guessed])] == ["Hello there.", "No word times here"]


# ----------------------------------------------------------------- 2. reels = ordered segments
def test_a_reel_joins_non_adjacent_segments_in_the_order_given(client, ctx, sermon, monkeypatch):
    cuts = []
    real = media.render_reel

    def spy(source, start, end, out, **kw):
        cuts.append(round(start))
        return real(source, start, end, out, **kw)
    monkeypatch.setattr(media, "render_reel", spy)
    reel = make(client, sermon, [{"start": 100, "end": 110}, {"start": 20, "end": 30}], language="English")
    assert [s["start"] for s in reel["segments"]][0] > 90 and 18 < reel["total_seconds"] < 22     # order kept, length = the sum
    job = client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"]
    j = run_job(client, ctx, job)
    assert j["result"]["steps"] == ["cut", "finish"]
    assert cuts[:2] == [100, 20]                                               # 100-110 first, then 20-30
    assert abs(duration_of(j["result"]["file"]) - reel["total_seconds"]) < 1.0
    assert client.get(f"/api/editor/reels/{reel['id']}").json()["status"] == "ready"


def test_reels_can_be_duplicated_deleted_and_versioned(client, sermon):
    reel = make(client, sermon, [{"start": 10, "end": 20}])
    dup = client.post(f"/api/editor/reels/{reel['id']}/duplicate").json()
    assert dup["id"] != reel["id"] and dup["segments"] == reel["segments"] and dup["title"].endswith("(copy)")
    v1 = client.post(f"/api/editor/reels/{reel['id']}/versions", json={"note": "first cut"}).json()
    client.patch(f"/api/editor/reels/{reel['id']}", json={"segments": [{"start": 50, "end": 60}]})
    v2 = client.post(f"/api/editor/reels/{reel['id']}/versions", json={"note": "second"}).json()
    assert (v1["version"], v2["version"]) == (1, 2)
    back = client.post(f"/api/editor/reels/{reel['id']}/versions/1/restore").json()
    assert back["segments"] == reel["segments"] and back["version"] == 2
    assert [v["note"] for v in client.get(f"/api/editor/reels/{reel['id']}/versions").json()["items"]] == ["second", "first cut"]
    assert client.delete(f"/api/editor/reels/{dup['id']}").status_code == 200
    assert client.get(f"/api/editor/reels/{dup['id']}").status_code == 404
    assert len(client.get(f"/api/sermons/{sermon}/reels").json()["items"]) == 1


def test_a_reel_can_start_from_a_planner_suggestion_and_still_be_edited(client, ctx, project_with_video):
    client.post(f"/api/projects/{project_with_video}/process", json={"steps": ["transcription"]})
    ctx.queue.drain()
    sug = client.get(f"/api/sermons/{project_with_video}/suggested-reels").json()["suggestions"]
    assert sug and sug[0]["id"].startswith("main_")
    reel = client.post(f"/api/sermons/{project_with_video}/reels", json={"suggestion": sug[0]["id"]}).json()
    assert reel["source"] == f"plan:{sug[0]['id']}" and reel["title"] == sug[0]["hook"]
    edited = client.patch(f"/api/editor/reels/{reel['id']}", json={"segments": [{"start": 10, "end": 25}]}).json()
    assert 14 < edited["total_seconds"] < 16                                  # the AI's choice is only a starting point


def test_bad_segments_give_a_clear_error(client, sermon):
    for body, text in (([{"start": 10, "end": 5}], "end must be after"), ([{"start": 900, "end": 950}], "after the end"),
                       ([{"start": "a", "end": 5}], "start and an end"), ([{"start": 10, "end": 10.1}], "shorter")):
        r = client.post(f"/api/sermons/{sermon}/reels", json={"segments": body})
        assert r.status_code == 400 and text in r.json()["detail"], (body, r.text)


def test_cuts_land_in_the_pause_between_words():
    words = [{"id": "0:0", "w": "a", "start": 1.0, "end": 1.4}, {"id": "0:1", "w": "b", "start": 2.0, "end": 2.4},
             {"id": "0:2", "w": "c", "start": 3.0, "end": 3.4}]
    a, b = ed.snap(1.9, 2.5, words, 10.0)               # starts a little before "b" and ends just after it
    assert 1.4 < a <= 2.0 and 2.4 <= b < 3.0            # in the silences on both sides, never inside a word
    assert ed.snap(5.0, 6.0, [], 10.0) == (5.0, 6.0)    # no words: as given


def test_a_reel_over_sixty_seconds_warns_but_is_allowed(client, sermon):
    reel = make(client, sermon, [{"start": 0, "end": 70}])
    assert reel["total_seconds"] > 60 and reel["warnings"] and "60" in reel["warnings"][0]
    assert make(client, sermon, [{"start": 0, "end": 20}])["warnings"] == []


# ----------------------------------------------------------------- 3 + 4. caption text, options, deleting a segment
def test_deleting_a_segment_updates_the_total(client, sermon):
    reel = make(client, sermon, [{"start": 10, "end": 20}, {"start": 50, "end": 70}, {"start": 100, "end": 105}])
    before = reel["total_seconds"]
    after = client.patch(f"/api/editor/reels/{reel['id']}", json={"segments": [reel["segments"][0], reel["segments"][2]]}).json()
    assert len(after["segments"]) == 2 and 14 < after["total_seconds"] < 16 and after["total_seconds"] < before - 15


def test_options_are_saved_validated_and_survive_a_restart(client, ctx, sermon):
    reel = make(client, sermon, [{"start": 10, "end": 20}])
    assert reel["options"]["music"]["path"] == "" and reel["options"]["caption"]["template"] == "wordpop"    # music off by default
    r = client.patch(f"/api/editor/reels/{reel['id']}", json={"aspect": "1:1", "options": {
        "caption": {"template": "karaoke", "color": "#FFD166", "size": 90}, "hook": {"enabled": True, "text": "Hear this"},
        "lower_third": {"enabled": True, "name": "Pastor John", "role": "Senior Pastor"},
        "end_card": {"enabled": True, "text": "Follow for more"}, "music": {"volume": 0.4},
        "posts": {"instagram": "My post"}}})
    assert r.status_code == 200 and r.json()["aspect"] == "1:1"
    ctx.db.reopen()                                                            # as after a restart
    got = client.get(f"/api/editor/reels/{reel['id']}").json()
    assert got["options"]["caption"]["template"] == "karaoke" and got["options"]["hook"]["text"] == "Hear this"
    assert got["options"]["lower_third"]["name"] == "Pastor John" and got["options"]["music"]["volume"] == 0.4
    assert got["options"]["posts"]["instagram"] == "My post" and got["options"]["posts"]["x"] == ""
    for bad in ({"aspect": "4:3"}, {"options": {"caption": {"template": "nope"}}}, {"options": {"caption": {"color": "red"}}}):
        assert client.patch(f"/api/editor/reels/{reel['id']}", json=bad).status_code == 400, bad


def test_editing_caption_text_does_not_retranscribe_or_recut(client, ctx, sermon, fakes):
    reel = make(client, sermon, [{"start": 10, "end": 18}])
    first = run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"])
    assert first["result"]["steps"] == ["cut", "finish"]
    words = client.get(f"/api/sermons/{sermon}/transcript").json()["sentences"][2]["words"]      # a word inside 10-18 s
    edited = client.patch(f"/api/editor/reels/{reel['id']}", json={"caption_edits": {words[1]["id"]: "GRACE!"}}).json()
    assert edited["caption_edits"] == {words[1]["id"]: "GRACE!"} and edited["segments"] == reel["segments"]
    assert edited["status"] == "draft"                                         # changed since the preview
    second = run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"])
    assert second["result"]["steps"] == ["finish"]                             # the joined cut was reused: no re-cut
    assert fakes.transcriber.calls == 1                                        # and no new transcription
    third = client.patch(f"/api/editor/reels/{reel['id']}", json={"options": {"caption": {"template": "karaoke"}}}).json()
    assert third["segments"] == reel["segments"]
    assert run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"])["result"]["steps"] == ["finish"]
    client.patch(f"/api/editor/reels/{reel['id']}", json={"segments": [{"start": 10, "end": 20}]})       # a new segment: re-cut
    assert run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"])["result"]["steps"] == ["cut", "finish"]


def test_corrected_words_are_in_the_captions_and_the_timing_is_kept():
    stored = [{"idx": 0, "start": 0.0, "end": 4.0, "text": "grace is free", "words": [
        {"w": " grace", "s": 1.0, "e": 1.5}, {"w": " is", "s": 1.6, "e": 1.8}, {"w": " free", "s": 2.0, "e": 2.6}]}]
    reel = {"segments": [{"start": 0.5, "end": 3.0}], "caption_edits": {"0:2": "FREE!"}}
    v = ed.virtual_segments(reel, stored)
    assert [(w["w"], w["s"], w["e"]) for w in v[0]["words"]] == [("grace", 0.5, 1.0), ("is", 1.1, 1.3), ("FREE!", 1.5, 2.1)]
    assert ed.reel_text(reel, stored) == "grace is FREE!"


def test_the_joined_timeline_keeps_captions_in_sync_after_a_join():
    stored = [{"idx": 0, "start": 0.0, "end": 100.0, "text": "x", "words": [
        {"w": " first", "s": 10.0, "e": 10.5}, {"w": " second", "s": 50.0, "e": 50.5}]}]
    reel = {"segments": [{"start": 9.0, "end": 12.0}, {"start": 49.0, "end": 52.0}], "caption_edits": {}}
    words = [w for seg in ed.virtual_segments(reel, stored) for w in seg["words"]]
    assert [(w["w"], w["s"]) for w in words] == [("first", 1.0), ("second", 4.0)]   # 3 s of segment one, then 1 s into segment two


# ----------------------------------------------------------------- 5. review and approval; dubbing only when approved
def test_a_reel_must_be_previewed_before_it_can_be_approved(client, sermon):
    reel = make(client, sermon, [{"start": 10, "end": 20}])
    r = client.post(f"/api/editor/reels/{reel['id']}/approve")
    assert r.status_code == 409 and "preview" in r.json()["detail"].lower()


def test_dubbing_starts_only_for_an_approved_reel_and_uses_the_edited_cut(client, ctx, sermon, fakes):
    reel = make(client, sermon, [{"start": 10, "end": 20}, {"start": 60, "end": 70}])
    body = {"target": "Hindi", "dub": True}
    r = client.post(f"/api/editor/reels/{reel['id']}/translate", json=body)
    assert r.status_code == 409 and "Approved" in r.json()["detail"]
    run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"])
    assert client.get(f"/api/editor/reels/{reel['id']}").json()["status"] == "ready"
    assert client.post(f"/api/editor/reels/{reel['id']}/translate", json=body).status_code == 409      # ready is not approved
    ap = client.post(f"/api/editor/reels/{reel['id']}/approve")
    assert ap.status_code == 202 and ap.json()["status"] == "approved"
    fin = run_job(client, ctx, ap.json()["job_id"])
    assert fin["result"]["mode"] == "final"
    got = client.get(f"/api/editor/reels/{reel['id']}").json()
    assert got["status"] == "rendered" and got["asset_id"]
    assert abs(duration_of(fin["result"]["file"]) - got["total_seconds"]) < 1.0
    assert fakes.translator.calls == []                                        # nothing dubbed so far
    t = client.post(f"/api/editor/reels/{reel['id']}/translate", json=body)
    assert t.status_code == 202
    run_job(client, ctx, t.json()["job_id"])
    assert [c["target"] for c in fakes.translator.calls] == ["Hindi"] and fakes.translator.calls[0]["video"] == fin["result"]["file"]


def test_editing_an_approved_reel_sends_it_back_to_draft(client, ctx, sermon):
    reel = make(client, sermon, [{"start": 10, "end": 20}])
    run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"])
    run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/approve").json()["job_id"])
    assert client.get(f"/api/editor/reels/{reel['id']}").json()["status"] == "rendered"
    assert client.patch(f"/api/editor/reels/{reel['id']}", json={"title": "New title"}).json()["status"] == "draft"
    assert client.post(f"/api/editor/reels/{reel['id']}/translate", json={"target": "Hindi"}).status_code == 409


# ----------------------------------------------------------------- 4. the things a reel can carry
def test_a_reel_can_carry_a_hook_card_lower_third_end_card_and_music(client, ctx, sermon, tmp_path):
    music = tmp_path / "track.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3", str(music)], check=True, capture_output=True)
    reel = make(client, sermon, [{"start": 10, "end": 16}], aspect="16:9", options={
        "hook": {"enabled": True, "text": "Grace is free"}, "end_card": {"enabled": True, "text": "Follow for more"},
        "lower_third": {"enabled": True, "name": "Pastor John", "role": "Senior Pastor"},
        "music": {"path": str(music), "volume": 0.2}, "caption": {"template": "classic"}})
    j = run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"])
    assert duration_of(j["result"]["file"]) > 6 + 2                            # 6 s of speech plus the opening and closing cards
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", j["result"]["file"]],
                           capture_output=True, text=True).stdout.split()
    assert "video" in probe and "audio" in probe


def test_hook_ideas_thumbnail_and_post_drafts(client, ctx, sermon):
    reel = make(client, sermon, [{"start": 5, "end": 25}])
    ideas = client.get(f"/api/editor/reels/{reel['id']}/hook-suggestions").json()["suggestions"]
    assert ideas and all(3 <= len(i.split()) <= 8 for i in ideas)
    assert client.post(f"/api/editor/reels/{reel['id']}/thumbnail", json={"time": 1, "text": "Grace"}).status_code == 409   # needs a preview
    run_job(client, ctx, client.post(f"/api/editor/reels/{reel['id']}/preview").json()["job_id"])
    t = client.post(f"/api/editor/reels/{reel['id']}/thumbnail", json={"time": 2.0, "text": "Grace is free"})
    assert t.status_code == 200
    img = client.get(f"/api/editor/reels/{reel['id']}/file/thumbnail")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert client.get(f"/api/editor/reels/{reel['id']}/file/preview").status_code == 200
    drafts = client.post(f"/api/editor/reels/{reel['id']}/posts/draft").json()
    assert set(drafts) == {"instagram", "facebook", "youtube", "x"} and all(drafts.values()) and len(drafts["x"]) <= 280
