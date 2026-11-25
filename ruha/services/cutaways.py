"""Animated cutaways inside a teaching reel.

The speaker's video and audio run without a break. At a few sentence boundaries the picture crossfades to an animated
scene (slow zoom over a themed or AI image, with the key word of that moment) for a few seconds, then crossfades back
to the speaker. Captions stay on top the whole time. This module only decides *where* and *what*; media.render_reel
does the compositing."""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_SECONDS = 3.6        # length of one cutaway
LEAD = 4.5                   # never cut away during the opening seconds (the speaker must be seen first)
TAIL = 4.5                   # ...or the last seconds
PER = 16.0                   # about one cutaway per this many seconds of teaching
MAX_CUTAWAYS = 3
MIN_REEL = 20.0              # shorter reels get none


def pick(segments, start: float, end: float, seconds: float = DEFAULT_SECONDS, max_n: int = MAX_CUTAWAYS) -> list:
    """Windows [{at, dur, text}] with ``at`` relative to the reel start, snapped to sentence starts."""
    length = float(end) - float(start)
    if length < MIN_REEL or max_n < 1:
        return []
    lo, hi = LEAD, length - TAIL - seconds
    if hi <= lo:
        return []
    n = max(1, min(int(max_n), int((hi - lo) // PER) + 1))
    starts = sorted(float(s["start"]) - start for s in segments
                    if lo - 3 <= float(s["start"]) - start <= hi + 3)
    out, last_end = [], -1e9
    for k in range(n):
        target = lo + (hi - lo) * (k + 1) / (n + 1) if n > 1 else (lo + hi) / 2
        near = [t for t in starts if abs(t - target) <= 3.0]
        at = min(near, key=lambda t: abs(t - target)) if near else target
        at = min(max(at, lo), hi)
        if at < last_end + 3.0:                                   # keep a gap between two cutaways
            continue
        text = " ".join(str(s["text"]).strip() for s in segments
                        if float(s["end"]) > start + at and float(s["start"]) < start + at + seconds + 3)
        out.append({"at": round(at, 2), "dur": seconds, "text": " ".join(text.split())})
        last_end = at + seconds
    return out


def keyword(text: str) -> str:
    """The one word that names the moment (reuses the caption engine's keyword rule)."""
    from ruha.services.subtitles import _key_index
    words = re.findall(r"[^\s]+", text or "")[:10]               # the sentence that opens at the cutaway
    clean = [re.sub(r"[^\w'’-]", "", w) for w in words]
    clean = [w for w in clean if w]
    if not clean:
        return ""
    return clean[_key_index(clean)].strip("'’-")


def scene_image(out, theme: str, text: str, size: tuple, accent: str, background=None) -> Path:
    """Draw one cutaway still: themed (or AI) scene with the key word. Pictures are animated later by ffmpeg."""
    from PIL import Image, ImageDraw, ImageFilter
    from ruha.services import themes
    from ruha.services.design import _hex, load_font
    w, h = size
    if background and Path(background).exists():
        img = Image.open(background).convert("RGB")
        sc = max(w / img.width, h / img.height)
        img = img.resize((int(img.width * sc) + 1, int(img.height * sc) + 1))
        img = img.crop(((img.width - w) // 2, (img.height - h) // 2, (img.width - w) // 2 + w, (img.height - h) // 2 + h))
    else:
        img = themes.canvas((w, h), theme, text or theme)
    img = img.convert("RGBA")
    word = keyword(text)
    if word:
        word = word[:1].upper() + word[1:]
        font = load_font(int(w * 0.17), True, word)
        probe = ImageDraw.Draw(img)
        tw = probe.textlength(word, font=font)
        while tw > w * 0.86 and font.size > 40:                   # long words shrink to fit
            font = load_font(int(font.size * 0.88), True, word)
            tw = probe.textlength(word, font=font)
        xy = ((w - tw) / 2, h * 0.30)
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).text(xy, word, font=font, fill=_hex(accent, 255))
        img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(w * 0.02)))
        ImageDraw.Draw(img).text(xy, word, font=font, fill=_hex(accent, 255), stroke_width=max(2, w // 270),
                                 stroke_fill=(0, 0, 0, 200))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG")
    return out
