"""Designed 1280x720 thumbnails: the best frame of the clip, the clip's hook (3-5 words) and the brand kit, in three layouts.

  left   the frame full-bleed, brand-coloured fade on the side away from the face, the hook large on that side
  split  a tight crop of the speaker on the left, a brand-coloured panel with the hook on the right
  band   the frame full-bleed, a bold brand band along the bottom carrying the hook in capitals

Every layout shows the clip's category as a small label and the logo of the brand kit, and highlights the hook's keyword in the
accent colour (same rule as the captions). Pure Pillow: no browser, no GPU.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ruha.services import capfonts, emphasis, hooks

SIZE = (1280, 720)
VARIANTS = ("left", "split", "band")
MIN_WORDS, MAX_WORDS = 3, 5
POPPINS = capfonts.FONT_DIR / "Poppins-ExtraBold.ttf"
BEBAS = capfonts.FONT_DIR / "BebasNeue.ttf"
DEFAULT_COLORS = {"background": "#0B1020", "background2": "#1B1646", "primary": "#7C3AED", "accent": "#22D3EE", "text": "#FFFFFF"}


def _best_window(words: list, lo: int = MIN_WORDS, hi: int = MAX_WORDS) -> list:
    """The 3-5 word stretch of a longer hook that reads best: meaningful words, not starting or ending on filler, and
    preferring the end of the hook (where the thought lands)."""
    sc = emphasis.word_scores(words)
    best, best_s = words[:hi], -1e9
    for n in range(hi, lo - 1, -1):
        for i in range(0, len(words) - n + 1):
            seg = sc[i:i + n]
            s_ = sum(min(x, 60) for x in seg) / 10.0
            s_ -= 3.0 if seg[0] == 0 else 0.0
            s_ -= 12.0 if seg[-1] == 0 else 0.0
            s_ += 4.0 if i + n == len(words) else 0.0
            s_ += 1.0 * n                                           # a fuller phrase over a clipped one
            if s_ > best_s:
                best, best_s = words[i:i + n], s_
    return best


def thumbnail_hook(hook: str, clip_text: str = "") -> str:
    """The thumbnail's words: the clip's hook as 3-5 whole words (a longer hook is cut to its best stretch, never mid-thought
    or ending on "the"/"and"); if the hook is shorter than three words, a phrase from the clip's own words."""
    words = str(hook or "").strip().strip('"').split()
    if len(words) < MIN_WORDS:
        words = hooks.tidy_hook(hooks.ensure_hook(hook, clip_text), 8).split()
    if len(words) > MAX_WORDS:
        words = _best_window(words)
    return hooks.tidy_hook(" ".join(words), MAX_WORDS)


def _rgb(c: str, default: str = "#FFFFFF") -> tuple:
    c = (c or default).lstrip("#")
    c = c if len(c) == 6 else default.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def colors_of(brand: dict | None) -> dict:
    return {**DEFAULT_COLORS, **((brand or {}).get("colors") or {})}


def _font(text: str, size: int, display: bool = False) -> ImageFont.FreeTypeFont:
    sc = capfonts.script_of(text)
    if sc and capfonts.available(sc):
        return ImageFont.truetype(str(capfonts.FONT_DIR / capfonts.SCRIPTS[sc][2]), size)
    path = BEBAS if display and BEBAS.exists() else POPPINS
    return ImageFont.truetype(str(path), size)


def _cover(img: Image.Image, size: tuple, focus: tuple = (0.5, 0.4), zoom: float = 1.0) -> Image.Image:
    """Scale ``img`` to cover ``size`` (optionally zoomed in) and crop around ``focus`` (x, y as fractions of the image)."""
    w, h = img.size
    k = max(size[0] / w, size[1] / h) * zoom
    big = img.resize((max(size[0], int(w * k)), max(size[1], int(h * k))), Image.LANCZOS)
    bw, bh = big.size
    x0 = int(min(max(0, focus[0] * bw - size[0] / 2), bw - size[0]))
    y0 = int(min(max(0, focus[1] * bh - size[1] / 2), bh - size[1]))
    return big.crop((x0, y0, x0 + size[0], y0 + size[1]))


def _framed(img: Image.Image, size: tuple, face, face_at: float = 0.5, face_h: float = 0.26, max_zoom: float = 1.7) -> Image.Image:
    """The frame as a ``size`` picture with the speaker's face at ``face_at`` of the width (0.5 = centred) and, in a wide shot,
    zoomed in until the face is about ``face_h`` of the height (never more than ``max_zoom``: the source has no more pixels)."""
    if face is None:
        return _cover(img, size)
    w, h = img.size
    base = max(size[0] / w, size[1] / h)
    zoom = min(max_zoom, max(1.0, face_h / max(0.02, face.h)))
    k = base * zoom
    big = img.resize((max(size[0], int(w * k)), max(size[1], int(h * k))), Image.LANCZOS)
    bw, bh = big.size
    x0 = int(min(max(0, face.cx * bw - face_at * size[0]), bw - size[0]))
    y0 = int(min(max(0, face.cy * bh - 0.36 * size[1]), bh - size[1]))
    return big.crop((x0, y0, x0 + size[0], y0 + size[1]))


def _gradient(size: tuple, rgb: tuple, alpha_start: float, alpha_end: float, horizontal: bool = True) -> Image.Image:
    """A flat colour whose opacity runs from ``alpha_start`` to ``alpha_end`` across the width (or the height)."""
    w, h = size
    n = w if horizontal else h
    a = (255 * (alpha_start + (alpha_end - alpha_start) * np.linspace(0.0, 1.0, n))).clip(0, 255).astype(np.uint8)
    arr = np.empty((h, w, 4), np.uint8)
    arr[..., :3] = rgb
    arr[..., 3] = a[None, :] if horizontal else a[:, None]
    return Image.fromarray(arr, "RGBA")


def _fit(text: str, box: tuple, max_size: int, display: bool = False, upper: bool = True, line_gap: float = 1.05) -> tuple:
    """(font, lines) with ``text`` wrapped and sized to fill the box (w, h) as large as it can, up to ``max_size``."""
    words = (text.upper() if upper and capfonts.script_of(text) is None else text).split()
    bw, bh = box
    for size in range(max_size, 24, -4):
        f = _font(text, size, display)
        lines, cur = [], []
        for w in words:
            if cur and f.getlength(" ".join(cur + [w])) > bw:
                lines.append(cur)
                cur = [w]
            else:
                cur.append(w)
        lines.append(cur)
        widest = max(f.getlength(" ".join(l)) for l in lines)
        if widest <= bw and len(lines) * size * line_gap <= bh and (len(lines) <= 3):
            return f, lines
    return _font(text, 24, display), [words]


def _draw_hook(d: ImageDraw.ImageDraw, lines: list, f, x: float, y: float, key_words: set, white, accent, align: str = "left",
               box_w: float = 0, stroke: int = 7, line_gap: float = 1.05):
    size = f.size
    for i, line in enumerate(lines):
        total = f.getlength(" ".join(line))
        cx = x if align == "left" else x + (box_w - total) / 2
        for w in line:
            col = accent if emphasis.norm(w) in key_words else white
            d.text((cx, y + i * size * line_gap), w, font=f, fill=col, stroke_width=stroke, stroke_fill=(0, 0, 0))
            cx += f.getlength(w + " ")


def _key_words(hook: str, keys=None) -> set:
    words = hook.split()
    idx = emphasis.pick(words, keys, 1)
    return {emphasis.norm(words[i]) for i in idx}


def _pill(img: Image.Image, text: str, xy: tuple, fill: tuple, ink: tuple, size: int = 34):
    if not text:
        return
    d = ImageDraw.Draw(img)
    f = _font(text, size, True)
    t = text.upper() if capfonts.script_of(text) is None else text
    w = int(f.getlength(t)) + 36
    d.rounded_rectangle((xy[0], xy[1], xy[0] + w, xy[1] + size + 22), radius=(size + 22) // 2, fill=fill)
    d.text((xy[0] + 18, xy[1] + 8), t, font=f, fill=ink)


def _logo(img: Image.Image, brand: dict | None, xy: tuple, height: int = 64, anchor: str = "lt"):
    p = (brand or {}).get("logo")
    if not p or not Path(p).exists():
        return False
    try:
        lg = Image.open(p).convert("RGBA")
    except OSError:
        return False
    k = height / lg.height
    lg = lg.resize((max(1, int(lg.width * k)), height), Image.LANCZOS)
    x, y = xy
    if anchor[0] == "r":
        x -= lg.width
    img.alpha_composite(lg, (int(x), int(y)))
    return True


def _face_focus(face, default=(0.5, 0.4)) -> tuple:
    return (face.cx, min(0.7, max(0.3, face.cy + 0.05))) if face is not None else default


def design(out, frame, hook: str, brand: dict | None = None, variant: str = "left", face=None, category: str = "",
           key_words=None, size: tuple = SIZE) -> Path:
    """Draw one thumbnail. ``frame``: path of the video frame; ``face``: its ``reframe.Face`` (None = unknown)."""
    c = colors_of(brand)
    bg, prim, acc, txt = _rgb(c["background"]), _rgb(c["primary"]), _rgb(c["accent"]), _rgb(c["text"])
    W, H = size
    img0 = Image.open(frame).convert("RGB")
    focus = _face_focus(face)
    keys = _key_words(hook, key_words)
    if variant == "split":
        pw = int(W * 0.40)
        photo = _framed(img0, (W - pw + 80, H), face, face_at=0.46, face_h=0.34, max_zoom=2.0).convert("RGBA")
        img = Image.new("RGBA", size, bg + (255,))
        img.alpha_composite(photo, (0, 0))
        panel = _gradient((pw, H), prim, 1.0, 1.0)
        dark = _gradient((pw, H), bg, 0.0, 0.55, horizontal=False)
        img.alpha_composite(panel, (W - pw, 0))
        img.alpha_composite(dark, (W - pw, 0))
        edge = _gradient((90, H), prim, 0.0, 1.0)
        img.alpha_composite(edge, (W - pw - 90, 0))
        d = ImageDraw.Draw(img)
        f, lines = _fit(hook, (pw - 90, int(H * 0.62)), 118)
        total_h = len(lines) * f.size * 1.05
        _draw_hook(d, lines, f, W - pw + 48, (H - total_h) / 2 + 18, keys, txt, acc, stroke=5)
        d.rectangle((W - pw + 48, int((H - total_h) / 2) - 18, W - pw + 48 + 150, int((H - total_h) / 2) - 8), fill=acc)
        _pill(img, category, (W - pw + 48, 40), acc, bg)
        _logo(img, brand, (W - 36, H - 96), 60, "rt")
    elif variant == "band":
        img = _framed(img0, size, face, face_at=0.5, face_h=0.30, max_zoom=1.6).convert("RGBA")
        shade = _gradient((W, int(H * 0.5)), (0, 0, 0), 0.0, 0.55, horizontal=False)
        img.alpha_composite(shade, (0, H - int(H * 0.5)))
        band_h = int(H * 0.36)
        band = Image.new("RGBA", (W, band_h), bg + (236,))
        img.alpha_composite(band, (0, H - band_h))
        d = ImageDraw.Draw(img)
        d.rectangle((0, H - band_h, W, H - band_h + 10), fill=acc)
        f, lines = _fit(hook, (W - 160, band_h - 70), 170, display=True)
        total_h = len(lines) * f.size * 1.05
        _draw_hook(d, lines, f, 80, H - band_h + (band_h - total_h) / 2 + 2, keys, txt, acc, align="center", box_w=W - 160, stroke=4)
        _pill(img, category, (40, 36), acc, bg)
        _logo(img, brand, (W - 40, 32), 64, "rt")
    else:                                                                    # "left": the hook on the side away from the face
        img = _framed(img0, size, face, face_at=0.74, face_h=0.30, max_zoom=1.6).convert("RGBA")   # face on the right, text on the left
        face_right = True
        fade = _gradient((int(W * 0.68), H), bg, 0.94, 0.0)
        img.alpha_composite(fade, (0, 0))
        d = ImageDraw.Draw(img)
        bw = int(W * 0.50)
        f, lines = _fit(hook, (bw, int(H * 0.66)), 132)
        total_h = len(lines) * f.size * 1.05
        x = 64
        _draw_hook(d, lines, f, x, (H - total_h) / 2 + 10, keys, txt, acc, stroke=7)
        d.rectangle((x, int((H - total_h) / 2) - 22, x + 170, int((H - total_h) / 2) - 10), fill=acc)
        _pill(img, category, (x, 40), acc, bg)
        _logo(img, brand, (x if face_right else W - 64, H - 100), 64, "lt" if face_right else "rt")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=92, optimize=True)
    return out
