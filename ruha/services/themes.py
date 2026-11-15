"""Sermon themes: palette + background motif used for posters, posts and reel hook/closing cards.
Everything is drawn locally with Pillow (no stock images, no internet)."""
from __future__ import annotations

import math
import random
import zlib

from PIL import Image, ImageDraw, ImageFilter

THEMES = {
    "warfare":  {"top": "#100408", "bottom": "#45101a", "glow": "#ff3b30", "accent": "#ffb74d", "motif": "embers",
                 "words": ["war", "battle", "spirit", "enemy", "satan", "demon", "fight", "darkness", "armor", "deliver"]},
    "spirit":   {"top": "#170a2c", "bottom": "#4b1d6e", "glow": "#ff9f43", "accent": "#ffd166", "motif": "flames",
                 "words": ["holy spirit", "fire", "anoint", "power", "wind", "breath", "pentecost", "ruach"]},
    "grace":    {"top": "#2a1904", "bottom": "#80581a", "glow": "#ffd166", "accent": "#fff1c1", "motif": "rays",
                 "words": ["grace", "mercy", "forgive", "gift", "salvation", "cross", "redeem", "free"]},
    "peace":    {"top": "#051c2a", "bottom": "#0f5f73", "glow": "#7fdcff", "accent": "#c9f4ff", "motif": "waves",
                 "words": ["peace", "rest", "calm", "heal", "comfort", "still", "anxious", "storm", "water"]},
    "hope":     {"top": "#1b1450", "bottom": "#d9703a", "glow": "#ffd89b", "accent": "#fff0cc", "motif": "dawn",
                 "words": ["hope", "new", "begin", "promise", "future", "restore", "morning", "joy"]},
    "faith":    {"top": "#040a1d", "bottom": "#17325f", "glow": "#9cc2ff", "accent": "#ffe9a8", "motif": "stars",
                 "words": ["faith", "trust", "believe", "pray", "prayer", "wait", "obey", "abraham"]},
    "love":     {"top": "#2a0a1c", "bottom": "#8d1f4c", "glow": "#ff7aa8", "accent": "#ffd1e0", "motif": "bokeh",
                 "words": ["love", "compassion", "kind", "neighbor", "family", "heart", "care"]},
    "strength": {"top": "#0a1114", "bottom": "#2f4852", "glow": "#9fd3e6", "accent": "#e8f4f8", "motif": "mountains",
                 "words": ["strength", "endure", "persever", "courage", "stand", "overcome", "victory", "brave"]},
    "growth":   {"top": "#06200f", "bottom": "#1f6e3d", "glow": "#9be37a", "accent": "#e8ffd6", "motif": "bokeh",
                 "words": ["grow", "disciple", "learn", "wisdom", "fruit", "seed", "mature", "renew"]},
}
DEFAULT = "faith"


def resolve(name, text: str = "") -> str:
    """A valid theme name: the model's choice when it is one of ours, else the best keyword match."""
    n = str(name or "").strip().lower()
    if n in THEMES:
        return n
    low = (text or "").lower()
    best, score = DEFAULT, 0
    for k, t in THEMES.items():
        sc = sum(low.count(w) for w in t["words"])
        if sc > score:
            best, score = k, sc
    return best


def _hex(c: str, a: int | None = None):
    c = c.lstrip("#")
    rgb = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return rgb + ((a,) if a is not None else ())


def _vgrad(size, top, bottom):
    w, h = size
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return col.resize((w, h))


def _blur(layer, r):
    return layer.filter(ImageFilter.GaussianBlur(r))


def canvas(size, theme: str, seed_text: str = "") -> Image.Image:
    """A themed background image of ``size`` (RGB)."""
    t = THEMES.get(theme) or THEMES[DEFAULT]
    w, h = size
    rnd = random.Random(zlib.crc32((seed_text or theme).encode("utf-8")))
    img = _vgrad(size, _hex(t["top"]), _hex(t["bottom"])).convert("RGBA")
    glow, accent = _hex(t["glow"]), _hex(t["accent"])
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    m = t["motif"]
    u = min(w, h)

    if m == "embers":                                   # dark, red glow from below, rising sparks
        d.ellipse((-w * 0.3, h * 0.72, w * 1.3, h * 1.5), fill=glow + (150,))
        img = Image.alpha_composite(img, _blur(layer, u * 0.09))
        layer = Image.new("RGBA", size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        for _ in range(70):
            x, y = rnd.random() * w, h * (0.25 + 0.75 * rnd.random() ** 0.7)
            r = rnd.uniform(2, u * 0.012)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(accent if rnd.random() < .6 else glow) + (rnd.randint(120, 230),))
        img = Image.alpha_composite(img, _blur(layer, 1.2))
    elif m == "flames":
        for i in range(9):
            cx = w * (0.1 + 0.1 * i) + rnd.uniform(-30, 30)
            fh = h * rnd.uniform(0.18, 0.42)
            d.ellipse((cx - u * 0.12, h - fh, cx + u * 0.12, h + fh * 0.4), fill=(glow if i % 2 else accent) + (120,))
        img = Image.alpha_composite(img, _blur(layer, u * 0.05))
        layer = Image.new("RGBA", size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        for _ in range(45):
            x, y = rnd.random() * w, h * (0.3 + 0.7 * rnd.random())
            r = rnd.uniform(2, u * 0.01)
            d.ellipse((x - r, y - r, x + r, y + r), fill=accent + (rnd.randint(140, 240),))
        img = Image.alpha_composite(img, _blur(layer, 1.0))
    elif m == "rays":                                   # beams of light from the top
        cx, cy = w * 0.5, -h * 0.05
        for i in range(-9, 10):
            ang = math.radians(90 + i * 7 + rnd.uniform(-2, 2))
            spread = math.radians(2.6)
            r = h * 1.4
            d.polygon([(cx, cy), (cx + r * math.cos(ang - spread), cy + r * math.sin(ang - spread)),
                       (cx + r * math.cos(ang + spread), cy + r * math.sin(ang + spread))], fill=glow + (rnd.randint(28, 62),))
        img = Image.alpha_composite(img, _blur(layer, u * 0.012))
        halo = Image.new("RGBA", size, (0, 0, 0, 0)); ImageDraw.Draw(halo).ellipse((w * .2, -h * .22, w * .8, h * .22), fill=glow + (150,))
        img = Image.alpha_composite(img, _blur(halo, u * 0.08))
    elif m == "waves":
        for k in range(6):
            base = h * (0.55 + 0.08 * k)
            amp, ph = u * rnd.uniform(0.015, 0.035), rnd.uniform(0, 6.28)
            pts = [(x, base + amp * math.sin(x / w * 6.28 * (1.3 + k * .2) + ph)) for x in range(0, w + 8, 8)]
            d.polygon(pts + [(w, h), (0, h)], fill=(glow if k % 2 else accent) + (34 + k * 6,))
        img = Image.alpha_composite(img, _blur(layer, 1.5))
        halo = Image.new("RGBA", size, (0, 0, 0, 0)); ImageDraw.Draw(halo).ellipse((w * .55, h * .05, w * 1.1, h * .35), fill=accent + (90,))
        img = Image.alpha_composite(img, _blur(halo, u * 0.07))
    elif m == "dawn":                                   # sun on the horizon
        d.ellipse((w * 0.1, h * 0.66, w * 0.9, h * 1.14), fill=accent + (210,))
        img = Image.alpha_composite(img, _blur(layer, u * 0.07))
        layer = Image.new("RGBA", size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        d.ellipse((w * 0.32, h * 0.74, w * 0.68, h * 1.02), fill=(255, 250, 225, 255))
        img = Image.alpha_composite(img, _blur(layer, u * 0.012))
        rays = Image.new("RGBA", size, (0, 0, 0, 0)); rd = ImageDraw.Draw(rays)
        for i in range(-6, 7):
            ang = math.radians(-90 + i * 9); r = h
            rd.polygon([(w / 2, h * 0.88), (w / 2 + r * math.cos(ang - .05), h * .88 + r * math.sin(ang - .05)),
                        (w / 2 + r * math.cos(ang + .05), h * .88 + r * math.sin(ang + .05))], fill=accent + (26,))
        img = Image.alpha_composite(img, _blur(rays, 3))
    elif m == "stars":
        for _ in range(130):
            x, y = rnd.random() * w, rnd.random() * h * 0.85
            r = rnd.uniform(1, u * 0.004 + 1)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rnd.randint(90, 240)))
        for _ in range(9):                              # a few four-point sparkles
            x, y, r = rnd.random() * w, rnd.random() * h * .7, rnd.uniform(14, u * 0.03)
            d.polygon([(x, y - r), (x + r * .18, y - r * .18), (x + r, y), (x + r * .18, y + r * .18),
                       (x, y + r), (x - r * .18, y + r * .18), (x - r, y), (x - r * .18, y - r * .18)], fill=accent + (200,))
        img = Image.alpha_composite(img, _blur(layer, 0.8))
        halo = Image.new("RGBA", size, (0, 0, 0, 0)); ImageDraw.Draw(halo).ellipse((w * .1, h * .62, w * .9, h * 1.3), fill=glow + (80,))
        img = Image.alpha_composite(img, _blur(halo, u * 0.1))
    elif m == "bokeh":
        for _ in range(26):
            x, y, r = rnd.random() * w, rnd.random() * h, rnd.uniform(u * 0.03, u * 0.11)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(glow if rnd.random() < .6 else accent) + (rnd.randint(25, 70),))
        img = Image.alpha_composite(img, _blur(layer, u * 0.012))
    elif m == "mountains":
        shades = [(0.58, 0.07), (0.68, 0.05), (0.78, 0.04)]
        for i, (base, amp) in enumerate(shades):
            pts, x = [(0, h)], 0
            while x <= w + 60:
                pts.append((x, h * base - rnd.uniform(0, h * amp) - (math.sin(x / w * 3 + i) + 1) * h * 0.03))
                x += rnd.randint(40, 110)
            pts.append((w, h))
            d.polygon(pts, fill=_hex(t["top"]) + (150 + i * 40,))
        halo = Image.new("RGBA", size, (0, 0, 0, 0)); ImageDraw.Draw(halo).ellipse((w * .15, h * .3, w * .85, h * .75), fill=glow + (70,))
        img = Image.alpha_composite(img, _blur(halo, u * 0.1))
        img = Image.alpha_composite(img, layer)
    # darken the middle band slightly so white text always reads well
    shade = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle((0, h * 0.2, w, h * 0.8), fill=(0, 0, 0, 70))
    img = Image.alpha_composite(img, _blur(shade, u * 0.08))
    return img.convert("RGB")
