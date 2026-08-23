"""CLI: python -m review --diff changes.patch --repo .
     python -m review --pr 7 --gh-repo owner/name --repo ./clone
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from review.pipeline import post_review, review_diff
from review.synthesizer import summary_comment


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="review")
    ap.add_argument("--repo", type=Path, default=Path("."), help="checkout of the NEW code")
    ap.add_argument("--data", type=Path, default=None)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--diff", type=Path, help="unified diff file to review")
    src.add_argument("--pr", type=int, help="PR number (needs --gh-repo)")
    ap.add_argument("--gh-repo", help="owner/name on GitHub")
    args = ap.parse_args()

    if args.pr is not None:
        if not args.gh_repo:
            ap.error("--pr requires --gh-repo")
        from gh.client import GitHubClient
        diff_text = GitHubClient().get_pr_diff(args.gh_repo, args.pr)
    else:
        diff_text = args.diff.read_text(encoding="utf-8")

    data = args.data or Path("data") / args.repo.resolve().name
    result = review_diff(diff_text, args.repo, data)

    print("\n" + "=" * 72)
    for f in result.summary.kept:
        print(f"{f.path}:{f.line}  [{f.severity}/{f.confidence:.2f}] "
              f"({f.reviewer}:{f.category})\n  {f.comment}\n")
    print(summary_comment(result.summary))
    print("=" * 72)
    print(f"raw findings: {len(result.all_findings)}  kept: {len(result.summary.kept)}")

    if args.pr is not None:
        post_review(result, args.gh_repo, args.pr)

    from llm.router import router
    print("router:", router.stats.summary())


if __name__ == "__main__":
    main()
