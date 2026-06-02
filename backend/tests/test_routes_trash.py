import json

import pytest
from fastapi.testclient import TestClient

from app.ai import gateway

_VALID_OUTPUT = {
    "summary": "One task.",
    "project_hint": None,
    "tasks": [
        {
            "title": "Do the thing",
            "description": None,
            "due_date": None,
            "priority": "medium",
            "assignee_hint": None,
            "confidence": 0.8,
        }
    ],
    "needs_review": False,
}


def test_trash_empty_by_default(client: TestClient) -> None:
    body = client.get("/api/trash").json()
    assert body == {"projects": [], "tasks": [], "inbox_items": []}


def test_project_delete_appears_in_trash_and_restores(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 204

    trash = client.get("/api/trash").json()
    assert [p["id"] for p in trash["projects"]] == [pid]
    assert client.get(f"/api/projects/{pid}").status_code == 404  # gone from active

    restored = client.post(f"/api/projects/{pid}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == pid

    assert pid in {p["id"] for p in client.get("/api/projects").json()}  # active again
    assert client.get("/api/trash").json()["projects"] == []  # gone from trash


def test_restore_unknown_project_404(client: TestClient) -> None:
    assert client.post("/api/projects/424242/restore").status_code == 404


def test_task_delete_appears_in_trash_and_restores(client: TestClient) -> None:
    tid = client.post("/api/tasks", json={"title": "Pay invoice"}).json()["id"]
    assert client.delete(f"/api/tasks/{tid}").status_code == 204

    trash = client.get("/api/trash").json()
    assert [t["id"] for t in trash["tasks"]] == [tid]

    restored = client.post(f"/api/tasks/{tid}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == tid
    assert client.get("/api/trash").json()["tasks"] == []
    assert client.get(f"/api/tasks/{tid}").status_code == 200


def test_inbox_dismiss_appears_in_trash_and_restores(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    inbox_id = client.post("/api/inbox", json={"raw_text": "restore me"}).json()["id"]
    client.post(f"/api/inbox/{inbox_id}/process")
    assert client.delete(f"/api/inbox/{inbox_id}").status_code == 204

    trash = client.get("/api/trash").json()
    assert [i["id"] for i in trash["inbox_items"]] == [inbox_id]

    restored = client.post(f"/api/inbox/{inbox_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == inbox_id
    assert client.get("/api/trash").json()["inbox_items"] == []
    assert client.get(f"/api/inbox/{inbox_id}").status_code == 200


def test_restore_inbox_conflicts_when_text_recaptured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    first_id = client.post("/api/inbox", json={"raw_text": "same text"}).json()["id"]
    assert client.delete(f"/api/inbox/{first_id}").status_code == 204

    # The same text is captured again → a new active item owns the hash now.
    second_id = client.post("/api/inbox", json={"raw_text": "same text"}).json()["id"]
    assert second_id != first_id

    # Restoring the dismissed original would violate the active-hash uniqueness.
    conflict = client.post(f"/api/inbox/{first_id}/restore")
    assert conflict.status_code == 409
    # The original stays dismissed; the active copy is untouched.
    assert client.get(f"/api/inbox/{first_id}").status_code == 404
    assert client.get(f"/api/inbox/{second_id}").status_code == 200


def test_restore_unknown_inbox_404(client: TestClient) -> None:
    assert client.post("/api/inbox/424242/restore").status_code == 404
