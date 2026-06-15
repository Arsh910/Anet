"""
ask_user — pause and ask the user a clarifying question, then wait for the answer.

For the moments when the task is genuinely ambiguous and guessing wrong would
waste work: "Postgres or SQLite?", "which of these files did you mean?",
"should I overwrite or create a new one?". The agent asks, the user answers, and
the answer comes back as the tool result so the agent can continue correctly.

Surfaces the question through the on_ask callback (installed by main.py), which
pauses the spinner and reads a line from the user. In headless mode the default
callback returns a note so the agent proceeds on its best assumption instead of
hanging.
"""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user a clarifying question and wait for their answer. Use ONLY when "
            "the task is genuinely ambiguous and a wrong guess would waste real work "
            "(e.g. which database, which of several matching files, overwrite vs create). "
            "Do NOT use for things you can decide yourself, look up, or infer from context "
            "— prefer acting over asking. Returns the user's answer as text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The single, specific question to ask. Keep it short and concrete.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional suggested choices. The user can pick one by number or type "
                        "their own answer. Provide 2–4 when the question is a clear either/or."
                    ),
                },
            },
            "required": ["question"],
        },
    },
}


async def run(input: dict) -> dict:
    question = (input.get("question") or "").strip()
    options = input.get("options") or []
    if not isinstance(options, list):
        options = []
    if not question:
        return {"error": "question is required"}

    from anet.core.context import on_ask

    try:
        ask_fn = on_ask.get()
        answer = await ask_fn(question, options)
    except Exception as exc:
        return {"error": f"could not ask the user: {exc}"}

    answer = (answer or "").strip()
    return {"result": answer or "(the user gave no answer)"}
