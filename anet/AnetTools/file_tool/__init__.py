"""
file_tool — OS-level file system operations for the file_agent.

All actions return a dict. On error the dict contains an "error" key.
On success it always contains a "result" key (string or list/dict).
"""

import csv
import fnmatch
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

try:
    import send2trash as _s2t
    _HAS_S2T = True
except ImportError:
    _HAS_S2T = False


# ── Helpers ───────────────────────────────────────────────────────────────────

_ANET_FILES_DIR = Path(__file__).parents[3] / "anet_files"


def _resolve_safe_path(path: str, agent: str) -> Path:
    """
    Redirect relative paths or bare filenames to anet_files/<agent>/
    to keep the Anet codebase clean. Absolute paths are used as-is.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    
    # If it's a relative path starting with . or just a filename, redirect.
    # We only do this if it's NOT the code_agent (which needs to work in the repo).
    if agent != "code_agent":
        safe_base = _ANET_FILES_DIR / agent
        safe_base.mkdir(parents=True, exist_ok=True)
        return safe_base / p
    
    return p


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1")


# ── Action implementations ────────────────────────────────────────────────────

def _read_file(params: dict) -> dict:
    path = params.get("path", "")
    p = Path(path)
    if not p.exists():
        return {"error": f"read_file failed — path not found: {path}"}
    if not p.is_file():
        return {"error": f"read_file failed — not a file: {path}"}
    try:
        text = _read_text(p)
        if p.suffix.lower() == ".json":
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        return {"result": text, "path": str(p), "size": _fmt_size(p.stat().st_size)}
    except Exception as exc:
        return {"error": f"read_file failed — {exc}"}


def _write_file(params: dict) -> dict:
    path    = params.get("path", "")
    content = params.get("content", "")
    mode    = params.get("mode", "overwrite")
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
        else:
            p.write_text(content, encoding="utf-8")
        nb = len(content.encode())
        return {"result": f"Wrote {nb} bytes to {path}", "path": str(p), "bytes_written": nb}
    except Exception as exc:
        return {"error": f"write_file failed — {exc}"}


def _create_folder(params: dict) -> dict:
    path = params.get("path", "")
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return {"result": f"Folder created: {path}", "path": str(p)}
    except Exception as exc:
        return {"error": f"create_folder failed — {exc}"}


def _copy_file(params: dict) -> dict:
    src, dst = params.get("src", ""), params.get("dst", "")
    s, d = Path(src), Path(dst)
    if not s.exists():
        return {"error": f"copy_file failed — source not found: {src}"}
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(s), str(d))
        return {"result": f"Copied to {dst}", "path": str(d)}
    except Exception as exc:
        return {"error": f"copy_file failed — {exc}"}


def _move_file(params: dict) -> dict:
    src, dst = params.get("src", ""), params.get("dst", "")
    s, d = Path(src), Path(dst)
    if not s.exists():
        return {"error": f"move_file failed — source not found: {src}"}
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return {"result": f"Moved to {dst}", "path": str(d)}
    except Exception as exc:
        return {"error": f"move_file failed — {exc}"}


def _delete_file(params: dict) -> dict:
    path = params.get("path", "")
    p = Path(path)
    if not p.exists():
        return {"error": f"delete_file failed — path not found: {path}"}
    if not _HAS_S2T:
        return {"error": "delete_file failed — send2trash not installed (pip install send2trash)"}
    try:
        _s2t.send2trash(str(p.resolve()))
        return {"result": f"Moved to Recycle Bin: {path}"}
    except Exception as exc:
        return {"error": f"delete_file failed — {exc}"}


def _rename_file(params: dict) -> dict:
    path     = params.get("path", "")
    new_name = params.get("new_name", "")
    p = Path(path)
    if not p.exists():
        return {"error": f"rename_file failed — path not found: {path}"}
    if not new_name:
        return {"error": "rename_file failed — new_name is required"}
    new_path = p.parent / new_name
    try:
        p.rename(new_path)
        return {"result": f"Renamed to {new_name}", "path": str(new_path)}
    except Exception as exc:
        return {"error": f"rename_file failed — {exc}"}


def _list_directory(params: dict) -> dict:
    path    = params.get("path", "")
    pattern = params.get("pattern", "*")
    p = Path(path)
    if not p.exists():
        return {"error": f"list_directory failed — path not found: {path}"}
    if not p.is_dir():
        return {"error": f"list_directory failed — not a directory: {path}"}
    try:
        entries = []
        for item in sorted(p.iterdir()):
            if not fnmatch.fnmatch(item.name, pattern):
                continue
            st = item.stat()
            entries.append({
                "name":     item.name,
                "type":     "dir" if item.is_dir() else "file",
                "size":     _fmt_size(st.st_size) if item.is_file() else "",
                "modified": _fmt_ts(st.st_mtime),
            })
        return {"result": entries, "count": len(entries), "path": str(p)}
    except Exception as exc:
        return {"error": f"list_directory failed — {exc}"}


def _search_files(params: dict) -> dict:
    root         = params.get("root", "")
    name_pattern = params.get("name_pattern", "*")
    file_type    = params.get("file_type", "any")
    r = Path(root)
    if not r.exists():
        return {"error": f"search_files failed — root not found: {root}"}
    try:
        matches = []
        for item in r.rglob("*"):
            if not fnmatch.fnmatch(item.name, name_pattern):
                continue
            if file_type == "file" and not item.is_file():
                continue
            if file_type == "folder" and not item.is_dir():
                continue
            matches.append(str(item))
        return {"result": matches, "count": len(matches)}
    except Exception as exc:
        return {"error": f"search_files failed — {exc}"}


def _get_file_info(params: dict) -> dict:
    path = params.get("path", "")
    p = Path(path)
    if not p.exists():
        return {"error": f"get_file_info failed — path not found: {path}"}
    try:
        st = p.stat()
        return {
            "result": {
                "path":       str(p.resolve()),
                "name":       p.name,
                "extension":  p.suffix,
                "size_bytes": st.st_size,
                "size":       _fmt_size(st.st_size),
                "created":    _fmt_ts(st.st_ctime),
                "modified":   _fmt_ts(st.st_mtime),
                "is_file":    p.is_file(),
                "is_dir":     p.is_dir(),
            }
        }
    except Exception as exc:
        return {"error": f"get_file_info failed — {exc}"}


def _parse_csv(params: dict) -> dict:
    path     = params.get("path", "")
    max_rows = int(params.get("max_rows", 100))
    p = Path(path)
    if not p.exists():
        return {"error": f"parse_csv failed — path not found: {path}"}
    try:
        rows = []
        for enc in ("utf-8", "latin-1"):
            try:
                with p.open(encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    for i, row in enumerate(reader):
                        if i >= max_rows:
                            break
                        rows.append(dict(row))
                break
            except UnicodeDecodeError:
                continue
        return {"result": json.dumps(rows, indent=2, ensure_ascii=False), "rows": len(rows)}
    except Exception as exc:
        return {"error": f"parse_csv failed — {exc}"}


def _parse_json(params: dict) -> dict:
    path = params.get("path", "")
    p = Path(path)
    if not p.exists():
        return {"error": f"parse_json failed — path not found: {path}"}
    try:
        text = _read_text(p)
        parsed = json.loads(text)
        return {"result": json.dumps(parsed, indent=2, ensure_ascii=False)}
    except json.JSONDecodeError as exc:
        return {"error": f"parse_json failed — invalid JSON at {exc}"}
    except Exception as exc:
        return {"error": f"parse_json failed — {exc}"}


def _read_lines(params: dict) -> dict:
    path  = params.get("path", "")
    start = int(params.get("start", 1))
    end   = int(params.get("end", 50))
    p = Path(path)
    if not p.exists():
        return {"error": f"read_lines failed — path not found: {path}"}
    try:
        lines = _read_text(p).splitlines()
        s = max(0, start - 1)
        e = min(len(lines), end)
        selected = lines[s:e]
        return {
            "result":       "\n".join(selected),
            "lines_returned": len(selected),
            "total_lines":  len(lines),
            "range":        f"{start}–{e}",
        }
    except Exception as exc:
        return {"error": f"read_lines failed — {exc}"}


def _zip_files(params: dict) -> dict:
    paths      = params.get("paths", [])
    output_zip = params.get("output_zip", "")
    if not paths:
        return {"error": "zip_files failed — paths list is empty"}
    out = Path(output_zip)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
            for path in paths:
                p = Path(path)
                if not p.exists():
                    return {"error": f"zip_files failed — not found: {path}"}
                if p.is_dir():
                    for f in p.rglob("*"):
                        if f.is_file():
                            zf.write(str(f), str(f.relative_to(p.parent)))
                else:
                    zf.write(str(p), p.name)
        return {
            "result": f"Created {output_zip}",
            "path":   str(out),
            "size":   _fmt_size(out.stat().st_size),
        }
    except Exception as exc:
        return {"error": f"zip_files failed — {exc}"}


def _unzip_file(params: dict) -> dict:
    zip_path   = params.get("zip_path", "")
    extract_to = params.get("extract_to", "")
    zp = Path(zip_path)
    if not zp.exists():
        return {"error": f"unzip_file failed — zip not found: {zip_path}"}
    try:
        out = Path(extract_to)
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(zp), "r") as zf:
            zf.extractall(str(out))
            names = zf.namelist()
        return {
            "result":     f"Extracted {len(names)} files to {extract_to}",
            "files":      names,
            "extract_to": str(out),
        }
    except Exception as exc:
        return {"error": f"unzip_file failed — {exc}"}


_DISPATCH = {
    "read_file":      _read_file,
    "write_file":     _write_file,
    "create_folder":  _create_folder,
    "copy_file":      _copy_file,
    "move_file":      _move_file,
    "delete_file":    _delete_file,
    "rename_file":    _rename_file,
    "list_directory": _list_directory,
    "search_files":   _search_files,
    "get_file_info":  _get_file_info,
    "parse_csv":      _parse_csv,
    "parse_json":     _parse_json,
    "read_lines":     _read_lines,
    "zip_files":      _zip_files,
    "unzip_file":     _unzip_file,
}


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(params: dict) -> dict:
    action = (params.get("action") or "").strip()
    agent  = (params.get("_agent_name") or "agent").strip()
    if not action:
        return {"error": "action is required"}

    # Pre-resolve common path parameters to keep agent files out of the Anet root
    for key in ["path", "src", "dst", "root", "output_zip", "zip_path", "extract_to"]:
        if key in params and isinstance(params[key], str) and params[key].strip():
            params[key] = str(_resolve_safe_path(params[key], agent))

    # zip_files uses a list of paths
    if "paths" in params and isinstance(params["paths"], list):
        params["paths"] = [str(_resolve_safe_path(p, agent)) for p in params["paths"]]

    handler = _DISPATCH.get(action)
    if handler is None:
        valid = ", ".join(_DISPATCH)
        return {"error": f"Unknown action '{action}'. Valid actions: {valid}"}
    try:
        return handler(params)
    except KeyError as exc:
        return {"error": f"{action} failed — missing required parameter: {exc}"}
    except Exception as exc:
        return {"error": f"{action} failed — {exc}"}


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_tool",
        "description": (
            "OS-level file system operations: read, write, copy, move, delete, rename, "
            "list, search, get info, parse CSV/JSON, read line ranges, zip and unzip. "
            "Always use action= to specify the operation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "read_file", "write_file", "create_folder",
                        "copy_file", "move_file", "delete_file", "rename_file",
                        "list_directory", "search_files", "get_file_info",
                        "parse_csv", "parse_json", "read_lines",
                        "zip_files", "unzip_file",
                    ],
                    "description": "Operation to perform.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Absolute or relative path to a file or folder. "
                        "Used by: read_file, write_file, create_folder, delete_file, "
                        "rename_file, get_file_info, list_directory, parse_csv, parse_json, read_lines."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write. Used by: write_file.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": "Write mode. Default: overwrite. Used by: write_file.",
                },
                "src": {
                    "type": "string",
                    "description": "Source path. Used by: copy_file, move_file.",
                },
                "dst": {
                    "type": "string",
                    "description": "Destination path. Used by: copy_file, move_file.",
                },
                "new_name": {
                    "type": "string",
                    "description": "New filename (no path, just the name). Used by: rename_file.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter entries, e.g. '*.py'. Default: '*'. Used by: list_directory.",
                },
                "root": {
                    "type": "string",
                    "description": "Root directory to search from. Used by: search_files.",
                },
                "name_pattern": {
                    "type": "string",
                    "description": "Glob pattern to match filenames, e.g. '*.log'. Used by: search_files.",
                },
                "file_type": {
                    "type": "string",
                    "enum": ["file", "folder", "any"],
                    "description": "Filter results by type. Default: any. Used by: search_files.",
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Max rows to return. Default: 100. Used by: parse_csv.",
                },
                "start": {
                    "type": "integer",
                    "description": "First line to return (1-indexed). Default: 1. Used by: read_lines.",
                },
                "end": {
                    "type": "integer",
                    "description": "Last line to return (inclusive). Default: 50. Used by: read_lines.",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file/folder paths to compress. Used by: zip_files.",
                },
                "output_zip": {
                    "type": "string",
                    "description": "Path for the output .zip file. Used by: zip_files.",
                },
                "zip_path": {
                    "type": "string",
                    "description": "Path to the .zip file to extract. Used by: unzip_file.",
                },
                "extract_to": {
                    "type": "string",
                    "description": "Directory to extract files into. Used by: unzip_file.",
                },
            },
            "required": ["action"],
        },
    },
}
