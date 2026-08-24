"""Provider-agnostic LLM layer.

Two "lanes" so callers express *intent* rather than picking providers:
    chat(messages, lane="quality")  -> settings.quality_model (long context, careful work)
    chat(messages, lane="fast")     -> settings.fast_model (low-latency agent loops)

Pass tools=[...] (OpenAI function schema) and the response carries normalized
tool_calls: [{"id", "name", "arguments"}] with arguments as a JSON string.

If the lane's primary provider rate-limits or errors, LiteLLM transparently
falls back down `settings.fallback_models`. Every call records which model
actually served it plus tokens + latency, so /metrics can later prove the
fallback path is real and not just configured.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import litellm

from config import settings

# LiteLLM reads provider keys from env vars; mirror settings into os.environ once.
os.environ.setdefault("GEMINI_API_KEY", settings.gemini_api_key)
os.environ.setdefault("GROQ_API_KEY", settings.groq_api_key)
os.environ.setdefault("OPENROUTER_API_KEY", settings.openrouter_api_key)
os.environ.setdefault("CEREBRAS_API_KEY", settings.cerebras_api_key)

litellm.suppress_debug_info = True

Lane = Literal["quality", "fast"]
log = logging.getLogger(__name__)

@dataclass
class LLMResponse:
    text: str
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    fell_back: bool
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    raw: Any = field(repr=False, default=None)


# USD per 1M tokens (prompt, completion). Every model we use is free-tier ($0),
# but the accounting is real so swapping in a paid model reports true cost.
PRICE_PER_1M: dict[str, tuple[float, float]] = {
    "gemini/gemini-3.6-flash": (0.0, 0.0),
    "groq/openai/gpt-oss-120b": (0.0, 0.0),
    "groq/openai/gpt-oss-20b": (0.0, 0.0),
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free": (0.0, 0.0),
}
DEFAULT_PRICE = (0.0, 0.0)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p_in, p_out = PRICE_PER_1M.get(model, DEFAULT_PRICE)
    return (prompt_tokens * p_in + completion_tokens * p_out) / 1_000_000


@dataclass
class UsageStats:
    calls: int = 0
    fallbacks: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latencies: list[float] = field(default_factory=list)
    by_model: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0

    def record(self, r: LLMResponse) -> None:
        self.calls += 1
        self.fallbacks += int(r.fell_back)
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.cost_usd += estimate_cost(r.model_used, r.prompt_tokens, r.completion_tokens)
        self.latencies.append(r.latency_s)
        self.by_model[r.model_used] = self.by_model.get(r.model_used, 0) + 1

    def summary(self) -> dict[str, Any]:
        lat = sorted(self.latencies)
        pct = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))] if lat else 0.0
        return {
            "calls": self.calls,
            "fallbacks": self.fallbacks,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "p50_latency_s": round(pct(0.50), 3),
            "p95_latency_s": round(pct(0.95), 3),
            "cost_usd": round(self.cost_usd, 6),
            "by_model": self.by_model,
        }


class LLMRouter:
    def __init__(self) -> None:
        self.stats = UsageStats()
        self._lane_models: dict[Lane, str] = {
            "quality": settings.quality_model,
            "fast": settings.fast_model,
        }

    def chat(
        self,
        messages: list[dict[str, str]],
        lane: Lane = "quality",
        temperature: float = 0.1,
        max_tokens: int = 2048,
        json_mode: bool = False,
        timeout_s: float = 90.0,
        **kwargs: Any,
    ) -> LLMResponse:
        primary = self._lane_models[lane]
        chain = [primary, *settings.fallback_models]
        last_err: Exception | None = None

        for i, model in enumerate(chain):
            t0 = time.perf_counter()
            try:
                extra: dict[str, Any] = {}
                if json_mode:
                    extra["response_format"] = {"type": "json_object"}
                # Gemini 3 warns temperature<1.0 "can cause infinite loops and
                # degraded reasoning" - let those models use their default.
                if not model.startswith("gemini/"):
                    extra["temperature"] = temperature
                resp = litellm.completion(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    timeout=timeout_s,
                    **extra,
                    **kwargs,
                )
                usage = getattr(resp, "usage", None)
                message = resp.choices[0].message
                tool_calls = [
                    {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                    for tc in (getattr(message, "tool_calls", None) or [])
                ]
                out = LLMResponse(
                    text=message.content or "",
                    tool_calls=tool_calls,
                    model_used=model,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    latency_s=time.perf_counter() - t0,
                    fell_back=(i > 0),
                    raw=resp,
                )
                self.stats.record(out)
                return out
            except Exception as e:
                log.warning("lane=%s model=%s failed (%s); trying next", lane, model, type(e).__name__)
                last_err = e
                continue
            

        raise RuntimeError(f"All providers failed for lane={lane}. Last error: {last_err}")


# Module-level singleton is fine for a single-process app; swap for DI later if needed.
router = LLMRouter()
