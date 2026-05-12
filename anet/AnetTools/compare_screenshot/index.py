# pip install httpx pillow pyautogui

import asyncio
import base64
import io
import json
import os
import re

import httpx

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = {
    "type": "function",
    "function": {
        "name": "compare-screenshot",
        "description": (
            "Takes and compares screenshots to verify whether a desktop automation task "
            "produced a visible change on screen. "
            "Use 'take' to capture a screenshot before or after an action. "
            "Use 'compare' to ask a vision model whether the expected change occurred."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["take", "compare"],
                    "description": "'take' to capture a screenshot; 'compare' to diff two screenshots.",
                },
                "save_path": {
                    "type": "string",
                    "description": "Absolute file path to save the captured PNG. Used by: take.",
                },
                "before_path": {
                    "type": "string",
                    "description": "Absolute path to the before screenshot PNG. Used by: compare.",
                },
                "after_path": {
                    "type": "string",
                    "description": "Absolute path to the after screenshot PNG. Used by: compare.",
                },
                "expected_change": {
                    "type": "string",
                    "description": (
                        "Plain-English description of what should have changed on screen. "
                        "E.g. 'Notepad opened with the text Hello typed in it'. "
                        "Used by: compare."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}

# ── Config ────────────────────────────────────────────────────────────────────

_VISION_MODEL = "google/gemini-flash-1.5"
_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_REQUIRED: dict[str, list[str]] = {
    "take":    ["save_path"],
    "compare": ["before_path", "after_path", "expected_change"],
}


# ── Handlers ──────────────────────────────────────────────────────────────────

def _do_take(inp: dict) -> dict:
    """
    Capture the current screen and save it to save_path.
    Returns: { "success": true, "path": "..." }
    """
    import pyautogui

    save_path = inp["save_path"]
    screenshot = pyautogui.screenshot()
    screenshot.save(save_path)
    return {"success": True, "path": save_path}


def _img_to_b64(path: str) -> str:
    from PIL import Image

    with Image.open(path) as img:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{[\s\S]+\}", text)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    return {"error": f"Could not parse JSON from model response: {text!r}"}


def _do_compare(inp: dict) -> dict:
    """
    Send before + after screenshots to a vision model and ask whether
    the expected change occurred.
    Returns: { "changed": bool, "confidence": "high"|"medium"|"low", "observation": "..." }
    """
    before_path     = inp["before_path"]
    after_path      = inp["after_path"]
    expected_change = inp["expected_change"]

    before_b64 = _img_to_b64(before_path)
    after_b64  = _img_to_b64(after_path)

    prompt_text = (
        f"Expected change: {expected_change}\n\n"
        "The FIRST image is the BEFORE screenshot. "
        "The SECOND image is the AFTER screenshot.\n\n"
        "Did the expected change actually happen between these two screenshots?\n"
        "Respond with ONLY a JSON object — no prose, no markdown:\n"
        '{"changed": true|false, '
        '"confidence": "high"|"medium"|"low", '
        '"observation": "<one sentence describing what you see>"}'
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text",      "text": prompt_text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{before_b64}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{after_b64}"}},
            ],
        }
    ]

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    resp = httpx.post(
        _API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": _VISION_MODEL, "messages": messages, "temperature": 0},
        timeout=60,
    )
    resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"] or ""
    parsed = _extract_json(raw)

    if "changed" not in parsed:
        parsed["changed"] = False
    if "confidence" not in parsed or parsed["confidence"] not in ("high", "medium", "low"):
        parsed["confidence"] = "low"
    parsed.setdefault("observation", "No observation returned.")

    return parsed


# ── Dispatch ──────────────────────────────────────────────────────────────────

_HANDLERS = {
    "take":    _do_take,
    "compare": _do_compare,
}


# ── Public entry point ────────────────────────────────────────────────────────

async def run(input: dict) -> dict:
    action = (input.get("action") or "").strip()
    if not action:
        return {"error": f"'action' is required. Choose from: {', '.join(_HANDLERS)}"}
    if action not in _HANDLERS:
        return {"error": f"Unknown action '{action}'. Valid: {', '.join(_HANDLERS)}"}

    missing = [p for p in _REQUIRED.get(action, []) if not input.get(p)]
    if missing:
        return {"error": f"Action '{action}' is missing required parameter(s): {', '.join(missing)}"}

    try:
        return await asyncio.to_thread(_HANDLERS[action], input)
    except httpx.HTTPStatusError as exc:
        return {"error": f"Vision API error: {exc.response.status_code} — {exc.response.text[:200]}"}
    except ImportError as exc:
        return {"error": f"Missing dependency: {exc}. Run: pip install pillow pyautogui"}
    except FileNotFoundError as exc:
        return {"error": f"Screenshot file not found: {exc}"}
    except Exception as exc:
        return {"error": f"{action} failed: {exc}"}
