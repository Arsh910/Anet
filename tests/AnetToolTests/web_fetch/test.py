"""Unit tests for the web_fetch tool. Offline — only the guard paths (no network)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.web_fetch import run, _is_blocked_host, _html_to_markdown


def _run(p): return asyncio.run(run(p))


def test_requires_url():
    r = _run({"url": "   "})
    assert "error" in r


def test_blocks_localhost_ssrf():
    r = _run({"url": "http://localhost:8000/secret"})
    assert "error" in r and "local/private" in r["error"]


def test_blocks_private_ip():
    assert _is_blocked_host("127.0.0.1")
    assert _is_blocked_host("10.0.0.5")
    assert _is_blocked_host("192.168.1.1")
    assert not _is_blocked_host("example.com")


def test_rejects_non_http_scheme():
    r = _run({"url": "ftp://example.com/file"})
    assert "error" in r and "scheme" in r["error"].lower()


def test_html_to_markdown_extracts_text_and_title():
    html = b"<html><head><title>Hi</title></head><body><script>x()</script>" \
           b"<h1>Heading</h1><p>Hello <a href='/x'>link</a></p></body></html>"
    title, md = _html_to_markdown(html, "utf-8")
    assert title == "Hi"
    assert "Heading" in md and "Hello" in md
    assert "x()" not in md            # script stripped
    assert "[link](/x)" in md          # link preserved as markdown


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: web_fetch")
