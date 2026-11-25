"""Post copy that is different for every clip.

The planner writes a caption for each clip while it plans the whole sermon, so captions drift toward the same wording. Here every
clip gets a caption of its own: the model is shown THAT clip's transcript and the captions already written (this run's, and the
latest published ones), and a caption that is too close to any of them - judged with the same embeddings the memory uses for posts -
is written again with the near-duplicate listed as "do not resemble", once; if it still resembles one, it is kept (the closest call
is flagged in the asset) rather than failing the reel.
"""
from __future__ import annotations

import math
import re

SIMILARITY_LIMIT = 0.82           # the same limit the posts' duplicate check has always used
MAX_TRIES = 2
KINDS = ["caption", "post"]


def cosine(a, b) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def prompt(clip_text: str, plan: dict, previous: list, avoid: list, key_words=None, note: str = "") -> str:
    prev = [p for p in previous if p][-12:]
    return (
        "You write the social media caption for ONE sermon reel. Return ONLY JSON "
        '{"caption":"1-3 short sentences","hashtags":["#tag","#tag"]}.\n'
        f"The sermon's theme: {plan.get('main_theme', '')}\n"
        f"THIS reel's own transcript (write about what is said HERE, nothing else):\n{clip_text[:2500]}\n"
        + (f"Words that matter in this reel: {', '.join(key_words)}\n" if key_words else "")
        + (f"Owner's note: {note}\n" if note else "")
        + ("Captions already written for other reels - yours must open differently and take a different angle, "
           "not reuse their wording:\n- " + "\n- ".join(p[:220] for p in prev) + "\n" if prev else "")
        + ("TOO SIMILAR - do NOT write anything like these:\n- " + "\n- ".join(a[:220] for a in avoid) + "\n" if avoid else "")
        + "Rules: stay faithful to the speaker (no invented quotes or Bible references); start with a hook or a question drawn from this "
          "reel; end with a short call to action; 3 to 6 hashtags; write in the language of the transcript.")


def _tags(raw) -> list:
    return [h if h.startswith("#") else f"#{h}" for h in
            (str(x).strip().replace(" ", "") for x in (raw or []) if isinstance(x, (str, int))) if h][:8]


def write(llm, cfg: dict, clip_text: str, plan: dict, previous: list, avoid=(), key_words=None, note: str = "") -> dict:
    """One caption for one clip: {"caption", "hashtags"} (the model's answer cleaned up; empty caption raises ValueError)."""
    raw = llm.generate_json(prompt(clip_text, plan, previous, list(avoid), key_words, note), cfg)
    caption = " ".join(str(raw.get("caption") or "").split())
    if not caption:
        raise ValueError("the model returned no caption")
    return {"caption": caption[:600], "hashtags": _tags(raw.get("hashtags"))}


class Checker:
    """Compares a caption with earlier ones: persisted ones through the memory's own similarity search, and the ones written
    earlier in this run through the memory's embedder (they are not indexed until their reel exists)."""

    def __init__(self, memory, limit: float = SIMILARITY_LIMIT):
        self.memory, self.limit = memory, limit

    def nearest(self, caption: str, local: list, title: str = "") -> list:
        """[{"score", "text", "asset_id"}] of everything at or above the limit, closest first. Memory holds a reel as its
        title, a line break and its caption, so that is what is looked up there."""
        text = caption
        hits = []
        try:
            hits += [{"score": float(h["score"]), "text": h["text"], "asset_id": h.get("asset_id")}
                     for h in self.memory.similar(chr(10).join(x for x in (title, caption) if x), KINDS, self.limit, n=3)]
        except Exception:
            pass
        loc = [t for t in local if t]
        if loc:
            try:
                vecs = self.memory.embedder().embed([text] + loc)
                for t, v in zip(loc, vecs[1:]):
                    sc = cosine(vecs[0], v)
                    if sc >= self.limit:
                        hits.append({"score": sc, "text": t, "asset_id": None})
            except Exception:
                pass
        return sorted(hits, key=lambda h: -h["score"])


def unique_copy(llm, cfg: dict, memory, clip_text: str, plan: dict, local: list, recent=(), key_words=None, note: str = "",
                log=print, title: str = "") -> tuple:
    """A caption for one clip that does not repeat the ones in ``local`` (this run's) or in memory.

    Returns (copy, similar, tries): ``copy`` {"caption", "hashtags"}; ``similar`` [] when the caption is distinct, else the
    near-duplicates it still resembles after the retry; ``tries`` how many captions were written."""
    check = Checker(memory)
    previous = [*recent, *local]
    avoid, best, tries = [], None, 0
    for attempt in range(MAX_TRIES):
        cp = write(llm, cfg, clip_text, plan, previous, avoid, key_words, note)
        tries += 1
        sims = check.nearest(cp["caption"], local, title)
        if not sims:
            return cp, [], tries
        if best is None or sims[0]["score"] < best[1][0]["score"]:
            best = (cp, sims)
        log(f"Caption resembles an earlier one (score {sims[0]['score']:.2f}); writing it again" if attempt + 1 < MAX_TRIES
            else f"Caption still resembles an earlier one (score {sims[0]['score']:.2f}); keeping the closest-to-distinct version", "WARNING")
        avoid = [h["text"] for h in sims] + [cp["caption"]]
    return best[0], [{"asset_id": h["asset_id"], "score": round(h["score"], 2)} for h in best[1]], tries
