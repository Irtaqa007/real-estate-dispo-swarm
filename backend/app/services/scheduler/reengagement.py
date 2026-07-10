"""Buyer re-engagement scheduler — fires re-engagement emails when buyers' target dates arrive."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

import app.database as _db
from app.models.models import ActivityLog, Buyer, Campaign, Deal, BuyerReengagementSchedule
from app.services.gmail_service import send_email
from app.services.ai_validator import ValidationResult, validate_ai_output
from app.services.email_generator import generate_touch_email

logger = logging.getLogger(__name__)


async def fire_buyer_reengagements() -> int:
    """Fire re-engagement emails for buyers whose scheduled target_date has arrived.

    For each due reengagement:
    1. Verify buyer is still active (not unsubscribed, not Do Not Contact)
    2. Find best matching active deal
    3. Check 2-deal cap and idempotency
    4. Generate AI re-engagement email with context from original statement
    5. Validate via validate_ai_output()
    6. Send email and create Campaign row
    7. Mark schedule as 'fired'

    Returns:
        Number of re-engagement emails fired.
    """
    async with _db.async_session_factory() as db:
        try:
            now = datetime.now(timezone.utc)

            # Find all due re-engagements
            result = await db.execute(
                select(BuyerReengagementSchedule).where(
                    BuyerReengagementSchedule.status == "waiting",
                    BuyerReengagementSchedule.target_date <= now,
                )
            )
            due_schedules = result.scalars().all()

            if not due_schedules:
                return 0

            fired_count = 0

            for schedule in due_schedules:
                try:
                    # 1. Load buyer — check active status
                    buyer = await db.get(Buyer, schedule.buyer_id)
                    if (
                        not buyer
                        or not buyer.email
                        or buyer.unsubscribed_at is not None
                        or buyer.status != "Active"
                    ):
                        schedule.status = "cancelled"
                        schedule.cancelled_at = now
                        schedule.cancellation_reason = "buyer_inactive"
                        db.add(schedule)
                        logger.info(
                            "Re-engagement cancelled for buyer %s: buyer inactive",
                            schedule.buyer_id,
                        )
                        continue

                    # 2. Find best matching active deal
                    deal = await db.get(Deal, schedule.deal_id) if schedule.deal_id else None
                    if not deal or deal.status not in ("Available", "Campaign Launched"):
                        # Find alternative active deal
                        deal_result = await db.execute(
                            select(Deal).where(
                                Deal.status.in_(["Available", "Campaign Launched"]),
                                Deal.deal_embedding.isnot(None),
                            )
                            .order_by(Deal.created_at.desc())
                            .limit(1)
                        )
                        best_deal = deal_result.scalar_one_or_none()
                        if not best_deal:
                            schedule.status = "no_deal_found"
                            db.add(schedule)
                            logger.warning(
                                "No matching deal found for re-engagement buyer %s",
                                schedule.buyer_id,
                            )
                            continue
                        deal = best_deal

                    # 3. Check idempotency: buyer already has campaign for this deal
                    existing_campaign = await db.execute(
                        select(Campaign).where(
                            Campaign.buyer_id == schedule.buyer_id,
                            Campaign.deal_id == deal.id,
                        ).limit(1)
                    )
                    if existing_campaign.scalar_one_or_none():
                        logger.info(
                            "Re-engagement skip for buyer %s deal %s: campaign already exists",
                            schedule.buyer_id, deal.id,
                        )
                        schedule.status = "cancelled"
                        schedule.cancelled_at = now
                        schedule.cancellation_reason = "campaign_already_exists"
                        db.add(schedule)
                        continue

                    # 4. Check 2-deal cap
                    from app.services.matching_service import get_active_deal_count_for_buyer
                    active_count = await get_active_deal_count_for_buyer(db, schedule.buyer_id)
                    if active_count >= 2:
                        from app.models.models import QueuedDealMatch
                        existing_qm = await db.execute(
                            select(QueuedDealMatch).where(
                                QueuedDealMatch.buyer_id == schedule.buyer_id,
                                QueuedDealMatch.deal_id == deal.id,
                                QueuedDealMatch.status == "waiting",
                            )
                        )
                        if not existing_qm.scalar_one_or_none():
                            db.add(QueuedDealMatch(
                                buyer_id=schedule.buyer_id,
                                deal_id=deal.id,
                                status="waiting",
                                queued_at=now,
                            ))
                        logger.info(
                            "Re-engagement queued for buyer %s — at 2-deal cap",
                            schedule.buyer_id,
                        )
                        continue

                    # 5. Generate re-engagement email
                    target_month_str = schedule.target_date.strftime("%B %Y")

                    reengagement_context = (
                        f"IMPORTANT RE-ENGAGEMENT CONTEXT:\n"
                        f"This buyer previously indicated they would be ready "
                        f"to buy around {target_month_str}.\n"
                        f"They said: '{schedule.stated_window_raw}'\n"
                        f"Open the email by naturally referencing that they "
                        f"mentioned this timeframe — make them feel remembered, "
                        f"not marketed to."
                    )

                    original_buy_box = buyer.buy_box
                    enhanced_buy_box = f"{reengagement_context}\n\n{original_buy_box}"
                    buyer.buy_box = enhanced_buy_box

                    try:
                        email_data = await generate_touch_email(
                            touch=1,
                            buyer_name=buyer.full_name,
                            buyer_email=buyer.email,
                            buy_box=enhanced_buy_box,
                            buyer_tier=buyer.buyer_tier or "C-List",
                            address=deal.address,
                            city=deal.city or "",
                            zip_code=deal.zip or "",
                            state=deal.state or "",
                            property_type=deal.property_type,
                            arv=float(deal.arv),
                            asking_price=float(deal.asking_price),
                            spread=float(deal.spread) if deal.spread else 0,
                            condition_description=deal.condition_description,
                            beds=deal.beds,
                            baths=deal.baths,
                            sqft=deal.sqft,
                            buyer_id=buyer.id,
                            expiry_date=deal.expiry_date,
                        )
                    finally:
                        buyer.buy_box = original_buy_box

                    subject = email_data.get("subject", "")
                    body = email_data.get("body", "")

                    if not subject or not body:
                        logger.warning(
                            "Re-engagement email generation failed for buyer %s",
                            schedule.buyer_id,
                        )
                        continue

                    # 6. Validate via AI validator
                    try:
                        validation = await validate_ai_output(
                            content=body,
                            content_type="campaign_email",
                            deal=deal,
                            buyer=buyer,
                        )
                    except Exception as val_err:
                        logger.error(
                            "AI validator failed for re-engagement, proceeding unvalidated: %s",
                            val_err,
                        )
                        validation = ValidationResult(
                            severity="pass", corrected_content=None,
                            violations=[], checks_run=[],
                        )

                    if validation.severity == "block":
                        logger.error(
                            "Re-engagement email blocked by validator for buyer %s: %s",
                            schedule.buyer_id, validation.violations,
                        )
                        continue

                    body_to_send = validation.corrected_content or body

                    # 7. Send email
                    campaign_id = uuid.uuid4()
                    send_result = await send_email(
                        to=buyer.email,
                        subject=subject,
                        body=body_to_send,
                        campaign_id=campaign_id.hex,
                        send_type="campaign",
                    )

                    # 8. Create Campaign row
                    campaign_record = Campaign(
                        id=campaign_id,
                        deal_id=deal.id,
                        buyer_id=schedule.buyer_id,
                        touch_number=1,
                        status="Sent" if send_result.get("status") == "sent" else "Queued",
                        sent_at=now if send_result.get("status") == "sent" else None,
                        subject=subject,
                        body=body_to_send,
                        scheduled_send_at=now,
                    )
                    db.add(campaign_record)

                    # 9. Mark schedule as fired
                    schedule.status = "fired"
                    schedule.fired_at = now
                    db.add(schedule)

                    # 10. Activity log
                    log_entry = ActivityLog(
                        id=uuid.uuid4(),
                        entity_type="buyer",
                        entity_id=schedule.buyer_id,
                        action="reengagement_fired",
                        metadata_json={
                            "buyer_id": str(schedule.buyer_id),
                            "deal_id": str(deal.id),
                            "target_date": schedule.target_date.isoformat(),
                            "stated_window_raw": schedule.stated_window_raw,
                            "alert_user": False,
                        },
                    )
                    db.add(log_entry)

                    await db.commit()
                    fired_count += 1

                    logger.info(
                        "Re-engagement fired for buyer %s -> deal %s "
                        "(target was %s, stated: '%s')",
                        schedule.buyer_id, deal.id,
                        schedule.target_date.strftime("%Y-%m-%d"),
                        schedule.stated_window_raw,
                    )

                except Exception as e:
                    logger.error(
                        "Failed to fire re-engagement for buyer %s: %s",
                        schedule.buyer_id, e, exc_info=True,
                    )
                    await db.rollback()
                    continue

            if fired_count:
                logger.info(
                    "Buyer re-engagement complete: %d re-engagement(s) fired",
                    fired_count,
                )
            return fired_count

        except Exception as e:
            logger.error("Buyer re-engagement failed: %s", e, exc_info=True)
            await db.rollback()
            return 0
