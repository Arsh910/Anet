"""
extool_validator.py — verify that an ExTool's __init__.py is loadable and valid.

This is what makes the `/newtool` generator trustworthy: a generated file that
*looks* right but doesn't import, or whose SCHEMA doesn't match what run() expects,
is worse than nothing. The toolsmith agent runs this after writing a tool and fixes
whatever it reports, until it passes.

Usage:
    python -m anet.core.extool_validator <path-to-__init__.py>

Exit code 0 = valid, 1 = invalid. Human-readable reasons are printed either way.
Checks are structural + an import smoke test only — run() is NEVER called, so
validation has no side effects.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path


def validate(init_path: str | Path) -> tuple[bool, list[str]]:
    """Return (ok, messages). ok is True only if every required check passes."""
    msgs: list[str] = []
    path = Path(init_path).resolve()

    if path.is_dir():
        path = path / "__init__.py"
    if not path.exists():
        return False, [f"FAIL: file not found: {path}"]
    if path.name != "__init__.py":
        msgs.append(f"WARN: expected an __init__.py, got '{path.name}'")

    # ── Import smoke test ─────────────────────────────────────────────────────
    # Add the tool folder to sys.path so a vendored sibling repo (imported via
    # sys.path manipulation inside the tool) resolves during the check.
    parent = str(path.parent)
    added = False
    if parent not in sys.path:
        sys.path.insert(0, parent)
        added = True
    try:
        spec = importlib.util.spec_from_file_location(f"_extool_check_{path.parent.name}", path)
        if spec is None or spec.loader is None:
            return False, [f"FAIL: cannot create import spec for {path}"]
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:  # noqa: BLE001 — surface any import-time failure
            return False, [
                f"FAIL: import error — {type(exc).__name__}: {exc}",
                "      (a missing dependency? add it to requirements.txt or pip install it,",
                "       or a wrong import path to the tool's source?)",
            ]
    finally:
        if added:
            try:
                sys.path.remove(parent)
            except ValueError:
                pass

    # ── run() ─────────────────────────────────────────────────────────────────
    run_fn = getattr(mod, "run", None)
    if run_fn is None:
        msgs.append("FAIL: module does not export a `run` function")
    elif not callable(run_fn):
        msgs.append("FAIL: `run` exists but is not callable")
    else:
        is_async = inspect.iscoroutinefunction(run_fn)
        msgs.append(f"OK: run() present ({'async' if is_async else 'sync'} - both are supported)")
        try:
            params = inspect.signature(run_fn).parameters
            if len(params) < 1:
                msgs.append("FAIL: run() must accept one argument (the params dict)")
        except (TypeError, ValueError):
            pass

    # ── SCHEMA ────────────────────────────────────────────────────────────────
    schema = getattr(mod, "SCHEMA", None)
    if schema is None:
        msgs.append("FAIL: module does not export a `SCHEMA` dict")
    elif not isinstance(schema, dict):
        msgs.append("FAIL: SCHEMA is not a dict")
    else:
        if schema.get("type") != "function":
            msgs.append("FAIL: SCHEMA['type'] must be 'function'")
        fn = schema.get("function")
        if not isinstance(fn, dict):
            msgs.append("FAIL: SCHEMA['function'] must be a dict")
        else:
            name = fn.get("name")
            if not isinstance(name, str) or not name.strip():
                msgs.append("FAIL: SCHEMA.function.name must be a non-empty string")
            else:
                msgs.append(f"OK: tool name = '{name}'")
                if name != path.parent.name:
                    msgs.append(
                        f"WARN: SCHEMA name '{name}' != folder '{path.parent.name}' — "
                        f"the registered `name:` in exanet.config.yaml must match SCHEMA.function.name"
                    )
            if not isinstance(fn.get("description"), str) or not fn.get("description", "").strip():
                msgs.append("FAIL: SCHEMA.function.description must be a non-empty string (the model reads this)")
            params_schema = fn.get("parameters")
            if not isinstance(params_schema, dict):
                msgs.append("FAIL: SCHEMA.function.parameters must be a dict")
            else:
                if params_schema.get("type") != "object":
                    msgs.append("FAIL: SCHEMA.function.parameters.type must be 'object'")
                props = params_schema.get("properties", {})
                if not isinstance(props, dict):
                    msgs.append("FAIL: SCHEMA.function.parameters.properties must be a dict")
                required = params_schema.get("required", [])
                if not isinstance(required, list):
                    msgs.append("FAIL: SCHEMA.function.parameters.required must be a list")
                elif isinstance(props, dict):
                    missing = [r for r in required if r not in props]
                    if missing:
                        msgs.append(
                            f"FAIL: required param(s) {missing} are not defined in properties - "
                            f"every required param must have a property entry"
                        )
                    else:
                        msgs.append(f"OK: parameters valid ({len(props)} defined, {len(required)} required)")

    ok = not any(m.startswith("FAIL") for m in msgs)
    return ok, msgs


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m anet.core.extool_validator <path-to-__init__.py>")
        return 2
    ok, msgs = validate(argv[0])
    for m in msgs:
        print("  " + m)
    print()
    if ok:
        print(f"PASS: {argv[0]} is a valid ExTool.")
        return 0
    print(f"INVALID: {argv[0]} has problems (see FAIL lines above).")
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
