"""Flask webhook receiver: GitHub pull_request events -> review jobs.

Security gates, in order:
  1. HMAC signature (X-Hub-Signature-256) verified against GITHUB_WEBHOOK_SECRET
     with hmac.compare_digest — an unsigned or mis-signed POST is rejected 403
     before its body is trusted for anything.
  2. Event filter: only pull_request opened / synchronize / reopened.
  3. Repo allowlist: same set the GitHub client enforces — checked here too so
     unallowed repos don't even enqueue.

Jobs land on an in-process queue consumed by one daemon worker thread. A new
push to the same PR supersedes its queued (not-yet-started) job — reviewing an
outdated commit is wasted spend. Tradeoff vs a real broker (Redis/rq): survives
nothing (process dies = queue dies), but zero infrastructure; Phase 6 revisits.

Run:  python -m api            # port 8000
Test: POST /webhook/github with a signed payload; GET /healthz.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import queue
import threading
from collections.abc import Callable

from flask import Flask, jsonify, request

from config import settings
from review.safety import RateLimiter

log = logging.getLogger(__name__)

ACTIONS = {"opened", "synchronize", "reopened"}


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


class JobQueue:
    """One worker thread; newest job per PR wins while queued."""

    def __init__(self, runner: Callable[[dict], None]) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._latest: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._runner = runner
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def submit(self, job: dict) -> str:
        key = f"{job['gh_repo']}#{job['pr_number']}"
        with self._lock:
            superseded = key in self._latest
            self._latest[key] = job          # newest payload wins
        if not superseded:
            self._q.put(key)
        log.info("job %s %s", key, "superseded pending" if superseded else "enqueued")
        return key

    def _loop(self) -> None:
        while True:
            key = self._q.get()
            with self._lock:
                job = self._latest.pop(key, None)
            if job is None:
                continue
            try:
                self._runner(job)
            except Exception:
                log.exception("review job %s failed", key)


def default_runner(job: dict) -> None:
    """Clone the PR head, index it, run the graph, post (dry-run-guarded)."""
    from pathlib import Path

    from gh.client import GitHubClient
    from graph.review_graph import Deps, run_review

    client = GitHubClient()
    repo_path = client.clone_repo(job["gh_repo"], ref=job.get("head_sha"))
    diff_text = client.get_pr_diff(job["gh_repo"], job["pr_number"])
    name = job["gh_repo"].replace("/", "_")
    deps = Deps(repo_path=repo_path, data_dir=Path("data") / f"webhook-{name}",
                gh_client=client, gh_repo=job["gh_repo"], pr_number=job["pr_number"],
                state_file=Path("data") / "pr_state.json",
                trace_dir=Path("data") / "traces")
    result = run_review(deps, diff_text)
    log.info("reviewed %s#%s: kept=%s skip=%r", job["gh_repo"], job["pr_number"],
             len(result["summary"].kept) if result.get("summary") else 0,
             result.get("skip_reason", ""))


def create_app(runner: Callable[[dict], None] | None = None) -> Flask:
    app = Flask(__name__)
    jobs = JobQueue(runner or default_runner)
    limiter = RateLimiter()
    app.extensions["jobs"] = jobs
    app.extensions["limiter"] = limiter

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", dry_run=settings.dry_run)

    @app.post("/webhook/github")
    def github_webhook():
        if not verify_signature(settings.github_webhook_secret, request.get_data(),
                                request.headers.get("X-Hub-Signature-256")):
            return jsonify(error="invalid signature"), 403
        if request.headers.get("X-GitHub-Event") != "pull_request":
            return jsonify(status="ignored", reason="not a pull_request event"), 200
        payload = request.get_json(silent=True) or {}
        if payload.get("action") not in ACTIONS:
            return jsonify(status="ignored", reason=f"action={payload.get('action')}"), 200

        gh_repo = payload.get("repository", {}).get("full_name", "")
        pr = payload.get("pull_request", {})
        if gh_repo not in settings.allowed_repos:
            return jsonify(status="ignored", reason="repo not allowlisted"), 200
        if not limiter.allow():
            log.warning("rate limit hit; refusing review for %s", gh_repo)
            return jsonify(status="rate_limited", reason="too many reviews this hour"), 429

        key = jobs.submit({"gh_repo": gh_repo, "pr_number": pr.get("number"),
                           "head_sha": pr.get("head", {}).get("sha", "")})
        return jsonify(status="queued", job=key), 202

    return app
