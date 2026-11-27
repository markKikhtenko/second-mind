from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
import inspect
import json
import mimetypes
import shutil
import sqlite3
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from docx import Document as DocxDocument
from pypdf import PdfReader

from .agent_engine import AgentEngineStore, ContextManager, RunTrace
from .config import AppSettings
from .llm import LLMUnavailable, LMStudioProvider
from .repositories import (
    ConversationRepository,
    DocumentRepository,
    ExecutionLogRepository,
    MemoryRepository,
    ReminderRepository,
    TaskRepository,
)
from .schemas import ChatResponse, MemoryCreate, TaskCreate, ToolEvent
from .storage import StorageManager
from .tools import SystemTools, ToolContext


class ChatService:
    def __init__(
        self,
        llm: LMStudioProvider,
        conversations: ConversationRepository,
        memories: MemoryRepository,
        tasks: TaskRepository,
        tools: SystemTools,
        logs: ExecutionLogRepository,
        engine: AgentEngineStore,
        settings: AppSettings,
    ) -> None:
        self.llm = llm
        self.conversations = conversations
        self.memories = memories
        self.tasks = tasks
        self.tools = tools
        self.logs = logs
        self.engine = engine
        self.settings = settings
        self.context_manager = ContextManager(memories, conversations)
        self._llm_lock = asyncio.Lock()
        self._running: dict[str, asyncio.Task[Any]] = {}

    async def _command(
        self, text: str, transport: str, conversation_id: str
    ) -> ChatResponse | None:
        command, _, body = text.strip().partition(" ")
        command = command.casefold()
        if command in {"/confirm", "/подтвердить"} and body.strip():
            result = await self.tools.approve(body.strip())
            answer = (
                "Действие подтверждено и выполнено."
                if result.get("ok")
                else f"Не удалось выполнить действие: {result.get('error', 'неизвестная ошибка')}"
            )
        elif command in {"/deny", "/отклонить"} and body.strip():
            approval = await self.engine.get_approval(body.strip())
            if approval and approval["status"] == "pending":
                await self.engine.resolve_approval(body.strip(), "denied")
                answer = "Действие отклонено."
            else:
                answer = "Ожидающее подтверждения действие не найдено."
        elif command in {"/task", "/задача"} and body.strip():
            task = await self.tasks.create(TaskCreate(title=body.strip(), source=transport))
            answer = f"Задача создана: {task['title']}"
        elif command in {"/remember", "/запомни"} and body.strip():
            memory = await self.memories.create(
                MemoryCreate(content=body.strip(), source=transport)
            )
            answer = f"Сохранил в память: {memory['content']}"
        elif command in {"/tasks", "/задачи"}:
            tasks = await self.tasks.list("open", 20)
            answer = "Открытых задач нет." if not tasks else "Открытые задачи:\n" + "\n".join(
                f"• {item['title']}" for item in tasks
            )
        else:
            return None
        await self.conversations.add_message(conversation_id, "assistant", answer, transport)
        return ChatResponse(conversation_id=conversation_id, text=answer)

    async def reply(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        transport: str = "desktop",
        user_id: str | None = None,
        images: list[str] | None = None,
        external_id: str | None = None,
        dry_run: bool = False,
        agent_id: str | None = None,
        skill_ids: list[str] | None = None,
    ) -> ChatResponse:
        started = time.perf_counter()
        conversation_id = conversation_id or str(uuid.uuid4())
        await self.conversations.add_message(
            conversation_id, "user", text, transport, external_id=external_id
        )
        command_result = await self._command(text, transport, conversation_id)
        if command_result:
            return command_result
        await self.engine.initialize()
        run_id = await self.engine.start_run(conversation_id, transport)
        trace = RunTrace(self.engine, run_id)
        await trace.add("classifying", "Определение намерения и профиля агента")
        agent = await self.engine.get_agent(agent_id) if agent_id else await self.engine.select_agent(text)
        if not agent or not agent["enabled"]:
            agent = await self.engine.select_agent(text)
        if skill_ids:
            available = {item["id"]: item for item in await self.engine.list_skills(enabled=True)}
            skills = [available[item] for item in skill_ids if item in available][:4]
        else:
            skills = await self.engine.select_skills(agent["id"], text)
        selected_skill_ids = [item["id"] for item in skills]
        model = None if agent["inherit_global_model"] else agent.get("model")
        model = model or self.settings.llm_model
        await self.engine.update_run(
            run_id,
            agent_id=agent["id"],
            model=model,
            skills_json=json.dumps(selected_skill_ids, ensure_ascii=False),
        )
        await trace.add(
            "context",
            "Формирование ограниченного контекста",
            agent=agent["id"],
            skills=selected_skill_ids,
        )
        messages = await self.context_manager.build(
            text=text,
            conversation_id=conversation_id,
            agent=agent,
            skills=skills,
            images=images,
        )
        events: list[ToolEvent] = []
        context = ToolContext(
            transport=transport,
            user_id=user_id,
            allow_external=False,
            agent_id=agent["id"],
            conversation_id=conversation_id,
            run_id=run_id,
            dry_run=dry_run,
        )
        llm_calls = 0
        prompt_tokens = 0
        completion_tokens = 0
        current_task = asyncio.current_task()
        if current_task:
            self._running[run_id] = current_task
        try:
            async with asyncio.timeout(float(agent["timeout_seconds"])):
                await trace.add("queued", "Ожидание последовательной очереди LM Studio")
                async with self._llm_lock:
                    await trace.add("inference", "Запрос к общей локальной модели")
                    while len(events) <= int(agent["max_tool_calls"]):
                        result = await self._complete(messages, self.tools.schemas(tuple(agent["tools"])), agent)
                        llm_calls += 1
                        if not isinstance(result, dict):
                            raise ValueError("Model returned a non-object response")
                        usage = result.pop("_usage", {}) or {}
                        prompt_tokens += int(usage.get("prompt_tokens") or 0)
                        completion_tokens += int(usage.get("completion_tokens") or 0)
                        await self.engine.update_run(
                            run_id,
                            llm_calls=llm_calls,
                            prompt_tokens=prompt_tokens or None,
                            completion_tokens=completion_tokens or None,
                        )
                        tool_calls = result.get("tool_calls") or []
                        if not isinstance(tool_calls, list):
                            raise ValueError("Model returned malformed tool_calls")
                        if not tool_calls:
                            answer = str(result.get("content") or "").strip()
                            if not answer:
                                answer = "Модель не вернула текстовый ответ."
                            await trace.add("completed", "Итоговый ответ сформирован")
                            return await self._finish(
                                run_id,
                                conversation_id,
                                transport,
                                answer,
                                agent["id"],
                                selected_skill_ids,
                                events,
                                trace,
                                started,
                                llm_calls,
                                prompt_tokens,
                                completion_tokens,
                            )
                        if len(events) + len(tool_calls) > int(agent["max_tool_calls"]):
                            break
                        messages.append(
                            {
                                "role": "assistant",
                                "content": result.get("content"),
                                "tool_calls": tool_calls,
                            }
                        )
                        await trace.add("tools", "Проверка политик и выполнение инструментов", count=len(tool_calls))
                        for call in tool_calls:
                            function = call.get("function", {})
                            name = str(function.get("name", ""))
                            raw_arguments = function.get("arguments") or {}
                            if isinstance(raw_arguments, str):
                                try:
                                    arguments = json.loads(raw_arguments)
                                except json.JSONDecodeError:
                                    arguments = {}
                            elif isinstance(raw_arguments, dict):
                                arguments = raw_arguments
                            else:
                                arguments = {}
                            tool_result = await self.tools.execute(name, arguments, context)
                            events.append(ToolEvent(name=name, arguments=arguments, result=tool_result))
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call.get("id", name),
                                    "name": name,
                                    "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                                }
                            )
                            await self.engine.update_run(run_id, tool_calls=len(events))
            answer = "Остановил выполнение: превышен лимит действий или времени."
            await trace.add("limited", answer)
            response = await self._finish(
                run_id,
                conversation_id,
                transport,
                answer,
                agent["id"],
                selected_skill_ids,
                events,
                trace,
                started,
                llm_calls,
                prompt_tokens,
                completion_tokens,
                status="limited",
            )
            return response
        except TimeoutError:
            answer = "Остановил выполнение: истёк таймаут агента."
            await trace.add("timeout", answer)
            return await self._finish(
                run_id,
                conversation_id,
                transport,
                answer,
                agent["id"],
                selected_skill_ids,
                events,
                trace,
                started,
                llm_calls,
                prompt_tokens,
                completion_tokens,
                status="timeout",
            )
        except asyncio.CancelledError:
            duration = int((time.perf_counter() - started) * 1000)
            await self.engine.update_run(
                run_id,
                stage="cancelled",
                status="cancelled",
                trace_json=json.dumps(trace.items, ensure_ascii=False),
                finished_at=datetime.now(UTC).isoformat(),
                duration_ms=duration,
            )
            raise
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            await self.logs.add(
                agent["id"], "chat", "failed", duration, {"error": str(exc)[:1000], "run_id": run_id}
            )
            await self.engine.update_run(
                run_id,
                stage="failed",
                status="failed",
                error=str(exc)[:2000],
                trace_json=json.dumps(trace.items, ensure_ascii=False),
                finished_at=datetime.now(UTC).isoformat(),
                duration_ms=duration,
            )
            raise
        finally:
            self._running.pop(run_id, None)

    async def _complete(
        self, messages: list[dict[str, Any]], schemas: list[dict[str, Any]], agent: dict[str, Any]
    ) -> dict[str, Any]:
        method = self.llm.complete
        parameters = inspect.signature(method).parameters
        kwargs: dict[str, Any] = {}
        if "model" in parameters:
            kwargs = {
                "model": None if agent["inherit_global_model"] else agent.get("model"),
                "temperature": agent["temperature"],
                "max_tokens": agent["generation_limit"],
                "reasoning_effort": agent.get("reasoning_effort"),
                "timeout_seconds": agent["timeout_seconds"],
            }
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await method(messages, schemas, **kwargs)
                if not isinstance(response, dict):
                    raise ValueError("Model returned a non-object response")
                if "tool_calls" in response and not isinstance(response.get("tool_calls"), list):
                    raise ValueError("Model returned malformed tool_calls")
                return response
            except (LLMUnavailable, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.2)
        if last_error:
            raise last_error
        raise RuntimeError("LLM request failed")

    async def _finish(
        self,
        run_id: str,
        conversation_id: str,
        transport: str,
        answer: str,
        agent_id: str,
        skills: list[str],
        events: list[ToolEvent],
        trace: RunTrace,
        started: float,
        llm_calls: int,
        prompt_tokens: int,
        completion_tokens: int,
        status: str = "completed",
    ) -> ChatResponse:
        await self.conversations.add_message(conversation_id, "assistant", answer, transport)
        duration = int((time.perf_counter() - started) * 1000)
        await self.logs.add(
            agent_id,
            "chat",
            status,
            duration,
            {"tools": len(events), "llm_calls": llm_calls, "run_id": run_id, "skills": skills},
        )
        await self.engine.update_run(
            run_id,
            stage=status,
            status=status,
            llm_calls=llm_calls,
            tool_calls=len(events),
            prompt_tokens=prompt_tokens or None,
            completion_tokens=completion_tokens or None,
            trace_json=json.dumps(trace.items, ensure_ascii=False),
            finished_at=datetime.now(UTC).isoformat(),
            duration_ms=duration,
        )
        return ChatResponse(
            conversation_id=conversation_id,
            text=answer,
            tool_events=events,
            run_id=run_id,
            agent_id=agent_id,
            skills=skills,
            trace=trace.items,
        )

    def cancel(self, run_id: str) -> bool:
        task = self._running.get(run_id)
        if not task or task.done():
            return False
        task.cancel()
        return True

    def status(self) -> dict[str, Any]:
        return {
            "running": list(self._running),
            "queue_depth": max(0, len(self._running) - int(self._llm_lock.locked())),
            "llm_busy": self._llm_lock.locked(),
        }


class SpeechService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._model: Any = None
        self._loading = False
        self._error: str | None = None

    async def load(self) -> None:
        if self._model is not None:
            return
        self._loading = True
        self._error = None
        try:
            from faster_whisper import WhisperModel

            self._model = await asyncio.to_thread(
                WhisperModel,
                self.settings.whisper_model,
                device=self.settings.whisper_device,
                compute_type=self.settings.whisper_compute_type,
            )
        except Exception as exc:
            self._error = str(exc)
            raise
        finally:
            self._loading = False

    async def transcribe(self, path: Path) -> dict[str, Any]:
        await self.load()

        def run() -> dict[str, Any]:
            segments, info = self._model.transcribe(
                str(path),
                language=self.settings.whisper_language,
                vad_filter=True,
                beam_size=5,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {
                "text": text,
                "language": info.language,
                "language_probability": info.language_probability,
            }

        return await asyncio.to_thread(run)

    async def unload(self) -> None:
        self._model = None
        await asyncio.to_thread(gc.collect)

    def status(self) -> dict[str, Any]:
        return {
            "loaded": self._model is not None,
            "loading": self._loading,
            "model": self.settings.whisper_model,
            "device": self.settings.whisper_device,
            "language": self.settings.whisper_language or "auto",
            "error": self._error,
        }


class DocumentService:
    ALLOWED_SUFFIXES = {".txt", ".md", ".csv", ".json", ".pdf", ".docx"}

    def __init__(self, settings: AppSettings, documents: DocumentRepository) -> None:
        self.settings = settings
        self.documents = documents

    @staticmethod
    def _extract(path: Path) -> str:
        suffix = path.suffix.casefold()
        if suffix in {".txt", ".md", ".csv", ".json"}:
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".pdf":
            reader = PdfReader(str(path))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        if suffix == ".docx":
            document = DocxDocument(str(path))
            return "\n".join(paragraph.text for paragraph in document.paragraphs)
        raise ValueError(f"Unsupported document type: {suffix}")

    async def ingest_bytes(
        self, filename: str, data: bytes, media_type: str | None = None, source: str = "upload"
    ) -> dict[str, Any]:
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix.casefold()
        if suffix not in self.ALLOWED_SUFFIXES:
            raise ValueError("Supported document types: txt, md, csv, json, pdf, docx")
        digest = hashlib.sha256(data).hexdigest()
        self.settings.attachments_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.attachments_dir / f"{digest}{suffix}"
        if not path.exists():
            await asyncio.to_thread(path.write_bytes, data)
        extracted = await asyncio.to_thread(self._extract, path)
        return await self.documents.create(
            filename=safe_name,
            media_type=media_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            storage_path=str(path),
            sha256=digest,
            extracted_text=extracted,
            source=source,
        )

    async def delete(self, document_id: str) -> bool:
        row = await self.documents.delete(document_id)
        if not row:
            return False
        path = Path(str(row["storage_path"]))
        other = await self.documents.db.fetch_one(
            "SELECT id FROM documents WHERE storage_path=?", (str(path),)
        )
        if not other and path.exists() and path.parent == self.settings.attachments_dir:
            path.unlink(missing_ok=True)
        return True


class SchedulerService:
    def __init__(
        self,
        reminders: ReminderRepository,
        logs: ExecutionLogRepository,
        storage: StorageManager,
    ) -> None:
        self.reminders = reminders
        self.logs = logs
        self.storage = storage
        self.telegram_sender: Any = None
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.add_job(
                self._deliver_due,
                "interval",
                seconds=20,
                id="deliver-reminders",
                max_instances=1,
                coalesce=True,
            )
            self.scheduler.add_job(
                self.storage.scheduled_backup,
                "interval",
                minutes=15,
                id="storage-backup",
                max_instances=1,
                coalesce=True,
            )
            self.scheduler.start()

    async def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def _deliver_due(self) -> None:
        for reminder in await self.reminders.due():
            try:
                if reminder["target_transport"] == "telegram":
                    if not self.telegram_sender or not reminder["target_user_id"]:
                        raise RuntimeError("Telegram target is unavailable")
                    await self.telegram_sender(reminder["target_user_id"], reminder["text"])
                await self.reminders.delivered(reminder["id"])
                await self.logs.add("scheduler", "deliver_reminder", "completed")
            except Exception as exc:
                await self.logs.add(
                    "scheduler",
                    "deliver_reminder",
                    "failed",
                    details={"error": str(exc), "reminder_id": reminder["id"]},
                )


def image_data_url(path: Path, media_type: str | None = None) -> str:
    mime = media_type or mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def backup_data(settings: AppSettings, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive_base = destination / f"second-mind-backup-{stamp}"
    if not settings.database_path.exists():
        raise FileNotFoundError("Second Mind database does not exist")
    with tempfile.TemporaryDirectory(prefix="second-mind-backup-") as temp_dir:
        staging = Path(temp_dir)
        source = sqlite3.connect(settings.database_path)
        target = sqlite3.connect(staging / settings.database_path.name)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        config = settings.data_dir / "config.json"
        if config.exists():
            shutil.copy2(config, staging / config.name)
        if settings.attachments_dir.exists():
            shutil.copytree(settings.attachments_dir, staging / "attachments")
        return Path(shutil.make_archive(str(archive_base), "zip", staging))
