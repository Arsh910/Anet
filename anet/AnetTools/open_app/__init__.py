# Required pip packages:
#   All platforms:   pip install pyautogui Pillow
#   Windows only:    pip install pywinauto
#   Linux optional:  apt install xdotool wmctrl xclip

import asyncio
import base64
import io
import os
import re
import subprocess
import sys
import time

_IS_WIN   = sys.platform == "win32"
_IS_MAC   = sys.platform == "darwin"
_IS_LINUX = sys.platform.startswith("linux")

if _IS_WIN:
    import ctypes

# ── SCHEMA ────────────────────────────────────────────────────────────────────

SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_app",
        "description": (
            "Cross-platform desktop control tool (Windows, macOS, Linux). "
            "Supports launching applications, searching for files/folders on disk, "
            "opening any path with its default handler, finding/focusing windows, "
            "typing text, clicking UI elements, keyboard shortcuts, screenshots, "
            "and reading visible text from windows. "
            "Window management on Linux requires xdotool (apt install xdotool). "
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
                        "open_path: open any file or folder with its default handler.\n"
                        "launch_and_type: open an app and type text in one call.\n"
                        "open_app: launch a named application.\n"
                        "type_text / find_window / focus_window / click_element / keyboard_shortcut / "
                        "take_screenshot / read_screen_text: standard desktop control."
                    ),
                },
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
                        "Root directory to start the search from. "
                        "Defaults to C:\\ on Windows, home directory on macOS/Linux. "
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
                "path": {
                    "type": "string",
                    "description": (
                        "Absolute path to open with the OS default handler. "
                        "Folders open in the system file manager, files in their default app. "
                        "Used by: open_path."
                    ),
                },
                "app_name": {
                    "type": "string",
                    "description": "Application name to launch. Used by: open_app, launch_and_type.",
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
                        "Used by: type_text, click_element, read_screen_text."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Text to type. Supports Unicode. Used by: type_text, launch_and_type.",
                },
                "element_title": {
                    "type": "string",
                    "description": (
                        "Accessible name or visible label of the UI element to click. "
                        "Windows only. Used by: click_element."
                    ),
                },
                "element_type": {
                    "type": "string",
                    "description": (
                        "Control type of the target element e.g. 'Button', 'Edit', 'MenuItem'. "
                        "Windows only. Used by: click_element."
                    ),
                },
                "coords": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": (
                        "Screen pixel coordinates [x, y] to click. "
                        "Works on all platforms. Used by: click_element."
                    ),
                },
                "keys": {
                    "type": "string",
                    "description": (
                        "Key combination, parts joined by '+'. "
                        "Examples: 'ctrl+s', 'alt+f4', 'cmd+space'. "
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
    "click_element":     [],
    "keyboard_shortcut": ["keys"],
    "take_screenshot":   [],
    "read_screen_text":  [],
}

# ── App aliases per platform ───────────────────────────────────────────────────

_APP_ALIASES_WIN: dict[str, str] = {
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

_APP_ALIASES_MAC: dict[str, str] = {
    "terminal":           "Terminal",
    "finder":             "Finder",
    "safari":             "Safari",
    "chrome":             "Google Chrome",
    "firefox":            "Firefox",
    "calculator":         "Calculator",
    "notes":              "Notes",
    "text editor":        "TextEdit",
    "textedit":           "TextEdit",
    "preview":            "Preview",
    "activity monitor":   "Activity Monitor",
    "system preferences": "System Preferences",
    "settings":           "System Settings",
    "xcode":              "Xcode",
    "vscode":             "Visual Studio Code",
    "vs code":            "Visual Studio Code",
}

_APP_ALIASES_LINUX: dict[str, str] = {
    "terminal":       "gnome-terminal",
    "file manager":   "nautilus",
    "files":          "nautilus",
    "text editor":    "gedit",
    "chrome":         "google-chrome",
    "firefox":        "firefox",
    "calculator":     "gnome-calculator",
    "vscode":         "code",
    "vs code":        "code",
    "settings":       "gnome-control-center",
}

def _app_aliases() -> dict[str, str]:
    if _IS_MAC:   return _APP_ALIASES_MAC
    if _IS_LINUX: return _APP_ALIASES_LINUX
    return _APP_ALIASES_WIN


# Directories skipped during find_path to avoid permission errors and huge trees
_SKIP_DIRS: set[str] = {
    # Windows
    "Windows", "System32", "SysWOW64", "WinSxS",
    "$Recycle.Bin", "$WINDOWS.~BT",
    # Linux virtual filesystems
    "proc", "sys", "dev", "run",
    # Common noise
    "node_modules", "__pycache__", ".git", ".svn",
}


# ── Platform helpers ───────────────────────────────────────────────────────────

def _desktop():
    """pywinauto Desktop — Windows only."""
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _find_win(title: str):
    """Find a window by partial title — Windows only (pywinauto)."""
    pattern = re.compile(re.escape(title), re.IGNORECASE)
    for w in _desktop().windows():
        if pattern.search(w.window_text()):
            return w
    raise RuntimeError(f"No open window found with title containing '{title}'")


def _active_window_win():
    """Return the currently focused window via Win32 API — Windows only."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return _desktop().window(handle=hwnd)


def _clipboard_paste(text: str) -> None:
    """Write text to the clipboard and paste — cross-platform."""
    import pyautogui

    if _IS_WIN:
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE  = 0x0002
        encoded = (text + "\0").encode("utf-16-le")

        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32

        # Fix for 64-bit Windows: default restype is c_int (32-bit), which truncates
        # 64-bit heap pointers returned by GlobalAlloc/GlobalLock → access violation.
        k32.GlobalAlloc.restype   = ctypes.c_void_p
        k32.GlobalAlloc.argtypes  = [ctypes.c_uint, ctypes.c_size_t]
        k32.GlobalLock.restype    = ctypes.c_void_p
        k32.GlobalLock.argtypes   = [ctypes.c_void_p]
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        u32.SetClipboardData.restype  = ctypes.c_void_p
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

    elif _IS_MAC:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        time.sleep(0.05)
        pyautogui.hotkey("command", "v")

    else:  # Linux — try xclip, then xsel, then fall back to xdotool type
        for cmd in (
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=3)
                time.sleep(0.05)
                pyautogui.hotkey("ctrl", "v")
                return
            except FileNotFoundError:
                continue
        try:
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--delay", "20", text],
                check=True, timeout=30,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "Cannot type text: install xclip or xdotool  "
                "(apt install xclip  or  apt install xdotool)"
            )


# ── Action handlers ────────────────────────────────────────────────────────────

def _do_find_path(inp: dict) -> dict:
    name         = inp["name"]
    default_root = "C:\\" if _IS_WIN else os.path.expanduser("~")
    search_from  = inp.get("search_from") or default_root
    path_type    = inp.get("path_type", "any")
    max_results  = 20

    pattern = re.compile(re.escape(name), re.IGNORECASE)
    found: list[str] = []

    home = os.path.expanduser("~")
    norm_search = os.path.normpath(search_from)
    norm_home   = os.path.normpath(home)
    priority_roots = [home] if norm_home.startswith(norm_search) else []
    all_roots = priority_roots + ([search_from] if search_from not in priority_roots else [])
    seen: set[str] = set()

    for root_dir in all_roots:
        try:
            for dirpath, dirs, files in os.walk(root_dir, topdown=True, onerror=lambda e: None):
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
    path = inp["path"]
    if not os.path.exists(path):
        return {"error": f"Path does not exist: '{path}'"}
    if _IS_WIN:
        os.startfile(path)
    elif _IS_MAC:
        subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    return {"success": True, "message": f"Opened '{path}'", "path": path}


def _do_open_app(inp: dict) -> dict:
    app_name = inp["app_name"]
    app_name = _app_aliases().get(app_name.lower(), app_name)

    if _IS_WIN:
        try:
            win = _find_win(app_name)
            win.set_focus()
            return {"success": True, "message": f"{app_name} already open — focused it. Now call type_text to type."}
        except Exception:
            pass
        proc = subprocess.Popen(
            ["cmd", "/c", "start", "", app_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.3)
        if proc.poll() is not None and proc.returncode != 0:
            err = (proc.stderr.read() or b"").decode(errors="replace").strip()
            return {"error": f"Failed to open '{app_name}': {err or 'unknown error'}"}

    elif _IS_MAC:
        # `open -a` launches the app or brings it to front if already running
        result = subprocess.run(
            ["open", "-a", app_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip()
            return {"error": f"Failed to open '{app_name}': {err or 'app not found'}"}

    else:  # Linux
        # Try to activate an existing window first
        activated = False
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", app_name, "windowactivate"],
                capture_output=True, text=True, timeout=3,
            )
            activated = result.returncode == 0 and bool(result.stdout.strip())
        except FileNotFoundError:
            pass

        if not activated:
            try:
                result = subprocess.run(
                    ["wmctrl", "-a", app_name],
                    capture_output=True, timeout=3,
                )
                activated = result.returncode == 0
            except FileNotFoundError:
                pass

        if not activated:
            try:
                subprocess.Popen(
                    [app_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                return {
                    "error": (
                        f"Application '{app_name}' not found. "
                        "Install it or provide the exact binary name."
                    )
                }

    time.sleep(1.5)
    return {"success": True, "message": f"Opened {app_name}. App is ready."}


def _do_find_window(inp: dict) -> dict:
    title   = inp["title"]
    pattern = re.compile(re.escape(title), re.IGNORECASE)

    if _IS_WIN:
        matches = [
            {"title": w.window_text(), "handle": w.handle}
            for w in _desktop().windows()
            if pattern.search(w.window_text())
        ]
        if not matches:
            return {"error": f"No open windows found matching '{title}'"}
        return {"success": True, "count": len(matches), "windows": matches}

    elif _IS_MAC:
        script = f'''
set found to {{}}
tell application "System Events"
    repeat with proc in (every process whose background only is false)
        try
            repeat with win in (every window of proc)
                if name of win contains "{title}" then
                    set end of found to (name of proc & ": " & name of win)
                end if
            end repeat
        end try
    end repeat
end tell
return found
'''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            raw = result.stdout.strip()
            lines = [l.strip() for l in raw.split(",") if l.strip()] if raw else []
            if not lines:
                return {"error": f"No open windows found matching '{title}'"}
            return {"success": True, "count": len(lines), "windows": [{"title": l} for l in lines]}
        except FileNotFoundError:
            return {"error": "osascript not available"}

    else:  # Linux
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return {"error": f"No open windows found matching '{title}'"}
            wids = result.stdout.strip().split()
            windows = []
            for wid in wids:
                name_res = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, text=True, timeout=2,
                )
                name = name_res.stdout.strip()
                if pattern.search(name):
                    windows.append({"title": name, "id": wid})
            if not windows:
                return {"error": f"No open windows found matching '{title}'"}
            return {"success": True, "count": len(windows), "windows": windows}
        except FileNotFoundError:
            return {"error": "xdotool not found — install with: apt install xdotool"}


def _do_focus_window(inp: dict) -> dict:
    title = inp["title"]

    if _IS_WIN:
        win = _find_win(title)
        win.set_focus()
        time.sleep(0.2)
        return {"success": True, "message": f"Focused: '{win.window_text()}'"}

    elif _IS_MAC:
        script = f'''
tell application "System Events"
    repeat with proc in (every process whose background only is false)
        try
            repeat with win in (every window of proc)
                if name of win contains "{title}" then
                    set frontmost of proc to true
                    perform action "AXRaise" of win
                    return "ok"
                end if
            end repeat
        end try
    end repeat
end tell
return "not found"
'''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            if "ok" in result.stdout.lower():
                return {"success": True, "message": f"Focused window matching '{title}'"}
            return {"error": f"No window found matching '{title}'"}
        except FileNotFoundError:
            return {"error": "osascript not available"}

    else:  # Linux — try xdotool, then wmctrl
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowactivate"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                time.sleep(0.2)
                return {"success": True, "message": f"Focused window matching '{title}'"}
        except FileNotFoundError:
            pass
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(0.2)
                return {"success": True, "message": f"Focused window matching '{title}'"}
            return {"error": f"No window found matching '{title}'"}
        except FileNotFoundError:
            return {"error": "Window focus requires xdotool or wmctrl: apt install xdotool wmctrl"}


def _do_type_text(inp: dict) -> dict:
    text         = inp["text"]
    window_title = inp.get("window_title")

    if window_title:
        focus_result = _do_focus_window({"title": window_title})
        if "error" in focus_result:
            return focus_result
        time.sleep(0.3)

    _clipboard_paste(text)
    return {"success": True, "message": f"Typed {len(text)} character(s)"}


def _do_click_element(inp: dict) -> dict:
    import pyautogui

    window_title  = inp.get("window_title")
    element_title = inp.get("element_title")
    element_type  = inp.get("element_type")
    coords        = inp.get("coords")

    # Coords-only: works on all platforms
    if coords and not (element_title or element_type):
        if window_title:
            focus_res = _do_focus_window({"title": window_title})
            if "error" in focus_res:
                return focus_res
            time.sleep(0.2)
        pyautogui.click(coords[0], coords[1])
        return {"success": True, "message": f"Clicked screen coordinates {coords}"}

    if not _IS_WIN:
        if coords:
            if window_title:
                _do_focus_window({"title": window_title})
                time.sleep(0.2)
            pyautogui.click(coords[0], coords[1])
            return {"success": True, "message": f"Clicked at {coords}"}
        return {
            "error": (
                "Clicking UI elements by name/type is only supported on Windows. "
                "Provide 'coords' [x, y] for cross-platform coordinate-based clicking."
            )
        }

    # Windows: full element lookup via pywinauto
    if not window_title:
        return {"error": "'window_title' is required when clicking a UI element by name or type"}

    win = _find_win(window_title)
    win.set_focus()
    time.sleep(0.2)

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

    if coords:
        pyautogui.click(coords[0], coords[1])
        return {"success": True, "message": f"Element not found by name; clicked at {coords}"}

    return {"error": "Provide element_title, element_type, and/or coords to click"}


def _do_keyboard_shortcut(inp: dict) -> dict:
    import pyautogui

    keys  = inp["keys"]
    parts = [k.strip().lower() for k in keys.split("+")]
    pyautogui.hotkey(*parts)
    return {"success": True, "message": f"Sent shortcut: {keys}"}


def _do_take_screenshot(inp: dict) -> dict:
    import pyautogui

    screenshot = pyautogui.screenshot()
    save_path  = inp.get("save_path")

    if save_path:
        screenshot.save(save_path)
        return {"success": True, "path": save_path, "size": list(screenshot.size)}

    buf = io.BytesIO()
    screenshot.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"success": True, "image_base64": b64, "size": list(screenshot.size)}


def _do_read_screen_text(inp: dict) -> dict:
    window_title = inp.get("window_title")

    if _IS_WIN:
        win    = _find_win(window_title) if window_title else _active_window_win()
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
            "success":       True,
            "window":        source,
            "text":          "\n".join(texts),
            "element_count": len(texts),
        }

    elif _IS_MAC:
        if window_title:
            script = f'''
tell application "System Events"
    repeat with proc in (every process whose background only is false)
        try
            repeat with win in (every window of proc)
                if name of win contains "{window_title}" then
                    return name of proc & ": " & name of win
                end if
            end repeat
        end try
    end repeat
end tell
return ""
'''
        else:
            script = '''
tell application "System Events"
    set proc to first process whose frontmost is true
    return name of proc & ": " & name of front window of proc
end tell
'''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            title = result.stdout.strip()
            return {
                "success": True,
                "window":  title or "(unknown)",
                "text":    title or "",
                "note":    "macOS: window title only — full UI text reading requires a dedicated macOS plugin.",
            }
        except FileNotFoundError:
            return {"error": "osascript not available"}

    else:  # Linux
        try:
            if window_title:
                result = subprocess.run(
                    ["xdotool", "search", "--name", window_title, "getwindowname"],
                    capture_output=True, text=True, timeout=5,
                )
            else:
                result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True, timeout=5,
                )
            name = result.stdout.strip()
            return {
                "success": True,
                "window":  name or "(unknown)",
                "text":    name or "",
                "note":    "Linux: window title only — full UI text reading requires a dedicated Linux plugin.",
            }
        except FileNotFoundError:
            return {"error": "xdotool not found — install with: apt install xdotool"}


# ── Composite action ───────────────────────────────────────────────────────────

def _do_launch_and_type(inp: dict) -> dict:
    app_name = inp["app_name"]
    text     = inp["text"]

    already_open = False

    if _IS_WIN:
        try:
            win = _find_win(app_name)
            win.set_focus()
            already_open = True
        except Exception:
            pass
    elif _IS_LINUX:
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", app_name, "windowactivate"],
                capture_output=True, timeout=3,
            )
            already_open = result.returncode == 0 and bool(result.stdout.strip())
        except FileNotFoundError:
            pass

    if not already_open:
        open_result = _do_open_app({"app_name": app_name})
        if "error" in open_result:
            return open_result
        time.sleep(1.5)

    if _IS_WIN:
        win = None
        for _ in range(6):
            try:
                win = _find_win(app_name)
                break
            except RuntimeError:
                time.sleep(0.5)
        if win:
            win.set_focus()
            time.sleep(0.3)
            _clipboard_paste(text)
            return {"success": True, "message": f"Opened '{win.window_text()}' and typed text."}
        _clipboard_paste(text)
        return {"success": True, "message": f"Opened {app_name} and typed text (window title not detected)."}
    else:
        # Mac: open -a already brought the app to front; Linux: windowactivate did it
        time.sleep(0.3)
        _clipboard_paste(text)
        return {"success": True, "message": f"Opened {app_name} and typed text."}


# ── Action inference ───────────────────────────────────────────────────────────

def _infer_action(inp: dict) -> str | None:
    if inp.get("path"):                                              return "open_path"
    if inp.get("name"):                                              return "find_path"
    if inp.get("app_name") and inp.get("text"):                      return "launch_and_type"
    if inp.get("text"):                                              return "type_text"
    if inp.get("keys"):                                              return "keyboard_shortcut"
    if inp.get("app_name"):                                          return "open_app"
    if inp.get("element_title") or inp.get("element_type") or inp.get("coords"): return "click_element"
    if inp.get("title"):                                             return "focus_window"
    if inp.get("window_title"):                                      return "read_screen_text"
    if inp.get("save_path") is not None:                             return "take_screenshot"
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
        action = _infer_action(input) or ""
        if not action:
            return {"error": f"'action' is required. Choose from: {', '.join(_HANDLERS)}"}
    if action not in _HANDLERS:
        return {"error": f"Unknown action '{action}'. Valid: {', '.join(_HANDLERS)}"}

    missing = [p for p in _REQUIRED[action] if not input.get(p)]
    if missing:
        return {"error": f"Action '{action}' is missing required parameter(s): {', '.join(missing)}"}

    try:
        return await asyncio.to_thread(_HANDLERS[action], input)
    except ImportError as exc:
        pkg = "pywinauto pyautogui Pillow" if _IS_WIN else "pyautogui Pillow"
        return {"error": f"Missing dependency: {exc}. Run: pip install {pkg}"}
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"{action} failed: {exc}"}
