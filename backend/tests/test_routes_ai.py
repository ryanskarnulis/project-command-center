from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.ai import gateway
from app.db.models import InboxItem, InboxSource, Project, Task, TaskPriority, TaskStatus
from app.services import dashboard as dashboard_service
from app.services import projects as projects_service
from app.services.common import soft_delete


def _create_project(db: Session, name: str) -> Project:
    p = Project(name=name)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _create_task(
    db: Session,
    *,
    project_id: int | None,
    title: str,
    status: TaskStatus,
    inbox_item_id: int | None = None,
) -> Task:
    t = Task(
        project_id=project_id,
        inbox_item_id=inbox_item_id,
        title=title,
        status=status,
        priority=TaskPriority.medium,
    )
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


def _project_rows_by_name(data: dict[str, object]) -> dict[str, int]:
    rows = data["projects"]
    assert isinstance(rows, list)
    return {
        str(row["project_name"]): int(row["open_task_count"])
        for row in rows
        if isinstance(row, dict)
    }


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
        deleted = _create_task(
            db_session,
            project_id=p.id,
            title="Deleted open task",
            status=TaskStatus.accepted,
        )
        soft_delete(deleted)
        db_session.commit()
        _create_task(
            db_session, project_id=p.id, title="Open one", status=TaskStatus.accepted
        )
        _create_task(db_session, project_id=p.id, title="Done one", status=TaskStatus.done)
        _create_task(db_session, project_id=p.id, title="Candidate", status=TaskStatus.candidate)
        _create_task(db_session, project_id=p.id, title="Rejected", status=TaskStatus.rejected)

        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_open_tasks"] == 1
        assert data["projects"][0]["open_task_count"] == 1

    def test_empty_projects_are_included_with_zero_counts(
        self, client: TestClient, db_session: Session
    ) -> None:
        _create_project(db_session, "Empty")

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        assert _project_rows_by_name(resp.json()) == {"Empty": 0}

    def test_soft_deleted_projects_are_excluded(
        self, client: TestClient, db_session: Session
    ) -> None:
        active_project = _create_project(db_session, "Active")
        deleted_project = _create_project(db_session, "Deleted")
        soft_delete(deleted_project)
        db_session.commit()
        _create_task(
            db_session,
            project_id=active_project.id,
            title="Open one",
            status=TaskStatus.accepted,
        )

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        assert _project_rows_by_name(resp.json()) == {"Active": 1}

    def test_recent_inbox_returned_newest_first_and_ignores_deleted(
        self, client: TestClient, db_session: Session
    ) -> None:
        first = _create_inbox(db_session, "first note")
        deleted = _create_inbox(db_session, "deleted note")
        second = _create_inbox(db_session, "second note")
        soft_delete(deleted)
        db_session.commit()

        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        recent_ids = [item["id"] for item in resp.json()["recent_inbox"]]
        assert recent_ids == [second.id, first.id]

    def test_recent_inbox_resolution_prefers_accepted_task_project(
        self, client: TestClient, db_session: Session
    ) -> None:
        suggested = _create_project(db_session, "Suggested")
        accepted = _create_project(db_session, "Accepted")
        item = _create_inbox(db_session, "reviewed note")
        item.suggested_project_id = suggested.id
        db_session.commit()
        _create_task(
            db_session,
            project_id=accepted.id,
            inbox_item_id=item.id,
            title="Accepted destination",
            status=TaskStatus.accepted,
        )

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        assert resp.json()["recent_inbox"][0]["resolved_project_id"] == accepted.id

    def test_recent_inbox_uses_active_suggestion_fallback(
        self, client: TestClient, db_session: Session
    ) -> None:
        active_project = _create_project(db_session, "Active suggestion")
        deleted_project = _create_project(db_session, "Deleted suggestion")
        active_item = _create_inbox(db_session, "active suggestion note")
        deleted_item = _create_inbox(db_session, "deleted suggestion note")
        active_item.suggested_project_id = active_project.id
        deleted_item.suggested_project_id = deleted_project.id
        soft_delete(deleted_project)
        db_session.commit()

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        resolved_by_id = {
            item["id"]: item["resolved_project_id"]
            for item in resp.json()["recent_inbox"]
        }
        assert resolved_by_id[active_item.id] == active_project.id
        assert resolved_by_id[deleted_item.id] is None

    def test_recent_inbox_resolution_ignores_soft_deleted_tasks(
        self, client: TestClient, db_session: Session
    ) -> None:
        project = _create_project(db_session, "Filed")
        item = _create_inbox(db_session, "deleted task note")
        task = _create_task(
            db_session,
            project_id=project.id,
            inbox_item_id=item.id,
            title="Deleted accepted task",
            status=TaskStatus.accepted,
        )
        soft_delete(task)
        db_session.commit()

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        assert resp.json()["recent_inbox"][0]["resolved_project_id"] is None

    def test_recent_inbox_resolution_tie_uses_earliest_task(
        self, client: TestClient, db_session: Session
    ) -> None:
        first_project = _create_project(db_session, "First")
        second_project = _create_project(db_session, "Second")
        item = _create_inbox(db_session, "split project note")
        _create_task(
            db_session,
            project_id=first_project.id,
            inbox_item_id=item.id,
            title="First task",
            status=TaskStatus.accepted,
        )
        _create_task(
            db_session,
            project_id=second_project.id,
            inbox_item_id=item.id,
            title="Second task",
            status=TaskStatus.accepted,
        )

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        assert resp.json()["recent_inbox"][0]["resolved_project_id"] == first_project.id

    def test_overview_query_count_is_bounded(
        self, db_session: Session, test_engine: Engine
    ) -> None:
        projects = [_create_project(db_session, f"Project {i}") for i in range(8)]
        for project in projects:
            _create_task(
                db_session,
                project_id=project.id,
                title=f"Open {project.id}",
                status=TaskStatus.accepted,
            )
        for i in range(12):
            item = _create_inbox(db_session, f"note {i}")
            item.suggested_project_id = projects[i % len(projects)].id
            if i % 2 == 0:
                _create_task(
                    db_session,
                    project_id=projects[(i + 1) % len(projects)].id,
                    inbox_item_id=item.id,
                    title=f"Reviewed {i}",
                    status=TaskStatus.accepted,
                )
            db_session.commit()

        statements: list[str] = []

        def count_statement(
            _conn: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(test_engine, "before_cursor_execute", count_statement)
        try:
            dashboard_service.get_overview(db_session)
        finally:
            event.remove(test_engine, "before_cursor_execute", count_statement)

        assert len(statements) <= 5

    def test_counts_stay_reachable_after_project_delete(
        self, db_session: Session
    ) -> None:
        p = _create_project(db_session, "Alpha")
        _create_task(db_session, project_id=p.id, title="Open one", status=TaskStatus.accepted)

        projects_service.soft_delete_project(db_session, p)
        db_session.commit()

        total, per_project, _recent = dashboard_service.get_overview(db_session)
        assert total == 1
        assert sum(count for _project, count in per_project) == 1
        assert per_project[0][0].name == "General"


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
