"""fix_deads_fell_through_typo

Renames the buyers.deads_fell_through column to deals_fell_through
(corrects a typo in the original migration).

Revision ID: o5p6q7r8s9t0
Revises: n1o2p3q4r5s6
Create Date: 2026-07-10 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "o5p6q7r8s9t0"
down_revision: Union[str, None] = "n1o2p3q4r5s6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("buyers", "deads_fell_through", new_column_name="deals_fell_through")


def downgrade() -> None:
    op.alter_column("buyers", "deals_fell_through", new_column_name="deads_fell_through")
