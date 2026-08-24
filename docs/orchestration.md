# Orchestration & Webhook Automation (Phase 4)

## The graph

`graph/review_graph.py` wires Phase 3's pieces into a LangGraph `StateGraph`:

```
START ─▶ planner ──(skip: empty / oversized PR)──▶ skipped ─▶ END
            │
            ▼
         enrich ──┬─▶ review_correctness ──┐
   (index + AST   ├─▶ review_security ─────┼─▶ synthesize ─▶ post ─▶ END
    context per   └─▶ review_style ────────┘   (fan-in via
    hunk)              (fan-out)                Annotated reducer)
```

- **planner** is deterministic (no LLM spend): rejects empty diffs, caps PR size
  (25 files / 20 hunks), tags risky paths (auth, workflows, secrets).
- **Fan-out/fan-in**: reviewer nodes each append to `findings`, declared as
  `Annotated[list[Finding], operator.add]` — LangGraph merges the three branches'
  writes automatically.
- **Every node is traced** (name + seconds) into the state; `run_review` saves a
  JSON trace per run under `data/traces/`. Langfuse integration lands with the
  Docker stack in Phase 6; the trace shape is already what it will consume.
- All dependencies (LLM, embedder, GitHub client, index dirs) are injected via
  `Deps` — the whole graph runs in tests with fakes.

Measured on the seeded 1-hunk PR (warm index): planner 0s → enrich ~7s →
reviewers 18–25s each → synthesize+post ~0s.

## The webhook receiver

`api/webhook.py`, run with `python -m api`:

1. **HMAC verification first**: `X-Hub-Signature-256` checked against
   `GITHUB_WEBHOOK_SECRET` with `hmac.compare_digest` (constant-time). Unsigned or
   mis-signed requests get 403 before their payload is trusted for anything.
2. Event filter: `pull_request` with action opened / synchronize / reopened.
3. Repo allowlist check (same set the GitHub client enforces) before enqueueing.

Jobs go to an in-process queue with one worker thread; a new push to a PR whose
job is still queued **supersedes** it (reviewing an outdated commit is wasted
spend). Tradeoff vs Redis/rq: zero infrastructure, but the queue dies with the
process — revisited in Phase 6.

## Idempotency

Re-reviews must not pile up duplicate bot comments. `data/pr_state.json` maps
`repo#pr → {comment_ids, summary_id}`:

- previous **inline comments** are deleted (guarded `supersede_review_comments`)
  before the new ones post;
- the **summary** is an *issue comment* (editable), updated in place via its id —
  this is why `post_review_summary` switched from `create_review` to
  `create_issue_comment`.

## GitHub webhook setup (when going live on a test repo)

Repo → Settings → Webhooks → Add:
- Payload URL: your tunnel URL + `/webhook/github` (e.g. smee.io / ngrok, Phase 6)
- Content type: `application/json`
- Secret: the value of `GITHUB_WEBHOOK_SECRET` in `.env`
- Events: "Pull requests" only

## Milestone (2026-08-23)

Signed webhook POST → 202 queued → worker → full graph → 5/5 findings on the
seeded diff → dry-run posts (24 `[DRY_RUN]` lines across two runs) → trace JSON
saved. A mis-signed POST → 403. A second `synchronize` event for the same PR ran
the supersede path (no-op under dry-run since nothing was truly posted; the
supersede/edit flow is pinned by `tests/test_graph_webhook.py` with a fake client
that returns real ids).
