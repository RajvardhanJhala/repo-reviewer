# Why AST-Aware Chunking

## The problem with naive chunking

The default RAG recipe — split text every N characters with some overlap — is actively
hostile to code:

- **Functions get split mid-body.** A retrieved fragment shows `if not validate(order_id):`
  with no idea what function it's in, what the arguments mean, or what gets returned.
- **Signatures separate from docstrings.** The two most information-dense lines of a
  function end up in different chunks.
- **Class context vanishes.** A method chunk without its class name is nearly useless —
  `def save(self)` could belong to anything.
- **Fixed sizes ignore meaning.** A 40-line function is one natural retrieval unit; naive
  chunking might make it 1.5 chunks, sharing space with an unrelated neighbor.

Code already has a structure — the parse tree. Chunking should use it.

## What this indexer does instead

Parsing is done with **tree-sitter** (incremental parsers for Python and JavaScript).
Chunk boundaries = AST node boundaries:

| Source construct | Becomes |
|---|---|
| top-level function | one chunk (`symbol_type=function`) |
| method | one chunk, `parent_symbol` = class name |
| class | one **skeleton** chunk: signatures + class-level code, method bodies elided to `...` |
| module-level code (imports, constants) | contiguous runs → `(module)` chunks |
| oversized function (> 120 lines) | split at statement boundaries; the signature is repeated at the top of every continuation part |
| markdown | one chunk per `#`/`##` section |
| config (YAML/TOML/JSON/…) | kept whole — config loses meaning when split |

**Nothing is lost**: every non-blank source line lands in at least one chunk
(tested in `tests/test_chunker.py::test_nothing_is_lost`).

### Context headers

Every chunk's embedded text is prefixed with a header:

```
# repo-reviewer/gh/client.py :: GitHubClient :: post_review_summary (lines 59-64)
```

The embedding model therefore sees *where the code lives*, not just what it says —
a bare `def save(self)` embeds very differently when the header says
`PaymentService :: save`. The header also carries the exact line range every answer
will cite (`path:start-end`), which Phase 2 turns into clickable GitHub permalinks.

### The class skeleton trick

A class chunk is the full class with each method body replaced by `...`:

```python
class PaymentService:
    """Handles refunds."""
    retries = 3
    def process_refund(self, order_id):
        ...
    def audit(self):
        ...
```

This reads like an API summary — perfect for "what does PaymentService do?" queries —
while each method's full body lives in its own chunk for detail queries.

## Hybrid retrieval: why BM25 still matters for code

Dense embeddings (bge-m3, local) understand *meaning*: "how are refunds handled?"
matches `process_refund` at cosine ≈ 0.64. But code queries are often *exact*:
searching `post_review_summary` must return that method, not something semantically
adjacent. Lexical search (BM25) wins there — especially with code-aware tokenization
that indexes `process_refund` both as the exact identifier and as sub-words
`process` + `refund` (camelCase is split the same way).

The two rankings are combined with **Reciprocal Rank Fusion**:
`score(chunk) = Σ 1/(60 + rank)` across both retrievers. RRF uses only rankings,
never raw scores, so there's no need to calibrate BM25 scores against cosine
similarities — they aren't comparable and don't have to be.

**But fusion is routed, not unconditional.** Measured on the eval set
(`docs/retrieval_eval.md`), BM25 *hurts* prose queries — documentation chunks use the
same words as the questions and lexically shadow the code they describe — while it
lifts identifier queries to 100% hit@5. So `search()` fuses BM25 only when the query
is identifier-shaped (one token, no spaces); everything else is served dense-only.

## Incremental re-indexing

The index keeps a manifest `{file path → sha256 of content}`. On re-index, only files
whose hash changed are re-chunked and re-embedded; deleted files are dropped.
Measured on this repo: full index ~6 min (CPU embeddings), no-op re-index ~13 s,
one-file change re-embeds only that file's chunks. This is what makes
webhook-triggered per-PR re-indexing viable in Phase 4.

## Symbol table

While walking the ASTs, definitions (`name → path:line`) and references (every
identifier occurrence matching a known definition, minus the definition site) are
collected into `symbols.json`. This answers "where is X defined / used?" — exact
identity questions that similarity search *cannot* answer reliably, and it becomes
the `lookup_symbol` tool in Phase 2.
