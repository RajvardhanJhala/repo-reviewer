"""The Q&A agent's four tools, wrapping Phase 1's index artifacts.

All read-only. Every path argument is resolved and confined to the repo root —
an agent tool that reads files is an injection target, so traversal is blocked
structurally, not by prompt.

Usage:
    tools = QATools(repo_path, index, symbols)
    tools.schemas()               # OpenAI function-calling schemas
    tools.dispatch("search_code", {"query": "refund handling"})
"""
from __future__ import annotations

import json
from pathlib import Path

from indexer.store import HybridIndex
from indexer.symbols import SymbolTable

MAX_READ_LINES = 200
MAX_RESULT_CHUNK_LINES = 60


class QATools:
    def __init__(self, repo_path: Path, index: HybridIndex, symbols: SymbolTable) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.index = index
        self.symbols = symbols

    # ------------------------------------------------------------ the tools

    def search_code(self, query: str, k: int = 5) -> str:
        results = self.index.search(query, k=min(int(k), 10))
        if not results:
            return "No results."
        blocks = []
        for chunk, _score in results:
            lines = chunk.text.splitlines()
            body = "\n".join(lines[:MAX_RESULT_CHUNK_LINES])
            if len(lines) > MAX_RESULT_CHUNK_LINES:
                body += f"\n... ({len(lines) - MAX_RESULT_CHUNK_LINES} more lines; use read_file)"
            blocks.append(f"=== {chunk.path}:{chunk.start_line}-{chunk.end_line} "
                          f"[{chunk.symbol_type}] {chunk.symbol} ===\n{body}")
        return "\n\n".join(blocks)

    def lookup_symbol(self, name: str) -> str:
        return json.dumps(self.symbols.lookup(name), indent=1)

    def read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        target = self._safe_path(path)
        if isinstance(target, str):
            return target  # error message
        if not target.is_file():
            return f"Error: {path} does not exist."
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return f"Error reading {path}: {e}"
        start = max(1, int(start))
        end = min(len(lines), int(end) if end else start + MAX_READ_LINES - 1)
        end = min(end, start + MAX_READ_LINES - 1)
        if start > len(lines):
            return f"Error: {path} has only {len(lines)} lines."
        numbered = [f"{i:5d}| {lines[i - 1]}" for i in range(start, end + 1)]
        suffix = f"\n... (file continues to line {len(lines)})" if end < len(lines) else ""
        return f"{path} lines {start}-{end} of {len(lines)}:\n" + "\n".join(numbered) + suffix

    def list_directory(self, path: str = ".") -> str:
        target = self._safe_path(path)
        if isinstance(target, str):
            return target
        if not target.is_dir():
            return f"Error: {path} is not a directory."
        entries = []
        for item in sorted(target.iterdir()):
            rel = item.relative_to(self.repo_path).as_posix()
            if item.is_dir():
                entries.append(f"{rel}/")
            else:
                entries.append(f"{rel}  ({item.stat().st_size} bytes)")
        return "\n".join(entries) or "(empty)"

    def _safe_path(self, path: str) -> Path | str:
        resolved = (self.repo_path / path).resolve()
        if not resolved.is_relative_to(self.repo_path):
            return f"Error: {path} is outside the repository."
        return resolved

    # ----------------------------------------------------- schema + dispatch

    def dispatch(self, name: str, arguments: dict) -> str:
        handlers = {
            "search_code": self.search_code,
            "lookup_symbol": self.lookup_symbol,
            "read_file": self.read_file,
            "list_directory": self.list_directory,
        }
        if name not in handlers:
            return f"Error: unknown tool {name!r}."
        try:
            return handlers[name](**arguments)
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"

    @staticmethod
    def schemas() -> list[dict]:
        def fn(name: str, description: str, params: dict, required: list[str]) -> dict:
            return {"type": "function",
                    "function": {"name": name, "description": description,
                                 "parameters": {"type": "object", "properties": params,
                                                "required": required}}}
        return [
            fn("search_code",
               "Hybrid semantic+keyword search over the indexed codebase. Returns the "
               "most relevant code/doc chunks with exact path:line locations.",
               {"query": {"type": "string", "description": "natural language or an exact identifier"},
                "k": {"type": "integer", "description": "number of results (default 5, max 10)"}},
               ["query"]),
            fn("lookup_symbol",
               "Exact lookup: where a function/class/method is defined and every place "
               "it is referenced. Use for 'where is X defined/used' questions.",
               {"name": {"type": "string", "description": "the exact symbol name"}},
               ["name"]),
            fn("read_file",
               "Read a file by exact line range (max 200 lines per call). Use after "
               "search to see surrounding context.",
               {"path": {"type": "string", "description": "repo-relative path"},
                "start": {"type": "integer", "description": "first line, 1-based (default 1)"},
                "end": {"type": "integer", "description": "last line (default start+199)"}},
               ["path"]),
            fn("list_directory",
               "List a directory's entries to orient yourself in the repo structure.",
               {"path": {"type": "string", "description": "repo-relative path (default repo root)"}},
               []),
        ]


def load_tools(repo_path: Path, data_dir: Path) -> QATools:
    """Production factory: open the persisted Phase 1 index for this repo."""
    from indexer.pipeline import open_index
    index, symbols = open_index(data_dir)
    return QATools(repo_path, index, symbols)
