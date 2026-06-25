"""project alias normalized + unique

Revision ID: 7ebcc24824c9
Revises: 5be1ff02ca06
Create Date: 2026-06-25 00:00:00.000000

Adds ``project_aliases.normalized_alias`` (the dedupe key) plus a partial unique
index over active rows so duplicate / case-variant aliases can't be added.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7ebcc24824c9'
down_revision: Union[str, None] = '5be1ff02ca06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable first so existing rows are accepted, then backfill. The SQL
    # backfill lowercases + trims but can't collapse interior whitespace (rare
    # in existing low-volume alias data); the column is authoritative going
    # forward via services.projects._normalize.
    with op.batch_alter_table('project_aliases', schema=None) as batch_op:
        batch_op.add_column(sa.Column('normalized_alias', sa.String(), nullable=True))

    op.execute(
        "UPDATE project_aliases SET normalized_alias = lower(trim(alias)) "
        "WHERE normalized_alias IS NULL"
    )

    with op.batch_alter_table('project_aliases', schema=None) as batch_op:
        batch_op.alter_column('normalized_alias', nullable=False)
        batch_op.create_index(
            'uq_project_alias_normalized',
            ['project_id', 'normalized_alias'],
            unique=True,
            sqlite_where=sa.text('deleted_at IS NULL'),
        )


def downgrade() -> None:
    with op.batch_alter_table('project_aliases', schema=None) as batch_op:
        batch_op.drop_index('uq_project_alias_normalized')
        batch_op.drop_column('normalized_alias')
