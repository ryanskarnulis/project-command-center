"""tasks always filed

Revision ID: 93bfbc8f40ab
Revises: 0b40bab55cb4
Create Date: 2026-08-08 13:00:33.366095

Backfill every remaining unfiled task into General, then make ``project_id``
NOT NULL so unfiled becomes unrepresentable rather than merely unlikely.

The service layer has filed on write since Sprint 6/7 (``tasks._default_project_id``
— "tasks are always filed"), but the column stayed nullable and the 2026-06-01
default-project migration only backfilled ``status IN ('accepted', 'done')``,
leaving the rows whose status was neither. Those survivors are what the
dashboard's "N unfiled" link has been counting: history, not new capture.

Soft-deleted rows are backfilled too. A trashed unfiled task would otherwise
restore into a state the schema no longer allows, and ``task_trash`` already
rehomes a restored task whose project is gone.

The downgrade only relaxes the constraint. Which rows were NULL is not
recoverable — the same one-way trade every backfill migration here makes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '93bfbc8f40ab'
down_revision: Union[str, None] = '0b40bab55cb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GENERAL_KEY = "general"


def _general_project_id(bind: sa.engine.Connection) -> int:
    """Id of the General project, creating it if this DB somehow lacks one.

    Normally 4f2c8b7d0a1e has already created it. Re-creating it here keeps the
    backfill self-sufficient rather than crashing on a DB where General was
    purged, and matches ``projects.ensure_default_project_id``'s own contract.
    """
    general_id = bind.execute(
        sa.text("SELECT id FROM projects WHERE system_key = :key"),
        {"key": GENERAL_KEY},
    ).scalar_one_or_none()
    if general_id is not None:
        return int(general_id)

    bind.execute(
        sa.text(
            """
            INSERT INTO projects (name, description, system_key, created_at, updated_at)
            VALUES ('General', 'Default project for unfiled tasks', :key,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ),
        {"key": GENERAL_KEY},
    )
    return int(
        bind.execute(
            sa.text("SELECT id FROM projects WHERE system_key = :key"),
            {"key": GENERAL_KEY},
        ).scalar_one()
    )


def upgrade() -> None:
    bind = op.get_bind()
    unfiled = bind.execute(
        sa.text("SELECT COUNT(*) FROM tasks WHERE project_id IS NULL")
    ).scalar_one()

    if unfiled:
        general_id = _general_project_id(bind)
        bind.execute(
            sa.text("UPDATE tasks SET project_id = :general_id WHERE project_id IS NULL"),
            {"general_id": general_id},
        )

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column(
            "project_id", existing_type=sa.Integer(), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column(
            "project_id", existing_type=sa.Integer(), nullable=True
        )
