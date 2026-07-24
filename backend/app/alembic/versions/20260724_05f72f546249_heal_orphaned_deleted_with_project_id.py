"""heal orphaned deleted_with_project_id

Data-only heal (no schema change). Before the trash/restore fixes,
``restore_project(restore_tasks=False)`` cleared only the project's
``deleted_at`` and left its cascade tasks stamped with ``deleted_with_project_id``
pointing at the now-*active* project. Those rows are filtered out of every trash
surface (``list_deleted_tasks`` hides stamped rows, and the project is no longer
in the project trash), so the tasks became unreachable.

This clears the marker on any still-trashed task whose ``deleted_with_project_id``
references an *active* (non-deleted) project, converting those stranded rows into
standalone Tasks-trash entries where they are discoverable and individually
restorable. Tasks stamped with a project that is itself still trashed are left
untouched — they legitimately belong to that project's trash entry.

Revision ID: 05f72f546249
Revises: d8e1dce821f8
Create Date: 2026-07-24 10:24:15.467455

"""
from typing import Sequence, Union

from alembic import op

revision: str = '05f72f546249'
down_revision: Union[str, None] = 'd8e1dce821f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tasks SET deleted_with_project_id = NULL
        WHERE deleted_with_project_id IS NOT NULL
          AND deleted_with_project_id IN (
              SELECT id FROM projects WHERE deleted_at IS NULL
          )
        """
    )


def downgrade() -> None:
    # A data heal has no meaningful inverse: the original (project id) marker was
    # informationally lossy once its project was restored, and re-stamping would
    # re-hide the rows. No-op.
    pass
