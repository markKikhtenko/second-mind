from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class Palette:
    background: str
    sidebar: str
    surface: str
    elevated: str
    border: str
    border_strong: str
    text: str
    muted: str
    subtle: str
    accent: str
    accent_hover: str
    accent_soft: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    code: str


DARK = Palette(
    background="#0b0d12",
    sidebar="#0e1016",
    surface="#12151c",
    elevated="#181c25",
    border="#252a36",
    border_strong="#343b4b",
    text="#f2f3f7",
    muted="#9aa2b4",
    subtle="#697184",
    accent="#8367ff",
    accent_hover="#947cff",
    accent_soft="#251f48",
    success="#52d29a",
    success_soft="#15372d",
    warning="#f2bd58",
    warning_soft="#3b2d16",
    danger="#ff7185",
    danger_soft="#401d26",
    code="#0a0c10",
)

LIGHT = Palette(
    background="#f5f6f9",
    sidebar="#fafbfc",
    surface="#ffffff",
    elevated="#f9fafc",
    border="#e1e4eb",
    border_strong="#cfd4df",
    text="#1b1d26",
    muted="#626b7d",
    subtle="#8b93a3",
    accent="#6e52e5",
    accent_hover="#5d42d2",
    accent_soft="#eeeaff",
    success="#168f62",
    success_soft="#e3f6ee",
    warning="#a76506",
    warning_soft="#fff2d7",
    danger="#cf4058",
    danger_soft="#ffeaee",
    code="#f0f2f6",
)


ICON_PATHS: dict[str, str] = {
    "overview": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
    "assistant": '<path d="M21 12a8 8 0 0 1-8 8H7l-4 2 1.4-4.2A8.5 8.5 0 1 1 21 12Z"/><path d="M8 12h.01M12 12h.01M16 12h.01"/>',
    "inbox": '<path d="M4 4h16v15H4z"/><path d="M4 14h4l2 3h4l2-3h4"/>',
    "tasks": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m8 9 1.5 1.5L12 8M8 15l1.5 1.5L12 14M14 9h3M14 15h3"/>',
    "memory": '<path d="M9 4a3 3 0 0 0-5 2.2A3.5 3.5 0 0 0 4.5 13 3.5 3.5 0 0 0 9 18V4ZM15 4a3 3 0 0 1 5 2.2 3.5 3.5 0 0 1-.5 6.8A3.5 3.5 0 0 1 15 18V4ZM9 8h2M13 8h2M9 14h2M13 14h2"/>',
    "projects": '<path d="M3 7h7l2 2h9v11H3z"/><path d="M3 7V5h7l2 2"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 17h.01M12 17h.01"/>',
    "documents": '<path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h6"/>',
    "models": '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4"/>',
    "agents": '<circle cx="12" cy="8" r="4"/><path d="M4 22a8 8 0 0 1 16 0M19 3l1 1M5 3 4 4"/>',
    "skills": '<path d="m12 2 2.1 6.1L20 10l-5.9 1.9L12 18l-2.1-6.1L4 10l5.9-1.9L12 2Z"/><path d="m19 17 .8 2.2L22 20l-2.2.8L19 23l-.8-2.2L16 20l2.2-.8L19 17Z"/>',
    "tools": '<path d="M14.7 6.3a4 4 0 0 0-5-5L12 3.6 3.6 12 2 16l4-1.6L14.4 6l2.3 2.3a4 4 0 0 0 5-5l-2 2-2-2 2-2"/>',
    "telegram": '<path d="m22 2-8 20-4.5-8-7.5-4 20-8Z"/><path d="m9.5 14 4-4"/>',
    "integrations": '<path d="M8 12h8M12 8v8"/><path d="M7 3h3v5H7a4 4 0 0 1 0-8M17 21h-3v-5h3a4 4 0 0 1 0 8"/>',
    "storage": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/>',
    "logs": '<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "updates": '<path d="M20 12a8 8 0 1 1-2.3-5.7L20 9"/><path d="M20 4v5h-5M12 8v8M9 13l3 3 3-3"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "refresh": '<path d="M20 6v5h-5M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 11M5.5 15A7 7 0 0 0 18 17.5l2-4.5"/>',
    "delete": '<path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/>',
    "edit": '<path d="m4 16-1 5 5-1L19 9l-4-4L4 16ZM13 7l4 4"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
    "attachment": '<path d="m21 11-8.5 8.5a6 6 0 0 1-8.5-8.5L13 2a4 4 0 0 1 5.7 5.7L9.5 17a2 2 0 0 1-2.8-2.8L15 6"/>',
    "send": '<path d="m22 2-8 20-4-8-8-4 20-8Z"/><path d="M10 14 22 2"/>',
    "folder": '<path d="M3 6h7l2 2h9v12H3z"/>',
    "shield": '<path d="M12 2 4 5v6c0 5 3.4 9 8 11 4.6-2 8-6 8-11V5l-8-3Z"/><path d="m9 12 2 2 4-5"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
    "collapse": '<path d="m15 18-6-6 6-6"/>',
    "expand": '<path d="m9 18 6-6-6-6"/>',
}


def svg_icon(name: str, color: str = "#8f96a8") -> QIcon:
    paths = ICON_PATHS.get(name, ICON_PATHS["overview"])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'
    ).encode()
    renderer = QSvgRenderer(QByteArray(svg))
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def stylesheet(p: Palette) -> str:
    return f"""
* {{ font-family: "Segoe UI"; font-size: 13px; }}
QWidget {{ color: {p.text}; background: transparent; }}
QMainWindow, QWidget#appRoot {{ background: {p.background}; }}
QWidget#sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
QLabel#brandName {{ font-size: 15px; font-weight: 700; letter-spacing: .2px; }}
QLabel#brandVersion, QLabel[role="muted"] {{ color: {p.muted}; }}
QLabel#pageTitle {{ font-size: 26px; font-weight: 700; }}
QLabel#pageSubtitle {{ color: {p.muted}; font-size: 13px; }}
QLabel#sectionTitle {{ font-size: 15px; font-weight: 650; }}
QLabel#eyebrow {{ color: {p.subtle}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
QFrame#card, QFrame[card="true"] {{
  background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px;
}}
QFrame#subtleCard {{ background: {p.elevated}; border: 1px solid {p.border}; border-radius: 10px; }}
QFrame#separator {{ background: {p.border}; min-height: 1px; max-height: 1px; border: 0; }}
QPushButton, QToolButton {{
  background: {p.accent}; color: white; border: 1px solid {p.accent};
  border-radius: 8px; min-height: 34px; padding: 0 13px; font-weight: 600;
}}
QPushButton:hover, QToolButton:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton:pressed, QToolButton:pressed {{ padding-top: 1px; }}
QPushButton:disabled, QToolButton:disabled {{ background: {p.elevated}; color: {p.subtle}; border-color: {p.border}; }}
QPushButton[kind="secondary"], QToolButton[kind="secondary"] {{
  background: {p.elevated}; color: {p.text}; border-color: {p.border_strong};
}}
QPushButton[kind="secondary"]:hover, QToolButton[kind="secondary"]:hover {{ background: {p.border}; }}
QPushButton[kind="ghost"], QToolButton[kind="ghost"] {{
  background: transparent; color: {p.muted}; border-color: transparent;
}}
QPushButton[kind="ghost"]:hover, QToolButton[kind="ghost"]:hover {{ background: {p.elevated}; color: {p.text}; }}
QPushButton[kind="danger"] {{ background: {p.danger_soft}; color: {p.danger}; border-color: {p.danger_soft}; }}
QPushButton[kind="danger"]:hover {{ border-color: {p.danger}; }}
QToolButton#navButton {{
  background: transparent; color: {p.muted}; border: 0; border-radius: 8px;
  min-height: 36px; padding: 0 10px; text-align: left; font-weight: 500;
}}
QToolButton#navButton:hover {{ background: {p.elevated}; color: {p.text}; }}
QToolButton#navButton:checked {{ background: {p.accent_soft}; color: {p.text}; font-weight: 650; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateTimeEdit {{
  background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong};
  border-radius: 8px; min-height: 34px; padding: 0 10px; selection-background-color: {p.accent};
}}
QTextEdit, QPlainTextEdit {{ padding: 9px 10px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDateTimeEdit:focus {{ border-color: {p.accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ background: {p.elevated}; color: {p.subtle}; }}
QComboBox::drop-down {{ border: 0; width: 28px; }}
QComboBox QAbstractItemView {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; selection-background-color: {p.accent_soft}; }}
QCheckBox {{ spacing: 8px; color: {p.text}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {p.border_strong}; border-radius: 5px; background: {p.surface}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
QTableWidget, QTableView {{
  background: {p.surface}; alternate-background-color: {p.elevated}; color: {p.text};
  border: 1px solid {p.border}; border-radius: 10px; gridline-color: transparent;
  selection-background-color: {p.accent_soft}; selection-color: {p.text}; outline: 0;
}}
QTableWidget::item, QTableView::item {{ padding: 8px; border-bottom: 1px solid {p.border}; }}
QHeaderView::section {{ background: {p.elevated}; color: {p.muted}; border: 0; border-bottom: 1px solid {p.border}; padding: 9px; font-weight: 650; }}
QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; min-height: 32px; border-radius: 4px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QProgressBar {{ background: {p.elevated}; border: 0; border-radius: 4px; height: 8px; color: transparent; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 10px; background: {p.surface}; top: -1px; }}
QTabBar::tab {{ color: {p.muted}; background: transparent; border: 0; padding: 9px 14px; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
QMenu {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QToolTip {{ background: {p.elevated}; color: {p.text}; border: 1px solid {p.border_strong}; padding: 5px; }}
"""


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, *, padding: int = 16) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(10)


class StatusPill(QLabel):
    def __init__(self, text: str = "Неизвестно", tone: str = "neutral") -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_status(text, tone)

    def set_status(self, text: str, tone: str = "neutral", palette: Palette = DARK) -> None:
        colors = {
            "success": (palette.success, palette.success_soft),
            "warning": (palette.warning, palette.warning_soft),
            "danger": (palette.danger, palette.danger_soft),
            "accent": (palette.accent, palette.accent_soft),
            "neutral": (palette.muted, palette.elevated),
        }
        foreground, background = colors.get(tone, colors["neutral"])
        self.setText(text)
        self.setStyleSheet(
            f"QLabel {{ color:{foreground}; background:{background}; border-radius:9px; "
            "padding:3px 8px; font-size:11px; font-weight:650; }}"
        )


class MetricCard(Card):
    def __init__(self, title: str, icon: str) -> None:
        super().__init__(padding=14)
        top = QHBoxLayout()
        label = QLabel(title)
        label.setProperty("role", "muted")
        symbol = QLabel()
        symbol.setPixmap(svg_icon(icon, DARK.accent).pixmap(18, 18))
        top.addWidget(label)
        top.addStretch()
        top.addWidget(symbol)
        self.value = QLabel("—")
        self.value.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.detail = QLabel("")
        self.detail.setProperty("role", "muted")
        self.detail.setWordWrap(True)
        self.body.addLayout(top)
        self.body.addWidget(self.value)
        self.body.addWidget(self.detail)

    def set_value(self, value: str, detail: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class ResourceChart(QWidget):
    def __init__(self, color: str = DARK.accent, capacity: int = 48) -> None:
        super().__init__()
        self.values: deque[float] = deque([0.0] * capacity, maxlen=capacity)
        self.color = QColor(color)
        self.setMinimumHeight(62)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def push(self, value: float) -> None:
        self.values.append(max(0.0, min(100.0, float(value))))
        self.update()

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 4, -1, -4)
        values = list(self.values)
        if len(values) < 2 or rect.width() <= 0:
            return
        path = QPainterPath()
        for index, value in enumerate(values):
            x = rect.left() + index * rect.width() / (len(values) - 1)
            y = rect.bottom() - value / 100.0 * rect.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        fill = QPainterPath(path)
        fill.lineTo(rect.right(), rect.bottom())
        fill.lineTo(rect.left(), rect.bottom())
        fill.closeSubpath()
        tint = QColor(self.color)
        tint.setAlpha(32)
        painter.fillPath(fill, tint)
        painter.setPen(QPen(self.color, 2))
        painter.drawPath(path)


class ResourceCard(Card):
    def __init__(self, title: str, color: str = DARK.accent) -> None:
        super().__init__(padding=14)
        row = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setProperty("role", "muted")
        self.value = QLabel("0%")
        self.value.setStyleSheet("font-weight: 700;")
        row.addWidget(self.title)
        row.addStretch()
        row.addWidget(self.value)
        self.chart = ResourceChart(color)
        self.body.addLayout(row)
        self.body.addWidget(self.chart)

    def update_value(self, value: float, detail: str | None = None) -> None:
        self.value.setText(detail or f"{value:.0f}%")
        self.chart.push(value)


def format_bytes(value: int | float | None) -> str:
    amount = float(value or 0)
    for suffix in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(amount) < 1024 or suffix == "ТБ":
            return f"{amount:.0f} {suffix}" if suffix == "Б" else f"{amount:.1f} {suffix}"
        amount /= 1024
    return "0 Б"


def format_duration(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"
