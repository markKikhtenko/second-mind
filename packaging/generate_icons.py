from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def render(svg: Path, destination: Path, size: int) -> None:
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG: {svg}")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    if not image.save(str(destination)):
        raise RuntimeError(f"Cannot write icon: {destination}")


def main() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    root = Path(__file__).resolve().parents[1]
    svg = root / "src" / "second_mind" / "assets" / "app-icon.svg"
    render(svg, root / "packaging" / "app-icon.ico", 256)
    render(svg, root / "src" / "second_mind" / "assets" / "app-icon.ico", 256)
    render(svg, root / "src" / "second_mind" / "assets" / "app-icon.png", 256)


if __name__ == "__main__":
    main()
