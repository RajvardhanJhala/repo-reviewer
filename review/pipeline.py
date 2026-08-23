"""The Phase 3 pipeline: diff text -> enriched hunks -> 3 reviewers -> synthesis -> post.

Posting goes through gh.client.GitHubClient, which enforces the allowlist and
DRY_RUN. With no PR number the result is just printed (local review mode).

Usage:
    python -m review --diff changes.patch --repo .
    python -m review --pr 7 --gh-repo owner/name --repo ./clone   # dry-run posts
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from review.context import ContextBuilder, HunkContext
from review.diff import FileDiff, commentable_lines, parse_diff
from review.reviewers import REVIEWER_PROMPTS, Finding, run_reviewer
from review.synthesizer import ReviewSummary, summary_comment, synthesize

log = logging.getLogger(__name__)

MAX_HUNKS_PER_REVIEW = 20   # planner-lite: giant PRs get truncated, loudly


@dataclass
class ReviewResult:
    summary: ReviewSummary
    all_findings: list[Finding]
    files: list[FileDiff]
    truncated: bool = False


def review_diff(diff_text: str, repo_path: Path, data_dir: Path,
                chat_fn: Callable[..., Any] | None = None,
                embed_fn: Callable | None = None) -> ReviewResult:
    if chat_fn is None:
        from llm.router import router
        chat_fn = router.chat

    files = parse_diff(diff_text)
    commentable = commentable_lines(files)

    from indexer.pipeline import index_repository, open_index
    index_repository(repo_path, data_dir, embed_fn=embed_fn)  # incremental: cheap when clean
    index, symbols = open_index(data_dir, embed_fn=embed_fn)
    builder = ContextBuilder(index, symbols, repo_path)

    contexts: list[HunkContext] = []
    for fd in files:
        for hunk in fd.hunks:
            contexts.append(builder.build(fd, hunk))
    truncated = len(contexts) > MAX_HUNKS_PER_REVIEW
    if truncated:
        log.warning("PR has %d hunks; reviewing first %d", len(contexts), MAX_HUNKS_PER_REVIEW)
        contexts = contexts[:MAX_HUNKS_PER_REVIEW]

    all_findings: list[Finding] = []
    for name in REVIEWER_PROMPTS:
        found = run_reviewer(name, contexts, chat_fn, commentable)
        log.info("reviewer=%s findings=%d", name, len(found))
        all_findings.extend(found)

    return ReviewResult(summary=synthesize(all_findings), all_findings=all_findings,
                        files=files, truncated=truncated)


def post_review(result: ReviewResult, gh_repo: str, pr_number: int) -> None:
    """Inline comments + summary through the guarded client (DRY_RUN respected)."""
    from gh.client import GitHubClient
    client = GitHubClient()
    for f in result.summary.kept:
        body = f"**[{f.severity} · {f.category} · {f.reviewer}]** {f.comment}"
        if f.suggested_fix:
            body += f"\n```suggestion\n{f.suggested_fix}\n```"
        client.post_review_comment(gh_repo, pr_number, f.path, f.line, body)
    client.post_review_summary(gh_repo, pr_number, summary_comment(result.summary))
