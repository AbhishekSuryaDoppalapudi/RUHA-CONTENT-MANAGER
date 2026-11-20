"""Fonts for captions: Poppins for Latin text, and a Noto Sans font per script for Indian-language captions.

Fonts live in ``ruha/assets/fonts`` and are copied next to the subtitle file for FFmpeg's caption renderer (libass), so
captions look the same on every PC and never depend on which fonts Windows happens to have. Each caption line is set in
the font of its own script (a Telugu caption in Noto Sans Telugu, a Hindi one in Noto Sans Devanagari, ...)."""
from __future__ import annotations

import shutil
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

LATIN = {"small": ("Poppins SemiBold", "Poppins-SemiBold.ttf"), "big": ("Poppins ExtraBold", "Poppins-ExtraBold.ttf"),
         "bold": ("Poppins", "Poppins-Bold.ttf")}

# script -> (Unicode ranges, font family as FFmpeg/libass must name it, file in FONT_DIR)
SCRIPTS = {
    "devanagari": ([(0x0900, 0x097F), (0xA8E0, 0xA8FF)], "Noto Sans Devanagari", "NotoSansDevanagari.ttf"),   # Hindi, Marathi, Nepali, Sanskrit, Konkani, Maithili, Dogri, Bodo
    "bengali": ([(0x0980, 0x09FF)], "Noto Sans Bengali", "NotoSansBengali.ttf"),                              # Bengali, Assamese
    "gurmukhi": ([(0x0A00, 0x0A7F)], "Noto Sans Gurmukhi", "NotoSansGurmukhi.ttf"),                           # Punjabi
    "gujarati": ([(0x0A80, 0x0AFF)], "Noto Sans Gujarati", "NotoSansGujarati.ttf"),
    "oriya": ([(0x0B00, 0x0B7F)], "Noto Sans Oriya", "NotoSansOriya.ttf"),                                    # Odia
    "tamil": ([(0x0B80, 0x0BFF)], "Noto Sans Tamil", "NotoSansTamil.ttf"),
    "telugu": ([(0x0C00, 0x0C7F)], "Noto Sans Telugu", "NotoSansTelugu.ttf"),
    "kannada": ([(0x0C80, 0x0CFF)], "Noto Sans Kannada", "NotoSansKannada.ttf"),
    "malayalam": ([(0x0D00, 0x0D7F)], "Noto Sans Malayalam", "NotoSansMalayalam.ttf"),
    "arabic": ([(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)], "Noto Sans Arabic", "NotoSansArabic.ttf"),  # Urdu, Sindhi, Kashmiri
}
FALLBACK_FAMILY = "Nirmala UI"          # Windows' own Indic font, for a script with no bundled font (Santali, Meitei ...)
LANGUAGE_SCRIPT = {"Hindi": "devanagari", "Marathi": "devanagari", "Nepali": "devanagari", "Sanskrit": "devanagari",
                   "Konkani": "devanagari", "Maithili": "devanagari", "Dogri": "devanagari", "Bodo": "devanagari",
                   "Bengali": "bengali", "Assamese": "bengali", "Punjabi": "gurmukhi", "Gujarati": "gujarati", "Odia": "oriya",
                   "Tamil": "tamil", "Telugu": "telugu", "Kannada": "kannada", "Malayalam": "malayalam", "Urdu": "arabic",
                   "Sindhi": "arabic", "Kashmiri": "arabic"}


def script_of(text: str) -> str | None:
    """The script most of ``text`` is written in (a key of SCRIPTS), or None for Latin / unknown text."""
    counts: dict = {}
    for ch in text or "":
        cp = ord(ch)
        if cp < 0x0250:
            continue
        for name, (ranges, _fam, _file) in SCRIPTS.items():
            if any(a <= cp <= b for a, b in ranges):
                counts[name] = counts.get(name, 0) + 1
                break
    return max(counts, key=counts.get) if counts else None


def available(script: str) -> bool:
    return script in SCRIPTS and (FONT_DIR / SCRIPTS[script][2]).exists()


def family_for(text: str, role: str = "small") -> str:
    """The font family to set ``text`` in. ``role``: small | big | bold (Latin only; Indian scripts have one weight)."""
    sc = script_of(text)
    if sc is None:
        return LATIN[role][0]
    return SCRIPTS[sc][1] if available(sc) else FALLBACK_FAMILY


def files_for(texts) -> list:
    """Font files needed to draw these captions (the Latin ones always, plus one per script that occurs)."""
    names = [f for _fam, f in LATIN.values()]
    for t in texts:
        sc = script_of(t)
        if sc and available(sc) and SCRIPTS[sc][2] not in names:
            names.append(SCRIPTS[sc][2])
    return [n for n in names if (FONT_DIR / n).exists()]


def stage(dest, texts=()) -> str:
    """Copy the fonts these captions need into ``dest/capfonts``; returns the ``:fontsdir=capfonts`` filter option."""
    d = Path(dest) / "capfonts"
    d.mkdir(parents=True, exist_ok=True)
    for name in files_for(list(texts)):
        shutil.copy2(FONT_DIR / name, d / name)
    return ":fontsdir=capfonts"
