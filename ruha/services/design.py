"""Brand-aware poster and social-post rendering with Pillow (fully local)."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DEFAULT_BRAND = {
    "name": "El Ruach",
    "tagline": "Create. Manage. Inspire.",
    "colors": {"background": "#0B1020", "background2": "#1B1646", "primary": "#7C3AED",
               "accent": "#22D3EE", "accent2": "#2DD4BF", "text": "#FFFFFF", "muted": "#B6BDD6"},
    "fonts": {"heading": None, "body": None},
    "logo": None,
}

LAYOUTS = ("classic", "band", "centered")
SIZES = {"poster": (1080, 1350), "social": (1080, 1350), "square": (1080, 1080)}

_WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
_LATIN = {"regular": ["segoeui.ttf", "arial.ttf", "calibri.ttf"],
          "bold": ["segoeuib.ttf", "arialbd.ttf", "calibrib.ttf"]}
_INDIC = {"regular": ["Nirmala.ttc", "NirmalaUI.ttf"], "bold": ["NirmalaB.ttc", "NirmalaUIB.ttf"]}
_LINUX = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def _hex(c: str, alpha: int | None = None):
    c = (c or "#000000").lstrip("#")
    rgb = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return rgb + ((alpha,) if alpha is not None else ())


def _is_latin(text: str) -> bool:
    return all(ord(ch) < 0x0250 or ord(ch) in range(0x2000, 0x2100) for ch in text)


def load_font(size: int, bold: bool, text: str = "", custom: str | None = None):
    if custom and Path(custom).exists():
        try:
            return ImageFont.truetype(custom, size)
        except OSError:
            pass
    group = _LATIN if _is_latin(text) else _INDIC
    for name in group["bold" if bold else "regular"]:
        p = _WIN_FONTS / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    for p in _LINUX:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list:
    lines = []
    for para in (text or "").split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for w in words[1:]:
            if draw.textlength(f"{cur} {w}", font=font) <= max_w:
                cur = f"{cur} {w}"
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _fit(draw, text, bold, max_w, max_h, start, minimum, custom, line_gap=1.18):
    size = start
    while size >= minimum:
        font = load_font(size, bold, text, custom)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) * size * line_gap <= max_h:
            return font, lines, size
        size -= 4
    font = load_font(minimum, bold, text, custom)
    lines = _wrap(draw, text, font, max_w)
    max_lines = max(1, int(max_h // (minimum * line_gap)))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;") + "…"
    return font, lines, minimum


def _gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", size, top)
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        px.line((0, y, w, y), fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img


def render_graphic(out, headline: str, body: str = "", reference: str = "", label: str = "",
                   kind: str = "social", brand: dict | None = None, layout: str = "classic",
                   size: tuple | None = None) -> Path:
    brand = {**DEFAULT_BRAND, **(brand or {})}
    colors = {**DEFAULT_BRAND["colors"], **(brand.get("colors") or {})}
    fonts = {**DEFAULT_BRAND["fonts"], **(brand.get("fonts") or {})}
    w, h = size or SIZES.get(kind, SIZES["social"])
    layout = layout if layout in LAYOUTS else "classic"
    img = _gradient((w, h), _hex(colors["background"]), _hex(colors["background2"]))
    # soft accent glow, drawn on a transparent overlay then composited
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((w * 0.45, -h * 0.15, w * 1.25, h * 0.45), fill=_hex(colors["primary"], 70))
    gd.ellipse((-w * 0.3, h * 0.7, w * 0.5, h * 1.2), fill=_hex(colors["accent"], 45))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    d = ImageDraw.Draw(img)
    pad = 96
    text_color, muted = _hex(colors["text"]), _hex(colors["muted"])
    align_center = layout == "centered"

    if layout == "classic":
        d.rounded_rectangle((52, 52, w - 52, h - 52), radius=40, outline=_hex(colors["accent"]), width=3)
    elif layout == "band":
        d.rectangle((0, 0, 28, h), fill=_hex(colors["primary"]))
        d.rectangle((28, 0, 40, h), fill=_hex(colors["accent"]))
        pad = 120

    label_font = load_font(30, True, label, fonts.get("heading"))
    y = pad + 20
    if label:
        lw = d.textlength(label.upper(), font=label_font)
        x = (w - lw) / 2 if align_center else pad
        d.text((x, y), label.upper(), font=label_font, fill=_hex(colors["accent"]))
        d.line((x, y + 52, x + 150, y + 52), fill=_hex(colors["primary"]), width=6)
    y += 110

    max_w = w - pad * 2
    head_h = int(h * (0.32 if body else 0.55))
    hfont, hlines, hsize = _fit(d, headline or "", True, max_w, head_h, 92 if kind == "poster" else 80, 40,
                                fonts.get("heading"))
    for line in hlines:
        lw = d.textlength(line, font=hfont)
        d.text(((w - lw) / 2 if align_center else pad, y), line, font=hfont, fill=text_color)
        y += int(hsize * 1.18)
    y += 36

    if body:
        body_h = int(h - y - 260)
        bfont, blines, bsize = _fit(d, body, False, max_w, max(body_h, 160), 40, 26, fonts.get("body"), 1.35)
        for line in blines:
            lw = d.textlength(line, font=bfont)
            d.text(((w - lw) / 2 if align_center else pad, y), line, font=bfont, fill=muted)
            y += int(bsize * 1.35)
        y += 20
    if reference:
        rfont = load_font(32, True, reference, fonts.get("heading"))
        lw = d.textlength(reference, font=rfont)
        d.text(((w - lw) / 2 if align_center else pad, y), reference, font=rfont, fill=_hex(colors["accent2"]))

    # footer: logo or brand name + tagline
    fy = h - pad - 40
    logo = brand.get("logo")
    if logo and Path(logo).exists():
        try:
            lg = Image.open(logo).convert("RGBA")
            lg.thumbnail((220, 90))
            img.paste(lg, (int((w - lg.width) / 2) if align_center else pad, fy - 20), lg)
        except Exception:
            logo = None
    if not (logo and Path(logo).exists()):
        nf = load_font(34, True, brand.get("name", ""), fonts.get("heading"))
        name = (brand.get("name") or "").upper()
        d.text(((w - d.textlength(name, font=nf)) / 2 if align_center else pad, fy - 10), name, font=nf,
               fill=text_color)
    tf = load_font(22, False, brand.get("tagline", ""), fonts.get("body"))
    tag = brand.get("tagline") or ""
    d.text(((w - d.textlength(tag, font=tf)) / 2 if align_center else pad, fy + 38), tag, font=tf, fill=muted)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out
