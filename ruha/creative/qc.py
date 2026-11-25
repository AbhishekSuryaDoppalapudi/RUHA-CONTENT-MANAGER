"""Quality control for every creative. Checks run on the real composed pixels and the browser-measured text geometry.
Nothing is auto-approved: a creative that passes is still 'draft' until a person approves it, and visual relevance
(does the picture fit the message?) is reported as a manual check because it cannot be machine-verified here."""
from __future__ import annotations

import numpy as np
from PIL import Image

from ruha.creative import brief as brief_mod
from ruha.creative import compose, formats

TOL = 3                      # px tolerance on geometry
MIN_CONTRAST_LARGE = 3.0     # WCAG AA for large text
MIN_CONTRAST_SMALL = 4.5     # WCAG AA for body text
DUP_FAIL, DUP_WARN = 8, 14   # dHash Hamming distance (of 64 bits)


def dhash(img: Image.Image) -> int:
    g = np.asarray(img.convert("L").resize((9, 8), Image.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _bbox(overlay: Image.Image, thresh: int = 24):
    a = np.asarray(overlay.getchannel("A"))
    ys, xs = np.where(a > thresh)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _text_contrast(bg: Image.Image, overlay: Image.Image, rect: dict | None, text_rgb) -> float | None:
    """Worst-case contrast of text against the picture beneath it (95th/5th percentile of background luminance)."""
    if not rect or rect.get("w", 0) < 2:
        return None
    x0, y0 = max(0, int(rect["x"])), max(0, int(rect["y"]))
    x1, y1 = min(bg.width, int(rect["x"] + rect["w"])), min(bg.height, int(rect["y"] + rect["h"]))
    if x1 <= x0 or y1 <= y0:
        return None
    a = np.asarray(overlay.crop((x0, y0, x1, y1)).getchannel("A")) > 180
    if a.sum() < 30:
        return None
    lum = compose.luminance(np.asarray(bg.crop((x0, y0, x1, y1)).convert("RGB")))[a]
    lt = float(compose.luminance(np.array(text_rgb, dtype=np.float64)))
    worst = float(np.percentile(lum, 95 if lt > 0.4 else 5))
    return compose.contrast_ratio(lt, worst)


def check(result: dict, concept: dict, fmt_key: str, *, transcript_text: str = "", other_hashes=(),
          other_headlines=(), show_brand: bool = True, slide: dict | None = None, backend: dict | None = None) -> dict:
    fmt = formats.FORMATS[fmt_key]
    W, H = fmt["size"]
    l, t, r, b = fmt["safe"]
    final, bgimg, overlay, m = result["final"], result["background"], result["overlay"], result.get("metrics") or {}
    cols = result["colours"]
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    add("aspect_ratio", "pass" if final.size == (W, H) else "fail", f"{final.size[0]}x{final.size[1]} (expected {W}x{H})")

    if not m:
        add("text_layout", "fail", "The browser did not report the text layout")
    else:
        add("text_fits", "pass" if m.get("fits") else "fail",
            "Text fits its box" if m.get("fits") else "Text does not fit its box at the minimum readable size")
        floor = m.get("minFont", 0)
        ok = m.get("fontSize", 0) >= floor - 1
        add("mobile_readability", "pass" if ok else "fail",
            f"Headline {m.get('fontSize')}px (minimum {floor}px for a {W}x{H} canvas)")
        sf = m.get("sfont") or 0
        if sf:
            ratio = m["fontSize"] / sf
            add("hierarchy", "pass" if ratio >= 1.6 else "warn", f"Headline is {ratio:.1f}x the supporting text")

    bb = _bbox(overlay)
    if bb is None:
        add("text_present", "fail", "No text was drawn")
    else:
        x0, y0, x1, y1 = bb
        inside = x0 >= l - TOL and y0 >= t - TOL and x1 <= W - r + TOL and y1 <= H - b + TOL
        add("safe_margins", "pass" if inside else "fail",
            f"Text spans x {x0}-{x1}, y {y0}-{y1}; safe area is x {l}-{W - r}, y {t}-{H - b}")
        edge = min(x0, y0, W - x1, H - y1)
        add("no_clipping", "pass" if edge >= 8 else "fail", f"Nearest text is {edge}px from the canvas edge")

    # contrast of each text element against the picture directly beneath it
    head_rgb = compose._hex_rgb(cols["text"])
    cr = _text_contrast(bgimg, overlay, m.get("h"), head_rgb)
    if cr is not None:
        add("contrast_headline", "pass" if cr >= MIN_CONTRAST_LARGE else "fail", f"{cr:.1f}:1 (needs {MIN_CONTRAST_LARGE}:1)")
    if m.get("s"):
        cs = _text_contrast(bgimg, overlay, m["s"], compose._hex_rgb(cols["support"]))
        if cs is not None:
            add("contrast_supporting", "pass" if cs >= MIN_CONTRAST_SMALL else "fail", f"{cs:.1f}:1 (needs {MIN_CONTRAST_SMALL}:1)")
    if m.get("r"):
        ca = _text_contrast(bgimg, overlay, m["r"], compose._hex_rgb(cols["accent"]))
        if ca is not None:
            add("contrast_reference", "pass" if ca >= MIN_CONTRAST_LARGE else "warn", f"{ca:.1f}:1")

    # picture quality (only when an image model made the picture)
    if concept.get("visual_source") == "photo":
        g = np.asarray(bgimg.convert("L").resize((270, int(270 * H / W))), dtype=np.float64)
        sd = float(g.std())
        lap = float(np.var(g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]))
        add("image_not_flat", "pass" if sd >= 12 else "fail", f"tonal spread {sd:.0f}")
        add("image_sharpness", "pass" if lap >= 6 else "warn", f"detail score {lap:.0f}")

    # duplicates: picture and wording against the rest of the batch and the project's earlier creatives
    h = dhash(bgimg)
    worst = min((hamming(h, o) for o in other_hashes), default=64)
    if worst <= DUP_FAIL:
        add("duplicate_visual", "fail", f"Almost identical to another creative (distance {worst}/64)")
    elif worst <= DUP_WARN:
        add("duplicate_visual", "warn", f"Similar to another creative (distance {worst}/64)")
    else:
        add("duplicate_visual", "pass", f"Distinct from {len(list(other_hashes))} others (distance {worst}/64)")
    head = (slide or concept)["headline"]
    dup = next((o for o in other_headlines if brief_mod.similar(head, o, 0.7)), None)
    add("duplicate_text", "fail" if dup else "pass", f"Headline repeats '{dup}'" if dup else "Headline is unique")

    # scripture and quotation integrity
    ref = (slide or concept).get("scripture_reference", "")
    ref_ok = (not ref) or bool(brief_mod.valid_reference(ref, transcript_text))
    add("scripture_integrity", "pass" if ref_ok else "fail",
        "No reference shown" if not ref else (f"'{ref}' is spoken in the video" if ref_ok else f"'{ref}' is NOT in the transcript"))
    shown = f"{head} {(slide or concept).get('supporting_text', '')}"
    stray = [x.group(0) for x in brief_mod.REF_RE.finditer(shown)
             if not brief_mod.valid_reference(x.group(0), transcript_text)]
    add("no_invented_references", "pass" if not stray else "fail",
        "No unspoken references in the text" if not stray else f"Unspoken reference in text: {stray[0]}")
    wc = len(head.split())
    add("headline_length", "pass" if wc <= fmt["max_words"] else "warn", f"{wc} words (max {fmt['max_words']} for this format)")

    if show_brand:
        add("brand_consistency", "pass" if m.get("b") else "fail", "El Ruach lockup present" if m.get("b") else "Brand lockup missing")

    # honest source labelling + the one check a machine cannot do
    if concept.get("visual_source") == "photo":
        add("visual_source", "pass", f"Generated by {(backend or {}).get('model', 'an image model')}")
        add("visual_relevance", "manual", "Not machine-verifiable (no vision model installed). Compare the picture with the "
                                          f"brief: '{concept.get('visual_subject', '')}' / '{concept.get('environment', '')}'.")
    else:
        add("visual_source", "pass", "Typographic design: tonal field drawn by the app (no AI artwork claimed)")

    fails = [c for c in checks if c["status"] == "fail"]
    return {"passed": not fails, "failed": [c["name"] for c in fails], "checks": checks,
            "score": round(sum(c["status"] == "pass" for c in checks) / max(1, len(checks)), 2),
            "needs_human_review": True, "hash": h}
