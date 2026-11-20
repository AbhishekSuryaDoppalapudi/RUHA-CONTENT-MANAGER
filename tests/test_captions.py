"""Captions: meaningful keyword highlights, four templates (stacked / word pop / karaoke / classic), real word timestamps,
platform safe areas and Noto Sans fonts for Indian scripts."""
import re
import shutil
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from ruha.ai import whisper as W
from ruha.creative import formats
from ruha.services import capfonts, emphasis, media, planner, subtitles
from ruha.services.subtitles import W as Word

ROOT = Path(__file__).resolve().parents[1]
B = chr(92)


# =============================================================== helpers
def parse_ass(text):
    """[(layer, start_s, end_s, style, body)] of every Dialogue line."""
    out = []
    for m in re.finditer(r"^Dialogue: (\d+),(\d+):(\d\d):(\d\d)\.(\d\d),(\d+):(\d\d):(\d\d)\.(\d\d),(\w+),[^,]*,\d+,\d+,\d+,[^,]*,(.*)$", text, re.M):
        g = m.groups()
        f = lambda h, mi, s, cs: int(h) * 3600 + int(mi) * 60 + int(s) + int(cs) / 100
        out.append((int(g[0]), f(*g[1:5]), f(*g[5:9]), g[9], g[10]))
    return out


def ass_of(cues, tmp_path, template, width=1080, height=1920, **style):
    p = subtitles.build_ass(cues, tmp_path / f"{template}.ass", width, height, {"caption_template": template, **style}, accent="#F2B84B")
    return p.read_text(encoding="utf-8-sig")


def crisp(txt):
    return [e for e in parse_ass(txt) if e[0] == 2]


def runs(body):
    """[(font, size, text)] of the {\\fn..\\fs..} runs of a dialogue body."""
    return [(m[0], int(m[1]), m[2].replace(B + "N", "").strip()) for m in re.findall(r"\{[^}]*?\\fn([^\\}]+)\\fs(\d+)[^}]*\}([^{]*)", body)]


def words_at(times, texts):
    return [{"w": t, "s": s, "e": e} for t, (s, e) in zip(texts, times)]


def spoken(sentence, start=0.0, per=0.4):
    toks = sentence.split()
    return [{"w": t, "s": round(start + i * per, 3), "e": round(start + i * per + per * 0.8, 3)} for i, t in enumerate(toks)]


# =============================================================== keyword highlights (emphasis.py)
def pick(sentence, keys=None):
    w = sentence.split()
    return [w[i] for i in emphasis.pick(w, keys)]


def test_names_of_god_come_first():
    assert pick("the grace of God and the cross") == ["God"]
    assert pick("Jesus is Lord") == ["Lord"]                                         # tie: the later word
    assert pick("trust in the Spirit of the living God today") == ["God"]
    assert emphasis.word_scores("God grace fortress".split(), ["fortress"]) == [100, 60, 80]
    assert pick("I love my Father in heaven") == ["Father"]                           # a capital in mid-sentence marks God
    assert pick("my father went to the market") != ["father"]                         # an ordinary father is just a noun


def test_bible_books_and_verse_numbers():
    assert emphasis.reference_spans("As John 3:16 says".split()) == [(1, 3)]
    assert emphasis.reference_spans("read 1 John 4 verse 8 today".split()) == [(1, 6)]
    assert emphasis.reference_spans("Romans chapter 8 verse 28 is great".split()) == [(0, 5)]
    assert emphasis.reference_spans("in the book of Ruth".split()) == [(4, 5)]
    assert emphasis.reference_spans("the Psalms say".split()) == [(1, 2)]
    assert emphasis.reference_spans("my friend John went home".split()) == []          # a name, no chapter after it
    assert emphasis.reference_spans("Mark my words".split()) == []
    assert emphasis.reference_spans("second Corinthians 5".split()) == [(0, 3)]
    assert emphasis.number_after_reference("see chapter 3 verse 16 now".split()) == {2, 4}
    sc = emphasis.word_scores("read John 3:16 today".split())
    assert sc[1] == sc[2] == 90 and sc[0] < 25 and sc[3] == 25
    assert pick("turn to chapter 3 verse 16") == ["16"]                                # the verse number


def test_priority_order():
    god, ref, llm, strong, plain = (emphasis.score("God"), emphasis.score("x", ref=True), emphasis.score("fortress", llm={"fortress"}),
                                    emphasis.score("forgiveness"), emphasis.score("market"))
    assert god > ref > llm > strong > plain >= emphasis.MIN_SCORE
    assert (god, ref, llm, strong, plain) == (100, 90, 80, 60, 25)


def test_the_models_key_words_outrank_strong_and_plain_words_but_not_names():
    assert pick("he is my fortress and refuge", ["fortress"]) == ["fortress"]
    assert pick("he is my fortress and refuge") == ["refuge"]                           # no key: the later content word
    assert pick("grace is my fortress", ["fortress"]) == ["fortress"]                   # the AI's pick beats strong vocabulary ...
    assert pick("grace is my fortress") == ["grace"]                                    # with no AI pick, strong vocabulary wins
    assert pick("grace and refuge") == ["grace"]                                        # ... which beats an ordinary word
    assert pick("God is my fortress", ["fortress"]) == ["God"]                          # but never a name of God


def test_meaning_not_length():
    assert pick("absolutely incredible forgiveness") == ["forgiveness"]                 # not the longest word
    assert pick("unfortunately everything basically happened") == ["happened"]          # -ly adverbs and "everything" are filler
    assert pick("persecuting people") == ["people"]
    assert pick("the New Testament") == ["Testament"]


def test_filler_is_never_highlighted():
    for phrase in ("and then we went to", "just really very basically", "you know what I mean", "he'll don't I'm they're",
                   "so um yeah okay right", "the of and in"):
        assert pick(phrase) == [], phrase
    for w in ("the", "and", "just", "really", "basically", "actually", "he'll", "gonna", "okay", "quickly", "thing"):
        assert emphasis.is_filler(w) and emphasis.score(w, llm={w}) == 0, w            # even a model hint cannot make filler a keyword
    assert not emphasis.is_filler("family") and not emphasis.is_filler("holy")        # -ly words that are real content
    assert pick("12 people came") == ["people"]                                         # a bare number is not a keyword


def test_strong_words_in_any_form():
    for w in ("loved", "loving", "healed", "healing", "prayers", "forgiveness", "forgiven", "surrender"):
        assert emphasis.is_strong(w), w
    assert not emphasis.is_strong("hello")
    assert pick("so loved the world") == ["loved"]


def test_grounded_and_automatic_keys():
    text = "Grace and faith change everything, even your fear"
    assert emphasis.grounded_keys(["Grace", "forgiveness", "the", "invented", "faith", "faith"], text) == ["grace", "faith"]
    assert emphasis.grounded_keys(["a", "b", "c", "d", "e"], "a b c d e") == []                        # filler / too short
    assert len(emphasis.grounded_keys(["grace", "faith", "fear", "change", "everything"], text)) <= 4
    assert emphasis.grounded_keys(None, text) == [] and emphasis.grounded_keys("grace", text) == ["grace"]
    auto = emphasis.auto_keys("the Holy Spirit gave grace and forgiveness today")
    assert 1 <= len(auto) <= 3 and all(not emphasis.is_filler(a) for a in auto)
    assert emphasis.auto_keys("and then we went to the") == []


# =============================================================== fonts per script (capfonts.py)
SAMPLES = {
    "devanagari": "प्रभु यीशु मसीह हमारा उद्धारकर्ता है", "bengali": "প্রভু যীশু আমাদের ত্রাণকর্তা", "gurmukhi": "ਪ੍ਰਭੂ ਯਿਸੂ ਸਾਡਾ ਮੁਕਤੀਦਾਤਾ ਹੈ",
    "gujarati": "પ્રભુ ઈસુ આપણા તારણહાર છે", "oriya": "ପ୍ରଭୁ ଯୀଶୁ ଆମର ତ୍ରାଣକର୍ତ୍ତା", "tamil": "கர்த்தராகிய இயேசு நம் இரட்சகர்",
    "telugu": "ప్రభువైన యేసు మన రక్షకుడు", "kannada": "ಕರ್ತನಾದ ಯೇಸು ನಮ್ಮ ರಕ್ಷಕ", "malayalam": "കർത്താവായ യേശു നമ്മുടെ രക്ഷകൻ",
    "arabic": "الرب يسوع هو مخلصنا",
}


@pytest.mark.parametrize("script", list(SAMPLES))
def test_each_script_gets_its_noto_font(script):
    text = SAMPLES[script]
    assert capfonts.script_of(text) == script and capfonts.available(script)
    assert capfonts.family_for(text) == f"Noto Sans {script.capitalize()}"
    assert capfonts.SCRIPTS[script][2] in capfonts.files_for([text])


@pytest.mark.parametrize("script", list(SAMPLES))
def test_the_bundled_font_has_every_character_of_the_sample(script):
    fontTools = pytest.importorskip("fontTools.ttLib")
    cmap = fontTools.TTFont(capfonts.FONT_DIR / capfonts.SCRIPTS[script][2]).getBestCmap()
    missing = [c for c in SAMPLES[script] if not c.isspace() and ord(c) not in cmap]
    assert missing == [], f"{script}: no glyph for {missing}"


def test_the_family_name_inside_each_font_matches_what_the_captions_ask_for():
    from PIL import ImageFont
    for script, (_r, family, fname) in capfonts.SCRIPTS.items():
        assert ImageFont.truetype(str(capfonts.FONT_DIR / fname), 20).getname()[0] == family, script
    for role, (family, fname) in capfonts.LATIN.items():
        assert ImageFont.truetype(str(capfonts.FONT_DIR / fname), 20).getname()[0] == family.split(" ")[0] or family.startswith("Poppins")


def test_latin_text_uses_poppins_and_mixed_lines_follow_their_main_script():
    assert capfonts.script_of("Grace is free") is None and capfonts.family_for("Grace is free") == "Poppins SemiBold"
    assert capfonts.family_for("Grace is free", "big") == "Poppins ExtraBold"
    assert capfonts.script_of("Hello మిత్రమా మిత్రమా దేవుడు") == "telugu"
    assert capfonts.script_of("") is None and capfonts.script_of("1234 !!") is None


def test_only_the_needed_fonts_are_staged(tmp_path):
    files = capfonts.files_for(["Grace", SAMPLES["telugu"]])
    assert "NotoSansTelugu.ttf" in files and "NotoSansTamil.ttf" not in files and "Poppins-ExtraBold.ttf" in files
    opt = capfonts.stage(tmp_path, ["Grace", SAMPLES["tamil"]])
    assert opt == ":fontsdir=capfonts"
    got = {p.name for p in (tmp_path / "capfonts").iterdir()}
    assert "NotoSansTamil.ttf" in got and "NotoSansTelugu.ttf" not in got and "Poppins-SemiBold.ttf" in got
    assert subtitles.stage_fonts(tmp_path / "x", [(0, 1, SAMPLES["bengali"])]) == ":fontsdir=capfonts"
    assert (tmp_path / "x" / "capfonts" / "NotoSansBengali.ttf").exists()


def test_a_script_without_a_bundled_font_falls_back_to_the_windows_font(monkeypatch):
    monkeypatch.setattr(capfonts, "FONT_DIR", Path("/nonexistent"))
    assert capfonts.family_for(SAMPLES["telugu"]) == capfonts.FALLBACK_FAMILY
    assert capfonts.files_for([SAMPLES["telugu"]]) == []
    santali = "ᱥᱟᱱᱛᱟᱲᱤ"                                                                         # Ol Chiki: no script entry at all
    assert capfonts.script_of(santali) is None


# =============================================================== cues and words
SEGS = [{"start": 10.0, "end": 14.0, "text": "God is good all the time and all the time God is good",
         "words": [{"w": " God", "s": 10.2, "e": 10.5}, {"w": " is", "s": 10.5, "e": 10.7}, {"w": " good", "s": 10.7, "e": 11.2},
                   {"w": " all", "s": 11.8, "e": 12.0}, {"w": " the", "s": 12.0, "e": 12.1}, {"w": " time", "s": 12.1, "e": 12.6},
                   {"w": " and", "s": 12.7, "e": 12.9}, {"w": " all", "s": 12.9, "e": 13.1}, {"w": " the", "s": 13.1, "e": 13.2},
                   {"w": " time", "s": 13.2, "e": 13.5}, {"w": " God", "s": 13.5, "e": 13.7}, {"w": " is", "s": 13.7, "e": 13.8},
                   {"w": " good.", "s": 13.8, "e": 14.0}]}]


def test_cues_follow_the_real_word_times():
    cues = subtitles.build_cues(SEGS, 10.0, 15.0, max_chars=14)
    assert len(cues) >= 2
    assert cues[0][0] == pytest.approx(0.2) and cues[-1][1] == pytest.approx(4.0)
    assert " ".join(c[2].replace("\n", " ") for c in cues) == "God is good all the time and all the time God is good."
    firsts = [c[0] for c in cues]
    assert firsts == sorted(firsts)
    n = len(cues[0][2].split())
    assert firsts[1] == pytest.approx(SEGS[0]["words"][n]["s"] - 10.0)                      # the next cue opens when ITS first word is spoken


def test_cues_without_word_times_are_spread_by_length():
    plain = [{"start": 10.0, "end": 14.0, "text": SEGS[0]["text"]}]
    cues = subtitles.build_cues(plain, 10.0, 15.0, max_chars=14)
    assert cues[0][0] == 0.0 and cues[-1][1] == pytest.approx(4.0)
    assert sum(b - a for a, b, _ in cues) == pytest.approx(4.0)


def test_clip_words_are_relative_to_the_clip():
    cw = subtitles.clip_words(SEGS, 10.0, 12.0)
    assert [w["w"] for w in cw] == ["God", "is", "good", "all", "the", "time"][:len(cw)] and cw[0]["s"] == pytest.approx(0.2)
    assert all(0 <= w["s"] <= w["e"] <= 2.0 for w in cw)
    assert subtitles.clip_words([{"start": 0, "end": 5, "text": "x"}], 0, 5) == []
    assert subtitles.clip_words(SEGS, 100, 110) == []


def test_timeline_uses_real_times_when_the_cue_is_the_transcripts_own_words():
    words = spoken("God is good all the time", 1.0, 0.5)
    tl = subtitles.timeline([(1.0, 4.0, "God is good\nall the time")], words)
    (a, b, ws), = tl
    assert [w.t for w in ws] == "God is good all the time".split() and [round(w.s, 2) for w in ws] == [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]


def test_timeline_spreads_translated_words_by_length():
    words = spoken("God is good", 1.0, 0.5)
    (a, b, ws), = subtitles.timeline([(1.0, 3.0, "దేవుడు మంచివాడు")], words)             # a translation: nothing to match
    assert len(ws) == 2 and ws[0].s == 1.0 and ws[-1].e == pytest.approx(3.0) and ws[0].e == pytest.approx(ws[1].s)
    (a, b, ws), = subtitles.timeline([(0, 2, "no words at all here")], None)
    assert ws[0].s == 0 and ws[-1].e == pytest.approx(2.0)


def test_timeline_keeps_cues_in_step_with_the_words():
    words = spoken("one two three four five six seven eight", 0.0, 0.5)
    tl = subtitles.timeline([(0.0, 2.0, "one two three four"), (2.0, 4.0, "five six seven eight")], words)
    assert [w.s for w in tl[1][2]] == [2.0, 2.5, 3.0, 3.5]
    odd = subtitles.timeline([(0.0, 2.0, "one two WRONG four"), (2.0, 4.0, "five six seven eight")], words)
    assert [w.s for w in odd[1][2]] == [2.0, 2.5, 3.0, 3.5]                           # one bad cue does not shift the next


def test_a_bible_reference_stays_in_one_piece():
    (a, b, ws), = subtitles.timeline([(0, 3, "read John 3:16 today")], spoken("read John 3:16 today", 0, 0.7))
    assert [w.t for w in ws] == ["read", "John 3:16", "today"] and ws[1].ref and ws[1].s == 0.7 and ws[1].e == pytest.approx(1.96)
    (a, b, ws), = subtitles.timeline([(0, 3, "the Psalms say")], None)
    assert [w.t for w in ws] == ["the", "Psalms", "say"] and ws[1].ref


# =============================================================== the four templates
TXT = "God is good all the time and all the time God is good"
CUE = [(0.0, 4.8, TXT)]


def test_template_selection_and_defaults(tmp_path):
    assert subtitles.template_of({}) == "stacked" and subtitles.template_of(None) == "stacked"
    assert subtitles.template_of({"caption_template": "KARAOKE"}) == "karaoke"
    assert subtitles.template_of({"caption_template": "fancy"}) == "stacked"
    assert subtitles.template_of({"caption_style": "classic"}) == "classic"            # the old setting still works
    assert subtitles.template_of({"caption_template": "wordpop", "caption_style": "classic"}) == "wordpop"
    assert set(subtitles.TEMPLATES) == set(subtitles.TEMPLATE_INFO) == set(subtitles.BUILDERS) == {"stacked", "wordpop", "karaoke", "classic"}
    for t in subtitles.TEMPLATES:
        assert subtitles.TEMPLATE_INFO[t]["label"] and subtitles.TEMPLATE_INFO[t]["note"]
    assert ass_of(CUE, tmp_path, "stacked") == subtitles.build_ass(CUE, tmp_path / "d.ass", 1080, 1920, {}, accent="#F2B84B").read_text(encoding="utf-8-sig")


def test_stacked_small_words_over_one_big_keyword(tmp_path):
    txt = ass_of([(0, 3, "As John 3:16 says God so loved the world")], tmp_path, "stacked", words=spoken("As John 3:16 says God so loved the world", 0, 0.35))
    ev = crisp(txt)
    r0, r1 = runs(ev[0][4]), runs(ev[1][4])
    big0 = [r for r in r0 if r[0] == "Poppins ExtraBold"]
    assert [r[2] for r in big0] == ["God"]                                              # God outranks the reference
    assert [r[2] for r in r0 if r[0] == "Poppins SemiBold"] == ["As John 3:16 says"]
    assert [r[2] for r in r1 if r[0] == "Poppins ExtraBold"] == ["loved"]                # strong verb, not the last/longest word
    assert min(r[1] for r in big0) > max(r[1] for r in r0 if r[0] == "Poppins SemiBold")
    assert 3 * len(ev) == len(parse_ass(txt)) and all(e[4].startswith("{" + B + "an2") for e in ev)


def test_stacked_shows_a_whole_reference_as_the_big_word(tmp_path):
    txt = ass_of([(0, 2, "read John 3:16 today")], tmp_path, "stacked", words=spoken("read John 3:16 today", 0, 0.5))
    big = [r[2] for e in crisp(txt) for r in runs(e[4]) if r[0] == "Poppins ExtraBold"]
    assert big == ["John 3:16"]


def test_stacked_gives_filler_phrases_no_highlight(tmp_path):
    txt = ass_of([(0, 4, "and then we went to the store just really"), (4, 6, "so I mean you know")], tmp_path, "stacked")
    ev = crisp(txt)
    assert all("Poppins ExtraBold" not in e[4] for e in ev if any(w in e[4] for w in ("you know", "I mean", "and then")))
    plain = [e for e in parse_ass(txt) if "you know" in e[4] or "I mean" in e[4]]
    assert plain and {e[0] for e in plain} == {0, 2}                                    # halo + crisp letters, no glow layer


def test_stacked_uses_the_models_key_words(tmp_path):
    with_key = crisp(ass_of([(0, 2, "He is my fortress and refuge")], tmp_path, "stacked", key_words=["fortress"]))
    without = crisp(ass_of([(0, 2, "He is my fortress and refuge")], tmp_path, "stacked"))
    big = lambda ev: [r[2] for e in ev for r in runs(e[4]) if r[0] == "Poppins ExtraBold"]
    assert big(with_key) == ["fortress"] and big(without) == ["refuge"]


def test_word_pop_one_big_word_with_the_previous_word_small_above(tmp_path):
    words = spoken("God is good all the time", 0.0, 0.5)
    txt = ass_of([(0.0, 3.0, "God is good all the time")], tmp_path, "wordpop", words=words)
    ev = crisp(txt)
    assert len(ev) == 6                                                                  # one event per spoken word
    assert [round(e[1], 2) for e in ev] == [w["s"] for w in words]                       # each appears exactly when spoken
    assert all(a[2] >= a[1] + 0.12 and b[1] >= a[1] for a, b in zip(ev, ev[1:]))
    big = [[r for r in runs(e[4]) if r[0] == "Poppins ExtraBold"] for e in ev]
    assert [b[0][2] for b in big] == ["God", "is", "good", "all", "the", "time"]
    assert B + "N" not in ev[0][4]                                                       # the first word has nothing above it
    for i in range(1, 6):
        small = [r for r in runs(ev[i][4]) if r[0] == "Poppins SemiBold"]
        assert [s[2] for s in small] == [words[i - 1]["w"]] and small[0][1] < big[i][0][1] and B + "N" in ev[i][4]


def test_word_pop_does_not_carry_a_word_across_a_pause(tmp_path):
    words = [{"w": "Hello", "s": 0.0, "e": 0.4}, {"w": "friends", "s": 2.5, "e": 3.0}]
    ev = crisp(ass_of([(0.0, 1.0, "Hello"), (2.5, 3.2, "friends")], tmp_path, "wordpop", words=words))
    assert len(ev) == 2 and B + "N" not in ev[1][4]


def test_word_pop_highlights_keywords_and_shrinks_filler(tmp_path):
    ev = crisp(ass_of([(0, 2, "the grace of God")], tmp_path, "wordpop", words=spoken("the grace of God", 0, 0.5)))
    size = {runs(e[4])[-1][2]: runs(e[4])[-1][1] for e in ev}
    assert size["the"] < size["God"] and size["of"] < size["grace"]
    assert "4BB8F2" in ev[3][4].upper()                                                  # God in the accent colour (BGR)
    assert "4BB8F2" not in ev[0][4].upper()                                              # "the" stays white


def test_karaoke_lights_up_the_spoken_word_in_the_brand_colour(tmp_path):
    sentence = "God is good all the time and all the time God is good"
    words = spoken(sentence, 0.0, 0.4)
    txt = ass_of([(0.0, 5.0, sentence)], tmp_path, "karaoke", words=words, brand_color="#7C3AED")
    ev = crisp(txt)
    legible = subtitles._legible("#7C3AED")
    bgr = legible[5:7] + legible[3:5] + legible[1:3]
    assert len(ev) == 13                                                                 # one event per word (the line changes as he speaks)
    for e in ev:
        assert e[4].count("&H" + bgr.upper() + "&") == 1                                 # exactly one word lit
    lines = {}
    for e in ev:
        lines.setdefault(tuple(r[2] for r in runs(e[4]) if r[2].strip()), []).append(e)
    assert all(3 <= len(k) <= 5 for k in lines)                                          # 3-5 words on screen at a time
    assert [round(e[1], 2) for e in ev] == [w["s"] for w in words]


def test_karaoke_words_light_up_in_order(tmp_path):
    sentence = "one two three four five"
    ev = crisp(ass_of([(0.0, 3.0, sentence)], tmp_path, "karaoke", words=spoken(sentence, 0, 0.5), brand_color="#FF0000"))
    legible = subtitles._legible("#FF0000")
    bgr = (legible[5:7] + legible[3:5] + legible[1:3]).upper()
    lit = []
    for e in ev:
        parts = re.findall(r"\{[^}]*1c&H([0-9A-F]{6})&[^}]*\}([^{]*)", e[4])
        lit += [t.strip() for c, t in parts if c == bgr]
    assert lit == ["one", "two", "three", "four", "five"]


def test_karaoke_breaks_a_wide_line_in_two(tmp_path):
    sentence = "transformation unbelievable forgiveness redemption"
    ev = crisp(ass_of([(0, 3, sentence)], tmp_path, "karaoke", width=270, height=480, size=40, words=spoken(sentence, 0, 0.7),
                      safe=(20, 68, 20, 108)))
    assert all(e[4].count(B + "N") == 1 for e in ev)


def test_classic_is_plain_two_line_subtitles(tmp_path):
    long = "this is a long caption that needs more than two lines of text to be shown on the screen so it is split into pages"
    ev = [e for e in parse_ass(ass_of([(0.0, 8.0, long)], tmp_path, "classic", words=spoken(long, 0, 0.3)))]
    assert len(ev) >= 2 and {e[0] for e in ev} == {0}
    assert all(e[4].count(B + "N") <= 1 for e in ev)                                     # never more than two lines
    assert all(a[2] <= b[1] + 1e-6 for a, b in zip(ev, ev[1:]))                          # pages follow each other
    body = " ".join(re.sub(r"\{[^}]*\}", "", e[4]).replace(B + "N", " ") for e in ev)
    assert body == long                                                                  # all the words, normal case, nothing dropped
    assert "ExtraBold" not in "".join(e[4] for e in ev) and "Poppins SemiBold" in ev[0][4]


def test_classic_keeps_short_captions_on_one_event(tmp_path):
    ev = parse_ass(ass_of([(1.0, 3.0, "Grace is free")], tmp_path, "classic"))
    assert len(ev) == 1 and ev[0][1] == 1.0 and ev[0][2] == 3.0


@pytest.mark.parametrize("template", ["stacked", "wordpop", "karaoke", "classic"])
@pytest.mark.parametrize("script", list(SAMPLES))
def test_every_template_sets_each_script_in_its_noto_font(tmp_path, template, script):
    text = SAMPLES[script]
    txt = ass_of([(0.0, 3.0, "Grace is free"), (3.0, 6.0, text)], tmp_path, template)
    fams = {r[0] for e in parse_ass(txt) for r in runs(e[4]) if any(ord(c) > 0x250 for c in r[2])}
    assert fams == {capfonts.SCRIPTS[script][1]}, (template, script, fams)
    assert any("Poppins" in e[4] for e in parse_ass(txt))                                 # the English line keeps Poppins


def test_indian_language_captions_get_a_highlight_and_no_uppercase(tmp_path):
    txt = ass_of([(0, 3, "దేవుని కృప అద్భుతమైనది")], tmp_path, "stacked")
    big = [r for e in crisp(txt) for r in runs(e[4]) if r[0] == "Noto Sans Telugu"]
    assert big
    hindi = ass_of([(0, 3, "यह है प्रभु की महिमा")], tmp_path, "stacked")
    assert "Noto Sans Devanagari" in hindi and "है" not in [r[2] for e in crisp(hindi) for r in runs(e[4]) if r[1] > 100]   # a function word is never the big one


# =============================================================== safe areas (real FFmpeg renders)
def ink_box(ass_file, size, t, tmp_path, texts, thr=90):
    """Render the captions onto black at time ``t`` and return the bounding box (x0, y0, x1, y1) of what is drawn."""
    from PIL import Image
    d = tmp_path / f"ink_{ass_file.stem}_{t}"
    d.mkdir(exist_ok=True)
    shutil.copy2(ass_file, d / "c.ass")
    opt = capfonts.stage(d, texts)
    png = d / "o.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={size[0]}x{size[1]}:r=25:d=12", "-vf",
                    f"ass=c.ass{opt}", "-ss", str(t), "-frames:v", "1", str(png)], cwd=d, check=True)
    a = np.asarray(Image.open(png).convert("RGB"))
    ys, xs = np.where(a.max(axis=2) > thr)
    return None if len(xs) == 0 else (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


SAFE_CUES = [(0.0, 4.0, "As John 3:16 says God so loved the world that he gave his only Son"),
             (4.0, 8.0, "దేవుని కృప అద్భుతమైనది ప్రభువు యేసు క్రీస్తు ద్వారా మనకు లభించింది"),
             (8.0, 12.0, "unbelievable transformation Revelation 22 forgiveness everlasting")]


@pytest.mark.parametrize("fid", list(formats.VIDEO_FORMATS))
@pytest.mark.parametrize("template", ["stacked", "wordpop", "karaoke", "classic"])
def test_captions_stay_inside_the_platform_safe_area(tmp_path, template, fid):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    size = formats.video_size(fid, 540)
    safe = formats.video_safe(fid, size)
    words = spoken(SAFE_CUES[0][2], 0.0, 0.25) + spoken(SAFE_CUES[1][2], 4.0, 0.25) + spoken(SAFE_CUES[2][2], 8.0, 0.25)
    p = subtitles.build_ass(SAFE_CUES, tmp_path / f"{template}_{fid.replace(':', 'x')}.ass", size[0], size[1],
                            {"caption_template": template, "safe": safe, "size": 39, "words": words}, accent="#F2B84B")
    times = [0.6, 1.5, 2.4, 3.5, 4.6, 5.3, 6.1, 8.3, 9.0, 9.6]                         # moments when somebody is speaking
    seen = 0
    for t in times:
        box = ink_box(p, size, t, tmp_path, [c[2] for c in SAFE_CUES])
        if box is None:
            continue
        seen += 1
        x0, y0, x1, y1 = box
        assert x0 >= safe[0] - 2 and x1 <= size[0] - safe[2] + 2, (template, fid, t, box, safe, size)
        assert y0 >= safe[1] - 2 and y1 <= size[1] - safe[3] + 2, (template, fid, t, box, safe, size)
    assert seen >= 6


def test_the_safe_area_comes_from_the_format_unless_the_style_sets_it():
    assert subtitles.safe_margins({}, 1080, 1920) == (80, 270, 80, 430)
    assert subtitles.safe_margins({}, 540, 960) == (40, 135, 40, 215)
    assert subtitles.safe_margins({}, 1920, 1080) == (96, 54, 96, 110)
    assert subtitles.safe_margins({"safe": (1, 2, 3, 4)}, 1080, 1920) == (1, 2, 3, 4)
    assert subtitles.safe_margins({"safe": "bad"}, 1080, 1080) == (80, 90, 80, 90)


def test_text_width_estimate_and_fitting():
    assert subtitles._tw("WWWW", 100) > subtitles._tw("iiii", 100) * 0.9
    assert subtitles._tw("క్రీస్తు", 100) < subtitles._tw("abcdefgh", 100)                  # vowel signs add little width
    assert subtitles._fit("short", 100, 1000) == 100
    f = subtitles._fit("a very long headline that cannot fit", 100, 300)
    assert 10 <= f < 100 and subtitles._tw("a very long headline that cannot fit", f) <= 300 + 1
    assert subtitles._legible("#FFFFFF") == "#FFFFFF" and subtitles._legible("#F2B84B") == "#F2B84B"
    dark = subtitles._legible("#7C3AED")
    assert dark != "#7C3AED" and dark.startswith("#") and len(dark) == 7 and subtitles._legible("bad") == "#FFFFFF"


# =============================================================== faster-whisper word timestamps
def fake_whisper(monkeypatch, calls):
    class Seg:
        def __init__(self, text, s, e, words):
            self.text, self.start, self.end, self.words = text, s, e, words

    class Wd:
        def __init__(self, w, s, e):
            self.word, self.start, self.end, self.probability = w, s, e, 0.9

    class Info:
        duration, language, language_probability = 4.0, "en", 0.99

    class Model:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kw):
            calls.append(kw)
            return iter([Seg(" God is good", 0.0, 2.0, [Wd(" God", 0.1, 0.5), Wd(" is", 0.5, 0.7), Wd(" good", 0.7, 1.2), Wd("  ", 1.2, 1.3)]),
                         Seg(" No words here", 2.0, 4.0, None)]), Info()

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(W, "cuda_available", lambda: False)


CFG = {"gpu.min_free_ram_gb": 1.0, "whisper.cpu_model": "small", "whisper.device": "auto", "gpu.mode": "auto", "whisper.language": "en"}


def test_whisper_returns_word_times(monkeypatch):
    calls = []
    fake_whisper(monkeypatch, calls)
    segs, _ = W.FasterWhisperTranscriber().transcribe("x.wav", CFG)
    assert calls[0]["word_timestamps"] is True                                           # on by default
    assert segs[0]["words"] == [{"w": " God", "s": 0.1, "e": 0.5}, {"w": " is", "s": 0.5, "e": 0.7}, {"w": " good", "s": 0.7, "e": 1.2}]
    assert "words" not in segs[1]                                                        # a segment without words stays valid


def test_word_timestamps_can_be_switched_off(monkeypatch):
    calls = []
    fake_whisper(monkeypatch, calls)
    W.FasterWhisperTranscriber().transcribe("x.wav", {**CFG, "whisper.word_timestamps": False})
    assert calls[0]["word_timestamps"] is False


def test_the_setting_exists_and_is_validated(client):
    assert client.get("/api/settings").json()["values"]["whisper.word_timestamps"] is True
    assert client.put("/api/settings", json={"values": {"whisper.word_timestamps": False}}).status_code == 200
    assert client.put("/api/settings", json={"values": {"whisper.word_timestamps": "yes"}}).status_code == 400


def test_migration_adds_the_word_column_without_touching_old_data(tmp_path, monkeypatch):
    from ruha import db as dbmod
    old = tmp_path / "migs"
    old.mkdir()
    shutil.copy2(dbmod.MIGRATIONS_DIR / "0001_initial.sql", old / "0001_initial.sql")
    monkeypatch.setattr(dbmod, "MIGRATIONS_DIR", old)
    database = dbmod.Database(tmp_path / "old.db")
    assert database.migrate() == ["0001_initial.sql"] and database.schema_version() == 1
    con = sqlite3.connect(tmp_path / "old.db")
    assert "words_json" not in [r[1] for r in con.execute("PRAGMA table_info(transcript_segments)")]
    con.close()
    monkeypatch.undo()
    database.dispose()
    database = dbmod.Database(tmp_path / "old.db")
    assert database.migrate() == ["0002_segment_words.sql"] and database.schema_version() == 2
    con = sqlite3.connect(tmp_path / "old.db")
    assert "words_json" in [r[1] for r in con.execute("PRAGMA table_info(transcript_segments)")]
    con.close()
    assert database.migrate() == []                                                       # and it is applied only once
    database.dispose()


# =============================================================== through the job pipeline
from tests.conftest import FakeTranscriber, make_segments  # noqa: E402


class WordTranscriber(FakeTranscriber):
    """Like the fake transcriber, but every segment carries word timestamps (as faster-whisper's would)."""
    def transcribe(self, audio_path, cfg, progress=None, log=None):
        segs, info = super().transcribe(audio_path, cfg, progress, log)
        out = []
        for s in segs:
            toks = s["text"].split()
            step = (s["end"] - s["start"]) / len(toks)
            out.append({**s, "words": [{"w": f" {t}", "s": round(s["start"] + i * step, 3), "e": round(s["start"] + (i + 0.9) * step, 3)}
                                       for i, t in enumerate(toks)]})
        return out, info


@pytest.fixture()
def spy(monkeypatch):
    """Record what the reel renderer hands to the caption builder (and still build the real file)."""
    seen = []
    real = media.build_ass

    def build(cues, path, width=1080, height=1920, style=None, accent="#FFD166"):
        seen.append({"cues": list(cues), "size": (width, height), "style": dict(style or {}), "accent": accent})
        return real(cues, path, width, height, style, accent)
    monkeypatch.setattr(media, "build_ass", build)
    return seen


def reels(client, pid, kind="short_reel"):
    return client.get(f"/api/assets?project_id={pid}&kind={kind}").json()


def run_pipeline(client, ctx, pid):
    client.post(f"/api/projects/{pid}/process", json={"steps": ["transcription", "reel_generation"]})
    ctx.queue.drain()


def test_words_are_stored_and_reach_the_caption_builder(client, ctx, fakes, project_with_video, spy):
    fakes.transcriber = WordTranscriber()
    run_pipeline(client, ctx, project_with_video)
    from ruha.models import Transcript, TranscriptSegment
    with ctx.db.session() as s:
        t = s.query(Transcript).filter_by(project_id=project_with_video).one()
        segs = s.query(TranscriptSegment).filter_by(transcript_id=t.id).order_by(TranscriptSegment.idx).all()
        assert segs[0].words and segs[0].words[0]["w"] == " Segment" and all(set(w) == {"w", "s", "e"} for w in segs[0].words)
    assert "words" not in client.get(f"/api/projects/{project_with_video}/transcript").json()["segments"][0]
    assert spy and all(c["style"].get("words") for c in spy)
    c0 = spy[0]
    first_word = c0["style"]["words"][0]
    assert first_word["s"] >= 0 and c0["cues"][0][0] == pytest.approx(first_word["s"], abs=0.01)   # captions start when the word is spoken


def test_old_transcripts_without_words_still_caption(client, ctx, project_with_video, spy):
    run_pipeline(client, ctx, project_with_video)                                          # the plain fake transcriber: no words
    assert spy and all("words" not in c["style"] for c in spy)
    assert all(r["has_file"] for r in reels(client, project_with_video))


def test_template_priority_project_then_settings(client, ctx, project_with_video, spy):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"subtitle.template": "wordpop"}})
    run_pipeline(client, ctx, pid)
    assert {c["style"]["caption_template"] for c in spy} == {"wordpop"}                    # no project choice: the Settings default
    assert reels(client, pid)[0]["meta"]["caption_template"] == "wordpop"
    r = client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "", "caption_template": "karaoke"})
    assert r.status_code == 200 and r.json()["caption_template"] == "karaoke"
    assert client.get(f"/api/projects/{pid}/instructions").json()["caption_template"] == "karaoke"
    spy.clear()
    rid = reels(client, pid, "main_reel")[0]["id"]
    client.post(f"/api/assets/{rid}/regenerate", json={"start": 10, "end": 70})
    ctx.queue.drain()
    assert spy and {c["style"]["caption_template"] for c in spy} == {"karaoke"}            # a re-render follows the project's style
    assert client.get(f"/api/assets/{rid}").json()["meta"]["caption_template"] == "karaoke"
    client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "", "caption_template": ""})
    assert client.get(f"/api/projects/{pid}/instructions").json()["caption_template"] == ""       # back to the default
    client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": ""})             # omitted: unchanged
    assert client.get(f"/api/projects/{pid}/instructions").json()["caption_template"] == ""


def test_template_validation(client, project_with_video):
    pid = project_with_video
    assert client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "", "caption_template": "fancy"}).status_code == 400
    assert client.put("/api/settings", json={"values": {"subtitle.template": "fancy"}}).status_code == 400
    for t in subtitles.TEMPLATES:
        assert client.put("/api/settings", json={"values": {"subtitle.template": t}}).status_code == 200
        assert client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "", "caption_template": t}).status_code == 200
    assert client.get(f"/api/projects/{pid}/instructions").json()["caption_template"] == "classic"


def test_every_template_renders_a_real_reel(client, ctx, project_with_video, fakes):
    fakes.transcriber = WordTranscriber()
    pid = project_with_video
    run_pipeline(client, ctx, pid)
    rid = reels(client, pid, "main_reel")[0]["id"]
    for t in subtitles.TEMPLATES:
        client.put(f"/api/projects/{pid}/instructions", json={"reels": "", "posters": "", "caption_template": t})
        assert client.post(f"/api/assets/{rid}/regenerate", json={"start": 10, "end": 40}).status_code == 202
        ctx.queue.drain()
        a = client.get(f"/api/assets/{rid}").json()
        assert a["meta"]["caption_template"] == t and a["has_file"]
    assert a["version"] == 1 + len(subtitles.TEMPLATES)


def test_style_gets_the_safe_area_brand_colour_and_key_words(client, ctx, project_with_video, spy):
    pid = project_with_video
    run_pipeline(client, ctx, pid)
    main = [c for c in spy if c["size"] == (270, 480)]
    assert main and all(c["style"]["safe"] == tuple(formats.video_safe("9:16", (270, 480))) for c in main)
    assert all(c["style"]["brand_color"] == "#7C3AED" for c in main)                       # the brand kit's primary colour
    keys = {tuple(c["style"].get("key_words", ())) for c in main}
    assert ("grace", "gift") in keys and ("free", "gift", "person") in keys                # the model's words, grounded in the clip
    assert not any("invented" in k or "the" in k for k in keys)
    short = reels(client, pid)[0]
    assert short["meta"]["key_words"] == ["free", "gift", "person"]


def test_each_video_format_gets_its_own_safe_area(client, ctx, project_with_video, spy):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reel.formats": ["9:16", "1:1", "16:9"]}})
    run_pipeline(client, ctx, pid)
    by_size = {}
    for c in spy:
        by_size.setdefault(c["size"], c["style"]["safe"])
    assert by_size == {(270, 480): tuple(formats.video_safe("9:16", (270, 480))), (270, 270): tuple(formats.video_safe("1:1", (270, 270))),
                       (480, 270): tuple(formats.video_safe("16:9", (480, 270)))}


def test_a_typed_reel_gets_key_words_too(client, ctx, project_with_video, spy):
    pid = project_with_video
    client.put("/api/settings", json={"values": {"reels.count": 2}})
    client.put(f"/api/projects/{pid}/instructions", json={"reels": "0:10-1:10 main\n1:20-1:55 short\n2:00-2:35 short", "posters": ""})
    run_pipeline(client, ctx, pid)
    typed = [r for r in reels(client, pid) + reels(client, pid, "main_reel") if r["meta"]["typed"]]
    assert typed and all(r["meta"]["key_words"] for r in typed)                            # from the clip itself when the model gave none


def test_planner_keeps_only_real_key_words(tmp_path):
    from tests.test_clip_selection import make_segs, plan_of, clip, run
    segs = make_segs(special={t: "Forgiveness sets you free from the chains of shame" for t in range(100, 140, 5)})
    plan = plan_of([clip(1000, 70, 18)], [clip(100, 40, 15, key_words=["forgiveness", "shame", "invented", "the", "chains"])])
    out = run(plan, segs=segs, rs=__import__("ruha.services.selection", fromlist=["x"]).reel_settings({"reels.count": 1}))
    assert out["short_reels"][0]["key_words"] == ["forgiveness", "shame", "chains"]
    assert out["main_reels"][0]["key_words"] == []                                         # none given, none invented


def test_fallback_plan_picks_its_own_key_words():
    from ruha.services import planfallback
    segs = make_segments(120)
    p = planfallback.fallback_plan(segs, 120)
    assert all(isinstance(c["key_words"], list) for c in p["main_reels"] + p["short_reels"])


def test_status_lists_templates_and_fonts(client):
    s = client.get("/api/system/status").json()
    assert [t["id"] for t in s["caption_templates"]] == ["stacked", "wordpop", "karaoke", "classic"]
    assert all(t["label"] and t["note"] for t in s["caption_templates"])
    assert s["caption_fonts"]["latin"] == "Poppins SemiBold" and all(s["caption_fonts"]["scripts"].values()) and len(s["caption_fonts"]["scripts"]) == 10
    assert client.get("/api/settings").json()["values"]["subtitle.template"] == "stacked"
