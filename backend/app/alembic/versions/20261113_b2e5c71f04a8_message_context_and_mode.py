"""conversation message context and mode

Adds ``conversation_messages.context`` and ``conversation_messages.mode``
(issue #527, delegate-api v1.2): the caller's entity refs and saved facts,
and ``"read"`` when the caller asked for no writes, kept with the user turn
that carried them for audit. Plain ``add_column``s, as in ``7d1c4e2a9b30``.
No backfill; turns written before this revision have neither.

Revision ID: b2e5c71f04a8
Revises: 7d1c4e2a9b30
Create Date: 2026-11-13 12:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2e5c71f04a8"
down_revision: Union[str, None] = "7d1c4e2a9b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversation_messages", sa.Column("context", sa.JSON(), nullable=True))
    op.add_column("conversation_messages", sa.Column("mode", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation_messages", "mode")
    op.drop_column("conversation_messages", "context")
