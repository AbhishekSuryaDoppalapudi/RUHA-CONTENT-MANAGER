"""Typed reel times + comments and poster instructions."""
from pathlib import Path

import pytest

from ruha.models import Project
from ruha.services import instructions as I


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("tok,secs", [("750", 750), ("12:30", 750), ("1:02:10", 3730), ("0:05", 5), ("90.5", 90.5)])
def test_time_formats(tok, secs):
    assert I.parse_time(tok) == secs


def test_three_formats_label_and_note():
    txt = ("12:30-13:45 main - focus on faith, hook as a question\n"
           "1:02:10-1:02:40 short: the lost coin\n"
           "750-825\n")
    e, w = I.parse_reels(txt, duration=4000)
    assert [(x["start"], x["end"], x["category"]) for x in e] == [(750, 825, "main"), (3730, 3760, "short"), (750, 825, "main")]
    assert e[0]["note"] == "focus on faith, hook as a question" and e[1]["note"] == "the lost coin" and e[2]["note"] == ""
    assert any("overlap" in x for x in w)


def test_label_defaults_by_length_and_odd_length_only_warns():
    e, w = I.parse_reels("10-25\n100-200", duration=500)
    assert [x["category"] for x in e] == ["short", "main"]
    assert any("outside the usual" in x for x in w)                 # 100 s main reel: allowed, flagged


def test_every_bad_line_is_reported_at_once():
    bad = "hello\n5:00-4:00\n99:99-100:00\n0-3\n10-9000"
    with pytest.raises(I.InstructionError) as ei:
        I.parse_reels(bad, duration=600)
    msg = str(ei.value)
    for n in (1, 2, 3, 4, 5):
        assert f"Line {n}:" in msg
    assert "only 10:00 long" in msg


def test_limits():
    with pytest.raises(I.InstructionError):
        I.parse_reels("\n".join(f"{i * 10}-{i * 10 + 9}" for i in range(14)), duration=999)           # 1 main + 12 short is the most
    assert I.parse_reels("", 100) == ([], []) and I.parse_reels("  \n ", 100) == ([], [])


def test_word_main_inside_a_comment_is_not_a_label():
    e, _ = I.parse_reels("10-90 maintain the faith", duration=100)
    assert e[0]["category"] == "main" and e[0]["note"] == "maintain the faith"      # length-based, comment kept whole
    e, _ = I.parse_reels("10-20 maintain the faith", duration=100)
    assert e[0]["category"] == "short" and e[0]["note"] == "maintain the faith"


# ------------------------------------------------------------------ API
def test_instructions_api_validates_saves_and_reloads(client, project_with_video):
    pid = project_with_video
    assert client.get(f"/api/projects/{pid}/instructions").json()["reels"] == ""
    r = client.put(f"/api/projects/{pid}/instructions", json={"reels": "bogus", "posters": "x"})
    assert r.status_code == 400 and "Line 1" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/instructions").json()["posters"] == ""          # nothing saved on error
    r = client.put(f"/api/projects/{pid}/instructions", json={"reels": "10-50 main - faith", "posters": "  warm  "})
    assert r.status_code == 200 and r.json()["parsed"][0]["note"] == "faith"
    d = client.get(f"/api/projects/{pid}/instructions").json()
    assert d["reels"] == "10-50 main - faith" and d["posters"] == "warm" and d["video_seconds"] == 200
    assert client.put(f"/api/projects/{pid}/instructions", json={"reels": "10-500"}).status_code == 400   # beyond the 200 s video
    assert client.get("/api/projects/9999/instructions").status_code == 404


# ------------------------------------------------------------------ pipeline
def _hook_calls(fakes):
    return [c for c in fakes.llm.calls if c.startswith("You write the opening hook")]


def test_typed_reels_use_exact_times_and_ai_writes_the_text(client, ctx, project_with_video, fakes):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reels.count": 2}})          # 1 typed + 1 filled by the AI
    client.put(f"/api/projects/{pid}/instructions",
               json={"reels": "0:10-0:55 main - hook as a question\n2:00-2:20 short", "posters": ""})
    assert client.post(f"/api/projects/{pid}/process").status_code == 202
    ctx.queue.drain()
    mains = client.get(f"/api/assets?project_id={pid}&kind=main_reel").json()
    shorts = client.get(f"/api/assets?project_id={pid}&kind=short_reel").json()
    span = lambda r: (round(r["reel"]["start"], 1), round(r["reel"]["end"], 1))
    assert len(mains) == 1 and span(mains[0]) == (10.0, 55.0)                 # exactly as typed, never snapped
    assert mains[0]["title"] == "Is grace the free gift?"                     # AI hook from the clip's words, guided by "question"
    typed = [r for r in shorts if span(r) == (120.0, 140.0)]
    assert len(typed) == 1 and typed[0]["title"] == "Grace is the free gift"
    assert len(shorts) == 2                                                   # AI filled the remaining short slot
    ai_short = [r for r in shorts if r is not typed[0]][0]
    assert not (ai_short["reel"]["start"] < 140 and ai_short["reel"]["end"] > 120)       # no overlap with typed reel
    assert len(_hook_calls(fakes)) == 2
    # rendering again reuses the saved text: no new LLM calls for the typed reels
    client.post(f"/api/projects/{pid}/jobs", json={"job_type": "reel_generation", "params": {"force": True}})
    ctx.queue.drain()
    assert len(_hook_calls(fakes)) == 2


def test_invalid_saved_times_fail_the_job_with_a_clear_message(client, ctx, processed, fakes):
    pid = processed
    with ctx.db.session() as s:
        ws = Path(s.get(Project, pid).workspace_dir)
    (ws / "instructions.json").write_text('{"reels": "10-5000", "posters": "", "generated": {}}', encoding="utf-8")
    jid = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "reel_generation", "params": {"force": True}}).json()["job_id"]
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and "reels instructions are not valid" in j["error"]


def test_poster_follows_instructions(client, ctx, project_with_video, fakes):
    pid = project_with_video
    client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "weakness becoming strength, hopeful"})
    assert client.post(f"/api/projects/{pid}/process").status_code == 202
    ctx.queue.drain()
    poster = client.get(f"/api/assets?project_id={pid}&kind=poster").json()[0]
    assert poster["title"] == "Weakness becomes strength"
    assert any(c.startswith("Write the text for ONE sermon poster") for c in fakes.llm.calls)


def test_empty_boxes_change_nothing(client, ctx, processed, fakes):
    mains = client.get(f"/api/assets?project_id={processed}&kind=main_reel").json()
    assert mains and not any(c.startswith(("You write the opening hook", "Write the text for ONE")) for c in fakes.llm.calls)


# ------------------------------------------------------------------ hook length
def test_tidy_hook_never_cuts_a_word_or_ends_on_a_connector():
    from ruha.services.planner import tidy_hook
    long = "Are we seeing clearly in our personal growth and spiritual direction today"
    h = tidy_hook(long)
    assert len(h.split()) <= 8 and len(h) <= 60 and long.startswith(h)
    assert h.split()[-1].lower() not in {"and", "in", "our", "the", "of"}
    assert tidy_hook('"Is faith enough?"') == "Is faith enough?"
    assert tidy_hook("") == ""


def test_long_hook_is_asked_for_again_once_then_trimmed():
    from ruha.services.planner import describe_reel

    class LLM:
        def __init__(self, answers):
            self.answers, self.calls = list(answers), 0

        def generate_json(self, prompt, cfg):
            self.calls += 1
            return {"hook": self.answers.pop(0), "caption": "c"}

    text = "We are seeing clearly today. Can you see clearly in your growth? Spiritual direction matters a lot."
    ok = LLM(["Are we seeing clearly in our personal growth and spiritual direction today", "Can you see clearly?"])
    d = describe_reel(ok, {}, text, "", {})
    assert d["hook"] == "Can you see clearly?" and ok.calls == 2
    stubborn = LLM(["Are we seeing clearly in our personal growth and spiritual direction today"] * 2)
    d = describe_reel(stubborn, {}, text, "", {})
    assert stubborn.calls == 2 and 3 <= len(d["hook"].split()) <= 8 and d["hook"] in text      # replaced by the clip's own words
    short = LLM(["Seeing clearly in your growth"])
    assert describe_reel(short, {}, text, "", {})["hook"] == "Seeing clearly in your growth" and short.calls == 1


def test_llm_failure_keeps_the_typed_reel_with_transcript_words():
    from ruha.services.planner import describe_reel

    class Down:
        def generate_json(self, prompt, cfg):
            raise RuntimeError("down")
    d = describe_reel(Down(), {}, "Have you seen any company because all of us know about companies", "", {}, log=lambda *a: None)
    assert d["hook"].startswith("Have you seen any company") and d["closing"] == "Follow for more"
