"""
VIGA tool - start, stop, and monitor background 3D render tasks.

Task state is persisted to agents/3dAgent/tasks/registry.json so status
survives server restarts mid-render.

Tasks are materialized under VIGA dataset roots:
  - agents/3dAgent/VIGA-release/data/dynamic_scene/<task_name>
  - agents/3dAgent/VIGA-release/data/static_scene/<task_name>

Execution is delegated to WSL runners using conda env "agent":
  - python runners/dynamic_scene.py ...
  - python runners/static_scene.py ...
"""

import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_VIGA_DIR          = Path(__file__).parent.parent / "VIGA-release"
_TASKS_DIR         = Path(__file__).parent.parent / "tasks"
_REGISTRY          = _TASKS_DIR / "registry.json"
_CONDA_ENV_NAME    = os.getenv("VIGA_CONDA_ENV", "agent")
_STALL_TIMEOUT_SEC = int(os.getenv("VIGA_STALL_TIMEOUT_SEC", "7200"))
_STALL_POLL_SEC    = int(os.getenv("VIGA_STALL_POLL_SEC", "10"))
_ASCII_GUARD_ENABLED = os.getenv("VIGA_ASCII_GUARD", "1").lower() not in {"0", "false", "no"}
_DEFAULT_BLENDER_SCRIPT = Path(__file__).with_name("generator_script_safe.py")


def _short_path(long_path: str) -> str:
    """Convert a Windows path to its 8.3 short form (no spaces, no quoting needed)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.kernel32.GetShortPathNameW(long_path, buf, 260)
        if buf.value:
            return buf.value
    except Exception:
        pass
    return long_path


def _find_blender() -> str:
    """Locate the Windows Blender executable. Returns short path (no spaces)."""
    override = os.getenv("BLENDER_EXE")
    if override and Path(override).exists():
        return _short_path(override)
    blender_root = Path("C:/Program Files/Blender Foundation")
    if blender_root.exists():
        for target in ["Blender 4.4", "Blender 4.3", "Blender 4.2", "Blender 4.1"]:
            exe = blender_root / target / "blender.exe"
            if exe.exists():
                return _short_path(str(exe))
        for d in sorted(blender_root.iterdir()):
            if "Blender 4" in d.name and (d / "blender.exe").exists():
                return _short_path(str(d / "blender.exe"))
    return "blender"


def _find_blend_file() -> str:
    """Return a valid .blend template file as a short path (no spaces)."""
    override = os.getenv("BLENDER_FILE")
    if override and Path(override).exists():
        return _short_path(override)
    blender_root = Path("C:/Program Files/Blender Foundation")
    if blender_root.exists():
        for target in ["Blender 4.4", "Blender 4.3", "Blender 4.2", "Blender 4.1"]:
            template = (
                blender_root / target
                / target.replace("Blender ", "")
                / "scripts/startup/bl_app_templates_system/Sculpting/startup.blend"
            )
            if template.exists():
                return _short_path(str(template))
    return ""


def _find_conda_python(env_name: str = "agent") -> str:
    """Return the Python executable inside a named conda env."""
    conda_prefix = os.getenv("CONDA_PREFIX", "")
    if conda_prefix:
        base = Path(conda_prefix)
        parts = base.parts
        if "envs" in parts:
            idx = max(i for i, p in enumerate(parts) if p == "envs")
            base = Path(*parts[:idx])
        candidate = base / "envs" / env_name / "python.exe"
        if candidate.exists():
            return str(candidate)
    for root in [
        Path.home() / "anaconda3",
        Path.home() / "miniconda3",
        Path("C:/ProgramData/anaconda3"),
        Path("C:/ProgramData/miniconda3"),
    ]:
        candidate = root / "envs" / env_name / "python.exe"
        if candidate.exists():
            return str(candidate)
    return ""


def _resolve_blender_script() -> str:
    """Resolve blender script path. Defaults to our CPU-safe wrapper script."""
    override = os.getenv("VIGA_BLENDER_SCRIPT", "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = _VIGA_DIR / candidate
    else:
        candidate = _DEFAULT_BLENDER_SCRIPT
    if not candidate.exists():
        return ""
    return _short_path(str(candidate))


def _resolve_task_root(task_root: str, scene_mode: str) -> Path:
    """Resolve task root directory.

    Priority:
    1) explicit input task_root
    2) VIGA_TASK_ROOT env var
    3) VIGA dataset root: VIGA-release/data/<scene_mode>
    """
    configured = (task_root or "").strip() or os.getenv("VIGA_TASK_ROOT", "").strip()
    if configured:
        root = Path(configured)
        if not root.is_absolute():
            root = (Path(__file__).parent.parent.parent / root).resolve()
        return root
    return _VIGA_DIR / "data" / scene_mode


def _to_wsl_path(path: str | Path) -> str:
    """Convert a Windows path to a WSL /mnt/... path."""
    value = str(Path(path).resolve()).replace("\\", "/")
    if len(value) >= 3 and value[1:3] == ":/":
        return f"/mnt/{value[0].lower()}{value[2:]}"
    return value


def _build_wsl_runner_command(
    *,
    scene_mode: str,
    task_name: str,
    model: str,
    max_rounds: int | None,
    generator_tools: str,
    verifier_tools: str,
    prompt_setting: str,
    test_id: str,
    conda_env: str,
    gpu_devices: str = "0",
) -> str:
    runner = "runners/dynamic_scene.py" if scene_mode == "dynamic_scene" else "runners/static_scene.py"
    dataset_path = f"data/{scene_mode}"
    q = shlex.quote

    # Use our safe Blender script: wraps GPU/CUDA init in try/except with CPU
    # fallback. VIGA's original has no error handling so a GPU crash silently
    # skips the render for that round.
    safe_script_wsl = _to_wsl_path(_DEFAULT_BLENDER_SCRIPT)

    python_cmd_parts = [
        "python", q(runner),
        f"--dataset-path={q(dataset_path)}",
        f"--task={q(task_name)}",
        f"--model={q(model)}",
        f"--generator-tools={q(generator_tools)}",
        f"--verifier-tools={q(verifier_tools)}",
        f"--prompt-setting={q(prompt_setting)}",
        "--max-workers=1",
        f"--test-id={q(test_id)}",
        f"--blender-script={q(safe_script_wsl)}",
    ]
    # Only pass --max-rounds when the caller explicitly requested a cap.
    # Without it, VIGA's runner uses its own default (100) and decides when done.
    if max_rounds is not None:
        python_cmd_parts.append(f"--max-rounds={int(max_rounds)}")
    if gpu_devices:
        python_cmd_parts.append(f"--gpu-devices={q(gpu_devices)}")

    lines = [
        "set -e",
        f"cd {q(_to_wsl_path(_VIGA_DIR))}",
        (
            'if command -v conda >/dev/null 2>&1; then eval "$(conda shell.bash hook)"; '
            'elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then . "$HOME/anaconda3/etc/profile.d/conda.sh"; '
            'elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then . "$HOME/miniconda3/etc/profile.d/conda.sh"; '
            'else echo "conda not found in WSL" >&2; exit 127; fi'
        ),
        f"conda activate {q(conda_env)}",
        f"export VIGA_USE_CUDA=1",
        f"export CUDA_VISIBLE_DEVICES={q(gpu_devices)}" if gpu_devices else "",
        " ".join(python_cmd_parts),
    ]
    return "\n".join(line for line in lines if line)


_API_KEY  = os.getenv("GOOGLE_API_KEY", "")
_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_MODEL    = os.getenv("VIGA_MODEL", "gemini-2.5-flash")

_lock = asyncio.Lock()

SCHEMA = {
    "type": "function",
    "function": {
        "name": "viga",
        "description": (
            "Generate 3D scenes using the VIGA dual-agent Blender system. "
            "start: launch a background render task (returns immediately with task_id). "
            "stop: cancel a running task by task_id. "
            "status: check progress of one task (task_id) or all tasks (omit task_id). "
            "\n\nChoose scene_mode based on the request: "
            "use 'static_scene' for still objects with no motion (product shots, furniture, "
            "a water bottle, a room, any scene that does not animate). "
            "Use 'dynamic_scene' for anything involving motion, physics, or animation "
            "(a ball rolling, water pouring, smoke, explosions, character movement). "
            "When in doubt, prefer 'static_scene'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "status"],
                    "description": "Action to perform",
                },
                "task": {
                    "type": "string",
                    "description": "Natural-language description of the 3D scene to generate (required for start)",
                },
                "target_image": {
                    "type": "string",
                    "description": "Absolute local path to a reference image (.png/.jpg/.jpeg) the model should match (required for start)",
                },
                "scene_mode": {
                    "type": "string",
                    "enum": ["static_scene", "dynamic_scene"],
                    "description": (
                        "static_scene: still objects, no animation (default for most requests). "
                        "dynamic_scene: animated/physics scenes with motion. "
                        "You must choose -- do not omit."
                    ),
                },
                "max_rounds": {
                    "type": "integer",
                    "description": "Cap on refinement rounds. Omit to let VIGA run until it decides the scene is complete (can be 100-200 rounds). Only set this for quick tests (e.g. 5-10) or to hard-cap a runaway task.",
                },
                "task_name": {
                    "type": "string",
                    "description": "Short slug for the task folder, e.g. water_bottle. Auto-generated if omitted.",
                },
                "prompt_setting": {
                    "type": "string",
                    "description": "Prompt preset: 'none' (default for static_scene), 'init' (default for dynamic_scene)",
                },
                "model": {
                    "type": "string",
                    "description": "LLM model for VIGA agents (default: gemini-2.5-flash)",
                },
                "generator_tools": {
                    "type": "string",
                    "description": "Comma-separated generator tool scripts override (advanced)",
                },
                "verifier_tools": {
                    "type": "string",
                    "description": "Comma-separated verifier tool scripts override (advanced)",
                },
                "task_root": {
                    "type": "string",
                    "description": "Override root directory for task data folders (advanced)",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID -- required for stop; optional for status (omit to list all)",
                },
            },
            "required": ["action"],
        },
    },
}


# -- Registry ------------------------------------------------------------------

def _load_registry() -> dict:
    if _REGISTRY.exists():
        try:
            return json.loads(_REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_registry(reg: dict) -> None:
    _TASKS_DIR.mkdir(parents=True, exist_ok=True)
    _REGISTRY.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _is_pid_alive(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True,
    )
    return str(pid) in result.stdout


def _parse_round(log_path: str) -> int | None:
    try:
        text = Path(log_path).read_text(errors="ignore")
        matches = re.findall(r"[Rr]ound[:\s]+(\d+)", text)
        if matches:
            return int(matches[-1])
    except Exception:
        pass
    return None


def _tail_text(path: Path, max_chars: int = 20000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="ignore")
        return data if len(data) <= max_chars else data[-max_chars:]
    except Exception:
        return ""


def _find_latest_script(output_dir: Path) -> Path | None:
    scripts_dir = output_dir / "scripts"
    if not scripts_dir.exists():
        return None
    best_idx = -1
    best_file: Path | None = None
    for p in scripts_dir.glob("*.py"):
        try:
            idx = int(p.stem)
        except ValueError:
            continue
        if idx > best_idx:
            best_idx = idx
            best_file = p
    return best_file


def _detect_script_syntax_error(script_path: Path) -> str | None:
    try:
        source = script_path.read_text(encoding="utf-8", errors="strict")
    except Exception:
        return None
    try:
        compile(source, str(script_path), "exec")
    except SyntaxError as exc:
        msg = (exc.msg or "invalid syntax").strip()
        line = exc.lineno or 0
        return f"Generated Blender script syntax error at {script_path}:{line} ({msg})"
    except Exception:
        return None
    return None


def _diagnose_failure(task: dict, returncode: int | None = None) -> str | None:
    output_dir = Path(task.get("output_dir", ""))
    log_path = Path(task.get("log_path", ""))

    latest_script = _find_latest_script(output_dir) if output_dir.exists() else None
    if latest_script:
        syntax_issue = _detect_script_syntax_error(latest_script)
        if syntax_issue:
            return syntax_issue

    if log_path.exists():
        tail = _tail_text(log_path)
        if "Traceback (most recent call last):" in tail:
            return "VIGA traceback detected in task log"
        if "Call tool execute_and_evaluate..." in tail and "=== Round 2 ===" not in tail:
            return "Likely stalled or failed inside execute_and_evaluate during round 1"

    if returncode is not None and returncode != 0:
        return f"VIGA process exited with non-zero return code {returncode}"
    return None


def _build_target_description(task_desc: str) -> str:
    if not _ASCII_GUARD_ENABLED:
        return task_desc
    guard = (
        "\n\n[Code constraints]\n"
        "- Return syntactically valid Python code.\n"
        "- Use ASCII characters for identifiers, keyword names, and argument names only.\n"
        "- Do not insert non-ASCII characters into Python parameter names."
    )
    return task_desc + guard


def _normalize_task_entry(task: dict) -> None:
    task.setdefault("exit_code", None)
    task.setdefault("failure_reason", None)
    if task.get("status") == "interrupted":
        task["status"] = "failed"
        if not task.get("failure_reason"):
            task["failure_reason"] = _diagnose_failure(task) or "Process interrupted"


def _best_output_file(output_dir: Path) -> str | None:
    """Return the best output file from an output directory.

    Preference order:
    1. Latest GLB file (dynamic_scene exports).
    2. Latest generator PNG: highest-numbered renders/<N>/*.png directory.
    3. Any PNG under renders/ (fallback).
    Returns None if nothing is found.
    """
    if not output_dir.exists():
        return None
    glb_files = sorted(output_dir.glob("**/*.glb"))
    if glb_files:
        return str(glb_files[-1])
    renders_dir = output_dir / "renders"
    if renders_dir.exists():
        numbered = sorted(
            [d for d in renders_dir.iterdir() if d.is_dir() and d.name.isdigit()],
            key=lambda d: int(d.name),
            reverse=True,
        )
        for rd in numbered:
            pngs = sorted(rd.glob("*.png"))
            if pngs:
                return str(pngs[-1])
    fallback = sorted(output_dir.glob("renders/**/*.png"))
    return str(fallback[0]) if fallback else None


def _refresh_running(reg: dict) -> dict:
    """Check PIDs of all 'running' tasks; mark as completed or failed.
    Called on every status check to recover stale entries after a server restart.
    """
    for task in reg.values():
        _normalize_task_entry(task)
        if task.get("status") != "running":
            continue
        pid = task.get("pid")
        if not pid or _is_pid_alive(pid):
            continue
        output_dir = Path(task.get("output_dir", ""))
        out_file   = _best_output_file(output_dir)
        if out_file:
            task["status"]      = "completed"
            task["output_file"] = out_file
        else:
            task["status"] = "failed"
            task["failure_reason"] = _diagnose_failure(task) or "Process exited unexpectedly"
        task["completed_at"] = datetime.now(timezone.utc).isoformat()
    return reg


# -- Actions -------------------------------------------------------------------

async def _start(params: dict) -> dict:
    task_desc       = (params.get("task") or "").strip()
    scene_mode      = (params.get("scene_mode") or "").strip()
    _mr             = params.get("max_rounds")
    max_rounds      = int(_mr) if _mr else None
    task_root_input = (params.get("task_root") or "").strip()
    model           = (params.get("model") or _MODEL).strip()
    prompt_default  = "init" if scene_mode == "dynamic_scene" else "none"
    prompt_setting  = (params.get("prompt_setting") or prompt_default).strip()
    task_name_input = (params.get("task_name") or "").strip()
    image_path      = (params.get("target_image") or "").strip()
    gpu_devices     = (params.get("gpu_devices") or os.getenv("VIGA_GPU_DEVICES", "0")).strip()

    if not task_desc:
        return {"error": "task description is required for start"}
    if not scene_mode:
        return {"error": "scene_mode is required: 'static_scene' (no motion) or 'dynamic_scene' (animated/physics)"}
    if scene_mode not in {"static_scene", "dynamic_scene"}:
        return {"error": "scene_mode must be one of: static_scene, dynamic_scene"}
    if not image_path:
        return {"error": "target_image is required when running via scene runners"}

    source = Path(image_path)
    if not source.exists():
        return {"error": f"target_image not found: {image_path}"}

    if scene_mode == "dynamic_scene":
        default_generator_tools = (
            "tools/blender/exec.py,tools/generator_base.py,tools/initialize_plan.py,tools/sam3d/init.py"
        )
    else:
        default_generator_tools = "tools/blender/exec.py,tools/generator_base.py,tools/initialize_plan.py"
    generator_tools = (params.get("generator_tools") or default_generator_tools).strip()
    verifier_tools  = (
        params.get("verifier_tools") or "tools/blender/investigator.py,tools/verifier_base.py"
    )

    task_id   = uuid.uuid4().hex[:8]
    raw_name  = task_name_input or task_id
    task_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._") or task_id

    task_root_dir = _resolve_task_root(task_root_input, scene_mode)
    task_dir = task_root_dir / task_name
    if task_dir.exists():
        task_name = f"{task_name}_{task_id}"
        task_dir  = task_root_dir / task_name

    assets_dir = task_dir / "assets"
    log_path   = task_dir / "viga.log"

    task_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    if source.is_dir():
        target_dir = task_dir / "target"
        shutil.rmtree(target_dir, ignore_errors=True)
        shutil.copytree(source, target_dir)
        copied_target_path = target_dir
    else:
        ext = source.suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg"}:
            return {"error": "target_image must be a .png, .jpg, or .jpeg file"}
        copied_target_path = task_dir / f"target{ext}"
        shutil.copy2(source, copied_target_path)

    (task_dir / "description.txt").write_text(
        _build_target_description(task_desc), encoding="utf-8"
    )

    output_dir = _VIGA_DIR / "output" / scene_mode / task_id / task_name

    wsl_script = _build_wsl_runner_command(
        scene_mode=scene_mode,
        task_name=task_name,
        model=model,
        max_rounds=max_rounds,
        generator_tools=generator_tools,
        verifier_tools=verifier_tools,
        prompt_setting=prompt_setting,
        test_id=task_id,
        conda_env=_CONDA_ENV_NAME,
        gpu_devices=gpu_devices,
    )

    log_file = open(log_path, "w", encoding="utf-8")
    proc = await asyncio.create_subprocess_exec(
        "wsl", "bash", "-lc", wsl_script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        env=os.environ.copy(),
    )

    async with _lock:
        reg = _load_registry()
        reg[task_id] = {
            "id":             task_id,
            "task":           task_desc,
            "scene_mode":     scene_mode,
            "task_name":      task_name,
            "task_root":      str(task_root_dir),
            "target_image":   str(copied_target_path),
            "output_dir":     str(output_dir),
            "log_path":       str(log_path),
            "pid":            proc.pid,
            "status":         "running",
            "started_at":     datetime.now(timezone.utc).isoformat(),
            "completed_at":   None,
            "output_file":    None,
            "exit_code":      None,
            "failure_reason": None,
            "wsl_command":    wsl_script,
        }
        _save_registry(reg)

    async def _watch():
        stalled = False
        while True:
            if proc.returncode is not None:
                break
            await asyncio.sleep(max(1, _STALL_POLL_SEC))
            if proc.returncode is not None:
                break
            try:
                age = time.time() - log_path.stat().st_mtime
            except Exception:
                continue
            if age >= _STALL_TIMEOUT_SEC:
                stalled = True
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, text=True,
                )
                break

        await proc.wait()
        log_file.close()
        async with _lock:
            reg = _load_registry()
            if task_id not in reg:
                return
            output_dir_path = Path(output_dir)
            out_file = _best_output_file(output_dir_path)
            status = "completed" if proc.returncode == 0 else "failed"
            failure_reason = None
            if stalled:
                status = "failed"
                failure_reason = (
                    f"No log updates for {_STALL_TIMEOUT_SEC} seconds; process killed as stalled"
                )
            if status != "completed":
                failure_reason = failure_reason or _diagnose_failure(reg[task_id], proc.returncode)

            output_log_path = None
            try:
                output_dir_path.mkdir(parents=True, exist_ok=True)
                dest_log = output_dir_path / "viga.log"
                shutil.copy2(log_path, dest_log)
                output_log_path = str(dest_log)
            except Exception:
                pass

            reg[task_id]["status"]         = status
            reg[task_id]["completed_at"]   = datetime.now(timezone.utc).isoformat()
            reg[task_id]["output_file"]    = out_file
            reg[task_id]["output_log"]     = output_log_path or str(log_path)
            reg[task_id]["exit_code"]      = proc.returncode
            reg[task_id]["failure_reason"] = failure_reason
            _save_registry(reg)

    asyncio.create_task(_watch())

    return {
        "task_id":    task_id,
        "status":     "started",
        "pid":        proc.pid,
        "task_name":  task_name,
        "task_dir":   str(task_dir),
        "output_dir": str(output_dir),
        "log":        str(log_path),
        "message": (
            f"VIGA task '{task_id}' started (PID {proc.pid}). "
            f"Data folder: {task_dir}. "
            f"Output will appear in: {output_dir}"
        ),
    }


async def _stop(params: dict) -> dict:
    task_id = (params.get("task_id") or "").strip()
    if not task_id:
        return {"error": "task_id is required for stop"}

    async with _lock:
        reg  = _load_registry()
        task = reg.get(task_id)
        if not task:
            return {"error": f"Task '{task_id}' not found"}
        if task["status"] != "running":
            return {"error": f"Task '{task_id}' is not running (status: {task['status']})"}

        pid = task.get("pid")
        if pid:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)

        task["status"]         = "cancelled"
        task["completed_at"]   = datetime.now(timezone.utc).isoformat()
        task["failure_reason"] = "Cancelled by user"
        _save_registry(reg)

    return {"task_id": task_id, "status": "cancelled", "message": f"Task '{task_id}' cancelled."}


async def _status(params: dict) -> dict:
    task_id = (params.get("task_id") or "").strip()

    async with _lock:
        reg = _refresh_running(_load_registry())
        _save_registry(reg)

    if task_id:
        task = reg.get(task_id)
        if not task:
            return {"error": f"Task '{task_id}' not found"}
        current_round = _parse_round(task["log_path"]) if task.get("log_path") else None
        return {**task, "current_round": current_round}

    summary = []
    for t in reg.values():
        current_round = (
            _parse_round(t["log_path"])
            if t.get("log_path") and t["status"] == "running"
            else None
        )
        summary.append({
            "id":            t["id"],
            "task":          t["task"],
            "status":        t["status"],
            "current_round": current_round,
            "started_at":    t["started_at"],
            "output_file":   t.get("output_file"),
        })

    return {"tasks": summary, "total": len(summary)}


# -- Entry point ---------------------------------------------------------------

async def run(input: dict) -> dict:
    action = (input.get("action") or "").strip()
    if action == "start":
        return await _start(input)
    if action == "stop":
        return await _stop(input)
    if action == "status":
        return await _status(input)
    return {"error": f"Unknown action '{action}'. Use: start, stop, status"}
