"""
web_search — DuckDuckGo-powered web and image search.

No API key required. Uses the duckduckgo-search package.
type="image" returns direct image URLs ready for download_file.
"""

import asyncio

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for information, documentation, code examples, or images. "
            "Use type='image' to find downloadable image URLs. "
            "Use type='code' for programming questions. "
            "Use recency_days only when freshness matters (news, recent events)."
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
                    "enum": ["general", "code", "image"],
                    "description": (
                        "general = broad web search (default). "
                        "code = biases toward GitHub, Stack Overflow, and official docs. "
                        "image = returns direct image URLs suitable for download_file."
                    ),
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (1–8). Default 5.",
                },
                "recency_days": {
                    "type": "integer",
                    "description": (
                        "Only return results from the last N days. "
                        "1=today, 7=this week, 30=this month. "
                        "Omit for documentation, code, or timeless queries."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

_DEFAULT_RESULTS = 5
_MAX_RESULTS = 8


def _timelimit(recency_days) -> str | None:
    if recency_days is None:
        return None
    days = int(recency_days)
    if days <= 1:
        return "d"
    if days <= 7:
        return "w"
    return "m"


async def run(input: dict) -> dict:
    query       = (input.get("query") or "").strip()
    search_type = input.get("type", "general")
    num_results = min(int(input.get("num_results", _DEFAULT_RESULTS)), _MAX_RESULTS)
    recency     = input.get("recency_days")
    timelimit   = _timelimit(recency)

    if not query:
        return {"error": "query is required"}

    try:
        from ddgs import DDGS
    except ImportError:
        return {"error": "ddgs is not installed. Run: pip install ddgs"}

    try:
        if search_type == "image":
            results = await asyncio.to_thread(
                _image_search, query, num_results, timelimit
            )
        else:
            results = await asyncio.to_thread(
                _text_search, query, search_type, num_results, timelimit
            )
        return results
    except Exception as exc:
        return {"error": f"Search failed: {exc}"}


def _text_search(query: str, search_type: str, num_results: int, timelimit) -> dict:
    from ddgs import DDGS

    # Prepend "python" or common dev terms for code searches
    effective_query = query
    if search_type == "code" and not any(
        kw in query.lower() for kw in ("python", "javascript", "github", "npm", "pip", "api")
    ):
        effective_query = f"{query} site:github.com OR site:stackoverflow.com OR docs"

    with DDGS() as ddgs:
        raw = list(ddgs.text(
            effective_query,
            max_results=num_results,
            timelimit=timelimit,
        ))

    if not raw:
        return {"results": [], "snippet": "No results found."}

    blocks = []
    structured = []
    for r in raw:
        title = r.get("title", "Untitled")
        url   = r.get("href", "")
        body  = (r.get("body") or "").strip()
        if body:
            blocks.append(f"### {title}\n{url}\n{body}")
        structured.append({"title": title, "url": url, "snippet": body[:500]})

    return {
        "snippet": "\n\n---\n\n".join(blocks) if blocks else "No content available.",
        "results": structured,
    }


def _image_search(query: str, num_results: int, timelimit) -> dict:
    from ddgs import DDGS

    with DDGS() as ddgs:
        raw = list(ddgs.images(
            query,
            max_results=num_results,
            timelimit=timelimit,
        ))

    if not raw:
        return {"results": [], "snippet": "No image results found."}

    # Prefer .jpeg URLs over .jpg — sort so .jpeg comes first
    def _ext_priority(r):
        url = (r.get("image") or "").lower().split("?")[0]
        if url.endswith(".jpeg"):
            return 0
        if url.endswith(".png"):
            return 1
        if url.endswith(".webp"):
            return 2
        return 3  # .jpg and everything else last

    raw.sort(key=_ext_priority)

    lines = []
    structured = []
    for r in raw:
        image_url = r.get("image", "")
        title     = r.get("title", "")
        width     = r.get("width", "?")
        height    = r.get("height", "?")
        source    = r.get("url", "")
        if image_url:
            lines.append(f"- {title} ({width}×{height})\n  Image URL: {image_url}\n  Source: {source}")
            structured.append({
                "title":     title,
                "image_url": image_url,
                "width":     width,
                "height":    height,
                "source":    source,
            })

    snippet = (
        "Direct image URLs (pass image_url to download_file):\n\n"
        + "\n\n".join(lines)
        if lines else "No image results found."
    )

    return {"snippet": snippet, "results": structured}
