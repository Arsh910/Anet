"""
schema.py — Pydantic models for the ANet Plugin Protocol.

Every agent that joins ANet must supply an agent.yaml that validates
against AgentManifest. The registry stores RegistryEntry objects.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AgentIdentity(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""


class ModelConfig(BaseModel):
    name: str
    provider: Literal["google", "anthropic", "openai", "openrouter"]
    temperature: float = 0.2
    max_tokens: int = 2048


class CapabilityConfig(BaseModel):
    task_types: list[str]


class ToolDefinition(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    file: str                  # path relative to agent folder
    description: str = ""      # human-readable, optional
    # "async" is a reserved Python keyword, so the YAML/JSON key "async" maps via alias
    async_tool: bool = Field(False, alias="async")  # tool returns task_id immediately; result arrives later
    poll_path: str = ""         # relative path to JSON registry file for polling
    result_key: str = ""        # key under which the async result is stored


class BehaviorConfig(BaseModel):
    timeout: int = 30
    max_retries: int = 2
    requires_confirmation: bool = False   # pause and ask user before executing
    can_be_parallelized: bool = True      # planner may batch this with other steps
    execution: Literal["sync", "async"] = "sync"   # async = returns task_id immediately


class AgentManifest(BaseModel):
    identity: AgentIdentity
    model: Optional[ModelConfig] = None
    capabilities: Optional[CapabilityConfig] = None
    tools: list[ToolDefinition] = Field(default_factory=list)
    prompt: Optional[dict] = None   # {"file": "..."} OR {"inline": "..."}
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)
    attach_to: list[str] = Field(default_factory=list)  # agent names to inject tools into

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def is_tool_extension(self) -> bool:
        """True when this plugin only adds tools to existing agents."""
        return bool(self.attach_to) and self.model is None


class RegistryEntry(BaseModel):
    agent_id: str              # did:anet:<hash>::<name>
    registered_at: str
    status: Literal["idle", "running", "disabled"] = "idle"
    path: str                  # absolute path to agent folder
    manifest: AgentManifest
    # Phase-2 identity fields (optional, unused locally)
    owner_id: str = ""
    owner_name: str = ""
    signature: str = ""


# ── Validation result types ───────────────────────────────────────────────────

class CheckResult(BaseModel):
    name: str
    passed: bool
    message: str = ""


class ValidationResult(BaseModel):
    passed: bool
    checks: list[CheckResult]
    errors: list[str]
    warnings: list[str]
