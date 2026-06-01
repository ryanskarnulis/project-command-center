from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai import gateway
from app.db.models import InboxItem, InboxSource, Project, Task, TaskPriority, TaskStatus
from app.services.common import active


def _create_project(db: Session, name: str) -> Project:
    p = Project(name=name)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _create_task(db: Session, *, project_id: int, title: str, status: TaskStatus) -> Task:
    t = Task(project_id=project_id, title=title, status=status, priority=TaskPriority.medium)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _create_inbox(db: Session, raw_text: str) -> InboxItem:
    item = InboxItem(
        raw_text=raw_text,
        input_hash=raw_text,  # good enough for tests
        source=InboxSource.web,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


class TestGetDashboard:
    def test_empty_db(self, client: TestClient) -> None:
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_open_tasks"] == 0
        assert data["projects"] == []
        assert data["recent_inbox"] == []

    def test_counts_accepted_tasks_only(
        self, client: TestClient, db_session: Session
    ) -> None:
        p = _create_project(db_session, "Alpha")
        _create_task(db_session, project_id=p.id, title="Open one", status=TaskStatus.accepted)
        _create_task(db_session, project_id=p.id, title="Done one", status=TaskStatus.done)
        _create_task(db_session, project_id=p.id, title="Candidate", status=TaskStatus.candidate)

        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_open_tasks"] == 1
        assert data["projects"][0]["open_task_count"] == 1

    def test_recent_inbox_returned(
        self, client: TestClient, db_session: Session
    ) -> None:
        _create_inbox(db_session, "first note")
        _create_inbox(db_session, "second note")

        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        assert len(resp.json()["recent_inbox"]) == 2


class TestGetProjectSummary:
    def test_404_for_missing_project(self, client: TestClient) -> None:
        resp = client.get("/api/projects/9999/summary")
        assert resp.status_code == 404

    def test_returns_summary_text(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(gateway, "complete", lambda **_: "Two tasks are in flight.")
        p = _create_project(db_session, "Beta")
        _create_task(db_session, project_id=p.id, title="Task A", status=TaskStatus.accepted)

        resp = client.get(f"/api/projects/{p.id}/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == p.id
        assert data["summary"] == "Two tasks are in flight."
        assert "model_name" in data

    def test_502_on_gateway_error(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(**_: object) -> str:
            raise RuntimeError("ollama not running")

        monkeypatch.setattr(gateway, "complete", boom)
        p = _create_project(db_session, "Gamma")

        resp = client.get(f"/api/projects/{p.id}/summary")
        assert resp.status_code == 502

    def test_excludes_non_accepted_tasks(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen_content: list[str] = []

        def capture(**kwargs: object) -> str:
            seen_content.append(str(kwargs.get("user_content", "")))
            return "Summary."

        monkeypatch.setattr(gateway, "complete", capture)
        p = _create_project(db_session, "Delta")
        _create_task(db_session, project_id=p.id, title="Accepted task", status=TaskStatus.accepted)
        _create_task(db_session, project_id=p.id, title="Done task", status=TaskStatus.done)

        client.get(f"/api/projects/{p.id}/summary")
        assert seen_content, "gateway.complete was not called"
        assert "Accepted task" in seen_content[0]
        assert "Done task" not in seen_content[0]
