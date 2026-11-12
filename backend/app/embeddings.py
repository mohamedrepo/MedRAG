"""Pluggable embedding provider.

Default: deterministic local hashed n-gram vectors (no network, no model download).
Optional: any OpenAI-compatible /embeddings endpoint (LM Studio, Ollama, etc.).

Vectors are for semantic discovery only — never the authoritative price store.
"""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

import httpx
import numpy as np

from app.config import settings
from app.normalization import normalize_query


class EmbeddingProvider(ABC):
    name: str = "base"
    dim: int = 256

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray: ...

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


class LocalHashEmbedding(EmbeddingProvider):
    """Character n-gram hashing with signed projections.

    Deterministic, multilingual (Arabic + English), fast, and good enough
    to pull near names together when lexical match is weak. Not a replacement
    for a real multilingual model — configure EMBEDDING_PROVIDER=openai for that.
    """

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.EMBEDDING_DIM
        self.name = "local-hash-v1"

    def _hash_ngrams(self, text: str, n: int) -> list[int]:
        grams = []
        padded = f" {text} "
        for i in range(len(padded) - n + 1):
            g = padded[i : i + n]
            h = int.from_bytes(hashlib.md5(g.encode("utf-8")).digest()[:8], "little")
            grams.append(h)
        return grams

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, raw in enumerate(texts):
            t = normalize_query(raw)
            if not t:
                continue
            vec = np.zeros(self.dim, dtype=np.float32)
            for n in (2, 3, 4):
                for h in self._hash_ngrams(t, n):
                    idx = h % self.dim
                    sign = 1.0 if (h >> 63) & 1 else -1.0
                    vec[idx] += sign / math.sqrt(n)
            # word-level features
            for w in t.split():
                hw = int.from_bytes(hashlib.md5(w.encode()).digest()[:8], "little")
                vec[hw % self.dim] += 1.0
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            out[i] = vec
        return out


class OpenAIEmbedding(EmbeddingProvider):
    def __init__(self) -> None:
        self.name = settings.EMBEDDING_MODEL
        self.dim = settings.EMBEDDING_DIM
        self.base = settings.EMBEDDING_API_BASE.rstrip("/")
        self.key = settings.EMBEDDING_API_KEY
        self.model = settings.EMBEDDING_MODEL

    def embed(self, texts: list[str]) -> np.ndarray:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        resp = httpx.post(
            f"{self.base}/embeddings",
            json={"model": self.model, "input": texts},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        vecs = [d["embedding"] for d in data]
        arr = np.array(vecs, dtype=np.float32)
        if arr.shape[1] != self.dim:
            # pad or truncate to configured dim
            out = np.zeros((arr.shape[0], self.dim), dtype=np.float32)
            d = min(self.dim, arr.shape[1])
            out[:, :d] = arr[:, :d]
            norms = np.linalg.norm(out, axis=1, keepdims=True)
            norms[norms == 0] = 1
            return out / norms
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return arr / norms


def get_embedder() -> EmbeddingProvider:
    if settings.EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbedding()
    return LocalHashEmbedding()


def pack_vector(vec: np.ndarray) -> str:
    """Store as compact base64 of float32 bytes."""
    import base64

    return base64.b64encode(vec.astype(np.float32).tobytes()).decode("ascii")


def unpack_vector(s: str | None) -> np.ndarray | None:
    if not s:
        return None
    import base64

    try:
        raw = base64.b64decode(s)
        return np.frombuffer(raw, dtype=np.float32)
    except Exception:
        return None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
