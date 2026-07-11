"""activity events actor column

Nullable ``actor`` on the append-only audit log: NULL means the user (every
pre-agent row stays correct with no backfill); agents stamp an identifier
such as "agent:mcp". Autogen also proposed dropping litestream's internal
``_litestream_*`` tables — removed by hand, they belong to the replication
tool, not the app schema.

Revision ID: 779903a6416c
Revises: 3ef5332305e3
Create Date: 2026-07-10 20:17:10.234572

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '779903a6416c'
down_revision: Union[str, None] = '3ef5332305e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('activity_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('actor', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('activity_events', schema=None) as batch_op:
        batch_op.drop_column('actor')
