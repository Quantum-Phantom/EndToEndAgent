"""Pure text-processing tools with no external side effects."""

from typing import Any

from langchain_core.tools import tool


@tool
def text_stats(text: str) -> dict[str, int]:
    """Count characters, non-whitespace characters, words and lines in text."""
    return {
        "characters": len(text),
        "non_whitespace_characters": sum(not char.isspace() for char in text),
        "words": len(text.split()),
        "lines": len(text.splitlines()) if text else 0,
    }


@tool
def find_text(text: str, query: str) -> dict[str, Any]:
    """Find all case-sensitive occurrences of a query in text."""
    if not query:
        raise ValueError("query must not be empty")
    positions: list[int] = []
    start = 0
    while (index := text.find(query, start)) >= 0:
        positions.append(index)
        start = index + len(query)
    return {"query": query, "count": len(positions), "positions": positions}


@tool
def replace_text(text: str, old: str, new: str) -> dict[str, Any]:
    """Replace every case-sensitive occurrence of old text with new text."""
    if not old:
        raise ValueError("old must not be empty")
    replacements = text.count(old)
    return {"text": text.replace(old, new), "replacements": replacements}


TEXT_TOOLS = [text_stats, find_text, replace_text]
