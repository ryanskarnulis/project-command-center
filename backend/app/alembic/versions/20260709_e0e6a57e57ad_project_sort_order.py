"""project sort_order

Revision ID: e0e6a57e57ad
Revises: ce33607756a9
Create Date: 2026-07-09 23:06:30.569328

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e0e6a57e57ad'
down_revision: Union[str, None] = 'ce33607756a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False)
        )
    # Existing projects keep their current (id) order.
    op.execute('UPDATE projects SET sort_order = id')


def downgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('sort_order')
