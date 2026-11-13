"""Job handlers. Each one is resumable: finished outputs are committed as they are produced
and skipped when the job runs again, so a failure never discards completed work."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ruha.config import QUALITY_CRF
from ruha.models import (GeneratedAsset, Poster, Project, Reel, SocialPost, Subtitle, Transcript,
                         TranscriptSegment, Translation)
from ruha.services import assets as asset_svc
from ruha.services import brand as brand_svc
from ruha.services import media, planner, projects
from ruha.services.design import render_graphic
from ruha.services.subtitles import build_srt, full_srt

PRESETS = {"draft": "ultrafast", "balanced": "veryfast", "high": "medium"}


def register(queue):
    queue.handlers.update({
        "transcription": h_transcription,
        "reel_generation": h_reels,
        "poster_generation": h_poster,
        "social_post_generation": h_social,
        "video_rendering": h_render,
        "translation": h_translate,
        "dubbing": h_translate,
    })


# ----------------------------- helpers ---------------------------------------
def _load_project(app, pid):
    with app.db.session() as s:
        p = s.get(Project, pid)
        if p is None:
            raise LookupError("Project no longer exists")
        v = projects.current_video(s, pid)
        return {"ws": Path(p.workspace_dir), "name": p.name,
                "video": None if v is None else {"id": v.id, "path": Path(v.path), "duration": v.duration,
                                                 "audio_tracks": v.audio_tracks}}


def _segments(app, tid):
    with app.db.session() as s:
        return [{"start": g.start, "end": g.end, "text": g.text} for g in
                s.query(TranscriptSegment).filter_by(transcript_id=tid).order_by(TranscriptSegment.idx)]


def _latest_transcript(app, pid):
    with app.db.session() as s:
        t = s.query(Transcript).filter_by(project_id=pid).order_by(Transcript.id.desc()).first()
        return None if t is None else {"id": t.id, "plan": t.plan, "language": t.language}


def ensure_plan(app, jc, force: bool = False):
    """Return (plan, transcript_id), asking the local LLM only when no plan is stored yet."""
    t = _latest_transcript(app, jc.project_id)
    if t is None:
        raise RuntimeError("No transcript yet - run transcription first")
    if t["plan"] and not force:
        return t["plan"], t["id"]
    segs = _segments(app, t["id"])
    proj = _load_project(app, jc.project_id)
    duration = max((proj["video"] or {}).get("duration") or 0, segs[-1]["end"] if segs else 0)
    full = " ".join(x["text"] for x in segs)
    ctxinfo = app.memory.prompt_context(jc.project_id, full[:2000])
    jc.log("Planning content with the local LLM")
    with app.model_lock:
        plan = planner.create_plan(app.services.llm, jc.cfg, segs, duration, ctxinfo, log=jc.log)
    with app.db.session() as s:
        tr = s.get(Transcript, t["id"])
        tr.plan_json = json.dumps(plan, ensure_ascii=False)
        tr.summary = plan["summary"]
        tr.themes_json = json.dumps(plan["themes"], ensure_ascii=False)
        tr.quotes_json = json.dumps(plan["quotes"], ensure_ascii=False)
    app.memory.index_transcript(t["id"])
    return plan, t["id"]


def _brand_for_render(app, jc):
    b = brand_svc.get_brand(app)
    cfg = jc.cfg
    return b, {
        "watermark": b["watermark"] if cfg.get("render.apply_watermark", True) else None,
        "intro": b["intro"] if cfg.get("render.apply_intro_outro", True) else None,
        "outro": b["outro"] if cfg.get("render.apply_intro_outro", True) else None,
    }


def _render_args(app, jc) -> dict:
    cfg = jc.cfg
    q = cfg.get("render.quality", "balanced")
    return {"width": int(cfg.get("render.width", 1080)), "height": int(cfg.get("render.height", 1920)),
            "crf": QUALITY_CRF.get(q, 23), "preset": PRESETS.get(q, "veryfast"),
            "burn_subtitles": bool(cfg.get("render.burn_subtitles", True)),
            "subtitle_style": cfg.get("subtitle.style", {}),
            "use_gpu": cfg.get("gpu.mode") == "gpu" and media.nvenc_available()}


def _excerpt(segs, a, b) -> str:
    return " ".join(x["text"] for x in segs if x["end"] > a and x["start"] < b)


def _brand_for_design(brand: dict) -> dict:
    return {"name": brand["name"], "tagline": brand["tagline"], "colors": brand["colors"],
            "fonts": brand["fonts"], "logo": brand["logo"]}


# ----------------------------- transcription ---------------------------------
def h_transcription(app, jc):
    proj = _load_project(app, jc.project_id)
    v = proj["video"]
    if not v:
        raise RuntimeError("Project has no source video")
    if v["audio_tracks"] == 0:
        raise RuntimeError("The source video has no audio track to transcribe")
    ws = proj["ws"]
    with app.db.session() as s:
        existing = s.query(Transcript).filter_by(project_id=jc.project_id, video_id=v["id"]).first()
        existing_id = existing.id if existing else None
    force = bool(jc.params.get("force"))
    if existing_id and not force:
        jc.log("Transcript already exists - skipping transcription")
        tid = existing_id
    else:
        jc.progress(0.03, "Extracting audio", force=True)
        wav = ws / "audio" / "source.wav"
        media.extract_audio(v["path"], wav)
        with app.model_lock:                       # sequential model loading
            try:
                app.services.llm.unload(jc.cfg)    # free LLM memory before Whisper
            except Exception:
                pass
            jc.progress(0.08, "Transcribing", force=True)
            segments, info = app.services.transcriber.transcribe(
                wav, jc.cfg, progress=lambda f: jc.progress(0.08 + 0.72 * f, "Transcribing"), log=jc.log)
        wav.unlink(missing_ok=True)
        if not segments:
            raise RuntimeError("No speech was detected in this video")
        tid = _store_transcript(app, jc, ws, v, segments, info)
    jc.progress(0.82, "Analysing content", force=True)
    plan_ok = True
    try:
        ensure_plan(app, jc, force=force)
    except Exception as e:   # transcript is already safe; analysis can be retried by the reel job
        plan_ok = False
        jc.log(f"Content analysis skipped: {e}", "WARNING")
    jc.progress(0.95, "Indexing memory", force=True)
    app.memory.index_transcript(tid)
    similar = _similar_projects(app, tid, jc.project_id)
    if similar:
        jc.log("Similar existing content: " + ", ".join(f"project {x['project_id']} ({x['score']:.2f})" for x in similar), "WARNING")
    return {"transcript_id": tid, "plan_created": plan_ok, "similar_projects": similar}


def _similar_projects(app, tid, pid, threshold=0.8) -> list:
    with app.db.session() as s:
        t = s.get(Transcript, tid)
        probe = (t.summary or t.full_text or "")[:1500]
    seen = {}
    for h in app.memory.search(probe, kinds=["summary", "transcript"], n=10, exclude_project=pid):
        if h["score"] >= threshold and h["project_id"]:
            seen[h["project_id"]] = max(h["score"], seen.get(h["project_id"], 0))
    return [{"project_id": k, "score": round(v, 3)} for k, v in sorted(seen.items(), key=lambda x: -x[1])]


def _store_transcript(app, jc, ws: Path, v: dict, segments: list, info: dict) -> int:
    tdir = ws / "transcript"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "transcript.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    (tdir / "transcript.txt").write_text(
        "\n".join(f"[{x['start']:.2f} - {x['end']:.2f}] {x['text']}" for x in segments), encoding="utf-8")
    srt = full_srt(segments, ws / "subtitles" / "original.srt")
    with app.db.session() as s:
        old = s.query(Transcript).filter_by(project_id=jc.project_id).all()
        for o in old:
            s.delete(o)
        for a in s.query(GeneratedAsset).filter(GeneratedAsset.project_id == jc.project_id,
                                                 GeneratedAsset.slot.in_(["transcript", "original_srt"])).all():
            s.delete(a)
        s.flush()
        t = Transcript(project_id=jc.project_id, video_id=v["id"], language=info.get("language"),
                       model=info.get("model"), full_text=" ".join(x["text"] for x in segments))
        s.add(t)
        s.flush()
        s.bulk_save_objects([TranscriptSegment(transcript_id=t.id, idx=i, start=x["start"], end=x["end"],
                                               text=x["text"]) for i, x in enumerate(segments)])
        asset_svc.register(app, s, jc.project_id, "transcript", tdir / "transcript.txt", slot="transcript",
                           title="Transcript", description=f"Language: {info.get('language')}", video_id=v["id"],
                           job_id=jc.job_id, meta={"language": info.get("language"), "segments": len(segments)})
        a2 = asset_svc.register(app, s, jc.project_id, "subtitle", srt, slot="original_srt",
                                title="Original subtitles", video_id=v["id"], job_id=jc.job_id,
                                meta={"language": info.get("language")})
        s.add(Subtitle(project_id=jc.project_id, asset_id=a2.id, language=info.get("language") or "original",
                       path=str(srt)))
        tid = t.id
    return tid


# ----------------------------- reels ------------------------------------------
def h_reels(app, jc):
    plan, tid = ensure_plan(app, jc)
    proj = _load_project(app, jc.project_id)
    v = proj["video"]
    if not v or not v["path"].exists():
        raise RuntimeError("Source video file is missing")
    segs = _segments(app, tid)
    brand, extras = _brand_for_render(app, jc)
    args = _render_args(app, jc)
    force = bool(jc.params.get("force"))
    slots = [(f"main_{i}", "main", c) for i, c in enumerate(plan["main_reels"], 1)] + \
            [(f"short_{i}", "short", c) for i, c in enumerate(plan["short_reels"], 1)]
    with app.db.session() as s:
        existing = {a.slot for a in s.query(GeneratedAsset).filter_by(project_id=jc.project_id)
                    if a.slot and a.slot.startswith(("main_", "short_")) and a.kind in ("main_reel", "short_reel")}
    done = []
    for n, (slot, cat, clip) in enumerate(slots):
        if slot in existing and not force:
            jc.log(f"{slot} already exists - skipped")
            continue
        jc.progress(n / len(slots), f"Rendering {slot}", force=True)
        out = proj["ws"] / "reels" / f"{slot}_v1.mp4"
        media.render_reel(v["path"], clip["start"], clip["end"], out, segments=segs, **args, **extras)
        srt = build_srt(segs, clip["start"], clip["end"], proj["ws"] / "subtitles" / f"{slot}_v1.srt",
                        max_chars=int(args["subtitle_style"].get("max_chars_per_line", 28)))
        thumb = media.make_thumbnail(out, proj["ws"] / "thumbs" / f"{slot}.jpg", at=min(1.0, (clip["end"] - clip["start"]) / 2))
        with app.db.session() as s:
            if force:
                for old in s.query(GeneratedAsset).filter(GeneratedAsset.project_id == jc.project_id,
                                                          GeneratedAsset.slot.in_([slot, f"{slot}_srt"])).all():
                    asset_svc.delete_files(app, old)
                    s.delete(old)
                s.flush()
            sa = asset_svc.register(app, s, jc.project_id, "subtitle", srt, slot=f"{slot}_srt",
                                    title=f"Subtitles - {slot}", video_id=v["id"], job_id=jc.job_id,
                                    meta={"start": clip["start"], "end": clip["end"]})
            a = asset_svc.register(
                app, s, jc.project_id, "main_reel" if cat == "main" else "short_reel", out, slot=slot,
                title=clip["hook"][:200] or f"{plan['title']} - {slot}", description=clip["reason"],
                caption=clip["caption"], hashtags=clip["hashtags"], video_id=v["id"], job_id=jc.job_id,
                preview_path=thumb, meta={"start": clip["start"], "end": clip["end"],
                                          "duration": round(clip["end"] - clip["start"], 2),
                                          "width": args["width"], "height": args["height"]})
            s.add(Reel(asset_id=a.id, category=cat, start_s=clip["start"], end_s=clip["end"], hook=clip["hook"],
                       reason=clip["reason"], transcript_excerpt=_excerpt(segs, clip["start"], clip["end"]),
                       subtitle_asset_id=sa.id))
            s.add(Subtitle(project_id=jc.project_id, asset_id=sa.id, language="original", path=str(srt)))
            aid = a.id
        app.memory.index_asset(aid)
        done.append(slot)
    jc.progress(1.0, "Reels ready", force=True)
    return {"rendered": done, "skipped": [s_ for s_, *_ in slots if s_ not in done]}


# ----------------------------- posters & social -------------------------------
def _poster_file(ws, asset_slot, version):
    return ws / "posters" / f"{asset_slot}_v{version}.png"


def h_poster(app, jc):
    asset_id = jc.params.get("asset_id")
    if asset_id:
        rerender_graphic(app, asset_id, layout=jc.params.get("layout"))
        return {"asset_id": asset_id, "mode": "rerender"}
    plan, _ = ensure_plan(app, jc)
    proj = _load_project(app, jc.project_id)
    with app.db.session() as s:
        have = s.query(GeneratedAsset).filter_by(project_id=jc.project_id, kind="poster").first()
        if have and not jc.params.get("force"):
            jc.log("Poster already exists - skipped")
            return {"skipped": True}
        if have:
            asset_svc.delete_files(app, have)
            s.delete(have)
        layout, tpl = brand_svc.layout_for(s, "poster")
    brand = brand_svc.get_brand(app)
    jc.progress(0.3, "Designing poster", force=True)
    p = plan["poster"]
    out = _poster_file(proj["ws"], "poster", 1)
    size = tuple(brand["export"]["poster"])
    render_graphic(out, p["headline"], p["quote"] or p["subheadline"], "", "Highlight", "poster",
                   _brand_for_design(brand), layout, size)
    with app.db.session() as s:
        a = asset_svc.register(app, s, jc.project_id, "poster", out, slot="poster", title=p["headline"][:200],
                               description=p["subheadline"], caption=p["quote"], hashtags=plan["hashtags"][:8],
                               job_id=jc.job_id, preview_path=out,
                               meta={"layout": layout, "template_id": tpl, "size": list(size)})
        s.add(Poster(asset_id=a.id, headline=p["headline"], subheadline=p["subheadline"], quote=p["quote"],
                     theme=p["theme"], template_id=tpl))
        aid = a.id
    app.memory.index_asset(aid)
    return {"asset_id": aid}


def h_social(app, jc):
    asset_id = jc.params.get("asset_id")
    if asset_id:
        mode = jc.params.get("mode", "regenerate")
        if mode == "regenerate":
            _regenerate_post(app, jc, asset_id)
        else:
            rerender_graphic(app, asset_id, layout=jc.params.get("layout"))
        return {"asset_id": asset_id, "mode": mode}
    plan, tid = ensure_plan(app, jc)
    proj = _load_project(app, jc.project_id)
    full = " ".join(x["text"] for x in _segments(app, tid))
    with app.db.session() as s:
        existing = {a.slot for a in s.query(GeneratedAsset).filter_by(project_id=jc.project_id, kind="social_post")}
    brand = brand_svc.get_brand(app)
    made = []
    for i, post in enumerate(plan["encouragement_posts"]):
        slot = f"social_{i + 1}"
        if slot in existing and not jc.params.get("force"):
            jc.log(f"{slot} already exists - skipped")
            continue
        jc.progress(i / 2, f"Designing {slot}", force=True)
        post, similar = _dedupe_post(app, jc, plan, full, post)
        with app.db.session() as s:
            layout, tpl = brand_svc.layout_for(s, "social", i)
        out = proj["ws"] / "social" / f"{slot}_v1.png"
        size = tuple(brand["export"]["social"])
        render_graphic(out, post["headline"], post["body"], post["reference"], "Encouragement", "social",
                       _brand_for_design(brand), layout, size)
        with app.db.session() as s:
            a = asset_svc.register(app, s, jc.project_id, "social_post", out, slot=slot, title=post["headline"][:200],
                                   caption=post["caption"], hashtags=plan["hashtags"][:8], job_id=jc.job_id,
                                   preview_path=out, meta={"layout": layout, "template_id": tpl, "size": list(size),
                                                           "similar_to": similar})
            s.add(SocialPost(asset_id=a.id, headline=post["headline"], body=post["body"],
                             reference=post["reference"], template_id=tpl))
            aid = a.id
        app.memory.index_asset(aid)
        made.append(slot)
    return {"created": made}


SIMILARITY_LIMIT = 0.82


def _dedupe_post(app, jc, plan, full, post, exclude_asset=None):
    """Avoid repeating earlier posts: regenerate once with an avoid-list, else flag the overlap."""
    text = f"{post['headline']}\n{post['body']}"
    sims = [h for h in app.memory.similar(text, ["post"], SIMILARITY_LIMIT, exclude_asset=exclude_asset)]
    if not sims:
        return post, []
    jc.log(f"Post resembles earlier content (score {sims[0]['score']:.2f}); regenerating once", "WARNING")
    try:
        ctxinfo = app.memory.prompt_context(jc.project_id, full[:1500])
        with app.model_lock:
            alt = planner.regenerate_posts(app.services.llm, jc.cfg, plan, full, [h["text"] for h in sims], ctxinfo, 1)[0]
        again = app.memory.similar(f"{alt['headline']}\n{alt['body']}", ["post"], SIMILARITY_LIMIT, exclude_asset=exclude_asset)
        if not again:
            return alt, []
        sims = again
        post = alt
    except Exception as e:
        jc.log(f"Could not regenerate a distinct post: {e}", "WARNING")
    return post, [{"asset_id": h["asset_id"], "score": h["score"]} for h in sims]


def _regenerate_post(app, jc, asset_id):
    with app.db.session() as s:
        a = s.get(GeneratedAsset, asset_id)
        sp = s.query(SocialPost).filter_by(asset_id=asset_id).first()
        if not a or not sp:
            raise LookupError("Social post not found")
        pid, old_text = a.project_id, f"{sp.headline}\n{sp.body}"
    jc.project_id = pid
    plan, tid = ensure_plan(app, jc)
    full = " ".join(x["text"] for x in _segments(app, tid))
    ctxinfo = app.memory.prompt_context(pid, full[:1500])
    avoid = [old_text] + [h["text"] for h in app.memory.similar(old_text, ["post"], 0.6, n=4)]
    with app.model_lock:
        new = planner.regenerate_posts(app.services.llm, jc.cfg, plan, full, avoid, ctxinfo, 1,
                                       note=jc.params.get("note", ""))[0]
    with app.db.session() as s:
        sp = s.query(SocialPost).filter_by(asset_id=asset_id).first()
        sp.headline, sp.body, sp.reference = new["headline"], new["body"], new["reference"]
        a = s.get(GeneratedAsset, asset_id)
        a.caption = new["caption"] or a.caption
        a.title = new["headline"][:200]
    rerender_graphic(app, asset_id, reason="regenerated")


def rerender_graphic(app, asset_id: int, layout: str | None = None, reason: str = "re-rendered"):
    """Re-draw a poster/social image from its stored text fields as a new version."""
    brand = brand_svc.get_brand(app)
    with app.db.session() as s:
        a = s.get(GeneratedAsset, asset_id)
        if not a or a.kind not in ("poster", "social_post"):
            raise LookupError("Not a poster or social post")
        meta = dict(a.meta)
        ws = Path(a.file_path).parent.parent
        if a.kind == "poster":
            r = s.query(Poster).filter_by(asset_id=a.id).first()
            head, body, ref, label, kind = r.headline, r.quote or r.subheadline, "", "Highlight", "poster"
            folder = "posters"
        else:
            r = s.query(SocialPost).filter_by(asset_id=a.id).first()
            head, body, ref, label, kind = r.headline, r.body, r.reference, "Encouragement", "social"
            folder = "social"
        layout = layout or meta.get("layout", "classic")
        size = tuple(meta.get("size") or brand["export"][kind])
        out = ws / folder / f"{a.slot}_v{a.version + 1}.png"
        render_graphic(out, head, body, ref, label, kind, _brand_for_design(brand), layout, size)
        meta["layout"] = layout
        asset_svc.replace_file(app, s, a, out, reason, meta=meta)
        a.preview_path = str(out)
    app.memory.index_asset(asset_id)


# ----------------------------- re-render a reel --------------------------------
def h_render(app, jc):
    asset_id = jc.params.get("asset_id")
    if not asset_id:
        raise RuntimeError("video_rendering needs an asset_id")
    with app.db.session() as s:
        a = s.get(GeneratedAsset, asset_id)
        r = s.query(Reel).filter_by(asset_id=asset_id).first()
        if not a or not r:
            raise LookupError("Reel not found")
        pid, slot, version = a.project_id, a.slot, a.version
        start = float(jc.params.get("start", r.start_s))
        end = float(jc.params.get("end", r.end_s))
        sub_id = r.subtitle_asset_id
    jc.project_id = pid
    proj = _load_project(app, pid)
    v = proj["video"]
    t = _latest_transcript(app, pid)
    segs = _segments(app, t["id"]) if t else []
    if end - start < 3 or start < 0 or (v["duration"] and end > v["duration"] + 0.5):
        raise RuntimeError("Invalid start/end for this video")
    brand, extras = _brand_for_render(app, jc)
    args = _render_args(app, jc)
    jc.progress(0.1, "Rendering reel", force=True)
    out = proj["ws"] / "reels" / f"{slot}_v{version + 1}.mp4"
    media.render_reel(v["path"], start, end, out, segments=segs, **args, **extras)
    srt = build_srt(segs, start, end, proj["ws"] / "subtitles" / f"{slot}_v{version + 1}.srt",
                    max_chars=int(args["subtitle_style"].get("max_chars_per_line", 28)))
    thumb = media.make_thumbnail(out, proj["ws"] / "thumbs" / f"{slot}_v{version + 1}.jpg", at=1.0)
    with app.db.session() as s:
        a = s.get(GeneratedAsset, asset_id)
        r = s.query(Reel).filter_by(asset_id=asset_id).first()
        r.start_s, r.end_s = start, end
        r.transcript_excerpt = _excerpt(segs, start, end)
        meta = dict(a.meta)
        meta.update({"start": start, "end": end, "duration": round(end - start, 2)})
        asset_svc.replace_file(app, s, a, out, "re-rendered", meta=meta)
        a.preview_path = str(thumb)
        if sub_id:
            sa = s.get(GeneratedAsset, sub_id)
            if sa:
                asset_svc.replace_file(app, s, sa, srt, "re-rendered")
    return {"asset_id": asset_id, "start": start, "end": end}


# ----------------------------- translation / dubbing ---------------------------
def h_translate(app, jc):
    proj = _load_project(app, jc.project_id)
    v = proj["video"]
    if not v:
        raise RuntimeError("Project has no source video")
    p = jc.params
    target = p.get("target") or "Hindi"
    dub = bool(p.get("dub")) or _job_type(app, jc.job_id) == "dubbing"
    out_dir = proj["ws"] / "translations" / f"{re.sub(r'[^A-Za-z]', '', target)}_{jc.job_id}"
    with app.db.session() as s:
        tr = Translation(project_id=jc.project_id, video_id=v["id"], target_language=target, dubbed=dub,
                         voice=p.get("voice", "Female"), status="running", job_id=jc.job_id)
        s.add(tr)
        s.flush()
        trid = tr.id
    try:
        with app.model_lock:
            try:
                app.services.llm.unload(jc.cfg)
            except Exception:
                pass
            res = app.services.translator.translate(
                v["path"], out_dir, source=p.get("source", "Auto-detect"), target=target,
                voice=p.get("voice", "Female"), dub=dub, keep_background=p.get("keep_background", True),
                burn=p.get("burn", False), resolution=p.get("resolution", "Original resolution"),
                progress=lambda f, d="": jc.progress(f, d or "Translating"), log=jc.log, cancel=jc.cancelled)
    except Exception:
        with app.db.session() as s:
            t = s.get(Translation, trid)
            t.status = "failed"
        raise
    with app.db.session() as s:
        t = s.get(Translation, trid)
        label = f"{target}{' dubbed' if dub else ''}"
        ids = {}
        for key, kind, slot, title in (("video", "translation_video", f"tr_{trid}_video", f"{label} video"),
                                       ("srt", "translation_subtitle", f"tr_{trid}_srt", f"{target} subtitles"),
                                       ("dub_audio", "audio", f"tr_{trid}_audio", f"{target} dub audio")):
            if res.get(key):
                a = asset_svc.register(app, s, jc.project_id, kind, Path(res[key]), slot=slot, title=title,
                                       description=res.get("status", ""), video_id=v["id"], job_id=jc.job_id,
                                       meta={"target": target, "dubbed": dub})
                ids[key] = a.id
        if "srt" in ids:
            s.add(Subtitle(project_id=jc.project_id, asset_id=ids["srt"], language=target, path=res["srt"]))
        t.status = "completed"
        t.video_asset_id, t.srt_asset_id, t.audio_asset_id = ids.get("video"), ids.get("srt"), ids.get("dub_audio")
    return {"translation_id": trid, "assets": ids, "status": res.get("status")}


def _job_type(app, job_id):
    from ruha.models import ProcessingJob
    with app.db.session() as s:
        return s.get(ProcessingJob, job_id).job_type
