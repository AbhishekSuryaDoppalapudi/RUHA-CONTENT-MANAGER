"""A basic content plan built from the transcript alone (no model), used when the AI plan fails.

Clips are chosen from sentence-boundary windows ranked by how much meaningful speech they hold per second
(questions and exclamations count extra); hooks are 3-8 word phrases taken from the clips themselves.
The result has the same shape as ``planner.normalize_plan`` so every later step works unchanged; it carries
``"fallback": True`` so the UI and logs can say it is a basic plan.
"""
from __future__ import annotations

import re
from collections import Counter

from ruha.services import emphasis, hooks, keywords, planner, selection, themes

MAIN_TARGET = (60.0, 90.0)


def _sentences(segments: list) -> list:
    """[{start, end, text}] - consecutive segments joined until the speech reaches . ? or !"""
    out, cur = [], None
    for s in segments:
        if cur is None:
            cur = {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        else:
            cur["end"], cur["text"] = s["end"], f"{cur['text']} {s['text'].strip()}"
        if re.search(r"[.?!][\"')\]]*$", s["text"].strip()) or cur["end"] - cur["start"] >= 10.0:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def _score(text: str, seconds: float) -> float:
    strong = len(keywords.content_words(text, min_len=5))
    marks = text.count("?") + 0.5 * text.count("!")
    return (strong + 2.0 * marks) / max(seconds, 1.0)


def _windows(sents: list, lo: float, hi: float, topic: str = "") -> list:
    """Every sentence-aligned window whose length lies in [lo, hi], with its score (+1 per share of the topic's words)."""
    out = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            length = sents[j]["end"] - sents[i]["start"]
            if length > hi:
                break
            if length >= lo:
                text = " ".join(x["text"] for x in sents[i:j + 1])
                bonus = selection.topic_hits(topic, text) if topic else 0.0
                out.append((_score(text, length) + bonus, sents[i]["start"], sents[j]["end"], text))
    return sorted(out, key=lambda w: -w[0])


def _pick(wins: list, used: list):
    for _sc, a, b, text in wins:
        if all(min(b, u[1]) - max(a, u[0]) <= 0 for u in used):
            return a, b, text
    return None


def _thirds(segments: list, duration: float, used: list, want: int) -> list:
    """Last resort for very short transcripts: equal slices of the speech."""
    start, end = segments[0]["start"], max(segments[-1]["end"], duration)
    step = (end - start) / max(want, 1)
    return [(round(start + k * step, 2), round(start + (k + 1) * step, 2)) for k in range(want)]


def fallback_plan(segments: list, duration: float, one_word: bool = False, rs: dict | None = None,
                  topic: str = "") -> dict:
    if not segments:
        raise planner.PlanError("Transcript has no segments to plan from.")
    sents = _sentences(segments)
    full = " ".join(s["text"] for s in segments)
    duration = max(duration or 0, segments[-1]["end"])

    rs = rs or selection.reel_settings({})
    topic = selection.clean_topic(topic)
    used, picks = [], {}
    plan_for = (("main_reels", MAIN_TARGET, planner.MAIN_RANGE, planner.N_MAIN),
                ("short_reels", (rs["min_s"], rs["max_s"]), (rs["min_s"], rs["max_s"]), rs["count"]))
    for key, (tlo, thi), (rlo, rhi), count in plan_for:
        chosen = []
        # prefer the target length, then the full accepted range, then anything the transcript allows
        for lo, hi in ((tlo, thi), (rlo, rhi), (min(rlo, duration * 0.25), rhi)):
            wins = _windows(sents, lo, hi, topic)
            while len(chosen) < count:
                got = _pick(wins, used)
                if not got:
                    break
                chosen.append(got)
                used.append((got[0], got[1]))
            if len(chosen) >= count:
                break
        picks[key] = chosen
    if not picks["main_reels"] or not picks["short_reels"]:
        want = max(2, min(rs["count"], 3) + planner.N_MAIN)
        spans = _thirds(segments, duration, [], want)
        it = iter(spans)
        picks = {"main_reels": [(a, b, hooks.clip_text(segments, a, b)) for a, b in [next(it)]],
                 "short_reels": [(a, b, hooks.clip_text(segments, a, b)) for a, b in it]}

    freq = Counter(w.strip("'-") for w in keywords.content_words(full, min_len=5))
    top = [w for w, _n in freq.most_common(6)]
    tags = [f"#{w}" for w in top[:4]] + ["#sermon"]
    ranked = sorted((s for s in sents if 8 <= len(s["text"].split()) <= 30),
                    key=lambda s: -_score(s["text"], max(s["end"] - s["start"], 1.0)))
    theme_text = " ".join([full[:3000], " ".join(top)])
    visual = themes.resolve(None, theme_text)
    main_theme = ("A message about " + ", ".join(top[:3])) if top else "A sermon"

    def clip(a, b, text):
        first = next((x["text"] for x in sents if x["start"] >= a - 0.5), "") or text
        hook = hooks.ensure_hook("", text, one_word, visual, main_theme)
        score, parts = selection.estimate_score(text, b - a)
        return {"start": round(a, 2), "end": round(b, 2), "hook": hook or "Listen to this",
                "closing": "Follow for more", "caption": first[:200], "hashtags": tags[:6],
                "reason": "Selected automatically from the transcript (the AI plan was not available)",
                "score": score, "score_parts": parts, "score_source": "estimated",
                "category": selection.infer_category(text),
                "key_words": emphasis.auto_keys(text),
                "topic_hits": round(selection.topic_hits(topic, text), 2) if topic else 0.0, "topic_llm": None}

    out = {"title": (hooks.extract_hook(full[:800]) or "Sermon")[:120], "main_theme": main_theme,
           "summary": " ".join(s["text"] for s in sents[:3])[:500], "themes": top[:5], "key_points": [],
           "quotes": [s["text"] for s in ranked[:3]], "hashtags": tags, "visual_theme": visual,
           "main_reels": [clip(*c) for c in picks["main_reels"]],
           "short_reels": sorted((clip(*c) for c in picks["short_reels"]), key=lambda c: (-selection.rank_value(c, topic), c["start"])),
           "fallback": True, "reel_settings": rs, "topic": topic, "selection_v": selection.SELECTION_V, "notes": []}
    if len(out["short_reels"]) < rs["count"]:
        out["notes"].append(f"Only {len(out['short_reels'])} of the {rs['count']} short reels you asked for fit in this video.")
    if topic and not any(c["topic_hits"] > 0 for c in out["main_reels"] + out["short_reels"]):
        out["notes"].append(f"No clear moment about \"{topic}\" was found in the talk.")
    head = hooks.extract_hook(ranked[0]["text"]) if ranked else ""
    out["poster"] = {"headline": (head or out["title"])[:140], "subheadline": "", "quote": "", "theme": main_theme}
    posts = []
    for s in ranked[1:1 + planner.N_POSTS] or sents[:planner.N_POSTS]:
        posts.append({"headline": (hooks.extract_hook(s["text"]) or hooks.tidy_hook(s["text"], 8))[:160],
                      "body": "", "caption": s["text"][:300], "reference": ""})
    while len(posts) < planner.N_POSTS:                                  # tiny transcripts
        posts.append({"headline": out["poster"]["headline"], "body": "", "caption": out["summary"][:300], "reference": ""})
    out["encouragement_posts"] = posts[:planner.N_POSTS]
    return out

