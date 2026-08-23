"""Diff parsing: raw unified diff -> structured hunks with new-file line numbers.

GitHub inline comments anchor to *new-file* line numbers on lines that appear
in the diff. Everything downstream (reviewers, synthesizer, poster) speaks
new-file lines only; this module is the single place old/new mapping happens.

Usage:
    files = parse_diff(diff_text)
    ok = commentable_lines(files)     # {path: {new-file lines a comment may target}}
"""
from __future__ import annotations

from dataclasses import dataclass, field

import unidiff

from indexer.chunker import SKIP_FILENAMES

SKIP_SUFFIXES = (".lock", ".min.js", ".map", ".svg")


@dataclass
class DiffLine:
    kind: str            # "+", "-", " "
    content: str         # without trailing newline
    new_line: int | None  # line number in the NEW file ("-" lines have None)
    old_line: int | None


@dataclass
class Hunk:
    path: str
    new_start: int       # first new-file line covered by this hunk
    new_end: int
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def added(self) -> list[tuple[int, str]]:
        return [(ln.new_line, ln.content) for ln in self.lines if ln.kind == "+"]

    @property
    def removed(self) -> list[tuple[int, str]]:
        return [(ln.old_line, ln.content) for ln in self.lines if ln.kind == "-"]

    def render(self) -> str:
        """Reviewer-facing text: every line tagged with its new-file number."""
        out = []
        for ln in self.lines:
            n = f"{ln.new_line:5d}" if ln.new_line is not None else "    -"
            out.append(f"{n} {ln.kind} {ln.content}")
        return "\n".join(out)


@dataclass
class FileDiff:
    path: str            # new path (rename target)
    old_path: str
    is_new: bool
    is_deleted: bool
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added_line_count(self) -> int:
        return sum(len(h.added) for h in self.hunks)


def parse_diff(diff_text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    for pf in unidiff.PatchSet(diff_text):
        if pf.path.split("/")[-1] in SKIP_FILENAMES or pf.path.endswith(SKIP_SUFFIXES):
            continue
        if pf.is_binary_file:
            continue
        fd = FileDiff(path=pf.path, old_path=pf.source_file.removeprefix("a/"),
                      is_new=pf.is_added_file, is_deleted=pf.is_removed_file)
        for h in pf:
            lines = [DiffLine(kind=ln.line_type, content=ln.value.rstrip("\n"),
                              new_line=ln.target_line_no, old_line=ln.source_line_no)
                     for ln in h]
            new_lines = [ln.new_line for ln in lines if ln.new_line is not None]
            if not new_lines:      # pure deletion hunk: anchor to the hunk position
                new_lines = [max(1, h.target_start)]
            fd.hunks.append(Hunk(path=fd.path, new_start=min(new_lines),
                                 new_end=max(new_lines), lines=lines))
        if fd.hunks:
            files.append(fd)
    return files


def commentable_lines(files: list[FileDiff]) -> dict[str, set[int]]:
    """New-file lines a GitHub inline comment may anchor to: any '+' or ' ' line
    that appears in the diff. Findings pointing anywhere else are invalid."""
    ok: dict[str, set[int]] = {}
    for fd in files:
        if fd.is_deleted:
            continue
        lines = {ln.new_line for h in fd.hunks for ln in h.lines
                 if ln.new_line is not None}
        if lines:
            ok[fd.path] = lines
    return ok
