"""add_missing_composite_indexes

Adds composite indexes for frequently-queried column combinations
that lack a covering index:

- campaigns (sent_at, status): dashboard today-count queries,
  scheduler campaign-processing queries
- deals (status, created_at): dashboard pipeline counts,
  deal listing ordered by creation date within a status
- queued_deal_matches (buyer_id, status): scheduler queued-match
  release queries filtering by buyer_id AND status='waiting'

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-07-12 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "p6q7r8s9t0u1"
down_revision: Union[str, None] = "o5p6q7r8s9t0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── campaigns (sent_at, status): dashboard today-email counts,
    #    scheduler campaign-processing queries ──
    op.create_index(
        "ix_campaigns_sent_at_status",
        "campaigns",
        ["sent_at", "status"],
        unique=False,
        postgresql_where=sa.text("sent_at IS NOT NULL"),
    )

    # ── deals (status, created_at): dashboard pipeline counts,
    #    deal listing within a status ordered by creation date ──
    op.create_index(
        "ix_deals_status_created_at",
        "deals",
        ["status", "created_at"],
        unique=False,
    )

    # ── queued_deal_matches (buyer_id, status): scheduler release
    #    queries that filter by buyer_id AND status='waiting' ──
    op.create_index(
        "ix_queued_matches_buyer_status",
        "queued_deal_matches",
        ["buyer_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_queued_matches_buyer_status", table_name="queued_deal_matches")
    op.drop_index("ix_deals_status_created_at", table_name="deals")
    op.drop_index("ix_campaigns_sent_at_status", table_name="campaigns")
