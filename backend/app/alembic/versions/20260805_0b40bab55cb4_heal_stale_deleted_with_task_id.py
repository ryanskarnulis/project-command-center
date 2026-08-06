"""heal stale deleted_with_task_id

Data-only heal (no schema change). Before the purge fix for issue #251,
``purge_task`` destroyed a trashed subtree without clearing the
``deleted_with_task_id`` markers on rows that survived the purge (e.g. a
trashed child owned by another project, which the project-scoped subtree walk
leaves behind). The column is a plain Integer over SQLite rowids — no FK, by
design — so a dangling marker is not inert: the next insert can be handed the
freed id, and ``_marked_descendant_ids`` then reads the stale-marked rows as
that new task's cascade, sweeping them into an unrelated restore or purge.

``purge_task`` now nulls those markers in the same transaction as the purge,
but only going forward: markers made stale by purges that ran before the fix
are still sitting in upgraded databases. This clears every marker whose target
task no longer exists, returning those rows to standalone trash entries that
are individually restorable. Markers naming a task that still exists are left
untouched — they belong to that task's cascade.

Revision ID: 0b40bab55cb4
Revises: 93c179708075
Create Date: 2026-08-05 09:41:22.318664

"""
from typing import Sequence, Union

from alembic import op

revision: str = '0b40bab55cb4'
down_revision: Union[str, None] = '93c179708075'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tasks SET deleted_with_task_id = NULL
        WHERE deleted_with_task_id IS NOT NULL
          AND deleted_with_task_id NOT IN (SELECT id FROM tasks)
        """
    )


def downgrade() -> None:
    # A data heal has no meaningful inverse: the purged target ids are gone for
    # good, and re-stamping rows with dangling markers would only recreate the
    # id-reuse hazard this revision exists to remove. No-op.
    pass
