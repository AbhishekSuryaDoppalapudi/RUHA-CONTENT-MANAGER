"""Stage 3: the EXISTING poster + encouragement-post pipeline (design.py / HTML engine / themes.canvas) in the isolated data dir,
and the existing one-word-hook rule applied to idea-hooks. Plan = normalize_plan() of a tester-written raw stub (no LLM)."""
import json, shutil, time
from common import *
from fastapi.testclient import TestClient
fa = build_app(); app = fa.state.ctx
ids = json.load(open(OUT / "ids.json"))
exp = json.load(open(OUT / "experimental_hooks.json", encoding="cp1252"))
segs = segments(); full = " ".join(x["text"] for x in segs); dur = segs[-1]["end"]
from ruha.services import planner
H = exp["hooks"]
raw = {"title": "Recognize Your Weakness, Strengthen Your Faith", "main_theme": "God does not require us to pretend to be strong; surrender weakness to Him.",
 "visual_theme": "strength", "summary": "God's power is made perfect in weakness. Recognize it, surrender it, pray honestly and be transformed.",
 "themes": ["weakness", "surrender", "prayer"], "key_points": ["Recognize your weakness", "Surrender is not giving up", "His power is made perfect in weakness"],
 "quotes": [SERMON[5], SERMON[11]],
 "main_reels": [{"start": segs[2]["start"], "end": segs[9]["end"], "hook": H[0], "closing": "Bring it to Him today", "reason": "core message", "caption": "God does not need you to pretend.", "hashtags": ["#faith"]}],
 "short_reels": [{"start": segs[7]["start"], "end": segs[8]["end"], "hook": H[1], "closing": "Surrender it", "reason": "cracked pot", "caption": "Light through the cracks", "hashtags": ["#faith"]},
                 {"start": segs[11]["start"], "end": segs[12]["end"], "hook": H[4], "closing": "He is enough", "reason": "strength ends", "caption": "His strength begins", "hashtags": ["#faith"]}],
 "poster": {"hook": exp["poster"]["headline"], "verse": "2 Corinthians 12:9", "verse_text": "my grace is sufficient for you, for my power is made perfect in weakness", "theme": "strength"},
 "encouragement_posts": [{"hook": p["headline"], "reference": "", "caption": p["caption"]} for p in exp["posts"]],
 "hashtags": ["#faith", "#weakness", "#strength"]}
plan = planner.normalize_plan(raw, segs, dur, full)
json.dump(plan, open(OUT / "plan_normalized.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
res = {"normalized_hooks": {"main": [c["hook"] for c in plan["main_reels"]], "short": [c["hook"] for c in plan["short_reels"]],
        "poster": plan["poster"].get("headline"), "posts": [p.get("headline") or p.get("hook") for p in plan["encouragement_posts"]]},
       "one_word_hook_on_idea_hooks": {h: planner.one_word_hook(h, "strength") for h in H + ["Your Weakness Is Not the End of Your Story", "When Your Strength Ends, His Begins"]}}
print(json.dumps(res, indent=1, ensure_ascii=False))
from ruha.models import Transcript
with app.db.session() as s:
    t = s.get(Transcript, ids["transcript_id"]); t.plan_json = json.dumps(plan, ensure_ascii=False)
app.config.set_many({"design.engine": "html"})
with TestClient(fa) as c:
    q = app.queue
    t0 = time.time()
    j1 = q.enqueue("poster_generation", ids["project_id"], {})
    j2 = q.enqueue("social_post_generation", ids["project_id"], {})
    with Monitor(0.3) as m: q.drain()
    res["legacy_perf"] = m.summary()
    jobs = c.get(f"/api/projects/{ids['project_id']}/jobs").json() if False else None
    from ruha.models import GeneratedAsset, ProcessingJob
    with app.db.session() as s:
        res["jobs"] = [(j.job_type, j.status, (j.error or "")[:200]) for j in s.query(ProcessingJob).filter_by(project_id=ids["project_id"])]
        res["assets"] = []
        for a in s.query(GeneratedAsset).filter_by(project_id=ids["project_id"]):
            res["assets"].append({"kind": a.kind, "slot": a.slot, "title": a.title, "caption": a.caption, "file": a.file_path, "meta": a.meta})
            if a.file_path and Path(a.file_path).exists():
                shutil.copy(a.file_path, OUT / f"legacy_{a.kind}_{a.slot}.png")
print(json.dumps({k: res[k] for k in ("legacy_perf", "jobs")}, indent=1)); print([ (a["kind"], a["slot"], a["title"]) for a in res["assets"]])
json.dump(res, open(OUT / "stage3.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False, default=str)
