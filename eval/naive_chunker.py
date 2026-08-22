"""The baseline everyone starts with: fixed-size line windows with overlap.

Deliberately structure-blind — it splits functions mid-body and embeds raw text
with no context header (raw_embed=True). It exists to be measured against, so
resist the urge to make it smarter.
"""
from __future__ import annotations

from pathlib import Path

from indexer.chunker import LANGUAGE_BY_EXT, SKIP_FILENAMES, Chunk

WINDOW_LINES = 40
STRIDE_LINES = 30  # 10-line overlap between consecutive windows


def naive_chunk_file(file_path: Path, repo: str, rel_path: str) -> list[Chunk]:
    if file_path.name in SKIP_FILENAMES:
        return []
    language = LANGUAGE_BY_EXT.get(file_path.suffix.lower())
    if language is None:
        return []
    try:
        source = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    lines = source.splitlines()
    if not lines:
        return []

    chunks = []
    start = 1
    while start <= len(lines):
        end = min(start + WINDOW_LINES - 1, len(lines))
        text = "\n".join(lines[start - 1:end])
        if text.strip():
            chunks.append(Chunk(repo=repo, path=rel_path, language=language,
                                symbol=f"L{start}-{end}", symbol_type="naive",
                                start_line=start, end_line=end, text=text,
                                raw_embed=True))
        if end == len(lines):
            break
        start += STRIDE_LINES
    return chunks
