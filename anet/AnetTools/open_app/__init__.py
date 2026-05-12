# Required pip packages (install before use):
#   pip install pywinauto pyautogui Pillow
#
# pywinauto  — window finding, focus, UI element clicking, text reading
# pyautogui  — keyboard shortcuts, typing, screenshots
# Pillow     — screenshot encoding (pyautogui dependency)

import asyncio
import base64
import ctypes
import io
import os
import re
import subprocess
import sys
import time

# ── SCHEMA ────────────────────────────────────────────────────────────────────
# Single function with an "action" enum so the LLM picks the right operation.
# All parameters beyond "action" are optional at the schema level;
# required-per-action validation happens inside run().

SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_app",
        "description": (
            "Full Windows desktop control tool. Supports launching applications, "
            "searching for files and folders on disk, opening any path in its default "
            "handler (File Explorer for folders), finding and focusing windows, typing text, "
            "clicking UI elements, sending keyboard shortcuts, taking screenshots, and "
            "reading visible text from any window. "
            "To find a folder and open it: first use find_path, then open_path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "find_path",
                        "open_path",
                        "launch_and_type",
                        "open_app",
                        "type_text",
                        "find_window",
                        "focus_window",
                        "click_element",
                        "keyboard_shortcut",
                        "take_screenshot",
                        "read_screen_text",
                    ],
                    "description": (
                        "Action to perform.\n"
                        "find_path: search the filesystem for a file or folder by name.\n"
                        "open_path: open any file or folder with its default handler (explorer for folders).\n"
                        "launch_and_type: open an app and type text in one call.\n"
                        "open_app: launch a named application.\n"
                        "type_text / find_window / focus_window / click_element / keyboard_shortcut / "
                        "take_screenshot / read_screen_text: standard desktop control."
                    ),
                },
                # ── find_path params ──────────────────────────────────────
                "name": {
                    "type": "string",
                    "description": (
                        "File or folder name to search for (case-insensitive, partial match). "
                        "Used by: find_path."
                    ),
                },
                "search_from": {
                    "type": "string",
                    "description": (
                        "Root directory to start the search from. Defaults to C:\\. "
                        "Used by: find_path."
                    ),
                },
                "path_type": {
                    "type": "string",
                    "enum": ["file", "folder", "any"],
                    "description": (
                        "Restrict results to files, folders, or both. Defaults to 'any'. "
                        "Used by: find_path."
                    ),
                },
                # ── open_path params ──────────────────────────────────────
                "path": {
                    "type": "string",
                    "description": (
                        "Absolute path to open with the OS default handler. "
                        "Folders open in File Explorer, files open in their default app. "
                        "Used by: open_path."
                    ),
                },
                # ── existing params ───────────────────────────────────────
                "app_name": {
                    "type": "string",
                    "description": (
                        "Application name to launch. "
                        "Used by: open_app, launch_and_type."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": (
                        "Window title or partial title (case-insensitive substring match). "
                        "Used by: find_window, focus_window."
                    ),
                },
                "window_title": {
                    "type": "string",
                    "description": (
                        "Window to target, identified by a partial title. "
                        "Used by: type_text (optional), click_element (required unless coords only), "
                        "read_screen_text (optional — defaults to active window)."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": (
                        "Text to type. Supports Unicode. "
                        "Used by: type_text, launch_and_type."
                    ),
                },
                "element_title": {
                    "type": "string",
                    "description": (
                        "Accessible name (AutomationId or visible label) of the UI element to click. "
                        "Used by: click_element."
                    ),
                },
                "element_type": {
                    "type": "string",
                    "description": (
                        "Control type of the target element e.g. 'Button', 'Edit', 'MenuItem'. "
                        "Used by: click_element."
                    ),
                },
                "coords": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": (
                        "Screen pixel coordinates [x, y] to click. "
                        "Used by: click_element as primary or fallback."
                    ),
                },
                "keys": {
                    "type": "string",
                    "description": (
                        "Key combination, parts joined by '+'. "
                        "Examples: 'ctrl+s', 'alt+f4', 'win+d'. "
                        "Used by: keyboard_shortcut."
                    ),
                },
                "save_path": {
                    "type": "string",
                    "description": (
                        "Absolute file path to save the screenshot PNG. "
                        "If omitted, the image is returned as base64. "
                        "Used by: take_screenshot."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}

# ── Required parameters per action ────────────────────────────────────────────
_REQUIRED: dict[str, list[str]] = {
    "find_path":         ["name"],
    "open_path":         ["path"],
    "launch_and_type":   ["app_name", "text"],
    "open_app":          ["app_name"],
    "find_window":       ["title"],
    "focus_window":      ["title"],
    "type_text":         ["text"],
    "click_element":     [],   # validated inside handler
    "keyboard_shortcut": ["keys"],
    "take_screenshot":   [],
    "read_screen_text":  [],
}

# ── Common app name aliases ────────────────────────────────────────────────────
# Maps human-readable names (lower) → executable name Windows can launch.
_APP_ALIASES: dict[str, str] = {
    "file explorer":    "explorer",
    "explorer":         "explorer",
    "command prompt":   "cmd",
    "terminal":         "cmd",
    "calculator":       "calc",
    "paint":            "mspaint",
    "wordpad":          "wordpad",
    "task manager":     "taskmgr",
    "control panel":    "control",
    "registry editor":  "regedit",
    "snipping tool":    "SnippingTool",
    "notepad":          "notepad",
    "chrome":           "chrome",
    "edge":             "msedge",
    "firefox":          "firefox",
}

# Directories skipped during find_path to avoid permission errors and huge trees
_SKIP_DIRS: set[str] = {
    "Windows", "System32", "SysWOW64", "WinSxS",
    "$Recycle.Bin", "$WINDOWS.~BT",
    "node_modules", "__pycache__", ".git", ".svn",
}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _find_win(title: str):
    """Return the first window whose title contains `title` (case-insensitive)."""
    pattern = re.compile(re.escape(title), re.IGNORECASE)
    for w in _desktop().windows():
        if pattern.search(w.window_text()):
            return w
    raise RuntimeError(f"No open window found with title containing '{title}'")


def _active_window():
    """Return the currently focused window via Win32 API."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return _desktop().window(handle=hwnd)


def _clipboard_paste(text: str) -> None:
    """Write text to the Windows clipboard via ctypes, then Ctrl+V.
    Handles Unicode correctly without needing pyperclip."""
    import pyautogui

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    encoded = (text + "\0").encode("utf-16-le")

    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32

    # Fix for 64-bit Windows: default restype is c_int (32-bit), which truncates
    # 64-bit heap pointers returned by GlobalAlloc/GlobalLock → access violation.
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    opened = u32.OpenClipboard(None)
    if not opened:
        raise RuntimeError("OpenClipboard failed")
    try:
        u32.EmptyClipboard()
        h_mem = k32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h_mem:
            raise RuntimeError("GlobalAlloc failed — out of memory?")
        p_mem = k32.GlobalLock(h_mem)
        if not p_mem:
            raise RuntimeError("GlobalLock failed")
        try:
            ctypes.memmove(p_mem, encoded, len(encoded))
        finally:
            k32.GlobalUnlock(h_mem)
        u32.SetClipboardData(CF_UNICODETEXT, h_mem)
    finally:
        u32.CloseClipboard()

    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")


# ── Action handlers (all synchronous — called via asyncio.to_thread) ──────────

def _do_find_path(inp: dict) -> dict:
    """
    Search the filesystem for files/folders whose name contains `name`.
    Checks the user's home directory first (fast), then broader paths.
    Skips known system/noise directories to stay fast and avoid permission errors.

    Returns: { "found": [...absolute paths...], "count": N, "truncated": bool }
    """
    name        = inp["name"]
    search_from = inp.get("search_from") or "C:\\"
    path_type   = inp.get("path_type", "any")   # "file" | "folder" | "any"
    max_results = 20

    pattern = re.compile(re.escape(name), re.IGNORECASE)
    found: list[str] = []

    # ── Priority 1: user home tree (fast, most likely location) ───────────────
    home = os.path.expanduser("~")
    priority_roots = [home] if home.startswith(search_from.rstrip("\\")) else []

    # ── Priority 2: full search_from tree ─────────────────────────────────────
    all_roots = priority_roots + ([search_from] if search_from not in priority_roots else [])

    seen: set[str] = set()

    for root_dir in all_roots:
        try:
            for dirpath, dirs, files in os.walk(root_dir, topdown=True, onerror=lambda e: None):
                # Prune skip-dirs in-place so os.walk doesn't descend into them
                dirs[:] = [
                    d for d in dirs
                    if d not in _SKIP_DIRS and not d.startswith(".")
                ]

                if path_type in ("folder", "any"):
                    for d in dirs:
                        if pattern.search(d):
                            p = os.path.join(dirpath, d)
                            if p not in seen:
                                seen.add(p)
                                found.append(p)
                                if len(found) >= max_results:
                                    return {"found": found, "count": len(found), "truncated": True}

                if path_type in ("file", "any"):
                    for f in files:
                        if pattern.search(f):
                            p = os.path.join(dirpath, f)
                            if p not in seen:
                                seen.add(p)
                                found.append(p)
                                if len(found) >= max_results:
                                    return {"found": found, "count": len(found), "truncated": True}
        except PermissionError:
            continue

    if not found:
        return {"found": [], "count": 0, "truncated": False,
                "message": f"No match for '{name}' under '{search_from}'."}
    return {"found": found, "count": len(found), "truncated": False}


def _do_open_path(inp: dict) -> dict:
    """
    Open a file or folder using the OS default handler.
    Folders open in File Explorer; files open in their default app.
    Uses os.startfile — identical to double-clicking in Explorer.
    """
    path = inp["path"]
    if not os.path.exists(path):
        return {"error": f"Path does not exist: '{path}'"}
    os.startfile(path)          # Windows only — opens with default handler
    time.sleep(0.8)             # brief wait so the window has time to appear
    return {"success": True, "message": f"Opened '{path}'", "path": path}


def _do_open_app(inp: dict) -> dict:
    """
    Launch an application without waiting for it to exit.

    If a window matching app_name is already open, focuses it instead of
    launching a new instance — prevents duplicate windows when the model
    calls open_app repeatedly.
    """
    app_name = inp["app_name"]
    # Resolve human-readable aliases to executable names
    app_name = _APP_ALIASES.get(app_name.lower(), app_name)
    try:
        # If app is already open, just focus it
        if sys.platform == "win32":
            try:
                win = _find_win(app_name)
                win.set_focus()
                return {"success": True, "message": f"{app_name} already open — focused it. Now call type_text to type."}
            except Exception:
                pass  # not found, proceed to launch

        if sys.platform == "win32":
            proc = subprocess.Popen(
                ["cmd", "/c", "start", "", app_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # Short pause to catch instant failures (bad app name etc.)
            time.sleep(0.3)
            if proc.poll() is not None and proc.returncode != 0:
                err = (proc.stderr.read() or b"").decode(errors="replace").strip()
                return {"error": f"Failed to open '{app_name}': {err or 'unknown error'}"}
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", "-a", app_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["xdg-open", app_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        # Wait for the app to finish loading before returning
        time.sleep(1.5)
        return {"success": True, "message": f"Opened {app_name}. App is ready."}
    except FileNotFoundError:
        return {"error": f"System launcher not found — cannot open '{app_name}'"}


def _do_find_window(inp: dict) -> dict:
    title = inp["title"]
    pattern = re.compile(re.escape(title), re.IGNORECASE)
    matches = [
        {"title": w.window_text(), "handle": w.handle}
        for w in _desktop().windows()
        if pattern.search(w.window_text())
    ]
    if not matches:
        return {"error": f"No open windows found matching '{title}'"}
    return {"success": True, "count": len(matches), "windows": matches}


def _do_focus_window(inp: dict) -> dict:
    win = _find_win(inp["title"])
    win.set_focus()
    time.sleep(0.2)
    return {"success": True, "message": f"Focused: '{win.window_text()}'"}


def _do_type_text(inp: dict) -> dict:
    import pyautogui

    text = inp["text"]
    window_title = inp.get("window_title")

    if window_title:
        win = _find_win(window_title)
        win.set_focus()
        time.sleep(0.3)

    # Use clipboard paste for full Unicode support; pyautogui.write is ASCII-only
    _clipboard_paste(text)
    return {"success": True, "message": f"Typed {len(text)} character(s)"}


def _do_click_element(inp: dict) -> dict:
    import pyautogui

    window_title = inp.get("window_title")
    element_title = inp.get("element_title")
    element_type = inp.get("element_type")
    coords = inp.get("coords")

    # Coords-only path (no window needed)
    if coords and not (element_title or element_type):
        pyautogui.click(coords[0], coords[1])
        return {"success": True, "message": f"Clicked screen coordinates {coords}"}

    if not window_title:
        return {"error": "'window_title' is required when clicking a UI element by name or type"}

    win = _find_win(window_title)
    win.set_focus()
    time.sleep(0.2)

    # Try UI element lookup first
    if element_title or element_type:
        kwargs: dict = {}
        if element_title:
            kwargs["title"] = element_title
        if element_type:
            kwargs["control_type"] = element_type
        try:
            win.child_window(**kwargs).click_input()
            label = element_title or element_type
            return {"success": True, "message": f"Clicked element '{label}' in '{win.window_text()}'"}
        except Exception as exc:
            if not coords:
                return {
                    "error": (
                        f"Element '{element_title or element_type}' not found in "
                        f"'{win.window_text()}': {exc}"
                    )
                }

    # Coordinate fallback
    if coords:
        pyautogui.click(coords[0], coords[1])
        return {"success": True, "message": f"Element not found by name; clicked at {coords}"}

    return {"error": "Provide element_title, element_type, and/or coords to click"}


def _do_keyboard_shortcut(inp: dict) -> dict:
    import pyautogui

    keys = inp["keys"]
    parts = [k.strip().lower() for k in keys.split("+")]
    pyautogui.hotkey(*parts)
    return {"success": True, "message": f"Sent shortcut: {keys}"}


def _do_take_screenshot(inp: dict) -> dict:
    import pyautogui

    screenshot = pyautogui.screenshot()
    save_path = inp.get("save_path")

    if save_path:
        screenshot.save(save_path)
        return {"success": True, "path": save_path, "size": list(screenshot.size)}

    buf = io.BytesIO()
    screenshot.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"success": True, "image_base64": b64, "size": list(screenshot.size)}


def _do_read_screen_text(inp: dict) -> dict:
    window_title = inp.get("window_title")

    if window_title:
        win = _find_win(window_title)
    else:
        win = _active_window()

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

    return {
        "success": True,
        "window": source,
        "text": "\n".join(texts),
        "element_count": len(texts),
    }


# ── Composite actions ─────────────────────────────────────────────────────────

def _do_launch_and_type(inp: dict) -> dict:
    """
    Open an app (or focus it if already running) then type text into it.
    Single tool call — no multi-step reasoning required from the model.
    """
    app_name = inp["app_name"]
    text = inp["text"]

    # ── Step 1: open or focus the app ─────────────────────────────────────────
    already_open = False
    if sys.platform == "win32":
        try:
            win = _find_win(app_name)
            win.set_focus()
            already_open = True
        except Exception:
            pass

    if not already_open:
        open_result = _do_open_app({"app_name": app_name})
        if "error" in open_result:
            return open_result
        # Extra wait to ensure the window is registered with the accessibility API
        time.sleep(1.5)

    # ── Step 2: find the window and type ──────────────────────────────────────
    # Retry a few times — the window title might not appear instantly
    win = None
    for attempt in range(6):
        try:
            win = _find_win(app_name)
            break
        except RuntimeError:
            time.sleep(0.5)

    if win is None:
        # Window never appeared — fall back to typing into whatever has focus
        _clipboard_paste(text)
        return {"success": True, "message": f"Opened {app_name} and typed text (window title not detected)."}

    win.set_focus()
    time.sleep(0.3)
    _clipboard_paste(text)
    return {"success": True, "message": f"Opened '{win.window_text()}' and typed: {text!r}"}


# ── Action inference ──────────────────────────────────────────────────────────
# Small models often call the tool without an 'action' param.
# Infer the most likely intent from whichever parameters they did provide.

def _infer_action(inp: dict) -> str | None:
    if inp.get("path"):
        return "open_path"
    if inp.get("name"):
        return "find_path"
    # Both app_name + text → composite open-and-type (most common omission)
    if inp.get("app_name") and inp.get("text"):
        return "launch_and_type"
    if inp.get("text"):
        return "type_text"
    if inp.get("keys"):
        return "keyboard_shortcut"
    if inp.get("app_name"):
        return "open_app"
    if inp.get("element_title") or inp.get("element_type") or inp.get("coords"):
        return "click_element"
    if inp.get("title"):
        return "focus_window"
    if inp.get("window_title"):
        return "read_screen_text"
    if inp.get("save_path") is not None:
        return "take_screenshot"
    return None


# ── Dispatch table ─────────────────────────────────────────────────────────────

_HANDLERS = {
    "find_path":         _do_find_path,
    "open_path":         _do_open_path,
    "launch_and_type":   _do_launch_and_type,
    "open_app":          _do_open_app,
    "find_window":       _do_find_window,
    "focus_window":      _do_focus_window,
    "type_text":         _do_type_text,
    "click_element":     _do_click_element,
    "keyboard_shortcut": _do_keyboard_shortcut,
    "take_screenshot":   _do_take_screenshot,
    "read_screen_text":  _do_read_screen_text,
}


# ── Public entry point ─────────────────────────────────────────────────────────

async def run(input: dict) -> dict:
    action = (input.get("action") or "").strip()

    if not action:
        # Infer action from whichever parameters the model provided
        action = _infer_action(input) or ""
        if not action:
            return {"error": f"'action' is required. Choose from: {', '.join(_HANDLERS)}"}
    if action not in _HANDLERS:
        return {"error": f"Unknown action '{action}'. Valid: {', '.join(_HANDLERS)}"}

    # Per-action required-param check
    missing = [p for p in _REQUIRED[action] if not input.get(p)]
    if missing:
        return {"error": f"Action '{action}' is missing required parameter(s): {', '.join(missing)}"}

    try:
        return await asyncio.to_thread(_HANDLERS[action], input)
    except ImportError as exc:
        return {
            "error": (
                f"Missing dependency: {exc}. "
                "Run: pip install pywinauto pyautogui Pillow"
            )
        }
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"{action} failed: {exc}"}
