from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import textwrap, os

def get_font(size, bold=False):
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def make_graphic(headline, body, output, label="A MESSAGE TO REMEMBER"):
    w, h = 1080, 1350
    img = Image.new("RGB", (w, h), (19, 27, 43))
    d = ImageDraw.Draw(img)
    # Restrained gradient-like bands, entirely generated locally.
    for y in range(h):
        t = y / h
        col = (int(19 + 15*t), int(27 + 10*t), int(43 + 28*t))
        d.line((0, y, w, y), fill=col)
    d.rounded_rectangle((72, 80, 1008, 1270), radius=36, outline=(190, 166, 112), width=3)
    d.text((110, 145), label.upper(), font=get_font(30, True), fill=(219, 194, 139))
    d.line((110, 215, 280, 215), fill=(219, 194, 139), width=5)
    lines = textwrap.wrap(headline or "A Message of Hope", width=24)
    y = 330
    for line in lines[:5]:
        d.text((110, y), line, font=get_font(68, True), fill=(255,255,255))
        y += 88
    body_lines = textwrap.wrap(body or "", width=48)
    y = min(max(y + 40, 800), 970)
    for line in body_lines[:5]:
        d.text((110, y), line, font=get_font(30), fill=(222,226,233))
        y += 48
    d.text((110, 1170), "LOCAL AI CONTENT STUDIO", font=get_font(22, True), fill=(177,185,198))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    img.save(output, quality=95)
    return output
