"""Local dashboard: browse past reviews, start new ones, ask the codebase questions.

Server-rendered Jinja (no build step, no JS framework) plus three small JSON
endpoints the pages poll. Reviews and questions take minutes, so both run on the
existing JobQueue worker and the page polls the run until it reaches a terminal
state — the request thread never blocks.

Mounted by api.webhook.create_app(); run with `python -m api`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from api.store import RunStore

log = logging.getLogger(__name__)

bp = Blueprint("dashboard", __name__)


def _store() -> RunStore:
    return current_app.extensions["runs"]


def _repo_path() -> Path:
    return Path(current_app.config["REPO_PATH"])


def _data_dir() -> Path:
    return Path(current_app.config["DATA_DIR"])


# ------------------------------------------------------------------- pages

@bp.get("/")
def index():
    return render_template("index.html", runs=_store().list())


@bp.get("/run/<run_id>")
def run_detail(run_id: str):
    run = _store().get(run_id)
    if run is None:
        return render_template("404.html", run_id=run_id), 404
    template = "review_detail.html" if run.kind == "review" else "ask_detail.html"
    return render_template(template, run=run)


@bp.get("/new/review")
def new_review():
    return render_template("new_review.html")


@bp.get("/new/ask")
def new_ask():
    return render_template("new_ask.html")


# --------------------------------------------------------------- actions

@bp.post("/new/review")
def start_review():
    diff_text = (request.form.get("diff") or "").strip()
    pr_number = (request.form.get("pr_number") or "").strip()
    gh_repo = (request.form.get("gh_repo") or "").strip()

    if pr_number:
        title = f"PR #{pr_number} · {gh_repo or 'unknown repo'}"
        payload = {"pr_number": int(pr_number), "gh_repo": gh_repo}
    elif diff_text:
        first = next((ln for ln in diff_text.splitlines() if ln.startswith("+++")), "")
        title = f"Pasted diff · {first.removeprefix('+++ b/') or 'unknown file'}"
        payload = {"diff": diff_text}
    else:
        return render_template("new_review.html", error="Paste a diff or give a PR number."), 400

    run = _store().create("review", title, payload)
    current_app.extensions["jobs"].submit({"dashboard_run": run.id, "kind": "review",
                                           "gh_repo": gh_repo or "local",
                                           "pr_number": pr_number or 0})
    return redirect(url_for("dashboard.run_detail", run_id=run.id))


@bp.post("/new/ask")
def start_ask():
    question = (request.form.get("question") or "").strip()
    if not question:
        return render_template("new_ask.html", error="Ask something."), 400
    run = _store().create("ask", question, {"question": question})
    current_app.extensions["jobs"].submit({"dashboard_run": run.id, "kind": "ask",
                                           "gh_repo": "local", "pr_number": 0})
    return redirect(url_for("dashboard.run_detail", run_id=run.id))


@bp.get("/api/run/<run_id>")
def run_json(run_id: str):
    run = _store().get(run_id)
    if run is None:
        return jsonify(error="not found"), 404
    return jsonify(run.to_dict())


# ------------------------------------------------- the work, off-thread

def make_dashboard_runner(repo_path: Path, data_dir: Path, store: RunStore,
                          chat_fn: Callable | None = None,
                          embed_fn: Callable | None = None) -> Callable[[dict], None]:
    """Returns a JobQueue runner that executes dashboard jobs and records results."""

    def run_job(job: dict) -> None:
        run_id = job.get("dashboard_run")
        if run_id is None:
            return
        store.update(run_id, status="running")
        run = store.get(run_id)
        try:
            if run.kind == "review":
                result = _do_review(run, repo_path, data_dir, chat_fn, embed_fn)
            else:
                result = _do_ask(run, repo_path, data_dir, chat_fn, embed_fn)
            store.update(run_id, status="done", result=result)
        except Exception as e:
            log.exception("dashboard run %s failed", run_id)
            store.update(run_id, status="error", error=f"{type(e).__name__}: {e}")

    return run_job


def _do_review(run, repo_path, data_dir, chat_fn, embed_fn) -> dict:
    from review.pipeline import review_diff

    if "diff" in run.request:
        diff_text = run.request["diff"]
    else:
        from gh.client import GitHubClient
        diff_text = GitHubClient().get_pr_diff(run.request["gh_repo"],
                                               run.request["pr_number"])
    result = review_diff(diff_text, repo_path, data_dir,
                         chat_fn=chat_fn, embed_fn=embed_fn)
    s = result.summary
    return {
        "verdict": s.verdict(),
        "findings": [f.model_dump() for f in s.kept],
        "counts": {"raw": len(result.all_findings), "kept": len(s.kept),
                   "low_confidence": s.dropped_low_confidence,
                   "duplicates": s.dropped_duplicates, "over_cap": s.dropped_over_cap},
        "files": [f.path for f in result.files],
    }


def _do_ask(run, repo_path, data_dir, chat_fn, embed_fn) -> dict:
    from indexer.pipeline import index_repository, open_index
    from qa.agent import QAAgent
    from qa.tools import QATools
    index_repository(repo_path, data_dir, embed_fn=embed_fn)
    index, symbols = open_index(data_dir, embed_fn=embed_fn)
    agent = QAAgent(QATools(repo_path, index, symbols), chat_fn=chat_fn)
    answer = agent.ask(run.request["question"])
    return {
        "answer": answer.answer,
        "citations": [{"path": c.path, "start": c.start, "end": c.end,
                       "valid": c.valid, "reason": c.reason} for c in answer.citations],
        "steps": answer.steps,
        "tool_trace": answer.tool_trace,
        "ran_out_of_steps": answer.ran_out_of_steps,
    }
