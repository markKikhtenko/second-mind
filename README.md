# Second Mind

Локальный персональный ИИ-ассистент для Windows: отдельный фоновый Core, desktop GUI, Telegram-бот, долговременная память, задачи, документы, локальная речь и MCP.

[English documentation](README.en.md) · [Архитектура](docs/architecture.md) · [Roadmap](ROADMAP.md) · [Безопасность](SECURITY.md)

> Статус: **MVP 0.1.0**. Работают текстовые и голосовые сообщения, изображения, индексирование документов, задачи, память, напоминания, Telegram long polling, LM Studio tool calling и локальный MCP server. Подключение требует установленного LM Studio Runtime/Bionic и загруженной локальной модели. Прямой публичный API управления Bionic-сессиями не документирован; детали — в [исследовании](docs/bionic-research.md).

## Возможности

- Независимый Core на FastAPI, доступный только через loopback.
- Windows GUI на PySide6 с мастером настройки, tray и тёмной/светлой темами.
- Адаптер LM Studio Runtime с автоматическим выбором модели и несколькими tool calls за запрос.
- Telegram allowlist, long polling, персистентный inbox и защита от повторной обработки.
- faster-whisper с загрузкой модели из GUI, CPU/CUDA и выгрузкой из памяти.
- SQLite WAL, миграции, FTS5, источники памяти, дедупликация и локальные вложения.
- Индексация TXT, Markdown, CSV, JSON, PDF и DOCX.
- MCP-инструменты: задачи, память, заметки, проекты, напоминания и документы.
- Секреты в Windows Credential Manager; конфигурационные файлы не содержат токенов.
- Фактическая телеметрия CPU/RAM/GPU/VRAM, очереди, запросов и ошибок.
- Проверка GitHub Releases и установка только после сверки опубликованного SHA-256 digest.

## Быстрый запуск для разработки

Требования: Windows 10/11, Python 3.11–3.14, LM Studio Runtime 0.4+ или совместимый локальный API.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[desktop,speech,dev]"
second-mind-desktop
```

GUI сам запустит Core отдельным фоновым процессом. Для ручной диагностики:

```powershell
second-mind-core
```

Swagger доступен локально по `http://127.0.0.1:8765/docs`. Он не публикуется в сеть.

## Настройка

1. В LM Studio включите Local Server на `http://127.0.0.1:1234` и загрузите модель с tool-use.
2. Запустите `second-mind-desktop` и пройдите мастер.
3. Для Telegram создайте бота у BotFather, укажите token и собственный числовой User ID. Пустой allowlist блокирует запуск бота.
4. Выберите Whisper-модель. Она скачивается при первом явном запуске/распознавании.
5. Проверьте фактические статусы на Dashboard.

Команды Telegram `/task текст`, `/tasks`, `/remember факт` работают детерминированно; обычные фразы обрабатывает локальная модель через инструменты.

## MCP

```json
{
  "mcpServers": {
    "second-mind": {
      "command": "C:\\path\\to\\second-mind-mcp.exe"
    }
  }
}
```

Для dev-установки используйте `python -m second_mind.mcp_server`. Актуальную конфигурацию возвращает `GET /api/mcp/config`.

## Проверка

```powershell
ruff check .
pytest --cov=second_mind
```

## Сборка Windows

```powershell
.\packaging\build.ps1
```

Скрипт собирает Core, Desktop и MCP через PyInstaller, затем при наличии Inno Setup создаёт установщик. Те же шаги выполняет GitHub Actions.

## Данные и приватность

Данные хранятся в `%LOCALAPPDATA%\Second Mind`. По умолчанию разрешены только loopback LLM URL и loopback Core API. Внешние LLM не включаются неявно. Не коммитьте локальную БД, вложения или экспорт Credential Manager.

Лицензия: Apache-2.0.
