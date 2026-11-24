"""conversation message answer

Adds ``conversation_messages.answer`` (conductor#241, delegate-api v1.3): a
read's answer in PCC's own words, ``{text, spoken, untrusted}``, on the
assistant turn of an agent caller's run that changed nothing, kept so a
retried exchange replays it. A plain ``add_column``, as in ``b2e5c71f04a8``.
No backfill; turns written before this revision have none.

Revision ID: c4a91e7d52b3
Revises: b2e5c71f04a8
Create Date: 2026-11-24 12:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4a91e7d52b3"
down_revision: Union[str, None] = "b2e5c71f04a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversation_messages", sa.Column("answer", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation_messages", "answer")
