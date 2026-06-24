# pip install httpx pillow pyautogui pywinauto

import asyncio
import base64
import ctypes
import io
import json
import os
import re
import subprocess
import sys

import httpx

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = {
    "type": "function",
    "function": {
        "name": "checker",
        "description": (
            "All-in-one task verification tool. "
            "Combines LLM-based result classification and diagnosis with "
            "direct Windows state inspection (windows, processes, filesystem) "
            "and screenshot capture/comparison. "
            "Use the state-inspection actions first for desktop tasks — they "
            "return ground-truth facts from the OS, not visual guesses."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        # ── LLM-based ──────────────────────────────────────
                        "classify",
                        "diagnose",
                        # ── Windows state ──────────────────────────────────
                        "check_window",
                        "list_windows",
                        "check_process",
                        "read_window_text",
                        # ── Filesystem ────────────────────────────────────
                        "check_path",
                        # ── Screenshots ───────────────────────────────────
                        "take_screenshot",
                        "compare_screenshots",
                    ],
                    "description": (
                        "Action to perform.\n"
                        "LLM: classify (did the result meet criteria?), diagnose (what to change on retry).\n"
                        "Windows state: check_window, list_windows, check_process, read_window_text.\n"
                        "Filesystem: check_path.\n"
                        "Screenshots: take_screenshot, compare_screenshots."
                    ),
                },
                # ── LLM params ────────────────────────────────────────────
                "task": {
                    "type": "string",
                    "description": "The original task that was attempted. Used by: classify, diagnose.",
                },
                "result": {
                    "type": "string",
                    "description": "The output returned by the agent. Used by: classify, diagnose.",
                },
                "success_criteria": {
                    "type": "string",
                    "description": "What a successful result looks like in plain English. Used by: classify.",
                },
                "failure_reason": {
                    "type": "string",
                    "description": "Why classify returned failure or partial. Used by: diagnose.",
                },
                "attempt_number": {
                    "type": "integer",
                    "description": "Which attempt this is (1-based). Used by: diagnose.",
                },
                # ── Window / process params ───────────────────────────────
                "title": {
                    "type": "string",
                    "description": (
                        "Window title or partial title to look up (case-insensitive). "
                        "Used by: check_window, read_window_text."
                    ),
                },
                "process_name": {
                    "type": "string",
                    "description": (
                        "Executable name to check, e.g. 'explorer.exe', 'notepad.exe'. "
                        "Used by: check_process."
                    ),
                },
                # ── Filesystem params ─────────────────────────────────────
                "path": {
                    "type": "string",
                    "description": "Absolute path to check. Used by: check_path.",
                },
                # ── Screenshot params ─────────────────────────────────────
                "save_path": {
                    "type": "string",
                    "description": "Absolute path to save the PNG. Used by: take_screenshot.",
                },
                "before_path": {
                    "type": "string",
                    "description": "Path to the before PNG. Used by: compare_screenshots.",
                },
                "after_path": {
                    "type": "string",
                    "description": "Path to the after PNG. Used by: compare_screenshots.",
                },
                "expected_change": {
                    "type": "string",
                    "description": (
                        "Plain-English description of what should have changed. "
                        "Used by: compare_screenshots."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}

# ── Config ────────────────────────────────────────────────────────────────────

# Verification is the highest-leverage role in a multi-agent system (see TRINITY /
# Conductor): a strong verifier that hunts for what's WRONG lifts the whole pipeline.
# Default stays small/cheap; point ANET_CHECKER_MODEL at a stronger model to improve
# verification quality.
_CHECKER_MODEL = os.environ.get("ANET_CHECKER_MODEL", "meta-llama/llama-3.1-8b-instruct")
_VISION_MODEL  = "google/gemini-flash-1.5"
_API_URL       = "https://openrouter.ai/api/v1/chat/completions"

_REQUIRED: dict[str, list[str]] = {
    "classify":           ["task", "result", "success_criteria"],
    "diagnose":           ["task", "result", "failure_reason"],
    "check_window":       ["title"],
    "list_windows":       [],
    "check_process":      ["process_name"],
    "read_window_text":   [],   # title is optional — defaults to active window
    "check_path":         ["path"],
    "take_screenshot":    ["save_path"],
    "compare_screenshots":["before_path", "after_path", "expected_change"],
}


# ── Shared helpers ────────────────────────────────────────────────────────────

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


def _llm_post(messages: list[dict], model: str, vision: bool = False) -> str:
    """Synchronous OpenRouter POST — wrapped in asyncio.to_thread by callers."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    body: dict = {"model": model, "messages": messages, "temperature": 0}
    if not vision:
        body["response_format"] = {"type": "json_object"}
    resp = httpx.post(
        _API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""


def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _find_win(title: str):
    pattern = re.compile(re.escape(title), re.IGNORECASE)
    for w in _desktop().windows():
        if pattern.search(w.window_text()):
            return w
    return None


# ── LLM-based handlers ────────────────────────────────────────────────────────

def _do_classify(inp: dict) -> dict:
    """Rigorous verification (the Verifier role). Don't rubber-stamp — actively hunt
    for what's wrong or missing, and return the fix together with the verdict (so the
    next attempt is grounded in the exact problems found)."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a rigorous VERIFIER for an AI task executor. Your job is to find "
                "what is WRONG, MISSING, or UNVERIFIED — NOT to rubber-stamp. A result that "
                "looks plausible but is incomplete, incorrect, only partially done, or "
                "fabricated must NOT pass. Check that the result is:\n"
                "  • CORRECT — no factual or logical errors;\n"
                "  • COMPLETE — every part of the task is done, including obvious edge cases "
                "and requirements that are clearly implied even if not spelled out;\n"
                "  • RESPONSIVE — it actually answers/does what was asked, not something near it;\n"
                "  • GROUNDED — claims are backed by the result, not invented.\n"
                "Only return success when you genuinely cannot find a real problem. "
                "Respond with ONLY a valid JSON object — no prose, no markdown."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Task: {inp['task']}\n\n"
                f"Result produced:\n{inp['result']}\n\n"
                f"Success criteria: {inp['success_criteria']}\n\n"
                "Verify strictly. List the specific problems you find (empty list if none), "
                "then give the SINGLE most important concrete fix for the next attempt "
                "(empty string if it already passes).\n"
                'Respond with exactly: {"status": "success"|"partial"|"failure", '
                '"reason": "<brief explanation of the verdict>", '
                '"issues": ["<specific problem>", ...], '
                '"adjustment": "<the one concrete fix for the next attempt, or empty>"}'
            ),
        },
    ]
    raw    = _llm_post(messages, _CHECKER_MODEL)
    parsed = _extract_json(raw)
    if parsed.get("status") not in ("success", "failure", "partial"):
        parsed["status"] = "failure"
        parsed.setdefault("reason", "Could not determine status from model response.")
    parsed.setdefault("issues", [])
    parsed.setdefault("adjustment", "")
    return parsed


def _do_diagnose(inp: dict) -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "You are a debugging agent for an AI task executor. "
                "Respond with ONLY a valid JSON object — no prose, no markdown."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Task: {inp['task']}\n\n"
                f"Result: {inp['result']}\n\n"
                f"Failure reason: {inp['failure_reason']}\n\n"
                f"Attempt number: {inp.get('attempt_number', 1)}\n\n"
                "What specific change should be made on the next attempt?\n"
                'Respond with exactly: {"adjustment": "<concrete change>", '
                '"use_different_tool": true|false, "suggested_tool": "<name or null>"}'
            ),
        },
    ]
    raw    = _llm_post(messages, _CHECKER_MODEL)
    parsed = _extract_json(raw)
    parsed.setdefault("adjustment", "Retry the task with no changes.")
    parsed.setdefault("use_different_tool", False)
    parsed.setdefault("suggested_tool", None)
    return parsed


# ── Windows state handlers ────────────────────────────────────────────────────

def _do_check_window(inp: dict) -> dict:
    """
    Check whether a window whose title contains `title` is currently open.
    Returns ground-truth OS state — no LLM involved.
    """
    title = inp["title"]
    win   = _find_win(title)
    if win is None:
        return {"exists": False, "title": None, "minimized": None}

    try:
        rect      = win.rectangle()
        minimized = (rect.width() == 0 and rect.height() == 0)
    except Exception:
        minimized = False

    return {"exists": True, "title": win.window_text(), "minimized": minimized}


def _do_list_windows(inp: dict) -> dict:
    """Return titles and process names of all visible top-level windows."""
    results = []
    for w in _desktop().windows():
        text = w.window_text().strip()
        if not text:
            continue
        try:
            proc = w.element_info.process_id
        except Exception:
            proc = None
        results.append({"title": text, "process_id": proc})
    return {"windows": results, "count": len(results)}


def _do_check_process(inp: dict) -> dict:
    """
    Check whether a process is running using Windows `tasklist`.
    No extra dependency — tasklist.exe ships with every Windows version.
    """
    name = inp["process_name"].lower()
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        running = name in out.lower()
        # Extract PID from first matching line if present
        pid = None
        for line in out.splitlines():
            if name in line.lower():
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                    except ValueError:
                        pass
                break
        return {"running": running, "process_name": inp["process_name"], "pid": pid}
    except Exception as exc:
        return {"error": f"check_process failed: {exc}"}


def _do_read_window_text(inp: dict) -> dict:
    """Read all visible text from a window (or the active window if no title given)."""
    title = inp.get("title")
    if title:
        win = _find_win(title)
        if win is None:
            return {"error": f"No window found with title containing '{title}'"}
    else:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        win  = _desktop().window(handle=hwnd)

    source = win.window_text()
    texts: list[str] = []
    try:
        for ctrl in win.descendants():
            t = ctrl.window_text().strip()
            if t:
                texts.append(t)
    except Exception:
        pass

    if not texts:
        return {"error": f"No readable text found in window '{source}'"}

    return {"window": source, "text": "\n".join(texts), "element_count": len(texts)}


# ── Filesystem handler ────────────────────────────────────────────────────────

def _do_check_path(inp: dict) -> dict:
    """Check whether a file or folder exists. Pure OS call — no LLM."""
    path = inp["path"]
    exists = os.path.exists(path)
    if not exists:
        return {"exists": False, "path": path, "type": None}
    kind = "directory" if os.path.isdir(path) else "file"
    return {"exists": True, "path": path, "type": kind}


# ── Screenshot handlers ───────────────────────────────────────────────────────

def _do_take_screenshot(inp: dict) -> dict:
    import pyautogui
    save_path  = inp["save_path"]
    screenshot = pyautogui.screenshot()
    screenshot.save(save_path)
    return {"success": True, "path": save_path}


def _img_to_b64(path: str) -> str:
    from PIL import Image
    with Image.open(path) as img:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()


def _do_compare_screenshots(inp: dict) -> dict:
    before_b64 = _img_to_b64(inp["before_path"])
    after_b64  = _img_to_b64(inp["after_path"])
    prompt = (
        f"Expected change: {inp['expected_change']}\n\n"
        "The FIRST image is BEFORE, the SECOND is AFTER.\n"
        "Did the expected change happen?\n"
        'Respond with ONLY: {"changed": true|false, '
        '"confidence": "high"|"medium"|"low", "observation": "<one sentence>"}'
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text",      "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{before_b64}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{after_b64}"}},
            ],
        }
    ]
    raw    = _llm_post(messages, _VISION_MODEL, vision=True)
    parsed = _extract_json(raw)
    parsed.setdefault("changed", False)
    if parsed.get("confidence") not in ("high", "medium", "low"):
        parsed["confidence"] = "low"
    parsed.setdefault("observation", "No observation returned.")
    return parsed


# ── Dispatch ──────────────────────────────────────────────────────────────────

_HANDLERS = {
    "classify":            _do_classify,
    "diagnose":            _do_diagnose,
    "check_window":        _do_check_window,
    "list_windows":        _do_list_windows,
    "check_process":       _do_check_process,
    "read_window_text":    _do_read_window_text,
    "check_path":          _do_check_path,
    "take_screenshot":     _do_take_screenshot,
    "compare_screenshots": _do_compare_screenshots,
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
        return {"error": f"Action '{action}' is missing: {', '.join(missing)}"}

    try:
        return await asyncio.to_thread(_HANDLERS[action], input)
    except httpx.HTTPStatusError as exc:
        return {"error": f"API error {exc.response.status_code}: {exc.response.text[:200]}"}
    except ImportError as exc:
        return {"error": f"Missing dependency: {exc}"}
    except Exception as exc:
        return {"error": f"{action} failed: {exc}"}
