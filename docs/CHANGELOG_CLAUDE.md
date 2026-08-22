# Change Log — Claude Code Sessions

Every change Claude makes to this codebase is logged here: **what** changed, **why**, and the
reasoning behind it. Concept boxes explain the ideas each change touches, layered by level:

- 💡 **(CS)** — computer-science fundamentals: hashing, parsing, indexes, complexity
- 💡 **(SE)** — software-engineering practice: testing, config, dependency injection
- 💡 **(ML)** — machine-learning: embeddings, transformers, vector math
- 💡 **(RAG/Agents)** — retrieval-augmented generation and agentic-AI patterns

Newest entries at the bottom, grouped by session date.

---

## 2026-08-22 — Session 1 (Phase 0): Cleanup, environment repair, model migration

### 1. Deleted orphaned folders at `C:\Agentic-RAG\` root

**What:** Removed `repo-reviewer-phase0\`, and the top-level `.venv\`, `.pytest_cache\`, `.ruff_cache\`.

**Why:** `repo-reviewer-phase0` was a leftover unzipped copy. Before deleting, we verified it held
nothing unique: its nested repo sat at the *same* commit (`01b822a`) as the real clone with a clean
working tree, its outer `.git` tracked only a gitlink (a pointer, not file content), and its `.env`
differed from the clone's only in line endings — proven by hashing every value in both files and
comparing the hashes, never the values themselves. The three cache folders belonged to a venv
created in the wrong directory.

**Logic:** Never delete before proving nothing unique is lost — compare commits, working-tree
status, and file hashes first. One working directory removes "which copy am I editing?" ambiguity.

> 💡 **(CS) Cryptographic hash functions.** A hash function maps any input to a fixed-size
> fingerprint. A *cryptographic* one (SHA-256) adds two properties: tiny input changes produce
> completely different outputs (avalanche effect), and finding two inputs with the same output is
> computationally infeasible (collision resistance). That makes hashes usable as *identity*:
>
> ```python
> import hashlib
> hashlib.sha256(b"def f(): return 1").hexdigest()[:16]   # 'cd802a81ac4cf7ae'
> hashlib.sha256(b"def f(): return 2").hexdigest()[:16]   # '994d3d2b1b81658b'  ← 1 char changed, everything changed
> ```
>
> We used this twice today: comparing `.env` values without revealing them (equal hashes ⇒ equal
> values), and later as the backbone of incremental indexing (entry 8). Git itself is built on
> this idea — every commit ID is a hash of the repository's entire content state, which is why
> two repos at commit `01b822a` are *provably* byte-identical in their tracked files.

> 💡 **(CS) Content-addressable storage & gitlinks.** Git stores objects under their own hash
> ("content addressing"): identical content is stored once, and naming a hash names exact content.
> A *gitlink* is a special entry where a repo records only "a sub-repository exists here at commit
> X" — a 40-character pointer, no files. That's why the outer `phase0` repo could be deleted
> safely: its one commit contained a pointer, not code.

---

### 2. Created scaffold directories with `.gitkeep`

**What:** Added empty dirs `indexer/ qa/ review/ graph/ api/ eval/ docker/`, each containing an
empty `.gitkeep` file.

**Why:** Homes for later build phases. Git tracks *files*, never directories — a directory exists
in git only as a path prefix on some file — so the convention is a zero-byte `.gitkeep` placeholder.

> 💡 **(RAG/Agents) What RAG is, from first principles.** An LLM's knowledge lives in its
> weights — "parametric" knowledge, frozen at training time, fuzzy, and it has never seen your
> repo. But an LLM is also an excellent *reader* of whatever you place in its context window.
> RAG (Retrieval-Augmented Generation) exploits that: **store your documents outside the model,
> retrieve the few most relevant pieces per question, and paste them into the prompt.**
>
> ```
> question ──▶ retriever ──▶ top-k relevant chunks ──▶ LLM(context = chunks + question) ──▶ cited answer
> ```
>
> Why not paste the whole repo? Cost (you pay per token), latency, context limits, and — most
> underrated — *focus*: models answer measurably better from 5 relevant chunks than from 500
> pages containing the answer somewhere. The retriever's quality therefore bounds the whole
> system's quality, which is why Phase 1 spends 1.5 weeks on it.
>
> The scaffold mirrors the pipeline: **indexer** (make code searchable) → **qa** (answer with
> retrieval) → **review** (apply it to PR diffs) → **graph** (orchestrate agents) → **eval**
> (measure quality) → **api/docker** (serve it).

> 💡 **(RAG/Agents) What makes an AI system "agentic".** A plain LLM call is one-shot:
> prompt in, text out. An *agent* is an LLM in a loop with tools: it sees a goal, *decides which
> tool to call* (search the code, read a file, look up a symbol), observes the result, and
> decides again — until it can answer. The model's output alternates between "thoughts" and
> structured tool calls; the surrounding program executes the calls and feeds results back.
> Phases 2–4 build exactly this loop with LangGraph.

---

### 3. Created `.venv` inside `repo-reviewer\`, installed `requirements.txt`

**What:** Fresh virtualenv (Python 3.13.7) at `repo-reviewer\.venv\`, then
`pip install -r requirements.txt`. Verified: `pytest -q` → 3 passed, `ruff check .` → clean.

**Why:** The old venv lived at the parent-directory level — orphaned once `repo-reviewer\` became
the only working dir. A venv belongs inside the project it serves.

> 💡 **(SE) Virtual environments.** Python resolves `import faiss` by searching a list of
> directories (`sys.path`). A venv is a private copy of that search space: its own `python.exe`
> and `site-packages`, so *this* project's `faiss 1.15` can't collide with another project's
> `faiss 1.7`. The `requirements.txt` makes the environment reproducible: any machine can rebuild
> it with one command. (Docker, in Phase 6, is the same idea extended to the whole OS.)

> 💡 **(RAG/Agents) Guardrails: safety by construction, not by prompt.** The 3 tests protect the
> agentic part of the project. An agent that can post to GitHub is an LLM with write access to
> the real world — and LLMs can be manipulated (see prompt injection, Phase 5). So the safety
> rules live in *code*, where no clever prompt can reach them:
>
> ```python
> # gh/client.py — the pattern
> if full_name not in settings.allowed_repos:          # 1. allowlist: refuse unknown repos
>     raise PermissionError(...)
> if settings.dry_run:                                 # 2. dry-run: log instead of act
>     log.info("DRY RUN: would post ..."); return
> # 3. and there simply is no approve()/merge()/push() method to call
> ```
>
> Layer 3 is the strongest form: a capability the code doesn't have is a capability no
> jailbreak can invoke. This "defense in depth" — allowlist + dry-run + structural absence —
> is the single most portfolio-worthy safety idea in the project.

> 💡 **(SE) Testing with mocks.** The guard tests never touch the real GitHub API — they inject
> fake objects and assert on *behavior* ("a disallowed repo raises `PermissionError`"). Mocking
> keeps tests fast, free, and deterministic, and it's the same trick that later lets the indexer
> tests run without a 2GB embedding model (entry 8, dependency injection).

---

### 4. Moved API keys from `.env.example` into `.env`; restored `.env.example`

**What:** Real credentials had been pasted into `.env.example` (the *tracked* template) instead of
`.env` (the *gitignored* secrets file). Values were moved programmatically (never printed), then
`git checkout -- .env.example` restored the template. Verified no commit in history ever carried
a real key.

**Why:** `.env.example` is committed and pushed — real keys in it would have been published by the
next `git push`. `.env` never leaves the machine.

**Logic:** The `.env` / `.env.example` split: the example documents *which* variables exist (safe
to publish), the real file holds *values* (never published). Also reverted a `.env.example` line
added to `.gitignore` — ignoring an already-tracked file does nothing (git keeps tracking it),
which is worse than nothing: it *looks* protected.

> 💡 **(SE) Secrets management & 12-factor config.** Configuration that varies by deployment
> (keys, endpoints, flags) belongs in the *environment*, not in code — so the same code runs in
> dev/CI/prod with different credentials, and secrets stay out of version control. The escalation
> path as stakes rise: `.env` file → CI secret stores → vault services with rotation. A leaked
> key on GitHub is found by scanner bots in *minutes* — treat any pushed secret as compromised
> and rotate it immediately.

> 💡 **(SE) Parsers are exact; a gotcha found today.** With `KEY=` empty followed by an inline
> `# comment`, pydantic-settings reads **the comment text as the value**:
>
> ```
> GEMINI_API_KEY=            # https://aistudio.google.com
>                ↑ value becomes "# https://aistudio.google.com"  (non-empty!)
> ```
>
> Every "is the key set?" check answered *yes* while every API call failed. Lesson: when
> debugging config, inspect what the parser *parsed*, not what the file *looks like*.

---

### 5. Replaced all four model IDs in `config.py`

**What:**

| Lane | Old (dead) | New (verified live) |
|---|---|---|
| quality | `gemini/gemini-2.5-flash` | `gemini/gemini-3.6-flash` |
| fast | `groq/llama-3.3-70b-versatile` | `groq/openai/gpt-oss-120b` |
| fallback 1 | `openrouter/deepseek/deepseek-chat-v3-0324:free` | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` |
| fallback 2 | `cerebras/llama-3.3-70b` | `groq/openai/gpt-oss-20b` |

**Why:** After fixing the keys, every provider still failed — but the error *changed* from
`AuthenticationError` to `NotFoundError`. That was the diagnostic: auth now worked; the model
names were stale. Google's API literally replied *"gemini-2.5-flash is no longer available to
new users, use gemini-3.6-flash"*; Groq had decommissioned all Llama chat models.

**Logic:** Each provider's live model list was fetched via API; every candidate was tested with a
real completion before selection. Rejected: `groq/qwen/qwen3.6-27b` (leaks raw `<think>` blocks
into its answer), `gemini/gemini-flash-latest` (a floating alias, 75s vs 1.78s for the pinned
ID — pin exact versions), Cerebras (no key set; only added noise to every fallback chain).

> 💡 **(ML) What "calling an LLM" actually is.** An LLM API call sends a list of messages; the
> provider runs them through a transformer that repeatedly predicts a probability distribution
> over its vocabulary and samples the next token — *autoregressive generation*, one token at a
> time, each conditioned on everything before it. You pay per token in and out; latency scales
> with output length. Everything else (SDKs, "chat", tool calls) is packaging around this loop.

> 💡 **(RAG/Agents) The router pattern: lanes + fallback chains.** `llm/router.py` implements
> two production ideas. **Lanes** match model to workload: a *quality* lane (careful, long-context
> reasoning — PR review) and a *fast* lane (an agent loop makes many small calls — planning,
> classification — where latency × call-count dominates). **Fallback chains** handle the fact
> that providers fail (outages, rate limits, deprecations): on error, try the next model in the
> list, so one provider's bad day doesn't take the system down. `litellm` makes this practical
> by giving every provider one uniform interface:
>
> ```python
> for model in [primary, *fallbacks]:
>     try:
>         return litellm.completion(model=model, messages=msgs)
>     except Exception as e:
>         last_err = e            # log and try the next lane member
> raise RuntimeError(f"all providers failed: {last_err}")
> ```

> 💡 **(RAG/Agents) Model deprecation — an operational reality.** Hosted models retire on a
> timescale of *months*. Hard-coded model IDs silently rot, and the resulting errors masquerade
> as auth bugs. Diagnostic rule of thumb: `AuthenticationError` ⇒ key problem; `NotFoundError` ⇒
> model-name problem; `RateLimitError` ⇒ quota. Production systems monitor deprecation notices
> and keep model IDs in config — exactly why ours live in `config.py`, not scattered in code.

---

### 6. Raised `max_tokens` 10 → 300 in `scripts/smoke_llm.py`

**What:** The smoke test's completion call now passes `max_tokens=300`.

**Why:** The first full run *connected* to both lanes but printed empty answers — `gpt-oss-120b`
reported 10 completion tokens used, yet produced no visible text. The entire budget went to
hidden reasoning. A smoke test that "passes" while proving nothing is worse than a failing one.

> 💡 **(ML) Tokens and tokenization (BPE).** Models don't read characters or words — they read
> *tokens*, subword units from a fixed vocabulary learned by byte-pair encoding (roughly:
> repeatedly merge the most frequent adjacent pairs). "process_refund" might be
> `process`+`_`+`ref`+`und`. Everything is priced and limited in tokens: context windows,
> `max_tokens`, API bills. Rule of thumb: ~4 characters ≈ 1 token in English; code tokenizes
> less efficiently.

> 💡 **(ML) Reasoning tokens vs `max_tokens`.** "Reasoning" models generate hidden
> chain-of-thought tokens *before* the visible answer — and those bill against `max_tokens`:
>
> ```
> max_tokens=10:   [10 thinking tokens][budget exhausted]        → content=None
> max_tokens=300:  [~50 thinking tokens]["pong"]                 → content="pong"
> ```
>
> Two practical rules: give reasoning models a real budget even for one-word answers, and never
> assume `response.content` is non-None. (A guard for this in `router.chat` is a standing TODO.)

---

### 7. Phase 0 milestone: PASSED ✅

| Check | Result |
|---|---|
| fast lane | `groq/openai/gpt-oss-120b` → `'pong'`, 0.56s, no fallback |
| quality lane | `gemini/gemini-3.6-flash` → `'pong'`, 2.36s, no fallback |
| embeddings | `bge-m3` local, dim=1024, cosine(code, question)=**0.635**, 8.6s cached |
| guard tests | `pytest -q` → 3 passed |
| lint | `ruff check .` → clean |

> 💡 **(ML) Embeddings — meaning as geometry.** An embedding model maps text to a vector (here:
> 1024 numbers) such that *similar meanings land near each other*. Similarity is measured by the
> angle between vectors — **cosine similarity**:
>
> ```python
> import numpy as np
> # cos(θ) = (a · b) / (|a| |b|);  for unit-length vectors this is just the dot product
> a = model.encode("def process_refund(order_id):", normalize_embeddings=True)
> b = model.encode("how are refunds handled?",      normalize_embeddings=True)
> float(a @ b)          # 0.635 — same topic, different wording
> ```
>
> 0.635 between *code* and a *natural-language question* is the number that makes RAG-over-code
> work: questions and their answering code land close together in vector space, so nearest-
> neighbor search can connect them. Unrelated pairs typically score ~0.2–0.4 on this model.
>
> How does a model learn this? **Contrastive training**: show it millions of (query, relevant
> passage) pairs and adjust weights so matching pairs score high and mismatched pairs low.
> `bge-m3` is such a model, small enough to run locally — which means embedding costs nothing
> and hits no rate limits, the property that makes per-PR re-indexing (Phase 4) free.

---

## 2026-08-22 — Session 2 (Phase 1): AST-aware indexing (core build)

### 8. Built the `indexer/` package

**What:** Six new modules + 20 new tests (23 total passing) + `docs/chunking.md`:

- `chunker.py` — tree-sitter AST chunking for Python/JS; markdown by heading; config kept whole
- `symbols.py` — symbol table: `{name → definition locations + reference locations}`
- `embedder.py` — lazy bge-m3 wrapper (nothing loads the 2GB model until vectors are needed)
- `store.py` — FAISS (dense) + BM25 (sparse) hybrid index, RRF fusion, incremental updates
- `pipeline.py` — repo walker tying it together; `__main__.py` — CLI (`index` / `search` / `symbol`)

**Milestone results (indexed repo-reviewer itself):** 20 files → 132 chunks, 91 symbols.
Full index ~6 min (CPU embeddings); no-op re-index ~13 s with **zero** re-embeds.
Exact-identifier query `post_review_summary` → the exact method (`gh/client.py:59-64`) at rank 1.
Natural-language "how does fallback between models work?" → `llm/router.py` + its `chat` method.
`symbol chat` → definition `llm/router.py:85`, reference `scripts/smoke_llm.py:14`.

The design decisions, bottom-up:

#### 8a. Parsing: from characters to structure

> 💡 **(CS) Parsers and ASTs.** Source code is stored as flat text, but it *means* a tree: a
> module contains classes, classes contain methods, methods contain statements. A **parser**
> rebuilds that tree — the **Abstract Syntax Tree** — from text, using the language's grammar.
> This is the front half of every compiler. `tree-sitter` gives us fast, error-tolerant parsers
> for ~any language behind one API:
>
> ```python
> from tree_sitter import Language, Parser
> import tree_sitter_python
>
> tree = Parser(Language(tree_sitter_python.language())).parse(b"""
> class PaymentService:
>     def process_refund(self, order_id):
>         return {"ok": True}
> """)
> # module
> # └── class_definition  name="PaymentService"          ← node.start_point = (row, col)
> #     └── block
> #         └── function_definition  name="process_refund"
> ```
>
> Every node knows its exact byte/line range — which is what makes precise citations
> (`payments.py:142-198`) possible for free.

#### 8b. Chunking: AST boundaries, not byte counts

Naive RAG chunking ("every 500 characters, 50 overlap") is hostile to code: it splits functions
mid-body, separates signatures from docstrings, and orphans methods from their class names. Full
write-up in [`docs/chunking.md`](chunking.md). Our rules:

| Construct | Chunk |
|---|---|
| function / method | one chunk each; methods carry `parent_symbol` = class name |
| class | one **skeleton** chunk: signatures + class-level code, method bodies elided to `...` |
| module-level code | contiguous runs of imports/constants → `(module)` chunks |
| >120-line function | split at *statement* boundaries, signature repeated in every part |
| markdown / config | by heading / kept whole |

**Nothing is lost** — a test asserts every non-blank source line appears in ≥1 chunk.

> 💡 **(RAG/Agents) The context-header trick.** Each chunk is embedded with a location prefix:
>
> ```
> # repo-reviewer/gh/client.py :: GitHubClient :: post_review_summary (lines 59-64)
> def post_review_summary(self, full_name, number, body):
>     ...
> ```
>
> Without it, `def save(self)` from two different classes embeds almost identically. With it,
> the vector encodes *where the code lives* — and the citation every answer needs travels
> attached to the chunk. Cheap, and one of the highest-leverage tricks in RAG-over-code.

> 💡 **(RAG/Agents) The class-skeleton chunk.** The class chunk keeps every signature and
> class-level statement but elides method bodies:
>
> ```python
> class PaymentService:
>     """Handles refunds."""
>     retries = 3
>     def process_refund(self, order_id):
>         ...
>     def audit(self):
>         ...
> ```
>
> It reads as an API summary — ideal for "what does PaymentService do?" — while each method's
> full body lives in its own chunk for detail questions. Two retrieval granularities, no
> duplicated bodies, nothing lost.

#### 8c. Dense retrieval: vectors + FAISS

> 💡 **(CS) Nearest-neighbor search and its cost.** "Find the 5 most similar chunks" =
> **k-nearest-neighbor search** in 1024-dimensional space. Brute force compares the query
> against every vector: O(n·d) per query — for 132 chunks × 1024 dims, microseconds; FAISS's
> `IndexFlatIP` does exactly this (`IP` = inner product; our vectors are unit-normalized, so
> inner product *is* cosine similarity). At millions of vectors you'd switch to **approximate**
> indexes (HNSW graphs, IVF clustering) that trade a little recall for orders-of-magnitude
> speed. Knowing when brute force is fine — here — is itself an engineering decision.

#### 8d. Sparse retrieval: BM25, and why code needs it

> 💡 **(CS) Inverted indexes.** Keyword search doesn't scan documents; it inverts them:
> `{term → list of documents containing it}` — the same structure as a book index. Lookup
> becomes O(1) per term. This 60-year-old idea still powers every search engine.

> 💡 **(CS→ML) BM25 in one paragraph.** BM25 ranks documents by summing, per query term:
> *term frequency* (more occurrences → more relevant, with diminishing returns — the `k1`
> saturation), × *inverse document frequency* (rare terms carry more signal than common ones),
> × a length normalization (long documents don't win just by being long). It's the strongest
> "classic" ranking function — and on *exact identifiers*, it beats embeddings outright:
> embeddings blur `process_refund` toward "refund-related things", while BM25 matches it
> *exactly*.
>
> Our tokenizer is code-aware — each identifier is indexed as itself **and** its sub-words:
>
> ```python
> tokenize("PaymentService.process_refund")
> # ['paymentservice', 'payment', 'service', 'process_refund', 'process', 'refund']
> #   exact ↑ (for identifier queries)      split ↑ (for natural-language queries)
> ```

#### 8e. Fusion: combining two retrievers that don't speak the same language

> 💡 **(RAG/Agents) Reciprocal Rank Fusion.** BM25 scores (unbounded, e.g. 14.2) and cosine
> similarities (−1…1) are incomparable — you can't average them. RRF ignores scores entirely
> and uses only each system's *ranking*: `score(d) = Σ 1/(60 + rank_i(d))`. Worked example:
>
> ```
> chunk          dense rank   bm25 rank   RRF = 1/(60+r₁) + 1/(60+r₂)
> process_refund     1            1        1/61 + 1/61 = 0.0328   ← both agree: wins
> charge             2            —        1/62        = 0.0161
> verify_token       —            2        1/62        = 0.0161
> ```
>
> Chunks that *both* retrievers like float to the top; a chunk only one likes still surfaces.
> No calibration, no tuned weights, empirically hard to beat — the standard baseline for
> hybrid retrieval. (The constant 60 damps the gap between rank 1 and rank 2 so one system
> can't dominate.)

#### 8f. Incremental indexing: hashing again

> 💡 **(CS→RAG) Change detection via content hashing.** The index stores a manifest
> `{file path → sha256(content)}`. On re-index, hash every current file and compare:
>
> ```
> hash unchanged → keep existing chunks + vectors     (free)
> hash changed   → re-chunk + re-embed just that file (cheap)
> path missing   → drop its chunks                    (free)
> ```
>
> Measured today: full index ~6 min, no-op re-index 13 s, one-file change re-embeds only that
> file. Same idea git uses to detect modified files. This is what makes webhook-triggered
> per-PR re-indexing (Phase 4) feel instant instead of taking minutes per push.

#### 8g. The symbol table: identity, not similarity

> 💡 **(CS→RAG) Symbol tables — a compilers idea reused for agents.** Compilers track
> `{name → where it's declared, where it's used}` to resolve references. We build the same
> structure while walking the ASTs, and expose it as a *tool*:
>
> ```python
> symbols.lookup("chat")
> # {"definitions": [{"path": "llm/router.py", "line": 85}],
> #  "references":  [{"path": "scripts/smoke_llm.py", "line": 14}]}
> ```
>
> "Where is X defined?" is an **identity** question. Vector search answers "what is *similar*
> to X?" — the wrong instrument, and it can never be sure it found *the* definition. Giving
> the future Q&A agent both tools — fuzzy semantic search *and* exact symbol lookup — is what
> enables multi-hop navigation: search → find symbol → jump to definition → read callers.

#### 8h. The engineering choice that shaped everything: dependency injection

> 💡 **(SE) Inject dependencies; don't import them.** Every component takes `embed_fn` as an
> argument rather than importing the model:
>
> ```python
> index = HybridIndex(data_dir, embed_fn=get_embedder())   # production: real bge-m3
> index = HybridIndex(data_dir, embed_fn=FakeEmbedder())   # tests: deterministic, instant
> ```
>
> One parameter is the difference between a test suite that needs a 2GB download and GPU-minutes
> per run, and one that finishes in **1.2 seconds**. The fake embedder hashes tokens into vector
> buckets — crude, but "similar text → similar vector" holds well enough to test ranking logic.
> Same principle as the mocked GitHub client in the guard tests (entry 3): inject what's slow,
> expensive, or external.

**Remaining Phase 1 work (next step):** the 25-question eval set with hit-rate@5 comparison —
naive vs AST vs AST+hybrid — over the seeded target repos. That table is the phase's headline
artifact.

> 💡 **(RAG/Agents) Why evaluate retrieval at all.** Every design choice above (AST boundaries,
> headers, hybrid, skeletons) is a *hypothesis* about what improves retrieval. **Hit-rate@k**
> ("for what fraction of questions does the correct file:line region appear in the top k
> results?") turns hypotheses into numbers on a fixed question set. Without it you're tuning by
> vibes; with it, every future change (a different embedding model, a new chunk size) gets a
> before/after score. Building the measuring stick *before* optimizing is the discipline that
> separates engineering from alchemy.

---

### 9. Enabled embedding progress bars for large batches

**What:** `indexer/embedder.py` now passes `show_progress_bar=len(texts) > 8` to
`model.encode()` — indexing runs show a live per-batch tqdm bar; single-query embeds
during search stay silent.

**Why:** A full index run is minutes of silent CPU work with no feedback until the vectors
file lands. sentence-transformers batches internally (32 texts per batch) and can report
each batch; the size threshold keeps search queries from printing a bar per call.

> 💡 **(SE) Observability of long-running work.** Batch jobs should emit progress
> proportional to their duration. Two subtleties met here: Python buffers *stdout* when
> piped (run `python -u` to stream prints live), while tqdm writes to *stderr*, which is
> unbuffered — so the bar streams even when prints don't. And when a process is silent by
> design, the filesystem is the fallback progress API: watching for `vectors.npy` to appear
> tells you which stage completed.

---

### 10. Retrieval eval harness — and the negative result that redesigned search

**What:** Built `eval/` (naive baseline chunker, 35-question ground-truth set, 4-config
harness), which immediately produced a *negative* result, whose diagnosis led to
query-routed hybrid search in `store.py`. Final numbers in `docs/retrieval_eval.md`.

**The sequence, because the order is the lesson:**

1. **Target repos too small.** The seeded eval targets (`reviewer-target-py`, 183 lines;
   `reviewer-target-mixed`, 72) would give every retriever ~100% — meaningless comparison.
   Evaluated against repo-reviewer itself (21 files, 141 AST / 79 naive chunks) instead;
   the harness is repo-agnostic for when real targets exist.
2. **First run:** AST beat naive (92% vs 84% hit@5, dense) — but **hybrid scored 68%,
   worse than dense alone.** The expected headline ("AST+hybrid wins") was simply false
   on this corpus.
3. **Diagnosis** (dumped the 7 lost questions): BM25 kept top-ranking *documentation* —
   `CHANGELOG_CLAUDE.md` and `chunking.md` describe the code in the exact words the
   questions use, lexically shadowing the code itself. Compounding it, fusion depth 50
   (of 140 chunks!) let BM25's weak tail displace dense's precise hits.
4. **Measured fixes:** depth 50→10 (68→72%), BM25 weight 0.5 (→84%) — still under
   dense's 92%. On all-prose questions, *any* BM25 vote hurts.
5. **The eval set itself was biased:** all 25 questions were natural-language; zero
   exact-identifier queries — the query class BM25 exists for and Phase 2's agent tools
   will issue constantly. Added 10 identifier queries as a labeled category.
6. **Per-category results:** identifiers with equal-weight fusion → 100% hit@5 (dense
   alone: 80%). Prose with any fusion → worse. Two query populations, opposite optima.
7. **Resolution — query routing** in `search()`: identifier-shaped queries (one token,
   no spaces) get equal-weight RRF fusion at depth 10; prose is served dense-only.
   2 new routing tests; 25 total passing.

**Final table (hit@5): naive+dense 80% → AST + routed hybrid 94%.** Identifiers 100%,
prose 92%, no configuration sacrificed. Also fixed mid-eval: ground-truth line regions
pointing into `store.py` had drifted after editing it — re-verified before publishing.

> 💡 **(RAG/Agents) Negative results are the eval paying rent.** The whole point of
> building the measuring stick before optimizing: "hybrid is better" was a *hypothesis*,
> and on this corpus it was false. Without the harness we'd have shipped a retriever 24
> points worse at prose questions and called it an upgrade. When a benchmark contradicts
> the textbook, the benchmark is doing its job — diagnose, don't discard.

> 💡 **(RAG/Agents) Lexical shadowing — docs vs code.** Any documented repo contains
> prose *describing* its code in the same vocabulary users ask questions in. Lexical
> search (BM25) will rank the description above the implementation; dense embeddings
> with context headers separate them far better. This failure mode grows with
> documentation quality — ironically, this project's teaching changelog made its own
> retrieval benchmark harder.

> 💡 **(RAG/Agents) Query routing.** Retrieval traffic is not one population: prose
> questions and identifier lookups have *opposite* optimal retrievers. Rather than one
> compromise pipeline, classify the query (here: a regex for identifier shape — zero
> cost, no LLM call) and dispatch to the right one. Same principle as the LLM router's
> fast/quality lanes in entry 5: match the machinery to the request.

> 💡 **(RAG/Agents) Evals are code too — they rot.** Ground-truth answer regions pinned
> to line numbers silently drifted when the file they pointed into was edited. Caught
> because hit-rates shifted without a retrieval change. Mitigations: re-verify after
> editing indexed files, or anchor answers to symbols resolved at eval time. An eval
> that can silently go stale will — treat its fixtures with the same suspicion as code.

> 💡 **(SE) Don't tune on your test set — at least know when you are.** Fusion depth and
> weights were chosen by measuring on the same 35 questions we report. At this scale
> that's unavoidable, but it's *overfitting risk*: the honest guards used here were
> preferring standard values (depth 10, equal weight) over squeezing maxima, categorical
> decisions (route/don't route) over fine-grained knobs, and disclosing the practice.
> The proper fix at larger scale is a held-out split: tune on one half, report the other.

---

## 2026-08-22 — Session 3 (Phase 2): Codebase Q&A agent

### 11. Built the `qa/` package — a from-scratch ReAct agent with validated citations

**What:** Tool-calling support in `llm/router.py`; four read-only tools (`qa/tools.py`);
the agent loop (`qa/agent.py`); CLI (`qa/__main__.py`); 7 new tests (32 passing);
`docs/qa_agent.md` with a captured multi-hop trace.

**Why from-scratch instead of LangGraph:** the plan allows either. Building the loop by
hand teaches the mechanics (the point of this project), reuses our router's lanes/
fallbacks/stats, and adds zero dependencies. LangGraph arrives in Phase 4 where the
parallel-reviewer pipeline genuinely needs orchestration.

**Milestone (live run):** the multi-hop question "if Groq goes down, what takes over,
in what order, where configured?" was answered correctly in 6 steps / 5 tool calls
(search → lookup_symbol → two read_files), all 6 citations validated. Poetically, Groq
was actually rate-limiting during the run — 3 of 6 calls served by the OpenRouter
fallback — so the agent described the fallback chain *while running on it*. The trace
also shows step 1 passing a bad argument, receiving the error string, and self-
correcting on step 2.

**Also fixed:** Windows consoles are cp1252; the model emitted a Unicode hyphen and
`print()` crashed the CLI *after* a successful run. `sys.stdout.reconfigure(
encoding="utf-8", errors="replace")` at CLI startup — model output is untrusted in
encoding, not just content.

> 💡 **(RAG/Agents) The ReAct loop — what "agent" actually means in code.** The model
> never executes anything. Each turn it either emits structured tool calls or a final
> answer. Our program runs the calls and appends results as `role="tool"` messages, so
> the conversation *is* the agent's working memory:
>
> ```
> while steps < max_steps:
>     resp = llm(messages, tools=schemas)
>     if not resp.tool_calls: return validate(resp.text)
>     messages += [assistant_turn(resp), *[tool_result(tc) for tc in resp.tool_calls]]
> ```
>
> Multi-hop reasoning (search → read → follow reference → answer) emerges from this
> loop without being programmed anywhere. The step cap matters: an agent that can loop
> is an agent that can loop forever, so on exhaustion we force an answer from gathered
> evidence rather than dying silently.

> 💡 **(RAG/Agents) Tool schemas = function calling.** Tools are advertised to the model
> as JSON Schemas (name, description, typed parameters). The provider constrains
> generation so tool-call output parses as valid JSON against the schema. The
> *descriptions* are prompt engineering — "use lookup_symbol for where-is-X questions"
> steers tool choice more than any code. Errors are returned as strings, not raised:
> a tool error the model can read is a tool error the model can recover from — observed
> live in the milestone trace (bad arg on step 1, corrected on step 2).

> 💡 **(RAG/Agents) Citation validation as a hallucination guard.** LLMs fabricate
> plausible-looking citations. Every `path:start-end` in a final answer is checked
> against the working tree — file exists, range within it. Invalid ones are *flagged,
> never dropped*: a fabricated citation marks the claim it supports as suspect, which
> is information the human needs. This converts "trust me" into "check line 41".

> 💡 **(RAG/Agents) Agent tool safety.** `read_file` resolves every path and rejects
> anything outside the repo root (`../../etc/passwd` → error string). Same principle as
> the GitHub client guards (entry 3): the model chooses tool *arguments*, and arguments
> are untrusted input. All four tools are read-only; caps (200 lines/read, k≤10)
> bound each call's blast radius and context cost.

> 💡 **(SE) The injection pattern, third time.** The agent takes `chat_fn` as a
> constructor argument, exactly as the index takes `embed_fn` and the guard tests take
> mock clients. A `ScriptedLLM` returning queued responses lets tests assert on the
> *loop's* behavior — tools executed, results fed back, step cap enforced, citations
> validated — in milliseconds, no API, deterministic. The entire agentic control flow
> is tested without a single LLM call.
