from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from .container import Container
from .tools import ToolContext

mcp = FastMCP("Second Mind", instructions="Local tasks, memory, notes, reminders and documents")
_container: Container | None = None
_lock = asyncio.Lock()


async def container() -> Container:
    global _container
    if _container is None:
        async with _lock:
            if _container is None:
                value = Container()
                await value.db.initialize()
                _container = value
    return _container


async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    value = await container()
    return await value.tools.execute(
        name,
        arguments,
        ToolContext(transport="mcp", allow_external=name == "send_telegram_message"),
    )


@mcp.tool()
async def create_task(
    title: str,
    description: str = "",
    priority: int = 0,
    project_id: str | None = None,
    due_at: str | None = None,
) -> dict[str, Any]:
    """Create an actionable task."""
    return await execute("create_task", locals())


@mcp.tool()
async def update_task(
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_at: str | None = None,
) -> dict[str, Any]:
    """Update an existing task."""
    return await execute("update_task", locals())


@mcp.tool()
async def get_tasks(status: str | None = None, limit: int = 20) -> dict[str, Any]:
    """List tasks, optionally filtered by status."""
    return await execute("get_tasks", locals())


@mcp.tool()
async def complete_task(task_id: str) -> dict[str, Any]:
    """Mark a task completed."""
    return await execute("complete_task", locals())


@mcp.tool()
async def save_memory(
    content: str, kind: str = "fact", source_ref: str | None = None
) -> dict[str, Any]:
    """Save a durable fact, preference, entity or summary."""
    return await execute("save_memory", locals())


@mcp.tool()
async def search_memory(query: str = "", kind: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Search local long-term memory."""
    return await execute("search_memory", locals())


@mcp.tool()
async def update_memory(
    memory_id: str, content: str | None = None, kind: str | None = None
) -> dict[str, Any]:
    """Correct an existing memory."""
    return await execute("update_memory", locals())


@mcp.tool()
async def create_note(content: str, source_ref: str | None = None) -> dict[str, Any]:
    """Create a local note."""
    return await execute("create_note", locals())


@mcp.tool()
async def search_notes(query: str = "", limit: int = 10) -> dict[str, Any]:
    """Search local notes."""
    return await execute("search_notes", {"query": query, "kind": "note", "limit": limit})


@mcp.tool()
async def create_reminder(
    text: str,
    due_at: str,
    target_transport: str = "desktop",
    target_user_id: str | None = None,
) -> dict[str, Any]:
    """Create a scheduled reminder."""
    return await execute("create_reminder", locals())


@mcp.tool()
async def get_projects(limit: int = 50) -> dict[str, Any]:
    """List local projects."""
    return await execute("get_projects", locals())


@mcp.tool()
async def search_documents(query: str, limit: int = 10) -> dict[str, Any]:
    """Search text extracted from local documents."""
    return await execute("search_documents", locals())


@mcp.tool()
async def send_telegram_message(user_id: str, text: str) -> dict[str, Any]:
    """Send a message to an allowlisted Telegram user."""
    return await execute("send_telegram_message", locals())


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
