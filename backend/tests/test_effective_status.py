"""Cross-surface regression matrix for the unified effective-status model.

Every read surface (task read model, dependency reads, search, dashboard,
list_tasks, effective_statuses) must agree on whether a task is done, once
roll-up and the dependency cap are both applied. These tests pin the four
divergences fixed in ``fix/unify-effective-task-status`` plus the invariant that
ties the two authoritative computations together.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.task_reads import read_with_blocked
from app.db.models import Task, TaskWorkflowStatus
from app.services import dashboard as dashboard_service
from app.services import projects as projects_service
from app.services import search as search_service
from app.services import task_dependencies as deps_service
from app.services import task_trash
from app.services import tasks as tasks_service


def _task(
    db: Session,
    title: str,
    *,
    parent_task_id: int | None = None,
    project_id: int | None = None,
) -> int:
    task = tasks_service.create_task(
        db, project_id=project_id, title=title, parent_task_id=parent_task_id
    )
    db.commit()
    return task.id


def _live(db: Session, task_id: int) -> Task:
    task = tasks_service.get_task(db, task_id)
    assert task is not None
    return task


def _complete(db: Session, task_id: int) -> None:
    tasks_service.mark_done(db, _live(db, task_id))
    db.commit()


def _done_child(db: Session, parent_id: int, title: str) -> int:
    """A subtask created then completed, so its parent rolls up toward done."""
    child_id = _task(db, title, parent_task_id=parent_id)
    _complete(db, child_id)
    return child_id


# --- #1: leaf stored-done-and-blocked via the trash/restore window -----------


def _stranded_done_blocked_leaf(db: Session) -> tuple[int, int]:
    """Reproduce the done-yet-blocked leaf: A->B, trash B, complete A, restore B."""
    a = _task(db, "publish")
    b = _task(db, "review")
    deps_service.add_dependency(db, a, b)
    db.commit()

    # Trash B: A now reads as not-blocked (a soft-deleted blocker is invisible).
    tasks_service.soft_delete_task(db, _live(db, b))
    db.commit()
    assert not deps_service.is_blocked(db, a)

    # Complete A through the (now-open) window, then restore B to re-block it.
    _complete(db, a)
    deleted_b = task_trash.get_deleted_task(db, b)
    assert deleted_b is not None
    task_trash.restore_task(db, deleted_b)
    db.commit()
    return a, b


def test_done_blocked_leaf_reads_in_progress_everywhere(db_session: Session) -> None:
    a, _b = _stranded_done_blocked_leaf(db_session)

    # Stored column is still done, but every read surface reports in_progress.
    assert _live(db_session, a).workflow_status == TaskWorkflowStatus.done

    read = read_with_blocked(db_session, _live(db_session, a))
    assert read.workflow_status == TaskWorkflowStatus.in_progress
    assert read.is_blocked is True

    assert deps_service.effective_statuses(db_session, [a]) == {
        a: TaskWorkflowStatus.in_progress
    }


def test_done_blocked_leaf_list_and_dashboard_agree(db_session: Session) -> None:
    a, _b = _stranded_done_blocked_leaf(db_session)

    not_done_ids = {
        t.id for t in tasks_service.list_tasks(db_session, exclude_done=True)
    }
    done_ids = {
        t.id
        for t in tasks_service.list_tasks(
            db_session, workflow_status=TaskWorkflowStatus.done
        )
    }
    assert a in not_done_ids
    assert a not in done_ids

    # The dashboard's open set uses the same cap now, so it counts A as open too.
    open_ids = {t.id for t in dashboard_service._open_tasks(db_session)}
    assert a in open_ids


# --- #2: checklist-parent blocker reads as effectively done ------------------


def test_done_checklist_parent_blocker_reads_done(
    client: TestClient, db_session: Session
) -> None:
    parent = _task(db_session, "release checklist")
    _done_child(db_session, parent, "cut branch")
    _done_child(db_session, parent, "tag build")
    # Parent's stored column never flips; the roll-up is what says "done".
    assert _live(db_session, parent).workflow_status == TaskWorkflowStatus.open

    x = _task(db_session, "announce")
    deps_service.add_dependency(db_session, x, parent)
    db_session.commit()

    # X is not blocked (its blocker rolls up to done)...
    assert not deps_service.is_blocked(db_session, x)

    # ...and the dependency edge read agrees: effective, not the stored column.
    resp = client.get(f"/api/tasks/{x}/dependencies")
    assert resp.status_code == 200
    edge = resp.json()[0]
    assert edge["depends_on_workflow_status"] == "done"
    assert edge["depends_on_done"] is True

    # Mirror direction: parent's dependents list reports X's effective status.
    resp = client.get(f"/api/tasks/{parent}/dependents")
    assert resp.status_code == 200
    dep = resp.json()[0]
    assert dep["dependent_task_id"] == x
    assert dep["dependent_done"] is False


# --- #3: search keeps an older open match over a full page of newer done -----


def test_search_keeps_older_open_match(db_session: Session) -> None:
    # Oldest (smallest id) exact match is open; three newer exact matches are done.
    open_id = _task(db_session, "sync")
    for _ in range(3):
        _complete(db_session, _task(db_session, "sync"))

    results = search_service.search(db_session, "sync", per_kind=3)

    ids = [t.id for t in results.tasks]
    assert len(ids) == 3  # per_kind cap still honored
    assert open_id in ids  # the older open match survived the cut...
    assert ids[0] == open_id  # ...and outranks the newer done matches
    assert results.tasks[0].workflow_status == TaskWorkflowStatus.open


# --- #4: dashboard total matches the effective not-done count ----------------


def test_dashboard_total_matches_effective_not_done(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Ship")
    db_session.commit()

    # A blocked-done checklist parent: children all done but it waits on an open
    # dependency, so it is effectively in_progress (counts as open). The blocker
    # edge is added while the parent is still open — you can't add a dependency to
    # an already-done task — then the child is completed.
    parent = _task(db_session, "ship checklist", project_id=project.id)
    child = _task(db_session, "qa", parent_task_id=parent, project_id=project.id)
    blocker = _task(db_session, "sign-off", project_id=project.id)
    deps_service.add_dependency(db_session, parent, blocker)
    db_session.commit()
    _complete(db_session, child)

    total, _per_project = dashboard_service.get_overview(db_session)

    # Parity: overview total equals the count of effective-not-done active tasks.
    active = tasks_service.list_tasks(db_session)
    effective = deps_service.effective_statuses(db_session, [t.id for t in active])
    expected_open = sum(
        1 for t in active if effective.get(t.id) != TaskWorkflowStatus.done
    )
    assert total == expected_open
    # And the blocked-done parent is among the counted-open tasks.
    open_ids = {t.id for t in dashboard_service._open_tasks(db_session)}
    assert parent in open_ids


# --- invariant: the two authorities agree per task ---------------------------


def test_effective_statuses_equals_capped_rollup(db_session: Session) -> None:
    # A transitive chain (a->b->c) plus a blocked-done checklist parent (p waits
    # on an open q) exercises leaves, parents, and the cap in one graph.
    a = _task(db_session, "a")
    b = _task(db_session, "b")
    c = _task(db_session, "c")
    deps_service.add_dependency(db_session, a, b)
    deps_service.add_dependency(db_session, b, c)

    # p is a checklist parent blocked on q; add the edge while p is still open,
    # then complete its child so p rolls up done-but-blocked (effective in_progress).
    p = _task(db_session, "p")
    p_child = _task(db_session, "p-child", parent_task_id=p)
    q = _task(db_session, "q")
    deps_service.add_dependency(db_session, p, q)
    db_session.commit()
    _complete(db_session, p_child)

    active = tasks_service.list_tasks(db_session)
    rollups = tasks_service.compute_rollups(db_session, active)
    effective = deps_service.effective_statuses(db_session, [t.id for t in active])

    for task in active:
        capped = tasks_service.capped_status(
            rollups[task.id].workflow_status,
            deps_service.is_blocked(db_session, task.id),
        )
        assert effective[task.id] == capped, f"mismatch for {task.title!r}"


# --- no-regression: normal tasks are untouched -------------------------------


def test_unblocked_tasks_keep_stored_status(
    client: TestClient, db_session: Session
) -> None:
    open_leaf = _task(db_session, "draft")
    done_leaf = _task(db_session, "archive")
    _complete(db_session, done_leaf)

    open_read = read_with_blocked(db_session, _live(db_session, open_leaf))
    done_read = read_with_blocked(db_session, _live(db_session, done_leaf))
    assert open_read.workflow_status == TaskWorkflowStatus.open
    assert open_read.is_blocked is False
    assert done_read.workflow_status == TaskWorkflowStatus.done

    # A done leaf blocker still reads as done on the dependency edge.
    dependent = _task(db_session, "followup")
    deps_service.add_dependency(db_session, dependent, done_leaf)
    db_session.commit()
    resp = client.get(f"/api/tasks/{dependent}/dependencies")
    edge = resp.json()[0]
    assert edge["depends_on_done"] is True
