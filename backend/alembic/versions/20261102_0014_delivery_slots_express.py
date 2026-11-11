"""delivery slots, urgent delivery, paid delivery below the minimum order

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-02 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROFILE = [
    sa.Column('small_order_fee_amd', sa.BigInteger(), nullable=True),
    sa.Column('window_from', sa.Time(), nullable=False, server_default='09:00'),
    sa.Column('window_to', sa.Time(), nullable=False, server_default='18:00'),
    sa.Column('slot_hours', sa.SmallInteger(), nullable=False, server_default='0'),
    sa.Column('express_fee_amd', sa.BigInteger(), nullable=True),
    sa.Column('express_until', sa.Time(), nullable=False, server_default='12:00'),
    sa.Column('express_hours', sa.SmallInteger(), nullable=False, server_default='3'),
    sa.Column('express_min_amd', sa.BigInteger(), nullable=False, server_default='0'),
]
ORDER = [
    sa.Column('slot_from', sa.Time(), nullable=True),
    sa.Column('slot_to', sa.Time(), nullable=True),
    sa.Column('express', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('express_fee_amd', sa.BigInteger(), nullable=False, server_default='0'),
]


def upgrade() -> None:
    for c in PROFILE:
        op.add_column('seller_profiles', c)
    for c in ORDER:
        op.add_column('orders', c)


def downgrade() -> None:
    for c in ORDER:
        op.drop_column('orders', c.name)
    for c in PROFILE:
        op.drop_column('seller_profiles', c.name)
