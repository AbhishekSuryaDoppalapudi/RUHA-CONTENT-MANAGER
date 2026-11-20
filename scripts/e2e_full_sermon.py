"""Full end-to-end run on the REAL models (no fakes): one sermon through
transcribe -> plan -> reels -> captions -> posters -> posts, then one reel dubbed into Telugu.

    .venv\\Scripts\\python.exe scripts\\e2e_full_sermon.py "D:\\sermons\\sunday.mp4"
    .venv\\Scripts\\python.exe scripts\\e2e_full_sermon.py sunday.mp4 --target Telugu --clone --consent

It talks to the running server (start.bat) over HTTP, so it exercises exactly what the web app uses. It prints a
pre-flight check, per-step timings, hook quality, warnings from the job logs and any errors, and writes the same as
JSON next to this script (scripts/e2e_report_<time>.json). Exit code 0 only when every job completed.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

STEP_NAMES = {"transcription": "Transcribe (Whisper)", "reel_generation": "Plan + reels + captions",
              "poster_generation": "Posters", "social_post_generation": "Social posts",
              "dubbing": "Dub one reel", "translation": "Translate one reel"}


def secs(a, b):
    if not a or not b:
        return None
    f = lambda t: datetime.fromisoformat(t)
    return round((f(b) - f(a)).total_seconds(), 1)


def fmt(t):
    if t is None:
        return "-"
    return f"{int(t // 60)}m {t % 60:04.1f}s" if t >= 60 else f"{t:.1f}s"


class Run:
    def __init__(self, base):
        self.base, self.s = base.rstrip("/"), requests.Session()

    def call(self, method, path, **kw):
        r = self.s.request(method, self.base + path, timeout=kw.pop("timeout", 60), **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise SystemExit(f"{method} {path} -> {r.status_code}: {detail}")
        return r.json() if r.content else None

    def wait(self, job_ids, label, limit_s):
        """Wait until every job in the list is finished; print one line per change."""
        last, t0 = {}, time.time()
        while time.time() - t0 < limit_s:
            jobs = [self.call("GET", f"/api/jobs/{j}") for j in job_ids]
            for j in jobs:
                key = (j["status"], j["step"])
                if last.get(j["id"]) != key:
                    last[j["id"]] = key
                    print(f"  [{time.time() - t0:6.0f}s] job {j['id']} {j['job_type']:<22} {j['status']:<10} {j['step'] or ''}", flush=True)
            if all(j["status"] in ("completed", "failed", "cancelled") for j in jobs):
                return jobs
            time.sleep(3)
        raise SystemExit(f"Timed out after {limit_s // 60} minutes waiting for {label}")


def preflight(run):
    st = run.call("GET", "/api/system/status")
    g = (st.get("gpu") or {})
    gpu = (g.get("gpus") or [None])[0] or {}
    w, o, t = st["whisper"], st["ollama"], st["translator"]
    rows = [
        ("GPU", bool(g.get("available")), f"{gpu.get('name', 'none')} {gpu.get('vram_total_mb', '')} MB"),
        ("Whisper on GPU", w.get("device") == "cuda", f"{w['model']} on {w.get('device')} ({w.get('compute_type')}); cuda={w['cuda_available']}, cached={w['model_cached']}"),
        ("Ollama planner", bool(o.get("connected") and o.get("effective") == st["active_llm"]),
         f"wanted {st['active_llm']}, will use {o.get('effective')} (fallback {st.get('fallback_llm')})"),
        ("FFmpeg", st["ffmpeg"]["available"], f"NVENC={st['ffmpeg']['nvenc']}"),
        ("Translator", t["available"], f"{t.get('engine')} engine, compat={t.get('compat')}, cloning={t.get('cloning')}, dir={t.get('directory')}"),
        ("RAM", st["ram"]["available_gb"] >= 3, f"{st['ram']['available_gb']} GB free of {st['ram']['total_gb']}"),
    ]
    print("PRE-FLIGHT")
    for name, ok, detail in rows:
        print(f"  {'OK  ' if ok else 'WARN'} {name:<16} {detail}")
    return st, rows


def hook_report(run, pid):
    out = []
    for a in run.call("GET", f"/api/assets?project_id={pid}&kind=main_reel,short_reel"):
        hook = (a.get("meta") or {}).get("hook") or a.get("title", "")
        n = len(re.findall(r"\S+", hook))
        out.append({"slot": a["slot"], "title": a["title"], "hook": hook, "words": n, "ok": 3 <= n <= 8})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", help="the sermon video file")
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--target", default="Telugu")
    ap.add_argument("--voice", default="Male", choices=["Male", "Female"])
    ap.add_argument("--clone", action="store_true", help="clone the speaker's voice (needs --consent)")
    ap.add_argument("--consent", action="store_true", help="confirm you have the speaker's permission to clone")
    ap.add_argument("--limit-min", type=int, default=240)
    a = ap.parse_args()
    video = Path(a.video)
    if not video.exists():
        raise SystemExit(f"File not found: {video}")
    if a.clone and not a.consent:
        raise SystemExit("--clone needs --consent (you must have the speaker's permission)")

    run = Run(a.url)
    started = datetime.now()
    status, pre = preflight(run)
    report = {"started": started.isoformat(timespec="seconds"), "video": video.name, "preflight":
              [{"check": n, "ok": ok, "detail": d} for n, ok, d in pre], "steps": [], "errors": [], "warnings": []}

    pid = run.call("POST", "/api/projects", json={"name": f"E2E test {started:%Y-%m-%d %H:%M}", "content_type": "sermon",
                                                  "tags": ["e2e-test"]})["id"]
    print(f"\nProject {pid} created. Uploading {video.name} ({video.stat().st_size / 1e6:.0f} MB)...")
    t = time.time()
    with open(video, "rb") as f:
        up = run.call("POST", f"/api/projects/{pid}/videos", files={"file": (video.name, f, "video/mp4")}, timeout=3600)
    report["video_seconds"] = up["video"]["duration"]
    report["upload_s"] = round(time.time() - t, 1)
    print(f"Uploaded in {fmt(report['upload_s'])}; duration {fmt(report['video_seconds'])}")

    print("\nPIPELINE (transcribe -> plan -> reels+captions -> posters -> posts)")
    ids = run.call("POST", f"/api/projects/{pid}/process", json={})["job_ids"]
    jobs = run.wait(ids, "the pipeline", a.limit_min * 60)

    print("\nDUB ONE REEL")
    reels = [x for x in run.call("GET", f"/api/assets?project_id={pid}&kind=short_reel,main_reel") if x["has_file"]]
    if not reels:
        report["errors"].append("No reel was produced, so nothing could be dubbed.")
    else:
        reel = next((r for r in reels if r["kind"] == "short_reel"), reels[0])
        params = {"target": a.target, "dub": True, "voice": a.voice, "asset_id": reel["id"], "burn": False}
        if a.clone:
            params.update(clone=True, clone_consent=True)
        print(f"  reel: {reel['title']!r} ({reel['slot']}) -> {a.target}{' with voice cloning' if a.clone else ''}")
        dj = run.call("POST", f"/api/projects/{pid}/jobs", json={"job_type": "dubbing", "params": params})["job_id"]
        jobs += run.wait([dj], "the dub", a.limit_min * 60)

    total = 0.0
    for j in jobs:
        d = run.call("GET", f"/api/jobs/{j['id']}")
        t_s = secs(d["started_at"], d["completed_at"])
        total += t_s or 0
        report["steps"].append({"job": d["id"], "type": d["job_type"], "status": d["status"], "seconds": t_s,
                                "attempts": d["attempts"], "error": d["error"]})
        if d["status"] != "completed":
            report["errors"].append(f"job {d['id']} {d['job_type']}: {d['status']} - {d['error']}")
        for lg in d["logs"]:
            if lg["level"] in ("WARNING", "ERROR"):
                report["warnings"].append(f"job {d['id']} {d['job_type']}: [{lg['level']}] {lg['message'][:300]}")
    report["total_job_seconds"] = round(total, 1)
    report["hooks"] = hook_report(run, pid)
    report["assets"] = run.call("GET", f"/api/projects/{pid}")["asset_counts"]
    tr = run.call("GET", f"/api/assets?project_id={pid}&kind=translation_video")
    report["dubbed_video"] = {"title": tr[0]["title"], "meta": tr[0]["meta"]} if tr else None
    report["finished"] = datetime.now().isoformat(timespec="seconds")

    print("\nTIMINGS")
    for s in report["steps"]:
        print(f"  {STEP_NAMES.get(s['type'], s['type']):<26} {s['status']:<10} {fmt(s['seconds']):>10}  attempts={s['attempts']}")
    print(f"  {'TOTAL (jobs)':<26} {'':<10} {fmt(report['total_job_seconds']):>10}   for a {fmt(report['video_seconds'])} sermon")
    print("\nHOOKS (must be 3-8 words from the sermon)")
    for h in report["hooks"]:
        print(f"  {'OK ' if h['ok'] else 'BAD'} {h['slot']:<9} {h['words']} words: {h['hook']}")
    print(f"\nASSETS: {report['assets']}")
    print(f"DUBBED: {report['dubbed_video']}")
    print("\nWARNINGS FROM THE JOB LOGS" if report["warnings"] else "\nNo warnings in the job logs.")
    for w in report["warnings"]:
        print("  -", w)
    print("\nERRORS" if report["errors"] else "\nNo errors.")
    for e in report["errors"]:
        print("  -", e)

    out = Path(__file__).with_name(f"e2e_report_{started:%Y%m%d_%H%M%S}.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport saved: {out}")
    print(f"The test project (#{pid}, tagged e2e-test) is kept so you can inspect it; delete it from the Projects page when done.")
    sys.exit(1 if report["errors"] else 0)


if __name__ == "__main__":
    main()
