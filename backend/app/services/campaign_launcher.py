"""Reusable campaign launch logic for a single buyer+deal pair.

Extracted from the launch_campaign endpoint so it can be called from:
- The launch_campaign endpoint (bulk, via matching)
- process_queued_matches (single buyer released from queue)

Generates 6 psychologically-optimized touch emails, creates Campaign rows
with staggered scheduling by tier, and sends touch 1 immediately for A-List.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import Campaign, Deal, QueuedDealMatch
from app.services.email_generator import generate_touch_email, TOUCH_CONFIGS
from app.services.ai_validator import ValidationResult, validate_ai_output
from app.services.gmail_service import send_email
from app.services.buyer_scoring import (
    assess_buyer_eligibility,
    check_fatigue_protection,
    increment_pitch_count,
)

logger = logging.getLogger(__name__)


async def launch_campaign_for_buyer(
    db: AsyncSession,
    buyer,
    deal: Deal,
    similarity_score: Optional[float] = None,
) -> Dict:
    """Launch a full 6-touch email campaign for a single buyer+deal pair.

    Creates Campaign rows with staggered scheduling based on buyer tier,
    generates touch emails via Groq AI, and sends touch 1 immediately
    for A-List buyers.

    This function does NOT commit — the caller is responsible for committing.

    Args:
        db: Database session.
        buyer: Buyer model instance with full_name, email, buy_box, buyer_tier,
               engagement_score, pitches_this_week, last_pitch_sent_at.
        deal: Deal model instance with address, city, state, property_type,
              arv, asking_price, spread, condition_description, beds, baths, sqft.
        similarity_score: Optional similarity score to log for audit trail.

    Returns:
        dict with keys:
            - success: bool
            - touches_created: int (0 if skipped)
            - reason: str (skip reason or "ok")
            - campaign_ids: list of created Campaign IDs (UUID strings)
            - touches: list of dicts with touch, subject, body, status,
              scheduled_send_at (ISO str or None) — only populated when
              touches were actually created
    """
    result: Dict = {
        "success": False,
        "touches_created": 0,
        "reason": "",
        "campaign_ids": [],
        "touches": [],
    }

    buyer_id = buyer.id
    deal_id = deal.id

    # ── Idempotency: skip if campaigns already exist for this buyer+deal ──
    existing = await db.execute(
        select(Campaign).where(
            Campaign.buyer_id == buyer_id,
            Campaign.deal_id == deal_id,
        ).limit(1)
    )
    if existing.scalar_one_or_none():
        result["reason"] = "campaigns_already_exist"
        logger.info(
            "Skipping campaign launch for buyer %s, deal %s — campaigns already exist",
            buyer_id, deal_id,
        )
        return result

    # ── FEATURE 3: 2-deal cap enforcement ──
    # Even if find_top_matches_for_deal applies the cap, this check ensures
    # that direct API calls to launch_campaign_for_buyer (or any other path)
    # also respect the cap. If the buyer already has 2 active deals, queue
    # this match and skip launch.
    # Lazy import to avoid circular import (matching_service imports us).
    from app.services.matching_service import get_active_deal_count_for_buyer
    active_count = await get_active_deal_count_for_buyer(db, buyer_id)
    if active_count >= 2:
        # Insert QueuedDealMatch so the deal isn't lost
        existing_qm = await db.execute(
            select(QueuedDealMatch).where(
                QueuedDealMatch.buyer_id == buyer_id,
                QueuedDealMatch.deal_id == deal_id,
                QueuedDealMatch.status == "waiting",
            )
        )
        if not existing_qm.scalar_one_or_none():
            now = datetime.now(timezone.utc)
            db.add(QueuedDealMatch(
                buyer_id=buyer_id,
                deal_id=deal_id,
                status="waiting",
                similarity_score=similarity_score,
                queued_at=now,
            ))
        result["reason"] = "buyer_at_deal_cap"
        logger.info(
            "Skipping campaign launch for buyer %s (%s), deal %s — "
            "buyer already at %d active deals (cap: 2). Queued match.",
            buyer_id, buyer.email, deal_id, active_count,
        )
        return result

    # ── Buyer eligibility: engagement, recency, C-List rules ──
    days_since_deal = (datetime.now(timezone.utc) - deal.created_at).days
    eligible, reason = await assess_buyer_eligibility(
        buyer, deal.created_at, days_since_deal,
    )
    if not eligible:
        result["reason"] = f"ineligible: {reason}"
        logger.info(
            "Skipping campaign for buyer %s (%s): %s",
            buyer_id, buyer.email, reason,
        )
        return result

    # ── Fatigue protection ──
    allowed, fatigue_reason = await check_fatigue_protection(buyer)
    if not allowed:
        result["reason"] = f"fatigued: {fatigue_reason}"
        logger.info(
            "Skipping campaign for buyer %s (%s): %s",
            buyer_id, buyer.email, fatigue_reason,
        )
        return result

    # ── Generate 6-touch campaign ──
    launch_time = datetime.now(timezone.utc)
    buyer_tier = buyer.buyer_tier or "C-List"
    touch_records: List[Campaign] = []

    for config in TOUCH_CONFIGS:
        touch_num = config["touch"]

        # Generate email via Groq AI
        email_data = await generate_touch_email(
            touch=touch_num,
            buyer_name=buyer.full_name,
            buyer_email=buyer.email,
            buy_box=buyer.buy_box,
            buyer_tier=buyer_tier,
            address=deal.address,
            city=deal.city or "",
            state=deal.state or "",
            property_type=deal.property_type,
            arv=float(deal.arv),
            asking_price=float(deal.asking_price),
            spread=float(deal.spread) if deal.spread else 0,
            condition_description=deal.condition_description,
            beds=deal.beds,
            baths=deal.baths,
            sqft=deal.sqft,
            buyer_id=buyer_id,
            # FEATURE 4: Pass buyer intelligence fields
            deals_closed=buyer.deals_closed or 0,
            last_reply_at=buyer.last_reply_at,
            engagement_score=buyer.engagement_score or 0,
            portfolio_insights=buyer.portfolio_insights,
            avg_spread_closed=float(buyer.avg_spread_closed) if buyer.avg_spread_closed else None,
            price_min=float(buyer.price_min) if buyer.price_min else None,
            price_max=float(buyer.price_max) if buyer.price_max else None,
            pref_cities=buyer.pref_cities,
        )

        # ── Staggered scheduling by tier ──
        # A-List: touch 1 sends immediately
        # B-List: touch 1 scheduled for day 1
        # C-List: touch 1 scheduled for day 3
        base_time = launch_time
        if touch_num == 1:
            if buyer_tier == "A-List":
                touch_status = "Sent"
                scheduled_send = launch_time
            elif buyer_tier == "B-List":
                touch_status = "Queued"
                scheduled_send = launch_time + timedelta(days=1)
            else:  # C-List
                touch_status = "Queued"
                scheduled_send = launch_time + timedelta(days=3)
        else:
            touch_status = "Queued"
            if buyer_tier == "B-List":
                base_time = launch_time + timedelta(days=1)
            elif buyer_tier == "C-List":
                base_time = launch_time + timedelta(days=3)
            scheduled_send = base_time + timedelta(days=config["delay_days"])

        campaign_record = Campaign(
            id=uuid.uuid4(),
            deal_id=deal_id,
            buyer_id=buyer_id,
            touch_number=touch_num,
            status=touch_status,
            subject=email_data.get("subject", ""),
            body=email_data.get("body", ""),
            scheduled_send_at=scheduled_send,
        )

        # Send touch 1 immediately (A-List only)
        if touch_num == 1 and touch_status == "Sent":
            try:
                # ── AI Validation pre-send guard ──
                try:
                    validation = await validate_ai_output(
                        content=email_data.get("body", ""),
                        content_type="campaign_email",
                        deal=deal,
                        buyer=buyer,
                    )
                except Exception as val_err:
                    logger.error(
                        "AI validator failed for touch 1, proceeding with unvalidated send: %s",
                        val_err,
                    )
                    validation = ValidationResult(severity="pass", corrected_content=None, violations=[], checks_run=[])

                if validation.severity == "block":
                    logger.error(
                        "Campaign email blocked by validator for buyer %s, deal %s: %s",
                        buyer_id, deal_id, validation.violations,
                    )
                    campaign_record.status = "Queued"
                else:
                    body_to_send = validation.corrected_content or email_data.get("body", "")
                    await send_email(
                        to=buyer.email,
                        subject=email_data.get("subject", ""),
                        body=body_to_send,
                        campaign_id=campaign_record.id.hex,
                        send_type="campaign",
                    )
                    campaign_record.sent_at = datetime.now(timezone.utc)
                    await increment_pitch_count(db, buyer)
            except Exception as e:
                logger.warning(
                    "Failed to auto-send touch 1 for buyer %s: %s",
                    buyer_id, e, exc_info=True,
                )
                campaign_record.status = "Queued"

        touch_records.append(campaign_record)
        result["campaign_ids"].append(str(campaign_record.id))
        result["touches"].append({
            "touch": touch_num,
            "subject": email_data.get("subject", ""),
            "body": email_data.get("body", ""),
            "status": touch_status,
            "scheduled_send_at": scheduled_send.isoformat() if scheduled_send else None,
        })

    db.add_all(touch_records)

    result["success"] = True
    result["touches_created"] = len(touch_records)

    logger.info(
        "Launched %d-touch campaign for buyer %s (%s) on deal %s (%s) [%s]",
        len(touch_records), buyer_id, buyer.email,
        deal_id, deal.address, buyer_tier,
    )

    return result
