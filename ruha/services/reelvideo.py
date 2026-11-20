"""Video reels in several formats: which formats the settings ask for, how big each is, how captions scale to it, and the
face-tracking step (which can never fail a reel - without it the reel just gets the old centre crop)."""
from __future__ import annotations

from ruha.creative import formats
from ruha.services import reframe


def chosen(cfg: dict) -> list:
    """[{id, size, primary, tag, file}] for the reel.formats setting, canonical order (9:16 first when chosen).

    The first one is the reel's main file (what the asset points to); the others are saved beside it and listed in the
    asset's ``variants``. ``tag`` is added to card/cutaway file names so formats never overwrite each other."""
    ids = formats.video_format_ids(cfg.get("reel.formats"))
    w, h = cfg.get("render.width", formats.BASE_WIDTH), cfg.get("render.height")
    out = []
    for i, fid in enumerate(ids):
        key = fid.replace(":", "x")
        out.append({"id": fid, "size": formats.video_size(fid, w, h), "primary": i == 0,
                    "tag": "" if i == 0 else f"_{key}", "file": key})
    return out


def style_for(style: dict, size: tuple, primary_size: tuple) -> dict:
    """Caption style for a format. It is designed for the primary size: text size follows the shorter side, and the
    distance from the bottom follows the height, so captions sit in the same place relative to the picture."""
    st = dict(style or {})
    (w, h), (pw, ph) = size, primary_size
    if (w, h) == (pw, ph):
        return st
    k_size, k_margin = min(w, h) / max(1, min(pw, ph)), h / max(1, ph)
    if "size" in st:
        st["size"] = max(12, int(round(float(st["size"]) * k_size)))
    if "margin_v" in st:
        st["margin_v"] = max(10, int(round(float(st["margin_v"]) * k_margin)))
    return st


def face_track(cfg: dict, source, start: float, end: float, src_size: tuple, log=None, cancel=None):
    """The speaker's face track for [start, end], or None when face tracking is off or could not run."""
    log = log or (lambda *a, **k: None)
    if not cfg.get("reel.face_tracking", True):
        return None
    try:
        trk = reframe.detect_track(source, start, end, src_size, str(cfg.get("reel.face_detector", "auto")), cancel=cancel)
    except RuntimeError as e:
        if str(e) == "Cancelled":
            raise
        log(f"Face tracking failed ({e}); using a centre crop for this reel", "WARNING")
        return None
    except Exception as e:                                         # never lose a reel to the tracker
        log(f"Face tracking failed ({type(e).__name__}: {e}); using a centre crop for this reel", "WARNING")
        return None
    if not trk.available:
        log("Face tracking is not available (" + "; ".join(trk.notes) + "); using a centre crop", "WARNING")
        return None
    log(f"Speaker found in {trk.coverage:.0%} of the sampled frames ({trk.backend}); "
        f"the rest is shown with blur fill")
    return trk
