"""Job handlers. Each one is resumable: finished outputs are committed as they are produced
and skipped when the job runs again, so a failure never discards completed work."""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path

from ruha.config import QUALITY_CRF
from ruha.models import (GeneratedAsset, Poster, Project, Reel, SocialPost, SourceVideo, Subtitle, Transcript,
                         TranscriptSegment, Translation)
from ruha.services import assets as asset_svc
from ruha.services import brand as brand_svc
from ruha.services import (carousel, clipcopy, hooks, instructions, media, planfallback, planner, projects, quotes, reelvideo,
                           reframe, selection, thumbnail, thumbs, themes)
from ruha.services.design import render_card, render_graphic
log = logging.getLogger("ruha.handlers")
from ruha.creative import formats
from ruha.services import subtitles
from ruha.services.subtitles import build_cues, build_srt, cues_to_srt, full_srt

PRESETS = {"draft": "ultrafast", "balanced": "veryfast", "high": "medium"}


def register(queue):
    queue.handlers.update({
        "transcription": h_transcription,
        "reel_generation": h_reels,
        "poster_generation": h_poster,
        "social_post_generation": h_social,
        "carousel_generation": h_carousel,
        "thumbnail_generation": h_thumbnails,
        "video_rendering": h_render,
        "translation": h_translate,
        "dubbing": h_translate,
        "video_download": h_download,
        "image_reel": h_image_reel,
        "yt_narrate": lambda app, jc: h_yt_narrate(app, jc),
        "yt_video": lambda app, jc: h_yt_video(app, jc),
        "creative_brief": lambda app, jc: __import__("ruha.creative.studio", fromlist=["x"]).h_brief(app, jc),
        "creative_generation": lambda app, jc: __import__("ruha.creative.studio", fromlist=["x"]).h_generation(app, jc),
        "creative_benchmark": lambda app, jc: __import__("ruha.creative.studio", fromlist=["x"]).h_benchmark(app, jc),
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
                                                 "audio_tracks": v.audio_tracks, "width": v.width, "height": v.height}}


def _segments(app, tid):
    with app.db.session() as s:
        return [{"start": g.start, "end": g.end, "text": g.text, **({"words": g.words} if g.words else {})} for g in
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
    stored = t["plan"] or {}
    proj = _load_project(app, jc.project_id)
    rs = selection.reel_settings(jc.cfg)
    topic = instructions.load(proj["ws"])["topic"]                # "Find a moment about ..." (empty = none)
    # a stored plan is reused only when it was made by this version (scored candidates) for the same reel settings
    # and the same topic; plans from older versions or other settings are redone
    current = (bool(stored.get("visual_theme")) and stored.get("selection_v") == selection.SELECTION_V
               and len(stored.get("main_reels", [])) == planner.N_MAIN and stored.get("reel_settings") == rs
               and (stored.get("topic") or "") == topic)
    if stored and current and not force:
        return stored, t["id"]
    segs = _segments(app, t["id"])
    duration = max((proj["video"] or {}).get("duration") or 0, segs[-1]["end"] if segs else 0)
    full = " ".join(x["text"] for x in segs)
    ctxinfo = app.memory.prompt_context(jc.project_id, full[:2000])
    jc.log("Planning content with the local LLM")
    try:
        with app.model_lock:
            plan = planner.create_plan(app.services.llm, jc.cfg, segs, duration, ctxinfo, log=jc.log, topic=topic)
    except planner.PlanError as e:                  # the plan failing must never fail the job: build one from the transcript
        jc.log(f"The AI plan failed ({e}). Using a basic plan built from the transcript's sentences instead; "
               "re-run with force to try the AI plan again.", "WARNING")
        plan = planfallback.fallback_plan(segs, duration, bool(jc.cfg.get("hook.one_word", False)), rs, topic)
    for note in plan.get("notes", []):
        jc.log(note, "WARNING")
    with app.db.session() as s:
        tr = s.get(Transcript, t["id"])
        tr.plan_json = json.dumps(plan, ensure_ascii=False)
        tr.summary = plan["summary"]
        tr.themes_json = json.dumps(plan["themes"], ensure_ascii=False)
        tr.quotes_json = json.dumps(plan["quotes"], ensure_ascii=False)
    app.memory.index_transcript(t["id"])
    return plan, t["id"]


def _brand_for_render(app, jc):
    b = brand_svc.get_brand(app, jc.project_id)
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
            "use_gpu": cfg.get("gpu.mode", "auto") != "cpu" and media.nvenc_available()}


def _excerpt(segs, a, b) -> str:
    return " ".join(x["text"] for x in segs if x["end"] > a and x["start"] < b)


def _brand_for_design(brand: dict, app=None, ws=None, theme=None, topic: str = "") -> dict:
    """Brand fields for the renderers, plus the render engine and (optionally) an AI-generated background."""
    bd = {"name": brand["name"], "tagline": brand["tagline"], "colors": brand["colors"],
          "fonts": brand["fonts"], "logo": brand["logo"]}
    if app is not None:
        bd["engine"] = app.config.get("design.engine", "html")
        if theme and ws is not None and app.config.get("design.ai_background", False):
            bd["background"] = _ai_background(app, Path(ws), theme, topic)
    return bd


def _ai_background(app, ws: Path, theme: str, topic: str = "") -> str | None:
    """One AI background per project and theme (cached on disk). None -> the built-in themed background."""
    out = ws / "backgrounds" / f"{theme}.png"
    if out.exists():
        return str(out)
    gen = getattr(app.services, "imagegen", None)
    if gen is None:
        return None
    failed = app.__dict__.setdefault("_bg_failed", {})
    if time.time() - failed.get(str(out), 0) < 900:          # do not reload a model that just failed, for 15 min
        return None
    try:
        from ruha.ai import imagegen
        with app.model_lock:                                  # one heavy GPU model at a time
            gen.generate(imagegen.prompt_for(theme, topic), out, app.config.all())
        return str(out)
    except Exception as e:
        failed[str(out)] = time.time()
        log.warning("AI background unavailable (%s); using the built-in themed background", e)
        return None


# ----------------------------- themes, language, cards ------------------------
LANG_CODES = {"English": "en", "Hindi": "hi", "Telugu": "te", "Tamil": "ta", "Kannada": "kn", "Malayalam": "ml",
              "Marathi": "mr", "Bengali": "bn", "Gujarati": "gu", "Punjabi": "pa", "Urdu": "ur", "Odia": "or",
              "Assamese": "as", "Nepali": "ne", "Sanskrit": "sa"}


def _theme_for(app, jc, plan) -> str | None:
    if not jc.cfg.get("design.theme_backgrounds", True):
        return None
    return plan.get("visual_theme") or themes.resolve(None, plan.get("main_theme", "") + " " + plan.get("summary", ""))


def _caption_template(jc, ws) -> str:
    """The caption template for this project's reels: the one chosen for the project, else the Settings default."""
    from ruha.services.subtitles import DEFAULT_TEMPLATE, TEMPLATES
    t = instructions.load(ws)["caption_template"] or str(jc.cfg.get("subtitle.template") or DEFAULT_TEMPLATE)
    return t if t in TEMPLATES else DEFAULT_TEMPLATE


def _caption_style(jc, ws, style: dict, size: tuple, fid: str, brand: dict, *, words=None, key_words=None) -> dict:
    """The caption style handed to the renderer: the saved look plus the template, the speaker's word times, the AI's key
    words, the brand colour and the platform safe area of this video format."""
    out = {**style, "caption_template": _caption_template(jc, ws), "safe": formats.video_safe(fid, size),
           "brand_color": ((brand or {}).get("colors") or {}).get("primary")}
    if words:
        out["words"] = words
    if key_words:
        out["key_words"] = list(key_words)
    return out


def _theme_accent(theme) -> str:
    return (themes.THEMES.get(theme) or themes.THEMES[themes.DEFAULT])["accent"]


def _translate_lines(app, jc, lines: list, language: str) -> list:
    """Translate subtitle lines with the local LLM; any batch that fails keeps the original text."""
    out = list(lines)
    for i in range(0, len(lines), 20):
        batch = lines[i:i + 20]
        prompt = (f"Translate each of these subtitle lines into {language}. Keep the meaning, keep them short and "
                  "natural, do not add or remove lines. Return ONLY JSON like {\"lines\":[\"...\"]} with exactly "
                  f"{len(batch)} items.\n" + json.dumps(batch, ensure_ascii=False))
        try:
            with app.model_lock:
                res = app.services.llm.generate_json(prompt, jc.cfg)
            got = [str(x).strip() for x in (res.get("lines") or [])]
            if len(got) == len(batch) and all(got):
                out[i:i + 20] = got
            else:
                jc.log(f"Subtitle translation to {language} returned {len(got)} of {len(batch)} lines; kept original", "WARNING")
        except Exception as e:
            jc.log(f"Subtitle translation to {language} failed ({e}); kept original text", "WARNING")
    return out


def _localized_cues(app, jc, segs, start, end, source_lang, style) -> tuple:
    """Subtitle cues for [start, end] in the requested language (default English). Returns (cues, language)."""
    language = jc.params.get("language") or jc.cfg.get("subtitle.language") or "English"
    cues = build_cues(segs, start, end, int(style.get("max_chars_per_line", 24)))
    code = LANG_CODES.get(language, "")
    if cues and code and (source_lang or "").lower()[:2] != code:
        texts = _translate_lines(app, jc, [c[2].replace("\n", " ") for c in cues], language)
        cues = [(a, b, t) for (a, b, _), t in zip(cues, texts)]
    return cues, language


def _card_size(jc) -> tuple:
    return int(jc.cfg.get("render.width", 1080)), int(jc.cfg.get("render.height", 1920))


def _reel_cards(app, jc, ws, slot, clip, plan, theme, brand, version=1, clip_text=None, size=None):
    """Render the opening hook image and the closing image for a reel (None when disabled)."""
    if not theme:
        return None, None
    bd = _brand_for_design(brand, app, ws, theme, plan.get("main_theme", ""))
    verse = (plan.get("poster") or {}).get("subheadline", "")
    op = cl = None
    if jc.cfg.get("reel.hook_card", True):
        # a 3-8 word phrase from the reel's own words (old stored one-word hooks are upgraded); the single bold
        # word only when the hook.one_word setting is on. A hook re-rendered from storage (no clip text) is kept as written.
        if jc.cfg.get("hook.one_word", False) or clip_text is not None:
            word = hooks.ensure_hook(clip.get("hook", ""), clip_text or "", bool(jc.cfg.get("hook.one_word", False)), theme,
                                     f"{plan.get('main_theme', '')} {clip.get('reason', '')}")
        else:
            word = clip.get("hook", "")
        op = render_card(ws / "cards" / f"{slot}_open_v{version}.png", word, theme, bd, "open", verse,
                         size=size or _card_size(jc))
    if jc.cfg.get("reel.closing_card", True):
        cl = render_card(ws / "cards" / f"{slot}_close_v{version}.png", "", theme, bd, "close", verse,
                         brand_svc.closing_text(brand, clip.get("closing") or "Follow for more"), size=size or _card_size(jc))
    return op, cl


def _cutaways(app, jc, ws, slot, version, segs, start, end, theme, size=None) -> list:
    """Animated example/transition scenes cut into the teaching of a MAIN reel (not shorts). [] when off or too short."""
    if not theme or not slot.startswith("main_") or not jc.cfg.get("reel.cutaways", True):
        return []
    from ruha.services import cutaways as cw
    out = []
    accent = _theme_accent(theme)
    for i, c in enumerate(cw.pick(segs, start, end, float(jc.cfg.get("reel.cutaway_seconds", cw.DEFAULT_SECONDS)),
                                  int(jc.cfg.get("reel.cutaway_max", cw.MAX_CUTAWAYS))), 1):
        bg = None                                   # the one cached AI picture per theme, never a new generation per cutaway
        if app.config.get("design.ai_background", False):
            bg = _ai_background(app, ws, theme, "")
        img = cw.scene_image(ws / "cutaways" / f"{slot}_v{version}_{i}.png", theme, c["text"], size or _card_size(jc), accent, bg)
        out.append({**c, "image": str(img)})
    return out


def _render_formats(app, jc, ws, slot, version, v, clip, segs, cues, plan, theme, brand_data, extras, args, clip_text=None):
    """Render one reel in every format chosen in Settings. The first format is the reel's main file; the others are saved
    beside it as variants. The speaker's face is tracked once; each format gets its own crop of it (blur fill where there is
    no face), its own cards and cutaways, and captions scaled to its size. Audio is measured once (-14 LUFS)."""
    start, end = clip["start"], clip["end"]
    fmts = reelvideo.chosen(jc.cfg)
    sw, sh = int(v.get("width") or 0), int(v.get("height") or 0)
    track = reelvideo.face_track(jc.cfg, v["path"], start, end, (sw, sh), jc.log, jc.cancelled) if sw and sh else None
    loud = media.loudness_filter(v["path"], start, end - start)
    res = {"variants": {}, "primary": None, "open": None, "close": None, "cutaways": [], "reframe": None, "format": fmts[0]["id"]}
    for f in fmts:
        w, h = f["size"]
        op, cl = _reel_cards(app, jc, ws, slot + f["tag"], clip, plan, theme, brand_data, version, clip_text, size=f["size"])
        cuts = _cutaways(app, jc, ws, slot + f["tag"], version, segs, start, end, theme, size=f["size"])
        rf = reframe.plan(track, sw, sh, w, h, end - start) if sw and sh else None
        path = ws / "reels" / (f"{slot}_v{version}.mp4" if f["primary"] else f"{slot}_{f['file']}_v{version}.mp4")
        look = reelvideo.style_for(args["subtitle_style"], f["size"], fmts[0]["size"])
        kw = {**args, "width": w, "height": h,
              "subtitle_style": _caption_style(jc, ws, look, f["size"], f["id"], brand_data,
                                               words=subtitles.clip_words(segs, start, end), key_words=clip.get("key_words"))}
        media.render_reel(v["path"], start, end, path, segments=segs, cues=cues, open_card=op, close_card=cl,
                          accent=_theme_accent(theme), cutaways=cuts, reframe=rf, audio_filter=loud, **kw, **extras)
        info = {"width": w, "height": h, "file": str(path), "size_bytes": path.stat().st_size,
                "reframe": rf.summary() if rf else {"mode": "center", "backend": None}}
        if f["primary"]:
            res.update(primary=path, open=op, close=cl, cutaways=cuts, reframe=info["reframe"])
        else:
            res["variants"][f["id"]] = info
    return res


# ----------------------------- reel from uploaded images -----------------------
def h_image_reel(app, jc):
    """Make a reel from pictures uploaded to the project: hook card, zoomed images with captions, closing card."""
    with app.db.session() as s:
        p = s.get(Project, jc.project_id)
        if p is None:
            raise LookupError("Project no longer exists")
        ws, pname = Path(p.workspace_dir), p.name
    img_dir = ws / "images"
    names = list(jc.params.get("images") or [])
    if not names and img_dir.exists():
        names = sorted(f.name for f in img_dir.glob("*") if f.is_file())
    files = [img_dir / n for n in names if Path(str(n)).name == str(n) and (img_dir / n).is_file()]   # no path tricks
    if not files:
        raise RuntimeError("Upload at least one image first")
    seconds = max(1.5, min(12.0, float(jc.params.get("seconds_per_image", 3.5))))
    hook = (jc.params.get("hook") or "").strip() or pname
    closing = (jc.params.get("closing") or "").strip() or "Follow for more"
    captions = [str(c).strip() for c in (jc.params.get("captions") or [])]
    language = jc.params.get("language") or jc.cfg.get("subtitle.language") or "English"
    verse = (jc.params.get("verse") or "").strip()
    t = _latest_transcript(app, jc.project_id)
    plan = (t or {}).get("plan") or {}
    theme = jc.params.get("theme")
    if theme not in themes.THEMES:
        theme = plan.get("visual_theme") or themes.resolve(None, f"{hook} {' '.join(captions)}")
    if language != "English" and any(captions):
        shown = [c for c in captions if c]
        tr = iter(_translate_lines(app, jc, shown, language))
        captions = [next(tr) if c else "" for c in captions]
    cues = [(i * seconds + 0.2, (i + 1) * seconds - 0.2, c) for i, c in enumerate(captions[:len(files)]) if c]
    brand = brand_svc.get_brand(app, jc.project_id)
    if not (jc.params.get("closing") or "").strip():
        closing = brand_svc.closing_text(brand, closing)          # the project's own closing line, if uploaded
    img_caption, img_tags = brand_svc.with_texts(brand, jc.params.get("caption", ""), list(plan.get("hashtags", []))[:8])
    args = _render_args(app, jc)
    bd = _brand_for_design(brand, app, ws, theme, hook)
    size = _card_size(jc)
    n = 1
    with app.db.session() as s:
        n = s.query(GeneratedAsset).filter_by(project_id=jc.project_id, kind="image_reel").count() + 1
    slot = f"image_{n}"
    jc.progress(0.1, "Designing hook and closing images", force=True)
    op = render_card(ws / "cards" / f"{slot}_open.png", hook, theme, bd, "open", verse, size=size) \
        if jc.cfg.get("reel.hook_card", True) else None
    cl = render_card(ws / "cards" / f"{slot}_close.png", "", theme, bd, "close", verse, closing, size=size) \
        if jc.cfg.get("reel.closing_card", True) else None
    out = ws / "reels" / f"{slot}_v1.mp4"
    jc.progress(0.25, "Rendering reel from images", force=True)
    wm = brand["watermark"] if jc.cfg.get("render.apply_watermark", True) else None
    media.render_image_reel(files, out, seconds_per_image=seconds, width=args["width"], height=args["height"],
                            crf=args["crf"], preset=args["preset"], cues=cues, accent=_theme_accent(theme),
                            subtitle_style=_caption_style(jc, ws, args["subtitle_style"], (args["width"], args["height"]),
                                                          formats.format_for_size((args["width"], args["height"])), brand),
                            open_card=op, close_card=cl, watermark=wm,
                            music=jc.cfg.get("reel.music_path") or None)
    srt = cues_to_srt(cues, ws / "subtitles" / f"{slot}_v1.srt")
    thumb = media.make_thumbnail(out, ws / "thumbs" / f"{slot}.jpg", at=min(1.0, seconds / 2))
    with app.db.session() as s:
        sa = asset_svc.register(app, s, jc.project_id, "subtitle", srt, slot=f"{slot}_srt",
                                title=f"Captions - {slot}", job_id=jc.job_id, meta={"language": language})
        a = asset_svc.register(app, s, jc.project_id, "image_reel", out, slot=slot, title=hook[:200],
                               description=f"Reel made from {len(files)} image(s)", caption=img_caption,
                               hashtags=img_tags, job_id=jc.job_id, preview_path=thumb,
                               meta={"images": [f.name for f in files], "seconds_per_image": seconds,
                                     "visual_theme": theme, "language": language, "closing": closing,
                                     "duration": round(seconds * len(files), 2), "width": args["width"],
                                     "height": args["height"]})
        s.add(Subtitle(project_id=jc.project_id, asset_id=sa.id, language=language, path=str(srt)))
        aid = a.id
    try:
        app.memory.index_asset(aid)
    except Exception:
        pass
    jc.progress(1.0, "Image reel ready", force=True)
    return {"asset_id": aid, "slot": slot}


# ----------------------------- download from a link --------------------------
def h_download(app, jc):
    from ruha.ai import ytdl
    url = jc.params.get("url", "")
    with app.db.session() as s:
        if projects.current_video(s, jc.project_id):
            jc.log("Project already has a video - skipping download")
            return
    jc.progress(0.02, "Downloading video", force=True)
    tmp_dir = app.settings.tmp_dir / f"dl_{jc.job_id}"
    try:
        path, title = ytdl.download(url, tmp_dir, int(jc.cfg.get("download.max_height", 720)),
                                    progress=lambda f: jc.progress(0.02 + f * 0.9, "Downloading video"),
                                    log=lambda m: jc.log(m, "WARNING"))
    except ytdl.DownloadError as e:
        raise RuntimeError(str(e))
    jc.progress(0.95, "Checking the video", force=True)
    with app.db.session() as s:
        p = s.get(Project, jc.project_id)
        if p is None:
            raise LookupError("Project no longer exists")
        projects.attach_video(app, s, p, path, f"{title[:120]}{path.suffix}")
        if jc.params.get("rename"):
            p.name = title[:200]
    shutil.rmtree(tmp_dir, ignore_errors=True)
    jc.log(f"Downloaded '{title}'")
    if jc.params.get("process", True):
        app.queue.enqueue_pipeline(jc.project_id, language=jc.params.get("language"))
        jc.log("Queued transcription and content generation")


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
                wav, {**jc.cfg, **({"whisper.language": jc.params["transcribe_language"]} if jc.params.get("transcribe_language") else {})},
                progress=lambda f: jc.progress(0.08 + 0.72 * f, "Transcribing"), log=jc.log)
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
        s.bulk_save_objects([TranscriptSegment(transcript_id=t.id, idx=i, start=x["start"], end=x["end"], text=x["text"],
                                               words_json=json.dumps(x["words"], ensure_ascii=False) if x.get("words") else None)
                             for i, x in enumerate(segments)])
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
def _with_typed_reels(app, jc, plan, segs, ws, duration):
    """Put the reels the producer typed (exact times + comments) in front of the AI's plan.
    The AI only writes each typed reel's hook/caption; the text is cached so re-renders keep it."""
    inst = instructions.load(ws)
    if not inst["reels"].strip():
        return plan
    try:
        rs = selection.reel_settings(jc.cfg)
        entries, warnings = instructions.parse_reels(inst["reels"], duration, (rs["min_s"], rs["max_s"]))
    except instructions.InstructionError as e:
        raise RuntimeError(f"The reels instructions are not valid: {e}")
    for w in warnings:
        jc.log(w, "WARNING")
    gen, dirty = dict(inst["generated"]), []

    def describe(e):
        text = " ".join(s["text"] for s in segs if s["end"] > e["start"] and s["start"] < e["end"])
        key = instructions.key_for("reel", planner.REEL_PROMPT_V, e["start"], e["end"], e["note"], text)
        if key not in gen:
            gen[key] = planner.describe_reel(app.services.llm, jc.cfg, text, e["note"], plan, log=jc.log)
            dirty.append(key)
        return gen[key]
    with app.model_lock:
        out = instructions.apply_manual_reels(plan, entries, describe, short_count=rs["count"])
    if dirty:
        instructions.save(ws, inst["reels"], inst["posters"], generated=gen)
    jc.log(f"Using {len(entries)} reel(s) with the times you typed")
    return out


def _typed_reels_cover_all(ws, duration, rs=None) -> bool:
    """True when the producer typed at least as many main and short reels as the plan would make."""
    rs = rs or selection.reel_settings({})
    inst = instructions.load(ws)
    if not inst["reels"].strip():
        return False
    try:
        entries, _ = instructions.parse_reels(inst["reels"], duration, (rs["min_s"], rs["max_s"]))
    except instructions.InstructionError as e:         # fail now, not after minutes of planning
        raise RuntimeError(f"The reels instructions are not valid: {e}")
    return (sum(e["category"] == "main" for e in entries) >= planner.N_MAIN
            and sum(e["category"] == "short" for e in entries) >= rs["count"])


def _plan_without_ai(app, jc, t, segs, duration, ws=None):
    """The plan for a reel job whose reels were all typed: the stored plan if there is one, else a basic plan from
    the transcript. The AI content planner is not run, and nothing is stored (the poster/post jobs plan on their own)."""
    stored = t["plan"] or {}
    if stored.get("visual_theme"):
        return stored
    return planfallback.fallback_plan(segs, duration, bool(jc.cfg.get("hook.one_word", False)),
                                      selection.reel_settings(jc.cfg), instructions.load(ws)["topic"] if ws else "")


def h_reels(app, jc):
    proj = _load_project(app, jc.project_id)
    v = proj["video"]
    if not v or not v["path"].exists():
        raise RuntimeError("Source video file is missing")
    t0 = _latest_transcript(app, jc.project_id)
    if t0 is not None and _typed_reels_cover_all(proj["ws"], v.get("duration") or 0, selection.reel_settings(jc.cfg)):
        tid = t0["id"]
        segs = _segments(app, tid)
        jc.log("Every reel was typed by you: skipping the AI content plan")
        plan = _plan_without_ai(app, jc, t0, segs, max(v.get("duration") or 0, segs[-1]["end"] if segs else 0), proj["ws"])
    else:
        plan, tid = ensure_plan(app, jc)
        segs = _segments(app, tid)
    plan = _with_typed_reels(app, jc, plan, segs, proj["ws"], v.get("duration") or 0)
    brand_data, extras = _brand_for_render(app, jc)
    args = _render_args(app, jc)
    src_lang = (_latest_transcript(app, jc.project_id) or {}).get("language")
    force = bool(jc.params.get("force"))
    slots = [(f"main_{i}", "main", c) for i, c in enumerate(plan["main_reels"], 1)] + \
            [(f"short_{i}", "short", c) for i, c in enumerate(plan["short_reels"], 1)]
    with app.db.session() as s:
        existing = {a.slot for a in s.query(GeneratedAsset).filter_by(project_id=jc.project_id)
                    if a.slot and a.slot.startswith(("main_", "short_")) and a.kind in ("main_reel", "short_reel")}
    done, written = [], []                                      # captions written in this run (for the duplicate check)
    for n, (slot, cat, clip) in enumerate(slots):
        if slot in existing and not force:
            jc.log(f"{slot} already exists - skipped")
            continue
        jc.progress(n / len(slots), f"Rendering {slot}", force=True)
        out = proj["ws"] / "reels" / f"{slot}_v1.mp4"
        theme = _theme_for(app, jc, plan)
        cues, language = _localized_cues(app, jc, segs, clip["start"], clip["end"], src_lang, args["subtitle_style"])
        ctext = hooks.clip_text(segs, clip["start"], clip["end"])
        copy_info = None
        # the title, the Reel record and the opening card all use the same hook: 3-8 words from this reel's own words
        # (older stored plans with a one-word hook are upgraded here); one word only if hook.one_word is on
        clip = {**clip, "hook": hooks.ensure_hook(clip.get("hook", ""), ctext, bool(jc.cfg.get("hook.one_word", False)),
                                                  theme or "", f"{plan.get('main_theme', '')} {clip.get('reason', '')}")}
        if jc.cfg.get("reels.unique_copy", True) and not clip.get("manual"):
            clip, copy_info = _own_copy(app, jc, plan, clip, ctext, written)
        written.append(clip.get("caption", ""))
        rendered = _render_formats(app, jc, proj["ws"], slot, 1, v, clip, segs, cues, plan, theme, brand_data, extras, args, ctext)
        out, op, cl, cuts = rendered["primary"], rendered["open"], rendered["close"], rendered["cutaways"]
        srt = cues_to_srt(cues, proj["ws"] / "subtitles" / f"{slot}_v1.srt")
        thumb = _reel_thumbnail(jc, out, proj["ws"] / "thumbs" / f"{slot}.jpg", min(1.0, (clip["end"] - clip["start"]) / 2))
        with app.db.session() as s:
            if force:
                for old in s.query(GeneratedAsset).filter(GeneratedAsset.project_id == jc.project_id,
                                                          GeneratedAsset.slot.in_([slot, f"{slot}_srt"])).all():
                    asset_svc.delete_files(app, old)
                    s.delete(old)
                s.flush()
            sa = asset_svc.register(app, s, jc.project_id, "subtitle", srt, slot=f"{slot}_srt",
                                    title=f"Subtitles - {slot}", video_id=v["id"], job_id=jc.job_id,
                                    meta={"start": clip["start"], "end": clip["end"], "language": language})
            reel_caption, reel_tags = brand_svc.with_texts(brand_data, clip["caption"], clip["hashtags"])
            a = asset_svc.register(
                app, s, jc.project_id, "main_reel" if cat == "main" else "short_reel", out, slot=slot,
                title=clip["hook"][:200] or f"{plan['title']} - {slot}", description=clip["reason"],
                caption=reel_caption, hashtags=reel_tags, video_id=v["id"], job_id=jc.job_id,
                preview_path=thumb, meta={"start": clip["start"], "end": clip["end"],
                                          "duration": round(clip["end"] - clip["start"], 2),
                                          "width": args["width"], "height": args["height"],
                                          "visual_theme": theme, "closing": clip.get("closing", ""),
                                          "language": language, "cutaways": len(cuts), "has_hook_card": bool(op),
                                          "has_closing_card": bool(cl),
                                          "format": rendered["format"], "variants": rendered["variants"],
                                          "reframe": rendered["reframe"], "key_words": clip.get("key_words") or [],
                                          "caption_template": _caption_template(jc, proj["ws"]),
                                          # the Message score (0-100), what it is made of, the clip's kind and whether it
                                          # matches the "find a moment about" topic; typed reels are not scored
                                          "message_score": clip.get("score"), "score_parts": clip.get("score_parts"),
                                          "score_source": clip.get("score_source") or ("typed" if clip.get("manual") else None),
                                          "category": clip.get("category"),
                                          "topic": plan.get("topic") or "", "topic_hits": clip.get("topic_hits", 0.0),
                                          "typed": bool(clip.get("manual")), "copy": copy_info})
            s.add(Reel(asset_id=a.id, category=cat, start_s=clip["start"], end_s=clip["end"], hook=clip["hook"],
                       reason=clip["reason"], transcript_excerpt=_excerpt(segs, clip["start"], clip["end"]),
                       subtitle_asset_id=sa.id))
            s.add(Subtitle(project_id=jc.project_id, asset_id=sa.id, language=language, path=str(srt)))
            aid = a.id
        app.memory.index_asset(aid)
        done.append(slot)
    jc.progress(1.0, "Reels ready", force=True)
    return {"rendered": done, "skipped": [s_ for s_, *_ in slots if s_ not in done]}


def _reel_thumbnail(jc, video, dest, fallback_at: float):
    """A reel's thumbnail: its best frame (big face, eyes open, sharp); the old frame near the start when scoring is off or fails."""
    if jc.cfg.get("thumbnail.best_frame", True):
        try:
            info = media.probe(video)
            t = thumbs.best_time(video, 0.0, info["duration"], (info["width"], info["height"]))
            if t is not None:
                return media.make_thumbnail(video, dest, at=t)
        except Exception as e:                                   # a thumbnail never fails a reel
            jc.log(f"Could not pick the best frame ({type(e).__name__}: {e}); using the frame near the start", "WARNING")
    return media.make_thumbnail(video, dest, at=fallback_at)


def _own_copy(app, jc, plan, clip, clip_text, written):
    """The clip's own caption: written from THIS clip's transcript and the captions already written, and rejected when it is a
    near-duplicate of an earlier one (the memory's embedding check). Returns (clip with its caption, info for the asset)."""
    from ruha.ai.llm import LLMUnavailable
    try:
        recent = (app.memory.prompt_context(jc.project_id, clip_text[:1500]) or {}).get("recent_content", [])[:5]
    except Exception:
        recent = []
    try:
        with app.model_lock:
            cp, similar, tries = clipcopy.unique_copy(app.services.llm, jc.cfg, app.memory, clip_text, plan, written, recent,
                                                      clip.get("key_words"), log=jc.log, title=clip.get("hook", ""))
    except LLMUnavailable:
        raise                                                    # Ollama is down: temporary, the job retries
    except Exception as e:                                       # a bad answer keeps the planner's caption
        jc.log(f"Could not write this clip's own caption ({type(e).__name__}); keeping the planned one", "WARNING")
        return clip, {"own": False, "similar_to": []}
    return ({**clip, "caption": cp["caption"], "hashtags": cp["hashtags"] or clip.get("hashtags", [])},
            {"own": True, "tries": tries, "similar_to": similar})


# ----------------------------- posters & social -------------------------------
def _poster_file(ws, asset_slot, version):
    return ws / "posters" / f"{asset_slot}_v{version}.png"


def h_poster(app, jc):
    asset_id = jc.params.get("asset_id")
    if asset_id:
        rerender_graphic(app, asset_id, layout=jc.params.get("layout"))
        return {"asset_id": asset_id, "mode": "rerender"}
    plan, tid = ensure_plan(app, jc)
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
    brand = brand_svc.get_brand(app, jc.project_id)
    jc.progress(0.3, "Designing poster", force=True)
    p = plan["poster"]
    inst = instructions.load(proj["ws"])
    if inst["posters"].strip():                               # the producer's instructions for the poster
        note = inst["posters"].strip()
        key = instructions.key_for("poster", planner.POSTER_PROMPT_V, note, plan.get("main_theme", ""), p["headline"])
        if key not in inst["generated"]:
            with app.model_lock:
                full = " ".join(x["text"] for x in _segments(app, tid))
                inst["generated"][key] = planner.poster_from_note(app.services.llm, jc.cfg, plan, full, note, log=jc.log)
            instructions.save(proj["ws"], inst["reels"], inst["posters"], generated=inst["generated"])
        p = inst["generated"][key]
        jc.log("Poster text follows your instructions")
    out = _poster_file(proj["ws"], "poster", 1)
    size = tuple(brand["export"]["poster"])
    theme = _theme_for(app, jc, plan)
    # the small label is the clip's category (or its hook) from the plan, not a fixed word
    label = p.get("label") or plan.get("poster", {}).get("label") or quotes.poster_label(quotes.best_clip(plan), plan)
    render_graphic(out, p["headline"], p["quote"], p["subheadline"], label, "poster",
                   _brand_for_design(brand, app, proj["ws"], theme, plan.get("main_theme", "")), layout, size, theme=theme)
    with app.db.session() as s:
        a = asset_svc.register(app, s, jc.project_id, "poster", out, slot="poster", title=p["headline"][:200],
                               description=p["subheadline"], caption=p["quote"],
                               hashtags=brand_svc.with_texts(brand, "", plan["hashtags"][:8])[1],
                               job_id=jc.job_id, preview_path=out,
                               meta={"layout": layout, "template_id": tpl, "size": list(size), "visual_theme": theme,
                                     "label": label, "quote_clip": p.get("clip") or plan.get("poster", {}).get("clip")})
        s.add(Poster(asset_id=a.id, headline=p["headline"], subheadline=p["subheadline"], quote=p["quote"],
                     theme=p["theme"], template_id=tpl))
        aid = a.id
    app.memory.index_asset(aid)
    return {"asset_id": aid}


def _thumbnail_clip(app, jc, plan, segs, proj, duration):
    """(start, end, hook, category, key words) of the clip the sermon's thumbnails are made for: the typed main reel if the owner
    typed one, else the plan's main reel."""
    clip = (plan.get("main_reels") or [None])[0] or quotes.best_clip(plan)
    start, end = (clip["start"], clip["end"]) if clip else (0.0, min(60.0, duration))
    inst = instructions.load(proj["ws"])
    if inst["reels"].strip():
        try:
            entries, _ = instructions.parse_reels(inst["reels"], duration)
            mains = [e for e in entries if e["category"] == "main"]
            if mains:
                start, end = mains[0]["start"], mains[0]["end"]
                clip = {**(clip or {}), "hook": "", "category": None}
        except instructions.InstructionError:
            pass
    text = hooks.clip_text(segs, start, end)
    hook = thumbnail.thumbnail_hook((clip or {}).get("hook") or plan.get("poster", {}).get("headline", ""), text)
    return start, end, hook, (clip or {}).get("category") or "", (clip or {}).get("key_words") or []


def h_thumbnails(app, jc):
    """Designed 1280x720 thumbnails (up to three layouts) from the best frames of the main clip, its hook (3-5 words) and the brand kit.
    Also replaces the sermon's own thumbnail (the frame at 10 %) with the best frame found across the whole video."""
    plan, tid = ensure_plan(app, jc)
    proj = _load_project(app, jc.project_id)
    v = proj["video"]
    if not v or not v["path"].exists():
        raise RuntimeError("Source video file is missing")
    segs = _segments(app, tid)
    size = (int(v.get("width") or 0), int(v.get("height") or 0))
    duration = max(v.get("duration") or 0, segs[-1]["end"] if segs else 0)
    start, end, hook, category, keys = _thumbnail_clip(app, jc, plan, segs, proj, duration)
    n = max(1, min(3, int(jc.cfg.get("thumbnail.variations", 3))))
    with app.db.session() as s:
        have = {a.slot for a in s.query(GeneratedAsset).filter_by(project_id=jc.project_id, kind="thumbnail")}
    todo = [i for i in range(1, n + 1) if f"thumb_{i}" not in have or jc.params.get("force")]
    if not todo:
        jc.log("Thumbnails already exist - skipped")
        return {"skipped": True}
    picks = []
    if jc.cfg.get("thumbnail.best_frame", True) and all(size):
        try:
            jc.progress(0.1, "Choosing the best frames", force=True)
            picks = thumbs.best_frames(v["path"], start, end, size, n)
        except Exception as e:
            jc.log(f"Could not score the frames ({type(e).__name__}: {e}); using evenly spaced frames", "WARNING")
    times = [p.t for p in picks] or [start + (end - start) * f for f in (0.5, 0.3, 0.7)][:n]
    faces = [p.face for p in picks] + [None] * n
    while len(times) < n:
        times.append(times[len(times) % max(1, len(times))])
    brand = brand_svc.get_brand(app, jc.project_id)
    theme = _theme_for(app, jc, plan)
    bd = _brand_for_design(brand, app, proj["ws"], theme, plan.get("main_theme", ""))
    made = []
    for i in todo:
        jc.progress(0.3 + 0.6 * (i - 1) / n, f"Designing thumbnail {i}", force=True)
        t, face = times[i - 1], faces[i - 1]
        frame = thumbs.save_frame(v["path"], t, proj["ws"] / "thumbs" / f"thumb_src_{i}.jpg")
        variant = thumbnail.VARIANTS[i - 1]
        out = proj["ws"] / "thumbnails" / f"thumb_{i}_v1.jpg"
        if out.exists():
            out = proj["ws"] / "thumbnails" / f"thumb_{i}_v{int(time.time())}.jpg"
        thumbnail.design(out, frame, hook, bd, variant, face, category, keys)
        meta = {"variant": variant, "size": [1280, 720], "hook": hook, "category": category, "frame_time": round(t, 2),
                "clip": [start, end], "words": len(hook.split()),
                "frame": ({"face": round(picks[i - 1].face_score, 2), "eyes": picks[i - 1].eyes, "sharp": round(picks[i - 1].sharp, 2),
                           "score": round(picks[i - 1].score, 2)} if i - 1 < len(picks) else None)}
        with app.db.session() as s:
            for old in s.query(GeneratedAsset).filter_by(project_id=jc.project_id, kind="thumbnail", slot=f"thumb_{i}").all():
                asset_svc.delete_files(app, old)
                s.delete(old)
            s.flush()
            asset_svc.register(app, s, jc.project_id, "thumbnail", out, slot=f"thumb_{i}", title=hook[:200],
                               description=f"{variant} layout, frame at {t:.1f}s", video_id=v["id"], job_id=jc.job_id,
                               preview_path=out, meta=meta)
        made.append(f"thumb_{i}")
    if jc.cfg.get("thumbnail.best_frame", True) and all(size):
        try:                                                     # the sermon's own thumbnail: best frame across the whole video
            jc.progress(0.92, "Choosing the sermon's thumbnail", force=True)
            t = thumbs.best_time(v["path"], 0.0, duration, size)
            if t is not None:
                th = media.make_thumbnail(v["path"], proj["ws"] / "thumbs" / f"video_{v['id']}.jpg", at=t)
                with app.db.session() as s:
                    sv = s.get(SourceVideo, v["id"])
                    if sv is not None:
                        sv.thumbnail_path = str(th)
        except Exception as e:
            jc.log(f"Could not update the sermon thumbnail ({type(e).__name__}: {e})", "WARNING")
    return {"created": made, "hook": hook}


def h_carousel(app, jc):
    """The sermon's main points as an Instagram carousel (5-7 slides: cover, points, closing) in the brand kit."""
    plan, tid = ensure_plan(app, jc)
    proj = _load_project(app, jc.project_id)
    slides = carousel.assemble(plan, jc.cfg.get("carousel.slides", carousel.DEFAULT_SLIDES))
    if not slides:
        jc.log("The sermon gave no usable main points for a carousel", "WARNING")
        return {"skipped": True, "slides": 0}
    with app.db.session() as s:
        existing = {a.slot for a in s.query(GeneratedAsset).filter_by(project_id=jc.project_id, kind="carousel")}
    if existing and not jc.params.get("force"):
        jc.log("The carousel already exists - skipped")
        return {"skipped": True}
    brand = brand_svc.get_brand(app, jc.project_id)
    theme = _theme_for(app, jc, plan)
    bd = _brand_for_design(brand, app, proj["ws"], theme, plan.get("main_theme", ""))
    size = tuple(brand["export"]["social"])
    keep = {f"carousel_{sl['index']}" for sl in slides}
    with app.db.session() as s:                                  # slides of an earlier, longer carousel are removed
        for old in s.query(GeneratedAsset).filter_by(project_id=jc.project_id, kind="carousel").all():
            if old.slot not in keep:
                asset_svc.delete_files(app, old)
                s.delete(old)
    made = []
    for sl in slides:
        i = sl["index"]
        jc.progress((i - 1) / len(slides), f"Designing slide {i} of {len(slides)}", force=True)
        with app.db.session() as s:
            layout, tpl = brand_svc.layout_for(s, "social", i - 1)
        out = proj["ws"] / "carousel" / f"carousel_{i}_v1.png"
        render_graphic(out, sl["headline"], sl["body"], sl["reference"], sl["label"], "social", bd, layout, size, theme=theme)
        with app.db.session() as s:
            for old in s.query(GeneratedAsset).filter_by(project_id=jc.project_id, kind="carousel", slot=f"carousel_{i}").all():
                asset_svc.delete_files(app, old)
                s.delete(old)
            s.flush()
            asset_svc.register(app, s, jc.project_id, "carousel", out, slot=f"carousel_{i}", title=sl["headline"][:200],
                               description=sl["body"], job_id=jc.job_id, preview_path=out,
                               meta={"slide": i, "total": len(slides), "slide_kind": sl["kind"], "layout": layout,
                                     "template_id": tpl, "size": list(size), "visual_theme": theme})
        made.append(f"carousel_{i}")
    return {"slides": len(slides), "created": made}


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
    brand = brand_svc.get_brand(app, jc.project_id)
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
        theme = _theme_for(app, jc, plan)
        render_graphic(out, post["headline"], post["body"], post["reference"], "Encouragement", "social",
                       _brand_for_design(brand, app, proj["ws"], theme, plan.get("main_theme", "")), layout, size, theme=theme)
        post_caption, post_tags = brand_svc.with_texts(brand, post["caption"], plan["hashtags"][:8])
        with app.db.session() as s:
            a = asset_svc.register(app, s, jc.project_id, "social_post", out, slot=slot, title=post["headline"][:200],
                                   caption=post_caption, hashtags=post_tags, job_id=jc.job_id,
                                   preview_path=out, meta={"layout": layout, "template_id": tpl, "size": list(size),
                                                           "similar_to": similar, "visual_theme": theme})
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
    with app.db.session() as s0:
        brand = brand_svc.get_brand(app, s0.query(GeneratedAsset.project_id).filter_by(id=asset_id).scalar())
    with app.db.session() as s:
        a = s.get(GeneratedAsset, asset_id)
        if not a or a.kind not in ("poster", "social_post"):
            raise LookupError("Not a poster or social post")
        meta = dict(a.meta)
        ws = Path(a.file_path).parent.parent
        if a.kind == "poster":
            r = s.query(Poster).filter_by(asset_id=a.id).first()
            head, body, ref, label, kind = r.headline, r.quote, r.subheadline, meta.get("label") or "Highlight", "poster"
            folder = "posters"
        else:
            r = s.query(SocialPost).filter_by(asset_id=a.id).first()
            head, body, ref, label, kind = r.headline, r.body, r.reference, "Encouragement", "social"
            folder = "social"
        layout = layout or meta.get("layout", "classic")
        size = tuple(meta.get("size") or brand["export"][kind])
        out = ws / folder / f"{a.slot}_v{a.version + 1}.png"
        theme = meta.get("visual_theme")
        if theme is None and app.config.get("design.theme_backgrounds", True):
            theme = themes.resolve(None, f"{head} {body}")
        render_graphic(out, head, body, ref, label, kind, _brand_for_design(brand, app, ws, theme, head), layout, size,
                       theme=theme)
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
    brand_data, extras = _brand_for_render(app, jc)
    args = _render_args(app, jc)
    jc.progress(0.1, "Rendering reel", force=True)
    out = proj["ws"] / "reels" / f"{slot}_v{version + 1}.mp4"
    plan = (t or {}).get("plan") or {}
    with app.db.session() as s:
        meta0 = dict(s.get(GeneratedAsset, asset_id).meta)
        rr = s.query(Reel).filter_by(asset_id=asset_id).first()
        hook0 = rr.hook if rr else ""
    theme = _theme_for(app, jc, plan) if plan else None
    clip = {"hook": hook0, "closing": meta0.get("closing", ""), "key_words": meta0.get("key_words") or []}
    cues, language = _localized_cues(app, jc, segs, start, end, (t or {}).get("language"), args["subtitle_style"])
    rendered = _render_formats(app, jc, proj["ws"], slot, version + 1, v, {**clip, "start": start, "end": end}, segs, cues,
                               plan, theme, brand_data, extras, args)
    out, cuts = rendered["primary"], rendered["cutaways"]
    srt = cues_to_srt(cues, proj["ws"] / "subtitles" / f"{slot}_v{version + 1}.srt")
    thumb = _reel_thumbnail(jc, out, proj["ws"] / "thumbs" / f"{slot}_v{version + 1}.jpg", 1.0)
    with app.db.session() as s:
        a = s.get(GeneratedAsset, asset_id)
        r = s.query(Reel).filter_by(asset_id=asset_id).first()
        r.start_s, r.end_s = start, end
        r.transcript_excerpt = _excerpt(segs, start, end)
        meta = dict(a.meta)
        old_variants = dict(meta.get("variants") or {})
        meta.update({"start": start, "end": end, "duration": round(end - start, 2), "cutaways": len(cuts),
                     "caption_template": _caption_template(jc, proj["ws"]),
                     "format": rendered["format"], "variants": rendered["variants"], "reframe": rendered["reframe"]})
        asset_svc.replace_file(app, s, a, out, "re-rendered", meta=meta)
        for old in old_variants.values():                       # the previous version's other-format files are replaced
            try:
                Path(old["file"]).unlink(missing_ok=True)
            except OSError:
                pass
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
    clone = bool(p.get("clone"))
    if clone and not (dub and p.get("clone_consent") is True):
        raise RuntimeError("Voice cloning needs dubbing and the confirmation that you have the speaker's permission")
    src_path, src_title = v["path"], None
    if p.get("asset_id") is not None:                  # dub/translate one finished reel instead of the whole sermon
        with app.db.session() as s:
            src_path, src_title = asset_svc.reel_source(s, jc.project_id, p["asset_id"])
    from ruha.ai.whisper import WHISPER_LANGUAGES
    hint = WHISPER_LANGUAGES.get(((_latest_transcript(app, jc.project_id) or {}).get("language") or "").lower())
    out_dir = proj["ws"] / "translations" / f"{re.sub(r'[^A-Za-z]', '', target)}_{jc.job_id}"
    with app.db.session() as s:
        tr = Translation(project_id=jc.project_id, video_id=v["id"], target_language=target, dubbed=dub,
                         voice=f'{p.get("voice", "Female")}{" (cloned)" if clone else ""}', status="running", job_id=jc.job_id)
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
                str(src_path), out_dir, source=p.get("source", "Auto-detect"), target=target,
                voice=p.get("voice", "Female"), dub=dub, keep_background=p.get("keep_background", True),
                burn=p.get("burn", False), resolution=p.get("resolution", "Original resolution"),
                clone=clone, source_hint=hint, progress=lambda f, d="": jc.progress(f, d or "Translating"), log=jc.log, cancel=jc.cancelled)
    except Exception:
        with app.db.session() as s:
            t = s.get(Translation, trid)
            t.status = "failed"
        raise
    with app.db.session() as s:
        t = s.get(Translation, trid)
        label = f"{target}{' dubbed' if dub else ''}" + (f" - {src_title}" if src_title else "")
        ids = {}
        for key, kind, slot, title in (("video", "translation_video", f"tr_{trid}_video", f"{label} video"),
                                       ("srt", "translation_subtitle", f"tr_{trid}_srt", f"{target} subtitles"),
                                       ("dub_audio", "audio", f"tr_{trid}_audio", f"{target} dub audio")):
            if res.get(key):
                a = asset_svc.register(app, s, jc.project_id, kind, Path(res[key]), slot=slot, title=title,
                                       description=res.get("status", ""), video_id=v["id"], job_id=jc.job_id,
                                       meta={"target": target, "dubbed": dub, "voice_cloned": clone,
                                             "ai_generated_voice": dub, "notes": res.get("notes", []),
                                             "source_asset_id": p.get("asset_id"),
                                             "engine": res.get("engine")})
                ids[key] = a.id
        if "srt" in ids:
            s.add(Subtitle(project_id=jc.project_id, asset_id=ids["srt"], language=target, path=res["srt"]))
        t.status = "completed"
        t.video_asset_id, t.srt_asset_id, t.audio_asset_id = ids.get("video"), ids.get("srt"), ids.get("dub_audio")
    return {"translation_id": trid, "assets": ids, "status": res.get("status"), "voice_cloned": clone,
            "notes": res.get("notes", [])}


def _job_type(app, job_id):
    from ruha.models import ProcessingJob
    with app.db.session() as s:
        return s.get(ProcessingJob, job_id).job_type


# ----------------------------- YT Video Making ---------------------------------
def h_yt_narrate(app, jc):
    from ruha.yt import service as yt
    p = jc.params
    with app.model_lock:
        try:
            app.services.llm.unload(jc.cfg)
        except Exception:
            pass
        secs = yt.narrate(app.settings, p["name"], p.get("lang", "English"), "Male",
                          progress=lambda f, d="": jc.progress(f, d or "Narrating"), cancel=jc.cancelled)
    return {"lang": p.get("lang"), "minutes": round(secs / 60, 2)}


def h_yt_video(app, jc):
    from ruha.yt import service as yt
    p = jc.params
    sv = yt.use(app.settings)
    jc.progress(0.02, "Preparing clips")
    cdir, made = sv.make_placeholder_clips(p["name"], p.get("size", "1920x1080"))   # only fills missing clips
    out, srt, mins = sv.assemble(p["name"], p.get("lang", "English"), p.get("size", "1920x1080"), 0.3,
                                 bool(p.get("burn")), cb=lambda f, d="": jc.progress(0.1 + 0.9 * f, d or "Building"))
    return {"video": out, "srt": srt, "minutes": round(mins, 2), "placeholder_clips_made": made}
