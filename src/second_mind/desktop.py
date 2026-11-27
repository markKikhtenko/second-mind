from __future__ import annotations

import base64
import ctypes
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from PySide6.QtCore import (
    QDateTime,
    QObject,
    QRunnable,
    QSettings,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QDesktopServices, QFont, QGuiApplication, QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from . import __version__
from .config import ConfigManager
from .ui_theme import (
    DARK,
    LIGHT,
    Card,
    MetricCard,
    Palette,
    ResourceCard,
    StatusPill,
    format_bytes,
    format_duration,
    stylesheet,
    svg_icon,
)


def asset_path(name: str) -> Path:
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    candidates = (
        bundled / "second_mind" / "assets" / name,
        bundled / "assets" / name,
        Path(__file__).parent / "assets" / name,
    )
    return next((path for path in candidates if path.exists()), candidates[-1])


def application_icon() -> QIcon:
    icon_name = "app-icon.ico" if sys.platform == "win32" else "app-icon.svg"
    icon = QIcon(str(asset_path(icon_name)))
    return icon if not icon.isNull() else QIcon(str(asset_path("app-icon.svg")))


def local_datetime(value: Any) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime(
            "%d.%m.%Y, %H:%M"
        )
    except (TypeError, ValueError):
        return str(value)


STATUS_LABELS = {
    "open": "Открыта",
    "in_progress": "В работе",
    "completed": "Завершена",
    "cancelled": "Отменена",
    "active": "Активен",
    "paused": "Приостановлен",
    "archived": "В архиве",
    "scheduled": "Запланировано",
    "delivered": "Доставлено",
    "pending": "В очереди",
    "processed": "Обработано",
    "failed": "Ошибка",
    "indexed": "Проиндексирован",
}


def human_status(value: Any) -> str:
    return STATUS_LABELS.get(str(value), str(value or "—"))


class ThemeManager(QObject):
    changed = Signal(str)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.settings = QSettings("SecondMind", "Desktop")
        self.mode = str(self.settings.value("theme", "system"))
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(
                lambda _value: self.apply() if self.mode == "system" else None
            )

    @property
    def dark(self) -> bool:
        if self.mode == "dark":
            return True
        if self.mode == "light":
            return False
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark

    @property
    def palette(self) -> Palette:
        return DARK if self.dark else LIGHT

    def apply(self) -> None:
        self.app.setStyleSheet(stylesheet(self.palette))
        self.changed.emit(self.mode)

    def set_mode(self, mode: str) -> None:
        if mode in {"system", "light", "dark"}:
            self.mode = mode
            self.settings.setValue("theme", mode)
            self.apply()


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        with httpx.Client(timeout=kwargs.pop("timeout", 30)) as client:
            response = client.request(method, f"{self.base_url}/api{path}", **kwargs)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                try:
                    detail = response.json().get("detail")
                except ValueError:
                    detail = None
                raise RuntimeError(detail or str(exc)) from exc
            return None if response.status_code == 204 else response.json()


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
            result = self.operation()
        except Exception as exc:
            with suppress(RuntimeError):
                self.signals.error.emit(str(exc))
        else:
            with suppress(RuntimeError):
                self.signals.result.emit(result)


class WorkerBridge(QObject):
    """Deliver worker callbacks on the GUI thread and keep the runnable alive."""

    def __init__(
        self,
        worker: Worker,
        success: Callable[[Any], None],
        error: Callable[[str], None],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.worker: Worker | None = worker
        self.success = success
        self.error = error

    @Slot(object)
    def deliver_result(self, value: Any) -> None:
        try:
            self.success(value)
        finally:
            self.worker = None
            self.deleteLater()

    @Slot(str)
    def deliver_error(self, message: str) -> None:
        try:
            self.error(message)
        finally:
            self.worker = None
            self.deleteLater()


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
                str(executable.parent.parent / "second-mind-core" / "second-mind-core.exe")
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
        raise RuntimeError("Core не запустился за 120 секунд")

    def stop(self) -> None:
        if not self.running():
            return
        with suppress(httpx.HTTPError):
            httpx.post(f"{self.api_url}/api/system/shutdown", timeout=5)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not self.running():
                return
            time.sleep(0.25)
        raise RuntimeError("Core не завершился за 30 секунд")

    def restart(self) -> None:
        self.stop()
        self.start()


class BasePage(QWidget):
    def __init__(
        self,
        client: ApiClient,
        theme: ThemeManager,
        title: str,
        subtitle: str = "",
    ) -> None:
        super().__init__()
        self.client = client
        self.theme = theme
        self.pool = QThreadPool.globalInstance()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(28, 24, 28, 24)
        self.layout.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        titles.addWidget(heading)
        if subtitle:
            description = QLabel(subtitle)
            description.setObjectName("pageSubtitle")
            description.setWordWrap(True)
            titles.addWidget(description)
        header.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        header.addLayout(self.actions)
        self.layout.addLayout(header)

    def work(
        self,
        operation: Callable[[], Any],
        success: Callable[[Any], None],
        error: Callable[[str], None] | None = None,
    ) -> None:
        worker = Worker(operation)
        bridge = WorkerBridge(worker, success, error or self.show_error, self)
        worker.signals.result.connect(
            bridge.deliver_result, Qt.ConnectionType.QueuedConnection
        )
        worker.signals.error.connect(
            bridge.deliver_error, Qt.ConnectionType.QueuedConnection
        )
        self.pool.start(worker)

    def button(
        self,
        text: str,
        icon: str,
        callback: Callable[[], None],
        *,
        kind: str = "secondary",
    ) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(svg_icon(icon, self.theme.palette.muted))
        button.setIconSize(QSize(17, 17))
        button.setProperty("kind", kind)
        button.clicked.connect(callback)
        return button

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Second Mind", message)

    def refresh(self) -> None:
        pass


class DashboardPage(BasePage):
    health_changed = Signal(dict)

    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Обзор",
            "Состояние локального ядра, моделей, очередей и ресурсов в реальном времени.",
        )
        self.actions.addWidget(self.button("Обновить", "refresh", self.refresh))
        self.summary = QLabel("Подключение к Core…")
        self.summary.setProperty("role", "muted")
        self.layout.addWidget(self.summary)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.metrics: dict[str, MetricCard] = {}
        definitions = (
            ("core", "Core", "overview"),
            ("runtime", "LM Studio Runtime", "models"),
            ("model", "Текущая модель", "skills"),
            ("telegram", "Telegram", "telegram"),
            ("speech", "Whisper", "assistant"),
            ("mcp", "MCP", "tools"),
            ("requests", "Запросы", "logs"),
            ("queue", "Очередь", "inbox"),
        )
        for index, (key, title, icon) in enumerate(definitions):
            card = MetricCard(title, icon)
            self.metrics[key] = card
            grid.addWidget(card, index // 4, index % 4)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        self.layout.addLayout(grid)
        section = QLabel("Ресурсы")
        section.setObjectName("sectionTitle")
        self.layout.addWidget(section)
        resources = QGridLayout()
        resources.setSpacing(12)
        self.cpu = ResourceCard("CPU", theme.palette.accent)
        self.ram = ResourceCard("Оперативная память", "#41b8d5")
        self.gpu = ResourceCard("GPU", "#52d29a")
        self.vram = ResourceCard("Видеопамять", "#f2bd58")
        for column, card in enumerate((self.cpu, self.ram, self.gpu, self.vram)):
            resources.addWidget(card, 0, column)
            resources.setColumnStretch(column, 1)
        self.layout.addLayout(resources)
        lower = QGridLayout()
        lower.setSpacing(12)
        activity = Card()
        heading = QLabel("Последние действия")
        heading.setObjectName("sectionTitle")
        self.activity = QTableWidget(0, 4)
        self.activity.setHorizontalHeaderLabels(["Время", "Компонент", "Действие", "Статус"])
        self.activity.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.activity.verticalHeader().setVisible(False)
        self.activity.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.activity.setMaximumHeight(220)
        activity.body.addWidget(heading)
        activity.body.addWidget(self.activity)
        lower.addWidget(activity, 0, 0, 1, 2)
        state = Card()
        heading = QLabel("Данные и версия")
        heading.setObjectName("sectionTitle")
        self.data_state = QLabel("—")
        self.data_state.setWordWrap(True)
        self.last_error = QLabel("Ошибок не зарегистрировано")
        self.last_error.setWordWrap(True)
        self.last_error.setProperty("role", "muted")
        state.body.addWidget(heading)
        state.body.addWidget(self.data_state)
        state.body.addWidget(self.last_error)
        state.body.addStretch()
        lower.addWidget(state, 0, 2)
        lower.setColumnStretch(0, 1)
        lower.setColumnStretch(1, 1)
        lower.setColumnStretch(2, 1)
        self.layout.addLayout(lower, 1)
        self._busy = False
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.work(
            lambda: self.client.request("GET", "/health", timeout=15),
            self.render,
            self.failed,
        )

    def failed(self, message: str) -> None:
        self._busy = False
        self.summary.setText(f"Core недоступен: {message}")
        self.metrics["core"].set_value("Отключён", "Проверьте службу Core")
        self.health_changed.emit({"online": False})

    def render(self, data: dict[str, Any]) -> None:
        self._busy = False
        self.health_changed.emit({"online": True, **data})
        self.summary.setText(
            f"Данные локальных компонентов • обновлено {local_datetime(data.get('time'))}"
        )
        self.metrics["core"].set_value(
            "Работает",
            f"{data['core']['version']} • {format_duration(data.get('uptime_seconds'))}",
        )
        llm = data.get("llm", {})
        self.metrics["runtime"].set_value(
            "Доступен" if llm.get("available") else "Недоступен",
            f"Моделей: {llm.get('models', 0)}"
            if llm.get("available")
            else str(llm.get("error", "")),
        )
        self.metrics["model"].set_value(
            str(llm.get("current_model") or "Не выбрана"),
            "Загружена в Runtime" if llm.get("available") else "Ожидание Runtime",
        )
        telegram = data.get("telegram", {})
        self.metrics["telegram"].set_value(
            "Запущен" if telegram.get("running") else "Остановлен",
            "Настроен" if telegram.get("configured") else "Токен не настроен",
        )
        speech = data.get("speech", {})
        self.metrics["speech"].set_value(
            "Загружен" if speech.get("loaded") else "Не загружен",
            f"{speech.get('model', '—')} • {speech.get('device', '—')}",
        )
        mcp = data.get("mcp", {})
        self.metrics["mcp"].set_value(
            "Готов" if mcp.get("available") else "Недоступен",
            f"Инструментов: {mcp.get('tools', 0)}",
        )
        self.metrics["requests"].set_value(
            str(data.get("requests", 0)),
            f"Среднее время: {data.get('average_request_ms', 0):.0f} мс",
        )
        queue = data.get("queue", {})
        self.metrics["queue"].set_value(
            str(queue.get("pending", 0)), f"Ошибок: {queue.get('failed', 0)}"
        )
        system = data.get("system", {})
        cpu = float(system.get("cpu_percent", 0))
        ram = float(system.get("ram_percent", 0))
        self.cpu.update_value(cpu)
        self.ram.update_value(
            ram,
            f"{format_bytes(system.get('ram_used_bytes'))} / {format_bytes(system.get('ram_total_bytes'))}",
        )
        gpu = system.get("gpu")
        if gpu:
            utilization = float(gpu.get("utilization_percent", 0))
            total = float(gpu.get("vram_total_mb", 0))
            used = float(gpu.get("vram_used_mb", 0))
            self.gpu.update_value(utilization, f"{utilization:.0f}% • {gpu.get('name', 'GPU')}")
            self.vram.update_value(used / total * 100 if total else 0, f"{used:.0f} / {total:.0f} МБ")
        else:
            self.gpu.update_value(0, "Не обнаружен")
            self.vram.update_value(0, "Нет данных")
        rows = data.get("recent_activity", [])
        self.activity.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                local_datetime(row.get("created_at")),
                row.get("agent", "—"),
                str(row.get("action", "—")).replace("_", " "),
                human_status(row.get("status")),
            )
            for column, value in enumerate(values):
                self.activity.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.data_state.setText(
            f"Задачи: {data.get('open_tasks', 0)}\n"
            f"Записи памяти: {data.get('memories', 0)}\n"
            f"Версия: {data['core']['version']}"
        )
        error = data.get("last_error")
        self.last_error.setText(
            f"Последняя ошибка: {str(error.get('action', '')).replace('_', ' ')} • "
            f"{local_datetime(error.get('created_at'))}"
            if error
            else "Ошибок не зарегистрировано"
        )


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str, theme: ThemeManager) -> None:
        super().__init__()
        palette = theme.palette
        user = role == "user"
        self.setStyleSheet(
            f"QFrame{{background:{palette.accent_soft if user else palette.surface};"
            f"border:1px solid {palette.accent if user else palette.border};border-radius:12px;}}"
            "QLabel,QTextBrowser,QToolButton{border:0;background:transparent;}"
        )
        self.setMaximumWidth(820)
        body = QVBoxLayout(self)
        body.setContentsMargins(14, 10, 14, 10)
        top = QHBoxLayout()
        author = QLabel("Вы" if user else "Second Mind")
        author.setStyleSheet("font-weight:700;")
        copy = QToolButton()
        copy.setProperty("kind", "ghost")
        copy.setIcon(svg_icon("copy", palette.muted))
        copy.setToolTip("Копировать")
        copy.setFixedSize(28, 28)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        top.addWidget(author)
        top.addStretch()
        top.addWidget(copy)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setMarkdown(text)
        browser.document().setDefaultStyleSheet(
            f"body{{color:{palette.text};font-family:'Segoe UI';font-size:13px;}}"
            f"pre{{background:{palette.code};border-radius:8px;padding:10px;white-space:pre-wrap;}}"
            "code{font-family:'Cascadia Code','Consolas';}"
        )
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        browser.document().documentLayout().documentSizeChanged.connect(
            lambda size: browser.setFixedHeight(max(34, int(size.height()) + 8))
        )
        body.addLayout(top)
        body.addWidget(browser)


class ComposerEdit(QPlainTextEdit):
    submit = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.submit.emit()
            return
        super().keyPressEvent(event)


class ChatPage(BasePage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Ассистент",
            "Локальный диалог с историей, Markdown, файлами и прозрачными вызовами инструментов.",
        )
        self.actions.addWidget(
            self.button("Новый диалог", "plus", self.new_conversation, kind="secondary")
        )
        body = QHBoxLayout()
        body.setSpacing(12)
        history = Card(padding=10)
        history.setFixedWidth(245)
        title = QLabel("История")
        title.setObjectName("sectionTitle")
        self.conversations = QListWidget()
        self.conversations.setFrameShape(QFrame.Shape.NoFrame)
        self.conversations.currentItemChanged.connect(self.load_conversation)
        history.body.addWidget(title)
        history.body.addWidget(self.conversations, 1)
        history.body.addWidget(
            self.button("Удалить", "delete", self.delete_conversation, kind="ghost")
        )
        body.addWidget(history)
        chat = Card(padding=0)
        chat.body.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        host = QWidget()
        self.messages = QVBoxLayout(host)
        self.messages.setContentsMargins(18, 18, 18, 18)
        self.messages.setSpacing(12)
        self.messages.addStretch()
        self.scroll.setWidget(host)
        chat.body.addWidget(self.scroll, 1)
        separator = QFrame()
        separator.setObjectName("separator")
        chat.body.addWidget(separator)
        composer = QWidget()
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 12, 14, 14)
        self.attachments = QLabel("")
        self.attachments.setProperty("role", "muted")
        self.attachments.hide()
        self.input = ComposerEdit()
        self.input.setPlaceholderText("Напишите сообщение…  Ctrl+Enter — отправить")
        self.input.setMaximumHeight(110)
        self.input.submit.connect(self.submit)
        actions = QHBoxLayout()
        attach = QToolButton()
        attach.setProperty("kind", "ghost")
        attach.setIcon(svg_icon("attachment", theme.palette.muted))
        attach.setToolTip("Прикрепить файлы или изображения")
        attach.clicked.connect(self.attach_files)
        self.stage = QLabel("Готов к запросу")
        self.stage.setProperty("role", "muted")
        self.send = QPushButton("Отправить")
        self.send.setIcon(svg_icon("send", "#ffffff"))
        self.send.clicked.connect(self.submit)
        actions.addWidget(attach)
        actions.addWidget(self.stage)
        actions.addStretch()
        actions.addWidget(self.send)
        composer_layout.addWidget(self.attachments)
        composer_layout.addWidget(self.input)
        composer_layout.addLayout(actions)
        chat.body.addWidget(composer)
        body.addWidget(chat, 1)
        self.layout.addLayout(body, 1)
        self.conversation_id: str | None = None
        self.files: list[Path] = []
        self.welcome: QWidget | None = None
        self._show_welcome()

    def _clear(self) -> None:
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.welcome = None

    def _show_welcome(self) -> None:
        welcome = QFrame()
        welcome.setObjectName("subtleCard")
        welcome.setMaximumWidth(650)
        content = QVBoxLayout(welcome)
        content.setContentsMargins(28, 26, 28, 26)
        content.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(svg_icon("assistant", self.theme.palette.accent).pixmap(34, 34))
        title = QLabel("Чем помочь?")
        title.setStyleSheet("font-size:20px;font-weight:700;")
        description = QLabel(
            "Задайте вопрос, прикрепите документ или попросите локального ассистента "
            "поработать с задачами и памятью."
        )
        description.setProperty("role", "muted")
        description.setWordWrap(True)
        content.addWidget(icon, alignment=Qt.AlignmentFlag.AlignCenter)
        content.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        content.addWidget(description)
        for prompt in (
            "Покажи мои активные задачи",
            "Что сохранено в памяти?",
            "Помоги разобрать входящие",
        ):
            button = QPushButton(prompt)
            button.setProperty("kind", "secondary")
            button.clicked.connect(lambda _checked=False, text=prompt: self.input.setPlainText(text))
            content.addWidget(button)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(30, 70, 30, 0)
        row_layout.addStretch()
        row_layout.addWidget(welcome)
        row_layout.addStretch()
        self.welcome = row
        self.messages.insertWidget(0, row)

    def add_message(self, role: str, text: str) -> None:
        if self.welcome is not None:
            welcome = self.welcome
            self.welcome = None
            self.messages.removeWidget(welcome)
            welcome.deleteLater()
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        bubble = MessageBubble(role, text, self.theme)
        if role == "user":
            layout.addStretch()
            layout.addWidget(bubble)
        else:
            layout.addWidget(bubble)
            layout.addStretch()
        self.messages.insertWidget(self.messages.count() - 1, row)
        QTimer.singleShot(
            0,
            lambda: self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            ),
        )

    def add_tools(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        card = QFrame()
        card.setObjectName("subtleCard")
        layout = QVBoxLayout(card)
        title = QLabel(f"Вызовы инструментов · {len(events)}")
        title.setStyleSheet("font-weight:650;")
        layout.addWidget(title)
        for event in events:
            ok = bool(event.get("result", {}).get("ok", True))
            line = QLabel(
                f"{'Выполнено' if ok else 'Ошибка'} · {event.get('name', 'инструмент')}"
            )
            line.setProperty("role", "muted")
            layout.addWidget(line)
        self.messages.insertWidget(self.messages.count() - 1, card)

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", "/conversations"), self.render_history)

    def render_history(self, rows: list[dict[str, Any]]) -> None:
        selected = self.conversation_id
        self.conversations.blockSignals(True)
        self.conversations.clear()
        for row in rows:
            item = QListWidgetItem(str(row.get("title") or "Новый диалог").strip()[:48])
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(
                f"{row.get('message_count', 0)} сообщений • {local_datetime(row.get('updated_at'))}"
            )
            self.conversations.addItem(item)
            if row["id"] == selected:
                self.conversations.setCurrentItem(item)
        self.conversations.blockSignals(False)

    def load_conversation(
        self, current: QListWidgetItem | None, _old: QListWidgetItem | None
    ) -> None:
        if not current:
            return
        conversation_id = str(current.data(Qt.ItemDataRole.UserRole))
        self.conversation_id = conversation_id
        self.work(
            lambda: self.client.request(
                "GET", f"/conversations/{conversation_id}/messages", params={"limit": 300}
            ),
            self.render_messages,
        )

    def render_messages(self, rows: list[dict[str, Any]]) -> None:
        self._clear()
        if not rows:
            self._show_welcome()
            return
        for row in rows:
            self.add_message(str(row.get("role", "assistant")), str(row.get("content", "")))

    def new_conversation(self) -> None:
        self.conversation_id = None
        self.conversations.clearSelection()
        self._clear()
        self._show_welcome()
        self.stage.setText("Новый диалог")
        self.input.setFocus()

    def delete_conversation(self) -> None:
        if not self.conversation_id:
            return
        if (
            QMessageBox.question(
                self, "Удалить диалог", "Удалить выбранный диалог и всю его историю?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        target = self.conversation_id
        self.work(
            lambda: self.client.request("DELETE", f"/conversations/{target}"),
            lambda _value: (self.new_conversation(), self.refresh()),
        )

    def attach_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Прикрепить файлы",
            "",
            "Поддерживаемые файлы (*.png *.jpg *.jpeg *.webp *.txt *.md *.csv *.json *.pdf *.docx)",
        )
        if paths:
            self.files = [Path(path) for path in paths[:8]]
            self.attachments.setText(
                "Прикреплено: " + ", ".join(path.name for path in self.files)
            )
            self.attachments.show()

    def submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text and not self.files:
            return
        prompt = text or "Проанализируй прикреплённые материалы."
        self.add_message("user", prompt)
        self.input.clear()
        files = list(self.files)
        self.files.clear()
        self.attachments.hide()
        self.send.setEnabled(False)
        self.stage.setText("Подготовка контекста и обращение к модели…")
        conversation_id = self.conversation_id

        def operation() -> dict[str, Any]:
            images: list[str] = []
            for path in files:
                media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                data = path.read_bytes()
                if media_type.startswith("image/"):
                    images.append(
                        f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
                    )
                else:
                    self.client.request(
                        "POST",
                        "/documents",
                        files={"file": (path.name, data, media_type)},
                        timeout=180,
                    )
            return self.client.request(
                "POST",
                "/chat",
                json={
                    "text": prompt,
                    "conversation_id": conversation_id,
                    "transport": "desktop",
                    "images": images,
                },
                timeout=600,
            )

        self.work(operation, self.received, self.failed)

    def received(self, result: dict[str, Any]) -> None:
        self.conversation_id = result["conversation_id"]
        self.add_message("assistant", result["text"])
        self.add_tools(result.get("tool_events", []))
        self.stage.setText("Готово")
        self.send.setEnabled(True)
        self.refresh()

    def failed(self, message: str) -> None:
        self.add_message("assistant", f"**Ошибка выполнения**\n\n{message}")
        self.stage.setText("Запрос завершился с ошибкой")
        self.send.setEnabled(True)


class EmptyState(QFrame):
    def __init__(self, icon: str, title: str, description: str, theme: ThemeManager) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol = QLabel()
        symbol.setPixmap(svg_icon(icon, theme.palette.subtle).pixmap(36, 36))
        heading = QLabel(title)
        heading.setStyleSheet("font-size:16px;font-weight:700;")
        text = QLabel(description)
        text.setProperty("role", "muted")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        layout.addWidget(symbol, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()


class RecordsPage(BasePage):
    def __init__(
        self,
        client: ApiClient,
        theme: ThemeManager,
        title: str,
        subtitle: str,
        endpoint: str,
        columns: list[tuple[str, str, Callable[[Any], str] | None]],
        icon: str,
        empty_title: str,
        empty_text: str,
        *,
        can_create: bool = True,
        can_edit: bool = True,
        can_delete: bool = True,
    ) -> None:
        super().__init__(client, theme, title, subtitle)
        self.endpoint = endpoint
        self.columns = columns
        self.rows: list[dict[str, Any]] = []
        if can_create:
            self.actions.addWidget(
                self.button("Создать", "plus", self.create_record, kind="secondary")
            )
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filters)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Все статусы", None)
        self.status_filter.currentIndexChanged.connect(self.apply_filters)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.status_filter)
        toolbar.addWidget(self.button("Обновить", "refresh", self.refresh, kind="ghost"))
        if can_edit:
            toolbar.addWidget(
                self.button("Изменить", "edit", self.edit_record, kind="secondary")
            )
        toolbar.addWidget(
            self.button("Открыть", "overview", self.view_record, kind="secondary")
        )
        if can_delete:
            toolbar.addWidget(
                self.button("Удалить", "delete", self.delete_record, kind="danger")
            )
        self.layout.addLayout(toolbar)
        self.stack = QStackedWidget()
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels([label for _key, label, _format in columns])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.doubleClicked.connect(self.view_record)
        self.empty = EmptyState(icon, empty_title, empty_text, theme)
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)
        self.layout.addWidget(self.stack, 1)

    def request_params(self) -> dict[str, Any]:
        return {}

    def refresh(self) -> None:
        endpoint = self.endpoint
        params = self.request_params()
        self.work(
            lambda: self.client.request("GET", endpoint, params=params),
            self.render,
        )

    def render(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        statuses = sorted({str(row.get("status")) for row in rows if row.get("status")})
        current = self.status_filter.currentData()
        self.status_filter.blockSignals(True)
        self.status_filter.clear()
        self.status_filter.addItem("Все статусы", None)
        for status in statuses:
            self.status_filter.addItem(human_status(status), status)
        self.status_filter.setCurrentIndex(max(0, self.status_filter.findData(current)))
        self.status_filter.blockSignals(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, (key, _label, formatter) in enumerate(self.columns):
                raw = row.get(key)
                value = formatter(raw) if formatter else str(raw if raw is not None else "—")
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row_index, column_index, item)
        self.table.setSortingEnabled(True)
        self.stack.setCurrentWidget(self.table if rows else self.empty)
        self.apply_filters()

    def apply_filters(self) -> None:
        query = self.search.text().strip().casefold()
        status = self.status_filter.currentData()
        visible = 0
        for row_index in range(self.table.rowCount()):
            item = self.table.item(row_index, 0)
            row = item.data(Qt.ItemDataRole.UserRole) if item else {}
            matches_text = not query or query in " ".join(
                str(value) for value in row.values()
            ).casefold()
            matches_status = status is None or row.get("status") == status
            hidden = not (matches_text and matches_status)
            self.table.setRowHidden(row_index, hidden)
            visible += 0 if hidden else 1
        if self.rows:
            self.stack.setCurrentWidget(self.table if visible else self.empty)

    def selected(self) -> dict[str, Any] | None:
        row_index = self.table.currentRow()
        item = self.table.item(row_index, 0) if row_index >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def require_selected(self) -> dict[str, Any] | None:
        row = self.selected()
        if not row:
            QMessageBox.information(self, "Second Mind", "Сначала выберите запись.")
        return row

    def create_record(self) -> None:
        pass

    def edit_record(self) -> None:
        pass

    def delete_label(self, row: dict[str, Any]) -> str:
        return str(
            row.get("title") or row.get("name") or row.get("content") or "запись"
        )[:80]

    def delete_record(self) -> None:
        row = self.require_selected()
        if not row:
            return
        if (
            QMessageBox.question(
                self,
                "Подтверждение удаления",
                f"Удалить «{self.delete_label(row)}»? Это действие нельзя отменить.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.work(
            lambda: self.client.request("DELETE", f"{self.endpoint}/{row['id']}"),
            lambda _value: self.refresh(),
        )

    def view_record(self) -> None:
        row = self.require_selected()
        if not row:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Карточка записи")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        card = Card()
        for key, label, formatter in self.columns:
            value = row.get(key)
            rendered = formatter(value) if formatter else str(value if value is not None else "—")
            name = QLabel(label.upper())
            name.setObjectName("eyebrow")
            content = QLabel(rendered)
            content.setWordWrap(True)
            content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card.body.addWidget(name)
            card.body.addWidget(content)
        layout.addWidget(card)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()


class TaskDialog(QDialog):
    def __init__(self, parent: QWidget, row: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изменить задачу" if row else "Новая задача")
        self.resize(520, 410)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(str((row or {}).get("title", "")))
        self.description = QPlainTextEdit(str((row or {}).get("description", "")))
        self.description.setMaximumHeight(120)
        self.priority = QComboBox()
        for label, value in (
            ("Обычный", 0),
            ("Низкий", 1),
            ("Средний", 2),
            ("Высокий", 3),
            ("Критический", 4),
        ):
            self.priority.addItem(label, value)
        self.priority.setCurrentIndex(
            max(0, self.priority.findData((row or {}).get("priority", 0)))
        )
        self.status = QComboBox()
        for value in ("open", "in_progress", "completed", "cancelled"):
            self.status.addItem(human_status(value), value)
        self.status.setCurrentIndex(
            max(0, self.status.findData((row or {}).get("status", "open")))
        )
        self.no_due = QCheckBox("Без срока")
        self.due = QDateTimeEdit(QDateTime.currentDateTime().addDays(1))
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("dd.MM.yyyy HH:mm")
        if (row or {}).get("due_at"):
            parsed = QDateTime.fromString(str(row["due_at"]), Qt.DateFormat.ISODate)
            if parsed.isValid():
                self.due.setDateTime(parsed.toLocalTime())
        else:
            self.no_due.setChecked(True)
            self.due.setEnabled(False)
        self.no_due.toggled.connect(lambda checked: self.due.setEnabled(not checked))
        form.addRow("Название", self.title)
        form.addRow("Описание", self.description)
        form.addRow("Приоритет", self.priority)
        form.addRow("Статус", self.status)
        form.addRow("Срок", self.due)
        form.addRow("", self.no_due)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self, creating: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title.text().strip(),
            "description": self.description.toPlainText().strip(),
            "priority": self.priority.currentData(),
            "due_at": None
            if self.no_due.isChecked()
            else self.due.dateTime().toUTC().toString(Qt.DateFormat.ISODate),
        }
        if not creating:
            payload["status"] = self.status.currentData()
        return payload


class TasksPage(RecordsPage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Задачи",
            "Планирование, сроки и текущий статус без технических полей.",
            "/tasks",
            [
                ("title", "Задача", None),
                ("status", "Статус", human_status),
                ("due_at", "Срок", local_datetime),
                (
                    "priority",
                    "Приоритет",
                    lambda value: (
                        "Обычный",
                        "Низкий",
                        "Средний",
                        "Высокий",
                        "Критический",
                    )[int(value or 0)],
                ),
                ("updated_at", "Изменена", local_datetime),
            ],
            "tasks",
            "Задач пока нет",
            "Создайте первую задачу — она будет доступна ассистенту.",
        )

    def create_record(self) -> None:
        dialog = TaskDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload(True)
        if not payload["title"]:
            self.show_error("Введите название задачи.")
            return
        self.work(
            lambda: self.client.request("POST", "/tasks", json=payload),
            lambda _value: self.refresh(),
        )

    def edit_record(self) -> None:
        row = self.require_selected()
        if not row:
            return
        dialog = TaskDialog(self, row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.payload(False)
            self.work(
                lambda: self.client.request(
                    "PATCH", f"/tasks/{row['id']}", json=payload
                ),
                lambda _value: self.refresh(),
            )


class MemoryDialog(QDialog):
    def __init__(self, parent: QWidget, row: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изменить запись" if row else "Новая запись памяти")
        self.resize(540, 360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        for value, label in (
            ("fact", "Факт"),
            ("note", "Заметка"),
            ("preference", "Предпочтение"),
            ("entity", "Сущность"),
            ("summary", "Резюме"),
        ):
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(
            max(0, self.kind.findData((row or {}).get("kind", "fact")))
        )
        self.content = QPlainTextEdit(str((row or {}).get("content", "")))
        form.addRow("Тип", self.kind)
        form.addRow("Содержание", self.content)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self, creating: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind.currentData(),
            "content": self.content.toPlainText().strip(),
        }
        if creating:
            payload["source"] = "desktop"
        return payload


class MemoryPage(RecordsPage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        kinds = {
            "fact": "Факт",
            "note": "Заметка",
            "preference": "Предпочтение",
            "entity": "Сущность",
            "summary": "Резюме",
        }
        super().__init__(
            client,
            theme,
            "Память",
            "Локальные факты, заметки и предпочтения, доступные ассистенту.",
            "/memories",
            [
                ("kind", "Тип", lambda value: kinds.get(str(value), str(value))),
                ("content", "Содержание", None),
                ("source", "Источник", lambda value: str(value).capitalize()),
                ("updated_at", "Обновлена", local_datetime),
            ],
            "memory",
            "Память пуста",
            "Добавьте факт или заметку. Дубликаты автоматически объединяются.",
        )

    def request_params(self) -> dict[str, Any]:
        return {"q": self.search.text().strip(), "limit": 500}

    def create_record(self) -> None:
        dialog = MemoryDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.payload(True)
            if payload["content"]:
                self.work(
                    lambda: self.client.request("POST", "/memories", json=payload),
                    lambda _value: self.refresh(),
                )

    def edit_record(self) -> None:
        row = self.require_selected()
        if not row:
            return
        dialog = MemoryDialog(self, row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.payload(False)
            self.work(
                lambda: self.client.request(
                    "PATCH", f"/memories/{row['id']}", json=payload
                ),
                lambda _value: self.refresh(),
            )


class ProjectDialog(QDialog):
    def __init__(self, parent: QWidget, row: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изменить проект" if row else "Новый проект")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(str((row or {}).get("name", "")))
        self.description = QPlainTextEdit(str((row or {}).get("description", "")))
        self.status = QComboBox()
        for value in ("active", "paused", "completed", "archived"):
            self.status.addItem(human_status(value), value)
        self.status.setCurrentIndex(
            max(0, self.status.findData((row or {}).get("status", "active")))
        )
        form.addRow("Название", self.name)
        form.addRow("Описание", self.description)
        form.addRow("Статус", self.status)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self, creating: bool) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name.text().strip(),
            "description": self.description.toPlainText().strip(),
        }
        if not creating:
            value["status"] = self.status.currentData()
        return value


class ProjectsPage(RecordsPage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Проекты",
            "Группировка задач и контекста по направлениям работы.",
            "/projects",
            [
                ("name", "Проект", None),
                ("status", "Статус", human_status),
                ("description", "Описание", None),
                ("updated_at", "Изменён", local_datetime),
            ],
            "projects",
            "Проектов пока нет",
            "Создайте проект, чтобы объединить связанные задачи и материалы.",
        )

    def create_record(self) -> None:
        dialog = ProjectDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.payload(True)
            self.work(
                lambda: self.client.request("POST", "/projects", json=payload),
                lambda _value: self.refresh(),
            )

    def edit_record(self) -> None:
        row = self.require_selected()
        if not row:
            return
        dialog = ProjectDialog(self, row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.payload(False)
            self.work(
                lambda: self.client.request(
                    "PATCH", f"/projects/{row['id']}", json=payload
                ),
                lambda _value: self.refresh(),
            )


class ReminderDialog(QDialog):
    def __init__(self, parent: QWidget, row: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изменить напоминание" if row else "Новое напоминание")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.text = QLineEdit(str((row or {}).get("text", "")))
        self.due = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("dd.MM.yyyy HH:mm")
        if (row or {}).get("due_at"):
            parsed = QDateTime.fromString(str(row["due_at"]), Qt.DateFormat.ISODate)
            if parsed.isValid():
                self.due.setDateTime(parsed.toLocalTime())
        self.target = QComboBox()
        self.target.addItem("Приложение", "desktop")
        self.target.addItem("Telegram", "telegram")
        self.target.setCurrentIndex(
            max(0, self.target.findData((row or {}).get("target_transport", "desktop")))
        )
        self.status = QComboBox()
        for value in ("scheduled", "delivered", "cancelled"):
            self.status.addItem(human_status(value), value)
        self.status.setCurrentIndex(
            max(0, self.status.findData((row or {}).get("status", "scheduled")))
        )
        form.addRow("Текст", self.text)
        form.addRow("Дата и время", self.due)
        form.addRow("Канал", self.target)
        form.addRow("Статус", self.status)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self, creating: bool) -> dict[str, Any]:
        value: dict[str, Any] = {
            "text": self.text.text().strip(),
            "due_at": self.due.dateTime().toUTC().toString(Qt.DateFormat.ISODate),
        }
        if creating:
            value["target_transport"] = self.target.currentData()
        else:
            value["status"] = self.status.currentData()
        return value


class RemindersPage(RecordsPage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Календарь",
            "Локальные напоминания с доставкой в приложение или Telegram.",
            "/reminders",
            [
                ("text", "Напоминание", None),
                ("due_at", "Когда", local_datetime),
                (
                    "target_transport",
                    "Канал",
                    lambda value: "Telegram" if value == "telegram" else "Приложение",
                ),
                ("status", "Статус", human_status),
            ],
            "calendar",
            "Напоминаний нет",
            "Создайте напоминание и выберите удобный канал доставки.",
        )

    def create_record(self) -> None:
        dialog = ReminderDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.payload(True)
            self.work(
                lambda: self.client.request("POST", "/reminders", json=payload),
                lambda _value: self.refresh(),
            )

    def edit_record(self) -> None:
        row = self.require_selected()
        if not row:
            return
        dialog = ReminderDialog(self, row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.payload(False)
            self.work(
                lambda: self.client.request(
                    "PATCH", f"/reminders/{row['id']}", json=payload
                ),
                lambda _value: self.refresh(),
            )


class DocumentsPage(RecordsPage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Документы",
            "Локальные материалы и извлечённый текст для поиска ассистента.",
            "/documents",
            [
                ("filename", "Документ", None),
                ("media_type", "Тип", None),
                ("status", "Статус", human_status),
                (
                    "text_length",
                    "Символов",
                    lambda value: f"{int(value or 0):,}".replace(",", " "),
                ),
                ("created_at", "Добавлен", local_datetime),
            ],
            "documents",
            "Документов пока нет",
            "Добавьте TXT, Markdown, CSV, JSON, PDF или DOCX.",
            can_edit=False,
        )

    def create_record(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Добавить документы",
            "",
            "Документы (*.txt *.md *.csv *.json *.pdf *.docx)",
        )
        if not paths:
            return

        def upload() -> int:
            for value in paths:
                path = Path(value)
                self.client.request(
                    "POST",
                    "/documents",
                    files={"file": (path.name, path.read_bytes())},
                    timeout=180,
                )
            return len(paths)

        self.work(
            upload,
            lambda count: (
                QMessageBox.information(self, "Документы", f"Добавлено документов: {count}"),
                self.refresh(),
            ),
        )


class InboxPage(RecordsPage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Входящие",
            "Очередь сообщений из подключённых каналов и результаты обработки.",
            "/inbox",
            [
                (
                    "transport",
                    "Канал",
                    lambda value: "Telegram" if value == "telegram" else str(value),
                ),
                ("sender_id", "Отправитель", lambda _value: "Пользователь"),
                ("status", "Статус", human_status),
                ("attempts", "Попытки", lambda value: str(value or 0)),
                ("created_at", "Получено", local_datetime),
            ],
            "inbox",
            "Входящих нет",
            "Новые сообщения из Telegram и других каналов появятся здесь.",
            can_create=False,
            can_edit=False,
        )

    def delete_label(self, row: dict[str, Any]) -> str:
        return f"входящее сообщение от {local_datetime(row.get('created_at'))}"


class ReadOnlyPage(RecordsPage):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs, can_create=False, can_edit=False, can_delete=False)


class TelegramPage(BasePage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Telegram",
            "Управление локальным шлюзом и проверка соединения с Bot API.",
        )
        self.actions.addWidget(
            self.button(
                "Проверить соединение", "shield", self.test_connection, kind="secondary"
            )
        )
        grid = QGridLayout()
        grid.setSpacing(12)
        self.status_card = MetricCard("Состояние шлюза", "telegram")
        self.config_card = MetricCard("Конфигурация", "settings")
        self.users_card = MetricCard("Разрешённые пользователи", "agents")
        grid.addWidget(self.status_card, 0, 0)
        grid.addWidget(self.config_card, 0, 1)
        grid.addWidget(self.users_card, 0, 2)
        self.layout.addLayout(grid)
        controls = Card()
        title = QLabel("Управление")
        title.setObjectName("sectionTitle")
        text = QLabel(
            "Long polling работает в Core и не зависит от открытого окна приложения."
        )
        text.setProperty("role", "muted")
        actions = QHBoxLayout()
        for label, action, icon in (
            ("Запустить", "start", "plus"),
            ("Остановить", "stop", "delete"),
            ("Перезапустить", "restart", "refresh"),
        ):
            actions.addWidget(
                self.button(
                    label,
                    icon,
                    lambda value=action: self.action(value),
                    kind="secondary",
                )
            )
        actions.addStretch()
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setProperty("role", "muted")
        controls.body.addWidget(title)
        controls.body.addWidget(text)
        controls.body.addLayout(actions)
        controls.body.addWidget(self.error)
        self.layout.addWidget(controls)
        self.layout.addStretch()

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", "/telegram/status"), self.render)

    def action(self, action: str) -> None:
        self.work(lambda: self.client.request("POST", f"/telegram/{action}"), self.render)

    def test_connection(self) -> None:
        self.work(
            lambda: self.client.request("POST", "/telegram/test", timeout=30),
            lambda result: QMessageBox.information(
                self,
                "Telegram",
                f"Соединение установлено.\nБот: @{result.get('username') or 'без имени'}",
            ),
        )

    def render(self, result: dict[str, Any]) -> None:
        self.status_card.set_value(
            "Запущен" if result.get("running") else "Остановлен", "Long polling"
        )
        self.config_card.set_value(
            "Готова" if result.get("configured") else "Не настроена",
            "Токен в Credential Manager"
            if result.get("configured")
            else "Укажите токен в настройках",
        )
        self.users_card.set_value(str(result.get("allowed_users", 0)), "Allowlist")
        self.error.setText(
            f"Последняя ошибка: {result['last_error']}"
            if result.get("last_error")
            else "Ошибок соединения нет."
        )


class ComingSoonPage(BasePage):
    def __init__(
        self,
        client: ApiClient,
        theme: ThemeManager,
        title: str,
        description: str,
        release: str,
    ) -> None:
        super().__init__(client, theme, title, description)
        card = Card()
        icon = QLabel()
        icon.setPixmap(svg_icon("skills", theme.palette.accent).pixmap(44, 44))
        status = StatusPill()
        status.set_status(f"Запланировано на {release}", "accent", theme.palette)
        heading = QLabel("Раздел ещё не реализован")
        heading.setStyleSheet("font-size:18px;font-weight:700;")
        text = QLabel(
            "Интерфейс честно показывает статус функции и не подменяет её демонстрационными данными."
        )
        text.setProperty("role", "muted")
        text.setWordWrap(True)
        card.body.addWidget(icon, alignment=Qt.AlignmentFlag.AlignCenter)
        card.body.addWidget(status, alignment=Qt.AlignmentFlag.AlignCenter)
        card.body.addWidget(heading, alignment=Qt.AlignmentFlag.AlignCenter)
        card.body.addWidget(text, alignment=Qt.AlignmentFlag.AlignCenter)
        card.body.addStretch()
        self.layout.addWidget(card, 1)


class StoragePage(BasePage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Хранилище",
            "SQLite, вложения, резервные копии и свободное место — локально и прозрачно.",
        )
        self.actions.addWidget(
            self.button("Открыть папку", "folder", self.open_folder, kind="secondary")
        )
        self.actions.addWidget(self.button("Обновить", "refresh", self.refresh, kind="ghost"))
        self.data: dict[str, Any] = {}
        path_card = Card()
        row = QHBoxLayout()
        labels = QVBoxLayout()
        title = QLabel("Расположение данных")
        title.setObjectName("sectionTitle")
        self.path = QLabel("—")
        self.path.setProperty("role", "muted")
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        labels.addWidget(title)
        labels.addWidget(self.path)
        self.integrity = StatusPill("Проверка…")
        row.addLayout(labels, 1)
        row.addWidget(self.integrity)
        path_card.body.addLayout(row)
        self.layout.addWidget(path_card)
        stats = QGridLayout()
        stats.setSpacing(12)
        self.stat_cards: dict[str, MetricCard] = {}
        for index, (key, title, icon) in enumerate(
            (
                ("database", "База данных", "storage"),
                ("attachments", "Вложения", "attachment"),
                ("backups", "Резервные копии", "shield"),
                ("free", "Свободно на диске", "folder"),
            )
        ):
            card = MetricCard(title, icon)
            self.stat_cards[key] = card
            stats.addWidget(card, 0, index)
        self.layout.addLayout(stats)
        tabs = QTabWidget()
        overview = QWidget()
        overview_layout = QHBoxLayout(overview)
        overview_layout.setContentsMargins(16, 16, 16, 16)
        counts_card = Card()
        count_title = QLabel("Записи по категориям")
        count_title.setObjectName("sectionTitle")
        self.counts = QTableWidget(0, 2)
        self.counts.setHorizontalHeaderLabels(["Категория", "Количество"])
        self.counts.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.counts.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.counts.verticalHeader().setVisible(False)
        self.counts.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        counts_card.body.addWidget(count_title)
        counts_card.body.addWidget(self.counts)
        overview_layout.addWidget(counts_card, 1)
        operations = Card()
        operation_title = QLabel("Операции")
        operation_title.setObjectName("sectionTitle")
        operations.body.addWidget(operation_title)
        for text, icon, callback, kind in (
            ("Создать резервную копию", "shield", self.backup, "secondary"),
            ("Восстановить из копии", "refresh", self.restore, "secondary"),
            ("Экспортировать данные", "download", self.export, "secondary"),
            ("Проверить целостность", "shield", self.check_integrity, "secondary"),
            ("Оптимизировать базу", "storage", self.optimize, "secondary"),
            ("Очистить временные файлы", "delete", self.cleanup, "danger"),
            ("Изменить расположение", "folder", self.move_storage, "secondary"),
        ):
            operations.body.addWidget(self.button(text, icon, callback, kind=kind))
        operations.body.addStretch()
        overview_layout.addWidget(operations)
        tabs.addTab(overview, "Обзор и операции")
        backup_tab = QWidget()
        backup_layout = QVBoxLayout(backup_tab)
        backup_layout.setContentsMargins(16, 16, 16, 16)
        options = Card()
        form = QFormLayout()
        self.auto_backup = QCheckBox("Создавать резервные копии автоматически")
        self.interval = QSpinBox()
        self.interval.setRange(1, 720)
        self.interval.setSuffix(" ч")
        self.retention = QSpinBox()
        self.retention.setRange(1, 3650)
        self.retention.setSuffix(" дн")
        form.addRow("Автокопии", self.auto_backup)
        form.addRow("Интервал", self.interval)
        form.addRow("Хранить", self.retention)
        form.addRow(
            "",
            self.button(
                "Сохранить расписание",
                "settings",
                self.save_backup_settings,
                kind="secondary",
            ),
        )
        options.body.addLayout(form)
        self.backup_table = QTableWidget(0, 3)
        self.backup_table.setHorizontalHeaderLabels(["Файл", "Создана", "Размер"])
        self.backup_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.backup_table.verticalHeader().setVisible(False)
        self.backup_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        backup_layout.addWidget(options)
        backup_layout.addWidget(self.backup_table, 1)
        tabs.addTab(backup_tab, "Резервные копии")
        self.layout.addWidget(tabs, 1)

    def refresh(self) -> None:
        self.work(lambda: self.client.request("GET", "/storage", timeout=90), self.render)

    def render(self, value: dict[str, Any]) -> None:
        self.data = value
        self.path.setText(value.get("database_path", "—"))
        integrity = value.get("integrity", {})
        self.integrity.set_status(
            "Целостность: OK" if integrity.get("ok") else "Требуется проверка",
            "success" if integrity.get("ok") else "danger",
            self.theme.palette,
        )
        self.stat_cards["database"].set_value(
            format_bytes(value.get("database_size")),
            f"Схема: v{value.get('schema_version', 0)}",
        )
        self.stat_cards["attachments"].set_value(
            format_bytes(value.get("attachments_size")),
            f"Документов: {value.get('counts', {}).get('documents', 0)}",
        )
        self.stat_cards["backups"].set_value(
            format_bytes(value.get("backups_size")),
            f"Последняя: {local_datetime(value.get('last_backup_at'))}",
        )
        self.stat_cards["free"].set_value(
            format_bytes(value.get("free_space")),
            f"Всего: {format_bytes(value.get('total_space'))}",
        )
        labels = {
            "tasks": "Задачи",
            "memories": "Память",
            "projects": "Проекты",
            "documents": "Документы",
            "conversations": "Диалоги",
            "messages": "Сообщения",
            "inbox": "Входящие",
            "reminders": "Напоминания",
            "execution_log": "События",
        }
        counts = value.get("counts", {})
        self.counts.setRowCount(len(labels))
        for index, (key, label) in enumerate(labels.items()):
            self.counts.setItem(index, 0, QTableWidgetItem(label))
            self.counts.setItem(index, 1, QTableWidgetItem(str(counts.get(key, 0))))
        backups = value.get("backups", [])
        self.backup_table.setRowCount(len(backups))
        for index, backup in enumerate(backups):
            self.backup_table.setItem(index, 0, QTableWidgetItem(backup["name"]))
            self.backup_table.setItem(
                index, 1, QTableWidgetItem(local_datetime(backup.get("created_at")))
            )
            self.backup_table.setItem(
                index, 2, QTableWidgetItem(format_bytes(backup.get("size")))
            )
        self.auto_backup.setChecked(bool(value.get("automatic_backups")))
        self.interval.setValue(int(value.get("backup_interval_hours", 24)))
        self.retention.setValue(int(value.get("backup_retention_days", 30)))

    def _done(self, message: str) -> Callable[[Any], None]:
        def complete(_value: Any) -> None:
            QMessageBox.information(self, "Хранилище", message)
            self.refresh()

        return complete

    def backup(self) -> None:
        self.work(
            lambda: self.client.request("POST", "/storage/backup", timeout=600),
            lambda result: (
                QMessageBox.information(
                    self, "Резервная копия", f"Копия создана:\n{result['path']}"
                ),
                self.refresh(),
            ),
        )

    def restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Резервная копия Second Mind",
            str(Path(self.data.get("data_dir", "")) / "backups"),
            "Second Mind backup (*.zip)",
        )
        if not path:
            return
        answer = QMessageBox.warning(
            self,
            "Восстановление данных",
            "Текущие данные будут заменены. Перед операцией автоматически создаётся страховочная копия. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.work(
            lambda: self.client.request(
                "POST",
                "/storage/restore",
                json={"path": path, "confirmed": True},
                timeout=1800,
            ),
            self._done("Данные восстановлены. Рекомендуется перезапустить Core."),
        )

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт данных",
            str(Path.home() / "second-mind-export.json"),
            "JSON (*.json)",
        )
        if path:
            self.work(
                lambda: self.client.request(
                    "POST", "/storage/export", json={"path": path}, timeout=600
                ),
                lambda result: QMessageBox.information(
                    self, "Экспорт", f"Экспорт сохранён:\n{result['path']}"
                ),
            )

    def check_integrity(self) -> None:
        self.work(
            lambda: self.client.request("POST", "/storage/integrity", timeout=600),
            lambda result: (
                QMessageBox.information(
                    self,
                    "Проверка SQLite",
                    "Целостность подтверждена."
                    if result.get("ok")
                    else f"Обнаружена проблема:\n{result.get('message')}",
                ),
                self.refresh(),
            ),
        )

    def optimize(self) -> None:
        self.work(
            lambda: self.client.request("POST", "/storage/optimize", timeout=1800),
            lambda result: (
                QMessageBox.information(
                    self,
                    "Оптимизация",
                    f"Оптимизация завершена. Освобождено: {format_bytes(result.get('saved'))}",
                ),
                self.refresh(),
            ),
        )

    def cleanup(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Очистка",
                "Удалить временные и незавершённые файлы? Пользовательские документы и резервные копии не затрагиваются.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.work(
            lambda: self.client.request(
                "POST", "/storage/cleanup", json={"confirmed": True}, timeout=600
            ),
            lambda result: (
                QMessageBox.information(
                    self,
                    "Очистка",
                    f"Удалено файлов: {result.get('files', 0)}\nОсвобождено: {format_bytes(result.get('bytes'))}",
                ),
                self.refresh(),
            ),
        )

    def move_storage(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Новое расположение хранилища")
        if not path:
            return
        answer = QMessageBox.warning(
            self,
            "Перенос хранилища",
            "База, вложения и резервные копии будут безопасно перенесены. Не закрывайте приложение до завершения. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.work(
            lambda: self.client.request(
                "POST",
                "/storage/move",
                json={"path": path, "confirmed": True},
                timeout=3600,
            ),
            self._done("Хранилище перенесено. Новое расположение уже используется Core."),
        )

    def open_folder(self) -> None:
        path = self.data.get("data_dir")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def save_backup_settings(self) -> None:
        payload = {
            "automatic_backups": self.auto_backup.isChecked(),
            "backup_interval_hours": self.interval.value(),
            "backup_retention_days": self.retention.value(),
        }
        self.work(
            lambda: self.client.request("PATCH", "/settings", json=payload),
            lambda _value: (
                QMessageBox.information(
                    self, "Резервные копии", "Расписание сохранено."
                ),
                self.refresh(),
            ),
        )


class SettingsPage(BasePage):
    def __init__(
        self, client: ApiClient, theme: ThemeManager, controller: ServiceController
    ) -> None:
        super().__init__(
            client,
            theme,
            "Настройки",
            "Подключения, локальные модели, темы и управление Core.",
        )
        self.controller = controller
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs, 1)
        self._build_general()
        self._build_ai()
        self._build_telegram()
        self._build_speech()
        self._build_diagnostics()

    def _tab(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        return page, layout

    def _build_general(self) -> None:
        page, layout = self._tab()
        card = Card()
        form = QFormLayout()
        self.theme_mode = QComboBox()
        self.theme_mode.addItem("Системная", "system")
        self.theme_mode.addItem("Светлая", "light")
        self.theme_mode.addItem("Тёмная", "dark")
        self.autostart = QCheckBox("Запускать Core при входе в Windows")
        form.addRow("Тема", self.theme_mode)
        form.addRow("Автозапуск", self.autostart)
        card.body.addLayout(form)
        card.body.addWidget(
            self.button("Сохранить", "settings", self.save, kind="secondary"),
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        core = Card()
        title = QLabel("Управление Core")
        title.setObjectName("sectionTitle")
        self.core_status = QLabel("Проверка состояния…")
        self.core_status.setProperty("role", "muted")
        controls = QHBoxLayout()
        controls.addWidget(
            self.button(
                "Запустить",
                "plus",
                lambda: self.core_action("start"),
                kind="secondary",
            )
        )
        controls.addWidget(
            self.button(
                "Остановить",
                "delete",
                lambda: self.core_action("stop"),
                kind="secondary",
            )
        )
        controls.addWidget(
            self.button(
                "Перезапустить",
                "refresh",
                lambda: self.core_action("restart"),
                kind="secondary",
            )
        )
        controls.addStretch()
        core.body.addWidget(title)
        core.body.addWidget(self.core_status)
        core.body.addLayout(controls)
        layout.addWidget(card)
        layout.addWidget(core)
        layout.addStretch()
        self.tabs.addTab(page, "Общие")

    def _build_ai(self) -> None:
        page, layout = self._tab()
        card = Card()
        form = QFormLayout()
        self.llm_url = QLineEdit()
        self.models = QComboBox()
        self.models.setEditable(True)
        self.models.setPlaceholderText("Автоматический выбор")
        form.addRow("LM Studio API", self.llm_url)
        form.addRow("Модель", self.models)
        card.body.addLayout(form)
        controls = QHBoxLayout()
        controls.addWidget(
            self.button("Получить модели", "refresh", self.load_models, kind="secondary")
        )
        controls.addWidget(
            self.button("Сохранить", "settings", self.save, kind="secondary")
        )
        controls.addStretch()
        self.model_status = QLabel("")
        self.model_status.setProperty("role", "muted")
        card.body.addLayout(controls)
        card.body.addWidget(self.model_status)
        layout.addWidget(card)
        layout.addStretch()
        self.tabs.addTab(page, "Модели ИИ")

    def _build_telegram(self) -> None:
        page, layout = self._tab()
        card = Card()
        form = QFormLayout()
        self.telegram_token = QLineEdit()
        self.telegram_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.allowed_ids = QLineEdit()
        self.allowed_ids.setPlaceholderText("123456789, 987654321")
        self.telegram_enabled = QCheckBox("Запускать Telegram вместе с Core")
        form.addRow("Bot token", self.telegram_token)
        form.addRow("Разрешённые User ID", self.allowed_ids)
        form.addRow("Шлюз", self.telegram_enabled)
        card.body.addLayout(form)
        controls = QHBoxLayout()
        controls.addWidget(
            self.button("Сохранить", "settings", self.save, kind="secondary")
        )
        controls.addWidget(
            self.button(
                "Проверить соединение", "shield", self.test_telegram, kind="secondary"
            )
        )
        controls.addStretch()
        self.telegram_test_status = QLabel(
            "Токен хранится в Windows Credential Manager и не отображается повторно."
        )
        self.telegram_test_status.setProperty("role", "muted")
        card.body.addLayout(controls)
        card.body.addWidget(self.telegram_test_status)
        layout.addWidget(card)
        layout.addStretch()
        self.tabs.addTab(page, "Telegram")

    def _build_speech(self) -> None:
        page, layout = self._tab()
        card = Card()
        form = QFormLayout()
        self.whisper_model = QComboBox()
        self.whisper_model.addItems(
            ["tiny", "base", "small", "medium", "large-v3", "turbo"]
        )
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["cpu", "cuda", "auto"])
        self.compute_type = QComboBox()
        self.compute_type.addItems(["int8", "float16", "int8_float16", "float32"])
        self.language = QComboBox()
        self.language.addItem("Автоопределение", "")
        self.language.addItem("Русский", "ru")
        self.language.addItem("English", "en")
        form.addRow("Модель", self.whisper_model)
        form.addRow("Устройство", self.whisper_device)
        form.addRow("Точность", self.compute_type)
        form.addRow("Язык", self.language)
        card.body.addLayout(form)
        self.speech_progress = QProgressBar()
        self.speech_progress.setRange(0, 100)
        self.speech_progress.setValue(0)
        self.speech_status = QLabel("Модель не загружена")
        self.speech_status.setProperty("role", "muted")
        controls = QHBoxLayout()
        controls.addWidget(
            self.button("Загрузить", "download", self.load_speech, kind="secondary")
        )
        controls.addWidget(
            self.button("Выгрузить", "delete", self.unload_speech, kind="secondary")
        )
        controls.addWidget(
            self.button("Тест аудио", "assistant", self.test_speech, kind="secondary")
        )
        controls.addStretch()
        card.body.addWidget(self.speech_progress)
        card.body.addWidget(self.speech_status)
        card.body.addLayout(controls)
        layout.addWidget(card)
        layout.addStretch()
        self.tabs.addTab(page, "Whisper")

    def _build_diagnostics(self) -> None:
        page, layout = self._tab()
        card = Card()
        title = QLabel("Расширенная диагностика")
        title.setObjectName("sectionTitle")
        description = QLabel(
            "Технические данные и сырой JSON доступны только здесь. Пользовательские экраны используют понятные статусы."
        )
        description.setProperty("role", "muted")
        description.setWordWrap(True)
        self.diagnostic_summary = QLabel("—")
        self.diagnostic_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        card.body.addWidget(title)
        card.body.addWidget(description)
        card.body.addWidget(self.diagnostic_summary)
        card.body.addWidget(
            self.button(
                "Показать полный JSON",
                "logs",
                self.show_raw_diagnostics,
                kind="secondary",
            ),
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        layout.addWidget(card)
        layout.addStretch()
        self.tabs.addTab(page, "Диагностика")
        self.raw_diagnostics: dict[str, Any] = {}

    def refresh(self) -> None:
        self.core_status.setText(
            "Core работает" if self.controller.running() else "Core остановлен"
        )
        if not self.controller.running():
            return
        self.work(lambda: self.client.request("GET", "/settings"), self.render)
        self.work(lambda: self.client.request("GET", "/health"), self.render_diagnostics)

    def render(self, value: dict[str, Any]) -> None:
        self.theme_mode.setCurrentIndex(
            max(0, self.theme_mode.findData(self.theme.mode))
        )
        self.autostart.setChecked(bool(value.get("autostart_enabled")))
        self.llm_url.setText(value.get("llm_base_url", ""))
        self.models.setCurrentText(value.get("llm_model") or "")
        self.allowed_ids.setText(
            ", ".join(str(item) for item in value.get("telegram_allowed_user_ids", []))
        )
        self.telegram_enabled.setChecked(bool(value.get("telegram_enabled")))
        self.telegram_token.setPlaceholderText(
            "Настроен" if value.get("telegram_token_configured") else "Не настроен"
        )
        self.whisper_model.setCurrentText(value.get("whisper_model", "small"))
        self.whisper_device.setCurrentText(value.get("whisper_device", "cpu"))
        self.compute_type.setCurrentText(value.get("whisper_compute_type", "int8"))
        self.language.setCurrentIndex(
            max(0, self.language.findData(value.get("whisper_language") or ""))
        )

    def save(self) -> None:
        try:
            ids = [
                int(value.strip())
                for value in self.allowed_ids.text().split(",")
                if value.strip()
            ]
        except ValueError:
            self.show_error("Telegram User ID должен состоять из цифр.")
            return
        payload: dict[str, Any] = {
            "llm_base_url": self.llm_url.text().strip(),
            "llm_model": self.models.currentText().strip() or None,
            "telegram_allowed_user_ids": ids,
            "telegram_enabled": self.telegram_enabled.isChecked(),
            "whisper_model": self.whisper_model.currentText(),
            "whisper_device": self.whisper_device.currentText(),
            "whisper_compute_type": self.compute_type.currentText(),
            "whisper_language": self.language.currentData() or None,
            "autostart_enabled": self.autostart.isChecked(),
        }
        if self.telegram_token.text():
            payload["telegram_token"] = self.telegram_token.text()
        self.theme.set_mode(str(self.theme_mode.currentData()))
        self.work(
            lambda: self.client.request("PATCH", "/settings", json=payload),
            lambda result: (
                self.telegram_token.clear(),
                self.render(result),
                QMessageBox.information(self, "Настройки", "Настройки сохранены."),
            ),
        )

    def load_models(self) -> None:
        self.model_status.setText("Подключение к LM Studio Runtime…")

        def render_models(rows: list[dict[str, Any]]) -> None:
            selected = self.models.currentText()
            self.models.clear()
            self.models.addItem("")
            for row in rows:
                key = str(row.get("key") or row.get("id") or row.get("display_name"))
                self.models.addItem(str(row.get("display_name") or key), key)
            index = self.models.findData(selected)
            self.models.setCurrentIndex(index if index >= 0 else 0)
            self.model_status.setText(
                f"Runtime доступен. Найдено моделей: {len(rows)}"
            )

        self.work(
            lambda: self.client.request("GET", "/models", timeout=30),
            render_models,
            lambda message: self.model_status.setText(f"Runtime недоступен: {message}"),
        )

    def test_telegram(self) -> None:
        token = self.telegram_token.text().strip()

        def operation() -> dict[str, Any]:
            if token:
                self.client.request("PATCH", "/settings", json={"telegram_token": token})
            return self.client.request("POST", "/telegram/test", timeout=30)

        self.telegram_test_status.setText("Проверка соединения…")
        self.work(
            operation,
            lambda result: self.telegram_test_status.setText(
                f"Соединение установлено: @{result.get('username') or 'бот'}"
            ),
            lambda message: self.telegram_test_status.setText(f"Ошибка: {message}"),
        )

    def speech_payload(self) -> dict[str, Any]:
        return {
            "whisper_model": self.whisper_model.currentText(),
            "whisper_device": self.whisper_device.currentText(),
            "whisper_compute_type": self.compute_type.currentText(),
            "whisper_language": self.language.currentData() or None,
        }

    def load_speech(self) -> None:
        payload = self.speech_payload()
        self.speech_progress.setRange(0, 0)
        self.speech_status.setText(
            "Загрузка модели. Первый запуск может занять несколько минут…"
        )

        def operation() -> dict[str, Any]:
            self.client.request("PATCH", "/settings", json=payload)
            return self.client.request("POST", "/speech/load", timeout=1800)

        self.work(operation, self.speech_ready, self.speech_failed)

    def speech_ready(self, result: dict[str, Any]) -> None:
        self.speech_progress.setRange(0, 100)
        self.speech_progress.setValue(100 if result.get("loaded") else 0)
        self.speech_status.setText(
            f"Модель {result.get('model')} загружена на {result.get('device')}."
        )

    def speech_failed(self, message: str) -> None:
        self.speech_progress.setRange(0, 100)
        self.speech_progress.setValue(0)
        self.speech_status.setText(f"Ошибка загрузки: {message}")

    def unload_speech(self) -> None:
        self.work(
            lambda: self.client.request("POST", "/speech/unload"),
            lambda result: (
                self.speech_ready(result),
                self.speech_status.setText("Модель выгружена из памяти."),
            ),
        )

    def test_speech(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Аудиофайл", "", "Аудио (*.wav *.mp3 *.ogg *.m4a *.flac)"
        )
        if not path:
            return
        audio = Path(path)
        self.speech_progress.setRange(0, 0)
        self.work(
            lambda: self.client.request(
                "POST",
                "/speech/transcribe",
                files={"file": (audio.name, audio.read_bytes())},
                timeout=1800,
            ),
            lambda result: (
                self.speech_progress.setRange(0, 100),
                self.speech_progress.setValue(100),
                QMessageBox.information(
                    self,
                    "Распознавание",
                    f"Язык: {result['language']}\n\n{result['text']}",
                ),
            ),
            self.speech_failed,
        )

    def render_diagnostics(self, value: dict[str, Any]) -> None:
        self.raw_diagnostics = value
        system = value.get("system", {})
        self.diagnostic_summary.setText(
            f"Core {value.get('core', {}).get('version', '—')} • PID {system.get('pid', '—')}\n"
            f"{system.get('platform', '—')}\nPython {system.get('python', '—')}"
        )

    def show_raw_diagnostics(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Расширенная диагностика")
        dialog.resize(780, 600)
        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(json.dumps(self.raw_diagnostics, ensure_ascii=False, indent=2))
        layout.addWidget(viewer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def core_action(self, action: str) -> None:
        self.core_status.setText(
            {
                "start": "Запуск Core…",
                "stop": "Остановка Core…",
                "restart": "Перезапуск Core…",
            }[action]
        )
        operation = {
            "start": self.controller.start,
            "stop": self.controller.stop,
            "restart": self.controller.restart,
        }[action]
        self.work(
            lambda: (operation(), self.controller.running())[1],
            lambda running: self.core_status.setText(
                "Core работает" if running else "Core остановлен"
            ),
            lambda message: self.core_status.setText(f"Ошибка: {message}"),
        )


class UpdatesPage(BasePage):
    def __init__(self, client: ApiClient, theme: ThemeManager) -> None:
        super().__init__(
            client,
            theme,
            "Обновления",
            "Подписанные релизы, безопасная установка и автоматический откат.",
        )
        self.release: dict[str, Any] | None = None
        self.download: dict[str, Any] | None = None
        self._manual_check = False
        versions = QGridLayout()
        versions.setSpacing(12)
        self.current = MetricCard("Установленная версия", "updates")
        self.available = MetricCard("Доступная версия", "download")
        self.trust = MetricCard("Цепочка доверия", "shield")
        versions.addWidget(self.current, 0, 0)
        versions.addWidget(self.available, 0, 1)
        versions.addWidget(self.trust, 0, 2)
        self.layout.addLayout(versions)
        options = Card()
        form = QFormLayout()
        self.repository = QLineEdit()
        self.automatic = QCheckBox("Проверять обновления автоматически")
        self.update_interval = QSpinBox()
        self.update_interval.setRange(1, 168)
        self.update_interval.setSuffix(" ч")
        form.addRow("GitHub Releases", self.repository)
        form.addRow("Автопроверка", self.automatic)
        form.addRow("Интервал", self.update_interval)
        options.body.addLayout(form)
        buttons = QHBoxLayout()
        self.check_button = self.button(
            "Проверить", "refresh", lambda: self.check_updates(True), kind="secondary"
        )
        self.download_button = self.button(
            "Скачать", "download", self.start_download, kind="secondary"
        )
        self.cancel_button = self.button(
            "Отменить", "delete", self.cancel_download, kind="ghost"
        )
        self.install_button = QPushButton("Установить и перезапустить")
        self.install_button.setIcon(svg_icon("updates", "#ffffff"))
        self.install_button.clicked.connect(self.install_update)
        buttons.addWidget(self.check_button)
        buttons.addWidget(
            self.button(
                "Сохранить настройки",
                "settings",
                self.save_settings,
                kind="secondary",
            )
        )
        buttons.addWidget(self.download_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.install_button)
        options.body.addLayout(buttons)
        self.status_label = QLabel("Проверка ещё не выполнялась.")
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        self.progress = QProgressBar()
        options.body.addWidget(self.status_label)
        options.body.addWidget(self.progress)
        self.layout.addWidget(options)
        changelog_card = Card()
        title = QLabel("Описание релиза")
        title.setObjectName("sectionTitle")
        self.changelog = QTextBrowser()
        self.changelog.setOpenExternalLinks(False)
        self.changelog.setPlaceholderText("CHANGELOG появится после проверки обновлений.")
        changelog_card.body.addWidget(title)
        changelog_card.body.addWidget(self.changelog)
        self.layout.addWidget(changelog_card, 1)
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(700)
        self.poll_timer.timeout.connect(self.poll_download)
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(lambda: self.check_updates(False))

    def refresh(self) -> None:
        self.current.set_value(__version__, "Текущая сборка")
        self.work(lambda: self.client.request("GET", "/settings"), self.render_settings)
        self.work(
            lambda: self.client.request("GET", "/updates/status"), self.render_status
        )

    def render_settings(self, value: dict[str, Any]) -> None:
        self.repository.setText(value.get("update_repository") or "")
        self.automatic.setChecked(bool(value.get("automatic_update_checks", True)))
        hours = int(value.get("update_check_interval_hours", 24))
        self.update_interval.setValue(hours)
        self.auto_timer.stop()
        if self.automatic.isChecked():
            self.auto_timer.start(hours * 60 * 60 * 1000)

    def save_settings(self, notify: bool = True) -> None:
        payload = {
            "update_repository": self.repository.text().strip() or None,
            "automatic_update_checks": self.automatic.isChecked(),
            "update_check_interval_hours": self.update_interval.value(),
        }
        self.work(
            lambda: self.client.request("PATCH", "/settings", json=payload),
            lambda value: (
                self.render_settings(value),
                QMessageBox.information(self, "Обновления", "Настройки сохранены.")
                if notify
                else None,
            ),
        )

    def start_automatic_checks(self) -> None:
        def configured(value: dict[str, Any]) -> None:
            self.render_settings(value)
            if self.automatic.isChecked():
                self.check_updates(False)

        self.work(lambda: self.client.request("GET", "/settings"), configured)

    def check_updates(self, manual: bool) -> None:
        payload = {
            "update_repository": self.repository.text().strip(),
            "automatic_update_checks": self.automatic.isChecked(),
            "update_check_interval_hours": self.update_interval.value(),
        }
        if not payload["update_repository"]:
            if manual:
                self.show_error("Укажите GitHub-репозиторий обновлений.")
            return
        self._manual_check = manual
        self.check_button.setEnabled(False)
        self.status_label.setText("Проверяю GitHub Releases и подпись манифеста…")

        def operation() -> dict[str, Any]:
            self.client.request("PATCH", "/settings", json=payload)
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
        self.current.set_value(result["current_version"], "Установлена")
        self.available.set_value(
            result["latest_version"], "Доступна" if result["available"] else "Актуальная"
        )
        self.trust.set_value("Проверено", "Ed25519 + SHA-256")
        self.changelog.setMarkdown(
            result.get("changelog") or "Описание изменений отсутствует."
        )
        self.download_button.setEnabled(bool(result["available"]))
        self.status_label.setText(
            f"Доступна версия {result['latest_version']}."
            if result["available"]
            else f"Установлена актуальная версия {result['current_version']}."
        )
        if self._manual_check and not result["available"]:
            QMessageBox.information(self, "Обновления", self.status_label.text())

    def start_download(self) -> None:
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText("Начинаю защищённое скачивание…")
        self.work(
            lambda: self.client.request("POST", "/updates/download", timeout=30),
            lambda _value: self.poll_timer.start(),
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
        elif phase == "ready":
            self.poll_timer.stop()
            self.download = status.get("download")
            self.cancel_button.setEnabled(False)
            self.install_button.setEnabled(bool(self.download))
            self.status_label.setText(
                "Скачивание завершено. SHA-256 и подпись проверены."
            )
        elif phase in {"failed", "cancelled"}:
            self.poll_timer.stop()
            self.cancel_button.setEnabled(False)
            self.download_button.setEnabled(
                bool(self.release and self.release.get("available"))
            )
            self.status_label.setText(status.get("error") or "Скачивание отменено.")

    def download_failed(self, message: str) -> None:
        self.poll_timer.stop()
        self.cancel_button.setEnabled(False)
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
            self.show_error(
                "Установка обновлений доступна в установленной Windows-версии."
            )
            return
        if (
            QMessageBox.question(
                self,
                "Установка обновления",
                "Second Mind закроется, Core будет остановлен, а данные сохранены в резервную копию. Продолжить?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        download_path = self.download["path"]
        self.work(
            lambda: self.client.request(
                "POST",
                "/updates/install",
                params={"path": download_path},
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
        self.client = client
        self.setWindowTitle("Настройка Second Mind")
        self.setWindowIcon(application_icon())
        self.setMinimumSize(720, 520)
        intro = QWizardPage()
        intro.setTitle("Добро пожаловать в Second Mind")
        intro.setSubTitle(
            "Локальный ассистент хранит пользовательские данные на этом компьютере."
        )
        layout = QVBoxLayout(intro)
        layout.addWidget(QLabel(f"Хранилище:\n{current['data_dir']}"))
        self.addPage(intro)
        runtime = QWizardPage()
        runtime.setTitle("LM Studio Runtime")
        runtime.setSubTitle("Подключение выполняется только к локальному loopback API.")
        form = QFormLayout(runtime)
        self.llm_url = QLineEdit(current["llm_base_url"])
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem(current.get("llm_model") or "")
        form.addRow("Локальный API", self.llm_url)
        form.addRow("Модель", self.model)
        self.addPage(runtime)
        telegram = QWizardPage()
        telegram.setTitle("Telegram")
        telegram.setSubTitle("Токен сохраняется в Windows Credential Manager.")
        form = QFormLayout(telegram)
        self.telegram_token = QLineEdit()
        self.telegram_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.allowed_ids = QLineEdit()
        self.enable_telegram = QCheckBox("Включить long polling")
        form.addRow("Bot token", self.telegram_token)
        form.addRow("Разрешённые User ID", self.allowed_ids)
        form.addRow("", self.enable_telegram)
        self.addPage(telegram)
        speech = QWizardPage()
        speech.setTitle("Распознавание речи")
        form = QFormLayout(speech)
        self.whisper = QComboBox()
        self.whisper.addItems(
            ["tiny", "base", "small", "medium", "large-v3", "turbo"]
        )
        self.whisper.setCurrentText(current["whisper_model"])
        self.device = QComboBox()
        self.device.addItems(["cpu", "cuda", "auto"])
        form.addRow("Whisper", self.whisper)
        form.addRow("Устройство", self.device)
        self.addPage(speech)
        ready = QWizardPage()
        ready.setTitle("Готово")
        ready.setSubTitle(
            "Настройки можно изменить в любой момент. Core продолжает работу после закрытия окна."
        )
        layout = QVBoxLayout(ready)
        layout.addWidget(
            QLabel(
                "После завершения откроется обзор с фактическими статусами компонентов."
            )
        )
        self.addPage(ready)

    def accept(self) -> None:
        try:
            ids = [
                int(value.strip())
                for value in self.allowed_ids.text().split(",")
                if value.strip()
            ]
            payload: dict[str, Any] = {
                "llm_base_url": self.llm_url.text().strip(),
                "llm_model": self.model.currentText().strip() or None,
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


NAVIGATION = (
    (
        "РАБОЧЕЕ ПРОСТРАНСТВО",
        (
            ("overview", "Обзор", "overview", False),
            ("assistant", "Ассистент", "assistant", False),
            ("inbox", "Входящие", "inbox", False),
            ("tasks", "Задачи", "tasks", False),
            ("memory", "Память", "memory", False),
            ("projects", "Проекты", "projects", False),
            ("calendar", "Календарь", "calendar", False),
            ("documents", "Документы", "documents", False),
        ),
    ),
    (
        "ИНТЕЛЛЕКТУАЛЬНОЕ ЯДРО",
        (
            ("models", "Модели ИИ", "models", False),
            ("agents", "Агенты", "agents", False),
            ("skills", "Skills", "skills", True),
            ("tools", "MCP и инструменты", "tools", False),
        ),
    ),
    (
        "СИСТЕМА",
        (
            ("telegram", "Telegram", "telegram", False),
            ("integrations", "Интеграции", "integrations", True),
            ("storage", "Хранилище", "storage", False),
            ("logs", "Журнал событий", "logs", False),
            ("updates", "Обновления", "updates", False),
            ("settings", "Настройки", "settings", False),
        ),
    ),
)


class NavigationSidebar(QWidget):
    page_requested = Signal(str)

    def __init__(self, theme: ThemeManager) -> None:
        super().__init__()
        self.theme = theme
        self.setObjectName("sidebar")
        self.setFixedWidth(252)
        self.expanded = True
        self.buttons: dict[str, QToolButton] = {}
        self.group_labels: list[QLabel] = []
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(3)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(application_icon().pixmap(34, 34))
        self.brand_text = QWidget()
        names = QVBoxLayout(self.brand_text)
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        name = QLabel("Second Mind")
        name.setObjectName("brandName")
        version = QLabel(f"v{__version__}")
        version.setObjectName("brandVersion")
        names.addWidget(name)
        names.addWidget(version)
        self.collapse = QToolButton()
        self.collapse.setProperty("kind", "ghost")
        self.collapse.setIcon(svg_icon("collapse", theme.palette.muted))
        self.collapse.setFixedSize(32, 32)
        self.collapse.clicked.connect(self.toggle)
        brand.addWidget(logo)
        brand.addWidget(self.brand_text, 1)
        brand.addWidget(self.collapse)
        layout.addLayout(brand)
        layout.addSpacing(12)
        for group_name, entries in NAVIGATION:
            label = QLabel(group_name)
            label.setObjectName("eyebrow")
            label.setContentsMargins(9, 11, 0, 4)
            self.group_labels.append(label)
            layout.addWidget(label)
            for key, title, icon, planned in entries:
                button = QToolButton()
                button.setObjectName("navButton")
                button.setCheckable(True)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                button.setIcon(svg_icon(icon, theme.palette.muted))
                button.setIconSize(QSize(19, 19))
                button.setText(title + ("  ·  скоро" if planned else ""))
                button.setToolTip(title)
                button.clicked.connect(
                    lambda _checked=False, value=key: self.page_requested.emit(value)
                )
                self.buttons[key] = button
                self.button_group.addButton(button)
                layout.addWidget(button)
        layout.addStretch()
        status = QFrame()
        status.setObjectName("subtleCard")
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(10, 8, 10, 8)
        self.core_dot = QLabel("●")
        self.core_dot.setStyleSheet(f"color:{theme.palette.warning};")
        self.core_text = QLabel("Core: проверка")
        self.core_text.setProperty("role", "muted")
        status_layout.addWidget(self.core_dot)
        status_layout.addWidget(self.core_text, 1)
        layout.addWidget(status)

    def select(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def set_core(self, online: bool) -> None:
        color = self.theme.palette.success if online else self.theme.palette.danger
        self.core_dot.setStyleSheet(f"color:{color};")
        self.core_text.setText("Core: работает" if online else "Core: недоступен")

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self.setFixedWidth(252 if self.expanded else 68)
        self.brand_text.setVisible(self.expanded)
        for label in self.group_labels:
            label.setVisible(self.expanded)
        for button in self.buttons.values():
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                if self.expanded
                else Qt.ToolButtonStyle.ToolButtonIconOnly
            )
        self.collapse.setIcon(
            svg_icon(
                "collapse" if self.expanded else "expand", self.theme.palette.muted
            )
        )


class MainWindow(QMainWindow):
    def __init__(
        self, client: ApiClient, theme: ThemeManager, controller: ServiceController
    ) -> None:
        super().__init__()
        self.client = client
        self.theme = theme
        self.controller = controller
        self.setWindowTitle("Second Mind")
        self.setWindowIcon(application_icon())
        self.resize(1500, 920)
        self.setMinimumSize(1100, 700)
        root = QWidget()
        root.setObjectName("appRoot")
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = NavigationSidebar(theme)
        self.stack = QStackedWidget()
        body.addWidget(self.sidebar)
        body.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.dashboard = DashboardPage(client, theme)
        self.updates_page = UpdatesPage(client, theme)
        self.pages = self._create_pages()
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.sidebar.page_requested.connect(self.open_page)
        self.dashboard.health_changed.connect(
            lambda value: self.sidebar.set_core(bool(value.get("online")))
        )
        self._force_quit = False
        self._create_tray()
        self.open_page("overview")
        QTimer.singleShot(2500, self.updates_page.start_automatic_checks)

    def _create_pages(self) -> dict[str, BasePage]:
        theme, client = self.theme, self.client
        return {
            "overview": self.dashboard,
            "assistant": ChatPage(client, theme),
            "inbox": InboxPage(client, theme),
            "tasks": TasksPage(client, theme),
            "memory": MemoryPage(client, theme),
            "projects": ProjectsPage(client, theme),
            "calendar": RemindersPage(client, theme),
            "documents": DocumentsPage(client, theme),
            "models": ReadOnlyPage(
                client,
                theme,
                "Модели ИИ",
                "Реальный список моделей из LM Studio Runtime.",
                "/models",
                [
                    ("display_name", "Модель", None),
                    ("type", "Тип", None),
                    ("params_string", "Параметры", None),
                    (
                        "loaded_instances",
                        "Состояние",
                        lambda value: "Загружена" if value else "Доступна",
                    ),
                ],
                "models",
                "Модели не найдены",
                "Запустите LM Studio Runtime и загрузите совместимую модель.",
            ),
            "agents": ReadOnlyPage(
                client,
                theme,
                "Агенты",
                "Текущие встроенные роли без расширения архитектуры в этом релизе.",
                "/agents",
                [
                    ("name", "Агент", None),
                    ("description", "Назначение", None),
                    (
                        "enabled",
                        "Состояние",
                        lambda value: "Включён" if value else "Отключён",
                    ),
                    ("tools", "Инструменты", lambda value: str(len(value or []))),
                ],
                "agents",
                "Агенты не настроены",
                "Встроенные роли появятся после запуска Core.",
            ),
            "skills": ComingSoonPage(
                client,
                theme,
                "Skills",
                "Библиотека Agent Skills вынесена в отдельный этап разработки.",
                "v0.4",
            ),
            "tools": ReadOnlyPage(
                client,
                theme,
                "MCP и инструменты",
                "Доступные локальные инструменты и уровень полномочий.",
                "/tools",
                [
                    ("name", "Инструмент", None),
                    ("description", "Описание", None),
                    ("permission", "Доступ", lambda value: str(value)),
                ],
                "tools",
                "Инструменты не найдены",
                "Проверьте состояние Core и MCP.",
            ),
            "telegram": TelegramPage(client, theme),
            "integrations": ComingSoonPage(
                client,
                theme,
                "Интеграции",
                "Внешние интеграции требуют отдельной модели согласий и подключений.",
                "v0.4",
            ),
            "storage": StoragePage(client, theme),
            "logs": ReadOnlyPage(
                client,
                theme,
                "Журнал событий",
                "Действия Core, длительность и ошибки в человекочитаемом виде.",
                "/logs",
                [
                    ("created_at", "Время", local_datetime),
                    ("agent", "Компонент", None),
                    ("action", "Действие", lambda value: str(value).replace("_", " ")),
                    ("status", "Статус", human_status),
                    (
                        "duration_ms",
                        "Длительность",
                        lambda value: f"{value} мс" if value is not None else "—",
                    ),
                ],
                "logs",
                "Событий пока нет",
                "Действия Core появятся здесь автоматически.",
            ),
            "updates": self.updates_page,
            "settings": SettingsPage(client, theme, self.controller),
        }

    def _create_tray(self) -> None:
        self.tray = QSystemTrayIcon(application_icon(), self)
        menu = QMenu()
        show = QAction("Открыть Second Mind", self)
        show.triggered.connect(self.show_normal)
        quit_action = QAction("Закрыть интерфейс", self)
        quit_action.triggered.connect(self.quit_gui)
        menu.addAction(show)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_normal()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

    def open_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page:
            self.sidebar.select(key)
            self.stack.setCurrentWidget(page)
            page.refresh()

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
            self.tray.showMessage(
                "Second Mind",
                "Интерфейс свёрнут. Фоновый Core продолжает работу.",
            )


def main() -> None:
    if sys.platform == "win32":
        with suppress(OSError):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
                "SecondMind.SecondMind.0.3"
            )
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Second Mind")
    app.setApplicationDisplayName("Second Mind")
    app.setOrganizationName("SecondMind")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    theme = ThemeManager(app)
    if "--theme" in sys.argv:
        index = sys.argv.index("--theme")
        if index + 1 < len(sys.argv):
            theme.mode = sys.argv[index + 1]
    theme.apply()
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
    window = MainWindow(client, theme, controller)
    window.show()
    if "--page" in sys.argv:
        index = sys.argv.index("--page")
        if index + 1 < len(sys.argv):
            window.open_page(sys.argv[index + 1])
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
                "Установка завершилась ошибкой. Состояние и журнал сохранены в каталоге updates.",
            ),
        )
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
