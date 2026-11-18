"""Local AI memory: SQLite records (memory_references) + ChromaDB vectors.

Retrieval only - nothing here trains or fine-tunes a model. Preferences, approvals and
rejections are retrieved and injected into prompts.
"""
from __future__ import annotations

import json
import threading
import uuid
from collections import Counter

from sqlalchemy import func, or_

from ruha.config import STARTER_TOPICS
from ruha.models import (Feedback, GeneratedAsset, MemoryReference, Poster, Project, SocialPost, Transcript)

KINDS = ["transcript", "summary", "theme", "quote", "caption", "post", "preference", "feedback"]


def _clean_meta(meta: dict) -> dict:
    out = {}
    for k, v in (meta or {}).items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False)
    return out


class MemoryStore:
    def __init__(self, ctx):
        self.ctx = ctx
        self._client = None
        self._lock = threading.RLock()
        self._embedder = None
        self._embedder_key = None

    # ---- plumbing ---------------------------------------------------------
    def close(self):
        with self._lock:
            self._client = None

    def client(self):
        with self._lock:
            if self._client is None:
                import chromadb
                from chromadb.config import Settings as CS
                self._client = chromadb.PersistentClient(
                    path=str(self.ctx.settings.chroma_dir), settings=CS(anonymized_telemetry=False))
            return self._client

    def embedder(self):
        cfg = self.ctx.config.all()
        key = (cfg.get("embedding.provider"), cfg.get("embedding.model"), cfg.get("llm.base_url"))
        with self._lock:
            if self._embedder is None or key != self._embedder_key:
                self._embedder = self.ctx.services.embedder_factory(cfg)
                self._embedder_key = key
            return self._embedder

    def collection(self):
        emb = self.embedder()
        return self.client().get_or_create_collection(f"ruha_memory_{emb.name}",
                                                      metadata={"hnsw:space": "cosine"})

    def _all_collections(self):
        c = self.client()
        out = []
        for col in c.list_collections():
            name = col if isinstance(col, str) else col.name
            if name.startswith("ruha_memory_"):
                out.append(c.get_collection(name))
        return out

    def _delete_vectors(self, vector_ids: list):
        if not vector_ids:
            return
        for col in self._all_collections():
            for i in range(0, len(vector_ids), 500):
                col.delete(ids=vector_ids[i:i + 500])

    # ---- writing ----------------------------------------------------------
    def add(self, kind: str, text: str, project_id=None, asset_id=None, meta: dict | None = None):
        return self.add_many([(kind, text, project_id, asset_id, meta or {})])

    def add_many(self, items: list) -> list:
        items = [(k, t.strip(), p, a, m) for k, t, p, a, m in items if t and t.strip()]
        if not items:
            return []
        vids = [uuid.uuid4().hex for _ in items]
        embs = self.embedder().embed([t for _, t, *_ in items])
        with self._lock:
            col = self.collection()
            col.add(ids=vids, embeddings=embs, documents=[t for _, t, *_ in items],
                    metadatas=[_clean_meta({**m, "kind": k, "project_id": p or 0, "asset_id": a or 0})
                               for k, _, p, a, m in items])
        with self.ctx.db.session() as s:
            for vid, (k, t, p, a, m) in zip(vids, items):
                s.add(MemoryReference(vector_id=vid, kind=k, project_id=p, asset_id=a, text=t,
                                      metadata_json=json.dumps(m, ensure_ascii=False)))
        return vids

    def reindex(self) -> int:
        """Rebuild the active vector collection from the stored texts (e.g. after switching embedder)."""
        with self.ctx.db.session() as s:
            rows = [(r.vector_id, r.kind, r.project_id, r.asset_id, r.text, r.meta)
                    for r in s.query(MemoryReference).all()]
        col = self.collection()
        if not rows:
            return 0
        existing = set(col.get(ids=[r[0] for r in rows[:5000]])["ids"]) if rows else set()
        todo = [r for r in rows if r[0] not in existing]
        for i in range(0, len(todo), 64):
            batch = todo[i:i + 64]
            col.add(ids=[b[0] for b in batch], embeddings=self.embedder().embed([b[4] for b in batch]),
                    documents=[b[4] for b in batch],
                    metadatas=[_clean_meta({**b[5], "kind": b[1], "project_id": b[2] or 0,
                                            "asset_id": b[3] or 0}) for b in batch])
        return len(todo)

    # ---- reading ----------------------------------------------------------
    def search(self, query: str, kinds: list | None = None, n: int = 10, project_id: int | None = None,
               exclude_project: int | None = None, outcome: str | None = None) -> list:
        if not query.strip():
            return []
        col = self.collection()
        total = col.count()
        if total == 0:
            with self.ctx.db.session() as s:
                has_refs = s.query(func.count(MemoryReference.id)).scalar()
            if has_refs:
                self.reindex()
                total = col.count()
        if total == 0:
            return []
        conds = []
        if kinds:
            conds.append({"kind": {"$in": list(kinds)}})
        if project_id:
            conds.append({"project_id": project_id})
        if outcome:
            conds.append({"outcome": outcome})
        where = None if not conds else (conds[0] if len(conds) == 1 else {"$and": conds})
        res = col.query(query_embeddings=self.embedder().embed([query]),
                        n_results=min(n + (20 if exclude_project else 0), total), where=where)
        hits = []
        for i, vid in enumerate(res["ids"][0]):
            md = res["metadatas"][0][i] or {}
            if exclude_project and md.get("project_id") == exclude_project:
                continue
            hits.append({"vector_id": vid, "text": res["documents"][0][i], "kind": md.get("kind"),
                         "project_id": md.get("project_id") or None, "asset_id": md.get("asset_id") or None,
                         "score": round(1.0 - float(res["distances"][0][i]), 4), "meta": md})
        return hits[:n]

    def similar(self, text: str, kinds: list, threshold: float = 0.8, n: int = 5,
                exclude_asset: int | None = None) -> list:
        hits = [h for h in self.search(text, kinds=kinds, n=n + 1) if h["score"] >= threshold]
        if exclude_asset:
            hits = [h for h in hits if h["asset_id"] != exclude_asset]
        return hits[:n]

    # ---- indexing helpers -------------------------------------------------
    def index_transcript(self, transcript_id: int) -> int:
        with self.ctx.db.session() as s:
            t = s.get(Transcript, transcript_id)
            if not t:
                return 0
            pid = t.project_id
            project = s.get(Project, pid)
            pname = project.name if project else ""
            segs = [(g.start, g.end, g.text) for g in t.segments]
            summary, themes, quotes = t.summary, t.themes, t.quotes
        self.delete(project_id=pid, kinds=["transcript", "summary", "theme", "quote"])
        items, cur, cur_start, cur_end = [], [], None, None
        for st, en, tx in segs:
            if cur_start is None:
                cur_start = st
            cur.append(tx)
            cur_end = en
            if sum(len(x) for x in cur) >= 450:
                items.append(("transcript", " ".join(cur), pid, None,
                              {"start": cur_start, "end": cur_end, "project": pname}))
                cur, cur_start = [], None
        if cur:
            items.append(("transcript", " ".join(cur), pid, None,
                          {"start": cur_start, "end": cur_end, "project": pname}))
        if summary:
            items.append(("summary", summary, pid, None, {"project": pname}))
        items += [("theme", th, pid, None, {"project": pname}) for th in themes]
        items += [("quote", q if isinstance(q, str) else q.get("text", ""), pid, None, {"project": pname})
                  for q in quotes]
        return len(self.add_many(items))

    def index_asset(self, asset_id: int) -> None:
        with self.ctx.db.session() as s:
            a = s.get(GeneratedAsset, asset_id)
            if not a or a.kind not in ("main_reel", "short_reel", "image_reel", "poster", "social_post", "creative"):
                return
            kind = "post" if a.kind in ("social_post", "poster") else "caption"
            if a.kind == "social_post":      # same representation the duplicate check compares against
                sp = s.query(SocialPost).filter_by(asset_id=asset_id).first()
                text = f"{sp.headline}\n{sp.body}" if sp else a.title
            elif a.kind == "poster":
                po = s.query(Poster).filter_by(asset_id=asset_id).first()
                text = "\n".join(x for x in ([po.headline, po.quote] if po else [a.title]) if x)
            else:
                text = "\n".join(x for x in [a.title, a.caption] if x)
            pid, status, akind = a.project_id, a.status, a.kind
        self.delete(asset_id=asset_id)
        self.add(kind, text, pid, asset_id, {"asset_kind": akind, "outcome": status})

    def set_outcome(self, asset_id: int, outcome: str) -> None:
        with self.ctx.db.session() as s:
            refs = s.query(MemoryReference).filter_by(asset_id=asset_id).all()
            vids = [r.vector_id for r in refs]
            for r in refs:
                m = r.meta
                m["outcome"] = outcome
                r.metadata_json = json.dumps(m)
        for col in self._all_collections():
            found = col.get(ids=vids) if vids else {"ids": [], "metadatas": []}
            if found["ids"]:
                col.update(ids=found["ids"], metadatas=[_clean_meta({**(m or {}), "outcome": outcome})
                                                        for m in found["metadatas"]])

    def remember_preference(self, text: str, category: str = "general") -> list:
        return self.add("preference", text, None, None, {"category": category})

    # ---- prompt context (retrieval-based learning) ------------------------
    def prompt_context(self, project_id: int, query_text: str) -> dict:
        with self.ctx.db.session() as s:
            prefs = [r.text for r in s.query(MemoryReference).filter_by(kind="preference")
                     .order_by(MemoryReference.id.desc()).limit(12)]
            recent = [r.text for r in s.query(MemoryReference).filter(
                MemoryReference.kind.in_(["caption", "post"]), MemoryReference.project_id != project_id)
                .order_by(MemoryReference.id.desc()).limit(10)]
            rejected_notes = [f"{f.snapshot[:160]} -> {f.note}".strip(" ->") for f in
                              s.query(Feedback).filter(Feedback.kind.in_(["negative", "revision"]))
                              .order_by(Feedback.id.desc()).limit(8)]
        approved, rejected = [], []
        try:
            approved = [h["text"] for h in self.search(query_text[:2000], kinds=["caption", "post"], n=3,
                                                       outcome="approved")]
            rejected = [h["text"] for h in self.search(query_text[:2000], kinds=["caption", "post"], n=3,
                                                       outcome="rejected")]
        except Exception:
            pass
        return {"preferences": prefs, "recent_content": recent, "approved_examples": approved,
                "rejected_examples": rejected, "rejection_notes": rejected_notes}

    # ---- topics -----------------------------------------------------------
    def covered_topics(self) -> list:
        counter, last = Counter(), {}
        with self.ctx.db.session() as s:
            for t, pname in s.query(Transcript, Project.name).join(Project, Project.id == Transcript.project_id):
                for th in t.themes:
                    key = str(th).strip().lower()
                    if key:
                        counter[key] += 1
                        last.setdefault(key, pname)
        return [{"topic": k, "count": v, "example_project": last[k]} for k, v in counter.most_common()]

    def recommend_topics(self, limit: int = 8) -> dict:
        covered = {c["topic"] for c in self.covered_topics()}
        covered_words = {w for c in covered for w in c.split()}
        fresh = [t for t in STARTER_TOPICS if t.lower() not in covered and t.lower() not in covered_words]
        return {"basis": "starter topic list minus themes found in your stored transcripts",
                "covered_count": len(covered), "suggestions": fresh[:limit]}

    # ---- inspect / export / delete ---------------------------------------
    def stats(self) -> dict:
        with self.ctx.db.session() as s:
            rows = s.query(MemoryReference.kind, func.count()).group_by(MemoryReference.kind).all()
        return {"total": sum(c for _, c in rows), "by_kind": {k: c for k, c in rows},
                "embedder": self.embedder().name}

    def list_refs(self, kind=None, q=None, project_id=None, limit=50, offset=0) -> dict:
        with self.ctx.db.session() as s:
            qs = s.query(MemoryReference)
            if kind:
                qs = qs.filter(MemoryReference.kind == kind)
            if project_id:
                qs = qs.filter(MemoryReference.project_id == project_id)
            if q:
                qs = qs.filter(MemoryReference.text.ilike(f"%{q}%"))
            total = qs.count()
            rows = qs.order_by(MemoryReference.id.desc()).offset(offset).limit(limit).all()
            return {"total": total, "items": [{"id": r.id, "kind": r.kind, "project_id": r.project_id,
                                               "asset_id": r.asset_id, "text": r.text, "meta": r.meta,
                                               "created_at": r.created_at.isoformat()} for r in rows]}

    def export(self) -> dict:
        data = self.list_refs(limit=10**7)["items"]
        with self.ctx.db.session() as s:
            fb = [{"asset_id": f.asset_id, "kind": f.kind, "note": f.note, "snapshot": f.snapshot,
                   "created_at": f.created_at.isoformat()} for f in s.query(Feedback).all()]
        return {"memory": data, "feedback": fb, "stats": self.stats()}

    def delete(self, ids: list | None = None, kinds: list | None = None, project_id=None,
               asset_id=None, everything: bool = False) -> int:
        with self.ctx.db.session() as s:
            q = s.query(MemoryReference)
            if not everything:
                if not any([ids, kinds, project_id, asset_id]):
                    return 0
                if ids:
                    q = q.filter(MemoryReference.id.in_(ids))
                if kinds:
                    q = q.filter(MemoryReference.kind.in_(kinds))
                if project_id:
                    q = q.filter(MemoryReference.project_id == project_id)
                if asset_id:
                    q = q.filter(MemoryReference.asset_id == asset_id)
            rows = q.all()
            vids = [r.vector_id for r in rows]
            for r in rows:
                s.delete(r)
            if everything:
                s.query(Feedback).delete()
        self._delete_vectors(vids)
        if everything:
            for col in self._all_collections():
                try:
                    self.client().delete_collection(col.name)
                except Exception:
                    pass
        return len(vids)
