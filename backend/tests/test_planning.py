from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import TaskReviewStatus, TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service
from app.services.common import soft_delete


def _project(db: Session, name: str) -> int:
    project = projects_service.create_project(db, name=name, description=None)
    db.commit()
    return project.id


def _task(db: Session, project_id: int | None, title: str, **kwargs: object) -> int:
    # ``scheduled_start`` is not a create_task arg this slice (set only via PATCH);
    # stamp it directly so the planning read can be exercised.
    scheduled_start = kwargs.pop("scheduled_start", None)
    task = tasks_service.create_task(db, project_id=project_id, title=title, **kwargs)
    if scheduled_start is not None:
        task.scheduled_start = scheduled_start  # type: ignore[assignment]
    db.commit()
    return task.id


def test_gantt_returns_accepted_not_done_for_project(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    other = _project(db_session, "Other")

    placed = _task(
        db_session,
        project,
        "scheduled",
        scheduled_start=date(2026, 6, 20),
        estimated_minutes=480,
    )
    due_only = _task(db_session, project, "due only", due_date=date(2026, 6, 25))
    _task(
        db_session,
        project,
        "done work",
        workflow_status=TaskWorkflowStatus.done,
    )
    _task(
        db_session,
        project,
        "candidate",
        review_status=TaskReviewStatus.candidate,
    )
    _task(db_session, other, "other project task", due_date=date(2026, 6, 22))

    response = client.get(f"/api/projects/{project}/gantt")

    assert response.status_code == 200
    body = response.json()
    assert sorted(t["id"] for t in body["tasks"]) == sorted([placed, due_only])
    placed_read = next(t for t in body["tasks"] if t["id"] == placed)
    assert placed_read["scheduled_start"] == "2026-06-20"


def test_gantt_excludes_subtasks_keeps_parent(
    client: TestClient, db_session: Session
) -> None:
    # Subtasks roll up into their parent, so only the top-level parent bar should
    # appear on the timeline — the child is excluded from the payload.
    project = _project(db_session, "Rollup")
    parent = _task(
        db_session,
        project,
        "parent",
        scheduled_start=date(2026, 6, 20),
    )
    _task(
        db_session,
        project,
        "child",
        scheduled_start=date(2026, 6, 21),
        parent_task_id=parent,
    )

    response = client.get(f"/api/projects/{project}/gantt")

    assert response.status_code == 200
    assert [t["id"] for t in response.json()["tasks"]] == [parent]


def test_global_gantt_excludes_subtasks(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Rollup")
    parent = _task(db_session, project, "parent", scheduled_start=date(2026, 6, 20))
    _task(
        db_session,
        project,
        "child",
        scheduled_start=date(2026, 6, 21),
        parent_task_id=parent,
    )

    response = client.get("/api/planning/gantt")

    assert response.status_code == 200
    assert [t["id"] for t in response.json()["tasks"]] == [parent]


def test_gantt_excludes_soft_deleted(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    kept = _task(db_session, project, "kept", due_date=date(2026, 6, 25))
    trashed = _task(db_session, project, "trashed", due_date=date(2026, 6, 26))
    task = tasks_service.get_task(db_session, trashed)
    assert task is not None
    soft_delete(task)
    db_session.commit()

    response = client.get(f"/api/projects/{project}/gantt")

    assert response.status_code == 200
    assert [t["id"] for t in response.json()["tasks"]] == [kept]


def test_gantt_returns_edges_only_between_payload_tasks(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    a = _task(db_session, project, "a", due_date=date(2026, 6, 20))
    b = _task(db_session, project, "b", due_date=date(2026, 6, 21))
    done = _task(
        db_session, project, "done", workflow_status=TaskWorkflowStatus.done
    )
    deps_service.add_dependency(db_session, a, b)
    # Edge to a done task: done is filtered out of the payload, so this edge must
    # not be returned (no bar to attach to).
    deps_service.add_dependency(db_session, a, done)
    db_session.commit()

    response = client.get(f"/api/projects/{project}/gantt")

    assert response.status_code == 200
    deps = response.json()["dependencies"]
    assert deps == [{"task_id": a, "depends_on_task_id": b}]


def test_gantt_404_unknown_project(
    client: TestClient, db_session: Session
) -> None:
    assert client.get("/api/projects/9999/gantt").status_code == 404


# --- Global cross-project gantt (Slice 8) ----------------------------------


def test_global_gantt_aggregates_across_projects(
    client: TestClient, db_session: Session
) -> None:
    firewall = _project(db_session, "Firewall")
    website = _project(db_session, "Website")

    a = _task(db_session, firewall, "fw task", scheduled_start=date(2026, 6, 20))
    b = _task(db_session, website, "web task", due_date=date(2026, 6, 25))
    # Filtered out: a done task and an unaccepted candidate must not appear.
    _task(db_session, firewall, "done", workflow_status=TaskWorkflowStatus.done)
    _task(db_session, website, "cand", review_status=TaskReviewStatus.candidate)

    response = client.get("/api/planning/gantt")

    assert response.status_code == 200
    body = response.json()
    assert sorted(t["id"] for t in body["tasks"]) == sorted([a, b])
    # Both projects own a bar -> both in the legend; ordered by project id.
    assert body["projects"] == [
        {"id": firewall, "name": "Firewall"},
        {"id": website, "name": "Website"},
    ]


def test_global_gantt_lists_only_projects_with_tasks(
    client: TestClient, db_session: Session
) -> None:
    firewall = _project(db_session, "Firewall")
    _project(db_session, "Empty")  # no tasks -> must not appear in the legend
    _task(db_session, firewall, "fw task", scheduled_start=date(2026, 6, 20))

    body = client.get("/api/planning/gantt").json()

    assert [p["id"] for p in body["projects"]] == [firewall]


def test_global_gantt_includes_cross_project_edges(
    client: TestClient, db_session: Session
) -> None:
    firewall = _project(db_session, "Firewall")
    website = _project(db_session, "Website")
    blocker = _task(db_session, firewall, "blocker", scheduled_start=date(2026, 6, 20))
    dependent = _task(
        db_session, website, "dependent", scheduled_start=date(2026, 6, 25)
    )
    deps_service.add_dependency(db_session, dependent, blocker)
    db_session.commit()

    deps = client.get("/api/planning/gantt").json()["dependencies"]

    assert deps == [{"task_id": dependent, "depends_on_task_id": blocker}]


def test_global_gantt_excludes_soft_deleted(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    kept = _task(db_session, project, "kept", due_date=date(2026, 6, 25))
    trashed = _task(db_session, project, "trashed", due_date=date(2026, 6, 26))
    task = tasks_service.get_task(db_session, trashed)
    assert task is not None
    soft_delete(task)
    db_session.commit()

    body = client.get("/api/planning/gantt").json()

    assert [t["id"] for t in body["tasks"]] == [kept]


def test_patch_cascades_across_project_boundaries(
    client: TestClient, db_session: Session
) -> None:
    # The Slice 8 cascade fix: a dependent in another project must shift when its
    # blocker moves. A per-project cascade would silently leave it unshifted.
    firewall = _project(db_session, "Firewall")
    website = _project(db_session, "Website")
    blocker = _task(db_session, firewall, "blocker", scheduled_start=date(2026, 6, 20))
    dependent = _task(
        db_session, website, "dependent", scheduled_start=date(2026, 6, 20)
    )
    deps_service.add_dependency(db_session, dependent, blocker)
    db_session.commit()

    response = client.patch(
        f"/api/tasks/{blocker}", json={"scheduled_start": "2026-06-25"}
    )
    assert response.status_code == 200

    # The dependent lives in the *other* project; it must follow to the 26th.
    starts = _starts(client, website)
    assert starts[dependent] == "2026-06-26"


# --- Dependency auto-shift through the PATCH route (Slice 5) ----------------


def _starts(client: TestClient, project: int) -> dict[int, str | None]:
    body = client.get(f"/api/projects/{project}/gantt").json()
    return {t["id"]: t["scheduled_start"] for t in body["tasks"]}


def test_patch_start_cascades_downstream_dependents(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    # A -> B -> C, single-day each, all initially on the 20th.
    a = _task(db_session, project, "a", scheduled_start=date(2026, 6, 20))
    b = _task(db_session, project, "b", scheduled_start=date(2026, 6, 20))
    c = _task(db_session, project, "c", scheduled_start=date(2026, 6, 20))
    deps_service.add_dependency(db_session, b, a)
    deps_service.add_dependency(db_session, c, b)
    db_session.commit()

    # Move A out to the 25th: B must follow to the 26th, then C to the 27th.
    response = client.patch(
        f"/api/tasks/{a}", json={"scheduled_start": "2026-06-25"}
    )
    assert response.status_code == 200

    starts = _starts(client, project)
    assert starts[a] == "2026-06-25"
    assert starts[b] == "2026-06-26"
    assert starts[c] == "2026-06-27"


def test_patch_start_does_not_shift_dependents_already_clear(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    a = _task(db_session, project, "a", scheduled_start=date(2026, 6, 20))
    b = _task(db_session, project, "b", scheduled_start=date(2026, 6, 30))
    deps_service.add_dependency(db_session, b, a)
    db_session.commit()

    # Nudge A slightly; B already starts well after A finishes -> untouched.
    client.patch(f"/api/tasks/{a}", json={"scheduled_start": "2026-06-21"})

    starts = _starts(client, project)
    assert starts[a] == "2026-06-21"
    assert starts[b] == "2026-06-30"


def test_patch_estimate_extends_bar_and_cascades(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    a = _task(
        db_session, project, "a", scheduled_start=date(2026, 6, 20), estimated_minutes=480
    )
    b = _task(db_session, project, "b", scheduled_start=date(2026, 6, 21))
    deps_service.add_dependency(db_session, b, a)
    db_session.commit()

    # Grow A to 3 days (1440 min): it now ends the 22nd, so B must move to the 23rd.
    client.patch(f"/api/tasks/{a}", json={"estimated_minutes": 1440})

    starts = _starts(client, project)
    assert starts[b] == "2026-06-23"


def test_cascade_spans_blocker_by_rolled_up_estimate_not_raw_column(
    client: TestClient, db_session: Session
) -> None:
    # Regression: a blocker with subtasks spans the *rolled-up* estimate (the value
    # its bar is drawn from), not its raw ``estimated_minutes`` column. Here the raw
    # column still holds 960 (2 days, ending the 24th) but the accepted subtasks have
    # no estimate, so the parent rolls up to a 1-day bar ending the 23rd. A dependent
    # must be allowed to start the 24th (right after the *visible* bar end); the old
    # cascade read the raw 960 and wrongly snapped it back to the 25th.
    project = _project(db_session, "Rollup cascade")
    blocker = _task(
        db_session,
        project,
        "blocker",
        scheduled_start=date(2026, 6, 23),
        estimated_minutes=960,
    )
    _task(db_session, project, "blocker child", parent_task_id=blocker)
    dependent = _task(
        db_session, project, "dependent", scheduled_start=date(2026, 6, 25)
    )
    deps_service.add_dependency(db_session, dependent, blocker)
    db_session.commit()

    # Pull the dependent up to the 24th — the day right after the rolled-up bar ends.
    response = client.patch(
        f"/api/tasks/{dependent}", json={"scheduled_start": "2026-06-24"}
    )
    assert response.status_code == 200

    starts = _starts(client, project)
    assert starts[dependent] == "2026-06-24"

    # And the 23rd is still forbidden (would start on the blocker's last day).
    client.patch(f"/api/tasks/{dependent}", json={"scheduled_start": "2026-06-23"})
    assert _starts(client, project)[dependent] == "2026-06-24"


def test_patch_unrelated_field_does_not_cascade(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    # B starts before A finishes (a standing conflict), but editing A's *title*
    # is not a placement change, so the cascade must not fire and move B.
    a = _task(
        db_session, project, "a", scheduled_start=date(2026, 6, 20), estimated_minutes=960
    )
    b = _task(db_session, project, "b", scheduled_start=date(2026, 6, 20))
    deps_service.add_dependency(db_session, b, a)
    db_session.commit()

    client.patch(f"/api/tasks/{a}", json={"title": "a renamed"})

    starts = _starts(client, project)
    assert starts[b] == "2026-06-20"


# --- What-if preview (Slice 6) ---------------------------------------------


def _what_if(
    client: TestClient, project: int, overrides: list[dict[str, object]]
) -> dict[int, str]:
    response = client.post(
        f"/api/projects/{project}/gantt/what-if", json={"overrides": overrides}
    )
    assert response.status_code == 200
    return {s["task_id"]: s["scheduled_start"] for s in response.json()["shifts"]}


def test_what_if_previews_override_and_cascade_without_saving(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    # A -> B -> C, single-day each, all on the 20th.
    a = _task(db_session, project, "a", scheduled_start=date(2026, 6, 20))
    b = _task(db_session, project, "b", scheduled_start=date(2026, 6, 20))
    c = _task(db_session, project, "c", scheduled_start=date(2026, 6, 20))
    deps_service.add_dependency(db_session, b, a)
    deps_service.add_dependency(db_session, c, b)
    db_session.commit()

    shifts = _what_if(
        client, project, [{"task_id": a, "scheduled_start": "2026-06-25"}]
    )
    # The override surfaces alongside the cascaded dependents.
    assert shifts == {
        a: "2026-06-25",
        b: "2026-06-26",
        c: "2026-06-27",
    }
    # Nothing persisted — the real schedule is untouched.
    assert _starts(client, project) == {
        a: "2026-06-20",
        b: "2026-06-20",
        c: "2026-06-20",
    }


def test_what_if_estimate_override_cascades(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    a = _task(
        db_session, project, "a", scheduled_start=date(2026, 6, 20), estimated_minutes=480
    )
    b = _task(db_session, project, "b", scheduled_start=date(2026, 6, 21))
    deps_service.add_dependency(db_session, b, a)
    db_session.commit()

    # Grow A to 3 days: it now ends the 22nd, pushing B to the 23rd. A's own start
    # is unchanged, so only B surfaces.
    shifts = _what_if(
        client, project, [{"task_id": a, "estimated_minutes": 1440}]
    )
    assert shifts == {b: "2026-06-23"}


def test_what_if_with_no_conflict_returns_only_the_override(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    a = _task(db_session, project, "a", scheduled_start=date(2026, 6, 20))
    b = _task(db_session, project, "b", scheduled_start=date(2026, 6, 30))
    deps_service.add_dependency(db_session, b, a)
    db_session.commit()

    # Move A to the 21st; B at the 30th is already clear, so only A surfaces.
    shifts = _what_if(
        client, project, [{"task_id": a, "scheduled_start": "2026-06-21"}]
    )
    assert shifts == {a: "2026-06-21"}


def test_what_if_404_unknown_project(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(
        "/api/projects/9999/gantt/what-if", json={"overrides": []}
    )
    assert response.status_code == 404
