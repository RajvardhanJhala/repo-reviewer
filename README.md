# repo-reviewer

An AI agent that indexes a GitHub repository with AST-aware chunking, answers architecture questions with `file:line` citations, and reviews pull requests with inline comments — safely (comment-only by construction, repo allowlist, dry-run mode).

> Status: Phase 0 — scaffolding, model layer, guarded GitHub client. See `docs/BUILD_PLAN.md`.

## Quickstart
```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .env.example .env                                # fill in keys
python -m scripts.smoke_llm
pytest -q
```
