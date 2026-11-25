"""Stage 2 of the creative pipeline: professional design composition.

The picture (stage 1, text-free) is cover-cropped, colour graded and given a text scrim in Pillow. All words are then
drawn by the browser onto a TRANSPARENT layer (real typography, correct Indian-language shaping, bundled open fonts),
which also reports the exact geometry of every text element. The layers are composited and the geometry feeds the
quality checks (qc.py), so clipping, safe margins and contrast are measured on real pixels, never assumed."""
from __future__ import annotations

import html
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ruha.creative import formats
from ruha.services import htmlrender, keywords

MARK = htmlrender.MARK
DARK_THEMES = {"warfare", "faith", "spirit", "strength"}

# Typography treatments: each engine has its own fonts, scale, alignment, ornament and highlight rules.
TREAT = {
    "cinematic": dict(font='"Montserrat"', weight=800, style="normal", case="none", track="-0.012em", lh=1.06,
                      size=0.098, hl=True, kicker="upper", rule="short", support_font='"Montserrat"', support_w=500,
                      text="#FFFFFF", support="#E9E7F0", shadow=True),
    "biblical": dict(font='"Cormorant Garamond"', weight=700, style="normal", case="none", track="0", lh=1.02,
                     size=0.125, hl=False, kicker="smallcaps", rule="ornament", support_font='"Cormorant Garamond"',
                     support_w=600, text="#FFF4DD", support="#F1E3C4", shadow=True),
    "editorial": dict(font='"Playfair Display"', weight=700, style="normal", case="none", track="-0.005em", lh=1.1,
                      size=0.088, hl=False, kicker="ruled", rule="hairline", support_font='"Montserrat"',
                      support_w=400, text="#FFFFFF", support="#E2E0E8", shadow=True),
    "emotional": dict(font='"Montserrat"', weight=600, style="normal", case="none", track="-0.005em", lh=1.16,
                      size=0.078, hl=False, kicker="none", rule="none", support_font='"Playfair Display"',
                      support_w=500, text="#FFFFFF", support="#F0EAE0", shadow=True, support_italic=True),
    "fineart": dict(font='"Cormorant Garamond"', weight=600, style="italic", case="none", track="0", lh=1.0,
                    size=0.14, hl=False, kicker="none", rule="none", support_font='"Cormorant Garamond"',
                    support_w=500, text="#FFFFFF", support="#EFEAF6", shadow=True),
    "minimal": dict(font='"Montserrat"', weight=700, style="normal", case="none", track="-0.02em", lh=1.04,
                    size=0.105, hl=False, kicker="upper", rule="none", support_font='"Montserrat"', support_w=400,
                    text="#17151C", support="#4A4753", shadow=False),
    "modern": dict(font='"Montserrat"', weight=800, style="normal", case="none", track="-0.015em", lh=1.08,
                   size=0.092, hl=True, kicker="pill", rule="bar", support_font='"Montserrat"', support_w=500,
                   text="#FFFFFF", support="#E6E8F2", shadow=True),
    "movie": dict(font='"Bebas Neue"', weight=400, style="normal", case="upper", track="0.012em", lh=0.92,
                  size=0.215, hl=True, kicker="pill", rule="none", support_font='"Montserrat"', support_w=700,
                  text="#FFFFFF", support="#F4F1EA", shadow=True, support_upper=True),
}

KICKERS = {"poster": "Sermon highlight", "reel_cover": "Sermon", "feed_post": "Sermon", "square_post": "Sermon",
           "thumbnail": "", "carousel": "Sermon", "hook": "Sermon", "hook_story": "Sermon"}


# ------------------------------------------------------------------ colour helpers
def _hex_rgb(h: str) -> tuple:
    h = (h or "#ffffff").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lin(c):
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def luminance(rgb) -> np.ndarray:
    """WCAG relative luminance for an (..., 3) array of 0-255 values."""
    l = _lin(rgb)
    return 0.2126 * l[..., 0] + 0.7152 * l[..., 1] + 0.0722 * l[..., 2]


def contrast_ratio(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ------------------------------------------------------------------ picture handling
def cover(img: Image.Image, size) -> Image.Image:
    w, h = size
    k = max(w / img.width, h / img.height)
    im = img.convert("RGB").resize((max(w, round(img.width * k)), max(h, round(img.height * k))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def grade(img: Image.Image, g: dict, seed: int = 0) -> Image.Image:
    im = ImageEnhance.Contrast(img).enhance(g.get("contrast", 1.0))
    im = ImageEnhance.Color(im).enhance(g.get("saturation", 1.0))
    im = ImageEnhance.Brightness(im).enhance(g.get("brightness", 1.0))
    a = np.asarray(im, dtype=np.float32)
    k = g.get("tint_k", 0.0)
    if k:
        a = a * (1 - k) + np.asarray(g["tint"], dtype=np.float32) * k
    h, w = a.shape[:2]
    v = g.get("vignette", 0.0)
    if v:
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / 1.41
        a *= (1 - v * np.clip(d, 0, 1) ** 2.2)[..., None]
    gr = g.get("grain", 0.0)
    if gr:
        rng = np.random.default_rng(seed or 1)
        a += rng.normal(0, gr * 255, (h, w, 1)).astype(np.float32)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def scrim(img: Image.Image, box: dict, zone: str, strength: float = 0.72) -> Image.Image:
    """Darken the side of the picture where the words go, so contrast does not depend on a lucky image."""
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if zone.startswith("lower"):
        top = max(0, box["y"] - 0.12 * h)
        a = np.clip((yy - top) / max(1, h - top), 0, 1) ** 1.25
    elif zone.startswith("upper"):
        bot = min(h, box["y"] + box["h"] + 0.12 * h)
        a = np.clip((bot - yy) / max(1, bot), 0, 1) ** 1.25
    elif zone == "left-half":
        edge = min(w, box["x"] + box["w"] + 0.18 * w)
        a = np.clip((edge - xx) / max(1, edge), 0, 1) ** 1.15
    else:                                                          # centre: soft dark pool behind the text
        cy, cx = box["y"] + box["h"] / 2, w / 2
        d = np.sqrt(((xx - cx) / (w * 0.62)) ** 2 + ((yy - cy) / (box["h"] * 0.95 + 1)) ** 2)
        a = np.clip(1 - d, 0, 1) ** 0.9
    a = (a * strength)[..., None]
    base = np.asarray(img, dtype=np.float32)
    return Image.fromarray(np.clip(base * (1 - a), 0, 255).astype(np.uint8))


def procedural_background(size, accent: str, seed: int, dark: bool) -> Image.Image:
    """Typographic engine only: a tonal textured field (paper or charcoal). Labelled 'typographic' - not AI artwork."""
    w, h = size
    rng = np.random.default_rng(seed or 1)
    ar = np.array(_hex_rgb(accent), dtype=np.float32)
    base = np.array((18, 19, 26) if dark else (238, 232, 220), dtype=np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    glow = np.exp(-(((xx - w * 0.85) / (w * 0.7)) ** 2 + ((yy - h * 0.1) / (h * 0.6)) ** 2))[..., None]
    a = base + (ar - base) * glow * (0.16 if dark else 0.10)
    a += rng.normal(0, 3.2, (h, w, 1)).astype(np.float32)                      # paper / film tooth
    soft = np.asarray(Image.fromarray(np.clip(rng.normal(128, 40, (h // 16 + 1, w // 16 + 1)), 0, 255).astype(np.uint8))
                      .resize((w, h), Image.BICUBIC), dtype=np.float32)[..., None]
    a += (soft - 128) * 0.05
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


# ------------------------------------------------------------------ the transparent text layer
def _hl_html(text: str, on: bool) -> str:
    return keywords.highlight_html(text) if on else html.escape(text)


def layout_boxes(fmt_key: str, zone: str, show_brand: bool) -> tuple:
    """(text box, brand box|None). The brand lockup takes the band opposite the text so they never collide."""
    fmt = formats.FORMATS[fmt_key]
    W, H = fmt["size"]
    l, t, r, b = fmt["safe"]
    box = formats.zone_box(fmt["size"], fmt["safe"], zone)
    brand = None
    if show_brand:
        bh = 96
        if zone == "left-half":
            brand = dict(x=l, y=H - b - bh, w=int(W * 0.4), h=bh, align="left")
            box["h"] -= bh + 30
        elif zone.startswith("lower"):
            brand = dict(x=l, y=t, w=W - l - r, h=bh, align="left")
        else:
            brand = dict(x=l, y=H - b - bh, w=W - l - r, h=bh, align="left" if box["align"] == "left" else "center")
    return box, brand


def overlay_html(concept: dict, fmt_key: str, box: dict, brand_box: dict | None, brand: dict, colours: dict,
                 slide: dict | None = None, shrink: float = 1.0) -> str:
    T = TREAT[concept["treatment"]]
    fmt = formats.FORMATS[fmt_key]
    W, H = fmt["size"]
    base = W * (0.78 if formats.aspect_class(fmt["size"]) == "wide" else 1.0)
    tall = 1.15 if formats.aspect_class(fmt["size"]) == "tall" else 1.0       # more vertical room: bigger type
    fs0 = round(T["size"] * base * shrink * tall)
    fmin = round(max(0.036 * H, 0.04 * base) if fmt_key != "thumbnail" else 0.11 * H)
    accent, text, support = colours["accent"], colours["text"], colours["support"]
    headline = (slide or concept)["headline"]
    supporting = (slide or concept).get("supporting_text", "")
    ref = (slide or concept).get("scripture_reference", "")
    kicker = KICKERS.get(fmt_key, "") if T["kicker"] != "none" else ""
    if slide and slide.get("role") != "hook":
        kicker = f"{slide.get('index', 1)} / {slide.get('total', 1)}" if slide.get("role") == "point" else kicker
    fonts_css, _head = htmlrender._font_css(None)
    fall = htmlrender._FALLBACK_STACK
    align, valign = box["align"], {"top": "flex-start", "middle": "center", "bottom": "flex-end"}[box["valign"]]
    ai = {"left": "flex-start", "center": "center", "right": "flex-end"}[align]
    shadow = "0 3px 18px rgba(0,0,0,.55),0 1px 2px rgba(0,0,0,.6)" if T["shadow"] else "none"
    hcase = "uppercase" if T["case"] == "upper" else "none"
    sc = "uppercase" if T.get("support_upper") else "none"
    sitalic = "italic" if T.get("support_italic") else "normal"
    hl_css = f".k{{color:{accent}}}" if T["hl"] else ""
    bar_css = (f".box.bar h1{{border-left:10px solid {accent};padding-left:{round(base*0.028)}px}}"
               if T["rule"] == "bar" else "")
    pill_css = (f"#k.pill{{display:inline-block;padding:{round(base*0.008)}px {round(base*0.022)}px;border-radius:999px;"
                f"background:{accent};color:#14121A;font-weight:800;letter-spacing:.14em;text-transform:uppercase;"
                f"font-size:{round(base*0.02)}px}}")
    kicker_html = ""
    if kicker:
        cls = {"pill": "pill", "ruled": "ruled", "smallcaps": "sc", "upper": "up"}.get(T["kicker"], "up")
        kicker_html = f'<div id="k" class="{cls}">{html.escape(kicker)}</div>'
    rule_html = {"short": f'<i class="rule" style="width:{round(base*0.11)}px;height:6px;background:{accent}"></i>',
                 "hairline": f'<i class="rule" style="width:100%;height:2px;background:{accent};opacity:.7"></i>',
                 "ornament": f'<div class="orn" id="o"><i></i><b></b><i></i></div>'}.get(T["rule"], "")
    sup_html = f'<p id="s">{html.escape(supporting)}</p>' if supporting else ""
    ref_html = f'<div id="r">{html.escape(ref)}</div>' if ref else ""
    brand_html = ""
    if brand_box:
        mark = f'<img src="{MARK.as_uri()}" style="width:64px;height:64px;border-radius:15px">' if MARK.exists() else ""
        nm = html.escape((brand.get("name") or "").upper())
        brand_html = (f'<div id="b" style="position:absolute;left:{brand_box["x"]}px;top:{brand_box["y"]}px;'
                      f'width:{brand_box["w"]}px;height:{brand_box["h"]}px;display:flex;align-items:center;gap:16px;'
                      f'justify-content:{"center" if brand_box["align"]=="center" else "flex-start"};color:{text};'
                      f'font:700 28px "Montserrat",{fall};letter-spacing:.06em;text-shadow:{shadow}">{mark}<span>{nm}</span></div>')
    css = f"""
{fonts_css}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px;background:transparent;overflow:hidden}}
.box{{position:absolute;left:{box['x']}px;top:{box['y']}px;width:{box['w']}px;height:{box['h']}px;display:flex;
  flex-direction:column;justify-content:{valign};align-items:{ai};text-align:{align};gap:{round(base*0.022)}px;overflow:hidden}}
h1{{font-family:{T['font']},{fall};font-weight:{T['weight']};font-style:{T['style']};text-transform:{hcase};
  letter-spacing:{T['track']};line-height:{T['lh']};color:{text};text-shadow:{shadow};text-wrap:balance;
  overflow-wrap:break-word;max-width:100%}}
p#s{{font-family:{T['support_font']},{fall};font-weight:{T['support_w']};font-style:{sitalic};text-transform:{sc};
  color:{support};text-shadow:{shadow};line-height:1.32;max-width:{'100%' if align=='center' else '92%'};
  font-size:{round(base*0.033)}px;text-wrap:pretty}}
#r{{font-family:"Montserrat",{fall};font-weight:700;color:{accent};letter-spacing:.06em;font-size:{round(base*0.03)}px;
  text-shadow:{shadow}}}
#k{{font-family:"Montserrat",{fall};font-weight:700;color:{accent};font-size:{round(base*0.021)}px;letter-spacing:.22em;
  text-transform:uppercase;text-shadow:{shadow}}}
#k.sc{{font-family:{T['font']},{fall};font-variant:small-caps;text-transform:none;letter-spacing:.2em;font-size:{round(base*0.034)}px}}
#k.ruled{{border-top:2px solid {accent};padding-top:{round(base*0.012)}px;width:100%}}
.rule{{display:block}}
.orn{{display:flex;align-items:center;gap:16px;opacity:.95}}
.orn i{{display:block;width:{round(base*0.09)}px;height:2px;background:{accent}}}
.orn b{{display:block;width:12px;height:12px;background:{accent};transform:rotate(45deg)}}
{hl_css}{bar_css}{pill_css}
"""
    script = f"""
document.fonts.ready.then(function(){{
  var box=document.getElementById('box'),h=document.getElementById('h'),fs={fs0},min={fmin};
  function ok(){{
    // measure real rectangles: upward overflow of bottom-aligned text is invisible to scrollHeight
    var br=box.getBoundingClientRect();
    return Array.prototype.every.call(box.children,function(c){{var q=c.getBoundingClientRect();
      return q.top>=br.top-1 && q.bottom<=br.bottom+1 && q.left>=br.left-1 && q.right<=br.right+1;}})
      && h.scrollWidth<=box.clientWidth+1;
  }}
  h.style.fontSize=fs+'px';
  while(!ok() && fs>min){{fs=Math.max(min,Math.floor(fs*0.96));h.style.fontSize=fs+'px';}}
  function r(id){{var e=document.getElementById(id);if(!e)return null;var q=e.getBoundingClientRect();
    return {{x:q.x,y:q.y,w:q.width,h:q.height}};}}
  var cs=getComputedStyle(h),lh=parseFloat(cs.lineHeight)||fs;
  console.log('RUHAMETRICS '+JSON.stringify({{fontSize:fs,minFont:min,fits:ok(),lines:Math.round(h.getBoundingClientRect().height/lh),
    box:r('box'),k:r('k'),h:r('h'),s:r('s'),r:r('r'),b:r('b'),
    sfont:(document.getElementById('s')?parseFloat(getComputedStyle(document.getElementById('s')).fontSize):0)}}));
}});
"""
    bar_cls = " bar" if T["rule"] == "bar" else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>'
            f'<div class="box{bar_cls}" id="box">{kicker_html}<h1 id="h">{_hl_html(headline, T["hl"])}</h1>{rule_html}{sup_html}{ref_html}</div>'
            f'{brand_html}<script>{script}</script></body></html>')


def render_overlay(html_text: str, size, timeout: int = 90) -> tuple:
    """Run the browser on the overlay page. Returns (RGBA image, metrics dict)."""
    exe = htmlrender.find_browser()
    if not exe:
        raise htmlrender.BrowserUnavailable("No Edge/Chrome/Chromium browser found (needed to draw the typography)")
    W, H = size
    work = Path(tempfile.mkdtemp(prefix="ruha_ov_"))
    try:
        page = work / "o.html"
        page.write_text(html_text, encoding="utf-8")
        shot = work / "o.png"
        cmd = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
               "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--allow-file-access-from-files",
               "--default-background-color=00000000", "--enable-logging=stderr", "--v=0",
               f"--user-data-dir={work / 'p'}", f"--window-size={W},{H}", "--virtual-time-budget=5000",
               f"--screenshot={shot}", page.as_uri()]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Typography render timed out after {timeout}s") from e
        if not shot.exists():
            raise RuntimeError("The browser produced no image for the typography layer")
        img = Image.open(shot).convert("RGBA")
        if img.size != (W, H):
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            canvas.paste(img.crop((0, 0, min(W, img.width), min(H, img.height))), (0, 0))
            img = canvas
        metrics = {}
        for line in (res.stderr or "").splitlines():
            m = re.search(r'RUHAMETRICS (\{.*\})", source', line)
            if m:
                metrics = json.loads(m.group(1))
        return img, metrics
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ composition
def palette_colours(treatment: str, accent: str, dark: bool = True) -> dict:
    T = TREAT[treatment]
    if treatment == "minimal":
        return ({"text": "#F4F2F8", "support": "#C9C6D3", "accent": accent} if dark else
                {"text": T["text"], "support": T["support"], "accent": _darken(accent, 0.55)})
    return {"text": T["text"], "support": T["support"], "accent": accent}


def _darken(hex_color: str, k: float) -> str:
    r, g, b = _hex_rgb(hex_color)
    return "#%02x%02x%02x" % (int(r * k), int(g * k), int(b * k))


def compose(background: Image.Image | None, concept: dict, fmt_key: str, brand: dict | None = None, *,
            show_brand: bool = True, slide: dict | None = None, shrink: float = 1.0, scrim_strength: float = 0.72,
            style: dict | None = None) -> dict:
    """Layer 1 picture -> grade -> scrim; layer 2 typography. Returns every layer plus the measured geometry."""
    from ruha.creative import styles as _styles
    st = style or _styles.STYLES[concept["style"]]
    fmt = formats.FORMATS[fmt_key]
    size = fmt["size"]
    box, brand_box = layout_boxes(fmt_key, concept["zone"], show_brand)
    dark_field = concept.get("emotional_theme") in DARK_THEMES
    if concept["treatment"] == "minimal":
        bg = procedural_background(size, concept["accent"], concept.get("seed", 1), dark_field)
        colours = palette_colours("minimal", concept["accent"], dark_field)
        scr = bg
    else:
        if background is None:
            raise ValueError("This design engine needs a generated picture")
        bg = grade(cover(background, size), st["grade"], concept.get("seed", 0))
        scr = scrim(bg, box, concept["zone"], scrim_strength)
        colours = palette_colours(concept["treatment"], concept["accent"])
    html_text = overlay_html(concept, fmt_key, box, brand_box, brand or {"name": "El Ruach"}, colours, slide, shrink)
    overlay, metrics = render_overlay(html_text, size)
    final = Image.alpha_composite(scr.convert("RGBA"), overlay).convert("RGB")
    return {"final": final, "background": scr, "overlay": overlay, "metrics": metrics, "box": box,
            "brand_box": brand_box, "colours": colours, "size": size}
