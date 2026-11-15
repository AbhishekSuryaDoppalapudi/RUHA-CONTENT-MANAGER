"""Brand-aware poster and social-post rendering with Pillow (fully local)."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import logging

from ruha.services import htmlrender, themes

log = logging.getLogger("ruha.design")

DEFAULT_MARK = Path(__file__).resolve().parents[1] / "assets" / "logo-mark.png"

DEFAULT_BRAND = {
    "name": "El Ruach",
    "tagline": "The God's Breath",
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


_STOP = {"the", "and", "for", "that", "this", "with", "you", "your", "are", "not", "but", "his", "her", "its",
         "from", "have", "has", "was", "were", "will", "into", "over", "they", "them", "our", "all", "can",
         "who", "what", "when", "then", "than", "join", "stop", "keep", "never", "every", "against"}


def _key_words(text: str, n: int = 2) -> set:
    """The strongest 1-2 words of a hook (longest non-trivial words) get the accent colour."""
    words = [w.strip(".,!?;:\"'()").lower() for w in (text or "").split()]
    cand = sorted({w for w in words if len(w) > 4 and w not in _STOP}, key=len, reverse=True)
    return set(cand[:n])


def _soft_glow(img, box, colour, alpha, blur):
    from PIL import ImageFilter
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(box, fill=colour + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(img, layer)


def _cover(path, size) -> Image.Image:
    """A picture scaled and centre-cropped to exactly ``size`` (CSS 'cover')."""
    im = Image.open(path).convert("RGB")
    w, h = size
    k = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * k)), max(h, round(im.height * k))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def _use_html(brand) -> bool:
    return (brand or {}).get("engine", "html") == "html" and htmlrender.available()


def _render_themed(out, headline, body, reference, label, brand, size, theme):
    """Poster / post on a sermon-themed background: centred, bold, keyword highlight, glow, verse panel."""
    from PIL import ImageFilter
    brand = {**DEFAULT_BRAND, **(brand or {})}
    fonts = {**DEFAULT_BRAND["fonts"], **(brand.get("fonts") or {})}
    w, h = size
    tdef = themes.THEMES.get(theme) or themes.THEMES[themes.DEFAULT]
    accent, glow = _hex(tdef["accent"]), _hex(tdef["glow"])
    bgfile = (brand or {}).get("background")
    img = (_cover(bgfile, (w, h)) if bgfile and Path(bgfile).exists()
           else themes.canvas((w, h), theme, f"{headline}|{label}")).convert("RGBA")

    # vignette so the text always has contrast, plus a soft glow centred behind the message
    shade = Image.new("L", (w, h), 0)
    ImageDraw.Draw(shade).ellipse((-w * 0.25, h * 0.08, w * 1.25, h * 0.92), fill=255)
    shade = shade.filter(ImageFilter.GaussianBlur(160))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 150))
    img = Image.composite(img, Image.alpha_composite(img, dark), shade)
    img = _soft_glow(img, (w * 0.15, h * 0.3, w * 0.85, h * 0.7), glow, 60, 120)
    d = ImageDraw.Draw(img)

    def overlay_rr(box, radius, fill, outline=None, width=1):
        nonlocal img, d
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        img = Image.alpha_composite(img, layer)
        d = ImageDraw.Draw(img)

    # frame with corner ticks
    d.rounded_rectangle((44, 44, w - 44, h - 44), radius=36, outline=accent + (200,), width=3)
    d.rounded_rectangle((62, 62, w - 62, h - 62), radius=26, outline=accent + (70,), width=1)

    # label pill, centred at the top
    y = 120
    if label:
        lf = load_font(30, True, label, fonts.get("heading"))
        txt = "  ".join(label.upper())
        tw = d.textlength(txt, font=lf)
        overlay_rr(((w - tw) / 2 - 40, y - 14, (w + tw) / 2 + 40, y + 52), 34,
                   accent + (40,), accent + (230,), 2)
        d.text(((w - tw) / 2, y), txt, font=lf, fill=accent + (255,))
    y_top = y + 120

    # headline: uppercase Latin, centred, keywords in the accent colour, glow behind
    max_w = w - 200
    verse_block = bool(body)
    head_h = int(h * (0.34 if verse_block else 0.46))
    text = headline or ""
    shown = text.upper() if _is_latin(text) else text
    hfont, hlines, hsize = _fit(d, shown, True, max_w, head_h, 112, 54, fonts.get("heading"), 1.16)
    lh = int(hsize * 1.16)
    block_h = len(hlines) * lh
    ref_h = 150 if reference and not verse_block else 0
    body_h_reserved = 0
    if verse_block:
        bfont, blines, bsize = _fit(d, body, False, w - 260, int(h * 0.2), 40, 28, fonts.get("body"), 1.35)
        body_h_reserved = len(blines) * int(bsize * 1.35) + 230
    avail_top, avail_bot = y_top, h - 330
    y = max(avail_top, int(avail_top + (avail_bot - avail_top - block_h - ref_h - body_h_reserved) / 2))
    keys = _key_words(text)

    glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    runs = []
    yy = y
    for line in hlines:
        words = line.split(" ")
        spacew = d.textlength(" ", font=hfont)
        widths = [d.textlength(x, font=hfont) for x in words]
        total = sum(widths) + spacew * (len(words) - 1)
        x = (w - total) / 2
        for word, ww in zip(words, widths):
            hot = word.strip(".,!?;:\"'()").lower() in keys
            runs.append((x, yy, word, hot))
            if hot:
                gd.text((x, yy), word, font=hfont, fill=glow + (255,))
            x += ww + spacew
        yy += lh
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(22))
    img = Image.alpha_composite(img, glow_layer)
    d = ImageDraw.Draw(img)
    for x, ty, word, hot in runs:
        d.text((x + 4, ty + 6), word, font=hfont, fill=(0, 0, 0, 160))          # drop shadow
        d.text((x, ty), word, font=hfont, fill=(accent if hot else (255, 255, 255)) + (255,))
    y = yy + 24

    def ornament(cy):
        d.line((w / 2 - 190, cy, w / 2 - 28, cy), fill=accent + (220,), width=3)
        d.line((w / 2 + 28, cy, w / 2 + 190, cy), fill=accent + (220,), width=3)
        d.polygon([(w / 2, cy - 12), (w / 2 + 12, cy), (w / 2, cy + 12), (w / 2 - 12, cy)], fill=accent + (255,))

    if verse_block:
        ornament(y + 14)
        y += 56
        panel_h = len(blines) * int(bsize * 1.35) + (170 if reference else 90)
        overlay_rr((100, y, w - 100, y + panel_h), 28, (0, 0, 0, 120), accent + (150,), 2)
        ty = y + 40
        blines = list(blines)
        blines[0] = "“" + blines[0]
        blines[-1] = blines[-1] + "”"
        for line in blines:
            lw = d.textlength(line, font=bfont)
            d.text(((w - lw) / 2, ty), line, font=bfont, fill=(245, 242, 250, 255))
            ty += int(bsize * 1.35)
        if reference:
            rf = load_font(36, True, reference, fonts.get("heading"))
            rw = d.textlength(reference, font=rf)
            d.text(((w - rw) / 2, y + panel_h - 78), reference, font=rf, fill=accent + (255,))
    elif reference:
        ornament(y + 20)
        rf = load_font(44, True, reference, fonts.get("heading"))
        rw = d.textlength(reference, font=rf)
        d.text(((w - rw) / 2, y + 62), reference, font=rf, fill=accent + (255,))

    # footer, centred
    fy = h - 190
    name = (brand.get("name") or "").upper()
    nf = load_font(34, True, name, fonts.get("heading"))
    tw = d.textlength(name, font=nf)
    mark_w = 0
    x0 = (w - tw) / 2
    if DEFAULT_MARK.exists():
        mk = Image.open(DEFAULT_MARK).convert("RGBA").resize((84, 84), Image.LANCZOS)
        mark_w = 84 + 18
        x0 = (w - (mark_w + tw)) / 2
        img.paste(mk, (int(x0), fy - 14), mk)
    d = ImageDraw.Draw(img)
    d.text((x0 + mark_w, fy), name, font=nf, fill=(255, 255, 255, 255))
    tag = brand.get("tagline") or ""
    tf = load_font(22, False, tag, fonts.get("body"))
    d.text((x0 + mark_w, fy + 44), tag, font=tf, fill=(235, 232, 245, 220))

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out


def render_graphic(out, headline: str, body: str = "", reference: str = "", label: str = "",
                   kind: str = "social", brand: dict | None = None, layout: str = "classic",
                   size: tuple | None = None, theme: str | None = None) -> Path:
    if theme:
        sz = size or SIZES.get(kind, SIZES["social"])
        if _use_html(brand):
            try:
                return htmlrender.render_graphic(out, headline, body, reference, label,
                                                 {**DEFAULT_BRAND, **(brand or {})}, sz, theme, (brand or {}).get("background"))
            except Exception as e:                       # never lose a poster because the browser misbehaved
                log.warning("HTML renderer failed (%s); using the built-in renderer", e)
        return _render_themed(out, headline, body, reference, label, brand, sz, theme)
    brand = {**DEFAULT_BRAND, **(brand or {})}
    colors = {**DEFAULT_BRAND["colors"], **(brand.get("colors") or {})}
    fonts = {**DEFAULT_BRAND["fonts"], **(brand.get("fonts") or {})}
    w, h = size or SIZES.get(kind, SIZES["social"])
    layout = layout if layout in LAYOUTS else "classic"
    if theme:                                           # sermon-theme background (flames, waves, rays...)
        tdef = themes.THEMES.get(theme) or themes.THEMES[themes.DEFAULT]
        img = themes.canvas((w, h), theme, f"{headline}|{label}")
        colors = {**colors, "accent": tdef["glow"], "accent2": tdef["accent"], "primary": tdef["glow"],
                  "text": "#FFFFFF", "muted": "#F1EEF8"}
    else:
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
    if not body:                                         # hook-only graphic: centre the text block vertically
        block_h = len(hlines) * int(hsize * 1.18) + (90 if reference else 0)
        y = max(y, int((h - block_h) / 2 - 40))
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
    tag_x = None
    if not (logo and Path(logo).exists()):
        nf = load_font(34, True, brand.get("name", ""), fonts.get("heading"))
        name = (brand.get("name") or "").upper()
        tw = d.textlength(name, font=nf)
        mark_w = 0
        if DEFAULT_MARK.exists():                       # the El Ruach "R" mark ships with the app
            mk = Image.open(DEFAULT_MARK).convert("RGBA").resize((84, 84), Image.LANCZOS)
            mark_w = 84 + 18
            x0 = (w - (mark_w + tw)) / 2 if align_center else pad
            img.paste(mk, (int(x0), fy - 22), mk)
        else:
            x0 = (w - tw) / 2 if align_center else pad
        d.text((x0 + mark_w, fy - 10), name, font=nf, fill=text_color)
        tag_x = x0 + mark_w
    tf = load_font(22, False, brand.get("tagline", ""), fonts.get("body"))
    tag = brand.get("tagline") or ""
    if tag_x is None:
        tag_x = (w - d.textlength(tag, font=tf)) / 2 if align_center else pad
    d.text((tag_x, fy + 38), tag, font=tf, fill=muted)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def render_card(out, hook: str, theme: str, brand: dict | None = None, kind: str = "open", verse: str = "",
                closing: str = "", size: tuple = (1080, 1920)) -> Path:
    """Opening hook card or closing card for a reel (full-frame image on a themed background)."""
    if _use_html(brand):
        try:
            return htmlrender.render_card(out, hook, theme, {**DEFAULT_BRAND, **(brand or {})}, kind, verse, closing,
                                          size, (brand or {}).get("background"))
        except Exception as e:
            log.warning("HTML renderer failed (%s); using the built-in renderer", e)
    bgfile = (brand or {}).get("background")
    brand = {**DEFAULT_BRAND, **(brand or {})}
    fonts = {**DEFAULT_BRAND["fonts"], **(brand.get("fonts") or {})}
    w, h = size
    tdef = themes.THEMES.get(theme) or themes.THEMES[themes.DEFAULT]
    if bgfile and Path(bgfile).exists():
        img = _cover(bgfile, (w, h))
        img = Image.alpha_composite(img.convert("RGBA"), Image.new("RGBA", (w, h), (0, 0, 0, 120))).convert("RGB")
    else:
        img = themes.canvas((w, h), theme, f"{kind}|{hook}")
    d = ImageDraw.Draw(img)
    white, accent, soft = (255, 255, 255), _hex(tdef["accent"]), (240, 236, 250)
    pad = 90

    def block(text, bold, y, max_h, start, minimum, color, gap=1.2):
        font, lines, sz = _fit(d, text, bold, w - pad * 2, max_h, start, minimum, fonts.get("heading" if bold else "body"), gap)
        for ln in lines:
            lw = d.textlength(ln, font=font)
            d.text(((w - lw) / 2 + 3, y + 3), ln, font=font, fill=(0, 0, 0))          # soft shadow
            d.text(((w - lw) / 2, y), ln, font=font, fill=color)
            y += int(sz * gap)
        return y

    mark = Image.open(DEFAULT_MARK).convert("RGBA") if DEFAULT_MARK.exists() else None
    if kind == "open":
        d.line((w / 2 - 90, h * 0.30, w / 2 + 90, h * 0.30), fill=accent, width=8)
        y = block((hook or "").upper() if _is_latin(hook or "") else hook, True, h * 0.34, h * 0.30, 104, 56, white, 1.18)
        if verse:
            y = block(verse, False, y + 40, 160, 44, 28, accent)
        fn = load_font(32, True, brand.get("name", ""), fonts.get("heading"))
        nm = (brand.get("name") or "").upper()
        d.text(((w - d.textlength(nm, font=fn)) / 2, h - 210), nm, font=fn, fill=soft)
    else:
        if mark:
            m2 = mark.resize((200, 200), Image.LANCZOS)
            img.paste(m2, ((w - 200) // 2, int(h * 0.26)), m2)
        y = block((brand.get("name") or "").upper(), True, h * 0.26 + 250, 120, 80, 48, white)
        y = block(brand.get("tagline") or "", False, y + 10, 90, 40, 26, soft)
        y = block(closing or "Follow for more", True, y + 90, 200, 62, 36, accent)
        if verse:
            block(verse, False, y + 30, 120, 40, 26, soft)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out
