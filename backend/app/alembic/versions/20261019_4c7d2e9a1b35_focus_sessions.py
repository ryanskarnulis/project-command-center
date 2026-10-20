"""focus sessions

Revision ID: 4c7d2e9a1b35
Revises: 6899f8513645
Create Date: 2026-10-19 23:40:00.000000

The mobile Focus session log and running clock, moved off the device (#374):
one live clock (``finished_at`` null) plus the day's finished blocks. New table,
no backfill — the previous home was each browser's localStorage, which the
client uploads once on its first load against this schema.
Design: docs/focus-day-plan.md.

The live-clock index is an expression index, which autogenerate neither
proposes nor compares, so it is written by hand here.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4c7d2e9a1b35'
down_revision: Union[str, None] = '6899f8513645'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('focus_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('plan_date', sa.Date(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('leg_started_at', sa.DateTime(), nullable=True),
    sa.Column('accumulated_ms', sa.Integer(), server_default='0', nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('minutes', sa.Integer(), nullable=True),
    sa.Column('planned_minutes', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint('(finished_at IS NULL AND minutes IS NULL AND planned_minutes IS NULL) OR (finished_at IS NOT NULL AND leg_started_at IS NULL AND minutes IS NOT NULL AND planned_minutes IS NOT NULL)', name='ck_focus_sessions_kind'),
    sa.CheckConstraint('accumulated_ms >= 0 AND (minutes IS NULL OR minutes >= 0) AND (planned_minutes IS NULL OR planned_minutes >= 0)', name='ck_focus_sessions_non_negative'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('focus_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_focus_sessions_plan_date'), ['plan_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_focus_sessions_task_id'), ['task_id'], unique=False)
        batch_op.create_index('uq_focus_sessions_finished_block', ['plan_date', 'task_id'], unique=True, sqlite_where=sa.text('finished_at IS NOT NULL AND deleted_at IS NULL'))
    op.create_index('uq_focus_sessions_live_clock', 'focus_sessions', [sa.text('(finished_at IS NULL)')], unique=True, sqlite_where=sa.text('finished_at IS NULL AND deleted_at IS NULL'))


def downgrade() -> None:
    op.drop_index('uq_focus_sessions_live_clock', table_name='focus_sessions')
    with op.batch_alter_table('focus_sessions', schema=None) as batch_op:
        batch_op.drop_index('uq_focus_sessions_finished_block')
        batch_op.drop_index(batch_op.f('ix_focus_sessions_task_id'))
        batch_op.drop_index(batch_op.f('ix_focus_sessions_plan_date'))

    op.drop_table('focus_sessions')
