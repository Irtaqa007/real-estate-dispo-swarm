"""add_unsubscribed_at_to_buyers

Revision ID: e5a1b2c3d4e5
Revises: d4f2a13b7c11
Create Date: 2026-06-13 18:00:00.000000

Adds unsubscribed_at column to the buyers table for email compliance
opt-out tracking. When a buyer unsubscribes (via link or reply), this
timestamp is set and future campaigns skip them.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5a1b2c3d4e5"
down_revision: Union[str, None] = "d4f2a13b7c11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "buyers",
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("buyers", "unsubscribed_at")
