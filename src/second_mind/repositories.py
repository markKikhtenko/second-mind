from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from .db import Database
from .schemas import (
    MemoryCreate,
    MemoryUpdate,
    ProjectCreate,
    ReminderCreate,
    TaskCreate,
    TaskUpdate,
)


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(row: dict[str, Any] | None, fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if row is None:
        return None
    for field in fields:
        if field in row:
            row[field.removesuffix("_json")] = json.loads(row.pop(field))
    return row


class TaskRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, value: TaskCreate) -> dict[str, Any]:
        task_id, now = new_id(), utcnow()
        await self.db.execute(
            """INSERT INTO tasks
            (id,title,description,status,priority,project_id,due_at,source,created_at,updated_at)
            VALUES (?,?,?,'open',?,?,?,?,?,?)""",
            (
                task_id,
                value.title.strip(),
                value.description,
                value.priority,
                value.project_id,
                value.due_at.isoformat() if value.due_at else None,
                value.source,
                now,
                now,
            ),
        )
        return await self.get(task_id)  # type: ignore[return-value]

    async def get(self, task_id: str) -> dict[str, Any] | None:
        return await self.db.fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))

    async def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            return await self.db.fetch_all(
                "SELECT * FROM tasks WHERE status=? ORDER BY priority DESC, due_at, created_at DESC LIMIT ?",
                (status, limit),
            )
        return await self.db.fetch_all(
            "SELECT * FROM tasks ORDER BY status='completed', priority DESC, due_at, created_at DESC LIMIT ?",
            (limit,),
        )

    async def update(self, task_id: str, value: TaskUpdate) -> dict[str, Any] | None:
        changes = value.model_dump(exclude_unset=True)
        allowed = {"title", "description", "status", "priority", "project_id", "due_at"}
        changes = {key: val for key, val in changes.items() if key in allowed}
        if "due_at" in changes and changes["due_at"] is not None:
            changes["due_at"] = changes["due_at"].isoformat()
        if not changes:
            return await self.get(task_id)
        changes["updated_at"] = utcnow()
        assignments = ",".join(f"{key}=?" for key in changes)
        await self.db.execute(
            f"UPDATE tasks SET {assignments} WHERE id=?", (*changes.values(), task_id)
        )
        return await self.get(task_id)

    async def delete(self, task_id: str) -> bool:
        return bool(await self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,)))


class MemoryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def content_hash(content: str) -> str:
        normalized = " ".join(content.casefold().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    async def create(self, value: MemoryCreate) -> dict[str, Any]:
        memory_id, now = new_id(), utcnow()
        digest = self.content_hash(value.content)

        def insert(connection: sqlite3.Connection) -> str:
            existing = connection.execute(
                "SELECT id FROM memories WHERE content_hash=?", (digest,)
            ).fetchone()
            if existing:
                return str(existing[0])
            connection.execute(
                """INSERT INTO memories
                (id,kind,content,content_hash,source,source_ref,metadata_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    memory_id,
                    value.kind,
                    value.content.strip(),
                    digest,
                    value.source,
                    value.source_ref,
                    _json(value.metadata),
                    now,
                    now,
                ),
            )
            return memory_id

        actual_id = await self.db.run(insert)
        return await self.get(actual_id)  # type: ignore[return-value]

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        return _decode(
            await self.db.fetch_one("SELECT * FROM memories WHERE id=?", (memory_id,)),
            ("metadata_json",),
        )

    async def search(
        self, query: str = "", kind: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        if query.strip():
            tokens = [token.replace('"', "") for token in query.split() if token]
            fts_query = " OR ".join(f'"{token}"*' for token in tokens)
            if not fts_query:
                return []
            sql = """SELECT m.* FROM memory_fts f
                     JOIN memories m ON m.rowid=f.rowid
                     WHERE memory_fts MATCH ?"""
            params: list[Any] = [fts_query]
            if kind:
                sql += " AND m.kind=?"
                params.append(kind)
            sql += " ORDER BY bm25(memory_fts), m.updated_at DESC LIMIT ?"
            params.append(limit)
            try:
                rows = await self.db.fetch_all(sql, params)
            except sqlite3.OperationalError:
                rows = await self.db.fetch_all(
                    "SELECT * FROM memories WHERE content LIKE ? ORDER BY updated_at DESC LIMIT ?",
                    (f"%{query}%", limit),
                )
        else:
            if kind:
                rows = await self.db.fetch_all(
                    "SELECT * FROM memories WHERE kind=? ORDER BY updated_at DESC LIMIT ?",
                    (kind, limit),
                )
            else:
                rows = await self.db.fetch_all(
                    "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?", (limit,)
                )
        return [_decode(row, ("metadata_json",)) or {} for row in rows]

    async def update(self, memory_id: str, value: MemoryUpdate) -> dict[str, Any] | None:
        changes = value.model_dump(exclude_unset=True)
        if "metadata" in changes:
            changes["metadata_json"] = _json(changes.pop("metadata"))
        if "content" in changes:
            changes["content"] = changes["content"].strip()
            changes["content_hash"] = self.content_hash(changes["content"])
        if not changes:
            return await self.get(memory_id)
        changes["updated_at"] = utcnow()
        assignments = ",".join(f"{key}=?" for key in changes)
        await self.db.execute(
            f"UPDATE memories SET {assignments} WHERE id=?", (*changes.values(), memory_id)
        )
        return await self.get(memory_id)

    async def delete(self, memory_id: str) -> bool:
        return bool(await self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,)))


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, value: ProjectCreate) -> dict[str, Any]:
        project_id, now = new_id(), utcnow()
        await self.db.execute(
            "INSERT INTO projects(id,name,description,status,created_at,updated_at) VALUES (?,?,?,'active',?,?)",
            (project_id, value.name.strip(), value.description, now, now),
        )
        return await self.db.fetch_one("SELECT * FROM projects WHERE id=?", (project_id,))  # type: ignore[return-value]

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT * FROM projects ORDER BY status, updated_at DESC LIMIT ?", (limit,)
        )


class ConversationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure(self, conversation_id: str, transport: str, title: str = "") -> None:
        now = utcnow()
        await self.db.execute(
            """INSERT INTO conversations(id,title,transport,created_at,updated_at)
            VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
            (conversation_id, title[:200], transport, now, now),
        )

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        transport: str,
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.ensure(conversation_id, transport, content[:80] if role == "user" else "")
        message_id = new_id()
        await self.db.execute(
            """INSERT INTO messages
            (id,conversation_id,role,content,transport,external_id,metadata_json,created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                message_id,
                conversation_id,
                role,
                content,
                transport,
                external_id,
                _json(metadata or {}),
                utcnow(),
            ),
        )
        return await self.db.fetch_one("SELECT * FROM messages WHERE id=?", (message_id,))  # type: ignore[return-value]

    async def history(self, conversation_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            """SELECT role,content,created_at FROM (
                SELECT role,content,created_at FROM messages WHERE conversation_id=?
                ORDER BY created_at DESC LIMIT ?
            ) ORDER BY created_at""",
            (conversation_id, limit),
        )
        return rows


class ReminderRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, value: ReminderCreate) -> dict[str, Any]:
        reminder_id = new_id()
        await self.db.execute(
            """INSERT INTO reminders
            (id,text,due_at,target_transport,target_user_id,status,created_at)
            VALUES (?,?,?,?,?,'scheduled',?)""",
            (
                reminder_id,
                value.text,
                value.due_at.astimezone(UTC).isoformat(),
                value.target_transport,
                value.target_user_id,
                utcnow(),
            ),
        )
        return await self.db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,))  # type: ignore[return-value]

    async def due(self) -> list[dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT * FROM reminders WHERE status='scheduled' AND due_at<=? ORDER BY due_at",
            (utcnow(),),
        )

    async def delivered(self, reminder_id: str) -> None:
        await self.db.execute(
            "UPDATE reminders SET status='delivered', delivered_at=? WHERE id=?",
            (utcnow(), reminder_id),
        )


class InboxRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def enqueue(
        self, transport: str, external_id: str, sender_id: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        item_id, now = new_id(), utcnow()

        def insert(connection: sqlite3.Connection) -> tuple[str, bool]:
            existing = connection.execute(
                "SELECT id FROM inbox WHERE transport=? AND external_id=?",
                (transport, external_id),
            ).fetchone()
            if existing:
                return str(existing[0]), False
            connection.execute(
                """INSERT INTO inbox
                (id,transport,external_id,sender_id,payload_json,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'pending',?,?)""",
                (item_id, transport, external_id, sender_id, _json(payload), now, now),
            )
            return item_id, True

        actual_id, created = await self.db.run(insert)
        row = await self.db.fetch_one("SELECT * FROM inbox WHERE id=?", (actual_id,))
        return _decode(row, ("payload_json",)) or {}, created

    async def mark(self, item_id: str, status: str, error: str | None = None) -> None:
        await self.db.execute(
            """UPDATE inbox SET status=?, attempts=attempts+1, last_error=?, updated_at=?
            WHERE id=?""",
            (status, error[:2000] if error else None, utcnow(), item_id),
        )

    async def counts(self) -> dict[str, int]:
        rows = await self.db.fetch_all("SELECT status,COUNT(*) AS count FROM inbox GROUP BY status")
        return {str(row["status"]): int(row["count"]) for row in rows}


class ExecutionLogRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        agent: str,
        action: str,
        status: str,
        duration_ms: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = {
            key: value
            for key, value in (details or {}).items()
            if "token" not in key.casefold() and "secret" not in key.casefold()
        }
        await self.db.execute(
            """INSERT INTO execution_log
            (id,agent,action,status,duration_ms,details_json,created_at) VALUES (?,?,?,?,?,?,?)""",
            (new_id(), agent, action, status, duration_ms, _json(safe_details), utcnow()),
        )

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT * FROM execution_log ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_decode(row, ("details_json",)) or {} for row in rows]


class DocumentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        *,
        filename: str,
        media_type: str,
        storage_path: str,
        sha256: str,
        extracted_text: str,
        source: str = "upload",
    ) -> dict[str, Any]:
        document_id = new_id()

        def insert(connection: sqlite3.Connection) -> str:
            existing = connection.execute(
                "SELECT id FROM documents WHERE sha256=?", (sha256,)
            ).fetchone()
            if existing:
                return str(existing[0])
            connection.execute(
                """INSERT INTO documents
                (id,filename,media_type,storage_path,sha256,extracted_text,status,source,created_at)
                VALUES (?,?,?,?,?,?,'indexed',?,?)""",
                (
                    document_id,
                    filename,
                    media_type,
                    storage_path,
                    sha256,
                    extracted_text,
                    source,
                    utcnow(),
                ),
            )
            return document_id

        actual_id = await self.db.run(insert)
        return await self.get(actual_id)  # type: ignore[return-value]

    async def get(self, document_id: str) -> dict[str, Any] | None:
        return await self.db.fetch_one("SELECT * FROM documents WHERE id=?", (document_id,))

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.db.fetch_all(
            """SELECT id,filename,media_type,status,source,created_at,
            length(extracted_text) AS text_length FROM documents
            ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        tokens = [token.replace('"', "") for token in query.split() if token]
        if not tokens:
            return await self.list(limit)
        fts_query = " OR ".join(f'"{token}"*' for token in tokens)
        try:
            return await self.db.fetch_all(
                """SELECT d.id,d.filename,d.media_type,d.status,d.source,d.created_at,
                snippet(document_fts, 1, '[', ']', ' … ', 24) AS excerpt
                FROM document_fts f JOIN documents d ON d.rowid=f.rowid
                WHERE document_fts MATCH ? ORDER BY bm25(document_fts) LIMIT ?""",
                (fts_query, limit),
            )
        except sqlite3.OperationalError:
            return await self.db.fetch_all(
                """SELECT id,filename,media_type,status,source,created_at,
                substr(extracted_text,1,500) AS excerpt FROM documents
                WHERE extracted_text LIKE ? OR filename LIKE ? ORDER BY created_at DESC LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            )

    async def delete(self, document_id: str) -> dict[str, Any] | None:
        row = await self.get(document_id)
        if row:
            await self.db.execute("DELETE FROM documents WHERE id=?", (document_id,))
        return row
