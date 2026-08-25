"""Dashboard routes + run store, driven with fakes (no LLM, no embeddings)."""
import json
import time

import pytest

from api.store import RunStore
from api.webhook import JobQueue, create_app
from config import settings

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


# ------------------------------------------------------------------ store

def test_store_roundtrip_and_ordering(tmp_path):
    store = RunStore(tmp_path)
    a = store.create("review", "first", {"diff": "x"})
    b = store.create("ask", "second", {"question": "q"})
    assert store.get(a.id).title == "first"
    assert next(r.id for r in store.list()) == b.id or a.created_at == b.created_at


def test_store_update_stamps_finish_time_on_terminal(tmp_path):
    store = RunStore(tmp_path)
    run = store.create("ask", "q", {})
    assert store.update(run.id, status="running").finished_at == ""
    done = store.update(run.id, status="done", result={"answer": "hi"})
    assert done.finished_at and done.duration_s is not None


def test_store_ignores_corrupt_files(tmp_path):
    store = RunStore(tmp_path)
    store.create("ask", "good", {})
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert len(store.list()) == 1        # one bad file must not break the dashboard


# ------------------------------------------------------------------ queue

def test_dashboard_jobs_do_not_supersede_each_other():
    seen = []
    q = JobQueue(seen.append)
    q.submit({"dashboard_run": "aaa", "gh_repo": "local", "pr_number": 0})
    q.submit({"dashboard_run": "bbb", "gh_repo": "local", "pr_number": 0})
    deadline = time.time() + 2
    while len(seen) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert {j["dashboard_run"] for j in seen} == {"aaa", "bbb"}


def test_webhook_jobs_still_supersede_by_pr():
    seen = []
    q = JobQueue(lambda j: (time.sleep(0.05), seen.append(j)))
    for sha in ("sha1", "sha2", "sha3"):
        q.submit({"gh_repo": "me/r", "pr_number": 7, "head_sha": sha})
    time.sleep(0.4)
    assert len(seen) < 3                 # stale queued reviews collapsed


# ------------------------------------------------------------------ routes

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "s")
    monkeypatch.chdir(tmp_path)          # data/runs lands in tmp
    (tmp_path / "payments.py").write_text(
        "def process_refund(order_id):\n    total = amount / count\n"
        "    return refund(order_id)\n", encoding="utf-8")

    from tests.test_graph_webhook import FakeChat
    from tests.test_store import FakeEmbedder
    app = create_app(repo_path=tmp_path, data_dir=tmp_path / "idx",
                     chat_fn=FakeChat(FINDING_JSON), embed_fn=FakeEmbedder())
    app.config["TESTING"] = True
    return app


def wait_for(client, run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/run/{run_id}").get_json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_empty_dashboard_renders(app):
    r = app.test_client().get("/")
    assert r.status_code == 200 and b"No runs yet" in r.data


def test_review_from_pasted_diff_end_to_end(app):
    c = app.test_client()
    r = c.post("/new/review", data={"diff": DIFF})
    assert r.status_code == 302
    run_id = r.headers["Location"].rsplit("/", 1)[-1]

    data = wait_for(c, run_id)
    assert data["status"] == "done", data.get("error")
    assert data["result"]["findings"][0]["path"] == "payments.py"

    page = c.get(f"/run/{run_id}")
    assert b"payments.py" in page.data and b"division by zero" in page.data


def test_review_requires_input(app):
    r = app.test_client().post("/new/review", data={"diff": "", "pr_number": ""})
    assert r.status_code == 400 and b"Paste a diff" in r.data


def test_ask_requires_question(app):
    r = app.test_client().post("/new/ask", data={"question": ""})
    assert r.status_code == 400


def test_unknown_run_404s(app):
    assert app.test_client().get("/run/deadbeef").status_code == 404
    assert app.test_client().get("/api/run/deadbeef").status_code == 404


def test_failed_run_is_recorded_not_crashed(app):
    """A diff the parser rejects must surface as an error run, not a 500.

    A bare hunk with no ---/+++ headers is exactly what PyGithub used to hand us
    (see gh.client.build_diff); unidiff rejects it with UnidiffParseError.
    """
    c = app.test_client()
    bare_hunk = "@@ -18,2 +18,2 @@ def f():\n-a\n+b\n"
    r = c.post("/new/review", data={"diff": bare_hunk})
    run_id = r.headers["Location"].rsplit("/", 1)[-1]
    data = wait_for(c, run_id)
    assert data["status"] == "error" and data["error"]
    assert b"Run failed" in c.get(f"/run/{run_id}").data


def test_webhook_still_works_alongside_dashboard(app):
    assert app.test_client().get("/healthz").get_json()["status"] == "ok"
    assert app.test_client().post("/webhook/github", data=b"{}").status_code == 403
