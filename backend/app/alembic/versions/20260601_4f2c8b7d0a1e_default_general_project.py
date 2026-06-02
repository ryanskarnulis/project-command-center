"""default general project

Revision ID: 4f2c8b7d0a1e
Revises: 8a3f2d1c9b0e
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4f2c8b7d0a1e"
down_revision: Union[str, None] = "8a3f2d1c9b0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GENERAL_KEY = "general"


def upgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("system_key", sa.String(), nullable=True))
        batch_op.create_unique_constraint("uq_projects_system_key", ["system_key"])

    bind = op.get_bind()
    existing_general = bind.execute(
        sa.text(
            """
            SELECT id
            FROM projects
            WHERE deleted_at IS NULL AND lower(name) = 'general'
            ORDER BY id
            LIMIT 1
            """
        )
    ).scalar_one_or_none()

    if existing_general is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO projects (name, description, system_key)
                VALUES ('General', 'Default project for unfiled tasks', :system_key)
                """
            ),
            {"system_key": GENERAL_KEY},
        )
    else:
        bind.execute(
            sa.text("UPDATE projects SET system_key = :system_key WHERE id = :id"),
            {"system_key": GENERAL_KEY, "id": existing_general},
        )

    general_id = bind.execute(
        sa.text("SELECT id FROM projects WHERE system_key = :system_key"),
        {"system_key": GENERAL_KEY},
    ).scalar_one()
    bind.execute(
        sa.text(
            """
            UPDATE tasks
            SET project_id = :general_id
            WHERE deleted_at IS NULL
              AND project_id IS NULL
              AND status IN ('accepted', 'done')
            """
        ),
        {"general_id": general_id},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE projects
            SET system_key = NULL
            WHERE system_key = :system_key
            """
        ),
        {"system_key": GENERAL_KEY},
    )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_constraint("uq_projects_system_key", type_="unique")
        batch_op.drop_column("system_key")
