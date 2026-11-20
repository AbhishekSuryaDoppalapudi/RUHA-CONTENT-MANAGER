"""Runs inside the *translator's* Python. Reads a JSON request on stdin, calls the translator and prints
progress/result lines prefixed with ``@@RUHA@@``.

Two translator generations are supported, detected from the translator folder:
  * shared engine  - ``dubbing_engine.py`` exists: ``translate_video`` (supports voice cloning).
  * legacy         - only ``app.py`` with ``_translate_impl`` (Male/Female stock voices, no cloning).

Nothing in the translator project is modified.
"""
import json
import os
import shutil
import sys

MARK = "@@RUHA@@"


def emit(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)


def run_shared(req, progress):
    """New engine. Returns {"video", "srt", "status", "notes", ...}."""
    import dubbing_engine as eng  # noqa: E402
    clone = bool(req.get("clone"))
    source = req.get("source") or "Auto-detect"
    if source == "Auto-detect" and req.get("source_hint") in getattr(eng, "SOURCE_CHOICES", ()):
        source = req["source_hint"]                       # use the language RUHA already knows, not a guess
    res = eng.translate_video(
        req["video"], req["target"], source=source, voice="clone" if clone else "standard",
        base_gender=str(req.get("voice") or "Male").lower(), dub=bool(req["dub"]),
        keep_background=bool(req["keep_background"]), separate=True, burn_subtitles=bool(req["burn"]),
        out_dir=req["out_dir"], progress=progress)
    r, info = res[req["target"]], res.get("_info", {})
    notes = list(r.get("notes") or [])
    status = (f"Done in {info.get('seconds', '?')}s. Spoken language: {info.get('source_language', '?')} -> "
              f"{req['target']}, {r.get('lines', 0)} lines. " + " ".join(notes)).strip()
    return {"video": r.get("video"), "srt": r.get("srt"), "status": status, "notes": notes, "engine": "shared"}


def run_legacy(req, progress):
    import app as translator  # noqa: E402  (the original El Ruach Video Translator module)
    if req.get("clone"):
        raise RuntimeError("This translator version has no voice cloning. Install the shared dubbing engine "
                           "(dubbing_engine.py) in the translator folder.")
    out, srt, dub_wav, status = translator._translate_impl(
        req["video"], req["source"], req["target"], req["voice"], req["dub"],
        req["keep_background"], req["burn"], 0, 0, req["resolution"], progress)
    return {"video": out, "srt": srt, "dub_audio": dub_wav, "status": str(status), "notes": [], "engine": "legacy"}


def run(req, progress=None):
    """Run one request; returns the result dict (file paths copied into req['out_dir'])."""
    tdir = req["translator_dir"]
    os.chdir(tdir)
    if tdir not in sys.path:
        sys.path.insert(0, tdir)
    shared = os.path.exists(os.path.join(tdir, "dubbing_engine.py"))
    raw = (run_shared if shared else run_legacy)(req, progress or (lambda *a: None))
    dest = req["out_dir"]
    os.makedirs(dest, exist_ok=True)
    result = {k: raw[k] for k in ("status", "notes", "engine") if k in raw}
    for key in ("video", "srt", "dub_audio"):
        src = raw.get(key)
        if src and os.path.exists(src):
            target = os.path.join(dest, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(target):
                shutil.copy2(src, target)
            result[key] = target
    return result


def main():
    req = json.loads(sys.stdin.read())

    def progress(value, desc=""):
        emit(progress=float(value or 0), desc=str(desc or ""))

    try:
        result = run(req, progress)
    except ImportError as e:
        emit(error=f"Could not import the translator: {type(e).__name__}: {e}")
        sys.exit(2)
    except BaseException as e:   # gr.Error / ValueError are plain Exception subclasses
        emit(error=f"{type(e).__name__}: {getattr(e, 'message', e)}")
        sys.exit(1)
    emit(result=result)


if __name__ == "__main__":
    main()
