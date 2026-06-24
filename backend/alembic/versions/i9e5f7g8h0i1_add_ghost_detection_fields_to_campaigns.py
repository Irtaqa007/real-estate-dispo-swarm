"""add_ghost_detection_fields_to_campaigns

Revision ID: i9e5f7g8h0i1
Revises: h8d4e6f8g0h1
Create Date: 2026-06-24 10:00:00.000000

Adds ghost detection and recovery fields to campaigns table:
- ghost_detected_at: DateTime when ghost was first detected
- ghost_recovery_touch: int tracking which recovery touch we're on (0 = not in recovery)
- ghost_recovery_sent_at: DateTime when last recovery touch was sent
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "i9e5f7g8h0i1"
down_revision: Union[str, None] = "h8d4e6f8g0h1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("ghost_detected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("ghost_recovery_touch", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "campaigns",
        sa.Column("ghost_recovery_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "ghost_detected_at")
    op.drop_column("campaigns", "ghost_recovery_touch")
    op.drop_column("campaigns", "ghost_recovery_sent_at")
