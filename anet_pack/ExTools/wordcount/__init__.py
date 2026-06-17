"""
wordcount — a tiny, zero-dependency example ExTool.

This is the "hello world" of ANet tools: it shows the exact contract every ExTool
follows — a SCHEMA dict + a run() function returning {"result": ...} or
{"error": ...}. No external packages, no credentials. Copy it as a starting point,
or run `/newtool` to have the ToolSmith generate one for you.

Try it: ask ANet "count the words in: the quick brown fox jumps".
"""

SCHEMA = {
    "type": "function",
    "function": {
        "name": "wordcount",  # MUST match the folder name and the registered name
        "description": (
            "Count the words, characters, and lines in a piece of text. "
            "Use when the user asks how long some text is or for basic text stats."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to measure."},
            },
            "required": ["text"],
        },
    },
}


async def run(params: dict) -> dict:  # may be sync or async
    text = params.get("text")
    if not text:
        return {"error": "text is required"}
    return {
        "result": {
            "words": len(text.split()),
            "characters": len(text),
            "lines": len(text.splitlines()) or 1,
        }
    }
