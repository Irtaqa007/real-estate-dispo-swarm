"""Seasonal/Market Adjustment service for smart price suggestions during campaigns.

On Touch 3 (Day 4), if reply rate < 20%: AI suggests dropping to floor price.
On Touch 4 (Day 7), if multiple counters: AI suggests holding firm with scarcity language.

The user approves or declines the suggestion via the API.
"""

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import Campaign, Deal
from app.services.groq_client import groq_chat_completion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPLY_RATE_THRESHOLD = 0.20  # 20% minimum reply rate to avoid price drop
TOUCH_3_DAY = 4              # Day 4 is when touch 3 fires
TOUCH_4_DAY = 7              # Day 7 is when touch 4 fires


# ---------------------------------------------------------------------------
# Market adjustment checks
# ---------------------------------------------------------------------------


async def check_touch_3_adjustment(
    db: AsyncSession,
    deal: Deal,
) -> Optional[Dict]:
    """Check if a price drop should be suggested when touch 3 fires.

    Calculates the reply rate for this deal across all buyers. If the
    reply rate is below 20%, suggests dropping the asking price to the
    floor price and regenerating campaign emails.

    Args:
        db: Database session.
        deal: The deal being campaigned.

    Returns:
        Dict with suggestion details, or None if no adjustment needed.
    """
    # Count sent and replied campaigns for this deal
    sent_result = await db.execute(
        select(func.count(Campaign.id)).where(
            Campaign.deal_id == deal.id,
            Campaign.status.in_(["Sent", "Replied"]),
        )
    )
    total_sent = sent_result.scalar() or 0

    replied_result = await db.execute(
        select(func.count(Campaign.id)).where(
            Campaign.deal_id == deal.id,
            Campaign.status == "Replied",
        )
    )
    total_replied = replied_result.scalar() or 0

    if total_sent == 0:
        return None

    reply_rate = total_replied / total_sent

    if reply_rate >= REPLY_RATE_THRESHOLD:
        return None

    # Reply rate is below threshold — suggest price drop
    asking_price = float(deal.asking_price)
    floor_price = float(deal.floor_price)
    current_spread = float(deal.spread) if deal.spread else float(deal.asking_price) - float(deal.contract_price)
    new_spread = floor_price - float(deal.contract_price)

    suggestion = await _generate_price_drop_suggestion(
        address=deal.address,
        asking_price=asking_price,
        floor_price=floor_price,
        current_spread=current_spread,
        new_spread=new_spread,
        reply_rate=reply_rate,
        total_sent=total_sent,
        total_replied=total_replied,
    )

    return {
        "adjustment_type": "price_drop",
        "touch": 3,
        "day": TOUCH_3_DAY,
        "reply_rate": round(reply_rate, 3),
        "total_sent": total_sent,
        "total_replied": total_replied,
        "current_asking": asking_price,
        "suggested_price": floor_price,
        "current_spread": current_spread,
        "new_spread": new_spread,
        "ai_suggestion": suggestion,
        "deal_id": str(deal.id),
        "deal_address": deal.address,
    }


async def check_touch_4_adjustment(
    db: AsyncSession,
    deal: Deal,
) -> Optional[Dict]:
    """Check if holding firm should be suggested when touch 4 fires.

    If multiple buyers have countered on this deal, suggests holding
    at the asking price with scarcity language.

    Args:
        db: Database session.
        deal: The deal being campaigned.

    Returns:
        Dict with suggestion details, or None if no adjustment needed.
    """
    # Count countered replies for this deal
    counter_result = await db.execute(
        select(func.count(Campaign.id)).where(
            Campaign.deal_id == deal.id,
            Campaign.reply_intent == "Counter",
        )
    )
    counter_count = counter_result.scalar() or 0

    if counter_count < 2:
        return None

    suggestion = await _generate_hold_firm_suggestion(
        address=deal.address,
        asking_price=float(deal.asking_price),
        counter_count=counter_count,
    )

    return {
        "adjustment_type": "hold_firm",
        "touch": 4,
        "day": TOUCH_4_DAY,
        "counter_count": counter_count,
        "current_asking": float(deal.asking_price),
        "ai_suggestion": suggestion,
        "deal_id": str(deal.id),
        "deal_address": deal.address,
    }


async def apply_price_drop(db: AsyncSession, deal: Deal) -> Optional[Dict]:
    """Apply a price drop: set asking_price to floor_price.

    This is called after the user approves the price drop suggestion.

    Args:
        db: Database session.
        deal: The deal to adjust.

    Returns:
        Dict with adjustment details, or None if no adjustment needed.
    """
    asking_price = float(deal.asking_price)
    floor_price = float(deal.floor_price)

    if asking_price <= floor_price:
        return None

    # Store the original price for audit trail
    previous_asking = asking_price

    # Apply the drop
    deal.asking_price = floor_price
    db.add(deal)

    logger.info(
        "Market adjuster: dropped asking price for deal %s (%s) from $%.2f to $%.2f",
        deal.id, deal.address, previous_asking, floor_price,
    )

    return {
        "previous_asking": previous_asking,
        "new_asking": floor_price,
        "deal_id": str(deal.id),
        "deal_address": deal.address,
    }


# ---------------------------------------------------------------------------
# AI suggestion generators
# ---------------------------------------------------------------------------


async def _generate_price_drop_suggestion(
    address: str,
    asking_price: float,
    floor_price: float,
    current_spread: float,
    new_spread: float,
    reply_rate: float,
    total_sent: int,
    total_replied: int,
) -> str:
    """Generate an AI suggestion text for the price drop decision."""
    messages = [
        {
            "role": "system",
            "content": "You are a wholesale real estate market analyst. Keep suggestions concise and data-driven.",
        },
        {
            "role": "user",
            "content": (
                f"Deal: {address}\n"
                f"Asking: ${asking_price:,.0f}\n"
                f"Floor: ${floor_price:,.0f}\n"
                f"Current Spread: ${current_spread:,.0f}\n"
                f"New Spread at Floor: ${new_spread:,.0f}\n"
                f"Reply Rate: {reply_rate:.1%} ({total_replied}/{total_sent})\n\n"
                f"Reply rate is below 20%. Generate a 2-sentence recommendation "
                f"whether to drop the asking price to floor (${floor_price:,.0f}) "
                f"or hold firm. Include the potential impact on profit."
            ),
        },
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.4,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Failed to generate price drop suggestion: %s", e, exc_info=True)
        return (
            f"Reply rate is low ({reply_rate:.1%}). Consider dropping asking from "
            f"${asking_price:,.0f} to floor ${floor_price:,.0f} "
            f"(spread decreases from ${current_spread:,.0f} to ${new_spread:,.0f})."
        )


async def _generate_hold_firm_suggestion(
    address: str,
    asking_price: float,
    counter_count: int,
) -> str:
    """Generate an AI suggestion text for holding firm."""
    messages = [
        {
            "role": "system",
            "content": "You are a wholesale real estate negotiator. Keep suggestions concise.",
        },
        {
            "role": "user",
            "content": (
                f"Deal: {address}\n"
                f"Asking: ${asking_price:,.0f}\n"
                f"Counters received: {counter_count}\n\n"
                f"Multiple buyers have countered. Generate a 2-sentence recommendation "
                f"whether to hold firm at asking or negotiate. "
                f"Include a scarcity angle to add to the next email."
            ),
        },
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            temperature=0.4,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Failed to generate hold firm suggestion: %s", e, exc_info=True)
        return (
            f"{counter_count} buyers have countered on {address}. "
            f"Multiple offers indicate strong demand. "
            f"Recommend holding firm at ${asking_price:,.0f} and emphasizing "
            f"competitive interest in the next touch."
        )
