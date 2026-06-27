"""add_campaign_indexes_and_missing_indexes

Revision ID: m1n2o3p4q5r6
Revises: l3m4n5o6p7q8
Create Date: 2026-06-27 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "m1n2o3p4q5r6"
down_revision: Union[str, None] = "l3m4n5o6p7q8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Campaign column indexes ──
    op.create_index(op.f("ix_campaigns_deal_id"), "campaigns", ["deal_id"], unique=False)
    op.create_index(op.f("ix_campaigns_buyer_id"), "campaigns", ["buyer_id"], unique=False)
    op.create_index(op.f("ix_campaigns_status"), "campaigns", ["status"], unique=False)
    op.create_index(op.f("ix_campaigns_sent_at"), "campaigns", ["sent_at"], unique=False)
    op.create_index(op.f("ix_campaigns_scheduled_send_at"), "campaigns", ["scheduled_send_at"], unique=False)
    op.create_index(op.f("ix_campaigns_ghost_detected_at"), "campaigns", ["ghost_detected_at"], unique=False)

    # ── Campaign composite indexes ──
    op.create_index("ix_campaigns_status_scheduled", "campaigns", ["status", "scheduled_send_at"], unique=False)
    op.create_index("ix_campaigns_buyer_deal", "campaigns", ["buyer_id", "deal_id"], unique=False)

    # ── ActivityLog resolved columns ──
    op.add_column("activity_log", sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("activity_log", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))

    # ── BuyerReengagementSchedule deal_id index ──
    op.create_index(
        op.f("ix_buyer_reengagement_schedule_deal_id"),
        "buyer_reengagement_schedule",
        ["deal_id"],
        unique=False,
    )


def downgrade() -> None:
    # ── BuyerReengagementSchedule deal_id index ──
    op.drop_index(
        op.f("ix_buyer_reengagement_schedule_deal_id"),
        table_name="buyer_reengagement_schedule",
    )

    # ── ActivityLog resolved columns ──
    op.drop_column("activity_log", "resolved_at")
    op.drop_column("activity_log", "resolved")

    # ── Campaign composite indexes ──
    op.drop_index("ix_campaigns_buyer_deal", table_name="campaigns")
    op.drop_index("ix_campaigns_status_scheduled", table_name="campaigns")

    # ── Campaign column indexes ──
    op.drop_index(op.f("ix_campaigns_ghost_detected_at"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_scheduled_send_at"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_sent_at"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_status"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_buyer_id"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_deal_id"), table_name="campaigns")
