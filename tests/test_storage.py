from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from second_mind.container import Container
from second_mind.schemas import MemoryCreate, MemoryUpdate
from second_mind.storage import StorageError


@pytest.mark.asyncio
async def test_backup_export_and_restore_are_real(container: Container, tmp_path: Path) -> None:
    await container.db.initialize()
    container.save_settings()
    memory = await container.memories.create(
        MemoryCreate(content="Original local fact", source="test")
    )
    container.settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    (container.settings.attachments_dir / "sample.txt").write_text(
        "attachment", encoding="utf-8"
    )

    backup = await container.storage.create_backup()
    assert backup.is_file()
    with zipfile.ZipFile(backup) as bundle:
        assert {"second-mind.db", "config.json", "backup-manifest.json"} <= set(
            bundle.namelist()
        )
        manifest = json.loads(bundle.read("backup-manifest.json"))
        assert manifest["secrets_included"] is False

    await container.memories.update(
        memory["id"], MemoryUpdate(content="Changed local fact")
    )
    safety = await container.storage.restore(backup, confirmed=True)
    assert safety.is_file()
    restored = await container.memories.get(memory["id"])
    assert restored and restored["content"] == "Original local fact"

    exported = await container.storage.export_json(tmp_path / "portable.json")
    payload = json.loads(exported.read_text(encoding="utf-8"))
    assert payload["secrets_included"] is False
    assert payload["data"]["memories"][0]["content"] == "Original local fact"


@pytest.mark.asyncio
async def test_storage_status_cleanup_and_move(container: Container, tmp_path: Path) -> None:
    await container.db.initialize()
    container.save_settings()
    temp_dir = container.settings.data_dir / "temp"
    temp_dir.mkdir()
    (temp_dir / "discard.tmp").write_bytes(b"temporary")
    status = await container.storage.status()
    assert status["schema_version"] == 1
    assert status["integrity"]["ok"] is True
    assert status["counts"]["tasks"] == 0

    cleaned = await container.storage.cleanup(confirmed=True)
    assert cleaned["files"] == 1
    assert not temp_dir.exists()

    with pytest.raises(StorageError, match="внутри текущей папки"):
        await container.storage.move(tmp_path / "nested", confirmed=True)

    destination = tmp_path.parent / f"{tmp_path.name}-moved"
    try:
        old, new = await container.storage.move(destination, confirmed=True)
        assert old == tmp_path.resolve()
        assert new == destination.resolve()
        assert container.db.path == destination / "second-mind.db"
        assert (destination / "second-mind.db").is_file()
        assert not (tmp_path / "second-mind.db").exists()
    finally:
        shutil.rmtree(destination, ignore_errors=True)
