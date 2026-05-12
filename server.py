"""
server.py — ANet Web Dashboard API

FastAPI backend for the ANet dashboard UI.
Runs independently from main.py — reads registry + agents_config at runtime.

Start with:  uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

from anet.plugin.registry import list_agents, update_status, add_agent, get_agent
from anet.plugin.schema import (
    RegistryEntry, AgentManifest, AgentIdentity,
    ModelConfig, CapabilityConfig, BehaviorConfig, ToolDefinition,
)
from anet.AnetAgents.agents_config import AGENTS

app = FastAPI(title="ANet Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── In-memory runtime state ───────────────────────────────────────────────────

_runtime_status: dict[str, str] = {}     # agent_name → status override
_tasks_today:    dict[str, int] = {}     # agent_name → count
_current_tasks:  dict[str, dict | None] = {}   # agent_name → current task
_task_history:   list[dict] = []


def _now_iso() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ── WebSocket log manager ─────────────────────────────────────────────────────

class LogManager:
    def __init__(self) -> None:
        self._clients: list[WebSocket] = []
        self._history: list[dict] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)
        for entry in self._history[-80:]:
            try:
                await ws.send_json(entry)
            except Exception:
                pass

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._clients:
            self._clients.remove(ws)

    async def broadcast(self, entry: dict) -> None:
        self._history.append(entry)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(entry)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def emit(self, agent: str, type_: str, message: str) -> None:
        await self.broadcast({
            "time":    _now_iso(),
            "agent":   agent,
            "type":    type_,
            "message": message,
        })


log_manager = LogManager()


# ── Agent data helpers ────────────────────────────────────────────────────────

def _builtin_to_api(a: dict) -> dict:
    name = a["name"]
    enabled = a.get("enabled", False)
    default_status = "idle" if enabled else "disabled"
    return {
        "agent_id":    f"did:anet:builtin::{name}",
        "name":        name,
        "status":      _runtime_status.get(name, default_status),
        "owner_name":  "Built-in",
        "owner_id":    "did:anet:builtin",
        "network_id":  "anet://localhost::v1",
        "model":       a.get("model", ""),
        "provider":    a.get("provider", ""),
        "tools":       [{"name": t, "async_tool": False} for t in a.get("tools", [])],
        "task_types":  a.get("task_types", []),
        "current_task": _current_tasks.get(name),
        "tasks_today": _tasks_today.get(name, 0),
        "registered_at": "2026-01-01",
        "_builtin":    True,
        "enabled":     enabled,
    }


def _registry_to_api(entry: RegistryEntry) -> dict:
    m    = entry.manifest
    name = m.name
    return {
        "agent_id":    entry.agent_id,
        "name":        name,
        "status":      _runtime_status.get(name, entry.status),
        "owner_name":  entry.owner_name or "Local",
        "owner_id":    entry.owner_id or "did:anet:local",
        "network_id":  "anet://localhost::v1",
        "model":       m.model.name if m.model else "",
        "provider":    m.model.provider if m.model else "",
        "tools":       [{"name": t.name, "async_tool": t.async_tool} for t in m.tools],
        "task_types":  m.capabilities.task_types if m.capabilities else [],
        "current_task": _current_tasks.get(name),
        "tasks_today": _tasks_today.get(name, 0),
        "registered_at": str(entry.registered_at)[:10],
        "_builtin":    False,
        "enabled":     entry.status != "disabled",
        "behavior": {
            "timeout":               m.behavior.timeout,
            "max_retries":           m.behavior.max_retries,
            "requires_confirmation": m.behavior.requires_confirmation,
            "can_be_parallelized":   m.behavior.can_be_parallelized,
            "execution":             m.behavior.execution,
        },
    }


def _all_agents() -> list[dict]:
    builtin_names = {a["name"] for a in AGENTS}
    out: list[dict] = [_builtin_to_api(a) for a in AGENTS]
    for entry in list_agents():
        if entry.manifest.name not in builtin_names:
            out.append(_registry_to_api(entry))
    return out


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/agents")
async def get_agents():
    return _all_agents()


@app.get("/agents/scan")
async def scan_agent_folder(path: str):
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    yaml_file = p / "agent.yaml"
    tools: list[str] = []

    tools_dir = p / "tools"
    if tools_dir.exists():
        tools = [f.stem for f in tools_dir.glob("*.py") if not f.stem.startswith("_")]
    elif yaml_file.exists():
        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            tools = [t["name"] for t in raw.get("tools", [])]
        except Exception:
            pass

    name = ""
    model = ""
    provider = ""
    task_types: list[str] = []
    description = ""

    if yaml_file.exists():
        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            name        = raw.get("identity", {}).get("name", p.name)
            description = raw.get("identity", {}).get("description", "")
            model       = raw.get("model", {}).get("name", "") if raw.get("model") else ""
            provider    = raw.get("model", {}).get("provider", "") if raw.get("model") else ""
            task_types  = raw.get("capabilities", {}).get("task_types", []) if raw.get("capabilities") else []
        except Exception:
            pass

    return {
        "has_yaml":   yaml_file.exists(),
        "tools":      tools,
        "name":       name,
        "model":      model,
        "provider":   provider,
        "task_types": task_types,
        "description": description,
    }


@app.get("/agents/{name}")
async def get_agent_detail(name: str):
    for a in _all_agents():
        if a["name"] == name:
            return a
    raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")


@app.patch("/agents/{name}/toggle")
async def toggle_agent(name: str):
    # Check registry first (plugin agents)
    entry = get_agent(name)
    if entry:
        new_status = "idle" if entry.status == "disabled" else "disabled"
        update_status(name, new_status)
        _runtime_status[name] = new_status
        await log_manager.emit(
            "manager", "info",
            f"Agent '{name}' {'enabled' if new_status == 'idle' else 'disabled'}"
        )
        return {"name": name, "status": new_status}

    # Built-in agent — toggle runtime status only (doesn't persist across restarts)
    current = _runtime_status.get(name, "idle")
    new_status = "disabled" if current != "disabled" else "idle"
    _runtime_status[name] = new_status
    await log_manager.emit(
        "manager", "info",
        f"Agent '{name}' {'enabled' if new_status == 'idle' else 'disabled'} (runtime only)"
    )
    return {"name": name, "status": new_status}


class RegisterRequest(BaseModel):
    path: str
    name: Optional[str] = None


@app.post("/agents/register")
async def register_agent(req: RegisterRequest):
    p = Path(req.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {req.path}")

    yaml_file = p / "agent.yaml"
    if not yaml_file.exists():
        raise HTTPException(status_code=400, detail="agent.yaml not found in this folder")

    try:
        raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        manifest = AgentManifest(**raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid agent.yaml: {exc}")

    import hashlib
    h = hashlib.sha256(str(p).encode()).hexdigest()[:8]
    agent_id = f"did:anet:{h}::{manifest.name}"

    entry = RegistryEntry(
        agent_id=agent_id,
        registered_at=datetime.now().isoformat(),
        status="idle",
        path=str(p.resolve()),
        manifest=manifest,
        owner_name="Local",
    )
    add_agent(entry)

    await log_manager.emit("manager", "ok", f"Agent '{manifest.name}' registered from {req.path}")
    return _registry_to_api(entry)


@app.get("/tasks")
async def get_tasks(agent: Optional[str] = None):
    if agent:
        return [t for t in _task_history if t.get("agent") == agent]
    return _task_history


# ── Log ingestion (called by main.py or other processes) ─────────────────────

@app.post("/log")
async def ingest_log(entry: dict = Body(...)):
    """Accept a log event from main.py and broadcast to all WS clients."""
    required = {"time", "agent", "type", "message"}
    if not required.issubset(entry.keys()):
        entry.setdefault("time", _now_iso())
        entry.setdefault("agent", "system")
        entry.setdefault("type", "info")
        entry.setdefault("message", str(entry))

    agent  = entry.get("agent", "")
    type_  = entry.get("type", "info")
    msg    = entry.get("message", "")

    # Update runtime status from log messages
    if "running" in msg.lower() or "executing" in msg.lower():
        _runtime_status[agent] = "running"
    elif "offloaded" in msg.lower() or "async" in msg.lower():
        _runtime_status[agent] = "async"
    elif any(x in msg.lower() for x in ("done", "complete", "success", "idle")):
        _runtime_status[agent] = "idle"

    await log_manager.broadcast(entry)
    return {"ok": True}


# ── Status push (from main.py runtime) ───────────────────────────────────────

@app.post("/agents/{name}/status")
async def push_agent_status(name: str, body: dict = Body(...)):
    status = body.get("status", "idle")
    _runtime_status[name] = status
    if body.get("current_task"):
        _current_tasks[name] = body["current_task"]
    elif status == "idle":
        _current_tasks[name] = None
    if body.get("task_done"):
        _tasks_today[name] = _tasks_today.get(name, 0) + 1
    return {"ok": True}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/log")
async def ws_log(ws: WebSocket):
    await log_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()   # keep-alive / ignore client messages
    except WebSocketDisconnect:
        log_manager.disconnect(ws)


# ── Demo log emitter (runs when no real ANet is connected) ───────────────────

_DEMO_LOGS = [
    ("manager",        "info",    "Planning request from user"),
    ("research_agent", "run",     "using tool: web_search..."),
    ("research_agent", "ok",      "Found 5 relevant results"),
    ("manager",        "info",    "Routing to file_agent"),
    ("file_agent",     "run",     "using tool: file_tool [read_file]..."),
    ("file_agent",     "ok",      "File read successfully"),
    ("checker_agent",  "info",    "checker: validating..."),
    ("checker_agent",  "ok",      "checker: success — task completed"),
    ("manager",        "info",    "Synthesising final response..."),
    ("viga_agent",     "run",     "using tool: viga_tool — render started"),
    ("viga_agent",     "info",    "offloaded → task a3f9b2c1..."),
    ("tele_agent",     "ok",      "Message sent to Telegram"),
    ("computer_agent", "run",     "using tool: open_app [find_path]..."),
    ("manager",        "ok",      "All steps complete"),
]

_demo_running = False


async def _demo_emitter():
    global _demo_running
    _demo_running = True
    await asyncio.sleep(3)
    i = 0
    while True:
        await asyncio.sleep(4.5)
        if log_manager._clients:
            agent, type_, msg = _DEMO_LOGS[i % len(_DEMO_LOGS)]
            await log_manager.emit(agent, type_, msg)
            i += 1


@app.on_event("startup")
async def startup():
    asyncio.create_task(_demo_emitter())
    # Seed mock task history
    today = str(date.today())
    _task_history.extend([
        {"id": "t001", "agent": "research_agent", "task": "Find latest AI news", "status": "success", "date": today, "elapsed": 4.2},
        {"id": "t002", "agent": "file_agent",     "task": "Write report to disk", "status": "success", "date": today, "elapsed": 1.1},
        {"id": "t003", "agent": "viga_agent",     "task": "Render water bottle 3D model", "status": "running", "date": today, "elapsed": 142.0},
        {"id": "t004", "agent": "computer_agent", "task": "Open Notepad and type notes", "status": "success", "date": today, "elapsed": 2.8},
        {"id": "t005", "agent": "tele_agent",     "task": "Send summary to Telegram", "status": "success", "date": today, "elapsed": 0.9},
    ])
    _tasks_today.update({
        "research_agent": 3,
        "file_agent":     2,
        "computer_agent": 1,
        "viga_agent":     1,
        "tele_agent":     1,
    })
    _current_tasks["viga_agent"] = {
        "description": "Render water bottle 3D model",
        "step": 1,
        "total_steps": 1,
        "elapsed": 142,
    }
    _runtime_status["viga_agent"] = "async"
