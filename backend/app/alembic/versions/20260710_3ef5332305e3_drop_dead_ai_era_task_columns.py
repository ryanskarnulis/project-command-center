"""drop dead ai-era task columns

Drops ``review_status``, ``confidence``, and ``assignee_hint`` from ``tasks``
(nothing produces candidates or confidences since the AI-training strip), and
replaces the ``(deleted_at, review_status)`` compound index with a plain
``deleted_at`` index so the active-list and trash scans keep their coverage.

Autogenerate also flagged litestream's runtime ``_litestream_*`` tables for
removal — those are created by the replication sidecar, are not part of the app
schema, and were removed from this migration by hand (same as the Sprint 29
index migration).

Revision ID: 3ef5332305e3
Revises: 55a75efe325c
Create Date: 2026-07-10 19:24:37.725270

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3ef5332305e3'
down_revision: Union[str, None] = '55a75efe325c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_deleted_at_review_status'))
        batch_op.create_index('ix_tasks_deleted_at', ['deleted_at'], unique=False)
        batch_op.drop_column('assignee_hint')
        batch_op.drop_column('confidence')
        batch_op.drop_column('review_status')


def downgrade() -> None:
    # ``review_status`` is NOT NULL, so re-adding it needs a value for existing
    # rows; every surviving row was accepted (the upgrade dropped the column
    # after candidate/rejected stopped being produced), so backfill 'accepted'.
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'review_status',
                sa.VARCHAR(length=9),
                nullable=False,
                server_default='accepted',
            )
        )
        batch_op.add_column(sa.Column('confidence', sa.FLOAT(), nullable=True))
        batch_op.add_column(sa.Column('assignee_hint', sa.VARCHAR(), nullable=True))
        batch_op.drop_index('ix_tasks_deleted_at')
        batch_op.create_index(
            batch_op.f('ix_tasks_deleted_at_review_status'),
            ['deleted_at', 'review_status'],
            unique=False,
        )
