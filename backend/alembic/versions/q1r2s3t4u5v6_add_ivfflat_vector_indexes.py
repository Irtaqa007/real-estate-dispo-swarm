"""add_ivfflat_vector_indexes

Adds IVFFlat approximate-nearest-neighbor indexes for the two embedding
(vector) columns used by the matching service:

- buyers.buy_box_embedding (1024-dim): used with <=> (cosine distance)
- deals.deal_embedding (1024-dim): used with <=> (cosine distance)

Without these indexes, every vector similarity search performs a full
table scan and sorts every row — O(n) per query. With IVFFlat, the
search is O(log n) and returns results in milliseconds.

The lists parameter is set to 100, which is appropriate for tables with
up to ~1M rows with 1024-dimensional vectors (lists = dim/10).

Both indexes use the vector_cosine_ops operator class to match the
`<=>` (cosine distance) operator used in queries.

Revision ID: q1r2s3t4u5v6
Revises: p6q7r8s9t0u1
Create Date: 2026-07-12 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "q1r2s3t4u5v6"
down_revision: Union[str, None] = "p6q7r8s9t0u1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Bump maintenance_work_mem for the index builds ──
    # IVFFlat index creation on 1024-dim vectors requires ~41 MB of working
    # memory per table being indexed. The default maintenance_work_mem on
    # many managed PostgreSQL instances (e.g. Supabase free tier) is only
    # 32 MB, which causes ProgramLimitExceededError. This session-level SET
    # is scoped to this migration's connection and is safe (no SUPERUSER
    # needed for session-level SET).
    op.execute("SET maintenance_work_mem = '64MB'")

    # ── IVFFlat index on buyers.buy_box_embedding ──
    # Used by matching_service.py:
    #   WHERE ... b.buy_box_embedding IS NOT NULL
    #     AND GREATEST(0, 1 - (b.buy_box_embedding <=> :deal_embedding)) >= :threshold
    #   ORDER BY b.buy_box_embedding <=> CAST(:deal_embedding AS vector)
    #
    # vector_cosine_ops is the required operator class for the <=> operator.
    # lists=100 is appropriate for 1024-dim vectors with up to ~1M rows.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_buyers_buy_box_embedding
        ON buyers
        USING ivfflat (buy_box_embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )

    # ── IVFFlat index on deals.deal_embedding ──
    # Used by deal_dedup.py and for future deal-to-deal similarity queries.
    # Same vector_cosine_ops operator class.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_deals_deal_embedding
        ON deals
        USING ivfflat (deal_embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_buyers_buy_box_embedding")
    op.execute("DROP INDEX IF EXISTS ix_deals_deal_embedding")
