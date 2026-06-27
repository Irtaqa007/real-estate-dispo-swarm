"""Portfolio-Level Buyer Insights service.

Calculates per-buyer portfolio insights from historical data:
- preferred_markets: Top markets from closed deals
- avg_offer_speed_hours: Avg time from pitch to reply
- price_sensitivity: How often they counter (ratio of counters to offers)
- rehab_appetite: Extract rehab preference from email replies
- closing_reliability: deals_closed / deals_offered_on
- seasonal_pattern: Detect most active months

Insights are stored on the Buyer model and used to personalize pitches.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Buyer, Campaign, Deal
from app.services.groq_client import groq_chat_completion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Insights calculation
# ---------------------------------------------------------------------------


async def calculate_buyer_insights(
    db: AsyncSession,
    buyer: Buyer,
) -> Dict[str, Any]:
    """Calculate portfolio-level insights for a single buyer.

    Args:
        db: Database session.
        buyer: The Buyer to calculate insights for.

    Returns:
        Dict with insight keys: preferred_markets, avg_offer_speed_hours,
        price_sensitivity, rehab_appetite, closing_reliability, seasonal_pattern.
    """
    buyer_id = buyer.id

    # 1. Preferred markets: extract cities from deals they've engaged with
    preferred_markets = await _get_preferred_markets(db, buyer_id)

    # 2. Average offer speed: avg time from first campaign send to first reply
    avg_offer_speed_hours = await _get_avg_offer_speed(db, buyer_id)

    # 3. Price sensitivity: ratio of Counter replies to total replies
    price_sensitivity = await _get_price_sensitivity(db, buyer_id)

    # 4. Closing reliability: deals_closed / deals_offered_on
    closing_reliability = await _get_closing_reliability(buyer)

    # 5. Rehab appetite: extract from email replies
    rehab_appetite = await _get_rehab_appetite(db, buyer_id)

    # 6. Seasonal pattern: detect active months from campaign activity
    seasonal_pattern = await _get_seasonal_pattern(db, buyer_id)

    insights = {
        "preferred_markets": preferred_markets,
        "avg_offer_speed_hours": avg_offer_speed_hours,
        "price_sensitivity": price_sensitivity,
        "rehab_appetite": rehab_appetite,
        "closing_reliability": closing_reliability,
        "seasonal_pattern": seasonal_pattern,
    }

    logger.info(
        "Calculated portfolio insights for %s (%s): %.100s",
        buyer.email, buyer.id, insights,
    )

    return insights


async def update_all_buyer_insights(db: AsyncSession) -> int:
    """Calculate and update portfolio insights for all active buyers.

    Called by the scheduler.

    Args:
        db: Database session.

    Returns:
        Number of buyers updated.
    """
    result = await db.execute(
        select(Buyer).where(Buyer.status == "Active")
    )
    buyers = result.scalars().all()

    count = 0
    for buyer in buyers:
        try:
            insights = await calculate_buyer_insights(db, buyer)
            buyer.portfolio_insights = insights
            db.add(buyer)
            count += 1
        except Exception as e:
            logger.warning("Failed to calculate insights for buyer %s: %s", buyer.id, e, exc_info=True)

    if count > 0:
        await db.commit()
        logger.info("Updated portfolio insights for %d buyers", count)

    return count


# ---------------------------------------------------------------------------
# Individual insight calculators
# ---------------------------------------------------------------------------


async def _get_preferred_markets(
    db: AsyncSession,
    buyer_id: str,
) -> List[Dict]:
    """Extract top markets from deals associated with this buyer.

    Looks at campaigns the buyer engaged with (sent/replied) and
    extracts the deal city to determine preferred markets.
    """
    result = await db.execute(
        select(Deal.city)
        .join(Campaign, Campaign.deal_id == Deal.id)
        .where(Campaign.buyer_id == buyer_id)
        .where(Campaign.status.in_(["Sent", "Replied"]))
        .where(Deal.city.isnot(None))
    )
    cities = [row[0] for row in result.all() if row[0]]

    if not cities:
        return [{"market": "Unknown", "count": 0}]

    city_counts = Counter(cities)
    top_markets = city_counts.most_common(5)
    return [{"market": city, "count": count} for city, count in top_markets]


async def _get_avg_offer_speed(
    db: AsyncSession,
    buyer_id: str,
) -> Optional[float]:
    """Calculate average time (hours) from first sent campaign to first reply.

    If the buyer has never replied, returns None.
    """
    result = await db.execute(
        select(
            func.avg(
                func.extract("epoch", Campaign.reply_received_at - Campaign.sent_at) / 3600
            )
        ).where(
            Campaign.buyer_id == buyer_id,
            Campaign.sent_at.isnot(None),
            Campaign.reply_received_at.isnot(None),
        )
    )
    avg_hours = result.scalar()
    if avg_hours is not None:
        return round(float(avg_hours), 1)
    return None


async def _get_price_sensitivity(
    db: AsyncSession,
    buyer_id: str,
) -> Dict:
    """Calculate how price-sensitive this buyer is.

    Ratio of Counter replies to total replies.
    Returns percentage and qualitative label.
    """
    total_result = await db.execute(
        select(func.count(Campaign.id)).where(
            Campaign.buyer_id == buyer_id,
            Campaign.reply_intent.isnot(None),
        )
    )
    total_replies = total_result.scalar() or 0

    counter_result = await db.execute(
        select(func.count(Campaign.id)).where(
            Campaign.buyer_id == buyer_id,
            Campaign.reply_intent == "Counter",
        )
    )
    counter_count = counter_result.scalar() or 0

    ratio = counter_count / total_replies if total_replies > 0 else None

    label = "unknown"
    if ratio is not None:
        if ratio >= 0.5:
            label = "high"
        elif ratio >= 0.2:
            label = "medium"
        else:
            label = "low"

    return {
        "counter_ratio": round(ratio, 2) if ratio is not None else None,
        "total_replies": total_replies,
        "counter_count": counter_count,
        "label": label,
    }


async def _get_closing_reliability(buyer: Buyer) -> Dict:
    """Calculate closing reliability as closed deals / offers.

    Returns ratio and qualitative label.
    """
    offered = buyer.deals_offered_on or 0
    closed = buyer.deals_closed or 0

    ratio = closed / offered if offered > 0 else None

    label = "unknown"
    if ratio is not None:
        if ratio >= 0.7:
            label = "high"
        elif ratio >= 0.4:
            label = "medium"
        else:
            label = "low"

    return {
        "ratio": round(ratio, 2) if ratio is not None else None,
        "offered": offered,
        "closed": closed,
        "label": label,
    }


async def _get_rehab_appetite(
    db: AsyncSession,
    buyer_id: str,
) -> Dict:
    """Extract rehab preference from buyer's email replies.

    Uses Groq to analyze the buyer's replied emails for mentions
    of rehab, condition, repairs, cosmetic preferences, etc.
    """
    result = await db.execute(
        select(Campaign.reply_body)
        .where(Campaign.buyer_id == buyer_id)
        .where(Campaign.reply_body.isnot(None))
        .order_by(Campaign.created_at.desc())
        .limit(5)
    )
    replies = [row[0] for row in result.all() if row[0]]

    if not replies:
        return {
            "appetite": "unknown",
            "preference": "No reply data available",
        }

    # Combine replies for analysis
    combined = " ".join(replies)[:2000]

    messages = [
        {
            "role": "system",
            "content": "You are a real estate analyst. Analyze this buyer's replies for rehab preference.",
        },
        {
            "role": "user",
            "content": (
                f"Based on these replies from a cash buyer, determine their rehab appetite:\n\n"
                f"{combined}\n\n"
                f"Return a single JSON object:\n"
                f"{{\n"
                f"  \"appetite\": \"heavy\" or \"light\" or \"none\" or \"unknown\",\n"
                f"  \"preference\": \"one-sentence summary of their condition/rehab preference\"\n"
                f"}}"
            ),
        },
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=150,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.strip().startswith("```"))
        parsed = json.loads(content)
        return {
            "appetite": parsed.get("appetite", "unknown"),
            "preference": parsed.get("preference", ""),
        }
    except Exception as e:
        logger.warning("Failed to analyze rehab appetite for buyer %s: %s", buyer_id, e, exc_info=True)
        return {"appetite": "unknown", "preference": "Analysis failed"}


async def _get_seasonal_pattern(
    db: AsyncSession,
    buyer_id: str,
) -> Dict:
    """Detect seasonal buying pattern from campaign activity months."""
    result = await db.execute(
        select(
            func.extract("month", Campaign.sent_at).label("month"),
            func.count(Campaign.id).label("count"),
        )
        .where(Campaign.buyer_id == buyer_id)
        .where(Campaign.sent_at.isnot(None))
        .group_by(func.extract("month", Campaign.sent_at))
        .order_by(func.count(Campaign.id).desc())
    )
    monthly_data = result.all()

    if not monthly_data:
        return {"active_months": [], "peak_quarter": None, "pattern": "insufficient_data"}

    months = {int(r.month): int(r.count) for r in monthly_data}
    month_names = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
    }

    active_months = [
        {"month": month_names[m], "count": c}
        for m, c in sorted(months.items(), key=lambda x: -x[1])
    ]

    # Determine peak quarter
    quarter_sums = {1: 0, 2: 0, 3: 0, 4: 0}
    for m, c in months.items():
        if m <= 3:
            quarter_sums[1] += c
        elif m <= 6:
            quarter_sums[2] += c
        elif m <= 9:
            quarter_sums[3] += c
        else:
            quarter_sums[4] += c

    peak_quarter = max(quarter_sums, key=quarter_sums.get) if any(quarter_sums.values()) else None

    return {
        "active_months": active_months[:6],
        "peak_quarter": f"Q{peak_quarter}" if peak_quarter else None,
        "total_activities": sum(months.values()),
        "pattern": "seasonal" if max(months.values()) > sum(months.values()) * 0.4 else "consistent",
    }


# ---------------------------------------------------------------------------
# Pitch personalization helper
# ---------------------------------------------------------------------------


def get_personalization_hints(insights: Optional[Dict]) -> Dict:
    """Extract pitch personalization hints from buyer insights.

    Args:
        insights: The buyer's portfolio_insights dict.

    Returns:
        Dict with personalization hints for pitch email generation.
    """
    if not insights:
        return {}

    hints = {}

    # Price sensitivity
    price_sens = insights.get("price_sensitivity", {})
    if isinstance(price_sens, dict):
        label = price_sens.get("label", "unknown")
        if label == "high":
            hints["lead_with"] = "spread"
            hints["emphasis"] = "Lead with the spread/profit potential, not the ARV"
        elif label == "low":
            hints["lead_with"] = "arv"

    # Closing reliability
    closing = insights.get("closing_reliability", {})
    if isinstance(closing, dict):
        label = closing.get("label", "unknown")
        if label == "high":
            hints["cta"] = "emphasize_speed"
            hints["emphasis"] = "Mention fast closing and reliable process"
        elif label == "low":
            hints["cta"] = "standard"

    # Rehab appetite
    rehab = insights.get("rehab_appetite", {})
    if isinstance(rehab, dict):
        appetite = rehab.get("appetite", "unknown")
        if appetite == "heavy":
            hints["focus"] = "condition"
            hints["emphasis"] = "Emphasize the condition and repair potential, less on cosmetics"
        elif appetite == "light":
            hints["focus"] = "move_in_ready"
        elif appetite == "none":
            hints["focus"] = "turnkey"

    return hints
