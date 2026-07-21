"""
diet.py — inference-time trajectory reduction (AgentDiet).

Implements the reflection module from "Reducing Cost of LLM Agents with
Trajectory Reduction" (Xiao et al., FSE 2026, arXiv:2509.23586), adapted to
ANet's orchestrator loop.

The problem: once a tool result lands in the trajectory it is resent to the
model on EVERY subsequent step. A 30k-token file read on step 2 of a 40-step
task is billed ~38 more times, even after the agent has moved on. The paper
measures 48.4K-token trajectories accumulating 1.0M input tokens per task,
with tool messages the single largest share.

The fix: after each agent step, a cheap "reflect" model rewrites ONE earlier
step, replacing useless / redundant / expired content with a short takeaway
("[... 2,890 lines omitted; relevant part kept above]"). The agent never sees
this happen — the paper found agents given an `erase` tool simply ignore it
and carry on with the task, so reduction has to be driven from outside.

Three guards keep it safe (all from the paper, which measured Pass% held or
improved on SWE-bench Verified even with a deliberately broken reducer):

  a=2      never touch the most recent steps — the agent is still using them
  theta    skip small steps, and discard a reduction that didn't save enough
  b=1      give the reducer surrounding context so it knows what's relevant

ANet-specific narrowing: we reduce only `role: "tool"` message content, not
assistant messages. Tool results are where the bulk sits, and assistant
messages carry tool_call ids that must stay structurally intact.

Off by default (it spends real tokens on a second model). Enable with a
`diet:` block in anet.config.yaml.
"""
from __future__ import annotations

import json

_DEFAULTS = {
    "enabled": False,
    "a": 2,        # reduce step s-a (never the most recent)
    "b": 1,        # context steps before the target
    "theta": 500,  # min tokens to bother reducing, and min saving to apply
}

# ANet has no tokenizer dependency; the paper's thresholds are in tokens, so
# convert with the usual ~4 chars/token approximation. Only used for "is this
# big enough to bother with" decisions, where being off by 20% is harmless.
_CHARS_PER_TOKEN = 4


def config() -> dict:
    """Resolve the `diet:` block from anet.config.yaml over the defaults."""
    try:
        from anet.core.config_loader import load
        cfg = (load() or {}).get("diet") or {}
    except Exception:
        cfg = {}
    out = dict(_DEFAULTS)
    out["enabled"] = bool(cfg.get("enabled", _DEFAULTS["enabled"]))
    for key in ("a", "b", "theta"):
        try:
            out[key] = int(cfg.get(key, _DEFAULTS[key]))
        except (TypeError, ValueError):
            out[key] = _DEFAULTS[key]
    out["model"] = cfg.get("model")
    out["provider"] = cfg.get("provider")
    return out


def _reflect_model(cfg: dict) -> tuple[str, str]:
    """(model, provider) for the reflect call: diet.{model,provider} ->
    manager -> built-in default. The paper's point is that this should be a
    CHEAP model — reduction is much easier than the agent's own task."""
    model, provider = cfg.get("model"), cfg.get("provider")
    if not model or not provider:
        try:
            from anet.core.config_loader import manager_config
            mgr = manager_config() or {}
        except Exception:
            mgr = {}
        model = model or mgr.get("model") or "gemini-2.5-flash"
        provider = provider or mgr.get("provider") or "openrouter"
    return model, provider


# ── Step boundaries in ANet's flat message list ──────────────────────────────

def find_steps(messages: list[dict], base_len: int) -> list[tuple[int, int]]:
    """Split the trajectory into agent steps.

    A step is one assistant message carrying tool calls, plus the tool result
    messages that follow it. Returns [(start, end)] index pairs (end
    exclusive). `base_len` is where the trajectory starts — everything before
    it (system prompt, history, the user task) is never touched.
    """
    steps: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(base_len, len(messages)):
        role = messages[i].get("role")
        if role == "assistant":
            if start is not None:
                steps.append((start, i))
            start = i
        elif role not in ("tool",) and start is not None:
            steps.append((start, i))
            start = None
    if start is not None:
        steps.append((start, len(messages)))
    return steps


def _tool_indices(messages: list[dict], step: tuple[int, int]) -> list[int]:
    return [i for i in range(*step) if messages[i].get("role") == "tool"]


def _approx_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


# ── The reflect call ─────────────────────────────────────────────────────────

_SYSTEM = (
    "You compress one tool result from an AI agent's working history, so it "
    "stops costing tokens on every future step.\n\n"
    "Remove three kinds of waste:\n"
    "- USELESS: content never relevant to the task (build noise, cache/"
    "binary file listings, long lists of passing tests).\n"
    "- REDUNDANT: content already stated elsewhere in the context.\n"
    "- EXPIRED: content that mattered for a step now finished (a file the "
    "agent looked at and moved on from, search hits it already rejected).\n\n"
    "RULES:\n"
    "- Keep anything the agent still needs: the specific code it is editing, "
    "error messages, failing tests, paths, names, values it will reference.\n"
    "- Replace what you remove with a SHORT bracketed note saying what was "
    "there, e.g. [... 2,890 more lines of app.py omitted ...] or "
    "[... 73 passing tests omitted ...]. Never silently delete.\n"
    "- Keep the original format (if it is JSON, return JSON).\n"
    "- If nothing can safely be removed, return the content unchanged.\n"
    "- Return ONLY the compressed content. No preamble, no explanation."
)


async def _reflect(content: str, task: str, ctx: str, cfg: dict) -> str:
    """Ask the cheap model for a compressed version of one tool result."""
    from anet.core.agent_runner import (
        build_vertex_client, _build_openai_client, _PROVIDERS, _DEFAULT_PROVIDER,
    )
    model, provider = _reflect_model(cfg)
    if provider in ("vertex_google", "vertex_anthropic", "vertex_claude"):
        client = build_vertex_client()
    elif provider in _PROVIDERS:
        client = _build_openai_client(provider)
    else:
        client = _build_openai_client(_DEFAULT_PROVIDER)

    user = (
        f"The agent is working on this task:\n{task}\n\n"
        f"Recent context (what the agent did around this step):\n{ctx}\n\n"
        f"Compress this tool result:\n{content}"
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": user}],
        max_tokens=2000,
        temperature=0.0,
    )
    from anet.core import tokens as _tok
    _tok.record(resp, stage="diet")   # so the overhead shows in the footer
    return (resp.choices[0].message.content or "").strip()


def _context_blurb(messages: list[dict], steps: list[tuple[int, int]],
                   target: int, b: int, budget: int = 2000) -> str:
    """A short digest of the steps around the target, so the reducer knows
    what the agent was doing. Truncated hard — this is context, not payload."""
    lo = max(0, target - b)
    parts: list[str] = []
    for idx in range(lo, len(steps)):
        start, end = steps[idx]
        msg = messages[start]
        text = (msg.get("content") or "").strip()
        calls = msg.get("tool_calls") or []
        names = ", ".join(
            (c.get("function", {}) or {}).get("name", "?")
            for c in calls if isinstance(c, dict)
        )
        line = f"[step {idx}] {text[:300]}"
        if names:
            line += f" (called: {names})"
        parts.append(line)
    return "\n".join(parts)[:budget]


# ── The public entry point (called from orchestrator's loop) ────────────────

async def maybe_reduce(messages: list[dict], base_len: int, task: str,
                       on_status=None, cfg: dict | None = None) -> int:
    """Reduce one earlier step of the trajectory, in place.

    Called after each agent step. Returns the number of tokens saved (0 if
    nothing was done). Never raises — a failed reduction must never break the
    agent's turn; the worst case is that the trajectory stays as it was.
    """
    cfg = cfg or config()
    if not cfg["enabled"]:
        return 0

    try:
        steps = find_steps(messages, base_len)
        target = len(steps) - 1 - cfg["a"]
        if target < 0:
            return 0   # not deep enough yet — the agent still needs recent steps

        theta_chars = cfg["theta"] * _CHARS_PER_TOKEN
        saved_total = 0

        for i in _tool_indices(messages, steps[target]):
            content = messages[i].get("content") or ""
            if len(content) <= theta_chars:
                continue   # too small to be worth a model call

            ctx = _context_blurb(messages, steps, target, cfg["b"])
            reduced = await _reflect(content, task, ctx, cfg)
            if not reduced:
                continue

            saved_chars = len(content) - len(reduced)
            if saved_chars <= theta_chars:
                continue   # not enough benefit — keep the original (paper line 16)

            messages[i]["content"] = reduced
            saved_total += saved_chars // _CHARS_PER_TOKEN

        if saved_total and on_status:
            on_status(f"diet: trimmed step {target} (-{saved_total} tokens)")
        return saved_total
    except Exception:
        return 0   # best-effort: never break the turn
