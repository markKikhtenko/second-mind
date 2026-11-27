from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .agent_engine import AgentEngineStore
from .config import AppSettings, ConfigManager, SecretStore
from .db import Database
from .llm import LMStudioProvider
from .repositories import (
    ConversationRepository,
    DocumentRepository,
    ExecutionLogRepository,
    InboxRepository,
    MemoryRepository,
    ProjectRepository,
    ReminderRepository,
    TaskRepository,
)
from .services import ChatService, DocumentService, SchedulerService, SpeechService
from .storage import StorageManager
from .system import windows_autostart_enabled
from .telegram_gateway import TelegramGateway
from .tools import SystemTools
from .updates import UpdateService


class Container:
    def __init__(
        self,
        settings: AppSettings | None = None,
        config_manager: ConfigManager | None = None,
        secret_store: SecretStore | None = None,
    ) -> None:
        self.started_at = datetime.now(UTC)
        self.config_manager = config_manager or ConfigManager()
        self.settings = settings or self.config_manager.load()
        if sys.platform == "win32":
            self.settings.autostart_enabled = windows_autostart_enabled()
        self.secrets = secret_store or SecretStore()
        self.db = Database(self.settings.database_path)
        self.tasks = TaskRepository(self.db)
        self.memories = MemoryRepository(self.db)
        self.projects = ProjectRepository(self.db)
        self.conversations = ConversationRepository(self.db)
        self.reminders = ReminderRepository(self.db)
        self.inbox = InboxRepository(self.db)
        self.logs = ExecutionLogRepository(self.db)
        self.storage = StorageManager(
            self.settings, self.config_manager, self.db, self.logs
        )
        self.documents = DocumentRepository(self.db)
        self.agent_engine = AgentEngineStore(self.db, self.settings)
        self.llm = LMStudioProvider(self.settings, self.secrets)
        self.speech = SpeechService(self.settings)
        self.document_service = DocumentService(self.settings, self.documents)
        self.tools = SystemTools(
            self.tasks,
            self.memories,
            self.projects,
            self.reminders,
            self.documents,
            control=self.agent_engine,
        )
        self.chat = ChatService(
            self.llm,
            self.conversations,
            self.memories,
            self.tasks,
            self.tools,
            self.logs,
            self.agent_engine,
            self.settings,
        )
        self.telegram = TelegramGateway(
            self.settings,
            self.secrets,
            self.inbox,
            self.chat,
            self.speech,
            self.document_service,
        )
        self.tools.telegram_sender = self.telegram.send
        self.scheduler = SchedulerService(self.reminders, self.logs, self.storage)
        self.scheduler.telegram_sender = self.telegram.send
        self.updates = UpdateService(self.settings, __version__)

    async def start(self) -> None:
        await self.db.initialize()
        await self.agent_engine.initialize()
        self.settings.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.scheduler.start()
        if self.settings.telegram_enabled:
            try:
                await self.telegram.start()
            except Exception as exc:
                self.telegram.last_error = str(exc)

    async def stop(self) -> None:
        await self.telegram.stop()
        await self.scheduler.stop()

    def save_settings(self) -> None:
        self.config_manager.save(self.settings)

    @classmethod
    def for_data_dir(cls, data_dir: Path, secret_store: SecretStore) -> Container:
        manager = ConfigManager(data_dir / "config.json")
        settings = AppSettings(data_dir=data_dir)
        return cls(settings=settings, config_manager=manager, secret_store=secret_store)
