"""FFmpeg / FFprobe helpers: probing, thumbnails, audio extraction and reel rendering."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ruha.security import redact
from ruha.services.subtitles import build_srt

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


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


def nvenc_available() -> bool:
    if not ffmpeg_available():
        return False
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=20)
        return "h264_nvenc" in p.stdout
    except Exception:
        return False


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
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", str(crf), "-b:v", "0", "-pix_fmt", "yuv420p"]
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


def render_reel(source, start: float, end: float, out, *, segments=None, width=1080, height=1920,
                crf=23, preset="veryfast", burn_subtitles=True, subtitle_style=None,
                srt_path=None, watermark=None, intro=None, outro=None, use_gpu=False) -> Path:
    """Cut [start, end] from ``source`` into a vertical reel; never touches the source file."""
    _require()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.5, end - start)
    style = subtitle_style or {}
    tmp = Path(tempfile.mkdtemp(prefix="ruha_render_"))
    try:
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
              f"crop={width}:{height},setsar=1,fps=30")
        if burn_subtitles and segments:
            srt = tmp / "subs.srt"
            build_srt(segments, start, end, srt, max_chars=int(style.get("max_chars_per_line", 28)))
            if srt.stat().st_size > 0:
                vf += (f",subtitles=subs.srt:original_size={width}x{height}:"
                       f"force_style='{_force_style(style)}'")
        main_out = tmp / "main.mp4" if (intro or outro) else out
        cmd = ["ffmpeg", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(source)]
        has_wm = bool(watermark and Path(watermark).exists())
        if has_wm:
            cmd += ["-i", str(watermark)]
            fc = (f"[0:v]{vf}[base];[1:v]scale={int(width * 0.18)}:-1,format=rgba,"
                  f"colorchannelmixer=aa=0.75[wm];[base][wm]overlay=W-w-40:70:format=auto[v]")
            cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a?"]
        else:
            cmd += ["-vf", vf, "-map", "0:v:0", "-map", "0:a?"]
        cmd += ["-t", f"{duration:.3f}", *_encoder_args(crf, preset, use_gpu),
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(main_out)]
        try:
            run(cmd, cwd=str(tmp))
        except MediaError:
            if not use_gpu:
                raise
            cmd2 = [c for c in cmd]
            i = cmd2.index("h264_nvenc")
            cmd2[i - 1:i + 8] = _encoder_args(crf, preset, False)
            run(cmd2, cwd=str(tmp))
        if intro or outro:
            ordered = []
            if intro and Path(intro).exists():
                _norm_segment(intro, tmp / "intro.mp4", width, height, crf, preset, str(tmp))
                ordered.append(tmp / "intro.mp4")
            ordered.append(main_out)
            if outro and Path(outro).exists():
                _norm_segment(outro, tmp / "outro.mp4", width, height, crf, preset, str(tmp))
                ordered.append(tmp / "outro.mp4")
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
