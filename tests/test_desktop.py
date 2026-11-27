from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from second_mind.desktop import MainWindow, ThemeManager  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, _method: str, path: str, **_kwargs: Any) -> Any:
        self.calls.append((_method, path))
        if path == "/health":
            return {
                "time": "2026-11-27T10:00:00+00:00",
                "uptime_seconds": 42,
                "core": {"version": "0.3.0"},
                "llm": {
                    "available": False,
                    "models": 0,
                    "current_model": None,
                    "error": "Runtime offline",
                },
                "telegram": {"running": False, "configured": False},
                "speech": {"loaded": False, "model": "small", "device": "cpu"},
                "mcp": {"available": True, "tools": 5},
                "requests": 0,
                "average_request_ms": 0,
                "queue": {},
                "system": {
                    "cpu_percent": 1,
                    "ram_percent": 20,
                    "ram_used_bytes": 1,
                    "ram_total_bytes": 10,
                    "gpu": None,
                },
                "recent_activity": [],
                "open_tasks": 0,
                "memories": 0,
                "last_error": None,
            }
        if path == "/settings":
            return {
                "update_repository": "markKikhtenko/second-mind",
                "automatic_update_checks": True,
                "update_check_interval_hours": 24,
            }
        if path == "/updates/status":
            return {"phase": "idle"}
        if path == "/updates/check":
            return {
                "current_version": "0.3.1",
                "latest_version": "0.3.1",
                "available": False,
                "changelog": "Thread-safe desktop startup",
            }
        if path == "/storage":
            return {
                "database_path": "C:/Second Mind/second-mind.db",
                "data_dir": "C:/Second Mind",
                "integrity": {"ok": True, "message": "ok"},
                "schema_version": 1,
                "counts": {},
                "backups": [],
                "automatic_backups": True,
                "backup_interval_hours": 24,
                "backup_retention_days": 30,
            }
        return []


class FakeController:
    def running(self) -> bool:
        return True

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def restart(self) -> None:
        pass


def test_all_desktop_pages_construct_and_themes_apply() -> None:
    app = QApplication.instance() or QApplication([])
    theme = ThemeManager(app)
    theme.set_mode("dark")
    client = FakeClient()
    window = MainWindow(client, theme, FakeController())  # type: ignore[arg-type]
    window.show()
    assert len(window.pages) == 20
    assert set(window.sidebar.buttons) == set(window.pages)
    window.open_page("storage")
    assert window.stack.currentWidget() is window.pages["storage"]
    theme.set_mode("light")
    assert theme.dark is False
    window.updates_page.start_automatic_checks()
    for _ in range(60):
        if ("GET", "/updates/check") in client.calls:
            break
        QTest.qWait(50)
    assert ("GET", "/updates/check") in client.calls
    window._force_quit = True
    window.close()
    app.processEvents()
