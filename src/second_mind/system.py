from __future__ import annotations

import os
import platform
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil


def diagnostics() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    cpu_percent = psutil.cpu_percent(interval=None)
    gpu: dict[str, Any] | None = None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        first = result.stdout.strip().splitlines()[0]
        name, utilization, used, total = [part.strip() for part in first.split(",", 3)]
        gpu = {
            "name": name,
            "utilization_percent": float(utilization),
            "vram_used_mb": float(used),
            "vram_total_mb": float(total),
        }
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        pass
    return {
        "cpu_percent": cpu_percent,
        "ram_used_bytes": memory.used,
        "ram_total_bytes": memory.total,
        "ram_percent": memory.percent,
        "gpu": gpu,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pid": os.getpid(),
    }


def core_command() -> str:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        sibling = executable.with_name("second-mind-core.exe")
        return f'"{sibling if sibling.exists() else executable}"'
    return f'"{sys.executable}" -m second_mind.server'


def set_windows_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Autostart management is currently supported on Windows only")
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, "SecondMindCore", 0, winreg.REG_SZ, core_command())
        else:
            with suppress(FileNotFoundError):
                winreg.DeleteValue(key, "SecondMindCore")


def windows_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "SecondMindCore")
            return bool(str(value).strip())
    except FileNotFoundError:
        return False
