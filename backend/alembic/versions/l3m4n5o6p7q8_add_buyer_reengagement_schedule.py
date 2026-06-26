"""add_buyer_reengagement_schedule

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
Create Date: 2026-06-27 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "l3m4n5o6p7q8"
down_revision: Union[str, None] = "k2l3m4n5o6p7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "buyer_reengagement_schedule",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("buyer_id", sa.UUID(), nullable=False),
        sa.Column("deal_id", sa.UUID(), nullable=True),
        sa.Column("stated_window_raw", sa.Text(), nullable=False),
        sa.Column("target_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="waiting"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["buyer_id"], ["buyers.id"], ),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_buyer_reengagement_schedule_buyer_id"), "buyer_reengagement_schedule", ["buyer_id"], unique=False)
    op.create_index(op.f("ix_buyer_reengagement_schedule_target_date"), "buyer_reengagement_schedule", ["target_date"], unique=False)
    op.create_index(op.f("ix_buyer_reengagement_schedule_status"), "buyer_reengagement_schedule", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_buyer_reengagement_schedule_status"), table_name="buyer_reengagement_schedule")
    op.drop_index(op.f("ix_buyer_reengagement_schedule_target_date"), table_name="buyer_reengagement_schedule")
    op.drop_index(op.f("ix_buyer_reengagement_schedule_buyer_id"), table_name="buyer_reengagement_schedule")
    op.drop_table("buyer_reengagement_schedule")
