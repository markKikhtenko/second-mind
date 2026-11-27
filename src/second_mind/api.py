from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from . import __version__
from .agents import AGENTS
from .config import AppSettings
from .container import Container
from .llm import LLMUnavailable
from .repositories import utcnow
from .schemas import (
    ChatRequest,
    ChatResponse,
    MemoryCreate,
    MemoryUpdate,
    ProjectCreate,
    ProjectUpdate,
    ReminderCreate,
    ReminderUpdate,
    SettingsPatch,
    StorageCleanupRequest,
    StorageExportRequest,
    StoragePathRequest,
    TaskCreate,
    TaskUpdate,
)
from .storage import StorageError
from .system import diagnostics, set_windows_autostart
from .updates import UpdateError

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def public_settings(container: Container) -> dict[str, Any]:
    data = container.settings.model_dump(mode="json")
    data.update(
        {
            "telegram_token_configured": bool(container.secrets.get("telegram_bot_token")),
            "llm_api_token_configured": bool(container.secrets.get("llm_api_token")),
        }
    )
    return data


def create_app(container: Container | None = None) -> FastAPI:
    target = container or Container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await target.start()
        yield
        await target.stop()

    app = FastAPI(
        title="Second Mind Core",
        version=__version__,
        description="Loopback-only API for the Second Mind desktop and gateways.",
        lifespan=lifespan,
    )
    app.state.container = target

    @app.exception_handler(LLMUnavailable)
    async def llm_unavailable(_: Request, exc: LLMUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "component": "llm"})

    @app.exception_handler(UpdateError)
    async def update_error(_: Request, exc: UpdateError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "component": "updates"})

    @app.exception_handler(StorageError)
    async def storage_error(_: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "component": "storage"})

    router = APIRouter(prefix="/api")

    @router.get("/ping")
    async def ping() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "pid": os.getpid()}

    @router.get("/health")
    async def health() -> dict[str, Any]:
        llm, inbox = await asyncio.gather(target.llm.health(), target.inbox.counts())
        task_count = await target.db.fetch_one(
            "SELECT COUNT(*) AS count FROM tasks WHERE status IN ('open','in_progress')"
        )
        memory_count = await target.db.fetch_one("SELECT COUNT(*) AS count FROM memories")
        request_stats = await target.db.fetch_one(
            """SELECT COUNT(*) AS count, COALESCE(AVG(duration_ms),0) AS avg_ms
            FROM execution_log WHERE action='chat'"""
        )
        last_error = await target.db.fetch_one(
            "SELECT action,details_json,created_at FROM execution_log WHERE status='failed' ORDER BY created_at DESC LIMIT 1"
        )
        recent_activity = await target.logs.list(8)
        return {
            "status": "ok",
            "time": utcnow(),
            "uptime_seconds": max(
                0, int((datetime.now(UTC) - target.started_at).total_seconds())
            ),
            "core": {"available": True, "version": app.version},
            "llm": llm,
            "telegram": target.telegram.status(),
            "mcp": {"available": True, "tools": len(target.tools.specs)},
            "speech": target.speech.status(),
            "queue": inbox,
            "open_tasks": int((task_count or {}).get("count", 0)),
            "memories": int((memory_count or {}).get("count", 0)),
            "requests": int((request_stats or {}).get("count", 0)),
            "average_request_ms": float((request_stats or {}).get("avg_ms", 0)),
            "last_error": last_error,
            "recent_activity": recent_activity,
            "updates": target.updates.status(),
            "system": diagnostics(),
        }

    @router.get("/settings")
    async def get_settings() -> dict[str, Any]:
        return public_settings(target)

    @router.patch("/settings")
    async def patch_settings(patch: SettingsPatch) -> dict[str, Any]:
        changes = patch.model_dump(exclude_unset=True)
        telegram_token = changes.pop("telegram_token", None)
        llm_api_token = changes.pop("llm_api_token", None)
        old_autostart = target.settings.autostart_enabled
        try:
            candidate = AppSettings.model_validate(
                target.settings.model_copy(update=changes).model_dump()
            )
            whisper_changed = any(
                key in changes
                and getattr(target.settings, key) != getattr(candidate, key)
                for key in (
                    "whisper_model",
                    "whisper_device",
                    "whisper_compute_type",
                    "whisper_language",
                )
            )
            if whisper_changed:
                await target.speech.unload()
            if candidate.autostart_enabled != old_autostart:
                set_windows_autostart(candidate.autostart_enabled)
            for key in target.settings.__class__.model_fields:
                setattr(target.settings, key, getattr(candidate, key))
            if telegram_token is not None:
                target.secrets.set("telegram_bot_token", telegram_token)
            if llm_api_token is not None:
                target.secrets.set("llm_api_token", llm_api_token)
            target.save_settings()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return public_settings(target)

    @router.get("/models")
    async def models() -> list[dict[str, Any]]:
        return await target.llm.list_models()

    @router.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        return await target.chat.reply(
            request.text,
            conversation_id=request.conversation_id,
            transport=request.transport,
            user_id=request.user_id,
            images=request.images,
        )

    @router.get("/conversations")
    async def conversations(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return await target.conversations.list(limit)

    @router.get("/conversations/{conversation_id}/messages")
    async def conversation_messages(
        conversation_id: str, limit: int = Query(200, ge=1, le=1000)
    ) -> list[dict[str, Any]]:
        return await target.conversations.history(conversation_id, limit)

    @router.delete("/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str) -> None:
        if not await target.conversations.delete(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")

    @router.post("/tasks", status_code=201)
    async def create_task(value: TaskCreate) -> dict[str, Any]:
        return await target.tasks.create(value)

    @router.get("/tasks")
    async def list_tasks(status: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return await target.tasks.list(status, limit)

    @router.patch("/tasks/{task_id}")
    async def update_task(task_id: str, value: TaskUpdate) -> dict[str, Any]:
        task = await target.tasks.update(task_id, value)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @router.delete("/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: str) -> None:
        if not await target.tasks.delete(task_id):
            raise HTTPException(status_code=404, detail="Task not found")

    @router.post("/memories", status_code=201)
    async def create_memory(value: MemoryCreate) -> dict[str, Any]:
        return await target.memories.create(value)

    @router.get("/memories")
    async def search_memories(
        q: str = "", kind: str | None = None, limit: int = Query(50, ge=1, le=500)
    ) -> list[dict[str, Any]]:
        return await target.memories.search(q, kind, limit)

    @router.patch("/memories/{memory_id}")
    async def update_memory(memory_id: str, value: MemoryUpdate) -> dict[str, Any]:
        memory = await target.memories.update(memory_id, value)
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")
        return memory

    @router.delete("/memories/{memory_id}", status_code=204)
    async def delete_memory(memory_id: str) -> None:
        if not await target.memories.delete(memory_id):
            raise HTTPException(status_code=404, detail="Memory not found")

    @router.post("/projects", status_code=201)
    async def create_project(value: ProjectCreate) -> dict[str, Any]:
        return await target.projects.create(value)

    @router.get("/projects")
    async def list_projects() -> list[dict[str, Any]]:
        return await target.projects.list()

    @router.patch("/projects/{project_id}")
    async def update_project(project_id: str, value: ProjectUpdate) -> dict[str, Any]:
        project = await target.projects.update(project_id, value)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @router.delete("/projects/{project_id}", status_code=204)
    async def delete_project(project_id: str) -> None:
        if not await target.projects.delete(project_id):
            raise HTTPException(status_code=404, detail="Project not found")

    @router.post("/reminders", status_code=201)
    async def create_reminder(value: ReminderCreate) -> dict[str, Any]:
        return await target.reminders.create(value)

    @router.get("/reminders")
    async def list_reminders(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return await target.db.fetch_all(
            "SELECT * FROM reminders ORDER BY status='delivered', due_at LIMIT ?", (limit,)
        )

    @router.patch("/reminders/{reminder_id}")
    async def update_reminder(
        reminder_id: str, value: ReminderUpdate
    ) -> dict[str, Any]:
        reminder = await target.reminders.update(reminder_id, value)
        if not reminder:
            raise HTTPException(status_code=404, detail="Reminder not found")
        return reminder

    @router.delete("/reminders/{reminder_id}", status_code=204)
    async def delete_reminder(reminder_id: str) -> None:
        if not await target.reminders.delete(reminder_id):
            raise HTTPException(status_code=404, detail="Reminder not found")

    @router.get("/documents")
    async def list_documents() -> list[dict[str, Any]]:
        return await target.documents.list()

    @router.get("/documents/search")
    async def search_documents(q: str, limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        return await target.documents.search(q, limit)

    @router.get("/documents/{document_id}")
    async def document_detail(document_id: str) -> dict[str, Any]:
        document = await target.documents.get(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return {
            key: value
            for key, value in document.items()
            if key not in {"storage_path", "sha256", "extracted_text"}
        } | {"text_length": len(str(document.get("extracted_text") or ""))}

    @router.post("/documents", status_code=201)
    async def upload_document(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Document is larger than 50 MB")
        try:
            return await target.document_service.ingest_bytes(
                file.filename or "document", data, file.content_type
            )
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc

    @router.delete("/documents/{document_id}", status_code=204)
    async def delete_document(document_id: str) -> None:
        if not await target.document_service.delete(document_id):
            raise HTTPException(status_code=404, detail="Document not found")

    @router.get("/speech/status")
    async def speech_status() -> dict[str, Any]:
        return target.speech.status()

    @router.post("/speech/load", status_code=202)
    async def speech_load() -> dict[str, Any]:
        try:
            await target.speech.load()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return target.speech.status()

    @router.post("/speech/unload")
    async def speech_unload() -> dict[str, Any]:
        await target.speech.unload()
        return target.speech.status()

    @router.post("/speech/transcribe")
    async def speech_transcribe(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Audio is larger than 50 MB")
        suffix = Path(file.filename or "audio.ogg").suffix or ".ogg"
        path = target.settings.attachments_dir / f"speech-{utcnow().replace(':','-')}{suffix}"
        path.write_bytes(data)
        try:
            return await target.speech.transcribe(path)
        finally:
            path.unlink(missing_ok=True)

    @router.get("/telegram/status")
    async def telegram_status() -> dict[str, Any]:
        return target.telegram.status()

    @router.post("/telegram/start")
    async def telegram_start() -> dict[str, Any]:
        await target.telegram.start()
        return target.telegram.status()

    @router.post("/telegram/stop")
    async def telegram_stop() -> dict[str, Any]:
        await target.telegram.stop()
        return target.telegram.status()

    @router.post("/telegram/restart")
    async def telegram_restart() -> dict[str, Any]:
        await target.telegram.restart()
        return target.telegram.status()

    @router.post("/telegram/test")
    async def telegram_test() -> dict[str, Any]:
        try:
            return await target.telegram.test_connection()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/agents")
    async def agents() -> list[dict[str, Any]]:
        return [
            {
                "id": key,
                "name": value.name,
                "description": value.description,
                "tools": value.tools,
                "enabled": value.enabled,
            }
            for key, value in AGENTS.items()
        ]

    @router.get("/tools")
    async def tools() -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "permission": spec.permission,
                "input_schema": spec.schema.model_json_schema(),
            }
            for spec in target.tools.specs.values()
        ]

    @router.get("/mcp/config")
    async def mcp_config() -> dict[str, Any]:
        if getattr(sys, "frozen", False):
            executable = (
                Path(sys.executable).parent.parent
                / "second-mind-mcp"
                / "second-mind-mcp.exe"
            )
            command = str(executable)
            args: list[str] = []
        else:
            command = sys.executable
            args = ["-m", "second_mind.mcp_server"]
        return {
            "mcpServers": {
                "second-mind": {
                    "command": command,
                    "args": args,
                }
            }
        }

    @router.get("/logs")
    async def logs(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return await target.logs.list(limit)

    @router.get("/inbox")
    async def inbox(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return await target.db.fetch_all(
            """SELECT id,transport,external_id,sender_id,status,attempts,last_error,created_at,updated_at
            FROM inbox ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )

    @router.delete("/inbox/{item_id}", status_code=204)
    async def delete_inbox_item(item_id: str) -> None:
        if not await target.db.execute("DELETE FROM inbox WHERE id=?", (item_id,)):
            raise HTTPException(status_code=404, detail="Inbox item not found")

    @router.post("/backup")
    async def backup(destination: str) -> dict[str, Any]:
        path = Path(destination).expanduser().resolve()
        return {"path": str(await target.storage.create_backup(path))}

    @router.get("/storage")
    async def storage_status() -> dict[str, Any]:
        return await target.storage.status()

    @router.post("/storage/backup")
    async def storage_backup(destination: str | None = None) -> dict[str, Any]:
        path = Path(destination).expanduser().resolve() if destination else None
        return {"path": str(await target.storage.create_backup(path))}

    @router.post("/storage/restore")
    async def storage_restore(value: StoragePathRequest) -> dict[str, Any]:
        safety = await target.storage.restore(Path(value.path), confirmed=value.confirmed)
        return {"ok": True, "safety_backup": str(safety)}

    @router.post("/storage/export")
    async def storage_export(value: StorageExportRequest) -> dict[str, Any]:
        return {"path": str(await target.storage.export_json(Path(value.path)))}

    @router.post("/storage/integrity")
    async def storage_integrity() -> dict[str, Any]:
        return await target.storage.integrity()

    @router.post("/storage/optimize")
    async def storage_optimize() -> dict[str, Any]:
        return await target.storage.optimize()

    @router.post("/storage/cleanup")
    async def storage_cleanup(value: StorageCleanupRequest) -> dict[str, Any]:
        return await target.storage.cleanup(confirmed=value.confirmed)

    @router.post("/storage/move")
    async def storage_move(value: StoragePathRequest) -> dict[str, Any]:
        old, new = await target.storage.move(Path(value.path), confirmed=value.confirmed)
        return {"from": str(old), "to": str(new)}

    @router.get("/updates/check")
    async def check_updates() -> dict[str, Any]:
        return await target.updates.check()

    @router.get("/updates/status")
    async def update_status() -> dict[str, Any]:
        return target.updates.status()

    @router.post("/updates/download", status_code=202)
    async def download_update() -> dict[str, Any]:
        return target.updates.start_download()

    @router.post("/updates/cancel")
    async def cancel_update() -> dict[str, Any]:
        return await target.updates.cancel_download()

    @router.post("/updates/install", status_code=202)
    async def install_update(path: str) -> dict[str, Any]:
        prepared = await target.updates.prepare_install(Path(path))
        return {
            **prepared,
            "data_dir": str(target.settings.data_dir),
            "api_url": target.settings.api_url,
            "current_version": __version__,
        }

    @router.post("/system/shutdown", status_code=202)
    async def shutdown(request: Request) -> dict[str, Any]:
        server = getattr(request.app.state, "server", None)
        if server is None:
            raise HTTPException(status_code=409, detail="Managed server handle is unavailable")
        server.should_exit = True
        return {"status": "stopping"}

    app.include_router(router)
    return app
