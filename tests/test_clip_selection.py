"""Clip selection: N short reels (+1 main) of a configurable length, Message score 0-100 with a reason and a category,
twice as many candidates as needed with the best non-overlapping kept, and "Find a moment about ...".
Everything uses a fake LLM; sentence snapping and overlap repair must keep working."""
import json

import pytest

from ruha.services import planfallback, planner, selection
from ruha.services.planner import PlanError


# ------------------------------------------------------------------ helpers
def make_segs(seconds=1200, step=5, special=None):
    special = special or {}
    return [{"start": float(t), "end": float(t + step),
             "text": special.get(t, f"Segment {t // step}: grace is the free gift of God number {t // step} for every person")}
            for t in range(0, seconds, step)]


def sc(v):
    return {"opening": v, "idea": v, "quotable": v, "emotion": v, "standalone": v}      # score = 5 * v


def clip(start, length, v, hook="Grace is the free gift", **kw):
    return {"start": start, "end": start + length, "hook": hook, "reason": f"scored {v * 5}", "score": sc(v), **kw}


def plan_of(main, shorts, **kw):
    return {"title": "T", "main_theme": "grace", "summary": "s", "visual_theme": "grace", "themes": ["grace"],
            "main_reels": main, "short_reels": shorts,
            "poster": {"hook": "Grace"}, "encouragement_posts": [{"hook": "a hook"}, {"hook": "another hook"}], **kw}


def run(plan, segs=None, rs=None, topic="", duration=None):
    segs = segs or make_segs()
    full = " ".join(s["text"] for s in segs)
    return planner.normalize_plan(plan, segs, duration or segs[-1]["end"], full, rs=rs, topic=topic)


def no_overlap(clips):
    return all(selection.overlap((a["start"], a["end"]), (b["start"], b["end"])) <= 0.5
               for i, a in enumerate(clips) for b in clips[i + 1:])


MAINS = [clip(1000, 70, 18, "Main best"), clip(1100, 70, 10, "Main weaker")]


# ------------------------------------------------------------------ settings
def test_defaults_and_clamping():
    assert selection.reel_settings({}) == {"count": 6, "min_s": 30.0, "max_s": 60.0}
    assert selection.reel_settings({"reels.count": 99})["count"] == 12 and selection.reel_settings({"reels.count": 0})["count"] == 1
    r = selection.reel_settings({"reels.min_s": 50, "reels.max_s": 52})
    assert r["max_s"] - r["min_s"] >= 5                                  # a real range, whatever was stored
    assert selection.reel_settings({"reels.count": True, "reels.min_s": "x"})["count"] == 6


def test_settings_api_validation(client):
    put = lambda **v: client.put("/api/settings", json={"values": v}).status_code
    assert put(**{"reels.count": 4}) == 200 and put(**{"reels.count": 12}) == 200 and put(**{"reels.count": 1}) == 200
    for bad in (0, 13, "6", True, 2.5):
        assert put(**{"reels.count": bad}) == 400, bad
    assert put(**{"reels.min_s": 20, "reels.max_s": 40}) == 200
    assert put(**{"reels.min_s": 40, "reels.max_s": 42}) == 400               # needs a 5 s range
    assert put(**{"reels.min_s": 38}) == 400                                  # checked against the stored max (40)
    assert put(**{"reels.max_s": 400}) == 400 and put(**{"reels.min_s": 3}) == 400
    v = client.get("/api/settings").json()["values"]
    assert (v["reels.count"], v["reels.min_s"], v["reels.max_s"]) == (1, 20, 40)


def test_candidates_are_twice_the_need_but_fit_the_video():
    rs = selection.reel_settings({})
    assert selection.candidates_wanted(1200, rs) == 12
    assert selection.candidates_wanted(1200, selection.reel_settings({"reels.count": 3})) == 6
    assert selection.candidates_wanted(150, rs) == 10                          # a short talk cannot hold 12 clips


# ------------------------------------------------------------------ the score
def test_score_is_the_sum_of_five_parts():
    assert selection.parse_score({"score": {"opening": 18, "idea": 17, "quotable": 15, "emotion": 14, "standalone": 16}}) == \
        (80, {"opening": 18, "idea": 17, "quotable": 15, "emotion": 14, "standalone": 16})
    assert selection.parse_score({"score": sc(99)})[0] == 100                      # each part is capped at 20
    assert selection.parse_score({"score": sc(-5)})[0] == 0
    s, parts = selection.parse_score({"score": {"opening": 20, "idea": 10, "quotable": 10, "emotion": 10}})   # one part missing
    assert parts["standalone"] == 12 and s == 62                                   # filled with the average of the others
    assert selection.parse_score({"score": {"opening": 5}}) == (None, None)        # too little to trust


def test_plain_numbers_and_garbage():
    assert selection.parse_score({"score": 83}) == (83, None)
    assert selection.parse_score({"score": 8.5}) == (85, None)                    # 0-10 scale
    assert selection.parse_score({"score": 0.7}) == (70, None)                    # 0-1 scale
    assert selection.parse_score({"score": 250})[0] == 100
    for bad in ({}, {"score": "great"}, {"score": None}, {"score": True}, {"score": float("nan")}, {"score": [1]}):
        assert selection.parse_score(bad) == (None, None), bad


def test_estimate_prefers_strong_clips_and_stays_in_range():
    strong = "Do you know God loves you? John 3:16 says it. You are not alone, and His grace is free. Hope is here!"
    weak = "and then we went to the store and they said that it was fine and so on and so forth for a while"
    s1, p1 = selection.estimate_score(strong)
    s2, p2 = selection.estimate_score(weak)
    assert 0 <= s2 < s1 <= 100 and set(p1) == set(selection.CRITERIA)
    assert all(0 <= v <= 20 for v in p1.values()) and sum(p1.values()) == s1
    assert selection.estimate_score("")[0] >= 0


def test_categories_must_be_supported_by_the_words():
    assert selection.clean_category("Scripture", "Romans 8:28 says all things work together") == "Scripture"
    assert selection.clean_category("Scripture", "we talked about the weather and our feelings") == "Teaching"
    assert selection.clean_category("prayer", "Let us pray. Heavenly Father, we thank you. Amen.") == "Prayer"
    assert selection.clean_category("Prayer", "we talked about the weather") == "Teaching"
    assert selection.clean_category("story", "x") == "Story" and selection.clean_category("ENCOURAGEMENT", "x") == "Encouragement"
    assert selection.clean_category("???", "Once there was a man who lost his way") == "Story"
    assert selection.clean_category(None, "It is written: man shall not live by bread alone") == "Scripture"
    assert selection.clean_category(None, "Don't give up, you are not alone") == "Encouragement"
    assert all(selection.clean_category(c, "x") in selection.CATEGORIES for c in (None, "", 5, "Sermon", "Teaching"))


# ------------------------------------------------------------------ selecting
def test_best_non_overlapping_candidates_win():
    starts = [0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600, 660]
    values = [8, 17, 12, 19, 5, 15, 9, 18, 6, 14, 11, 16]             # 12 candidates, none overlapping
    shorts = [clip(s, 40, v, f"Short at {s}") for s, v in zip(starts, values)]
    out = run(plan_of(MAINS, shorts))
    got = [(c["start"], c["score"]) for c in out["short_reels"]]
    top6 = sorted(zip(starts, [v * 5 for v in values]), key=lambda x: -x[1])[:6]
    assert got == top6                                                  # exactly the six best, best first
    assert [c["score"] for c in out["short_reels"]] == sorted((c["score"] for c in out["short_reels"]), reverse=True)
    assert len(out["main_reels"]) == 1 and out["main_reels"][0]["score"] == 90 and out["main_reels"][0]["start"] == 1000
    assert no_overlap(out["main_reels"] + out["short_reels"])


def test_a_lower_scored_overlapping_candidate_is_dropped():
    shorts = [clip(100, 40, 14, "Lower"), clip(110, 40, 19, "Higher overlapping"), clip(300, 40, 10, "Separate")]
    out = run(plan_of(MAINS, shorts), rs=selection.reel_settings({"reels.count": 2}))
    starts = [c["start"] for c in out["short_reels"]]
    assert starts == [110, 300]                                          # 110 (95) beat 100 (70); 300 (50) fills the second slot
    assert no_overlap(out["short_reels"])


def test_length_range_comes_from_the_settings_and_clips_snap_to_the_transcript():
    rs = selection.reel_settings({"reels.count": 3, "reels.min_s": 20, "reels.max_s": 40})
    shorts = [clip(103, 12, 15), clip(203, 120, 14), clip(400, 30, 13)]          # one too short, one far too long
    out = run(plan_of(MAINS, shorts), rs=rs)
    starts, ends = {s["start"] for s in make_segs()}, {s["end"] for s in make_segs()}
    for c in out["short_reels"]:
        assert 20 <= c["end"] - c["start"] <= 40, c
        assert c["start"] in starts and c["end"] in ends                          # sentence snapping is kept
    assert 55 <= out["main_reels"][0]["end"] - out["main_reels"][0]["start"] <= 100


def test_missing_candidates_are_filled_from_free_stretches():
    out = run(plan_of(MAINS, [clip(100, 40, 15)]))
    shorts = out["short_reels"]
    assert len(shorts) == 6 and no_overlap(out["main_reels"] + shorts)
    assert shorts[0]["score_source"] == "ai" and shorts[0]["start"] == 100 and shorts[0]["score"] == 75
    rest = shorts[1:]
    assert all(c["score_source"] == "estimated" and c["reason"].startswith("Selected automatically") for c in rest)
    assert all(0 <= c["score"] <= 100 for c in shorts)


def test_no_main_candidates_still_gives_a_main_reel():
    out = run(plan_of([], [clip(100, 40, 15)]))
    assert len(out["main_reels"]) == 1 and out["main_reels"][0]["reason"].startswith("Selected automatically")
    assert 55 <= out["main_reels"][0]["end"] - out["main_reels"][0]["start"] <= 100


def test_a_short_video_returns_fewer_reels_and_says_so():
    segs = make_segs(150)
    shorts = [clip(10, 40, 15), clip(60, 40, 14), clip(100, 40, 12)]
    out = run(plan_of([clip(0, 60, 15)], shorts), segs=segs)
    assert 1 <= len(out["short_reels"]) < 6 and no_overlap(out["main_reels"] + out["short_reels"])
    assert any(n.startswith(f"Only {len(out['short_reels'])} of the 6 short reels") for n in out["notes"])
    assert out["reel_settings"] == {"count": 6, "min_s": 30.0, "max_s": 60.0} and out["selection_v"] == selection.SELECTION_V


def test_unscored_candidates_get_a_flagged_estimate():
    out = run(plan_of(MAINS, [{"start": 100, "end": 140, "hook": "No score given", "reason": ""}, clip(300, 40, 12)]),
              rs=selection.reel_settings({"reels.count": 2}))
    by_start = {c["start"]: c for c in out["short_reels"]}
    est = by_start[100]
    assert est["score_source"] == "estimated" and 0 <= est["score"] <= 100 and est["score_parts"]
    assert "Estimated" in est["reason"] and by_start[300]["score_source"] == "ai"


def test_a_plain_number_score_is_accepted():
    out = run(plan_of(MAINS, [{"start": 100, "end": 140, "hook": "Plain", "score": 83, "reason": "good"}]),
              rs=selection.reel_settings({"reels.count": 1}))
    c = out["short_reels"][0]
    assert c["score"] == 83 and c["score_parts"] is None and c["score_source"] == "ai"


def test_every_clip_carries_score_reason_and_category():
    out = run(plan_of(MAINS, [clip(100, 40, 15, category="Story"), clip(300, 40, 12, category="nonsense")]),
              rs=selection.reel_settings({"reels.count": 2}))
    for c in out["main_reels"] + out["short_reels"]:
        assert isinstance(c["score"], int) and 0 <= c["score"] <= 100
        assert c["reason"] and c["category"] in selection.CATEGORIES
        assert 3 <= len(c["hook"].split()) <= 8                                  # the hook rule still applies
    assert out["short_reels"][0]["category"] == "Story"


def test_scripture_category_needs_scripture_in_the_clip():
    segs = make_segs(special={t: "As John 3:16 says, God so loved the world that he gave his only Son" for t in range(100, 140, 5)})
    out = run(plan_of(MAINS, [clip(100, 40, 15, category="Scripture"), clip(300, 40, 14, category="Scripture")]), segs=segs,
              rs=selection.reel_settings({"reels.count": 2}))
    cats = {c["start"]: c["category"] for c in out["short_reels"]}
    assert cats[100] == "Scripture" and cats[300] == "Teaching"                  # the second claim has no scripture in it


def test_invalid_candidates_are_skipped_but_all_invalid_is_an_error():
    out = run(plan_of(MAINS, [{"start": None, "end": "x", "hook": "bad"}, clip(100, 40, 15)]),
              rs=selection.reel_settings({"reels.count": 1}))
    assert [c["start"] for c in out["short_reels"]] == [100]
    with pytest.raises(PlanError, match="usable timestamps"):
        run(plan_of(MAINS, [{"start": None, "end": "x"}, {"start": "a", "end": "b"}]))


def test_main_reel_never_overlaps_a_short_and_overlap_is_repaired():
    # the best short sits inside the main reel's stretch: the main is picked first, the short is trimmed/moved clear
    out = run(plan_of([clip(100, 70, 18)], [clip(110, 40, 19, "Inside the main"), clip(400, 40, 10)]),
              rs=selection.reel_settings({"reels.count": 2}))
    assert no_overlap(out["main_reels"] + out["short_reels"]) and len(out["short_reels"]) == 2


# ------------------------------------------------------------------ "Find a moment about ..."
def topic_segs():
    return make_segs(special={t: "Forgiveness sets you free when you forgive the one who hurt you" for t in range(200, 240, 5)})


def test_topic_lifts_a_matching_clip_over_a_higher_scored_one():
    shorts = [clip(100, 40, 17, "Higher score, other subject"), clip(200, 40, 12, "About forgiveness")]
    one = selection.reel_settings({"reels.count": 1})
    assert run(plan_of(MAINS, shorts), segs=topic_segs(), rs=one)["short_reels"][0]["start"] == 100          # no topic: best score
    got = run(plan_of(MAINS, shorts), segs=topic_segs(), rs=one, topic="forgiveness")
    assert got["short_reels"][0]["start"] == 200 and got["short_reels"][0]["topic_hits"] > 0 and got["topic"] == "forgiveness"
    assert got["notes"] == []


def test_a_topic_claim_without_the_words_is_not_trusted():
    shorts = [clip(100, 40, 17, "Claims the topic", topic_match=100), clip(200, 40, 12, "Really about it")]
    got = run(plan_of(MAINS, shorts), segs=topic_segs(), rs=selection.reel_settings({"reels.count": 1}), topic="forgiveness")
    assert got["short_reels"][0]["start"] == 200


def test_topic_not_in_the_talk_is_reported_not_faked():
    got = run(plan_of(MAINS, [clip(100, 40, 15)]), segs=topic_segs(), rs=selection.reel_settings({"reels.count": 1}), topic="baptism")
    assert any('No clear moment about "baptism"' in n for n in got["notes"])
    assert got["short_reels"][0]["topic_hits"] == 0 and got["short_reels"][0]["start"] == 100


def test_topic_cleaning():
    assert selection.clean_topic("  forgiveness \n  and mercy ") == "forgiveness and mercy" and selection.clean_topic(None) == ""
    assert len(selection.clean_topic("x" * 500)) == 200
    assert selection.topic_hits("forgiveness", "she forgave him") == 0.0 or selection.topic_hits("forgiveness", "forgiven and free") > 0
    assert selection.topic_hits("", "anything") == 0.0


# ------------------------------------------------------------------ what the LLM is asked
class Capture:
    """A fake LLM that records prompts and returns a fixed plan (or map moments for the long-transcript path)."""
    def __init__(self, plan):
        self.plan, self.prompts = plan, []

    def generate_json(self, prompt, cfg):
        self.prompts.append(prompt)
        if prompt.startswith("Analyse this PART"):
            return {"summary": "p", "themes": ["grace"], "quotes": [], "moments": [clip(100, 40, 15)]}
        return self.plan


def test_prompt_asks_for_twice_as_many_candidates_with_the_score_rubric():
    llm = Capture(plan_of(MAINS, [clip(100, 40, 15)]))
    plan = planner.create_plan(llm, {"reels.count": 6}, make_segs(), 1200, {})
    p = llm.prompts[0]
    assert "2 main_reels candidates" in p and "12 short_reels candidates (30-60 seconds each)" in p
    assert "MESSAGE SCORE" in p and all(c in p for c in selection.CATEGORIES)
    assert all(k in p for k in selection.CRITERIA) and "reason" in p and "THE PRODUCER WANTS" not in p
    assert plan["reel_settings"]["count"] == 6 and len(plan["short_reels"]) == 6
    llm = Capture(plan_of(MAINS, [clip(100, 40, 15)]))
    planner.create_plan(llm, {"reels.count": 3, "reels.min_s": 20, "reels.max_s": 45}, make_segs(), 1200, {})
    assert "6 short_reels candidates (20-45 seconds each)" in llm.prompts[0] and "(3 short reels and 1 main reel)" in llm.prompts[0]


def test_topic_goes_into_the_prompt_only_when_given():
    llm = Capture(plan_of(MAINS, [clip(200, 40, 15)]))
    plan = planner.create_plan(llm, {}, topic_segs(), 1200, {}, topic="forgiveness")
    assert 'THE PRODUCER WANTS A MOMENT ABOUT: "forgiveness"' in llm.prompts[0] and "topic_match" in llm.prompts[0]
    assert plan["topic"] == "forgiveness"


def test_long_transcripts_use_map_reduce_with_scores_and_topic():
    segs = make_segs(2400, special={t: "Forgiveness is a choice we make every single day of our lives " * 2 for t in range(0, 2400, 5) if t % 3 == 0})
    assert sum(len(s["text"]) for s in segs) > planner.SINGLE_PASS_CHARS
    llm = Capture(plan_of(MAINS, [clip(100, 40, 15)]))
    plan = planner.create_plan(llm, {"reels.count": 4}, segs, 2400, {}, topic="forgiveness")
    maps = [p for p in llm.prompts if p.startswith("Analyse this PART")]
    reds = [p for p in llm.prompts if not p.startswith("Analyse this PART")]
    assert len(maps) >= 2 and len(reds) == 1
    assert all("MESSAGE SCORE" in m and "forgiveness" in m and "score" in m for m in maps)
    assert "scored candidate moments" in reds[0] and "8 short_reels candidates" in reds[0]
    assert plan["selection_v"] == selection.SELECTION_V


# ------------------------------------------------------------------ the fallback plan (no model)
def test_fallback_plan_follows_the_settings_and_is_scored():
    segs = make_segs(1200, special={t: "Why does forgiveness matter? Because it sets us free!" for t in range(300, 345, 5)})
    rs = selection.reel_settings({"reels.count": 4, "reels.min_s": 20, "reels.max_s": 40})
    plan = planfallback.fallback_plan(segs, 1200, rs=rs, topic="forgiveness")
    assert plan["fallback"] and len(plan["main_reels"]) == 1 and len(plan["short_reels"]) == 4
    assert all(20 <= c["end"] - c["start"] <= 40 for c in plan["short_reels"])
    assert no_overlap(plan["main_reels"] + plan["short_reels"])
    for c in plan["main_reels"] + plan["short_reels"]:
        assert c["score_source"] == "estimated" and 0 <= c["score"] <= 100 and c["category"] in selection.CATEGORIES and c["reason"]
    assert any(c["topic_hits"] > 0 for c in plan["short_reels"] + plan["main_reels"])
    assert plan["reel_settings"] == rs and plan["topic"] == "forgiveness" and plan["selection_v"] == selection.SELECTION_V
    again = planner.normalize_plan(plan, segs, 1200, " ".join(s["text"] for s in segs), rs=rs, topic="forgiveness")
    assert len(again["short_reels"]) == 4                                         # same shape as an AI plan


def test_fallback_on_a_tiny_video_never_fails():
    segs = make_segs(60)
    plan = planfallback.fallback_plan(segs, 60)
    assert plan["main_reels"] and plan["short_reels"] and all(c["score"] is not None for c in plan["short_reels"])


# ------------------------------------------------------------------ through the real job pipeline
def reel_assets(client, pid, kind="short_reel"):
    return sorted((a for a in client.get(f"/api/assets?project_id={pid}&kind={kind}").json()), key=lambda a: a["slot"])


def test_pipeline_stores_score_reason_and_category_best_first(client, ctx, processed):
    shorts = reel_assets(client, processed)
    scores = [a["meta"]["message_score"] for a in shorts]
    assert scores == [84, 61, 52]                                                 # slot short_1 is the best clip
    assert len(shorts) == 3
    assert [a["meta"]["category"] for a in shorts] == ["Encouragement", "Story", "Teaching"]   # "Prayer" had no prayer in it
    assert shorts[0]["description"] == "complete idea" and shorts[0]["meta"]["score_parts"]["idea"] == 18
    assert shorts[0]["meta"]["score_source"] == "ai" and shorts[0]["meta"]["typed"] is False
    main = reel_assets(client, processed, "main_reel")
    assert len(main) == 1 and main[0]["meta"]["message_score"] == 80
    reel = client.get(f"/api/assets/{shorts[0]['id']}").json()
    assert reel["reel"]["reason"] == "complete idea"
    plan = client.get(f"/api/projects/{processed}/transcript").json()["plan"]
    assert plan["reel_settings"]["count"] == 6 and plan["selection_v"] == selection.SELECTION_V
    assert any("short reels you asked for fit" in n for n in plan["notes"])       # 200 s cannot hold six 30-60 s clips
    jobs = client.get(f"/api/jobs?project_id={processed}").json()["items"]
    logs = [l for j in jobs for l in client.get(f"/api/jobs/{j['id']}").json()["logs"]]       # the plan is made in the first job
    assert any(l["level"] == "WARNING" and "short reels you asked for fit" in l["message"] for l in logs)


def test_changing_the_settings_makes_a_new_plan(client, ctx, processed, fakes):
    n = len(fakes.llm.prompts)
    assert client.put("/api/settings", json={"values": {"reels.count": 2}}).status_code == 200
    client.post(f"/api/projects/{processed}/jobs", json={"job_type": "reel_generation", "params": {}})
    ctx.queue.drain()
    new = [p for p in fakes.llm.prompts[n:] if "MESSAGE SCORE" in p]
    assert new and "(2 short reels and 1 main reel)" in new[0]
    plan = client.get(f"/api/projects/{processed}/transcript").json()["plan"]
    assert plan["reel_settings"]["count"] == 2 and len(plan["short_reels"]) == 2
    n2 = len(fakes.llm.prompts)                                                    # same settings again: the plan is reused
    client.post(f"/api/projects/{processed}/jobs", json={"job_type": "reel_generation", "params": {}})
    ctx.queue.drain()
    assert not [p for p in fakes.llm.prompts[n2:] if "MESSAGE SCORE" in p]


def test_old_plans_without_scores_are_redone(client, ctx, processed, fakes):
    from ruha.models import Transcript
    with ctx.db.session() as s:
        t = s.query(Transcript).filter_by(project_id=processed).one()
        plan = dict(t.plan)
        plan.pop("selection_v")                                                    # what a plan from the old version looks like
        t.plan_json = json.dumps(plan)
    n = len(fakes.llm.prompts)
    client.post(f"/api/projects/{processed}/jobs", json={"job_type": "reel_generation", "params": {}})
    ctx.queue.drain()
    assert [p for p in fakes.llm.prompts[n:] if "MESSAGE SCORE" in p]


def test_topic_box_is_saved_and_used(client, ctx, project_with_video, fakes):
    pid = project_with_video
    r = client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "", "topic": "  forgiveness  "})
    assert r.status_code == 200 and r.json()["topic"] == "forgiveness"
    assert client.get(f"/api/projects/{pid}/instructions").json()["topic"] == "forgiveness"
    # saving the other boxes without a topic keeps it
    client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "a note"})
    assert client.get(f"/api/projects/{pid}/instructions").json()["topic"] == "forgiveness"
    client.post(f"/api/projects/{pid}/process", json={})
    ctx.queue.drain()
    with_topic = [p for p in fakes.llm.prompts if "MESSAGE SCORE" in p]
    assert with_topic and 'THE PRODUCER WANTS A MOMENT ABOUT: "forgiveness"' in with_topic[0]
    plan = client.get(f"/api/projects/{pid}/transcript").json()["plan"]
    assert plan["topic"] == "forgiveness" and any('No clear moment about "forgiveness"' in n for n in plan["notes"])
    assert reel_assets(client, pid)[0]["meta"]["topic"] == "forgiveness"
    client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "", "topic": ""})      # cleared again
    assert client.get(f"/api/projects/{pid}/instructions").json()["topic"] == ""


def test_typed_reels_count_against_the_reel_setting(client, ctx, project_with_video, fakes):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reels.count": 2}})
    typed = "0:00-1:10 main - intro\n1:15-1:50 short - one\n2:00-2:35 short - two"
    assert client.put(f"/api/projects/{pid}/instructions", json={"reels": typed, "posters": ""}).status_code == 200
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription"]})
    ctx.queue.drain()
    n = len(fakes.llm.prompts)                                                     # transcription made the plan
    client.post(f"/api/projects/{pid}/jobs", json={"job_type": "reel_generation", "params": {}})
    ctx.queue.drain()
    assert not [p for p in fakes.llm.prompts[n:] if "MESSAGE SCORE" in p]          # 1 main + 2 shorts typed: no AI plan
    shorts = reel_assets(client, pid)
    assert len(shorts) == 2 and all(a["meta"]["typed"] and a["meta"]["message_score"] is None for a in shorts)
