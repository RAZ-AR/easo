"""rename service to Easo: demo users move from @qb.local to @easo.local

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-01 16:45:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    ('users', 'email'),
    ('user_identities', 'email'),
    ('user_identities', 'external_id'),
    ('login_codes', 'email'),
    ('organizations', 'email'),
]


def _move(old: str, new: str) -> None:
    for table, column in COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = replace({column}, '{old}', '{new}') WHERE {column} LIKE '%{old}'"
        )


def upgrade() -> None:
    _move('@qb.local', '@easo.local')


def downgrade() -> None:
    _move('@easo.local', '@qb.local')
