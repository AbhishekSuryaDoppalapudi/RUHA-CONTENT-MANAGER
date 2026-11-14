"""Runs inside the *translator's* Python. Reads a JSON request on stdin, calls the translator's
existing ``_translate_impl`` and prints progress/result lines prefixed with ``@@RUHA@@``.

Nothing in the translator project is modified.
"""
import json
import os
import shutil
import sys

MARK = "@@RUHA@@"


def emit(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)


def main():
    req = json.loads(sys.stdin.read())
    tdir = req["translator_dir"]
    os.chdir(tdir)
    sys.path.insert(0, tdir)
    try:
        import app as translator  # noqa: E402  (the existing El Ruach Video Translator module)
    except Exception as e:
        emit(error=f"Could not import translator app.py: {type(e).__name__}: {e}")
        sys.exit(2)

    def progress(value, desc=""):
        emit(progress=float(value or 0), desc=str(desc or ""))

    try:
        out, srt, dub_wav, status = translator._translate_impl(
            req["video"], req["source"], req["target"], req["voice"], req["dub"],
            req["keep_background"], req["burn"], 0, 0, req["resolution"], progress)
    except BaseException as e:   # gr.Error is a plain Exception subclass
        emit(error=f"{type(e).__name__}: {getattr(e, 'message', e)}")
        sys.exit(1)

    dest = req["out_dir"]
    os.makedirs(dest, exist_ok=True)
    result = {"status": str(status)}
    for key, src in (("video", out), ("srt", srt), ("dub_audio", dub_wav)):
        if src and os.path.exists(src):
            target = os.path.join(dest, os.path.basename(src))
            shutil.copy2(src, target)
            result[key] = target
    emit(result=result)


if __name__ == "__main__":
    main()
