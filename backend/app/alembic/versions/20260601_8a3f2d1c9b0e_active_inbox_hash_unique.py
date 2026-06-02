"""unique active inbox input hash

Revision ID: 8a3f2d1c9b0e
Revises: 09002cc3cb7c
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a3f2d1c9b0e"
down_revision: Union[str, None] = "09002cc3cb7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            """
            SELECT input_hash, COUNT(*) AS count
            FROM inbox_items
            WHERE deleted_at IS NULL
            GROUP BY input_hash
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot add unique active inbox hash index: duplicate active "
            f"inbox_items.input_hash={duplicate.input_hash!r} exists."
        )

    with op.batch_alter_table("inbox_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_inbox_items_input_hash"))
        batch_op.create_index(
            "uq_inbox_items_active_input_hash",
            ["input_hash"],
            unique=True,
            sqlite_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    with op.batch_alter_table("inbox_items", schema=None) as batch_op:
        batch_op.drop_index("uq_inbox_items_active_input_hash")
        batch_op.create_index(
            batch_op.f("ix_inbox_items_input_hash"), ["input_hash"], unique=False
        )
