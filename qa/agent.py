"""The Q&A agent: a ReAct loop over the four codebase tools.

One iteration = the model either calls tools (we execute them and feed results
back) or produces the final answer. Multi-hop questions emerge naturally:
search -> read the hit -> follow a reference -> answer.

The LLM is injected as `chat_fn` (same signature as router.chat) so tests can
script the loop without any API. Citations in the final answer are validated
against the actual repo — a cited file must exist and its line range must be
inside the file. Invalid ones are flagged, never silently dropped: a wrong
citation is a signal the answer may be confabulated.

Usage:
    agent = QAAgent(tools)                       # real LLM via llm.router
    result = agent.ask("How is retry handled?")  # -> QAResult
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from qa.tools import QATools

MAX_STEPS = 10

SYSTEM_PROMPT = """You are a code analyst answering questions about one specific repository.

Rules:
- Use the tools to find evidence before answering. Never answer from prior knowledge \
about libraries or guesswork about this repo.
- For "where is X defined/used" questions prefer lookup_symbol; for concepts use \
search_code; use read_file to see full context around a hit.
- Every factual claim about the code MUST carry a citation in the exact form \
path:start-end (e.g. llm/router.py:85-130) pointing at real lines you have seen.
- When the evidence is sufficient, give a concise answer with citations. If you \
cannot find the answer, say so explicitly."""

CITATION_RE = re.compile(r"\b([\w./\\-]+\.[A-Za-z]{1,6}):(\d+)(?:-(\d+))?")


@dataclass
class Citation:
    path: str
    start: int
    end: int
    valid: bool
    reason: str = ""


@dataclass
class QAResult:
    answer: str
    citations: list[Citation]
    steps: int
    tool_trace: list[dict] = field(default_factory=list)
    ran_out_of_steps: bool = False

    @property
    def valid_citations(self) -> list[Citation]:
        return [c for c in self.citations if c.valid]


class QAAgent:
    def __init__(self, tools: QATools, chat_fn: Callable[..., Any] | None = None,
                 max_steps: int = MAX_STEPS) -> None:
        if chat_fn is None:
            from llm.router import router
            chat_fn = router.chat
        self.tools = tools
        self.chat_fn = chat_fn
        self.max_steps = max_steps

    def ask(self, question: str) -> QAResult:
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        trace: list[dict] = []

        for step in range(1, self.max_steps + 1):
            resp = self.chat_fn(messages, lane="fast", tools=QATools.schemas(),
                                max_tokens=2048)
            if not resp.tool_calls:
                return QAResult(answer=resp.text, citations=self._validate(resp.text),
                                steps=step, tool_trace=trace)

            # Echo the assistant turn (with its tool calls) back into context,
            # then answer each call with a role="tool" message keyed by call id.
            messages.append({
                "role": "assistant",
                "content": resp.text or None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in resp.tool_calls
                ],
            })
            for tc in resp.tool_calls:
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = None
                result = (self.tools.dispatch(tc["name"], args)
                          if isinstance(args, dict)
                          else f"Error: arguments were not valid JSON: {tc['arguments']!r}")
                trace.append({"step": step, "tool": tc["name"],
                              "arguments": tc["arguments"], "result_chars": len(result)})
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": result})

        # Out of steps: force a final answer from whatever evidence is gathered.
        messages.append({"role": "user",
                         "content": "Stop searching. Answer now from the evidence above, "
                                    "with citations, or say what is still unknown."})
        resp = self.chat_fn(messages, lane="fast", max_tokens=2048)
        return QAResult(answer=resp.text, citations=self._validate(resp.text),
                        steps=self.max_steps, tool_trace=trace, ran_out_of_steps=True)

    def _validate(self, answer: str) -> list[Citation]:
        citations = []
        for m in CITATION_RE.finditer(answer):
            path, start = m.group(1).replace("\\", "/"), int(m.group(2))
            end = int(m.group(3)) if m.group(3) else start
            target = self.tools.repo_path / path
            if not target.is_file():
                citations.append(Citation(path, start, end, False, "file not found"))
                continue
            n_lines = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            if start < 1 or end > n_lines or start > end:
                citations.append(Citation(path, start, end, False,
                                          f"range outside file (1-{n_lines})"))
            else:
                citations.append(Citation(path, start, end, True))
        return citations
