"""
web_fetch — fetch a URL and read its content as clean markdown.

Complements web_search (which returns snippets) and download_file (which saves
binaries). web_fetch pulls a page/doc INTO context: it downloads the URL,
strips boilerplate (scripts, nav, footers), and converts the main content to
readable markdown so the agent can actually read documentation, articles, API
references, raw files, etc.

Uses httpx (async) + BeautifulSoup/lxml — no new dependencies.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

import httpx

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch a web page or text document by URL and read its content as clean "
            "markdown. Use this AFTER web_search to actually read a result, or whenever "
            "you have a URL whose content you need (docs, articles, API references, raw "
            "files on GitHub, changelogs). For binary files (images, pdf, zip, video) "
            "use download_file instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch. http(s) only.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters of content to return (default 12000). Output is truncated past this with a note.",
                },
            },
            "required": ["url"],
        },
    },
}

_DEFAULT_MAX_CHARS = 12000
_HARD_MAX_CHARS    = 40000
_MAX_BYTES         = 4 * 1024 * 1024   # don't pull more than 4 MB
_TIMEOUT           = 20.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Anet/1.0"
)

# Tags whose entire subtree is boilerplate / non-content.
_DROP_TAGS = {
    "script", "style", "noscript", "svg", "iframe", "form", "nav",
    "header", "footer", "aside", "button", "input", "select", "template",
}


def _is_blocked_host(host: str) -> bool:
    """Basic SSRF guard — refuse localhost and private/link-local addresses."""
    if not host:
        return True
    h = host.lower()
    if h in ("localhost", "0.0.0.0", "broadcasthost") or h.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False   # a normal hostname — allowed


async def run(input: dict) -> dict:
    url = (input.get("url") or "").strip()
    max_chars = input.get("max_chars")
    try:
        max_chars = min(int(max_chars), _HARD_MAX_CHARS) if max_chars else _DEFAULT_MAX_CHARS
    except (TypeError, ValueError):
        max_chars = _DEFAULT_MAX_CHARS

    if not url:
        return {"error": "url is required"}
    # Reject any explicit non-http(s) scheme (file://, ftp://, gopher://, …).
    # Only prepend https:// when no scheme is present at all (e.g. "example.com").
    if "://" in url:
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            return {"error": f"Unsupported scheme '{scheme}'. Only http/https are allowed."}
    else:
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": f"Unsupported scheme '{parsed.scheme}'. Only http/https are allowed."}
    if _is_blocked_host(parsed.hostname or ""):
        return {"error": "Refusing to fetch a local/private address."}

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_TIMEOUT, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"error": f"Fetch failed: {exc}"}

    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code} fetching {url}"}

    content_type = (resp.headers.get("content-type") or "").lower()
    raw = resp.content[:_MAX_BYTES]

    # Binary / non-readable content → point at download_file.
    if any(b in content_type for b in (
        "application/pdf", "image/", "video/", "audio/", "application/zip",
        "application/octet-stream", "font/",
    )):
        return {
            "error": (
                f"'{content_type}' is binary content, not readable text. "
                f"Use download_file to save it instead."
            )
        }

    is_html = "html" in content_type or (not content_type and b"<html" in raw[:2000].lower())

    if is_html:
        title, body = _html_to_markdown(raw, resp.encoding or "utf-8")
    else:
        title = ""
        body = raw.decode(resp.encoding or "utf-8", errors="replace")
        body = _collapse_blank_lines(body)

    body = body.strip()
    if not body:
        return {"error": f"No readable text content found at {url}"}

    truncated = len(body) > max_chars
    if truncated:
        body = body[:max_chars].rstrip() + (
            f"\n\n[... truncated at {max_chars} chars — refetch with a larger "
            f"max_chars, or search within the page for what you need]"
        )

    out = {
        "result": body,
        "final_url": str(resp.url),
        "truncated": truncated,
        "chars": len(body),
    }
    if title:
        out["title"] = title
    return out


# ── HTML → markdown ─────────────────────────────────────────────────────────────

def _html_to_markdown(raw: bytes, encoding: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        soup = BeautifulSoup(raw, "html.parser")

    title = (soup.title.get_text(strip=True) if soup.title else "")

    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, str) and s.strip().startswith("<!--")):
        c.extract()

    # Prefer the main content region when present.
    root = soup.find("article") or soup.find("main") or soup.body or soup
    md = _serialize(root)
    return title, _collapse_blank_lines(md)


_BLOCK = {"p", "div", "section", "article", "ul", "ol", "table", "tr", "blockquote", "pre"}


def _serialize(node) -> str:
    """Walk the DOM into a compact markdown string."""
    from bs4 import NavigableString, Tag

    parts: list[str] = []

    def walk(el, depth=0):
        for child in getattr(el, "children", []):
            if isinstance(child, NavigableString):
                text = re.sub(r"\s+", " ", str(child))
                if text.strip():
                    parts.append(text)
                continue
            if not isinstance(child, Tag):
                continue

            name = child.name
            if name in _DROP_TAGS:
                continue

            if re.fullmatch(r"h[1-6]", name or ""):
                level = int(name[1])
                parts.append(f"\n\n{'#' * level} {child.get_text(' ', strip=True)}\n")
            elif name == "a":
                text = child.get_text(" ", strip=True)
                href = child.get("href", "")
                if text and href and href not in ("#",) and not href.startswith("javascript:"):
                    parts.append(f"[{text}]({href})")
                elif text:
                    parts.append(text)
            elif name in ("strong", "b"):
                parts.append(f"**{child.get_text(' ', strip=True)}**")
            elif name in ("em", "i"):
                parts.append(f"*{child.get_text(' ', strip=True)}*")
            elif name == "code" and child.parent.name != "pre":
                parts.append(f"`{child.get_text('', strip=True)}`")
            elif name == "pre":
                code = child.get_text("", strip=False).strip("\n")
                parts.append(f"\n\n```\n{code}\n```\n")
            elif name == "li":
                parts.append(f"\n- {child.get_text(' ', strip=True)}")
            elif name == "br":
                parts.append("\n")
            elif name in ("hr",):
                parts.append("\n\n---\n")
            elif name == "blockquote":
                parts.append(f"\n\n> {child.get_text(' ', strip=True)}\n")
            elif name in _BLOCK:
                walk(child, depth + 1)
                parts.append("\n\n")
            else:
                walk(child, depth + 1)

    walk(node)
    return "".join(parts)


def _collapse_blank_lines(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text
