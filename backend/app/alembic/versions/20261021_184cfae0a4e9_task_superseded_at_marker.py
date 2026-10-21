"""task superseded_at marker

Adds ``tasks.superseded_at`` (issue #457). Un-skipping a recurring occurrence
whose series already has a live successor rewinds that successor onto the
skipped date; the original skipped row and its trashed subtree used to be
hard-deleted at that point, a purge reachable from the agent and MCP. They now
stay soft-deleted and are stamped ``superseded_at`` instead, which takes them
out of the Tasks trash and makes ``restore_task`` refuse them.

No backfill: every row an un-skip made obsolete before this revision was purged
by it, so there is nothing left to mark.

Plain ``add_column``/``drop_column`` rather than autogenerate's
``batch_alter_table``: SQLite does both in place (``ALTER TABLE ... ADD/DROP
COLUMN``), and a batch table rebuild would drop the ``tasks_fts`` sync
triggers that ``e3f1a9c2b7d4`` hangs on ``tasks``. Autogenerate proposed no
``_litestream_lock``/``_litestream_seq`` ops this time (it was run against a
scratch database); nothing else was changed by hand.

Revision ID: 184cfae0a4e9
Revises: e3f1a9c2b7d4
Create Date: 2026-10-21 12:26:34.487486

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '184cfae0a4e9'
down_revision: Union[str, None] = 'e3f1a9c2b7d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('superseded_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('tasks', 'superseded_at')
