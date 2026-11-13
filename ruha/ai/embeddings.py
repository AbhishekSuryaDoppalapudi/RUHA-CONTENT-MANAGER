"""Local embedding providers: Ollama (semantic) and a dependency-free hashing fallback."""
from __future__ import annotations

import hashlib
import math
import re

import requests

HASH_DIM = 512
_TOKEN = re.compile(r"\w+", re.UNICODE)


class HashEmbedder:
    """Deterministic feature-hashing embedding (word unigrams/bigrams + char trigrams).

    Fully offline; captures lexical similarity (good for duplicate detection) but not
    deep semantics. Used when no Ollama embedding model is available.
    """
    name = "hash512"

    def embed(self, texts: list) -> list:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list:
        vec = [0.0] * HASH_DIM
        toks = [t.lower() for t in _TOKEN.findall(text or "")]
        feats = list(toks) + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for t in toks:
            padded = f"#{t}#"
            feats += [padded[i:i + 3] for i in range(len(padded) - 2)] if len(t) > 3 else []
        for f in feats:
            h = int.from_bytes(hashlib.md5(f.encode("utf-8")).digest()[:8], "little")
            vec[h % HASH_DIM] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str):
        self.base, self.model = base_url.rstrip("/"), model
        self.name = f"ollama-{re.sub(r'[^A-Za-z0-9]+', '_', model)}"

    def embed(self, texts: list) -> list:
        out = []
        for t in texts:
            r = requests.post(f"{self.base}/api/embeddings", json={"model": self.model, "prompt": t,
                                                                 "keep_alive": "30s"}, timeout=120)
            r.raise_for_status()
            out.append(r.json()["embedding"])
        return out


def make_embedder(cfg: dict):
    provider = cfg.get("embedding.provider", "auto")
    base, model = cfg.get("llm.base_url", "http://localhost:11434"), cfg.get("embedding.model", "nomic-embed-text")
    if provider in ("auto", "ollama"):
        try:
            r = requests.get(f"{base.rstrip('/')}/api/tags", timeout=1.5)
            names = [m["name"].split(":")[0] for m in r.json().get("models", [])]
            if model.split(":")[0] in names:
                return OllamaEmbedder(base, model)
        except requests.RequestException:
            pass
        if provider == "ollama":
            raise RuntimeError(f"Embedding model '{model}' is not available in Ollama")
    return HashEmbedder()
