"""add_jv_partner_phone_and_source

Revision ID: g7c3d5e7f9g0
Revises: f6b2c4d6e7f8
Create Date: 2026-06-16 14:00:00.000000

Adds phone and source columns to the jv_partners table.
Both are optional — name and email remain required.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g7c3d5e7f9g0"
down_revision: Union[str, None] = "f6b2c4d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jv_partners", sa.Column("phone", sa.Text(), nullable=True))
    op.add_column("jv_partners", sa.Column("source", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jv_partners", "source")
    op.drop_column("jv_partners", "phone")
