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


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    conversation_id: str | None = None
    transport: Literal["desktop", "telegram", "api"] = "desktop"
    user_id: str | None = None
    images: list[str] = Field(default_factory=list, max_length=8)


class ToolEvent(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class ChatResponse(BaseModel):
    conversation_id: str
    text: str
    tool_events: list[ToolEvent] = Field(default_factory=list)


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
    telegram_token: str | None = Field(default=None, max_length=500)
    llm_api_token: str | None = Field(default=None, max_length=2_000)
