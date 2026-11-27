from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .repositories import (
    DocumentRepository,
    MemoryRepository,
    ProjectRepository,
    ReminderRepository,
    TaskRepository,
)
from .schemas import MemoryCreate, MemoryUpdate, ReminderCreate, TaskCreate, TaskUpdate


class CreateTaskArgs(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10_000)
    priority: int = Field(default=0, ge=0, le=4)
    project_id: str | None = None
    due_at: datetime | None = None


class UpdateTaskArgs(BaseModel):
    task_id: str
    title: str | None = None
    description: str | None = None
    status: Literal["open", "in_progress", "completed", "cancelled"] | None = None
    priority: int | None = Field(default=None, ge=0, le=4)
    due_at: datetime | None = None


class GetTasksArgs(BaseModel):
    status: Literal["open", "in_progress", "completed", "cancelled"] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class CompleteTaskArgs(BaseModel):
    task_id: str


class SaveMemoryArgs(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    kind: Literal["fact", "preference", "entity", "summary"] = "fact"
    source_ref: str | None = None


class SearchMemoryArgs(BaseModel):
    query: str = ""
    kind: Literal["fact", "note", "preference", "entity", "summary"] | None = None
    limit: int = Field(default=10, ge=1, le=50)


class UpdateMemoryArgs(BaseModel):
    memory_id: str
    content: str | None = Field(default=None, min_length=1, max_length=100_000)
    kind: Literal["fact", "note", "preference", "entity", "summary"] | None = None


class CreateNoteArgs(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    source_ref: str | None = None


class CreateReminderArgs(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    due_at: datetime
    target_transport: Literal["desktop", "telegram"] = "desktop"
    target_user_id: str | None = None


class GetProjectsArgs(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)


class SearchDocumentsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)
    limit: int = Field(default=10, ge=1, le=50)


class SendTelegramArgs(BaseModel):
    user_id: str
    text: str = Field(min_length=1, max_length=4_000)


@dataclass(slots=True)
class ToolContext:
    transport: str
    user_id: str | None = None
    allow_external: bool = False
    agent_id: str = ""
    conversation_id: str | None = None
    run_id: str | None = None
    dry_run: bool = False
    confirmed_action_id: str | None = None


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    schema: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Awaitable[dict[str, Any]]]
    permission: Literal["read", "write", "external"] = "read"

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema.model_json_schema(),
            },
        }


class SystemTools:
    def __init__(
        self,
        tasks: TaskRepository,
        memories: MemoryRepository,
        projects: ProjectRepository,
        reminders: ReminderRepository,
        documents: DocumentRepository,
        telegram_sender: Callable[[str, str], Awaitable[None]] | None = None,
        control: Any | None = None,
    ) -> None:
        self.tasks = tasks
        self.memories = memories
        self.projects = projects
        self.reminders = reminders
        self.documents = documents
        self.telegram_sender = telegram_sender
        self.control = control
        self.specs: dict[str, ToolSpec] = {}
        self._register_all()

    def _add(
        self,
        name: str,
        description: str,
        schema: type[BaseModel],
        handler: Callable[[BaseModel, ToolContext], Awaitable[dict[str, Any]]],
        permission: Literal["read", "write", "external"] = "read",
    ) -> None:
        self.specs[name] = ToolSpec(name, description, schema, handler, permission)

    def _register_all(self) -> None:
        self._add("create_task", "Create an actionable task", CreateTaskArgs, self._create_task, "write")
        self._add("update_task", "Update fields of an existing task", UpdateTaskArgs, self._update_task, "write")
        self._add("get_tasks", "List tasks, optionally filtered by status", GetTasksArgs, self._get_tasks)
        self._add("complete_task", "Mark a task completed", CompleteTaskArgs, self._complete_task, "write")
        self._add("save_memory", "Save a durable user fact or preference", SaveMemoryArgs, self._save_memory, "write")
        self._add("search_memory", "Search durable local memory", SearchMemoryArgs, self._search_memory)
        self._add("update_memory", "Correct an existing memory", UpdateMemoryArgs, self._update_memory, "write")
        self._add("create_note", "Create a local note", CreateNoteArgs, self._create_note, "write")
        self._add("search_notes", "Search local notes", SearchMemoryArgs, self._search_notes)
        self._add("create_reminder", "Schedule a local or Telegram reminder", CreateReminderArgs, self._create_reminder, "write")
        self._add("get_projects", "List local projects", GetProjectsArgs, self._get_projects)
        self._add("search_documents", "Search indexed local documents", SearchDocumentsArgs, self._search_documents)
        self._add("send_telegram_message", "Send a Telegram message", SendTelegramArgs, self._send_telegram, "external")

    def schemas(self, allowed: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        names = allowed or tuple(self.specs)
        return [self.specs[name].openai_schema() for name in names if name in self.specs]

    async def execute(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        started = time.perf_counter()
        spec = self.specs.get(name)
        if spec is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        policy = "confirm" if spec.permission == "external" else "auto"
        timeout_seconds = 60
        if self.control is not None and context.agent_id:
            if not await self.control.agent_allows_tool(context.agent_id, name):
                result = {"ok": False, "error": "Tool is not allowed for this agent"}
                await self._record(name, arguments, context, "deny", result, started)
                return result
            policy = await self.control.tool_policy(context.agent_id, name, spec.permission)
            timeout_seconds = await self.control.tool_timeout(context.agent_id, name)
            if await self.control.agent_requires_confirmation(context.agent_id, spec.permission):
                policy = "confirm"
        if policy == "deny":
            result = {"ok": False, "error": "Tool execution is prohibited by backend policy"}
            await self._record(name, arguments, context, policy, result, started)
            return result
        if context.dry_run and spec.permission != "read":
            result = {
                "ok": True,
                "dry_run": True,
                "executed": False,
                "message": "Mutation validated but not executed in Dry Run mode",
            }
            try:
                spec.schema.model_validate(arguments)
            except ValidationError as exc:
                result = {"ok": False, "error": "Invalid tool arguments", "details": exc.errors()}
            await self._record(name, arguments, context, policy, result, started)
            return result
        requires_confirmation = policy == "confirm" or (
            spec.permission == "external" and not context.allow_external
        )
        if requires_confirmation and not context.confirmed_action_id:
            if self.control is None or not context.agent_id:
                return {"ok": False, "error": "External action requires explicit user confirmation"}
            try:
                spec.schema.model_validate(arguments)
            except ValidationError as exc:
                result = {"ok": False, "error": "Invalid tool arguments", "details": exc.errors()}
                await self._record(name, arguments, context, policy, result, started)
                return result
            approval = await self.control.create_approval(
                run_id=context.run_id,
                conversation_id=context.conversation_id,
                transport=context.transport,
                user_id=context.user_id,
                agent_id=context.agent_id,
                tool_name=name,
                arguments=arguments,
            )
            result = {
                "ok": False,
                "requires_confirmation": True,
                "approval_id": approval["id"],
                "message": "Action is waiting for user confirmation",
            }
            await self._record(name, arguments, context, policy, result, started)
            return result
        if context.confirmed_action_id and self.control is not None:
            approval = await self.control.get_approval(context.confirmed_action_id)
            if (
                not approval
                or approval["status"] not in {"pending", "approved"}
                or approval["agent_id"] != context.agent_id
                or approval["tool_name"] != name
                or approval["arguments"] != arguments
            ):
                result = {"ok": False, "error": "Invalid or expired action confirmation"}
                await self._record(name, arguments, context, policy, result, started)
                return result
        try:
            parsed = spec.schema.model_validate(arguments)
            result = spec.handler(parsed, context)
            if not inspect.isawaitable(result):
                raise RuntimeError("Tool handler must be async")
            resolved = await asyncio.wait_for(result, timeout=timeout_seconds)
        except ValidationError as exc:
            resolved = {"ok": False, "error": "Invalid tool arguments", "details": exc.errors()}
        except Exception as exc:
            resolved = {"ok": False, "error": str(exc)}
        await self._record(name, arguments, context, policy, resolved, started)
        if context.confirmed_action_id and self.control is not None:
            await self.control.resolve_approval(
                context.confirmed_action_id,
                "completed" if resolved.get("ok") else "failed",
                resolved,
            )
        return resolved

    async def _record(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        policy: str,
        result: dict[str, Any],
        started: float,
    ) -> None:
        if self.control is None:
            return
        status = (
            "pending_confirmation"
            if result.get("requires_confirmation")
            else "completed"
            if result.get("ok")
            else "failed"
        )
        await self.control.record_tool_call(
            run_id=context.run_id,
            agent_id=context.agent_id,
            tool_name=name,
            policy=policy,
            status=status,
            duration_ms=int((time.perf_counter() - started) * 1000),
            arguments=arguments,
            result=result,
        )

    async def approve(self, approval_id: str) -> dict[str, Any]:
        if self.control is None:
            return {"ok": False, "error": "Approval store is unavailable"}
        approval = await self.control.get_approval(approval_id)
        if not approval or approval["status"] != "pending":
            return {"ok": False, "error": "Pending approval not found"}
        await self.control.resolve_approval(approval_id, "approved")
        return await self.execute(
            approval["tool_name"],
            approval["arguments"],
            ToolContext(
                transport=approval["transport"],
                user_id=approval["user_id"],
                allow_external=True,
                agent_id=approval["agent_id"],
                conversation_id=approval["conversation_id"],
                run_id=approval["run_id"],
                confirmed_action_id=approval_id,
            ),
        )

    async def _create_task(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = CreateTaskArgs.model_validate(raw.model_dump())
        task = await self.tasks.create(TaskCreate(**args.model_dump(), source=context.transport))
        return {"ok": True, "task": task}

    async def _update_task(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = UpdateTaskArgs.model_validate(raw.model_dump())
        task_id = args.task_id
        task = await self.tasks.update(task_id, TaskUpdate(**args.model_dump(exclude={"task_id"})))
        return {"ok": task is not None, "task": task, "error": None if task else "Task not found"}

    async def _get_tasks(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = GetTasksArgs.model_validate(raw.model_dump())
        return {"ok": True, "tasks": await self.tasks.list(args.status, args.limit)}

    async def _complete_task(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = CompleteTaskArgs.model_validate(raw.model_dump())
        task = await self.tasks.update(args.task_id, TaskUpdate(status="completed"))
        return {"ok": task is not None, "task": task, "error": None if task else "Task not found"}

    async def _save_memory(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = SaveMemoryArgs.model_validate(raw.model_dump())
        memory = await self.memories.create(
            MemoryCreate(**args.model_dump(), source=context.transport)
        )
        return {"ok": True, "memory": memory}

    async def _search_memory(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = SearchMemoryArgs.model_validate(raw.model_dump())
        return {"ok": True, "memories": await self.memories.search(args.query, args.kind, args.limit)}

    async def _update_memory(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = UpdateMemoryArgs.model_validate(raw.model_dump())
        memory = await self.memories.update(
            args.memory_id, MemoryUpdate(**args.model_dump(exclude={"memory_id"}))
        )
        return {"ok": memory is not None, "memory": memory, "error": None if memory else "Memory not found"}

    async def _create_note(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = CreateNoteArgs.model_validate(raw.model_dump())
        memory = await self.memories.create(
            MemoryCreate(
                content=args.content,
                kind="note",
                source=context.transport,
                source_ref=args.source_ref,
            )
        )
        return {"ok": True, "note": memory}

    async def _search_notes(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = SearchMemoryArgs.model_validate(raw.model_dump())
        return {"ok": True, "notes": await self.memories.search(args.query, "note", args.limit)}

    async def _create_reminder(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = CreateReminderArgs.model_validate(raw.model_dump())
        target_user = args.target_user_id or context.user_id
        reminder = await self.reminders.create(
            ReminderCreate(**args.model_dump(exclude={"target_user_id"}), target_user_id=target_user)
        )
        return {"ok": True, "reminder": reminder}

    async def _get_projects(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = GetProjectsArgs.model_validate(raw.model_dump())
        return {"ok": True, "projects": await self.projects.list(args.limit)}

    async def _search_documents(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = SearchDocumentsArgs.model_validate(raw.model_dump())
        return {"ok": True, "documents": await self.documents.search(args.query, args.limit)}

    async def _send_telegram(self, raw: BaseModel, context: ToolContext) -> dict[str, Any]:
        args = SendTelegramArgs.model_validate(raw.model_dump())
        if self.telegram_sender is None:
            return {"ok": False, "error": "Telegram is not running"}
        await self.telegram_sender(args.user_id, args.text)
        return {"ok": True}
