"""Retrieval eval: hit-rate@k for naive vs AST chunking, dense vs hybrid search.

A retrieval is a *hit* when any returned chunk overlaps any ground-truth answer
region — same path AND intersecting line ranges. Overlap (not equality) because
the two chunkers produce different boundaries by design; what matters is whether
the reader was handed text containing the answer.

Usage:
    python -m eval --repo . --questions eval/questions_repo_reviewer.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eval.naive_chunker import naive_chunk_file
from indexer.chunker import Chunk
from indexer.embedder import EmbedFn, get_embedder
from indexer.pipeline import index_repository, iter_source_files
from indexer.store import HybridIndex

KS = (1, 3, 5)
EXCLUDE_DIRS = {"eval"}  # the questions file must not be part of the corpus


def _overlaps(chunk: Chunk, answers: list[dict]) -> bool:
    return any(chunk.path == a["path"]
               and chunk.start_line <= a["end"]
               and chunk.end_line >= a["start"] for a in answers)


def _filtered_files(repo_path: Path):
    for path in iter_source_files(repo_path):
        rel_parts = path.relative_to(repo_path).parts
        if not any(part in EXCLUDE_DIRS for part in rel_parts):
            yield path


def build_naive_index(repo_path: Path, data_dir: Path, embed_fn: EmbedFn) -> HybridIndex:
    repo_name = repo_path.resolve().name
    files: dict[str, tuple[str, list[Chunk]]] = {}
    for path in _filtered_files(repo_path):
        rel = path.relative_to(repo_path).as_posix()
        source = path.read_text(encoding="utf-8")
        chunks = naive_chunk_file(path, repo=repo_name, rel_path=rel)
        if chunks:
            files[rel] = (hashlib.sha256(source.encode()).hexdigest(), chunks)
    index = HybridIndex(data_dir, embed_fn)
    stats = index.update(files)
    print(f"naive index: {stats}")
    return index


def build_ast_index(repo_path: Path, data_dir: Path, embed_fn: EmbedFn) -> HybridIndex:
    # Same corpus filter as the naive index, so the comparison is apples-to-apples.
    import indexer.pipeline as pipeline
    original = pipeline.iter_source_files
    pipeline.iter_source_files = _filtered_files
    try:
        stats = index_repository(repo_path, data_dir, embed_fn=embed_fn)
        print(f"ast index:   {stats}")
    finally:
        pipeline.iter_source_files = original
    return HybridIndex(data_dir, embed_fn)


def evaluate(questions: list[dict], search_fn) -> dict[int, float]:
    hits = dict.fromkeys(KS, 0)
    for item in questions:
        results = [c for c, _ in search_fn(item["q"], max(KS))]
        for k in KS:
            if any(_overlaps(c, item["answers"]) for c in results[:k]):
                hits[k] += 1
    return {k: hits[k] / len(questions) for k in KS}


def by_kind(questions: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {"all": questions}
    for item in questions:
        groups.setdefault(item.get("kind", "natural"), []).append(item)
    return groups


def run(repo_path: Path, questions_path: Path, data_root: Path) -> str:
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = data["questions"]
    embed_fn = get_embedder()

    naive = build_naive_index(repo_path, data_root / "eval-naive", embed_fn)
    ast = build_ast_index(repo_path, data_root / "eval-ast", embed_fn)

    configs = [
        ("naive chunks + dense",  lambda q, k: naive.search_dense_only(q, k)),
        ("naive chunks + hybrid", lambda q, k: naive.search(q, k)),
        ("AST chunks + dense",    lambda q, k: ast.search_dense_only(q, k)),
        ("AST chunks + hybrid",   lambda q, k: ast.search(q, k)),
    ]

    lines = [f"# Retrieval eval — {data['repo']} ({len(questions)} questions)", ""]
    for kind, group in by_kind(questions).items():
        lines += [f"## {kind} ({len(group)} questions)", "",
                  "| configuration | hit@1 | hit@3 | hit@5 |",
                  "|---|---|---|---|"]
        for name, fn in configs:
            rates = evaluate(group, fn)
            print(f"[{kind:10}] {name:24}", {f"hit@{k}": round(v, 3) for k, v in rates.items()})
            lines.append(f"| {name} | {rates[1]:.0%} | {rates[3]:.0%} | {rates[5]:.0%} |")
        lines.append("")
    return "\n".join(lines)
