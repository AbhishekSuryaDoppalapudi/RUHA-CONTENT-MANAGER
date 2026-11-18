"""SRT generation for reels and full videos."""
from __future__ import annotations

import textwrap
from pathlib import Path


def srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        sec, ms = sec + 1, 0
    h, m, s = int(sec // 3600), int(sec % 3600 // 60), int(sec % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _chunks(text: str, max_chars: int) -> list:
    """Split a long caption into <= 2 lines of max_chars, and several cues if needed."""
    text = " ".join(text.split())
    if not text:
        return []
    per_cue = max_chars * 2
    words = text.split(" ")
    cues, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > per_cue:
            cues.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        cues.append(cur)
    return cues


def build_cues(segments, start: float, end: float, max_chars: int = 28) -> list:
    """Return [(start, end, text)] relative to ``start`` for the window [start, end]."""
    cues = []
    for seg in segments:
        s_start, s_end = float(seg["start"]), float(seg["end"])
        if s_end <= start or s_start >= end:
            continue
        a, b = max(s_start, start), min(s_end, end)
        pieces = _chunks(seg["text"], max_chars)
        if not pieces or b <= a:
            continue
        total = sum(len(p) for p in pieces) or 1
        t = a
        for p in pieces:
            d = (b - a) * len(p) / total
            cues.append((t - start, t + d - start, "\n".join(textwrap.wrap(p, max_chars)) or p))
            t += d
    return cues


def build_srt(segments, start: float, end: float, path, max_chars: int = 28) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (a, b, text) in enumerate(build_cues(segments, start, end, max_chars), 1):
        lines.append(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def full_srt(segments, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for i, s in enumerate(segments, 1):
        out.append(f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text'].strip()}\n")
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# ------------------------- creative (ASS) subtitles ---------------------------
def cues_to_srt(cues, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = [f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1)]
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    cs = int(round(sec * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    c = (hex_color or "#FFFFFF").lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    return f"&H{alpha:02X}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _is_latin(text: str) -> bool:
    return all(ord(ch) < 0x0250 or 0x2000 <= ord(ch) < 0x2100 for ch in text)


def _emphasis(words: list, n: int = 2) -> set:
    """Indexes of the words to highlight: the longest meaningful ones."""
    order = sorted(range(len(words)), key=lambda i: -len(words[i].strip(".,!?;:'\"")))
    return {i for i in order[:n] if len(words[i].strip(".,!?;:'\"")) >= 5}


CC = chr(92) + "c"                       # ASS colour override prefix
NL = chr(92) + "N"                    # ASS hard line break
POP = "{" + chr(92) + "an2" + chr(92) + "fad(110,70)" + chr(92) + "fscx86" + chr(92) + "fscy86" + chr(92) + "t(0,170," + chr(92) + "fscx100" + chr(92) + "fscy100)}"


FONT_SMALL, FONT_KEY = "Poppins SemiBold", "Poppins ExtraBold"
FONT_FILES = ("Poppins-SemiBold.ttf", "Poppins-ExtraBold.ttf", "Poppins-Bold.ttf")
_STOP = set("""about with that this they them then when what have from were will your been there their would could
should because into than also just very some more most such only over under after before while where which these
those here does done"""
            .split())
_POWER = {"god", "lord", "love", "hope", "fear", "sin", "war", "pray", "fire", "life", "grace", "cross", "blood"}
_TRIM =".,!?;:'\"“”‘’()"


def stage_fonts(dest) -> str:
    """Copy the bundled caption fonts next to the subtitle file; returns the ``fontsdir`` filter option."""
    import shutil
    src = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    d = Path(dest) / "capfonts"
    d.mkdir(parents=True, exist_ok=True)
    for f in FONT_FILES:
        if (src / f).exists():
            shutil.copy2(src / f, d / f)
    return ":fontsdir=capfonts"


def _groups(words: list, per: int = 4) -> list:
    """Even-sized phrases of at most ``per`` words (a caption flashes a few words at a time)."""
    n = len(words)
    k = max(1, -(-n // per))
    base, extra = divmod(n, k)
    out, i = [], 0
    for g in range(k):
        size = base + (1 if g < extra else 0)
        out.append(words[i:i + size])
        i += size
    return [g for g in out if g]


def _key_index(words: list) -> int:
    """The word that gets the big treatment: the last meaningful word of the phrase."""
    plain = [w.strip(_TRIM).lower().replace("'", "").replace("’", "") for w in words]
    for i in range(len(words) - 1, -1, -1):
        if plain[i] in _POWER or (len(plain[i]) >= 5 and plain[i] not in _STOP):
            return i
    best = max(range(len(words)), key=lambda i: (len(plain[i]), i))        # nothing strong: the longest word
    return best


def _stack_cues(cues) -> list:
    """Split every cue into short phrases with their own time slice: [(start, end, [words])]."""
    out = []
    for a, b, text in cues:
        words = text.replace("\n", " ").split()
        if not words:
            continue
        groups = _groups(words)
        total = sum(len(" ".join(g)) for g in groups) or 1
        t = a
        for g in groups:
            d = (b - a) * len(" ".join(g)) / total
            out.append((t, t + d, g))
            t += d
    return out


def _build_ass_stack(cues, path, width, height, style, accent) -> Path:
    """Stacked captions: small supporting words on top, ONE big keyword beneath, glow, white/gold keywords alternating."""
    st = style or {}
    size = int(st.get("size", 78))
    margin_v = int(st.get("margin_v", 300))
    small_fs, key_fs = int(size * 0.72), int(size * 2.0)
    white, gold = "FFFFFF", (accent or "#F2B84B").lstrip("#")
    bgr = lambda h: h[4:6] + h[2:4] + h[0:2]
    maxw = width * 0.86
    B = chr(92)
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Stack,{FONT_SMALL},{small_fs},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,0,0,2,70,70,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for n, (a, b, words) in enumerate(_stack_cues(cues)):
        k = _key_index(words)
        pre, key, post = " ".join(words[:k]), words[k].strip(_TRIM), " ".join(words[k + 1:])
        col = white if n % 2 == 0 else gold
        kfs = int(min(key_fs, maxw / (0.64 * max(1, len(key)))))
        sfs_pre = int(min(small_fs, maxw / (0.60 * max(1, len(pre))))) if pre else small_fs
        sfs_post = int(min(small_fs, maxw / (0.60 * max(1, len(post))))) if post else small_fs

        def body(layer: int) -> str:
            def run(txt, font, fs, colour):
                if layer == 0:        # dark halo: keeps the words readable on bright pictures
                    t = f"{B}1a&HFF&{B}bord18{B}blur16{B}3c&H000000&{B}3a&H78&"
                elif layer == 1:      # coloured glow
                    t = f"{B}1a&HFF&{B}bord9{B}blur9{B}3c&H{bgr(colour)}&{B}3a&H70&"
                else:                 # the crisp letters
                    t = f"{B}1c&H{bgr(colour)}&{B}1a&H00&{B}bord0{B}blur0.4{B}shad3{B}4c&H000000&{B}4a&H90&"
                return "{" + f"{B}fn{font}{B}fs{fs}{t}" + "}" + txt
            parts = []
            if pre:
                parts.append(run(pre, FONT_SMALL, sfs_pre, white))
            parts.append(run(key, FONT_KEY, kfs, col))
            if post:
                parts.append(run(post, FONT_SMALL, sfs_post, white))
            return (B + "N").join(parts)

        for layer in (0, 1, 2):
            lines.append(f"Dialogue: {layer},{_ass_time(a)},{_ass_time(b)},Stack,,0,0,0,,{POP}{body(layer)}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def build_ass(cues, path, width: int = 1080, height: int = 1920, style: dict | None = None,
              accent: str = "#FFD166") -> Path:
    """Creative captions. Latin text uses the stacked 'small words + one big keyword' look; other scripts keep
    the classic bold style (their fonts are different). ``caption_style: classic`` forces the classic look."""
    st = style or {}
    cues = list(cues)
    if st.get("caption_style", "stack") == "stack" and cues and all(_is_latin(t) for _, _, t in cues):
        return _build_ass_stack(cues, path, width, height, st, accent)
    size = int(st.get("size", 78))
    margin_v = int(st.get("margin_v", 300))
    font_latin = st.get("font", "Segoe UI Black")
    font_indic = st.get("indic_font", "Nirmala UI")
    primary = _ass_color(st.get("primary", "#FFFFFF"))
    outline = _ass_color(st.get("outline_color", "#000000"))
    hl = _ass_color(accent)
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Latin,{font_latin},{size},{primary},&H000000FF,{outline},&H96000000,-1,0,0,0,100,100,1,0,1,6,3,2,70,70,{margin_v},1\n"
        f"Style: Indic,{font_indic},{int(size * 0.92)},{primary},&H000000FF,{outline},&H96000000,-1,0,0,0,100,100,0,0,1,6,3,2,70,70,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for a, b, text in cues:
        latin = _is_latin(text)
        flat = text.replace("\n", " ")
        words = flat.split(" ")
        show = [w.upper() for w in words] if latin else words
        keys = _emphasis(words)
        parts = [("{" + CC + hl + "&}" + w + "{" + CC + primary + "&}" if i in keys else w) for i, w in enumerate(show)]
        # re-wrap onto at most two balanced lines
        half = max(1, len(parts) // 2) if len(flat) > 26 else len(parts)
        body = " ".join(parts[:half]) + ((NL + " ".join(parts[half:])) if half < len(parts) else "")
        fx = POP
        lines.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},{'Latin' if latin else 'Indic'},,0,0,0,,{fx}{body}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8-sig")
    return path
