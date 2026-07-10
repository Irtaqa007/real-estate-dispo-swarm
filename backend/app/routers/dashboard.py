"""Dashboard stats API endpoint.

Returns real operational metrics for the dashboard:
- Deal pipeline counts by status
- Today's email activity
- Active conversations and contract-ready buyers
- Conversion rate from pitched to contract
"""

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Campaign, Deal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
):
    """Return real operational metrics for the dashboard.

    Returns:
        dict with keys: deals, today, active, conversion.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ------------------------------------------------------------------
    # 1. Deal pipeline counts
    # ------------------------------------------------------------------
    deal_counts = await _count_deals_by_status(db)

    # ------------------------------------------------------------------
    # 2. Today's email activity
    # ------------------------------------------------------------------
    today_emails = await _count_today_campaigns(db, today_start)
    today_replies = await _count_today_replies(db, today_start)

    # ------------------------------------------------------------------
    # 3. Active conversations & contract-ready
    # ------------------------------------------------------------------
    active_conversations = await _count_active_conversations(db)
    contract_ready = await _count_contract_ready(db)

    # ------------------------------------------------------------------
    # 4. Conversion metrics
    # ------------------------------------------------------------------
    total_pitched = await _count_total_pitched(db)
    total_contracts = await _count_total_contracts(db)
    rate_pct = round((total_contracts / total_pitched * 100), 1) if total_pitched > 0 else 0.0

    return {
        "deals": {
            "available": deal_counts.get("Available", 0),
            "launched": deal_counts.get("Campaign Launched", 0),
            "under_contract": deal_counts.get("Under Contract", 0),
            "closed": deal_counts.get("Sold", 0),
        },
        "today": {
            "emails_sent": today_emails,
            "replies_received": today_replies,
        },
        "active": {
            "conversations": active_conversations,
            "contract_ready": contract_ready,
        },
        "conversion": {
            "total_pitched": total_pitched,
            "total_contracts": total_contracts,
            "rate_pct": rate_pct,
        },
    }


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def _count_deals_by_status(db: AsyncSession) -> dict[str, int]:
    """Return a dict of {status: count} for all deals."""
    result = await db.execute(
        select(Deal.status, func.count(Deal.id))
        .group_by(Deal.status)
    )
    return dict(result.all())


async def _count_today_campaigns(db: AsyncSession, today_start: datetime) -> int:
    """Count campaigns where sent_at >= today_start UTC."""
    result = await db.execute(
        select(func.count(Campaign.id))
        .where(Campaign.sent_at >= today_start)
    )
    return result.scalar() or 0


async def _count_today_replies(db: AsyncSession, today_start: datetime) -> int:
    """Count campaigns where reply_received_at >= today_start UTC."""
    result = await db.execute(
        select(func.count(Campaign.id))
        .where(Campaign.reply_received_at >= today_start)
    )
    return result.scalar() or 0


async def _count_active_conversations(db: AsyncSession) -> int:
    """Count campaigns where status='Replied' and conversation_stage is not passed/contract_ready."""
    result = await db.execute(
        select(func.count(Campaign.id))
        .where(
            Campaign.status == "Replied",
            Campaign.conversation_stage.notin_(["passed", "contract_ready"]),
        )
    )
    return result.scalar() or 0


async def _count_contract_ready(db: AsyncSession) -> int:
    """Count campaigns where status='Contract_Pending'."""
    result = await db.execute(
        select(func.count(Campaign.id))
        .where(Campaign.status == "Contract_Pending")
    )
    return result.scalar() or 0


async def _count_total_pitched(db: AsyncSession) -> int:
    """Count distinct (deal_id, buyer_id) pairs across all campaigns ever launched."""
    sub = (
        select(Campaign.deal_id, Campaign.buyer_id)
        .distinct()
        .subquery()
    )
    result = await db.execute(select(func.count()).select_from(sub))
    return result.scalar() or 0


async def _count_total_contracts(db: AsyncSession) -> int:
    """Count campaigns where status='Contract_Pending' or conversation_stage='contract_ready'."""
    result = await db.execute(
        select(func.count(Campaign.id))
        .where(
            (Campaign.status == "Contract_Pending")
            | (Campaign.conversation_stage == "contract_ready")
        )
    )
    return result.scalar() or 0
