"""fix net_spread_formula column type: varchar -> numeric(12,2) with computed expression

Revision ID: 0e7a3b1c9d5f
Revises: fac01b5a3b28
Create Date: 2026-06-13 12:00:00.000000

The column was manually added as character varying but the SQLAlchemy model
defines it as Numeric(12, 2) with a Computed expression. This mismatch caused:
- InvalidRequestError: Unknown PG numeric type: 1043

The fix drops the varchar column and recreates it as a proper generated column
with numeric(12,2) type. Since the column contains derived data (computed from
asking_price, contract_price, and repair_estimate), no data is lost — PostgreSQL
recomputes the values from the source columns.

Also includes all other columns that exist in the model but are missing from
the database due to model-only additions without migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0e7a3b1c9d5f"
down_revision: Union[str, None] = "fac01b5a3b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Fix net_spread_formula: drop varchar, recreate as numeric GENERATED ALWAYS
    # ------------------------------------------------------------------
    # Drop the existing varchar column (if it exists)
    op.drop_column("deals", "net_spread_formula")

    # Recreate with correct numeric type and computed expression
    op.add_column(
        "deals",
        sa.Column(
            "net_spread_formula",
            sa.Numeric(precision=12, scale=2),
            sa.Computed(
                "(asking_price - contract_price) - COALESCE(repair_estimate, 0)"
            ),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # 2. Add missing columns to buyers table
    # ------------------------------------------------------------------
    op.add_column(
        "buyers",
        sa.Column(
            "pitches_this_week",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=True,
        ),
    )
    op.add_column(
        "buyers",
        sa.Column(
            "pitches_this_week_reset_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "buyers",
        sa.Column("portfolio_insights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # ------------------------------------------------------------------
    # 3. Add missing columns to deals table
    # ------------------------------------------------------------------
    op.add_column(
        "deals",
        sa.Column(
            "priority_score",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=True,
        ),
    )
    op.add_column(
        "deals",
        sa.Column(
            "market_velocity",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # 4. Add missing columns to campaigns table
    # ------------------------------------------------------------------
    op.add_column(
        "campaigns",
        sa.Column(
            "question_round",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # 5. Create failed_campaigns table (model exists but table was never created)
    # ------------------------------------------------------------------
    op.create_table(
        "failed_campaigns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("last_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Reverse all changes in reverse order
    # ------------------------------------------------------------------
    op.drop_table("failed_campaigns")
    op.drop_column("campaigns", "question_round")
    op.drop_column("deals", "market_velocity")
    op.drop_column("deals", "priority_score")
    op.drop_column("buyers", "portfolio_insights")
    op.drop_column("buyers", "pitches_this_week_reset_at")
    op.drop_column("buyers", "pitches_this_week")

    # Drop the computed column and restore as varchar (for downgrade safety)
    op.drop_column("deals", "net_spread_formula")
    op.add_column(
        "deals",
        sa.Column("net_spread_formula", sa.Text(), nullable=True),
    )
