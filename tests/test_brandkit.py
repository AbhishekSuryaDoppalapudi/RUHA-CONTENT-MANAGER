"""Per-project logo and text templates, and the stacked 'small words + one big keyword' captions."""
import io
import json
from pathlib import Path

from PIL import Image

from ruha import handlers
from ruha.services import brand as brand_svc, subtitles
from conftest import upload


def png(color=(20, 120, 220), size=(200, 80)) -> bytes:
    b = io.BytesIO()
    Image.new("RGBA", size, color + (255,)).save(b, "PNG")
    return b.getvalue()


def kit(client, pid):
    return client.get(f"/api/projects/{pid}/brand-kit").json()


def new_project(client, name="Second"):
    return client.post("/api/projects", json={"name": name}).json()["id"]


# ----------------------------------- text templates -----------------------------------
def test_text_templates_can_be_added_edited_deleted_and_validated(client):
    pid = new_project(client)
    assert kit(client, pid) == {"has_logo": False, "texts": [], "kinds": brand_svc.TEXT_KINDS}
    r = client.post(f"/api/projects/{pid}/brand-kit/texts", json={"kind": "closing", "text": "Follow El Ruach"})
    assert r.status_code == 201 and r.json()["texts"][0]["name"] == "Follow El Ruach"
    tid = r.json()["texts"][0]["id"]
    r = client.put(f"/api/projects/{pid}/brand-kit/texts/{tid}", json={"kind": "caption", "name": "CTA", "text": "Watch the full message"})
    assert r.json()["texts"][0]["kind"] == "caption" and r.json()["texts"][0]["name"] == "CTA"
    assert client.post(f"/api/projects/{pid}/brand-kit/texts", json={"kind": "nope", "text": "x"}).status_code == 400
    assert client.post(f"/api/projects/{pid}/brand-kit/texts", json={"kind": "closing", "text": "   "}).status_code == 400
    assert client.post(f"/api/projects/{pid}/brand-kit/texts", json={"kind": "closing", "text": "x" * 700}).status_code == 400
    assert client.delete(f"/api/projects/{pid}/brand-kit/texts/{tid}").json()["texts"] == []
    assert client.get("/api/projects/9999/brand-kit").status_code == 404


def test_text_templates_upload_from_txt_and_json(client):
    pid = new_project(client)
    r = client.post(f"/api/projects/{pid}/brand-kit/texts/upload?kind=hashtags",
                    files={"file": ("t.txt", "#ElRuach #Faith\n\n#Sermon\n".encode(), "text/plain")})
    assert r.status_code == 200 and [t["kind"] for t in r.json()["texts"]] == ["hashtags", "hashtags"]
    data = json.dumps([{"kind": "closing", "name": "Close", "text": "Join us Sunday"}, "Plain string"])
    r = client.post(f"/api/projects/{pid}/brand-kit/texts/upload?kind=caption", files={"file": ("t.json", data.encode(), "application/json")})
    kinds = [t["kind"] for t in r.json()["texts"]]
    assert kinds.count("closing") == 1 and kinds.count("caption") == 1
    assert client.post(f"/api/projects/{pid}/brand-kit/texts/upload", files={"file": ("t.exe", b"x", "application/octet-stream")}).status_code == 400
    assert client.post(f"/api/projects/{pid}/brand-kit/texts/upload", files={"file": ("t.json", b"{bad", "application/json")}).status_code == 400
    assert client.post(f"/api/projects/{pid}/brand-kit/texts/upload", files={"file": ("t.txt", b"\n\n", "text/plain")}).status_code == 400


def test_caption_footer_hashtags_and_closing_helpers():
    b = {"texts": [{"kind": "caption", "text": "Watch the full message"}, {"kind": "hashtags", "text": "#ElRuach, faith #Grace"},
                   {"kind": "closing", "text": "Join us Sunday"}]}
    cap, tags = brand_svc.with_texts(b, "A caption", ["#grace", "#hope"])
    assert cap == "A caption\n\nWatch the full message" and tags == ["#grace", "#hope", "#ElRuach", "#faith"]
    assert brand_svc.with_texts(b, cap, tags) == (cap, tags)                        # idempotent: no duplicates
    assert brand_svc.closing_text(b, "Follow for more") == "Join us Sunday"
    assert brand_svc.closing_text({"texts": []}, "Follow for more") == "Follow for more"
    assert brand_svc.with_texts({"texts": []}, "x", ["#a"]) == ("x", ["#a"])


# ------------------------------------------ logo -------------------------------------------
def test_project_logo_upload_validation_serving_and_isolation(client, ctx):
    a, b = new_project(client, "A"), new_project(client, "B")
    assert client.post(f"/api/projects/{a}/brand-kit/logo", files={"file": ("l.png", b"not an image", "image/png")}).status_code == 400
    assert client.post(f"/api/projects/{a}/brand-kit/logo", files={"file": ("l.exe", b"x", "application/octet-stream")}).status_code == 400
    r = client.post(f"/api/projects/{a}/brand-kit/logo", files={"file": ("l.png", png(), "image/png")})
    assert r.status_code == 200 and r.json()["has_logo"]
    served = client.get(f"/api/projects/{a}/brand-kit/logo")
    assert served.status_code == 200 and Image.open(io.BytesIO(served.content)).size == (200, 80)
    logo_a = brand_svc.get_brand(ctx, a)["logo"]
    assert logo_a and Path(logo_a).exists() and "brand" in Path(logo_a).parts
    assert brand_svc.get_brand(ctx, a)["watermark"] == logo_a                    # also the reel watermark
    assert brand_svc.get_brand(ctx, b)["logo"] != logo_a                         # other projects are unaffected
    assert brand_svc.get_brand(ctx)["logo"] != logo_a
    assert client.get(f"/api/projects/{b}/brand-kit/logo").status_code == 404
    first = logo_a
    client.post(f"/api/projects/{a}/brand-kit/logo", files={"file": ("l2.png", png((200, 30, 30)), "image/png")})
    assert not Path(first).exists() and brand_svc.get_brand(ctx, a)["logo"] != first       # replaced, old file removed
    assert client.delete(f"/api/projects/{a}/brand-kit/logo").json()["has_logo"] is False
    assert brand_svc.get_brand(ctx, a)["logo"] == brand_svc.get_brand(ctx)["logo"]


def test_deleting_a_project_forgets_its_kit(client, ctx):
    pid = new_project(client)
    client.post(f"/api/projects/{pid}/brand-kit/texts", json={"kind": "closing", "text": "Bye"})
    client.post(f"/api/projects/{pid}/brand-kit/logo", files={"file": ("l.png", png(), "image/png")})
    assert client.delete(f"/api/projects/{pid}?confirm=true").status_code == 200
    assert brand_svc.get_kit(ctx, pid) == {"logo": None, "texts": []}


def test_pipeline_uses_the_project_logo_closing_line_footer_and_hashtags(client, ctx, project_with_video, monkeypatch):
    pid = project_with_video
    client.post(f"/api/projects/{pid}/brand-kit/logo", files={"file": ("l.png", png(), "image/png")})
    for kind, text in (("closing", "Join El Ruach on Sunday"), ("caption", "Watch the full message on YouTube"),
                       ("hashtags", "#ElRuach #SundayService")):
        client.post(f"/api/projects/{pid}/brand-kit/texts", json={"kind": kind, "text": text})
    logo = brand_svc.get_brand(ctx, pid)["logo"]
    closings, logos = [], []
    real_card, real_graphic = handlers.render_card, handlers.render_graphic

    def card(*a, **k):
        if len(a) > 4 and a[4] == "close":
            closings.append(a[6])
        logos.append(a[3]["logo"] if a[3] else None)
        return real_card(*a, **k)

    def graphic(*a, **k):
        logos.append(a[6]["logo"])
        return real_graphic(*a, **k)

    monkeypatch.setattr(handlers, "render_card", card)
    monkeypatch.setattr(handlers, "render_graphic", graphic)
    assert client.post(f"/api/projects/{pid}/process").status_code == 202
    ctx.queue.drain()
    assert closings and set(closings) == {"Join El Ruach on Sunday"}              # every reel's closing card
    assert logos and set(logos) == {logo}                                         # cards, poster and posts
    items = client.get(f"/api/assets?project_id={pid}").json()
    for kind in ("main_reel", "short_reel", "social_post"):
        for it in [x for x in items if x["kind"] == kind]:
            d = client.get(f"/api/assets/{it['id']}").json()
            assert d["caption"].endswith("Watch the full message on YouTube"), kind
            assert "#ElRuach" in d["hashtags"] and "#SundayService" in d["hashtags"], kind
    poster = [x for x in items if x["kind"] == "poster"][0]
    assert "#ElRuach" in client.get(f"/api/assets/{poster['id']}").json()["hashtags"]


# ------------------------------------- stacked captions -------------------------------------
def test_stacked_captions_use_small_words_over_one_big_keyword(tmp_path):
    f = subtitles.build_ass([(0, 2, "the New Testament"), (2, 4.2, "He went about persecuting people")], tmp_path / "s.ass",
                            1080, 1920, {}, accent="#F2B84B")
    txt = f.read_text(encoding="utf-8-sig")
    assert "Poppins ExtraBold" in txt and "Poppins SemiBold" in txt
    assert "Testament" in txt and "TESTAMENT" not in txt                 # mixed case, like the reference
    assert "the New" in txt and "persecuting" in txt and "people" in txt
    assert txt.count("Dialogue: 0,") == txt.count("Dialogue: 1,") == txt.count("Dialogue: 2,") >= 3   # halo, glow, crisp
    assert "&H004BB8F2&" in txt or "&H4BB8F2&" in txt                     # the gold keyword colour (BGR)


def test_keyword_is_the_last_meaningful_word_and_phrases_are_short():
    assert subtitles._key_index("the New Testament".split()) == 2
    assert subtitles._key_index("persecuting people".split()) == 1
    assert subtitles._key_index("He went about".split()) == 2                 # nothing strong: the last word
    assert subtitles._key_index("trust God".split()) == 1
    assert subtitles._key_index("he'll kill me.".split()) == 1                 # a contraction is never the big word
    groups = subtitles._groups("and then right when I was about ready to give up".split())
    assert len(groups) == 3 and all(len(g) <= 4 for g in groups)
    assert [w for g in groups for w in g] == "and then right when I was about ready to give up".split()


def test_stacked_style_applies_to_latin_only_and_classic_can_be_forced(tmp_path):
    cues = [(0, 2, "The wonderful grace of God")]
    assert "Poppins" in subtitles.build_ass(cues, tmp_path / "a.ass", 1080, 1920, {}).read_text(encoding="utf-8-sig")
    classic = subtitles.build_ass(cues, tmp_path / "b.ass", 1080, 1920, {"caption_style": "classic"}).read_text(encoding="utf-8-sig")
    assert "WONDERFUL" in classic and "Poppins" not in classic
    mixed = subtitles.build_ass(cues + [(2, 4, "దేవుని కృప")], tmp_path / "c.ass", 1080, 1920, {}).read_text(encoding="utf-8-sig")
    assert "Style: Indic" in mixed and "Poppins" not in mixed                  # Indian scripts keep their own fonts


def test_stacked_captions_render_into_a_real_video(tmp_path, sample_video):
    from ruha.services import media
    out = media.render_reel(sample_video, 0, 3, tmp_path / "r.mp4", width=270, height=480, crf=32, preset="ultrafast",
                            cues=[(0, 1.5, "the New Testament"), (1.5, 3, "He went about persecuting people")])
    info = media.probe(out)
    assert info["width"] == 270 and info["audio_tracks"] == 1
    from PIL import Image as _I
    import subprocess
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1", str(frame)], check=True)
    plain = tmp_path / "p.mp4"
    media.render_reel(sample_video, 0, 3, plain, width=270, height=480, crf=32, preset="ultrafast", burn_subtitles=False)
    pframe = tmp_path / "pf.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(plain), "-frames:v", "1", str(pframe)], check=True)
    a, b = _I.open(frame).convert("L"), _I.open(pframe).convert("L")
    diff = sum(abs(x - y) > 40 for x, y in zip(a.getdata(), b.getdata()))
    assert diff > 150                                                           # caption pixels were really drawn
