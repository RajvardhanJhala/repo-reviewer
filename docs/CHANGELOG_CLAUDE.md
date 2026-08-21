# Change Log — Claude Code Sessions

Every change Claude makes to this codebase is logged here: **what** changed, **why**, and the
reasoning behind it. Concept boxes (`> 💡`) explain any Agentic-AI / RAG / LLM ideas the change touches.
Newest entries at the bottom, grouped by session date.

---

## 2026-08-22 — Session 1: Cleanup, environment repair, model migration

### 1. Deleted orphaned folders at `C:\Agentic-RAG\` root

**What:** Removed `repo-reviewer-phase0\`, and the top-level `.venv\`, `.pytest_cache\`, `.ruff_cache\`.

**Why:** `repo-reviewer-phase0` was a leftover unzipped copy. Before deleting, we verified it held
nothing unique: its nested repo sat at the *same* commit (`01b822a`) as the real clone with a clean
working tree, its outer `.git` tracked only a gitlink (a pointer, no actual file content), and its
`.env` differed from the clone's only in line endings (all 8 values hash-identical). The three cache
folders belonged to a venv created in the wrong directory.

**Logic:** Never delete before proving nothing unique is lost — compare commits, working-tree status,
and file hashes first. One working directory (`repo-reviewer\`) removes ambiguity about "which copy
am I editing?"

---

### 2. Created scaffold directories with `.gitkeep`

**What:** Added empty dirs `indexer/ qa/ review/ graph/ api/ eval/ docker/`, each containing an
empty `.gitkeep` file.

**Why:** These are homes for later build phases (indexing, Q&A, PR review, orchestration graph,
API, evaluation, deployment). Git cannot track empty directories — it tracks files only — so the
conventional workaround is a zero-byte `.gitkeep` inside each.

**Logic:** Scaffolding the layout now means every future phase drops code into a pre-agreed place,
and the repo structure documents the roadmap.

> 💡 **Concept — anatomy of an agentic RAG system.** The folder names mirror the standard pipeline:
> **indexer** turns the codebase into searchable vectors/keywords; **qa** answers questions by
> retrieving relevant chunks and letting an LLM reason over them (that's RAG — Retrieval-Augmented
> Generation: instead of hoping the model "knows" your code, you *retrieve* the relevant snippets
> and paste them into the prompt); **review** applies that to PR diffs; **graph** wires the steps
> into an agent workflow (LangGraph — an agent is an LLM that decides *which tool to call next* in
> a loop, rather than answering in one shot); **eval** measures whether answers are actually good.

---

### 3. Created `.venv` inside `repo-reviewer\`, installed `requirements.txt`

**What:** Fresh virtualenv (Python 3.13.7) at `repo-reviewer\.venv\`, then
`pip install -r requirements.txt` (litellm, pydantic-settings, sentence-transformers, faiss-cpu,
PyGithub, langgraph, pytest, ruff, …).

**Why:** The old venv lived at the parent directory level — orphaned once we settled on
`repo-reviewer\` as the only working dir. A venv should live inside the project it serves.

**Logic:** Verified immediately: `pytest -q` → 3 passed, `ruff check .` → clean.

> 💡 **Concept — the guardrail tests.** The 3 tests protect the *agentic* part of this project:
> an agent that can post to GitHub is an LLM with write access to the real world. The tests assert
> (a) a repo **allowlist** — the client refuses any repo not explicitly permitted, (b) **dry-run
> mode** — actions print instead of executing until Phase 5, (c) the client class physically has no
> `approve`/`merge`/`push` methods. This is defense-in-depth for agent safety: don't just prompt
> the model to behave — make dangerous actions structurally impossible.

---

### 4. Moved API keys from `.env.example` into `.env`; restored `.env.example`

**What:** The real credentials had been pasted into `.env.example` (the *tracked* template file)
instead of `.env` (the *gitignored* secrets file). Values were moved into `.env` programmatically
(never printed to screen), then `.env.example` was restored to placeholders with
`git checkout -- .env.example`.

**Why:** `.env.example` is committed to git and pushed to GitHub. Real keys in it would have been
published by the next `git add . && git push`. `.env` is listed in `.gitignore`, so keys there
never leave the machine. We verified git history was clean — no commit ever contained a real key.

**Logic:** The whole point of the `.env` / `.env.example` split: the example documents *which*
variables exist (safe to publish), the real file holds *values* (never published). Also reverted
a `.env.example` line someone added to `.gitignore` — ignoring an already-tracked file does
nothing; git keeps tracking it, which gives false comfort.

**Gotcha discovered:** with `KEY=` empty followed by an inline `# comment`, pydantic-settings
reads the *comment text as the value*. That made earlier diagnostics claim keys were "filled"
when they held `# https://...`. Once a real value is present before the comment, parsing is correct.

---

### 5. Replaced all four model IDs in `config.py`

**What:**

| Lane | Old (dead) | New (verified live) |
|---|---|---|
| quality | `gemini/gemini-2.5-flash` | `gemini/gemini-3.6-flash` |
| fast | `groq/llama-3.3-70b-versatile` | `groq/openai/gpt-oss-120b` |
| fallback 1 | `openrouter/deepseek/deepseek-chat-v3-0324:free` | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` |
| fallback 2 | `cerebras/llama-3.3-70b` | `groq/openai/gpt-oss-20b` |

**Why:** After fixing the keys, every provider still failed — but the error changed from
`AuthenticationError` to `NotFoundError`. That distinction was the diagnostic: auth now worked,
the *model names* were stale. Google's API literally replied *"gemini-2.5-flash is no longer
available to new users, use gemini-3.6-flash"*; Groq had decommissioned all Llama chat models;
the DeepSeek free route vanished from OpenRouter's list.

**Logic:** Each provider's live model list was queried via API, then every candidate was tested
with a real completion call before being chosen. Rejected candidates, with reasons:

- `groq/qwen/qwen3.6-27b` — leaks raw `<think>…</think>` reasoning into its answer text.
- `gemini/gemini-flash-latest` — a floating alias that took **75s** vs 1.78s for the pinned ID.
  Pin exact model versions; aliases change under you.
- Cerebras dropped entirely — its key is empty (optional), so it only added a noisy
  `Missing credentials` error at the end of every fallback chain. Re-add when a key exists.

> 💡 **Concept — model router with lanes and fallbacks.** `llm/router.py` implements a common
> production pattern: a **"quality" lane** (slower, smarter model for careful work like PR review)
> and a **"fast" lane** (cheap, low-latency model for the many small calls an agent loop makes —
> planning, classification, tool selection). If a lane's primary model fails, the router walks a
> **fallback chain** so one provider outage doesn't take the system down. `litellm` makes this
> possible by giving every provider (Google, Groq, OpenRouter, …) one uniform calling interface.

> 💡 **Concept — model deprecation.** Hosted LLMs retire on a timescale of months. Any config that
> hard-codes model IDs will silently rot; the errors it produces (`NotFound`, `BadRequest`) look
> like auth bugs. Diagnostic rule of thumb: `AuthenticationError` = key problem,
> `NotFoundError` = model-name problem.

> 💡 **Concept — reasoning tokens vs `max_tokens`.** Modern "reasoning" models think in hidden
> tokens *before* writing the visible answer, and that thinking bills against `max_tokens`. At
> `max_tokens=10`, every candidate model spent the whole budget thinking and returned
> `content=None` — while reporting 80+ completion tokens used. Practical rules: give reasoning
> models a real budget (hundreds of tokens even for one-word answers), and never assume
> `response.content` is non-None. `scripts/smoke_llm.py` still calls with `max_tokens=10`;
> a guard in `router.chat` is a known TODO.

---

### 6. Raised `max_tokens` 10 → 300 in `scripts/smoke_llm.py`

**What:** The single completion call in the smoke script now passes `max_tokens=300` instead of 10.

**Why:** The first full smoke run *connected* to both lanes but printed empty answers (`''`) —
`gpt-oss-120b` reported 10 completion tokens used yet produced no visible text. That is the
reasoning-token issue from entry 5 in action: the entire 10-token budget went to hidden thinking,
leaving zero for the answer. A smoke test that "passes" while proving nothing about text output
is worse than a failing one.

**Logic:** 300 tokens is cheap (fractions of a cent / free tier) and leaves headroom for any
reasoning model's thinking phase. After the fix, both lanes returned the expected `'pong'`.

### 7. Phase 0 milestone: PASSED ✅

Final `python -m scripts.smoke_llm` results (2026-08-22):

| Check | Result |
|---|---|
| fast lane | `groq/openai/gpt-oss-120b` → `'pong'`, 0.56s, no fallback |
| quality lane | `gemini/gemini-3.6-flash` → `'pong'`, 2.36s, no fallback |
| embeddings | `bge-m3` local, dim=1024, cosine(code, question)=**0.635**, 8.6s cached (388s incl. first download) |
| guard tests | `pytest -q` → 3 passed |
| lint | `ruff check .` → clean |

The 0.635 cosine score means the embedding model rates the code snippet and the natural-language
question as clearly related (unrelated text pairs typically score near 0.2–0.4 with normalized
embeddings) — retrieval will be able to match questions to code in Phase 1.

**Still uncommitted:** scaffold dirs, `scripts/__init__.py`, `config.py` model changes,
`smoke_llm.py` token fix, this changelog. Awaiting go-ahead to commit.

**Known TODO carried forward:** `router.chat` has no guard for `content=None` from a reasoning
model that exhausts its budget — any Phase 1 code assuming non-empty text will hit this edge.

> 💡 **Concept — embeddings (the smoke test's second half).** An embedding model (here `bge-m3`,
> running locally) converts text into a vector of numbers such that *similar meanings land close
> together*. The smoke test embeds a code line (`def process_refund(...)`) and a natural-language
> question ("how are refunds handled?") and checks their **cosine similarity** — a score near 1
> means the model can match questions to relevant code. That matching is the heart of the RAG
> retrieval step Phase 1 will build with FAISS (a fast vector-search index).
