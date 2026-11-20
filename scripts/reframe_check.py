"""Try speaker-aware reframing on a video, without the web app.

    .venv\\Scripts\\python.exe scripts\\reframe_check.py sermon.mp4 --start 600 --end 660
    .venv\\Scripts\\python.exe scripts\\reframe_check.py sermon.mp4 --start 600 --end 660 --formats 9:16 4:5 1:1 16:9
    .venv\\Scripts\\python.exe scripts\\reframe_check.py sermon.mp4 --walking-demo        (builds a walking-pastor test clip first)

For each format it finds the speaker's face (5 frames a second), plans the camera, renders the reel (NVENC when it works,
libx264 otherwise), measures the loudness of the result and prints a report: how often a face was found, how many seconds
used blur fill, how many camera moves, the loudness (target -14 LUFS), the encoder and the time taken. The reels are
written to scripts/reframe_out/ - open them and look at how the camera follows the speaker.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ruha.creative import formats  # noqa: E402
from ruha.services import media, reframe  # noqa: E402

OUT = Path(__file__).with_name("reframe_out")


def make_walking_demo(source, out, frame_at: float = 600.0, seconds: int = 12) -> Path:
    """A clip where the speaker WALKS ACROSS THE STAGE: his upper body, cut from one frame of ``source``, slides left to
    right (0-4 s), leaves the stage (4-7 s: nobody there), comes back and walks left again (7-12 s). Red bars mark the
    left and right edges of the frame so a blur-filled (whole frame) picture is easy to tell from a crop."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    person = out.with_suffix(".person.png")
    probe = media.probe(source)
    sw, sh = probe["width"], probe["height"]
    pw, ph = int(sw * 0.28) // 2 * 2, int(sh * 0.86) // 2 * 2
    px = int(sw * 0.34)                                                        # where he stands in the real frame
    media.run(["ffmpeg", "-y", "-ss", str(frame_at), "-i", str(source), "-frames:v", "1",
               "-vf", f"crop={pw}:{ph}:{px}:{int(sh * 0.08)}", str(person)])
    x = f"if(lt(t,4),{int(sw * 0.06)}+{sw * 0.13:.0f}*t,if(lt(t,9),{int(sw * 0.3)},{int(sw * 0.3)}-(t-9)*{sw * 0.055:.0f}))"
    fc = (f"[0:v]drawbox=x=0:y=0:w=40:h={sh}:color=red:t=fill,drawbox=x={sw - 40}:y=0:w=40:h={sh}:color=red:t=fill[bg];"
          f"[bg][1:v]overlay=x='{x}':y={int(sh * 0.08)}:enable='lt(t,4)+gte(t,7)'[v]")
    media.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x202830:s={sw}x{sh}:r=24:d={seconds}",
               "-loop", "1", "-t", str(seconds), "-i", str(person), "-f", "lavfi", "-i", f"sine=f=220:d={seconds}",
               "-filter_complex", fc, "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-shortest", str(out)])
    person.unlink(missing_ok=True)
    return out


def measure_output_lufs(path) -> float | None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, creationflags=media.CREATE_NO_WINDOW)
    m = re.findall(r"I:\s+(-?[\d.]+)\s+LUFS", p.stderr or "")
    return float(m[-1]) if m else None


def run_check(video, start, end, fmt_ids, base_width=1080, detector="auto", use_gpu=None, out_dir=OUT):
    video = Path(video)
    info = media.probe(video)
    sw, sh = info["width"], info["height"]
    end = min(end if end else info["duration"], info["duration"])
    duration = end - start
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gpu = media.nvenc_available() if use_gpu is None else use_gpu
    report = {"video": video.name, "source": f"{sw}x{sh}", "start": start, "end": end, "nvenc": media.nvenc_available(),
              "encoder": "h264_nvenc" if gpu else "libx264", "formats": []}
    t0 = time.time()
    track = reframe.detect_track(video, start, end, (sw, sh), detector)
    report["detector"] = {"used": track.backend, "available": track.available, "notes": track.notes,
                          "faces_found": round(track.coverage, 2), "samples": len(track.samples),
                          "seconds": round(time.time() - t0, 1), "status": reframe.detector_status(detector)}
    loud = media.loudness_filter(video, start, duration)
    for fid in fmt_ids:
        w, h = formats.video_size(fid, base_width)
        rf = reframe.plan(track, sw, sh, w, h, duration)
        out = out_dir / f"{video.stem}_{int(start)}-{int(end)}_{fid.replace(':', 'x')}.mp4"
        t1 = time.time()
        media.render_reel(video, start, end, out, width=w, height=h, burn_subtitles=False, use_gpu=gpu, reframe=rf,
                          audio_filter=loud, crf=23, preset="veryfast")
        report["formats"].append({**rf.summary(), "format": fid, "file": str(out), "render_seconds": round(time.time() - t1, 1),
                                  "lufs": measure_output_lufs(out), "camera_x": rf.keys[:40], "blur_windows": rf.blur})
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=0.0, help="0 = to the end of the video")
    ap.add_argument("--formats", nargs="+", default=["9:16"], choices=list(formats.VIDEO_FORMATS))
    ap.add_argument("--width", type=int, default=1080, help="render width (1080 = full size)")
    ap.add_argument("--detector", default="auto", choices=["auto", "mediapipe", "opencv"])
    ap.add_argument("--cpu", action="store_true", help="force libx264 instead of NVENC")
    ap.add_argument("--walking-demo", action="store_true", help="first build a clip where the speaker walks across the stage")
    a = ap.parse_args()
    video = Path(a.video)
    if a.walking_demo:
        video = make_walking_demo(video, OUT / "walking_pastor_demo.mp4")
        a.start, a.end = 0.0, 0.0
        print(f"Built the walking-speaker demo: {video}")
    rep = run_check(video, a.start, a.end, a.formats, a.width, a.detector, False if a.cpu else None)
    print(json.dumps(rep, indent=2, default=str))
    print("\nSUMMARY")
    d = rep["detector"]
    print(f"  detector: {d['used']} (face found in {d['faces_found']:.0%} of {d['samples']} frames, {d['seconds']}s)  encoder: {rep['encoder']}")
    for f in rep["formats"]:
        print(f"  {f['format']:<5} {f['size'][0]}x{f['size'][1]:<5} mode={f['mode']:<6} blur={f['blur_seconds']}s moves={f['pans']} "
              f"loudness={f['lufs']} LUFS  rendered in {f['render_seconds']}s -> {Path(f['file']).name}")


if __name__ == "__main__":
    main()
