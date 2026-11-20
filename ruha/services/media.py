"""FFmpeg / FFprobe helpers: probing, thumbnails, audio extraction and reel rendering."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ruha.security import redact
from ruha.services.subtitles import build_ass, build_cues, build_srt, cues_to_srt, stage_fonts

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
log = logging.getLogger("ruha.media")


class MediaError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _require():
    if not ffmpeg_available():
        raise MediaError("FFmpeg/FFprobe not found. Install FFmpeg and add it to PATH.")


def run(cmd: list, cwd: str | None = None, timeout: int | None = None) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{Path(cmd[0]).name} timed out") from e
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-6:])
        raise MediaError(redact(f"{Path(cmd[0]).name} failed: {tail}"))
    return p


def sha256_file(path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def probe(path) -> dict:
    _require()
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(p.stdout or "{}")
    streams = data.get("streams", [])
    vids = [s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    if not vids:
        raise MediaError("File has no video stream - it is not a playable video.")
    v = vids[0]
    fps = 0.0
    try:
        n, d = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = float(n) / float(d) if float(d) else 0.0
    except (ValueError, ZeroDivisionError):
        pass
    dur = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "duration": dur, "width": int(v.get("width") or 0), "height": int(v.get("height") or 0),
        "fps": round(fps, 3), "video_codec": v.get("codec_name"),
        "audio_tracks": len(auds), "audio_codec": auds[0].get("codec_name") if auds else None,
        "size_bytes": int(data.get("format", {}).get("size") or 0),
    }


def make_thumbnail(video, out, at: float | None = None, width: int = 640) -> Path:
    _require()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if at is None:
        at = max(0.0, min(probe(video)["duration"] * 0.1, 30.0))
    run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "3", str(out)])
    return out


def extract_audio(video, wav) -> Path:
    _require()
    Path(wav).parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav)])
    return Path(wav)


_NVENC = {"ok": None, "at": 0.0}
_NVENC_TTL_S = 300.0


def nvenc_available() -> bool:
    """True only when an NVENC H.264 encode really works here (a 0.2 s test clip), not merely when FFmpeg was built with
    it - that is also true on a PC without an NVIDIA GPU. The answer is remembered for five minutes."""
    import time
    if _NVENC["ok"] is not None and time.time() - _NVENC["at"] < _NVENC_TTL_S:
        return bool(_NVENC["ok"])
    ok = False
    if ffmpeg_available():
        try:
            p = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                               creationflags=CREATE_NO_WINDOW, timeout=20)
            if "h264_nvenc" in p.stdout:
                t = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                                    "color=c=black:s=256x144:r=30:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
                                   capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=30)
                ok = t.returncode == 0
        except Exception:
            ok = False
    _NVENC["ok"], _NVENC["at"] = ok, time.time()
    return ok


def _hex_to_ass(color: str) -> str:
    c = (color or "#FFFFFF").lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    return f"&H00{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _force_style(style: dict) -> str:
    parts = {
        "FontName": style.get("font", "Arial"), "FontSize": int(style.get("size", 64)),
        "Bold": 1 if style.get("bold", True) else 0,
        "PrimaryColour": _hex_to_ass(style.get("primary", "#FFFFFF")),
        "OutlineColour": _hex_to_ass(style.get("outline_color", "#000000")),
        "BorderStyle": 1, "Outline": int(style.get("outline", 4)), "Shadow": 0,
        "Alignment": 2, "MarginV": int(style.get("margin_v", 260)),
    }
    return ",".join(f"{k}={v}" for k, v in parts.items())


def _encoder_args(crf: int, preset: str, use_gpu: bool) -> list:
    if use_gpu:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf), "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]


def _norm_segment(src, out, w, h, crf, preset, cwd) -> None:
    """Re-encode an intro/outro clip to the exact parameters used for reels so it can be concatenated."""
    info = probe(src)
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if info["audio_tracks"] == 0:
        cmd += ["-f", "lavfi", "-t", f"{max(info['duration'], 0.1):.3f}", "-i",
                "anullsrc=r=48000:cl=stereo"]
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
          f"setsar=1,fps=30,format=yuv420p")
    cmd += ["-vf", vf, "-map", "0:v:0", "-map", "0:a:0" if info["audio_tracks"] else "1:a:0",
            "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "160k",
            *_encoder_args(crf, preset, False), "-shortest", str(out)]
    run(cmd, cwd=cwd)


def _bed(seconds: float, impact: bool = False) -> str:
    """lavfi source for a soft synthesised ambient pad (no files needed), optionally with a low opening hit."""
    expr = ("0.10*sin(2*PI*110*t)+0.07*sin(2*PI*165*t)+0.05*sin(2*PI*220*t)*(1+0.3*sin(2*PI*0.3*t))"
            + ("+0.45*sin(2*PI*55*t)*exp(-5*t)" if impact else ""))
    return f"aevalsrc={expr}|{expr}:s=48000:d={seconds:.2f}"


def _bed_fades(seconds: float) -> str:
    return f"afade=t=in:d=0.4,afade=t=out:st={max(0.1, seconds - 0.8):.2f}:d=0.8"


def _card_video(png, out, seconds: float, w, h, crf, preset, cwd, fade_in: bool = True) -> None:
    """Turn a still image into a short clip (with a soft ambient bed) matching the reel's encoding so it can be joined."""
    vf = f"scale={w}:{h},setsar=1,fps=30,format=yuv420p"
    if fade_in:
        vf += ",fade=t=in:st=0:d=0.35"
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-t", f"{seconds:.2f}", "-i", str(png),
           "-f", "lavfi", "-t", f"{seconds:.2f}", "-i", _bed(seconds, impact=fade_in),
           "-vf", vf, "-af", _bed_fades(seconds), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
           *_encoder_args(crf, preset, False), "-shortest", str(out)]
    run(cmd, cwd=cwd)


LOUDNESS_I, LOUDNESS_TP, LOUDNESS_LRA = -14.0, -1.5, 11.0     # integrated LUFS / true peak dBTP / range: what social platforms expect


def measure_loudness(source, start: float, duration: float, timeout: int = 600) -> dict | None:
    """First pass of two-pass loudness normalisation: measure [start, start+duration] of ``source``.
    None when there is no audio, the audio is silent, or FFmpeg cannot do it (the caller then normalises in one pass)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.5, duration):.3f}",
           "-i", str(source), "-vn", "-af",
           f"loudnorm=I={LOUDNESS_I}:TP={LOUDNESS_TP}:LRA={LOUDNESS_LRA}:print_format=json", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr or "", re.S)
    if p.returncode != 0 or not m:
        return None
    try:
        d = json.loads(m.group(0))
        vals = {k: float(d[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError, TypeError):
        return None
    return vals if all(math.isfinite(v) for v in vals.values()) else None


def loudness_filter(source=None, start: float = 0.0, duration: float = 0.0, measured: dict | None = None) -> str:
    """The ``-af`` loudnorm filter at -14 LUFS. With a measurement of the clip it is the accurate two-pass form
    (linear gain, no pumping); without one, the single-pass form."""
    base = f"loudnorm=I={LOUDNESS_I}:TP={LOUDNESS_TP}:LRA={LOUDNESS_LRA}"
    if measured is None and source is not None:
        measured = measure_loudness(source, start, duration)
    if not measured:
        return base
    return (f"{base}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true")


def x_expr(keys: list) -> str:
    """FFmpeg expression for the crop's left edge over time: piecewise linear through ``keys`` [(t, x)], written as
    a sum of clipped ramps (no nesting, so a long camera path stays a short expression)."""
    x0 = keys[0][1]
    terms = [f"{x0:.1f}"]
    for (t0, a), (t1, b) in zip(keys, keys[1:]):
        if abs(b - a) >= 0.05:
            terms.append(f"({b - a:.2f})*clip((t-{t0:.3f})/{max(t1 - t0, 0.04):.3f},0,1)")
    return "+".join(terms)


def _enable_expr(windows: list) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)


def _blur_fill(src_label: str, out_label: str, width: int, height: int, tag: str) -> str:
    """Blur fill: the frame zoomed to cover the output and blurred, with the whole frame centred on top."""
    bw, bh = max(2, (width // 4) // 2 * 2), max(2, (height // 4) // 2 * 2)
    return (f"[{src_label}]split=2[{tag}a][{tag}b];"
            f"[{tag}a]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=8:2,"
            f"scale={width}:{height}[{tag}bg];"
            f"[{tag}b]scale={width}:{height}:force_original_aspect_ratio=decrease[{tag}fg];"
            f"[{tag}bg][{tag}fg]overlay=(W-w)/2:(H-h)/2[{out_label}]")


def reframe_graph(rf, width: int, height: int, out_label: str = "b0") -> str:
    """Filter graph from the input video ``[0:v]`` to ``[out_label]`` at width x height, 30 fps.

    ``rf`` is a ``reframe.Reframe`` (or None for the old centre crop)."""
    if rf is None or rf.mode in ("full", "center"):                # the old behaviour: fill the frame, centre crop
        return (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                f"setsar=1,fps=30[{out_label}]")
    cw, ch = rf.crop
    if rf.mode == "blur":                                          # the whole clip: full frame over a blurred copy
        return "[0:v]fps=30[rf0];" + _blur_fill("rf0", "rf1", width, height, "z") + f";[rf1]setsar=1[{out_label}]"
    cropped = f"crop={cw}:{ch}:x='{x_expr(rf.keys)}':y=0,scale={width}:{height}:flags=lanczos"
    if not rf.blur:
        return f"[0:v]fps=30,{cropped},setsar=1[{out_label}]"
    return (f"[0:v]fps=30,split=2[rfs][rfm];[rfs]{cropped}[trk];" + _blur_fill("rfm", "bf", width, height, "z")
            + f";[trk][bf]overlay=enable='{_enable_expr(rf.blur)}'[rfo];[rfo]setsar=1[{out_label}]")


def render_reel(source, start: float, end: float, out, *, segments=None, width=1080, height=1920,
                crf=23, preset="veryfast", burn_subtitles=True, subtitle_style=None,
                srt_path=None, watermark=None, intro=None, outro=None, use_gpu=False,
                cues=None, accent="#FFD166", open_card=None, close_card=None,
                card_seconds=(2.2, 2.6), cutaways=None, reframe=None, audio_filter=None) -> Path:
    """Cut [start, end] from ``source`` into a reel of width x height; never touches the source file.

    ``reframe`` (a ``reframe.Reframe``) makes the picture follow the speaker's face and blur-fill the stretches without
    one; None = the old centre crop. ``audio_filter`` = the loudnorm filter (default: measured here, -14 LUFS).
    ``cutaways`` = [{"at": seconds into the reel, "dur": seconds, "image": path}]: the picture crossfades to an animated
    (slow zoom) still for that window while the speaker's audio and the captions carry on.
    Encodes with NVENC when ``use_gpu``; if the GPU encode fails the reel is encoded again with libx264."""
    _require()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.5, end - start)
    style = subtitle_style or {}
    tmp = Path(tempfile.mkdtemp(prefix="ruha_render_"))
    try:
        sub_vf = ""
        if burn_subtitles and (cues or segments):
            use_cues = cues if cues is not None else build_cues(segments, start, end, int(style.get("max_chars_per_line", 28)))
            if use_cues and style.get("creative", True):
                build_ass(use_cues, tmp / "subs.ass", width, height, style, accent)
                sub_vf = "ass=subs.ass" + stage_fonts(tmp, use_cues)
            elif use_cues:
                cues_to_srt(use_cues, tmp / "subs.srt")
                sub_vf = (f"subtitles=subs.srt:original_size={width}x{height}:"
                          f"force_style='{_force_style(style)}'")
        cutaways = [c for c in (cutaways or []) if c.get("image") and Path(c["image"]).exists()
                    and 0 <= float(c["at"]) < duration - 0.5]
        main_out = tmp / "main.mp4" if (intro or outro or open_card or close_card) else out
        cmd = ["ffmpeg", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(source)]
        has_wm = bool(watermark and Path(watermark).exists())
        fc = reframe_graph(reframe, width, height, "b0")
        last, n_in = "b0", 1
        for i, c in enumerate(cutaways):
            d = min(float(c["dur"]), duration - float(c["at"]))
            fr, fd = max(2, int(round(d * 30))), min(0.35, d / 3)
            cmd += ["-i", str(c["image"])]
            z = "min(1+0.0011*on,1.16)" if i % 2 == 0 else "if(eq(on,0),1.16,max(1.16-0.0011*on,1.0))"
            fc += (f";[{n_in}:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                   f"crop={width * 2}:{height * 2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                   f"d={fr}:s={width}x{height}:fps=30,setsar=1,format=yuva420p,"
                   f"fade=t=in:st=0:d={fd:.2f}:alpha=1,fade=t=out:st={d - fd:.2f}:d={fd:.2f}:alpha=1,"
                   f"setpts=PTS+{float(c['at']):.3f}/TB[c{i}]"
                   f";[{last}][c{i}]overlay=eof_action=pass:format=auto[o{i}]")
            last, n_in = f"o{i}", n_in + 1
        if sub_vf:
            fc += f";[{last}]{sub_vf}[sb]"
            last = "sb"
        if has_wm:
            cmd += ["-i", str(watermark)]
            fc += (f";[{n_in}:v]scale={int(width * 0.18)}:-1,format=rgba,"
                   f"colorchannelmixer=aa=0.75[wm];[{last}][wm]overlay=W-w-40:70:format=auto[v]")
            last = "v"
        cmd += ["-filter_complex", fc, "-map", f"[{last}]", "-map", "0:a?"]
        af = audio_filter or loudness_filter(source, start, duration)

        def tail(enc: list) -> list:
            return [*enc, "-af", af, "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                    "-t", f"{duration:.3f}", "-movflags", "+faststart", str(main_out)]
        try:
            run(cmd + tail(_encoder_args(crf, preset, use_gpu)), cwd=str(tmp))
        except MediaError as e:
            if not use_gpu:
                raise
            log.warning("NVENC encode failed (%s); encoding this reel with libx264 instead", e)
            run(cmd + tail(_encoder_args(crf, preset, False)), cwd=str(tmp))
        if intro or outro or open_card or close_card:
            ordered = []
            if open_card and Path(open_card).exists():
                _card_video(open_card, tmp / "open.mp4", card_seconds[0], width, height, crf, preset, str(tmp))
                ordered.append(tmp / "open.mp4")
            if intro and Path(intro).exists():
                _norm_segment(intro, tmp / "intro.mp4", width, height, crf, preset, str(tmp))
                ordered.append(tmp / "intro.mp4")
            ordered.append(main_out)
            if outro and Path(outro).exists():
                _norm_segment(outro, tmp / "outro.mp4", width, height, crf, preset, str(tmp))
                ordered.append(tmp / "outro.mp4")
            if close_card and Path(close_card).exists():
                _card_video(close_card, tmp / "close.mp4", card_seconds[1], width, height, crf, preset, str(tmp))
                ordered.append(tmp / "close.mp4")
            lst = tmp / "list.txt"
            lst.write_text("".join(f"file '{p.name}'\n" for p in ordered), encoding="utf-8")
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy",
                 "-movflags", "+faststart", str(out)], cwd=str(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def file_size(path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def render_image_reel(images, out, *, seconds_per_image: float = 3.5, width=1080, height=1920, crf=23,
                      preset="veryfast", cues=None, accent="#FFD166", subtitle_style=None, open_card=None,
                      close_card=None, watermark=None, card_seconds=(2.2, 2.6), music=None) -> Path:
    """Slideshow reel from still images: slow zoom on each picture, creative captions, hook/closing cards."""
    _require()
    images = [Path(i) for i in images]
    if not images:
        raise MediaError("Choose at least one image")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    style = subtitle_style or {}
    fr = max(1, int(round(seconds_per_image * 30)))
    tmp = Path(tempfile.mkdtemp(prefix="ruha_imgreel_"))
    try:
        cmd = ["ffmpeg", "-y"]
        chains = []
        for i, img in enumerate(images):
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{seconds_per_image:.2f}", "-i", str(img)]
            zoom_in = i % 2 == 0
            z = "min(zoom+0.0009,1.14)" if zoom_in else "if(eq(on,0),1.14,max(zoom-0.0009,1.0))"
            chains.append(f"[{i}:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                          f"crop={width * 2}:{height * 2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                          f"d={fr}:s={width}x{height}:fps=30,setsar=1,format=yuv420p[v{i}]")
        n = len(images)
        total = seconds_per_image * n
        has_music = bool(music and Path(music).exists())
        if has_music:                                    # the user's own track, looped to the slideshow length
            cmd += ["-stream_loop", "-1", "-t", f"{total:.2f}", "-i", str(music)]
        else:
            cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", _bed(total)]
        fc = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat]"
        last = "cat"
        if cues:
            build_ass(cues, tmp / "subs.ass", width, height, style, accent)
            fc += f";[{last}]ass=subs.ass{stage_fonts(tmp, cues)}[sub]"
            last = "sub"
        has_wm = bool(watermark and Path(watermark).exists())
        if has_wm:
            cmd += ["-i", str(watermark)]
            fc += (f";[{n + 1}:v]scale={int(width * 0.18)}:-1,format=rgba,colorchannelmixer=aa=0.75[wm];"
                   f"[{last}][wm]overlay=W-w-40:70:format=auto[fin]")
            last = "fin"
        main_out = tmp / "main.mp4" if (open_card or close_card) else out
        afx = _bed_fades(total) if not has_music else f"volume=0.8,{_bed_fades(total)}"
        cmd += ["-filter_complex", fc, "-af", afx, "-map", f"[{last}]", "-map", f"{n}:a", "-t", f"{total:.2f}",
                *_encoder_args(crf, preset, False), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(main_out)]
        run(cmd, cwd=str(tmp))
        if open_card or close_card:
            ordered = []
            if open_card and Path(open_card).exists():
                _card_video(open_card, tmp / "open.mp4", card_seconds[0], width, height, crf, preset, str(tmp))
                ordered.append(tmp / "open.mp4")
            ordered.append(main_out)
            if close_card and Path(close_card).exists():
                _card_video(close_card, tmp / "close.mp4", card_seconds[1], width, height, crf, preset, str(tmp))
                ordered.append(tmp / "close.mp4")
            (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in ordered), encoding="utf-8")
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy",
                 "-movflags", "+faststart", str(out)], cwd=str(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
