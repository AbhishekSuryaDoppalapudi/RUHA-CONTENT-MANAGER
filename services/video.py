import subprocess, json, shutil
from pathlib import Path

def require_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg/FFprobe not found. Install FFmpeg and add its bin folder to PATH.")

def probe_video(path):
    require_ffmpeg()
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration:format_tags=title", "-of", "json", str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(p.stdout)
    return {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "title": data.get("format", {}).get("tags", {}).get("title", Path(path).stem)
    }

def extract_audio(video_path, audio_path):
    require_ffmpeg()
    Path(audio_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1",
           "-ar", "16000", "-c:a", "pcm_s16le", str(audio_path)]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

def render_clip(source, start, end, output, width=1080, height=1920):
    require_ffmpeg()
    duration = max(1.0, end - start)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    # Center-crop to a vertical canvas. Source audio is preserved.
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},setsar=1")
    cmd = ["ffmpeg", "-y", "-ss", str(max(0, start)), "-i", str(source),
           "-t", str(duration), "-vf", vf, "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
           "-b:a", "160k", "-movflags", "+faststart", str(output)]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

def write_srt(segments, start, end, path):
    def stamp(sec):
        sec = max(0, sec)
        h = int(sec // 3600); m = int((sec % 3600) // 60)
        s = int(sec % 60); ms = int((sec - int(sec)) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    selected = [s for s in segments if s["end"] > start and s["start"] < end]
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(selected, 1):
            a = max(0, seg["start"] - start)
            b = min(end, seg["end"]) - start
            f.write(f"{i}\n{stamp(a)} --> {stamp(b)}\n{seg['text'].strip()}\n\n")
