"""split task review and workflow status

Revision ID: 9b2c1d7e4a6f
Revises: 3263531ae531
Create Date: 2026-06-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b2c1d7e4a6f"
down_revision: Union[str, None] = "3263531ae531"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            new_column_name="review_status",
            existing_type=sa.String(),
        )
        batch_op.add_column(
            sa.Column(
                "workflow_status",
                sa.String(),
                nullable=False,
                server_default="open",
            )
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE tasks
            SET workflow_status = 'done',
                review_status = 'accepted'
            WHERE review_status = 'done'
            """
        )
    )

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.alter_column("workflow_status", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE tasks
            SET review_status = 'done'
            WHERE workflow_status = 'done'
              AND review_status = 'accepted'
            """
        )
    )
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("workflow_status")
        batch_op.alter_column(
            "review_status",
            new_column_name="status",
            existing_type=sa.String(),
        )
