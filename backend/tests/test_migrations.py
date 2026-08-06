import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

# backend/ — where alembic.ini lives and the alembic CLI must run from.
BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_head(tmp_path: Path) -> None:
    """A real `alembic upgrade head` against a fresh SQLite file must succeed.

    The rest of the suite builds the schema with Base.metadata.create_all and so
    never exercises the migration chain. This guards that the chain itself applies
    cleanly from empty — the same thing a real deployment does.
    """
    db_path = tmp_path / "migration_smoke.db"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "alembic upgrade head failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )

    # Prove the upgrade actually built schema rather than no-opping.
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {"projects", "tasks"} <= tables, f"expected core tables, got: {sorted(tables)}"


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"},
        capture_output=True,
        text=True,
    )


def test_active_occurrence_migration_resolves_existing_duplicates(
    tmp_path: Path,
) -> None:
    """Duplicate live occurrences are trashed (not purged) so the index can be built.

    A database written before the uniqueness fix may already hold two active rows on
    one ``(recurrence_id, due_date)``, which would make the CREATE UNIQUE INDEX fail.
    The migration resolves them first: lowest id stays, the rest go to the trash with
    an ``activity_events`` row — restorable, auditable, no hard deletes.
    """
    db_path = tmp_path / "dupes.db"
    # Stop one revision short of the uniqueness index, then plant the bad data.
    assert _alembic(db_path, "upgrade", "05f72f546249").returncode == 0

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO projects (name, created_at, updated_at) "
                    "VALUES ('P', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            for title in ("keeper", "twin", "later twin"):
                conn.execute(
                    text(
                        "INSERT INTO tasks (project_id, title, workflow_status, "
                        "priority, due_date, recurrence_id, created_at, updated_at) "
                        "VALUES (1, :title, 'open', 'medium', '2026-06-08', 'r1', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"title": title},
                )

        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, result.stderr

        with engine.begin() as conn:
            live = conn.execute(
                text(
                    "SELECT id, title FROM tasks "
                    "WHERE recurrence_id = 'r1' AND deleted_at IS NULL"
                )
            ).all()
            trashed = conn.execute(
                text(
                    "SELECT id FROM tasks "
                    "WHERE recurrence_id = 'r1' AND deleted_at IS NOT NULL"
                )
            ).all()
            events = conn.execute(
                text(
                    "SELECT entity_id, action, summary FROM activity_events "
                    "WHERE action = 'deleted'"
                )
            ).all()
            # The index now refuses a fresh duplicate.
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO tasks (project_id, title, workflow_status, "
                        "priority, due_date, recurrence_id, created_at, updated_at) "
                        "VALUES (1, 'another', 'open', 'medium', '2026-06-08', 'r1', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
    finally:
        engine.dispose()

    assert [title for _id, title in live] == ["keeper"]
    assert len(trashed) == 2
    assert {entity_id for entity_id, _a, _s in events} == {
        task_id for task_id, in trashed
    }
    assert all("duplicate recurring" in summary for _e, _a, summary in events)


def test_active_occurrence_migration_cascades_to_checklist_subtasks(
    tmp_path: Path,
) -> None:
    """A duplicate recurring *checklist* takes its live subtasks to the trash with it.

    Trashing only the duplicate parent left its cloned subtasks active beneath a
    trashed row, and the read model promotes those to effective top-level work —
    checklist steps silently appearing on boards and Focus. The heal has to match
    ``services/tasks.soft_delete_task``'s subtree cascade.
    """
    db_path = tmp_path / "dupe_checklist.db"
    assert _alembic(db_path, "upgrade", "05f72f546249").returncode == 0

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO projects (name, created_at, updated_at) "
                    "VALUES ('P', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # 1 = survivor, 2 = duplicate checklist parent.
            for title in ("keeper", "duplicate checklist"):
                conn.execute(
                    text(
                        "INSERT INTO tasks (project_id, title, workflow_status, "
                        "priority, due_date, recurrence_id, created_at, updated_at) "
                        "VALUES (1, :title, 'open', 'medium', '2026-06-08', 'r1', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"title": title},
                )
            # 3 = cloned checklist step under the duplicate; 4 = grandchild.
            for title, parent in (("step", 2), ("substep", 3)):
                conn.execute(
                    text(
                        "INSERT INTO tasks (project_id, title, workflow_status, "
                        "priority, parent_task_id, created_at, updated_at) "
                        "VALUES (1, :title, 'open', 'medium', :parent, "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"title": title, "parent": parent},
                )
            # An unrelated live child of the *survivor* must be left alone.
            conn.execute(
                text(
                    "INSERT INTO tasks (project_id, title, workflow_status, "
                    "priority, parent_task_id, created_at, updated_at) "
                    "VALUES (1, 'kept step', 'open', 'medium', 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, result.stderr

        with engine.begin() as conn:
            live = conn.execute(
                text("SELECT id FROM tasks WHERE deleted_at IS NULL ORDER BY id")
            ).all()
            trashed = conn.execute(
                text("SELECT id FROM tasks WHERE deleted_at IS NOT NULL ORDER BY id")
            ).all()
            # No live task may hang off a trashed one.
            orphans = conn.execute(
                text(
                    "SELECT c.id FROM tasks c JOIN tasks p ON p.id = c.parent_task_id "
                    "WHERE c.deleted_at IS NULL AND p.deleted_at IS NOT NULL"
                )
            ).all()
            events = conn.execute(
                text(
                    "SELECT entity_id FROM activity_events WHERE action = 'deleted'"
                )
            ).all()
    finally:
        engine.dispose()

    assert [task_id for task_id, in live] == [1, 5]
    assert [task_id for task_id, in trashed] == [2, 3, 4]
    assert orphans == []
    # Every trashed row is auditable and therefore restorable from the trash UI.
    assert {task_id for task_id, in events} == {2, 3, 4}


def test_heal_revision_cascades_leaked_subtasks_on_upgraded_databases(
    tmp_path: Path,
) -> None:
    """Databases that ran the pre-cascade migration are healed after the fact.

    Reconstructs exactly what the old ``3ab1c74d9e02`` left behind — a trashed
    duplicate occurrence with its audit event, and a still-live subtree under it —
    and asserts the follow-up revision cascades it, while a live task orphaned for
    any *other* reason is untouched.
    """
    db_path = tmp_path / "already_upgraded.db"
    assert _alembic(db_path, "upgrade", "3ab1c74d9e02").returncode == 0

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO projects (name, created_at, updated_at) "
                    "VALUES ('P', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # 1 = the duplicate occurrence the old migration trashed flat.
            conn.execute(
                text(
                    "INSERT INTO tasks (project_id, title, workflow_status, priority, "
                    "due_date, recurrence_id, deleted_at, created_at, updated_at) "
                    "VALUES (1, 'duplicate checklist', 'open', 'medium', '2026-06-08', "
                    "'r1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO activity_events (project_id, entity_type, entity_id, "
                    "action, summary, created_at, updated_at) VALUES "
                    "(1, 'task', 1, 'deleted', 'Task \"duplicate checklist\" moved to "
                    "trash: duplicate recurring occurrence for the same due date', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # 2/3 = the leaked live subtree; 4 = a trashed task orphaning 5 for an
            # unrelated reason, which must stay live.
            for title, parent in (("step", 1), ("substep", 2)):
                conn.execute(
                    text(
                        "INSERT INTO tasks (project_id, title, workflow_status, "
                        "priority, parent_task_id, created_at, updated_at) "
                        "VALUES (1, :title, 'open', 'medium', :parent, "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"title": title, "parent": parent},
                )
            conn.execute(
                text(
                    "INSERT INTO tasks (project_id, title, workflow_status, priority, "
                    "deleted_at, created_at, updated_at) VALUES (1, 'hand-trashed', "
                    "'open', 'medium', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO tasks (project_id, title, workflow_status, priority, "
                    "parent_task_id, created_at, updated_at) VALUES (1, 'valid orphan', "
                    "'open', 'medium', 4, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, result.stderr

        with engine.begin() as conn:
            live = conn.execute(
                text("SELECT id FROM tasks WHERE deleted_at IS NULL ORDER BY id")
            ).all()
            events = conn.execute(
                text(
                    "SELECT entity_id FROM activity_events WHERE action = 'deleted' "
                    "ORDER BY entity_id"
                )
            ).all()

        # Re-running the heal on an already-healed database changes nothing.
        assert _alembic(db_path, "downgrade", "3ab1c74d9e02").returncode == 0
        second = _alembic(db_path, "upgrade", "head")
        assert second.returncode == 0, second.stderr

        with engine.begin() as conn:
            live_again = conn.execute(
                text("SELECT id FROM tasks WHERE deleted_at IS NULL ORDER BY id")
            ).all()
    finally:
        engine.dispose()

    assert [task_id for task_id, in live] == [5]
    assert [task_id for task_id, in events] == [1, 2, 3]
    assert live_again == live


def test_heal_revision_clears_markers_stale_from_pre_fix_purges(
    tmp_path: Path,
) -> None:
    """Markers left dangling by purges that predate the #251 fix are nulled.

    ``purge_task`` now clears ``deleted_with_task_id`` on surviving rows when it
    destroys the marker's target, but a database purged before that fix can still
    hold markers naming task ids that no longer exist — and because ``tasks.id``
    is a plain rowid, a later insert can be handed the freed id and inherit those
    rows as its cascade. Reconstructs that pre-fix state and asserts the heal
    nulls only the dangling marker, while one naming a still-present trashed task
    keeps its cascade membership.
    """
    db_path = tmp_path / "stale_markers.db"
    # Stop at the revision that added the column, just before the heal.
    assert _alembic(db_path, "upgrade", "93c179708075").returncode == 0

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO projects (name, created_at, updated_at) "
                    "VALUES ('P', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # 1 = trashed parent still in the trash; 2 = its cascade child,
            # whose marker names a live row and must survive the heal.
            conn.execute(
                text(
                    "INSERT INTO tasks (project_id, title, workflow_status, "
                    "priority, deleted_at, created_at, updated_at) "
                    "VALUES (1, 'trashed parent', 'open', 'medium', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO tasks (project_id, title, workflow_status, "
                    "priority, deleted_with_task_id, deleted_at, created_at, "
                    "updated_at) VALUES (1, 'valid cascade child', 'open', "
                    "'medium', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
            # 3 = trashed row stamped with a task id a pre-fix purge destroyed;
            # no row 999 exists, exactly the dangling state the heal targets.
            conn.execute(
                text(
                    "INSERT INTO tasks (project_id, title, workflow_status, "
                    "priority, deleted_with_task_id, deleted_at, created_at, "
                    "updated_at) VALUES (1, 'stale marker', 'open', 'medium', "
                    "999, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )

        result = _alembic(db_path, "upgrade", "head")
        assert result.returncode == 0, result.stderr

        with engine.begin() as conn:
            markers = conn.execute(
                text("SELECT id, deleted_with_task_id FROM tasks ORDER BY id")
            ).all()

        # Re-running the heal on an already-healed database changes nothing.
        assert _alembic(db_path, "downgrade", "93c179708075").returncode == 0
        second = _alembic(db_path, "upgrade", "head")
        assert second.returncode == 0, second.stderr

        with engine.begin() as conn:
            markers_again = conn.execute(
                text("SELECT id, deleted_with_task_id FROM tasks ORDER BY id")
            ).all()
    finally:
        engine.dispose()

    assert [(task_id, marker) for task_id, marker in markers] == [
        (1, None),
        (2, 1),
        (3, None),
    ]
    assert markers_again == markers
