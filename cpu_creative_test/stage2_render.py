"""Stage 2 (Tests B-E): concepts -> typography -> QC for each asset type/format. Photo styles use a SYNTHETIC plate from the
engine's own procedural_background() (labelled; NOT AI art). 'minimal' is the engine's genuine typographic path."""
import json, time
from common import *
fa = build_app(); app = fa.state.ctx
cfg = {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in app.config.all().items()}
rd = lambda n: json.load(open(OUT / n, encoding="cp1252"))
brief, exp = rd("brief.json"), rd("experimental_hooks.json")
ids = json.load(open(OUT / "ids.json"))
from ruha.creative import brief as bm, compose, formats, qc, studio, styles
brand = studio._brand(app, ids["project_id"])
full = " ".join(x["text"] for x in segments())
res = {"image_status": app.services.images.status(cfg)}
res["image_status"]["hardware"] = {k: v for k, v in res["image_status"]["hardware"].items() if k != "models"}
try:
    app.services.images.generate("test", "", (512, 512), 1, cfg); res["image_generate"] = "UNEXPECTED SUCCESS"
except Exception as e:
    res["image_generate"] = f"{type(e).__name__}: {e}"
print("image status:", res["image_status"]["available"], "|", res["image_status"]["reason"][:200]); print("generate:", res["image_generate"][:200])

hooks = [h.strip() for h in exp["hooks"]]
H = brief["candidates"]["headlines"]
cases = [  # (id, label, format, style, headline, supporting, plate_seed)
 ("main1", "Main reel cover 1", "reel_cover", "cinematic", H[0], brief["supporting_text"], 11),
 ("main2", "Main reel cover 2", "reel_cover", "editorial", H[1], brief["supporting_text"], 22),
 ("main3", "Main reel cover 3", "reel_cover", "fineart", H[2], "", 33),
 ("short1", "Short reel cover 1", "reel_cover", "modern", hooks[4], "", 44),
 ("short2", "Short reel cover 2", "reel_cover", "movie", hooks[1], "", 55),
 ("poster", "Highlight poster", "poster", "biblical", exp["poster"]["headline"], exp["poster"]["subheadline"], 66),
 ("post1", "Encouragement post 1 (feed)", "feed_post", "emotional", exp["posts"][0]["headline"], exp["posts"][0]["caption"], 77),
 ("post2", "Encouragement post 2 (square)", "square_post", "minimal", exp["posts"][1]["headline"], exp["posts"][1]["caption"], 88),
 ("thumb", "YouTube thumbnail", "thumbnail", "movie", "When Your Strength Ends", "", 99),
 ("minimal_reel", "Typographic reel cover (minimal, real path)", "reel_cover", "minimal", H[0], "", 111),
]
metas = brief["candidates"]["metaphors"]
rows, hashes, heads = [], [], []
for k, (cid, label, fmt, sid, head, sup, seed) in enumerate(cases):
    t0 = time.time()
    c = bm.expand_concepts(brief, fmt, [sid], seeds=[seed])[0]
    # rotate the sermon's own visual metaphors so the concepts differ (the engine does this per style within one call)
    m = metas[k % len(metas)]
    c.update(headline=bm.shorten(head, formats.FORMATS[fmt]["max_words"]), supporting_text=sup,
             visual_subject=m["subject"], visual_metaphor=m["subject"], environment=m["environment"])
    size = formats.FORMATS[fmt]["size"]
    plate = None
    if c["visual_source"] == "photo":
        plate = compose.procedural_background(size, c["accent"], seed, True)
    try:
        r = compose.compose(plate, c, fmt, brand, show_brand=True)
        q = qc.check(r, c, fmt, transcript_text=full, other_hashes=hashes, other_headlines=heads, show_brand=True,
                     backend={"backend": "synthetic-plate" if plate else "typographic"})
        hashes.append(q["hash"]); heads.append(c["headline"])
        path = OUT / f"{cid}_{fmt}_{sid}.png"; r["final"].save(path)
        rows.append({"id": cid, "label": label, "format": fmt, "size": list(size), "style": sid, "zone": c["zone"],
                     "headline": c["headline"], "supporting": sup, "subject": c["visual_subject"], "environment": c["environment"],
                     "lighting": c["lighting"], "palette": c["colour_palette"], "composition": c["composition"],
                     "plate": "SYNTHETIC procedural plate (not AI/photo)" if plate else "typographic field (engine-native)",
                     "seconds": round(time.time() - t0, 1), "qc_passed": q["passed"], "qc_failed": q["failed"],
                     "qc_warn": [x["name"] + ": " + x["detail"] for x in q["checks"] if x["status"] == "warn"], "file": str(path.name)})
    except Exception as e:
        rows.append({"id": cid, "label": label, "error": f"{type(e).__name__}: {e}"})
    print(rows[-1].get("id"), rows[-1].get("qc_passed"), rows[-1].get("qc_failed"), rows[-1].get("seconds"), rows[-1].get("error", ""))
res["renders"] = rows
json.dump(res, open(OUT / "stage2.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
