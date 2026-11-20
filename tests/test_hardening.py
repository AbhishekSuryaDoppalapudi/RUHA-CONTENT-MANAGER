"""Hooks (3-8 words, own words), planner fallback, automatic retries, RAM floor, status cache, shared keyword rule,
typed-reel planner skip, translator move and project-folder cleanup."""
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import ROOT, make_segments
from ruha.ai.llm import LLMError, LLMUnavailable, OllamaLLM
from ruha.config import DEFAULT_CONFIG
from ruha.jobs import is_temporary
from ruha.models import Project, Transcript
from ruha.services import hooks, keywords, planfallback, planner
from ruha.services import projects as project_svc
from ruha.services import system as sysinfo

CLIP = ("So many people are tired. Have you ever hit a peak and stayed there? "
        "Faith is not a feeling, it is a decision that you keep making every single day.")


def put_settings(client, **values):
    return client.put("/api/settings", json={"values": values})


def job_logs(client, jid):
    return [l["message"] for l in client.get(f"/api/jobs/{jid}").json()["logs"]]


# ============================================================ 1. hooks
def test_hook_is_a_3_to_8_word_phrase_from_the_clip():
    h = hooks.ensure_hook("Have you ever hit a peak and stayed there?", CLIP)       # 9 words: cut to whole words
    assert 3 <= len(h.split()) <= 8 and CLIP.startswith("So many people are tired. " + h) and len(h) <= hooks.MAX_CHARS


def test_hook_rules_in_detail():
    assert hooks.ensure_hook("Faith is a decision", CLIP) == "Faith is a decision"          # grounded, 4 words: kept
    one = hooks.ensure_hook("Faith", CLIP)                                                 # one word: replaced from the clip
    assert 3 <= len(one.split()) <= 8 and one in CLIP
    made_up = hooks.ensure_hook("Unlock your destiny today friend", CLIP)                  # not from the clip: replaced
    assert made_up in CLIP and 3 <= len(made_up.split()) <= 8
    long = hooks.ensure_hook("Faith is not a feeling it is a decision that you keep making every day", CLIP)
    assert 3 <= len(long.split()) <= 8
    assert hooks.ensure_hook("", "") == ""                                                 # nothing to work with


def test_questions_are_preferred_and_filler_openers_dropped():
    assert hooks.extract_hook("Well, so, this is a sentence about nothing much at all. Do you trust Him today?") == \
        "Do you trust Him today?"
    assert not hooks.extract_hook("So and but now okay yeah then.")                         # too short to be a hook


def test_one_word_is_an_opt_in_setting_off_by_default(client):
    assert DEFAULT_CONFIG["hook.one_word"]["value"] is False
    assert client.get("/api/settings").json()["values"]["hook.one_word"] is False
    assert len(hooks.ensure_hook("Have you ever hit a peak", CLIP, one_word=True, theme="faith").split()) == 1
    assert len(hooks.ensure_hook("Faith is a decision", CLIP, one_word=True).split()) == 1
    assert put_settings(client, **{"hook.one_word": True}).status_code == 200
    assert put_settings(client, **{"hook.one_word": "yes"}).status_code == 400


def test_planner_no_longer_forces_one_word_hooks():
    plan = {"title": "T", "main_theme": "m", "summary": "s", "visual_theme": "grace", "themes": ["grace"],
            "main_reels": [{"start": 1, "end": 46, "hook": "Grace", "closing": "x"}],
            "short_reels": [{"start": 162, "end": 182, "hook": "Hi"}, {"start": 183, "end": 200, "hook": "Grace is the free gift"}],
            "poster": {"hook": "H"}, "encouragement_posts": [{"hook": "a b c", "caption": "c"}, {"hook": "d e f", "caption": "c"}]}
    out = planner.normalize_plan(plan, make_segments(), 200, " ".join(s["text"] for s in make_segments()))
    for r in out["main_reels"] + out["short_reels"]:
        assert 3 <= len(r["hook"].split()) <= 8, r["hook"]
    one = planner.normalize_plan(plan, make_segments(), 200, " ".join(s["text"] for s in make_segments()), one_word=True)
    assert all(len(r["hook"].split()) == 1 for r in one["main_reels"] + one["short_reels"])
    assert "ONE bold word" not in planner.PLAN_SCHEMA and "do NOT use a single word" in planner._rules(100)


def test_reels_get_phrase_hooks_and_one_word_only_when_enabled(client, ctx, processed):
    pid = processed
    titles = [a["title"] for a in client.get(f"/api/assets?project_id={pid}&kind=short_reel").json()] + \
        [a["title"] for a in client.get(f"/api/assets?project_id={pid}&kind=main_reel").json()]
    assert titles and all(3 <= len(t.split()) <= 8 for t in titles), titles          # "Short one" (2 words) was upgraded
    put_settings(client, **{"hook.one_word": True})
    client.post(f"/api/projects/{pid}/jobs", json={"job_type": "reel_generation", "params": {"force": True}})
    ctx.queue.drain()
    titles = [a["title"] for a in client.get(f"/api/assets?project_id={pid}&kind=short_reel").json()]
    assert titles and all(len(t.split()) == 1 for t in titles), titles


# ============================================================ 2. planner failures
def test_raw_output_is_kept_on_bad_json_and_logged_with_the_failure(monkeypatch):
    llm = OllamaLLM()
    monkeypatch.setattr(llm, "generate", lambda prompt, cfg, as_json=True, timeout=900: "Sure! here you go: not json at all")
    with pytest.raises(LLMError) as ei:
        llm.generate_json("p", {})
    assert ei.value.raw == "Sure! here you go: not json at all"
    assert "not json" in planner.raw_preview(None, ei.value)
    assert planner.raw_preview({"a": 1}) == '{"a": 1}' and planner.raw_preview(None) == "(no output)"
    assert planner.raw_preview("x" * 5000).endswith("[5000 chars]")


def test_create_plan_logs_raw_output_then_raises_plan_error():
    class Bad:
        def generate_json(self, prompt, cfg):
            return ["not", "an", "object"]
    logs = []
    with pytest.raises(planner.PlanError):
        planner.create_plan(Bad(), {}, make_segments(), 200, {}, log=lambda m, lvl="INFO": logs.append((lvl, m)))
    assert sum("Raw model output that was rejected" in m for _l, m in logs) == 2          # both attempts
    assert any('["not", "an", "object"]' in m for _l, m in logs)


def test_malformed_plan_shapes_are_plan_errors_not_crashes():
    class Weird:
        def generate_json(self, prompt, cfg):
            return {"main_reels": [{"start": None, "end": "x"}], "poster": ["not", "a", "dict"]}
    with pytest.raises(planner.PlanError):
        planner.create_plan(Weird(), {}, make_segments(), 200, {}, log=lambda *a: None)


def test_unreachable_ollama_is_not_treated_as_a_plan_failure():
    class Down:
        def generate_json(self, prompt, cfg):
            raise LLMUnavailable("Could not use Ollama")
    with pytest.raises(LLMUnavailable):
        planner.create_plan(Down(), {}, make_segments(), 200, {}, log=lambda *a: None)


def test_long_transcript_second_attempt_reuses_the_map_step(monkeypatch):
    """Before: a failure in the first reduce step made the second attempt hit an unbound variable."""
    monkeypatch.setattr(planner, "SINGLE_PASS_CHARS", 100)
    monkeypatch.setattr(planner, "CHUNK_CHARS", 800)
    calls = []

    class LLM:
        def generate_json(self, prompt, cfg):
            reduce_step = prompt.startswith("You are a careful")
            calls.append("reduce" if reduce_step else "map")
            return ["bad"] if reduce_step else {"summary": "s", "themes": [], "quotes": [], "moments": []}
    with pytest.raises(planner.PlanError):
        planner.create_plan(LLM(), {}, make_segments(), 200, {}, log=lambda *a: None)
    assert calls.count("reduce") == 2 and 2 <= calls.count("map") < 8                     # map ran once per part, not twice


@pytest.mark.parametrize("duration", [200, 90, 60, 25, 12])
def test_fallback_plan_is_valid_for_any_length(duration):
    segs = make_segments(duration)
    full = " ".join(s["text"] for s in segs)
    p = planfallback.fallback_plan(segs, duration)
    assert p["fallback"] and len(p["main_reels"]) == planner.N_MAIN and 1 <= len(p["short_reels"]) <= 6   # as many as fit
    assert len(p["encouragement_posts"]) == planner.N_POSTS and p["visual_theme"] in planner.THEME_NAMES
    clips = sorted(p["main_reels"] + p["short_reels"], key=lambda c: c["start"])
    for c in clips:
        assert 0 <= c["start"] < c["end"] <= duration + 0.01 and 3 <= len(c["hook"].split()) <= 8 and c["closing"]
    if duration >= 90:
        assert all(a["end"] <= b["start"] + 0.01 for a, b in zip(clips, clips[1:]))        # no overlaps when there is room
    if duration < 90:
        return                                                                                 # tiny transcripts: stored as they are
    again = planner.normalize_plan({**p, "poster": {"hook": p["poster"]["headline"]},
                                    "encouragement_posts": [{"hook": x["headline"], "caption": x["caption"]} for x in p["encouragement_posts"]]},
                                   segs, duration, full)
    assert len(again["main_reels"]) == 1 and 1 <= len(again["short_reels"]) <= 6            # same shape as an AI plan


def test_fallback_plan_prefers_questions_and_whole_sentences():
    text = ["Grace is a gift.", "You cannot earn it.", "Do you really believe that today?", "We carry on with more words here.",
            "Another sentence about nothing in particular.", "Why would God give it freely?", "Because He loves you, that is why."]
    segs = [{"start": float(i * 10), "end": float(i * 10 + 10), "text": t} for i, t in enumerate(text * 12)]
    p = planfallback.fallback_plan(segs, segs[-1]["end"])
    ends = {s["end"] for s in segs}
    assert all(c["start"] in {s["start"] for s in segs} and c["end"] in ends for c in p["main_reels"] + p["short_reels"])
    with pytest.raises(planner.PlanError):
        planfallback.fallback_plan([], 10)


def test_a_failed_ai_plan_never_fails_the_pipeline(client, ctx, project_with_video, fakes, monkeypatch):
    pid = project_with_video
    monkeypatch.setattr(fakes.llm, "generate_json", lambda prompt, cfg: ["not", "an", "object"])
    r = client.post(f"/api/projects/{pid}/process")
    assert r.status_code == 202
    ctx.queue.drain()
    jobs = {j["job_type"]: j for j in client.get(f"/api/jobs?project_id={pid}").json()["items"]}
    assert all(j["status"] == "completed" for j in jobs.values()), {k: (v["status"], v["error"]) for k, v in jobs.items()}
    logs = job_logs(client, jobs["transcription"]["id"])
    assert any("Raw model output that was rejected" in m for m in logs) and any("basic plan" in m for m in logs)
    assert len(client.get(f"/api/assets?project_id={pid}&kind=main_reel").json()) == 1
    assert 2 <= len(client.get(f"/api/assets?project_id={pid}&kind=short_reel").json()) <= 6
    assert len(client.get(f"/api/assets?project_id={pid}&kind=poster").json()) == 1
    with ctx.db.session() as s:
        assert s.query(Transcript).filter_by(project_id=pid).first().plan["fallback"] is True


# ============================================================ 3. retries
@pytest.mark.parametrize("exc,temporary", [
    (LLMUnavailable("down"), True), (TimeoutError("slow"), True), (ConnectionError("refused"), True),
    (RuntimeError("CUDA out of memory"), True), (RuntimeError("request timed out"), True),
    (ValueError("bad input"), False), (KeyError("x"), False), (FileNotFoundError("gone"), False),
    (RuntimeError("Source video file is missing"), False), (planner.PlanError("bad plan"), False)])
def test_error_classification(exc, temporary):
    assert is_temporary(exc) is temporary


def test_ram_error_is_temporary():
    from ruha.ai.whisper import ResourceError
    assert is_temporary(ResourceError("Only 1.0 GB of RAM is free")) is True


def _flaky_transcriber(fakes, monkeypatch, fail_times, exc=ConnectionError("Ollama down")):
    real, state = fakes.transcriber.transcribe, {"n": 0}

    def transcribe(*a, **k):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise exc
        return real(*a, **k)
    monkeypatch.setattr(fakes.transcriber, "transcribe", transcribe)
    return state


def test_temporary_errors_retry_automatically_with_backoff(client, ctx, project_with_video, fakes, monkeypatch):
    pid = project_with_video
    put_settings(client, **{"jobs.retry_backoff_s": 0})
    state = _flaky_transcriber(fakes, monkeypatch, fail_times=2)
    jid = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "transcription", "params": {}}).json()["job_id"]
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "completed" and j["attempts"] == 3 and state["n"] == 3
    msgs = [m for m in job_logs(client, jid) if "Retrying automatically" in m]
    assert len(msgs) == 2 and "retry 1 of 3" in msgs[0] and "retry 2 of 3" in msgs[1]


def test_backoff_grows_and_job_waits_in_the_queue(client, ctx, project_with_video, fakes, monkeypatch):
    pid = project_with_video
    put_settings(client, **{"jobs.retry_backoff_s": 10})
    _flaky_transcriber(fakes, monkeypatch, fail_times=99)
    jid = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "transcription", "params": {}}).json()["job_id"]
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "queued" and "Waiting to retry (1 of 3)" in j["step"]
    with ctx.db.session() as s:
        from ruha.models import ProcessingJob
        row = s.get(ProcessingJob, jid)
        wait = row.params["_retry_at"] - time.time()
        assert 5 < wait <= 10 and row.params["_auto_retries"] == 1
    assert ctx.queue.drain() == []                                   # nothing runnable until the wait is over


def test_gives_up_after_three_retries_then_manual_retry_starts_fresh(client, ctx, project_with_video, fakes, monkeypatch):
    pid = project_with_video
    put_settings(client, **{"jobs.retry_backoff_s": 0})
    state = _flaky_transcriber(fakes, monkeypatch, fail_times=99)
    jid = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "transcription", "params": {}}).json()["job_id"]
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and state["n"] == 4 and "gave up after 3 automatic retries" in j["error"]
    state["n"] = 99                                                   # the service is back for the manual retry
    assert client.post(f"/api/jobs/{jid}/retry").status_code in (200, 202)
    ctx.queue.drain()
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "completed"


def test_permanent_errors_fail_at_once_without_retries(client, ctx, project_with_video, fakes, monkeypatch):
    pid = project_with_video
    put_settings(client, **{"jobs.retry_backoff_s": 0})
    state = _flaky_transcriber(fakes, monkeypatch, fail_times=99, exc=ValueError("unsupported audio"))
    jid = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "transcription", "params": {}}).json()["job_id"]
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and state["n"] == 1 and "unsupported audio" in j["error"]
    assert "automatic" not in j["error"]


def test_a_failed_reel_job_does_not_block_the_poster_and_posts(client, ctx, project_with_video, fakes):
    pid = project_with_video

    def broken(app, jc):
        raise RuntimeError("reel renderer broke")
    ctx.queue.handlers["reel_generation"] = broken
    client.post(f"/api/projects/{pid}/process")
    ctx.queue.drain()
    js = {j["job_type"]: j for j in client.get(f"/api/jobs?project_id={pid}").json()["items"]}
    assert js["reel_generation"]["status"] == "failed" and "reel renderer broke" in js["reel_generation"]["error"]
    assert js["poster_generation"]["status"] == "completed" and js["social_post_generation"]["status"] == "completed"
    assert js["transcription"]["status"] == "completed"


def test_a_job_waiting_to_retry_does_not_block_the_others(client, ctx, project_with_video, fakes):
    pid = project_with_video
    put_settings(client, **{"jobs.retry_backoff_s": 3600})

    def down(app, jc):
        raise ConnectionError("Ollama is not running")
    ctx.queue.handlers["reel_generation"] = down
    client.post(f"/api/projects/{pid}/process")
    ctx.queue.drain()
    js = {j["job_type"]: j for j in client.get(f"/api/jobs?project_id={pid}").json()["items"]}
    assert js["reel_generation"]["status"] == "queued" and "Waiting to retry" in js["reel_generation"]["step"]
    assert js["poster_generation"]["status"] == "completed" and js["social_post_generation"]["status"] == "completed"
    assert client.get(f"/api/projects/{pid}").json()["status"] == "processing"      # still working on it, not failed


def test_a_transcription_failure_still_stops_everything_that_needs_it(client, ctx, project_with_video, fakes):
    pid = project_with_video
    fakes.transcriber.fail = True                                    # RuntimeError: permanent
    client.post(f"/api/projects/{pid}/process")
    ctx.queue.drain()
    js = {j["job_type"]: j for j in client.get(f"/api/jobs?project_id={pid}").json()["items"]}
    assert all(js[t]["status"] == "failed" and "Not run because job" in js[t]["error"]
               for t in ("reel_generation", "poster_generation", "social_post_generation"))


# ============================================================ 4. RAM floor
def test_ram_floor_default_is_3gb_and_minimum_is_1gb(client):
    assert DEFAULT_CONFIG["gpu.min_free_ram_gb"]["value"] == 3.0
    assert client.get("/api/settings").json()["values"]["gpu.min_free_ram_gb"] == 3.0
    for bad in (0, 0.2, 0.99, -1, 65, "3", True, None):
        assert put_settings(client, **{"gpu.min_free_ram_gb": bad}).status_code == 400, bad
    for good in (1, 1.0, 2.5, 3, 16):
        assert put_settings(client, **{"gpu.min_free_ram_gb": good}).status_code == 200, good


# ============================================================ 5. status cache
def test_probe_cache_is_background_ttl_cached_and_keeps_old_value_on_failure():
    calls, gate = [], threading.Event()

    def slow():
        calls.append(1)
        time.sleep(0.4)
        if len(calls) == 3:
            raise RuntimeError("probe broke")
        return {"n": len(calls)}
    pc = sysinfo.ProbeCache(slow, ttl=0.6, background=True)
    t0 = time.perf_counter()
    assert pc.get(wait=0.05) is None and time.perf_counter() - t0 < 0.25                      # first call does not wait for the probe
    time.sleep(0.5)
    assert pc.get() == {"n": 1}
    t0 = time.perf_counter()
    for _ in range(20):
        assert pc.get() == {"n": 1}
    assert time.perf_counter() - t0 < 0.1 and len(calls) == 1                                # cached: no new probe
    time.sleep(0.5)                                                                            # ttl over: stale value now, refresh in background
    t0 = time.perf_counter()
    assert pc.get() == {"n": 1} and time.perf_counter() - t0 < 0.1
    time.sleep(0.6)
    assert pc.get() == {"n": 2} and len(calls) == 2
    time.sleep(0.6)
    pc.get()
    time.sleep(0.6)
    assert pc.get() == {"n": 2}                                                                # third probe raised: old value kept


def test_inline_mode_never_caches():
    n = []
    pc = sysinfo.ProbeCache(lambda: n.append(1) or {"n": len(n)}, background=False)
    assert pc.get() == {"n": 1} and pc.get() == {"n": 2}


def test_status_call_returns_fast_while_probes_are_slow(client, ctx):
    ctx._probe_cache = sysinfo.ProbeCache(lambda: (time.sleep(1.5), sysinfo._slow_probes(ctx))[1], ttl=30, background=True)
    t0 = time.perf_counter()
    first = client.get("/api/system/status")
    cold = time.perf_counter() - t0
    assert first.status_code == 200 and cold < 0.3, cold
    assert first.json()["ollama"].get("checking") is True and "ram" in first.json() and "whisper_languages" in first.json()
    assert ctx._probe_cache._first.wait(60)                          # wait for the background probes to really finish
    t0 = time.perf_counter()
    warm = client.get("/api/system/status")
    assert time.perf_counter() - t0 < 0.3
    assert warm.json()["ollama"].get("checking") is None and warm.json()["ollama"]["connected"] is True


def test_probe_results_are_the_same_shape_as_before(client):
    s = client.get("/api/system/status").json()
    for key in ("whisper", "ollama", "embedding", "gpu", "ram", "cpu", "disk", "ffmpeg", "translator", "queue",
                "model_loading", "active_llm", "whisper_models", "whisper_languages"):
        assert key in s, key
    assert {"installed", "model", "cuda_available", "model_cached", "status"} <= set(s["whisper"])


# ============================================================ 6. shared key-word rule
def test_one_key_word_rule_shared_by_cards_graphics_and_studio():
    from ruha.creative import compose
    from ruha.services import design, htmlrender
    assert design._key_words is keywords.key_words and htmlrender._key_words is keywords.key_words
    src = {p: (ROOT / "ruha" / p).read_text(encoding="utf-8") for p in
           ("services/design.py", "services/htmlrender.py", "creative/compose.py")}
    assert not any("def _key_words" in t for t in src.values()) and not any("_STOP = " in t for t in src.values())
    text = "Your breakthrough is closer than you think"
    assert keywords.key_words(text) == {"breakthrough", "closer"}
    assert keywords.key_words("") == set() and keywords.key_words("Is it you") == set()
    assert htmlrender._hook_html(text) == keywords.highlight_html(text) == compose._hl_html(text, True)
    assert '<span class="k">breakthrough</span>' in keywords.highlight_html(text)
    assert compose._hl_html("a <b> c", False) == "a &lt;b&gt; c" and "<b>" not in keywords.highlight_html("<b> bold")


# ============================================================ 7. typed reels skip the AI plan
def _typed(client, pid, text):
    r = client.put(f"/api/projects/{pid}/instructions", json={"reels": text, "posters": ""})
    assert r.status_code == 200, r.text


def _clear_plan(ctx, pid):
    with ctx.db.session() as s:
        s.query(Transcript).filter_by(project_id=pid).first().plan_json = None


def _planner_calls(fakes):
    return [c for c in fakes.llm.calls if c.startswith("You are a careful")]


def test_all_typed_reels_skip_the_ai_planner(client, ctx, processed, fakes):
    pid = processed
    client.put("/api/settings", json={"values": {"reels.count": 2}})         # typed: 1 main + 2 short = everything asked for
    _clear_plan(ctx, pid)
    _typed(client, pid, "0:10-0:55 main\n1:40-2:00 short\n2:10-2:30 short")
    fakes.llm.calls.clear()
    jid = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "reel_generation", "params": {"force": True}}).json()["job_id"]
    ctx.queue.drain()
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "completed"
    assert _planner_calls(fakes) == []                                       # the long AI planning call never ran
    spans = sorted((round(a["reel"]["start"]), round(a["reel"]["end"])) for k in ("main_reel", "short_reel")
                   for a in client.get(f"/api/assets?project_id={pid}&kind={k}").json()
                   if a["slot"] in ("main_1", "short_1", "short_2"))          # short_3 is left over from the earlier AI run
    assert spans == [(10, 55), (100, 120), (130, 150)]
    assert any("skipping the AI content plan" in m for m in job_logs(client, jid))
    with ctx.db.session() as s:
        assert s.query(Transcript).filter_by(project_id=pid).first().plan_json is None      # nothing stored for later jobs


def test_partly_typed_reels_still_use_the_planner(client, ctx, processed, fakes):
    pid = processed
    _clear_plan(ctx, pid)
    _typed(client, pid, "0:10-0:55 main\n1:40-2:00 short")                   # only 1 of the 2 short reels
    fakes.llm.calls.clear()
    client.post(f"/api/projects/{pid}/jobs", json={"job_type": "reel_generation", "params": {"force": True}})
    ctx.queue.drain()
    assert len(_planner_calls(fakes)) == 1


def test_invalid_typed_times_fail_fast_before_any_planning(client, ctx, processed, fakes):
    pid = processed
    _clear_plan(ctx, pid)
    with ctx.db.session() as s:
        ws = Path(s.get(Project, pid).workspace_dir)
    (ws / "instructions.json").write_text('{"reels": "10-5000", "posters": "", "generated": {}}', encoding="utf-8")
    fakes.llm.calls.clear()
    jid = client.post(f"/api/projects/{pid}/jobs", json={"job_type": "reel_generation", "params": {}}).json()["job_id"]
    ctx.queue.drain()
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "failed" and "reels instructions are not valid" in j["error"] and _planner_calls(fakes) == []


# ============================================================ 8. translator moved out of tests/
def test_translator_copy_lives_outside_tests_and_is_ignored_by_git():
    assert not (ROOT / "tests" / "indic-video-translator").exists()
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "external/" in [l.strip() for l in ignore]


# ============================================================ 9. project folders
def test_remove_tree_handles_read_only_files_and_retries(tmp_path, monkeypatch):
    d = tmp_path / "ws"
    (d / "a" / "b").mkdir(parents=True)
    f = d / "a" / "b" / "x.txt"
    f.write_text("x")
    os.chmod(f, 0o444)
    real, n = shutil.rmtree, {"n": 0}

    def flaky(path, *a, **k):
        n["n"] += 1
        if n["n"] == 1:
            raise OSError("The directory is in use")                          # what OneDrive/Windows does
        return real(path, *a, **k)
    monkeypatch.setattr(project_svc.shutil, "rmtree", flaky)
    assert project_svc.remove_tree(d, attempts=3, delay=0) == [] and not d.exists() and n["n"] == 2


def test_remove_tree_reports_what_is_left(tmp_path, monkeypatch):
    d = tmp_path / "ws"
    (d / "sub").mkdir(parents=True)
    monkeypatch.setattr(project_svc.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    left = project_svc.remove_tree(d, attempts=2, delay=0)
    assert left and d.exists()


def test_deleting_a_project_removes_its_whole_folder(client, ctx, processed):
    pid = processed
    with ctx.db.session() as s:
        ws = Path(s.get(Project, pid).workspace_dir)
    assert ws.exists()
    r = client.delete(f"/api/projects/{pid}?confirm=true")
    assert r.status_code == 200 and r.json()["files_removed"] is True and not ws.exists()


def _age(path, seconds=3600):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_orphan_folders_are_listed_and_only_empty_shells_removed_by_default(client, ctx, project_with_video):
    root = ctx.settings.projects_dir
    shell = root / "00090-old-project"
    (shell / "reels" / "deep").mkdir(parents=True)
    full = root / "00091-has-a-video"
    (full / "source").mkdir(parents=True)
    (full / "source" / "sermon.mp4").write_bytes(b"x" * 10)
    fresh = root / "00092-just-made"
    fresh.mkdir()
    for d in (shell, full):
        _age(d)
    with ctx.db.session() as s:
        live = Path(s.get(Project, project_with_video).workspace_dir)

    listed = {o["name"]: o for o in client.get("/api/system/orphan-workspaces").json()["items"]}
    assert set(listed) == {shell.name, full.name, fresh.name} and listed[shell.name]["empty"] and not listed[full.name]["empty"]
    assert live.name not in listed                                          # a real project's folder is never an orphan

    dry = client.post("/api/system/orphan-workspaces/cleanup").json()                     # no confirm -> dry run
    assert dry["dry_run"] and [o["name"] for o in dry["removed"]] == [shell.name] and shell.exists()

    done = client.post("/api/system/orphan-workspaces/cleanup?confirm=true").json()
    assert not done["dry_run"] and [o["name"] for o in done["removed"]] == [shell.name]
    assert not shell.exists() and full.exists() and fresh.exists() and live.exists()
    reasons = {o["name"]: o["reason"] for o in done["kept"]}
    assert "holds files" in reasons[full.name] and "last few minutes" in reasons[fresh.name]

    forced = client.post("/api/system/orphan-workspaces/cleanup?confirm=true&include_files=true").json()
    assert [o["name"] for o in forced["removed"]] == [full.name] and not full.exists() and live.exists()


def test_a_project_that_cannot_be_fully_deleted_is_cleaned_up_later(client, ctx, processed, monkeypatch):
    pid = processed
    with ctx.db.session() as s:
        ws = Path(s.get(Project, pid).workspace_dir)
    for f in [x for x in ws.rglob("*") if x.is_file()]:
        f.unlink()                                                           # what the failed deletes left: folders only
    real = project_svc.remove_tree
    monkeypatch.setattr(project_svc, "remove_tree", lambda p, *a, **k: [str(p)])          # this delete "fails"
    r = client.delete(f"/api/projects/{pid}?confirm=true")
    assert r.status_code == 200 and r.json()["files_removed"] is False and ws.exists()
    monkeypatch.setattr(project_svc, "remove_tree", real)
    _age(ws)
    res = client.post("/api/system/orphan-workspaces/cleanup?confirm=true").json()
    assert [o["name"] for o in res["removed"]] == [ws.name] and not ws.exists()


# ============================================================ 2b. cause of the failures on short transcripts
def test_reel_started_near_the_end_is_lengthened_instead_of_rejecting_the_plan():
    """The cause found with the real model on a 171 s talk: 'A main_reels entry is too short after snapping'."""
    segs = make_segments(170)
    full = " ".join(s["text"] for s in segs)
    plan = {"title": "T", "main_theme": "m", "summary": "s", "visual_theme": "grace", "themes": ["grace"],
            "main_reels": [{"start": 150, "end": 169, "hook": "Grace is free"}],          # only 20 s left in the video
            "short_reels": [{"start": 20, "end": 40, "hook": "Grace is the free gift"},
                            {"start": 60, "end": 80, "hook": "Grace is the free gift"}],
            "poster": {"hook": "H"}, "encouragement_posts": [{"hook": "a b c", "caption": "c"}, {"hook": "d e f", "caption": "c"}]}
    out = planner.normalize_plan(plan, segs, 170, full)
    a, b = out["main_reels"][0]["start"], out["main_reels"][0]["end"]
    assert b - a >= planner.MAIN_RANGE[0] and b <= 170 and a < 150                          # grown backwards
    assert planner._lengthen_back(segs, 160, 170, 30, 95)[0] <= 140                         # at least 30 s, whole segments
    assert planner._lengthen_back(segs, 0, 5, 30, 95) == (0.0, 5.0)                          # nowhere to grow: unchanged, no crash
