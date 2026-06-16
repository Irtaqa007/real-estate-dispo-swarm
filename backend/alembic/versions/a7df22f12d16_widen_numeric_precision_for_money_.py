"""widen_numeric_precision_for_money_columns

Revision ID: a7df22f12d16
Revises: 0e7a3b1c9d5f
Create Date: 2026-06-13 16:44:15.061663

Widens 14 money columns from Numeric(12,2) to Numeric(19,2) across
deals, buyers, and jv_partners tables to support high-value deals.

Strategy:
1. Drop the generated columns (spread, net_spread_formula) so their
   dependency columns (asking_price, contract_price, repair_estimate)
   can be altered freely.
2. ALTER all 12 non-computed money columns from 12,2 → 19,2.
3. Recreate the generated columns at the new precision.

net_spread_formula is already at 19,2 in the DB but is dropped and
recreated at 19,2 to allow altering repair_estimate (its dependency).
jv_split_percentage (Numeric(5,2)) is left untouched.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7df22f12d16"
down_revision: Union[str, None] = "0e7a3b1c9d5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 12 non-computed money columns to widen (excluding spread and net_spread_formula
# which are handled as computed columns).
NON_COMPUTED_COLUMNS: list[tuple[str, str, bool]] = [
    ("buyers", "avg_spread_closed", True),
    ("buyers", "total_lifetime_spread", True),
    ("deals", "repair_estimate", True),
    ("deals", "arv", False),
    ("deals", "asking_price", False),
    ("deals", "floor_price", False),
    ("deals", "contract_price", False),
    ("deals", "closed_price", True),
    ("deals", "net_spread", True),
    ("deals", "jv_payout", True),
    ("deals", "my_payout", True),
    ("jv_partners", "total_revenue_generated", True),
    ("jv_partners", "total_split_revenue", True),
]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Drop generated columns whose source columns are being altered
    # ------------------------------------------------------------------
    # spread depends on: asking_price, contract_price
    # net_spread_formula depends on: asking_price, contract_price, repair_estimate
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS spread CASCADE")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS net_spread_formula CASCADE")

    # ------------------------------------------------------------------
    # 2. Alter all 13 non-computed money columns from 12,2 → 19,2
    # ------------------------------------------------------------------
    for table, column, nullable in NON_COMPUTED_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.NUMERIC(precision=12, scale=2),
            type_=sa.Numeric(precision=19, scale=2),
            existing_nullable=nullable,
        )

    # ------------------------------------------------------------------
    # 3. Recreate the generated columns at 19,2 precision
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE deals "
        "ADD COLUMN spread NUMERIC(19, 2) "
        "GENERATED ALWAYS AS (asking_price - contract_price) STORED"
    )
    op.execute(
        "ALTER TABLE deals "
        "ADD COLUMN net_spread_formula NUMERIC(19, 2) "
        "GENERATED ALWAYS AS "
        "((asking_price - contract_price) - COALESCE(repair_estimate, 0)) STORED"
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Drop generated columns
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS spread CASCADE")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS net_spread_formula CASCADE")

    # ------------------------------------------------------------------
    # 2. Revert all 13 columns from 19,2 → 12,2
    # ------------------------------------------------------------------
    for table, column, nullable in reversed(NON_COMPUTED_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(precision=19, scale=2),
            type_=sa.NUMERIC(precision=12, scale=2),
            existing_nullable=nullable,
        )

    # ------------------------------------------------------------------
    # 3. Recreate computed columns at 12,2 precision
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE deals "
        "ADD COLUMN spread NUMERIC(12, 2) "
        "GENERATED ALWAYS AS (asking_price - contract_price) STORED"
    )
    op.execute(
        "ALTER TABLE deals "
        "ADD COLUMN net_spread_formula NUMERIC(12, 2) "
        "GENERATED ALWAYS AS "
        "((asking_price - contract_price) - COALESCE(repair_estimate, 0)) STORED"
    )
