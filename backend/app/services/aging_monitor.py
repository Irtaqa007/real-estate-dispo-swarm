"""Deal Aging Escalation service.

Run daily by the scheduler. Monitors available deals and escalates based on age:
- 14 days: Notify user (deal aging, consider price drop or re-pitch)
- 21 days: Auto-suggest price drop to floor
- 30 days: Move to "Dead" status or re-launch to C-List at floor price
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import ActivityLog, Deal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AGES = {
    14: {
        "action": "notify",
        "label": "aging_14",
        "message": "Deal is 14 days old. Consider a price drop or re-pitch.",
    },
    21: {
        "action": "suggest_drop",
        "label": "aging_21_suggest_drop",
        "message": "Deal is 21 days old. Consider dropping to floor price (${floor:,.0f}).",
    },
    30: {
        "action": "auto_dead_or_relaunch",
        "label": "aging_30",
        "message": "Deal is 30 days old. Auto-moving to Dead or re-launching to C-List at floor.",
    },
}


# ---------------------------------------------------------------------------
# Aging monitor
# ---------------------------------------------------------------------------


async def run_aging_monitor(db: AsyncSession) -> List[Dict]:
    """Run the deal aging escalation check.

    Iterates over all Available deals, checks their age in days, and
    takes appropriate action based on the aging schedule.

    Returns:
        List of action records: [{deal_id, address, days_old, action, ...}]
    """
    result = await db.execute(
        select(Deal).where(Deal.status == "Available")
    )
    available_deals = result.scalars().all()

    if not available_deals:
        logger.debug("Aging monitor: no available deals to check")
        return []

    now = datetime.now(timezone.utc)
    actions_taken: List[Dict] = []

    for deal in available_deals:
        if not deal.created_at:
            continue

        days_old = (now - deal.created_at).days

        if days_old >= 30:
            action = await _handle_30_day_aging(db, deal, now)
            actions_taken.append(action)
        elif days_old >= 21:
            action = _handle_21_day_aging(db, deal, now)
            actions_taken.append(action)
        elif days_old >= 14:
            action = _handle_14_day_aging(db, deal, now)
            actions_taken.append(action)

    if actions_taken:
        await db.commit()
        logger.info("Aging monitor: %d actions taken", len(actions_taken))

    return actions_taken


# ---------------------------------------------------------------------------
# Age handlers
# ---------------------------------------------------------------------------


def _handle_14_day_aging(db: AsyncSession, deal: Deal, now: datetime) -> Dict:
    """Handle 14-day aging: create a notification."""
    log_entry = ActivityLog(
        entity_type="deal",
        entity_id=deal.id,
        action="aging_14_notification",
        metadata_json={
            "days_old": 14,
            "address": deal.address,
            "message": AGES[14]["message"],
            "timestamp": now.isoformat(),
        },
    )
    db.add(log_entry)

    logger.info("Aging 14d: deal %s (%s) — notifying user", deal.id, deal.address)
    return {
        "deal_id": str(deal.id),
        "address": deal.address,
        "days_old": 14,
        "action": "notify",
        "message": AGES[14]["message"],
    }


def _handle_21_day_aging(db: AsyncSession, deal: Deal, now: datetime) -> Dict:
    """Handle 21-day aging: suggest price drop to floor."""
    message = AGES[21]["message"].format(
        floor=float(deal.floor_price),
    )

    log_entry = ActivityLog(
        entity_type="deal",
        entity_id=deal.id,
        action="aging_21_suggest_drop",
        metadata_json={
            "days_old": 21,
            "address": deal.address,
            "asking_price": float(deal.asking_price),
            "floor_price": float(deal.floor_price),
            "message": message,
            "timestamp": now.isoformat(),
        },
    )
    db.add(log_entry)

    logger.info("Aging 21d: deal %s (%s) — suggesting price drop", deal.id, deal.address)
    return {
        "deal_id": str(deal.id),
        "address": deal.address,
        "days_old": 21,
        "action": "suggest_drop",
        "message": message,
        "asking_price": float(deal.asking_price),
        "floor_price": float(deal.floor_price),
    }


async def _handle_30_day_aging(db: AsyncSession, deal: Deal, now: datetime) -> Dict:
    """Handle 30-day aging: move to Dead or suggest re-launch to C-List at floor.

    The current implementation moves to Dead status. A future enhancement
    could offer the option to re-launch to C-List at floor price.
    """
    # Mark the deal as Dead
    deal.status = "Dead"
    db.add(deal)

    log_entry = ActivityLog(
        entity_type="deal",
        entity_id=deal.id,
        action="aging_30_dead",
        metadata_json={
            "days_old": 30,
            "address": deal.address,
            "action_taken": "moved_to_dead",
            "timestamp": now.isoformat(),
        },
    )
    db.add(log_entry)

    logger.info(
        "Aging 30d: deal %s (%s) — moved to Dead status",
        deal.id, deal.address,
    )

    return {
        "deal_id": str(deal.id),
        "address": deal.address,
        "days_old": 30,
        "action": "moved_to_dead",
        "message": f"Deal {deal.address} auto-moved to Dead status (30 days old).",
    }


# ---------------------------------------------------------------------------
# Manual re-launch to bottom tier
# ---------------------------------------------------------------------------


async def relaunch_to_bottom_tier(db: AsyncSession, deal: Deal) -> Dict:
    """Prepare a deal for re-launch to C-List buyers at floor price.

    This does not actually launch the campaign — it resets the deal's
    asking price to floor price and sets status back to Available so
    the user can re-launch manually or via the scheduler.

    Args:
        db: Database session.
        deal: The deal to re-launch.

    Returns:
        Dict with re-launch details.
    """
    previous_status = deal.status
    previous_asking = float(deal.asking_price)
    floor_price = float(deal.floor_price)

    deal.status = "Available"
    deal.asking_price = floor_price
    db.add(deal)

    log_entry = ActivityLog(
        entity_type="deal",
        entity_id=deal.id,
        action="aging_relaunch",
        metadata_json={
            "from_status": previous_status,
            "previous_asking": previous_asking,
            "new_asking": floor_price,
            "target_tier": "C-List",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.add(log_entry)

    logger.info(
        "Aging re-launch: deal %s (%s) re-launched to C-List at floor $%.2f",
        deal.id, deal.address, floor_price,
    )

    return {
        "deal_id": str(deal.id),
        "address": deal.address,
        "from_status": previous_status,
        "previous_asking": previous_asking,
        "new_asking": floor_price,
        "target_tier": "C-List",
    }
