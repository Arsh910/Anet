import os
from pathlib import Path

import httpx
from PIL import Image

_DOWNLOADS_DIR = Path(__file__).parents[3] / "downloads"

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

    if not url:
        return {"error": "url is required"}

    _DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    # Derive filename from URL if not provided
    if not filename:
        filename = url.split("?")[0].rstrip("/").split("/")[-1]
        if not filename or "." not in filename:
            filename = "download.jpg"

    dest = _DOWNLOADS_DIR / filename

    # Avoid overwriting existing files
    counter = 1
    while dest.exists():
        dest = _DOWNLOADS_DIR / f"{dest.stem}_{counter}{dest.suffix}"
        counter += 1

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
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
