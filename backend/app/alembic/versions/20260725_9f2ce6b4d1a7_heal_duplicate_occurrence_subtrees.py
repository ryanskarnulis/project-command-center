"""heal duplicate occurrence subtrees

Data-only heal (no schema change) for databases that already ran the *pre-cascade*
version of ``3ab1c74d9e02``. That version trashed each duplicate live occurrence
with a single flat ``UPDATE``, skipping the subtree cascade
``services/tasks.soft_delete_task`` applies. A duplicate occurrence that was a
recurring *checklist* therefore kept its cloned subtasks active beneath a trashed
parent, and the read model — which deliberately treats a live task with a trashed
parent as an effective top-level task — surfaced those checklist steps on boards
and Focus as unrelated top-level work.

The roots are identified from the audit trail the earlier migration wrote: task
``deleted`` events whose summary is the duplicate-occurrence reason, for rows that
are still trashed. Only *descendants of those specific rows* are cascaded, so
legitimate cross-project effective orphans (a live task whose parent was trashed
by hand) are left alone. Each cascaded row gets its own ``activity_events`` entry
and stays individually restorable — no hard deletes.

Idempotent: a database that ran the fixed ``3ab1c74d9e02`` has no active
descendants left to find, and this is a no-op.

Revision ID: 9f2ce6b4d1a7
Revises: 3ab1c74d9e02
Create Date: 2026-07-25 09:12:44.108216

"""
from typing import Sequence, Union

from alembic import op

revision: str = '9f2ce6b4d1a7'
down_revision: Union[str, None] = '3ab1c74d9e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Still-active descendants of the occurrences the earlier migration trashed.
# ``UNION`` (not ``UNION ALL``) makes a corrupt parent cycle terminate. The roots
# themselves are already trashed, so they never appear in the result.
_LEAKED_CTE = """
WITH RECURSIVE roots(id) AS (
    SELECT DISTINCT e.entity_id FROM activity_events e
    JOIN tasks t ON t.id = e.entity_id
    WHERE e.entity_type = 'task'
      AND e.action = 'deleted'
      AND e.summary LIKE '%duplicate recurring occurrence for the same due date'
      AND t.deleted_at IS NOT NULL
),
leaked(id) AS (
    SELECT t.id FROM tasks t JOIN roots r ON t.parent_task_id = r.id
    WHERE t.deleted_at IS NULL
    UNION
    SELECT t.id FROM tasks t JOIN leaked l ON t.parent_task_id = l.id
    WHERE t.deleted_at IS NULL
)
"""


def upgrade() -> None:
    # Audit first: the summary reads the title and has to be written while the
    # rows are still active.
    op.execute(
        f"""
        {_LEAKED_CTE}
        INSERT INTO activity_events
            (project_id, entity_type, entity_id, action, summary, actor, created_at, updated_at)
        SELECT
            t.project_id,
            'task',
            t.id,
            'deleted',
            'Task "' || t.title || '" moved to trash: '
                || 'parent occurrence was a duplicate for the same due date',
            NULL,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM leaked l JOIN tasks t ON t.id = l.id
        """
    )
    op.execute(
        f"""
        {_LEAKED_CTE}
        UPDATE tasks SET deleted_at = CURRENT_TIMESTAMP
        WHERE id IN (SELECT id FROM leaked)
        """
    )


def downgrade() -> None:
    # A data heal with no meaningful inverse: re-activating those rows would
    # recreate the incoherent state this migration exists to remove, and they
    # remain individually restorable from the trash. No-op.
    pass
