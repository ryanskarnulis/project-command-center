"""strip ai/training/inbox tables

Drops the tables and task columns that backed the removed AI subsystem, training
pipeline, inbox, and Discord capture: ``ai_training_examples``, ``eval_runs``,
``inbox_items``, and the ``tasks.inbox_item_id`` / ``tasks.breakdown_output_json``
columns (plus the ``tasks -> inbox_items`` foreign key).

The ``tasks`` foreign key and columns are dropped FIRST, while ``inbox_items``
still exists, so SQLite batch-mode reflection of ``tasks`` doesn't chase a
foreign key to an already-dropped table. The downgrade recreates the schema
(data is not restored — the training corpus was disposable) and, symmetrically,
recreates ``inbox_items`` before re-adding the ``tasks`` foreign key to it.

Revision ID: 019a9b406cae
Revises: 76394c84cc39
Create Date: 2026-07-09 12:16:35.307240

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '019a9b406cae'
down_revision: Union[str, None] = '76394c84cc39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the task FK + columns first, while inbox_items still exists, so batch
    # reflection of `tasks` can resolve its foreign key.
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_tasks_inbox_item_id'), type_='foreignkey')
        batch_op.drop_column('breakdown_output_json')
        batch_op.drop_column('inbox_item_id')

    with op.batch_alter_table('inbox_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('uq_inbox_items_active_input_hash'), sqlite_where=sa.text('deleted_at IS NULL'))
    op.drop_table('inbox_items')

    with op.batch_alter_table('eval_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_runs_suite'))
    op.drop_table('eval_runs')

    op.drop_table('ai_training_examples')


def downgrade() -> None:
    op.create_table('ai_training_examples',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('task_name', sa.VARCHAR(), nullable=False),
    sa.Column('input_text', sa.VARCHAR(), nullable=False),
    sa.Column('model_output_json', sa.VARCHAR(), nullable=False),
    sa.Column('corrected_output_json', sa.VARCHAR(), nullable=True),
    sa.Column('accepted', sa.BOOLEAN(), nullable=False),
    sa.Column('model_profile', sa.VARCHAR(), nullable=False),
    sa.Column('model_name', sa.VARCHAR(), nullable=False),
    sa.Column('created_at', sa.DATETIME(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DATETIME(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('deleted_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('eval_runs',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('suite', sa.VARCHAR(), nullable=False),
    sa.Column('passed', sa.INTEGER(), nullable=False),
    sa.Column('total', sa.INTEGER(), nullable=False),
    sa.Column('created_at', sa.DATETIME(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DATETIME(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('eval_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_runs_suite'), ['suite'], unique=False)

    op.create_table('inbox_items',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('raw_text', sa.VARCHAR(), nullable=False),
    sa.Column('input_hash', sa.VARCHAR(), nullable=False),
    sa.Column('source', sa.VARCHAR(length=7), nullable=False),
    sa.Column('summary', sa.VARCHAR(), nullable=True),
    sa.Column('project_hint', sa.VARCHAR(), nullable=True),
    sa.Column('needs_review', sa.BOOLEAN(), nullable=False),
    sa.Column('processed_at', sa.DATETIME(), nullable=True),
    sa.Column('model_output_json', sa.VARCHAR(), nullable=True),
    sa.Column('model_name', sa.VARCHAR(), nullable=True),
    sa.Column('created_at', sa.DATETIME(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DATETIME(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('deleted_at', sa.DATETIME(), nullable=True),
    sa.Column('reviewed_at', sa.DATETIME(), nullable=True),
    sa.Column('suggested_project_id', sa.INTEGER(), nullable=True),
    sa.Column('match_input_text', sa.VARCHAR(), nullable=True),
    sa.Column('match_output_json', sa.VARCHAR(), nullable=True),
    sa.Column('match_model_name', sa.VARCHAR(), nullable=True),
    sa.Column('matched_alias', sa.VARCHAR(), nullable=True),
    sa.ForeignKeyConstraint(['suggested_project_id'], ['projects.id'], name=op.f('fk_inbox_items_suggested_project_id')),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('inbox_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('uq_inbox_items_active_input_hash'), ['input_hash'], unique=1, sqlite_where=sa.text('deleted_at IS NULL'))

    # inbox_items now exists, so the tasks FK to it can be re-created.
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('inbox_item_id', sa.INTEGER(), nullable=True))
        batch_op.add_column(sa.Column('breakdown_output_json', sa.VARCHAR(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_tasks_inbox_item_id'), 'inbox_items', ['inbox_item_id'], ['id'])
