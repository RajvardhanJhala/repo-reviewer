"""Hybrid dense+sparse index with incremental re-indexing.

Dense:  bge-m3 vectors in a FAISS inner-product index (vectors are normalized,
        so inner product == cosine similarity).
Sparse: BM25 over code-aware tokens (identifiers split on snake_case/camelCase).
Fusion: Reciprocal Rank Fusion — no score calibration between the two systems
        needed, only their rankings.

Incremental: a manifest maps file path -> content hash. On update(), only files
whose hash changed get re-chunked and re-embedded; on a per-PR re-index this
turns minutes into seconds.

Persisted layout under data_dir/:
    chunks.json    all chunks + which file they came from
    vectors.npy    float32 matrix, row i == chunks[i]
    manifest.json  {path: sha256}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from indexer.chunker import Chunk
from indexer.embedder import EmbedFn

RRF_K = 60          # standard damping constant from the RRF paper
FUSION_DEPTH = 10   # how deep each retriever's ranking goes before fusion

# Query routing (measured on the eval set - see docs/retrieval_eval.md):
# prose queries lose accuracy when BM25 votes (docs chunks lexically shadow the
# code they describe), while identifier queries gain from it. So search() fuses
# BM25 only when the query looks like a code identifier.
_IDENT_QUERY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,64}$")

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def tokenize(text: str) -> list[str]:
    """Code-aware tokens: each identifier yields itself PLUS its sub-words, so
    `process_refund` matches the exact query "process_refund" and also "refund"."""
    tokens: list[str] = []
    for ident in _TOKEN_RE.findall(text):
        lower = ident.lower()
        tokens.append(lower)
        parts = [p.lower() for part in ident.split("_") if part
                 for p in _CAMEL_RE.findall(part)]
        if len(parts) > 1:
            tokens.extend(parts)
    return tokens


class HybridIndex:
    def __init__(self, data_dir: Path, embed_fn: EmbedFn) -> None:
        self.data_dir = Path(data_dir)
        self.embed_fn = embed_fn
        self.chunks: list[Chunk] = []
        self.vectors: np.ndarray | None = None
        self.manifest: dict[str, str] = {}
        self._faiss: faiss.Index | None = None
        self._bm25: BM25Okapi | None = None
        if (self.data_dir / "manifest.json").exists():
            self._load()

    # ------------------------------------------------------------- updating

    def update(self, files: dict[str, tuple[str, list[Chunk]]]) -> dict:
        """files: {rel_path: (content_hash, chunks)} for every indexable file
        currently in the repo. Handles adds, changes, and deletions."""
        changed = [p for p, (h, _) in files.items() if self.manifest.get(p) != h]
        removed = [p for p in self.manifest if p not in files]
        unchanged = len(files) - len(changed)

        if changed or removed:
            keep = [i for i, c in enumerate(self.chunks)
                    if c.path in files and c.path not in changed]
            kept_chunks = [self.chunks[i] for i in keep]
            kept_vectors = self.vectors[keep] if self.vectors is not None and keep else None

            new_chunks = [c for p in changed for c in files[p][1]]
            if new_chunks:
                new_vectors = self.embed_fn([c.embed_text for c in new_chunks])
                self.vectors = (np.vstack([kept_vectors, new_vectors])
                                if kept_vectors is not None else new_vectors)
            else:
                self.vectors = kept_vectors
            self.chunks = kept_chunks + new_chunks
            self.manifest = {p: h for p, (h, _) in files.items()}
            self._save()
            self._rebuild()

        return {"changed": len(changed), "unchanged": unchanged,
                "removed": len(removed), "total_chunks": len(self.chunks)}

    # ------------------------------------------------------------ searching

    def search(self, query: str, k: int = 5) -> list[tuple[Chunk, float]]:
        """Routed search: identifier-shaped queries fuse dense+BM25; prose is dense-only."""
        if not self.chunks:
            return []
        if not _IDENT_QUERY_RE.match(query.strip()):
            return self.search_dense_only(query, k)
        if self._faiss is None:
            self._rebuild()

        n = min(FUSION_DEPTH, len(self.chunks))
        qvec = self.embed_fn([query])
        _, dense_ids = self._faiss.search(qvec, n)
        dense_rank = {int(idx): rank for rank, idx in enumerate(dense_ids[0]) if idx != -1}

        bm25_scores = self._bm25.get_scores(tokenize(query))
        sparse_order = np.argsort(bm25_scores)[::-1][:n]
        sparse_rank = {int(idx): rank for rank, idx in enumerate(sparse_order)
                       if bm25_scores[idx] > 0}

        fused: dict[int, float] = {}
        for idx_map in (dense_rank, sparse_rank):
            for idx, rank in idx_map.items():
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)

        top = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [(self.chunks[i], score) for i, score in top]

    def search_dense_only(self, query: str, k: int = 5) -> list[tuple[Chunk, float]]:
        """For the eval harness: dense retrieval without BM25, same interface."""
        if not self.chunks:
            return []
        if self._faiss is None:
            self._rebuild()
        qvec = self.embed_fn([query])
        scores, ids = self._faiss.search(qvec, min(k, len(self.chunks)))
        return [(self.chunks[int(i)], float(s))
                for s, i in zip(scores[0], ids[0], strict=True) if i != -1]

    # ---------------------------------------------------------- persistence

    def _rebuild(self) -> None:
        dim = self.vectors.shape[1]
        self._faiss = faiss.IndexFlatIP(dim)
        self._faiss.add(self.vectors)
        self._bm25 = BM25Okapi([tokenize(c.embed_text) for c in self.chunks])

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "chunks.json").write_text(
            json.dumps([c.to_dict() for c in self.chunks]), encoding="utf-8")
        np.save(self.data_dir / "vectors.npy", self.vectors)
        (self.data_dir / "manifest.json").write_text(
            json.dumps(self.manifest, indent=1), encoding="utf-8")

    def _load(self) -> None:
        self.chunks = [Chunk.from_dict(d) for d in
                       json.loads((self.data_dir / "chunks.json").read_text(encoding="utf-8"))]
        self.vectors = np.load(self.data_dir / "vectors.npy")
        self.manifest = json.loads((self.data_dir / "manifest.json").read_text(encoding="utf-8"))
