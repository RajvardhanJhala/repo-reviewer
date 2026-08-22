"""AST-aware chunking: split source files at structural boundaries, not byte counts.

Chunk boundaries are top-level functions, classes, and methods (via tree-sitter).
Every chunk carries a context header so the embedding model sees *where* the code
lives, plus metadata for citations (path, symbol, exact line range).

Usage:
    from indexer.chunker import chunk_file
    chunks = chunk_file(Path("app/payments.py"), repo="repo-reviewer", rel_path="app/payments.py")
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_python
from tree_sitter import Language, Node, Parser

MAX_CHUNK_LINES = 120  # anything bigger gets split at child-statement boundaries

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yml": "config",
    ".yaml": "config",
    ".toml": "config",
    ".json": "config",
    ".ini": "config",
    ".cfg": "config",
    ".txt": "config",
}

SKIP_FILENAMES = {"package-lock.json", "yarn.lock", "poetry.lock", "uv.lock", "Pipfile.lock"}

_PARSERS: dict[str, Parser] = {}


def _parser(language: str) -> Parser:
    if language not in _PARSERS:
        lang = {"python": tree_sitter_python, "javascript": tree_sitter_javascript}[language]
        _PARSERS[language] = Parser(Language(lang.language()))
    return _PARSERS[language]


@dataclass
class Chunk:
    repo: str
    path: str            # repo-relative, forward slashes
    language: str
    symbol: str          # "process_refund", "PaymentService", "(module)", a heading, ...
    symbol_type: str     # function | class | method | module | markdown | config
    start_line: int      # 1-based, inclusive
    end_line: int
    text: str            # raw source of the chunk
    parent_symbol: str = ""
    part: int = 1        # >1 for continuation chunks of an oversized symbol
    parts: int = 1
    raw_embed: bool = False  # True: embed text without the context header (naive-chunking eval)
    chunk_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.chunk_id:
            suffix = f"#p{self.part}" if self.parts > 1 else ""
            self.chunk_id = f"{self.path}::{self.symbol}::{self.start_line}{suffix}"

    @property
    def header(self) -> str:
        qualifier = f"{self.parent_symbol} :: " if self.parent_symbol else ""
        part = f" [part {self.part}/{self.parts}]" if self.parts > 1 else ""
        return (f"# {self.repo}/{self.path} :: {qualifier}{self.symbol} "
                f"(lines {self.start_line}-{self.end_line}){part}")

    @property
    def embed_text(self) -> str:
        """What actually gets embedded / BM25-indexed: context header + code."""
        return self.text if self.raw_embed else f"{self.header}\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Chunk:
        return cls(**d)


# ---------------------------------------------------------------- code chunking

def chunk_file(file_path: Path, repo: str, rel_path: str) -> list[Chunk]:
    """Dispatch on file type. Returns [] for files we don't index."""
    if file_path.name in SKIP_FILENAMES:
        return []
    language = LANGUAGE_BY_EXT.get(file_path.suffix.lower())
    if language is None:
        return []
    try:
        source = file_path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return []
    return chunk_source(source, repo=repo, rel_path=rel_path, language=language)


def chunk_source(source: str, repo: str, rel_path: str, language: str) -> list[Chunk]:
    if not source.strip():
        return []
    if language == "markdown":
        return _chunk_markdown(source, repo, rel_path)
    if language == "config":
        return _chunk_config(source, repo, rel_path)
    return _chunk_code(source, repo, rel_path, language)


def _chunk_code(source: str, repo: str, rel_path: str, language: str) -> list[Chunk]:
    tree = _parser(language).parse(source.encode())
    lines = source.splitlines()
    chunks: list[Chunk] = []
    module_run: list[Node] = []  # contiguous top-level statements between defs

    def flush_module_run() -> None:
        if not module_run:
            return
        start, end = _line_span(module_run[0], module_run[-1])
        chunks.append(_make_chunk(repo, rel_path, language, "(module)", "module",
                                  start, end, _slice(lines, start, end)))
        module_run.clear()

    for node in tree.root_node.children:
        kind = _classify(node, language)
        if kind == "function":
            flush_module_run()
            chunks.extend(_function_chunks(node, lines, repo, rel_path, language, parent=""))
        elif kind == "class":
            flush_module_run()
            chunks.extend(_class_chunks(node, lines, repo, rel_path, language))
        else:
            module_run.append(node)
    flush_module_run()
    return [c for c in chunks if c.text.strip()]


def _classify(node: Node, language: str) -> str:
    """Is this top-level node a function, a class, or plain module code?"""
    inner = _unwrap(node)
    if language == "python":
        if inner.type == "function_definition":
            return "function"
        if inner.type == "class_definition":
            return "class"
    else:  # javascript
        if inner.type in ("function_declaration", "generator_function_declaration"):
            return "function"
        if inner.type == "class_declaration":
            return "class"
        if inner.type == "lexical_declaration" and _arrow_name(inner):
            return "function"
    return "module"


def _unwrap(node: Node) -> Node:
    """Look through decorators (py) and export statements (js) to the real definition."""
    if node.type == "decorated_definition":
        return node.child_by_field_name("definition") or node
    if node.type == "export_statement":
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration",
                             "lexical_declaration", "generator_function_declaration"):
                return child
    return node


def _node_name(node: Node) -> str:
    inner = _unwrap(node)
    name = inner.child_by_field_name("name")
    if name is not None:
        return name.text.decode()
    if inner.type == "lexical_declaration":
        return _arrow_name(inner) or "(anonymous)"
    return "(anonymous)"


def _arrow_name(node: Node) -> str | None:
    """`const foo = (...) => {...}` -> "foo"."""
    for decl in node.children:
        if decl.type == "variable_declarator":
            value = decl.child_by_field_name("value")
            if value is not None and value.type in ("arrow_function", "function_expression"):
                name = decl.child_by_field_name("name")
                return name.text.decode() if name is not None else None
    return None


def _body_node(node: Node) -> Node | None:
    inner = _unwrap(node)
    if inner.type == "lexical_declaration":  # arrow fn: body lives on the arrow_function
        for decl in inner.children:
            if decl.type == "variable_declarator":
                value = decl.child_by_field_name("value")
                if value is not None:
                    return value.child_by_field_name("body")
    return inner.child_by_field_name("body")


def _line_span(first: Node, last: Node) -> tuple[int, int]:
    return first.start_point[0] + 1, last.end_point[0] + 1


def _slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1:end])


def _make_chunk(repo: str, path: str, language: str, symbol: str, symbol_type: str,
                start: int, end: int, text: str, parent: str = "",
                part: int = 1, parts: int = 1) -> Chunk:
    return Chunk(repo=repo, path=path, language=language, symbol=symbol,
                 symbol_type=symbol_type, start_line=start, end_line=end,
                 text=text, parent_symbol=parent, part=part, parts=parts)


def _function_chunks(node: Node, lines: list[str], repo: str, path: str,
                     language: str, parent: str) -> list[Chunk]:
    """One chunk per function — split at child statements when oversized."""
    symbol = _node_name(node)
    symbol_type = "method" if parent else "function"
    start, end = _line_span(node, node)

    if end - start + 1 <= MAX_CHUNK_LINES:
        return [_make_chunk(repo, path, language, symbol, symbol_type,
                            start, end, _slice(lines, start, end), parent)]

    # Oversized: signature lines stay with part 1 and are repeated (as context)
    # in every continuation part, so no part ever loses "what function am I in?".
    body = _body_node(node)
    if body is None or not body.children:
        return [_make_chunk(repo, path, language, symbol, symbol_type,
                            start, end, _slice(lines, start, end), parent)]
    sig_end = max(start, body.children[0].start_point[0])  # last line before body starts
    signature = _slice(lines, start, sig_end)

    groups: list[tuple[int, int]] = []
    group_start = None
    prev_end = None
    for stmt in body.children:
        s, e = _line_span(stmt, stmt)
        if group_start is None:
            group_start = s
        elif e - group_start + 1 > MAX_CHUNK_LINES:
            groups.append((group_start, prev_end))
            group_start = s
        prev_end = e
    if group_start is not None:
        groups.append((group_start, prev_end))

    parts = len(groups)
    chunks = []
    for i, (gs, ge) in enumerate(groups, start=1):
        body_text = _slice(lines, gs, ge)
        text = f"{signature}\n{body_text}" if i > 1 else _slice(lines, start, ge)
        chunks.append(_make_chunk(repo, path, language, symbol, symbol_type,
                                  gs if i > 1 else start, ge, text, parent,
                                  part=i, parts=parts))
    return chunks


def _class_chunks(node: Node, lines: list[str], repo: str, path: str,
                  language: str) -> list[Chunk]:
    """A class becomes: one skeleton chunk (signatures + class-level code, method
    bodies elided) + one chunk per method. Nothing is lost, and the skeleton
    doubles as an API summary of the class."""
    class_name = _node_name(node)
    start, end = _line_span(node, node)
    body = _body_node(node)

    methods: list[Node] = []
    if body is not None:
        for child in body.children:
            inner = _unwrap(child)
            if inner.type in ("function_definition", "method_definition",
                             "function_declaration"):
                methods.append(child)

    if not methods:
        return [_make_chunk(repo, path, language, class_name, "class",
                            start, end, _slice(lines, start, end))]

    # Skeleton: full class text, but each method body replaced with "..."
    elide: list[tuple[int, int]] = []  # line ranges to drop
    for m in methods:
        m_start, m_end = _line_span(m, m)
        m_body = _body_node(m)
        sig_end = max(m_start, m_body.children[0].start_point[0]) if (
            m_body is not None and m_body.children) else m_end
        if sig_end < m_end:
            elide.append((sig_end + 1, m_end))

    skeleton_lines = []
    for lineno in range(start, end + 1):
        if any(s <= lineno <= e for s, e in elide):
            if any(lineno == s for s, _ in elide):
                indent = " " * (len(lines[lineno - 1]) - len(lines[lineno - 1].lstrip()))
                skeleton_lines.append(f"{indent}...")
            continue
        skeleton_lines.append(lines[lineno - 1])

    chunks = [_make_chunk(repo, path, language, class_name, "class",
                          start, end, "\n".join(skeleton_lines))]
    for m in methods:
        chunks.extend(_function_chunks(m, lines, repo, path, language, parent=class_name))
    return chunks


# ---------------------------------------------------------- non-code chunking

def _chunk_markdown(source: str, repo: str, rel_path: str) -> list[Chunk]:
    """Split on # / ## headings; each section is one chunk named by its heading."""
    lines = source.splitlines()
    sections: list[tuple[str, int]] = []  # (heading, start_line)
    for i, line in enumerate(lines, start=1):
        if line.startswith("# ") or line.startswith("## "):
            sections.append((line.lstrip("#").strip(), i))
    if not sections:
        return [_make_chunk(repo, rel_path, "markdown", "(document)", "markdown",
                            1, len(lines), source)]
    chunks = []
    if sections[0][1] > 1:  # preamble before the first heading
        chunks.append(_make_chunk(repo, rel_path, "markdown", "(preamble)", "markdown",
                                  1, sections[0][1] - 1, _slice(lines, 1, sections[0][1] - 1)))
    for i, (heading, start) in enumerate(sections):
        end = sections[i + 1][1] - 1 if i + 1 < len(sections) else len(lines)
        chunks.append(_make_chunk(repo, rel_path, "markdown", heading, "markdown",
                                  start, end, _slice(lines, start, end)))
    return [c for c in chunks if c.text.strip()]


def _chunk_config(source: str, repo: str, rel_path: str) -> list[Chunk]:
    """Config files are kept whole — they're small and lose meaning when split."""
    lines = source.splitlines()
    return [_make_chunk(repo, rel_path, "config", Path(rel_path).name, "config",
                        1, len(lines), source)]
