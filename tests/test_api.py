from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from second_mind.api import create_app
from second_mind.container import Container


def test_api_crud_and_health(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        created = client.post("/api/tasks", json={"title": "API task"})
        assert created.status_code == 201
        task = created.json()

        listed = client.get("/api/tasks").json()
        assert [item["id"] for item in listed] == [task["id"]]

        memory = client.post(
            "/api/memories", json={"content": "A source-aware fact", "source": "test"}
        )
        assert memory.status_code == 201
        assert client.get("/api/memories", params={"q": "source"}).json()

        health = client.get("/api/health")
        assert health.status_code == 200
        payload = health.json()
        assert payload["core"]["available"] is True
        assert payload["open_tasks"] == 1
        ping = client.get("/api/ping").json()
        assert ping["version"] == payload["core"]["version"]
        assert ping["pid"] > 0


def test_document_upload_indexes_real_text(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/documents",
            files={"file": ("notes.txt", b"local searchable knowledge", "text/plain")},
        )
        assert response.status_code == 201
        results = client.get("/api/documents/search", params={"q": "searchable"}).json()
        assert results[0]["filename"] == "notes.txt"


def test_storage_api_reports_and_backs_up_real_database(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        client.post("/api/tasks", json={"title": "Stored task"})
        status = client.get("/api/storage")
        assert status.status_code == 200
        assert status.json()["counts"]["tasks"] == 1
        assert status.json()["integrity"]["ok"] is True

        backup = client.post("/api/storage/backup")
        assert backup.status_code == 200
        assert container.settings.backups_dir in Path(backup.json()["path"]).parents

        changed = client.patch(
            "/api/settings",
            json={
                "automatic_backups": False,
                "backup_interval_hours": 12,
                "backup_retention_days": 90,
            },
        )
        assert changed.status_code == 200
        assert changed.json()["backup_retention_days"] == 90
