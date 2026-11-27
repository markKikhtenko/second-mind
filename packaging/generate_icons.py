from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def rasterize(svg: Path, size: int) -> QImage:
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG: {svg}")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def render(svg: Path, destination: Path, size: int) -> None:
    image = rasterize(svg, size)
    if not image.save(str(destination)):
        raise RuntimeError(f"Cannot write icon: {destination}")


def render_windows_icon(svg: Path, destination: Path) -> None:
    """Write a multi-resolution PNG-backed ICO for Windows and High DPI."""
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    frames: list[tuple[int, bytes]] = []
    for size in sizes:
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise RuntimeError("Cannot create an in-memory PNG buffer.")
        if not rasterize(svg, size).save(buffer, "PNG"):
            raise RuntimeError(f"Cannot render {size}px icon frame.")
        buffer.close()
        frames.append((size, bytes(data)))

    header_size = 6 + 16 * len(frames)
    entries = bytearray()
    payload = bytearray()
    offset = header_size
    for size, frame in frames:
        dimension = 0 if size == 256 else size
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(frame),
                offset,
            )
        )
        payload.extend(frame)
        offset += len(frame)
    destination.write_bytes(
        struct.pack("<HHH", 0, 1, len(frames)) + bytes(entries) + bytes(payload)
    )


def main() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    root = Path(__file__).resolve().parents[1]
    svg = root / "src" / "second_mind" / "assets" / "app-icon.svg"
    render_windows_icon(svg, root / "packaging" / "app-icon.ico")
    render_windows_icon(svg, root / "src" / "second_mind" / "assets" / "app-icon.ico")
    render(svg, root / "src" / "second_mind" / "assets" / "app-icon.png", 256)


if __name__ == "__main__":
    main()
