"""conversation message idempotency key

Adds ``conversation_messages.idempotency_key`` (issue #502, delegate-api
v1.1): the delegate's ``Idempotency-Key`` stored on the user turn that opened
an exchange, so a retry under it replays the stored answer instead of running
the loop again. Looked up per conversation, hence the composite index.

Plain ``add_column`` and ``create_index``, as in ``184cfae0a4e9``: SQLite does
both in place, and a batch rebuild is not needed. No backfill; turns written
before this revision have no key.

Revision ID: 7d1c4e2a9b30
Revises: 184cfae0a4e9
Create Date: 2026-11-05 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7d1c4e2a9b30'
down_revision: Union[str, None] = '184cfae0a4e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'conversation_messages',
        sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    )
    op.create_index(
        'ix_conversation_messages_idempotency',
        'conversation_messages',
        ['conversation_id', 'idempotency_key'],
    )


def downgrade() -> None:
    op.drop_index('ix_conversation_messages_idempotency', table_name='conversation_messages')
    op.drop_column('conversation_messages', 'idempotency_key')
