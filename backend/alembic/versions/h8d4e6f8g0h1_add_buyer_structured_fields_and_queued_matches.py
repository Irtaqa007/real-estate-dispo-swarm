"""add_buyer_structured_fields_and_queued_matches

Revision ID: h8d4e6f8g0h1
Revises: g7c3d5e7f9g0
Create Date: 2026-06-16 15:00:00.000000

Adds structured fields to buyers for hard-filter matching:
- price_min, price_max: Numeric price range
- pref_property_type: House, Land, or NULL for both
- pref_cities: ARRAY(Text) of preferred cities/areas

Creates queued_deal_matches table for buyers at their 2-deal cap,
with status tracking (waiting, invalidated, released, expired).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY


# revision identifiers, used by Alembic.
revision: str = "h8d4e6f8g0h1"
down_revision: Union[str, None] = "g7c3d5e7f9g0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add structured filter columns to buyers
    op.add_column("buyers", sa.Column("price_min", sa.Numeric(19, 2), nullable=True))
    op.add_column("buyers", sa.Column("price_max", sa.Numeric(19, 2), nullable=True))
    op.add_column("buyers", sa.Column("pref_property_type", sa.Text(), nullable=True))
    op.add_column("buyers", sa.Column("pref_cities", ARRAY(sa.Text()), nullable=True))

    # Create queued_deal_matches table
    op.create_table(
        "queued_deal_matches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("deal_id", UUID(as_uuid=True), sa.ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.Text(), default="waiting", index=True),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("queued_deal_matches")
    op.drop_column("buyers", "price_min")
    op.drop_column("buyers", "price_max")
    op.drop_column("buyers", "pref_property_type")
    op.drop_column("buyers", "pref_cities")
