from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message

from .config import AppSettings, SecretStore
from .repositories import InboxRepository
from .services import ChatService, DocumentService, SpeechService, image_data_url


class TelegramGateway:
    def __init__(
        self,
        settings: AppSettings,
        secrets: SecretStore,
        inbox: InboxRepository,
        chat: ChatService,
        speech: SpeechService,
        documents: DocumentService,
    ) -> None:
        self.settings = settings
        self.secrets = secrets
        self.inbox = inbox
        self.chat = chat
        self.speech = speech
        self.documents = documents
        self.bot: Bot | None = None
        self.dispatcher: Dispatcher | None = None
        self.task: asyncio.Task[Any] | None = None
        self.last_error: str | None = None

    def _allowed(self, message: Message) -> bool:
        return bool(
            message.from_user
            and message.from_user.id in set(self.settings.telegram_allowed_user_ids)
        )

    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        token = self.secrets.get("telegram_bot_token")
        if not token:
            raise RuntimeError("Telegram bot token is not configured")
        if not self.settings.telegram_allowed_user_ids:
            raise RuntimeError("At least one allowed Telegram User ID is required")
        self.last_error = None
        self.bot = Bot(token=token)
        self.dispatcher = Dispatcher()
        router = Router(name="second-mind")
        router.message.register(self._handle_message)
        self.dispatcher.include_router(router)
        self.task = asyncio.create_task(
            self.dispatcher.start_polling(self.bot, handle_signals=False),
            name="telegram-long-polling",
        )
        self.task.add_done_callback(self._polling_done)

    def _polling_done(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            self.last_error = str(exc)

    async def stop(self) -> None:
        if self.dispatcher:
            await self.dispatcher.stop_polling()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self.task.cancel()
        if self.bot:
            await self.bot.session.close()
        self.task = None
        self.bot = None
        self.dispatcher = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def send(self, user_id: str, text: str) -> None:
        if not self.bot:
            raise RuntimeError("Telegram bot is not running")
        numeric_id = int(user_id)
        if numeric_id not in set(self.settings.telegram_allowed_user_ids):
            raise PermissionError("Telegram recipient is not in the allowlist")
        await self.bot.send_message(numeric_id, text)

    async def _handle_message(self, message: Message) -> None:
        if not self._allowed(message):
            return
        assert message.from_user is not None
        external_id = f"{message.chat.id}:{message.message_id}"
        item, created = await self.inbox.enqueue(
            "telegram",
            external_id,
            str(message.from_user.id),
            {
                "chat_id": message.chat.id,
                "text": message.text or message.caption,
                "has_voice": message.voice is not None,
                "has_photo": bool(message.photo),
                "has_document": message.document is not None,
            },
        )
        if not created and item.get("status") == "processed":
            return
        try:
            text = message.text or message.caption or ""
            images: list[str] = []
            with tempfile.TemporaryDirectory(prefix="second-mind-") as temp_dir:
                temp_path = Path(temp_dir)
                if message.voice:
                    voice_path = temp_path / "voice.ogg"
                    assert self.bot is not None
                    await self.bot.download(message.voice, destination=voice_path)
                    transcription = await self.speech.transcribe(voice_path)
                    text = transcription["text"]
                if message.photo:
                    photo_path = temp_path / "photo.jpg"
                    assert self.bot is not None
                    await self.bot.download(message.photo[-1], destination=photo_path)
                    images.append(image_data_url(photo_path, "image/jpeg"))
                    text = text or "Опиши и проанализируй это изображение."
                if message.document:
                    document_path = temp_path / (message.document.file_name or "document")
                    assert self.bot is not None
                    await self.bot.download(message.document, destination=document_path)
                    indexed = await self.documents.ingest_bytes(
                        document_path.name,
                        document_path.read_bytes(),
                        message.document.mime_type,
                        source="telegram",
                    )
                    text = text or f"Документ {indexed['filename']} сохранён и проиндексирован."
            if not text:
                await message.answer("Поддерживаются текст, голос, изображения и документы.")
            else:
                result = await self.chat.reply(
                    text,
                    conversation_id=f"telegram:{message.chat.id}",
                    transport="telegram",
                    user_id=str(message.from_user.id),
                    images=images,
                    external_id=external_id,
                )
                await message.answer(result.text)
            await self.inbox.mark(item["id"], "processed")
        except Exception as exc:
            await self.inbox.mark(item["id"], "failed", str(exc))
            await message.answer(f"Не удалось обработать сообщение: {str(exc)[:500]}")

    def status(self) -> dict[str, Any]:
        running = bool(self.task and not self.task.done())
        return {
            "configured": bool(self.secrets.get("telegram_bot_token")),
            "enabled": self.settings.telegram_enabled,
            "running": running,
            "allowed_users": len(self.settings.telegram_allowed_user_ids),
            "last_error": self.last_error,
        }
