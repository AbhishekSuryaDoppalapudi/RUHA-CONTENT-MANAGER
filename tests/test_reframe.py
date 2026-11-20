"""Speaker-aware reframing, video formats, NVENC with libx264 fallback and -14 LUFS loudness.

Unit tests use fake detectors and hand-made tracks; render tests run real FFmpeg on small synthetic videos whose true
positions are known; the last test follows a "walking pastor" built from the real sermon when that file is present."""
import importlib.util
import json
import random
import re
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from ruha.creative import formats
from ruha.services import media, reelvideo, reframe
from ruha.services.reframe import Face, Reframe, Track

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("reframe_check", ROOT / "scripts" / "reframe_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lufs(path) -> float:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(re.findall(r"I:\s+(-?[\d.]+)\s+LUFS", p.stderr)[-1])


def frames(path, w, h, fps=4):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True)
    return np.frombuffer(p.stdout, np.uint8).reshape(-1, h, w, 3)


def face(cx, cy=0.4, w=0.06, h=0.12, score=0.9):
    return Face(cx, cy, w, h, score)


def samples_from(pattern, cx=0.5, fps=5.0):
    """'XX..X' -> a face at ``cx`` for X, none for '.' (one character per sample)."""
    return [(i / fps, face(cx) if ch == "X" else None) for i, ch in enumerate(pattern)]


def eval_keys(keys, t):
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, a), (t1, b) in zip(keys, keys[1:]):
        if t <= t1:
            return a + (b - a) * ((t - t0) / (t1 - t0) if t1 > t0 else 1.0)
    return keys[-1][1]


# =============================================================== formats (formats.py)
def test_video_formats_reuse_the_existing_sizes():
    vf = formats.VIDEO_FORMATS
    assert list(vf) == ["9:16", "4:5", "1:1", "16:9"]
    assert vf["9:16"]["size"] == formats.FORMATS["reel_cover"]["size"] == (1080, 1920)
    assert vf["4:5"]["size"] == formats.FORMATS["feed_post"]["size"] == (1080, 1350)
    assert vf["1:1"]["size"] == formats.FORMATS["square_post"]["size"] == (1080, 1080)
    assert vf["16:9"]["size"] == (1920, 1080)
    assert "16:9" not in formats.FORMATS and "video_landscape" not in formats.FORMATS      # Creative Studio's list is untouched


def test_video_size_scales_and_stays_even():
    assert {k: formats.video_size(k) for k in formats.VIDEO_FORMATS} == {k: v["size"] for k, v in formats.VIDEO_FORMATS.items()}
    small = {k: formats.video_size(k, 270, 480) for k in formats.VIDEO_FORMATS}
    assert small == {"9:16": (270, 480), "4:5": (270, 338), "1:1": (270, 270), "16:9": (480, 270)}
    assert all(w % 2 == 0 and h % 2 == 0 for w, h in small.values())
    assert formats.video_size("9:16", 720, 1280) == (720, 1280)                 # an explicit height is honoured for 9:16


def test_video_format_ids_order_and_default():
    assert formats.video_format_ids(["16:9", "9:16", "1:1"]) == ["9:16", "1:1", "16:9"]
    assert formats.video_format_ids(["1:1", "1:1", "bogus"]) == ["1:1"]
    assert formats.video_format_ids([]) == ["9:16"] and formats.video_format_ids(None) == ["9:16"] and formats.video_format_ids("9:16") == ["9:16"]


def test_chosen_formats_primary_first():
    ch = reelvideo.chosen({"reel.formats": ["16:9", "4:5"], "render.width": 540, "render.height": 960})
    assert [c["id"] for c in ch] == ["4:5", "16:9"] and ch[0]["primary"] and not ch[1]["primary"]
    assert ch[0]["size"] == (540, 676) and ch[1]["size"] == (960, 540)
    assert [c["tag"] for c in ch] == ["", "_16x9"] and ch[1]["file"] == "16x9"
    assert reelvideo.chosen({})[0]["id"] == "9:16"


def test_caption_style_scales_with_the_format():
    st = {"size": 78, "margin_v": 300, "font": "X"}
    assert reelvideo.style_for(st, (1080, 1920), (1080, 1920)) == st            # the main format is unchanged
    sq = reelvideo.style_for(st, (1080, 1080), (1080, 1920))
    assert sq["size"] == 78 and sq["margin_v"] == round(300 * 1080 / 1920) and sq["font"] == "X"
    wide = reelvideo.style_for(st, (1920, 1080), (1080, 1920))
    assert wide["size"] == 78 and wide["margin_v"] == round(300 * 1080 / 1920)
    small = reelvideo.style_for(st, (135, 135), (270, 480))
    assert small["size"] == 39 and small["margin_v"] >= 10


# =============================================================== settings API
def test_format_settings_validation(client):
    put = lambda **v: client.put("/api/settings", json={"values": v}).status_code
    assert put(**{"reel.formats": ["9:16", "4:5", "1:1", "16:9"]}) == 200 and put(**{"reel.formats": ["1:1"]}) == 200
    for bad in ([], ["3:2"], ["9:16", "9:16"], "9:16", None, ["9:16", 5], ["9:16", "4:5", "1:1", "16:9", "9:16"]):
        assert put(**{"reel.formats": bad}) == 400, bad
    assert put(**{"reel.face_tracking": False}) == 200 and put(**{"reel.face_tracking": "yes"}) == 400
    for d in ("auto", "mediapipe", "opencv"):
        assert put(**{"reel.face_detector": d}) == 200
    assert put(**{"reel.face_detector": "dlib"}) == 400
    v = client.get("/api/settings").json()["values"]
    assert v["reel.formats"] == ["1:1"] and v["reel.face_tracking"] is False and v["reel.face_detector"] == "opencv"


def test_defaults_keep_the_old_behaviour():
    from ruha.config import DEFAULT_CONFIG
    d = {k: v["value"] for k, v in DEFAULT_CONFIG.items()}
    assert d["reel.formats"] == ["9:16"] and d["reel.face_tracking"] is True and d["reel.face_detector"] == "auto"


# =============================================================== choosing the speaker
def test_pick_speaker():
    big, small = face(0.3, w=0.08, h=0.16), face(0.7, w=0.03, h=0.06)
    assert reframe.pick_speaker([small, big], None) is big
    assert reframe.pick_speaker([], None) is None and reframe.pick_speaker([face(0.5, score=0.3)], None) is None
    prev = face(0.7, w=0.05, h=0.1)
    near_but_smaller = face(0.72, w=0.065, h=0.13)                   # almost as big as the biggest, close to who was tracked
    assert reframe.pick_speaker([big, near_but_smaller], prev) is near_but_smaller
    tiny_near = face(0.71, w=0.02, h=0.04)                           # far smaller: the big face wins
    assert reframe.pick_speaker([big, tiny_near], prev) is big


# =============================================================== the camera path
SRC_W, CROP_W = 1280, 405


def cam(pattern_faces, fps=5.0):
    return reframe.camera_path([(i / fps, f) for i, f in enumerate(pattern_faces)], fps, SRC_W, CROP_W)


def test_camera_holds_still_inside_the_dead_zone():
    rnd = random.Random(1)
    path = cam([face(0.5 + rnd.uniform(-0.01, 0.01)) for _ in range(60)])        # +-13 px of jitter, dead zone is 40 px
    xs = [x for _, x in path]
    assert max(xs) - min(xs) < 1.0
    assert abs(xs[0] - (0.5 * SRC_W - CROP_W / 2)) < 30


def test_camera_follows_a_walking_speaker_smoothly():
    path = cam([face(0.3 + 0.4 * i / 39) for i in range(40)])                    # walks right across 40 % of the frame in 8 s
    xs = [x for _, x in path]
    assert all(b >= a - 1e-6 for a, b in zip(xs, xs[1:]))                       # no jitter backwards
    assert max(b - a for a, b in zip(xs, xs[1:])) < 0.25 * CROP_W               # no sudden jumps
    fx = (0.3 + 0.4) * SRC_W
    assert fx - (1 - reframe.FACE_MARGIN) * CROP_W - 1 <= xs[-1] <= fx - reframe.FACE_MARGIN * CROP_W + 1


def test_the_speaker_is_never_cut_off():
    faces = [face(0.2)] * 10 + [face(0.8)] * 10                                    # he jumps across the stage
    for (t, x), f in zip(cam(faces), faces):
        fx = f.cx * SRC_W
        assert x + reframe.FACE_MARGIN * CROP_W - 1 <= fx <= x + (1 - reframe.FACE_MARGIN) * CROP_W + 1


def test_camera_stays_inside_the_frame():
    for cx in (0.01, 0.99):
        xs = [x for _, x in cam([face(cx)] * 20)]
        assert min(xs) >= 0 and max(xs) <= SRC_W - CROP_W


def test_short_gaps_hold_the_camera():
    faces = [face(0.4)] * 10 + [None] * 3 + [face(0.4)] * 10
    xs = [x for _, x in cam(faces)]
    assert max(xs) - min(xs) < 1.0


# =============================================================== blur windows
def test_blur_windows_need_a_long_enough_gap():
    s = lambda p: reframe.blur_windows(samples_from(p), 5.0, len(p) / 5.0)
    assert s("XXXXXXXXXX") == []
    assert s("XXXX..XXXX") == []                                                  # 0.4 s gap: the camera just holds
    assert s("XXXX......XXXX") == [(0.7, 1.9)]                                    # 1.2 s gap: blur fill
    assert s("......XXXX") == [(0.0, 1.1)]                                        # no speaker at the start
    assert s("XXXX......") == [(0.7, 2.0)]                                        # ... or at the end (to the end of the clip)
    assert s("X......XXX......X") == [(0.1, 3.1)]                                  # two gaps 0.6 s apart are merged
    assert s("X......XXXXXXXXX......X") == [(0.1, 1.3), (3.1, 4.3)]                # 1.8 s apart: two windows


# =============================================================== the plan
def test_plan_modes():
    t = Track(samples_from("X" * 50), "fake")
    assert reframe.plan(t, 1920, 1080, 1920, 1080, 10).mode == "full"
    assert reframe.plan(t, 1920, 1080, 1280, 720, 10).mode == "full"             # same shape
    assert reframe.plan(t, 720, 1280, 1920, 1080, 10).mode == "blur"             # a phone video into 16:9: the whole frame over blur
    rf = reframe.plan(t, 1920, 1080, 1080, 1920, 10)
    assert rf.mode == "crop" and rf.crop == (608, 1080) and rf.blur == [] and rf.keys[0][0] == 0
    assert reframe.plan(None, 1920, 1080, 1080, 1920, 10).mode == "center"
    assert reframe.plan(Track([], None, available=False), 1920, 1080, 1080, 1920, 10).mode == "center"
    assert reframe.plan(Track([], "fake"), 1920, 1080, 1080, 1920, 10).mode == "center"
    none = Track(samples_from("." * 50), "fake")
    rf = reframe.plan(none, 1920, 1080, 1080, 1920, 10)
    assert rf.mode == "blur" and rf.blur_seconds == 10.0                         # nobody anywhere: the whole clip is blur fill


def test_plan_crop_sizes_per_format():
    t = Track(samples_from("X" * 50), "fake")
    got = {fid: reframe.plan(t, 1280, 720, *formats.video_size(fid), 10) for fid in formats.VIDEO_FORMATS}
    assert got["9:16"].crop == (404, 720) and got["4:5"].crop == (576, 720) and got["1:1"].crop == (720, 720)
    assert got["16:9"].mode == "full"
    assert all(g.crop[0] % 2 == 0 for g in got.values())


def test_plan_with_a_gap_has_a_blur_window_and_a_ready_camera():
    # the speaker stands at the far left, leaves for 3 s, and comes back at the far right
    pattern = [face(0.2)] * 15 + [None] * 15 + [face(0.8)] * 15
    rf = reframe.plan(Track([(i / 5, f) for i, f in enumerate(pattern)], "fake"), 1280, 720, 1080, 1920, 9.0)
    assert rf.mode == "crop" and len(rf.blur) == 1
    (a, b), = rf.blur
    assert abs(a - 2.9) < 0.11 and abs(b - 5.9) < 0.11 and rf.blur_seconds == round(b - a, 2)
    cw = rf.crop[0]
    want = min(1280 - cw, max(0, 0.8 * 1280 - cw / 2))
    assert abs(eval_keys(rf.keys, b) - want) < 2                                   # in position the moment the speaker is back
    assert abs(eval_keys(rf.keys, a) - eval_keys(rf.keys, 0.0)) < 40               # and had not moved when the window began
    ts = [t for t, _ in rf.keys]
    assert ts == sorted(ts) and ts[0] == 0 and ts[-1] == 9.0
    assert all(0 <= x <= 1280 - cw for _, x in rf.keys)
    s = rf.summary()
    assert s["mode"] == "crop" and s["backend"] == "fake" and s["blur_seconds"] == rf.blur_seconds and s["size"] == [1080, 1920]


def test_rdp_keeps_the_shape_with_few_keys():
    pts = [(i / 5, 100 + 2 * i) for i in range(50)]
    keys = reframe._rdp(pts, 1.0)
    assert len(keys) == 2 and keys[0] == pts[0] and keys[-1] == pts[-1]
    bent = [(i / 5, 100 + (2 * i if i < 25 else 50 - 2 * (i - 25) + 100)) for i in range(50)]
    assert 3 <= len(reframe._rdp(bent, 1.0)) <= 5


# =============================================================== the FFmpeg expressions
def test_x_expr_matches_the_keyframes():
    rnd = random.Random(7)
    keys, t = [], 0.0
    for _ in range(12):
        keys.append((round(t, 3), round(rnd.uniform(0, 800), 1)))
        t += rnd.uniform(0.3, 1.5)
    expr = media.x_expr(keys)
    clip = lambda a, lo, hi: max(lo, min(hi, a))
    for probe in [0, 0.1, 0.5, 1.0, 2.2, 3.7, 5.0, 8.0, 12.0, 30.0]:
        got = eval(expr, {"clip": clip, "t": probe})                                # our own generated expression
        assert abs(got - eval_keys(keys, probe)) < 0.1, (probe, expr)
    assert media.x_expr([(0, 50.0), (5, 50.0)]) == "50.0"                          # a still camera is a plain number


def test_graphs_per_mode():
    g = media.reframe_graph(None, 270, 480)
    assert "force_original_aspect_ratio=increase" in g and g.endswith("[b0]")
    center = Reframe("center", (1280, 720), (270, 480), (404, 720), keys=[(0, 438)])
    assert media.reframe_graph(center, 270, 480) == g
    crop = Reframe("crop", (1280, 720), (270, 480), (404, 720), keys=[(0, 100.0), (3, 300.0)])
    gc = media.reframe_graph(crop, 270, 480)
    assert "crop=404:720:x='" in gc and "overlay" not in gc and "boxblur" not in gc and gc.endswith("[b0]")
    crop.blur = [(1.0, 2.0), (4.0, 5.5)]
    gb = media.reframe_graph(crop, 270, 480)
    assert "boxblur" in gb and "enable='between(t,1.000,2.000)+between(t,4.000,5.500)'" in gb and gb.endswith("[b0]")
    whole = Reframe("blur", (1280, 720), (270, 480), (404, 720))
    gw = media.reframe_graph(whole, 270, 480)
    assert "boxblur" in gw and "crop=404" not in gw and gw.endswith("[b0]")


# =============================================================== detectors
class FakeDet:
    def __init__(self, name, found):
        self.name, self.found, self.calls = name, found, 0

    def detect(self, frame):
        i, self.calls = self.calls, self.calls + 1
        r = self.found(i, frame)
        return [r] if r is not None else []


def test_a_good_first_detector_is_enough(sample_video):
    a, b = FakeDet("a", lambda i, f: face(0.5)), FakeDet("b", lambda i, f: face(0.6))
    trk = reframe.detect_track(sample_video, 0, 6, (640, 360), detectors=[a, b])
    assert len(trk.samples) == 30 and trk.coverage == 1.0 and trk.backend == "a" and b.calls == 0


def test_a_weak_first_detector_is_merged_with_the_second(sample_video):
    a = FakeDet("a", lambda i, f: face(0.4) if i % 2 == 0 else None)
    b = FakeDet("b", lambda i, f: face(0.6) if i % 2 == 1 else None)
    trk = reframe.detect_track(sample_video, 0, 6, (640, 360), detectors=[a, b])
    assert trk.coverage == 1.0 and trk.backend == "a+b" and a.calls == b.calls == 30
    assert [f.cx for _, f in trk.samples][:4] == [0.4, 0.6, 0.4, 0.6]                # a's face where it has one, else b's


def test_no_detector_means_no_track(sample_video):
    trk = reframe.detect_track(sample_video, 0, 4, (640, 360), detectors=[])
    assert trk.available is False and trk.samples == [] and trk.notes
    assert reframe.plan(trk, 640, 360, 270, 480, 4).mode == "center"


def test_a_crashing_detector_does_not_lose_the_reel(sample_video):
    def boom(i, f):
        raise RuntimeError("model crashed")
    trk = reframe.detect_track(sample_video, 0, 3, (640, 360), detectors=[FakeDet("x", boom)])
    assert trk.available and trk.coverage == 0.0 and len(trk.samples) == 15


def test_sampling_rate_and_times(sample_video):
    seen = []
    det = FakeDet("x", lambda i, f: seen.append(f.shape) or None)
    trk = reframe.detect_track(sample_video, 10, 14, (640, 360), detectors=[det])
    assert len(trk.samples) == 20 and trk.fps == 5.0 and [round(t, 2) for t, _ in trk.samples][:3] == [0.0, 0.2, 0.4]
    assert seen[0] == (360, 640, 3)                                                     # decoded 640 wide, RGB


def test_cancel_stops_detection(sample_video):
    with pytest.raises(RuntimeError, match="Cancelled"):
        reframe.detect_track(sample_video, 0, 6, (640, 360), detectors=[FakeDet("x", lambda i, f: None)], cancel=lambda: True)


def test_tile_search_finds_small_faces_for_mediapipe_only():
    frame = np.zeros((360, 640, 3), np.uint8)

    class Tiny:
        def __init__(self, name):
            self.name = name

        def detect(self, f):
            return [] if f.shape[1] == 640 else [Face(0.5, 0.5, 0.2, 0.2, 0.9)]
    got = reframe.find_faces(Tiny("mediapipe"), frame)
    assert sorted(round(f.cx, 2) for f in got) == [0.28, 0.5, 0.72] and all(abs(f.w - 0.2 * 360 / 640) < 1e-6 for f in got)
    assert reframe.find_faces(Tiny("opencv"), frame) == []


def test_loading_detectors_in_order_with_reasons(monkeypatch):
    calls = {"mp": 0}

    def mp():
        calls["mp"] += 1
        raise reframe.DetectorUnavailable("blocked by policy")
    monkeypatch.setattr(reframe, "FACTORIES", {"mediapipe": mp, "opencv": lambda: FakeDet("opencv", lambda i, f: None)})
    reframe.reset_pool()
    dets, why = reframe.load_detectors("auto")
    assert [d.name for d in dets] == ["opencv"] and why == ["mediapipe: blocked by policy"]
    reframe.load_detectors("auto")
    assert calls["mp"] == 1                                                          # a failed start is not retried at once
    reframe._POOL_AT["mediapipe"] -= 400
    reframe.load_detectors("auto")
    assert calls["mp"] == 2                                                          # ... but is tried again after a while
    assert reframe.load_detectors("mediapipe") == ([], ["mediapipe: blocked by policy"])
    assert [d.name for d in reframe.load_detectors("opencv")[0]] == ["opencv"]
    st = reframe.detector_status("auto")
    assert st["active"] == "opencv" and st["detectors"]["mediapipe"]["available"] is False and "policy" in st["detectors"]["mediapipe"]["reason"]
    reframe.reset_pool()


def test_mediapipe_problems_become_detector_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "mediapipe", None)                              # not installed
    with pytest.raises(reframe.DetectorUnavailable, match="not available"):
        reframe.MediaPipeDetector()
    mp, tasks, py, vision = (types.ModuleType(n) for n in ("mediapipe", "mediapipe.tasks", "mediapipe.tasks.python", "mediapipe.tasks.python.vision"))
    py.BaseOptions = lambda **k: k
    vision.FaceDetectorOptions = lambda **k: k

    class Blocked:
        @staticmethod
        def create_from_options(o):
            raise OSError("An Application Control policy has blocked this file")
    vision.FaceDetector = Blocked
    tasks.python, py.vision = py, vision
    mp.tasks = tasks
    for name, mod in (("mediapipe", mp), ("mediapipe.tasks", tasks), ("mediapipe.tasks.python", py), ("mediapipe.tasks.python.vision", vision)):
        monkeypatch.setitem(sys.modules, name, mod)
    with pytest.raises(reframe.DetectorUnavailable, match="could not start.*OSError.*Application Control"):
        reframe.MediaPipeDetector()


def test_opencv_problems_become_detector_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    with pytest.raises(reframe.DetectorUnavailable, match="OpenCV is not available"):
        reframe.YuNetDetector()


def test_bundled_models_exist():
    assert reframe.BLAZE_MODEL.stat().st_size > 100_000 and reframe.YUNET_MODEL.stat().st_size > 100_000
    assert reframe.BLAZE_MODEL.read_bytes()[4:8] == b"TFL3"


def test_the_real_yunet_runs_and_finds_nothing_in_an_empty_frame():
    pytest.importorskip("cv2")
    det = reframe.YuNetDetector()
    assert det.detect(np.full((360, 640, 3), 40, np.uint8)) == []


# =============================================================== the tracking step never fails a reel
def test_face_track_switched_off_or_unavailable_or_crashing(monkeypatch):
    logs = []
    log = lambda m, lvl="INFO": logs.append((lvl, m))
    cfg = {"reel.face_tracking": False}
    monkeypatch.setattr(reframe, "detect_track", lambda *a, **k: pytest.fail("must not run when switched off"))
    assert reelvideo.face_track(cfg, "x.mp4", 0, 5, (640, 360), log) is None and logs == []
    cfg = {"reel.face_tracking": True}
    monkeypatch.setattr(reframe, "detect_track", lambda *a, **k: Track([], None, available=False, notes=["opencv: gone"]))
    assert reelvideo.face_track(cfg, "x.mp4", 0, 5, (640, 360), log) is None
    assert logs[-1][0] == "WARNING" and "centre crop" in logs[-1][1] and "opencv: gone" in logs[-1][1]

    def boom(*a, **k):
        raise ValueError("bad frame")
    monkeypatch.setattr(reframe, "detect_track", boom)
    assert reelvideo.face_track(cfg, "x.mp4", 0, 5, (640, 360), log) is None and "bad frame" in logs[-1][1]
    monkeypatch.setattr(reframe, "detect_track", lambda *a, **k: Track(samples_from("XXXX..XXXX"), "fake"))
    t = reelvideo.face_track(cfg, "x.mp4", 0, 5, (640, 360), log)
    assert t.coverage == 0.8 and "80%" in logs[-1][1] and "fake" in logs[-1][1]

    def cancelled(*a, **k):
        raise RuntimeError("Cancelled")
    monkeypatch.setattr(reframe, "detect_track", cancelled)
    with pytest.raises(RuntimeError, match="Cancelled"):
        reelvideo.face_track(cfg, "x.mp4", 0, 5, (640, 360), log)


# =============================================================== real FFmpeg: following a moving subject
@pytest.fixture(scope="module")
def stage(tmp_path_factory):
    """640x360, 10 s. A white 40x60 "speaker" walks right 0-5 s (x 40 -> 540), is absent 5-8 s, then stands at x 300.
    Red bars mark the left and right edges so a whole-frame (blur fill) picture is easy to tell from a crop."""
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("stage") / "stage.mp4"
    fc = ("[0:v]drawbox=x=0:y=0:w=20:h=360:color=red:t=fill,drawbox=x=620:y=0:w=20:h=360:color=red:t=fill[bg];"
          "[bg][1:v]overlay=x='if(lt(t,5),40+100*t,300)':y=150:enable='lt(t,5)+gte(t,8)'[v]")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x203040:s=640x360:r=25:d=10", "-f", "lavfi", "-i", "color=c=white:s=40x60:r=25:d=10",
                    "-f", "lavfi", "-i", "sine=f=440:d=10", "-filter_complex", fc, "-map", "[v]", "-map", "2:a", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True, capture_output=True)
    return out


def truth_track(fps=5.0):
    """The exact detections a perfect detector would give on the stage video."""
    out = []
    for i in range(int(10 * fps)):
        t = i / fps
        if 5 <= t < 8:
            out.append((t, None))
        else:
            x = 40 + 100 * t if t < 5 else 300
            out.append((t, Face((x + 20) / 640, 0.5, 40 / 640, 60 / 360, 0.95)))
    return Track(out, "truth")


def white_box_center(frame):
    m = (frame > 235).all(axis=2)
    cols = np.where(m.sum(axis=0) > 8)[0]
    return None if len(cols) < 4 else float(cols.mean() / frame.shape[1])


def whole_frame(frame):
    """True for a blur-fill picture: the red bars at BOTH edges of the source are in the frame (a crop is too narrow for that)."""
    left, right = red_edges(frame)
    return left > 0.3 and right > 0.3


def red_edges(frame):
    h, w = frame.shape[:2]
    band = frame[int(h * 0.42):int(h * 0.58)]                                       # the rows the full frame occupies in a tall output
    red = (band[:, :, 0] > 190) & (band[:, :, 1] < 70) & (band[:, :, 2] < 70)
    return red[:, : max(3, w // 30)].mean(), red[:, -max(3, w // 30):].mean()


def test_the_camera_follows_the_speaker_and_blur_fills_his_absence(stage, tmp_path):
    rf = reframe.plan(truth_track(), 640, 360, 270, 480, 10.0)
    assert rf.mode == "crop" and len(rf.blur) == 1
    out = media.render_reel(stage, 0, 10, tmp_path / "r.mp4", width=270, height=480, burn_subtitles=False, reframe=rf, crf=28, preset="ultrafast")
    info = media.probe(out)
    assert (info["width"], info["height"]) == (270, 480) and 9.7 < info["duration"] < 10.4 and info["audio_tracks"] == 1
    fr = frames(out, 270, 480, fps=4)
    seen, tracked = 0, 0
    for i, f in enumerate(fr):
        t = i / 4
        if 5.3 < t < 7.7:                                                          # nobody on stage: the whole frame over blur
            assert whole_frame(f), (t, red_edges(f))
            assert white_box_center(f) is None
        elif t < 4.7 or t > 8.3:                                                   # speaker present: a tight crop, centred on him
            assert not whole_frame(f), (t, red_edges(f))
            c = white_box_center(f)
            tracked += c is not None
            assert c is not None and 0.2 < c < 0.8, (t, c)
            seen += 1
    assert seen > 20 and tracked == seen


def test_the_old_centre_crop_would_have_lost_him(stage, tmp_path):
    out = media.render_reel(stage, 0, 10, tmp_path / "c.mp4", width=270, height=480, burn_subtitles=False, crf=28, preset="ultrafast")
    fr = frames(out, 270, 480, fps=4)
    walk = [f for i, f in enumerate(fr) if i / 4 < 4.5]
    visible = sum(white_box_center(f) is not None for f in walk)
    assert visible < 0.6 * len(walk)                                                # he spends much of the walk outside the middle crop


def test_every_format_comes_out_at_its_size(stage, tmp_path):
    for fid in formats.VIDEO_FORMATS:
        w, h = formats.video_size(fid, 270, 480)
        rf = reframe.plan(truth_track(), 640, 360, w, h, 10.0)
        out = media.render_reel(stage, 0, 6, tmp_path / f"{fid.replace(':', 'x')}.mp4", width=w, height=h, burn_subtitles=False, reframe=rf,
                                crf=30, preset="ultrafast")
        info = media.probe(out)
        assert (info["width"], info["height"]) == (w, h), fid
        assert 5.7 < info["duration"] < 6.4


def test_blur_fill_for_a_whole_clip_and_for_a_wider_target(stage, tmp_path):
    whole = reframe.plan(Track([(i / 5, None) for i in range(50)], "x"), 640, 360, 270, 480, 10.0)
    assert whole.mode == "blur"
    out = media.render_reel(stage, 0, 4, tmp_path / "w.mp4", width=270, height=480, burn_subtitles=False, reframe=whole, crf=30, preset="ultrafast")
    for f in frames(out, 270, 480, fps=2):
        assert whole_frame(f)                                                       # the full frame is there, centred
    # a portrait source into 16:9 is also whole-frame blur fill
    portrait = tmp_path / "p.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=25:duration=2", "-f", "lavfi", "-i", "sine=f=300:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(portrait)], check=True, capture_output=True)
    assert reframe.plan(None, 360, 640, 480, 270, 2.0).mode == "blur"               # even with no track: a centre crop of a portrait video would be a thin slice
    rf = reframe.plan(Track(samples_from("X" * 10), "x"), 360, 640, 480, 270, 2.0)
    assert rf.mode == "blur"
    out = media.render_reel(portrait, 0, 2, tmp_path / "p169.mp4", width=480, height=270, burn_subtitles=False, reframe=rf, crf=30, preset="ultrafast")
    assert (media.probe(out)["width"], media.probe(out)["height"]) == (480, 270)


def test_captions_and_cutaways_still_work_with_a_reframed_picture(stage, tmp_path):
    from PIL import Image
    img = tmp_path / "cut.png"
    Image.new("RGB", (64, 64), (200, 40, 40)).save(img)
    rf = reframe.plan(truth_track(), 640, 360, 270, 480, 10.0)
    out = media.render_reel(stage, 0, 8, tmp_path / "x.mp4", width=270, height=480, reframe=rf, crf=30, preset="ultrafast",
                            cues=[(0.5, 3.0, "Grace is the free gift of God")], cutaways=[{"at": 3.0, "dur": 1.5, "image": str(img)}],
                            subtitle_style={"size": 20})
    assert media.probe(out)["duration"] > 7.5


# =============================================================== loudness -14 LUFS
def tone(path, seconds, volume):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=f=330:d={seconds}", "-af", f"volume={volume}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)
    return path


@pytest.mark.parametrize("volume", [0.02, 0.3, 1.0])
def test_reels_come_out_at_minus_14_lufs(tmp_path, volume):
    src = tone(tmp_path / "t.mp4", 8, volume)
    out = media.render_reel(src, 1, 7, tmp_path / "o.mp4", width=180, height=320, burn_subtitles=False, crf=30, preset="ultrafast")
    assert abs(lufs(out) - (-14.0)) < 1.0, lufs(out)


def test_two_pass_filter_uses_the_measurement(tmp_path):
    src = tone(tmp_path / "t.mp4", 6, 0.1)
    m = media.measure_loudness(src, 0, 5)
    assert m and set(m) == {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"} and m["input_i"] < -14
    f = media.loudness_filter(src, 0, 5)
    assert f.startswith("loudnorm=I=-14.0:TP=-1.5:LRA=11.0:measured_I=") and f.endswith(":linear=true") and "offset=" in f
    assert media.loudness_filter(measured=m).count("measured_I") == 1
    assert media.loudness_filter() == "loudnorm=I=-14.0:TP=-1.5:LRA=11.0"            # nothing to measure: single pass


def test_no_audio_or_silence_falls_back_to_single_pass(tmp_path):
    silent = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(silent)], check=True, capture_output=True)
    mute = tmp_path / "m.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mute)],
                   check=True, capture_output=True)
    assert media.measure_loudness(silent, 0, 2) is None and media.measure_loudness(mute, 0, 2) is None
    assert media.loudness_filter(silent, 0, 2) == "loudnorm=I=-14.0:TP=-1.5:LRA=11.0"
    out = media.render_reel(mute, 0, 2, tmp_path / "o.mp4", width=180, height=320, burn_subtitles=False, crf=30, preset="ultrafast")
    assert out.exists() and media.probe(out)["audio_tracks"] == 0


# =============================================================== NVENC with libx264 fallback
def test_nvenc_check_needs_a_real_encode(monkeypatch):
    seen = []

    def fake(cmd, **kw):
        seen.append(cmd)
        if "-encoders" in cmd:
            return types.SimpleNamespace(stdout=" V....D h264_nvenc  NVIDIA NVENC", returncode=0)
        return types.SimpleNamespace(stdout="", returncode=fake.rc)
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(media.subprocess, "run", fake)
    fake.rc = 1                                                                     # compiled in, but no NVIDIA GPU
    media._NVENC.update(ok=None, at=0.0)
    assert media.nvenc_available() is False and any("h264_nvenc" in c and "-f" in c and "lavfi" in c for c in seen)
    n = len(seen)
    assert media.nvenc_available() is False and len(seen) == n                       # remembered
    fake.rc = 0
    media._NVENC.update(ok=None, at=0.0)
    assert media.nvenc_available() is True
    media._NVENC.update(ok=None, at=0.0)
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(stdout="libx264 only", returncode=0))
    assert media.nvenc_available() is False                                         # no NVENC encoder at all
    media._NVENC.update(ok=None, at=0.0)


def test_encoder_arguments():
    gpu, cpu = media._encoder_args(23, "veryfast", True), media._encoder_args(23, "veryfast", False)
    assert gpu[:2] == ["-c:v", "h264_nvenc"] and "-cq" in gpu and gpu[gpu.index("-cq") + 1] == "23" and gpu[-2:] == ["-pix_fmt", "yuv420p"]
    assert cpu[:2] == ["-c:v", "libx264"] and "-crf" in cpu


def test_a_failed_gpu_encode_is_redone_with_libx264(stage, tmp_path, monkeypatch):
    real, cmds = media.run, []

    def run(cmd, cwd=None, timeout=None):
        cmds.append(list(cmd))
        if "h264_nvenc" in cmd:
            raise media.MediaError("ffmpeg failed: OpenEncodeSessionEx failed: no capable devices found")
        return real(cmd, cwd=cwd, timeout=timeout)
    monkeypatch.setattr(media, "run", run)
    out = media.render_reel(stage, 0, 3, tmp_path / "g.mp4", width=270, height=480, burn_subtitles=False, use_gpu=True, crf=28, preset="ultrafast")
    enc = [c for c in cmds if "-c:v" in c]
    assert len(enc) == 2 and "h264_nvenc" in enc[0] and "libx264" in enc[1] and "h264_nvenc" not in enc[1]
    i = enc[1].index("-pix_fmt")
    assert enc[1][i:i + 3] == ["-pix_fmt", "yuv420p", "-af"]                         # no stray argument left behind
    assert out.exists() and media.probe(out)["duration"] > 2.5


def test_a_cpu_encode_error_is_not_retried(stage, tmp_path, monkeypatch):
    calls = []

    def run(cmd, cwd=None, timeout=None):
        calls.append(cmd)
        raise media.MediaError("broken")
    monkeypatch.setattr(media, "run", run)
    with pytest.raises(media.MediaError):
        media.render_reel(stage, 0, 3, tmp_path / "g.mp4", width=270, height=480, burn_subtitles=False, use_gpu=False)
    assert len(calls) == 1


def test_nvenc_is_used_unless_cpu_only_is_chosen(monkeypatch):
    from ruha import handlers
    monkeypatch.setattr(media, "nvenc_available", lambda: True)
    job = lambda **c: types.SimpleNamespace(cfg={"render.quality": "balanced", **c})
    assert handlers._render_args(None, job())["use_gpu"] is True
    assert handlers._render_args(None, job(**{"gpu.mode": "auto"}))["use_gpu"] is True
    assert handlers._render_args(None, job(**{"gpu.mode": "gpu"}))["use_gpu"] is True
    assert handlers._render_args(None, job(**{"gpu.mode": "cpu"}))["use_gpu"] is False
    monkeypatch.setattr(media, "nvenc_available", lambda: False)
    assert handlers._render_args(None, job())["use_gpu"] is False


# =============================================================== through the job pipeline
class CentreFace:
    name = "fake"

    def detect(self, frame):
        return [Face(0.5, 0.4, 0.08, 0.16, 0.95)]


@pytest.fixture()
def fake_detectors(monkeypatch):
    def blocked():
        raise reframe.DetectorUnavailable("blocked by policy")
    monkeypatch.setattr(reframe, "FACTORIES", {"mediapipe": blocked, "opencv": CentreFace})
    reframe.reset_pool()
    yield
    reframe.reset_pool()


def reels(client, pid, kind="short_reel"):
    return client.get(f"/api/assets?project_id={pid}&kind={kind}").json()


def raw_meta(ctx, aid):
    from ruha.models import GeneratedAsset
    with ctx.db.session() as s:
        a = s.get(GeneratedAsset, aid)
        return dict(a.meta), a.file_path


def test_default_settings_make_one_vertical_file_per_reel(client, ctx, processed):
    for r in reels(client, processed) + reels(client, processed, "main_reel"):
        assert r["meta"]["format"] == "9:16" and r["meta"]["variants"] == {}
        w = media.probe(raw_meta(ctx, r["id"])[1])
        assert (w["width"], w["height"]) == (270, 480)


def test_several_formats_per_reel(client, ctx, project_with_video):
    pid = project_with_video
    assert client.put("/api/settings", json={"values": {"reel.formats": ["16:9", "1:1", "9:16"]}}).status_code == 200
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    got = reels(client, pid) + reels(client, pid, "main_reel")
    assert len(got) == 4
    for r in got:
        public = r["meta"]
        assert public["format"] == "9:16" and set(public["variants"]) == {"1:1", "16:9"}
        assert all("file" not in v and v["has_file"] for v in public["variants"].values())      # no server paths in the API
        meta, main = raw_meta(ctx, r["id"])
        sizes = {fid: (v["width"], v["height"]) for fid, v in meta["variants"].items()}
        assert sizes == {"1:1": (270, 270), "16:9": (480, 270)}
        for fid, v in meta["variants"].items():
            info = media.probe(v["file"])
            assert (info["width"], info["height"]) == sizes[fid] and info["audio_tracks"] == 1 and Path(v["file"]).stat().st_size == v["size_bytes"]
        assert (media.probe(main)["width"], media.probe(main)["height"]) == (270, 480)
    r = got[0]
    assert client.get(f"/api/assets/{r['id']}/variants/1:1/file").status_code == 200
    assert client.get(f"/api/assets/{r['id']}/variants/16:9/file?download=true").headers["content-disposition"].endswith('16x9.mp4"')
    assert client.get(f"/api/assets/{r['id']}/variants/9:16/file").status_code == 200
    assert client.get(f"/api/assets/{r['id']}/variants/main/file").status_code == 200
    assert client.get(f"/api/assets/{r['id']}/variants/4:5/file").status_code == 404
    assert client.get("/api/assets/99999/variants/1:1/file").status_code == 404


def test_primary_is_the_first_chosen_format_in_canonical_order(client, ctx, project_with_video):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reel.formats": ["4:5", "16:9"]}})
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    r = reels(client, pid, "main_reel")[0]
    assert r["meta"]["format"] == "4:5" and set(r["meta"]["variants"]) == {"16:9"}
    w = media.probe(raw_meta(ctx, r["id"])[1])
    assert (w["width"], w["height"]) == (270, 338)


def test_deleting_a_reel_removes_every_format_file(client, ctx, project_with_video):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reel.formats": ["9:16", "1:1"]}})
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    r = reels(client, pid)[0]
    meta, main = raw_meta(ctx, r["id"])
    var = meta["variants"]["1:1"]["file"]
    assert Path(var).exists() and Path(main).exists()
    assert client.delete(f"/api/assets/{r['id']}").status_code == 200
    assert not Path(var).exists() and not Path(main).exists()


def test_re_rendering_replaces_the_variants(client, ctx, project_with_video):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reel.formats": ["9:16", "1:1"]}})
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    r = reels(client, pid, "main_reel")[0]
    old = raw_meta(ctx, r["id"])[0]["variants"]["1:1"]["file"]
    assert client.post(f"/api/assets/{r['id']}/regenerate", json={"start": 10, "end": 70}).status_code == 202
    ctx.queue.drain()
    meta, main = raw_meta(ctx, r["id"])
    new = meta["variants"]["1:1"]["file"]
    assert new != old and new.endswith("_v2.mp4") and Path(new).exists() and not Path(old).exists()
    assert meta["start"] == 10 and meta["end"] == 70
    assert (media.probe(new)["width"], media.probe(new)["height"]) == (270, 270)
    assert client.get(f"/api/assets/{r['id']}").json()["version"] == 2


def test_face_tracking_in_a_reel_job(client, ctx, project_with_video, fake_detectors):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reel.face_tracking": True}})
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    r = reels(client, pid)[0]
    rf = r["meta"]["reframe"]
    assert rf["mode"] == "crop" and rf["backend"] == "fake" and rf["faces"] == 1.0 and rf["blur_seconds"] == 0
    jobs = client.get(f"/api/jobs?project_id={pid}").json()["items"]
    logs = [l["message"] for j in jobs for l in client.get(f"/api/jobs/{j['id']}").json()["logs"]]
    assert any("Speaker found in 100%" in m for m in logs)
    assert all(j["status"] == "completed" for j in jobs)


def test_no_detector_means_a_centre_crop_and_a_warning_not_a_failure(client, ctx, project_with_video, monkeypatch):
    def blocked():
        raise reframe.DetectorUnavailable("not installed")
    monkeypatch.setattr(reframe, "FACTORIES", {"mediapipe": blocked, "opencv": blocked})
    reframe.reset_pool()
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reel.face_tracking": True}})
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    assert reels(client, pid)[0]["meta"]["reframe"]["mode"] == "center"
    jobs = client.get(f"/api/jobs?project_id={pid}").json()["items"]
    assert all(j["status"] == "completed" for j in jobs)
    warn = [l for j in jobs for l in client.get(f"/api/jobs/{j['id']}").json()["logs"] if l["level"] == "WARNING" and "centre crop" in l["message"]]
    assert warn and "not installed" in warn[0]["message"]
    reframe.reset_pool()


def test_face_tracking_off_skips_detection(client, ctx, project_with_video, monkeypatch):
    monkeypatch.setattr(reframe, "detect_track", lambda *a, **k: pytest.fail("detection must not run"))
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    assert reels(client, pid)[0]["meta"]["reframe"]["mode"] == "center"


def test_status_reports_video_settings(client):
    s = client.get("/api/system/status").json()
    rv = s["reel_video"]
    assert rv["formats"] == ["9:16"] and rv["face_tracking"] is False and rv["loudness_lufs"] == -14
    assert rv["encoder"] in ("h264_nvenc", "libx264") and set(rv["face_detector"]) >= {"active", "detectors"}


# =============================================================== the walking pastor (needs the real sermon)
SERMON = next(iter((ROOT / "ruha_data" / "projects").glob("*/source/*.mp4")), None) if (ROOT / "ruha_data" / "projects").exists() else None


@pytest.mark.skipif(SERMON is None, reason="the real sermon video is not on this machine")
def test_walking_pastor_demo_on_the_real_sermon(tmp_path):
    pytest.importorskip("cv2")
    script = load_script()
    reframe.reset_pool()
    demo = script.make_walking_demo(SERMON, tmp_path / "walk.mp4")
    rep = script.run_check(demo, 0.0, 0.0, ["9:16", "4:5", "1:1", "16:9"], base_width=540, detector="auto", use_gpu=False, out_dir=tmp_path)
    d = rep["detector"]
    assert d["available"] and 0.70 <= d["faces_found"] <= 0.80, d                      # on stage for 9 of the 12 seconds
    by = {f["format"]: f for f in rep["formats"]}
    for fid in ("9:16", "4:5", "1:1"):
        assert by[fid]["mode"] == "crop" and 2.4 <= by[fid]["blur_seconds"] <= 3.6, (fid, by[fid]["blur_seconds"])
    assert by["16:9"]["mode"] == "full"
    assert all(f["lufs"] is not None and abs(f["lufs"] + 14) < 1.0 for f in rep["formats"])
    out = Path(by["9:16"]["file"])
    w, h = formats.video_size("9:16", 540)
    det = reframe.YuNetDetector()
    fr = frames(out, w, h, fps=4)
    present = absent = 0
    for i, f in enumerate(fr):
        t = i / 4
        found = reframe.pick_speaker(det.detect(f), None)
        if t < 3.8 or t > 7.3:                                                         # the pastor is on stage
            if found is not None:
                present += 1
                assert 0.25 < found.cx < 0.75, (t, found.cx)                          # and the camera has him near the middle
        elif 4.3 < t < 6.7:                                                            # the empty stage: whole frame over blur
            absent += 1
            assert found is None
            assert whole_frame(f)
    assert present >= 0.8 * sum(1 for i in range(len(fr)) if i / 4 < 3.8 or i / 4 > 7.3) and absent >= 8
