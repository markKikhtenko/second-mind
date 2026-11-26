from __future__ import annotations

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


def test_document_upload_indexes_real_text(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/documents",
            files={"file": ("notes.txt", b"local searchable knowledge", "text/plain")},
        )
        assert response.status_code == 201
        results = client.get("/api/documents/search", params={"q": "searchable"}).json()
        assert results[0]["filename"] == "notes.txt"

