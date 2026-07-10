"""drop project_aliases

Revision ID: ce33607756a9
Revises: 019a9b406cae
Create Date: 2026-07-09 20:36:54.328685

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ce33607756a9'
down_revision: Union[str, None] = '019a9b406cae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('project_aliases', schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f('uq_project_alias_normalized'),
            sqlite_where=sa.text('deleted_at IS NULL'),
        )

    op.drop_table('project_aliases')


def downgrade() -> None:
    op.create_table(
        'project_aliases',
        sa.Column('id', sa.INTEGER(), nullable=False),
        sa.Column('project_id', sa.INTEGER(), nullable=False),
        sa.Column('alias', sa.VARCHAR(), nullable=False),
        sa.Column(
            'created_at',
            sa.DATETIME(),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DATETIME(),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column('deleted_at', sa.DATETIME(), nullable=True),
        sa.Column('normalized_alias', sa.VARCHAR(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('project_aliases', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('uq_project_alias_normalized'),
            ['project_id', 'normalized_alias'],
            unique=True,
            sqlite_where=sa.text('deleted_at IS NULL'),
        )
