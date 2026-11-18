"""HTML/CSS poster and card rendering through a locally installed Chromium-family browser (Edge, Chrome,
Chromium, Brave). No extra Python package and no download: the browser's own headless screenshot is used.

Why: real typography (kerning, glows, shadows, blur), correct shaping of Indic and Arabic scripts, and
bundled open-licence fonts so the output looks the same on every machine. If no browser is found the caller
falls back to the Pillow renderer (design.py)."""
from __future__ import annotations

import html
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from ruha.services import themes

log = logging.getLogger("ruha.htmlrender")

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
MARK = Path(__file__).resolve().parents[1] / "assets" / "logo-mark.png"

# (css family, file) - variable fonts, so every weight comes from one file
_FACES = [
    ("Montserrat", "Montserrat.ttf", "normal"),
    ("Playfair Display", "PlayfairDisplay.ttf", "normal"),
    ("Playfair Display", "PlayfairDisplay-Italic.ttf", "italic"),
    ("Cormorant Garamond", "CormorantGaramond.ttf", "normal"),
    ("Cormorant Garamond", "CormorantGaramond-Italic.ttf", "italic"),
    ("Bebas Neue", "BebasNeue.ttf", "normal"),
    ("Noto Sans Devanagari", "NotoSansDevanagari.ttf", "normal"),
    ("Noto Sans Telugu", "NotoSansTelugu.ttf", "normal"),
    ("Noto Sans Tamil", "NotoSansTamil.ttf", "normal"),
    ("Noto Sans Bengali", "NotoSansBengali.ttf", "normal"),
    ("Noto Sans Kannada", "NotoSansKannada.ttf", "normal"),
    ("Noto Sans Malayalam", "NotoSansMalayalam.ttf", "normal"),
    ("Noto Sans Gujarati", "NotoSansGujarati.ttf", "normal"),
    ("Noto Sans Gurmukhi", "NotoSansGurmukhi.ttf", "normal"),
    ("Noto Sans Oriya", "NotoSansOriya.ttf", "normal"),
    ("Noto Sans Arabic", "NotoSansArabic.ttf", "normal"),
]
_FALLBACK_STACK = ('"Noto Sans Devanagari","Noto Sans Telugu","Noto Sans Tamil","Noto Sans Bengali","Noto Sans Kannada",'
                   '"Noto Sans Malayalam","Noto Sans Gujarati","Noto Sans Gurmukhi","Noto Sans Oriya","Noto Sans Arabic",'
                   '"Segoe UI",Arial,sans-serif')

_STOP = {"the", "and", "for", "that", "this", "with", "you", "your", "are", "not", "but", "his", "her", "its",
         "from", "have", "has", "was", "were", "will", "into", "over", "they", "them", "our", "all", "can",
         "who", "what", "when", "then", "than", "join", "stop", "keep", "never", "every", "against"}


class BrowserUnavailable(RuntimeError):
    pass


_browser_cache: dict = {}


def find_browser() -> str | None:
    """Path of a Chromium-family browser, or None. Override with the RUHA_BROWSER environment variable."""
    if "path" in _browser_cache:
        return _browser_cache["path"]
    cands = []
    if os.environ.get("RUHA_BROWSER"):
        cands.append(os.environ["RUHA_BROWSER"])
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [rf"{base}\Microsoft\Edge\Application\msedge.exe", rf"{base}\Google\Chrome\Application\chrome.exe",
                          rf"{base}\BraveSoftware\Brave-Browser\Application\brave.exe"]
    elif sys.platform == "darwin":
        cands += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    found = next((c for c in cands if c and Path(c).exists()), None)
    _browser_cache["path"] = found
    return found


def available() -> bool:
    return find_browser() is not None


def _screenshot(html_text: str, out: Path, w: int, h: int, timeout: int = 60) -> None:
    exe = find_browser()
    if not exe:
        raise BrowserUnavailable("No Edge/Chrome/Chromium browser found")
    k = 1 if w >= 600 else math.ceil(600 / w)             # browsers refuse very small windows: render big, scale down
    W, H = w * k, h * k
    work = Path(tempfile.mkdtemp(prefix="ruha_html_"))
    try:
        page = work / "index.html"
        page.write_text(html_text.replace("__K__", str(k)), encoding="utf-8")
        shot = work / "shot.png"
        cmd = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
               "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--allow-file-access-from-files",
               f"--user-data-dir={work / 'profile'}", f"--window-size={W},{H}", "--virtual-time-budget=4000",
               f"--screenshot={shot}", page.as_uri()]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Browser render timed out after {timeout}s") from e
        if not shot.exists() or shot.stat().st_size < 1000:
            raise RuntimeError("Browser produced no screenshot")
        img = Image.open(shot).convert("RGB")
        if img.size != (W, H):                              # some builds add/remove a few rows: crop or pad
            canvas = Image.new("RGB", (W, H), img.getpixel((0, 0)))
            canvas.paste(img.crop((0, 0, min(W, img.width), min(H, img.height))), (0, 0))
            img = canvas
        if k != 1:
            img = img.resize((w, h), Image.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG", optimize=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _font_css(brand_font: str | None = None) -> tuple[str, str]:
    css = []
    for fam, fn, style in _FACES:
        p = FONT_DIR / fn
        if p.exists():
            css.append(f'@font-face{{font-family:"{fam}";src:url("{p.as_uri()}");font-weight:100 900;font-style:{style};}}')
    head = f'"Montserrat",{_FALLBACK_STACK}'
    if brand_font and Path(brand_font).exists():
        css.append(f'@font-face{{font-family:"BrandHeading";src:url("{Path(brand_font).as_uri()}");}}')
        head = f'"BrandHeading",{head}'
    return "".join(css), head


def _key_words(text: str, n: int = 2) -> set:
    words = [w.strip(".,!?;:\"'()").lower() for w in (text or "").split()]
    cand = sorted({w for w in words if len(w) > 4 and w not in _STOP}, key=len, reverse=True)
    return set(cand[:n])


def _hook_html(text: str) -> str:
    keys = _key_words(text)
    parts = []
    for word in (text or "").split():
        esc = html.escape(word)
        parts.append(f'<span class="k">{esc}</span>' if word.strip(".,!?;:\"'()").lower() in keys else esc)
    return " ".join(parts)


def _theme_vars(theme: str) -> dict:
    return themes.THEMES.get(theme) or themes.THEMES[themes.DEFAULT]


def _background_file(theme: str, seed: str, size: tuple, background: str | None) -> tuple:
    """The themed backdrop as an image file: an AI/user supplied picture when given, else the Pillow motif canvas.
    Returns (path, is_temporary)."""
    if background and Path(background).exists():
        return Path(background), False
    fd, name = tempfile.mkstemp(prefix="ruha_bg_", suffix=".png")
    os.close(fd)
    themes.canvas(size, theme, seed).save(name, "PNG")
    return Path(name), True


_BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:__BW__;height:__BH__;overflow:hidden;background:#000}
.stage{position:absolute;left:0;top:0;width:__W__px;height:__H__px;transform:scale(__SCALE__);transform-origin:0 0;
  font-family:__HEAD__;color:#fff;overflow:hidden}
.bg{position:absolute;inset:0;background:url("__BG__") center/cover no-repeat}
.shade{position:absolute;inset:0;background:
  radial-gradient(ellipse at 50% 50%,rgba(0,0,0,0) 28%,rgba(0,0,0,__VIG__) 100%),
  linear-gradient(rgba(0,0,0,__FLAT__),rgba(0,0,0,__FLAT__))}
.glow{position:absolute;inset:0;background:radial-gradient(circle at 50% 46%,__GLOW__38 0%,transparent 58%)}
.frame{position:absolute;inset:__F1__px;border:3px solid __ACC__;border-radius:__R1__px;opacity:.85}
.frame2{position:absolute;inset:__F2__px;border:1px solid __ACC__55;border-radius:__R2__px}
.wrap{position:absolute;inset:__F2__px;display:flex;flex-direction:column;align-items:center;padding:__PADY__px __PADX__px}
.pill{font-weight:800;font-size:__LBL__px;letter-spacing:var(--ls,.42em);text-transform:uppercase;color:__ACC__;
  padding:__PV__px __PH__px;border:2px solid __ACC__;border-radius:999px;background:__ACC__28;}
.mid{flex:1;width:calc(100% + 140px);margin:0 -70px;padding:36px 70px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;
  gap:__GAP__px;min-height:0;overflow:hidden}
h1{font-weight:800;line-height:1.12;text-transform:uppercase;letter-spacing:.01em;
  text-shadow:0 6px 0 rgba(0,0,0,.55),0 0 36px rgba(0,0,0,.45)}
.k{color:__ACC__;text-shadow:0 0 44px __GLOW__,0 0 18px __GLOW__99,0 6px 0 rgba(0,0,0,.6)}
.orn{display:flex;align-items:center;gap:20px}
.orn i{display:block;width:150px;height:3px;background:__ACC__}
.orn b{display:block;width:20px;height:20px;background:__ACC__;transform:rotate(45deg)}
.panel{width:100%;padding:__PPV__px __PPH__px;border:2px solid __ACC__66;border-radius:28px;background:rgba(0,0,0,.42);
  backdrop-filter:blur(6px)}
.verse{font-family:"Playfair Display",__FALL__;font-style:italic;font-weight:500;line-height:1.38;color:#f7f3ff}
.ref{font-weight:800;color:__ACC__;letter-spacing:.04em}
.foot{display:flex;align-items:center;gap:18px;margin-top:__GAP__px}
.foot img.mark{width:84px;height:84px;border-radius:20px}
.foot img.logo{max-height:90px;max-width:260px}
.foot .nm{font-weight:800;font-size:34px;letter-spacing:.04em;text-transform:uppercase}
.foot .tg{font-size:22px;opacity:.85;margin-top:2px}
"""


def _page(css_extra: str, body: str, w: int, h: int, bg: Path, tv: dict, brand_font: str | None, *,
          vignette=0.55, flat=0.22, script: str = "") -> str:
    fonts, head = _font_css(brand_font)
    u = w / 1080.0
    css = _BASE_CSS + css_extra
    # all layout numbers in the CSS are for a 1080-wide canvas; the stage is scaled by CSS transform below
    rep = {"__W__": str(1080), "__H__": str(round(h / u)), "__HEAD__": head, "__FALL__": _FALLBACK_STACK,
           "__BG__": bg.as_uri(), "__VIG__": str(vignette), "__FLAT__": str(flat), "__GLOW__": tv["glow"],
           "__ACC__": tv["accent"], "__F1__": "44", "__F2__": "62", "__R1__": "36", "__R2__": "26", "__PADY__": "58",
           "__PADX__": "70", "__LBL__": "30", "__PV__": "12", "__PH__": "44", "__GAP__": "34", "__PPV__": "44",
           "__PPH__": "56"}
    for k, v in rep.items():
        css = css.replace(k, v)
    css = css.replace("__SCALE__", f"calc({u:.5f} * __K__)").replace("__BW__", f"calc({w}px * __K__)")         .replace("__BH__", f"calc({h}px * __K__)")
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts}{css}</style></head><body>'
            f'<div class="stage"><div class="bg"></div><div class="shade"></div><div class="glow"></div>'
            f'<div class="frame"></div><div class="frame2"></div>{body}</div>'
            f'<script>{script}</script></body></html>')


_FIT_JS = """
document.fonts.ready.then(function(){
  var mid=document.getElementById('mid'),h=document.getElementById('h'),v=document.getElementById('v');
  if(!mid||!h)return;
  var hs=__HS__, vs=__VS__, s=1;
  function apply(){h.style.fontSize=(hs*s)+'px'; if(v)v.style.fontSize=(vs*s)+'px';}
  apply();
  while(mid.scrollHeight>mid.clientHeight+2 && s>0.42){s-=0.04;apply();}
});
"""


def _footer(brand: dict) -> str:
    logo = brand.get("logo")
    if logo and Path(logo).exists():
        img = f'<img class="logo" src="{Path(logo).as_uri()}">'
        text = ""
    else:
        img = f'<img class="mark" src="{MARK.as_uri()}">' if MARK.exists() else ""
        text = (f'<div><div class="nm">{html.escape(brand.get("name") or "")}</div>'
                f'<div class="tg">{html.escape(brand.get("tagline") or "")}</div></div>')
    return f'<div class="foot">{img}{text}</div>'


def render_graphic(out, headline: str, body: str, reference: str, label: str, brand: dict, size: tuple,
                   theme: str, background: str | None = None) -> Path:
    """Poster / post: pill label, big centred hook with highlighted keywords, optional verse panel, brand footer."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    tv = _theme_vars(theme)
    bg, tmp_bg = _background_file(theme, f"{headline}|{label}", (max(w, 540), max(h, 540)), background)
    fonts = (brand.get("fonts") or {})
    verse = bool(body)
    mid = f'<h1 id="h">{_hook_html(headline)}</h1>'
    if verse:
        mid += ('<div class="orn"><i></i><b></b><i></i></div>'
                f'<div class="panel"><div class="verse" id="v">“{html.escape(body)}”</div>'
                + (f'<div class="ref" style="margin-top:22px;font-size:36px">{html.escape(reference)}</div>' if reference else "")
                + '</div>')
    elif reference:
        mid += ('<div class="orn"><i></i><b></b><i></i></div>'
                f'<div class="ref" style="font-size:44px">{html.escape(reference)}</div>')
    ls = "" if label.isascii() else ' style="--ls:0"'      # letter-spacing would split Indic conjuncts
    pill = f'<div class="pill"{ls}>{html.escape(label)}</div>' if label else ""
    inner = f'<div class="wrap">{pill}<div class="mid" id="mid">{mid}</div>{_footer(brand)}</div>'
    script = _FIT_JS.replace("__HS__", "112" if not verse else "104").replace("__VS__", "40")
    extra = " h1{font-size:112px} .verse{font-size:40px}"
    page = _page(extra, inner, w, h, bg, tv, fonts.get("heading"), script=script)
    try:
        _screenshot(page, out, w, h)
    finally:
        if tmp_bg:
            bg.unlink(missing_ok=True)
    return out


def render_card(out, hook: str, theme: str, brand: dict, kind: str, verse: str, closing: str, size: tuple,
                background: str | None = None) -> Path:
    """Reel opening hook card (kind='open') or closing card (kind='close'), full-frame vertical."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    tv = _theme_vars(theme)
    bg, tmp_bg = _background_file(theme, f"{kind}|{hook}", (max(w, 540), max(h, 540)), background)
    fonts = (brand.get("fonts") or {})
    name = html.escape((brand.get("name") or "").upper())
    if kind == "open":
        mid = ('<div style="width:180px;height:8px;border-radius:4px;background:%s"></div>' % tv["accent"]
               + f'<h1 id="h">{_hook_html(hook)}</h1>')
        if verse:
            mid += f'<div class="verse" id="v" style="color:{tv["accent"]};font-style:normal;font-weight:700">{html.escape(verse)}</div>'
        inner = (f'<div class="wrap"><div class="mid" id="mid">{mid}</div>'
                 f'<div class="nm" style="font-weight:800;font-size:32px;letter-spacing:.08em;opacity:.9;margin-bottom:60px">{name}</div></div>')
        script = _FIT_JS.replace("__HS__", "118").replace("__VS__", "46")
        extra = " h1{font-size:118px} .verse{font-size:46px}"
    else:
        mark = f'<img src="{MARK.as_uri()}" style="width:200px;height:200px;border-radius:44px">' if MARK.exists() else ""
        mid = (mark + f'<div style="font-weight:800;font-size:80px;letter-spacing:.05em">{name}</div>'
               f'<div style="font-size:38px;opacity:.88">{html.escape(brand.get("tagline") or "")}</div>'
               '<div class="orn"><i></i><b></b><i></i></div>'
               f'<h1 id="h" style="font-size:66px">{_hook_html(closing or "Follow for more")}</h1>')
        if verse:
            mid += f'<div class="verse" id="v" style="font-size:40px">{html.escape(verse)}</div>'
        inner = f'<div class="wrap"><div class="mid" id="mid">{mid}</div></div>'
        script = _FIT_JS.replace("__HS__", "66").replace("__VS__", "40")
        extra = ""
    page = _page(extra, inner, w, h, bg, tv, fonts.get("heading"), vignette=0.6, flat=0.18, script=script)
    try:
        _screenshot(page, out, w, h)
    finally:
        if tmp_bg:
            bg.unlink(missing_ok=True)
    return out
