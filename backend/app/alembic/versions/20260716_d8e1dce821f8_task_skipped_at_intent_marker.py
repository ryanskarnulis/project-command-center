"""task skipped_at intent marker

Adds ``tasks.skipped_at`` so a skipped recurring occurrence is distinguishable
from a normally-trashed one. Both set ``deleted_at``; without this marker
``restore_task`` treated every recurring restore as an un-skip, rescheduling the
live occurrence backward and hard-deleting the row the user asked to restore.

Backfill reconstructs intent from ``activity_events``, which is append-only and
records a "skipped" action for every skip that went through the service layer.

Known gap: ``services/tasks.log_task_event`` returns early for tasks with no
project, so an *unfiled* task that was skipped has no event and backfills as an
ordinary delete. It then restores in place instead of un-skipping — the safe
direction (nothing is destroyed), and rare enough to accept over guessing.

Autogenerate also proposed dropping ``_litestream_lock``/``_litestream_seq``.
Those are Litestream's own replication bookkeeping (see scripts/backup_db.sh),
not app tables and not in our models — removed from this migration by hand.

Revision ID: d8e1dce821f8
Revises: 7efad5645027
Create Date: 2026-07-16 10:19:26.301044

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8e1dce821f8'
down_revision: Union[str, None] = '7efad5645027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('skipped_at', sa.DateTime(), nullable=True))

    # Mark the already-trashed occurrences that the audit log says were skipped.
    # skipped_at mirrors deleted_at: the event's own timestamp would be close but
    # not identical, and the two columns are written together everywhere else.
    op.execute(
        """
        UPDATE tasks SET skipped_at = deleted_at
        WHERE deleted_at IS NOT NULL
          AND recurrence_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM activity_events e
              WHERE e.entity_type = 'task'
                AND e.entity_id = tasks.id
                AND e.action = 'skipped'
          )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('skipped_at')
