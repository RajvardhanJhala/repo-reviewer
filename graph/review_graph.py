"""The Phase 4 LangGraph: planner -> analyze -> enrich -> 3 reviewers (fan-out)
-> synthesize (fan-in) -> post.

Same building blocks as Phase 3's linear pipeline, now as graph nodes with:
  - a planner that can skip trivial/oversized PRs before any LLM spend,
  - reviewer fan-out/fan-in via LangGraph's Annotated-reducer state,
  - idempotent posting (re-reviews supersede the previous bot comments),
  - a per-node trace recorded into the state and saved as JSON per run.

Dependencies (LLM, embedder, GitHub client, index dirs) are injected via Deps —
the whole graph runs in tests with fakes, like every other layer here.
"""
from __future__ import annotations

import json
import operator
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from review.context import ContextBuilder, HunkContext
from review.diff import FileDiff, commentable_lines, parse_diff
from review.reviewers import REVIEWER_PROMPTS, Finding, run_reviewer
from review.synthesizer import ReviewSummary, summary_comment, synthesize

MAX_FILES = 25
MAX_HUNKS = 20
RISKY_PATH_MARKERS = ("auth", "security", "crypto", "secret", "token",
                      ".github/workflows", "Dockerfile")


@dataclass
class Deps:
    repo_path: Path
    data_dir: Path
    chat_fn: Callable[..., Any] | None = None
    embed_fn: Callable[..., Any] | None = None
    gh_client: Any = None                  # GitHubClient-shaped; None = don't post
    gh_repo: str = ""
    pr_number: int = 0
    state_file: Path | None = None         # idempotency store (JSON)
    trace_dir: Path | None = None

    def resolve(self) -> None:
        if self.chat_fn is None:
            from llm.router import router
            self.chat_fn = router.chat


class ReviewState(TypedDict, total=False):
    diff_text: str
    skip_reason: str
    risk_notes: list[str]
    files: list[FileDiff]
    commentable: dict[str, set[int]]
    contexts: list[HunkContext]
    findings: Annotated[list[Finding], operator.add]
    summary: ReviewSummary
    posted_comment_ids: list[int]
    trace: Annotated[list[dict], operator.add]


def _traced(name: str, fn: Callable[[ReviewState], dict]) -> Callable[[ReviewState], dict]:
    def wrapped(state: ReviewState) -> dict:
        t0 = time.perf_counter()
        out = fn(state)
        out.setdefault("trace", []).append(
            {"node": name, "seconds": round(time.perf_counter() - t0, 3)})
        return out
    return wrapped


def build_review_graph(deps: Deps):
    deps.resolve()

    # ---------------------------------------------------------------- nodes

    def planner(state: ReviewState) -> dict:
        """Deterministic gate: no LLM spend on empty or unreviewably large PRs."""
        files = parse_diff(state["diff_text"])
        if not files:
            return {"files": [], "skip_reason": "no reviewable files in diff"}
        n_hunks = sum(len(f.hunks) for f in files)
        if len(files) > MAX_FILES or n_hunks > MAX_HUNKS:
            return {"files": files,
                    "skip_reason": f"too large to review well ({len(files)} files, "
                                   f"{n_hunks} hunks; caps {MAX_FILES}/{MAX_HUNKS})"}
        risks = [f.path for f in files
                 if any(m in f.path.lower() for m in RISKY_PATH_MARKERS)]
        return {"files": files, "commentable": commentable_lines(files),
                "risk_notes": [f"touches sensitive path: {p}" for p in risks]}

    def enrich(state: ReviewState) -> dict:
        from indexer.pipeline import index_repository, open_index
        index_repository(deps.repo_path, deps.data_dir, embed_fn=deps.embed_fn)
        index, symbols = open_index(deps.data_dir, embed_fn=deps.embed_fn)
        builder = ContextBuilder(index, symbols, deps.repo_path)
        contexts = [builder.build(fd, hunk)
                    for fd in state["files"] for hunk in fd.hunks]
        return {"contexts": contexts}

    def make_reviewer(name: str):
        def node(state: ReviewState) -> dict:
            found = run_reviewer(name, state["contexts"], deps.chat_fn,
                                 state["commentable"])
            return {"findings": found}
        return node

    def synthesize_node(state: ReviewState) -> dict:
        return {"summary": synthesize(state.get("findings", []))}

    def post(state: ReviewState) -> dict:
        if deps.gh_client is None or not deps.gh_repo:
            return {"posted_comment_ids": []}
        store = _load_state(deps.state_file)
        key = f"{deps.gh_repo}#{deps.pr_number}"
        prior = store.get(key, {})
        if prior.get("comment_ids"):
            deps.gh_client.supersede_review_comments(
                deps.gh_repo, deps.pr_number, prior["comment_ids"])
        ids = []
        for f in state["summary"].kept:
            body = f"**[{f.severity} · {f.category} · {f.reviewer}]** {f.comment}"
            if f.suggested_fix:
                body += f"\n```suggestion\n{f.suggested_fix}\n```"
            cid = deps.gh_client.post_review_comment(
                deps.gh_repo, deps.pr_number, f.path, f.line, body)
            if cid is not None:
                ids.append(cid)
        summary_id = deps.gh_client.post_review_summary(
            deps.gh_repo, deps.pr_number, summary_comment(state["summary"]),
            prior_comment_id=prior.get("summary_id"))
        store[key] = {"comment_ids": ids, "summary_id": summary_id}
        _save_state(deps.state_file, store)
        return {"posted_comment_ids": ids}

    def skip_post(state: ReviewState) -> dict:
        return {"posted_comment_ids": []}

    # ---------------------------------------------------------------- wiring

    g = StateGraph(ReviewState)
    g.add_node("planner", _traced("planner", planner))
    g.add_node("enrich", _traced("enrich", enrich))
    for name in REVIEWER_PROMPTS:
        g.add_node(f"review_{name}", _traced(f"review_{name}", make_reviewer(name)))
    g.add_node("synthesize", _traced("synthesize", synthesize_node))
    g.add_node("post", _traced("post", post))
    g.add_node("skipped", _traced("skipped", skip_post))

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner",
                            lambda s: "skipped" if s.get("skip_reason") else "enrich",
                            {"skipped": "skipped", "enrich": "enrich"})
    for name in REVIEWER_PROMPTS:          # fan-out after enrichment
        g.add_edge("enrich", f"review_{name}")
        g.add_edge(f"review_{name}", "synthesize")   # fan-in via findings reducer
    g.add_edge("synthesize", "post")
    g.add_edge("post", END)
    g.add_edge("skipped", END)
    return g.compile()


def run_review(deps: Deps, diff_text: str) -> ReviewState:
    graph = build_review_graph(deps)
    result: ReviewState = graph.invoke({"diff_text": diff_text, "findings": [], "trace": []})
    if deps.trace_dir:
        deps.trace_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = f"{deps.gh_repo.replace('/', '_') or 'local'}-pr{deps.pr_number}-{stamp}.json"
        (deps.trace_dir / name).write_text(json.dumps({
            "skip_reason": result.get("skip_reason", ""),
            "risk_notes": result.get("risk_notes", []),
            "n_findings": len(result.get("findings", [])),
            "n_kept": len(result["summary"].kept) if result.get("summary") else 0,
            "nodes": result.get("trace", []),
        }, indent=1), encoding="utf-8")
    return result


# ------------------------------------------------------- idempotency store

def _load_state(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path | None, data: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
