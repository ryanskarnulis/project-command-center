"""active occurrence uniqueness

A recurring series holds at most one *live* occurrence per due date. That was
only ever an application-level convention, and two paths broke it: skipping an
occurrence whose date already held a normally-trashed row landed a live
replacement there, and restoring the trashed row then produced two active tasks
with the same ``recurrence_id`` and ``due_date``.

Adds the partial unique index ``uq_tasks_active_occurrence`` on
``(recurrence_id, due_date) WHERE recurrence_id IS NOT NULL AND deleted_at IS
NULL``, so the invariant survives races and future callers. Partial, so trashed
history and non-recurring tasks are exempt.

Existing databases may already hold duplicates, which would make the index
creation fail. They are resolved first, non-destructively: on each conflicting
``(recurrence_id, due_date)`` the lowest ``id`` stays active and the rest are
*soft*-deleted into the trash (no hard deletes — CLAUDE.md prime directive 2),
each with an ``activity_events`` row so the move is auditable and the row is
discoverable and restorable. Restoring one now raises ``OccurrenceConflictError``
until its date is free, which is the point.

Revision ID: 3ab1c74d9e02
Revises: 05f72f546249
Create Date: 2026-07-24 11:55:02.118433

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '3ab1c74d9e02'
down_revision: Union[str, None] = '05f72f546249'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The duplicate active occurrences, excluding the survivor (lowest id) on each
# (recurrence_id, due_date). Shared by the audit-log insert and the soft delete so
# the two can't drift.
_DUPLICATES = """
    SELECT t.id FROM tasks t
    WHERE t.recurrence_id IS NOT NULL
      AND t.due_date IS NOT NULL
      AND t.deleted_at IS NULL
      AND t.id > (
          SELECT MIN(o.id) FROM tasks o
          WHERE o.recurrence_id = t.recurrence_id
            AND o.due_date = t.due_date
            AND o.deleted_at IS NULL
      )
"""


def upgrade() -> None:
    # Audit first: the summary has to be written while the rows are still active,
    # and it reads the title, so it can't be reconstructed afterwards.
    op.execute(
        f"""
        INSERT INTO activity_events
            (project_id, entity_type, entity_id, action, summary, actor, created_at, updated_at)
        SELECT
            t.project_id,
            'task',
            t.id,
            'deleted',
            'Task "' || t.title || '" moved to trash: duplicate recurring '
                || 'occurrence for the same due date',
            NULL,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM tasks t
        WHERE t.id IN ({_DUPLICATES})
        """
    )
    op.execute(
        f"""
        UPDATE tasks SET deleted_at = CURRENT_TIMESTAMP
        WHERE id IN ({_DUPLICATES})
        """
    )
    op.create_index(
        "uq_tasks_active_occurrence",
        "tasks",
        ["recurrence_id", "due_date"],
        unique=True,
        sqlite_where=sa.text("recurrence_id IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("recurrence_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    # The index only. The duplicate resolution is a data heal with no meaningful
    # inverse: re-activating those rows would recreate the state this migration
    # exists to make impossible, and they remain individually restorable from the
    # trash anyway.
    op.drop_index("uq_tasks_active_occurrence", table_name="tasks")
