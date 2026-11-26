from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

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
    ReminderCreate,
    SettingsPatch,
    TaskCreate,
    TaskUpdate,
)
from .services import backup_data
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
        version="0.1.0",
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

    router = APIRouter(prefix="/api")

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
        return {
            "status": "ok",
            "time": utcnow(),
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

    @router.post("/reminders", status_code=201)
    async def create_reminder(value: ReminderCreate) -> dict[str, Any]:
        return await target.reminders.create(value)

    @router.get("/reminders")
    async def list_reminders(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return await target.db.fetch_all(
            "SELECT * FROM reminders ORDER BY status='delivered', due_at LIMIT ?", (limit,)
        )

    @router.get("/documents")
    async def list_documents() -> list[dict[str, Any]]:
        return await target.documents.list()

    @router.get("/documents/search")
    async def search_documents(q: str, limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        return await target.documents.search(q, limit)

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

    @router.post("/backup")
    async def backup(destination: str) -> dict[str, Any]:
        path = Path(destination).expanduser().resolve()
        return {"path": str(await asyncio.to_thread(backup_data, target.settings, path))}

    @router.get("/updates/check")
    async def check_updates() -> dict[str, Any]:
        return await target.updates.check()

    @router.post("/updates/download")
    async def download_update() -> dict[str, Any]:
        return await target.updates.download()

    @router.post("/updates/install", status_code=202)
    async def install_update(path: str) -> dict[str, Any]:
        await target.updates.install(Path(path))
        return {"status": "installer_started"}

    @router.post("/system/shutdown", status_code=202)
    async def shutdown(request: Request) -> dict[str, Any]:
        server = getattr(request.app.state, "server", None)
        if server is None:
            raise HTTPException(status_code=409, detail="Managed server handle is unavailable")
        server.should_exit = True
        return {"status": "stopping"}

    app.include_router(router)
    return app
