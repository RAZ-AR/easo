"""rename service to Easo: new order numbers start with EASO-

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-01 16:30:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMBER = "'{}-' || to_char(now(), 'YY') || '-' || lpad(nextval('order_number_seq')::text, 6, '0')"


def upgrade() -> None:
    # Existing orders keep their QB- numbers: they are already printed on documents
    op.alter_column('orders', 'number', server_default=sa.text(NUMBER.format('EASO')))


def downgrade() -> None:
    op.alter_column('orders', 'number', server_default=sa.text(NUMBER.format('QB')))
