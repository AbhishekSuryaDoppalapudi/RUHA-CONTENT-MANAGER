"""Stage 1b: the engine's own build_brief() with the real LLM, fed a HAND-WRITTEN plan stub (the planner failed on the 171 s test
sermon, see stage1.json). Plus one clearly-labelled EXPERIMENTAL prompt for 5 idea-hooks + poster + 2 posts."""
import json
from common import *
fa = build_app(); app = fa.state.ctx
llm = app.services.llm
cfg = {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in app.config.all().items()}
segs = segments(); full = " ".join(x["text"] for x in segs)
plan_stub = {  # written by the tester from the sermon outline - NOT produced by RUHA
    "title": "Recognize Your Weakness, Strengthen Your Faith",
    "main_theme": "God does not require us to pretend to be strong; surrender weakness to Him and grow in faith through His strength.",
    "summary": "The speaker says we hide our weakness, but Paul's thorn shows God's power is made perfect in weakness. Recognize it, surrender it, "
               "pray honestly, and be transformed. Your weakness is the doorway to His strength.",
    "visual_theme": "strength",
    "key_points": ["Recognize your weakness honestly", "Surrender is not giving up", "God's power is made perfect in weakness",
                   "Honest prayer, not polished prayer", "When your strength ends, His begins"],
    "poster": {"hook": "", "subheadline": "2 Corinthians 12:9"}, "encouragement_posts": [], "main_reels": [], "short_reels": []}
from ruha.creative import brief as brief_mod
res = {"plan_source": "tester-written stub"}
ab = lambda: llm.unload(cfg)
with Monitor(0.30, ab) as m:
    try:
        br = brief_mod.build_brief(llm, cfg, plan_stub, full, plan_stub["title"]); res["brief_error"] = None
    except Exception as e:
        br, res["brief_error"] = None, f"{type(e).__name__}: {e}"
res["brief_perf"] = m.summary()
if br:
    json.dump(br, open(OUT / "brief.json", "w"), indent=2, ensure_ascii=False)
    print("brief source:", br["source"]); print(json.dumps(br, indent=1, ensure_ascii=False)[:2500])
json.dump(plan_stub, open(OUT / "plan_stub.json", "w"), indent=2)
EXP = ("You are the creative director of a church media team. SERMON SUMMARY: " + plan_stub["summary"] + "\nKEY POINTS: " + "; ".join(plan_stub["key_points"]) +
 "\nReturn ONLY JSON: {\"hooks\": [5 DIFFERENT reel-cover hook lines, 4 to 9 words, each communicates an idea and creates curiosity, "
 "never a single generic word like Faith/Trust/Believe/Strength/Battle], \"poster\": {\"headline\": \"...\", \"subheadline\": \"...\"}, "
 "\"posts\": [2 encouragement posts with clearly different angles: {\"headline\": \"...\", \"caption\": \"one or two sentences\"}]}. "
 "Stay faithful to the message that God does not require us to pretend to be strong. Never invent Bible verses.")
with Monitor(0.30, ab) as m2:
    try:
        exp = llm.generate_json(EXP, cfg); res["exp_error"] = None
    except Exception as e:
        exp, res["exp_error"] = None, f"{type(e).__name__}: {e}"
res["exp_perf"] = m2.summary()
json.dump(exp, open(OUT / "experimental_hooks.json", "w"), indent=2, ensure_ascii=False)
print("EXPERIMENTAL:", json.dumps(exp, indent=1, ensure_ascii=False)[:2500])
try: llm.unload(cfg)
except Exception: pass
json.dump(res, open(OUT / "stage1b.json", "w"), indent=2); print(json.dumps(res, indent=1))
