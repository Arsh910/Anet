"""
tokens.py — per-turn token accounting.

Every provider returns token usage on each completion, but ANet was discarding it.
This module captures it: a ContextVar holds a TokenUsage accumulator for the
current turn; the model-call sites (agent_runner, stage_models) call record(resp)
after each completion; the CLI reads the running total for the spinner and the
per-turn total for the reply footer.

ContextVar + a mutable accumulator means parallel agent tasks all bump the SAME
counter (the var's value is copied by reference into child tasks), so concurrent
executors aggregate correctly without locks.

`stage` lets calls tag their phase (decomposer / merger / arbiter / agent name)
so the AdaptOrch coordinator can show a per-phase breakdown.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    prompt: int = 0
    completion: int = 0
    cache_read: int = 0       # input tokens served from the prompt cache (cheap)
    cache_creation: int = 0   # input tokens written to the cache (slight premium)
    calls: int = 0
    by_stage: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def add(self, prompt: int = 0, completion: int = 0,
            cache_read: int = 0, cache_creation: int = 0,
            stage: str | None = None) -> None:
        self.prompt += prompt
        self.completion += completion
        self.cache_read += cache_read
        self.cache_creation += cache_creation
        self.calls += 1
        if stage:
            self.by_stage[stage] = self.by_stage.get(stage, 0) + prompt + completion


_usage_var: ContextVar[TokenUsage | None] = ContextVar("token_usage", default=None)


def begin() -> TokenUsage:
    """Start a fresh accounting scope for a turn and return the accumulator."""
    u = TokenUsage()
    _usage_var.set(u)
    return u


def current() -> TokenUsage | None:
    return _usage_var.get()


def record(resp, stage: str | None = None) -> None:
    """Pull token usage off a completion response and add it to the active counter.

    Handles OpenAI-style (prompt_tokens/completion_tokens, optionally with
    prompt_tokens_details.cached_tokens) and Anthropic-style (input_tokens/
    output_tokens with cache_read_input_tokens / cache_creation_input_tokens).
    Best-effort — never raises."""
    u = _usage_var.get()
    if u is None or resp is None:
        return
    try:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if prompt is None:
            prompt = getattr(usage, "input_tokens", 0)
        if completion is None:
            completion = getattr(usage, "output_tokens", 0)

        # Cache-hit accounting. Anthropic exposes cache_{read,creation}_input_tokens
        # directly; OpenAI-compat returns cached_tokens nested under prompt_tokens_details.
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if not cache_read:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cache_read = getattr(details, "cached_tokens", 0) or 0

        u.add(
            prompt=int(prompt or 0),
            completion=int(completion or 0),
            cache_read=int(cache_read or 0),
            cache_creation=int(cache_creation or 0),
            stage=stage,
        )
    except Exception:
        pass


def fmt(n: int) -> str:
    """Compact human count: 940, 12.3k, 1.2M."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)
