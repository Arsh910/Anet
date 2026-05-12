"""
validator.py — validates an agent folder against the ANet Plugin Protocol.

Collects all failures before returning so the user sees every problem at once.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from anet.plugin.schema import AgentManifest, CheckResult, ValidationResult
from anet.plugin.registry import get_agent

_KNOWN_PROVIDERS = {"google", "anthropic", "openai", "openrouter"}


def _ok(name: str, msg: str = "") -> CheckResult:
    return CheckResult(name=name, passed=True, message=msg)


def _fail(name: str, msg: str) -> CheckResult:
    return CheckResult(name=name, passed=False, message=msg)


def validate_agent(path: str) -> ValidationResult:
    agent_path = Path(path)
    checks:   list[CheckResult] = []
    errors:   list[str]         = []
    warnings: list[str]         = []

    # ── 1. agent.yaml exists ──────────────────────────────────────────────────
    manifest_file = agent_path / "agent.yaml"
    if not manifest_file.exists():
        return ValidationResult(
            passed=False,
            checks=[_fail("agent.yaml exists", "agent.yaml not found in this directory")],
            errors=["agent.yaml not found"],
            warnings=[],
        )
    checks.append(_ok("agent.yaml exists"))

    # ── 2. Valid YAML ─────────────────────────────────────────────────────────
    try:
        raw = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        msg = f"agent.yaml is not valid YAML: {exc}"
        return ValidationResult(passed=False, checks=checks + [_fail("valid YAML", msg)],
                                errors=[msg], warnings=[])
    checks.append(_ok("valid YAML"))

    # ── 3. Schema validation ──────────────────────────────────────────────────
    try:
        manifest = AgentManifest(**raw)
        checks.append(_ok("manifest schema"))
    except ValidationError as exc:
        field_errors = []
        for e in exc.errors():
            loc = " → ".join(str(x) for x in e["loc"])
            field_errors.append(f"{loc}: {e['msg']}")
        errors.extend(field_errors)
        checks.append(_fail("manifest schema", "; ".join(field_errors)))
        return ValidationResult(passed=False, checks=checks, errors=errors, warnings=[])

    # ── 4. Tool files ─────────────────────────────────────────────────────────
    for tool in manifest.tools:
        label     = f"tool '{tool.name}'"
        tool_path = agent_path / tool.file

        if not tool_path.exists():
            msg = f"{tool.file} not found"
            checks.append(_fail(f"{label} file", msg))
            errors.append(f"tool file not found: {tool.file}")
            continue
        checks.append(_ok(f"{label} file"))

        # importable?
        try:
            spec = importlib.util.spec_from_file_location(f"_val_{tool.name}", tool_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            checks.append(_ok(f"{label} importable"))
        except Exception as exc:
            msg = f"cannot import {tool.file}: {exc}"
            checks.append(_fail(f"{label} importable", msg))
            errors.append(msg)
            continue

        # run() present?
        if not hasattr(mod, "run"):
            candidates = [n for n in dir(mod)
                          if callable(getattr(mod, n)) and not n.startswith("_")][:3]
            hint = f"  → found callable names: {candidates}" if candidates else ""
            msg  = f"'run' not found in {tool.file}{hint}"
            checks.append(_fail(f"{label} run()", msg))
            errors.append(msg)
        else:
            checks.append(_ok(f"{label} run()"))

        # SCHEMA present? (warning, not error — tool still works without it)
        if not hasattr(mod, "SCHEMA"):
            msg = f"SCHEMA dict not defined in {tool.file} — model won't know what the tool does"
            checks.append(_fail(f"{label} SCHEMA", msg))
            warnings.append(msg)
        else:
            checks.append(_ok(f"{label} SCHEMA"))

    # ── 5. System prompt (skipped for tool-extension plugins) ────────────────
    if manifest.is_tool_extension:
        checks.append(_ok("system prompt", "skipped — tool extension"))
    elif manifest.prompt is None:
        msg = "prompt is required for full agent plugins"
        checks.append(_fail("system prompt", msg))
        errors.append(msg)
    elif "file" in manifest.prompt:
        sp_path = agent_path / manifest.prompt["file"]
        if not sp_path.exists():
            msg = f"prompt file not found: {manifest.prompt['file']}"
            checks.append(_fail("system prompt", msg))
            errors.append(msg)
        else:
            checks.append(_ok("system prompt", f"file: {manifest.prompt['file']}"))
    elif "inline" in manifest.prompt:
        checks.append(_ok("system prompt", "inline"))
    else:
        msg = "prompt must have 'file' or 'inline' key"
        checks.append(_fail("system prompt", msg))
        errors.append(msg)

    # ── 6. Name collision (warning only — connect will overwrite) ─────────────
    existing = get_agent(manifest.name)
    if existing:
        warnings.append(
            f"'{manifest.name}' is already connected — running `anet connect` will replace it"
        )

    return ValidationResult(
        passed=len(errors) == 0,
        checks=checks,
        errors=errors,
        warnings=warnings,
    )
