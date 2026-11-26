from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            priority INTEGER NOT NULL DEFAULT 0,
            project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
            due_at TEXT,
            source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at);
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            source_ref TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            content, kind, content='memories', content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memory_fts(rowid, content, kind) VALUES (new.rowid, new.content, new.kind);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, content, kind)
            VALUES ('delete', old.rowid, old.content, old.kind);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, content, kind)
            VALUES ('delete', old.rowid, old.content, old.kind);
            INSERT INTO memory_fts(rowid, content, kind) VALUES (new.rowid, new.content, new.kind);
        END;
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            transport TEXT NOT NULL DEFAULT 'desktop',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            transport TEXT NOT NULL,
            external_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external
            ON messages(transport, external_id) WHERE external_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
        CREATE TABLE IF NOT EXISTS inbox (
            id TEXT PRIMARY KEY,
            transport TEXT NOT NULL,
            external_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(transport, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status, created_at);
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            due_at TEXT NOT NULL,
            target_transport TEXT NOT NULL DEFAULT 'desktop',
            target_user_id TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled',
            created_at TEXT NOT NULL,
            delivered_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            extracted_text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'indexed',
            source TEXT NOT NULL DEFAULT 'upload',
            created_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
            filename, extracted_text, content='documents', content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS document_ai AFTER INSERT ON documents BEGIN
            INSERT INTO document_fts(rowid, filename, extracted_text)
            VALUES (new.rowid, new.filename, new.extracted_text);
        END;
        CREATE TRIGGER IF NOT EXISTS document_ad AFTER DELETE ON documents BEGIN
            INSERT INTO document_fts(document_fts, rowid, filename, extracted_text)
            VALUES ('delete', old.rowid, old.filename, old.extracted_text);
        END;
        CREATE TABLE IF NOT EXISTS execution_log (
            id TEXT PRIMARY KEY,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        """,
    )
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, script in MIGRATIONS:
                if version not in applied:
                    connection.executescript(script)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                    )
            connection.commit()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with closing(self._connect()) as connection:
            try:
                result = operation(connection)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    async def run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._run, operation)

    async def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        return await self.run(lambda conn: conn.execute(sql, parameters).rowcount)

    async def fetch_one(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> dict[str, Any] | None:
        def query(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute(sql, parameters).fetchone()
            return dict(row) if row else None

        return await self.run(query)

    async def fetch_all(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        return await self.run(
            lambda conn: [dict(row) for row in conn.execute(sql, parameters).fetchall()]
        )
