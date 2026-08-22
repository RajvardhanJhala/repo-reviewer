"""Repo walker: files -> chunks -> hybrid index + symbol table.

Usage:
    from indexer.pipeline import index_repository, open_index
    stats = index_repository(Path("./target-repo"), Path("data/target-repo"))
    index, symbols = open_index(Path("data/target-repo"))
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from indexer.chunker import LANGUAGE_BY_EXT, Chunk, chunk_file
from indexer.embedder import EmbedFn, get_embedder
from indexer.store import HybridIndex
from indexer.symbols import SymbolTable

log = logging.getLogger(__name__)

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
             ".ruff_cache", "data", "dist", "build", ".mypy_cache", "site-packages"}
MAX_FILE_BYTES = 200_000  # generated/vendored monsters aren't worth indexing


def iter_source_files(repo_path: Path):
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(repo_path).parts):
            continue
        if path.suffix.lower() not in LANGUAGE_BY_EXT:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        yield path


def index_repository(repo_path: Path, data_dir: Path,
                     embed_fn: EmbedFn | None = None, repo_name: str = "") -> dict:
    repo_path = Path(repo_path)
    repo_name = repo_name or repo_path.resolve().name
    embed_fn = embed_fn or get_embedder()

    files: dict[str, tuple[str, list[Chunk]]] = {}
    symbols = SymbolTable()
    for path in iter_source_files(repo_path):
        rel = path.relative_to(repo_path).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        content_hash = hashlib.sha256(source.encode()).hexdigest()
        chunks = chunk_file(path, repo=repo_name, rel_path=rel)
        if not chunks:
            continue
        files[rel] = (content_hash, chunks)
        language = LANGUAGE_BY_EXT[path.suffix.lower()]
        symbols.add_file(source, rel, language)

    symbols.resolve_references()
    symbols.save(Path(data_dir) / "symbols.json")

    index = HybridIndex(Path(data_dir), embed_fn)
    stats = index.update(files)
    stats["files"] = len(files)
    stats["symbols"] = len(symbols.definitions)
    log.info("indexed %s: %s", repo_name, stats)
    return stats


def open_index(data_dir: Path, embed_fn: EmbedFn | None = None) -> tuple[HybridIndex, SymbolTable]:
    data_dir = Path(data_dir)
    index = HybridIndex(data_dir, embed_fn or get_embedder())
    symbols = SymbolTable.load(data_dir / "symbols.json")
    return index, symbols
