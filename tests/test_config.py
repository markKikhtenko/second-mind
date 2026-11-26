from __future__ import annotations

import pytest
from pydantic import ValidationError

from second_mind.config import AppSettings


def test_core_rejects_non_loopback_bind() -> None:
    with pytest.raises(ValidationError):
        AppSettings(api_host="0.0.0.0")


def test_external_llm_requires_a_future_explicit_adapter() -> None:
    with pytest.raises(ValidationError):
        AppSettings(llm_base_url="https://example.com/v1")

