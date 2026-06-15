"""
code_execution — run a Python snippet and get its output back.

A sandboxed-lite Python runner for the moments shelling out is clumsy: compute
something, transform data, parse/validate JSON, check a regex, verify logic.
The agent writes Python, this runs it in a child process, and stdout (plus the
value of a trailing expression, Jupyter-style) comes back as the result.

Backend is pluggable. Default "local" runs the snippet with the same Python
interpreter in a child process — timeout, captured output, bounded size, a
dedicated working directory. Same trust level as shell_tool (gated by the same
confirmation prompt). A "docker" backend can be slotted in later for true
isolation without changing this tool's interface.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "code_execution",
        "description": (
            "Run a Python 3 snippet and return its output. Use for computation, data "
            "transforms, parsing/validating JSON or text, quick logic checks, or "
            "verifying an answer — anything cleaner in Python than as a shell command. "
            "stdout is captured; the value of a trailing expression is auto-printed "
            "(like a REPL), so 'sum(range(10))' on the last line returns '45' without "
            "an explicit print(). Use print() for intermediate values. The standard "
            "library is available; install packages via shell_tool first if needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python source to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 30, max 300.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the run. Default: current directory.",
                },
            },
            "required": ["code"],
        },
    },
}

_MAX_OUTPUT = 8_000
_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 300

# Runner: parse the snippet, exec all but a trailing expression, then eval that
# expression and print its repr (REPL-style auto-display). Passed via `-c` and
# given the user-code file path as argv[1] — no shell, no quoting hazards.
_RUNNER = r"""
import ast, sys
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
try:
    tree = ast.parse(src)
except SyntaxError as e:
    print(f"SyntaxError: {e}", file=sys.stderr); sys.exit(1)
ns = {"__name__": "__main__", "__file__": path}
last = None
if tree.body and isinstance(tree.body[-1], ast.Expr):
    last = tree.body.pop().value
try:
    exec(compile(ast.Module(body=tree.body, type_ignores=[]), path, "exec"), ns)
    if last is not None:
        val = eval(compile(ast.Expression(last), path, "eval"), ns)
        if val is not None:
            print(repr(val))
except SystemExit:
    raise
except BaseException:
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""


async def run(params: dict) -> dict:
    code = params.get("code", "")
    if not isinstance(code, str) or not code.strip():
        return {"error": "No code provided."}
    timeout = min(int(params.get("timeout", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT), _MAX_TIMEOUT)

    cwd = params.get("cwd")
    work_dir = None
    if cwd:
        wd = Path(cwd)
        if not wd.exists():
            return {"error": f"Directory not found: {cwd}"}
        work_dir = str(wd)

    backend = params.get("_backend", "local")
    if backend != "local":
        return {"error": f"Unknown code_execution backend '{backend}'."}

    return await _run_local(code, timeout, work_dir)


async def _run_local(code: str, timeout: int, work_dir: str | None) -> dict:
    # Write the snippet to a temp file so the runner can read its source.
    tmp = Path(tempfile.gettempdir()) / f".anet_code_{abs(hash(code)) % 10_000_000}.py"
    try:
        tmp.write_text(code, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Could not stage code: {exc}"}

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", _RUNNER, str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=work_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": f"Code timed out after {timeout}s."}

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        combined = (out + ("\n[stderr]\n" + err if err.strip() else "")).strip()
        if len(combined) > _MAX_OUTPUT:
            combined = combined[:_MAX_OUTPUT] + f"\n… [output truncated at {_MAX_OUTPUT} chars]"

        return {
            "result": combined or "(no output)",
            "exit_code": proc.returncode,
            "success": proc.returncode == 0,
        }
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
