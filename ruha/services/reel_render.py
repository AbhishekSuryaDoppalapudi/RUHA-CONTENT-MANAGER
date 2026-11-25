"""Render a Reel Editor reel in two separate steps, so an edit only redoes what it changed.

  1. CUT     every segment is cut from the sermon (cropped to the aspect ratio, a 50 ms audio fade at both ends so a join never
             clicks) and the pieces are joined. The joined file is cached under a name made from the segments + aspect +
             quality, so it is reused until a segment or the aspect ratio changes.
  2. FINISH  captions (with the owner's corrections), hook / end card, watermark, lower third, music and loudness are put on
             the cached cut. Changing any of them never cuts the sermon again and never touches the transcript.

``render`` returns which steps actually ran (``steps``), which is how the tests - and the owner - can see that."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ruha.creative import formats
from ruha.services import media, reel_editor, subtitles

CARD_SECONDS = (2.2, 2.6)
LOWER_THIRD_AT, LOWER_THIRD_FOR = 0.5, 4.0


def dims(aspect: str, mode: str) -> tuple:
    w, h = reel_editor.ASPECTS[aspect]
    if mode == "preview":                                   # a lighter draft: half size, still sharp enough to judge the cut
        return (w // 2) // 2 * 2, (h // 2) // 2 * 2
    return w, h


def fade_filter(duration: float) -> str:
    f = reel_editor.FADE
    return f"afade=t=in:st=0:d={f},afade=t=out:st={max(0.0, duration - f):.3f}:d={f}"


def cut_path_for(workdir: Path, reel_id: int, mode: str, key: str) -> Path:
    return Path(workdir) / f"reel_{reel_id}_cut_{mode}_{key}.mp4"


def make_cut(source, segments: list, out: Path, w: int, h: int, crf: int, preset: str, *, plan_fn=None, use_gpu=False,
             progress=None) -> Path:
    """Cut, crop and join the segments into ``out``. ``plan_fn(start, end, w, h)`` may return a face-following crop plan."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ruha_cut_"))
    try:
        parts = []
        for i, g in enumerate(segments):
            if progress:
                progress(i / max(1, len(segments)), f"Cutting segment {i + 1} of {len(segments)}")
            rf = plan_fn(g["start"], g["end"], w, h) if plan_fn else None
            p = tmp / f"seg_{i}.mp4"
            media.render_reel(source, g["start"], g["end"], p, width=w, height=h, crf=crf, preset=preset,
                              burn_subtitles=False, reframe=rf, use_gpu=use_gpu,
                              audio_filter=fade_filter(g["end"] - g["start"]))
            parts.append(p)
        if len(parts) == 1:
            shutil.move(str(parts[0]), str(out))
        else:
            (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
            media.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy",
                       "-movflags", "+faststart", str(out)], cwd=str(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def lower_third_png(out: Path, name: str, role: str, size: tuple, colour: str = "#FFD166") -> Path:
    """A transparent name plate for the speaker (top left, clear of the captions)."""
    from PIL import Image, ImageDraw
    from ruha.services.design import load_font, _hex
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fs = max(18, int(min(w, h) * 0.045))
    f1, f2 = load_font(fs, True, name), load_font(int(fs * 0.62), False, role or " ")
    tw = max(d.textlength(name, font=f1), d.textlength(role or "", font=f2))
    x, y, pad = int(w * 0.06), int(h * 0.10), int(fs * 0.5)
    box_h = int(fs * 1.25 + (fs * 0.85 if role else 0)) + pad
    d.rounded_rectangle((x, y, x + tw + 2 * pad + 8, y + box_h), radius=int(pad * 0.6), fill=(0, 0, 0, 170))
    d.rectangle((x, y, x + 8, y + box_h), fill=_hex(colour, 255))
    d.text((x + pad + 8, y + pad // 2), name, font=f1, fill=(255, 255, 255, 255))
    if role:
        d.text((x + pad + 8, y + pad // 2 + fs * 1.2), role, font=f2, fill=(230, 230, 230, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def caption_style(options: dict, base: dict, size: tuple, brand_colour=None) -> dict:
    c = options["caption"]
    st = {**(base or {}), "creative": True, "caption_template": c["template"], "size": int(c["size"]),
          "primary": c["color"], "brand_color": brand_colour}
    if c.get("font"):
        st["font"] = c["font"]
    try:
        st["safe"] = formats.video_safe(formats.format_for_size(size), size)
    except Exception:
        pass
    return st


def finish(cut: Path, out: Path, reel: dict, stored: list, w: int, h: int, crf: int, preset: str, *, base_style=None,
           watermark=None, open_card=None, close_card=None, brand_colour=None, workdir: Path | None = None) -> Path:
    """Captions, cards, watermark, loudness, then (only when asked) the lower third and music, on the cached cut."""
    opts = reel["options"]
    total = reel_editor.total_seconds(reel["segments"])
    vsegs = reel_editor.virtual_segments(reel, stored)
    style = caption_style(opts, base_style, (w, h), brand_colour)
    style["words"] = subtitles.clip_words(vsegs, 0.0, total + 1)
    cues = subtitles.build_cues(vsegs, 0.0, total + 1, int(style.get("max_chars_per_line", 24)))
    lt, music = opts["lower_third"], opts["music"]
    extra = bool((lt["enabled"] and lt["name"]) or (music["path"] and Path(music["path"]).exists()))
    stage = Path(out) if not extra else Path(out).with_name(Path(out).stem + "_stage.mp4")
    media.render_reel(cut, 0.0, total, stage, width=w, height=h, crf=crf, preset=preset,
                      burn_subtitles=bool(opts["caption"]["enabled"] and cues), subtitle_style=style, cues=cues,
                      accent=opts["caption"]["color"], watermark=watermark if opts["watermark"]["enabled"] else None,
                      open_card=open_card, close_card=close_card, card_seconds=CARD_SECONDS)
    if not extra:
        return Path(out)
    cmd, fc, n, last_v, last_a = ["ffmpeg", "-y", "-i", str(stage)], [], 1, "0:v", "0:a"
    if lt["enabled"] and lt["name"]:
        png = lower_third_png(Path(workdir or out.parent) / f"reel_{reel['id']}_lt.png", lt["name"], lt["role"], (w, h))
        t0 = LOWER_THIRD_AT + (CARD_SECONDS[0] if open_card else 0.0)
        cmd += ["-i", str(png)]
        fc.append(f"[0:v][{n}:v]overlay=0:0:enable='between(t,{t0:.2f},{t0 + LOWER_THIRD_FOR:.2f})':format=auto[lt]")
        last_v, n = "[lt]", n + 1
    if music["path"] and Path(music["path"]).exists():
        cmd += ["-stream_loop", "-1", "-i", str(music["path"])]
        fc.append(f"[{n}:a]volume={float(music['volume']):.2f}[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")
        last_a = "[a]"
    cmd += ["-filter_complex", ";".join(fc), "-map", last_v, "-map", last_a]
    cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(out)]
    try:
        media.run(cmd)
    finally:
        stage.unlink(missing_ok=True)
    return Path(out)


def render(*, source, video_id, reel: dict, stored: list, workdir, mode: str = "preview", crf=None, preset=None,
           plan_fn=None, base_style=None, watermark=None, open_card=None, close_card=None, brand_colour=None,
           use_gpu=False, progress=None, log=None) -> dict:
    """Render ``reel`` (a ``reel_editor.as_dict``). Returns {"path", "steps": ["cut"?, "finish"], "seconds"}."""
    if not reel["segments"]:
        raise reel_editor.EditorError("Add at least one part of the sermon to the reel first")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    w, h = dims(reel["aspect"], mode)
    crf = crf if crf is not None else (25 if mode == "preview" else 20)
    preset = preset or ("veryfast" if mode == "preview" else "veryfast")
    key = reel_editor.cut_key(video_id, reel["segments"], reel["aspect"], mode)
    cut = cut_path_for(workdir, reel["id"], mode, key)
    steps = []
    if not cut.exists():
        for old in workdir.glob(f"reel_{reel['id']}_cut_{mode}_*.mp4"):          # a cut of an older version of the reel
            old.unlink(missing_ok=True)
        make_cut(source, reel["segments"], cut, w, h, crf, preset, plan_fn=plan_fn, use_gpu=use_gpu, progress=progress)
        steps.append("cut")
    elif log:
        log("The joined cut is unchanged - reusing it (only captions, cards and extras are redone)")
    if progress:
        progress(0.75, "Adding captions and extras")
    out = workdir / (f"reel_{reel['id']}_preview.mp4" if mode == "preview" else f"reel_{reel['id']}_v{reel['version']}.mp4")
    finish(cut, out, reel, stored, w, h, crf, preset, base_style=base_style, watermark=watermark, open_card=open_card,
           close_card=close_card, brand_colour=brand_colour, workdir=workdir)
    steps.append("finish")
    return {"path": str(out), "steps": steps, "seconds": reel_editor.total_seconds(reel["segments"])}


def thumbnail_from(video: Path, at: float, out: Path, text: str = "", size: tuple | None = None) -> Path:
    """One frame of a reel (at ``at`` seconds), optionally with a line of text on it, as a PNG."""
    from PIL import Image, ImageDraw
    from ruha.services.design import load_font
    out.parent.mkdir(parents=True, exist_ok=True)
    media.run(["ffmpeg", "-y", "-ss", f"{max(0.0, at):.3f}", "-i", str(video), "-frames:v", "1", str(out)])
    if text.strip():
        img = Image.open(out).convert("RGB")
        w, h = img.size
        d = ImageDraw.Draw(img, "RGBA")
        f = load_font(max(20, int(min(w, h) * 0.075)), True, text)
        words, lines, cur = text.split(), [], ""
        for x in words:
            t = (cur + " " + x).strip()
            if d.textlength(t, font=f) <= w * 0.86 or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = x
        lines.append(cur)
        lh = int(f.size * 1.25) if hasattr(f, "size") else 40
        y0 = int(h * 0.72) - lh * len(lines) // 2
        d.rectangle((0, y0 - 14, w, y0 + lh * len(lines) + 14), fill=(0, 0, 0, 150))
        for i, ln in enumerate(lines):
            d.text(((w - d.textlength(ln, font=f)) / 2, y0 + i * lh), ln, font=f, fill=(255, 255, 255, 255))
        img.save(out)
    return out
