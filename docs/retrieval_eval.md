# Retrieval eval — repo-reviewer (35 questions)

## all (35 questions)

| configuration | hit@1 | hit@3 | hit@5 |
|---|---|---|---|
| naive chunks + dense | 40% | 71% | 80% |
| naive chunks + hybrid | 43% | 74% | 89% |
| AST chunks + dense | 54% | 77% | 89% |
| AST chunks + hybrid | 54% | 77% | 94% |

## natural (25 questions)

| configuration | hit@1 | hit@3 | hit@5 |
|---|---|---|---|
| naive chunks + dense | 40% | 72% | 84% |
| naive chunks + hybrid | 40% | 72% | 84% |
| AST chunks + dense | 44% | 76% | 92% |
| AST chunks + hybrid | 44% | 76% | 92% |

## identifier (10 questions)

| configuration | hit@1 | hit@3 | hit@5 |
|---|---|---|---|
| naive chunks + dense | 40% | 70% | 70% |
| naive chunks + hybrid | 50% | 80% | 100% |
| AST chunks + dense | 80% | 80% | 80% |
| AST chunks + hybrid | 80% | 80% | 100% |

## Reading the table

- **AST chunking beats naive fixed-size windows** in every configuration — the headline
  claim of Phase 1. On natural-language questions: 92% vs 84% hit@5.
- **"hybrid" here means routed hybrid**: BM25 is fused (equal-weight RRF, depth 10) only
  when the query is identifier-shaped; prose queries are served dense-only. An earlier
  unrouted hybrid (BM25 fused into every query, depth 50) scored **68%** hit@5 on the
  natural questions — *worse* than dense alone — because documentation chunks lexically
  shadow the code they describe. The routing decision came directly from this eval.
- **Context headers do heavy lifting on identifier queries**: AST+dense hits 80% @1 on
  bare symbol names purely because the symbol appears in the embedded header; naive
  chunks without headers manage 40%.
- Ground truth: `eval/questions_repo_reviewer.json` — 25 natural + 10 identifier
  questions with hand-verified answer regions. A hit = any returned chunk overlaps an
  answer region. Corpus = this repo (21 files, 141 AST chunks / 79 naive chunks),
  `eval/` excluded. Caveat: answer regions are pinned to line numbers, so they must be
  re-verified after editing the files they point into.

Rerun with: `python -m eval --repo .`
