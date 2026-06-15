"""
wordcount.py — a tiny sample library used as the SUBJECT for the /newtool
walkthrough (tests/AnetTests/add_tool_test.md).

Pure functions, no dependencies — an easy thing for the toolsmith agent to wrap
into an ExTool. Not registered anywhere; it does nothing until you run /newtool.
"""


def count_words(text: str) -> int:
    """Return the number of whitespace-separated words in text."""
    return len(text.split())


def count_chars(text: str, include_spaces: bool = True) -> int:
    """Return the number of characters in text, optionally excluding spaces."""
    return len(text) if include_spaces else len(text.replace(" ", ""))


def count_lines(text: str) -> int:
    """Return the number of lines in text."""
    return len(text.splitlines())
