\"\"\"Add pass reason capture fields to Campaign, Deal, and JVPartner.

This migration adds:
- Campaign: pass_reason_category, pass_reason_raw, pass_reason_confidence, passed_at
- Deal: pass_count, pass_reasons_summary
- JVPartner: title_issue_count, condition_issue_count, total_passes, pass_reasons_breakdown

Revision ID: j1k2l3m4n5o6
Revises: i9e5f7g8h0i1
Create Date: 2026-06-25 22:00:00.000000
\"\"\"

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "j1k2l3m4n5o6"
down_revision: Union[str, None] = "i9e5f7g8h0i1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Campaign pass reason columns
    op.add_column("campaigns", sa.Column("pass_reason_category", sa.Text(), nullable=True))
    op.add_column("campaigns", sa.Column("pass_reason_raw", sa.Text(), nullable=True))
    op.add_column("campaigns", sa.Column("pass_reason_confidence", sa.Text(), nullable=True))
    op.add_column("campaigns", sa.Column("passed_at", sa.DateTime(timezone=True), nullable=True))

    # Deal pass intelligence columns
    op.add_column("deals", sa.Column("pass_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("deals", sa.Column("pass_reasons_summary", JSONB(), nullable=True))

    # JVPartner pass intelligence columns
    op.add_column("jv_partners", sa.Column("title_issue_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("jv_partners", sa.Column("condition_issue_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("jv_partners", sa.Column("total_passes", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("jv_partners", sa.Column("pass_reasons_breakdown", JSONB(), nullable=True))


def downgrade() -> None:
    # Remove Campaign columns
    op.drop_column("campaigns", "passed_at")
    op.drop_column("campaigns", "pass_reason_confidence")
    op.drop_column("campaigns", "pass_reason_raw")
    op.drop_column("campaigns", "pass_reason_category")

    # Remove Deal columns
    op.drop_column("deals", "pass_reasons_summary")
    op.drop_column("deals", "pass_count")

    # Remove JVPartner columns
    op.drop_column("jv_partners", "pass_reasons_breakdown")
    op.drop_column("jv_partners", "total_passes")
    op.drop_column("jv_partners", "condition_issue_count")
    op.drop_column("jv_partners", "title_issue_count")
