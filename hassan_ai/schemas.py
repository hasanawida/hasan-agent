"""Typed records shared by the orchestrator, memory store and API."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    awaiting_approval = "awaiting_approval"
    completed = "completed"
    failed = "failed"
    rejected = "rejected"


class Mode(str, Enum):
    fast = "fast"
    auto = "auto"
    consensus = "consensus"


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    mode: Mode = Mode.auto
    workspace: str | None = None
    execute: bool = False
    project: str | None = None
    kind: Literal["project", "operate"] = "project"
    origin: str | None = None  # e.g. "telegram:<chat id>" or "schedule:<id>"
    budget: Literal["auto", "free", "balanced", "best"] = "auto"


class AgentOutput(BaseModel):
    agent: str
    model: str
    content: str
    started_at: float
    finished_at: float
    ok: bool = True
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class Evidence(BaseModel):
    kind: str  # inspect | git_status | git_diff | build | test | mcp | write
    title: str
    ok: bool
    summary: str
    detail: str = ""
    command: list[str] | None = None
    exit_code: int | None = None
    duration: float | None = None
    media: str | None = None  # file name under data/media (photo, screenshot, recording, render)


class FileChange(BaseModel):
    path: str
    action: Literal["write", "delete"] = "write"
    content: str = ""
    reason: str = ""


class ChangePlan(BaseModel):
    """Structured output the Coder must produce to change files."""

    summary: str = ""
    changes: list[FileChange] = Field(default_factory=list)
    verify: list[str] = Field(default_factory=list)  # capability names, e.g. ["build", "test"]


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: new_id("apr"))
    task_id: str
    action: str
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    diff: str = ""
    status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: float = Field(default_factory=now)
    decided_at: float | None = None


class TaskRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    prompt: str
    mode: Mode
    resolved_mode: Mode | None = None
    workspace: str | None = None
    execute: bool = False
    project: str | None = None
    kind: str = "project"
    origin: str | None = None
    budget: str = "auto"
    tier: str | None = None  # simple | medium | complex (decides which brains are used)
    paid_ok: bool | None = None  # may this task use paid brains? (None = not decided yet)
    paid_asked: bool = False
    status: TaskStatus = TaskStatus.queued
    phase: str = "queued"
    outputs: list[AgentOutput] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    change_plan: ChangePlan | None = None
    checkpoint: dict[str, Any] | None = None
    decision: str | None = None
    verified: bool | None = None
    repair_round: int = 0
    error: str | None = None
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)


class Event(BaseModel):
    task_id: str
    kind: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=now)
