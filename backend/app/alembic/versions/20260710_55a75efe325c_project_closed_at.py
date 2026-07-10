"""project closed_at

Revision ID: 55a75efe325c
Revises: e0e6a57e57ad
Create Date: 2026-07-10 11:15:20.638903

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '55a75efe325c'
down_revision: Union[str, None] = 'e0e6a57e57ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('closed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('closed_at')
