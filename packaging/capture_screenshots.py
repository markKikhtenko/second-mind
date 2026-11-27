from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from second_mind.desktop import ApiClient, MainWindow, ServiceController, ThemeManager


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=Path("docs/screenshots"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Second Mind Screenshot")
    app.setFont(QFont("Segoe UI", 10))
    app.setQuitOnLastWindowClosed(False)
    theme = ThemeManager(app)
    window = MainWindow(ApiClient(args.api_url), theme, ServiceController(args.api_url))
    window.resize(1500, 920)
    window.show()
    captures = [
        ("dark", "overview"),
        ("dark", "assistant"),
        ("dark", "storage"),
        ("light", "overview"),
        ("light", "assistant"),
        ("light", "storage"),
    ]

    def capture(index: int = 0) -> None:
        if index >= len(captures):
            QThreadPool.globalInstance().waitForDone(5000)
            window._force_quit = True
            window.close()
            app.quit()
            return
        mode, page = captures[index]
        theme.mode = mode
        theme.apply()
        window.open_page(page)

        def save() -> None:
            destination = args.output / f"{page}-{mode}.png"
            if not window.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"Cannot save {destination}")
            print(destination)
            QTimer.singleShot(200, lambda: capture(index + 1))

        wait_ms = 7000 if page == "overview" else 2200
        QTimer.singleShot(wait_ms, save)

    QTimer.singleShot(500, capture)
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
