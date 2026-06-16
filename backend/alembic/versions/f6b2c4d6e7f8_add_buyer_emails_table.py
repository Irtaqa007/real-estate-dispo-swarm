"""add_buyer_emails_table

Revision ID: f6b2c4d6e7f8
Revises: fac01b5a3b28
Create Date: 2026-06-16 13:30:00.000000

Adds a buyer_emails table to support multiple active emails per buyer.
The primary email remains in buyers.email; additional emails go here.
Also adds a unique constraint on (buyer_id, email) to prevent duplicates
per buyer, and indexes for fast email lookups used by reply processing.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "f6b2c4d6e7f8"
down_revision: Union[str, None] = "e5a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "buyer_emails",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("email", sa.Text(), nullable=False, index=True),
        sa.Column("email_verified", sa.Boolean(), default=False),
        sa.Column("email_verification_status", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("buyer_id", "email", name="uq_buyer_emails_buyer_email"),
    )


def downgrade() -> None:
    op.drop_table("buyer_emails")
