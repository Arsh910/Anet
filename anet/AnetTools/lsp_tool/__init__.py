"""
lsp_tool — Language Server Protocol client for code intelligence.

Starts and manages LSP servers per language+project. Gives the code_agent
IDE-level knowledge: real type errors, go-to-definition, find-all-references,
workspace-wide rename, and symbol navigation.

Supported servers (if installed):
  Python     → pylsp   (pip install python-lsp-server)
  Python     → pyright (pip install pyright)
  Go         → gopls   (go install golang.org/x/tools/gopls@latest)
  TypeScript → typescript-language-server  (npm i -g typescript-language-server typescript)
  JavaScript → typescript-language-server
  C / C++    → clangd  (package manager: apt/brew/choco)
  Rust       → rust-analyzer  (rustup component add rust-analyzer)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

SCHEMA = {
    "type": "function",
    "function": {
        "name": "lsp_tool",
        "description": (
            "Language Server Protocol client — IDE-level code intelligence. "
            "diagnostics: all errors/warnings in a file (better than running a linter). "
            "hover: type info and docs for the symbol at line:col. "
            "definition: jump to where a symbol is defined. "
            "references: find every usage of a symbol across the project. "
            "rename: rename a symbol across the entire workspace (updates all imports). "
            "symbols: list all functions/classes/variables in a file. "
            "status: show running LSP servers and stop one if needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["diagnostics", "hover", "definition", "references", "rename", "symbols", "status"],
                    "description": "Operation to perform.",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path to the source file.",
                },
                "root": {
                    "type": "string",
                    "description": "Project root directory. Auto-detected from common markers (pyproject.toml, go.mod, tsconfig.json…) if omitted.",
                },
                "line": {
                    "type": "integer",
                    "description": "0-based line number (required for hover/definition/references/rename).",
                },
                "col": {
                    "type": "integer",
                    "description": "0-based character column (required for hover/definition/references/rename).",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name for the rename action.",
                },
                "stop": {
                    "type": "string",
                    "description": "Server key to stop (from status output). Only used with status action.",
                },
            },
            "required": ["action"],
        },
    },
}

# ── Language detection ────────────────────────────────────────────────────────

_LANG_BY_EXT: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".go": "go",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".rs": "rust",
}

# languageId sent in textDocument/didOpen
_LANG_ID: dict[str, str] = {
    "python": "python", "go": "go",
    "typescript": "typescript", "javascript": "javascript",
    "c": "c", "cpp": "cpp", "rust": "rust",
}

# Adapter commands, tried in order — first whose binary exists is used.
_ADAPTERS: dict[str, list[dict]] = {
    "python": [
        {"cmd": ["pylsp"],                              "install": "pip install python-lsp-server"},
        {"cmd": ["pyright-langserver", "--stdio"],      "install": "pip install pyright"},
    ],
    "go": [
        {"cmd": ["gopls"],                              "install": "go install golang.org/x/tools/gopls@latest"},
    ],
    "typescript": [
        {"cmd": ["typescript-language-server", "--stdio"], "install": "npm install -g typescript-language-server typescript"},
    ],
    "javascript": [
        {"cmd": ["typescript-language-server", "--stdio"], "install": "npm install -g typescript-language-server typescript"},
    ],
    "c": [
        {"cmd": ["clangd"],                             "install": "install clangd via your package manager"},
    ],
    "cpp": [
        {"cmd": ["clangd"],                             "install": "install clangd via your package manager"},
    ],
    "rust": [
        {"cmd": ["rust-analyzer"],                      "install": "rustup component add rust-analyzer"},
    ],
}

# Files that mark a project root, per language
_ROOT_MARKERS: dict[str, list[str]] = {
    "python":     ["pyproject.toml", "setup.py", "setup.cfg", "pyrightconfig.json"],
    "go":         ["go.mod", "go.work"],
    "typescript": ["tsconfig.json", "jsconfig.json", "package.json"],
    "javascript": ["tsconfig.json", "jsconfig.json", "package.json"],
    "c":          ["compile_commands.json", "CMakeLists.txt", "Makefile"],
    "cpp":        ["compile_commands.json", "CMakeLists.txt", "Makefile"],
    "rust":       ["Cargo.toml"],
}

_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}

_SYMBOL_KIND = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum_member", 23: "struct", 24: "event",
    25: "operator", 26: "type_param",
}

_CLIENT_CAPABILITIES = {
    "textDocument": {
        "synchronization": {"openClose": True, "change": 1},  # 1 = full sync
        "hover": {"contentFormat": ["plaintext", "markdown"]},
        "definition": {"linkSupport": False},
        "references": {},
        "rename": {"prepareSupport": False},
        "documentSymbol": {
            "hierarchicalDocumentSymbolSupport": True,
            "symbolKind": {"valueSet": list(range(1, 27))},
        },
        "publishDiagnostics": {},
    },
    "workspace": {"applyEdit": True},
}


# ── URI helpers ───────────────────────────────────────────────────────────────

def _to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _from_uri(uri: str) -> Path:
    """Convert a file:// URI back to a Path, handling Windows drive letters."""
    if not uri.startswith("file://"):
        return Path(uri)
    rest = unquote(uri[7:])
    # Windows: file:///C:/path  →  uri[7:] = /C:/path  →  strip leading /
    if sys.platform == "win32" and re.match(r"^/[A-Za-z]:", rest):
        rest = rest[1:]
    return Path(rest)


# ── Language / root detection ─────────────────────────────────────────────────

def _detect_lang(path: Path, override: Optional[str] = None) -> Optional[str]:
    if override:
        return override.lower()
    return _LANG_BY_EXT.get(path.suffix.lower())


def _find_root(file_path: Path, lang: str) -> Path:
    """Walk up the directory tree until a root marker is found."""
    markers = _ROOT_MARKERS.get(lang, [])
    current = file_path.parent.resolve()
    while True:
        for m in markers:
            if (current / m).exists():
                return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return file_path.parent.resolve()


def _resolve_cmd(lang: str) -> Optional[tuple[list[str], str]]:
    """Return (command_list, install_hint) for the first available adapter."""
    for adapter in _ADAPTERS.get(lang, []):
        if shutil.which(adapter["cmd"][0]):
            return adapter["cmd"], adapter["install"]
    return None


# ── Wire protocol ─────────────────────────────────────────────────────────────

def _encode(msg: dict) -> bytes:
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


# ── LSP server ────────────────────────────────────────────────────────────────

class _LSPServer:
    def __init__(self, key: str, cmd: list[str], root: Path, lang: str):
        self.key   = key
        self.cmd   = cmd
        self.root  = root
        self.lang  = lang
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._seq  = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._diagnostics: dict[str, list] = {}      # uri → list[diagnostic]
        self._diag_events:  dict[str, asyncio.Event] = {}
        self._open_files:   dict[str, int] = {}      # uri → version
        self._reader_task:  Optional[asyncio.Task] = None
        self.capabilities:  dict = {}

    # ── Startup ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        kwargs: dict = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.DEVNULL,
            "cwd": str(self.root),
        }
        if sys.platform == "win32":
            import subprocess
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._proc = await asyncio.create_subprocess_exec(*self.cmd, **kwargs)

        loop = asyncio.get_running_loop()
        self._reader_task = loop.create_task(self._reader_loop(), name=f"lsp-reader:{self.key}")

        try:
            result = await self._send_request("initialize", {
                "processId": os.getpid(),
                "rootUri": _to_uri(self.root),
                "rootPath": str(self.root),
                "capabilities": _CLIENT_CAPABILITIES,
                "workspaceFolders": [{"uri": _to_uri(self.root), "name": self.root.name}],
            }, timeout=30.0)
        except Exception as exc:
            self._proc.kill()
            raise RuntimeError(f"LSP initialize failed for {self.lang}: {exc}") from exc

        self.capabilities = (result or {}).get("capabilities", {})
        await self._send_notification("initialized", {})

    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                await asyncio.wait_for(self._send_request("shutdown", None, timeout=3.0), timeout=3.0)
            except Exception:
                pass
            try:
                await self._send_notification("exit", {})
            except Exception:
                pass
            try:
                self._proc.kill()
            except Exception:
                pass
        if self._reader_task:
            self._reader_task.cancel()

    # ── Background reader ─────────────────────────────────────────────────────

    async def _reader_loop(self) -> None:
        buf = bytearray()
        try:
            while True:
                chunk = await self._proc.stdout.read(8192)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    sep = buf.find(b"\r\n\r\n")
                    if sep == -1:
                        break
                    header = buf[:sep].decode("ascii", errors="ignore")
                    m = re.search(r"Content-Length:\s*(\d+)", header, re.IGNORECASE)
                    if not m:
                        buf = buf[sep + 4:]
                        continue
                    n = int(m.group(1))
                    start = sep + 4
                    if len(buf) < start + n:
                        break
                    body = bytes(buf[start: start + n])
                    buf = buf[start + n:]
                    try:
                        self._dispatch(json.loads(body.decode("utf-8")))
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("LSP server disconnected"))
            self._pending.clear()

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg and "method" not in msg:
            # Response to one of our requests
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if msg.get("error"):
                    err = msg["error"]
                    fut.set_exception(RuntimeError(err.get("message", f"LSP error code {err.get('code')}")))
                else:
                    fut.set_result(msg.get("result"))
        elif msg.get("method") == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            uri    = params.get("uri", "")
            diags  = params.get("diagnostics", [])
            self._diagnostics[uri] = diags
            evt = self._diag_events.get(uri)
            if evt:
                evt.set()
        # All other notifications ($/progress, window/logMessage, etc.) are silently ignored.

    # ── Low-level send ────────────────────────────────────────────────────────

    async def _write(self, data: bytes) -> None:
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def _send_request(self, method: str, params, timeout: float = 10.0):
        self._seq += 1
        req_id = self._seq
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[req_id] = fut
        await self._write(_encode(msg))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"LSP '{method}' timed out after {timeout}s")

    async def _send_notification(self, method: str, params) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._write(_encode(msg))

    # ── Document management ───────────────────────────────────────────────────

    async def open_file(self, path: Path) -> str:
        """Open a file in the LSP server (didOpen) and return its URI."""
        uri = _to_uri(path)
        if uri not in self._open_files:
            content  = path.read_text(encoding="utf-8", errors="replace")
            lang_id  = _LANG_ID.get(self.lang, self.lang)
            await self._send_notification("textDocument/didOpen", {
                "textDocument": {"uri": uri, "languageId": lang_id, "version": 1, "text": content},
            })
            self._open_files[uri] = 1
        return uri

    async def sync_file(self, path: Path) -> str:
        """Re-sync file content (didChange with full text) and return URI."""
        uri = _to_uri(path)
        content = path.read_text(encoding="utf-8", errors="replace")
        if uri not in self._open_files:
            lang_id = _LANG_ID.get(self.lang, self.lang)
            await self._send_notification("textDocument/didOpen", {
                "textDocument": {"uri": uri, "languageId": lang_id, "version": 1, "text": content},
            })
            self._open_files[uri] = 1
        else:
            version = self._open_files[uri] + 1
            self._open_files[uri] = version
            await self._send_notification("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": content}],
            })
        return uri

    # ── Diagnostics ───────────────────────────────────────────────────────────

    async def get_diagnostics(self, path: Path, timeout: float = 5.0) -> list:
        # Clear stale event so we catch a fresh notification
        uri = _to_uri(path)
        self._diag_events[uri] = asyncio.Event()
        await self.sync_file(path)
        try:
            await asyncio.wait_for(self._diag_events[uri].wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._diag_events.pop(uri, None)
        return self._diagnostics.get(uri, [])

    # ── Requests ──────────────────────────────────────────────────────────────

    async def hover(self, path: Path, line: int, col: int):
        uri = await self.open_file(path)
        return await self._send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })

    async def definition(self, path: Path, line: int, col: int):
        uri = await self.open_file(path)
        return await self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })

    async def references(self, path: Path, line: int, col: int):
        uri = await self.open_file(path)
        return await self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
            "context": {"includeDeclaration": True},
        })

    async def rename(self, path: Path, line: int, col: int, new_name: str):
        uri = await self.open_file(path)
        return await self._send_request("textDocument/rename", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
            "newName": new_name,
        })

    async def document_symbols(self, path: Path):
        uri = await self.open_file(path)
        return await self._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })


# ── Server registry ───────────────────────────────────────────────────────────

_REGISTRY: dict[str, _LSPServer] = {}


async def _get_server(path: Path, root: Optional[Path], lang_override: Optional[str]) -> _LSPServer:
    lang = _detect_lang(path, lang_override)
    if not lang:
        raise ValueError(f"Unsupported file type: {path.suffix}  (supported: {', '.join(_LANG_BY_EXT)})")

    root = root or _find_root(path, lang)
    key  = f"{lang}:{root}"

    existing = _REGISTRY.get(key)
    if existing and existing.alive():
        return existing

    resolved = _resolve_cmd(lang)
    if not resolved:
        adapters = _ADAPTERS.get(lang, [])
        names    = [a["cmd"][0] for a in adapters]
        install  = adapters[0]["install"] if adapters else ""
        raise RuntimeError(
            f"No LSP server found for {lang}. "
            f"Tried: {', '.join(names)}. "
            f"Install with: {install}"
        )

    cmd, _ = resolved
    server = _LSPServer(key, cmd, root, lang)
    await server.start()
    _REGISTRY[key] = server
    return server


# ── Text edit application (for rename) ───────────────────────────────────────

def _apply_edits_to_string(content: str, edits: list[dict]) -> str:
    """Apply a list of LSP TextEdits in reverse order (bottom-to-top)."""
    lines = content.split("\n")
    for edit in sorted(edits, key=lambda e: (e["range"]["start"]["line"], e["range"]["start"]["character"]), reverse=True):
        sl = edit["range"]["start"]["line"]
        sc = edit["range"]["start"]["character"]
        el = edit["range"]["end"]["line"]
        ec = edit["range"]["end"]["character"]
        nt = edit["newText"]
        if sl == el:
            row = lines[sl] if sl < len(lines) else ""
            lines[sl] = row[:sc] + nt + row[ec:]
        else:
            start_row = lines[sl] if sl < len(lines) else ""
            end_row   = lines[el] if el < len(lines) else ""
            merged    = start_row[:sc] + nt + end_row[ec:]
            lines[sl:el + 1] = merged.split("\n")
    return "\n".join(lines)


def _apply_workspace_edit(edit: dict) -> list[str]:
    """Apply a WorkspaceEdit to disk. Returns list of 'N edits → path' strings."""
    edits_by_uri: dict[str, list] = {}
    if "documentChanges" in edit:
        for change in edit["documentChanges"]:
            if "kind" in change:
                continue  # skip resource ops (create/rename/delete) for safety
            uri  = change["textDocument"]["uri"]
            edits_by_uri.setdefault(uri, []).extend(change.get("edits", []))
    elif "changes" in edit:
        for uri, text_edits in edit["changes"].items():
            edits_by_uri[uri] = text_edits

    results = []
    for uri, text_edits in edits_by_uri.items():
        p = _from_uri(uri)
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            updated = _apply_edits_to_string(content, text_edits)
            p.write_text(updated, encoding="utf-8")
            results.append(f"  {len(text_edits)} edit(s) → {p}")
        except OSError as exc:
            results.append(f"  [error] {p}: {exc}")
    return results


# ── Output formatters ─────────────────────────────────────────────────────────

def _fmt_pos(pos: dict) -> str:
    return f"{pos['line']+1}:{pos['character']+1}"


def _fmt_range(r: dict) -> str:
    s, e = r["start"], r["end"]
    if s["line"] == e["line"]:
        return f"line {s['line']+1}, col {s['character']+1}-{e['character']+1}"
    return f"lines {s['line']+1}-{e['line']+1}"


def _fmt_location(loc: dict, cwd: Optional[Path] = None) -> str:
    p = _from_uri(loc.get("uri", loc.get("targetUri", "")))
    if cwd:
        try:
            p = p.relative_to(cwd)
        except ValueError:
            pass
    r = loc.get("range", loc.get("targetSelectionRange", loc.get("targetRange", {})))
    return f"{p}:{r.get('start', {}).get('line', 0)+1}"


def _fmt_hover(result) -> str:
    if not result:
        return "No hover information."
    contents = result.get("contents")
    if isinstance(contents, str):
        return contents.strip()
    if isinstance(contents, dict):
        return contents.get("value", "").strip()
    if isinstance(contents, list):
        parts = []
        for c in contents:
            parts.append(c.get("value", c) if isinstance(c, dict) else str(c))
        return "\n---\n".join(p.strip() for p in parts if p)
    return str(contents)


def _fmt_symbols(symbols: list, indent: int = 0) -> list[str]:
    lines = []
    for s in symbols:
        kind  = _SYMBOL_KIND.get(s.get("kind", 0), "symbol")
        name  = s.get("name", "?")
        r     = s.get("range", s.get("location", {}).get("range", {}))
        start = r.get("start", {})
        ln    = start.get("line", 0) + 1
        lines.append(f"{'  ' * indent}{kind:12}  {name}  (line {ln})")
        for child in s.get("children", []):
            lines.extend(_fmt_symbols([child], indent + 1))
    return lines


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(params: dict) -> dict:
    action   = params.get("action", "")
    path_str = params.get("path", "").strip()
    lang_ovr = params.get("language")
    root_str = params.get("root", "").strip()

    # ── status ────────────────────────────────────────────────────────────────
    if action == "status":
        stop_key = params.get("stop", "").strip()
        if stop_key and stop_key in _REGISTRY:
            srv = _REGISTRY.pop(stop_key)
            await srv.stop()
            return {"result": f"Stopped: {stop_key}"}
        if not _REGISTRY:
            return {"result": "No LSP servers running."}
        lines = ["Running LSP servers:"]
        for k, srv in _REGISTRY.items():
            state = "alive" if srv.alive() else "dead"
            lines.append(f"  [{k}]  {srv.lang}  root={srv.root}  status={state}")
        return {"result": "\n".join(lines)}

    # All other actions require a file path
    if not path_str:
        return {"error": "path is required"}

    path = Path(path_str)
    if not path.exists():
        return {"error": f"File not found: {path}"}

    root = Path(root_str) if root_str else None

    try:
        srv = await _get_server(path, root, lang_ovr)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}

    # ── diagnostics ───────────────────────────────────────────────────────────
    if action == "diagnostics":
        try:
            diags = await srv.get_diagnostics(path)
        except Exception as exc:
            return {"error": f"diagnostics failed: {exc}"}

        if not diags:
            return {"result": f"No diagnostics for {path}"}

        lines = [f"{len(diags)} diagnostic(s) in {path}:\n"]
        for d in diags:
            sev  = _SEVERITY.get(d.get("severity", 1), "?")
            r    = d.get("range", {})
            ln   = r.get("start", {}).get("line", 0) + 1
            col  = r.get("start", {}).get("character", 0) + 1
            msg  = d.get("message", "")
            src  = d.get("source", "")
            code = d.get("code", "")
            tag  = f" [{src}:{code}]" if src or code else ""
            lines.append(f"  {sev:7}  line {ln}:{col}{tag}  {msg}")
        return {"result": "\n".join(lines)}

    # ── hover ─────────────────────────────────────────────────────────────────
    elif action == "hover":
        line = params.get("line")
        col  = params.get("col", 0)
        if line is None:
            return {"error": "line is required for hover"}
        try:
            result = await srv.hover(path, line, col)
        except Exception as exc:
            return {"error": f"hover failed: {exc}"}
        return {"result": _fmt_hover(result)}

    # ── definition ───────────────────────────────────────────────────────────
    elif action == "definition":
        line = params.get("line")
        col  = params.get("col", 0)
        if line is None:
            return {"error": "line is required for definition"}
        try:
            result = await srv.definition(path, line, col)
        except Exception as exc:
            return {"error": f"definition failed: {exc}"}
        if not result:
            return {"result": "No definition found."}
        locs = result if isinstance(result, list) else [result]
        lines = [f"Definition(s) for {path}:{line+1}:{col+1}:\n"]
        for loc in locs:
            lines.append(f"  {_fmt_location(loc, srv.root)}")
        return {"result": "\n".join(lines)}

    # ── references ───────────────────────────────────────────────────────────
    elif action == "references":
        line = params.get("line")
        col  = params.get("col", 0)
        if line is None:
            return {"error": "line is required for references"}
        try:
            result = await srv.references(path, line, col)
        except Exception as exc:
            return {"error": f"references failed: {exc}"}
        if not result:
            return {"result": "No references found."}
        lines = [f"{len(result)} reference(s):\n"]
        for loc in result:
            lines.append(f"  {_fmt_location(loc, srv.root)}")
        return {"result": "\n".join(lines)}

    # ── rename ───────────────────────────────────────────────────────────────
    elif action == "rename":
        line     = params.get("line")
        col      = params.get("col", 0)
        new_name = params.get("new_name", "").strip()
        if line is None:
            return {"error": "line is required for rename"}
        if not new_name:
            return {"error": "new_name is required for rename"}
        try:
            workspace_edit = await srv.rename(path, line, col, new_name)
        except Exception as exc:
            return {"error": f"rename failed: {exc}"}
        if not workspace_edit:
            return {"result": "Server returned no edits — symbol may not be renameable here."}
        applied = _apply_workspace_edit(workspace_edit)
        if not applied:
            return {"result": "Rename produced an empty WorkspaceEdit (no files changed)."}
        lines = [f"Renamed to '{new_name}' across {len(applied)} file(s):"]
        lines.extend(applied)
        return {"result": "\n".join(lines)}

    # ── symbols ───────────────────────────────────────────────────────────────
    elif action == "symbols":
        try:
            result = await srv.document_symbols(path)
        except Exception as exc:
            return {"error": f"symbols failed: {exc}"}
        if not result:
            return {"result": "No symbols found."}
        lines = [f"Symbols in {path}:\n"]
        lines.extend(_fmt_symbols(result))
        return {"result": "\n".join(lines)}

    else:
        return {"error": f"Unknown action '{action}'. Valid: diagnostics, hover, definition, references, rename, symbols, status"}
