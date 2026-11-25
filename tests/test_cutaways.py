"""Animated cutaways inside a main teaching reel: placement rules and a real composited render."""
import subprocess

from PIL import Image

from ruha.services import cutaways, media


def segs(n=12, each=5.0):
    return [{"start": i * each, "end": (i + 1) * each, "text": f"Sentence {i} about the grace of God today"} for i in range(n)]


def test_pick_places_cutaways_inside_the_teaching_only():
    s = segs(12)
    wins = cutaways.pick(s, 0, 60)
    assert 1 <= len(wins) <= cutaways.MAX_CUTAWAYS
    for w in wins:
        assert w["at"] >= cutaways.LEAD and w["at"] + w["dur"] <= 60 - cutaways.TAIL + 0.01
        assert w["at"] % 5 == 0                                    # snapped to the start of a sentence
        assert w["text"]
    for a, b in zip(wins, wins[1:]):
        assert b["at"] >= a["at"] + a["dur"] + 2.9                 # a gap of speaker between two cutaways


def test_pick_skips_short_reels_and_can_be_switched_off():
    assert cutaways.pick(segs(3), 0, 15) == []
    assert cutaways.pick(segs(12), 0, 60, max_n=0) == []


def test_keyword_is_one_real_word():
    assert cutaways.keyword("He went about persecuting people") == "people"
    assert cutaways.keyword("") == ""


def test_scene_image_is_drawn_at_the_requested_size(tmp_path):
    p = cutaways.scene_image(tmp_path / "c.png", "grace", "His grace is sufficient", (270, 480), "#ffd166")
    assert Image.open(p).size == (270, 480)


def test_cutaway_replaces_the_picture_but_keeps_audio_and_captions(tmp_path, sample_video):
    img = cutaways.scene_image(tmp_path / "c.png", "warfare", "the spiritual battle", (270, 480), "#ffb74d")
    plain = media.render_reel(sample_video, 0, 6, tmp_path / "plain.mp4", width=270, height=480, crf=32,
                              preset="ultrafast", burn_subtitles=False)
    cut = media.render_reel(sample_video, 0, 6, tmp_path / "cut.mp4", width=270, height=480, crf=32, preset="ultrafast",
                            burn_subtitles=False, cutaways=[{"at": 2.0, "dur": 3.0, "image": str(img)}])
    info = media.probe(cut)
    assert info["audio_tracks"] == 1 and abs(info["duration"] - 6) < 0.3        # audio runs unbroken, same length

    def grab(video, t, name):
        f = tmp_path / name
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(f)], check=True)
        return list(Image.open(f).convert("L").getdata())

    def changed(t):
        a, b = grab(plain, t, f"a{t}.png"), grab(cut, t, f"b{t}.png")
        return sum(abs(x - y) > 40 for x, y in zip(a, b)) / len(a)

    assert changed(0.5) < 0.02 and changed(5.6) < 0.02                         # the speaker before and after is untouched
    assert changed(3.5) > 0.4                                                  # mid-cutaway the picture is the scene


def test_cutaway_with_captions_and_watermark_renders(tmp_path, sample_video):
    img = cutaways.scene_image(tmp_path / "c.png", "hope", "a new morning", (270, 480), "#fff0cc")
    wm = tmp_path / "wm.png"
    Image.new("RGBA", (60, 30), (255, 0, 0, 255)).save(wm)
    out = media.render_reel(sample_video, 0, 6, tmp_path / "all.mp4", width=270, height=480, crf=32, preset="ultrafast",
                            cues=[(0, 3, "the New Testament"), (3, 6, "persecuting people")], watermark=wm,
                            cutaways=[{"at": 2.0, "dur": 3.0, "image": str(img)}])
    assert media.probe(out)["audio_tracks"] == 1
