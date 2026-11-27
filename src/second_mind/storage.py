from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from . import __version__
from .config import AppSettings, ConfigManager
from .db import Database
from .repositories import ExecutionLogRepository


class StorageError(RuntimeError):
    pass


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _human_backup_time(path: Path | None) -> str | None:
    if path is None:
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()


class StorageManager:
    """Safe local storage operations for SQLite, attachments and portable archives."""

    TABLES = (
        "tasks",
        "memories",
        "projects",
        "documents",
        "conversations",
        "messages",
        "inbox",
        "reminders",
        "execution_log",
        "agent_configs",
        "skills",
        "agent_skills",
        "tool_policies",
        "tool_call_history",
        "tool_approvals",
        "agent_runs",
    )

    def __init__(
        self,
        settings: AppSettings,
        config_manager: ConfigManager,
        db: Database,
        logs: ExecutionLogRepository,
    ) -> None:
        self.settings = settings
        self.config_manager = config_manager
        self.db = db
        self.logs = logs
        self._lock = asyncio.Lock()

    @staticmethod
    def _backup_sqlite(source_path: Path, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(source_path, timeout=30)) as source:
            source.execute("PRAGMA busy_timeout=10000")
            with closing(sqlite3.connect(target_path)) as target:
                source.backup(target, pages=1024, sleep=0.05)
                target.execute("PRAGMA journal_mode=DELETE")
                target.commit()

    @staticmethod
    def _integrity(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"ok": False, "message": "База данных не найдена"}
        try:
            uri = f"file:{path.as_posix()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=15)) as connection:
                rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
            ok = rows == ["ok"]
            return {"ok": ok, "message": "ok" if ok else "; ".join(rows[:10])}
        except sqlite3.Error as exc:
            return {"ok": False, "message": str(exc)}

    def _backup_files(self) -> list[Path]:
        if not self.settings.backups_dir.exists():
            return []
        return sorted(
            self.settings.backups_dir.glob("second-mind-backup-*.zip"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )

    async def status(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for table in self.TABLES:
            row = await self.db.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
            counts[table] = int((row or {}).get("count", 0))
        schema = await self.db.fetch_one(
            "SELECT COALESCE(MAX(version),0) AS version FROM schema_migrations"
        )

        def filesystem() -> dict[str, Any]:
            backups = self._backup_files()
            disk = shutil.disk_usage(self.settings.data_dir)
            database_size = sum(
                path.stat().st_size
                for path in (
                    self.settings.database_path,
                    self.settings.database_path.with_name(self.settings.database_path.name + "-wal"),
                    self.settings.database_path.with_name(self.settings.database_path.name + "-shm"),
                )
                if path.exists()
            )
            return {
                "data_dir": str(self.settings.data_dir),
                "database_path": str(self.settings.database_path),
                "database_size": database_size,
                "attachments_size": _directory_size(self.settings.attachments_dir),
                "skills_size": _directory_size(self.settings.skills_dir),
                "backups_size": sum(path.stat().st_size for path in backups),
                "free_space": disk.free,
                "total_space": disk.total,
                "last_backup_at": _human_backup_time(backups[0] if backups else None),
                "backups": [
                    {
                        "name": item.name,
                        "path": str(item),
                        "size": item.stat().st_size,
                        "created_at": _human_backup_time(item),
                    }
                    for item in backups
                ],
                "integrity": self._integrity(self.settings.database_path),
            }

        result = await asyncio.to_thread(filesystem)
        result.update(
            {
                "schema_version": int((schema or {}).get("version", 0)),
                "counts": counts,
                "automatic_backups": self.settings.automatic_backups,
                "backup_interval_hours": self.settings.backup_interval_hours,
                "backup_retention_days": self.settings.backup_retention_days,
            }
        )
        return result

    def _write_archive(self, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        archive = destination / f"second-mind-backup-{stamp}.zip"
        suffix = 1
        while archive.exists():
            archive = destination / f"second-mind-backup-{stamp}-{suffix}.zip"
            suffix += 1
        with tempfile.TemporaryDirectory(prefix="second-mind-backup-") as temp_dir:
            staging = Path(temp_dir)
            self._backup_sqlite(self.settings.database_path, staging / "second-mind.db")
            if self.config_manager.path.exists():
                shutil.copy2(self.config_manager.path, staging / "config.json")
            if self.settings.attachments_dir.exists():
                shutil.copytree(self.settings.attachments_dir, staging / "attachments")
            if self.settings.skills_dir.exists():
                shutil.copytree(self.settings.skills_dir, staging / "skills")
            manifest = {
                "format": "second-mind-backup",
                "format_version": 1,
                "application_version": __version__,
                "created_at": datetime.now(UTC).isoformat(),
                "includes": ["sqlite", "configuration", "attachments", "skills"],
                "secrets_included": False,
            }
            (staging / "backup-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary = archive.with_suffix(".zip.tmp")
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as bundle:
                for item in staging.rglob("*"):
                    if item.is_file():
                        bundle.write(item, item.relative_to(staging).as_posix())
            os.replace(temporary, archive)
        return archive

    async def create_backup(self, destination: Path | None = None) -> Path:
        async with self._lock:
            target = (destination or self.settings.backups_dir).expanduser().resolve()
            path = await asyncio.to_thread(self._write_archive, target)
            await self.prune_backups()
            await self.logs.add(
                "storage", "backup", "completed", details={"path": str(path)}
            )
            return path

    async def prune_backups(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.backup_retention_days)

        def prune() -> int:
            deleted = 0
            for path in self._backup_files():
                modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                if modified < cutoff:
                    path.unlink(missing_ok=True)
                    deleted += 1
            return deleted

        return await asyncio.to_thread(prune)

    async def scheduled_backup(self) -> None:
        if not self.settings.automatic_backups or self._lock.locked():
            return
        backups = await asyncio.to_thread(self._backup_files)
        if backups:
            age = datetime.now(UTC) - datetime.fromtimestamp(backups[0].stat().st_mtime, UTC)
            if age < timedelta(hours=self.settings.backup_interval_hours):
                return
        try:
            await self.create_backup()
        except Exception as exc:
            await self.logs.add(
                "storage", "scheduled_backup", "failed", details={"error": str(exc)}
            )

    @staticmethod
    def _safe_extract(archive: Path, destination: Path) -> None:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                name = PurePosixPath(member.filename)
                if name.is_absolute() or ".." in name.parts:
                    raise StorageError("Архив содержит небезопасный путь")
                if member.file_size > 10 * 1024 * 1024 * 1024:
                    raise StorageError("Файл в архиве превышает допустимый размер")
            bundle.extractall(destination)

    def _restore(self, archive: Path) -> None:
        if not archive.is_file():
            raise StorageError("Файл резервной копии не найден")
        with tempfile.TemporaryDirectory(prefix="second-mind-restore-") as temp_dir:
            staging = Path(temp_dir)
            self._safe_extract(archive, staging)
            database = staging / "second-mind.db"
            manifest_path = staging / "backup-manifest.json"
            if not database.exists() or not manifest_path.exists():
                raise StorageError("Это не резервная копия Second Mind")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("format") != "second-mind-backup":
                raise StorageError("Неизвестный формат резервной копии")
            integrity = self._integrity(database)
            if not integrity["ok"]:
                raise StorageError(f"Резервная база повреждена: {integrity['message']}")

            with (
                closing(sqlite3.connect(database, timeout=30)) as source,
                closing(sqlite3.connect(self.settings.database_path, timeout=30)) as target,
            ):
                source.backup(target, pages=1024, sleep=0.05)
                target.commit()

            restored_attachments = staging / "attachments"
            current = self.settings.attachments_dir
            rollback = current.with_name(f"attachments.pre-restore-{uuid.uuid4().hex}")
            if current.exists():
                current.replace(rollback)
            try:
                if restored_attachments.exists():
                    shutil.copytree(restored_attachments, current)
                else:
                    current.mkdir(parents=True, exist_ok=True)
            except Exception:
                shutil.rmtree(current, ignore_errors=True)
                if rollback.exists():
                    rollback.replace(current)
                raise
            shutil.rmtree(rollback, ignore_errors=True)

            restored_skills = staging / "skills"
            current_skills = self.settings.skills_dir
            skills_rollback = current_skills.with_name(
                f"skills.pre-restore-{uuid.uuid4().hex}"
            )
            if current_skills.exists():
                current_skills.replace(skills_rollback)
            try:
                if restored_skills.exists():
                    shutil.copytree(restored_skills, current_skills)
                else:
                    current_skills.mkdir(parents=True, exist_ok=True)
            except Exception:
                shutil.rmtree(current_skills, ignore_errors=True)
                if skills_rollback.exists():
                    skills_rollback.replace(current_skills)
                raise
            shutil.rmtree(skills_rollback, ignore_errors=True)

            restored_config = staging / "config.json"
            if restored_config.exists():
                payload = json.loads(restored_config.read_text(encoding="utf-8"))
                payload["data_dir"] = str(self.settings.data_dir)
                candidate = AppSettings.model_validate(payload)
                for key in self.settings.__class__.model_fields:
                    setattr(self.settings, key, getattr(candidate, key))
                self.config_manager.save(self.settings)

    async def restore(self, archive: Path, *, confirmed: bool) -> Path:
        if not confirmed:
            raise StorageError("Восстановление требует явного подтверждения")
        async with self._lock:
            emergency = await asyncio.to_thread(
                self._write_archive, self.settings.backups_dir
            )
            try:
                await asyncio.to_thread(self._restore, archive.expanduser().resolve())
            except Exception:
                await self.logs.add(
                    "storage",
                    "restore",
                    "failed",
                    details={"archive": str(archive), "safety_backup": str(emergency)},
                )
                raise
            await self.logs.add(
                "storage",
                "restore",
                "completed",
                details={"archive": str(archive), "safety_backup": str(emergency)},
            )
            return emergency

    async def export_json(self, destination: Path) -> Path:
        payload: dict[str, Any] = {
            "format": "second-mind-export",
            "format_version": 1,
            "application_version": __version__,
            "created_at": datetime.now(UTC).isoformat(),
            "secrets_included": False,
            "data": {},
        }
        for table in self.TABLES:
            payload["data"][table] = await self.db.fetch_all(f"SELECT * FROM {table}")
        destination = destination.expanduser().resolve()
        if destination.suffix.casefold() != ".json":
            destination = destination / (
                "second-mind-export-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ".json"
            )

        def write() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, destination)

        await asyncio.to_thread(write)
        await self.logs.add(
            "storage", "export", "completed", details={"path": str(destination)}
        )
        return destination

    async def integrity(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._integrity, self.settings.database_path)

    async def optimize(self) -> dict[str, int]:
        async with self._lock:
            before = self.settings.database_path.stat().st_size

            def run() -> None:
                with closing(sqlite3.connect(self.settings.database_path, timeout=60)) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.execute("VACUUM")
                    conn.execute("PRAGMA optimize")

            await asyncio.to_thread(run)
            after = self.settings.database_path.stat().st_size
            await self.logs.add("storage", "optimize", "completed")
            return {"before": before, "after": after, "saved": max(0, before - after)}

    async def cleanup(self, *, confirmed: bool) -> dict[str, int]:
        if not confirmed:
            raise StorageError("Очистка требует явного подтверждения")

        def clean() -> dict[str, int]:
            removed = 0
            bytes_removed = 0
            targets = [self.settings.data_dir / "temp"]
            targets.extend(
                path
                for path in (self.settings.data_dir / "updates").glob("*.part")
                if path.is_file()
            )
            for target in targets:
                if target.is_file():
                    bytes_removed += target.stat().st_size
                    target.unlink(missing_ok=True)
                    removed += 1
                elif target.is_dir():
                    files = [item for item in target.rglob("*") if item.is_file()]
                    bytes_removed += sum(item.stat().st_size for item in files)
                    removed += len(files)
                    shutil.rmtree(target)
            return {"files": removed, "bytes": bytes_removed}

        result = await asyncio.to_thread(clean)
        await self.logs.add("storage", "cleanup", "completed", details=result)
        return result

    def _move(self, destination: Path) -> tuple[Path, Path]:
        source_dir = self.settings.data_dir.resolve()
        destination = destination.expanduser().resolve()
        if destination == source_dir:
            raise StorageError("Новое расположение совпадает с текущим")
        if source_dir in destination.parents or destination in source_dir.parents:
            raise StorageError("Новое расположение не должно быть внутри текущей папки или над ней")
        if destination.exists() and any(destination.iterdir()):
            raise StorageError("Для переноса выберите пустую папку")
        destination.mkdir(parents=True, exist_ok=True)
        probe = destination / f".write-test-{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        staging = destination / f".second-mind-transfer-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            self._backup_sqlite(self.settings.database_path, staging / "second-mind.db")
            if self.settings.attachments_dir.exists():
                shutil.copytree(self.settings.attachments_dir, staging / "attachments")
            if self.settings.backups_dir.exists():
                shutil.copytree(self.settings.backups_dir, staging / "backups")
            if self.settings.skills_dir.exists():
                shutil.copytree(self.settings.skills_dir, staging / "skills")
            integrity = self._integrity(staging / "second-mind.db")
            if not integrity["ok"]:
                raise StorageError(f"Перенесённая база не прошла проверку: {integrity['message']}")
            for item in staging.iterdir():
                item.replace(destination / item.name)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        old_database = self.settings.database_path
        old_attachments = self.settings.attachments_dir
        old_backups = self.settings.backups_dir
        old_skills = self.settings.skills_dir
        with closing(sqlite3.connect(destination / "second-mind.db")) as connection:
            rows = connection.execute("SELECT id,storage_path FROM documents").fetchall()
            for document_id, storage_path in rows:
                path = Path(str(storage_path))
                try:
                    relative = path.relative_to(old_attachments)
                except ValueError:
                    continue
                connection.execute(
                    "UPDATE documents SET storage_path=? WHERE id=?",
                    (str(destination / "attachments" / relative), document_id),
                )
            skill_rows = connection.execute(
                "SELECT id,source_path FROM skills WHERE is_builtin=0 OR source_path LIKE ?",
                (f"{old_skills}%",),
            ).fetchall()
            for skill_id, source_path in skill_rows:
                if not source_path:
                    continue
                try:
                    relative = Path(str(source_path)).relative_to(old_skills)
                except ValueError:
                    continue
                connection.execute(
                    "UPDATE skills SET source_path=? WHERE id=?",
                    (str(destination / "skills" / relative), skill_id),
                )
            connection.commit()

        self.settings.data_dir = destination
        self.config_manager.save(self.settings)
        self.db.path = destination / "second-mind.db"
        for path in (
            old_database,
            old_database.with_name(old_database.name + "-wal"),
            old_database.with_name(old_database.name + "-shm"),
        ):
            path.unlink(missing_ok=True)
        shutil.rmtree(old_attachments, ignore_errors=True)
        shutil.rmtree(old_backups, ignore_errors=True)
        shutil.rmtree(old_skills, ignore_errors=True)
        return source_dir, destination

    async def move(self, destination: Path, *, confirmed: bool) -> tuple[Path, Path]:
        if not confirmed:
            raise StorageError("Перенос данных требует явного подтверждения")
        async with self._lock:
            old, new = await asyncio.to_thread(self._move, destination)
            await self.logs.add(
                "storage",
                "move",
                "completed",
                details={"from": str(old), "to": str(new)},
            )
            return old, new
