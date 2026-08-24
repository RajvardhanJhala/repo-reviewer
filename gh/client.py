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


def build_diff(files) -> str:
    """PyGithub File objects -> a parseable unified diff.

    Pure function of the file list so it is testable without any network.
    """
    parts = []
    for f in files:
        patch = getattr(f, "patch", None)
        if not patch:                      # binary files have no patch
            continue
        status = getattr(f, "status", "modified")
        old_name = getattr(f, "previous_filename", None) or f.filename
        old = "/dev/null" if status == "added" else f"a/{old_name}"
        new = "/dev/null" if status == "removed" else f"b/{f.filename}"
        parts.append(f"--- {old}\n+++ {new}\n{patch.rstrip()}\n")
    return "".join(parts)


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
        """Reassemble a unified diff from PyGithub's per-file patches.

        f.patch contains ONLY the hunks - no `--- a/x` / `+++ b/x` file headers -
        so the headers must be rebuilt or any diff parser rejects the result with
        "Unexpected hunk found". Renames use previous_filename for the old side;
        added/removed files use /dev/null, which is how the parser detects them.
        """
        return build_diff(self.get_pr(full_name, number).get_files())

    # ---------- writes (guarded) ----------
    def _guard(self, full_name: str, action: str) -> bool:
        """Return True only if the write should actually execute."""
        if full_name not in settings.allowed_repos:
            raise PermissionError(f"{full_name} is not in GITHUB_ALLOWED_REPOS; refusing {action}")
        if settings.dry_run:
            log.info("[DRY_RUN] would %s on %s", action, full_name)
            return False
        return True

    def post_review_comment(self, full_name: str, number: int, path: str, line: int,
                            body: str) -> int | None:
        """Returns the comment id (None in dry-run) so re-reviews can supersede it."""
        if not self._guard(full_name, f"post inline comment {path}:{line}"):
            print(f"[DRY_RUN] {full_name}#{number} {path}:{line}\n  {body}\n")
            return None
        pr = self.get_pr(full_name, number)
        commit = pr.get_commits().reversed[0]
        return pr.create_review_comment(body=body, commit=commit, path=path, line=line).id

    def post_review_summary(self, full_name: str, number: int, body: str,
                            prior_comment_id: int | None = None) -> int | None:
        """Summary is an ISSUE comment (not a review): issue comments are editable,
        which lets a re-review update one summary in place instead of piling a new
        review onto the PR per push. Advisory text only — the no-approve/no-merge
        invariant is unchanged."""
        if not self._guard(full_name, "post summary comment"):
            print(f"[DRY_RUN] {full_name}#{number} SUMMARY:\n  {body}\n")
            return None
        repo = self._gh.get_repo(full_name)
        if prior_comment_id is not None:
            try:
                repo.get_issue(number).get_comment(prior_comment_id).edit(body)
                return prior_comment_id
            except Exception:
                log.warning("prior summary %s gone; posting fresh", prior_comment_id)
        return self.get_pr(full_name, number).create_issue_comment(body).id

    def supersede_review_comments(self, full_name: str, number: int,
                                  comment_ids: list[int]) -> None:
        """Remove this bot's own previous inline comments before a re-review."""
        if not self._guard(full_name, f"supersede {len(comment_ids)} old comments"):
            print(f"[DRY_RUN] {full_name}#{number} would supersede comments {comment_ids}")
            return
        pr = self.get_pr(full_name, number)
        for cid in comment_ids:
            try:
                pr.get_review_comment(cid).delete()
            except Exception:
                log.warning("old comment %s already gone", cid)
