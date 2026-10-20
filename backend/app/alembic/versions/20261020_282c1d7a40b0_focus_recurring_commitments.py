"""focus recurring commitments

Revision ID: 282c1d7a40b0
Revises: 4c7d2e9a1b35
Create Date: 2026-10-20 08:31:18.319759

Recurring commitments (issue #376). Two nullable columns on
``focus_plan_entries``:

* ``repeat_interval`` turns a commitment into a series that recurs from its
  ``plan_date`` on (the task cadence shape). Occurrences are read per day and
  never materialised.
* ``series_id`` marks a fourth kind of row, a **skip**: that series'
  occurrence on ``plan_date`` doesn't happen.

The kind check constraint is widened to admit skips and to keep the new
columns off the other kinds; changing a CHECK on SQLite means batch mode
rebuilds the table (copy, drop, rename), which keeps every existing row as-is.
Every existing row has both new columns null, so it stays the kind it was and
satisfies the new check. No backfill.
Design: docs/focus-day-plan.md.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '282c1d7a40b0'
down_revision: Union[str, None] = '4c7d2e9a1b35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_KIND = (
    "(task_id IS NOT NULL AND title IS NULL AND duration_minutes IS NULL)"
    " OR (task_id IS NULL AND title IS NOT NULL"
    " AND start_minute IS NOT NULL AND duration_minutes IS NOT NULL)"
)
_NEW_KIND = (
    "(task_id IS NOT NULL AND series_id IS NULL AND title IS NULL"
    " AND duration_minutes IS NULL AND repeat_interval IS NULL)"
    " OR (task_id IS NULL AND series_id IS NULL AND title IS NOT NULL"
    " AND start_minute IS NOT NULL AND duration_minutes IS NOT NULL)"
    " OR (series_id IS NOT NULL AND task_id IS NULL AND title IS NULL"
    " AND start_minute IS NULL AND duration_minutes IS NULL"
    " AND repeat_interval IS NULL)"
)


def upgrade() -> None:
    with op.batch_alter_table('focus_plan_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('repeat_interval', sa.JSON(none_as_null=True), nullable=True))
        batch_op.add_column(sa.Column('series_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_focus_plan_entries_series_id', 'focus_plan_entries', ['series_id'], ['id']
        )
        batch_op.drop_constraint('ck_focus_plan_entries_kind', type_='check')
        batch_op.create_check_constraint('ck_focus_plan_entries_kind', sa.text(_NEW_KIND))
        batch_op.create_index(
            'uq_focus_plan_entries_live_skip',
            ['series_id', 'plan_date'],
            unique=True,
            sqlite_where=sa.text('series_id IS NOT NULL AND deleted_at IS NULL'),
        )


def downgrade() -> None:
    # Series and skips have no shape in the old schema. Soft-deleted rows are
    # history and go too: the narrower check would refuse them all the same.
    op.execute(
        "DELETE FROM focus_plan_entries WHERE series_id IS NOT NULL"
    )
    op.execute(
        "UPDATE focus_plan_entries SET repeat_interval = NULL"
        " WHERE repeat_interval IS NOT NULL"
    )
    with op.batch_alter_table('focus_plan_entries', schema=None) as batch_op:
        batch_op.drop_index(
            'uq_focus_plan_entries_live_skip',
            sqlite_where=sa.text('series_id IS NOT NULL AND deleted_at IS NULL'),
        )
        batch_op.drop_constraint('ck_focus_plan_entries_kind', type_='check')
        batch_op.create_check_constraint('ck_focus_plan_entries_kind', sa.text(_OLD_KIND))
        batch_op.drop_constraint('fk_focus_plan_entries_series_id', type_='foreignkey')
        batch_op.drop_column('series_id')
        batch_op.drop_column('repeat_interval')
