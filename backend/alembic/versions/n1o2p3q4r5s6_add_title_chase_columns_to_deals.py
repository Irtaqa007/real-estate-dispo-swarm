"""add_title_chase_columns_to_deals

Revision ID: n1o2p3q4r5s6
Revises: m1n2o3p4q5r6
Create Date: 2026-06-27 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "n1o2p3q4r5s6"
down_revision: Union[str, None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Title chase columns on deals table ──
    op.add_column("deals", sa.Column("title_opened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deals", sa.Column("title_last_chase_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deals", sa.Column("title_chase_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("deals", sa.Column("title_acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("deals", sa.Column("title_company_email", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("deals", "title_company_email")
    op.drop_column("deals", "title_acknowledged")
    op.drop_column("deals", "title_chase_count")
    op.drop_column("deals", "title_last_chase_at")
    op.drop_column("deals", "title_opened_at")
