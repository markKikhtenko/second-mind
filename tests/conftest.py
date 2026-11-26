from __future__ import annotations

from pathlib import Path

import pytest

from second_mind.config import AppSettings, ConfigManager, MemorySecretStore
from second_mind.container import Container


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = AppSettings(data_dir=tmp_path)
    manager = ConfigManager(tmp_path / "config.json")
    return Container(
        settings=settings,
        config_manager=manager,
        secret_store=MemorySecretStore(),
    )

