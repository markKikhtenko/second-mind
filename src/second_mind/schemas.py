from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

TaskStatus = Literal["open", "in_progress", "completed", "cancelled"]


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10_000)
    priority: int = Field(default=0, ge=0, le=4)
    project_id: str | None = None
    due_at: datetime | None = None
    source: str = Field(default="user", max_length=100)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus | None = None
    priority: int | None = Field(default=None, ge=0, le=4)
    project_id: str | None = None
    due_at: datetime | None = None


class Task(TaskCreate):
    id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    kind: Literal["fact", "note", "preference", "entity", "summary"] = "fact"
    source: str = Field(default="user", max_length=100)
    source_ref: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=100_000)
    kind: Literal["fact", "note", "preference", "entity", "summary"] | None = None
    metadata: dict[str, Any] | None = None


class Memory(MemoryCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    status: Literal["active", "paused", "completed", "archived"] | None = None


class Project(ProjectCreate):
    id: str
    status: str
    created_at: datetime
    updated_at: datetime


class ReminderCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    due_at: datetime
    target_transport: Literal["desktop", "telegram"] = "desktop"
    target_user_id: str | None = None

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Reminder time must include a timezone offset")
        return value


class ReminderUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=2_000)
    due_at: datetime | None = None
    status: Literal["scheduled", "delivered", "cancelled"] | None = None

    @field_validator("due_at")
    @classmethod
    def require_update_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Reminder time must include a timezone offset")
        return value


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    conversation_id: str | None = None
    transport: Literal["desktop", "telegram", "api"] = "desktop"
    user_id: str | None = None
    images: list[str] = Field(default_factory=list, max_length=8)
    dry_run: bool = False


class ToolEvent(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class ChatResponse(BaseModel):
    conversation_id: str
    text: str
    tool_events: list[ToolEvent] = Field(default_factory=list)
    run_id: str | None = None
    agent_id: str | None = None
    skills: list[str] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)


class AgentConfigCreate(BaseModel):
    id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    system_prompt: str = Field(min_length=1, max_length=100_000)
    enabled: bool = True
    model: str | None = Field(default=None, max_length=500)
    inherit_global_model: bool = True
    temperature: float = Field(default=0.2, ge=0, le=2)
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    context_limit: int = Field(default=16_000, ge=1_024, le=1_000_000)
    generation_limit: int = Field(default=2_048, ge=64, le=100_000)
    timeout_seconds: int = Field(default=180, ge=5, le=900)
    max_tool_calls: int = Field(default=8, ge=0, le=64)
    tools: list[str] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=100)
    memory_policy: Literal["none", "relevant"] = "relevant"
    confirmation_policy: Literal["policy", "always"] = "policy"


class AgentConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=100_000)
    enabled: bool | None = None
    model: str | None = Field(default=None, max_length=500)
    inherit_global_model: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    context_limit: int | None = Field(default=None, ge=1_024, le=1_000_000)
    generation_limit: int | None = Field(default=None, ge=64, le=100_000)
    timeout_seconds: int | None = Field(default=None, ge=5, le=900)
    max_tool_calls: int | None = Field(default=None, ge=0, le=64)
    tools: list[str] | None = Field(default=None, max_length=100)
    skills: list[str] | None = Field(default=None, max_length=100)
    memory_policy: Literal["none", "relevant"] | None = None
    confirmation_policy: Literal["policy", "always"] | None = None


class SkillPayload(BaseModel):
    content: str = Field(min_length=1, max_length=500_000)
    enabled: bool = True


class SkillToggle(BaseModel):
    enabled: bool


class SkillPathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=32_000)


class ToolPolicyUpdate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=80)
    policy: Literal["auto", "confirm", "deny"]
    timeout_seconds: int = Field(default=60, ge=1, le=900)


class PlaygroundRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    agent_id: str | None = Field(default=None, max_length=80)
    skill_ids: list[str] = Field(default_factory=list, max_length=20)
    mode: Literal["dry_run", "execution"] = "dry_run"


class SettingsPatch(BaseModel):
    llm_base_url: str | None = None
    llm_model: str | None = None
    telegram_enabled: bool | None = None
    telegram_allowed_user_ids: list[int] | None = None
    whisper_model: str | None = None
    whisper_device: Literal["cpu", "cuda", "auto"] | None = None
    whisper_compute_type: str | None = None
    whisper_language: str | None = None
    setup_completed: bool | None = None
    autostart_enabled: bool | None = None
    update_repository: str | None = Field(default=None, max_length=200)
    automatic_update_checks: bool | None = None
    update_check_interval_hours: int | None = Field(default=None, ge=1, le=168)
    automatic_backups: bool | None = None
    backup_interval_hours: int | None = Field(default=None, ge=1, le=720)
    backup_retention_days: int | None = Field(default=None, ge=1, le=3650)
    telegram_token: str | None = Field(default=None, max_length=500)
    llm_api_token: str | None = Field(default=None, max_length=2_000)


class StoragePathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=32_000)
    confirmed: bool = False


class StorageExportRequest(BaseModel):
    path: str = Field(min_length=1, max_length=32_000)


class StorageCleanupRequest(BaseModel):
    confirmed: bool = False
