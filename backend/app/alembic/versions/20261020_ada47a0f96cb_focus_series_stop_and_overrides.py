"""focus series stop and overrides

Revision ID: ada47a0f96cb
Revises: 282c1d7a40b0
Create Date: 2026-10-20 12:39:18.560373

End a repeating commitment from a day on, and edit one day's occurrence
(issue #428):

* ``stop_date`` (nullable) on ``focus_plan_entries``: a series' first day
  without an occurrence. A new check keeps it to series rows and after the
  series' first day.
* A fifth row kind, the **override**: ``series_id`` plus a full title, start
  and length, for that series' occurrence on ``plan_date``. The kind check is
  widened to admit it; the existing live-skip unique index on
  ``(series_id, plan_date)`` already allows one live skip *or* override per
  day, so no new index.

Autogenerate only saw the column (it doesn't compare CHECK constraints); the
two check changes are written by hand. Changing a CHECK on SQLite means batch
mode rebuilds the table, which keeps every row as-is: existing rows have
``stop_date`` null and no override shape, so they satisfy both checks. No
backfill. Design: docs/focus-day-plan.md.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ada47a0f96cb'
down_revision: Union[str, None] = '282c1d7a40b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_KIND = (
    "(task_id IS NOT NULL AND series_id IS NULL AND title IS NULL"
    " AND duration_minutes IS NULL AND repeat_interval IS NULL)"
    " OR (task_id IS NULL AND series_id IS NULL AND title IS NOT NULL"
    " AND start_minute IS NOT NULL AND duration_minutes IS NOT NULL)"
    " OR (series_id IS NOT NULL AND task_id IS NULL AND title IS NULL"
    " AND start_minute IS NULL AND duration_minutes IS NULL"
    " AND repeat_interval IS NULL)"
)
_NEW_KIND = (
    _OLD_KIND
    + " OR (series_id IS NOT NULL AND task_id IS NULL AND title IS NOT NULL"
    " AND start_minute IS NOT NULL AND duration_minutes IS NOT NULL"
    " AND repeat_interval IS NULL)"
)
_STOP_DATE = "stop_date IS NULL OR (repeat_interval IS NOT NULL AND stop_date > plan_date)"


def upgrade() -> None:
    with op.batch_alter_table('focus_plan_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stop_date', sa.Date(), nullable=True))
        batch_op.drop_constraint('ck_focus_plan_entries_kind', type_='check')
        batch_op.create_check_constraint('ck_focus_plan_entries_kind', sa.text(_NEW_KIND))
        batch_op.create_check_constraint(
            'ck_focus_plan_entries_stop_date', sa.text(_STOP_DATE)
        )


def downgrade() -> None:
    # Overrides have no shape in the old schema, and the narrower check would
    # refuse them (live or soft-deleted alike), so they go. A series' end is
    # dropped with its column: the series repeats for good again.
    op.execute(
        "DELETE FROM focus_plan_entries WHERE series_id IS NOT NULL AND title IS NOT NULL"
    )
    with op.batch_alter_table('focus_plan_entries', schema=None) as batch_op:
        batch_op.drop_constraint('ck_focus_plan_entries_stop_date', type_='check')
        batch_op.drop_constraint('ck_focus_plan_entries_kind', type_='check')
        batch_op.create_check_constraint('ck_focus_plan_entries_kind', sa.text(_OLD_KIND))
        batch_op.drop_column('stop_date')
