"""Stage 1 (Test A): plan + creative brief from the fictional sermon with the REAL local LLM (Ollama qwen2.5:3b)."""
import json, sys, time
from common import *
from fastapi.testclient import TestClient

fa = build_app(); app = fa.state.ctx
llm = app.services.llm
cfg = app.config.all()
cfg = {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in cfg.items()}
print("llm model:", cfg.get("llm.model"), "free RAM GB:", round(free_gb(), 2))

with TestClient(fa) as c:
    pid = c.post("/api/projects", json={"name": "CPU TEST - Recognize Your Weakness", "tags": ["cpu-test"]}).json()["id"]
    from ruha.models import Transcript, TranscriptSegment
    segs = segments()
    with app.db.session() as s:
        t = Transcript(project_id=pid, language="en", model="synthetic-test", full_text=" ".join(x["text"] for x in segs))
        s.add(t); s.flush()
        for i, g in enumerate(segs):
            s.add(TranscriptSegment(transcript_id=t.id, idx=i, start=g["start"], end=g["end"], text=g["text"]))
        tid = t.id
    json.dump({"project_id": pid, "transcript_id": tid}, open(OUT / "ids.json", "w"))
    duration = segs[-1]["end"]
    print("project", pid, "transcript", tid, "duration", duration)

    from ruha.services import planner
    from ruha.creative import brief as brief_mod
    res = {"duration": duration}
    ab = lambda: llm.unload(cfg)
    if free_gb() < 1.2:
        print("ABORT: free RAM below safe start level", free_gb()); sys.exit(3)
    with Monitor(0.35, ab) as m:
        try:
            plan = planner.create_plan(llm, cfg, segs, duration, {}, log=lambda *a, **k: print("LOG", a))
            res["plan_error"] = None
        except Exception as e:
            plan, res["plan_error"] = None, f"{type(e).__name__}: {e}"
    res["plan_perf"] = m.summary()
    print("plan:", res["plan_error"] or "ok", res["plan_perf"])
    if plan:
        json.dump(plan, open(OUT / "plan.json", "w"), indent=2, ensure_ascii=False)
        with app.db.session() as s:
            tr = s.get(Transcript, tid); tr.plan_json = json.dumps(plan, ensure_ascii=False)
        full = " ".join(x["text"] for x in segs)
        with Monitor(0.35, ab) as m2:
            try:
                br = brief_mod.build_brief(llm, cfg, plan, full, "Recognize Your Weakness, Strengthen Your Faith")
                res["brief_error"] = None
            except Exception as e:
                br, res["brief_error"] = None, f"{type(e).__name__}: {e}"
        res["brief_perf"] = m2.summary()
        if br:
            json.dump(br, open(OUT / "brief.json", "w"), indent=2, ensure_ascii=False)
            print("brief source:", br["source"], "| headlines:", br["candidates"]["headlines"])
    try: llm.unload(cfg)
    except Exception: pass
    json.dump(res, open(OUT / "stage1.json", "w"), indent=2)
    print(json.dumps(res, indent=1))
