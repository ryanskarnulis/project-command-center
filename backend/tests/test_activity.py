from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import InboxItem, TaskStatus
from app.schemas.inbox import ReviewDecision, ReviewEdit
from app.services import activity as activity_service
from app.services import projects as projects_service
from app.services import review as review_service
from app.services import tasks as tasks_service


def test_record_and_list_events_newest_first_and_limit(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Net")
    # create_project already logged one event; add two more explicitly.
    activity_service.record_event(
        db_session,
        project_id=project.id,
        entity_type="task",
        entity_id=1,
        action="created",
        summary="first",
    )
    activity_service.record_event(
        db_session,
        project_id=project.id,
        entity_type="task",
        entity_id=2,
        action="updated",
        summary="second",
    )

    events = activity_service.list_events(db_session, project.id)
    # Newest first.
    assert [e.summary for e in events[:2]] == ["second", "first"]

    limited = activity_service.list_events(db_session, project.id, limit=1)
    assert len(limited) == 1
    assert limited[0].summary == "second"


def test_list_events_filters_by_project(db_session: Session) -> None:
    a = projects_service.create_project(db_session, name="A")
    b = projects_service.create_project(db_session, name="B")

    a_events = activity_service.list_events(db_session, a.id)
    assert all(e.project_id == a.id for e in a_events)
    assert b.id not in [e.project_id for e in a_events]


def test_project_lifecycle_emits_events(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    projects_service.update_project(db_session, project, {"description": "x"})
    projects_service.soft_delete_project(db_session, project)

    actions = [e.action for e in activity_service.list_events(db_session, project.id)]
    # Newest first: deleted, updated, created.
    assert actions == ["deleted", "updated", "created"]


def test_task_lifecycle_emits_events_only_with_project(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Home")

    # Candidate with no project: no task event.
    candidate = tasks_service.create_task(
        db_session, project_id=None, title="loose candidate"
    )
    assert candidate.project_id is None
    task_events = [
        e
        for e in activity_service.list_events(db_session, project.id)
        if e.entity_type == "task"
    ]
    assert task_events == []

    # Accepting the candidate (review sets project_id) logs an "updated" event.
    tasks_service.update_task(db_session, candidate, {"project_id": project.id})
    tasks_service.mark_done(db_session, candidate)
    tasks_service.soft_delete_task(db_session, candidate)

    task_actions = [
        e.action
        for e in activity_service.list_events(db_session, project.id)
        if e.entity_type == "task"
    ]
    # Newest first: deleted, completed, updated. (No "created" — it had no project.)
    assert task_actions == ["deleted", "completed", "updated"]


def test_created_task_with_project_emits_created_event(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Direct")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="direct task"
    )

    task_events = [
        e
        for e in activity_service.list_events(db_session, project.id)
        if e.entity_type == "task" and e.entity_id == task.id
    ]
    assert [e.action for e in task_events] == ["created"]
    assert task_events[0].summary == 'Task "direct task" created'


def test_review_accept_emits_task_created_event(db_session: Session) -> None:
    # The AI path: a candidate accepted into a project at review must appear in
    # the feed even though review commits in bulk (not via tasks_service).
    project = projects_service.create_project(db_session, name="Filed")
    item = InboxItem(raw_text="messy note", input_hash="hash-1", summary="s")
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    candidate = tasks_service.create_task(
        db_session,
        project_id=None,
        title="from inbox",
        status=TaskStatus.candidate,
        inbox_item_id=item.id,
    )

    review_service.review_inbox(
        db_session,
        item,
        [
            ReviewDecision(
                task_id=candidate.id,
                action="accept",
                edits=ReviewEdit(project_id=project.id),
            )
        ],
    )

    task_events = [
        e
        for e in activity_service.list_events(db_session, project.id)
        if e.entity_type == "task"
    ]
    assert [e.action for e in task_events] == ["created"]
    assert task_events[0].summary == 'Task "from inbox" created'


def test_review_reject_emits_no_task_event(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Empty")
    item = InboxItem(raw_text="note", input_hash="hash-2")
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    candidate = tasks_service.create_task(
        db_session,
        project_id=None,
        title="rejected",
        status=TaskStatus.candidate,
        inbox_item_id=item.id,
    )

    review_service.review_inbox(
        db_session,
        item,
        [ReviewDecision(task_id=candidate.id, action="reject")],
    )

    task_events = [
        e
        for e in activity_service.list_events(db_session, project.id)
        if e.entity_type == "task"
    ]
    assert task_events == []


def test_activity_route_returns_events_and_404(client: TestClient) -> None:
    project_id = client.post("/api/projects", json={"name": "Routed"}).json()["id"]

    resp = client.get(f"/api/projects/{project_id}/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert any(e["action"] == "created" and e["entity_type"] == "project" for e in body)

    assert client.get("/api/projects/9999/activity").status_code == 404
