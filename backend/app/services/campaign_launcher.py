"""Reusable campaign launch logic for a single buyer+deal pair.

Extracted from the launch_campaign endpoint so it can be called from:
- The launch_campaign endpoint (bulk, via matching)
- process_queued_matches (single buyer released from queue)

Generates 6 psychologically-optimized touch emails, creates Campaign rows
with staggered scheduling by tier, and sends touch 1 immediately for A-List.
"""

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Campaign, Deal, DealComp, QueuedDealMatch
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

    # Re-fetch deal and buyer fresh to avoid MissingGreenlet on expired ORM objects
    from sqlalchemy import select as _select
    _deal_result = await db.execute(_select(Deal).where(Deal.id == deal_id))
    deal = _deal_result.scalar_one()
    from app.models.models import Buyer as _Buyer
    _buyer_result = await db.execute(_select(_Buyer).where(_Buyer.id == buyer_id))
    buyer = _buyer_result.scalar_one()

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

    # ── Fetch comps for this deal ──
    comps_result = await db.execute(
        select(DealComp).where(DealComp.deal_id == deal_id)
        .order_by(DealComp.sold_date.desc())
    )
    comps_list = [
        {
            "address": c.address,
            "sold_price": float(c.sold_price),
            "sold_date": c.sold_date.strftime("%B %Y") if hasattr(c.sold_date, 'strftime') else str(c.sold_date),
            "beds": c.beds,
            "baths": float(c.baths) if c.baths else None,
            "sqft": c.sqft,
        }
        for c in comps_result.scalars().all()
    ]

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
            zip_code=deal.zip or "",
            property_type=deal.property_type,
            arv=float(deal.arv),
            asking_price=float(deal.asking_price),
            spread=float(deal.asking_price - deal.contract_price) if deal.asking_price and deal.contract_price else 0,
            rehab_estimate=float(deal.repair_estimate) if deal.repair_estimate else None,
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
            comps=comps_list if comps_list else None,
            expiry_date=deal.expiry_date,
        )

        # Skip touch if Groq failed — never send fallback garbage
        if email_data.get("status") == "Failed":
            logger.error("Skipping touch %d for buyer %s — email generation failed", touch_num, buyer_id)
            continue

        # ── Staggered scheduling by tier ──
        # A-List: touch 1 sends immediately
        # B-List: touch 1 scheduled for day 1
        # C-List: touch 1 scheduled for day 3
        base_time = launch_time
        if touch_num == 1:
            if buyer_tier == "A-List":
                touch_status = "Sent"
                scheduled_send = None  # Already sent at launch — scheduler must never resend
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

        # Add human-like jitter: random offset so emails never land at exact intervals
        if touch_status == "Queued":
            jitter_hours = random.uniform(-3, 4)
            jittered = scheduled_send + timedelta(hours=jitter_hours)
            if jittered < launch_time + timedelta(minutes=30):
                jittered = scheduled_send + timedelta(hours=abs(jitter_hours))
            scheduled_send = jittered

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


async def launch_package_campaign(
    db: AsyncSession,
    package,
    deals: list,
    matched_buyers: list[dict],
    total_individual: float = 0,
    savings: float = 0,
    total_profit: float = 0,
) -> dict:
    """Launch package campaigns for matched buyers.

    Creates 4 package-touch campaigns for each matched buyer with optimized
    scheduling (faster arc than single-deal campaigns).

    Returns:
        dict with keys: campaigns_created, errors.
    """
    from app.services.email_generator import generate_package_email
    from app.services.gmail_service import send_email

    launch_time = datetime.now(timezone.utc)
    campaigns_created = 0
    errors = []

    # Package touch config: faster arc
    package_touches = [
        {"touch": 1, "delay_days": 0},
        {"touch": 2, "delay_days": 2},
        {"touch": 3, "delay_days": 5},
        {"touch": 4, "delay_days": 10},
    ]

    package_price = float(package.package_price)
    package_arv = float(package.package_arv) if package.package_arv else 0

    for buyer_info in matched_buyers:
        buyer_id = buyer_info["id"]
        for config in package_touches:
            touch_num = config["touch"]

            try:
                email_data = await generate_package_email(
                    package=package,
                    deals=deals,
                    buyer_name=buyer_info["full_name"],
                    buyer_email=buyer_info["email"],
                    buy_box=buyer_info["buy_box"],
                    buyer_tier=buyer_info["buyer_tier"],
                    touch=touch_num,
                    package_price=package_price,
                    package_arv=package_arv,
                    total_individual=total_individual,
                    savings=savings,
                    total_profit=total_profit,
                )

                if email_data.get("status") == "Failed":
                    logger.warning("Package touch %d failed for buyer %s", touch_num, buyer_id)
                    continue

                scheduled_send = None
                if touch_num == 1:
                    touch_status = "Sent"
                    # Send immediately
                    try:
                        await send_email(
                            to=buyer_info["email"],
                            subject=email_data.get("subject", ""),
                            body=email_data.get("body", ""),
                            send_type="campaign",
                        )
                    except Exception as e:
                        logger.warning("Failed to auto-send package touch 1: %s", e)
                        touch_status = "Queued"
                        scheduled_send = launch_time + timedelta(hours=1)
                else:
                    touch_status = "Queued"
                    scheduled_send = launch_time + timedelta(days=config["delay_days"])

                # Create Campaign record for each deal in the package
                for deal_obj in deals:
                    jitter_hours = random.uniform(-2, 3) if touch_status == "Queued" else 0
                    jittered = None
                    if scheduled_send:
                        jittered = scheduled_send + timedelta(hours=jitter_hours)

                    campaign = Campaign(
                        id=uuid.uuid4(),
                        deal_id=deal_obj.id,
                        buyer_id=buyer_id,
                        touch_number=touch_num,
                        status=touch_status,
                        subject=email_data.get("subject", ""),
                        body=email_data.get("body", ""),
                        scheduled_send_at=jittered,
                        package_id=package.id,
                    )
                    db.add(campaign)
                    campaigns_created += 1

            except Exception as e:
                logger.error("Package campaign error for buyer %s, touch %d: %s", buyer_id, touch_num, e, exc_info=True)
                errors.append({"buyer_id": str(buyer_id), "touch": touch_num, "error": str(e)[:200]})

    logger.info(
        "Package campaigns created: %d campaigns for %d buyers on %d deals",
        campaigns_created, len(matched_buyers), len(deals),
    )

    return {
        "campaigns_created": campaigns_created,
        "errors": errors,
    }