"""Thin GitHub wrapper. Every WRITE goes through _guard(): allowlist + DRY_RUN.

Design rule (document in README): this client deliberately has NO approve /
request-changes / merge / push methods. The agent cannot do those by construction.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from git import Repo
from github import Auth, Github

from config import settings

log = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self) -> None:
        self._gh = Github(auth=Auth.Token(settings.github_token))

    # ---------- reads ----------
    def clone_repo(self, full_name: str, dest: Path | None = None, ref: str | None = None) -> Path:
        dest = dest or Path(tempfile.mkdtemp(prefix="repo-reviewer-"))
        url = f"https://x-access-token:{settings.github_token}@github.com/{full_name}.git"
        repo = Repo.clone_from(url, dest, depth=50)
        if ref:
            repo.git.checkout(ref)
        return dest

    def get_pr(self, full_name: str, number: int):
        return self._gh.get_repo(full_name).get_pull(number)

    def get_pr_diff(self, full_name: str, number: int) -> str:
        pr = self.get_pr(full_name, number)
        # PyGithub doesn't expose the raw diff directly; stitch per-file patches
        return "\n".join(f.patch for f in pr.get_files() if f.patch)

    # ---------- writes (guarded) ----------
    def _guard(self, full_name: str, action: str) -> bool:
        """Return True only if the write should actually execute."""
        if full_name not in settings.allowed_repos:
            raise PermissionError(f"{full_name} is not in GITHUB_ALLOWED_REPOS; refusing {action}")
        if settings.dry_run:
            log.info("[DRY_RUN] would %s on %s", action, full_name)
            return False
        return True

    def post_review_comment(self, full_name: str, number: int, path: str, line: int, body: str) -> None:
        if not self._guard(full_name, f"post inline comment {path}:{line}"):
            print(f"[DRY_RUN] {full_name}#{number} {path}:{line}\n  {body}\n")
            return
        pr = self.get_pr(full_name, number)
        commit = pr.get_commits().reversed[0]
        pr.create_review_comment(body=body, commit=commit, path=path, line=line)

    def post_review_summary(self, full_name: str, number: int, body: str) -> None:
        if not self._guard(full_name, "post summary review"):
            print(f"[DRY_RUN] {full_name}#{number} SUMMARY:\n  {body}\n")
            return
        # event="COMMENT" only. Never APPROVE / REQUEST_CHANGES.
        self.get_pr(full_name, number).create_review(body=body, event="COMMENT")
