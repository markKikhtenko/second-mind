from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from .config import ConfigManager

DARK_STYLE = """
QWidget { background: #111318; color: #e7e9ee; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow { background: #0d0f13; }
QListWidget { background: #171a21; border: 0; padding: 8px; outline: 0; }
QListWidget::item { padding: 10px 12px; border-radius: 7px; margin: 2px; }
QListWidget::item:selected { background: #6558f5; color: white; }
QPushButton { background: #6558f5; color: white; border: 0; border-radius: 7px; padding: 8px 14px; }
QPushButton:hover { background: #766bf7; }
QPushButton:disabled { background: #343742; color: #8f93a2; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
  background: #1b1e26; border: 1px solid #303541; border-radius: 6px; padding: 7px;
}
QHeaderView::section { background: #1b1e26; border: 0; padding: 7px; font-weight: 600; }
QFrame#card { background: #1b1e26; border: 1px solid #282d38; border-radius: 10px; }
QLabel#title { font-size: 25px; font-weight: 650; }
QLabel#metric { font-size: 20px; font-weight: 650; color: #9d95ff; }
"""

LIGHT_STYLE = """
QWidget { background: #f6f7fa; color: #20232b; font-family: 'Segoe UI'; font-size: 13px; }
QListWidget { background: white; border: 0; padding: 8px; outline: 0; }
QListWidget::item { padding: 10px 12px; border-radius: 7px; margin: 2px; }
QListWidget::item:selected { background: #6558f5; color: white; }
QPushButton { background: #6558f5; color: white; border: 0; border-radius: 7px; padding: 8px 14px; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
  background: white; border: 1px solid #d9dce5; border-radius: 6px; padding: 7px;
}
QHeaderView::section { background: #eceef3; border: 0; padding: 7px; font-weight: 600; }
QFrame#card { background: white; border: 1px solid #e1e3ea; border-radius: 10px; }
QLabel#title { font-size: 25px; font-weight: 650; }
QLabel#metric { font-size: 20px; font-weight: 650; color: #5145d8; }
"""


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        with httpx.Client(timeout=kwargs.pop("timeout", 30)) as client:
            response = client.request(method, f"{self.base_url}/api{path}", **kwargs)
            response.raise_for_status()
            if response.status_code == 204:
                return None
            return response.json()


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.result.emit(self.operation())
        except Exception as exc:
            self.signals.error.emit(str(exc))


class ServiceController:
    def __init__(self, api_url: str) -> None:
        self.api_url = api_url

    def running(self) -> bool:
        try:
            return httpx.get(f"{self.api_url}/api/ping", timeout=2).is_success
        except httpx.HTTPError:
            return False

    def start(self) -> None:
        if self.running():
            return
        if getattr(sys, "frozen", False):
            executable = Path(sys.executable)
            command = [
                str(
                    executable.parent.parent
                    / "second-mind-core"
                    / "second-mind-core.exe"
                )
            ]
        else:
            command = [sys.executable, "-m", "second_mind.server"]
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.running():
                return
            time.sleep(0.25)
        raise RuntimeError("Second Mind Core did not start within 120 seconds")


class BasePage(QWidget):
    def __init__(self, client: ApiClient, title: str) -> None:
        super().__init__()
        self.client = client
        self.pool = QThreadPool.globalInstance()
        self.layout = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setObjectName("title")
        self.layout.addWidget(heading)

    def work(
        self,
        operation: Callable[[], Any],
        success: Callable[[Any], None],
        error: Callable[[str], None] | None = None,
    ) -> None:
        worker = Worker(operation)
        worker.signals.result.connect(success)
        worker.signals.error.connect(error or self.show_error)
        self.pool.start(worker)

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Second Mind", message)

    def refresh(self) -> None:
        pass


class DashboardPage(BasePage):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(client, "Dashboard")
        self.status = QLabel("Обновление…")
        self.status.setWordWrap(True)
        self.layout.addWidget(self.status)
        cards = QHBoxLayout()
        self.metrics: dict[str, QLabel] = {}
        for key, label in (
            ("core", "Core"),
            ("llm", "Bionic / LM Studio"),
            ("telegram", "Telegram"),
            ("memory", "Память"),
            ("tasks", "Задачи"),
            ("system", "Система"),
        ):
            card = QFrame()
            card.setObjectName("card")
            body = QVBoxLayout(card)
            body.addWidget(QLabel(label))
            metric = QLabel("—")
            metric.setObjectName("metric")
            metric.setWordWrap(True)
            body.addWidget(metric)
            self.metrics[key] = metric
            cards.addWidget(card)
        self.layout.addLayout(cards)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.layout.addWidget(self.details, 1)
        button = QPushButton("Обновить")
        button.clicked.connect(self.refresh)
        self.layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", "/health"), self.render)

    def render(self, data: dict[str, Any]) -> None:
        self.status.setText(f"Core работает • {data['time']}")
        self.metrics["core"].setText("● Online")
        self.metrics["llm"].setText(
            f"● {data['llm']['models']} моделей" if data["llm"]["available"] else "○ Недоступен"
        )
        telegram = data["telegram"]
        self.metrics["telegram"].setText("● Запущен" if telegram["running"] else "○ Остановлен")
        self.metrics["memory"].setText(str(data["memories"]))
        self.metrics["tasks"].setText(str(data["open_tasks"]))
        system = data["system"]
        self.metrics["system"].setText(
            f"CPU {system['cpu_percent']:.0f}%\nRAM {system['ram_percent']:.0f}%"
        )
        self.details.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))


class ChatPage(BasePage):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(client, "Чат с ассистентом")
        self.conversation_id: str | None = None
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.input = QPlainTextEdit()
        self.input.setMaximumHeight(100)
        self.input.setPlaceholderText("Сообщение… Можно просить несколько действий сразу")
        self.send = QPushButton("Отправить")
        self.send.clicked.connect(self.submit)
        self.layout.addWidget(self.transcript, 1)
        self.layout.addWidget(self.input)
        self.layout.addWidget(self.send, alignment=Qt.AlignmentFlag.AlignRight)

    def submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.transcript.appendPlainText(f"Вы: {text}\n")
        self.send.setEnabled(False)
        payload = {"text": text, "conversation_id": self.conversation_id, "transport": "desktop"}
        self.work(
            lambda: self.client.request("POST", "/chat", json=payload, timeout=300),
            self.received,
            self.failed,
        )

    def received(self, result: dict[str, Any]) -> None:
        self.conversation_id = result["conversation_id"]
        self.transcript.appendPlainText(f"Second Mind: {result['text']}\n")
        for event in result.get("tool_events", []):
            self.transcript.appendPlainText(f"  ↳ {event['name']}: {'ok' if event['result'].get('ok') else 'error'}")
        self.send.setEnabled(True)

    def failed(self, message: str) -> None:
        self.transcript.appendPlainText(f"Ошибка: {message}\n")
        self.send.setEnabled(True)


class CrudTablePage(BasePage):
    def __init__(
        self,
        client: ApiClient,
        title: str,
        endpoint: str,
        columns: list[tuple[str, str]],
    ) -> None:
        super().__init__(client, title)
        self.endpoint, self.columns = endpoint, columns
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels([label for _, label in columns])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.layout.addWidget(self.table, 1)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.refresh)
        self.layout.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignLeft)

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", self.endpoint), self.render)

    def render(self, rows: list[dict[str, Any]]) -> None:
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, (key, _) in enumerate(self.columns):
                value = row.get(key, "")
                item = QTableWidgetItem(str(value if value is not None else ""))
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row_index, column_index, item)

    def selected(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 0):
            return None
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)


class TasksPage(CrudTablePage):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(client, "Задачи", "/tasks", [("title", "Задача"), ("status", "Статус"), ("due_at", "Срок"), ("priority", "Приоритет")])
        controls = QHBoxLayout()
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Новая задача")
        add = QPushButton("Создать")
        add.clicked.connect(self.add_task)
        complete = QPushButton("Завершить выбранную")
        complete.clicked.connect(self.complete_task)
        controls.addWidget(self.title_input, 1)
        controls.addWidget(add)
        controls.addWidget(complete)
        self.layout.insertLayout(1, controls)

    def add_task(self) -> None:
        title = self.title_input.text().strip()
        if title:
            self.work(lambda: self.client.request("POST", "/tasks", json={"title": title}), lambda _: (self.title_input.clear(), self.refresh()))

    def complete_task(self) -> None:
        item = self.selected()
        if item:
            self.work(lambda: self.client.request("PATCH", f"/tasks/{item['id']}", json={"status": "completed"}), lambda _: self.refresh())


class MemoryPage(CrudTablePage):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(client, "Память", "/memories", [("kind", "Тип"), ("content", "Содержание"), ("source", "Источник"), ("updated_at", "Обновлено")])
        controls = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Поиск или новый факт")
        search = QPushButton("Найти")
        search.clicked.connect(self.refresh)
        save = QPushButton("Сохранить факт")
        save.clicked.connect(self.save_memory)
        delete = QPushButton("Удалить выбранное")
        delete.clicked.connect(self.delete_memory)
        for widget in (self.query, search, save, delete):
            controls.addWidget(widget)
        self.layout.insertLayout(1, controls)

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", "/memories", params={"q": self.query.text()}), self.render)

    def save_memory(self) -> None:
        content = self.query.text().strip()
        if content:
            self.work(lambda: self.client.request("POST", "/memories", json={"content": content}), lambda _: (self.query.clear(), self.refresh()))

    def delete_memory(self) -> None:
        item = self.selected()
        if item and QMessageBox.question(self, "Удаление", "Удалить запись памяти?") == QMessageBox.StandardButton.Yes:
            self.work(lambda: self.client.request("DELETE", f"/memories/{item['id']}"), lambda _: self.refresh())


class CreateListPage(CrudTablePage):
    def __init__(self, client: ApiClient, title: str, endpoint: str, columns: list[tuple[str, str]], placeholder: str, payload_key: str = "name") -> None:
        super().__init__(client, title, endpoint, columns)
        self.payload_key = payload_key
        controls = QHBoxLayout()
        self.value = QLineEdit()
        self.value.setPlaceholderText(placeholder)
        add = QPushButton("Создать")
        add.clicked.connect(self.create)
        controls.addWidget(self.value, 1)
        controls.addWidget(add)
        self.layout.insertLayout(1, controls)

    def create(self) -> None:
        value = self.value.text().strip()
        if value:
            self.work(lambda: self.client.request("POST", self.endpoint, json={self.payload_key: value}), lambda _: (self.value.clear(), self.refresh()))


class DocumentsPage(CrudTablePage):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(client, "Документы", "/documents", [("filename", "Файл"), ("media_type", "Тип"), ("status", "Статус"), ("text_length", "Символов")])
        upload = QPushButton("Добавить документ")
        upload.clicked.connect(self.upload)
        self.layout.insertWidget(1, upload, alignment=Qt.AlignmentFlag.AlignLeft)

    def upload(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Документ", "", "Documents (*.txt *.md *.csv *.json *.pdf *.docx)")
        if not path:
            return
        file_path = Path(path)
        self.work(
            lambda: self.client.request("POST", "/documents", files={"file": (file_path.name, file_path.read_bytes())}, timeout=120),
            lambda _: self.refresh(),
        )


class RemindersPage(CrudTablePage):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(
            client,
            "Календарь и напоминания",
            "/reminders",
            [("text", "Напоминание"), ("due_at", "Когда"), ("status", "Статус")],
        )
        controls = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText("Текст напоминания")
        self.due = QLineEdit()
        self.due.setPlaceholderText("2026-12-01T09:00:00+03:00")
        add = QPushButton("Создать")
        add.clicked.connect(self.create_reminder)
        controls.addWidget(self.text, 2)
        controls.addWidget(self.due, 1)
        controls.addWidget(add)
        self.layout.insertLayout(1, controls)

    def create_reminder(self) -> None:
        text, due = self.text.text().strip(), self.due.text().strip()
        if not text or not due:
            self.show_error("Укажите текст и дату в ISO-формате с часовым поясом")
            return
        self.work(
            lambda: self.client.request(
                "POST", "/reminders", json={"text": text, "due_at": due}
            ),
            lambda _: (self.text.clear(), self.due.clear(), self.refresh()),
        )


class TelegramPage(BasePage):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(client, "Telegram")
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        controls = QHBoxLayout()
        for label, action in (("Запустить", "start"), ("Остановить", "stop"), ("Перезапустить", "restart")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=action: self.action(value))
            controls.addWidget(button)
        self.layout.addLayout(controls)
        self.layout.addWidget(self.status, 1)

    def action(self, action: str) -> None:
        self.work(lambda: self.client.request("POST", f"/telegram/{action}"), self.render)

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", "/telegram/status"), self.render)

    def render(self, result: dict[str, Any]) -> None:
        self.status.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))


class SettingsPage(BasePage):
    def __init__(self, client: ApiClient, theme_callback: Callable[[], None]) -> None:
        super().__init__(client, "Настройки")
        form = QFormLayout()
        self.llm_url = QLineEdit()
        self.model = QLineEdit()
        self.telegram_token = QLineEdit()
        self.telegram_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.allowed_ids = QLineEdit()
        self.telegram_enabled = QCheckBox("Запускать Telegram вместе с Core")
        self.whisper_model = QComboBox()
        self.whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3", "turbo"])
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["cpu", "cuda", "auto"])
        self.autostart = QCheckBox("Автозапуск Core при входе в Windows")
        for label, widget in (
            ("LM Studio URL", self.llm_url),
            ("Модель", self.model),
            ("Telegram token", self.telegram_token),
            ("Разрешённые User ID", self.allowed_ids),
            ("Telegram", self.telegram_enabled),
            ("Whisper", self.whisper_model),
            ("Устройство", self.whisper_device),
            ("Автозапуск", self.autostart),
        ):
            form.addRow(label, widget)
        self.layout.addLayout(form)
        controls = QHBoxLayout()
        save = QPushButton("Сохранить")
        save.clicked.connect(self.save)
        theme = QPushButton("Сменить тему")
        theme.clicked.connect(theme_callback)
        speech_load = QPushButton("Загрузить Whisper")
        speech_load.clicked.connect(self.load_speech)
        speech_unload = QPushButton("Выгрузить Whisper")
        speech_unload.clicked.connect(self.unload_speech)
        speech_test = QPushButton("Тест аудио")
        speech_test.clicked.connect(self.test_speech)
        controls.addWidget(save)
        controls.addWidget(theme)
        controls.addWidget(speech_load)
        controls.addWidget(speech_unload)
        controls.addWidget(speech_test)
        self.layout.addLayout(controls)
        self.note = QLabel("Токены сохраняются в Windows Credential Manager и не отображаются повторно.")
        self.layout.addWidget(self.note)
        self.layout.addStretch()

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", "/settings"), self.render)

    def render(self, value: dict[str, Any]) -> None:
        self.llm_url.setText(value["llm_base_url"])
        self.model.setText(value.get("llm_model") or "")
        self.allowed_ids.setText(", ".join(str(item) for item in value["telegram_allowed_user_ids"]))
        self.telegram_enabled.setChecked(value["telegram_enabled"])
        self.whisper_model.setCurrentText(value["whisper_model"])
        self.whisper_device.setCurrentText(value["whisper_device"])
        self.autostart.setChecked(value["autostart_enabled"])
        self.telegram_token.setPlaceholderText("Настроен" if value["telegram_token_configured"] else "Не настроен")

    def save(self) -> None:
        try:
            ids = [int(value.strip()) for value in self.allowed_ids.text().split(",") if value.strip()]
        except ValueError:
            self.show_error("Telegram User ID должны быть целыми числами")
            return
        payload: dict[str, Any] = {
            "llm_base_url": self.llm_url.text().strip(),
            "llm_model": self.model.text().strip() or None,
            "telegram_allowed_user_ids": ids,
            "telegram_enabled": self.telegram_enabled.isChecked(),
            "whisper_model": self.whisper_model.currentText(),
            "whisper_device": self.whisper_device.currentText(),
            "autostart_enabled": self.autostart.isChecked(),
        }
        if self.telegram_token.text():
            payload["telegram_token"] = self.telegram_token.text()
        self.work(lambda: self.client.request("PATCH", "/settings", json=payload), lambda result: (self.telegram_token.clear(), self.render(result), QMessageBox.information(self, "Second Mind", "Настройки сохранены")))

    def load_speech(self) -> None:
        model = self.whisper_model.currentText()
        device = self.whisper_device.currentText()

        def operation() -> dict[str, Any]:
            self.client.request(
                "PATCH",
                "/settings",
                json={"whisper_model": model, "whisper_device": device},
            )
            return self.client.request("POST", "/speech/load", timeout=900)

        self.work(
            operation,
            lambda result: QMessageBox.information(
                self, "Whisper", f"Модель {result['model']} загружена."
            ),
        )

    def unload_speech(self) -> None:
        self.work(
            lambda: self.client.request("POST", "/speech/unload"),
            lambda _: QMessageBox.information(self, "Whisper", "Модель выгружена."),
        )

    def test_speech(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Аудиофайл", "", "Audio (*.wav *.mp3 *.ogg *.m4a *.flac)"
        )
        if not path:
            return
        audio = Path(path)
        self.work(
            lambda: self.client.request(
                "POST",
                "/speech/transcribe",
                files={"file": (audio.name, audio.read_bytes())},
                timeout=900,
            ),
            lambda result: QMessageBox.information(
                self,
                "Распознавание",
                f"Язык: {result['language']}\n\n{result['text']}",
            ),
        )


class UpdatesPage(BasePage):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(client, "Обновления")
        self.release: dict[str, Any] | None = None
        self.download: dict[str, Any] | None = None
        self._manual_check = False

        versions = QFrame()
        versions.setObjectName("card")
        version_layout = QFormLayout(versions)
        self.current_version = QLabel("—")
        self.available_version = QLabel("—")
        self.authenticity = QLabel("Ожидание проверки")
        self.authenticity.setWordWrap(True)
        version_layout.addRow("Текущая версия", self.current_version)
        version_layout.addRow("Доступная версия", self.available_version)
        version_layout.addRow("Подлинность", self.authenticity)
        self.layout.addWidget(versions)

        options = QFormLayout()
        self.repository = QLineEdit()
        self.repository.setPlaceholderText("owner/repository")
        self.automatic = QCheckBox("Проверять обновления автоматически")
        self.interval = QSpinBox()
        self.interval.setRange(1, 168)
        self.interval.setSuffix(" ч")
        options.addRow("GitHub Releases", self.repository)
        options.addRow("Автопроверка", self.automatic)
        options.addRow("Интервал", self.interval)
        self.layout.addLayout(options)

        controls = QHBoxLayout()
        self.check_button = QPushButton("Проверить сейчас")
        self.check_button.clicked.connect(lambda: self.check_updates(True))
        save_button = QPushButton("Сохранить настройки")
        save_button.clicked.connect(self.save_settings)
        self.download_button = QPushButton("Скачать")
        self.download_button.clicked.connect(self.start_download)
        self.download_button.setEnabled(False)
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.setEnabled(False)
        self.install_button = QPushButton("Установить и перезапустить")
        self.install_button.clicked.connect(self.install_update)
        self.install_button.setEnabled(False)
        for button in (
            self.check_button,
            save_button,
            self.download_button,
            self.cancel_button,
            self.install_button,
        ):
            controls.addWidget(button)
        controls.addStretch()
        self.layout.addLayout(controls)

        self.status_label = QLabel("Проверка ещё не выполнялась.")
        self.status_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.changelog = QPlainTextEdit()
        self.changelog.setReadOnly(True)
        self.changelog.setPlaceholderText("CHANGELOG релиза появится после проверки.")
        self.layout.addWidget(self.status_label)
        self.layout.addWidget(self.progress)
        self.layout.addWidget(QLabel("CHANGELOG"))
        self.layout.addWidget(self.changelog, 1)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self.poll_download)
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(lambda: self.check_updates(False))

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", "/settings"), self.render_settings)
        self.work(lambda: self.client.request("GET", "/updates/status"), self.render_status)

    def render_settings(self, value: dict[str, Any]) -> None:
        self.repository.setText(value.get("update_repository") or "")
        self.automatic.setChecked(bool(value.get("automatic_update_checks", True)))
        hours = int(value.get("update_check_interval_hours", 24))
        self.interval.setValue(hours)
        self.auto_timer.stop()
        if self.automatic.isChecked():
            self.auto_timer.start(hours * 60 * 60 * 1000)

    def save_settings(self, notify: bool = True) -> None:
        payload = {
            "update_repository": self.repository.text().strip() or None,
            "automatic_update_checks": self.automatic.isChecked(),
            "update_check_interval_hours": self.interval.value(),
        }

        def saved(value: dict[str, Any]) -> None:
            self.render_settings(value)
            if notify:
                QMessageBox.information(self, "Обновления", "Настройки сохранены.")

        self.work(lambda: self.client.request("PATCH", "/settings", json=payload), saved)

    def start_automatic_checks(self) -> None:
        def configured(value: dict[str, Any]) -> None:
            self.render_settings(value)
            if self.automatic.isChecked():
                self.check_updates(False)

        self.work(lambda: self.client.request("GET", "/settings"), configured)

    def check_updates(self, manual: bool) -> None:
        if not self.repository.text().strip():
            if manual:
                self.show_error("Укажите GitHub-репозиторий обновлений.")
            return
        self._manual_check = manual
        self.check_button.setEnabled(False)
        self.status_label.setText("Проверяю GitHub Releases и подпись манифеста…")

        def operation() -> dict[str, Any]:
            self.client.request(
                "PATCH",
                "/settings",
                json={
                    "update_repository": self.repository.text().strip(),
                    "automatic_update_checks": self.automatic.isChecked(),
                    "update_check_interval_hours": self.interval.value(),
                },
            )
            return self.client.request("GET", "/updates/check", timeout=45)

        self.work(operation, self.render_release, self.check_failed)

    def check_failed(self, message: str) -> None:
        self.check_button.setEnabled(True)
        self.status_label.setText(f"Проверка не выполнена: {message}")
        if self._manual_check:
            self.show_error(message)

    def render_release(self, result: dict[str, Any]) -> None:
        self.release = result
        self.check_button.setEnabled(True)
        self.current_version.setText(result["current_version"])
        self.available_version.setText(result["latest_version"])
        self.authenticity.setText("Подписанный Ed25519-манифест проверен")
        self.changelog.setPlainText(result.get("changelog") or "Описание изменений отсутствует.")
        self.download_button.setEnabled(bool(result["available"]))
        if result["available"]:
            self.status_label.setText(
                f"Доступна версия {result['latest_version']}. Дистрибутив подписан и готов к скачиванию."
            )
        else:
            self.status_label.setText(
                f"Установлена актуальная версия {result['current_version']}."
            )
            if self._manual_check:
                QMessageBox.information(self, "Обновления", self.status_label.text())

    def start_download(self) -> None:
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.install_button.setEnabled(False)
        self.progress.setValue(0)
        self.status_label.setText("Начинаю защищённое скачивание…")
        self.work(
            lambda: self.client.request("POST", "/updates/download", timeout=30),
            lambda _: self.poll_timer.start(),
            self.download_failed,
        )

    def poll_download(self) -> None:
        self.work(
            lambda: self.client.request("GET", "/updates/status"),
            self.render_status,
            self.download_failed,
        )

    def render_status(self, status: dict[str, Any]) -> None:
        phase = status.get("phase", "idle")
        self.progress.setValue(int(status.get("progress_percent", 0)))
        if phase in {"checking", "downloading"}:
            received = int(status.get("bytes_received", 0)) / (1024 * 1024)
            total = int(status.get("bytes_total", 0)) / (1024 * 1024)
            self.status_label.setText(
                f"Скачивание: {received:.1f} из {total:.1f} МБ"
                if total
                else "Проверяю метаданные релиза…"
            )
            self.cancel_button.setEnabled(True)
            if not self.poll_timer.isActive():
                self.poll_timer.start()
        elif phase == "ready":
            self.poll_timer.stop()
            self.download = status.get("download")
            self.cancel_button.setEnabled(False)
            self.install_button.setEnabled(bool(self.download))
            self.status_label.setText(
                "Скачивание завершено. SHA-256 и подпись повторно проверены."
            )
        elif phase in {"failed", "cancelled"}:
            self.poll_timer.stop()
            self.cancel_button.setEnabled(False)
            self.download_button.setEnabled(bool(self.release and self.release.get("available")))
            self.status_label.setText(status.get("error") or "Скачивание отменено.")

    def download_failed(self, message: str) -> None:
        self.poll_timer.stop()
        self.cancel_button.setEnabled(False)
        self.download_button.setEnabled(bool(self.release and self.release.get("available")))
        self.status_label.setText(f"Ошибка скачивания: {message}")
        self.show_error(message)

    def cancel_download(self) -> None:
        self.work(
            lambda: self.client.request("POST", "/updates/cancel"), self.render_status
        )

    def install_update(self) -> None:
        if not self.download:
            return
        if not getattr(sys, "frozen", False):
            self.show_error("Установка обновлений доступна в установленной Windows-версии.")
            return
        answer = QMessageBox.question(
            self,
            "Установка обновления",
            "Second Mind закроется, Core будет остановлен, а база данных — сохранена в резервную копию. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.install_button.setEnabled(False)
        self.status_label.setText("Финальная проверка дистрибутива…")
        self.work(
            lambda: self.client.request(
                "POST",
                "/updates/install",
                params={"path": self.download["path"]},
                timeout=120,
            ),
            self.launch_updater,
            self.download_failed,
        )

    def launch_updater(self, prepared: dict[str, Any]) -> None:
        install_dir = Path(sys.executable).resolve().parent.parent
        bundled_updater = install_dir / "second-mind-updater.exe"
        if not bundled_updater.exists():
            self.show_error("Компонент обновления не найден. Переустановите Second Mind.")
            self.install_button.setEnabled(True)
            return
        updater_copy = (
            Path(prepared["data_dir"])
            / "updates"
            / f"second-mind-updater-{prepared['version']}.exe"
        )
        updater_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled_updater, updater_copy)
        command = [
            str(updater_copy),
            "--installer",
            prepared["path"],
            "--metadata",
            prepared["metadata_path"],
            "--install-dir",
            str(install_dir),
            "--data-dir",
            prepared["data_dir"],
            "--api-url",
            prepared["api_url"],
            "--desktop-pid",
            str(os.getpid()),
            "--current-version",
            prepared["current_version"],
        ]
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=(
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            ),
        )
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget._force_quit = True
        QApplication.quit()


class SetupWizard(QWizard):
    def __init__(self, client: ApiClient, current: dict[str, Any]) -> None:
        super().__init__()
        self.client, self.current = client, current
        self.setWindowTitle("Настройка Second Mind")
        self.setMinimumSize(720, 500)
        intro = QWizardPage()
        intro.setTitle("Добро пожаловать в Second Mind")
        intro.setSubTitle("Все данные по умолчанию остаются на этом компьютере.")
        layout = QVBoxLayout(intro)
        layout.addWidget(QLabel(f"Локальное хранилище:\n{current['data_dir']}"))
        self.addPage(intro)

        llm = QWizardPage()
        llm.setTitle("Bionic / LM Studio Runtime")
        form = QFormLayout(llm)
        self.llm_url = QLineEdit(current["llm_base_url"])
        self.model = QLineEdit(current.get("llm_model") or "")
        form.addRow("Локальный API", self.llm_url)
        form.addRow("Модель (пусто = выбрать автоматически)", self.model)
        self.addPage(llm)

        telegram = QWizardPage()
        telegram.setTitle("Telegram")
        telegram.setSubTitle("Bot token будет сохранён в Windows Credential Manager.")
        form = QFormLayout(telegram)
        self.telegram_token = QLineEdit()
        self.telegram_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.allowed_ids = QLineEdit()
        self.enable_telegram = QCheckBox("Включить long polling")
        form.addRow("Bot token", self.telegram_token)
        form.addRow("Разрешённые User ID через запятую", self.allowed_ids)
        form.addRow("", self.enable_telegram)
        self.addPage(telegram)

        speech = QWizardPage()
        speech.setTitle("Локальное распознавание речи")
        form = QFormLayout(speech)
        self.whisper = QComboBox()
        self.whisper.addItems(["tiny", "base", "small", "medium", "large-v3", "turbo"])
        self.whisper.setCurrentText(current["whisper_model"])
        self.device = QComboBox()
        self.device.addItems(["cpu", "cuda", "auto"])
        form.addRow("Модель Whisper", self.whisper)
        form.addRow("Устройство", self.device)
        self.addPage(speech)

        for title, text in (
            ("MCP", "Second Mind MCP server поставляется вместе с приложением. Конфигурация доступна в разделе MCP-серверы."),
            ("Проверка", "После завершения Dashboard покажет фактическое состояние Core, модели, Telegram, MCP, Whisper и оборудования."),
            ("Готово", "Core работает независимо от окна приложения. Закрытие GUI не остановит Telegram-бота."),
        ):
            page = QWizardPage()
            page.setTitle(title)
            body = QVBoxLayout(page)
            label = QLabel(text)
            label.setWordWrap(True)
            body.addWidget(label)
            self.addPage(page)

    def accept(self) -> None:
        try:
            ids = [int(value.strip()) for value in self.allowed_ids.text().split(",") if value.strip()]
            payload: dict[str, Any] = {
                "llm_base_url": self.llm_url.text().strip(),
                "llm_model": self.model.text().strip() or None,
                "telegram_allowed_user_ids": ids,
                "telegram_enabled": self.enable_telegram.isChecked(),
                "whisper_model": self.whisper.currentText(),
                "whisper_device": self.device.currentText(),
                "setup_completed": True,
            }
            if self.telegram_token.text():
                payload["telegram_token"] = self.telegram_token.text()
            self.client.request("PATCH", "/settings", json=payload)
            if self.enable_telegram.isChecked():
                self.client.request("POST", "/telegram/start")
        except Exception as exc:
            QMessageBox.warning(self, "Настройка", str(exc))
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, client: ApiClient) -> None:
        super().__init__()
        self.client = client
        self.setWindowTitle("Second Mind")
        self.resize(1360, 860)
        root = QWidget()
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(220)
        self.stack = QStackedWidget()
        body.addWidget(self.navigation)
        body.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.updates_page = UpdatesPage(client)
        self.pages: list[tuple[str, BasePage]] = [
            ("Dashboard", DashboardPage(client)),
            ("Чат", ChatPage(client)),
            ("Входящие", CrudTablePage(client, "Входящие", "/inbox", [("transport", "Канал"), ("sender_id", "Отправитель"), ("status", "Статус"), ("created_at", "Время")])),
            ("Telegram", TelegramPage(client)),
            ("Модели ИИ", CrudTablePage(client, "Модели ИИ", "/models", [("display_name", "Модель"), ("type", "Тип"), ("params_string", "Параметры"), ("loaded_instances", "Загрузка")])),
            ("Агенты", CrudTablePage(client, "Агенты", "/agents", [("name", "Агент"), ("description", "Назначение"), ("enabled", "Включён"), ("tools", "Инструменты")])),
            ("MCP-серверы", CrudTablePage(client, "MCP", "/tools", [("name", "Инструмент"), ("description", "Описание"), ("permission", "Доступ")])),
            ("Инструменты", CrudTablePage(client, "Инструменты", "/tools", [("name", "Имя"), ("permission", "Полномочие"), ("description", "Описание")])),
            ("Память", MemoryPage(client)),
            ("Задачи", TasksPage(client)),
            ("Проекты", CreateListPage(client, "Проекты", "/projects", [("name", "Проект"), ("status", "Статус"), ("description", "Описание")], "Название проекта")),
            ("Календарь", RemindersPage(client)),
            ("Документы", DocumentsPage(client)),
            ("Интеграции", DashboardPage(client)),
            ("Журнал событий", CrudTablePage(client, "Журнал событий", "/logs", [("created_at", "Время"), ("agent", "Компонент"), ("action", "Действие"), ("status", "Статус")])),
            ("Обновления", self.updates_page),
            ("Настройки", SettingsPage(client, self.toggle_theme)),
        ]
        for name, page in self.pages:
            self.navigation.addItem(name)
            self.stack.addWidget(page)
        self.navigation.currentRowChanged.connect(self.open_page)
        self.navigation.setCurrentRow(0)
        self._force_quit = False
        self._create_tray()
        QTimer.singleShot(2500, self.updates_page.start_automatic_checks)

    def _create_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = self.tray.contextMenu() or None
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        show = QAction("Открыть Second Mind", self)
        show.triggered.connect(self.show_normal)
        quit_action = QAction("Закрыть интерфейс", self)
        quit_action.triggered.connect(self.quit_gui)
        menu.addAction(show)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def open_page(self, index: int) -> None:
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        self.pages[index][1].refresh()

    def show_normal(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_gui(self) -> None:
        self._force_quit = True
        QApplication.quit()

    def closeEvent(self, event: Any) -> None:
        if self._force_quit:
            event.accept()
        else:
            event.ignore()
            self.hide()
            self.tray.showMessage("Second Mind", "Интерфейс свёрнут. Фоновый Core продолжает работу.")

    def toggle_theme(self) -> None:
        settings = QSettings("SecondMind", "Desktop")
        dark = not settings.value("dark", True, type=bool)
        settings.setValue("dark", dark)
        QApplication.instance().setStyleSheet(DARK_STYLE if dark else LIGHT_STYLE)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Second Mind")
    app.setOrganizationName("SecondMind")
    app.setQuitOnLastWindowClosed(False)
    ui_settings = QSettings("SecondMind", "Desktop")
    app.setStyleSheet(DARK_STYLE if ui_settings.value("dark", True, type=bool) else LIGHT_STYLE)
    config = ConfigManager().load()
    controller = ServiceController(config.api_url)
    try:
        controller.start()
    except Exception as exc:
        QMessageBox.critical(None, "Second Mind", str(exc))
        raise SystemExit(1) from exc
    client = ApiClient(config.api_url)
    settings = client.request("GET", "/settings")
    if not settings["setup_completed"]:
        wizard = SetupWizard(client, settings)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            raise SystemExit(0)
    window = MainWindow(client)
    window.show()
    if "--post-update" in sys.argv:
        index = sys.argv.index("--post-update")
        version = sys.argv[index + 1] if index + 1 < len(sys.argv) else "новая"
        QTimer.singleShot(
            500,
            lambda: QMessageBox.information(
                window,
                "Second Mind обновлён",
                f"Версия {version} установлена. Core перезапущен, данные сохранены.",
            ),
        )
    elif "--update-rolled-back" in sys.argv:
        QTimer.singleShot(
            500,
            lambda: QMessageBox.warning(
                window,
                "Обновление отменено",
                "Новая версия не прошла проверку запуска. Восстановлены предыдущая версия и резервная копия базы данных.",
            ),
        )
    elif "--update-failed" in sys.argv:
        QTimer.singleShot(
            500,
            lambda: QMessageBox.warning(
                window,
                "Обновление не установлено",
                "Установка обновления завершилась ошибкой. Текущее состояние и журнал сохранены в каталоге данных updates.",
            ),
        )
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
