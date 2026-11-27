from __future__ import annotations

import os

import pytest

from second_mind.config import AppSettings, MemorySecretStore
from second_mind.llm import LMStudioProvider

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("SECOND_MIND_RUN_LLM_INTEGRATION") != "1",
    reason="Set SECOND_MIND_RUN_LLM_INTEGRATION=1 only with a real loaded local model",
)
async def test_real_local_lm_studio_chat() -> None:
    provider = LMStudioProvider(AppSettings(), MemorySecretStore())
    health = await provider.health()
    assert health["available"] is True
    response = await provider.complete(
        [{"role": "user", "content": "Ответь одним словом: готов"}],
        [],
        max_tokens=16,
        timeout_seconds=60,
    )
    assert str(response.get("content") or "").strip()
