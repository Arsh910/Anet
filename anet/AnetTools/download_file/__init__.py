import os
from pathlib import Path

import httpx
from PIL import Image

_ANET_FILES_DIR = Path(__file__).parents[3] / "anet_files"

# Many hosts (Wikimedia in particular) return 403 to requests with no/default
# User-Agent. Send a normal browser UA so direct file URLs and Commons
# Special:FilePath redirects download cleanly.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 ANet/1.0"
    )
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "download_file",
        "description": (
            "Download a file from a URL to local disk. "
            "Returns the local path. For images, also returns width, height, size_kb, and format "
            "so the manager can warn the user if the image is too small for 3D generation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to download",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional filename to save as. Auto-detected from URL if omitted.",
                },
            },
            "required": ["url"],
        },
    },
}


async def run(input: dict) -> dict:
    url      = (input.get("url") or "").strip()
    filename = (input.get("filename") or "").strip()
    agent    = (input.get("_agent_name") or "research_agent").strip()

    if not url:
        return {"error": "url is required"}

    # NOTE: the directory is created lazily — only right before we actually write
    # bytes (see below) — so a failed download never leaves an empty folder behind.
    downloads_dir = _ANET_FILES_DIR / agent

    # Derive filename from URL if not provided
    if not filename:
        filename = url.split("?")[0].rstrip("/").split("/")[-1]
        if not filename or "." not in filename:
            filename = "download.jpg"

    dest = downloads_dir / filename

    # Avoid overwriting existing files
    counter = 1
    while dest.exists():
        dest = downloads_dir / f"{dest.stem}_{counter}{dest.suffix}"
        counter += 1

    # Content-Type → correct extension mapping
    _CT_EXT = {
        "image/jpeg":   ".jpeg",
        "image/jpg":    ".jpeg",
        "image/png":    ".png",
        "image/webp":   ".webp",
        "image/gif":    ".gif",
        "image/bmp":    ".bmp",
        "image/tiff":   ".tiff",
    }

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            # Use Content-Type to pick the right extension — CDNs often serve
            # WebP at a .jpg URL, which produces unreadable files if we trust the URL.
            ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            real_ext = _CT_EXT.get(ct)
            if real_ext and dest.suffix.lower() != real_ext:
                correct_dest = dest.with_suffix(real_ext)
                counter = 1
                while correct_dest.exists():
                    correct_dest = downloads_dir / f"{correct_dest.stem}_{counter}{real_ext}"
                    counter += 1
                dest = correct_dest

            # Reject HTML responses — CDN returned an error/redirect page
            content = resp.content
            if content[:15].lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
                return {"error": f"URL returned an HTML page instead of a file — the server blocked the download or the URL is a webpage, not a direct file link: {url}"}

            # Create the sandbox folder only now that we have real bytes to write —
            # a failed/blocked download leaves no empty anet_files/<agent>/ behind.
            downloads_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
    except httpx.TimeoutException:
        return {"error": "Download timed out"}
    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code} downloading {url}"}
    except Exception as exc:
        return {"error": f"Download failed: {exc}"}

    result: dict = {
        "path":    str(dest),
        "size_kb": dest.stat().st_size // 1024,
    }

    # Image metadata — helps manager warn user about low-res references
    try:
        with Image.open(dest) as img:
            result["width"]  = img.width
            result["height"] = img.height
            result["format"] = img.format
            if img.width < 256 or img.height < 256:
                result["warning"] = (
                    f"Image is only {img.width}×{img.height}px — "
                    "may be too small for good 3D reconstruction."
                )
    except Exception:
        pass  # Not an image or unreadable — path is still returned

    return result
