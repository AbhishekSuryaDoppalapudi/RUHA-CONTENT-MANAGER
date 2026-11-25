"""Runs inside the *translator's* Python (it owns torch, IndicTrans2 and Indic Parler-TTS).
Reads a JSON request on stdin, makes the narration audio for a YT Video Making project and prints
progress/result lines prefixed with ``@@RUHA@@``. Nothing in the translator project is modified.
"""
import json
import os
import sys

MARK = "@@RUHA@@"


def emit(**kw):
    print(MARK + json.dumps(kw, ensure_ascii=False), flush=True)


def load_engine(tdir):
    """The voice engine module: ``compat`` (old function names on top of dubbing_engine) when the translator
    folder has the shared engine, otherwise the old ``app`` module. Needs translate(), speak(), trim_silence(), LANGS."""
    if os.path.exists(os.path.join(tdir, "dubbing_engine.py")) and os.path.exists(os.path.join(tdir, "compat.py")):
        import compat
        return compat
    import app
    return app


def main():
    req = json.loads(sys.stdin.read())
    tdir = req["translator_dir"]
    os.chdir(tdir)
    sys.path.insert(0, tdir)
    sys.path.insert(0, req["yt_dir"])
    try:
        translator = load_engine(tdir)
        import story_video as sv
    except Exception as e:
        emit(error=f"Could not load the voice engine: {type(e).__name__}: {e}")
        sys.exit(2)
    sv.set_work(req["work_dir"])
    try:
        secs = sv.narrate(req["project"], req["lang"], req["voice"], engine=translator,
                          cb=lambda f, d="": emit(progress=float(f or 0), desc=str(d or "")))
    except BaseException as e:
        emit(error=f"{type(e).__name__}: {getattr(e, 'message', e)}")
        sys.exit(1)
    emit(result={"seconds": secs})


if __name__ == "__main__":
    main()
