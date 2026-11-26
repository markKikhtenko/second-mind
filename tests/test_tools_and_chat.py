from __future__ import annotations

import json
from typing import Any

import pytest

from second_mind.container import Container
from second_mind.tools import ToolContext


class MultiToolLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "task-call",
                        "type": "function",
                        "function": {
                            "name": "create_task",
                            "arguments": json.dumps({"title": "Buy milk"}),
                        },
                    },
                    {
                        "id": "memory-call",
                        "type": "function",
                        "function": {
                            "name": "save_memory",
                            "arguments": json.dumps(
                                {"content": "User drinks oat milk", "kind": "preference"}
                            ),
                        },
                    },
                ],
            }
        return {"content": "Created the task and saved the preference.", "tool_calls": []}


@pytest.mark.asyncio
async def test_chat_executes_multiple_actions(container: Container) -> None:
    await container.db.initialize()
    fake = MultiToolLLM()
    container.chat.llm = fake  # type: ignore[assignment]
    response = await container.chat.reply("Buy milk and remember I drink oat milk")

    assert fake.calls == 2
    assert [event.name for event in response.tool_events] == ["create_task", "save_memory"]
    assert (await container.tasks.list())[0]["title"] == "Buy milk"
    assert (await container.memories.search("oat milk"))[0]["kind"] == "preference"


@pytest.mark.asyncio
async def test_tool_validation_and_external_permission(container: Container) -> None:
    await container.db.initialize()
    invalid = await container.tools.execute(
        "create_task", {"title": ""}, ToolContext(transport="test")
    )
    assert invalid["ok"] is False

    denied = await container.tools.execute(
        "send_telegram_message",
        {"user_id": "123", "text": "hello"},
        ToolContext(transport="test", allow_external=False),
    )
    assert denied == {"ok": False, "error": "External action requires explicit user confirmation"}


@pytest.mark.asyncio
async def test_inbox_is_idempotent(container: Container) -> None:
    await container.db.initialize()
    first, created = await container.inbox.enqueue("telegram", "42", "7", {"text": "hi"})
    second, created_again = await container.inbox.enqueue("telegram", "42", "7", {"text": "hi"})
    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]

