# Second Mind

Локальный персональный ИИ-ассистент для Windows: отдельный фоновый Core, desktop GUI, Telegram-бот, долговременная память, задачи, документы, локальная речь и MCP.

[English documentation](README.en.md) · [Архитектура](docs/architecture.md) · [Roadmap](ROADMAP.md) · [Безопасность](SECURITY.md)

> Статус: **0.2.0**. Работают текстовые и голосовые сообщения, изображения, индексирование документов, задачи, память, напоминания, Telegram long polling, LM Studio tool calling, локальный MCP server и полностью графическое обновление приложения. Подключение модели требует установленного LM Studio Runtime/Bionic. Прямой публичный API управления Bionic-сессиями не документирован; детали — в [исследовании](docs/bionic-research.md).

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
- Отдельный экран обновлений: ручная/автоматическая проверка GitHub Releases, CHANGELOG и прогресс скачивания.
- Подписанный Ed25519-манифест релиза и обязательная проверка SHA-256 перед запуском Setup.exe.
- Транзакционное обновление: остановка Core/MCP, backup приложения и SQLite, health-check новой версии и автоматический rollback.

## Установка Windows

Скачайте единый `Second-Mind-Setup-<версия>.exe` из [GitHub Releases](https://github.com/markKikhtenko/second-mind/releases). Python и командная строка не нужны: установщик включает Core, Desktop, MCP, updater и все runtime-зависимости.

Мастер установки создаёт ярлык в меню «Пуск», предлагает ярлык рабочего стола и автозапуск фонового Core. Эти параметры можно менять в GUI. Повторная установка и обновление используют тот же AppId и каталог программы. Пользовательские данные находятся отдельно, в `%LOCALAPPDATA%\Second Mind\Second Mind`, поэтому переустановка и обычное удаление приложения их не удаляют.

## Обновления и восстановление

Раздел «Обновления» показывает текущую и доступную версии, описание релиза и состояние доверенной проверки. При установке обновления отдельный updater:

1. повторно проверяет подпись манифеста, имя, размер и SHA-256 Setup.exe;
2. закрывает Desktop, штатно останавливает Core и завершает остальные процессы установленной версии;
3. сохраняет копию каталога программы и консистентный SQLite backup в `%LOCALAPPDATA%\Second Mind\Second Mind\update-backups`;
4. запускает Setup.exe без консоли, поднимает новый Core и проверяет точную версию через health API;
5. при ошибке восстанавливает предыдущие файлы и базу данных, затем перезапускает рабочую версию.

Журнал последней транзакции хранится в `%LOCALAPPDATA%\Second Mind\Second Mind\updates\update.log`. Цепочка доверия основана на Ed25519 public key, встроенном в приложение; CI хранит private key только в GitHub Actions secret `UPDATE_SIGNING_PRIVATE_KEY_B64`. При наличии сертификата CI дополнительно поддерживает Authenticode-подпись через `WINDOWS_SIGNING_CERTIFICATE_B64` и `WINDOWS_SIGNING_CERTIFICATE_PASSWORD`.

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

Скрипт собирает Core, Desktop, MCP и отдельный one-file updater через PyInstaller, затем Inno Setup создаёт единый установщик. Те же шаги выполняет GitHub Actions. Release workflow запускает unit-тесты, чистую установку, настоящую detached-транзакцию updater и обновление с предыдущего стабильного релиза с проверкой сохранности БД/настроек, создаёт подписанный update manifest и только затем публикует GitHub Release.

## Данные и приватность

Данные хранятся в `%LOCALAPPDATA%\Second Mind\Second Mind`. По умолчанию разрешены только loopback LLM URL и loopback Core API. Внешние LLM не включаются неявно. Не коммитьте локальную БД, вложения или экспорт Credential Manager.

Лицензия: Apache-2.0.
