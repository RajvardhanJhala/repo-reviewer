# Codebase Q&A Agent (Phase 2)

`python -m qa --repo . "your question"` — answers questions about an indexed repository
with validated `file:line` citations, using a from-scratch ReAct loop over four tools.

## The loop

```
system prompt + question
        │
        ▼
┌─▶ model responds
│       │
│       ├── tool calls?  ──▶ execute each (search_code / lookup_symbol /
│       │                    read_file / list_directory), append results
│       │                    as role="tool" messages ──┐
│       │◀─────────────────────────────────────────────┘
│       │
│       └── final text ──▶ extract citations, validate each against the
│                          real repo (file exists? lines in range?)
└── max 10 steps, then the agent is told to answer from gathered evidence
```

The LLM is injected (`chat_fn`), so tests drive the loop with scripted responses —
no API calls. Tools are read-only and path-confined to the repo root.

## A real multi-hop trace (captured 2026-08-22)

**Q:** *"If Groq goes down entirely, what happens to a fast-lane request? Which models
take over, in what order, and where is that order configured?"*

| step | tool call | note |
|---|---|---|
| 1 | `search_code(query="fast-lane", path="")` | invalid arg → error returned → **model self-corrected** |
| 2 | `search_code(query="fast-lane")` | found router + config chunks |
| 3 | `lookup_symbol("fast_model")` | exact definition location |
| 4 | `read_file("config.py", 10-50)` | read the lane + fallback config |
| 5 | `read_file("llm/router.py", 89-140)` | read the fallback loop itself |
| 6 | — | final answer |

Answer (correct): primary `groq/openai/gpt-oss-120b` → `openrouter/nvidia/nemotron-3-super-120b-a12b:free`
→ `groq/openai/gpt-oss-20b`, chain built as `[primary, *settings.fallback_models]`,
with 6/6 citations validated (`config.py:37`, `config.py:39-44`, `llm/router.py:98-99`, …).

**The run demonstrated its own answer:** Groq was rate-limiting during the trace, so 3 of
the 6 LLM calls actually fell back to the OpenRouter model — router stats recorded
`fallbacks: 3`. The agent described the fallback chain while running on it.

## Citation validation as a hallucination guard

Every `path:start-end` in the final answer is checked against the working tree: the file
must exist and the range must be inside it. Invalid citations are flagged, not dropped —
a fabricated citation is evidence the claim itself may be fabricated, and the CLI marks
it `BAD` so the human sees exactly which claims to distrust.
