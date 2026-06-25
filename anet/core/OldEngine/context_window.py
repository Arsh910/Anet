"""
context_window.py — short-term (working) memory: a token-budgeted, tiered view of
the conversation for the model.

Every turn the engine has the full thread history but must hand the model a bounded
slice. Instead of a fixed message count (the old `messages[-8:]`), this module packs:

    [ rolling summary of older turns ]   ← always carried, grows incrementally
    [ recent turns, verbatim ]           ← as many as the token budget allows

The boundary between the two advances as the conversation grows: once the recent
turns no longer fit the budget, the overflow is folded into the summary (one LLM
call, only when overflow occurs — not every turn). The newest turns are always kept
verbatim so the latest exchange is never lost.

This module is pure logic (no I/O, no LLM calls) so it's easy to test; the engine
owns the store reads/writes and the summarisation call, using the prompt built here.
"""
from __future__ import annotations

# ── Token counting ────────────────────────────────────────────────────────────
# Prefer tiktoken when present (accurate); otherwise a chars/4 heuristic, which is
# close enough for budgeting with the safety margin built into the defaults.

_encoder = None
_encoder_tried = False


def _get_encoder():
    global _encoder, _encoder_tried
    if _encoder_tried:
        return _encoder
    _encoder_tried = True
    try:
        import tiktoken
        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _encoder = None
    return _encoder


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // 4 + 1


def msg_tokens(m: dict) -> int:
    # +4 for role/format overhead per message (matches OpenAI's per-message fudge).
    return count_tokens(m.get("content") or "") + 4


# ── Windowing ───────────────────────────────────────────────────────────────────

def plan_window(
    messages: list[dict],
    recent_tokens: int,
    min_recent: int,
    summarized_count: int,
) -> tuple[int, list[dict]]:
    """Decide the recent-verbatim region and what overflows into the summary.

    Returns (keep_from, overflow) where:
      • messages[keep_from:] are kept verbatim (fit within `recent_tokens`, but the
        last `min_recent` messages are always kept even if that exceeds the budget),
      • overflow = messages[summarized_count:keep_from] — the not-yet-summarised
        turns that should now be folded into the rolling summary.
    """
    n = len(messages)
    summarized_count = max(0, min(summarized_count, n))

    total = 0
    keep_from = n  # walk backward from the end, growing the verbatim region
    for i in range(n - 1, summarized_count - 1, -1):
        t = msg_tokens(messages[i])
        # Stop once we'd blow the budget — but never below `min_recent` messages.
        if total + t > recent_tokens and (n - i) > min_recent:
            break
        total += t
        keep_from = i

    overflow = messages[summarized_count:keep_from]
    return keep_from, overflow


def assemble(messages: list[dict], summary: str, keep_from: int) -> list[dict]:
    """Build the message list to send the model: the rolling summary (if any) as a
    leading system message, followed by the recent verbatim turns."""
    out: list[dict] = []
    if summary:
        out.append({
            "role": "system",
            "content": "Summary of earlier conversation (for context):\n" + summary,
        })
    out.extend(messages[keep_from:])
    return out


# ── Summarisation prompt (the LLM call itself lives in the engine) ───────────────

_SUMMARY_SYSTEM = (
    "You maintain a running summary of a conversation between a user and an AI "
    "assistant. Given the summary so far and the new messages that are about to "
    "scroll out of the live window, return an updated summary that PRESERVES every "
    "durable fact: decisions made, file paths, names, numbers, the user's goals and "
    "constraints, and unresolved threads. Drop pleasantries and superseded details. "
    "Keep it tight and factual — bullet points or short sentences. Return ONLY the "
    "updated summary text."
)


def build_summary_messages(old_summary: str, overflow: list[dict]) -> list[dict]:
    """Messages for the summariser model: prior summary + the overflow turns."""
    convo = "\n".join(
        f"{m.get('role', '?').upper()}: {(m.get('content') or '').strip()}"
        for m in overflow
        if (m.get("content") or "").strip()
    )
    user = (
        (f"Summary so far:\n{old_summary}\n\n" if old_summary else "")
        + f"New messages scrolling out of the window:\n{convo}\n\n"
        "Return the updated running summary."
    )
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user},
    ]
