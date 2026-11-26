from __future__ import annotations

from datetime import UTC, datetime

import pytest

from second_mind.container import Container
from second_mind.schemas import (
    MemoryCreate,
    MemoryUpdate,
    ProjectCreate,
    ReminderCreate,
    TaskCreate,
    TaskUpdate,
)


@pytest.mark.asyncio
async def test_task_lifecycle(container: Container) -> None:
    await container.db.initialize()
    task = await container.tasks.create(TaskCreate(title="Prepare release", priority=2))
    assert task["status"] == "open"

    updated = await container.tasks.update(task["id"], TaskUpdate(status="completed"))
    assert updated is not None
    assert updated["status"] == "completed"
    assert len(await container.tasks.list("completed")) == 1


@pytest.mark.asyncio
async def test_memory_deduplicates_and_searches(container: Container) -> None:
    await container.db.initialize()
    first = await container.memories.create(
        MemoryCreate(content="User prefers dark mode", source="test")
    )
    duplicate = await container.memories.create(
        MemoryCreate(content="  user PREFERS   dark mode ", source="test-2")
    )
    assert first["id"] == duplicate["id"]

    found = await container.memories.search("dark mode")
    assert [item["id"] for item in found] == [first["id"]]
    changed = await container.memories.update(
        first["id"], MemoryUpdate(content="User prefers the light theme")
    )
    assert changed is not None
    assert "light" in changed["content"]


@pytest.mark.asyncio
async def test_projects_are_real_persistent_records(container: Container) -> None:
    await container.db.initialize()
    project = await container.projects.create(ProjectCreate(name="Second Mind"))
    assert project["name"] == "Second Mind"
    assert (await container.projects.list())[0]["id"] == project["id"]


@pytest.mark.asyncio
async def test_reminder_is_normalized_to_utc(container: Container) -> None:
    await container.db.initialize()
    reminder = await container.reminders.create(
        ReminderCreate(text="Review", due_at=datetime(2030, 1, 1, 10, tzinfo=UTC))
    )
    assert reminder["due_at"].endswith("+00:00")
