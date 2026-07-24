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
