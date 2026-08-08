from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import (
    Project,
    Task,
    TaskPriority,
    TaskWorkflowStatus,
)
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
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open,
) -> Task:
    t = Task(
        project_id=project_id,
        title=title,
        workflow_status=workflow_status,
        priority=TaskPriority.medium,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


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

    def test_counts_open_active_tasks_only(
        self, client: TestClient, db_session: Session
    ) -> None:
        p = _create_project(db_session, "Alpha")
        deleted = _create_task(
            db_session,
            project_id=p.id,
            title="Deleted open task",
        )
        soft_delete(deleted)
        db_session.commit()
        _create_task(db_session, project_id=p.id, title="Open one")
        _create_task(
            db_session,
            project_id=p.id,
            title="Done one",
            workflow_status=TaskWorkflowStatus.done,
        )

        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_open_tasks"] == 1
        assert data["projects"][0]["open_task_count"] == 1
        # The done half is the complement of the open half over the same active
        # set, so the trashed task is absent from both.
        assert data["projects"][0]["done_task_count"] == 1

    def test_empty_projects_are_included_with_zero_counts(
        self, client: TestClient, db_session: Session
    ) -> None:
        _create_project(db_session, "Empty")

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        assert _project_rows_by_name(resp.json()) == {"Empty": 0}

    def test_done_counts_are_scoped_to_their_own_project(
        self, client: TestClient, db_session: Session
    ) -> None:
        # The lane progress bar reads done / (open + done), so a done task must
        # land on its own project's row and nowhere else.
        alpha = _create_project(db_session, "Alpha")
        beta = _create_project(db_session, "Beta")
        _create_task(db_session, project_id=alpha.id, title="Alpha open")
        for title in ("Alpha done 1", "Alpha done 2"):
            _create_task(
                db_session,
                project_id=alpha.id,
                title=title,
                workflow_status=TaskWorkflowStatus.done,
            )
        _create_task(db_session, project_id=beta.id, title="Beta open")
        # Unfiled work belongs to no lane at all.
        _create_task(
            db_session,
            project_id=None,
            title="Unfiled done",
            workflow_status=TaskWorkflowStatus.done,
        )

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        rows = {row["project_name"]: row for row in resp.json()["projects"]}
        assert (rows["Alpha"]["open_task_count"], rows["Alpha"]["done_task_count"]) == (
            1,
            2,
        )
        assert (rows["Beta"]["open_task_count"], rows["Beta"]["done_task_count"]) == (
            1,
            0,
        )

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
        )

        resp = client.get("/api/dashboard")

        assert resp.status_code == 200
        assert _project_rows_by_name(resp.json()) == {"Active": 1}

    def test_overview_query_count_is_bounded(
        self, db_session: Session, test_engine: Engine
    ) -> None:
        projects = [_create_project(db_session, f"Project {i}") for i in range(8)]
        for project in projects:
            _create_task(
                db_session,
                project_id=project.id,
                title=f"Open {project.id}",
            )

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

    def test_deleting_a_project_removes_its_tasks_from_counts(
        self, db_session: Session
    ) -> None:
        # Tasks are cascade-trashed with their project, so they drop out of the
        # dashboard open-task counts (no longer rehomed to General).
        p = _create_project(db_session, "Alpha")
        _create_task(db_session, project_id=p.id, title="Open one")

        projects_service.soft_delete_project(db_session, p)
        db_session.commit()

        total, per_project = dashboard_service.get_overview(db_session)
        assert total == 0
        assert sum(row.open_count for row in per_project) == 0
        assert sum(row.done_count for row in per_project) == 0
