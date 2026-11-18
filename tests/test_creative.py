"""Creative Studio: hardware, brief intelligence, eight design engines, two-stage composition, QC, workflow."""
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from PIL import Image

from ruha.creative import backends, brief, compose, formats, hardware, qc, studio, styles
from ruha.models import GeneratedAsset
from ruha.services import htmlrender

needs_browser = pytest.mark.skipif(not htmlrender.available(), reason="no Edge/Chrome/Chromium installed")
LONG = "Why the battle you are fighting was never against people at all"


# --------------------------------------------------------------- hardware
def test_model_recommendation_follows_real_vram():
    assert hardware.recommend(None)["model"] is None and "No NVIDIA GPU" in hardware.recommend(None)["reason"]
    assert hardware.recommend(12)["model"] == "sdxl"
    assert hardware.recommend(8)["model"] == "sd15" and hardware.recommend(6)["offload"] == "model"
    assert hardware.recommend(2)["model"] is None
    assert hardware.MODELS["sdxl-turbo"]["licence"].lower().count("non-commercial") == 1   # licence is surfaced, not hidden


def test_resource_gate_refuses_when_gpu_or_ram_is_busy(monkeypatch):
    monkeypatch.setattr(hardware, "gpu_info", lambda: {"name": "RTX 3060", "total_gb": 12.0, "free_gb": 0.8})
    monkeypatch.setattr(hardware, "ram_info", lambda: {"total_gb": 16, "free_gb": 9})
    assert "GPU memory" in hardware.check_resources("sdxl")
    monkeypatch.setattr(hardware, "gpu_info", lambda: {"name": "RTX 3060", "total_gb": 12.0, "free_gb": 10})
    assert hardware.check_resources("sdxl") is None
    monkeypatch.setattr(hardware, "ram_info", lambda: {"total_gb": 16, "free_gb": 0.9})
    assert "RAM" in hardware.check_resources("sdxl")


def test_local_backend_reports_missing_install_plainly():
    try:
        import diffusers  # noqa: F401
        pytest.skip("diffusers is installed here")
    except ImportError:
        pass
    ok, why = backends.DiffusersBackend().available({})
    assert not ok and "requirements-gpu.txt" in why


def test_service_says_so_when_no_model_is_available():
    svc = backends.ImageService(comfy=backends.ComfyBackend(urlopen=lambda *a, **k: (_ for _ in ()).throw(OSError("down"))),
                                diffusers=type("D", (), {"name": "diffusers", "available": lambda s, c: (False, "not installed"),
                                                         "unload": lambda s: None})())
    st = svc.status({})
    assert not st["available"] and "No image-generation model is available" in st["reason"]
    with pytest.raises(backends.ImageBackendError):
        svc.generate("p", "n", (512, 768), 1, {})
    assert "switched off" in backends.ImageService().status({"creative.backend": "none"})["reason"]


class _FakeComfy(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/system_stats"):
            self._send(b"{}")
        elif self.path.startswith("/object_info/CheckpointLoaderSimple"):
            self._send(json.dumps({"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sd15.safetensors"]]}}}}).encode())
        elif self.path.startswith("/history/"):
            self._send(json.dumps({"abc": {"outputs": {"7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}}}}).encode())
        elif self.path.startswith("/view"):
            b = io.BytesIO()
            Image.new("RGB", (64, 96), (10, 120, 200)).save(b, "PNG")
            self._send(b.getvalue(), "image/png")

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        _FakeComfy.seen = json.loads(self.rfile.read(n))
        self._send(b'{"prompt_id": "abc"}')


def test_comfyui_protocol_round_trip():
    srv = HTTPServer(("127.0.0.1", 0), _FakeComfy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = {"creative.backend": "comfyui", "creative.comfyui_url": f"http://127.0.0.1:{srv.server_port}"}
        svc = backends.ImageService()
        assert svc.status(cfg)["backend"] == "comfyui"
        img, info = svc.generate("a hill at dawn", "text, watermark", (512, 768), 777, cfg)
        assert img.size == (64, 96) and info["backend"] == "comfyui"
        wf = _FakeComfy.seen["prompt"]
        assert wf["2"]["inputs"]["text"] == "a hill at dawn" and wf["3"]["inputs"]["text"] == "text, watermark"
        assert wf["5"]["inputs"]["seed"] == 777 and wf["4"]["inputs"]["width"] == 512 and wf["4"]["inputs"]["height"] == 768
        assert wf["1"]["inputs"]["ckpt_name"] == "sd15.safetensors"
    finally:
        srv.shutdown()


# --------------------------------------------------------------- design engines
def test_there_are_eight_genuinely_different_engines():
    assert len(styles.STYLES) == 8
    assert len({s["treatment"] for s in styles.STYLES.values()}) == 8            # eight typography systems
    assert set(styles.STYLES) <= {t for t in styles.STYLES} and all(s["treatment"] in compose.TREAT for s in styles.STYLES.values())
    grades = {json.dumps(s["grade"], sort_keys=True) for s in styles.STYLES.values()}
    assert len(grades) == 8                                                       # eight colour treatments
    fonts = {compose.TREAT[s["treatment"]]["font"] for s in styles.STYLES.values()}
    assert len(fonts) >= 4
    photo = [s for s in styles.STYLES.values() if s["visual_source"] == "photo"]
    assert len(photo) == 7 and styles.STYLES["minimal"]["visual_source"] == "typographic"


def test_image_prompts_never_ask_the_model_to_write_text_and_forbid_faces_of_god():
    b = {"emotional_theme": "warfare", "headline": "x", "candidates": {"headlines": ["a b c"], "metaphors": [
        {"subject": "a lone figure kneeling in prayer", "environment": "a ridge"}], "key_points": []}}
    for sid, st in styles.STYLES.items():
        if st["visual_source"] != "photo":
            continue
        c = brief.expand_concepts(b, "feed_post", [sid])[0]
        assert "text" in c["negative"] and "face of Jesus" in c["negative"]
        assert "headline" not in c["prompt"].lower()
        assert "face not visible" in c["prompt"]                                   # human subject seen from behind


def test_formats_have_their_own_safe_areas_and_zones():
    sizes = {k: v["size"] for k, v in formats.FORMATS.items()}
    assert sizes["reel_cover"] == (1080, 1920) and sizes["thumbnail"] == (1280, 720) and sizes["square_post"] == (1080, 1080)
    assert formats.FORMATS["reel_cover"]["safe"][3] > formats.FORMATS["feed_post"]["safe"][3]    # reel UI band
    zones = {formats.aspect_class(s): None for s in sizes.values()}
    assert set(zones) == {"tall", "portrait", "square", "wide"}
    for st in styles.STYLES.values():
        assert st["zones"]["wide"] == "left-half"                                  # thumbnails are left-weighted
    gw, gh = formats.gen_size((1080, 1920), (832, 1216))
    assert abs(gw / gh - 1080 / 1920) < 0.02 and gw % 8 == 0 and gh % 8 == 0


# --------------------------------------------------------------- brief intelligence
PLAN = {"title": "Grace Sermon", "main_theme": "God's grace is free", "summary": "A talk about grace.", "visual_theme": "grace",
        "key_points": ["grace is free", "you cannot earn it"], "poster": {"headline": "Grace Is Free", "subheadline": "Segment 3"},
        "encouragement_posts": [{"headline": "You are loved", "reference": "Hezekiah 9:9"}],
        "main_reels": [{"hook": "Grace is free"}], "short_reels": [{"hook": "Short one"}]}
TEXT = "Segment 3: grace is the free gift of God number 3 for every person"


def test_brief_is_derived_from_the_transcript_and_guards_scripture(ctx):
    b = brief.build_brief(ctx.services.llm, {}, PLAN, TEXT, "Sunday")
    assert b["emotional_theme"] == "grace" and b["message"] and b["audience"]
    assert len(b["candidates"]["headlines"]) == 3 and len({h.lower() for h in b["candidates"]["headlines"]}) == 3
    assert len(b["candidates"]["metaphors"]) == 3
    joined = " ".join(b["candidates"]["headlines"]) + b["supporting_text"]
    assert "Romans 8:28" not in joined and "John 3:16" not in joined                   # invented verses are stripped
    assert b["scripture_reference"] == "Segment 3"                                     # only a reference actually spoken
    assert brief.valid_reference("Hezekiah 9:9", TEXT) == ""                           # unspoken reference refused
    for k in ("message", "emotional_theme", "audience", "visual_metaphor", "visual_subject", "environment", "lighting",
              "colour_palette", "composition", "headline", "supporting_text", "scripture_reference", "creative_direction"):
        assert k in b


def test_brief_falls_back_to_the_stored_plan_when_the_model_fails(ctx):
    ctx.services.llm.fail = True
    b = brief.build_brief(ctx.services.llm, {}, PLAN, TEXT, "Sunday")
    assert b["source"].startswith("plan") and b["candidates"]["headlines"][0] == "Grace Is Free"
    assert all(not h.lower().startswith("in this sermon") for h in b["candidates"]["headlines"])
    assert len(b["candidates"]["metaphors"]) == 3


def test_headline_shortening_prefers_a_clean_clause_and_never_ends_mid_air():
    assert brief.shorten("The enemy is real, but God is greater than any battle you face today", 6) == "The enemy is real"
    assert len(brief.shorten(LONG, 5).split()) <= 5
    assert brief.similar("Stop fighting the wrong enemy", "stop fighting the wrong enemy!")
    assert not brief.similar("Grace is free", "Keep walking in faith")


def test_three_concepts_differ_in_style_subject_light_palette_and_composition(ctx):
    b = brief.build_brief(ctx.services.llm, {}, PLAN, TEXT, "Sunday")
    cs = brief.expand_concepts(b, "poster", styles.DEFAULT_TRIO)
    assert len(cs) == 3
    for field in ("style", "visual_subject", "lighting", "colour_palette", "accent", "zone", "headline", "prompt", "treatment"):
        assert len({c[field] for c in cs}) == 3, field
    assert [c["style"] for c in cs] == ["cinematic", "editorial", "fineart"]
    th = brief.expand_concepts(b, "thumbnail", ["movie"])[0]
    assert th["zone"] == "left-half" and len(th["headline"].split()) <= 5 and th["supporting_text"] == ""


def test_carousel_slides_use_only_the_sermons_own_points(ctx):
    b = brief.build_brief(ctx.services.llm, {}, PLAN, TEXT, "Sunday")
    sl = brief.carousel_slides(b, 5)
    assert sl[0]["role"] == "hook" and sl[-1]["role"] == "close" and sl[-1]["scripture_reference"] == "Segment 3"
    assert [s["headline"] for s in sl if s["role"] == "point"] == ["grace is free", "you cannot earn it"]


# --------------------------------------------------------------- QC (no browser needed)
def _fake_result(text_rgb="#FFFFFF", bg=(250, 250, 250), size=(1080, 1350), box=(100, 900, 900, 200), fits=True, font=100):
    W, H = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    x, y, w, h = box
    overlay.paste(Image.new("RGBA", (w, h), (255, 255, 255, 255)), (x, y))
    bgimg = Image.new("RGB", size, bg)
    final = Image.alpha_composite(bgimg.convert("RGBA"), overlay).convert("RGB")
    m = {"fits": fits, "fontSize": font, "minFont": 48, "h": {"x": x, "y": y, "w": w, "h": h}, "s": None, "r": None, "b": {"x": 1}}
    return {"final": final, "background": bgimg, "overlay": overlay, "metrics": m,
            "colours": {"text": text_rgb, "support": "#E0E0E0", "accent": "#F0A040"}}


C0 = {"headline": "Grace is free", "supporting_text": "", "scripture_reference": "", "visual_source": "typographic",
      "visual_subject": "", "environment": ""}


def test_qc_catches_unreadable_text_clipping_and_unsafe_margins():
    bad = qc.check(_fake_result(), C0, "feed_post", transcript_text=TEXT)        # white text on a near-white picture
    assert "contrast_headline" in bad["failed"] and not bad["passed"]
    clipped = qc.check(_fake_result(bg=(10, 10, 10), box=(0, 900, 1080, 200)), C0, "feed_post", transcript_text=TEXT)
    assert "safe_margins" in clipped["failed"] and "no_clipping" in clipped["failed"]
    nofit = qc.check(_fake_result(bg=(10, 10, 10), fits=False), C0, "feed_post", transcript_text=TEXT)
    assert "text_fits" in nofit["failed"]
    ok = qc.check(_fake_result(bg=(10, 10, 10)), C0, "feed_post", transcript_text=TEXT)
    assert ok["passed"] and ok["needs_human_review"] is True                       # passes, but still needs a person


def test_qc_blocks_fabricated_scripture_and_duplicates():
    r = _fake_result(bg=(10, 10, 10))
    c = {**C0, "scripture_reference": "Hezekiah 9:9", "headline": "See Romans 8:28 today"}
    q = qc.check(r, c, "feed_post", transcript_text=TEXT)
    assert {"scripture_integrity", "no_invented_references"} <= set(q["failed"])
    same = qc.check(r, C0, "feed_post", transcript_text=TEXT, other_hashes=[qc.dhash(r["background"])],
                    other_headlines=["Grace is free!"])
    assert {"duplicate_visual", "duplicate_text"} <= set(same["failed"])


def test_photo_creatives_always_carry_a_manual_relevance_check_and_are_never_marked_verified():
    r = _fake_result(bg=(40, 60, 90))
    rng = np.random.default_rng(1)
    r["background"] = Image.fromarray(rng.integers(0, 255, (1350, 1080, 3), dtype=np.uint8))
    q = qc.check(r, {**C0, "visual_source": "photo"}, "feed_post", transcript_text=TEXT, backend={"model": "fake"})
    rel = next(c for c in q["checks"] if c["name"] == "visual_relevance")
    assert rel["status"] == "manual" and "vision model" in rel["detail"]


# --------------------------------------------------------------- composition with the real browser
def _picture(size, seed=3):
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, size[1])[:, None, None]
    a = (rng.integers(20, 120, 3) * (1 - y) + rng.integers(100, 230, 3) * y) * np.ones((1, size[0], 1))
    return Image.fromarray(a.astype("uint8"))


@needs_browser
def test_every_design_engine_composes_readable_unclipped_typography():
    b = {"emotional_theme": "faith", "headline": LONG, "supporting_text": "A short supporting line", "scripture_reference": "Ephesians 6:12",
         "candidates": {"headlines": [LONG] * 3, "metaphors": [{"subject": "a lone figure", "environment": "a ridge"}] * 3, "key_points": []}}
    seen = set()
    for sid in styles.STYLES:
        c = brief.expand_concepts(b, "feed_post", [sid])[0]
        c["headline"] = brief.shorten(LONG, 8)
        res = compose.compose(_picture((832, 1040)), c, "feed_post", {"name": "El Ruach"})
        q = qc.check(res, c, "feed_post", transcript_text="Ephesians six twelve")
        bb = res["overlay"].getchannel("A").getbbox()
        l, t, r, bt = formats.FORMATS["feed_post"]["safe"]
        assert bb[0] >= l - 3 and bb[1] >= t - 3 and bb[2] <= 1080 - r + 3 and bb[3] <= 1350 - bt + 3, (sid, bb)   # independent bbox check
        assert q["passed"], (sid, q["failed"])
        assert res["metrics"]["fontSize"] >= res["metrics"]["minFont"]
        seen.add(res["final"].resize((16, 20)).tobytes())
    assert len(seen) == 8                                                           # eight visibly different designs


@needs_browser
def test_overflowing_text_is_detected_not_clipped_silently():
    b = {"emotional_theme": "faith", "headline": "x", "candidates": {"headlines": ["x"], "metaphors": [{"subject": "s", "environment": "e"}], "key_points": []}}
    c = brief.expand_concepts(b, "thumbnail", ["cinematic"])[0]
    c["headline"] = f"{LONG}. {LONG}"                                                # far too long for a thumbnail
    c["supporting_text"], c["scripture_reference"] = "A supporting line as well", "Ephesians 6:12"
    res = compose.compose(_picture((1280, 720)), c, "thumbnail", {"name": "El Ruach"})
    q = qc.check(res, c, "thumbnail", transcript_text="")
    assert not q["passed"] and ({"text_fits", "mobile_readability"} & set(q["failed"]))


# --------------------------------------------------------------- end to end through the API
def _gen(client, ctx, pid, **body):
    r = client.post(f"/api/projects/{pid}/creative/generate", json=body)
    assert r.status_code == 202, r.text
    ctx.queue.drain()
    return client.get(f"/api/projects/{pid}/creative").json()


@needs_browser
def test_three_distinct_poster_concepts_through_the_whole_workflow(client, ctx, processed):
    fake = ctx.services.images
    ws = client.get(f"/api/projects/{processed}/creative").json()
    assert ws["source"]["main_theme"] and ws["assets"] == []
    out = _gen(client, ctx, processed, formats=["poster"])
    items = out["assets"]
    assert len(items) == 3 and len(fake.calls) == 3 and fake.unloads >= 1
    assert out["brief"]["emotional_theme"] and len(out["brief"]["candidates"]["headlines"]) == 3
    assert {i["meta"]["style"] for i in items} == set(styles.DEFAULT_TRIO)
    assert len({i["meta"]["seed"] for i in items}) == 3 and len({c["prompt"] for c in fake.calls}) == 3
    assert len({i["title"] for i in items}) == 3                                    # no repeated headlines
    assert all(i["kind"] == "creative" and i["status"] in ("draft", "revision_requested") for i in items)
    assert all(i["status"] != "approved" for i in items)                            # never auto-approved
    for i in items:
        qcr = i["meta"]["qc"]
        assert qcr["checks"] and "hash" in qcr and i["meta"]["visual_source"] == "photo" and i["meta"]["model"] == "fake-sd"
        assert not {"safe_margins", "no_clipping", "scripture_integrity"} & set(qcr["failed"])
    hashes = [i["meta"]["qc"]["hash"] for i in items]
    assert min(qc.hamming(a, b) for a in hashes for b in hashes if a != b) > 8     # pictures are visibly different
    for i in items:                                                                  # real files at the right size
        r = client.get(f"/api/assets/{i['id']}/file")
        assert Image.open(io.BytesIO(r.content)).size == (1080, 1350)


@needs_browser
def test_each_format_is_composed_for_its_own_size(client, ctx, processed):
    out = _gen(client, ctx, processed, formats=["reel_cover", "thumbnail", "square_post"], styles=["cinematic"])
    by = {i["meta"]["format"]: i for i in out["assets"]}
    assert set(by) == {"reel_cover", "thumbnail", "square_post"}
    for k, i in by.items():
        assert Image.open(io.BytesIO(client.get(f"/api/assets/{i['id']}/file").content)).size == formats.FORMATS[k]["size"]
    assert by["thumbnail"]["meta"]["concept"]["zone"] == "left-half" and by["reel_cover"]["meta"]["concept"]["zone"] == "lower-left"
    sizes = {tuple(c["size"]) for c in ctx.services.images.calls}
    assert len({round(w / h, 2) for w, h in sizes}) == 3                            # a picture generated per aspect ratio


@needs_browser
def test_carousel_makes_a_continuous_set_of_slides(client, ctx, processed):
    out = _gen(client, ctx, processed, formats=["carousel"], styles=["editorial"])
    slides = sorted(out["assets"], key=lambda a: a["meta"]["slide"])
    points = out["brief"]["candidates"]["key_points"]
    assert len(slides) == 2 + len(points)                                            # hook + the sermon's own points + close; nothing invented
    assert slides[0]["meta"]["slide"] == 1 and len(ctx.services.images.calls) == 1   # one picture for the whole set
    assert len({s["title"] for s in slides}) == len(slides)


@needs_browser
def test_headline_edit_recomposes_without_regenerating_and_keeps_history(client, ctx, processed):
    out = _gen(client, ctx, processed, formats=["feed_post"], styles=["cinematic"])
    a = out["assets"][0]
    calls = len(ctx.services.images.calls)
    r = client.post(f"/api/creative/{a['id']}/recompose", json={"headline": "A brand new headline"})
    assert r.status_code == 202
    ctx.queue.drain()
    d = client.get(f"/api/assets/{a['id']}").json()
    assert d["version"] == 2 and d["title"] == "A brand new headline" and len(ctx.services.images.calls) == calls
    assert [v["version"] for v in d["versions"]] == [1, 2]
    bad = client.post(f"/api/creative/{a['id']}/recompose", json={"scripture_reference": "Hezekiah 9:9"})
    ctx.queue.drain()
    d2 = client.get(f"/api/assets/{a['id']}").json()
    assert d2["status"] == "revision_requested" and "scripture_integrity" in d2["meta"]["qc"]["failed"]   # fabricated verse blocked
    client.post(f"/api/creative/{a['id']}/regenerate")
    ctx.queue.drain()
    assert len(ctx.services.images.calls) == calls + 1                              # regenerate paints a new picture
    assert client.post(f"/api/assets/{a['id']}/review", json={"action": "approve"}).status_code in (200, 201)
    assert client.get(f"/api/assets/{a['id']}").json()["status"] == "approved"


def test_without_an_image_model_the_app_says_so_instead_of_faking_artwork(client, ctx, processed):
    ctx.services.images.up = False
    r = client.post(f"/api/projects/{processed}/creative/generate", json={"formats": ["poster"]})
    assert r.status_code == 409 and "No image-generation model is available" in r.json()["detail"]
    assert client.get(f"/api/projects/{processed}/creative").json()["assets"] == []
    st = client.get("/api/creative/status").json()
    assert st["available"] is False and len(st["styles"]) == 8 and any(f["id"] == "thumbnail" for f in st["formats"])


@needs_browser
def test_typographic_engine_works_without_a_model_and_is_labelled_honestly(client, ctx, processed):
    ctx.services.images.up = False
    out = _gen(client, ctx, processed, formats=["feed_post"], styles=["minimal"])
    a = out["assets"][0]
    assert a["meta"]["visual_source"] == "typographic" and ctx.services.images.calls == []
    vs = next(c for c in a["meta"]["qc"]["checks"] if c["name"] == "visual_source")
    assert "no AI artwork claimed" in vs["detail"]


def test_benchmark_job_records_measurements(client, ctx):
    r = client.post("/api/creative/benchmark")
    assert r.status_code == 202
    ctx.queue.drain()
    bm = client.get("/api/creative/status").json()["benchmark"]
    assert bm["backend"] == "fake" and bm["seconds_first_image"] >= 0 and bm["size"] == [512, 768]


@needs_browser
def test_encouragement_posts_use_the_sermons_own_hooks_verbatim(client, ctx, processed):
    src = client.get(f"/api/projects/{processed}/creative").json()["source"]
    hooks = src["encouragement"][:2]
    assert hooks == ["You are loved", "Keep going"]
    out = _gen(client, ctx, processed, formats=["feed_post"], styles=["emotional", "modern"], headlines=hooks)
    posts = sorted(out["assets"], key=lambda a: a["id"])
    assert [p["title"] for p in posts] == hooks and {p["meta"]["style"] for p in posts} == {"emotional", "modern"}
    assert all(p["meta"]["concept"]["supporting_text"] == "" for p in posts)            # hook-only, no invented body copy
    assert len({p["meta"]["qc"]["hash"] for p in posts}) == 2


def test_unsafe_or_repetitive_visual_metaphors_are_replaced_with_curated_symbolic_scenes(ctx):
    class Angels:
        def generate_json(self, prompt, cfg):
            return {"emotional_theme": "warfare", "headlines": ["Who is winning?", "Stand firm today", "Know your enemy"],
                    "visual_metaphors": [{"subject": "angels in a battlefield", "environment": "dark sky"},
                                         {"subject": "angels in formation", "environment": "organized night sky"},
                                         {"subject": "Satan on a throne", "environment": "fire"}]}
    b = brief.build_brief(Angels(), {}, PLAN, TEXT, "Sunday")
    metas = b["candidates"]["metaphors"]
    assert len(metas) == 3 and len({m["subject"] for m in metas}) == 3
    assert not any(brief.UNSAFE_SUBJECT.search(m["subject"] + " " + m["environment"]) for m in metas)
    assert [m["subject"] for m in metas] == [x[0] for x in styles.METAPHORS["warfare"]]
