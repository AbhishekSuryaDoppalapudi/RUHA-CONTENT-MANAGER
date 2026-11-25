"""Pick the best frame of a clip for a thumbnail: a big face, eyes open, sharp - not "the frame at 10 %".

Two passes, so a long clip stays cheap:
  1. every candidate frame (2 a second for a short clip, ~36 spread over a long video) is decoded small; the face detector
     gives the face size and position, and the Laplacian variance gives the sharpness;
  2. only the best few get the eye check, on a crop around the face from the full-resolution frame: MediaPipe's face
     landmarker (eye-blink scores; looking down counts against it), or OpenCV's eye detector when MediaPipe cannot run.
The final score is 0.40 face + 0.35 eyes + 0.25 sharpness. Frames with no face can only win when no frame has one (then the
sharpest, well-lit frame is used). Nothing here can fail a job: any problem falls back to the old "frame near the start".
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ruha.services import media, reframe

log = logging.getLogger("ruha.thumbs")

LANDMARKER = reframe.MODEL_DIR / "face_landmarker.task"
W_FACE, W_EYES, W_SHARP = 0.40, 0.35, 0.25
SHORT_CLIP_S = 150.0        # up to this long: decode the clip at SAMPLE_FPS; longer: one seek per candidate
SAMPLE_FPS = 2.0
LONG_CANDIDATES = 36
EYE_CHECKS = 8              # how many frames get the (slower) eye check
MIN_GAP_S = 2.5
MIN_FACE_SCORE = 0.6          # a detection below this (a torso, a poster) is not a face for a thumbnail


@dataclass
class FrameScore:
    t: float                          # seconds in the source (absolute)
    score: float = 0.0
    face: reframe.Face | None = None
    face_score: float = 0.0
    eyes: float | None = None         # 0 closed .. 1 open; None = could not tell
    sharp: float = 0.0                # 0..1 within this clip
    bright: float = 0.0
    notes: list = field(default_factory=list)


# ------------------------------------------------------------------ the measurements
def gray(rgb: np.ndarray) -> np.ndarray:
    return (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)


def sharpness(g: np.ndarray) -> float:
    """Variance of the Laplacian: high when edges are crisp, low when the frame is blurred."""
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    lap = -4.0 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


def well_lit(g: np.ndarray) -> bool:
    m = float(g.mean())
    return 25.0 <= m <= 235.0 and float(g.std()) > 8.0                # not black, not blown out, not a flat fade


def face_size_score(face: reframe.Face | None) -> float:
    """0..1: a bigger face is better (full marks from a face 30 % of the frame high), and a face cut off by the frame edge is not."""
    if face is None:
        return 0.0
    s = min(1.0, face.h / 0.30)
    if face.cx - face.w / 2 < 0.01 or face.cx + face.w / 2 > 0.99 or face.cy - face.h / 2 < 0.0 or face.cy + face.h / 2 > 1.0:
        s *= 0.5
    off = abs(face.cx - 0.5)                                            # a little for being near the middle (crops later)
    return s * (1.0 - 0.25 * min(1.0, off / 0.5))


# ------------------------------------------------------------------ eyes
class MediaPipeEyes:
    """Eyes-open score from MediaPipe's face landmarker blend shapes (eyeBlink, eyeLookDown)."""
    name = "mediapipe"

    def __init__(self):
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mpt
            from mediapipe.tasks.python import vision
        except Exception as e:
            raise reframe.DetectorUnavailable(f"MediaPipe is not available ({type(e).__name__})")
        if not LANDMARKER.exists():
            raise reframe.DetectorUnavailable("the face landmarker model file is missing")
        try:
            opts = vision.FaceLandmarkerOptions(base_options=mpt.BaseOptions(model_asset_path=str(LANDMARKER)),
                                                output_face_blendshapes=True, num_faces=2, min_face_detection_confidence=0.3)
            self._lm = vision.FaceLandmarker.create_from_options(opts)
        except Exception as e:
            raise reframe.DetectorUnavailable(f"MediaPipe could not start ({type(e).__name__}: {str(e)[:100]})")
        self._mp = mp

    def score(self, rgb: np.ndarray) -> float | None:
        res = self._lm.detect(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
        if not res.face_landmarks or not res.face_blendshapes:
            return None
        b = {c.category_name: c.score for c in res.face_blendshapes[0]}
        blink = max(b.get("eyeBlinkLeft", 0.0), b.get("eyeBlinkRight", 0.0))
        down = max(b.get("eyeLookDownLeft", 0.0), b.get("eyeLookDownRight", 0.0))
        return float(max(0.0, min(1.0, 1.0 - blink - 0.25 * down)))

    def close(self):
        try:
            self._lm.close()
        except Exception:
            pass


class HaarEyes:
    """Fallback: OpenCV's eye detector on the upper half of the face (two eyes found = open)."""
    name = "opencv"

    def __init__(self):
        try:
            import cv2
        except Exception as e:
            raise reframe.DetectorUnavailable(f"OpenCV is not available ({type(e).__name__})")
        self._cv2 = cv2
        self._cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
        if self._cc.empty():
            raise reframe.DetectorUnavailable("OpenCV has no eye detector")

    def score(self, rgb: np.ndarray) -> float | None:
        g = self._cv2.cvtColor(np.ascontiguousarray(rgb), self._cv2.COLOR_RGB2GRAY)
        h = g.shape[0]
        eyes = self._cc.detectMultiScale(g[: int(h * 0.62)], 1.1, 4, minSize=(max(8, g.shape[1] // 12),) * 2)
        return {0: 0.2, 1: 0.6}.get(len(eyes), 1.0)

    def close(self):
        pass


EYE_FACTORIES = {"mediapipe": MediaPipeEyes, "opencv": HaarEyes}        # tests replace these
_EYES: dict = {}


def reset_eyes():
    for e in _EYES.values():
        if hasattr(e, "close"):
            e.close()
    _EYES.clear()


def eye_checker():
    """The first eye checker that starts on this machine (MediaPipe, then OpenCV), or None. A failed start is remembered."""
    for name in ("mediapipe", "opencv"):
        got = _EYES.get(name)
        if got is False:
            continue
        if got is not None:
            return got
        try:
            _EYES[name] = EYE_FACTORIES[name]()
            return _EYES[name]
        except reframe.DetectorUnavailable as e:
            log.info("eye check %s unavailable: %s", name, e)
            _EYES[name] = False
    return None


def face_crop(rgb: np.ndarray, face: reframe.Face, grow: float = 3.0) -> np.ndarray:
    """A square crop of the frame around the face (``grow`` times the face), for the eye check."""
    h, w = rgb.shape[:2]
    side = int(max(face.w * w, face.h * h) * grow)
    side = max(64, min(side, min(h, w)))
    x0 = int(min(max(0, face.cx * w - side / 2), w - side))
    y0 = int(min(max(0, face.cy * h - side / 2), h - side))
    return rgb[y0:y0 + side, x0:x0 + side]


# ------------------------------------------------------------------ frames
def _decode_at(source, t: float, width: int, src_size: tuple) -> np.ndarray | None:
    sw, sh = src_size
    h = max(2, int(round(width * sh / max(sw, 1) / 2)) * 2)
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(source), "-frames:v", "1", "-vf",
                        f"scale={width}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True,
                       creationflags=media.CREATE_NO_WINDOW)
    if len(p.stdout) < width * h * 3:
        return None
    return np.frombuffer(p.stdout, np.uint8).reshape(h, width, 3).copy()


def candidate_frames(source, start: float, end: float, src_size: tuple, width: int = 640):
    """Yield (absolute seconds, small RGB frame) across [start, end]."""
    dur = max(0.5, end - start)
    if dur <= SHORT_CLIP_S:
        for t, fr in reframe.sample_frames(source, start, dur, src_size, SAMPLE_FPS, width):
            yield start + t, fr
        return
    for i in range(LONG_CANDIDATES):                                    # a long video: spread seeks, skip the first/last 4 %
        t = start + dur * (0.04 + 0.92 * i / max(1, LONG_CANDIDATES - 1))
        fr = _decode_at(source, t, width, src_size)
        if fr is not None:
            yield t, fr


def full_frame(source, t: float, src_size: tuple) -> np.ndarray | None:
    """The frame at ``t`` at the source's own size."""
    return _decode_at(source, t, src_size[0], src_size)


def save_frame(source, t: float, out, width: int | None = None, quality: int = 2) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(source), "-frames:v", "1"]
    if width:
        cmd += ["-vf", f"scale={width}:-2"]
    subprocess.run(cmd + ["-q:v", str(quality), str(out)], check=True, capture_output=True, creationflags=media.CREATE_NO_WINDOW)
    return out


def _combine(face_s: float, eyes: float | None, sharp: float) -> float:
    return W_FACE * face_s + W_EYES * (0.5 if eyes is None else eyes) + W_SHARP * sharp


def best_frames(source, start: float, end: float, src_size: tuple, k: int = 1, *, detectors=None, eyes="auto") -> list:
    """The ``k`` best, well-separated frames of [start, end] of ``source``: [FrameScore], best first.

    ``detectors`` (tests) replaces the face detectors; ``eyes``: "auto" (MediaPipe / OpenCV), None (skip) or an object
    with ``score(rgb) -> float | None``."""
    dets = detectors if detectors is not None else reframe.load_detectors("auto")[0]
    cands, sharps = [], []
    for t, fr in candidate_frames(source, start, end, src_size):
        g = gray(fr)
        if not well_lit(g):
            continue
        found = []
        for det in dets:                                                    # every detector votes; weak detections are ignored
            try:
                found += [f for f in reframe.find_faces(det, fr) if f.score >= MIN_FACE_SCORE]
            except Exception as e:                                         # one bad frame never stops the search
                log.warning("face detector %s failed: %s", getattr(det, "name", "?"), e)
        face = reframe.pick_speaker(found, None)
        fs = FrameScore(t=t, face=face, face_score=face_size_score(face), bright=float(g.mean()))
        fs.sharp = sharpness(g[face_box(face, g.shape)] if face else g)
        cands.append(fs)
        sharps.append(fs.sharp)
    if not cands:
        return []
    top = max(sharps) or 1.0
    for c in cands:                                                         # sharpness relative to the sharpest candidate
        c.sharp = min(1.0, c.sharp / top)
        c.score = _combine(c.face_score, None, c.sharp) if c.face else 0.25 * c.sharp
    ranked = sorted(cands, key=lambda c: -c.score)
    checker = eye_checker() if eyes == "auto" else eyes
    if checker is not None:
        for c in [r for r in ranked if r.face][:EYE_CHECKS]:
            full = full_frame(source, c.t, src_size)
            if full is None:
                continue
            try:
                c.eyes = checker.score(face_crop(full, c.face))
            except Exception as e:
                log.warning("eye check failed: %s", e)
                c.eyes = None
            c.score = _combine(c.face_score, c.eyes, c.sharp)
            if c.eyes is not None and c.eyes < 0.35:
                c.notes.append("eyes closed")
        ranked = sorted(cands, key=lambda c: -c.score)
    gap = MIN_GAP_S if end - start <= SHORT_CLIP_S else (end - start) / 12
    out = []
    for c in ranked:
        if all(abs(c.t - o.t) >= gap for o in out):
            out.append(c)
        if len(out) >= k:
            break
    return out


def face_box(face: reframe.Face, shape: tuple) -> tuple:
    """Slices (rows, cols) of the face region (a bit larger than the box) of a frame of ``shape``."""
    h, w = shape[:2]
    x0, x1 = int(max(0, (face.cx - face.w) * w)), int(min(w, (face.cx + face.w) * w))
    y0, y1 = int(max(0, (face.cy - face.h) * h)), int(min(h, (face.cy + face.h) * h))
    return slice(y0, max(y1, y0 + 3)), slice(x0, max(x1, x0 + 3))


def best_time(source, start: float, end: float, src_size: tuple, **kw) -> float | None:
    got = best_frames(source, start, end, src_size, 1, **kw)
    return got[0].t if got else None
