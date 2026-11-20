"""SRT generation for reels and full videos, and the ASS caption templates burned into reels.

Four caption templates (a project picks one; ``style["caption_template"]``):

  stacked   a few small words with ONE big keyword beneath (the original look)
  wordpop   one big word at a time, the previous word small above it
  karaoke   a 3-5 word line, the word being spoken in the brand colour
  classic   plain two-line subtitles at the bottom

All four use the real spoken time of every word when the transcript has word timestamps (faster-whisper); otherwise the
words of a line are spread over its time by length. Keyword highlights follow ``emphasis`` (names of God, Bible books and
verse numbers, the AI's key words for the clip, strong nouns and verbs; never filler). Captions stay inside the platform
safe area of the video format, and every line is set in the font of its own script (Poppins, or a Noto Sans font for
Indian-language captions - see ``capfonts``).
"""
from __future__ import annotations

import textwrap
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ruha.creative import formats
from ruha.services import capfonts, emphasis

TEMPLATES = ("stacked", "wordpop", "karaoke", "classic")
TEMPLATE_INFO = {
    "stacked": {"label": "Stacked", "note": "Small words over one big keyword - the original look."},
    "wordpop": {"label": "Word pop", "note": "One big word at a time, the previous word small above it."},
    "karaoke": {"label": "Karaoke", "note": "A 3-5 word line; the word being spoken lights up in the brand colour."},
    "classic": {"label": "Classic", "note": "Plain two-line subtitles at the bottom."},
}
DEFAULT_TEMPLATE = "stacked"


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


# ------------------------------------------------------------------ cues (with real word times when we have them)
def _seg_tokens(seg: dict, lo: float, hi: float) -> list:
    """[(token, start, end)] for the words of a segment that lie in [lo, hi]; [] when the segment has no word times."""
    out = []
    for w in seg.get("words") or []:
        try:
            s_, e_ = float(w["s"]), float(w["e"])
        except (KeyError, TypeError, ValueError):
            return []
        if lo <= (s_ + e_) / 2 <= hi:
            out += [(tok, s_, e_) for tok in str(w.get("w", "")).split()]
    return out


def build_cues(segments, start: float, end: float, max_chars: int = 28) -> list:
    """Return [(start, end, text)] relative to ``start`` for the window [start, end].

    A segment with word timestamps is cut into cues at word boundaries with the words' own times (so a caption appears
    when its first word is spoken); one without is cut by length and the time shared out in proportion."""
    cues = []
    for seg in segments:
        s_start, s_end = float(seg["start"]), float(seg["end"])
        if s_end <= start or s_start >= end:
            continue
        a, b = max(s_start, start), min(s_end, end)
        toks = _seg_tokens(seg, start, end)
        if toks:
            i = 0
            for piece in _chunks(" ".join(t for t, _, _ in toks), max_chars):
                n = len(piece.split())
                ws = toks[i:i + n]
                i += n
                if ws:
                    cues.append((max(0.0, ws[0][1] - start), max(0.0, ws[-1][2] - start),
                                 "\n".join(textwrap.wrap(piece, max_chars)) or piece))
            continue
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


def clip_words(segments, start: float, end: float) -> list:
    """[{"w", "s", "e"}] - every transcript word of [start, end] with its spoken time relative to ``start``."""
    out = []
    for seg in segments:
        if float(seg["end"]) <= start or float(seg["start"]) >= end:
            continue
        for w in seg.get("words") or []:
            try:
                s_, e_ = float(w["s"]), float(w["e"])
            except (KeyError, TypeError, ValueError):
                continue
            t = str(w.get("w", "")).strip()
            if t and start <= (s_ + e_) / 2 <= end:
                out.append({"w": t, "s": round(max(0.0, s_ - start), 3), "e": round(max(0.0, min(e_, end) - start), 3)})
    return out


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


def cues_to_srt(cues, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = [f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1)]
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# ------------------------------------------------------------------ words with times
@dataclass
class W:
    """A caption word with its spoken time (seconds into the clip). ``ref``: a whole Bible reference kept as one unit."""
    t: str
    s: float
    e: float
    ref: bool = False


def _estimate(tokens: list, a: float, b: float) -> list:
    """Spread the words over [a, b] in proportion to their length."""
    weights = [len(t) + 2 for t in tokens]
    total, t0, out = sum(weights) or 1, a, []
    for tok, w in zip(tokens, weights):
        d = (b - a) * w / total
        out.append(W(tok, t0, t0 + d))
        t0 += d
    return out


def merge_refs(ws: list) -> list:
    """Words of a Bible reference ("John 3:16", "1 John 4 verse 8") become ONE unit, so it is never split across phrases."""
    spans = emphasis.reference_spans([w.t for w in ws])
    if not spans:
        return ws
    out, i = [], 0
    for a, b in spans:
        out += ws[i:a]
        out.append(W(" ".join(w.t for w in ws[a:b]), ws[a].s, ws[b - 1].e, True))
        i = b
    return out + ws[i:]


def timeline(cues, words=None) -> list:
    """[(start, end, [W])]: every cue's words with a time each. The real spoken times are used when the cue is the
    transcript's own words (matched one by one); a translated cue, or a transcript without word times, gets the words
    spread over the cue by length."""
    pool = [w if isinstance(w, W) else W(str(w["w"]).strip(), float(w["s"]), float(w["e"])) for w in (words or [])]
    out, ptr = [], 0
    for a, b, text in cues:
        toks = text.replace("\n", " ").split()
        if not toks:
            continue
        while ptr < len(pool) and pool[ptr].s < a - 0.25:
            ptr += 1
        cand = pool[ptr:ptr + len(toks)]
        if len(cand) == len(toks) and all(emphasis.norm(c.t) == emphasis.norm(t) for c, t in zip(cand, toks)):
            ws = [W(t, max(a, c.s), max(min(b, c.e), max(a, c.s) + 0.04)) for t, c in zip(toks, cand)]
            ptr += len(toks)
        else:
            ws = _estimate(toks, a, b)
        out.append((a, b, merge_refs(ws)))
    return out


def _cue_scores(ws: list, keys) -> list:
    """The highlight score of every word of a cue (see ``emphasis``); a Bible reference scores as a whole."""
    llm = emphasis.llm_keys(keys)
    nums = emphasis.number_after_reference([w.t for w in ws])
    out = []
    for i, w in enumerate(ws):
        if w.ref or i in nums:
            out.append(90)
            continue
        raw = w.t.strip(emphasis.TRIM)
        out.append(emphasis.score(w.t, llm=llm, mid_capital=bool(raw[:1].isupper()) and i > 0))
    return out


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
    """The word that gets the big treatment (also used for cutaway cards): the best highlight word of the phrase;
    when nothing is meaningful, the last word (a card needs *a* word; captions use ``emphasis.pick`` and show none)."""
    sc = emphasis.word_scores(words)
    cand = [i for i, s in enumerate(sc) if s >= emphasis.MIN_SCORE]
    return max(cand, key=lambda i: (sc[i], i)) if cand else len(words) - 1


# ------------------------------------------------------------------ ASS plumbing
B = chr(92)                                   # backslash: ASS override tags
POP = "{" + B + "an2" + B + "fad(110,70)" + B + "fscx86" + B + "fscy86" + B + "t(0,170," + B + "fscx100" + B + "fscy100)}"
NL = B + "N"
FADE = "{" + B + "an2" + B + "fad(60,40)}"


def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    cs = int(round(sec * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    c = (hex_color or "#FFFFFF").lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    return f"&H{alpha:02X}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _bgr(hex_color: str) -> str:
    c = (hex_color or "#FFFFFF").lstrip("#")
    c = c if len(c) == 6 else "FFFFFF"
    return (c[4:6] + c[2:4] + c[0:2]).upper()


def _is_latin(text: str) -> bool:
    return all(ord(ch) < 0x0250 or 0x2000 <= ord(ch) < 0x2100 for ch in text)


def _legible(hex_color: str, min_lum: float = 0.18) -> str:
    """The colour, lightened toward white just enough to read against the dark halo behind captions (a deep brand purple
    would otherwise disappear); bright colours are left alone."""
    c = (hex_color or "#FFFFFF").lstrip("#")
    if len(c) != 6:
        return "#FFFFFF"
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))

    def lum(rgb):
        f = lambda v: (v / 255 / 12.92) if v / 255 <= 0.03928 else (((v / 255) + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2])
    for step in range(0, 11):
        k = step / 10
        rgb = tuple(int(v + (255 - v) * k * 0.7) for v in (r, g, b))
        if lum(rgb) >= min_lum:
            return "#%02X%02X%02X" % rgb
    return "#FFFFFF"


def _tw(text: str, fs: float, bold: bool = True) -> float:
    """Estimated width in pixels of ``text`` at font size ``fs`` (Indian-script vowel signs add little width)."""
    w = 0.0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me"):
            continue
        if cat == "Mc":
            w += 0.25
        elif ch == " ":
            w += 0.3
        elif ord(ch) >= 0x0250:
            w += 0.72
        elif ch.isupper() or ch.isdigit():
            w += 0.66
        else:
            w += 0.6 if bold else 0.56
    return w * fs


def _fit(text: str, fs: float, maxw: float, bold: bool = True) -> int:
    """The font size, at most ``fs``, at which ``text`` fits in ``maxw`` pixels."""
    wid = _tw(text, fs, bold)
    return max(10, int(fs * min(1.0, maxw / wid))) if wid else int(fs)


def safe_margins(style: dict | None, width: int, height: int) -> tuple:
    """(left, top, right, bottom) pixels captions must stay inside of: the style's own ``safe`` (set per video format), or the
    nearest format's platform safe area scaled to the frame."""
    st = style or {}
    s = st.get("safe")
    if isinstance(s, (list, tuple)) and len(s) == 4:
        return tuple(int(v) for v in s)
    return formats.safe_for_size((width, height))


def _head(width: int, height: int, safe: tuple, extra_style: str = "") -> str:
    l, t, r, b = safe
    return (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{capfonts.LATIN['small'][0]},60,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,0,0,2,{l},{r},{b},1\n"
        f"{extra_style}\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def _layer_tags(layer: int, colour: str, k: float) -> str:
    """Override tags of the three drawing layers: dark halo (readability on bright pictures), coloured glow, crisp letters."""
    if layer == 0:
        return f"{B}1a&HFF&{B}bord{int(18 * k)}{B}blur{int(16 * k)}{B}3c&H000000&{B}3a&H78&"
    if layer == 1:
        return f"{B}1a&HFF&{B}bord{int(9 * k)}{B}blur{int(9 * k)}{B}3c&H{_bgr(colour)}&{B}3a&H70&"
    return f"{B}1c&H{_bgr(colour)}&{B}1a&H00&{B}bord0{B}blur0.4{B}shad{max(1, int(3 * k))}{B}4c&H000000&{B}4a&H90&"


def _run(txt: str, role: str, fs: int, colour: str, layer: int, k: float, extra: str = "") -> str:
    return "{" + f"{B}fn{capfonts.family_for(txt, role)}{B}fs{fs}{_layer_tags(layer, colour, k)}{extra}" + "}" + txt


def _write(path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")
    return path


def _phrase_times(phrases: list, a: float, b: float) -> list:
    """(start, end) of each phrase: from its first word to the start of the next phrase (the last one to the cue's end)."""
    out = []
    for i, ph in enumerate(phrases):
        s = a if i == 0 else ph[0][0].s
        e = phrases[i + 1][0][0].s if i + 1 < len(phrases) else max(b, ph[-1][0].e)
        out.append((s, max(e, s + 0.12)))
    return out


# ------------------------------------------------------------------ template: stacked
def _ass_stacked(tl: list, path, width, height, st: dict, accent: str) -> Path:
    """Small supporting words on top, ONE big keyword beneath, glow; white and gold keywords alternate."""
    size = int(st.get("size", 78))
    l, t, r, b = safe_margins(st, width, height)
    maxw, availh = width - l - r, height - t - b
    small_fs, gold = int(size * 0.72), (accent or "#F2B84B")
    lines, n = [], 0
    for a, bcue, ws in tl:
        sc = _cue_scores(ws, st.get("key_words"))
        phrases = _groups(list(zip(ws, sc)), 4)
        for ph, (s, e) in zip(phrases, _phrase_times(phrases, a, bcue)):
            words, scs = [w.t for w, _ in ph], [x for _, x in ph]
            joined = " ".join(words)
            latin = _is_latin(joined)
            cand = [i for i, x in enumerate(scs) if x >= emphasis.MIN_SCORE]
            k_idx = max(cand, key=lambda i: (scs[i], i)) if cand else None
            if k_idx is None:                                                   # nothing meaningful: plain small words
                fs = min(_fit(joined, size * 0.95, maxw, False), int(availh / 1.3))
                for layer in (0, 2):
                    lines.append(f"Dialogue: {layer},{_ass_time(s)},{_ass_time(e)},Cap,,0,0,0,,{POP}"
                                 + _run(joined, "small", fs, "FFFFFF", layer, fs / 78))
                continue
            pre, key, post = " ".join(words[:k_idx]), words[k_idx].strip(emphasis.TRIM), " ".join(words[k_idx + 1:])
            kfs = _fit(key, size * (2.0 if latin else 1.7), maxw)
            pfs = _fit(pre, small_fs, maxw, False) if pre else small_fs
            qfs = _fit(post, small_fs, maxw, False) if post else small_fs
            est = kfs * 1.2 + (pfs * 1.25 if pre else 0) + (qfs * 1.25 if post else 0)
            if est > availh:                                                    # never taller than the safe area
                f = availh / est
                kfs, pfs, qfs = int(kfs * f), int(pfs * f), int(qfs * f)
            col = "FFFFFF" if n % 2 == 0 else gold
            n += 1
            for layer in (0, 1, 2):
                k = kfs / 156
                parts = []
                if pre:
                    parts.append(_run(pre, "small", pfs, "FFFFFF", layer, k))
                parts.append(_run(key, "big", kfs, col, layer, k))
                if post:
                    parts.append(_run(post, "small", qfs, "FFFFFF", layer, k))
                lines.append(f"Dialogue: {layer},{_ass_time(s)},{_ass_time(e)},Cap,,0,0,0,,{POP}{NL.join(parts)}")
    return _write(path, _head(width, height, (l, t, r, b)) + "\n".join(lines) + "\n")


# ------------------------------------------------------------------ template: word pop
def _ass_wordpop(tl: list, path, width, height, st: dict, accent: str) -> Path:
    """One big word at a time, the previous word small above it. Keywords take the accent colour; filler is smaller."""
    size = int(st.get("size", 78))
    l, t, r, b = safe_margins(st, width, height)
    maxw, availh = width - l - r, height - t - b
    small_fs, gold = int(size * 0.55), (accent or "#F2B84B")
    flat = []                                                                    # (word, score, cue index)
    for ci, (a, bcue, ws) in enumerate(tl):
        flat += [(w, s, ci) for w, s in zip(ws, _cue_scores(ws, st.get("key_words")))]
    lines = []
    for i, (w, s, ci) in enumerate(flat):
        nxt = flat[i + 1] if i + 1 < len(flat) else None
        end = (nxt[0].s if nxt[0].s - w.e < 0.4 else w.e + 0.2) if nxt else w.e + 0.35
        end = max(end, w.s + 0.12)
        prev = flat[i - 1] if i > 0 and flat[i - 1][2] == ci and w.s - flat[i - 1][0].e < 0.8 else None
        word = w.t.strip(emphasis.TRIM) or w.t
        key = s >= emphasis.MIN_SCORE
        big = int(size * (2.1 if _is_latin(word) else 1.8) * (1.0 if key else 0.8))
        bfs = _fit(word, big, maxw)
        pfs = _fit(prev[0].t, small_fs, maxw, False) if prev else small_fs
        est = bfs * 1.2 + (pfs * 1.3 if prev else 0)
        if est > availh:
            f = availh / est
            bfs, pfs = int(bfs * f), int(pfs * f)
        col = gold if key else "FFFFFF"
        for layer in (0, 1, 2) if key else (0, 2):
            k = bfs / 156
            parts = []
            if prev:
                parts.append(_run(prev[0].t.strip(emphasis.TRIM) or prev[0].t, "small", pfs, "D9D9D9", layer, k))
            parts.append(_run(word, "big", bfs, col, layer, k))
            lines.append(f"Dialogue: {layer},{_ass_time(w.s)},{_ass_time(end)},Cap,,0,0,0,,{POP}{NL.join(parts)}")
    return _write(path, _head(width, height, (l, t, r, b)) + "\n".join(lines) + "\n")


# ------------------------------------------------------------------ template: karaoke
def _balanced_lines(texts: list, fs: float, maxw: float) -> list:
    """Indexes where a 3-5 word caption line breaks onto a second line, when one line is too wide (at the middle)."""
    if _tw(" ".join(texts), fs) <= maxw or len(texts) < 2:
        return []
    best = min(range(1, len(texts)), key=lambda i: abs(len(" ".join(texts[:i])) - len(" ".join(texts[i:]))))
    return [best]


def _ass_karaoke(tl: list, path, width, height, st: dict, accent: str) -> Path:
    """A 3-5 word line; the word being spoken is in the brand colour, the rest white."""
    size = int(st.get("size", 78))
    l, t, r, b = safe_margins(st, width, height)
    maxw, availh = width - l - r, height - t - b
    brand = _legible(st.get("brand_color") or accent or "#F2B84B")
    lines = []
    for a, bcue, ws in tl:
        groups = _groups(ws, 5)
        for gi, g in enumerate(groups):
            texts = [w.t for w in g]
            latin = _is_latin(" ".join(texts))
            base = size * (1.05 if latin else 0.95)
            fs = _fit(" ".join(texts), base, maxw * 1.0)
            brk = _balanced_lines(texts, fs, maxw)
            if brk:                                                              # two lines: fit the longer one
                longest = max((" ".join(texts[:brk[0]]), " ".join(texts[brk[0]:])), key=len)
                fs = _fit(longest, base, maxw)
            fs = min(fs, int(availh / (2.6 if brk else 1.4)))
            nxt_start = groups[gi + 1][0].s if gi + 1 < len(groups) else None
            g_end = min(g[-1].e + 0.18, nxt_start) if nxt_start is not None else max(bcue, g[-1].e)
            for k, w in enumerate(g):
                s = g[0].s if k == 0 else w.s
                e = g[k + 1].s if k + 1 < len(g) else g_end
                e = max(e, s + 0.1)
                for layer in (0, 2):
                    runs = []
                    for j, x in enumerate(g):
                        col = brand if j == k else "FFFFFF"
                        grow = f"{B}fscx110{B}fscy110" if j == k else ""
                        runs.append(_run(x.t, "big", fs, col, layer, fs / 78, grow))
                        if j + 1 < len(g):
                            runs.append(NL if brk and j + 1 == brk[0] else " ")
                    lines.append(f"Dialogue: {layer},{_ass_time(s)},{_ass_time(e)},Cap,,0,0,0,,{FADE}{''.join(runs)}")
    return _write(path, _head(width, height, (l, t, r, b)) + "\n".join(lines) + "\n")


# ------------------------------------------------------------------ template: classic
def _wrap_px(texts: list, fs: float, maxw: float) -> list:
    """Greedy line breaks by pixel width: [[words of line 1], [words of line 2], ...]."""
    lines, cur = [], []
    for tx in texts:
        if cur and _tw(" ".join(cur + [tx]), fs, False) > maxw:
            lines.append(cur)
            cur = [tx]
        else:
            cur.append(tx)
    return lines + ([cur] if cur else [])


def _ass_classic(tl: list, path, width, height, st: dict, accent: str) -> Path:
    """Plain subtitles: white text with a dark outline, at most two lines at the bottom (inside the safe area)."""
    l, t, r, b = safe_margins(st, width, height)
    maxw, availh = width - l - r, height - t - b
    fs = max(24, int(int(st.get("size", 78)) * 0.8))
    prim, outl = _bgr(st.get("primary", "#FFFFFF")), _bgr(st.get("outline_color", "#000000"))
    lines = []
    for a, bcue, ws in tl:
        lns = _wrap_px([w.t for w in ws], fs, maxw)
        pages = [lns[i:i + 2] for i in range(0, len(lns), 2)]
        counts = [sum(len(x) for x in p) for p in pages]
        idx, starts = 0, []
        for c in counts:
            starts.append(ws[idx].s if idx else a)
            idx += c
        for pi, pg in enumerate(pages):
            s = starts[pi]
            e = starts[pi + 1] if pi + 1 < len(pages) else max(bcue, ws[-1].e)
            body = NL.join(" ".join(x) for x in pg)
            fsz = fs
            if len(pg) * fs * 1.3 > availh:
                fsz = int(availh / (len(pg) * 1.3))
            tags = "{" + f"{B}an2{B}fad(60,40){B}fn{capfonts.family_for(body, 'small')}{B}fs{fsz}{B}1c&H{prim}&{B}3c&H{outl}&{B}bord{max(2, fsz // 11)}{B}shad2" + "}"
            lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(max(e, s + 0.12))},Cap,,0,0,0,,{tags}{body}")
    return _write(path, _head(width, height, (l, t, r, b)) + "\n".join(lines) + "\n")


BUILDERS = {"stacked": _ass_stacked, "wordpop": _ass_wordpop, "karaoke": _ass_karaoke, "classic": _ass_classic}


def template_of(style: dict | None) -> str:
    """The caption template a style asks for (``caption_template``; the old ``caption_style`` values still work)."""
    st = style or {}
    t = str(st.get("caption_template") or "").lower()
    if t in BUILDERS:
        return t
    return "classic" if str(st.get("caption_style", "")).lower() == "classic" else DEFAULT_TEMPLATE


def build_ass(cues, path, width: int = 1080, height: int = 1920, style: dict | None = None,
              accent: str = "#FFD166") -> Path:
    """Write the ASS caption file for ``cues`` [(start, end, text)] in the style's template (default: stacked).

    ``style`` may carry: ``caption_template``, ``words`` (the clip's transcript words with times, see ``clip_words``),
    ``key_words`` (the AI's key words for the clip), ``brand_color``, ``safe`` (left, top, right, bottom pixels), ``size``."""
    st = style or {}
    tl = timeline(list(cues), st.get("words"))
    return BUILDERS[template_of(st)](tl, path, int(width), int(height), st, accent)


def stage_fonts(dest, cues=()) -> str:
    """Copy the caption fonts these cues need next to the subtitle file (Poppins, plus a Noto Sans font for every Indian
    script that occurs); returns the ``:fontsdir`` filter option."""
    return capfonts.stage(dest, [c[2] for c in cues])
