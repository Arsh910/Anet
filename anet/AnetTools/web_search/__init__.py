import httpx
import os
from datetime import datetime, timedelta, timezone

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information, news, and facts on any topic",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "recency_days": {
                    "type": "integer",
                    "description": "Only return results from the last N days. Use 1 for breaking news, 7 for recent events, 30 for general recent info. Omit for all-time.",
                }
            },
            "required": ["query"],
        },
    },
}

_EXA_URL = "https://api.exa.ai/search"
_TIMEOUT = 15.0
_NUM_RESULTS = 3
_SNIPPET_CHARS = 800


async def run(input: dict) -> dict:
    query = input.get("query", "").strip()
    recency_days = input.get("recency_days", 7)  # default to last 7 days

    if not query:
        return {"error": "No query provided"}

    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        return {"error": "EXA_API_KEY is not set in the environment"}

    # build date filter
    start_date = (
        datetime.now(timezone.utc) - timedelta(days=recency_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                _EXA_URL,
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "numResults": _NUM_RESULTS,
                    "useAutoprompt": True,
                    "startPublishedDate": start_date,  # ← freshness filter
                    "sortBy": "date",                   # ← newest first
                    "contents": {
                        "text": {"maxCharacters": _SNIPPET_CHARS},
                    },
                },
            )
            response.raise_for_status()
            data = response.json()

        results = data.get("results", [])

        # if no results with date filter, retry without it
        if not results:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    _EXA_URL,
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "numResults": _NUM_RESULTS,
                        "useAutoprompt": True,
                        "contents": {
                            "text": {"maxCharacters": _SNIPPET_CHARS},
                        },
                    },
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])

        if not results:
            return {"title": "", "snippet": "No results found.", "url": ""}

        combined = []
        for r in results:
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            text = (r.get("text") or "").strip()
            published = r.get("publishedDate", "")
            if text:
                combined.append(f"**{title}**\n{url}\nPublished: {published}\n{text}")

        top = results[0]
        return {
            "title": top.get("title", ""),
            "url": top.get("url", ""),
            "snippet": "\n\n---\n\n".join(combined) if combined else top.get("text", "No content available"),
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (r.get("text") or "")[:400],
                    "published_date": r.get("publishedDate", ""),
                }
                for r in results
            ],
        }

    except httpx.TimeoutException:
        return {"error": "Exa search timed out"}
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:300]
        return {"error": f"Exa API returned HTTP {exc.response.status_code}: {body}"}
    except Exception as exc:
        return {"error": str(exc)}