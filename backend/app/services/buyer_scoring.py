"""Buyer scoring, tier promotion, and eligibility assessment service.

Provides:
- Auto Tier Promotion: daily check that promotes buyers based on deals_closed and response_rate
- Buyer Eligibility: assess whether a buyer should be pitched based on engagement and recency
- Fatigue Protection: track and enforce max pitches per week per buyer
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Buyer, Campaign, Deal, JVPartner
from app.services.audit_logger import audit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier thresholds
# ---------------------------------------------------------------------------

TIER_THRESHOLDS = {
    "A-List": {
        "min_deals_closed": 3,
        "min_response_rate": 0.5,
    },
    "B-List": {
        "min_deals_closed": 1,
        "min_response_rate": 0.3,
    },
}

# ---------------------------------------------------------------------------
# Fatigue protection
# ---------------------------------------------------------------------------

MAX_PITCHES_PER_WEEK = 3
WEEK_IN_SECONDS = 7 * 24 * 3600


async def reset_pitch_counters(db: AsyncSession) -> int:
    """Reset weekly pitch counters for all buyers if a week has passed.

    Called daily by the scheduler. Resets pitches_this_week to 0 for
    any buyer whose last reset was more than 7 days ago.

    Returns:
        Number of buyers whose counters were reset.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=WEEK_IN_SECONDS)

    result = await db.execute(
        select(Buyer).where(
            Buyer.pitches_this_week_reset_at.is_(None)
            | (Buyer.pitches_this_week_reset_at < cutoff)
        )
    )
    buyers = result.scalars().all()

    count = 0
    now = datetime.now(timezone.utc)
    for buyer in buyers:
        buyer.pitches_this_week = 0
        buyer.pitches_this_week_reset_at = now
        db.add(buyer)
        count += 1

    if count > 0:
        await db.commit()
        logger.info("Reset pitch counters for %d buyers", count)

    return count


async def increment_pitch_count(db: AsyncSession, buyer: Buyer) -> None:
    """Increment a buyer's weekly pitch counter and update last_pitch_sent_at.

    Args:
        db: Database session.
        buyer: The Buyer to update.
    """
    buyer.pitches_this_week = (buyer.pitches_this_week or 0) + 1
    buyer.last_pitch_sent_at = datetime.now(timezone.utc)

    # Ensure reset timestamp is set
    if not buyer.pitches_this_week_reset_at:
        buyer.pitches_this_week_reset_at = datetime.now(timezone.utc)

    db.add(buyer)
    logger.debug("Incremented pitch count for buyer %s to %d", buyer.id, buyer.pitches_this_week)


async def check_fatigue_protection(buyer_row) -> Tuple[bool, Optional[str]]:
    """Check if a buyer is fatigued (received too many pitches this week).

    Args:
        buyer_row: A Buyer model instance or SQL result row with pitches_this_week.

    Returns:
        Tuple of (allowed: bool, reason: str or None).
        If not allowed, reason explains why.
    """
    pitches = getattr(buyer_row, 'pitches_this_week', 0) or 0
    if pitches >= MAX_PITCHES_PER_WEEK:
        return False, f"Fatigue protection: {pitches}/{MAX_PITCHES_PER_WEEK} pitches this week"
    return True, None


# ---------------------------------------------------------------------------
# Buyer eligibility
# ---------------------------------------------------------------------------


async def assess_buyer_eligibility(
    buyer_row,
    deal_created_at: datetime,
    days_since_deal_upload: int,
) -> Tuple[bool, Optional[str]]:
    """Assess whether a buyer is eligible to be pitched for a deal.

    Smart filtering rules:
    - Must be Active + email_verified (already enforced in SQL)
    - engagement_score >= 20 OR buyer_tier IN ('A-List', 'B-List')
    - last_pitch_sent_at is NULL OR > 7 days ago
    - Skip C-List with 0 engagement unless deal is > 14 days old

    Args:
        buyer_row: A SQL result row with buyer attributes.
        deal_created_at: When the deal was created.
        days_since_deal_upload: Days since the deal was uploaded.

    Returns:
        Tuple of (eligible: bool, reason: str or None).
    """
    tier = getattr(buyer_row, 'buyer_tier', 'C-List') or 'C-List'
    engagement = float(getattr(buyer_row, 'engagement_score', 0) or 0)
    last_pitch = getattr(buyer_row, 'last_pitch_sent_at', None)

    # Rule 1: Must have engagement >= 20 OR be A-List/B-List
    if engagement < 20 and tier not in ('A-List', 'B-List'):
        # Rule 1b: C-List with 0 engagement only allowed if deal > 14 days old
        if tier == 'C-List' and engagement == 0 and days_since_deal_upload <= 14:
            return False, "C-List with 0 engagement; deal too recent (< 14 days)"

    # Rule 2: Must not have been pitched in the last 7 days
    if last_pitch is not None:
        days_since_pitch = (datetime.now(timezone.utc) - last_pitch).days
        if days_since_pitch <= 7:
            return False, f"Already pitched {days_since_pitch} days ago (min 7 day gap)"

    return True, None


# ---------------------------------------------------------------------------
# Auto Tier Promotion
# ---------------------------------------------------------------------------


async def run_tier_promotions(db: AsyncSession) -> List[Dict[str, any]]:
    """Run daily tier promotions based on buyer performance.

    Promotion rules:
    - deals_closed >= 3 AND response_rate > 0.5 → A-List
    - deals_closed >= 1 OR response_rate > 0.3 → B-List

    Buyers already in a higher or equal tier are skipped.

    Args:
        db: Database session.

    Returns:
        List of promotion records: [{"buyer_id": ..., "old_tier": ..., "new_tier": ...}, ...]
    """
    result = await db.execute(select(Buyer).where(Buyer.status == "Active"))
    all_buyers = result.scalars().all()

    promotions = []
    now = datetime.now(timezone.utc)

    for buyer in all_buyers:
        new_tier = _determine_tier(buyer)
        if new_tier and new_tier != buyer.buyer_tier:
            old_tier = buyer.buyer_tier
            buyer.buyer_tier = new_tier
            db.add(buyer)

            await audit.log(
                db,
                entity_type="buyer",
                entity_id=buyer.id,
                action="tier_promotion",
                metadata={
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                    "deals_closed": buyer.deals_closed,
                    "response_rate": buyer.response_rate,
                    "promoted_at": now.isoformat(),
                },
            )

            promotions.append({
                "buyer_id": str(buyer.id),
                "buyer_email": buyer.email,
                "old_tier": old_tier,
                "new_tier": new_tier,
            })

            logger.info(
                "Tier promotion: %s (%s): %s → %s",
                buyer.email, buyer.id, old_tier, new_tier,
            )

    if promotions:
        await db.commit()
        logger.info("Tier promotions complete: %d buyers promoted", len(promotions))
    else:
        logger.info("Tier promotions: no buyers qualified for promotion")

    return promotions


def _determine_tier(buyer: Buyer) -> Optional[str]:
    """Determine the highest tier a buyer qualifies for.

    Returns None if the buyer stays at their current tier.
    """
    deals_closed = buyer.deals_closed or 0
    response_rate = buyer.response_rate or 0
    current_tier = buyer.buyer_tier or "C-List"

    # Check A-List (highest)
    if deals_closed >= TIER_THRESHOLDS["A-List"]["min_deals_closed"] and response_rate > TIER_THRESHOLDS["A-List"]["min_response_rate"]:
        if current_tier != "A-List":
            return "A-List"
        return None

    # Check B-List
    if deals_closed >= TIER_THRESHOLDS["B-List"]["min_deals_closed"] or response_rate > TIER_THRESHOLDS["B-List"]["min_response_rate"]:
        if current_tier in ("C-List", None):
            return "B-List"
        return None

    return None


# ---------------------------------------------------------------------------
# Formula hardening: improved calculations
# ---------------------------------------------------------------------------


def calculate_engagement_score(buyer: Buyer) -> float:
    """Calculate an improved, dynamic engagement score.

    Formula: (response_rate * 0.4) + (close_rate * 0.3) + (recency_score * 0.2) + (avg_spread * 0.1)

    All components normalized to 0-100 scale.

    Args:
        buyer: The Buyer to calculate for.

    Returns:
        Engagement score from 0-100.
    """
    response_rate = min(1.0, buyer.response_rate or 0)
    response_component = response_rate * 40  # 0-40 points

    close_rate = (buyer.deals_closed or 0) / max(1, (buyer.deals_offered_on or 1))
    close_component = close_rate * 30  # 0-30 points

    # Recency: more recent replies = higher score
    recency_component = 0.0
    if buyer.last_reply_at:
        days_since_reply = (datetime.now(timezone.utc) - buyer.last_reply_at).days
        if days_since_reply <= 7:
            recency_component = 20
        elif days_since_reply <= 30:
            recency_component = 15
        elif days_since_reply <= 90:
            recency_component = 10
        else:
            recency_component = 5
    elif buyer.last_pitch_sent_at:
        # No reply ever, but recently pitched = minimal recency
        recency_component = 5

    # Average spread bonus
    avg_spread = float(buyer.avg_spread_closed or 0)
    spread_component = min(10, avg_spread / 10000)  # 0-10 points, $10k spread = 1 point

    score = response_component + close_component + recency_component + spread_component
    return min(100, max(0, round(score, 1)))


async def calculate_and_update_engagement(db: AsyncSession) -> int:
    """Recalculate engagement_score for all active buyers using the improved formula.

    Returns:
        Number of buyers updated.
    """
    result = await db.execute(select(Buyer).where(Buyer.status == "Active"))
    buyers = result.scalars().all()

    count = 0
    for buyer in buyers:
        new_score = calculate_engagement_score(buyer)
        if abs((buyer.engagement_score or 0) - new_score) > 0.5:
            buyer.engagement_score = new_score
            db.add(buyer)
            count += 1

    if count > 0:
        await db.commit()
        logger.info("Recalculated engagement scores for %d buyers", count)

    return count


def calculate_weighted_title_issue_rate(
    total_deals: int,
    recent_issues: int,
    lien_count: int,
    minor_delays: int,
) -> float:
    """Calculate a weighted title issue rate where recent issues count 2x
    and liens count 3x compared to minor delays.

    Args:
        total_deals: Total number of deals handled.
        recent_issues: Number of recent (last 30 days) title issues.
        lien_count: Number of lien-related issues.
        minor_delays: Number of minor delay issues.

    Returns:
        Weighted title issue rate (0.0-1.0).
    """
    if total_deals == 0:
        return 0.0

    # Weighted: recent issues count 2x, liens count 3x, minor delays count 1x
    weighted_issues = (recent_issues * 2) + (lien_count * 3) + minor_delays
    return min(1.0, weighted_issues / total_deals)
