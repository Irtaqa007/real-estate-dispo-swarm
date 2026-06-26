"""Add payment confirmation and Drive archive fields to deals.

Adds to deals table:
- payment_confirmed: Boolean tracking whether payment was received
- payment_confirmed_at: Timestamp of when payment was confirmed
- payment_amount: Actual amount received (may differ from projected my_payout)
- drive_folder_id: Google Drive folder ID for the deal's document folder
- drive_archived: Boolean tracking whether Drive folder was archived
- drive_archived_at: Timestamp of when Drive folder was archived
- drive_archive_folder_id: ID of the archive folder where deal docs were moved

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-06-26 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "k2l3m4n5o6p7"
down_revision: Union[str, None] = "j1k2l3m4n5o6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Payment confirmation fields
    op.add_column("deals", sa.Column("payment_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("deals", sa.Column("payment_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deals", sa.Column("payment_amount", sa.Numeric(19, 2), nullable=True))

    # Drive archive fields
    op.add_column("deals", sa.Column("drive_folder_id", sa.Text(), nullable=True))
    op.add_column("deals", sa.Column("drive_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("deals", sa.Column("drive_archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deals", sa.Column("drive_archive_folder_id", sa.Text(), nullable=True))


def downgrade() -> None:
    # Remove Drive archive fields
    op.drop_column("deals", "drive_archive_folder_id")
    op.drop_column("deals", "drive_archived_at")
    op.drop_column("deals", "drive_archived")
    op.drop_column("deals", "drive_folder_id")

    # Remove payment confirmation fields
    op.drop_column("deals", "payment_amount")
    op.drop_column("deals", "payment_confirmed_at")
    op.drop_column("deals", "payment_confirmed")
