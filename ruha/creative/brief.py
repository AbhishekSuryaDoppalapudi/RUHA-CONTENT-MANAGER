"""Creative intelligence: a structured brief derived from the actual transcript, then distinct concepts from it.

The LLM supplies meaning (message, audience, three visual metaphors, three headlines). Everything that must be
trustworthy is enforced in code: scripture references are only ever taken from what the speaker said (planner guards),
headlines are length-limited and de-duplicated, and each concept gets its own subject, lighting, palette, composition."""
from __future__ import annotations

import re

from ruha.creative import formats, styles
from ruha.services import planner, themes

THEME_NAMES = list(themes.THEMES)
REF_RE = re.compile(r"\b(?:[1-3]\s)?[A-Z][a-z]+(?:\s+of\s+[A-Z][a-z]+)?\s+\d{1,3}:\d{1,3}(?:[-–]\d{1,3})?")
# Subjects an image model must not be asked to draw: sacred figures (disrespectful or uncanny when generated),
# demonic imagery, crowds and text. The brief falls back to curated symbolic scenes instead.
UNSAFE_SUBJECT = re.compile(r"\b(angels?|jesus|christ|god|lord|holy spirit|trinity|satan|devil|demons?|lucifer|"
                           r"crowds?|mob|text|words?|letters?|signs?|banners?|books?|bible)\b", re.I)
BAD_OPENERS = ("in this sermon", "this sermon", "in this message", "this message", "today we", "in this video")


def _clean_headline(h: str) -> str:
    h = " ".join(str(h or "").replace("“", "").replace("”", "").replace('"', "").split()).strip(" .")
    return h


def _words(t: str) -> set:
    return {w for w in re.findall(r"[a-z']{3,}", (t or "").lower())}


def similar(a: str, b: str, limit: float = 0.6) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / max(1, len(wa | wb)) >= limit


def shorten(text: str, max_words: int) -> str:
    """Fit a headline to a format. Prefer a clean clause; only as a last resort cut words (QC flags that)."""
    t = _clean_headline(text)
    if len(t.split()) <= max_words:
        return t
    for sep in (":", " - ", " – ", ",", ";", "?", "."):
        part = t.split(sep)[0].strip(" ,;:-–")
        if 3 <= len(part.split()) <= max_words:
            return part
    return " ".join(t.split()[:max_words]).strip(" ,;:-–")


def valid_reference(ref: str, full_text: str) -> str:
    """A scripture reference survives only if its book name is actually spoken in the video."""
    ref = (ref or "").strip()
    return ref if ref and planner.reference_supported(ref, full_text) else ""


def _strip_unspoken_refs(text: str, full_text: str) -> str:
    def keep(m):
        return m.group(0) if planner.reference_supported(m.group(0), full_text) else ""
    return " ".join(REF_RE.sub(keep, text or "").split())


def plan_reference(plan: dict, full_text: str) -> str:
    cands = [(plan.get("poster") or {}).get("subheadline", "")]
    cands += [p.get("reference", "") for p in plan.get("encouragement_posts", [])]
    for c in cands:
        v = valid_reference(c, full_text)
        if v:
            return v
    return ""


def _fallback_headlines(plan: dict) -> list:
    pool = [(plan.get("poster") or {}).get("headline", "")]
    pool += [p.get("headline") or p.get("hook", "") for p in plan.get("encouragement_posts", [])]
    pool += [c.get("hook", "") for c in plan.get("main_reels", []) + plan.get("short_reels", [])]
    pool += list(plan.get("key_points", []))
    out = []
    for p in pool:
        p = _clean_headline(p)
        if p and not p.lower().startswith(BAD_OPENERS) and not any(similar(p, o) for o in out):
            out.append(p)
    return out


def _prompt(plan: dict, excerpt: str, name: str) -> str:
    pts = "; ".join(plan.get("key_points", [])[:6])
    return (
        "You are the creative director of a church media team. Read this sermon and write a creative brief for "
        "social-media artwork.\n"
        f"TITLE: {plan.get('title') or name}\nMAIN THEME: {plan.get('main_theme', '')}\nSUMMARY: {plan.get('summary', '')}\n"
        f"KEY POINTS: {pts}\nTRANSCRIPT EXCERPT: {excerpt[:3500]}\n\n"
        "Return ONLY JSON with these keys:\n"
        '{"message": "the single most important truth, one sentence, faithful to the speaker",\n'
        f' "emotional_theme": one of {THEME_NAMES},\n'
        ' "audience": "who this speaks to, a short phrase",\n'
        ' "visual_metaphors": [{"subject": "...", "environment": "..."}, ... exactly 3 DIFFERENT concrete scenes],\n'
        ' "headlines": ["...", "...", "..."],\n'
        ' "supporting_text": "one short line, max 14 words",\n'
        ' "creative_direction": "one sentence on the mood and look"}\n'
        "Rules: the 3 visual metaphors are real photographable or paintable scenes (landscape, object, a person seen "
        "from behind) - never text, never a face of Jesus or God, never a crowd. The 3 headlines are DIFFERENT: one "
        "curiosity question, one bold statement, one short memorable phrase from the speaker's message; 3 to 8 words "
        "each, mixed case, never starting with 'In this sermon'. Never invent Bible verses, references or quotations; "
        "stay faithful to what was actually said."
    )


def build_brief(llm, cfg, plan: dict, transcript_text: str, project_name: str = "") -> dict:
    """Brief from the transcript. ``source`` says whether the LLM or the stored plan produced it."""
    theme = themes.resolve(plan.get("visual_theme"), f"{plan.get('main_theme', '')} {plan.get('summary', '')}")
    raw = {}
    source = "llm"
    try:
        raw = llm.generate_json(_prompt(plan, transcript_text, project_name), cfg) or {}
    except Exception:
        raw, source = {}, "plan"
    if isinstance(raw.get("emotional_theme"), str) and raw["emotional_theme"].lower() in themes.THEMES:
        theme = raw["emotional_theme"].lower()

    # headlines: model's, cleaned, guarded, de-duplicated, topped up from the stored plan
    heads: list = []
    for h in list(raw.get("headlines") or []) + _fallback_headlines(plan):
        h = _strip_unspoken_refs(_clean_headline(h), transcript_text)
        if not h or h.lower().startswith(BAD_OPENERS) or len(h.split()) < 2:
            continue
        if any(similar(h, o) for o in heads):
            continue
        heads.append(h)
        if len(heads) >= 3 and source == "llm":
            break
    if not heads:
        heads = [_clean_headline(plan.get("main_theme") or plan.get("title") or project_name or "Be encouraged")]
    while len(heads) < 3:                                         # never fewer than three distinct options
        heads.append(heads[len(heads) % max(1, len(heads))])
    if len({h.lower() for h in heads}) < 3:
        source = source + "+repeat"

    # visual metaphors: model's if usable, else the curated table; always three, all different
    metas = []
    for m in raw.get("visual_metaphors") or []:
        if not (isinstance(m, dict) and str(m.get("subject", "")).strip()):
            continue
        subj, env = str(m["subject"]).strip()[:140], str(m.get("environment", "")).strip()[:140]
        if UNSAFE_SUBJECT.search(subj) or UNSAFE_SUBJECT.search(env) or len(subj.split()) < 2:
            continue
        if any(similar(f"{subj} {env}", f"{x['subject']} {x['environment']}", 0.4) for x in metas):
            continue                                              # three near-identical scenes are not three concepts
        metas.append({"subject": subj, "environment": env})
    for subj, env in styles.METAPHORS.get(theme, styles.METAPHORS["faith"]):
        if len(metas) >= 3:
            break
        if not any(similar(subj, x["subject"], 0.5) for x in metas):
            metas.append({"subject": subj, "environment": env})
    metas = metas[:3]

    ref = plan_reference(plan, transcript_text)
    support = _strip_unspoken_refs(_clean_headline(raw.get("supporting_text", "")), transcript_text)
    if len(support.split()) > 16:
        support = shorten(support, 14)
    message = _clean_headline(raw.get("message") or plan.get("main_theme") or plan.get("summary") or "")
    brief = {
        "message": message,
        "emotional_theme": theme,
        "audience": str(raw.get("audience") or "people facing a hard season who need to hear this").strip()[:140],
        "visual_metaphor": metas[0]["subject"] if metas else "",
        "visual_subject": metas[0]["subject"] if metas else "",
        "environment": metas[0]["environment"] if metas else "",
        "lighting": "", "colour_palette": "", "composition": "",
        "headline": heads[0],
        "supporting_text": support,
        "scripture_reference": ref,
        "creative_direction": str(raw.get("creative_direction") or f"A {theme}-toned, reverent and cinematic look").strip()[:240],
        "candidates": {"headlines": heads, "metaphors": metas, "key_points": list(plan.get("key_points", []))[:8]},
        "source": source,
    }
    return brief


def normalise_edited_brief(brief: dict) -> dict:
    """Light validation for a brief the user edited in the workspace."""
    b = dict(brief)
    th = str(b.get("emotional_theme", "")).lower()
    b["emotional_theme"] = th if th in themes.THEMES else themes.DEFAULT
    c = b.setdefault("candidates", {})
    c.setdefault("headlines", [b.get("headline", "")] * 3)
    c.setdefault("metaphors", [{"subject": b.get("visual_subject", ""), "environment": b.get("environment", "")}])
    return b


def build_prompt(style: dict, subject: str, environment: str, lighting: str, palette: str, zone: str) -> tuple:
    """(prompt, negative) for the image model. Short on purpose (SD 1.5 reads ~75 tokens); no words to render."""
    human = style.get("human") or any(w in subject.lower() for w in styles.HUMAN_WORDS)
    parts = [style["medium"], subject, environment, lighting, palette, formats.copy_space_phrase(zone), style["finish"]]
    if human:
        parts.insert(2, "seen from behind or in silhouette, face not visible")
    prompt = ", ".join(p.strip() for p in parts if p and p.strip())
    neg = ", ".join(x for x in (styles.NEGATIVE_BASE, style.get("negative", "")) if x)
    return prompt, neg


def expand_concepts(brief: dict, fmt_key: str, style_ids: list | None = None, seeds: list | None = None) -> list:
    """One concept per style. Each differs in style, subject, environment, lighting, palette, composition, headline."""
    fmt = formats.FORMATS[fmt_key]
    ids = [s for s in (style_ids or styles.DEFAULT_TRIO) if s in styles.STYLES] or styles.DEFAULT_TRIO
    theme = brief.get("emotional_theme") or themes.DEFAULT
    metas = (brief.get("candidates") or {}).get("metaphors") or [
        {"subject": brief.get("visual_subject", ""), "environment": brief.get("environment", "")}]
    heads = (brief.get("candidates") or {}).get("headlines") or [brief.get("headline", "")]
    pal = styles.PALETTES.get(theme, styles.PALETTES["faith"])
    cls = formats.aspect_class(fmt["size"])
    out, used = [], set()
    for i, sid in enumerate(ids):
        st = styles.STYLES[sid]
        j = i
        while metas and (metas[j % len(metas)]["subject"].lower() in used) and j < i + len(metas):
            j += 1
        meta = metas[j % len(metas)] if metas else {"subject": "", "environment": ""}
        used.add(meta["subject"].lower())
        zone = st["zones"][cls]
        lighting = st["lighting"][i % len(st["lighting"])]
        palette, accent = pal[i % len(pal)]
        prompt, neg = build_prompt(st, meta["subject"], meta["environment"], lighting, palette, zone)
        headline = shorten(heads[i % len(heads)], fmt["max_words"])
        out.append({
            "style": sid, "style_name": st["name"], "treatment": st["treatment"], "visual_source": st["visual_source"],
            "format": fmt_key, "zone": zone,
            "message": brief.get("message", ""), "emotional_theme": theme, "audience": brief.get("audience", ""),
            "visual_metaphor": meta["subject"], "visual_subject": meta["subject"], "environment": meta["environment"],
            "lighting": lighting, "colour_palette": palette, "accent": accent,
            "composition": formats.copy_space_phrase(zone),
            "headline": headline, "supporting_text": "" if fmt_key == "thumbnail" else brief.get("supporting_text", ""),
            "scripture_reference": brief.get("scripture_reference", ""),
            "creative_direction": brief.get("creative_direction", ""),
            "prompt": prompt, "negative": neg,
            "seed": (seeds[i] if seeds and i < len(seeds) else 1000 + i * 7919),
        })
    return out


def carousel_slides(brief: dict, n: int = 5) -> list:
    """Slide copy: hook, the sermon's own key points, then scripture + closing. Nothing invented."""
    pts = [shorten(p, 14) for p in (brief.get("candidates") or {}).get("key_points", []) if p][: max(0, n - 2)]
    slides = [{"role": "hook", "headline": brief.get("headline", ""), "supporting_text": brief.get("supporting_text", ""),
               "scripture_reference": ""}]
    for p in pts:
        slides.append({"role": "point", "headline": p, "supporting_text": "", "scripture_reference": ""})
    slides.append({"role": "close", "headline": shorten((brief.get("candidates") or {}).get("headlines", [""])[-1], 8),
                   "supporting_text": brief.get("message", "")[:120], "scripture_reference": brief.get("scripture_reference", "")})
    return slides[:n]
