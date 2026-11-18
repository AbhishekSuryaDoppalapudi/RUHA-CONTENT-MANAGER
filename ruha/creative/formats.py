"""Output formats. Each has its own safe area and its own text-zone rules; a design is composed for the format, not
resized from another one. ``safe`` = (left, top, right, bottom) margins in pixels that no text may enter."""
from __future__ import annotations

import math

FORMATS = {
    "reel_cover": {"label": "Instagram Reel cover", "size": (1080, 1920), "safe": (80, 270, 80, 430), "max_words": 7,
                   "note": "Top and bottom bands stay clear of the Reels interface; the 4:5 centre crop shown in the profile grid is respected."},
    "feed_post": {"label": "Instagram feed post (4:5)", "size": (1080, 1350), "safe": (80, 110, 80, 130), "max_words": 9},
    "square_post": {"label": "Instagram square post", "size": (1080, 1080), "safe": (80, 90, 80, 90), "max_words": 8},
    "thumbnail": {"label": "YouTube thumbnail", "size": (1280, 720), "safe": (70, 50, 70, 50), "max_words": 5,
                  "note": "Big, short and left-weighted so it reads at 160 px wide; the bottom-right timestamp corner stays empty."},
    "carousel": {"label": "Instagram carousel slides", "size": (1080, 1350), "safe": (84, 120, 84, 140), "max_words": 14,
                 "slides": 5},
    "poster": {"label": "Sermon highlight poster", "size": (1080, 1350), "safe": (84, 120, 84, 140), "max_words": 10},
    "hook": {"label": "Instagram hook image", "size": (1080, 1350), "safe": (80, 110, 80, 130), "max_words": 7,
             "variants": {"hook_story": (1080, 1920)}},
    "hook_story": {"label": "Instagram hook image (story/reel, 9:16)", "size": (1080, 1920), "safe": (80, 270, 80, 430),
                   "max_words": 7},
}


def aspect_class(size) -> str:
    r = size[1] / size[0]
    if r >= 1.6:
        return "tall"
    if r >= 1.2:
        return "portrait"
    if r >= 0.9:
        return "square"
    return "wide"


def gen_size(target, native) -> tuple:
    """Generation size with the target's aspect ratio and about the model's native pixel count (multiples of 8)."""
    area = native[0] * native[1]
    ratio = target[0] / target[1]
    h = math.sqrt(area / ratio)
    w = h * ratio
    return max(256, int(round(w / 8)) * 8), max(256, int(round(h / 8)) * 8)


def zone_box(fmt_size, safe, zone: str) -> dict:
    """Text box for a zone: {x, y, w, h, align, valign} inside the safe area."""
    W, H = fmt_size
    l, t, r, b = safe
    inner_w, inner_h = W - l - r, H - t - b
    z = {
        "lower-left": dict(x=l, y=t + inner_h * 0.48, w=inner_w, h=inner_h * 0.52, align="left", valign="bottom"),
        "lower-center": dict(x=l, y=t + inner_h * 0.48, w=inner_w, h=inner_h * 0.52, align="center", valign="bottom"),
        "center": dict(x=l, y=t + inner_h * 0.22, w=inner_w, h=inner_h * 0.56, align="center", valign="middle"),
        "upper-left": dict(x=l, y=t, w=inner_w, h=inner_h * 0.5, align="left", valign="top"),
        "upper-center": dict(x=l, y=t, w=inner_w, h=inner_h * 0.5, align="center", valign="top"),
        "left-half": dict(x=l, y=t, w=inner_w * 0.56, h=inner_h, align="left", valign="middle"),
    }[zone]
    return {k: (round(v) if isinstance(v, float) else v) for k, v in z.items()}


def copy_space_phrase(zone: str) -> str:
    return {
        "lower-left": "subject in the upper two thirds of the frame, calm uncluttered darker foreground in the lower third",
        "lower-center": "subject in the upper two thirds of the frame, calm uncluttered darker foreground in the lower third",
        "center": "balanced symmetrical composition, subject small and centred, generous empty space around it",
        "upper-left": "subject low in the frame, open sky or empty space across the upper half",
        "upper-center": "subject low in the frame, open sky or empty space across the upper half",
        "left-half": "subject on the right side of the frame, calm empty space on the left half",
    }[zone]
