"""Phase 4: graph end-to-end with fakes, webhook security gates, idempotent posting."""
import hashlib
import hmac
import json
import time

import pytest

from api.webhook import create_app, verify_signature
from config import settings
from graph.review_graph import Deps, run_review
from tests.test_qa_agent import FakeResponse
from tests.test_store import FakeEmbedder

DIFF = """\
--- a/payments.py
+++ b/payments.py
@@ -1,2 +1,3 @@
 def process_refund(order_id):
+    total = amount / count
     return refund(order_id)
"""

FINDING_JSON = json.dumps({"findings": [{
    "path": "payments.py", "line": 2, "severity": "high", "category": "logic",
    "comment": "Possible division by zero when count is 0.", "confidence": 0.9}]})


class FakeChat:
    def __init__(self, payload=FINDING_JSON):
        self.payload = payload
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        return FakeResponse(text=self.payload)


class FakeGH:
    """Records posts; returns increasing ids like the real client."""

    def __init__(self):
        self.posted = []
        self.superseded = []
        self.summaries = []
        self._next = 100

    def post_review_comment(self, repo, pr, path, line, body):
        self._next += 1
        self.posted.append((path, line, self._next))
        return self._next

    def post_review_summary(self, repo, pr, body, prior_comment_id=None):
        self.summaries.append(prior_comment_id)
        return prior_comment_id or 900

    def supersede_review_comments(self, repo, pr, ids):
        self.superseded.append(list(ids))


def make_deps(tmp_path, gh=None, chat=None):
    (tmp_path / "payments.py").write_text(
        "def process_refund(order_id):\n    total = amount / count\n"
        "    return refund(order_id)\n", encoding="utf-8")
    return Deps(repo_path=tmp_path, data_dir=tmp_path / "data",
                chat_fn=chat or FakeChat(), embed_fn=FakeEmbedder(),
                gh_client=gh, gh_repo="me/repo" if gh else "", pr_number=7,
                state_file=tmp_path / "pr_state.json")


def test_graph_runs_all_reviewers_and_synthesizes(tmp_path):
    chat = FakeChat()
    result = run_review(make_deps(tmp_path, chat=chat), DIFF)
    assert chat.calls == 3                       # correctness, security, style all ran
    assert len(result["findings"]) == 3          # fan-in collected each branch
    assert len(result["summary"].kept) == 1      # same-line dedupe kept strongest
    nodes = {t["node"] for t in result["trace"]}
    assert {"planner", "enrich", "review_correctness", "review_security",
            "review_style", "synthesize"} <= nodes


def test_planner_skips_empty_diff_without_llm_spend(tmp_path):
    chat = FakeChat()
    result = run_review(make_deps(tmp_path, chat=chat), "")
    assert result["skip_reason"]
    assert chat.calls == 0


def test_planner_skips_oversized_pr(tmp_path):
    hunks = "\n".join(
        f"--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1,1 +1,1 @@\n-a\n+b" for i in range(30))
    chat = FakeChat()
    result = run_review(make_deps(tmp_path, chat=chat), hunks + "\n")
    assert "too large" in result["skip_reason"]
    assert chat.calls == 0


def test_second_review_supersedes_first(tmp_path):
    gh = FakeGH()
    deps = make_deps(tmp_path, gh=gh)
    run_review(deps, DIFF)
    assert gh.superseded == []                   # first review: nothing to supersede
    first_ids = [cid for _, _, cid in gh.posted]

    run_review(deps, DIFF)                       # simulated new push
    assert gh.superseded == [first_ids]          # old inline comments removed
    assert gh.summaries[1] == 900                # summary edited in place, not re-created


# --------------------------------------------------------------- webhook

def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "s3cret")
    monkeypatch.setattr(settings, "github_allowed_repos", "me/allowed")
    ran = []
    app = create_app(runner=ran.append)
    app.config["ran"] = ran
    return app


def payload(repo="me/allowed", action="opened", number=7):
    return json.dumps({"action": action, "repository": {"full_name": repo},
                       "pull_request": {"number": number, "head": {"sha": "abc123"}}}).encode()


def post(app, body, secret="s3cret", event="pull_request"):
    return app.test_client().post(
        "/webhook/github", data=body,
        headers={"X-Hub-Signature-256": sign(secret, body), "X-GitHub-Event": event,
                 "Content-Type": "application/json"})


def test_bad_signature_rejected(app):
    body = payload()
    assert post(app, body, secret="wrong").status_code == 403
    r = app.test_client().post("/webhook/github", data=body)   # unsigned
    assert r.status_code == 403


def test_valid_pr_event_enqueues(app):
    r = post(app, payload())
    assert r.status_code == 202
    deadline = time.time() + 2
    while not app.config["ran"] and time.time() < deadline:
        time.sleep(0.02)
    assert app.config["ran"][0]["gh_repo"] == "me/allowed"
    assert app.config["ran"][0]["head_sha"] == "abc123"


def test_non_pr_events_and_unallowed_repos_ignored(app):
    assert post(app, payload(), event="push").status_code == 200
    assert b"ignored" in post(app, payload(action="closed")).data
    assert b"not allowlisted" in post(app, payload(repo="evil/repo")).data
    assert app.config["ran"] == []


def test_verify_signature_requires_secret():
    assert not verify_signature("", b"body", "sha256=deadbeef")
    assert not verify_signature("secret", b"body", None)
