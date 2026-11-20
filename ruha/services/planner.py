"""LLM content planning: chunked analysis, timestamp snapping and faithfulness guards."""
from __future__ import annotations

import json
import re

from ruha.ai.llm import LLMError, LLMUnavailable
from ruha.services import emphasis, hooks, selection, themes

MAIN_RANGE = (55.0, 100.0)   # accepted seconds for the one long main reel (asked for 60-90)
# the short reels' length and count are SETTINGS (reels.count, reels.min_s, reels.max_s); this is only their default range
SHORT_RANGE = (selection.DEFAULT_MIN_S, selection.DEFAULT_MAX_S)
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

# A reel opens on ONE bold word. The model is asked for it, but small models often return a sentence or a summary
# ("In this sermon, we'll explore ..."), so the rule is enforced here: keep a genuine single word, otherwise pick the
# strongest matching word from the clip's own message, otherwise the sermon's visual theme.
_HOOK_WORDS = [
    (r"\b(battle|war|warfare|fight\w*|enem\w+|satan\w*|devil|demon\w*|spirits?|armou?r)\b", "Battle"),
    (r"\b(grace|forgiv\w+|mercy|pardon\w*)\b", "Grace"),
    (r"\b(peace|calm|still|rest|storm)\b", "Peace"),
    (r"\b(hope|future|tomorrow|promise\w*)\b", "Hope"),
    (r"\b(faith|believ\w+|trust\w*)\b", "Faith"),
    (r"\b(love|loved|beloved)\b", "Love"),
    (r"\b(strength|strong|weak\w*|courage|bold)\b", "Strength"),
    (r"\b(grow\w*|season|mature\w*|patience)\b", "Growth"),
    (r"\b(pray\w*)\b", "Pray"),
    (r"\b(wait\w*)\b", "Wait"),
    (r"\b(repent\w*|return)\b", "Return"),
    (r"\b(freedom|free|deliver\w*|breakthrough)\b", "Freedom"),
]
_THEME_WORD = {"warfare": "Battle", "spirit": "Spirit", "grace": "Grace", "peace": "Peace", "hope": "Hope",
               "faith": "Faith", "love": "Love", "strength": "Strength", "growth": "Growth"}
_WEAK_WORDS = {"the", "a", "an", "in", "this", "that", "we", "today", "sermon", "talk", "message", "video", "god",
               "our", "your", "you", "it", "is", "are", "and", "of", "to", "for", "with", "about", "listen"}


def one_word_hook(hook: str, theme: str = "", context: str = "") -> str:
    """Return a single bold word for a reel's opening card."""
    h = re.sub(r"[^\w'\- ]", "", str(hook or "")).strip()
    words = h.split()
    if len(words) == 1 and words[0].lower() not in _WEAK_WORDS and len(words[0]) > 2:
        return words[0].capitalize()
    text = f"{hook} {context}"
    for rx, word in _HOOK_WORDS:
        if re.search(rx, text, re.I):
            return word
    return _THEME_WORD.get((theme or "").lower(), "Listen")
N_MAIN, N_POSTS = 1, 2          # one long main reel; the number of short reels is the reels.count setting

_CLIP_FIELDS = ('"start":0,"end":60,"hook":"3-8 words in the speaker\'s own words","closing":"max 8 words",'
                '"reason":"ONE line: why this clip is strong or weak","caption":"...",'
                '"key_words":["up to 4 single words, copied from this clip\'s own speech, that carry its message"],'
                '"category":"ONE of: Scripture, Encouragement, Story, Teaching, Prayer",'
                '"score":{"opening":0-20,"idea":0-20,"quotable":0-20,"emotion":0-20,"standalone":0-20}')

PLAN_SCHEMA = """{
 "title":"short descriptive title",
 "main_theme":"one sentence",
 "visual_theme":"ONE of: warfare, spirit, grace, peace, hope, faith, love, strength, growth",
 "summary":"faithful 3-5 sentence summary",
 "themes":["2-6 single or two-word topics"],
 "key_points":["..."],
 "quotes":["exact sentences copied verbatim from the transcript"],
 "main_reels":[{""" + _CLIP_FIELDS + """}],
 "short_reels":[{""" + _CLIP_FIELDS + """}],
 "poster":{"hook":"max 8 words","verse":"reference the speaker cited, else empty","verse_text":"the verse words exactly as spoken, else empty","theme":"..."},
 "encouragement_posts":[{"hook":"max 10 words","reference":"","caption":"..."}],
 "hashtags":["#..."]
}"""

KEYWORD_RULE = ("- key_words: for every reel, up to 4 single words copied exactly from that reel's own speech that carry its message "
                "(a caption highlights them). Names of God, Bible book names and verse numbers are highlighted automatically, so "
                "list the OTHER important words. Never list filler words (the, and, just, really, very, thing).")

SCORING_RULES = """- MESSAGE SCORE: give every reel candidate a "score" object. Rate each part from 0 to 20 (20 = excellent, 10 = average, 0 = absent) and be critical - most clips should NOT score near 100:
    opening = a strong opening line that makes a viewer stay (a bold claim, a question, a striking sentence);
    idea = ONE clear, complete idea that begins and lands inside the clip;
    quotable = scripture read aloud or a line people would want to quote;
    emotion = an emotional moment (conviction, comfort, joy, tension);
    standalone = it makes sense with no context from the rest of the sermon.
  "category" is the clip's main kind: Scripture (the clip is built on a Bible text), Encouragement, Story (a testimony or illustration), Teaching, or Prayer. "reason" is one short line that justifies the score (name the strongest or weakest part)."""


def _rules(duration: float, rs: dict | None = None, topic: str = "") -> str:
    rs = rs or selection.reel_settings({})
    n_short = selection.candidates_wanted(duration, rs)
    topic_rule = ""
    if topic:
        topic_rule = (f"\n- THE PRODUCER WANTS A MOMENT ABOUT: \"{topic}\". Make sure the best moments that really are about this "
                      "are among the candidates, and give every candidate a \"topic_match\" from 0 (not about it) to 100 "
                      "(clearly about it). Never invent a match: it must be what the speaker actually says.")
    return f"""Requirements:
- Return {N_MAIN * 2} main_reels candidates (60-90 seconds each: the best long clip of the talk) and {n_short} short_reels candidates ({rs['min_s']:.0f}-{rs['max_s']:.0f} seconds each) and EXACTLY {N_POSTS} encouragement_posts.
- That is MORE candidates than will be used: afterwards the best-scoring ones that do not overlap are kept ({rs['count']} short reels and {N_MAIN} main reel). Candidates may overlap each other.
- Timestamps are in seconds and must lie between 0 and {duration:.1f}.
- Choose complete thoughts: begin where an idea starts and end where it lands.
{SCORING_RULES}{topic_rule}
{KEYWORD_RULE}
- HOOK TEXT: each reel's hook is a short phrase of 3 to 8 words taken from the speaker's OWN words in that reel (copy a striking sentence or the first clause of one; you may drop filler words). It must make a viewer want to keep watching. Do NOT write a summary, do NOT start with "In this sermon" or "This talk", and do NOT use a single word. Poster and post hooks are short lines (max 8-10 words) that capture the theme.
- "closing" is a short call-to-action or blessing shown at the end of a reel.
- visual_theme: pick the single theme that best matches the sermon's mood (warfare=spiritual battle, spirit=Holy Spirit/fire, grace=forgiveness/gift, peace=rest/healing, hope=new beginnings, faith=trust/prayer, love=compassion, strength=endurance, growth=discipleship).
- quotes must be copied word-for-word from the transcript; never invent quotes.
- verse / reference: ONLY a Bible reference the speaker explicitly says in the talk; otherwise an empty string. verse_text only if the speaker actually reads it aloud.
- The two encouragement posts must have clearly different angles and wording.
- Stay faithful to the speaker's meaning. Write in the same language as the transcript."""


def _single_prompt(lines: str, duration: float, ctxinfo: dict, extra: str = "", rs: dict | None = None, topic: str = "") -> str:
    return (f"You are a careful editor of sermons and long-form talks.\n"
            f"Analyse the transcript and return ONLY valid JSON matching this schema:\n{PLAN_SCHEMA}\n\n"
            f"{_rules(duration, rs, topic)}\n\n{_context_block(ctxinfo)}\n{extra}\n\nTRANSCRIPT:\n{lines}\n")


def _map_prompt(lines: str, rs: dict | None = None, topic: str = "", n_parts: int = 1, duration: float = 0.0) -> str:
    rs = rs or selection.reel_settings({})
    per = max(4, -(-selection.candidates_wanted(duration or 3600, rs) // max(n_parts, 1)) + 2)
    return ("Analyse this PART of a longer transcript. Return ONLY JSON: "
            '{"summary":"...","themes":["..."],"quotes":["verbatim sentences"],'
            '"moments":[{"start":0,"end":60,"hook":"...","reason":"one line","caption":"...",'
            '"category":"Scripture|Encouragement|Story|Teaching|Prayer",'
            '"score":{"opening":0-20,"idea":0-20,"quotable":0-20,"emotion":0-20,"standalone":0-20}'
            + (',"topic_match":0-100' if topic else '') + "}]} "
            f"with at most {per} moments (complete thoughts, {rs['min_s']:.0f}-{rs['max_s']:.0f} seconds each, plus at most one "
            "longer 60-90 second moment). Timestamps in seconds as in the text.\n" + SCORING_RULES + "\n"
            + (f'The producer wants a moment about: "{topic}" - include the moments that really are about it.\n' if topic else "")
            + f"\nTRANSCRIPT PART:\n{lines}\n")


def _reduce_prompt(parts: list, duration: float, ctxinfo: dict, extra: str, rs: dict | None = None, topic: str = "") -> str:
    digest = json.dumps(parts, ensure_ascii=False)
    return (f"You are a careful editor of sermons. A long talk was analysed in parts; the JSON below lists "
            f"each part's summary, themes, verbatim quotes and scored candidate moments.\n"
            f"Combine them into ONE plan. Return ONLY valid JSON matching this schema:\n{PLAN_SCHEMA}\n\n"
            f"{_rules(duration, rs, topic)}\n- Take the reel candidates from the moments (you may trim them slightly and "
            "keep or adjust their scores).\n\n"
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


def _lengthen_back(segments, a: float, b: float, lo: float, hi: float):
    """Move the start back on segment boundaries until the clip is at least ``lo`` long (never past ``hi``)."""
    starts = sorted({s["start"] for s in segments})
    while b - a < lo:
        earlier = [x for x in starts if x < a]
        if not earlier:
            break
        a = max(earlier)
    if b - a > hi:
        ok = [x for x in starts if b - x <= hi]
        a = min(ok) if ok else b - hi
    return round(a, 2), round(b, 2)


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


def _make_clip(it: dict, a: float, b: float, segments: list, topic: str, default_tags: list) -> dict:
    """A snapped candidate as a clip: times, hook text, and its Message score, category and topic match."""
    text = hooks.clip_text(segments, a, b)
    score, parts = selection.parse_score(it)
    source = "ai"
    if score is None:
        score, parts = selection.estimate_score(text, b - a)
        source = "estimated"
    reason = str(it.get("reason", "")).strip().replace("\n", " ")[:240]
    if not reason and source == "estimated":
        reason = "Estimated from the transcript (the AI gave no score)"
    llm_topic = it.get("topic_match", it.get("topic_llm"))
    return {"start": a, "end": b, "hook": str(it.get("hook", "")).strip()[:120],
            "closing": str(it.get("closing", "")).strip()[:120], "reason": reason,
            "caption": str(it.get("caption", "")).strip(),
            "hashtags": [h if h.startswith("#") else f"#{h}"
                         for h in (str(x).strip().replace(" ", "") for x in (it.get("hashtags") or [])) if h] or default_tags[:6],
            "score": score, "score_parts": parts, "score_source": source,
            "category": selection.clean_category(it.get("category"), text),
            "key_words": emphasis.grounded_keys(it.get("key_words"), text),
            "topic_hits": round(selection.topic_hits(topic, text), 2) if topic else 0.0,
            "topic_llm": llm_topic if isinstance(llm_topic, (int, float)) and not isinstance(llm_topic, bool) else None}


def _candidate(it: dict, lo: float, hi: float, segments: list, duration: float, topic: str, default_tags: list):
    """One model candidate snapped to the transcript and lengthened if needed; None when its times are unusable."""
    try:
        a, b = _snap(segments, float(it["start"]), float(it["end"]), lo, hi, duration)
    except (KeyError, TypeError, ValueError):
        return None
    if b - a < min(lo, duration * 0.5):
        # Usual cause on short talks: the model started the reel too close to the end, so it cannot grow forward.
        # Grow it backwards instead; if that still is not enough the candidate is dropped.
        a, b = _lengthen_back(segments, a, b, lo, hi)
        if b - a < min(lo, duration * 0.5):
            return None
    return _make_clip(it, a, b, segments, topic, default_tags)


def _choose(items: list, label: str, lo: float, hi: float, count: int, used: list, segments: list, duration: float,
            topic: str, default_tags: list) -> list:
    """The best ``count`` clips among the candidates: highest score first, none overlapping each other or ``used``.

    Candidates that overlap a better one are repaired (trimmed or moved, as before) in rank order; whatever is still
    missing is filled from free stretches of the video (estimated score). Fewer than ``count`` come back only when the
    video has no more room."""
    cands = [c for c in (_candidate(i, lo, hi, segments, duration, topic, default_tags) for i in items) if c]
    if items and not cands:
        raise PlanError(f"No {label} reel candidate had usable timestamps.")
    kept, rest = selection.pick(cands, count, used, topic)
    taken = lambda: used + [(k["start"], k["end"]) for k in kept]
    for c in rest:                                              # overlap repair, best candidates first
        if len(kept) >= count:
            break
        try:
            a, b = _repair_overlap(segments, c["start"], c["end"], lo, hi, duration, taken())
        except PlanError:
            continue
        if selection.overlap((a, b), (c["start"], c["end"])) >= 0.6 * (b - a):      # still the same message, just trimmed
            kept.append({**c, "start": a, "end": b})
        else:                                                                       # moved elsewhere: not the scored message
            kept.append(_make_clip({"reason": "Selected automatically (moved clear of a better clip)"}, a, b, segments,
                                   topic, default_tags))
    while len(kept) < count:                                    # the AI suggested fewer usable reels than needed
        try:
            a, b = _free_window(segments, lo, hi, duration, taken())
        except PlanError:
            break
        kept.append(_make_clip({"reason": "Selected automatically (the AI suggested fewer reels than needed)"}, a, b,
                               segments, topic, default_tags))
    if not kept:
        raise PlanError("There is no free part of the video left for another reel.")
    kept.sort(key=lambda c: (-selection.rank_value(c, topic), c["start"]))
    used.extend((k["start"], k["end"]) for k in kept)
    return kept


def normalize_plan(plan: dict, segments: list, duration: float, full_text: str, one_word: bool = False,
                   rs: dict | None = None, topic: str = "") -> dict:
    if not isinstance(plan, dict):
        raise PlanError("The model returned JSON that is not an object.")
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
    rs = rs or selection.reel_settings({})
    topic = selection.clean_topic(topic)
    used, notes = [], []
    for key, (lo, hi), count in (("main_reels", MAIN_RANGE, N_MAIN), ("short_reels", (rs["min_s"], rs["max_s"]), rs["count"])):
        items = plan.get(key) or plan.get(key[:-1]) or []       # tolerate "main_reel" from a small model
        if isinstance(items, dict):
            items = [items]
        items = [i for i in items if isinstance(i, dict)][:selection.MAX_COUNT * 2 + 4]
        out[key] = _choose(items, key[:-6], lo, hi, count, used, segments, duration, topic, out["hashtags"])
    if len(out["short_reels"]) < rs["count"]:
        notes.append(f"Only {len(out['short_reels'])} of the {rs['count']} short reels you asked for fit in this video "
                     f"(each {rs['min_s']:.0f}-{rs['max_s']:.0f} s, without overlapping).")
    if topic and not any(c["topic_hits"] > 0 for c in out["main_reels"] + out["short_reels"]):
        notes.append(f"No clear moment about \"{topic}\" was found in the talk; the best-scoring reels were used instead.")
    out["reel_settings"], out["topic"], out["notes"], out["selection_v"] = rs, topic, notes, selection.SELECTION_V
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
    for key in ("main_reels", "short_reels"):                # a reel always has a 3-8 word hook from its own words
        for r in out[key]:
            r["hook"] = hooks.ensure_hook(r["hook"], hooks.clip_text(segments, r["start"], r["end"]), one_word,
                                          out["visual_theme"],
                                          " ".join([r.get("reason", ""), r.get("caption", ""), out["main_theme"],
                                                    " ".join(out["themes"])]))
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


def create_plan(llm, cfg: dict, segments: list, duration: float, ctxinfo: dict, log=print, topic: str = "") -> dict:
    full_text = " ".join(s["text"] for s in segments)
    lines = transcript_lines(segments)
    one_word = bool(cfg.get("hook.one_word", False))
    rs, topic = selection.reel_settings(cfg), selection.clean_topic(topic)
    last_err, parts = None, None
    for attempt in range(2):
        extra = f"\nPREVIOUS ATTEMPT WAS REJECTED: {last_err}. Fix this.\n" if last_err else ""
        raw = None
        try:
            if len(lines) <= SINGLE_PASS_CHARS:
                raw = llm.generate_json(_single_prompt(lines, duration, ctxinfo, extra, rs, topic), cfg)
            else:
                chunks = _chunk_segments(segments)
                if parts is None:
                    log(f"Long transcript: analysing in {len(chunks)} parts")
                    parts = [llm.generate_json(_map_prompt(transcript_lines(c), rs, topic, len(chunks), duration), cfg) for c in chunks]
                raw = llm.generate_json(_reduce_prompt(parts, duration, ctxinfo, extra, rs, topic), cfg)
            return normalize_plan(raw, segments, duration, full_text, one_word=one_word, rs=rs, topic=topic)
        except LLMUnavailable:
            raise                                   # Ollama is not reachable: not a plan problem, the job retries
        except (PlanError, LLMError, KeyError, TypeError, ValueError, AttributeError) as e:   # malformed plan shapes too
            last_err = f"{type(e).__name__}: {e}" if not isinstance(e, (PlanError, LLMError)) else str(e)
            log(f"Plan rejected ({last_err}); {'retrying' if attempt == 0 else 'giving up'}", "WARNING")
            log("Raw model output that was rejected: " + raw_preview(raw, e), "WARNING")
    raise PlanError(f"Could not produce a valid content plan: {last_err}")


def raw_preview(raw, err=None, limit: int = 1800) -> str:
    """The model's rejected answer, shortened, so a failed plan can be diagnosed from the job log."""
    if isinstance(raw, (dict, list)):
        text = json.dumps(raw, ensure_ascii=False)
    else:
        text = getattr(err, "raw", None) or (str(raw) if raw is not None else "")
    text = str(text).replace("\n", " ")
    return (text[:limit] + f"... [{len(text)} chars]") if len(text) > limit else (text or "(no output)")


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


# ------------------------------------------------------------------ typed reels / poster instructions
def _words(text: str, n: int) -> str:
    return " ".join((text or "").split()[:n])


HOOK_WORDS, HOOK_CHARS = hooks.MAX_WORDS, hooks.MAX_CHARS
REEL_PROMPT_V = 4          # bump when describe_reel changes, so saved hooks written by an older prompt are redone
POSTER_PROMPT_V = 2        # same for poster_from_note
tidy_hook = hooks.tidy_hook


def describe_reel(llm, cfg: dict, text: str, note: str, plan: dict, log=print) -> dict:
    """Hook, closing line and caption for a reel whose times the producer typed in.

    The hook is a 3-8 word phrase from the speaker's own words (not a one-word label, not a summary); the
    producer's comment guides which one. A hook that is the wrong length or not from the reel is asked for
    again once, then replaced by a phrase extracted from the reel itself, so a typed reel is never lost.
    """
    prompt = (
        "You write the opening hook, closing line and caption for ONE vertical sermon reel. Return ONLY JSON "
        '{"hook":"3-8 words","closing":"max 8 words","caption":"1-2 sentences","hashtags":["#tag"],"reason":"one sentence",'
        '"key_words":["up to 4 single words copied from this reel transcript that carry its message"]}.\n'
        f"Sermon theme: {plan.get('main_theme', '')}\nReel transcript:\n{text[:3000]}\n"
        + (f"Producer's instructions for this reel (follow them): {note}\n" if note else "")
        + "Rules: the hook is a phrase of 3 to 8 words copied from the speaker's own words in this reel transcript "
          "(a striking sentence or its first clause; filler words may be dropped) that makes a viewer want to keep watching; "
          "it is NOT a summary, not a single word, and must not start with 'In this sermon'; "
          "stay faithful to what the speaker said; write in the language of the transcript.")
    raw = {}
    for attempt in range(2):
        try:
            raw = llm.generate_json(prompt if attempt == 0 else
                                    prompt + f"\nYour previous hook was not {hooks.MIN_WORDS}-{HOOK_WORDS} words copied from the transcript. Try again.", cfg)
        except Exception as e:  # LLM down / bad JSON: keep the reel, use plain fallbacks
            log(f"Could not write the hook for a typed reel ({type(e).__name__}); using the transcript's own words", "WARNING")
            raw = {}
            break
        h = str(raw.get("hook") or "")
        if hooks.MIN_WORDS <= len(h.split()) <= HOOK_WORDS and hooks.grounded(h, text):
            break
    hook = hooks.ensure_hook(raw.get("hook") or "", text, bool(cfg.get("hook.one_word", False)),
                             plan.get("visual_theme", ""), plan.get("main_theme", ""))
    tags = [h if h.startswith("#") else f"#{h}" for h in
            (str(x).strip().replace(" ", "") for x in (raw.get("hashtags") or [])) if h][:8]
    return {"hook": hook, "closing": str(raw.get("closing") or "").strip()[:80] or "Follow for more",
            "caption": str(raw.get("caption") or "").strip() or _words(text, 30),
            "hashtags": tags, "reason": str(raw.get("reason") or "").strip() or "Chosen by the producer",
            "key_words": emphasis.grounded_keys(raw.get("key_words"), text) or emphasis.auto_keys(text)}


def poster_from_note(llm, cfg: dict, plan: dict, full_text: str, note: str, log=print) -> dict:
    """Poster text guided by the producer's instructions. Verses and quotes are still only kept when
    the speaker really said them; anything else falls back to the planned poster."""
    base = dict(plan["poster"])
    prompt = (
        'Write the text for ONE sermon poster. Return ONLY JSON {"headline":"max 8 words","verse":"","verse_text":""}.\n'
        f"THE POSTER MUST BE ABOUT THIS (the producer's instructions, they decide the message and tone): {note}\n"
        "The headline must express that message in a short powerful line of your own; do not use a different topic.\n"
        f"Background only (the whole talk): title {plan.get('title')}; theme {plan.get('main_theme')}; "
        f"key points {json.dumps(plan.get('key_points', []), ensure_ascii=False)}\n"
        f"Quotes the speaker really said: {json.dumps(plan.get('quotes', []), ensure_ascii=False)}\n"
        "Rules: 'verse' only if the speaker cited it, else empty; 'verse_text' must be copied exactly from the "
        "speaker's words, else empty.")
    try:
        raw = llm.generate_json(prompt, cfg)
    except Exception as e:
        log(f"Could not apply the poster instructions ({type(e).__name__}); using the planned poster", "WARNING")
        return base
    headline = str(raw.get("headline") or "").strip().strip('"')
    ref = str(raw.get("verse") or "").strip()
    vt = str(raw.get("verse_text") or "").strip()
    if headline:
        base["headline"] = headline[:140]
    if ref and reference_supported(ref, full_text):
        base["subheadline"] = ref
    if vt and quote_in_transcript(vt, full_text):
        base["quote"] = vt
    return base
