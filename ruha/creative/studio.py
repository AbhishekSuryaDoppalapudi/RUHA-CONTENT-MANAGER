"""The creative workflow: brief -> concepts -> (stage 1) picture -> (stage 2) typography -> QC -> asset.

Runs as background jobs on the single sequential worker, so only one heavy model is ever in memory: the LLM is asked to
unload before an image model loads, and the image model is unloaded when the batch ends."""
from __future__ import annotations

import json
import time
from pathlib import Path

from PIL import Image

from ruha.creative import brief as brief_mod
from ruha.creative import compose, formats, qc, styles
from ruha.creative.backends import ImageBackendError
from ruha.models import GeneratedAsset, TranscriptSegment
from ruha.services import assets as asset_svc


class CreativeUnavailable(RuntimeError):
    """No image model is available. Said plainly - a template is never passed off as AI artwork."""


# ------------------------------------------------------------------ storage
def _dir(ws: Path, *parts) -> Path:
    p = Path(ws, "creative", *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def brief_file(ws: Path) -> Path:
    return Path(ws) / "creative" / "brief.json"


def load_brief(ws: Path) -> dict | None:
    p = brief_file(ws)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def save_brief(ws: Path, brief: dict) -> dict:
    _dir(ws)
    brief_file(ws).write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    return brief


def sermon_text(app, tid: int) -> tuple:
    """(full transcript, representative excerpt sampled evenly across the whole talk)."""
    with app.db.session() as s:
        full = " ".join(g.text for g in s.query(TranscriptSegment).filter_by(transcript_id=tid).order_by(TranscriptSegment.idx))
    if len(full) <= 3500:
        return full, full
    step = len(full) // 6
    return full, " ... ".join(full[i * step:i * step + 580] for i in range(6))


def _ctx(app, jc):
    from ruha import handlers
    proj = handlers._load_project(app, jc.project_id)
    plan, tid = handlers.ensure_plan(app, jc)
    full, excerpt = sermon_text(app, tid)
    return proj, plan, full, excerpt


# ------------------------------------------------------------------ brief job
def h_brief(app, jc):
    proj, plan, full, excerpt = _ctx(app, jc)
    jc.progress(0.3, "Writing the creative brief from the transcript", force=True)
    with app.model_lock:
        brief = brief_mod.build_brief(app.services.llm, jc.cfg, plan, excerpt, proj["name"])
    save_brief(proj["ws"], brief)
    jc.log(f"Brief ready (source: {brief['source']}; theme: {brief['emotional_theme']})")
    return {"theme": brief["emotional_theme"], "source": brief["source"]}


# ------------------------------------------------------------------ generation
def _existing(app, project_id: int, fmt_key: str) -> tuple:
    hashes, heads = [], []
    with app.db.session() as s:
        for a in s.query(GeneratedAsset).filter_by(project_id=project_id, kind="creative"):
            m = a.meta
            if m.get("format") == fmt_key and m.get("qc", {}).get("hash") is not None:
                hashes.append(int(m["qc"]["hash"]))
                heads.append(a.title)
    return hashes, heads


def _need_images(style_ids) -> bool:
    return any(styles.STYLES[s]["visual_source"] == "photo" for s in style_ids)


def _native_size(cfg) -> tuple:
    from ruha.creative import hardware
    key = cfg.get("creative.model")
    if key not in hardware.MODELS:
        g = hardware.gpu_info()
        key = hardware.recommend(g["total_gb"] if g else None)["model"] or "sd15"
    return hardware.MODELS[key]["size"]


def _get_picture(app, jc, concept, fmt_key, ws, *, force_new: bool = False, progress=None):
    """Stage 1. Returns (PIL image | None, info). Cached on disk per concept so recomposing never re-generates."""
    if concept["visual_source"] != "photo":
        return None, {"backend": "typographic", "model": "none"}
    cls = formats.aspect_class(formats.FORMATS[fmt_key]["size"])
    cache = _dir(ws, "bg") / f"{concept['style']}_{cls}_{concept['seed']}.png"
    meta = cache.with_suffix(".json")
    if cache.exists() and not force_new:
        return Image.open(cache).convert("RGB"), json.loads(meta.read_text("utf-8")) if meta.exists() else {}
    size = formats.gen_size(formats.FORMATS[fmt_key]["size"], _native_size(jc.cfg))
    with app.model_lock:
        img, info = app.services.images.generate(concept["prompt"], concept["negative"], size, concept["seed"],
                                                 jc.cfg, progress)
    img.save(cache, "PNG")
    meta.write_text(json.dumps(info), encoding="utf-8")
    return img, info


def _design(app, jc, concept, fmt_key, ws, brand, transcript, others, heads, *, slide=None, show_brand=True,
            force_new_picture=False, base_progress=0.0, span=0.1, headline_pool=()):
    """Picture + composition + QC with targeted repairs. Returns (result, qc, info, concept)."""
    max_attempts = int(jc.cfg.get("creative.max_attempts", 2) or 2)
    attempt, shrink, strength, regen = 0, 1.0, 0.72, force_new_picture
    cand = list(headline_pool)
    info = {}
    while True:
        attempt += 1
        jc.progress(base_progress + span * min(0.9, 0.15 * attempt), f"{concept['style_name']}: picture" if regen or attempt == 1 else "Adjusting layout", force=True)
        pic, info = _get_picture(app, jc, concept, fmt_key, ws, force_new=regen)
        regen = False
        res = compose.compose(pic, concept, fmt_key, brand, show_brand=show_brand, slide=slide, shrink=shrink,
                              scrim_strength=strength)
        q = qc.check(res, concept, fmt_key, transcript_text=transcript, other_hashes=others, other_headlines=heads,
                     show_brand=show_brand, slide=slide, backend=info)
        if q["passed"] or attempt >= max_attempts + 2:
            return res, q, info, concept
        fails = set(q["failed"])
        jc.log(f"QC failed for {concept['style']}/{fmt_key} (attempt {attempt}): {', '.join(sorted(fails))}", "WARNING")
        if fails & {"text_fits", "safe_margins", "no_clipping", "mobile_readability", "headline_length"}:
            mw = max(3, formats.FORMATS[fmt_key]["max_words"] - 2 * attempt)
            concept = {**concept, "headline": brief_mod.shorten(concept["headline"], mw)}
            shrink *= 0.94
        if fails & {"contrast_headline", "contrast_supporting", "contrast_reference"}:
            strength = min(0.92, strength + 0.12)
        if fails & {"duplicate_text"}:
            alts = [h for h in cand if not any(brief_mod.similar(h, o, 0.7) for o in heads)]
            if alts:
                concept = {**concept, "headline": brief_mod.shorten(alts[0], formats.FORMATS[fmt_key]["max_words"])}
        if fails & {"duplicate_visual", "image_not_flat"} and concept["visual_source"] == "photo":
            concept = {**concept, "seed": images_new_seed()}
            regen = True


def images_new_seed() -> int:
    import random
    return random.randint(1, 2**31 - 1)


def _register(app, jc, ws, concept, fmt_key, res, q, info, *, slot, version_asset: GeneratedAsset | None = None,
              slide_no: int | None = None, reason: str = "regenerated", brief_source: str = "") -> int:
    fmt = formats.FORMATS[fmt_key]
    meta = {"format": fmt_key, "format_label": fmt["label"], "style": concept["style"], "style_name": concept["style_name"],
            "concept": {k: v for k, v in concept.items() if k not in ("prompt", "negative")}, "prompt": concept["prompt"],
            "negative_prompt": concept["negative"], "seed": concept["seed"], "visual_source": concept["visual_source"],
            "backend": info.get("backend"), "model": info.get("model"), "qc": q, "brief_source": brief_source,
            "size": list(fmt["size"]), "headline": concept["headline"], "slide": slide_no,
            "background": str(Path(ws, "creative", "bg", f"{concept['style']}_{formats.aspect_class(fmt['size'])}_{concept['seed']}.png"))
            if concept["visual_source"] == "photo" else None}
    status = "draft" if q["passed"] else "revision_requested"       # never auto-approved
    with app.db.session() as s:
        if version_asset is None:
            out = Path(ws, "creative", f"{slot}_v1.png")
            res["final"].save(out, "PNG", optimize=True)
            a = asset_svc.register(app, s, jc.project_id, "creative", out, slot=slot, title=concept["headline"][:200],
                                   description=f"{concept['style_name']} · {fmt['label']}",
                                   caption=concept.get("supporting_text", ""), job_id=jc.job_id, preview_path=out, meta=meta)
            a.status = status
            return a.id
        a = s.get(GeneratedAsset, version_asset.id)
        out = Path(ws, "creative", f"{a.slot}_v{a.version + 1}.png")
        res["final"].save(out, "PNG", optimize=True)
        asset_svc.replace_file(app, s, a, out, reason, fields={"title": concept["headline"][:200],
                                                                "caption": concept.get("supporting_text", "")}, meta=meta)
        a.preview_path = str(out)
        a.status = status
        return a.id


def _next_slot(app, project_id, fmt_key, sid) -> str:
    with app.db.session() as s:
        n = s.query(GeneratedAsset).filter(GeneratedAsset.project_id == project_id, GeneratedAsset.kind == "creative",
                                           GeneratedAsset.slot.like(f"{fmt_key}-{sid}-%")).count()
    return f"{fmt_key}-{sid}-{n + 1}"


def h_generation(app, jc):
    mode = jc.params.get("mode", "new")
    if mode in ("recompose", "regenerate"):
        return _redo(app, jc, mode)
    proj, plan, full, excerpt = _ctx(app, jc)
    ws, brand = proj["ws"], _brand(app, jc.project_id)
    fmts = [f for f in (jc.params.get("formats") or ["poster"]) if f in formats.FORMATS]
    if not fmts:
        raise RuntimeError("Choose at least one format")
    ids = [s for s in (jc.params.get("styles") or styles.DEFAULT_TRIO) if s in styles.STYLES] or styles.DEFAULT_TRIO
    show_brand = bool(jc.cfg.get("creative.show_brand", True))

    brief = jc.params.get("brief") or load_brief(ws)
    if not brief:
        jc.progress(0.04, "Writing the creative brief from the transcript", force=True)
        with app.model_lock:
            brief = brief_mod.build_brief(app.services.llm, jc.cfg, plan, excerpt, proj["name"])
        save_brief(ws, brief)
    if jc.params.get("headlines"):                       # caller-supplied copy (e.g. encouragement hooks) is used as written
        brief = {**brief, "candidates": {**(brief.get("candidates") or {}), "headlines": list(jc.params["headlines"])},
                 "supporting_text": "", "headline": jc.params["headlines"][0]}
    pool = list((brief.get("candidates") or {}).get("headlines", []))

    if _need_images(ids):
        st = app.services.images.status(jc.cfg)
        if not st["available"]:
            raise CreativeUnavailable(st["reason"] + " Only the 'Minimalist typography' design works without an image model.")
        with app.model_lock:                                  # free the LLM's memory before an image model loads
            try:
                app.services.llm.unload(jc.cfg)
            except Exception:
                pass
    created = []
    total = len(fmts) * len(ids)
    done = 0
    try:
        for fmt_key in fmts:
            concepts = brief_mod.expand_concepts(brief, fmt_key, ids, seeds=[images_new_seed() for _ in ids])
            others, heads = _existing(app, jc.project_id, fmt_key)
            batch_hashes, batch_heads = list(others), list(heads)
            slides = brief_mod.carousel_slides(brief, formats.FORMATS["carousel"]["slides"]) if fmt_key == "carousel" else None
            for c in concepts:
                base = 0.08 + 0.9 * done / total
                try:
                    if slides:
                        created += _carousel(app, jc, ws, c, brand, full, slides, show_brand, batch_hashes, batch_heads,
                                             brief.get("source", ""), base, 0.9 / total)
                    else:
                        res, q, info, c2 = _design(app, jc, c, fmt_key, ws, brand, full, batch_hashes, batch_heads,
                                                   show_brand=show_brand, base_progress=base, span=0.9 / total,
                                                   headline_pool=pool)
                        slot = _next_slot(app, jc.project_id, fmt_key, c2["style"])
                        created.append(_register(app, jc, ws, c2, fmt_key, res, q, info, slot=slot,
                                                 brief_source=brief.get("source", "")))
                        batch_hashes.append(q["hash"])
                        batch_heads.append(c2["headline"])
                except ImageBackendError as e:
                    raise CreativeUnavailable(str(e)) from e
                done += 1
    finally:
        try:
            app.services.images.unload()
        except Exception:
            pass
    jc.log(f"Created {len(created)} creative(s); none are approved - review them in the workspace")
    return {"created": created}


def _carousel(app, jc, ws, concept, brand, full, slides, show_brand, hashes, heads, source, base, span):
    """One picture per concept, cropped/graded differently for each slide so the set feels continuous."""
    out = []
    n = len(slides)
    pic, info = _get_picture(app, jc, concept, "carousel", ws)
    for i, sl in enumerate(slides):
        sl = {**sl, "index": i, "total": n}
        c = {**concept, "headline": sl["headline"], "supporting_text": sl.get("supporting_text", ""),
             "scripture_reference": sl.get("scripture_reference", ""), "seed": concept["seed"]}
        zoom = 1.0 + 0.08 * (i % 3)
        shifted = pic
        if pic is not None and zoom > 1:
            w, h = pic.size
            cw, ch = int(w / zoom), int(h / zoom)
            x0 = int((w - cw) * (0.2 + 0.3 * (i % 3)))
            shifted = pic.crop((x0, 0, x0 + cw, ch)).resize((w, h), Image.LANCZOS)
        res = compose.compose(shifted, c, "carousel", brand, show_brand=show_brand and i == 0, slide=sl)
        q = qc.check(res, c, "carousel", transcript_text=full, other_hashes=[], other_headlines=[h for h in heads],
                     show_brand=show_brand and i == 0, slide=sl, backend=info)
        slot = f"{_next_slot(app, jc.project_id, 'carousel', concept['style'])}-s{i + 1}"
        out.append(_register(app, jc, ws, c, "carousel", res, q, info, slot=slot[:40], slide_no=i + 1, brief_source=source))
    heads.append(concept["headline"])
    return out


def _brand(app, project_id: int | None = None) -> dict:
    from ruha import handlers
    from ruha.services import brand as brand_svc
    return handlers._brand_for_design(brand_svc.get_brand(app, project_id))


# ------------------------------------------------------------------ edit / regenerate one creative
def _redo(app, jc, mode):
    aid = int(jc.params["asset_id"])
    with app.db.session() as s:
        a = s.get(GeneratedAsset, aid)
        if a is None or a.kind != "creative":
            raise LookupError("Creative not found")
        meta, pid = dict(a.meta), a.project_id
        title = a.title
    jc.project_id = pid
    proj, plan, full, excerpt = _ctx(app, jc)
    ws, brand = proj["ws"], _brand(app, jc.project_id)
    fmt_key = meta["format"]
    concept = dict(meta["concept"])
    concept["prompt"], concept["negative"] = meta["prompt"], meta["negative_prompt"]
    edits = jc.params.get("edits") or {}
    for k in ("headline", "supporting_text", "scripture_reference"):
        if edits.get(k) is not None:
            concept[k] = str(edits[k]).strip()
    with app.db.session() as s:
        asset = s.get(GeneratedAsset, aid)
        s.expunge(asset)
    show_brand = bool(jc.cfg.get("creative.show_brand", True))
    others, heads = _existing(app, pid, fmt_key)
    heads = [h for h in heads if h != title]
    others = [h for h in others if h != (meta.get("qc") or {}).get("hash")]
    if mode == "regenerate":
        concept["seed"] = images_new_seed()
        if concept["visual_source"] == "photo":
            st = app.services.images.status(jc.cfg)
            if not st["available"]:
                raise CreativeUnavailable(st["reason"])
            with app.model_lock:
                try:
                    app.services.llm.unload(jc.cfg)
                except Exception:
                    pass
    try:
        slide = None
        if meta.get("slide"):
            slide = {"role": "point", "headline": concept["headline"], "supporting_text": concept.get("supporting_text", ""),
                     "scripture_reference": concept.get("scripture_reference", ""), "index": meta["slide"] - 1,
                     "total": formats.FORMATS["carousel"]["slides"]}
        if mode == "recompose":                       # keep the stored picture; only the typography changes
            bgp = meta.get("background")
            pic = Image.open(bgp).convert("RGB") if bgp and Path(bgp).exists() else None
            info = {"backend": meta.get("backend"), "model": meta.get("model")}
            res = compose.compose(pic, concept, fmt_key, brand, show_brand=show_brand, slide=slide)
            q = qc.check(res, concept, fmt_key, transcript_text=full, other_hashes=others, other_headlines=heads,
                         show_brand=show_brand, slide=slide, backend=info)
        else:
            res, q, info, concept = _design(app, jc, concept, fmt_key, ws, brand, full, others, heads, slide=slide,
                                            show_brand=show_brand, force_new_picture=True,
                                            headline_pool=((load_brief(ws) or {}).get("candidates") or {}).get("headlines", []))
    except ImageBackendError as e:
        raise CreativeUnavailable(str(e)) from e
    finally:
        try:
            app.services.images.unload()
        except Exception:
            pass
    _register(app, jc, ws, concept, fmt_key, res, q, info, slot=None, version_asset=asset,
              reason="headline edited" if mode == "recompose" else "regenerated", brief_source=meta.get("brief_source", ""))
    return {"asset_id": aid, "mode": mode, "qc_passed": q["passed"]}


# ------------------------------------------------------------------ benchmark
def h_benchmark(app, jc):
    """Measure this machine: seconds and peak VRAM for one real generation with the active back-end."""
    cfg = jc.cfg
    st = app.services.images.status(cfg)
    if not st["available"]:
        raise CreativeUnavailable(st["reason"])
    with app.model_lock:
        try:
            app.services.llm.unload(cfg)
        except Exception:
            pass
        t0 = time.time()
        size = (512, 768)
        img, info = app.services.images.generate("a lone tree on a hill at sunrise, cinematic photograph",
                                                 styles.NEGATIVE_BASE, size, 42, cfg)
        secs = round(time.time() - t0, 1)
    peak = None
    try:
        import torch
        if torch.cuda.is_available():
            peak = round(torch.cuda.max_memory_allocated() / 1024**3, 2)
    except Exception:
        pass
    app.services.images.unload()
    res = {"backend": info.get("backend"), "model": info.get("model"), "seconds_first_image": secs, "size": list(img.size),
           "peak_vram_gb": peak, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    app.config.set_many({"creative.benchmark": res})
    jc.log(f"Benchmark: {res}")
    return res
