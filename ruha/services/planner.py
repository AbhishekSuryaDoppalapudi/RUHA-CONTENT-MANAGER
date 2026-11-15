"""LLM content planning: chunked analysis, timestamp snapping and faithfulness guards."""
from __future__ import annotations

import json
import re

from ruha.services import themes

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


THEME_NAMES = ["warfare", "spirit", "grace", "peace", "hope", "faith", "love", "strength", "growth"]
N_MAIN, N_SHORT, N_POSTS = 1, 2, 2

PLAN_SCHEMA = """{
 "title":"short descriptive title",
 "main_theme":"one sentence",
 "visual_theme":"ONE of: warfare, spirit, grace, peace, hope, faith, love, strength, growth",
 "summary":"faithful 3-5 sentence summary",
 "themes":["2-6 single or two-word topics"],
 "key_points":["..."],
 "quotes":["exact sentences copied verbatim from the transcript"],
 "main_reels":[{"start":0,"end":60,"hook":"max 8 words","closing":"max 8 words","reason":"...","caption":"...","hashtags":["#..."]}],
 "short_reels":[{"start":0,"end":20,"hook":"max 8 words","closing":"max 8 words","reason":"...","caption":"...","hashtags":["#..."]}],
 "poster":{"hook":"max 8 words","verse":"reference the speaker cited, else empty","verse_text":"the verse words exactly as spoken, else empty","theme":"..."},
 "encouragement_posts":[{"hook":"max 10 words","reference":"","caption":"..."}],
 "hashtags":["#..."]
}"""


def _rules(duration: float) -> str:
    return f"""Requirements:
- Return EXACTLY {N_MAIN} main_reel (35-90 seconds), {N_SHORT} short_reels (15-30 seconds each) and EXACTLY {N_POSTS} encouragement_posts.
- Timestamps are in seconds and must lie between 0 and {duration:.1f}. Reels must not overlap each other.
- Choose complete thoughts: begin where an idea starts and end where it lands.
- HOOK TEXT: every hook (reels, poster, posts) is a short, powerful line that captures the THEME of the sermon and makes a viewer stop scrolling. Write it yourself in your own words - do NOT copy a long sentence from the transcript, do NOT write a summary and never start with "In this sermon" or "This talk". Good hooks are short and bold, e.g. "Stop fighting the wrong enemy", "Your breakthrough is closer than you think".
- "closing" is a short call-to-action or blessing shown at the end of a reel.
- visual_theme: pick the single theme that best matches the sermon's mood (warfare=spiritual battle, spirit=Holy Spirit/fire, grace=forgiveness/gift, peace=rest/healing, hope=new beginnings, faith=trust/prayer, love=compassion, strength=endurance, growth=discipleship).
- quotes must be copied word-for-word from the transcript; never invent quotes.
- verse / reference: ONLY a Bible reference the speaker explicitly says in the talk; otherwise an empty string. verse_text only if the speaker actually reads it aloud.
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


def _free_parts(a: float, b: float, used: list) -> list:
    """The parts of [a, b] not covered by any already-chosen reel."""
    parts = [(a, b)]
    for u in sorted(used):
        nxt = []
        for s, e in parts:
            if u[1] <= s or u[0] >= e:
                nxt.append((s, e))
                continue
            if u[0] > s:
                nxt.append((s, u[0]))
            if u[1] < e:
                nxt.append((u[1], e))
        parts = nxt
    return parts


def _repair_overlap(segments, a, b, lo, hi, duration, used):
    """Fix a reel that overlaps earlier ones: keep its longest free part, else move it into the
    largest free gap of the video. Raises PlanError only when there is genuinely no room."""
    def ok(x, y):
        return y - x >= min(lo, duration * 0.5) and not any(_overlap((x, y), u) > 0.4 * (y - x) for u in used)

    for s, e in sorted(_free_parts(a, b, used), key=lambda p: p[0] - p[1]):      # longest first
        if e - s >= lo:
            x, y = _snap(segments, s, e, lo, hi, duration)
            if ok(x, y):
                return x, y
    return _free_window(segments, lo, hi, duration, used, ok)


def _free_window(segments, lo, hi, duration, used, ok=None):
    """A moderate-length clip in the largest free gap of the video (leaves room for the other reels)."""
    ok = ok or (lambda x, y: y - x >= min(lo, duration * 0.5) and
                not any(_overlap((x, y), u) > 0.4 * (y - x) for u in used))
    for s, e in sorted(_free_parts(0.0, duration, used), key=lambda p: p[0] - p[1]):
        if e - s >= lo:
            x, y = _snap(segments, s, min(e, s + (lo + hi) / 2), lo, hi, duration)
            if ok(x, y):
                return x, y
    raise PlanError("There is no free part of the video left for another reel.")


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
    for key, (lo, hi), count in (("main_reels", MAIN_RANGE, N_MAIN), ("short_reels", SHORT_RANGE, N_SHORT)):
        items = plan.get(key) or plan.get(key[:-1]) or []       # tolerate "main_reel" from a small model
        if isinstance(items, dict):
            items = [items]
        items = [i for i in items if isinstance(i, dict)][:count]
        while len(items) < count:                                # small models sometimes leave one out:
            items.append({"_auto": True})                        # pick a free stretch of the video ourselves
        fixed = []
        for it in items:
            if it.get("_auto"):
                a, b = _free_window(segments, lo, hi, duration, used)
                it = {"reason": "Selected automatically (the AI suggested fewer reels than needed)"}
            else:
                try:
                    a, b = _snap(segments, float(it["start"]), float(it["end"]), lo, hi, duration)
                except (KeyError, TypeError, ValueError):
                    raise PlanError(f"A {key} entry has invalid timestamps.")
                if b - a < min(lo, duration * 0.5):
                    raise PlanError(f"A {key} entry is too short after snapping to the transcript.")
                if any(_overlap((a, b), u) > 0.4 * (b - a) for u in used):
                    a, b = _repair_overlap(segments, a, b, lo, hi, duration, used)
            used.append((a, b))
            fixed.append({"start": a, "end": b, "hook": str(it.get("hook", "")).strip()[:120],
                          "closing": str(it.get("closing", "")).strip()[:120],
                          "reason": str(it.get("reason", "")).strip(),
                          "caption": str(it.get("caption", "")).strip(),
                          "hashtags": [h if h.startswith("#") else f"#{h}"
                                       for h in (str(x).strip().replace(" ", "") for x in it.get("hashtags", [])) if h]
                                      or out["hashtags"][:6]})
        out[key] = fixed
    out["visual_theme"] = themes.resolve(plan.get("visual_theme"), " ".join(
        [out["main_theme"], out["summary"], " ".join(out["themes"])]))
    poster = plan.get("poster") or {}
    ref = str(poster.get("verse") or poster.get("reference") or "").strip()
    if ref and not reference_supported(ref, full_text):
        ref = ""
    vt = str(poster.get("verse_text") or poster.get("quote") or "").strip()
    if not ref or not quote_in_transcript(vt, full_text):
        vt = ""                                              # only verses the speaker really read aloud
    hook = str(poster.get("hook") or poster.get("headline") or out["main_theme"] or out["title"]).strip()
    out["poster"] = {"headline": hook[:140], "subheadline": ref, "quote": vt,
                     "theme": str(poster.get("theme") or out["main_theme"]).strip()}
    out["encouragement_posts"] = normalize_posts(plan.get("encouragement_posts") or [], full_text, N_POSTS)
    for key in ("main_reels", "short_reels"):                # a reel always has a hook to open with
        for r in out[key]:
            r["hook"] = r["hook"] or out["poster"]["headline"]
            r["closing"] = r["closing"] or "Follow for more"
    return out


def normalize_posts(posts: list, full_text: str, count: int) -> list:
    """Posts carry a theme-based hook line and, only when the speaker cited one, the verse reference."""
    if len(posts) < count:
        raise PlanError(f"The model returned {len(posts)} posts; expected {count}.")
    res = []
    for p in posts[:count]:
        ref = str(p.get("reference") or p.get("scripture_reference") or p.get("verse") or "").strip()
        if ref and not reference_supported(ref, full_text):
            ref = ""
        hook = str(p.get("hook") or p.get("headline") or "").strip()
        if not hook:
            raise PlanError("A post has no hook text.")
        res.append({"headline": hook[:160], "body": "", "caption": str(p.get("caption", "")).strip(),
                    "reference": ref})
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
              '{"posts":[{"hook":"max 10 words","caption":"...","reference":""}]}.\n'
              f"Talk title: {plan.get('title')}\nTheme: {plan.get('main_theme')}\nSummary: {plan.get('summary')}\n"
              f"Key points: {json.dumps(plan.get('key_points', []), ensure_ascii=False)}\n"
              "Rules: the hook is a short, powerful line capturing the THEME of the talk in your own words (not a copied sentence); stay faithful to the talk; reference only if the speaker explicitly cited it, else empty; "
              "write in the language of the summary.\n"
              + (f"Owner note for this revision: {note}\n" if note else "")
              + ("DO NOT resemble these existing posts:\n- " + "\n- ".join(x[:250] for x in avoid) + "\n" if avoid else "")
              + _context_block(ctxinfo))
    raw = llm.generate_json(prompt, cfg)
    return normalize_posts(raw.get("posts") or [], full_text, count)
