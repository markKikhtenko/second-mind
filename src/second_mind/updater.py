from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from .updates import UpdateError, verify_download_metadata

DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            # Windows can briefly deny replacement while the GUI or an
            # integration monitor has the previous status file open.
            if attempt == 19:
                raise
            time.sleep(0.1)


class UpdateTransaction:
    def __init__(self, arguments: argparse.Namespace) -> None:
        self.installer = Path(arguments.installer).resolve()
        self.metadata = Path(arguments.metadata).resolve()
        self.install_dir = Path(arguments.install_dir).resolve()
        self.data_dir = Path(arguments.data_dir).resolve()
        self.api_url = str(arguments.api_url).rstrip("/")
        self.desktop_pid = int(arguments.desktop_pid)
        self.current_version = str(arguments.current_version)
        self.no_restart = bool(arguments.no_restart)
        self.state_path = self.data_dir / "updates" / "last-update.json"
        self.log_path = self.data_dir / "updates" / "update.log"
        self.target_version = "unknown"
        self.backup_dir: Path | None = None
        self.database_backup: Path | None = None
        self.config_backup: Path | None = None
        self.backup_complete = False

    def log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).isoformat()
        with self.log_path.open("a", encoding="utf-8") as output:
            output.write(f"{stamp} {message}\n")

    def state(self, status: str, **details: Any) -> None:
        atomic_json(
            self.state_path,
            {
                "status": status,
                "from_version": self.current_version,
                "to_version": self.target_version,
                "updated_at": datetime.now(UTC).isoformat(),
                "backup_dir": str(self.backup_dir) if self.backup_dir else None,
                "database_backup": (
                    str(self.database_backup) if self.database_backup else None
                ),
                "config_backup": str(self.config_backup) if self.config_backup else None,
                **details,
            },
        )
        self.log(status + (f": {details}" if details else ""))

    def validate_paths(self) -> None:
        if not self.installer.is_file() or not self.metadata.is_file():
            raise UpdateError("Installer or its verification metadata is missing")
        if (
            self.install_dir == self.data_dir
            or self.install_dir in self.data_dir.parents
            or self.data_dir in self.install_dir.parents
        ):
            raise UpdateError("Application and user data directories must be separate")
        desktop = self.install_dir / "second-mind-desktop" / "second-mind-desktop.exe"
        if not desktop.exists():
            raise UpdateError("The existing Second Mind installation was not found")

    def wait_for_desktop(self) -> None:
        if self.desktop_pid <= 0:
            return
        try:
            process = psutil.Process(self.desktop_pid)
            process.wait(timeout=30)
        except psutil.NoSuchProcess:
            return
        except psutil.TimeoutExpired as exc:
            if psutil.pid_exists(self.desktop_pid):
                raise UpdateError("Desktop did not close before the update") from exc

    def health(self) -> dict[str, Any] | None:
        try:
            with urllib.request.urlopen(f"{self.api_url}/api/ping", timeout=3) as response:
                return json.loads(response.read())
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def stop_core(self) -> None:
        health = self.health()
        pid = int((health or {}).get("pid", 0))
        if health:
            request = urllib.request.Request(
                f"{self.api_url}/api/system/shutdown", data=b"", method="POST"
            )
            with suppress(urllib.error.URLError):
                urllib.request.urlopen(request, timeout=5).close()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.health() is None:
                return
            time.sleep(0.5)
        if pid:
            try:
                process = psutil.Process(pid)
                executable = Path(process.exe()).resolve()
                if self.install_dir in executable.parents:
                    process.terminate()
                    process.wait(timeout=10)
                    return
            except (psutil.Error, OSError):
                pass
        raise UpdateError("Second Mind Core did not stop safely")

    def stop_installed_processes(self) -> None:
        targets: list[psutil.Process] = []
        for process in psutil.process_iter(["pid", "exe"]):
            try:
                executable_text = process.info.get("exe")
                if not executable_text:
                    continue
                executable = Path(executable_text).resolve()
                if self.install_dir in executable.parents:
                    process.terminate()
                    targets.append(process)
            except (psutil.Error, OSError):
                continue
        _, alive = psutil.wait_procs(targets, timeout=10)
        for process in alive:
            with suppress(psutil.Error):
                process.kill()
        _, alive = psutil.wait_procs(alive, timeout=10)
        if alive:
            raise UpdateError("Installed application processes did not stop")

    def create_backups(self) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        root = self.data_dir / "update-backups" / (
            f"{self.current_version}-to-{self.target_version}-{stamp}"
        )
        root.mkdir(parents=True, exist_ok=False)
        self.backup_dir = root / "application"
        shutil.copytree(self.install_dir, self.backup_dir)
        database = self.data_dir / "second-mind.db"
        if database.exists():
            self.database_backup = root / "second-mind.db"
            source = sqlite3.connect(database)
            target = sqlite3.connect(self.database_backup)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        config = self.data_dir / "config.json"
        if config.exists():
            self.config_backup = root / "config.json"
            shutil.copy2(config, self.config_backup)
        self.backup_complete = True

    def run_installer(self) -> None:
        command = [
            str(self.installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            f"/DIR={self.install_dir}",
        ]
        result = subprocess.run(command, timeout=900, check=False)
        if result.returncode != 0:
            raise UpdateError(f"Installer failed with exit code {result.returncode}")

    def core_executable(self) -> Path:
        return self.install_dir / "second-mind-core" / "second-mind-core.exe"

    def desktop_executable(self) -> Path:
        return self.install_dir / "second-mind-desktop" / "second-mind-desktop.exe"

    def start_core_and_validate(self, expected_version: str, timeout: int = 180) -> None:
        core = self.core_executable()
        if not core.exists():
            raise UpdateError("Installed Core executable is missing")
        subprocess.Popen(
            [str(core)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
        deadline = time.monotonic() + timeout
        last_version: str | None = None
        while time.monotonic() < deadline:
            health = self.health()
            if health:
                last_version = str(health.get("version", ""))
                if last_version == expected_version:
                    return
            time.sleep(0.5)
        raise UpdateError(
            f"Updated Core health check failed (expected {expected_version}, got {last_version})"
        )

    def restore(self) -> None:
        self.stop_core()
        self.stop_installed_processes()
        if (
            not self.backup_complete
            or not self.backup_dir
            or not self.backup_dir.exists()
        ):
            raise UpdateError("Application rollback backup is missing")
        failed_dir = self.install_dir.with_name(self.install_dir.name + ".failed-update")
        restore_dir = self.install_dir.with_name(self.install_dir.name + ".rollback")
        if failed_dir.exists():
            shutil.rmtree(failed_dir)
        if restore_dir.exists():
            shutil.rmtree(restore_dir)
        shutil.copytree(self.backup_dir, restore_dir)

        database = self.data_dir / "second-mind.db"
        database_restore = Path(str(database) + ".rollback")
        database_restore.unlink(missing_ok=True)
        if self.database_backup and self.database_backup.exists():
            shutil.copy2(self.database_backup, database_restore)
        config = self.data_dir / "config.json"
        config_restore = Path(str(config) + ".rollback")
        config_restore.unlink(missing_ok=True)
        if self.config_backup and self.config_backup.exists():
            shutil.copy2(self.config_backup, config_restore)

        if self.install_dir.exists():
            self.install_dir.replace(failed_dir)
        restore_dir.replace(self.install_dir)
        if database_restore.exists():
            for suffix in ("-wal", "-shm"):
                Path(str(database) + suffix).unlink(missing_ok=True)
            os.replace(database_restore, database)
        if config_restore.exists():
            os.replace(config_restore, config)
        shutil.rmtree(failed_dir, ignore_errors=True)

    def launch_desktop(self, rolled_back: bool = False, failed: bool = False) -> None:
        if self.no_restart:
            self.stop_core()
            return
        command = [str(self.desktop_executable())]
        if rolled_back:
            command.append("--update-rolled-back")
        elif failed:
            command.append("--update-failed")
        else:
            command.extend(["--post-update", self.target_version])
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )

    def execute(self) -> int:
        try:
            self.validate_paths()
            manifest = verify_download_metadata(self.installer, self.metadata)
            self.target_version = str(manifest["version"])
            self.state("waiting_for_app")
            self.wait_for_desktop()
            self.state("stopping_core")
            self.stop_core()
            self.stop_installed_processes()
            self.state("backing_up")
            self.create_backups()
            self.state("installing")
            self.run_installer()
            self.state("validating")
            self.start_core_and_validate(self.target_version)
            self.state("succeeded")
            self.launch_desktop()
            return 0
        except Exception as exc:
            self.log(f"update failed: {exc}")
            try:
                if self.backup_complete:
                    self.state("rolling_back", error=str(exc))
                    self.restore()
                    self.start_core_and_validate(self.current_version)
                    self.state("rolled_back", error=str(exc))
                    self.launch_desktop(rolled_back=True)
                    return 2
            except Exception as rollback_exc:
                self.state(
                    "rollback_failed", error=str(exc), rollback_error=str(rollback_exc)
                )
                with suppress(Exception):
                    self.launch_desktop(failed=True)
                return 3
            self.state("failed", error=str(exc))
            with suppress(Exception):
                self.launch_desktop(failed=True)
            return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Second Mind detached updater")
    parser.add_argument("--installer", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--desktop-pid", type=int, required=True)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--no-restart", action="store_true")
    return parser


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Second Mind updater is supported on Windows only")
    arguments = build_parser().parse_args()
    raise SystemExit(UpdateTransaction(arguments).execute())


if __name__ == "__main__":
    main()
