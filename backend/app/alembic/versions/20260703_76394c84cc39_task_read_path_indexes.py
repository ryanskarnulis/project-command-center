"""task read-path indexes

Revision ID: 76394c84cc39
Revises: edb902c152de
Create Date: 2026-07-03 21:52:21.925352

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '76394c84cc39'
down_revision: Union[str, None] = 'edb902c152de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Read-path indexes on ``tasks`` (Sprint 29 hardening). Autogenerate also
    # flagged litestream's runtime ``_litestream_*`` tables for removal — those
    # are created by the replication sidecar, are not part of the app schema, and
    # must NOT be dropped here; those ops were removed by hand.
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index('ix_tasks_deleted_at_review_status', ['deleted_at', 'review_status'], unique=False)
        batch_op.create_index('ix_tasks_parent_task_id', ['parent_task_id'], unique=False)
        batch_op.create_index('ix_tasks_project_id', ['project_id'], unique=False)
        batch_op.create_index('ix_tasks_recurrence_id', ['recurrence_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index('ix_tasks_recurrence_id')
        batch_op.drop_index('ix_tasks_project_id')
        batch_op.drop_index('ix_tasks_parent_task_id')
        batch_op.drop_index('ix_tasks_deleted_at_review_status')
