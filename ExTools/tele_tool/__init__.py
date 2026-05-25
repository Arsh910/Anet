import mimetypes
import os
from pathlib import Path

import httpx

SCHEMA = {
    "type": "function",
    "function": {
        "name": "tele_tool",
        "description": (
            "Send a text message or a file to a Telegram chat via the Telegram Bot API. "
            "Pass file_path to send a file (photo, video, audio, or any document). "
            "Pass message for text-only, or as a caption alongside a file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "Text to send, or caption when also sending a file. "
                        "Markdown supported. Optional when file_path is provided."
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Absolute path to a local file to send. "
                        "Photos (.jpg/.png/.webp), videos (.mp4/.mov), "
                        "audio (.mp3/.m4a/.ogg), or any other file type."
                    ),
                },
                "chat_id": {
                    "type": "string",
                    "description": "Telegram chat ID — overrides TELEGRAM_CHAT_ID env var.",
                },
            },
            "required": [],
        },
    },
}


def _file_method(path: Path) -> tuple[str, str]:
    """Return (api_method, multipart_field) based on file extension."""
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "sendPhoto", "photo"
    if ext in {".mp4", ".mov", ".avi", ".mkv"}:
        return "sendVideo", "video"
    if ext in {".mp3", ".m4a", ".flac", ".ogg", ".wav"}:
        return "sendAudio", "audio"
    return "sendDocument", "document"


async def run(params: dict) -> dict:
    token     = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id   = params.get("chat_id") or os.getenv("TELEGRAM_CHAT_ID")
    message   = params.get("message", "")
    file_path = params.get("file_path", "")

    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN is not set in environment"}
    if not chat_id:
        return {"error": "TELEGRAM_CHAT_ID is not set — pass chat_id or set the env var"}
    if not message and not file_path:
        return {"error": "Provide 'message' (text to send) or 'file_path' (file to send)"}

    # Guard: file_path must be a local path, not a URL
    if file_path and file_path.startswith(("http://", "https://")):
        return {
            "error": (
                "file_path must be a local file path, not a URL. "
                "The file must be downloaded first (use research_agent / download_file), "
                "then pass the absolute local path here."
            )
        }

    base = f"https://api.telegram.org/bot{token}"

    try:
        async with httpx.AsyncClient(timeout=60) as client:

            if file_path:
                p = Path(file_path)
                if not p.exists():
                    return {"error": f"File not found: {file_path}"}
                if p.stat().st_size > 50 * 1024 * 1024:
                    return {"error": f"File too large for Telegram Bot API (max 50 MB): {p.name}"}

                method, field = _file_method(p)
                form: dict = {"chat_id": chat_id}
                if message:
                    form["caption"]    = message
                    form["parse_mode"] = "Markdown"

                mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"

                with p.open("rb") as fh:
                    resp = await client.post(
                        f"{base}/{method}",
                        data=form,
                        files={field: (p.name, fh, mime)},
                    )
                result = resp.json()

                if not result.get("ok") and method != "sendDocument":
                    with p.open("rb") as fh:
                        resp = await client.post(
                            f"{base}/sendDocument",
                            data=form,
                            files={"document": (p.name, fh, mime)},
                        )
                    result = resp.json()

            else:
                resp = await client.post(f"{base}/sendMessage", json={
                    "chat_id":    chat_id,
                    "text":       message,
                    "parse_mode": "Markdown",
                })
                result = resp.json()

    except Exception as exc:
        err_msg = str(exc) or type(exc).__name__
        return {"error": f"Network error sending to Telegram: {err_msg}"}

    if result.get("ok"):
        msg_id = result["result"]["message_id"]
        if file_path:
            return {"result": f"File sent: {Path(file_path).name} (message_id: {msg_id})"}
        return {"result": f"Message sent (message_id: {msg_id})"}

    return {"error": f"Telegram API error: {result.get('description', 'unknown')}"}
