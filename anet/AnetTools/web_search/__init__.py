"""
web_search — Exa-powered semantic web search.

Key behaviours:
- recency_days is truly optional. When omitted, no date filter is applied
  and results are sorted by relevance (best for docs/code queries).
  Pass recency_days=1 for breaking news, 7 for recent events.
- type="code" biases results toward GitHub, Stack Overflow, and official docs.
- num_results lets the caller control result count (1–8, default 3).
"""

import os
from datetime import datetime, timedelta, timezone

import httpx

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for information, documentation, and code examples. "
            "Use type='code' for programming questions, library docs, or error messages. "
            "Use recency_days only when freshness matters (news, changelogs, status updates)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "type": {
                    "type": "string",
                    "enum": ["general", "code"],
                    "description": (
                        "general = broad web search (default). "
                        "code = biases toward GitHub, Stack Overflow, and official docs."
                    ),
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (1–8). Default 3.",
                },
                "recency_days": {
                    "type": "integer",
                    "description": (
                        "Only return results from the last N days. "
                        "Use 1 for breaking news, 7 for recent events. "
                        "Omit entirely for documentation, code, or timeless queries."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

_EXA_URL       = "https://api.exa.ai/search"
_TIMEOUT       = 20.0
_SNIPPET_CHARS = 1500
_MAX_RESULTS   = 8
_DEFAULT_RESULTS = 3

# Domains to include when type="code"
_CODE_DOMAINS = [
    "github.com",
    "stackoverflow.com",
    "developer.mozilla.org",
    "docs.python.org",
    "npmjs.com",
    "pypi.org",
    "reactjs.org",
    "react.dev",
    "vitejs.dev",
    "tailwindcss.com",
    "nextjs.org",
    "fastapi.tiangolo.com",
    "docs.anthropic.com",
    "platform.openai.com",
]


async def _search(client: httpx.AsyncClient, api_key: str, body: dict) -> list[dict]:
    """Execute one Exa search call and return raw results list."""
    response = await client.post(
        _EXA_URL,
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json=body,
    )
    response.raise_for_status()
    return response.json().get("results", [])


async def run(input: dict) -> dict:
    query       = input.get("query", "").strip()
    search_type = input.get("type", "general")
    num_results = min(int(input.get("num_results", _DEFAULT_RESULTS)), _MAX_RESULTS)
    recency_days = input.get("recency_days")   # None = no date filter

    if not query:
        return {"error": "query is required"}

    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        return {"error": "EXA_API_KEY is not set"}

    # Build request body
    body: dict = {
        "query":         query,
        "numResults":    num_results,
        "useAutoprompt": True,
        "contents": {
            "text": {"maxCharacters": _SNIPPET_CHARS},
        },
    }

    # Date filter + sort — only when the caller explicitly wants freshness
    if recency_days is not None:
        start = (datetime.now(timezone.utc) - timedelta(days=int(recency_days))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        body["startPublishedDate"] = start
        body["sortBy"] = "date"
    else:
        # No date filter — sort by relevance for docs/code queries
        body["sortBy"] = "relevance"

    # Code mode: bias toward developer domains
    if search_type == "code":
        body["includeDomains"] = _CODE_DOMAINS

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            results = await _search(client, api_key, body)

            # If code search returns nothing, fall back to general (same query, no domain filter)
            if not results and search_type == "code":
                fallback = {k: v for k, v in body.items() if k != "includeDomains"}
                results = await _search(client, api_key, fallback)

            # If still nothing AND we used a date filter, retry without it
            if not results and recency_days is not None:
                no_date = {k: v for k, v in body.items()
                           if k not in ("startPublishedDate", "sortBy", "includeDomains")}
                no_date["sortBy"] = "relevance"
                results = await _search(client, api_key, no_date)

        if not results:
            return {"results": [], "snippet": "No results found."}

        # Format output
        blocks = []
        for r in results:
            title     = r.get("title") or "Untitled"
            url       = r.get("url", "")
            text      = (r.get("text") or "").strip()
            published = r.get("publishedDate", "")
            date_line = f"Published: {published}\n" if published else ""
            if text:
                blocks.append(f"### {title}\n{url}\n{date_line}{text}")

        return {
            "snippet": "\n\n---\n\n".join(blocks) if blocks else "No content available.",
            "results": [
                {
                    "title":          r.get("title", ""),
                    "url":            r.get("url", ""),
                    "snippet":        (r.get("text") or "")[:500],
                    "published_date": r.get("publishedDate", ""),
                }
                for r in results
            ],
        }

    except httpx.TimeoutException:
        return {"error": "Search timed out after 20s"}
    except httpx.HTTPStatusError as exc:
        return {"error": f"Exa API HTTP {exc.response.status_code}: {exc.response.text[:300]}"}
    except Exception as exc:
        return {"error": str(exc)}
