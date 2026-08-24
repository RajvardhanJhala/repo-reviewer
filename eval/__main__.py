"""CLI:
    python -m eval --repo .              # retrieval hit-rate benchmark
    python -m eval --reviews --repo .    # PR-review precision/recall benchmark
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from eval.retrieval_eval import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="eval")
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--questions", type=Path, default=Path("eval/questions_repo_reviewer.json"))
    ap.add_argument("--out", type=Path, default=Path("docs/retrieval_eval.md"))
    ap.add_argument("--reviews", action="store_true",
                    help="run the PR review precision/recall benchmark instead")
    args = ap.parse_args()

    if args.reviews:
        from eval.review_eval import run as run_reviews
        table = run_reviews(args.repo, Path("eval/benchmark_prs.json"), Path("data/repo-reviewer"))
        out = Path("docs/review_eval.md")
        out.write_text(table, encoding="utf-8")
        print(f"\n{table}\nwritten to {out}")
        return

    table = run(args.repo, args.questions, data_root=Path("data"))
    args.out.write_text(table, encoding="utf-8")
    print(f"\n{table}\nwritten to {args.out}")


if __name__ == "__main__":
    main()
