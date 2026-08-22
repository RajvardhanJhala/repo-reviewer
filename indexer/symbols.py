"""Symbol table: {name -> definition locations + reference locations}.

Built while walking the same tree-sitter ASTs used for chunking. This answers
"where is X defined / used?" — a question pure vector search fundamentally
cannot, because it requires exact identity, not similarity.

Usage:
    table = SymbolTable()
    table.add_file(source, rel_path, language)   # once per code file
    table.resolve_references()                   # after all files are added
    table.lookup("process_refund")
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from indexer.chunker import _node_name, _parser, _unwrap

MAX_REFERENCES_PER_SYMBOL = 100

DEF_TYPES = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "generator_function_declaration",
                   "class_declaration", "method_definition"},
}
IDENTIFIER_TYPES = {"identifier", "property_identifier", "type_identifier"}


@dataclass
class Location:
    path: str
    line: int  # 1-based

    def to_dict(self) -> dict:
        return {"path": self.path, "line": self.line}


class SymbolTable:
    def __init__(self) -> None:
        self.definitions: dict[str, list[Location]] = defaultdict(list)
        self.references: dict[str, list[Location]] = defaultdict(list)
        # (source, path, language) kept until resolve_references(), because a
        # reference to a symbol defined in another file can't be recognized
        # until every file's definitions have been collected.
        self._pending: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------- building

    def add_file(self, source: str, rel_path: str, language: str) -> None:
        if language not in DEF_TYPES:
            return
        tree = _parser(language).parse(source.encode())
        self._collect_definitions(tree.root_node, rel_path, language)
        self._pending.append((source, rel_path, language))

    def _collect_definitions(self, node, rel_path: str, language: str) -> None:
        inner = _unwrap(node)
        if inner.type in DEF_TYPES[language]:
            name = _node_name(node)
            if name and name != "(anonymous)":
                self.definitions[name].append(Location(rel_path, inner.start_point[0] + 1))
        elif inner.type == "lexical_declaration" and language == "javascript":
            name = _node_name(node)
            if name and name != "(anonymous)":
                self.definitions[name].append(Location(rel_path, inner.start_point[0] + 1))
        for child in node.children:
            self._collect_definitions(child, rel_path, language)

    def resolve_references(self) -> None:
        """Second pass: every identifier whose text matches a known definition
        (and isn't the definition site itself) is a reference."""
        def_lines = {(loc.path, loc.line, name)
                     for name, locs in self.definitions.items() for loc in locs}
        for source, rel_path, language in self._pending:
            tree = _parser(language).parse(source.encode())
            stack = [tree.root_node]
            while stack:
                node = stack.pop()
                if node.type in IDENTIFIER_TYPES:
                    name = node.text.decode()
                    line = node.start_point[0] + 1
                    if name in self.definitions and (rel_path, line, name) not in def_lines:
                        if len(self.references[name]) < MAX_REFERENCES_PER_SYMBOL:
                            self.references[name].append(Location(rel_path, line))
                stack.extend(node.children)
        self._pending.clear()

    # -------------------------------------------------------------- queries

    def lookup(self, name: str) -> dict:
        return {
            "symbol": name,
            "definitions": [loc.to_dict() for loc in self.definitions.get(name, [])],
            "references": [loc.to_dict() for loc in self.references.get(name, [])],
        }

    # ---------------------------------------------------------- persistence

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "definitions": {n: [loc.to_dict() for loc in ls] for n, ls in self.definitions.items()},
            "references": {n: [loc.to_dict() for loc in ls] for n, ls in self.references.items()},
        }
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> SymbolTable:
        table = cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        for name, locs in data["definitions"].items():
            table.definitions[name] = [Location(**loc) for loc in locs]
        for name, locs in data["references"].items():
            table.references[name] = [Location(**loc) for loc in locs]
        return table
