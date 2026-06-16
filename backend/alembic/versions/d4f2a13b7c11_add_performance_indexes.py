"""add_performance_indexes

Revision ID: d4f2a13b7c11
Revises: c3fe08c19ebe
Create Date: 2026-06-13 17:15:00.000000

Adds indexes on foreign key columns and frequently-queried columns
to improve JOIN and WHERE performance as data grows.

Indexes added:
- ix_campaigns_deal_id          (campaigns.deal_id)
- ix_campaigns_buyer_id         (campaigns.buyer_id)
- ix_campaigns_status           (campaigns.status)
- ix_deals_assigned_buyer_id    (deals.assigned_buyer_id)
- ix_deals_jv_partner_id        (deals.jv_partner_id)
- ix_deals_status               (deals.status)
- ix_failed_campaigns_campaign_id (failed_campaigns.campaign_id)
- ix_email_verifications_buyer_id (email_verifications.buyer_id)
- ix_buyers_status              (buyers.status)
- ix_activity_log_entity        (activity_log.entity_type, activity_log.entity_id)
- ix_activity_log_created_at    (activity_log.created_at)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4f2a13b7c11"
down_revision: Union[str, None] = "c3fe08c19ebe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- campaigns ---
    op.create_index("ix_campaigns_deal_id", "campaigns", ["deal_id"])
    op.create_index("ix_campaigns_buyer_id", "campaigns", ["buyer_id"])
    op.create_index("ix_campaigns_status", "campaigns", ["status"])

    # --- deals ---
    op.create_index("ix_deals_assigned_buyer_id", "deals", ["assigned_buyer_id"])
    op.create_index("ix_deals_jv_partner_id", "deals", ["jv_partner_id"])
    op.create_index("ix_deals_status", "deals", ["status"])

    # --- failed_campaigns ---
    op.create_index(
        "ix_failed_campaigns_campaign_id",
        "failed_campaigns",
        ["campaign_id"],
    )

    # --- email_verifications ---
    op.create_index(
        "ix_email_verifications_buyer_id",
        "email_verifications",
        ["buyer_id"],
    )

    # --- buyers ---
    op.create_index("ix_buyers_status", "buyers", ["status"])

    # --- activity_log (composite for entity lookups + date ordering) ---
    op.create_index(
        "ix_activity_log_entity",
        "activity_log",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "ix_activity_log_created_at",
        "activity_log",
        ["created_at"],
    )


def downgrade() -> None:
    # --- activity_log ---
    op.drop_index("ix_activity_log_entity")
    op.drop_index("ix_activity_log_created_at")

    # --- buyers ---
    op.drop_index("ix_buyers_status")

    # --- email_verifications ---
    op.drop_index("ix_email_verifications_buyer_id")

    # --- failed_campaigns ---
    op.drop_index("ix_failed_campaigns_campaign_id")

    # --- deals ---
    op.drop_index("ix_deals_status")
    op.drop_index("ix_deals_jv_partner_id")
    op.drop_index("ix_deals_assigned_buyer_id")

    # --- campaigns ---
    op.drop_index("ix_campaigns_status")
    op.drop_index("ix_campaigns_buyer_id")
    op.drop_index("ix_campaigns_deal_id")
