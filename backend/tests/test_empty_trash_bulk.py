"""``DELETE /api/trash`` works no matter how big the trash is (#275).

``empty_trash`` snapshots every trashed project/task id straight out of the
database and hands the lists to ``purge_selected``. Those lists never cross a
request boundary, so ``MAX_PURGE_IDS`` (#264) structurally cannot bound them —
they are as long as the trash is. ``purge_selected`` expanded them into
``IN (...)`` with one bound parameter per id, so past SQLite's 32,766-parameter
ceiling the *first* statement of the purge raised ``OperationalError: too many
SQL variables``: a 500, nothing purged, and no retry that could ever improve it.

The fix is service-layer chunking (``common.chunked``), so these tests pin the
invariant rather than the ceiling: seeding 33k rows to reproduce the original
crash would make the suite crawl for no extra signal. Instead the trash is
seeded past one chunk and the widest bound-parameter list any statement of the
purge binds is asserted to stay inside a chunk — which is exactly what fails on
the unchunked code, at any trash size above ``IN_CHUNK``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session

from app.db.models import Project, Task
from app.services import trash as trash_service
from app.services.common import IN_CHUNK

# SQLite's compiled-in ``SQLITE_MAX_VARIABLE_NUMBER`` since 3.32 (999 before it).
SQLITE_MAX_VARIABLES = 32_766

# Enough trashed rows of each kind that the id lists cannot be bound in one
# statement. Comfortably past ``IN_CHUNK`` and cheap to purge.
BULK_ROWS = IN_CHUNK + 50


def _seed_trash(db: Session, *, rows: int) -> None:
    """``rows`` trashed projects, each holding one trashed task.

    Filed rather than unfiled so the project-scoped read in ``_removed_task_ids``
    (``Task.project_id.in_(project_ids)``) has real rows to find, not just a long
    parameter list.
    """
    now = datetime.now(UTC)
    projects = [Project(name=f"project {i}", deleted_at=now) for i in range(rows)]
    db.add_all(projects)
    db.flush()
    db.add_all(
        [
            Task(title=f"task {i}", project_id=project.id, deleted_at=now)
            for i, project in enumerate(projects)
        ]
    )
    db.commit()


@contextmanager
def _bound_parameter_counts(engine: Engine) -> Iterator[list[int]]:
    """Collect the parameter count of every single-row statement executed inside.

    ``executemany`` batches are skipped: they bind one parameter set per row, and
    the driver splits them — they were never what overran the ceiling.
    """
    counts: list[int] = []

    def record(
        _conn: Any,
        _cursor: Any,
        _statement: str,
        parameters: Any,
        _context: Any,
        executemany: bool,
    ) -> None:
        if not executemany and parameters is not None:
            counts.append(len(parameters))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield counts
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_chunk_size_leaves_headroom_under_the_sqlite_ceiling() -> None:
    """The point of chunking: a chunk can never overrun SQLite, on any build."""
    assert IN_CHUNK < SQLITE_MAX_VARIABLES


def test_empty_trash_clears_a_trash_larger_than_one_in_chunk(
    db_session: Session,
) -> None:
    """The regression: emptying more rows than fit in one ``IN`` list succeeds."""
    _seed_trash(db_session, rows=BULK_ROWS)
    assert trash_service.count_trash(db_session).purge_total == BULK_ROWS * 2

    counts = trash_service.empty_trash(db_session)
    db_session.commit()

    assert counts.projects == BULK_ROWS
    assert counts.tasks == BULK_ROWS
    # Every chunk was purged, not just the first: nothing is left behind.
    assert db_session.scalar(select(func.count()).select_from(Project)) == 0
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    trash = trash_service.count_trash(db_session)
    assert (trash.projects, trash.tasks, trash.purge_total) == (0, 0, 0)


def test_empty_trash_never_binds_more_ids_than_one_chunk(
    db_session: Session, test_engine: Engine
) -> None:
    """No statement in the purge expands an unbounded id list.

    This is the assertion the unchunked code fails: it bound all ``BULK_ROWS``
    ids in the first id-resolution read, and with a real (33k-row) trash that is
    the ``too many SQL variables`` 500.
    """
    _seed_trash(db_session, rows=BULK_ROWS)

    with _bound_parameter_counts(test_engine) as counts:
        trash_service.empty_trash(db_session)
        db_session.commit()

    assert counts, "no statements observed — the listener is not wired up"
    assert max(counts) <= IN_CHUNK
