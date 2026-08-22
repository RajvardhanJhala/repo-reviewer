"""CLI: python -m qa --repo . "How does model fallback work?"

Indexes the repo first if no index exists (or --reindex to refresh).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows consoles default to cp1252; model output is Unicode. Never crash on print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from qa.agent import QAAgent
from qa.tools import load_tools


def main() -> None:
    ap = argparse.ArgumentParser(prog="qa")
    ap.add_argument("question")
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--data", type=Path, default=None)
    ap.add_argument("--reindex", action="store_true")
    args = ap.parse_args()

    data = args.data or Path("data") / args.repo.resolve().name
    if args.reindex or not (data / "manifest.json").exists():
        from indexer.pipeline import index_repository
        print(f"indexing {args.repo} -> {data} ...")
        index_repository(args.repo, data)

    agent = QAAgent(load_tools(args.repo, data))
    result = agent.ask(args.question)

    print("\n" + "=" * 72)
    print(result.answer)
    print("=" * 72)
    print(f"\nsteps: {result.steps}   tool calls: {len(result.tool_trace)}"
          + ("   [hit step limit]" if result.ran_out_of_steps else ""))
    for t in result.tool_trace:
        print(f"  step {t['step']}: {t['tool']}({t['arguments']}) -> {t['result_chars']} chars")
    if result.citations:
        print("citations:")
        for c in result.citations:
            mark = "ok " if c.valid else f"BAD ({c.reason})"
            print(f"  [{mark}] {c.path}:{c.start}-{c.end}")

    from llm.router import router
    print("\nrouter:", router.stats.summary())


if __name__ == "__main__":
    main()
