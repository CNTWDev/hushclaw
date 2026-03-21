"""Vector embedding storage and cosine similarity search.

Backends (in descending preference):
  1. local  — TF-IDF style sparse embedding, pure stdlib (default)
  2. ollama — nomic-embed-text via local HTTP
  3. openai — OpenAI embeddings API (urllib)
  4. None   — falls back to FTS-only
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import struct
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path


# ---------------------------------------------------------------------------
# Local TF-IDF embedding (no external deps)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z\u4e00-\u9fff]+", text.lower())


def _local_embed(text: str, dim: int = 512) -> list[float]:
    """Deterministic hashed TF-IDF style embedding."""
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * dim
    tf = Counter(tokens)
    vec = [0.0] * dim
    for token, count in tf.items():
        h = hash(token) % dim
        # Use multiple hashes to spread signal
        for seed in range(4):
            idx = (h + seed * 97) % dim
            vec[idx] += count / len(tokens)
    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ---------------------------------------------------------------------------
# Ollama embedding
# ---------------------------------------------------------------------------

def _ollama_embed(text: str, model: str = "nomic-embed-text") -> list[float] | None:
    try:
        data = json.dumps({"model": model, "prompt": text}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/embeddings",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("embedding")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OpenAI embedding (urllib, no SDK)
# ---------------------------------------------------------------------------

def _openai_embed(text: str, api_key: str, model: str = "text-embedding-3-small") -> list[float] | None:
    try:
        data = json.dumps({"input": text, "model": model}).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result["data"][0]["embedding"]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pack/unpack helpers
# ---------------------------------------------------------------------------

def _pack(floats: list[float]) -> bytes:
    return struct.pack(f"{len(floats)}f", *floats)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------

class VectorStore:
    def __init__(self, conn: sqlite3.Connection, embed_provider: str = "local",
                 api_key: str = "") -> None:
        self.conn = conn
        self.embed_provider = embed_provider
        self.api_key = api_key

    def _embed(self, text: str) -> list[float] | None:
        if self.embed_provider == "ollama":
            vec = _ollama_embed(text)
            if vec:
                return vec
            # Fallback to local
        if self.embed_provider == "openai":
            vec = _openai_embed(text, self.api_key)
            if vec:
                return vec
        if self.embed_provider in ("local", "ollama", "openai"):
            return _local_embed(text)
        return None

    def index(self, note_id: str, text: str) -> bool:
        """Embed and store a note's vector."""
        vec = self._embed(text)
        if vec is None:
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO embeddings (note_id, model, dim, vec) VALUES (?,?,?,?)",
            (note_id, self.embed_provider, len(vec), _pack(vec)),
        )
        self.conn.commit()
        return True

    def search(self, query: str, limit: int = 10, scopes: list[str] | None = None) -> list[dict]:
        """Return notes ranked by cosine similarity to query embedding."""
        q_vec = self._embed(query)
        if q_vec is None:
            return []

        if scopes:
            placeholders = ",".join("?" * len(scopes))
            rows = self.conn.execute(
                f"SELECT e.note_id, e.vec, n.title, n.created, b.body "
                f"FROM embeddings e "
                f"JOIN notes n ON n.note_id = e.note_id "
                f"JOIN note_bodies b ON b.note_id = e.note_id "
                f"WHERE n.scope IN ({placeholders})",
                tuple(scopes),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT e.note_id, e.vec, n.title, n.created, b.body "
                "FROM embeddings e "
                "JOIN notes n ON n.note_id = e.note_id "
                "JOIN note_bodies b ON b.note_id = e.note_id"
            ).fetchall()

        scored = []
        for row in rows:
            vec = _unpack(row["vec"])
            score = _cosine(q_vec, vec)
            scored.append({
                "note_id": row["note_id"],
                "title": row["title"],
                "body": row["body"],
                "created": row["created"],
                "score_vec": score,
            })

        scored.sort(key=lambda x: x["score_vec"], reverse=True)
        return scored[:limit]
