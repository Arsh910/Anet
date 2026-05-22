"""
server.py — ANet Web Dashboard API

FastAPI backend for the ANet dashboard UI.
Runs independently from main.py — reads exanet.config.yaml + agents_config at runtime.

Start with:  uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

from anet.AnetAgents.agents_config import AGENTS

_ROOT      = Path(__file__).parent
_EX_CONFIG = _ROOT / "exanet.config.yaml"

app = FastAPI(title="ANet Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── In-memory runtime state ───────────────────────────────────────────────────

_runtime_status: dict[str, str] = {}
_tasks_today:    dict[str, int] = {}
_current_tasks:  dict[str, dict | None] = {}
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

def _load_ex_agents() -> list[dict]:
    """Read ExAgents from exanet.config.yaml."""
    if not _EX_CONFIG.exists():
        return []
    try:
        raw = yaml.safe_load(_EX_CONFIG.read_text(encoding="utf-8")) or {}
        return raw.get("agents") or []
    except Exception:
        return []


def _builtin_to_api(a: dict) -> dict:
    name    = a["name"]
    enabled = a.get("enabled", False)
    return {
        "agent_id":      f"did:anet:builtin::{name}",
        "name":          name,
        "status":        _runtime_status.get(name, "idle" if enabled else "disabled"),
        "owner_name":    "Built-in",
        "owner_id":      "did:anet:builtin",
        "network_id":    "anet://localhost::v1",
        "model":         a.get("model", ""),
        "provider":      a.get("provider", ""),
        "tools":         [{"name": t, "async_tool": False} for t in a.get("tools", [])],
        "task_types":    a.get("task_types", []),
        "current_task":  _current_tasks.get(name),
        "tasks_today":   _tasks_today.get(name, 0),
        "registered_at": "—",
        "_builtin":      True,
        "enabled":       enabled,
    }


def _external_to_api(spec: dict) -> dict:
    name    = spec.get("name", "")
    enabled = spec.get("enabled", True)
    tools   = spec.get("tools") or []
    mcp     = spec.get("mcp") or []
    all_tools = [{"name": t, "async_tool": False} for t in tools] + \
                [{"name": f"[mcp] {s}", "async_tool": False} for s in mcp]
    return {
        "agent_id":      f"did:anet:external::{name}",
        "name":          name,
        "status":        _runtime_status.get(name, "idle" if enabled else "disabled"),
        "owner_name":    "External",
        "owner_id":      "did:anet:external",
        "network_id":    "anet://localhost::v1",
        "model":         spec.get("model", ""),
        "provider":      spec.get("provider", ""),
        "tools":         all_tools,
        "task_types":    spec.get("task_types") or [],
        "current_task":  _current_tasks.get(name),
        "tasks_today":   _tasks_today.get(name, 0),
        "registered_at": "exanet.config.yaml",
        "_builtin":      False,
        "enabled":       enabled,
    }


def _all_agents() -> list[dict]:
    builtin_names = {a["name"] for a in AGENTS}
    out = [_builtin_to_api(a) for a in AGENTS]
    for spec in _load_ex_agents():
        if spec.get("name") not in builtin_names:
            out.append(_external_to_api(spec))
    return out


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/agents")
async def get_agents():
    return _all_agents()


@app.get("/agents/{name}")
async def get_agent_detail(name: str):
    for a in _all_agents():
        if a["name"] == name:
            return a
    raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")


@app.patch("/agents/{name}/toggle")
async def toggle_agent(name: str):
    current    = _runtime_status.get(name, "idle")
    new_status = "disabled" if current != "disabled" else "idle"
    _runtime_status[name] = new_status
    await log_manager.emit(
        "manager", "info",
        f"Agent '{name}' {'enabled' if new_status == 'idle' else 'disabled'} (runtime only)"
    )
    return {"name": name, "status": new_status}


@app.get("/tasks")
async def get_tasks(agent: Optional[str] = None):
    if agent:
        return [t for t in _task_history if t.get("agent") == agent]
    return _task_history


# ── Log ingestion (called by main.py or other processes) ─────────────────────

@app.post("/log")
async def ingest_log(entry: dict = Body(...)):
    entry.setdefault("time",    _now_iso())
    entry.setdefault("agent",   "system")
    entry.setdefault("type",    "info")
    entry.setdefault("message", "")

    agent = entry.get("agent", "")
    msg   = entry.get("message", "").lower()
    if "running" in msg or "executing" in msg:
        _runtime_status[agent] = "running"
    elif "offloaded" in msg or "async" in msg:
        _runtime_status[agent] = "async"
    elif any(x in msg for x in ("done", "complete", "success", "idle")):
        _runtime_status[agent] = "idle"

    await log_manager.broadcast(entry)
    return {"ok": True}


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
            await ws.receive_text()
    except WebSocketDisconnect:
        log_manager.disconnect(ws)


# ── Demo log emitter ──────────────────────────────────────────────────────────

_DEMO_LOGS = [
    ("manager",        "info", "Planning request from user"),
    ("research_agent", "run",  "using tool: web_search..."),
    ("research_agent", "ok",   "Found 5 relevant results"),
    ("manager",        "info", "Routing to code_agent"),
    ("code_agent",     "run",  "using tool: edit_tool..."),
    ("code_agent",     "ok",   "File edited successfully"),
    ("checker_agent",  "info", "checker: validating..."),
    ("checker_agent",  "ok",   "checker: success — task completed"),
    ("tele_agent",     "ok",   "Message sent to Telegram"),
    ("computer_agent", "run",  "using tool: open_app [find_path]..."),
    ("manager",        "ok",   "All steps complete"),
]


async def _demo_emitter():
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
    today = str(date.today())
    _task_history.extend([
        {"id": "t001", "agent": "research_agent", "task": "Find latest AI news",           "status": "success", "date": today, "elapsed": 4.2},
        {"id": "t002", "agent": "code_agent",     "task": "Fix layout in Destinations.jsx", "status": "success", "date": today, "elapsed": 8.3},
        {"id": "t003", "agent": "computer_agent", "task": "Open Notepad and type notes",   "status": "success", "date": today, "elapsed": 2.8},
        {"id": "t004", "agent": "tele_agent",     "task": "Send summary to Telegram",      "status": "success", "date": today, "elapsed": 0.9},
    ])
    _tasks_today.update({
        "research_agent": 3,
        "code_agent":     2,
        "computer_agent": 1,
        "tele_agent":     1,
    })
