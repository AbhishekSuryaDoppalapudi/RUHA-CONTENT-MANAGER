"""Best-frame thumbnails and designed thumbnails, posters that quote their clip, the automatic carousel, and a caption of its own
for every clip (with near-duplicates rejected)."""
import shutil
import subprocess
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from ruha.ai.embeddings import HashEmbedder
from ruha.models import GeneratedAsset, SourceVideo
from ruha.services import carousel, clipcopy, media, planner, quotes, reframe, thumbnail, thumbs
from ruha.services.reframe import Face


def seg(i, text, start=None, step=5.0):
    s = i * step if start is None else start
    return {"start": float(s), "end": float(s + step), "text": text}


# =============================================================== quotes.py - the strongest sentence, word for word
SEGS = [seg(0, "So today we are going to talk about something. Jesus said, I am the way, the truth and the life."),
        seg(1, "Nobody comes to the Father except through me. And that is the whole message."),
        seg(2, "Um you know I mean it is kind of hard to explain."),
        seg(3, "God loves you and he will never leave you.")]
FULL = " ".join(s["text"] for s in SEGS)


def test_sentences_run_across_segment_boundaries():
    segs = [seg(0, "Brother Saul, the Lord Jesus has sent me so that you may"), seg(1, "regain your sight and be filled with the Holy Spirit.")]
    out = quotes.sentences(segs, 0, 10)
    assert len(out) == 1 and out[0][0].startswith("Brother Saul") and out[0][0].endswith("Holy Spirit.")
    assert 0 <= out[0][1] < out[0][2] <= 10


def test_the_quote_is_word_for_word_from_the_transcript():
    hit = quotes.strongest_sentence(SEGS, 0, 20)
    assert quotes.is_verbatim(hit["text"], FULL) and hit["score"] > 0
    assert "Jesus" in hit["text"] or "God loves you" in hit["text"]                   # names of God and strong words win
    for s in quotes.sentences(SEGS, 0, 20):
        assert quotes.is_verbatim(s[0], FULL)


def test_filler_and_context_dependent_sentences_lose():
    assert quotes.sentence_score("Um you know I mean it is kind of hard to explain.") < quotes.sentence_score("God loves you and he will never leave you.")
    assert quotes.sentence_score("This is why it matters to all of us here.") < quotes.sentence_score("Grace is a gift that you cannot earn.")
    assert quotes.sentence_score("Yes.") == -1.0 and quotes.sentence_score(" ".join(["word"] * 40)) == -1.0


def test_a_leading_conjunction_is_dropped_but_the_rest_stays_verbatim():
    segs = [seg(0, "And God loves you more than you will ever know.")]
    hit = quotes.strongest_sentence(segs, 0, 5)
    assert hit["text"] == "God loves you more than you will ever know." and quotes.is_verbatim(hit["text"], segs[0]["text"])


def test_a_long_sentence_is_quoted_from_its_strongest_clauses():
    long = ("Brother Saul, the Lord Jesus who appeared to you on the road has sent me, so that you may regain your sight, "
            "and be filled with the Holy Spirit and tell everybody what you have seen.")
    hit = quotes.strongest_sentence([seg(0, long)], 0, 5)
    assert hit and len(hit["text"].split()) <= quotes.MAX_WORDS and quotes.is_verbatim(hit["text"], long)
    assert "Jesus" in hit["text"] or "Holy Spirit" in hit["text"]


def test_a_transcript_without_punctuation_still_gives_a_quote():
    segs = [seg(0, "grace is the free gift of God for every person"), seg(1, "and it can never be earned by anyone at all")]
    hit = quotes.strongest_sentence(segs, 0, 10)
    assert hit and quotes.is_verbatim(hit["text"], " ".join(s["text"] for s in segs))


def test_the_models_own_quotes_break_a_tie():
    segs = [seg(0, "Water is wet and the sky is blue today."), seg(1, "Rivers run toward the sea every single morning.")]
    plain = quotes.strongest_sentence(segs, 0, 10)
    pick = quotes.strongest_sentence(segs, 0, 10, ["Rivers run toward the sea every single morning"])
    assert pick["text"].startswith("Rivers") and quotes.sentence_score(pick["text"], ["Rivers run toward the sea every single morning"]) > quotes.sentence_score(pick["text"])
    assert plain["text"]


def test_nothing_to_quote():
    assert quotes.strongest_sentence([seg(0, "Yes. No. Okay.")], 0, 5) is None
    assert quotes.strongest_sentence([], 0, 5) is None
    assert quotes.strongest_sentence(SEGS, 500, 600) is None


def test_bible_reference_in_the_quote():
    assert quotes.reference_in("As John 3:16 says, God so loved the world") == "John 3:16"
    assert quotes.reference_in("No reference here at all") == ""
    segs = [seg(0, "For God so loved the world, John 3:16 tells us, that he gave his only Son.")]
    assert quotes.strongest_sentence(segs, 0, 5)["reference"] == "John 3:16"


def clip(start, end, score=None, category=None, hook="", **kw):
    return {"start": start, "end": end, "score": score, "category": category, "hook": hook, **kw}


def test_best_clip_and_label_come_from_the_plan():
    main, a, b = clip(0, 60, 70, "Teaching", "Main hook"), clip(100, 130, 88, "Scripture", "Best one"), clip(200, 230, 60, "Story")
    plan = {"main_reels": [main], "short_reels": [a, b], "poster": {"headline": "Poster hook words"}, "title": "T"}
    assert quotes.best_clip(plan) is a and quotes.poster_label(a, plan) == "Scripture"
    tie = {"main_reels": [clip(0, 60, 80)], "short_reels": [clip(100, 130, 80)]}
    assert quotes.best_clip(tie) is tie["main_reels"][0]                                    # the main reel wins a tie
    typed = {"main_reels": [clip(0, 60, None)], "short_reels": [clip(100, 130, 55)]}
    assert quotes.best_clip(typed)["start"] == 100                                           # a typed clip has no score
    assert quotes.best_clip({}) is None
    assert quotes.poster_label(clip(0, 5, 50, None, "The Lord is near today friends"), plan) == "The Lord"       # no category: its hook (cut to whole words)
    assert quotes.poster_label(None, plan) == "Poster hook words"
    label = quotes.poster_label(clip(0, 5, 50, "Nonsense"), plan)
    assert label and label.lower() != "highlight"                                            # never the fixed word


def test_poster_fields_from_the_best_clip():
    plan = {"main_reels": [clip(0, 10, 40, "Teaching")], "short_reels": [clip(10, 20, 90, "Scripture", "Best")], "quotes": [], "title": "T"}
    got = quotes.poster_from_clip(plan, SEGS, FULL)
    assert got["label"] == "Scripture" and got["clip"]["start"] == 10 and quotes.is_verbatim(got["quote"], FULL)
    assert quotes.poster_from_clip({"main_reels": [], "short_reels": []}, SEGS, FULL) == {}


# =============================================================== planner: the poster and the carousel come out of the plan
def make_text_segments(n=60, step=5, text="Segment {i}: grace is the free gift of God number {i} for every person."):
    return [{"start": float(i * step), "end": float((i + 1) * step), "text": text.format(i=i)} for i in range(n)]


def raw_plan(**over):
    p = {"title": "Grace Sermon", "main_theme": "God's grace is free", "summary": "A talk about grace. It is free for all.", "visual_theme": "grace",
         "themes": ["grace"], "key_points": ["grace is the free gift of God"], "quotes": ["grace is the free gift of God number 3 for every person"],
         "main_reels": [{"start": 1, "end": 70, "hook": "Grace is free", "reason": "x", "score": {"opening": 18, "idea": 17, "quotable": 15, "emotion": 14, "standalone": 16},
                         "category": "Teaching"}],
         "short_reels": [{"start": 75, "end": 110, "hook": "Short one", "reason": "y", "category": "Encouragement",
                          "score": {"opening": 19, "idea": 19, "quotable": 19, "emotion": 19, "standalone": 19}}],
         "poster": {"hook": "Grace Is Free", "verse": "", "verse_text": ""}, "encouragement_posts": [{"hook": "a hook"}, {"hook": "another hook"}],
         "carousel": [{"title": "A gift", "text": "Grace is the free gift of God"}, {"title": "Every person", "text": "The gift is for every person"}]}
    p.update(over)
    return p


def test_the_plan_carries_the_posters_quote_label_and_clip():
    segs = make_text_segments(60)
    plan = planner.normalize_plan(raw_plan(), segs, 300, " ".join(s["text"] for s in segs), rs={"count": 1, "min_s": 30.0, "max_s": 60.0})
    p = plan["poster"]
    assert p["label"] == "Encouragement" and p["label"].lower() != "highlight"                # the best clip's category
    assert quotes.is_verbatim(p["quote"], " ".join(s["text"] for s in segs)) and p["clip"]["start"] <= p["clip"]["quote_start"]
    assert p["clip"]["start"] == plan["short_reels"][0]["start"]                              # taken from the highest scoring clip
    assert plan["selection_v"] >= 3


def test_an_unverifiable_poster_quote_is_dropped():
    segs = [{"start": 0.0, "end": 5.0, "text": "Hi."}]
    plan = {"title": "T", "main_theme": "m", "poster": {"headline": "H", "quote": "A quote nobody ever said aloud", "subheadline": "", "theme": "t"},
            "main_reels": [], "short_reels": [], "quotes": []}
    planner._poster_from_best_clip(plan, segs, "Hi.")
    assert plan["poster"]["quote"] == "" and plan["poster"]["label"]


def test_the_prompt_asks_for_a_carousel():
    assert '"carousel"' in planner.PLAN_SCHEMA and "carousel: 5 to 7 items" in planner._rules(600)


# =============================================================== carousel.py
TALK = ("Grace is the free gift of God. Nobody can earn forgiveness by working hard. Faith is trusting Jesus completely every day. "
        "Prayer keeps our hearts close to the Father. Serve the people around you with joy. Hope does not disappoint us.")


def pt(title, text):
    return {"title": title, "text": text}


def test_a_point_must_be_short_a_point_and_said_in_the_talk():
    ok = carousel.clean_point("Free gift", "Grace is the free gift of God", TALK)
    assert ok == {"title": "Free gift", "text": "Grace is the free gift of God."}
    assert carousel.clean_point(None, "Too short", TALK) is None
    assert carousel.clean_point(None, " ".join(["grace"] * 40), TALK) is None
    assert carousel.clean_point(None, "and then we went to the", TALK) is None               # all filler
    assert carousel.clean_point(None, "Quantum entanglement defies classical locality constraints entirely", TALK) is None   # never said
    t = carousel.clean_point("This title is much too long for any slide really", "Faith is trusting Jesus completely every day", TALK)
    assert len(t["title"].split()) <= carousel.TITLE_MAX


def test_titles_come_from_the_point_itself():
    assert carousel.title_from("Faith is trusting Jesus completely every day") in "Faith is trusting Jesus completely every day"
    assert len(carousel.title_from("Prayer keeps our hearts close to the Father always").split()) <= carousel.TITLE_MAX
    assert carousel.title_from("Short title") == "Short title"


def test_points_are_distinct_and_come_from_the_best_source_first():
    plan = {"key_points": ["Grace is the free gift of God", "Prayer keeps our hearts close to the Father"], "quotes": ["Hope does not disappoint us ever"]}
    raw = [pt("Gift", "Grace is the free gift of God"), pt("Same again", "The free gift of God is grace"), pt("Faith", "Faith is trusting Jesus completely every day")]
    got = carousel.candidates(raw, plan, TALK)
    texts = [g["text"] for g in got]
    assert texts[0] == "Grace is the free gift of God." and "Faith is trusting Jesus completely every day." in texts
    assert len(got) == len({g["text"] for g in got}) and not any("The free gift of God is grace" in t for t in texts)   # the repeat is dropped
    assert any(t.startswith("Prayer") for t in texts) and any(t.startswith("Hope") for t in texts)                     # then key points, then quotes
    assert carousel.candidates("nonsense", {}, TALK) == [] and carousel.candidates(None, {"key_points": [None, 5, ""]}, TALK) == []


def test_assembled_slides_are_a_cover_points_and_a_closing():
    pts = carousel.candidates([pt("Gift", "Grace is the free gift of God"), pt("Faith", "Faith is trusting Jesus completely every day"),
                               pt("Prayer", "Prayer keeps our hearts close to the Father"), pt("Serve", "Serve the people around you with joy"),
                               pt("Hope", "Hope does not disappoint us")], {}, TALK)
    plan = {"title": "Grace That Changes", "main_theme": "God's grace changes everything", "summary": "Grace is free. Receive it today.",
            "carousel_points": pts, "poster": {"subheadline": "Romans 8"}, "main_reels": [{"closing": "Receive it today"}], "short_reels": []}
    for target, total in ((5, 5), (6, 6), (7, 7), (99, 7), (1, 5), ("x", 6)):
        slides = carousel.assemble(plan, target)
        assert len(slides) == min(total, len(pts) + 2), (target, len(slides))
    s = carousel.assemble(plan, 6)
    assert [x["kind"] for x in s] == ["cover", "point", "point", "point", "point", "closing"]
    assert s[0]["headline"] == "Grace That Changes" and s[0]["label"] == "Swipe" and s[0]["body"]
    assert [x["label"] for x in s[1:]] == ["2 / 6", "3 / 6", "4 / 6", "5 / 6", "6 / 6"] and all(x["total"] == 6 for x in s)
    assert s[-1]["headline"] == "Receive it today" and s[-1]["reference"] == "Romans 8" and s[-1]["body"] == "Receive it today."
    assert s[1]["headline"] == "Gift" and s[1]["body"] == "Grace is the free gift of God."


def test_few_points_make_fewer_slides_and_none_make_none():
    plan = {"title": "T", "main_theme": "m", "carousel_points": [{"title": "One", "text": "Only one point was made."}]}
    s = carousel.assemble(plan, 6)
    assert [x["kind"] for x in s] == ["cover", "point", "closing"]                           # nothing is invented to fill the count
    assert carousel.assemble({"title": "T", "carousel_points": []}, 6) == [] and carousel.assemble({}, 6) == []
    assert carousel.target_slides(3) == 5 and carousel.target_slides(9) == 7 and carousel.target_slides(None) == 6


# =============================================================== thumbs.py - the best frame
class Tape:
    """Fake face detector / eye checker driven by the frame number: the sampler yields 2 frames a second, in order."""
    name = "fake"

    def __init__(self, face_at, eyes_at=None):
        self.face_at, self.eyes_at, self.i, self.j = face_at, eyes_at or {}, 0, 0

    def detect(self, frame):
        t = self.i / 2
        self.i += 1
        f = self.face_at(t)
        return [] if f is None else [f]

    def score(self, rgb):
        self.j += 1
        return self.eyes_at.get("value")


@pytest.fixture(scope="module")
def clip20(tmp_path_factory):
    """20 s test pattern: sharp everywhere except 4-8 s (blurred), black at 12-14 s."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("tb") / "c.mp4"
    vf = "boxblur=14:3:enable='between(t,4,8)',drawbox=x=0:y=0:w=640:h=360:color=black:t=fill:enable='between(t,12,14)'"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=20", "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def lit20(tmp_path_factory):
    """20 s test pattern, sharp except 4-8 s (blurred); no black stretch, so a detector that counts frames stays in step."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("tb2") / "c.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=20", "-vf", "boxblur=14:3:enable='between(t,4,8)'", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(out)], check=True, capture_output=True)
    return out


SIZE = (640, 360)


def face(h=0.3, cx=0.5, cy=0.45, score=0.9):
    return Face(cx, cy, h * 0.6, h, score)


def test_sharpness_and_light_measures():
    rng = np.random.default_rng(1)
    crisp = rng.integers(0, 255, (90, 160)).astype(np.float32)
    soft = np.full((90, 160), 128.0, np.float32)
    assert thumbs.sharpness(crisp) > 100 * max(1.0, thumbs.sharpness(soft))
    assert thumbs.well_lit(crisp) and not thumbs.well_lit(np.zeros((90, 160), np.float32)) and not thumbs.well_lit(np.full((90, 160), 250.0, np.float32))
    assert not thumbs.well_lit(soft)                                                       # a flat grey fade has no detail


def test_a_bigger_face_scores_higher_and_a_cropped_one_less():
    assert thumbs.face_size_score(face(0.3)) > thumbs.face_size_score(face(0.1)) > 0 == thumbs.face_size_score(None)
    assert thumbs.face_size_score(face(0.3, cx=0.99)) < thumbs.face_size_score(face(0.3, cx=0.9))        # cut by the edge
    assert thumbs.face_size_score(face(0.3, cx=0.5)) > thumbs.face_size_score(face(0.3, cx=0.9))        # a little for the middle
    assert thumbs.face_size_score(face(0.9)) <= 1.0


def test_the_best_frame_has_the_biggest_face_open_eyes_and_is_sharp(lit20):
    # the face grows over the clip, so the biggest face is at the end - but 4-8 s are blurred and 12-14 s black
    tape = Tape(lambda t: face(0.10 + 0.012 * t), {"value": 0.9})
    got = thumbs.best_frames(lit20, 0, 20, SIZE, 1, detectors=[tape], eyes=tape)
    assert got and 14 <= got[0].t <= 20 and got[0].face_score > 0.5 and got[0].eyes == 0.9 and got[0].sharp > 0.5
    assert got[0].score == pytest.approx(thumbs.W_FACE * got[0].face_score + thumbs.W_EYES * 0.9 + thumbs.W_SHARP * got[0].sharp)


def test_blurred_and_black_frames_never_win(clip20):
    tape = Tape(lambda t: face(0.3), {"value": 0.9})                                       # the same big face everywhere
    best = thumbs.best_frames(clip20, 0, 20, SIZE, 12, detectors=[tape], eyes=tape)
    for g in best:
        assert not (12 <= g.t <= 14), g.t                                                   # black frames are skipped
    top = best[0]
    assert not (4.5 <= top.t <= 7.5)                                                        # and the blurred stretch is not the best


def test_open_eyes_beat_closed_eyes_even_with_a_smaller_face(lit20, monkeypatch):
    # the speaker's x position encodes the time (so the eye checker can tell which frame it is looking at):
    # before 10 s the eyes are closed but the face is bigger; after 10 s they are open and the face a little smaller
    monkeypatch.setattr(thumbs, "face_crop", lambda rgb, f: np.full((8, 8, 3), int(round(f.cx * 100)), np.uint8))
    tape = Tape(lambda t: face(0.30 if t < 10 else 0.26, cx=0.2 + 0.6 * t / 20))
    eyes = types.SimpleNamespace(score=lambda rgb: 0.1 if int(rgb[0, 0, 0]) < 50 else 0.95)         # cx 0.5 <-> t = 10
    got = thumbs.best_frames(lit20, 0, 20, SIZE, 1, detectors=[tape], eyes=eyes)
    assert got[0].t >= 10 and got[0].eyes == 0.95


def test_a_frame_with_closed_eyes_is_noted_and_demoted(lit20, monkeypatch):
    monkeypatch.setattr(thumbs, "face_crop", lambda rgb, f: np.zeros((8, 8, 3), np.uint8))
    shut = types.SimpleNamespace(score=lambda rgb: 0.1)
    tape = Tape(lambda t: face(0.3))
    got = thumbs.best_frames(lit20, 0, 20, SIZE, 8, detectors=[tape], eyes=shut)
    checked = [g for g in got if g.eyes is not None]
    assert checked and all(g.eyes == 0.1 and "eyes closed" in g.notes for g in checked)
    unchecked = [g for g in got if g.eyes is None]
    assert unchecked and got[0].eyes is None                                                          # closed eyes drop below "not checked"
    assert max(g.score for g in checked) < min(g.score for g in unchecked) + 0.2


def test_eyes_of_unknown_state_are_neutral(lit20):
    got = thumbs.best_frames(lit20, 0, 20, SIZE, 1, detectors=[Tape(lambda t: face(0.3))], eyes=None)
    assert got and got[0].eyes is None and got[0].score == pytest.approx(thumbs.W_FACE * got[0].face_score + thumbs.W_EYES * 0.5 + thumbs.W_SHARP * got[0].sharp)


def test_frames_without_a_face_only_win_when_there_is_no_face(lit20):
    only_late = Tape(lambda t: face(0.2) if 17 <= t <= 18 else None)
    got = thumbs.best_frames(lit20, 0, 20, SIZE, 1, detectors=[only_late], eyes=None)
    assert got and 17 <= got[0].t <= 18 and got[0].face is not None
    none = thumbs.best_frames(lit20, 0, 20, SIZE, 1, detectors=[Tape(lambda t: None)], eyes=None)
    assert none and none[0].face is None and not (4.5 <= none[0].t <= 7.5)                    # the sharpest, lit frame


def test_weak_detections_are_not_faces(clip20):
    weak = Tape(lambda t: Face(0.5, 0.5, 0.2, 0.4, 0.52))                                  # a "face" on a torso, 0.52 confident
    got = thumbs.best_frames(clip20, 0, 20, SIZE, 1, detectors=[weak], eyes=None)
    assert got and got[0].face is None


def test_several_detectors_vote_and_the_confident_face_wins(clip20):
    torso = Tape(lambda t: Face(0.5, 0.5, 0.2, 0.4, 0.52))
    real = Tape(lambda t: Face(0.4, 0.3, 0.05, 0.12, 0.93))
    got = thumbs.best_frames(clip20, 0, 20, SIZE, 1, detectors=[torso, real], eyes=None)
    assert got[0].face.h == 0.12 and got[0].face.score == 0.93


def test_the_best_frames_are_well_separated(clip20):
    got = thumbs.best_frames(clip20, 0, 20, SIZE, 3, detectors=[Tape(lambda t: face(0.3))], eyes=None)
    assert len(got) == 3 and all(abs(a.t - b.t) >= thumbs.MIN_GAP_S for i, a in enumerate(got) for b in got[i + 1:])
    assert got == sorted(got, key=lambda g: -g.score)


def test_a_failing_detector_or_eye_check_does_not_stop_the_search(clip20):
    class Boom:
        name = "boom"

        def detect(self, f):
            raise RuntimeError("model crashed")

        def score(self, rgb):
            raise RuntimeError("eyes crashed")
    got = thumbs.best_frames(clip20, 0, 20, SIZE, 1, detectors=[Boom()], eyes=Boom())
    assert got and got[0].face is None
    got = thumbs.best_frames(clip20, 0, 20, SIZE, 1, detectors=[Tape(lambda t: face(0.3))], eyes=Boom())
    assert got and got[0].face is not None and got[0].eyes is None


def test_no_frames_no_pick(tmp_path):
    assert thumbs.best_frames(tmp_path / "missing.mp4", 0, 5, SIZE, 1, detectors=[], eyes=None) == []


def test_a_long_video_is_sampled_by_seeking(clip20, monkeypatch):
    monkeypatch.setattr(thumbs, "SHORT_CLIP_S", 5.0)
    monkeypatch.setattr(thumbs, "LONG_CANDIDATES", 10)
    seen = []
    tape = Tape(lambda t: seen.append(t) or face(0.3))
    got = thumbs.best_frames(clip20, 0, 20, SIZE, 2, detectors=[tape], eyes=None)
    assert len(seen) <= 10 and len(got) == 2 and abs(got[0].t - got[1].t) >= 20 / 12 - 1e-6


def test_face_crop_stays_inside_the_frame():
    fr = np.zeros((360, 640, 3), np.uint8)
    for f in (face(0.3), face(0.3, cx=0.02, cy=0.02), face(0.3, cx=0.98, cy=0.98), face(0.9)):
        c = thumbs.face_crop(fr, f)
        assert c.shape[0] == c.shape[1] and 0 < c.shape[0] <= 360
    assert thumbs.face_box(face(0.3), (360, 640)) is not None


def test_save_frame_and_best_time(clip20, tmp_path):
    p = thumbs.save_frame(clip20, 3.0, tmp_path / "a" / "f.jpg", width=320)
    assert p.exists() and Image.open(p).size == (320, 180)
    t = thumbs.best_time(clip20, 0, 20, SIZE, detectors=[Tape(lambda t: face(0.3))], eyes=None)
    assert isinstance(t, float)


def test_eye_checkers_are_chosen_and_a_failed_start_is_remembered(monkeypatch):
    calls = {"mp": 0}

    def mp():
        calls["mp"] += 1
        raise reframe.DetectorUnavailable("blocked")
    monkeypatch.setattr(thumbs, "EYE_FACTORIES", {"mediapipe": mp, "opencv": lambda: types.SimpleNamespace(name="cv", score=lambda r: 1.0)})
    thumbs.reset_eyes()
    assert thumbs.eye_checker().name == "cv" and thumbs.eye_checker().name == "cv" and calls["mp"] == 1
    monkeypatch.setattr(thumbs, "EYE_FACTORIES", {"mediapipe": mp, "opencv": mp})
    thumbs.reset_eyes()
    assert thumbs.eye_checker() is None
    thumbs.reset_eyes()


def test_the_bundled_landmarker_model_exists():
    assert thumbs.LANDMARKER.stat().st_size > 1_000_000


def test_the_real_eye_checker_finds_nothing_in_an_empty_frame():
    pytest.importorskip("cv2")
    eyes = thumbs.HaarEyes()
    assert eyes.score(np.full((200, 200, 3), 60, np.uint8)) == 0.2                         # no eyes found = closed / unknown-low


# =============================================================== thumbnail.py - the designed thumbnail
def test_the_hook_is_three_to_five_whole_words():
    t = thumbnail.thumbnail_hook
    assert t("Grace is free") == "Grace is free" and t("Hope never dies here") == "Hope never dies here"
    long = t("Everything changes when your eyes are opened and you finally see")
    assert 3 <= len(long.split()) <= 5 and long.split()[-1].lower() not in ("the", "and", "a", "to", "of", "you")
    assert 3 <= len(t("Trust", "Faith means trusting God even when you cannot see the road ahead").split()) <= 5      # too short: from the clip
    assert t("") == "" or len(t("").split()) >= 0
    for words in ("one two three four five six seven eight nine ten", "the and of to in for with", "Believe"):
        assert len(t(words).split()) <= thumbnail.MAX_WORDS


def test_the_best_window_ends_on_a_meaningful_word():
    w = thumbnail._best_window("we are going to talk about the grace of God".split())
    assert 3 <= len(w) <= 5 and "God" in w or "grace" in w
    assert w[-1].lower() not in ("the", "of", "to", "a")


@pytest.fixture()
def frame_file(tmp_path):
    img = Image.new("RGB", (1280, 720), (30, 40, 60))
    px = img.load()
    for x in range(1280):
        for y in range(0, 720, 4):
            px[x, y] = (30 + x // 12, 40, 60 + y // 8)
    p = tmp_path / "frame.jpg"
    img.save(p)
    return p


BRAND = {"colors": {"primary": "#C21807", "accent": "#FFD700", "background": "#101010", "text": "#FFFFFF"}}


@pytest.mark.parametrize("variant", thumbnail.VARIANTS)
@pytest.mark.parametrize("with_face", [True, False])
def test_every_layout_is_a_1280_by_720_jpeg(tmp_path, frame_file, variant, with_face):
    out = thumbnail.design(tmp_path / f"{variant}.jpg", frame_file, "Grace is free", BRAND, variant, face(0.15, cx=0.4) if with_face else None, "Teaching")
    im = Image.open(out)
    assert im.size == (1280, 720) and im.format == "JPEG" and out.stat().st_size > 20_000


def test_the_layouts_differ_and_use_the_brand_colours(tmp_path, frame_file):
    ims = {v: Image.open(thumbnail.design(tmp_path / f"{v}.jpg", frame_file, "Grace is free", BRAND, v, face(0.15, cx=0.4), "Teaching")).convert("RGB") for v in thumbnail.VARIANTS}
    arrs = {v: np.asarray(i).astype(int) for v, i in ims.items()}
    assert np.abs(arrs["left"] - arrs["split"]).mean() > 5 and np.abs(arrs["split"] - arrs["band"]).mean() > 5
    r, g, b = arrs["split"][110, 1250]                                                     # the split layout's panel is the brand primary
    assert r > 130 and g < 60 and b < 60
    band = arrs["band"][700, 20]                                                           # the band is the brand background, nearly black
    assert max(band) < 60
    accent = [(abs(p[0] - 255) < 40 and abs(p[1] - 215) < 40 and p[2] < 90) for p in arrs["left"].reshape(-1, 3)[::37]]
    assert any(accent)                                                                     # the keyword is in the accent colour


def test_the_logo_and_label_are_drawn_when_given(tmp_path, frame_file):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 80), (255, 0, 255, 255)).save(logo)
    plain = np.asarray(Image.open(thumbnail.design(tmp_path / "a.jpg", frame_file, "Grace is free", BRAND, "band", None, "")).convert("RGB")).astype(int)
    full = np.asarray(Image.open(thumbnail.design(tmp_path / "b.jpg", frame_file, "Grace is free", {**BRAND, "logo": str(logo)}, "band", None, "Scripture")).convert("RGB")).astype(int)
    magenta = ((full[..., 0] > 220) & (full[..., 1] < 40) & (full[..., 2] > 220)).sum()
    assert magenta > 500 and ((plain[..., 0] > 220) & (plain[..., 1] < 40) & (plain[..., 2] > 220)).sum() == 0
    assert np.abs(plain - full).mean() > 0.5


def test_a_telugu_hook_uses_a_telugu_font(tmp_path, frame_file):
    out = thumbnail.design(tmp_path / "t.jpg", frame_file, "దేవుని కృప అద్భుతమైనది", BRAND, "left", None, "")
    assert Image.open(out).size == (1280, 720)
    f = thumbnail._font("దేవుని", 80)
    assert "Telugu" in f.getname()[0]


# =============================================================== clipcopy.py - a caption of its own, near-duplicates rejected
class Memory:
    """The parts of the memory the duplicate check uses: persisted similar items and an embedder."""
    def __init__(self, persisted=()):
        self.persisted, self.looked_up = list(persisted), []
        self._e = HashEmbedder()

    def similar(self, text, kinds, threshold=0.8, n=5, exclude_asset=None):
        self.looked_up.append((text, kinds, threshold))
        return [h for h in self.persisted if h["score"] >= threshold][:n]

    def embedder(self):
        return self._e


class Scripted:
    """A fake model that answers the caption prompt from a list, remembering every prompt."""
    def __init__(self, *captions):
        self.captions, self.prompts = list(captions), []

    def generate_json(self, prompt, cfg):
        self.prompts.append(prompt)
        c = self.captions.pop(0)
        return c if isinstance(c, dict) else {"caption": c, "hashtags": ["grace", "#faith"]}


PLAN = {"main_theme": "God's grace is free"}


def test_the_prompt_has_this_clips_transcript_and_the_earlier_captions():
    p = clipcopy.prompt("THE CLIPS OWN WORDS about mercy", PLAN, ["first caption", "second caption"], ["too close one"], ["mercy"], "keep it short")
    assert "THIS reel's own transcript" in p and "THE CLIPS OWN WORDS about mercy" in p and "God's grace is free" in p
    assert "- first caption" in p and "- second caption" in p and "TOO SIMILAR" in p and "- too close one" in p
    assert "mercy" in p and "keep it short" in p and "no invented quotes" in p
    assert "TOO SIMILAR" not in clipcopy.prompt("x", PLAN, [], [])
    many = clipcopy.prompt("x", PLAN, [f"c{i}" for i in range(30)], [])
    assert "- c29" in many and "- c18" in many and "- c17\n" not in many                      # only the latest 12


def test_a_caption_is_cleaned_and_must_not_be_empty():
    got = clipcopy.write(Scripted({"caption": "  Hello\n  world  ", "hashtags": ["a", "#b", "c d", None, 5]}), {}, "text", PLAN, [])
    assert got == {"caption": "Hello world", "hashtags": ["#a", "#b", "#cd", "#5"]}
    with pytest.raises(ValueError):
        clipcopy.write(Scripted({"caption": "  "}), {}, "text", PLAN, [])
    assert len(clipcopy.write(Scripted("x" * 2000), {}, "t", PLAN, [])["caption"]) == 600


def test_cosine():
    assert clipcopy.cosine([1, 0], [1, 0]) == 1 and clipcopy.cosine([1, 0], [0, 1]) == 0 and clipcopy.cosine([0, 0], [1, 1]) == 0


def test_the_checker_uses_the_memory_and_this_runs_captions():
    mem = Memory([{"score": 0.9, "text": "published earlier", "asset_id": 7}])
    chk = clipcopy.Checker(mem)
    hits = chk.nearest("Mercy rises over every chain tonight", ["Mercy rises over every chain tonight", "something else entirely different here"], "Hook")
    assert [h["asset_id"] for h in hits if h["asset_id"]] == [7] and any(h["asset_id"] is None and h["score"] >= 0.82 for h in hits)
    assert hits == sorted(hits, key=lambda h: -h["score"])
    assert mem.looked_up[0][0] == "Hook\nMercy rises over every chain tonight" and mem.looked_up[0][1] == ["caption", "post"]      # how a reel is stored
    assert clipcopy.Checker(Memory()).nearest("brand new words about rivers", ["completely unrelated sentence about cars"]) == []


def test_a_distinct_caption_is_kept_at_once():
    llm = Scripted("Mercy rises over every chain tonight")
    cp, similar, tries = clipcopy.unique_copy(llm, {}, Memory(), "clip words", PLAN, ["Light returns after the long night of waiting"])
    assert similar == [] and tries == 1 and cp["caption"] == "Mercy rises over every chain tonight" and len(llm.prompts) == 1


def test_a_near_duplicate_is_written_again_with_the_duplicate_listed():
    logs = []
    llm = Scripted("Mercy rises over every chain tonight", "Hope wakes the weary heart this morning")
    cp, similar, tries = clipcopy.unique_copy(llm, {}, Memory(), "clip", PLAN, ["Mercy rises over every chain tonight"], log=lambda m, lvl="INFO": logs.append((lvl, m)))
    assert tries == 2 and similar == [] and cp["caption"].startswith("Hope wakes")
    assert "TOO SIMILAR" not in llm.prompts[0] and "TOO SIMILAR" in llm.prompts[1] and "- Mercy rises over every chain tonight" in llm.prompts[1]
    assert any("writing it again" in m for _, m in logs)


def test_a_caption_that_stays_a_duplicate_is_kept_and_flagged_not_fatal():
    logs = []
    llm = Scripted("Mercy rises over every chain tonight", "Mercy rises over every chain tonight too")
    cp, similar, tries = clipcopy.unique_copy(llm, {}, Memory(), "clip", PLAN, ["Mercy rises over every chain tonight"], log=lambda m, lvl="INFO": logs.append((lvl, m)))
    assert tries == clipcopy.MAX_TRIES and similar and similar[0]["score"] >= clipcopy.SIMILARITY_LIMIT
    assert cp["caption"].startswith("Mercy rises") and any("keeping" in m for _, m in logs)


def test_the_memory_check_alone_can_reject_a_caption():
    mem = Memory([{"score": 0.95, "text": "an earlier published caption", "asset_id": 3}])
    llm = Scripted("whatever words", "other words")
    cp, similar, tries = clipcopy.unique_copy(llm, {}, mem, "clip", PLAN, [])
    assert tries == 2 and similar == [{"asset_id": 3, "score": 0.95}]


def test_a_model_error_is_not_swallowed_here():
    class Down:
        def generate_json(self, p, c):
            raise ConnectionError("ollama is down")
    with pytest.raises(ConnectionError):
        clipcopy.unique_copy(Down(), {}, Memory(), "clip", PLAN, [])


# =============================================================== through the job pipeline
def assets(client, pid, kind):
    return sorted(client.get(f"/api/assets?project_id={pid}&kind={kind}").json(), key=lambda a: a["slot"])


def raw(ctx, aid):
    with ctx.db.session() as s:
        a = s.get(GeneratedAsset, aid)
        return dict(a.meta), a.file_path


def test_the_pipeline_now_ends_with_a_carousel_and_thumbnails(client, ctx, processed):
    kinds = {a["kind"] for a in client.get(f"/api/assets?project_id={processed}").json()}
    assert {"carousel", "thumbnail", "poster", "social_post", "main_reel", "short_reel"} <= kinds
    jobs = client.get(f"/api/jobs?project_id={processed}").json()["items"]
    assert {j["job_type"] for j in jobs} >= {"carousel_generation", "thumbnail_generation"} and all(j["status"] == "completed" for j in jobs)
    from ruha.jobs import PIPELINE
    assert PIPELINE[-2:] == ["carousel_generation", "thumbnail_generation"]


def test_the_carousel_has_five_to_seven_slides_of_the_main_points(client, ctx, processed):
    slides = assets(client, processed, "carousel")
    assert 5 <= len(slides) <= 7 and [s["slot"] for s in slides] == [f"carousel_{i}" for i in range(1, len(slides) + 1)]
    kinds = [s["meta"]["slide_kind"] for s in slides]
    assert kinds[0] == "cover" and kinds[-1] == "closing" and set(kinds[1:-1]) == {"point"}
    assert [s["meta"]["slide"] for s in slides] == list(range(1, len(slides) + 1)) and all(s["meta"]["total"] == len(slides) for s in slides)
    for s in slides:
        meta, path = raw(ctx, s["id"])
        assert Image.open(path).size == tuple(meta["size"]) == (1080, 1350)
    assert slides[1]["title"] == "Grace is a gift" and slides[1]["description"]
    assert "Nothing to earn" not in [s["title"] for s in slides]                              # a point the speaker never made is dropped


def test_the_carousel_follows_the_slide_setting_and_is_not_redone(client, ctx, processed):
    assert client.put("/api/settings", json={"values": {"carousel.slides": 5}}).status_code == 200
    n0 = len(assets(client, processed, "carousel"))
    client.post(f"/api/projects/{processed}/jobs", json={"job_type": "carousel_generation", "params": {}})
    ctx.queue.drain()
    assert len(assets(client, processed, "carousel")) == n0                                    # skipped: it exists
    client.post(f"/api/projects/{processed}/jobs", json={"job_type": "carousel_generation", "params": {"force": True}})
    ctx.queue.drain()
    again = assets(client, processed, "carousel")
    assert len(again) == 5 and again[-1]["meta"]["slide_kind"] == "closing" and len({a["id"] for a in again}) == 5


def test_a_sermon_with_no_points_gets_no_carousel(client, ctx, project_with_video, fakes, monkeypatch):
    plan = fakes.llm.generate_json("x", {})
    plan.pop("carousel")
    plan["key_points"], plan["quotes"] = [], []
    real = fakes.llm.generate_json
    monkeypatch.setattr(fakes.llm, "generate_json", lambda p, c: dict(plan) if p.startswith("You are a careful editor") else real(p, c))
    client.post(f"/api/projects/{project_with_video}/process", json={"steps": ["transcription", "carousel_generation"]})
    ctx.queue.drain()
    assert assets(client, project_with_video, "carousel") == []
    jobs = {j["job_type"]: j for j in client.get(f"/api/jobs?project_id={project_with_video}").json()["items"]}
    assert jobs["carousel_generation"]["status"] == "completed"
    logs = client.get(f"/api/jobs/{jobs['carousel_generation']['id']}").json()["logs"]
    assert any("no usable main points" in l["message"] for l in logs)


def test_three_designed_thumbnails_from_the_main_clip(client, ctx, processed):
    th = assets(client, processed, "thumbnail")
    assert [t["slot"] for t in th] == ["thumb_1", "thumb_2", "thumb_3"] and [t["meta"]["variant"] for t in th] == list(thumbnail.VARIANTS)
    for t in th:
        meta, path = raw(ctx, t["id"])
        assert Image.open(path).size == (1280, 720) and meta["size"] == [1280, 720] and 3 <= meta["words"] <= 5 and t["title"] == meta["hook"]
        assert t["has_preview"] and meta["clip"][0] == 0.0 and meta["clip"][1] >= 50                       # the main reel's span
    assert len({t["meta"]["frame_time"] for t in th}) == 3                                          # a different frame for each layout


def test_thumbnails_use_the_typed_main_reel_and_the_variation_setting(client, ctx, project_with_video):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"thumbnail.variations": 2}})
    client.put(f"/api/projects/{pid}/instructions", json={"reels": "0:20-1:20 main - the opening", "posters": ""})
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "thumbnail_generation"]})
    ctx.queue.drain()
    th = assets(client, pid, "thumbnail")
    assert len(th) == 2 and all(t["meta"]["clip"] == [20.0, 80.0] for t in th)


def test_best_frames_choose_the_thumbnails_when_scoring_is_on(client, ctx, project_with_video, monkeypatch):
    pid = project_with_video
    chosen = []

    def fake_best(source, start, end, size, k=1, **kw):
        chosen.append((round(start), round(end), size, k))
        return [thumbs.FrameScore(t=start + 3 + 11 * i, score=0.8 - 0.1 * i, face=face(0.2), face_score=0.7, eyes=0.9, sharp=0.6) for i in range(k)]
    monkeypatch.setattr(thumbs, "best_frames", fake_best)
    monkeypatch.setattr(thumbs, "best_time", lambda *a, **k: 33.0)
    client.put("/api/settings", json={"values": {"thumbnail.best_frame": True}})
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "thumbnail_generation"]})
    ctx.queue.drain()
    th = assets(client, pid, "thumbnail")
    assert [t["meta"]["frame_time"] for t in th] == [3.0, 14.0, 25.0]
    assert th[0]["meta"]["frame"] == {"face": 0.7, "eyes": 0.9, "sharp": 0.6, "score": 0.8} and th[1]["meta"]["frame"]["score"] == 0.7
    assert chosen and chosen[0][3] == 3 and chosen[0][2] == (640, 360)
    with ctx.db.session() as s:
        v = s.query(SourceVideo).filter_by(project_id=pid).one()
        assert v.thumbnail_path and Path(v.thumbnail_path).exists()                               # the sermon's own thumbnail was replaced


def test_a_failing_frame_search_falls_back_to_evenly_spaced_frames(client, ctx, project_with_video, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no frames")
    monkeypatch.setattr(thumbs, "best_frames", boom)
    monkeypatch.setattr(thumbs, "best_time", boom)
    client.put("/api/settings", json={"values": {"thumbnail.best_frame": True}})
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "thumbnail_generation"]})
    ctx.queue.drain()
    assert len(assets(client, pid, "thumbnail")) == 3
    jobs = client.get(f"/api/jobs?project_id={pid}").json()["items"]
    assert all(j["status"] == "completed" for j in jobs)
    logs = [l["message"] for j in jobs for l in client.get(f"/api/jobs/{j['id']}").json()["logs"]]
    assert any("evenly spaced" in m for m in logs) and any("sermon thumbnail" in m for m in logs)


def test_reel_thumbnails_use_the_best_frame_of_the_reel(client, ctx, project_with_video, monkeypatch):
    picks = []
    monkeypatch.setattr(thumbs, "best_time", lambda v, a, b, size, **k: picks.append((Path(v).name, round(a), round(b), size)) or 1.5)
    client.put("/api/settings", json={"values": {"thumbnail.best_frame": True}})
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    reel_picks = [p for p in picks if p[0].startswith(("main_", "short_"))]
    assert len(reel_picks) == 4 and all(p[1] == 0 and p[3] == (270, 480) for p in reel_picks)       # each rendered reel was scanned
    for a in assets(client, pid, "short_reel"):
        assert a["has_preview"]


def test_reel_thumbnails_keep_the_old_frame_when_scoring_is_off(client, ctx, project_with_video, monkeypatch):
    monkeypatch.setattr(thumbs, "best_time", lambda *a, **k: pytest.fail("must not score frames when switched off"))
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    assert all(a["has_preview"] for a in assets(client, pid, "short_reel"))


# ----- the poster
def test_the_poster_label_and_quote_come_from_the_plan(client, ctx, processed):
    from ruha.models import Poster
    po = assets(client, processed, "poster")[0]
    meta, _ = raw(ctx, po["id"])
    assert meta["label"] == "Encouragement" and meta["label"].lower() != "highlight"             # the best clip's category
    d = client.get(f"/api/assets/{po['id']}").json()
    full = " ".join(s["text"] for s in client.get(f"/api/projects/{processed}/transcript").json()["segments"])
    assert quotes.is_verbatim(d["poster"]["quote"], full) and d["poster"]["quote"].startswith("Segment ")
    assert meta["quote_clip"]["start"] == 75.0 and meta["quote_clip"]["quote_start"] >= 75.0    # from the highest-scoring clip (short_1)
    assert po["caption"] == d["poster"]["quote"]


def test_a_redrawn_poster_keeps_its_label(client, ctx, processed, monkeypatch):
    seen = []
    from ruha import handlers
    real = handlers.render_graphic
    monkeypatch.setattr(handlers, "render_graphic", lambda out, head, body, ref, label, *a, **k: seen.append(label) or real(out, head, body, ref, label, *a, **k))
    po = assets(client, processed, "poster")[0]
    client.post(f"/api/assets/{po['id']}/regenerate", json={"layout": "band"})
    ctx.queue.drain()
    assert seen == ["Encouragement"]


def test_an_old_poster_without_a_label_still_redraws(client, ctx, processed):
    po = assets(client, processed, "poster")[0]
    with ctx.db.session() as s:
        a = s.get(GeneratedAsset, po["id"])
        m = dict(a.meta)
        m.pop("label")
        a.metadata_json = __import__("json").dumps(m)
    client.post(f"/api/assets/{po['id']}/regenerate", json={"layout": "band"})
    ctx.queue.drain()
    assert client.get(f"/api/assets/{po['id']}").json()["version"] == 2


def test_the_poster_label_follows_the_typed_clip_free_plan(client, ctx, project_with_video, fakes, monkeypatch):
    plan = fakes.llm.generate_json("x", {})
    for c in plan["short_reels"]:
        c["category"] = "Prayer"
        c.pop("score", None)
    plan["main_reels"][0]["category"] = "Story"
    real = fakes.llm.generate_json
    monkeypatch.setattr(fakes.llm, "generate_json", lambda p, c: dict(plan) if p.startswith("You are a careful editor") else real(p, c))
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "poster_generation"]})
    ctx.queue.drain()
    meta, _ = raw(ctx, assets(client, pid, "poster")[0]["id"])
    assert meta["label"] in ("Story", "Teaching", "Prayer")                                       # whichever clip scored best: a category, never "Highlight"


# ----- a caption of its own for every clip
def reel_captions(client, pid):
    return [a["caption"] for a in assets(client, pid, "main_reel") + assets(client, pid, "short_reel")]


def test_every_clip_gets_its_own_caption_from_its_own_transcript(client, ctx, processed, fakes):
    caps = reel_captions(client, processed)
    assert len(caps) == 4 and len(set(caps)) == 4                                                  # four different captions
    prompts = [p for p in fakes.llm.prompts if p.startswith("You write the social media caption")]
    assert len(prompts) == 4 and fakes.llm.copy_calls == 4
    for p in prompts:
        own = p.split("THIS reel's own transcript", 1)[1]
        segs = {int(n) for n in __import__("re").findall(r"Segment (\d+):", own)}
        assert segs and max(segs) - min(segs) < 20                                                  # only this clip's stretch of the talk
    assert prompts[0].count("- ") < prompts[-1].count("- ")                                        # later clips see the earlier captions
    assert "mercy rises" in prompts[1] and "light returns" not in prompts[1]
    for r in assets(client, processed, "short_reel"):
        assert r["meta"]["copy"] == {"own": True, "tries": 1, "similar_to": []}


def test_a_duplicate_caption_is_written_again_and_flagged_if_it_stays(client, ctx, project_with_video, fakes, monkeypatch):
    same = {"caption": "Mercy rises over every chain tonight", "hashtags": ["#mercy"]}
    real = fakes.llm.generate_json
    monkeypatch.setattr(fakes.llm, "generate_json", lambda p, c: dict(same) if p.startswith("You write the social media caption") else real(p, c))
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    reels = assets(client, pid, "main_reel") + assets(client, pid, "short_reel")
    info = [r["meta"]["copy"] for r in reels]
    assert info[0]["tries"] == 1 and info[0]["similar_to"] == []                                   # the first caption has nothing to repeat
    assert all(i["tries"] == 2 and i["similar_to"] for i in info[1:])                              # the others were rewritten, then flagged
    assert all(r["caption"].startswith("Mercy rises") for r in reels)                              # ... and kept: a reel is never lost over this
    jobs = client.get(f"/api/jobs?project_id={pid}").json()["items"]
    logs = [l for j in jobs for l in client.get(f"/api/jobs/{j['id']}").json()["logs"]]
    assert any("writing it again" in l["message"] for l in logs) and any(l["level"] == "WARNING" and "keeping" in l["message"] for l in logs)
    assert all(j["status"] == "completed" for j in jobs)


def test_the_switch_turns_per_clip_captions_off(client, ctx, project_with_video, fakes):
    client.put("/api/settings", json={"values": {"reels.unique_copy": False}})
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    assert fakes.llm.copy_calls == 0
    caps = reel_captions(client, pid)
    assert caps[0].startswith("Free grace #1")                                                      # the planner's own caption
    assert all("copy" in a["meta"] and a["meta"]["copy"] is None for a in assets(client, pid, "short_reel"))


def test_typed_reels_keep_their_own_text(client, ctx, project_with_video, fakes):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reels.count": 2}})
    client.put(f"/api/projects/{pid}/instructions", json={"reels": "0:10-1:10 main\n1:20-1:55 short\n2:00-2:35 short", "posters": ""})
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    assert fakes.llm.copy_calls == 0                                                                # all three were typed: the AI writes none here


def test_a_bad_caption_answer_keeps_the_planned_caption(client, ctx, project_with_video, fakes, monkeypatch):
    real = fakes.llm.generate_json

    def gen(p, c):
        if p.startswith("You write the social media caption"):
            return {"caption": "", "hashtags": []}                                                  # an empty answer
        return real(p, c)
    monkeypatch.setattr(fakes.llm, "generate_json", gen)
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    jobs = client.get(f"/api/jobs?project_id={pid}").json()["items"]
    assert all(j["status"] == "completed" for j in jobs)
    assert reel_captions(client, pid)[0].startswith("Free grace #1")
    assert all(a["meta"]["copy"] == {"own": False, "similar_to": []} for a in assets(client, pid, "short_reel"))


def test_an_unreachable_model_is_a_temporary_error_not_a_lost_reel(client, ctx, project_with_video, fakes, monkeypatch):
    from ruha.ai.llm import LLMUnavailable
    real = fakes.llm.generate_json

    def gen(p, c):
        if p.startswith("You write the social media caption"):
            raise LLMUnavailable("Ollama is down")
        return real(p, c)
    monkeypatch.setattr(fakes.llm, "generate_json", gen)
    client.put("/api/settings", json={"values": {"jobs.auto_retries": 0}})
    pid = project_with_video
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()
    failed = [j for j in client.get(f"/api/jobs?project_id={pid}").json()["items"] if j["job_type"] == "reel_generation"][0]
    assert failed["status"] == "failed" and "Ollama" in failed["error"]


def test_captions_are_indexed_so_later_projects_see_them(client, ctx, processed):
    hits = ctx.memory.similar("mercy rises over every chain (segment 0)", ["caption"], 0.5)
    assert hits                                                                                     # this project's reel captions are in memory


# ----- settings, listing, library
def test_the_new_settings_are_validated(client):
    put = lambda **v: client.put("/api/settings", json={"values": v}).status_code
    assert put(**{"thumbnail.variations": 1}) == 200 and put(**{"thumbnail.variations": 3}) == 200
    for bad in (0, 4, True, "3", 2.5):
        assert put(**{"thumbnail.variations": bad}) == 400, bad
    assert put(**{"carousel.slides": 5}) == 200 and put(**{"carousel.slides": 7}) == 200
    for bad in (4, 8, True, "6"):
        assert put(**{"carousel.slides": bad}) == 400, bad
    assert put(**{"reels.unique_copy": False}) == 200 and put(**{"reels.unique_copy": "no"}) == 400
    assert put(**{"thumbnail.best_frame": False}) == 200 and put(**{"thumbnail.best_frame": 1}) == 400


def test_the_new_kinds_are_listed_and_filterable(client, ctx, processed):
    lib = client.get("/api/library?type=carousel").json()
    assert lib["total"] >= 5 and all(i["kind"] == "carousel" for i in lib["items"])
    assert client.get("/api/library?type=thumbnail").json()["total"] == 3
    from ruha.config import ASSET_KINDS, JOB_TYPES
    assert {"thumbnail", "carousel"} <= set(ASSET_KINDS) and {"thumbnail_generation", "carousel_generation"} <= set(JOB_TYPES)


def test_deleting_a_project_removes_the_new_files(client, ctx, processed):
    paths = [raw(ctx, a["id"])[1] for a in assets(client, processed, "carousel") + assets(client, processed, "thumbnail")]
    assert paths and all(Path(p).exists() for p in paths)
    assert client.delete(f"/api/projects/{processed}?confirm=true").status_code == 200
    assert not any(Path(p).exists() for p in paths)
