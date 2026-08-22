"""CLI: python -m eval --repo . [--questions eval/questions_repo_reviewer.json]"""
from __future__ import annotations

import argparse
from pathlib import Path

from eval.retrieval_eval import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="eval")
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--questions", type=Path, default=Path("eval/questions_repo_reviewer.json"))
    ap.add_argument("--out", type=Path, default=Path("docs/retrieval_eval.md"))
    args = ap.parse_args()

    table = run(args.repo, args.questions, data_root=Path("data"))
    args.out.write_text(table, encoding="utf-8")
    print(f"\n{table}\nwritten to {args.out}")


if __name__ == "__main__":
    main()
