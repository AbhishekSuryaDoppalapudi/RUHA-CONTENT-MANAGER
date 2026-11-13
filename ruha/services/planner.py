"""LLM content planning: chunked analysis, timestamp snapping and faithfulness guards."""
from __future__ import annotations

import json
import re

MAIN_RANGE = (30.0, 95.0)    # accepted seconds for main reels (target 35-90)
SHORT_RANGE = (12.0, 35.0)   # accepted seconds for short reels (target 15-30)
SINGLE_PASS_CHARS = 22000
CHUNK_CHARS = 14000


class PlanError(RuntimeError):
    pass


def _fmt(sec: float) -> str:
    return f"{int(sec // 60):02}:{int(sec % 60):02}"


def transcript_lines(segments) -> str:
    return "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", "", (text or "").lower(), flags=re.UNICODE).split().__str__()


def _squash(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower(), flags=re.UNICODE).split())


def quote_in_transcript(quote: str, full_text: str) -> bool:
    q, t = _squash(quote), _squash(full_text)
    return bool(q) and q in t


def reference_supported(ref: str, full_text: str) -> bool:
    """A scripture/source reference is only kept if its book/name is actually spoken in the video."""
    ref = (ref or "").strip()
    if not ref:
        return False
    name = re.sub(r"[\d:;,\-–\s]+$", "", ref).strip()
    return bool(name) and _squash(name) in _squash(full_text)


def _context_block(ctxinfo: dict) -> str:
    parts = []
    if ctxinfo.get("preferences"):
        parts.append("OWNER PREFERENCES (follow these):\n- " + "\n- ".join(ctxinfo["preferences"]))
    if ctxinfo.get("approved_examples"):
        parts.append("PREVIOUSLY APPROVED STYLE EXAMPLES:\n- " + "\n- ".join(x[:300] for x in ctxinfo["approved_examples"]))
    if ctxinfo.get("rejected_examples") or ctxinfo.get("rejection_notes"):
        bad = [x[:300] for x in ctxinfo.get("rejected_examples", [])] + ctxinfo.get("rejection_notes", [])
        parts.append("PREVIOUSLY REJECTED OR REVISED (avoid these patterns):\n- " + "\n- ".join(bad))
    if ctxinfo.get("recent_content"):
        parts.append("RECENT POSTS/CAPTIONS ALREADY PUBLISHED - do NOT repeat their wording or angle:\n- "
                     + "\n- ".join(x[:200] for x in ctxinfo["recent_content"]))
    return "\n\n".join(parts)


PLAN_SCHEMA = """{
 "title":"short descriptive title",
 "main_theme":"one sentence",
 "summary":"faithful 3-5 sentence summary",
 "themes":["2-6 single or two-word topics"],
 "key_points":["..."],
 "quotes":["exact sentences copied verbatim from the transcript"],
 "main_reels":[{"start":0,"end":60,"hook":"...","reason":"...","caption":"...","hashtags":["#..."]}],
 "short_reels":[{"start":0,"end":20,"hook":"...","reason":"...","caption":"...","hashtags":["#..."]}],
 "poster":{"headline":"...","subheadline":"...","quote":"verbatim quote","theme":"..."},
 "encouragement_posts":[{"headline":"...","body":"...","caption":"...","reference":""}],
 "hashtags":["#..."]
}"""


def _rules(duration: float) -> str:
    return f"""Requirements:
- Return EXACTLY 3 main_reels (35-90 seconds each) and 2 short_reels (15-30 seconds each) and EXACTLY 2 encouragement_posts.
- Timestamps are in seconds and must lie between 0 and {duration:.1f}. Reels must not overlap each other.
- Choose complete thoughts: begin where an idea starts and end where it lands. Prefer strong stories and practical teaching.
- Every reel needs a strong opening hook (first line a viewer reads/hears).
- quotes and poster.quote must be copied word-for-word from the transcript; never invent quotes.
- reference: only a Bible/source reference the speaker explicitly mentions; otherwise an empty string.
- The two encouragement posts must have clearly different angles and wording.
- Stay faithful to the speaker's meaning. Write in the same language as the transcript."""


def _single_prompt(lines: str, duration: float, ctxinfo: dict, extra: str = "") -> str:
    return (f"You are a careful editor of sermons and long-form talks.\n"
            f"Analyse the transcript and return ONLY valid JSON matching this schema:\n{PLAN_SCHEMA}\n\n"
            f"{_rules(duration)}\n\n{_context_block(ctxinfo)}\n{extra}\n\nTRANSCRIPT:\n{lines}\n")


def _map_prompt(lines: str) -> str:
    return ("Analyse this PART of a longer transcript. Return ONLY JSON: "
            '{"summary":"...","themes":["..."],"quotes":["verbatim sentences"],'
            '"moments":[{"start":0,"end":60,"hook":"...","reason":"...","caption":"..."}]} '
            "with at most 4 moments (complete thoughts, 15-90 seconds each). Timestamps in seconds as in the text.\n\n"
            f"TRANSCRIPT PART:\n{lines}\n")


def _reduce_prompt(parts: list, duration: float, ctxinfo: dict, extra: str) -> str:
    digest = json.dumps(parts, ensure_ascii=False)
    return (f"You are a careful editor of sermons. A long talk was analysed in parts; the JSON below lists "
            f"each part's summary, themes, verbatim quotes and candidate moments.\n"
            f"Combine them into ONE plan. Return ONLY valid JSON matching this schema:\n{PLAN_SCHEMA}\n\n"
            f"{_rules(duration)}\n- Choose reel timestamps only from the candidate moments (you may trim them slightly).\n\n"
            f"{_context_block(ctxinfo)}\n{extra}\n\nPARTS:\n{digest}\n")


def _chunk_segments(segments) -> list:
    chunks, cur, size = [], [], 0
    for s in segments:
        line = f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}\n"
        if size + len(line) > CHUNK_CHARS and cur:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(line)
    if cur:
        chunks.append(cur)
    return chunks


def _snap(segments, start: float, end: float, lo: float, hi: float, duration: float):
    """Snap to segment boundaries, then force the clip length into [lo, hi]."""
    starts = [s["start"] for s in segments]
    ends = [s["end"] for s in segments]
    start = max(0.0, min(float(start), duration))
    end = max(start + 1, min(float(end), duration))
    cand = [x for x in starts if x <= start + 0.5]
    start = max(cand) if cand else starts[0]
    cand_e = [x for x in ends if x >= end - 0.5]
    end = min(cand_e) if cand_e else ends[-1]
    if end - start < lo:                       # extend forward on segment boundaries
        for e in ends:
            if e > start and e - start >= lo:
                end = e
                break
        else:
            end = ends[-1]
    if end - start > hi:                       # trim back on a segment boundary
        ok = [e for e in ends if start < e <= start + hi]
        end = max(ok) if ok else start + hi
    return round(start, 2), round(min(end, duration), 2)


def _overlap(a, b) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def normalize_plan(plan: dict, segments: list, duration: float, full_text: str) -> dict:
    if not segments:
        raise PlanError("Transcript has no segments to plan from.")
    out = dict(plan)
    out["title"] = str(plan.get("title") or "Untitled").strip()[:200]
    out["main_theme"] = str(plan.get("main_theme") or "").strip()
    out["summary"] = str(plan.get("summary") or "").strip()
    out["themes"] = [str(t).strip() for t in plan.get("themes", []) if str(t).strip()][:8]
    out["key_points"] = [str(t).strip() for t in plan.get("key_points", []) if str(t).strip()]
    out["quotes"] = [q for q in (str(x).strip() for x in plan.get("quotes", []))
                     if quote_in_transcript(q, full_text)][:10]
    out["hashtags"] = [h if h.startswith("#") else f"#{h}" for h in
                       (str(x).strip().replace(" ", "") for x in plan.get("hashtags", [])) if h][:15]
    used = []
    for key, (lo, hi), count in (("main_reels", MAIN_RANGE, 3), ("short_reels", SHORT_RANGE, 2)):
        items = plan.get(key) or []
        if len(items) != count:
            raise PlanError(f"The model returned {len(items)} {key}; expected {count}.")
        fixed = []
        for it in items:
            try:
                a, b = _snap(segments, float(it["start"]), float(it["end"]), lo, hi, duration)
            except (KeyError, TypeError, ValueError):
                raise PlanError(f"A {key} entry has invalid timestamps.")
            if b - a < min(lo, duration * 0.5):
                raise PlanError(f"A {key} entry is too short after snapping to the transcript.")
            if any(_overlap((a, b), u) > 0.4 * (b - a) for u in used):
                raise PlanError("Two selected reels overlap heavily; choose distinct moments.")
            used.append((a, b))
            fixed.append({"start": a, "end": b, "hook": str(it.get("hook", "")).strip(),
                          "reason": str(it.get("reason", "")).strip(),
                          "caption": str(it.get("caption", "")).strip(),
                          "hashtags": [h if h.startswith("#") else f"#{h}"
                                       for h in (str(x).strip().replace(" ", "") for x in it.get("hashtags", [])) if h]
                                      or out["hashtags"][:6]})
        out[key] = fixed
    poster = plan.get("poster") or {}
    q = str(poster.get("quote", "")).strip()
    if q and not quote_in_transcript(q, full_text):
        q = out["quotes"][0] if out["quotes"] else ""
    out["poster"] = {"headline": str(poster.get("headline") or out["title"]).strip(),
                     "subheadline": str(poster.get("subheadline", "")).strip(), "quote": q,
                     "theme": str(poster.get("theme") or out["main_theme"]).strip()}
    out["encouragement_posts"] = normalize_posts(plan.get("encouragement_posts") or [], full_text, 2)
    return out


def normalize_posts(posts: list, full_text: str, count: int) -> list:
    if len(posts) < count:
        raise PlanError(f"The model returned {len(posts)} posts; expected {count}.")
    res = []
    for p in posts[:count]:
        ref = str(p.get("reference") or p.get("scripture_reference") or "").strip()
        if ref and not reference_supported(ref, full_text):
            ref = ""
        res.append({"headline": str(p.get("headline", "")).strip(), "body": str(p.get("body", "")).strip(),
                    "caption": str(p.get("caption", "")).strip(), "reference": ref})
    return res


def create_plan(llm, cfg: dict, segments: list, duration: float, ctxinfo: dict, log=print) -> dict:
    full_text = " ".join(s["text"] for s in segments)
    lines = transcript_lines(segments)
    last_err = None
    for attempt in range(2):
        extra = f"\nPREVIOUS ATTEMPT WAS REJECTED: {last_err}. Fix this.\n" if last_err else ""
        try:
            if len(lines) <= SINGLE_PASS_CHARS:
                raw = llm.generate_json(_single_prompt(lines, duration, ctxinfo, extra), cfg)
            else:
                chunks = _chunk_segments(segments)
                log(f"Long transcript: analysing in {len(chunks)} parts")
                parts = [llm.generate_json(_map_prompt(transcript_lines(c)), cfg) for c in chunks] \
                    if attempt == 0 else parts
                raw = llm.generate_json(_reduce_prompt(parts, duration, ctxinfo, extra), cfg)
            return normalize_plan(raw, segments, duration, full_text)
        except PlanError as e:
            last_err = str(e)
            log(f"Plan rejected ({last_err}); {'retrying' if attempt == 0 else 'giving up'}", "WARNING")
    raise PlanError(f"Could not produce a valid content plan: {last_err}")


def regenerate_posts(llm, cfg: dict, plan: dict, full_text: str, avoid: list, ctxinfo: dict, count: int = 1,
                     note: str = "") -> list:
    prompt = (f"Write {count} NEW encouragement social post(s) based on this talk. Return ONLY JSON "
              '{"posts":[{"headline":"...","body":"...","caption":"...","reference":""}]}.\n'
              f"Talk title: {plan.get('title')}\nTheme: {plan.get('main_theme')}\nSummary: {plan.get('summary')}\n"
              f"Key points: {json.dumps(plan.get('key_points', []), ensure_ascii=False)}\n"
              "Rules: stay faithful to the talk; reference only if the speaker explicitly cited it, else empty; "
              "write in the language of the summary.\n"
              + (f"Owner note for this revision: {note}\n" if note else "")
              + ("DO NOT resemble these existing posts:\n- " + "\n- ".join(x[:250] for x in avoid) + "\n" if avoid else "")
              + _context_block(ctxinfo))
    raw = llm.generate_json(prompt, cfg)
    return normalize_posts(raw.get("posts") or [], full_text, count)
