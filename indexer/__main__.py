"""CLI for the indexer.

    python -m indexer index  --repo ../target-repo [--data data/target-repo]
    python -m indexer search "where is retry logic implemented?" [--data ...] [-k 5]
    python -m indexer symbol process_refund [--data ...]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from indexer.pipeline import index_repository, open_index


def main() -> None:
    ap = argparse.ArgumentParser(prog="indexer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="(re)index a repository")
    p_index.add_argument("--repo", required=True, type=Path)
    p_index.add_argument("--data", type=Path, default=None)

    p_search = sub.add_parser("search", help="hybrid search over an indexed repo")
    p_search.add_argument("query")
    p_search.add_argument("--data", type=Path, default=None)
    p_search.add_argument("-k", type=int, default=5)

    p_sym = sub.add_parser("symbol", help="look up where a symbol is defined/used")
    p_sym.add_argument("name")
    p_sym.add_argument("--data", type=Path, default=None)

    args = ap.parse_args()

    if args.cmd == "index":
        data = args.data or Path("data") / args.repo.resolve().name
        stats = index_repository(args.repo, data)
        print(json.dumps(stats, indent=2))
        return

    data = args.data or _only_data_dir()
    if args.cmd == "search":
        index, _ = open_index(data)
        for chunk, score in index.search(args.query, k=args.k):
            print(f"{score:.4f}  {chunk.path}:{chunk.start_line}-{chunk.end_line}"
                  f"  [{chunk.symbol_type}] {chunk.symbol}")
    elif args.cmd == "symbol":
        _, symbols = open_index(data)
        print(json.dumps(symbols.lookup(args.name), indent=2))


def _only_data_dir() -> Path:
    """If exactly one repo is indexed under data/, use it without --data."""
    candidates = [p for p in Path("data").glob("*/manifest.json")]
    if len(candidates) == 1:
        return candidates[0].parent
    raise SystemExit(f"pass --data explicitly; found {len(candidates)} indexed repos under data/")


if __name__ == "__main__":
    main()
