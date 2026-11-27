from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from second_mind.agent_engine import AgentEngineStore
from second_mind.config import AppSettings
from second_mind.db import MIGRATIONS, Database


@pytest.mark.asyncio
async def test_v030_database_upgrades_without_losing_user_data(tmp_path: Path) -> None:
    database_path = tmp_path / "second-mind.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(MIGRATIONS[0][1])
    connection.execute("INSERT INTO schema_migrations(version) VALUES(1)")
    connection.execute(
        """INSERT INTO tasks(id,title,description,status,priority,source,created_at,updated_at)
        VALUES('old-task','Данные v0.3','не потерять','open',2,'user','2026-11-26','2026-11-26')"""
    )
    connection.commit()
    connection.close()

    database = Database(database_path)
    await database.initialize()
    settings = AppSettings(data_dir=tmp_path)
    engine = AgentEngineStore(database, settings)
    await engine.initialize()

    task = await database.fetch_one("SELECT * FROM tasks WHERE id='old-task'")
    versions = await database.fetch_all("SELECT version FROM schema_migrations ORDER BY version")
    assert task and task["title"] == "Данные v0.3"
    assert [row["version"] for row in versions] == [1, 2]
    assert len(await engine.list_agents()) == 6
    assert len(await engine.list_skills()) == 19
