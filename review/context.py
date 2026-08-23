"""Context enrichment: what a good reviewer knows beyond the 10 diff lines.

For each hunk, pull from the Phase 1 index of the *post-change* working tree:
  1. the full enclosing function/class of the changed region (AST chunks),
  2. call sites of changed symbols (symbol table) — contract-break detection,
  3. similar code elsewhere in the repo (hybrid search) — convention checks.

Everything is budgeted: reviewers read this, and reviewer tokens are the cost
driver of the whole pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from indexer.store import HybridIndex
from indexer.symbols import SymbolTable
from review.diff import FileDiff, Hunk

MAX_ENCLOSING_LINES = 80
MAX_CALL_SITES = 5
MAX_RELATED = 2


@dataclass
class HunkContext:
    hunk: Hunk
    enclosing: str = ""          # full enclosing symbol chunk (trimmed)
    call_sites: list[str] = field(default_factory=list)   # "path:line" of callers
    related: list[str] = field(default_factory=list)      # similar code elsewhere

    def render(self) -> str:
        parts = [f"### Diff hunk — {self.hunk.path} (new-file lines "
                 f"{self.hunk.new_start}-{self.hunk.new_end})",
                 "Line numbers below are NEW-file numbers; '-' lines no longer exist.",
                 "```", self.hunk.render(), "```"]
        if self.enclosing:
            parts += ["", "Enclosing definition (post-change):", "```", self.enclosing, "```"]
        if self.call_sites:
            parts += ["", "Call sites of changed symbols (check these contracts still hold): "
                      + ", ".join(self.call_sites)]
        if self.related:
            parts += ["", "Related code elsewhere in this repo (its conventions apply):"]
            parts += [f"```\n{r}\n```" for r in self.related]
        return "\n".join(parts)


class ContextBuilder:
    def __init__(self, index: HybridIndex, symbols: SymbolTable, repo_path: Path) -> None:
        self.index = index
        self.symbols = symbols
        self.repo_path = Path(repo_path)

    def build(self, fd: FileDiff, hunk: Hunk) -> HunkContext:
        ctx = HunkContext(hunk=hunk)
        if not fd.is_deleted:
            ctx.enclosing = self._enclosing_chunk(hunk)
            ctx.call_sites = self._call_sites(hunk)
            ctx.related = self._related_code(hunk)
        return ctx

    def _enclosing_chunk(self, hunk: Hunk) -> str:
        """Smallest indexed AST chunk containing the changed region."""
        best = None
        for chunk in self.index.chunks:
            if (chunk.path == hunk.path and chunk.symbol_type in ("function", "method", "class")
                    and chunk.start_line <= hunk.new_start and chunk.end_line >= hunk.new_end):
                if best is None or (chunk.end_line - chunk.start_line) < (best.end_line - best.start_line):
                    best = chunk
        if best is None:
            return ""
        lines = best.embed_text.splitlines()
        if len(lines) > MAX_ENCLOSING_LINES:
            lines = [*lines[:MAX_ENCLOSING_LINES], "... (truncated)"]
        return "\n".join(lines)

    def _changed_symbols(self, hunk: Hunk) -> list[str]:
        """Symbols whose definition lines are inside the changed region."""
        out = []
        for name, locs in self.symbols.definitions.items():
            for loc in locs:
                if loc.path == hunk.path and hunk.new_start <= loc.line <= hunk.new_end:
                    out.append(name)
        return out

    def _call_sites(self, hunk: Hunk) -> list[str]:
        sites = []
        for name in self._changed_symbols(hunk):
            for ref in self.symbols.references.get(name, []):
                if ref.path != hunk.path:      # same-file refs are visible in enclosing
                    sites.append(f"{name} <- {ref.path}:{ref.line}")
        return sites[:MAX_CALL_SITES]

    def _related_code(self, hunk: Hunk) -> list[str]:
        added_text = "\n".join(content for _, content in hunk.added)
        if not added_text.strip():
            return []
        out = []
        for chunk, _score in self.index.search(added_text[:500], k=MAX_RELATED + 2):
            if chunk.path == hunk.path:        # itself / same file — not "elsewhere"
                continue
            header = f"# {chunk.path}:{chunk.start_line}-{chunk.end_line} [{chunk.symbol}]"
            body = "\n".join(chunk.text.splitlines()[:20])
            out.append(f"{header}\n{body}")
            if len(out) == MAX_RELATED:
                break
        return out
