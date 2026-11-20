"""Speaker-aware reframing: find the speaker's face on sampled frames, plan a smooth virtual camera, and decide which
stretches have no face (those get a blurred "blur fill" background with the whole frame centred).

Pipeline, one reel at a time (``media.render_reel`` draws the result):

  1. ``detect_track``  - decode the clip at 5 frames per second, run a face detector on each frame, keep the speaker
                         (the biggest face, sticking with the person tracked so far) -> a ``Track``.
  2. ``plan``          - for one output format (9:16, 4:5, 1:1, 16:9): the crop window, a smoothed camera path
                         (dead-zone + moving average, so the picture does not jitter) and the blur-fill windows.

Detectors: MediaPipe Face Detection first. Its short-range model is made for close-ups and misses the small face of a
speaker in a wide shot (about half the frames in testing), so unless it already sees the speaker in nearly every frame
OpenCV's YuNet also runs and the two are merged frame by frame. YuNet alone is used when MediaPipe cannot start (it is
not installed, or a Windows Application Control policy blocks its native library). Both models ship in
``ruha/assets/models`` so nothing is downloaded. When no detector works at all the reel gets the old centre crop.
"""
from __future__ import annotations

import atexit
import logging
import math
import subprocess
import time
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("ruha.reframe")

MODEL_DIR = Path(__file__).resolve().parents[1] / "assets" / "models"
BLAZE_MODEL = MODEL_DIR / "blaze_face_short_range.tflite"      # MediaPipe BlazeFace (Apache-2.0)
YUNET_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"  # OpenCV Zoo YuNet (MIT)

SAMPLE_FPS = 5.0           # frames per second looked at
DETECT_WIDTH = 640         # sampled frames are decoded this wide (faces in a wide shot are still big enough)
MIN_SCORE = 0.5
GOOD_COVERAGE = 0.9        # once this share of the frames has a face, the next detector is not needed
DEAD_ZONE = 0.10           # the camera holds still while the face is within this share of the crop width of its centre
TAU = 0.45                 # seconds the camera takes to close most of a gap once it starts following
SMOOTH_SAMPLES = 3         # moving-average window over the camera path (0.6 s at 5 fps)
FACE_MARGIN = 0.15         # the face centre is always kept this far (share of crop width) inside the crop edges
HOLD_S = 1.0               # a gap shorter than this holds the camera still; a longer one switches to blur fill
MERGE_S = 0.8              # blur windows closer than this are merged (no flicker)
WHOLE_BLUR = 0.9           # blur fill for at least this share of the clip: use it for the whole clip

Face = namedtuple("Face", "cx cy w h score")     # all normalised to the frame (0..1)


class DetectorUnavailable(RuntimeError):
    pass


# ------------------------------------------------------------------ detectors
class MediaPipeDetector:
    """MediaPipe Face Detection (BlazeFace short range, Tasks API) from the bundled model file."""
    name = "mediapipe"

    def __init__(self, min_confidence: float = MIN_SCORE):
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mpt
            from mediapipe.tasks.python import vision
        except Exception as e:                                         # not installed / unsupported Python
            raise DetectorUnavailable(f"MediaPipe is not available ({type(e).__name__}: {str(e)[:100]})")
        if not BLAZE_MODEL.exists():
            raise DetectorUnavailable("the MediaPipe face model file is missing")
        try:
            opts = vision.FaceDetectorOptions(base_options=mpt.BaseOptions(model_asset_path=str(BLAZE_MODEL)),
                                              min_detection_confidence=min_confidence)
            self._det = vision.FaceDetector.create_from_options(opts)
        except Exception as e:                                         # e.g. OSError: blocked by Application Control
            raise DetectorUnavailable(f"MediaPipe could not start ({type(e).__name__}: {str(e)[:120]})")
        self._mp = mp

    def detect(self, rgb: np.ndarray) -> list:
        h, w = rgb.shape[:2]
        res = self._det.detect(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
        out = []
        for d in res.detections:
            b = d.bounding_box
            out.append(Face((b.origin_x + b.width / 2) / w, (b.origin_y + b.height / 2) / h, b.width / w, b.height / h,
                            float(d.categories[0].score) if d.categories else 0.5))
        return out

    def close(self):
        try:
            self._det.close()
        except Exception:
            pass


class YuNetDetector:
    """OpenCV's YuNet face detector (ONNX). Used when MediaPipe cannot run; it copes well with the small face of a
    speaker in a wide shot."""
    name = "opencv"

    def __init__(self, min_confidence: float = 0.6):
        try:
            import cv2
        except Exception as e:
            raise DetectorUnavailable(f"OpenCV is not available ({type(e).__name__})")
        if not hasattr(cv2, "FaceDetectorYN"):
            raise DetectorUnavailable("this OpenCV has no face detector")
        if not YUNET_MODEL.exists():
            raise DetectorUnavailable("the YuNet face model file is missing")
        self._cv2, self._conf, self._det, self._size = cv2, min_confidence, None, None

    def detect(self, rgb: np.ndarray) -> list:
        h, w = rgb.shape[:2]
        if self._size != (w, h):
            self._det = self._cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (w, h), self._conf, 0.3, 20)
            self._size = (w, h)
        _, faces = self._det.detect(np.ascontiguousarray(rgb[:, :, ::-1]))
        if faces is None:
            return []
        return [Face(float((r[0] + r[2] / 2) / w), float((r[1] + r[3] / 2) / h), float(r[2] / w), float(r[3] / h),
                     float(r[-1])) for r in faces]

    def close(self):
        self._det = None


FACTORIES = {"mediapipe": MediaPipeDetector, "opencv": YuNetDetector}      # tests replace these
_POOL: dict = {}                                                           # name -> detector, or the reason it failed
_POOL_AT: dict = {}
_RETRY_FAILED_S = 300.0


def reset_pool():
    for d in _POOL.values():
        if hasattr(d, "close"):
            d.close()
    _POOL.clear()
    _POOL_AT.clear()


atexit.register(reset_pool)        # close MediaPipe's native detector before the interpreter shuts down


def _get(name: str):
    """(detector or None, reason or None) - loaded once, a failed start is not retried for five minutes."""
    got = _POOL.get(name)
    if got is not None and not isinstance(got, str):
        return got, None
    if isinstance(got, str) and time.time() - _POOL_AT.get(name, 0) < _RETRY_FAILED_S:
        return None, got
    try:
        det = FACTORIES[name]()
    except DetectorUnavailable as e:
        _POOL[name], _POOL_AT[name] = str(e), time.time()
        return None, str(e)
    _POOL[name] = det
    return det, None


def order_for(preference: str) -> list:
    return {"mediapipe": ["mediapipe"], "opencv": ["opencv"]}.get(str(preference or "auto"), ["mediapipe", "opencv"])


def load_detectors(preference: str = "auto") -> tuple:
    """([detectors in the order to try], [why a detector is not available])."""
    got, why = [], []
    for name in order_for(preference):
        det, reason = _get(name)
        if det is not None:
            got.append(det)
        else:
            why.append(f"{name}: {reason}")
    return got, why


def detector_status(preference: str = "auto") -> dict:
    """For the status page: which detectors can run on this machine."""
    out = {}
    for name in FACTORIES:
        det, reason = _get(name)
        out[name] = {"available": det is not None, "reason": reason}
    active = next((n for n in order_for(preference) if out[n]["available"]), None)
    return {"active": active, "detectors": out}


# ------------------------------------------------------------------ the track
@dataclass
class Track:
    """What was seen: ``samples`` = [(seconds into the clip, Face or None)] at ``fps``."""
    samples: list
    backend: str | None
    fps: float = SAMPLE_FPS
    available: bool = True                   # False when no detector could run at all (the reel gets a centre crop)
    notes: list = field(default_factory=list)

    @property
    def found(self) -> int:
        return sum(1 for _, f in self.samples if f is not None)

    @property
    def coverage(self) -> float:
        return self.found / len(self.samples) if self.samples else 0.0


def pick_speaker(faces: list, prev: Face | None) -> Face | None:
    """The speaker among the faces of one frame: the biggest, unless the person tracked so far is still close by and
    nearly as big (audience members and posters are ignored; the pastor is not dropped for a bigger passer-by)."""
    faces = [f for f in faces if f.score >= MIN_SCORE]
    if not faces:
        return None
    best = max(faces, key=lambda f: f.w * f.h * f.score)
    if prev is not None:
        near = min(faces, key=lambda f: abs(f.cx - prev.cx) + abs(f.cy - prev.cy))
        if near.w * near.h >= 0.5 * best.w * best.h and abs(near.cx - prev.cx) < 0.25:
            return near
    return best


def _tiles(frame: np.ndarray):
    """Three overlapping squares side by side. A fixed-input detector (BlazeFace looks at 128 px) sees a small face in a
    wide shot much larger inside a tile, so tiles are searched when the whole frame shows nothing."""
    h, w = frame.shape[:2]
    if w <= h * 1.2:
        return
    side = h
    for x0 in sorted({0, (w - side) // 2, w - side}):
        yield x0, frame[:, x0:x0 + side]


def find_faces(det, frame: np.ndarray) -> list:
    faces = det.detect(frame)
    if faces or getattr(det, "name", "") != "mediapipe":
        return faces
    h, w = frame.shape[:2]
    out = []
    for x0, tile in _tiles(frame):
        th, tw = tile.shape[:2]
        for f in det.detect(tile):
            out.append(Face((x0 + f.cx * tw) / w, f.cy, f.w * tw / w, f.h, f.score))
    return out


def sample_frames(source, start: float, duration: float, src_size: tuple, fps: float = SAMPLE_FPS, width: int = DETECT_WIDTH):
    """Yield (seconds into the clip, RGB frame) decoded at ``fps`` frames per second and ``width`` pixels wide."""
    from ruha.services import media
    sw, sh = src_size
    h = max(2, int(round(width * sh / max(sw, 1) / 2)) * 2)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{duration:.3f}", "-i", str(source),
           "-an", "-vf", f"fps={fps},scale={width}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=media.CREATE_NO_WINDOW)
    size, i = width * h * 3, 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield i / fps, np.frombuffer(buf, np.uint8).reshape(h, width, 3)
            i += 1
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def _run_detector(det, source, start, duration, src_size, fps, cancel=None) -> Track:
    samples, prev, lost, errors = [], None, 0, 0
    for t, frame in sample_frames(source, start, duration, src_size, fps):
        if cancel and cancel():
            raise RuntimeError("Cancelled")
        try:
            faces = find_faces(det, frame)
        except Exception as e:                                        # one bad frame must not lose the reel
            errors += 1
            faces = []
            if errors == 1:
                log.warning("face detector %s failed on a frame: %s", det.name, e)
        face = pick_speaker(faces, prev)
        if face is not None:
            prev, lost = face, 0
        else:
            lost += 1
            if lost > 2 * fps:                                        # gone for 2 s: forget who was tracked
                prev = None
        samples.append((t, face))
    return Track(samples, det.name, fps)


def merge_tracks(tracks: list) -> Track:
    """Frame by frame: the face from the first track that has one. Backends that contributed a face are named."""
    first = tracks[0]
    samples, used = [], []
    for i, (t, f) in enumerate(first.samples):
        face = f
        if face is None:
            for other in tracks[1:]:
                if i < len(other.samples) and other.samples[i][1] is not None:
                    face = other.samples[i][1]
                    break
        samples.append((t, face))
    for tr in tracks:
        if tr.found and tr.backend not in used:
            used.append(tr.backend)
    return Track(samples, "+".join(used) or first.backend, first.fps, notes=list(first.notes))


def detect_track(source, start: float, end: float, src_size: tuple, preference: str = "auto", fps: float = SAMPLE_FPS,
                 cancel=None, detectors: list | None = None) -> Track:
    """Face track of [start, end] of ``source``. ``detectors`` (tests) bypasses loading."""
    duration = max(0.5, end - start)
    dets, why = (detectors, []) if detectors is not None else load_detectors(preference)
    if not dets:
        return Track([], None, fps, available=False, notes=why or ["no face detector is available"])
    tracks, merged = [], None
    for det in dets:
        trk = _run_detector(det, source, start, duration, src_size, fps, cancel)
        tracks.append(trk)
        merged = merge_tracks(tracks)
        if merged.coverage >= GOOD_COVERAGE:
            break                                                      # the next detector is only a backup
        if len(dets) > 1 and det is not dets[-1]:
            log.info("%s found the speaker in %.0f%% of the frames; adding %s", det.name, 100 * trk.coverage, dets[len(tracks)].name)
    merged.notes = list(why)
    return merged


# ------------------------------------------------------------------ the plan
@dataclass
class Reframe:
    """How one format is cut from the source.

    mode  "full"  the source already has the target shape: scale it
          "crop"  a window of ``crop`` pixels that follows the camera path ``keys`` [(t, x pixels)], with ``blur`` windows
                  [(t0, t1)] where the whole frame is shown over a blurred copy instead
          "blur"  the whole clip is blur fill (no face anywhere, or the target is wider than the source)
          "center" a plain centre crop (no face detector could run)"""
    mode: str
    src: tuple
    out: tuple
    crop: tuple
    keys: list = field(default_factory=list)
    blur: list = field(default_factory=list)
    backend: str | None = None
    coverage: float = 0.0
    duration: float = 0.0

    @property
    def blur_seconds(self) -> float:
        return round(self.duration if self.mode == "blur" else sum(b - a for a, b in self.blur), 2)

    def summary(self) -> dict:
        return {"mode": self.mode, "backend": self.backend, "faces": round(self.coverage, 2),
                "blur_seconds": self.blur_seconds, "pans": max(0, len(self.keys) - 1), "size": list(self.out)}


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _rdp(points: list, eps: float) -> list:
    """Ramer-Douglas-Peucker on (t, x): fewest keyframes that stay within ``eps`` pixels of the camera path."""
    if len(points) < 3:
        return points
    (t0, x0), (t1, x1) = points[0], points[-1]
    worst, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        t, x = points[i]
        want = x0 + (x1 - x0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0.0)
        if abs(x - want) > worst:
            worst, idx = abs(x - want), i
    if worst <= eps:
        return [points[0], points[-1]]
    return _rdp(points[:idx + 1], eps)[:-1] + _rdp(points[idx:], eps)


def blur_windows(samples: list, fps: float, duration: float, hold_s: float = HOLD_S, merge_s: float = MERGE_S) -> list:
    """[(t0, t1)] stretches with no face for at least ``hold_s`` seconds (shorter gaps hold the camera instead),
    with windows closer than ``merge_s`` joined so the picture does not flicker between the two looks."""
    dt, runs, start = 1.0 / fps, [], None
    for i, (t, f) in enumerate(samples + [(None, True)]):             # sentinel closes a run at the end
        if f is None and start is None:
            start = i
        elif f is not None and start is not None:
            runs.append((start, i))
            start = None
    wins = []
    for a, b in runs:
        t0, t1 = max(0.0, samples[a][0] - dt / 2), min(duration, samples[b - 1][0] + dt / 2)
        if a == 0:
            t0 = 0.0
        if b == len(samples):
            t1 = duration
        if t1 - t0 >= hold_s:
            wins.append([t0, t1])
    merged = []
    for w in wins:
        if merged and w[0] - merged[-1][1] < merge_s:
            merged[-1][1] = w[1]
        else:
            merged.append(w)
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def camera_path(samples: list, fps: float, src_w: float, crop_w: float, dead_zone: float = DEAD_ZONE, tau: float = TAU) -> list:
    """[(t, x)] left edge of the crop window at every sample: it holds still inside the dead zone, follows (eased)
    when the face leaves it, is averaged over a short window, and never lets the face leave the window."""
    dt, hi = 1.0 / fps, max(0.0, src_w - crop_w)
    target = [None if f is None else min(hi, max(0.0, f.cx * src_w - crop_w / 2)) for _, f in samples]
    first = next((x for x in target if x is not None), hi / 2)
    pos, panning, raw = first, False, []
    for x in target:
        if x is not None:
            err = x - pos
            if not panning and abs(err) > dead_zone * crop_w:
                panning = True
            if panning:
                pos += err * (1.0 - math.exp(-dt / tau))
                if abs(x - pos) < 0.03 * crop_w:
                    panning = False
        raw.append(pos)
    k, sm = SMOOTH_SAMPLES // 2, []
    for i in range(len(raw)):
        win = raw[max(0, i - k):i + k + 1]
        sm.append(sum(win) / len(win))
    out = []
    for (t, f), x in zip(samples, sm):
        if f is not None:                                              # the speaker is never cut off
            fx = f.cx * src_w
            x = min(max(x, fx - (1 - FACE_MARGIN) * crop_w), fx - FACE_MARGIN * crop_w)
        out.append((t, min(hi, max(0.0, x))))
    return out


def segmented_path(samples: list, wins: list, fps: float, src_w: float, crop_w: float) -> list:
    """The camera path, planned separately for every stretch between blur windows: each stretch starts exactly on the
    speaker (a new start has no old position to ease in from), and the moving average never mixes the two sides."""
    inside = lambda t: any(a <= t <= b for a, b in wins)
    path, run, last_in = [], [], False
    for t, f in samples:
        now_in = inside(t)
        if now_in != last_in and run:
            path += camera_path(run, fps, src_w, crop_w)
            run = []
        last_in = now_in
        if not now_in:
            run.append((t, f))
    if run:
        path += camera_path(run, fps, src_w, crop_w)
    return path


def window_keys(path: list, wins: list, duration: float) -> list:
    """Camera keyframes with the blur windows taken out: nothing of the crop is visible inside a window, so the camera
    uses it to get in position - it holds where it was when the window starts and has arrived at the speaker's next
    position when it ends (no pan from the old spot is ever seen when the speaker comes back)."""
    inside = lambda t: any(a <= t <= b for a, b in wins)
    keys = [(t, x) for t, x in path if not inside(t)]
    for a, b in wins:
        before = [x for t, x in path if t < a]
        after = [x for t, x in path if t > b]
        p0 = before[-1] if before else (after[0] if after else path[0][1])
        p1 = after[0] if after else p0
        keys += [(a, p0), (max(a, b - 0.04), p1)]
    keys.sort(key=lambda k: k[0])
    if keys[0][0] > 0:
        keys.insert(0, (0.0, keys[0][1]))
    return keys


def plan(track: Track | None, src_w: int, src_h: int, out_w: int, out_h: int, duration: float) -> Reframe:
    """The reframe for one output format."""
    src, out = (int(src_w), int(src_h)), (int(out_w), int(out_h))
    sa, oa = src_w / src_h, out_w / out_h
    base = dict(src=src, out=out, duration=float(duration))
    if abs(sa - oa) / oa < 0.02:
        return Reframe("full", crop=src, **base)
    if oa > sa * 1.02:                                                # target wider than the source: show it all over blur
        return Reframe("blur", crop=src, backend=getattr(track, "backend", None), **base)
    cw, ch = min(src[0], _even(src_h * oa)), src[1]
    center = [(0.0, (src[0] - cw) / 2)]
    if track is None or not track.available or not track.samples:
        return Reframe("center", crop=(cw, ch), keys=center, **base)
    wins = blur_windows(track.samples, track.fps, duration)
    if sum(b - a for a, b in wins) >= WHOLE_BLUR * duration:
        return Reframe("blur", crop=(cw, ch), backend=track.backend, coverage=track.coverage, **base)
    path = segmented_path(track.samples, wins, track.fps, src[0], cw) or camera_path(track.samples, track.fps, src[0], cw)
    keys = _rdp([(round(t, 3), round(x, 1)) for t, x in window_keys(path, wins, duration)], 2.0)
    if keys[-1][0] < duration:
        keys.append((round(duration, 3), keys[-1][1]))
    return Reframe("crop", crop=(cw, ch), keys=keys, blur=wins, backend=track.backend, coverage=track.coverage, **base)
